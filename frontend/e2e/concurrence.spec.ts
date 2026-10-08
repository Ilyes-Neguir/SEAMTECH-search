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
 *      appliquée côté serveur, pas seulement cachée dans l'interface ;
 *   5. une DÉCISION (valider) est liée à la révision revue : A ne peut pas
 *      approuver un écran que B vient de changer (409, aucune écriture, aucune
 *      ligne « valider » au journal) ;
 *   6. la révision N'EST PAS LUE (lecture retardée puis en échec) : l'écran
 *      n'écrit plus, et une requête DIRECTE sans révision est refusée (428)
 *      sans laisser de trace ;
 *   7. une RÉPONSE TARDIVE de la fiche précédente n'écrase jamais l'état de la
 *      fiche affichée (valeurs ET révision restent celles de la fiche choisie).
 *
 * Chaque vérification d'état passe par la BASE (via l'API authentifiée, qui la
 * lit) : un message d'interface ne prouve rien à lui seul.
 *
 * Les scénarios CONSTITUENT leurs fiches (`fichesDeTravail`) au lieu de
 * supposer que la file en contient encore : le premier passage CI a montré
 * qu'une preuve qui dépend de l'ordre d'exécution (la file rétrécit à mesure
 * que les scénarios valident des fiches) devient instable — donc sans valeur.
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

  /** Attend que la FILE de validation soit chargée et rendue.
   *
   *  Les scénarios ne choisissent plus leurs fiches DANS la file (elle
   *  rétrécit d'un scénario à l'autre) : ils les constituent par l'API
   *  (`fichesDeTravail`). Cette attente reste la porte d'entrée commune — on ne
   *  clique une ligne qu'une fois la file affichée. */
  async function attendreFile(poste: Page): Promise<void> {
    if (!poste.url().includes("/validation")) await poste.goto("/validation")
    await expect(poste.getByTestId("file-validation")).toBeVisible()
    await poste.getByTestId("file-validation").getByTestId("code-fiche").first().waitFor({ timeout: 15000 })
  }

  /** Fiches de travail d'un scénario, choisies PAR L'API et ramenées à
   *  « a_valider » par le chemin documenté (`rouvrir` avec la révision relue).
   *
   *  Pourquoi ce détour : la file de validation RÉTRÉCIT au fil du fichier —
   *  les scénarios précédents valident des fiches. Un scénario qui « espère »
   *  2 fiches dans la file dépend donc de l'ORDRE d'exécution : le premier
   *  passage CI l'a mesuré (0 fiche pour l'un, 1 au lieu de 2 pour l'autre).
   *  Ici chaque scénario constitue son jeu, et les fiches importées PENDANT le
   *  run (codes REF-*) sont écartées : un import en cours écrit la fiche
   *  pendant la mesure, ce qui fausserait « l'état n'a pas bougé ».
   */
  async function fichesDeTravail(poste: Page, combien: number): Promise<string[]> {
    const reponse = await poste.request.get("/api/fiches?taille=200")
    expect(reponse.status(), await reponse.text()).toBe(200)
    const corps = (await reponse.json()) as { fiches?: Array<{ code?: string }> }
    const codes = (corps.fiches ?? [])
      .map((fiche) => String(fiche.code ?? ""))
      .filter((code) => code && !code.includes("7792") && !code.toUpperCase().startsWith("REF-"))
      .sort()
    expect(
      codes.length,
      `corpus e2e insuffisant (${codes.length} fiche(s) hors 7792 et hors import) : ${codes.join(", ")}`,
    ).toBeGreaterThanOrEqual(combien)
    const choisis = codes.slice(0, combien)
    for (const code of choisis) await garantirAValider(poste, code)
    return choisis
  }

  /** Nombre de lignes du JOURNAL d'une fiche (audit) : une écriture refusée ne
   *  doit ajouter AUCUNE ligne — un « valider » trompeur serait pire que
   *  l'absence de décision. */
  async function lignesDeJournal(poste: Page, code: string): Promise<number> {
    const reponse = await poste.request.get(`/api/fiches/${encodeURIComponent(code)}/historique`)
    expect(reponse.status(), await reponse.text()).toBe(200)
    const lignes = (await reponse.json()) as unknown[]
    return lignes.length
  }

  /** Ouvre une fiche PRÉCISE dans la vue validation, depuis N'IMPORTE quelle
   *  page : le poste navigue lui-même vers la file. Sans cette navigation, un
   *  poste resté sur l'écran d'accueil (cas réel : il vient de se connecter)
   *  ne trouverait aucune ligne — c'est ce que la CI a montré au premier
   *  passage (deux scénarios en échec sur `li[data-code]` introuvable). */
  async function ouvrirFiche(poste: Page, code: string): Promise<void> {
    await attendreFile(poste)
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
    // ATTENDRE que les champs soient rendus : `ouvrirFiche` garantit la fiche
    // et sa révision, mais la liste des champs arrive par une SECONDE requête.
    // Choisir trop tôt levait « aucun champ corrigeable » — un échec
    // intermittent (le retry CI rattrapait le test : mesuré 3 passés / 1 flaky
    // au run 37640455508). On attend donc l'état attendu, on ne relâche pas
    // l'exigence.
    await expect(poste.locator('input[data-testid^="champ-"]').first()).toBeVisible({ timeout: 15000 })
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
      // Une fiche SYNTHÉTIQUE hors 7792-SO (d'autres épreuves vivent dessus) et
      // hors BIS-7792 (sa copie) : le scénario constitue son jeu par l'API.
      const [code] = await fichesDeTravail(posteA, 1)

      // LES DEUX POSTES OUVRENT LA MÊME FICHE, À LA MÊME RÉVISION.
      await Promise.all([ouvrirFiche(posteA, code), ouvrirFiche(posteB, code)])
      const { selecteur, champ } = await choisirChamp(posteA)
      const etatInitial = await etatEnBase(posteA, code, champ)
      await expect(posteA.locator(selecteur)).toBeVisible()
      await expect(posteB.locator(selecteur)).toBeVisible()
      const valeurInitiale = await posteA.locator(selecteur).inputValue()
      // ASSERTION avec réessai, jamais une lecture unique : `inputValue()` fige
      // l'instant où il est appelé et peut observer un rendu intermédiaire
      // (l'écran de B vient d'être ouvert) — c'est exactement le profil d'un
      // « flaky qui passe au retry ». `toHaveValue` attend que l'écran CONVERGE
      // vers la valeur du collègue, et échoue tout aussi nettement s'il ne
      // converge pas.
      await expect(posteB.locator(selecteur)).toHaveValue(valeurInitiale, { timeout: 15000 })

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
      const [code] = await fichesDeTravail(posteA, 1)

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

  /** Ramène la fiche à « a_valider » (elle peut avoir été validée par un autre
   *  scénario) et renvoie son état réel : ces scénarios ne supposent jamais un
   *  numéro de révision, ils le LISENT. */
  async function etatReel(
    poste: Page,
    code: string,
  ): Promise<{ statut: string; revision: number }> {
    const reponse = await poste.request.get(`/api/fiches/${encodeURIComponent(code)}/etat`)
    expect(reponse.status(), await reponse.text()).toBe(200)
    const corps = (await reponse.json()) as { statut: string; revision: number }
    return { statut: corps.statut, revision: corps.revision }
  }

  /** Valeur ENREGISTRÉE d'un champ, lue par l'API authentifiée (donc en base). */
  async function etatChamp(poste: Page, code: string, champ: string): Promise<string | null> {
    const reponse = await poste.request.get(`/api/fiches/${encodeURIComponent(code)}/champs`)
    expect(reponse.status(), await reponse.text()).toBe(200)
    const lignes = (await reponse.json()) as Array<{ champ: string; valeur_normalisee: string | null }>
    return lignes.find((ligne) => ligne.champ === champ)?.valeur_normalisee ?? null
  }

  async function garantirAValider(poste: Page, code: string): Promise<{ statut: string; revision: number }> {
    let etat = await etatReel(poste, code)
    if (etat.statut !== "a_valider") {
      const reouverture = await poste.request.post(`/api/fiches/${encodeURIComponent(code)}/rouvrir`, {
        data: { revision: etat.revision, effacer_corrections: false },
      })
      expect(reouverture.status(), await reouverture.text()).toBe(200)
      etat = await etatReel(poste, code)
    }
    return etat
  }

  test("décision liée à la révision revue : A ne peut pas approuver ce que B vient de changer", async ({ browser }) => {
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      const [code] = await fichesDeTravail(posteA, 1)
      const etatInitial = await garantirAValider(posteA, code)

      // LES DEUX POSTES ouvrent la MÊME fiche, à la MÊME révision.
      await ouvrirFiche(posteA, code)
      await ouvrirFiche(posteB, code)
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-statut", "a_valider")

      // B corrige une valeur de fabrication (le collègue travaille pendant que
      // A relit) : la fiche avance d'une révision.
      const { selecteur, champ } = await choisirChamp(posteB)
      const valeurCollegue = `E2E-DECISION-${Date.now()}`
      await posteB.locator(selecteur).fill(valeurCollegue)
      await posteB.getByTestId("bouton-corriger").first().click()
      await expect(posteB.getByTestId("message-ok")).toBeVisible({ timeout: 15000 })
      const apresB = await etatReel(posteA, code)
      expect(apresB.revision).toBe(etatInitial.revision + 1)

      // A, sur SON écran (révision N), clique « Valider » : REFUS — et le refus
      // ne doit laisser AUCUNE trace d'audit trompeuse (une ligne « valider »
      // ferait croire que la fabrication a été approuvée).
      const journalAvantRefus = await lignesDeJournal(posteA, code)
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", String(etatInitial.revision))
      await posteA.getByTestId("bouton-valider").click()
      const conflitA = posteA.getByTestId("conflit-revision")
      await expect(conflitA).toBeVisible({ timeout: 15000 })
      await expect(conflitA).toContainText("décision NON appliquée")
      await expect(conflitA).toContainText("valider")
      await expect(conflitA.getByTestId("conflit-revisions")).toContainText(
        `${etatInitial.revision} → ${apresB.revision}`,
      )

      // PREUVE EN BASE : la fiche est TOUJOURS a_valider — la décision n'a pas
      // été appliquée, et la valeur de B est intacte.
      const etatApresRefus = await etatReel(posteA, code)
      expect(etatApresRefus).toEqual({ statut: "a_valider", revision: apresB.revision })
      const champs = await etatChamp(posteA, code, champ)
      expect(champs).toBe(valeurCollegue)
      expect(
        await lignesDeJournal(posteA, code),
        "une décision REFUSÉE a laissé une ligne d'audit (elle ferait croire à une approbation)",
      ).toBe(journalAvantRefus)

      // A RECHARGE (l'écran exige une relecture), puis décide sur l'état à jour.
      await posteA.getByTestId("bouton-recharger-fiche").click()
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", String(apresB.revision))
      await expect(conflitA).toHaveCount(0)
      await posteA.getByTestId("bouton-valider").click()
      // MESSAGE SPÉCIFIQUE À LA DÉCISION, jamais le seul testid : après un
      // rechargement, « message-ok » porte déjà « Fiche X rechargée à jour » —
      // l'attendre par son testid seul laissait passer l'assertion AVANT que la
      // décision soit appliquée (mesuré : 1 test flaky au run pull_request, la
      // lecture suivante voyait encore « a_valider »). On exige donc le texte
      // de LA décision.
      await expect(posteA.getByTestId("message-ok")).toHaveText(/valider enregistré au journal/, {
        timeout: 15000,
      })
      const etatFinal = await etatReel(posteA, code)
      expect(etatFinal).toEqual({ statut: "valide", revision: apresB.revision + 1 })
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test("révision non lue : correction et décision bloquées, et le backend refuse aussi", async ({ browser }) => {
    // Budget EXPLICITE (90 s au lieu des 30 s par défaut) : ce scénario
    // ÉPROUVE DES CHEMINS D'ÉCHEC — lecture retardée de 1,5 s, puis lecture
    // abandonnée, chaque attente d'écran ayant elle-même 15 s de marge. Le
    // premier passage CI l'a mesuré : « Test timeout of 30000ms exceeded » sur
    // les trois tentatives. Le délai par défaut n'est pas une exigence du
    // produit ; ce qui doit rester borné, ce sont les attentes INTERNES.
    test.setTimeout(90_000)
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      const [code] = await fichesDeTravail(posteA, 1)

      // LECTURE RETARDÉE : tant que la révision n'est pas là, aucun champ n'est
      // affiché — l'état n'est jamais « à moitié chargé » (champs sans jeton).
      await posteA.route(`**/api/fiches/${encodeURIComponent(code)}/etat`, async (route) => {
        await new Promise((resoudre) => setTimeout(resoudre, 1500))
        await route.continue()
      })
      await posteA.goto("/validation")
      await expect(posteA.getByTestId("file-validation")).toBeVisible()
      await posteA.locator(`li[data-code="${code}"]`).getByTestId("code-fiche").click()
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-chargement-etat", "1")
      await expect(posteA.locator('input[data-testid^="champ-"]')).toHaveCount(0)
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", /^[0-9]+$/, {
        timeout: 15000,
      })

      // LECTURE EN ÉCHEC : le poste OUVRE la fiche pendant que la lecture
      // d'état échoue (« le service ne répond pas »). On n'attend pas un bouton
      // de reprise qui n'existe QUE déjà en état d'échec : cette attente morte
      // consommait le budget entier du test — mesuré en CI, trois tentatives de
      // 30 s échouées à la même seconde. On éprouve donc le geste réel, puis le
      // retour du service.
      const avant = await etatReel(posteB, code)
      const journalAvant = await lignesDeJournal(posteB, code)
      await posteA.unroute(`**/api/fiches/${encodeURIComponent(code)}/etat`)
      await posteA.route(`**/api/fiches/${encodeURIComponent(code)}/etat`, (route) => route.abort())
      await posteA.goto("/validation")
      await expect(posteA.getByTestId("file-validation")).toBeVisible()
      await posteA.locator(`li[data-code="${code}"]`).getByTestId("code-fiche").click()
      const bandeau = posteA.getByTestId("revision-indisponible")
      // L'écran ne RESTE PAS « en chargement » : il a bien constaté l'échec et
      // il le dit. C'est la différence entre « bloqué » et « figé ».
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-chargement-etat", "0", {
        timeout: 15000,
      })
      await expect(bandeau).toBeVisible({ timeout: 15000 })
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", "")
      // Rien à corriger sans état lu : aucun champ n'est rendu, donc aucun
      // bouton « corriger » n'existe — l'écran ne peut pas écrire à l'aveugle.
      await expect(posteA.getByTestId("bouton-corriger")).toHaveCount(0)
      await expect(posteA.getByTestId("bouton-valider")).toBeVisible()
      // Le bouton de reprise est bien PRÉSENT — et un second refus le laisse en
      // place : aucune écriture n'est rendue possible par un rechargement raté.
      await posteA.getByTestId("bouton-recharger-revision").click()
      await expect(bandeau).toBeVisible()
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", "")
      // SERVICE REVENU : la reprise rend la révision ET les champs — l'écran
      // redevient capable d'écrire, sous verrou (révision relue, jamais supposée).
      await posteA.unroute(`**/api/fiches/${encodeURIComponent(code)}/etat`)
      await posteA.getByTestId("bouton-recharger-revision").click()
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", /^[0-9]+$/, {
        timeout: 15000,
      })
      await expect(bandeau).toHaveCount(0)

      // REQUÊTE DIRECTE SANS RÉVISION (ce que ferait un script ou un ancien
      // client) : le BACKEND refuse aussi — 428, et rien n'est écrit.
      const directe = await posteB.request.post(`/api/fiches/${encodeURIComponent(code)}/corriger`, {
        data: { champ: "fiche.designation", valeur: "ECRITURE-SANS-REVISION" },
      })
      expect(directe.status(), await directe.text()).toBe(428)
      const decisionDirecte = await posteB.request.post(`/api/fiches/${encodeURIComponent(code)}/valider`, {
        data: {},
      })
      expect(decisionDirecte.status(), await decisionDirecte.text()).toBe(428)

      // L'ÉTAT EN BASE N'A PAS BOUGÉ — et on le prouve par des faits PRÉCIS,
      // pas par une comparaison globale : la valeur refusée n'est nulle part,
      // le JOURNAL n'a pas gagné une ligne (aucun « valider » trompeur), le
      // statut et la révision sont ceux d'avant. Le message d'échec dit lequel
      // des quatre a bougé.
      const apres = await etatReel(posteB, code)
      // La valeur refusée n'existe sur AUCUN champ de la fiche (le contrôle ne
      // dépend donc pas de l'existence du champ visé, ni de son rang).
      const champsApres = await posteB.request.get(`/api/fiches/${encodeURIComponent(code)}/champs`)
      expect(champsApres.status(), await champsApres.text()).toBe(200)
      expect(await champsApres.text()).not.toContain("ECRITURE-SANS-REVISION")
      expect(await lignesDeJournal(posteB, code), "une écriture refusée a laissé une ligne d'audit").toBe(
        journalAvant,
      )
      expect(apres.statut, "statut après refus").toBe(avant.statut)
      expect(apres.revision, "révision après refus").toBe(avant.revision)
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test("réponse en retard : la fiche affichée n'est jamais écrasée par la précédente", async ({ browser }) => {
    const { ctxA, ctxB, posteA, posteB } = await deuxPostes(browser)
    try {
      const [rapide, lente] = await fichesDeTravail(posteA, 2)

      // La lecture d'état de la fiche LENTE est retardée : elle arrivera APRÈS
      // la sélection de la fiche RAPIDE.
      await posteA.route(`**/api/fiches/${encodeURIComponent(lente)}/etat`, async (route) => {
        await new Promise((resoudre) => setTimeout(resoudre, 2000))
        await route.continue()
      })
      await posteA.goto("/validation")
      await expect(posteA.getByTestId("file-validation")).toBeVisible()
      await posteA.locator(`li[data-code="${lente}"]`).getByTestId("code-fiche").click()
      await posteA.locator(`li[data-code="${rapide}"]`).getByTestId("code-fiche").click()
      await expect(posteA.getByTestId("titre-fiche")).toHaveText(rapide)
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", /^[0-9]+$/)

      // On laisse la réponse tardive arriver, puis on vérifie que l'écran
      // affiche TOUJOURS la fiche rapide — ses champs ET sa révision.
      await posteA.waitForTimeout(2500)
      await expect(posteA.getByTestId("titre-fiche")).toHaveText(rapide)
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-fiche", rapide)
      const { selecteur, champ } = await choisirChamp(posteA)
      const attendue = await etatChamp(posteA, rapide, champ)
      await expect(posteA.locator(selecteur)).toHaveValue(attendue ?? "")
      const revisionRapide = (await etatReel(posteB, rapide)).revision
      await expect(posteA.getByTestId("validation-app")).toHaveAttribute("data-revision", String(revisionRapide))
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
