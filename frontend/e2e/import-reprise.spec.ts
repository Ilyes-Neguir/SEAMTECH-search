import { expect, test, type Page } from "@playwright/test"
import fs from "fs"
import path from "path"
import { deposerCheminParCDP } from "./helpers"

// Seuls les appels import sont simulés ici ; le banc avec backend/Redis réels
// complète ces scénarios d'interface. Aucun chemin ni document réel n'est utilisé.
const ID = "suivi-synthetique"
const resultat = {
  id: ID, job_id: ID, import_id: ID, source_path: "/source-synthetique",
  status: "completed", progress: 100, stage: "done", files_detected: 1,
  analyzed_files: 1, upload_status: "partial", warnings: [], files: [], candidates: [],
  data: { reference: "SYNTHETIQUE", extraction_status: "success", confidence: 1, warnings: [] },
}
const enCours = { id: ID, job_id: ID, status: "running", progress: 30, stage: "extracting" }

async function preparer(page: Page) {
  await page.goto("/login?next=/fichiers")
  await page.getByLabel("Mot de passe").fill(process.env.SEAMTECH_UI_PASSWORD ?? "e2e-shared-password")
  await page.getByRole("button", { name: "Se connecter" }).click()
  await expect(page.getByTestId("archive-browser")).toBeVisible({ timeout: 20000 })
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await page.getByPlaceholder(/Chemin du dossier sur le serveur/i).fill("/source-synthetique")
  let creations = 0
  await page.route("**/api/imports", async (route) => {
    creations++
    await route.fulfill({ status: 202, json: { id: ID, job_id: ID, status: "pending", progress: 0, stage: "queued" } })
  })
  return () => creations
}

async function commencer(page: Page) {
  await page.getByRole("button", { name: /Importation rapide/i }).click()
}

test("un GET bloqué expire, reprend le suivi et ignore sa réponse tardive", async ({ page }) => {
  test.setTimeout(45000)
  const creations = await preparer(page)
  let lectures = 0
  let liberer!: () => void
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    if (lectures === 1) {
      await attente
      await route.fulfill({ json: enCours }).catch(() => {})
    } else {
      await route.fulfill({ json: resultat })
    }
  })
  try {
    await commencer(page)
    await expect(page.getByText(/suivi.*indisponible.*automatique/i)).toBeVisible({ timeout: 13000 })
    await expect(page.getByRole("button", { name: /Traitement/i })).toBeDisabled()
    await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 10000 })
    liberer()
    await page.waitForTimeout(900)
    await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible()
    await expect(page.getByRole("button", { name: "Annuler", exact: true })).toHaveCount(0)
    expect(lectures).toBeGreaterThanOrEqual(2)
    expect(creations()).toBe(1)
  } finally {
    liberer()
  }
})

test("un 503 ne fait pas oublier le job et le suivi reprend sans nouvel import", async ({ page }) => {
  const creations = await preparer(page)
  let lectures = 0
  let liberer!: () => void
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    if (lectures === 1) await route.fulfill({ status: 503, json: { detail: "Indisponible" } })
    else {
      await attente
      await route.fulfill({ json: resultat }).catch(() => {})
    }
  })
  try {
    await commencer(page)
    await expect(page.getByText(/suivi.*indisponible.*automatique/i)).toBeVisible({ timeout: 8000 })
    await expect(page.getByRole("button", { name: /Traitement/i })).toBeDisabled()
    await expect.poll(() => lectures, { timeout: 8000 }).toBeGreaterThanOrEqual(2)
    liberer()
    await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
    expect(creations()).toBe(1)
  } finally {
    liberer()
  }
})

test("recharger retrouve le dernier suivi sans enregistrer le chemin ni le résultat", async ({ page }) => {
  const creations = await preparer(page)
  let lectures = 0
  let terminer = false
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    await route.fulfill({ json: terminer ? resultat : enCours })
  })
  await commencer(page)
  await expect.poll(() => lectures, { timeout: 8000 }).toBeGreaterThan(0)
  const contenu = await page.evaluate(() => Object.values(sessionStorage).join(" "))
  expect(contenu).toContain(ID)
  expect(contenu).not.toContain("/source-synthetique")
  expect(contenu).not.toContain("SYNTHETIQUE")
  terminer = true
  await page.reload()
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
  expect(creations()).toBe(1)
  expect(lectures).toBeGreaterThanOrEqual(2)
})

