import { expect, test } from "@playwright/test"
import { mkdtemp, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { APPROBATEUR, LECTURE, signIn, signInWith } from "./helpers"

const jeton = "jeton_e2e_televersement_123456"

function etatSession(fichiersRecus: number, octetsRecus: number, statut = "en_cours") {
  return {
    token: jeton,
    idempotence_creation: 1,
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

  test("un échec réseau s'affiche en français pour l'opérateur (MAN-05)", async ({ page }) => {
    await signIn(page)

    const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-televersement-"))
    const racine = path.join(parent, "VOILES-DEMO")
    const cible = path.join(racine, "2031", "AFFAIRE-UN", "fiche_ffab.pdf")
    await mkdir(path.dirname(cible), { recursive: true })
    await writeFile(cible, "0123456789")

    await page.route("**/api/imports/dossier/televersements", async (route) => {
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ ...etatSession(0, 0), nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 10 }) })
    })
    await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
      await route.abort("failed")
    })

    try {
      await page.goto("/nouveau")
      await page.getByTestId("input-dossier-lot").setInputFiles(racine)
      await expect(page.getByTestId("resume-televersement")).toContainText("1 fichier(s)")
      await page.getByTestId("bouton-televerser-lot").click()
      const erreur = page.getByTestId("erreur-depot")
      await expect(erreur).toContainText("Connexion au serveur impossible ou interrompue", { timeout: 15000 })
      await expect(erreur).not.toContainText("Failed to fetch")
    } finally {
      await rm(parent, { recursive: true, force: true })
    }
  })
})

test("dossier complet vers PostgreSQL et stockage S3 émulé, lecture sans source locale", async ({ page }) => {
  test.skip(!process.env.SEAMTECH_E2E_S3_EMULE || !process.env.SEAMTECH_E2E_DATABASE_URL,
    "test opt-in : PostgreSQL et S3rver isolés requis")
  test.setTimeout(90000)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-reel-"))
  const racine = path.join(parent, "ARCHIVE-SYNTHETIQUE")
  const dossier = path.join(racine, "DOSSIER")
  const pdf = await readFile(path.resolve(__dirname, "../../sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"))
  await mkdir(dossier, { recursive: true })
  await writeFile(path.join(dossier, "fiche.pdf"), pdf)
  await writeFile(path.join(dossier, "annexe-e2e.txt"), "annexe synthetique e2e")
  try {
    await signInWith(page, APPROBATEUR)
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await expect(page.getByTestId("resume-televersement")).toContainText("2 fichier(s)")
    const creation = page.waitForResponse(response => response.url().endsWith("/api/imports/dossier/televersements") && response.request().method() === "POST")
    await page.getByTestId("bouton-televerser-lot").click()
    const session = await creation
    expect(session.status()).toBe(201)
    const token = (await session.json()).token as string
    await expect(page.getByTestId("suivi-lot")).toContainText("terminé", { timeout: 60000 })
    await expect(page.getByTestId("ligne-lot")).toContainText("traite")
    // Déplace les sources de test sans les détruire : la lecture doit passer par les objets.
    const sources = path.resolve(__dirname, "e2e-data/uploads/lots-televerses", token, "fichiers", "ARCHIVE-SYNTHETIQUE")
    await rename(sources, path.join(parent, "sources-conservees"))
    const liste = await page.request.get("/api/fiches/7792-SO/pieces")
    expect(liste.status()).toBe(200)
    const pieces = (await liste.json()).pieces as Array<{ id: number; name: string; is_primary_pdf?: boolean }>
    const principale = pieces.find(piece => piece.is_primary_pdf)
    const annexe = pieces.find(piece => piece.name === "annexe-e2e.txt")
    expect(principale).toBeTruthy()
    expect(annexe).toBeTruthy()
    const apercuPdf = await page.request.get(`/api/pieces/${principale!.id}/apercu`)
    const apercuAnnexe = await page.request.get(`/api/pieces/${annexe!.id}/apercu`)
    expect(apercuPdf.status()).toBe(200)
    expect(await apercuPdf.body()).toEqual(pdf)
    expect(apercuAnnexe.status()).toBe(200)
    expect(await apercuAnnexe.body()).toEqual(Buffer.from("annexe synthetique e2e"))
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("un dépôt partiellement illisible refuse le dossier entier et efface la sélection précédente", async ({ page }) => {
  await signIn(page)
  await page.goto("/nouveau")
  const zone = page.getByTestId("zone-depot-lot")
  await zone.evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "avant.pdf", file: (ok: (f: File) => void) => ok(new File(["a"], "avant.pdf")) }
    const e = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(e, "dataTransfer", { value: { items: [{ kind: "file", webkitGetAsEntry: () => fichier }], files: [] } })
    element.dispatchEvent(e)
  })
  await expect(page.getByTestId("resume-televersement")).toContainText("1 fichier(s)")
  await zone.evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "visible.pdf", file: (ok: (f: File) => void) => ok(new File(["a"], "visible.pdf")) }
    const e = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(e, "dataTransfer", { value: { items: [{ kind: "file", webkitGetAsEntry: () => fichier }, { kind: "file", webkitGetAsEntry: () => null }], files: [] } })
    element.dispatchEvent(e)
  })
  await expect(page.getByTestId("erreur-depot")).toContainText("impossible de lire toutes les entrées")
  await expect(page.getByTestId("resume-televersement")).toHaveCount(0)
  await expect(page.getByTestId("bouton-televerser-lot")).toHaveCount(0)
})

