import { expect, test, type Page } from "@playwright/test"
import { mkdtemp, mkdir, copyFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { signIn } from "./helpers"

/**
 * Constat A08 de l'audit du 2026-10-08 — « Nouveau dossier » appelle
 * `/api/lots/{id}` après un dépôt, et cette route FRONT n'existait pas : Next
 * répondait son propre 404, l'écran avalait l'erreur et n'affichait AUCUN suivi.
 * L'opérateur lisait « Dossier traité » puis plus rien : ni progression, ni
 * raison d'échec, ni fichiers restants.
 *
 * Ce que la première spec prouve SANS navigateur (donc exécutable partout) :
 * le relais existe et répond ce que dit le backend. Avant correction, la
 * requête recevait un 404 « route introuvable » émis par le front lui-même —
 * impossible à distinguer, côté écran, d'un lot inexistant.
 *
 * La seconde spec exige la pile live (PostgreSQL seedé) : elle dépose deux
 * dossiers et vérifie que le suivi affiché est celui du DERNIER lot, même
 * lorsqu'une réponse du lot précédent arrive après coup (garde de génération
 * du suivi — le minuteur d'un lot abandonné ne doit pas réécrire l'écran).
 */

/** Dossier temporaire : copie d'un PDF réel du dépôt, jamais une donnée client inventée. */
async function dossierTemporaire(): Promise<string> {
  const racine = await mkdtemp(path.join(tmpdir(), "seamtech-e2e-a08-"))
  await mkdir(racine, { recursive: true })
  await copyFile(
    path.resolve(__dirname, "../../sample_data/CLIENT-GENOA/fiche-genois.pdf"),
    path.join(racine, "fiche.pdf"),
  )
  return racine
}

async function deposer(page: Page, dossier: string): Promise<number> {
  const reponseDepot = page.waitForResponse(
    (r) => r.url().includes("/api/imports/dossier") && r.request().method() === "POST",
  )
  await page.getByTestId("champ-dossier").fill(dossier)
  await page.getByTestId("bouton-deposer").click()
  const reponse = await reponseDepot
  expect(reponse.status()).toBe(201)
  const corps = (await reponse.json()) as { statut: string; id_lot: number | null; lot?: { id_lot: number } }
  const idLot = corps.id_lot ?? corps.lot?.id_lot ?? null
  expect(idLot, `le dépôt doit rendre un lot suivi (réponse : ${JSON.stringify(corps)})`).not.toBeNull()
  return idLot as number
}

test.describe("Suivi de lot après dépôt (constat A08)", () => {
  test("le relais /api/lots/{id} existe : il relaie, il n'invente pas un 404 local", async ({ page }) => {
    await signIn(page)

    const liste = await page.request.get("/api/lots")
    if (liste.status() === 200) {
      // Pile live : le relais doit rendre EXACTEMENT le lot demandé.
      const lots = (await liste.json()) as Array<{ id_lot: number }>
      expect(Array.isArray(lots)).toBe(true)
      test.skip(lots.length === 0, "aucun lot dans cette base — rien à relire")
      const id = lots[0].id_lot
      const detail = await page.request.get(`/api/lots/${id}`)
      expect(detail.status()).toBe(200)
      const corps = (await detail.json()) as { id_lot: number; statut: string }
      expect(corps.id_lot).toBe(id)
      expect(typeof corps.statut).toBe("string")
    } else {
      // Mode sans PostgreSQL : le relais doit PROPAGER la raison du backend
      // (502 injoignable / 503 non configuré) — et surtout jamais répondre son
      // propre 404 « introuvable », qui se lirait comme « lot inexistant ».
      expect([502, 503]).toContain(liste.status())
      const detail = await page.request.get("/api/lots/1")
      expect([502, 503]).toContain(detail.status())
    }
  })

  test("dépôt → le suivi s'affiche, et un lot abandonné ne réécrit pas l'écran", async ({ page }) => {
    test.skip(
      !process.env.SEAMTECH_E2E_DATABASE_URL,
      "parcours live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)",
    )
    await signIn(page)

    // Réponse du premier lot RETARDÉE : sans garde de génération, elle
    // reviendrait après le second dépôt et réafficherait le premier lot.
    const retards = new Map<number, number>()
    await page.route("**/api/lots/*", async (route) => {
      const id = Number(route.request().url().split("/").pop())
      const delai = retards.get(id)
      if (delai) await new Promise((r) => setTimeout(r, delai))
      await route.continue()
    })

    const premier = await dossierTemporaire()
    const second = await dossierTemporaire()
    try {
      await page.goto("/nouveau")

      const id1 = await deposer(page, premier)
      await expect(page.getByTestId("resultat-depot")).toBeVisible({ timeout: 20000 })
      await expect(page.getByTestId("suivi-lot")).toContainText(`Lot #${id1}`, { timeout: 20000 })
      await expect(page.getByTestId("erreur-suivi")).toHaveCount(0)

      // Rafraîchissement du lot #1 : cette réponse-là arrivera APRÈS le 2e dépôt.
      retards.set(id1, 3000)
      const rafraichi = page.waitForResponse(
        (r) => r.url().endsWith(`/api/lots/${id1}`) && r.request().method() === "GET",
      )
      await page.getByRole("button", { name: "Rafraîchir le lot" }).click()

      const id2 = await deposer(page, second)
      expect(id2).not.toBe(id1)

      // Le suivi suit LE NOUVEAU lot, et la réponse périmée du 1er lot est
      // arrivée (elle a été servie) : l'écran ne doit toujours pas l'avoir reprise.
      await expect(page.getByTestId("suivi-lot")).toContainText(`Lot #${id2}`, { timeout: 20000 })
      await rafraichi
      await expect(page.getByTestId("suivi-lot")).toContainText(`Lot #${id2}`)
      await expect(page.getByTestId("suivi-lot")).not.toContainText(`Lot #${id1}`)
      await expect(page.getByTestId("erreur-suivi")).toHaveCount(0)
    } finally {
      await rm(premier, { recursive: true, force: true })
      await rm(second, { recursive: true, force: true })
    }
  })
})
