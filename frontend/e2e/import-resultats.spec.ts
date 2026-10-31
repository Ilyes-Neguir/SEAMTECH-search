import { expect, test, type Page } from "@playwright/test"

// Les états du backend sont simulés : cette suite qualifie le suivi React,
// pas l'autorisation BFF ni la durabilité du stockage.
const ID = "import-synthetique"
const resultat = {
  import_id: ID,
  source_path: "/source-synthetique",
  status: "completed",
  files_detected: 1,
  analyzed_files: 1,
  technical_pdf: "/source-synthetique/piece.pdf",
  upload_status: "partial",
  warnings: [],
  files: [],
  candidates: [],
  data: { reference: "SYNTHETIQUE", extraction_status: "success", confidence: 1, warnings: [] },
}

async function preparer(page: Page) {
  await page.goto("/login?next=/fichiers")
  await page.getByLabel("Mot de passe").fill(process.env.SEAMTECH_UI_PASSWORD ?? "e2e-shared-password")
  await page.getByRole("button", { name: "Se connecter" }).click()
  await expect(page.getByTestId("archive-browser")).toBeVisible({ timeout: 20000 })
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await page.getByPlaceholder(/Chemin du dossier sur le serveur/i).fill("/source-synthetique")
  await page.route("**/api/imports", async (route) => {
    expect(route.request().method()).toBe("POST")
    await route.fulfill({ status: 202, json: { id: ID, job_id: ID, status: "pending", progress: 0, stage: "queued" } })
  })
}

async function commencer(page: Page) {
  await page.getByRole("button", { name: /Importation rapide/i }).click()
}

test("la revue terminale affiche l'état et le résultat durable, pas le résultat imbriqué périmé", async ({ page }) => {
  await preparer(page)
  let lectures = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    await route.fulfill({ json: {
      ...resultat, status: "needs_review", progress: 100, stage: "done", job_id: ID,
      warnings: ["Conflit de catalogue synthétique"],
      result: { ...resultat, warnings: ["Avertissement périmé"] },
    } })
  })
  await commencer(page)
  await expect(page.getByText(/État\s*:\s*needs_review/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText("Conflit de catalogue synthétique")).toBeVisible()
  await expect(page.getByText("Avertissement périmé")).toHaveCount(0)
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeEnabled()
  const dernierNombre = lectures
  await page.waitForTimeout(1100)
  expect(lectures).toBe(dernierNombre)
})

test("une publication incertaine interdit la reprise aveugle et annonce sa revue", async ({ page }) => {
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({ json: { authenticated: true, identifiant: "synthetique", nom: "Synthétique",
                                  role: "approbateur", mode: "nominatif", doit_changer_mot_de_passe: false } })
  })
  await preparer(page)
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: {
      ...resultat, status: "needs_review", upload_status: "upload_incomplete", progress: 100,
      stage: "reconciliation", job_id: ID, publication_pending: true,
      result: { ...resultat, publication_pending: true },
    } })
  })
  await commencer(page)
  await expect(page.getByText(/État\s*:\s*needs_review/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(/publication incertaine/i)).toBeVisible()
  await expect(page.getByText(/réconciliation/i)).toBeVisible()
  await expect(page.getByRole("button", { name: /Relancer l’envoi/i })).toHaveCount(0)
  await expect(page.getByText("Correction manuelle")).toHaveCount(0)
  await expect(page.getByText(/placé en quarantaine/i)).toHaveCount(0)
})

test("un envoi incomplet dont la source reste sur place peut être repris", async ({ page }) => {
  await preparer(page)
  let reprises = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: {
      ...resultat, status: "upload_incomplete", upload_status: "upload_incomplete", progress: 100,
      stage: "upload_incomplete", job_id: ID, publication_pending: false,
      quarantine: { deplace: false, destination: null, raison: "source conservée sur place" },
      cleanup: { purge: false, raison: "purge suspendue" },
    } })
  })
  await page.route(`**/api/imports/${ID}/retry`, async (route) => {
    reprises++
    expect(route.request().method()).toBe("POST")
    await route.fulfill({ json: { ...resultat, status: "needs_review", warnings: ["Reprise enregistrée"] } })
  })
  await commencer(page)
  await expect(page.getByText(/État\s*:\s*upload_incomplete/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(/conservé.*sur place/i)).toBeVisible()
  await expect(page.getByText(/placé en quarantaine/i)).toHaveCount(0)
  await page.getByRole("button", { name: /Relancer l’envoi/i }).click()
  await expect(page.getByText("Reprise enregistrée")).toBeVisible()
  expect(reprises).toBe(1)
})