test("quitter puis rouvrir la recherche ne perd pas le suivi", async ({ page }) => {
  const creations = await preparer(page)
  let lectures = 0
  let terminer = false
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    await route.fulfill({ json: terminer ? resultat : enCours })
  })
  await commencer(page)
  await expect.poll(() => lectures, { timeout: 8000 }).toBeGreaterThan(0)
  await page.getByRole("button", { name: "Catalogue d’archive" }).click()
  terminer = true
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
  expect(creations()).toBe(1)
})

test("une annulation confirmée relit le résultat durable et son intention incertaine", async ({ page }) => {
  await preparer(page)
  let annule = false
  let lecturesApres = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    if (annule) {
      lecturesApres++
      await route.fulfill({ json: { ...resultat, status: "cancelled", stage: "cancelled",
        publication_pending: true, error: "Import annulé ; publication incertaine à réconcilier." } })
    } else await route.fulfill({ json: enCours })
  })
  await page.route(`**/api/imports/${ID}/cancel`, async (route) => {
    annule = true
    await route.fulfill({ json: { id: ID, status: "cancelled" } })
  })
  await commencer(page)
  await expect(page.getByRole("button", { name: "Annuler", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Annuler", exact: true }).click()
  await expect(page.getByText(/État\s*:\s*cancelled/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(/publication incertaine/i).last()).toBeVisible()
  expect(lecturesApres).toBeGreaterThan(0)
})

test("le suivi d'une autre identité n'est pas repris automatiquement", async ({ page }) => {
  let identifiant = "compte-synthetique-a"
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({ json: { authenticated: true, identifiant, nom: "Synthétique",
      role: "utilisateur", mode: "nominatif", doit_changer_mot_de_passe: false } })
  })
  await preparer(page)
  let lectures = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    await route.fulfill({ json: enCours })
  })
  await commencer(page)
  await expect.poll(() => lectures, { timeout: 8000 }).toBeGreaterThan(0)
  identifiant = "compte-synthetique-b"
  await page.reload()
  const avant = lectures
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await page.waitForTimeout(1200)
  expect(lectures).toBe(avant)
  await expect(page.getByRole("button", { name: "Annuler", exact: true })).toHaveCount(0)
})

test("si le stockage de session est refusé, l'identifiant permet une reprise explicite", async ({ page }) => {
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = function (cle: string, valeur: string) {
      if (cle === "seamtech:suivi-import:v1") throw new DOMException("storage disabled", "SecurityError")
      return original.call(this, cle, valeur)
    }
  })
  const creations = await preparer(page)
  let terminer = false
  await page.route(`**/api/imports/${ID}`, async (route) => {
    await route.fulfill({ json: terminer ? resultat : enCours })
  })
  await commencer(page)
  await expect(page.getByText(/suivi.*pas.*enregistr/i)).toBeVisible({ timeout: 8000 })
  await expect(page.getByText(ID, { exact: true })).toBeVisible()
  terminer = true
  await page.reload()
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await page.getByLabel(/identifiant d’un import à suivre/i).fill(ID)
  await page.getByRole("button", { name: /reprendre le suivi/i }).click()
  await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
  expect(creations()).toBe(1)
})

