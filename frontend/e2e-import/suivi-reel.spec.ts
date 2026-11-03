import { expect, test, type Page } from "@playwright/test"
import { createHash } from "crypto"
import fs from "fs"
import { signIn } from "../e2e/helpers"

const BACKEND = "http://127.0.0.1:18135"
const TEST_HEADERS = { "X-Test-Token": "e2e-suivi-jeton-service", "X-SEAMTECH-TOKEN": "e2e-suivi-jeton-service" }

async function ouvrirImport(page: Page) {
  await signIn(page, "e2e-shared-password")
  await page.getByRole("button", { name: "Recherche avancée" }).click()
}

async function observations(page: Page) {
  const response = await page.request.get(`${BACKEND}/__suivi_test__/etat`, { headers: TEST_HEADERS })
  expect(response.status()).toBe(200)
  return response.json() as Promise<{ ouvertes: number; deconnectees: number }>
}

test("rechargement et annulation avec API, file Redis, base et worker séparé réels", async ({ page }) => {
  const cheminInfo = process.env.SEAMTECH_E2E_SUIVI_INFO
  expect(cheminInfo).toBeTruthy()
  const info = JSON.parse(fs.readFileSync(cheminInfo!, "utf8")) as {
    source: string; fichier: string; sha256: string; base: string
  }
  expect(info.base).toBe("postgresql")
  const sha = () => createHash("sha256").update(fs.readFileSync(info.fichier)).digest("hex")
  expect(sha()).toBe(info.sha256)

  await ouvrirImport(page)
  await page.getByPlaceholder(/Chemin du dossier sur le serveur/i).fill(info.source)
  let creations = 0
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/imports" && request.method() === "POST") creations++
  })
  const creation = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/imports" && response.request().method() === "POST")
  await page.getByRole("button", { name: /Importation rapide/i }).click()
  const reponse = await creation
  expect(reponse.status()).toBe(202)
  const accepte = await reponse.json()
  expect(accepte.durability).toBe("durable")
  expect(accepte.durable).toBe(true)
  expect(accepte.job_id).toMatch(/^[a-f0-9]{32}$/)
  const id = accepte.job_id as string
  await expect(page.getByText(id, { exact: true })).toBeVisible()

  await page.reload()
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await expect(page.getByText(id, { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Annuler", exact: true })).toBeVisible()
  const suivi = await page.request.get(`/api/imports/${id}`)
  expect(suivi.status()).toBe(200)
  expect((await suivi.json()).status).toBe("pending")

  const annulation = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/imports/${id}/cancel` && response.request().method() === "POST")
  await page.getByRole("button", { name: "Annuler", exact: true }).click()
  const annule = await annulation
  expect(annule.status()).toBe(200)
  expect((await annule.json()).status).toBe("cancelled")
  await expect(page.getByText(/^Import annulé\./i)).toBeVisible({ timeout: 10000 })
  expect(creations).toBe(1)

  // La tâche déjà livrée est ensuite consommée par un véritable autre
  // processus : il doit l'acquitter sans exécuter l'import annulé.
  const worker = await page.request.post(`${BACKEND}/__suivi_test__/worker`, {
    headers: TEST_HEADERS, timeout: 40000,
  })
  expect(worker.status()).toBe(200)
  const passage = await worker.json()
  expect(passage.processus_separe).toBe(true)
  expect(passage.avant).toEqual({ queue: 1, processing: 0, retry: 0, deadletter: 0 })
  expect(passage.apres).toEqual({ queue: 0, processing: 0, retry: 0, deadletter: 0 })
  expect(sha()).toBe(info.sha256)
  const final = await page.request.get(`/api/imports/${id}`)
  expect(final.status()).toBe(200)
  const corps = await final.json()
  expect(corps.status).toBe("cancelled")
  expect(corps.result).toBeNull()

  await page.getByRole("button", { name: "Catalogue d’archive" }).click()
  await page.getByRole("button", { name: "Recherche avancée" }).click()
  await expect(page.getByText(/^Import annulé\./i)).toBeVisible({ timeout: 10000 })
  expect(creations).toBe(1)
})

test("le proxy BFF borne la lecture d'un backend bloqué et ferme sa connexion", async ({ page }) => {
  await ouvrirImport(page)
  const avant = await observations(page)
  const debut = Date.now()
  const response = await page.request.get("/api/imports/suivi-bloque", { timeout: 13000 })
  const duree = Date.now() - debut
  expect(response.status()).toBe(504)
  expect(duree).toBeLessThan(12000)
  expect(duree).toBeGreaterThan(1000)
  await expect.poll(async () => (await observations(page)).deconnectees, { timeout: 5000 })
    .toBeGreaterThan(avant.deconnectees)
  await expect.poll(async () => (await observations(page)).ouvertes, { timeout: 5000 }).toBe(0)
})

test("un client qui interrompt le GET ne laisse pas le BFF attendre le backend", async ({ page }) => {
  await ouvrirImport(page)
  const avant = await observations(page)
  await page.evaluate(() => {
    const controleur = new AbortController()
    ;(window as Window & { controleurTest?: AbortController }).controleurTest = controleur
    void fetch("/api/imports/suivi-bloque", { signal: controleur.signal }).catch(() => undefined)
  })
  await expect.poll(async () => (await observations(page)).ouvertes, { timeout: 8000 }).toBe(1)
  await page.evaluate(() => (window as Window & { controleurTest?: AbortController }).controleurTest?.abort())
  await expect.poll(async () => (await observations(page)).deconnectees, { timeout: 5000 })
    .toBeGreaterThan(avant.deconnectees)
  await expect.poll(async () => (await observations(page)).ouvertes, { timeout: 5000 }).toBe(0)
})

test("la vérification nominative bloquée expire aussi sans annoncer une session révoquée", async ({ page }) => {
  await page.goto("/login?next=/fichiers")
  await page.getByLabel("Identifiant").fill("e2e-utilisateur-suivi")
  await page.getByLabel("Mot de passe").fill("e2e-suivi-mot-de-passe")
  await page.getByRole("button", { name: "Se connecter" }).click()
  await expect(page.getByTestId("archive-browser")).toBeVisible()
  const session = await page.request.get("/api/auth/session")
  expect(session.status()).toBe(200)
  expect((await session.json()).mode).toBe("nominatif")
  const avant = await observations(page)
  const controle = (bloquer: boolean) => page.request.post(`${BACKEND}/__suivi_test__/auth-bloquee`, {
    headers: TEST_HEADERS, data: { bloquer },
  })
  expect((await controle(true)).status()).toBe(200)
  try {
    const debut = Date.now()
    const response = await page.request.get("/api/imports/suivi-auth", { timeout: 13000 })
    expect(response.status()).toBe(504)
    expect(Date.now() - debut).toBeLessThan(12000)
    await expect.poll(async () => (await observations(page)).deconnectees, { timeout: 5000 })
      .toBeGreaterThan(avant.deconnectees)
  } finally {
    await controle(false)
  }
  const sessionToujoursValide = await page.request.get("/api/auth/session")
  expect(sessionToujoursValide.status()).toBe(200)
  expect((await sessionToujoursValide.json()).authenticated).toBe(true)
})

test("une panne de vérification nominative n'est pas confondue avec une révocation", async ({ page }) => {
  await page.goto("/login?next=/fichiers")
  await page.getByLabel("Identifiant").fill("e2e-utilisateur-suivi")
  await page.getByLabel("Mot de passe").fill("e2e-suivi-mot-de-passe")
  await page.getByRole("button", { name: "Se connecter" }).click()
  await expect(page.getByTestId("archive-browser")).toBeVisible()
  const controle = (panne: boolean) => page.request.post(`${BACKEND}/__suivi_test__/auth-bloquee`, {
    headers: TEST_HEADERS, data: { panne },
  })
  expect((await controle(true)).status()).toBe(200)
  try {
    const suivi = await page.request.get("/api/imports/suivi-auth")
    expect(suivi.status()).toBe(502)
  } finally {
    await controle(false)
  }
  const session = await page.request.get("/api/auth/session")
  expect(session.status()).toBe(200)
  expect((await session.json()).authenticated).toBe(true)
})

test("une session nominative révoquée reste refusée au suivi", async ({ page }) => {
  await page.goto("/login?next=/fichiers")
  await page.getByLabel("Identifiant").fill("e2e-utilisateur-suivi")
  await page.getByLabel("Mot de passe").fill("e2e-suivi-mot-de-passe")
  await page.getByRole("button", { name: "Se connecter" }).click()
  await expect(page.getByTestId("archive-browser")).toBeVisible()
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "seamtech_session")
  expect(cookie).toBeTruthy()
  await page.getByRole("button", { name: "Se déconnecter" }).click()
  await page.waitForURL(/\/login/)
  await page.context().addCookies([cookie!])
  const suivi = await page.request.get("/api/imports/suivi-auth")
  expect(suivi.status()).toBe(401)
})
