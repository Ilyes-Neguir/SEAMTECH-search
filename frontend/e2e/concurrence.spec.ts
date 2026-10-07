/**
 * CONCURRENCE — plusieurs postes, plusieurs SESSIONS, une seule vérité.
 *
 * Exigence de la revue indépendante du 2026-10-07 : « les tests séquentiels ne
 * prouvent PAS la concurrence ». Ici, deux (ou trois) sessions de navigateur
 * INDÉPENDANTES travaillent en même temps contre le vrai backend et la vraie
 * base PostgreSQL :
 *
 *   1. deux postes ouvrent la MÊME fiche à la même révision ; A enregistre,
 *      B soumet une correction périmée → conflit détecté (409), la valeur de A
 *      est CONSERVÉE en base, B comprend et se reprend sans rien perdre ;
 *   2. un poste IMPORTE pendant qu'un autre CHERCHE et TÉLÉCHARGE : les deux
 *      aboutissent, et le contenu réellement téléchargé est vérifié (octets
 *      PDF), pas seulement un message d'interface ;
 *   3. la session d'un poste EXPIRE en pleine correction : aucun faux succès,
 *      et l'état en base est INCHANGÉ ;
 *   4. un opérateur se voit REFUSER par le BACKEND une action réservée à
 *      l'administrateur (l'administrateur, lui, obtient 200) — la règle est
 *      appliquée côté serveur, pas seulement cachée dans l'interface.
 *
 * Chaque vérification d'état passe par la BASE (via l'API authentifiée, qui la
 * lit) : un message d'interface ne prouve rien à lui seul.
 *
 * Mode « live » uniquement : sans `SEAMTECH_E2E_DATABASE_URL`, la pile tourne
 * sur SQLite, qui ne porte NI les fiches NI la validation humaine (§17.1).
 */

import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test"
import path from "node:path"
import { ADMIN, OPERATEUR, signInWith } from "./helpers"