test("un état inconnu ou un mauvais identifiant ne libère pas un import actif", async ({ page }) => {
  await preparer(page)
  let lectures = 0
  let liberer!: () => void
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    if (lectures === 1) await route.fulfill({ json: { ...enCours, status: "nouvel_etat" } })
    else if (lectures === 2) await route.fulfill({ json: { ...resultat, id: "autre", job_id: "autre" } })
    else {
      await attente
      await route.fulfill({ json: resultat }).catch(() => {})
    }
  })
  try {
    await commencer(page)
    await expect(page.getByText(/suivi.*indisponible.*automatique/i)).toBeVisible({ timeout: 8000 })
    await expect(page.getByRole("button", { name: /Traitement/i })).toBeDisabled()
    await expect.poll(() => lectures, { timeout: 10000 }).toBeGreaterThanOrEqual(3)
    await expect(page.getByRole("button", { name: /Traitement/i })).toBeDisabled()
    liberer()
    await expect(page.getByText(/État\s*:\s*completed/i)).toBeVisible({ timeout: 8000 })
  } finally {
    liberer()
  }
})

test("abandonner un suivi indisponible exige confirmation et ne prétend pas annuler le travail", async ({ page }) => {
  await preparer(page)
  let lectures = 0
  await page.route(`**/api/imports/${ID}`, async (route) => {
    lectures++
    await route.fulfill({ status: 404, json: { detail: "Import inconnu" } })
  })
  await commencer(page)
  await expect(page.getByText(/suivi.*indisponible.*automatique/i)).toBeVisible({ timeout: 8000 })
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toMatch(/n’annule pas/i)
    await dialog.dismiss()
  })
  await page.getByRole("button", { name: /arrêter le suivi/i }).click()
  await expect(page.getByRole("button", { name: /Traitement/i })).toBeDisabled()
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toMatch(/n’annule pas/i)
    await dialog.accept()
  })
  await page.getByRole("button", { name: /arrêter le suivi/i }).click()
  await expect(page.getByText(/suivi arrêté.*peut encore être en cours/i)).toBeVisible()
  await expect(page.getByLabel(/identifiant d’un import à suivre/i)).toHaveValue(ID)
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeEnabled()
  const apres = lectures
  await page.waitForTimeout(2000)
  expect(lectures).toBe(apres)
})

for (const selection of ["chemin", "fichier"] as const) {
  test(`un changement d'identité efface la sélection en mémoire : ${selection}`, async ({ page }) => {
    let identifiant = "compte-synthetique-a"
    await page.route("**/api/auth/session", async (route) => {
      await route.fulfill({ json: { authenticated: true, identifiant, nom: "Synthétique",
        role: "utilisateur", mode: "nominatif", doit_changer_mot_de_passe: false } })
    })
    await preparer(page)
    if (selection === "fichier") {
      await page.locator('input[type="file"]').first().setInputFiles({
        name: "document-synthetique.txt", mimeType: "text/plain", buffer: Buffer.from("contenu fictif"),
      })
      await expect(page.getByText(/1 fichier.*prêt/i)).toBeVisible()
      await expect(page.getByPlaceholder(/Chemin du dossier sur le serveur/i)).toHaveValue("")
    } else {
      await expect(page.getByPlaceholder(/Chemin du dossier sur le serveur/i)).toHaveValue("/source-synthetique")
    }
    identifiant = "compte-synthetique-b"
    await page.waitForTimeout(5200)
    await page.evaluate(() => window.dispatchEvent(new Event("focus")))
    await expect(page.getByTestId("session-identite")).toContainText(identifiant, { timeout: 10000 })
    await expect(page.getByPlaceholder(/Chemin du dossier sur le serveur/i)).toHaveValue("")
    await expect(page.getByText(/1 fichier.*prêt/i)).toHaveCount(0)
  })
}

test("une lecture de dépôt tardive ne revient pas dans la session suivante", async ({ page }) => {
  let identifiant = "compte-synthetique-a"
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({ json: { authenticated: true, identifiant, nom: "Synthétique",
      role: "utilisateur", mode: "nominatif", doit_changer_mot_de_passe: false } })
  })
  await preparer(page)
  await page.waitForTimeout(5200)
  await page.getByTestId("drop-zone").evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "tardif.txt", file: (ok: (f: File) => void) => {
      ;(window as Window & { libererFichierTest?: () => void }).libererFichierTest = () => ok(new File(["fictif"], "tardif.txt"))
    } }
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: { items: [{ webkitGetAsEntry: () => fichier }], files: [] } })
    element.dispatchEvent(evenement)
  })
  identifiant = "compte-synthetique-b"
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  await expect(page.getByTestId("session-identite")).toContainText(identifiant, { timeout: 10000 })
  await page.evaluate(() => (window as Window & { libererFichierTest?: () => void }).libererFichierTest?.())
  await page.waitForTimeout(600)
  await expect(page.getByText("tardif.txt", { exact: true })).toHaveCount(0)
  await expect(page.getByText(/1 fichier.*prêt/i)).toHaveCount(0)
})