test("un échec terminal conserve les détails et ne reste pas en progression", async ({ page }) => {
  await preparer(page)
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: {
      ...resultat, status: "failed", progress: 100, stage: "failed", job_id: ID,
      error: "Échec synthétique", result: { ...resultat, status: "failed" },
    } })
  })
  await commencer(page)
  await expect(page.getByText("Échec synthétique")).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(/État\s*:\s*failed/i)).toBeVisible()
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeEnabled()
})

test("une réponse de suivi tardive ne réactive pas un import annulé", async ({ page }) => {
  await preparer(page)
  let debut!: () => void
  let liberer!: () => void
  const suiviCommence = new Promise<void>((resolve) => { debut = resolve })
  const attendre = new Promise<void>((resolve) => { liberer = resolve })
  let reponseTardive = false
  let annule = false
  await page.route(`**/api/imports/${ID}`, async (route) => {
    if (annule) {
      await route.fulfill({ json: { id: ID, status: "cancelled", progress: 50, stage: "cancelled" } })
      return
    }
    debut()
    await attendre
    reponseTardive = true
    await route.fulfill({ json: { id: ID, job_id: ID, status: "running", progress: 50, stage: "extracting" } }).catch(() => {})
  })
  await page.route(`**/api/imports/${ID}/cancel`, async (route) => {
    expect(route.request().method()).toBe("POST")
    annule = true
    await route.fulfill({ json: { id: ID, status: "cancelled" } })
  })
  await commencer(page)
  await suiviCommence
  await page.getByRole("button", { name: "Annuler", exact: true }).click()
  await expect(page.getByText("Import annulé.")).toBeVisible()
  liberer()
  await expect.poll(() => reponseTardive).toBe(true)
  await page.waitForTimeout(700)
  await expect(page.getByRole("button", { name: "Annuler", exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeEnabled()
})

test("le résumé Excel respecte le contrat réel, y compris les cellules et feuilles", async ({ page }) => {
  const avertissements: string[] = []
  page.on("console", (message) => {
    if (message.text().includes("unique \"key\" prop")) avertissements.push(message.text())
  })
  await preparer(page)
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: {
      ...resultat, status: "completed", progress: 100, stage: "done", job_id: ID,
      excel_summary: {
        filename: "classeur-synthetique.xlsx", path: "/source-synthetique/classeur.xlsx", total_sheets: 2,
        sheet_names: ["Feuille A", "Feuille B"], total_rows: 4,
        sheets: [
          { sheet_name: "Feuille A", row_count: 3, column_count: 2,
            headers: ["Article", "Quantité"], sample_rows: [["Pièce Alpha", "42"], ["Pièce Bêta", "7"]] },
          { sheet_name: "Feuille B", row_count: 1, column_count: 1,
            headers: ["Mesure"], sample_rows: [["Valeur Gamma"]] },
        ],
      },
    } })
  })
  await commencer(page)
  await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText("Résumé du classeur Excel (classeur-synthetique.xlsx)")).toBeVisible()
  await expect(page.getByText(/Feuille A.*3 lignes.*2 colonnes/i)).toBeVisible()
  await expect(page.getByText(/Feuille B.*1 lignes.*1 colonnes/i)).toBeVisible()
  await expect(page.getByRole("cell", { name: "Pièce Alpha", exact: true })).toBeVisible()
  await expect(page.getByRole("cell", { name: "42", exact: true })).toBeVisible()
  await expect(page.getByRole("cell", { name: "Valeur Gamma", exact: true })).toBeVisible()
  expect(avertissements).toEqual([])
})