test("une entrée inconnue dans un sous-dossier ne produit jamais une sélection partielle", async ({ page }) => {
  await signIn(page)
  await page.goto("/nouveau")
  await page.getByTestId("zone-depot-lot").evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "visible.pdf", file: (ok: (f: File) => void) => ok(new File(["a"], "visible.pdf")) }
    const inconnue = { isFile: false, isDirectory: false, name: "inconnu" }
    const racine = { isFile: false, isDirectory: true, name: "racine", createReader: () => {
      let fini = false
      return { readEntries: (ok: (entries: unknown[]) => void) => { if (fini) ok([]); else { fini = true; ok([fichier, inconnue]) } } }
    } }
    const e = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(e, "dataTransfer", { value: { items: [{ kind: "file", webkitGetAsEntry: () => racine }], files: [] } })
    element.dispatchEvent(e)
  })
  await expect(page.getByTestId("erreur-depot")).toContainText("entrée de dossier non reconnue")
  await expect(page.getByTestId("resume-televersement")).toHaveCount(0)
})

test("un dossier aplati sans accès aux entrées est refusé au lieu de sembler complet", async ({ page }) => {
  await signIn(page)
  await page.goto("/nouveau")
  await page.getByTestId("zone-depot-lot").evaluate((element) => {
    const e = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(e, "dataTransfer", { value: { items: [], files: [new File(["a"], "partiel.pdf")] } })
    element.dispatchEvent(e)
  })
  await expect(page.getByTestId("erreur-depot")).toContainText("impossible de lire toutes les entrées")
  await expect(page.getByTestId("resume-televersement")).toHaveCount(0)
})

test("un second dépôt ne remplace pas l'analyse d'un dossier déjà en cours", async ({ page }) => {
  await signIn(page)
  await page.goto("/nouveau")
  const zone = page.getByTestId("zone-depot-lot")
  await zone.evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "premier.pdf", file: (ok: (f: File) => void) => setTimeout(() => ok(new File(["a"], "premier.pdf")), 1000) }
    const e = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(e, "dataTransfer", { value: { items: [{ kind: "file", webkitGetAsEntry: () => fichier }], files: [] } })
    element.dispatchEvent(e)
  })
  await expect(page.getByTestId("bouton-choisir-dossier-lot")).toBeDisabled()
  await zone.evaluate((element) => {
    const fichier = { isFile: true, isDirectory: false, name: "second.pdf", file: (ok: (f: File) => void) => ok(new File(["a"], "second.pdf")) }
    const e = new Event("drop", { bubbles: true, cancelable: true })
    Object.defineProperty(e, "dataTransfer", { value: { items: [{ kind: "file", webkitGetAsEntry: () => fichier }, { kind: "file", webkitGetAsEntry: () => fichier }], files: [] } })
    element.dispatchEvent(e)
  })
  await expect(page.getByTestId("resume-televersement")).toContainText("1 fichier(s)")
  await expect(page.getByTestId("erreur-depot")).toHaveCount(0)
})