test("un second dépôt ne remplace pas une lecture de dossier encore en cours", async ({ page }) => {
  await preparer(page)
  const secondLu = await page.getByTestId("drop-zone").evaluate((element) => {
    let secondAppelee = false
    const premier = { isFile: true, isDirectory: false, name: "premier.txt", file: (ok: (f: File) => void) => {
      ;(window as Window & { libererDepotTest?: () => void }).libererDepotTest = () => ok(new File(["un"], "premier.txt"))
    } }
    const second = { isFile: true, isDirectory: false, name: "second.txt", file: (ok: (f: File) => void) => {
      secondAppelee = true
      ok(new File(["deux"], "second.txt"))
    } }
    for (const entree of [premier, second]) {
      const evenement = new Event("drop", { bubbles: true, cancelable: true })
      Object.defineProperty(evenement, "dataTransfer", { value: { items: [{ webkitGetAsEntry: () => entree }], files: [] } })
      element.dispatchEvent(evenement)
    }
    return secondAppelee
  })
  expect(secondLu).toBe(false)
  await expect(page.getByRole("button", { name: "Choisir des fichiers" })).toBeDisabled()
  await page.evaluate(() => (window as Window & { libererDepotTest?: () => void }).libererDepotTest?.())
  await expect(page.getByText("premier.txt", { exact: true })).toBeVisible()
  await expect(page.getByText("second.txt", { exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Choisir des fichiers" })).toBeEnabled()
})

test("un dépôt partiellement illisible est refusé au lieu de paraître complet", async ({ page }) => {
  await preparer(page)
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "ancien.txt", mimeType: "text/plain", buffer: Buffer.from("ancien contenu fictif"),
  })
  await expect(page.getByText("ancien.txt", { exact: true })).toBeVisible()
  await page.getByTestId("drop-zone").evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "lisible.txt", file: (ok: (f: File) => void) => ok(new File(["ok"], "lisible.txt")) }
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: {
      items: [{ kind: "file", webkitGetAsEntry: () => fichier }, { kind: "file", webkitGetAsEntry: () => null }],
      files: [new File(["ok"], "lisible.txt")],
    } })
    element.dispatchEvent(evenement)
  })
  await expect(page.getByText(/lecture du dossier impossible/i)).toBeVisible()
  await expect(page.getByText("lisible.txt", { exact: true })).toHaveCount(0)
  await expect(page.getByText("ancien.txt", { exact: true })).toHaveCount(0)
  await expect(page.getByText(/1 fichier.*prêt/i)).toHaveCount(0)
})

test("une erreur tardive de lecture n'envoie ni ne présente une liste partielle", async ({ page }) => {
  await preparer(page)
  let envois = 0
  await page.route("**/api/imports/upload", async (route) => { envois++; await route.abort() })
  await page.getByTestId("drop-zone").evaluate((element) => {
    const bon = { isFile: true, isDirectory: false, name: "bon.txt", file: (ok: (f: File) => void) => ok(new File(["ok"], "bon.txt")) }
    const mauvais = { isFile: true, isDirectory: false, name: "illisible.txt", file: (_ok: unknown, non: (e: Error) => void) => non(new Error("défaut de lecture")) }
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: {
      items: [bon, mauvais].map((entree) => ({ kind: "file", webkitGetAsEntry: () => entree })), files: [],
    } })
    element.dispatchEvent(evenement)
  })
  await expect(page.getByText(/lecture du dossier impossible/i)).toBeVisible()
  await expect(page.getByText("bon.txt", { exact: true })).toHaveCount(0)
  expect(envois).toBe(0)
})