test("une annulation non confirmée relit le statut plutôt que d'affirmer un succès", async ({ page }) => {
  await preparer(page)
  let lireEnCours!: () => void
  const lectureLancee = new Promise<void>((resolve) => { lireEnCours = resolve })
  let requetes = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    requetes++
    if (requetes === 1) {
      lireEnCours()
      await new Promise((resolve) => setTimeout(resolve, 1000))
      await route.fulfill({ json: { id: ID, status: "running", progress: 50, stage: "extracting" } }).catch(() => {})
    } else {
      await route.fulfill({ json: { ...resultat, status: "completed", progress: 100, stage: "done", job_id: ID } })
    }
  })
  await page.route(`**/api/imports/${ID}/cancel`, async (route) => {
    await route.fulfill({ json: { id: ID, status: "completed" } })
  })
  await commencer(page)
  await lectureLancee
  await page.getByRole("button", { name: "Annuler", exact: true }).click()
  await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(/^Import annulé\./i)).toHaveCount(0)
  expect(requetes).toBeGreaterThanOrEqual(2)
})

test("un double clic sur Annuler ne soumet qu'une demande", async ({ page }) => {
  await preparer(page)
  let nbAnnulations = 0
  let annule = false
  let liberer!: () => void
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: annule
      ? { id: ID, status: "cancelled", progress: 20, stage: "cancelled" }
      : { id: ID, status: "running", progress: 20, stage: "extracting" } })
  })
  await page.route(`**/api/imports/${ID}/cancel`, async (route) => {
    nbAnnulations++
    await attente
    annule = true
    await route.fulfill({ json: { id: ID, status: "cancelled" } }).catch(() => {})
  })
  await commencer(page)
  await page.getByRole("button", { name: "Annuler", exact: true }).dblclick()
  await expect.poll(() => nbAnnulations).toBeGreaterThan(0)
  await page.waitForTimeout(400)
  const constate = nbAnnulations
  liberer()
  await expect(page.getByText(/Import annulé\./i)).toBeVisible()
  expect(constate).toBe(1)
})

test("une régénération interrompue reste corrigeable mais ne permet pas la reprise de l'envoi", async ({ page }) => {
  // La session nominative est simulée ici pour vérifier seulement l'affichage.
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({ json: { authenticated: true, identifiant: "synthetique", nom: "Synthétique",
                                  role: "approbateur", mode: "nominatif", doit_changer_mot_de_passe: false } })
  })
  await preparer(page)
  let corrections = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    if (route.request().method() === "PATCH") {
      corrections++
      expect(route.request().postDataJSON().reference).toBe("CORRECTION-SYNTHETIQUE")
      await route.fulfill({ json: { ...resultat, status: "needs_review", publication_pending: false,
                                    report_generation_pending: false, warnings: ["Correction enregistrée"] } })
    } else {
      await route.fulfill({ json: {
        ...resultat, status: "needs_review", upload_status: "upload_incomplete", job_id: ID,
        progress: 100, stage: "done", publication_pending: false, report_generation_pending: true,
      } })
    }
  })
  await commencer(page)
  await expect(page.getByText(/régénération.*rapports/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText("Correction manuelle")).toBeVisible()
  await expect(page.getByRole("button", { name: /Relancer l’envoi/i })).toHaveCount(0)
  await page.getByLabel("Référence", { exact: true }).fill("CORRECTION-SYNTHETIQUE")
  await page.getByRole("button", { name: "Enregistrer la correction" }).click()
  await expect(page.getByText("Correction enregistrée")).toBeVisible()
  expect(corrections).toBe(1)
})

test("un import annulé avec intention incertaine ne perd pas l'avertissement", async ({ page }) => {
  await preparer(page)
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: {
      ...resultat, status: "cancelled", progress: 100, stage: "cancelled", job_id: ID,
      publication_pending: true, error: "Import annulé ; publication incertaine à réconcilier.",
    } })
  })
  await commencer(page)
  await expect(page.getByText(/État\s*:\s*cancelled/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(/publication incertaine/i).last()).toBeVisible()
  await expect(page.getByRole("button", { name: /Relancer l’envoi/i })).toHaveCount(0)
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeEnabled()
})

test("un suivi terminé sans aucun résultat n'est pas présenté comme une réussite exploitable", async ({ page }) => {
  await preparer(page)
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: { id: ID, job_id: ID, status: "completed", progress: 100, stage: "done" } })
  })
  await commencer(page)
  await expect(page.getByText(/résultat.*indisponible/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeEnabled()
})