test.describe("concurrence entre postes (sessions indépendantes)", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "concurrence : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL vivant + fiches réelles)",
  )

  const RACINE = path.resolve(__dirname, "../..")

  /** Deux sessions de navigateur RÉELLEMENT distinctes (cookies séparés). */
  async function deuxPostes(browser: Browser): Promise<{
    ctxA: BrowserContext
    ctxB: BrowserContext
    posteA: Page
    posteB: Page
  }> {
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const posteA = await ctxA.newPage()
    const posteB = await ctxB.newPage()
    await signInWith(posteA, OPERATEUR) // poste A : opérateur nominatif
    await signInWith(posteB, ADMIN) // poste B : administrateur nominatif
    return { ctxA, ctxB, posteA, posteB }
  }

  /** Codes des fiches présentes dans la file de validation. */
  async function codesDeLaFile(poste: Page): Promise<string[]> {
    await poste.goto("/validation")
    const file = poste.getByTestId("file-validation")
    await expect(file).toBeVisible()
    await file.getByTestId("code-fiche").first().waitFor({ timeout: 15000 })
    return file.locator("li[data-code]").evaluateAll((lignes) =>
      lignes.map((li) => li.getAttribute("data-code") ?? ""),
    )
  }

  /** Ouvre une fiche PRÉCISE dans la vue validation, depuis N'IMPORTE quelle
   *  page : le poste navigue lui-même vers la file. Sans cette navigation, un
   *  poste resté sur l'écran d'accueil (cas réel : il vient de se connecter)
   *  ne trouverait aucune ligne — c'est ce que la CI a montré au premier
   *  passage (deux scénarios en échec sur `li[data-code]` introuvable). */
  async function ouvrirFiche(poste: Page, code: string): Promise<void> {
    if (!poste.url().includes("/validation")) {
      await poste.goto("/validation")
      await expect(poste.getByTestId("file-validation")).toBeVisible()
    }
    const ligne = poste.locator(`li[data-code="${code}"]`)
    await expect(ligne).toBeVisible({ timeout: 15000 })
    await ligne.getByTestId("code-fiche").click()
    await expect(poste.getByTestId("titre-fiche")).toHaveText(code)
    // La révision est chargée ASYNCHRONEMENT : tant qu'elle n'est pas là,
    // l'écran enregistrerait SANS protection. On attend qu'elle soit armée —
    // c'est une condition de justesse du scénario, pas une coquetterie.
    await expect(poste.getByTestId("validation-app")).toHaveAttribute("data-fiche", code)
    await expect(poste.getByTestId("validation-app")).toHaveAttribute("data-revision", /^[0-9]+$/)
  }

  /** Champ RÉELLEMENT présent sur la fiche, et son code.

   * Le premier passage CI a montré l'erreur à ne pas refaire : le test
   * supposait un champ nommé « materiau ». Les fiches réelles portent
   * `materiau.tissu_principal` (famille `materiau.`), et les autres familles
   * suivent le même préfixe — le sélecteur exact n'est donc PAS devinable.
   * On choisit donc, dans l'ordre : le champ textile connu, la première
   * matière, une désignation libre, et en dernier recours le premier champ
   * corrigeable qui n'est pas le CODE de la fiche (écrire « MATERIAU-A » dans
   * un code n'aurait aucun sens métier).
   */
  async function choisirChamp(poste: Page): Promise<{ selecteur: string; champ: string }> {
    // Les champs PROVENANT DU PDF (`data-zone="true"`) viennent d'abord : ils
    // portent une seule valeur sur la fiche. Le champ « matière » d'une fiche
    // réelle, lui, peut apparaître PLUSIEURS FOIS (un rang par zone du plan) et
    // partager alors le même `data-testid` : une preuve « la valeur du collègue
    // est intacte » y serait ambiguë, et l'interface de correction — une valeur
    // à la fois — ne saurait pas quel rang viser.
    const candidats = [
      'input[data-testid="champ-materiau.tissu_principal"][data-zone="true"]',
      'input[data-testid^="champ-"][data-zone="true"]',
      'input[data-testid="champ-materiau.tissu_principal"]',
      'input[data-testid="champ-fiche.designation"]',
      'input[data-testid^="champ-"]:not([data-testid="champ-fiche.code"])',
    ]
    for (const selecteur of candidats) {
      const premier = poste.locator(selecteur).first()
      if ((await poste.locator(selecteur).count()) === 0) continue
      const testid = (await premier.getAttribute("data-testid")) ?? ""
      if (!testid.startsWith("champ-")) continue
      // L'identifiant doit désigner UN SEUL champ de l'écran : sinon le champ
      // existe en plusieurs rangs (même `data-testid`), la preuve « la valeur
      // du collègue est intacte » porterait peut-être sur un autre rang que
      // celui lu en base, et un sélecteur de PRÉFIXE ferait échouer Playwright
      // en mode strict (« resolved to N elements ») — un flake, pas une preuve.
      const exact = `input[data-testid="${testid}"]`
      if ((await poste.locator(exact).count()) !== 1) continue
      // On rend TOUJOURS le sélecteur exact : la liste de candidats sert à
      // choisir, jamais à interroger l'écran.
      return { selecteur: exact, champ: testid.slice("champ-".length) }
    }
    throw new Error("aucun champ corrigeable UNIQUE (et présent) trouvé sur la fiche")
  }

  /** Valeur d'un champ et révision lue depuis la BASE par l'API authentifiée. */
  async function etatEnBase(
    poste: Page,
    code: string,
    champ: string,
  ): Promise<{ valeur: string | null; revision: number | null }> {
    const champs = await poste.request.get(`/api/fiches/${encodeURIComponent(code)}/champs`)
    expect(champs.status(), await champs.text()).toBe(200)
    const lignes = (await champs.json()) as Array<{ champ: string; valeur_normalisee: string | null }>
    const detail = await poste.request.get(`/api/fiches/${encodeURIComponent(code)}`)
    expect(detail.status(), await detail.text()).toBe(200)
    const corps = (await detail.json()) as { revision?: number }
    const ligne = lignes.find((l) => l.champ === champ)
    return { valeur: ligne?.valeur_normalisee ?? null, revision: corps.revision ?? null }
  }

  test("deux postes, même fiche : A enregistre, B est arrêté et se reprend", async ({ browser }) => {
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      // Une fiche SYNTHÉTIQUE de la file : on ne touche pas à la vraie fiche
      // 7792-SO (d'autres épreuves vivent dessus), ni à sa copie « BIS-7792 ».
      const codes = (await codesDeLaFile(posteA)).filter((code) => code && !code.includes("7792"))
      expect(codes.length, `file de validation inattendue : ${codes.join(", ")}`).toBeGreaterThan(0)
      const code = codes[codes.length - 1]

      // LES DEUX POSTES OUVRENT LA MÊME FICHE, À LA MÊME RÉVISION.
      await Promise.all([ouvrirFiche(posteA, code), ouvrirFiche(posteB, code)])
      const { selecteur, champ } = await choisirChamp(posteA)
      const etatInitial = await etatEnBase(posteA, code, champ)
      await expect(posteA.locator(selecteur)).toBeVisible()
      await expect(posteB.locator(selecteur)).toBeVisible()
      const valeurInitiale = await posteA.locator(selecteur).inputValue()
      expect(await posteB.locator(selecteur).inputValue()).toBe(valeurInitiale)

      // A CORRIGE ET ENREGISTRE.
      await posteA.locator(selecteur).fill("MATERIAU-POSTE-A")
      await posteA.getByTestId("bouton-corriger").first().click()
      await expect(posteA.getByTestId("message-ok")).toContainText("corrigé", { timeout: 15000 })
      const apresA = await etatEnBase(posteA, code, champ)
      expect(apresA.valeur).toBe("MATERIAU-POSTE-A")
      expect(apresA.revision).toBe((etatInitial.revision ?? 0) + 1)

      // B, RESTÉ SUR L'ÉTAT D'OUVERTURE, SOUMET SA CORRECTION.
      await posteB.locator(selecteur).fill("MATERIAU-POSTE-B")
      await posteB.getByTestId("bouton-corriger").first().click()

      const conflit = posteB.getByTestId("conflit-revision")
      await expect(conflit).toBeVisible({ timeout: 15000 })
      await expect(conflit).toContainText("NON appliquée")
      // La valeur du collègue est MONTRÉE, avec son auteur : B comprend.
      await expect(posteB.getByTestId("conflit-valeur")).toHaveText("MATERIAU-POSTE-A")
      await expect(posteB.getByTestId("conflit-auteur")).toHaveText(OPERATEUR.identifiant)
      await expect(posteB.getByTestId("conflit-revisions")).toContainText("→")
      // AUCUN faux succès : le bandeau vert ne doit pas apparaître.
      await expect(posteB.getByTestId("message-ok")).toHaveCount(0)

      // PREUVE EN BASE : le travail de A est intact, rien de B n'a été écrit.
      const apresB = await etatEnBase(posteA, code, champ)
      expect(apresB.valeur).toBe("MATERIAU-POSTE-A")
      expect(apresB.revision).toBe(apresA.revision)

      // B SE REPREND : rechargement puis nouvelle correction, avec la bonne
      // révision — aucun travail perdu, aucun doublon silencieux.
      await posteB.getByTestId("bouton-recharger-fiche").click()
      await expect(posteB.getByTestId("conflit-revision")).toHaveCount(0, { timeout: 15000 })
      await expect(posteB.locator(selecteur)).toHaveValue("MATERIAU-POSTE-A", { timeout: 15000 })
      // ET la révision rechargée est bien CELLE DU GAGNANT : sans cette
      // attente, la nouvelle correction pourrait partir SANS verrou (le
      // chargement de la révision est une seconde requête) et le scénario
      // passerait en prouvant moins que ce qu'il annonce.
      await expect(posteB.getByTestId("validation-app")).toHaveAttribute(
        "data-revision",
        String(apresA.revision),
        { timeout: 15000 },
      )
      await posteB.locator(selecteur).fill("MATERIAU-POSTE-B")
      await posteB.getByTestId("bouton-corriger").first().click()
      await expect(posteB.getByTestId("message-ok")).toContainText("corrigé", { timeout: 15000 })
      const apresReprise = await etatEnBase(posteA, code, champ)
      expect(apresReprise.valeur).toBe("MATERIAU-POSTE-B")
      expect(apresReprise.revision).toBe((apresA.revision ?? 0) + 1)
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test("un poste importe pendant qu'un autre cherche et télécharge", async ({ browser }) => {
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      // POSTE A — IMPORT RÉEL d'un dossier par l'interface. `CLIENT-123` n'est
      // pas déposé par le seed : l'import crée donc bien une fiche de plus,
      // pendant que B travaille.
      const dossier = path.join(RACINE, "sample_data", "CLIENT-123")
      await posteA.getByRole("button", { name: "Recherche avancée" }).click()
      const champDossier = posteA.getByPlaceholder(/Chemin du dossier sur le serveur/i)
      await expect(champDossier).toBeVisible({ timeout: 15000 })
      await champDossier.fill(dossier)
      await posteA.getByRole("button", { name: /Importation rapide/i }).click()

      // POSTE B — PENDANT CE TEMPS : recherche par dimension + téléchargement
      // RÉEL du PDF de la fiche 7792-SO (contenu vérifié, pas un lien).
      await posteB.goto(`/recherche?q=${encodeURIComponent("6,60")}`)
      const ouvrir = posteB.getByRole("link", { name: /Ouvrir la fiche 7792-SO/ })
      await expect(ouvrir).toBeVisible({ timeout: 30000 })
      await ouvrir.click()
      await expect(posteB.getByTestId("fiche-code")).toHaveText("7792-SO", { timeout: 30000 })

      const lien = posteB.getByRole("link", { name: /Télécharger .*\.pdf/i }).first()
      await expect(lien).toBeVisible({ timeout: 30000 })
      const [telechargement] = await Promise.all([posteB.waitForEvent("download"), lien.click()])
      const chemin = await telechargement.path()
      expect(chemin, "le téléchargement n'a produit aucun fichier").not.toBeNull()
      const { readFileSync } = await import("node:fs")
      const octets = readFileSync(chemin!)
      expect(octets.length).toBeGreaterThan(1000)
      expect(octets.subarray(0, 4).toString("latin1")).toBe("%PDF")

      // L'IMPORT DE A ABOUTIT MALGRÉ LA CHARGE SIMULTANÉE.
      await expect(posteA.getByText(/État :/i)).toBeVisible({ timeout: 60000 })
      await expect(posteA.getByText(/REF-2026-CLIENT123/i)).toBeVisible({ timeout: 60000 })

      // Et B, toujours connecté, obtient une réponse complète (sa session et sa
      // recherche n'ont pas été cassées par l'import concurrent).
      const reprise = await posteB.request.get(`/api/fiches/7792-SO/champs`)
      expect(reprise.status(), await reprise.text()).toBe(200)
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test("session expirée en pleine correction : aucun faux succès, état inchangé", async ({ browser }) => {
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      const codes = (await codesDeLaFile(posteA)).filter((code) => code && !code.includes("7792"))
      expect(codes.length).toBeGreaterThan(0)
      const code = codes[0]

      await ouvrirFiche(posteB, code)
      const { selecteur, champ } = await choisirChamp(posteB)
      await expect(posteB.locator(selecteur)).toBeVisible()
      const avant = await etatEnBase(posteB, code, champ)

      // LA SESSION EXPIRE : le cookie disparaît (déconnexion/révocation).
      await ctxB.clearCookies()

      await posteB.locator(selecteur).fill("MATERIAU-SESSION-MORTE")
      await posteB.getByTestId("bouton-corriger").first().click()

      // Aucun faux succès : soit la porte de session s'affiche, soit une erreur
      // explicite — jamais le bandeau « corrigé ».
      await expect(posteB.getByTestId("message-ok")).toHaveCount(0)
      await posteB.waitForURL(/\/login/, { timeout: 20000 })

      // ET L'ÉTAT EN BASE EST INCHANGÉ : la correction n'a pas été appliquée.
      const apres = await etatEnBase(posteA, code, champ)
      expect(apres.valeur).toBe(avant.valeur)
      expect(apres.revision).toBe(avant.revision)
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test("un opérateur est refusé par le BACKEND sur une action d'administrateur", async ({ browser }) => {
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      const identifiant = `intrus-e2e-${Date.now()}`

      // POSTE A (opérateur) : la création de compte est réservée à l'administrateur.
      const refusLecture = await posteA.request.get("/api/auth/utilisateurs")
      expect(refusLecture.status()).toBe(403)
      const refusEcriture = await posteA.request.post("/api/auth/utilisateurs", {
        data: { identifiant, nom: "Intrus e2e", role: "operateur", mot_de_passe: "mot-de-passe-intrus" },
      })
      expect(refusEcriture.status()).toBe(403)
      const detail = (await refusEcriture.json()) as { detail?: string }
      expect(String(detail.detail ?? "")).toMatch(/administrateur/)

      // POSTE B (administrateur) : la MÊME requête passe — c'est bien le rôle,
      // et seulement lui, qui décide (côté backend).
      const autorise = await posteB.request.get("/api/auth/utilisateurs")
      expect(autorise.status(), await autorise.text()).toBe(200)

      // PREUVE EN BASE : le compte refusé n'existe pas.
      const liste = (await autorise.json()) as Array<{ identifiant?: string }>
      expect(liste.some((compte) => compte.identifiant === identifiant)).toBe(false)
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