test("les lots successifs d'entrées et les sous-dossiers restent tous dans le formulaire", async ({ page }) => {
  await preparer(page)
  let contenu: string | null = null
  await page.route("**/api/imports/upload", async (route) => {
    contenu = route.request().postDataBuffer()?.toString("utf8") ?? ""
    await route.fulfill({ json: { source_path: "/copie-synthetique", staged_path: "/copie-synthetique",
      files_detected: 121, candidates: [], excel_candidates: [], warnings: [] } })
  })
  await page.getByTestId("drop-zone").evaluate((element) => {
    const fichier = (nom: string) => ({ isFile: true, isDirectory: false, name: nom,
      file: (ok: (f: File) => void) => ok(new File([nom], nom)) })
    const dossier = (nom: string, lots: unknown[][]) => ({ isFile: false, isDirectory: true, name: nom,
      createReader: () => { let i = 0; return { readEntries: (ok: (items: unknown[]) => void) => ok(lots[i++] ?? []) } } })
    const premiers = Array.from({ length: 100 }, (_, i) => fichier(`piece-${String(i).padStart(3, "0")}.txt`))
    const autres = Array.from({ length: 20 }, (_, i) => fichier(`annexe-${String(i).padStart(3, "0")}.txt`))
    const root = dossier("racine", [premiers, [fichier("fin.txt"), dossier("sous", [autres, []])], []])
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: { items: [{ kind: "file", webkitGetAsEntry: () => root }], files: [] } })
    element.dispatchEvent(evenement)
  })
  await expect(page.getByText(/121 fichiers prêts à envoyer/i)).toBeVisible()
  await page.getByRole("button", { name: "Envoyer et analyser" }).click()
  await expect.poll(() => contenu !== null).toBe(true)
  expect(contenu!.match(/name="files"; filename=/g)).toHaveLength(121)
  expect(contenu).toContain('filename="racine/piece-000.txt"')
  expect(contenu).toContain('filename="racine/fin.txt"')
  expect(contenu).toContain('filename="racine/sous/annexe-019.txt"')
  await expect(page.getByText(/121 fichiers analysés/i)).toBeVisible()
})

