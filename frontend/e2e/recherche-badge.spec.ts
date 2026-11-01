import { test, expect } from "@playwright/test"
import { APPROBATEUR, csrfHeadersForPage, signIn, signInWith } from "./helpers"

// Contrat 2026-09-29 (production-readiness Phase 2.1) : les fiches A_VALIDER
// sont cherchables PAR DÉFAUT, badgées « Non vérifiée » ; le filtre « Validées
// uniquement » (inclure_a_valider=false) les retire. Le statut n'est jamais
// masqué. Données : le seed crée BIS-7792 (copie a_valider de 7792-SO).

const CODE_A_VALIDER = "BIS-7792"
const CODE_VALIDEE = "7792-SO"

test.describe("Recherche — badge « Non vérifiée » et archive de confiance", () => {
  // e2e « live » : la recherche de fiches exige PostgreSQL (503 en SQLite) et
  // BIS-7792 n'existe que dans la base seedée par e2e/seed-live-pg.py.
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("une fiche a_valider sort par défaut, badgée, et disparaît avec « Validées uniquement »", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche?q=7792")

    // Par défaut : la fiche a_valider EST présente, avec son badge.
    // Locator EXACT par code de fiche : une recherche « 7792 » ramène aussi
    // BIS-7792, dont l'extrait cite le PDF partagé « fiche-7792-SO_ffab.pdf ».
    // Un filtre `hasText` sur « 7792-SO » désignait donc parfois la ligne
    // BIS-7792 (a_valider, donc badgée) et faisait échouer l'assertion sur un
    // produit correct — constaté le 29/10/2026 en suite complète, invisible en
    // exécution isolée et invisible en CI (spec sautée sans PostgreSQL).
    const ligneAValider = page.locator('li[data-code="BIS-7792"]')
    await expect(ligneAValider).toBeVisible({ timeout: 15000 })
    await expect(ligneAValider.getByTestId("badge-non-verifiee")).toBeVisible()
    await expect(ligneAValider).toContainText("Non vérifiée")

    // INVARIANT VÉRIFIÉ, pas une hypothèse de seed : le badge affiché doit être
    // EXACTEMENT le statut servi par l'API, pour CHAQUE ligne de résultat.
    // L'ancienne assertion (« 7792-SO est validée donc sans badge ») reposait sur
    // une donnée fausse : dans la base e2e, 7792-SO est « a_valider » jusqu'à ce
    // que `validation.spec.ts` la valide. Elle ne passait que parce que le
    // locator `hasText: "7792-SO"` attrapait une AUTRE ligne (l'extrait du
    // doublon cite le PDF partagé), donc elle ne prouvait rien — constaté le
    // 29/10/2026, invisible en CI (spec sautée sans PostgreSQL).
    // Lecture UNIQUE du DOM : des `expect` répétés sur une liste qui se
    // rafraîchit mesureraient deux rendus différents.
    const lignesAffichees = await page.locator("li[data-code]").evaluateAll((elements) =>
      elements.map((element) => ({
        code: element.getAttribute("data-code") ?? "",
        badge: element.querySelector('[data-testid="badge-non-verifiee"]') !== null,
      })),
    )
    const reponse = await page.request.get("/api/recherche?q=7792")
    expect(reponse.ok(), "l'API de recherche doit répondre").toBeTruthy()
    const corps = (await reponse.json()) as { resultats: { code: string | null; statut: string }[] }
    const statutsApi = new Map(corps.resultats.filter((f) => f.code).map((f) => [f.code as string, f.statut]))
    expect(statutsApi.size, "la recherche doit rendre des fiches").toBeGreaterThan(0)
    for (const ligne of lignesAffichees) {
      if (!statutsApi.has(ligne.code)) continue
      expect(ligne.badge, `badge de ${ligne.code} (statut API : ${statutsApi.get(ligne.code)})`).toBe(
        statutsApi.get(ligne.code) === "a_valider",
      )
    }
    // Dans la base e2e, 7792-SO et BIS-7792 sont TOUTES DEUX « a_valider »
    // (mesuré : GET /api/recherche?q=7792 → 2 fiches, statut a_valider). Le
    // contrat « une fiche validée n'a pas de badge » est prouvé par le test
    // suivant, qui valide puis rouvre une vraie fiche — pas par une hypothèse
    // sur le seed.
    // Le filtre « Validées uniquement » retire les fiches a_valider…
    const caseArchive = page.getByTestId("case-validees-uniquement")
    await expect(caseArchive).toBeVisible()
    await caseArchive.check()
    await expect(page.locator(`li[data-code="${CODE_A_VALIDER}"]`)).toHaveCount(0, { timeout: 15000 })
    await expect(page.locator(`li[data-code="${CODE_VALIDEE}"]`)).toHaveCount(0, { timeout: 15000 })
    // …et l'état est partageable dans l'URL.
    await expect(page).toHaveURL(/inclure_a_valider=false/, { timeout: 5000 })
    await expect(page.getByTestId("pastille-validees-uniquement")).toBeVisible()

    // Décocher ré-affiche la fiche badgée (l'état n'est jamais caché).
    await caseArchive.uncheck()
    await expect(page.locator(`li[data-code="${CODE_A_VALIDER}"]`)).toBeVisible({ timeout: 15000 })
    await expect(
      page.locator(`li[data-code="${CODE_A_VALIDER}"]`).getByTestId("badge-non-verifiee"),
    ).toBeVisible()
  })

  test("une fiche validée par l'approbateur perd son badge, la rouvrir le remet", async ({ page }) => {
    // Sens NÉGATIF du contrat, jusqu'ici non prouvé : le badge ne s'affiche que
    // pour « a_valider ». On passe par le VRAI chemin produit (POST valider puis
    // POST rouvrir, rôle approbateur, révision optimiste) et on remet l'état de
    // départ pour ne pas polluer les specs suivantes.
    test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL")
    test.setTimeout(120_000)
    await signInWith(page, APPROBATEUR)
    const entetes = await csrfHeadersForPage(page)

    const etatAvant = await page.request.get(`/api/fiches/${CODE_VALIDEE}/etat`)
    expect(etatAvant.ok(), "GET /etat doit répondre").toBeTruthy()
    const avant = (await etatAvant.json()) as { statut: string; revision: number }
    expect(avant.statut).toBe("a_valider")

    const validation = await page.request.post(`/api/fiches/${CODE_VALIDEE}/valider`, {
      headers: entetes,
      data: { revision: avant.revision },
    })
    expect(validation.status(), "la validation doit aboutir").toBeLessThan(300)

    await page.goto(`/recherche?q=${CODE_VALIDEE}`)
    const ligne = page.locator(`li[data-code="${CODE_VALIDEE}"]`)
    await expect(ligne).toBeVisible({ timeout: 15000 })
    await expect(ligne.getByTestId("badge-non-verifiee")).toHaveCount(0)

    // REMISE EN ÉTAT par le chemin réel : rouvrir la fiche.
    const etatApres = await page.request.get(`/api/fiches/${CODE_VALIDEE}/etat`)
    const apres = (await etatApres.json()) as { statut: string; revision: number }
    expect(apres.statut).toBe("valide")
    const reouverture = await page.request.post(`/api/fiches/${CODE_VALIDEE}/rouvrir`, {
      headers: entetes,
      data: { revision: apres.revision, motif: "Remise en état e2e après preuve du badge." },
    })
    expect(reouverture.status(), "la réouverture doit aboutir").toBeLessThan(300)

    const etatFinal = await page.request.get(`/api/fiches/${CODE_VALIDEE}/etat`)
    expect(((await etatFinal.json()) as { statut: string }).statut).toBe("a_valider")
    await page.goto(`/recherche?q=${CODE_VALIDEE}`)
    await expect(
      page.locator(`li[data-code="${CODE_VALIDEE}"]`).getByTestId("badge-non-verifiee"),
    ).toBeVisible({ timeout: 15000 })
  })

  test("Échap ferme les suggestions et libère les filtres (MAN-03)", async ({ page }) => {
    // Campagne manuelle Lot 10 : avec un préfixe fréquent, la liste de
    // suggestions (ouverte par l'autofocus) recouvrait les filtres et le
    // premier clic partait dans une suggestion. Échap doit la fermer.
    await signIn(page)
    await page.goto("/recherche")

    const saisie = page.getByTestId("recherche-saisie")
    await saisie.fill("77")
    const suggestions = page.getByTestId("recherche-suggestions")
    await expect(suggestions).toBeVisible({ timeout: 15000 })

    await saisie.press("Escape")
    await expect(suggestions).toHaveCount(0, { timeout: 5000 })

    // Le filtre redevient cliquable immédiatement après fermeture.
    await page.getByTestId("case-validees-uniquement").check()
    await expect(page).toHaveURL(/inclure_a_valider=false/, { timeout: 5000 })
  })
})
