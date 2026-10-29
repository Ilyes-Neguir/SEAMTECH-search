import { expect, test, type Page } from "@playwright/test"
import { APPROBATEUR, LECTURE, UTILISATEUR, csrfHeadersForPage, signInWith } from "./helpers"

/**
 * Lot 3b — l'écran approbateur des extractions de voiles, éprouvé dans le
 * navigateur contre PostgreSQL réel (base live semée par seed-live-pg.py :
 * les dépôts 7792-SO / GENOA / E2E-TROIS ont produit de vraies lignes
 * `sail_extractions` via les Lots 3a/3b).
 *
 * Le parcours prouvé : file → détail côte à côte (PDF réel) → correction du
 * libellé de bateau brut → décision de liaison (création d'un modèle
 * canonique + alias appris) → correction d'une cote relue dans l'historique.
 * Les refus de rôle sont éprouvés à l'écran (lecture/utilisateur : ni lien
 * de navigation ni accès), les gardes d'API sont prouvées par
 * tests/test_lot3b_liaison_voile.py.
 */

const CODE_REFERENCE = "7792-SO"
const LIBELLE_TEST = `Bateau Démo Lot3b ${Date.now()}`
const MODELE_TEST = `Modèle Démo Lot3b ${Date.now()}`

/**
 * Remet la fiche de référence dans l'état où le seed l'a laissée.
 *
 * `libelleOriginal` est lu AVANT la correction ; on le réapplique via l'API de
 * correction (motif explicite : une correction sans motif est refusée), puis on
 * relance la résolution, qui relie la candidate à son modèle canonique d'origine
 * grâce à l'alias exact déjà présent dans `boat_models`.
 */
async function restaurerModeleDeReference(
  page: Page,
  idExtraction: number,
  libelleOriginal: string,
): Promise<void> {
  // Les mutations passent par la double soumission CSRF du BFF : sans l'en-tête
  // `x-csrf-token`, la requête est refusée (403) — c'est le comportement voulu.
  const entetes = await csrfHeadersForPage(page)
  const restaurer = await page.request.post(`/api/extractions-voile/${idExtraction}/corriger`, {
    headers: entetes,
    data: {
      champ: "modele_bateau_brut",
      valeur: libelleOriginal,
      motif: "Nettoyage e2e Lot 3b : remise en état du libellé de référence.",
      revision_fiche: null,
    },
  })
  expect(
    restaurer.ok(),
    `la remise en état du libellé de modèle doit réussir (HTTP ${restaurer.status()} : ${(await restaurer.text()).slice(0, 300)})`,
  ).toBeTruthy()
  const resolution = await page.request.post("/api/extractions-voile/resoudre-lot", {
    headers: entetes,
    data: {},
  })
  expect(resolution.ok(), `la résolution de remise en état doit réussir (HTTP ${resolution.status()})`).toBeTruthy()
}