async function analyserSelectionAncienne(page: Page) {
  await page.route("**/api/imports/scan", (route) => route.fulfill({ json: {
    source_path: "/source-synthetique", files_detected: 1, warnings: [], excel_candidates: [],
    candidates: [{ path: "/source-synthetique/ancien.pdf", name: "ancien.pdf", size: 20,
      anchor_count: 1, anchors_matched: ["fiche"] }],
  } }))
  await page.getByRole("button", { name: "Analyser le dossier" }).click()
  await expect(page.getByText("ancien.pdf", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Importer les fichiers sélectionnés" })).toBeEnabled()
}

test("changer le chemin invalide l'analyse et la sélection précédentes", async ({ page }) => {
  await preparer(page)
  await analyserSelectionAncienne(page)
  await page.getByPlaceholder(/Chemin du dossier sur le serveur/i).fill("/nouvelle-source-synthetique")
  await expect(page.getByText("ancien.pdf", { exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Importer les fichiers sélectionnés" })).toHaveCount(0)
})

test("choisir d'autres fichiers n'autorise pas l'ancien dossier ni son analyse", async ({ page }) => {
  await preparer(page)
  await analyserSelectionAncienne(page)
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "nouveau.pdf", mimeType: "application/pdf", buffer: Buffer.from("synthetique"),
  })
  await expect(page.getByText("nouveau.pdf", { exact: true })).toBeVisible()
  await expect(page.getByPlaceholder(/Chemin du dossier sur le serveur/i)).toHaveValue("")
  await expect(page.getByText("ancien.pdf", { exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Importer les fichiers sélectionnés" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: /Importation rapide/i })).toBeDisabled()
})

test("une nouvelle tentative de dépôt illisible ne laisse pas une ancienne analyse exploitable", async ({ page }) => {
  await preparer(page)
  await analyserSelectionAncienne(page)
  await page.getByTestId("drop-zone").evaluate((element) => {
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: {
      items: [{ kind: "file", webkitGetAsEntry: () => null }], files: [],
    } })
    element.dispatchEvent(evenement)
  })
  await expect(page.getByText(/lecture du dossier impossible/i)).toBeVisible()
  await expect(page.getByText("ancien.pdf", { exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Importer les fichiers sélectionnés" })).toHaveCount(0)
})

test("le chemin et les sélections ne changent pas pendant une analyse en cours", async ({ page }) => {
  await preparer(page)
  let liberer!: () => void
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  await page.route("**/api/imports/scan", async (route) => {
    await attente
    await route.fulfill({ json: { source_path: "/source-synthetique", files_detected: 0,
      candidates: [], excel_candidates: [], warnings: [] } }).catch(() => {})
  })
  try {
    await page.getByRole("button", { name: "Analyser le dossier" }).click()
    await expect(page.getByPlaceholder(/Chemin du dossier sur le serveur/i)).toBeDisabled()
    await expect(page.getByRole("button", { name: "Choisir des fichiers" })).toBeDisabled()
    liberer()
    await expect(page.getByText(/0 fichiers analysés/i)).toBeVisible()
    await expect(page.getByPlaceholder(/Chemin du dossier sur le serveur/i)).toBeEnabled()
  } finally { liberer() }
})

function stagingSynthetique(chemin: unknown, suffixe: string): string {
  if (typeof chemin !== "string" || !path.isAbsolute(chemin) ||
      path.basename(path.dirname(chemin)) !== "uploads" ||
      !new RegExp(`^[0-9a-f]{32}_${suffixe}$`).test(path.basename(chemin)) ||
      fs.realpathSync(chemin) !== chemin) throw new Error("Staging de test inattendu")
  return chemin
}

test("un fichier local déposé par CDP atteint le backend avec ses octets intacts", async ({ page }, info) => {
  await preparer(page)
  const fichier = info.outputPath("sonde.txt")
  fs.mkdirSync(path.dirname(fichier), { recursive: true })
  fs.writeFileSync(fichier, "CONTENU-SYNTHETIQUE")
  await page.getByTestId("drop-zone").evaluate((zone) => {
    zone.addEventListener("drop", (event) => {
      const e = event as DragEvent
      ;(window as Window & { preuveDepot?: unknown }).preuveDepot = {
        trusted: e.isTrusted, fichier: e.dataTransfer?.items[0]?.webkitGetAsEntry()?.isFile,
      }
    }, { once: true })
  })
  await deposerCheminParCDP(page, fichier, "drop-zone")
  await expect(page.getByText("sonde.txt", { exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as Window & { preuveDepot?: unknown }).preuveDepot))
    .toEqual({ trusted: true, fichier: true })
  const [reponse] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/imports/upload") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Envoyer et analyser" }).click(),
  ])
  expect(reponse.ok()).toBe(true)
  const resultat = await reponse.json()
  const staging = stagingSynthetique(resultat.staged_path, "upload")
  try {
    expect(resultat.files_detected).toBe(1)
    expect(fs.readFileSync(path.join(staging, "sonde.txt"), "utf8")).toBe("CONTENU-SYNTHETIQUE")
    expect(fs.readFileSync(fichier, "utf8")).toBe("CONTENU-SYNTHETIQUE")
  } finally {
    fs.rmSync(staging, { recursive: true })
  }
})

test("un dossier local multi-lots est parcouru par les vrais FileSystemEntry de Chromium", async ({ page }, info) => {
  await preparer(page)
  const racine = info.outputPath("racine")
  fs.mkdirSync(path.join(racine, "sous"), { recursive: true })
  for (let i = 0; i < 100; i++) fs.writeFileSync(path.join(racine, `piece-${String(i).padStart(3, "0")}.txt`), `piece-${i}`)
  fs.writeFileSync(path.join(racine, "fin.txt"), "fin")
  for (let i = 0; i < 20; i++) fs.writeFileSync(path.join(racine, "sous", `annexe-${String(i).padStart(3, "0")}.txt`), `annexe-${i}`)
  await page.getByTestId("drop-zone").evaluate((zone) => {
    zone.addEventListener("drop", (event) => {
      const e = event as DragEvent
      ;(window as Window & { preuveDepot?: unknown }).preuveDepot = {
        trusted: e.isTrusted, dossier: e.dataTransfer?.items[0]?.webkitGetAsEntry()?.isDirectory,
      }
    }, { once: true })
  })
  await deposerCheminParCDP(page, racine, "drop-zone")
  await expect(page.getByText(/121 fichiers prêts à envoyer/i)).toBeVisible()
  expect(await page.evaluate(() => (window as Window & { preuveDepot?: unknown }).preuveDepot))
    .toEqual({ trusted: true, dossier: true })
  const [reponse] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/imports/upload") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Envoyer et analyser" }).click(),
  ])
  expect(reponse.ok()).toBe(true)
  const resultat = await reponse.json()
  const staging = stagingSynthetique(resultat.staged_path, "racine")
  try {
    expect(resultat.files_detected).toBe(121)
    for (let i = 0; i < 100; i++) {
      const nom = `piece-${String(i).padStart(3, "0")}.txt`
      expect(fs.readFileSync(path.join(staging, "racine", nom), "utf8")).toBe(`piece-${i}`)
      expect(fs.readFileSync(path.join(racine, nom), "utf8")).toBe(`piece-${i}`)
    }
    expect(fs.readFileSync(path.join(staging, "racine", "fin.txt"), "utf8")).toBe("fin")
    expect(fs.readFileSync(path.join(racine, "fin.txt"), "utf8")).toBe("fin")
    for (let i = 0; i < 20; i++) {
      const nom = `annexe-${String(i).padStart(3, "0")}.txt`
      expect(fs.readFileSync(path.join(staging, "racine", "sous", nom), "utf8")).toBe(`annexe-${i}`)
      expect(fs.readFileSync(path.join(racine, "sous", nom), "utf8")).toBe(`annexe-${i}`)
    }
  } finally {
    fs.rmSync(staging, { recursive: true })
  }
})