test("deux dépôts dans le même événement ne permutent pas les dossiers", async ({ page }) => {
  await signIn(page)
  await page.goto("/nouveau")
  const secondLu = await page.getByTestId("zone-depot-lot").evaluate((element) => {
    let secondAppelee = false
    const entree = (nom: string, delai: number) => ({ isFile: true, isDirectory: false, name: nom, file: (ok: (f: File) => void) => { if (nom === "second.pdf") secondAppelee = true; setTimeout(() => ok(new File(["a"], nom)), delai) } })
    const envoyer = (elements: unknown[]) => {
      const evenement = new Event("drop", { bubbles: true, cancelable: true })
      Object.defineProperty(evenement, "dataTransfer", { value: { items: elements.map((item) => ({ kind: "file", webkitGetAsEntry: () => item })), files: [] } })
      element.dispatchEvent(evenement)
    }
    envoyer([entree("premier.pdf", 1000)])
    envoyer([entree("second.pdf", 10), entree("troisieme.pdf", 10)])
    return secondAppelee
  })
  expect(secondLu).toBe(false)
  await expect(page.getByTestId("resume-televersement")).toContainText("1 fichier(s)")
  await expect(page.getByTestId("erreur-depot")).toHaveCount(0)
})

test("une interruption d'envoi reprend la même session sans recréer de lot partiel", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-reprise-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  await writeFile(path.join(racine, "b.pdf"), "b")
  let creations = 0
  let echecsRestants = 3
  const recus = new Set<string>()
  const nomsEnvoi: string[] = []
  const session = (statut = "en_cours") => ({
    ...etatSession(recus.size, recus.size, statut), nom_racine: "DOSSIER-SYNTHESE",
    nombre_fichiers_attendus: 2, taille_totale_attendue_octets: 2,
    limites: { ...etatSession(0, 0).limites, fichiers_par_envoi_recommande: 1 },
  })
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    creations += 1
    await route.fulfill({ status: 201, json: session() })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => {
    await route.fulfill({ status: 200, json: session() })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
    const corps = route.request().postDataBuffer()?.toString("utf8") ?? ""
    const match = corps.match(/name="chemins"\r?\n\r?\n([^\r\n]+)/)
    expect(match).not.toBeNull()
    const noms = JSON.parse(match![1]) as string[]
    nomsEnvoi.push(noms[0])
    if (noms[0].endsWith("b.pdf") && echecsRestants-- > 0) {
      await route.fulfill({ status: 503, json: { detail: "panne synthétique" } })
      return
    }
    recus.add(noms[0])
    await route.fulfill({ status: 200, json: session() })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/terminer`, async (route) => {
    expect(recus.size).toBe(2)
    await route.fulfill({ status: 200, json: session("pret") })
  })
  await page.route("**/api/imports/dossier/lot", async (route) => {
    expect(recus.size).toBe(2)
    await route.fulfill({ status: 202, json: { id_lot: 4242, statut: "en_attente" } })
  })
  await page.route("**/api/lots/4242", async (route) => {
    await route.fulfill({ status: 200, json: { id_lot: 4242, statut: "termine", dossiers: [], restants: [], nb_traites: 1, nb_echecs: 0, nb_dossiers: 1 } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("panne synthétique", { timeout: 15000 })
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("message-televersement")).toContainText("lot #4242")
    expect(creations).toBe(1)
    expect(recus.size).toBe(2)
    expect(nomsEnvoi.filter((nom) => nom.endsWith("a.pdf"))).toHaveLength(2)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("la reprise après échec de finalisation refinalise avant de créer le lot", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-finalisation-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let finalisations = 0
  let lots = 0
  let recu = false
  const session = (statut = "en_cours") => ({
    ...etatSession(recu ? 1 : 0, recu ? 1 : 0, statut), nom_racine: "DOSSIER-SYNTHESE",
    nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1,
  })
  await page.route("**/api/imports/dossier/televersements", async (route) => { await route.fulfill({ status: 201, json: session() }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => { await route.fulfill({ status: 200, json: session() }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
    recu = true
    await route.fulfill({ status: 200, json: session() })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/terminer`, async (route) => {
    finalisations += 1
    if (finalisations === 1) await route.fulfill({ status: 503, json: { detail: "finalisation interrompue" } })
    else await route.fulfill({ status: 200, json: session("pret") })
  })
  await page.route("**/api/imports/dossier/lot", async (route) => {
    lots += 1
    if (finalisations < 2) await route.fulfill({ status: 409, json: { detail: "session non finalisée" } })
    else await route.fulfill({ status: 202, json: { id_lot: 4242 } })
  })
  await page.route("**/api/lots/4242", async (route) => {
    await route.fulfill({ status: 200, json: { id_lot: 4242, statut: "termine", dossiers: [], restants: [], nb_traites: 1, nb_echecs: 0, nb_dossiers: 1 } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("finalisation interrompue")
    await page.getByTestId("bouton-reprendre-creation-lot").click()
    await expect(page.getByTestId("message-televersement")).toContainText("lot #4242")
    expect(finalisations).toBe(2)
    expect(lots).toBe(1)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("réponse perdue après réception : reprise idempotente sur le vrai backend", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-reponse-perdue-"))
  const racine = path.join(parent, "DOSSIER-REPRISE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "premier synthétique")
  await writeFile(path.join(racine, "b.pdf"), "second synthétique")
  let jetonReel = ""
  let creations = 0
  let echecs = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    creations += 1
    const reponse = await route.fetch()
    expect(reponse.status()).toBe(201)
    const corps = await reponse.json()
    jetonReel = corps.token as string
    corps.limites.fichiers_par_envoi_recommande = 1
    await route.fulfill({ response: reponse, json: corps })
  })
  await page.route("**/api/imports/dossier/televersements/*/fichiers", async (route) => {
    const corps = route.request().postDataBuffer()?.toString("utf8") ?? ""
    const match = corps.match(/name="chemins"\r?\n\r?\n([^\r\n]+)/)
    expect(match).not.toBeNull()
    const noms = JSON.parse(match![1]) as string[]
    if (noms.some((nom) => nom.endsWith("b.pdf")) && echecs < 3) {
      echecs += 1
      if (echecs === 1) {
        // route.fetch peut remplacer les octets d'un fichier multipart par un
        // corps vide. Envoyer explicitement l'échantillon à la vraie API.
        const entetes = await route.request().allHeaders()
        const reponseServeur = await page.request.post(route.request().url(), {
          headers: { "x-csrf-token": entetes["x-csrf-token"], origin: entetes.origin },
          multipart: {
            chemins: JSON.stringify(noms),
            fichiers: { name: "b.pdf", mimeType: "application/pdf", buffer: await readFile(path.join(racine, "b.pdf")) },
          },
        })
        expect(reponseServeur.status()).toBe(200) // bien reçu, réponse ensuite perdue
        const stockage = path.resolve(__dirname, "e2e-data/uploads/lots-televerses", jetonReel, "fichiers", "DOSSIER-REPRISE")
        expect(await readFile(path.join(stockage, "b.pdf"), "utf8")).toBe("second synthétique")
      }
      await route.fulfill({ status: 503, json: { detail: "réponse synthétiquement perdue" } })
      return
    }
    await route.continue()
  })
  await page.route("**/api/imports/dossier/lot", async (route) => {
    await route.fulfill({ status: 202, json: { id_lot: 4242 } })
  })
  await page.route("**/api/lots/4242", async (route) => {
    await route.fulfill({ status: 200, json: { id_lot: 4242, statut: "termine", dossiers: [], restants: [], nb_traites: 1, nb_echecs: 0, nb_dossiers: 1 } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("réponse synthétiquement perdue", { timeout: 15000 })
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("message-televersement")).toContainText("lot #4242")
    expect(creations).toBe(1)
    const etat = await page.request.get(`/api/imports/dossier/televersements/${jetonReel}`)
    expect(etat.status()).toBe(200)
    expect(await etat.json()).toMatchObject({ statut: "pret", nombre_fichiers_recus: 2 })
    const fichiers = path.resolve(__dirname, "e2e-data/uploads/lots-televerses", jetonReel, "fichiers", "DOSSIER-REPRISE")
    expect(await readFile(path.join(fichiers, "a.pdf"), "utf8")).toBe("premier synthétique")
    expect(await readFile(path.join(fichiers, "b.pdf"), "utf8")).toBe("second synthétique")
  } finally {
    if (jetonReel) {
      const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "seamtech_csrf")?.value
      if (csrf) await page.request.delete(`/api/imports/dossier/televersements/${jetonReel}`, { headers: { "x-csrf-token": csrf } })
    }
    await rm(parent, { recursive: true, force: true })
  }
})

test("un échec d'annulation en cours conserve la session pour réessayer", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-annulation-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let liberer: () => void = () => {}
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  let tentativesAnnulation = 0
  const session = { ...etatSession(0, 0), nom_racine: "DOSSIER-SYNTHESE", nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1 }
  await page.route("**/api/imports/dossier/televersements", async (route) => { await route.fulfill({ status: 201, json: session }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
    await attente
    await route.abort().catch(() => {})
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => {
    tentativesAnnulation += 1
    await route.fulfill({ status: 503, json: { detail: "suppression temporairement impossible" } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    const envoi = page.waitForRequest((request) => request.url().includes(`/${jeton}/fichiers`))
    await page.getByTestId("bouton-televerser-lot").click()
    await envoi
    await page.getByTestId("bouton-annuler-televersement").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("suppression temporairement impossible")
    await expect(page.getByTestId("bouton-retirer-televersement")).toContainText("Annuler la session")
    const reponseSuppression = page.waitForResponse((response) => response.url().endsWith(`/${jeton}`) && response.request().method() === "DELETE")
    await page.getByTestId("bouton-retirer-televersement").click()
    await reponseSuppression
    expect(tentativesAnnulation).toBe(2)
  } finally {
    liberer()
    await rm(parent, { recursive: true, force: true })
  }
})

test("une finalisation effectuée avec réponse perdue ne renvoie pas les fichiers d'une session prête", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-finalisation-perdue-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let pret = false
  let envois = 0
  let finalisations = 0
  const session = () => ({
    ...etatSession(envois ? 1 : 0, envois ? 1 : 0, pret ? "pret" : "en_cours"), nom_racine: "DOSSIER-SYNTHESE",
    nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1,
  })
  await page.route("**/api/imports/dossier/televersements", async (route) => { await route.fulfill({ status: 201, json: session() }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => { await route.fulfill({ status: 200, json: session() }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
    envois += 1
    await route.fulfill({ status: 200, json: session() })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/terminer`, async (route) => {
    finalisations += 1
    pret = true
    if (finalisations === 1) await route.fulfill({ status: 503, json: { detail: "réponse perdue" } })
    else await route.fulfill({ status: 200, json: session() })
  })
  await page.route("**/api/imports/dossier/lot", async (route) => { await route.fulfill({ status: 202, json: { id_lot: 4242 } }) })
  await page.route("**/api/lots/4242", async (route) => {
    await route.fulfill({ status: 200, json: { id_lot: 4242, statut: "termine", dossiers: [], restants: [], nb_traites: 1, nb_echecs: 0, nb_dossiers: 1 } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("réponse perdue")
    await page.getByTestId("bouton-reprendre-creation-lot").click()
    await expect(page.getByTestId("message-televersement")).toContainText("lot #4242")
    expect(envois).toBe(1)
    expect(finalisations).toBe(2)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("annulation pendant création sans réponse ne prétend pas qu'aucune session n'existe", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-creation-incertaine-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let liberer: () => void = () => {}
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  let creations = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    creations += 1
    if (creations === 1) {
      await attente
      await route.abort().catch(() => {})
    } else {
      await route.fulfill({ status: 503, json: { detail: "réseau indisponible" } })
    }
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    const creation = page.waitForRequest((request) => request.url().endsWith("/televersements") && request.method() === "POST")
    await page.getByTestId("bouton-televerser-lot").click()
    await creation
    await page.getByTestId("bouton-annuler-televersement").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("état de création de session inconnu")
    await page.getByTestId("bouton-retirer-televersement").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("état de création de session inconnu")
    await expect(page.getByTestId("message-televersement")).toHaveCount(0)
  } finally {
    liberer()
    await rm(parent, { recursive: true, force: true })
  }
})

test("la reprise refuse une session qui ne correspond pas à la sélection", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-session-incoherente-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let creations = 0
  let envois = 0
  const session = { ...etatSession(0, 0), nom_racine: "DOSSIER-SYNTHESE", nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1 }
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    creations += 1
    await route.fulfill({ status: 201, json: session })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => {
    envois += 1
    await route.fulfill({ status: 503, json: { detail: "panne synthétique" } })
  })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => {
    await route.fulfill({ status: 200, json: { ...session, taille_totale_attendue_octets: 99 } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("panne synthétique", { timeout: 15000 })
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("session ne correspond plus")
    expect(creations).toBe(1)
    expect(envois).toBe(3)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("une nouvelle sélection ne fait pas oublier une session inachevée", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-selection-session-"))
  const ancien = path.join(parent, "DOSSIER-ANCIEN")
  const nouveau = path.join(parent, "DOSSIER-NOUVEAU")
  await mkdir(ancien, { recursive: true })
  await mkdir(nouveau, { recursive: true })
  await writeFile(path.join(ancien, "a.pdf"), "a")
  await writeFile(path.join(nouveau, "b.pdf"), "b")
  const session = { ...etatSession(0, 0), nom_racine: "DOSSIER-ANCIEN", nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1 }
  let annulations = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => { await route.fulfill({ status: 201, json: session }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => { await route.fulfill({ status: 503, json: { detail: "panne synthétique" } }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => {
    if (route.request().method() === "DELETE") annulations += 1
    await route.fulfill({ status: 200, json: session })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(ancien)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("panne synthétique", { timeout: 15000 })
    await page.getByTestId("input-dossier-lot").setInputFiles(nouveau)
    await expect(page.getByTestId("resume-televersement")).toContainText("DOSSIER-ANCIEN")
    await expect(page.getByTestId("erreur-depot")).toContainText("session en cours")
    await page.getByTestId("zone-depot-lot").dispatchEvent("drop", { dataTransfer: await page.evaluateHandle(() => new DataTransfer()) })
    await expect(page.getByTestId("resume-televersement")).toContainText("DOSSIER-ANCIEN")
    await page.getByTestId("bouton-retirer-televersement").click()
    await expect(page.getByTestId("resume-televersement")).toHaveCount(0)
    expect(annulations).toBe(1)
    await page.getByTestId("input-dossier-lot").setInputFiles(nouveau)
    await expect(page.getByTestId("resume-televersement")).toContainText("DOSSIER-NOUVEAU")
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("une réponse perdue à la création retrouve la session réelle sans en créer une autre", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-creation-perdue-"))
  const racine = path.join(parent, "DOSSIER-CREATION")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "synthese")
  const cles: string[] = []
  const jetons: string[] = []
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    const corps = route.request().postDataJSON() as { cle_idempotence?: string }
    cles.push(corps.cle_idempotence ?? "")
    const reponse = await route.fetch()
    expect(reponse.status()).toBe(201)
    const json = await reponse.json()
    jetons.push(json.token as string)
    if (jetons.length === 1) await route.fulfill({ status: 503, json: { detail: "réponse de création perdue" } })
    else await route.fulfill({ response: reponse })
  })
  await page.route("**/api/imports/dossier/lot", async (route) => { await route.fulfill({ status: 202, json: { id_lot: 4242 } }) })
  await page.route("**/api/lots/4242", async (route) => {
    await route.fulfill({ status: 200, json: { id_lot: 4242, statut: "termine", dossiers: [], restants: [], nb_traites: 1, nb_echecs: 0, nb_dossiers: 1 } })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("réponse de création perdue")
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("message-televersement")).toContainText("lot #4242")
    expect(cles).toHaveLength(2)
    expect(cles[0]).toMatch(/^[0-9a-f]{64}$/)
    expect(cles[0]).toBe(cles[1])
    expect(jetons[0]).toBe(jetons[1])
    const fichiers = path.resolve(__dirname, "e2e-data/uploads/lots-televerses", jetons[0], "fichiers", "DOSSIER-CREATION")
    expect(await readFile(path.join(fichiers, "a.pdf"), "utf8")).toBe("synthese")
  } finally {
    const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "seamtech_csrf")?.value
    for (const token of new Set(jetons)) if (csrf) await page.request.delete(`/api/imports/dossier/televersements/${token}`, { headers: { "x-csrf-token": csrf } })
    await rm(parent, { recursive: true, force: true })
  }
})

test("annuler pendant la réponse de création retrouve et supprime la vraie session", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-annuler-creation-"))
  const racine = path.join(parent, "DOSSIER-ANNULATION")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let jetonReel = ""
  let corpsCreation: Record<string, unknown> | null = null
  let liberer: () => void = () => {}
  let recue: () => void = () => {}
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  const serveurACree = new Promise<void>((resolve) => { recue = resolve })
  let demandes = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    demandes += 1
    if (demandes > 1) { await route.continue(); return }
    corpsCreation = route.request().postDataJSON() as Record<string, unknown>
    const response = await route.fetch()
    expect(response.status()).toBe(201)
    jetonReel = (await response.json()).token as string
    recue()
    await attente
    await route.abort().catch(() => {})
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await serveurACree
    await page.getByTestId("bouton-annuler-televersement").click()
    await expect(page.getByTestId("message-televersement")).toContainText("supprimés")
    const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "seamtech_csrf")!.value
    const etat = await page.request.get(`/api/imports/dossier/televersements/${jetonReel}`)
    expect(etat.status()).toBe(404)
    const rejoue = await page.request.post("/api/imports/dossier/televersements", { headers: { "x-csrf-token": csrf }, data: corpsCreation! })
    expect(rejoue.status()).toBe(409)
  } finally {
    liberer()
    if (jetonReel) {
      const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "seamtech_csrf")?.value
      if (csrf) await page.request.delete(`/api/imports/dossier/televersements/${jetonReel}`, { headers: { "x-csrf-token": csrf } })
    }
    await rm(parent, { recursive: true, force: true })
  }
})

test("un serveur sans confirmation d'idempotence ne reçoit pas de seconde création", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-protocole-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let creations = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => {
    creations += 1
    const session = { ...etatSession(0, 0), nom_racine: "DOSSIER-SYNTHESE", nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1 }
    const { idempotence_creation: _ignore, ...ancienneReponse } = session
    await route.fulfill({ status: 201, json: ancienneReponse })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("non idempotente")
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("nouvelle création interdite")
    await page.getByTestId("bouton-retirer-televersement").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("nouvelle création interdite")
    expect(creations).toBe(1)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("sans capacité de création idempotente aucun POST de création n'est émis", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-capacite-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  const autre = path.join(parent, "DOSSIER-AUTRE")
  await mkdir(racine, { recursive: true })
  await mkdir(autre, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  await writeFile(path.join(autre, "b.pdf"), "b")
  let creations = 0
  await page.route("**/api/imports/dossier/televersements/capacites", async (route) => { await route.fulfill({ status: 200, json: {} }) })
  await page.route("**/api/imports/dossier/televersements", async (route) => { creations += 1; await route.abort() })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("ne garantit pas la reprise")
    expect(creations).toBe(0)
    await page.getByTestId("input-dossier-lot").setInputFiles(autre)
    await expect(page.getByTestId("resume-televersement")).toContainText("DOSSIER-AUTRE")
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("sélection et nouvel envoi restent bloqués pendant la suppression de session", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-suppression-en-cours-"))
  const ancien = path.join(parent, "DOSSIER-ANCIEN")
  const nouveau = path.join(parent, "DOSSIER-NOUVEAU")
  await mkdir(ancien, { recursive: true })
  await mkdir(nouveau, { recursive: true })
  await writeFile(path.join(ancien, "a.pdf"), "a")
  await writeFile(path.join(nouveau, "b.pdf"), "b")
  const session = { ...etatSession(0, 0), nom_racine: "DOSSIER-ANCIEN", nombre_fichiers_attendus: 1, taille_totale_attendue_octets: 1 }
  let liberer: () => void = () => {}
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  let creations = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => { creations += 1; await route.fulfill({ status: 201, json: session }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}/fichiers`, async (route) => { await route.fulfill({ status: 503, json: { detail: "panne synthétique" } }) })
  await page.route(`**/api/imports/dossier/televersements/${jeton}`, async (route) => {
    if (route.request().method() === "DELETE") await attente
    await route.fulfill({ status: 200, json: session })
  })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(ancien)
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("panne synthétique", { timeout: 15000 })
    const suppression = page.waitForRequest((r) => r.url().endsWith(`/${jeton}`) && r.method() === "DELETE")
    await page.getByTestId("bouton-retirer-televersement").click()
    await suppression
    await page.getByTestId("input-dossier-lot").setInputFiles(nouveau)
    await expect(page.getByTestId("erreur-depot")).toContainText("pendant l'annulation")
    await expect(page.getByTestId("resume-televersement")).toContainText("DOSSIER-ANCIEN")
    await page.getByTestId("bouton-televerser-lot").click()
    expect(creations).toBe(1)
    liberer()
    await expect(page.getByTestId("resume-televersement")).toHaveCount(0)
    await page.getByTestId("input-dossier-lot").setInputFiles(nouveau)
    await expect(page.getByTestId("resume-televersement")).toContainText("DOSSIER-NOUVEAU")
  } finally {
    liberer()
    await rm(parent, { recursive: true, force: true })
  }
})

test("capacité de téléversement BFF : sans session et rôle lecture refusés", async ({ page }) => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "nécessite les comptes nominatifs PostgreSQL de test")
  const sansSession = await page.request.get("/api/imports/dossier/televersements/capacites")
  expect(sansSession.status()).toBe(401)
  await signInWith(page, LECTURE)
  const lecture = await page.request.get("/api/imports/dossier/televersements/capacites")
  expect(lecture.status()).toBe(403)
})

test("un générateur aléatoire indisponible bloque la création avec une erreur visible", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-aleatoire-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let creations = 0
  await page.route("**/api/imports/dossier/televersements", async (route) => { creations += 1; await route.abort() })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    await page.evaluate(() => { Object.defineProperty(crypto, "getRandomValues", { value: () => { throw new Error("synthétique") } }) })
    await page.getByTestId("bouton-televerser-lot").click()
    await expect(page.getByTestId("erreur-depot")).toContainText("générateur aléatoire sécurisé")
    expect(creations).toBe(0)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("annuler avant la fin du contrôle de capacité ne crée pas de session", async ({ page }) => {
  await signIn(page)
  const parent = await mkdtemp(path.join(os.tmpdir(), "seamtech-e2e-capacite-annulee-"))
  const racine = path.join(parent, "DOSSIER-SYNTHESE")
  await mkdir(racine, { recursive: true })
  await writeFile(path.join(racine, "a.pdf"), "a")
  let liberer: () => void = () => {}
  const attente = new Promise<void>((resolve) => { liberer = resolve })
  let creations = 0
  await page.route("**/api/imports/dossier/televersements/capacites", async (route) => { await attente; await route.abort().catch(() => {}) })
  await page.route("**/api/imports/dossier/televersements", async (route) => { creations += 1; await route.abort() })
  try {
    await page.goto("/nouveau")
    await page.getByTestId("input-dossier-lot").setInputFiles(racine)
    const controle = page.waitForRequest((req) => req.url().endsWith("/televersements/capacites"))
    await page.getByTestId("bouton-televerser-lot").click()
    await controle
    await page.getByTestId("bouton-annuler-televersement").click()
    await expect(page.getByTestId("message-televersement")).toContainText("avant toute création de session")
    expect(creations).toBe(0)
  } finally {
    liberer()
    await rm(parent, { recursive: true, force: true })
  }
})
