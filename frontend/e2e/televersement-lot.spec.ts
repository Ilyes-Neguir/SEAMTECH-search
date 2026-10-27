import { expect, test } from "@playwright/test"
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { signIn } from "./helpers"

const jeton = "jeton_e2e_televersement_123456"

function etatSession(fichiersRecus: number, octetsRecus: number, statut = "en_cours") {
  return {
    token: jeton,
    statut,
    nom_racine: "VOILES-DEMO",
    nombre_fichiers_attendus: 5,
    taille_totale_attendue_octets: 50,
    nombre_fichiers_recus: fichiersRecus,
    taille_recue_octets: octetsRecus,
    progression_pct: Math.round((octetsRecus / 50) * 100),
    id_lot: null,
    limites: {
      fichiers_par_envoi_recommande: 2,
      taille_envoi_recommandee_octets: 1024 * 1024,
      fichiers_par_envoi_max: 200,
      taille_envoi_max_octets: 1024 * 1024,
      taille_fichier_max_octets: 1024 * 1024,
    },
  }
}

test.describe("Téléversement d'un dossier complet", () => {
  test("un dossier récursif part en plusieurs envois bornés puis crée un seul lot", async ({ page }) => {
    await signIn(page)

    const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-televersement-"))
    const racine = path.join(parent, "VOILES-DEMO")
    const fichiers = [
      "2031/AFFAIRE-UN/fiche_ffab.pdf",
      "2031/AFFAIRE-UN/notes.txt",
      "2031/AFFAIRE-DEUX/fiche.pdf",
      "ACCESSOIRES/AFFAIRE-TROIS/fiche.pdf",
      "ACCESSOIRES/AFFAIRE-TROIS/photo.jpg",
    ]
    for (const relatif of fichiers) {
      const cible = path.join(racine, ...relatif.split("/"))
      await mkdir(path.dirname(cible), { recursive: true })
      await writeFile(cible, "0123456789")
    }

    let creation: Record<string, unknown> | null = null
    let nombreEnvois = 0
    let fichiersRecus = 0
    const cheminsRecus: string[] = []
    let creationLot: Record<string, unknown> | null = null

    await page.route("**/api/imports/dossier/televersements", async (route) => {
      creation = route.request().postDataJSON() as Record<string, unknown>
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(etatSession(0, 0)) })
    })
    await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
      nombreEnvois += 1
      const corps = route.request().postDataBuffer()?.toString("utf8") ?? ""
      const match = corps.match(/name="chemins"\r?\n\r?\n([^\r\n]+)/)
      expect(match, "chaque envoi multipart doit porter la liste JSON des chemins").not.toBeNull()
      const chemins = JSON.parse(match![1]) as string[]
      expect(chemins.length).toBeLessThanOrEqual(2)
      cheminsRecus.push(...chemins)
      fichiersRecus += chemins.length
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(etatSession(fichiersRecus, fichiersRecus * 10)),
      })
    })
    await page.route(`**/api/imports/dossier/televersements/${jeton}/terminer`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(etatSession(5, 50, "pret")),
      })
    })
    await page.route("**/api/imports/dossier/lot", async (route) => {
      creationLot = route.request().postDataJSON() as Record<string, unknown>
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ id_lot: 4242, statut: "en_cours", traitement: "file_durable" }),
      })
    })
    await page.route("**/api/lots/4242", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id_lot: 4242,
          dossier_racine: "session-televersee",
          statut: "termine",
          nb_dossiers: 3,
          nb_traites: 3,
          nb_echecs: 0,
          progression_pct: 100,
          dossiers: [
            {
              chemin_dossier: "/serveur/session/VOILES-DEMO/2031/AFFAIRE-UN",
              chemin_relatif: "VOILES-DEMO/2031/AFFAIRE-UN",
              statut: "traite",
              raison: null,
              id_fiche: 1,
              nb_pieces: 1,
            },
          ],
          restants: [],
        }),
      })
    })

    try {
      await page.goto("/nouveau")
      await page.getByTestId("input-dossier-lot").setInputFiles(racine)
      await expect(page.getByTestId("resume-televersement")).toContainText("5 fichier(s)")
      await expect(page.getByTestId("resume-televersement")).toContainText("3 dossier(s) contenant au moins un PDF direct")

      await page.getByTestId("bouton-televerser-lot").click()
      await expect(page.getByTestId("message-televersement")).toContainText("lot #4242")
      await expect(page.getByTestId("suivi-lot")).toContainText("VOILES-DEMO/2031/AFFAIRE-UN")

      expect(creation).toMatchObject({
        nom_racine: "VOILES-DEMO",
        nombre_fichiers: 5,
        taille_totale_octets: 50,
      })
      expect(nombreEnvois).toBe(3)
      expect(cheminsRecus.sort()).toEqual(
        fichiers.map((relatif) => `VOILES-DEMO/${relatif}`).sort(),
      )
      expect(creationLot).toEqual({ token_televersement: jeton, recursif: true })
    } finally {
      await rm(parent, { recursive: true, force: true })
    }
  })

  test("le glisser-déposer parcourt récursivement les entrées de dossiers", async ({ page }) => {
    await signIn(page)
    await page.goto("/nouveau")

    await page.getByTestId("zone-depot-lot").evaluate((zone) => {
      const fichier = (nom: string) => ({
        isFile: true,
        isDirectory: false,
        name: nom,
        file: (succes: (f: File) => void) => succes(new File(["0123456789"], nom)),
      })
      const dossier = (nom: string, enfants: unknown[]) => ({
        isFile: false,
        isDirectory: true,
        name: nom,
        createReader: () => {
          let servi = false
          return {
            readEntries: (succes: (entrees: unknown[]) => void) => {
              if (servi) succes([])
              else {
                servi = true
                succes(enfants)
              }
            },
          }
        },
      })
      const racine = dossier("VOILES-DEMO", [
        dossier("2031", [
          dossier("AFFAIRE-UN", [fichier("fiche_ffab.pdf"), fichier("notes.txt")]),
          dossier("AFFAIRE-DEUX", [fichier("fiche.pdf")]),
        ]),
      ])
      const evenement = new Event("drop", { bubbles: true, cancelable: true })
      Object.defineProperty(evenement, "dataTransfer", {
        value: {
          items: [{ webkitGetAsEntry: () => racine }],
          files: [],
        },
      })
      zone.dispatchEvent(evenement)
    })

    await expect(page.getByTestId("resume-televersement")).toContainText("VOILES-DEMO — 3 fichier(s)")
    await expect(page.getByTestId("resume-televersement")).toContainText("2 dossier(s) contenant au moins un PDF direct")
  })
})