test("un dépôt aplati sans entrées lisibles est refusé plutôt que considéré complet", async ({ page }) => {
  await preparer(page)
  await page.getByTestId("drop-zone").evaluate((zone) => {
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: {
      items: [], files: [new File(["partiel"], "partiel.txt")],
    } })
    zone.dispatchEvent(evenement)
  })
  await expect(page.getByText(/lecture du dossier impossible/i)).toBeVisible()
  await expect(page.getByText("partiel.txt", { exact: true })).toHaveCount(0)
})

test("une entrée de dossier non reconnue interrompt toute la sélection", async ({ page }) => {
  await preparer(page)
  await page.getByTestId("drop-zone").evaluate((zone) => {
    const inconnue = { isFile: false, isDirectory: false, name: "inconnue" }
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: {
      items: [{ kind: "file", webkitGetAsEntry: () => inconnue }], files: [],
    } })
    zone.dispatchEvent(evenement)
  })
  await expect(page.getByText(/lecture du dossier impossible/i)).toBeVisible()
  await expect(page.getByText(/fichiers? prêts? à envoyer/i)).toHaveCount(0)
})

test("un dossier vide est signalé sans autoriser d'envoi", async ({ page }) => {
  await preparer(page)
  await page.getByTestId("drop-zone").evaluate((zone) => {
    const dossier = { isFile: false, isDirectory: true, name: "vide",
      createReader: () => ({ readEntries: (ok: (items: unknown[]) => void) => ok([]) }) }
    const evenement = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(evenement, "dataTransfer", { value: {
      items: [{ kind: "file", webkitGetAsEntry: () => dossier }], files: [],
    } })
    zone.dispatchEvent(evenement)
  })
  await expect(page.getByText(/aucun fichier lisible dans ce dépôt/i)).toBeVisible()
  await expect(page.getByRole("button", { name: "Envoyer et analyser" })).toHaveCount(0)
})