test.describe("Lot 3b — extractions de voiles", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "Lot 3b navigateur : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL et comptes nominatifs)",
  )
  test("approbateur : file, PDF, correction du libellé, liaison décidée, cote corrigée", async ({ page }) => {
    await signInWith(page, APPROBATEUR)

    // Le lien de navigation n'existe que pour l'approbateur nominatif.
    await page.getByRole("link", { name: "Extractions" }).click()
    await page.waitForURL(/\/extractions$/, { timeout: 20000 })

    const file = page.getByTestId("file-extractions")
    await expect(file).toBeVisible()
    await expect(file.getByTestId("extraction-item").first()).toBeVisible({ timeout: 30000 })

    // La résolution en lot ne lie que les preuves exactes et rend un bilan.
    await page.getByTestId("bouton-resoudre-lot").click()
    await expect(page.getByTestId("message-extraction")).toContainText("Résolution en lot", { timeout: 30000 })

    // La fiche de référence 7792-SO a une extraction réelle avec son PDF.
    await page.getByTestId("recherche-extractions").fill(CODE_REFERENCE)
    const ligne = file.getByTestId("extraction-item").first()
    await expect(ligne).toContainText(CODE_REFERENCE, { timeout: 30000 })
    await ligne.click()

    await expect(page.getByTestId("code-extraction")).toContainText(CODE_REFERENCE, { timeout: 20000 })
    await expect(page.getByTestId("panneau-liaison")).toBeVisible()
    // État d'origine relevé AVANT toute correction : c'est lui qu'on remettra.
    const libelleOriginal = (await page.getByTestId("libelle-brut").textContent())?.trim() ?? ""
    expect(libelleOriginal, "le libellé brut d'origine doit être lisible").not.toBe("")
    // L'identifiant n'est pas dans l'URL : il vient de l'API (source de vérité).
    const listeExtractions = await page.request.get("/api/extractions-voile?limite=100")
    expect(listeExtractions.ok(), "la liste des extractions doit répondre").toBeTruthy()
    const corpsExtractions = (await listeExtractions.json()) as {
      extractions: { id_extraction: number; code_fiche: string | null }[]
    }
    const reference = corpsExtractions.extractions.find((e) => (e.code_fiche ?? "").includes(CODE_REFERENCE))
    expect(reference, "l'extraction de la fiche de référence doit exister").toBeTruthy()
    const idExtraction = reference!.id_extraction
    const canevas = page.getByTestId("pdf-canvas")
    await expect(canevas).toBeVisible({ timeout: 30000 })
    await expect(page.getByTestId("pdf-page")).toContainText("/", { timeout: 30000 })

    // Correction du libellé brut : la résolution est rouverte, plus rien
    // n'est lié tant qu'une preuve exacte ou une décision ne tranche pas.
    await page.getByTestId("motif-correction").fill("Parcours e2e Lot 3b : libellé relu sur le PDF.")
    await page.getByTestId("champ-modele_bateau_brut").fill(LIBELLE_TEST)
    await page.getByTestId("corriger-modele_bateau_brut").click()
    await expect(page.getByTestId("message-extraction")).toBeVisible({ timeout: 30000 })
    await expect(page.getByTestId("libelle-brut")).toHaveText(LIBELLE_TEST, { timeout: 20000 })
    const panneau = page.getByTestId("panneau-liaison")
    await expect(panneau.getByTestId("statut-liaison")).toHaveAttribute(
      "data-statut",
      /non_resolu|ambigu/,
      { timeout: 20000 },
    )

    // Décision humaine : créer le modèle canonique et lier (alias appris).
    await page.getByTestId("nouveau-modele").fill(MODELE_TEST)
    await page.getByTestId("bouton-creer-lier").click()
    await expect(page.getByTestId("message-extraction")).toContainText("alias appris", { timeout: 30000 })
    await expect(panneau.getByTestId("statut-liaison")).toHaveAttribute("data-statut", "lie_humain", {
      timeout: 20000,
    })
    await expect(page.getByTestId("modele-lie")).toContainText(MODELE_TEST)

    // Correction d'une cote vérifiée sur le PDF : historisée à l'écran.
    await page.getByTestId("champ-sf_m").fill("3.09")
    await page.getByTestId("corriger-sf_m").click()
    await expect(page.getByTestId("message-extraction")).toContainText("Correction enregistrée", {
      timeout: 30000,
    })
    const historique = page.getByTestId("historique-corrections")
    await expect(historique).toContainText("modele_bateau_brut")
    await expect(historique).toContainText("sf_m")
    await expect(historique).toContainText("3.09")

    // ---------------------------------------------------------------------
    // REMISE EN ÉTAT (isolation des specs) — défaut d'ordre corrigé le 28/10.
    //
    // Ce test RENOMME le libellé de modèle de la fiche de référence 7792-SO
    // puis la lie à un modèle de démonstration. Sans remise en état, la
    // population de référence du Lot 5 (5 voiles du modèle « 29er ») ne
    // contient plus la candidate : `lot5-confiance` échouait alors qu'il passe
    // SEUL. Autrement dit la suite dépendait de l'ordre alphabétique des
    // fichiers — une suite qui ne dit la vérité que dans un ordre est une suite
    // qui ment.
    //
    // On restaure par l'API du produit (correction + résolution), pas par SQL :
    // on éprouve le chemin réel de reprise en main.
    // ---------------------------------------------------------------------
    await restaurerModeleDeReference(page, idExtraction, libelleOriginal)
  })

  test("lecture : ni lien de navigation, ni accès à l’écran", async ({ page }) => {
    await signInWith(page, LECTURE)
    await expect(page.getByRole("link", { name: "Extractions" })).toHaveCount(0)
    await page.goto("/extractions")
    await expect(page.getByTestId("acces-refuse")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("file-extractions")).toHaveCount(0)
  })

  test("utilisateur : l’écran de décision lui est refusé aussi", async ({ page }) => {
    await signInWith(page, UTILISATEUR)
    await expect(page.getByRole("link", { name: "Extractions" })).toHaveCount(0)
    await page.goto("/extractions")
    await expect(page.getByTestId("acces-refuse")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("file-extractions")).toHaveCount(0)
  })
})
