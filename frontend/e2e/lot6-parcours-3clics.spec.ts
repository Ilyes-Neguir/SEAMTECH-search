import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test"
import { ADMIN, APPROBATEUR_2, LECTURE, UTILISATEUR, signIn, type Identifiants } from "./helpers"
import { signInWith } from "./helpers"

/**
 * Lot 6 — parcours métier comptés en clics + matrice de navigation par rôle.
 *
 * Convention de comptage (explicite, donc honnête) : seuls les clics sont
 * comptés, à partir d'un point d'entrée naturel déjà ouvert (lien profond
 * de recherche, liste des demandes) ; la saisie libre (motif) n'est pas un
 * clic. Le parcours complet « chercher → demander → approuver →
 * télécharger » doit tenir en 3 clics par acteur, 1 clic pour le
 * téléchargement autorisé.
 *
 * La matrice vérifie que la navigation ne promet jamais un écran refusé au
 * rôle connecté, et que les écrans voiles se relient entre eux (extraction
 * → confiance → fiche métier).
 */

const CODE_REFERENCE = "7792-SO"

async function ouvrirPoste(
  browser: Browser,
  contextes: BrowserContext[],
  identifiants?: Identifiants,
): Promise<Page> {
  const contexte = await browser.newContext()
  contextes.push(contexte)
  const page = await contexte.newPage()
  if (identifiants) {
    await signInWith(page, identifiants)
  } else {
    await signIn(page)
  }
  return page
}

interface Piece {
  id: number | string
  is_primary_pdf?: boolean
  kind?: string
}

async function piecePrincipale(page: Page, code: string): Promise<Piece> {
  const reponse = await page.request.get(`/api/fiches/${encodeURIComponent(code)}/pieces`)
  expect(reponse.ok(), "les pièces de la fiche doivent répondre").toBeTruthy()
  const corps = (await reponse.json()) as { pieces?: Piece[] } | Piece[]
  const pieces = Array.isArray(corps) ? corps : (corps.pieces ?? [])
  const piece = pieces.find((p) => p.is_primary_pdf) ?? pieces.find((p) => p.kind === "pdf")
  expect(piece, `aucune pièce PDF rattachée à ${code}`).toBeTruthy()
  return piece as Piece
}

async function nettoyerDemandesTelechargement(page: Page, idPiece: number | string): Promise<void> {
  const reponse = await page.request.get("/api/approvals?vue=miennes&statut=en_attente&limit=100")
  if (!reponse.ok()) return
  const corps = (await reponse.json()) as {
    demandes: { id_demande: number; type_demande: string; id_piece: number | string | null }[]
  }
  for (const demande of corps.demandes.filter(
    (d) => d.type_demande === "telechargement" && String(d.id_piece) === String(idPiece),
  )) {
    await page.request.post(`/api/approvals/${demande.id_demande}/annuler`, { data: {} })
  }
}

test.describe("Lot 6 — parcours 3 clics et cohérence globale", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "Lot 6 navigateur : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL et comptes nominatifs)",
  )

  test("parcours complet compté : chercher → demander (3 clics) → approuver (3 clics) → télécharger (1 clic)", async ({
    browser,
  }) => {
    const contextes: BrowserContext[] = []
    try {
      const utilisateur = await ouvrirPoste(browser, contextes, UTILISATEUR)
      const piece = await piecePrincipale(utilisateur, CODE_REFERENCE)
      await nettoyerDemandesTelechargement(utilisateur, piece.id)
      // Une autorisation encore valide fausserait le parcours de demande.
      const avantDemande = await utilisateur.request.get(`/api/pieces/${piece.id}/telecharger`)
      expect(avantDemande.status(), "pas d'autorisation de téléchargement préexistante").toBe(403)

      // --- Utilisateur : 3 clics depuis les résultats de recherche.
      await utilisateur.goto(`/recherche?q=${encodeURIComponent("7792")}`)
      let clicsUtilisateur = 0
      await utilisateur.getByRole("link", { name: `Ouvrir la fiche ${CODE_REFERENCE}` }).click()
      clicsUtilisateur += 1
      await expect(utilisateur.getByTestId("fiche-code")).toHaveText(CODE_REFERENCE, { timeout: 30000 })
      const listePieces = utilisateur.getByTestId("pieces-jointes")
      await listePieces.getByTestId(`piece-download-request-${piece.id}`).click()
      clicsUtilisateur += 1
      const formulaire = listePieces.getByTestId(`piece-download-form-${piece.id}`)
      await formulaire.locator("textarea").fill("Recette Lot 6 : parcours compté en clics.")
      await formulaire.getByRole("button", { name: "Envoyer la demande" }).click()
      clicsUtilisateur += 1
      await expect(listePieces.getByTestId(`piece-download-pending-${piece.id}`)).toBeVisible({ timeout: 30000 })
      expect(clicsUtilisateur, "demande de téléchargement en 3 clics depuis la recherche").toBe(3)

      // --- Approbateur : 3 clics depuis la liste « à décider ».
      const reponseDemandes = await utilisateur.request.get(
        "/api/approvals?vue=miennes&type_demande=telechargement&limit=100",
      )
      const corpsDemandes = (await reponseDemandes.json()) as {
        demandes: { id_demande: number; statut: string; motif: string }[]
      }
      const demande = corpsDemandes.demandes.find((d) => d.motif.includes("Recette Lot 6"))
      expect(demande, "la demande Lot 6 doit exister").toBeTruthy()

      const approbateur = await ouvrirPoste(browser, contextes, APPROBATEUR_2)
      await approbateur.goto("/approvals")
      let clicsApprobateur = 0
      await approbateur.getByTestId(`approval-${demande!.id_demande}`).click()
      clicsApprobateur += 1
      const decision = approbateur.getByTestId("decision-form")
      await expect(decision).toBeVisible({ timeout: 30000 })
      await decision.getByRole("button", { name: "Approuver", exact: true }).click()
      clicsApprobateur += 1
      await decision.getByRole("button", { name: "Enregistrer la décision" }).click()
      clicsApprobateur += 1
      await expect(approbateur.locator('[role="status"]', { hasText: /approuv/i })).toBeVisible({ timeout: 30000 })
      expect(clicsApprobateur, "décision en 3 clics depuis la liste à décider").toBe(3)

      // --- Utilisateur : 1 clic pour le téléchargement autorisé, octets relus.
      await utilisateur.goto(`/fiches/${CODE_REFERENCE}`)
      const bouton = utilisateur.getByTestId("pieces-jointes").getByTestId(`piece-download-${piece.id}`)
      await expect(bouton).toBeVisible({ timeout: 30000 })
      const [telechargement] = await Promise.all([utilisateur.waitForEvent("download"), bouton.click()])
      const chemin = await telechargement.path()
      expect(chemin, "le fichier téléchargé doit exister").toBeTruthy()
      const { readFile } = await import("node:fs/promises")
      const octets = await readFile(chemin as string)
      expect(octets.subarray(0, 4).toString("latin1"), "un vrai PDF, pas une page d'erreur").toBe("%PDF")
    } finally {
      for (const contexte of contextes) await contexte.close()
    }
  })

  test("matrice de navigation par rôle : aucun lien promis ne débouche sur un refus", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    const attendus: Array<{ nom: string; compte?: Identifiants; visibles: string[]; masques: string[] }> = [
      {
        // Compte de secours = admin d'urgence documenté (ROLE_SECOURS =
        // ROLE_ADMIN, « Secours admin » à l'écran) : il dépose et administre
        // techniquement, mais n'a AUCUNE identité nominative — tous les
        // écrans métier nominatifs lui sont fermés (Lot 1).
        nom: "secours",
        visibles: ["Recherche", "Dossiers", "Fichiers", "Qualité", "Nouveau dossier", "Gabarits", "Comptes"],
        masques: ["Validation", "Extractions", "Comparaison", "Confiance", "Demandes"],
      },
      {
        nom: "lecture",
        compte: LECTURE,
        visibles: ["Recherche", "Comparaison", "Confiance", "Demandes"],
        masques: ["Nouveau dossier", "Validation", "Extractions", "Gabarits", "Comptes"],
      },
      {
        nom: "utilisateur",
        compte: UTILISATEUR,
        visibles: ["Nouveau dossier", "Comparaison", "Confiance", "Demandes"],
        masques: ["Validation", "Extractions", "Gabarits", "Comptes"],
      },
      {
        nom: "admin",
        compte: ADMIN,
        visibles: ["Nouveau dossier", "Comparaison", "Confiance", "Demandes", "Gabarits", "Comptes"],
        masques: ["Validation", "Extractions"], // l'admin ne décide jamais (Lot 1)
      },
    ]
    try {
      for (const cas of attendus) {
        const page = await ouvrirPoste(browser, contextes, cas.compte)
        const nav = page.getByTestId("nav")
        await expect(nav, `${cas.nom} : navigation visible`).toBeVisible({ timeout: 30000 })
        for (const libelle of cas.visibles) {
          await expect(nav.getByRole("link", { name: libelle }), `${cas.nom} doit voir « ${libelle} »`).toBeVisible()
        }
        for (const libelle of cas.masques) {
          await expect(nav.getByRole("link", { name: libelle }), `${cas.nom} ne doit pas voir « ${libelle} »`).toHaveCount(0)
        }
        // Chaque lien promis s'ouvre sans refus d'accès immédiat.
        const hrefs = await nav.locator("a[href]").evaluateAll((elements) =>
          elements.map((el) => el.getAttribute("href")).filter((h): h is string => Boolean(h)),
        )
        for (const href of hrefs) {
          await page.goto(href)
          await expect(
            page.getByTestId("acces-refuse"),
            `${cas.nom} : ${href} promis par la navigation ne doit pas refuser l'accès`,
          ).toHaveCount(0)
        }
      }
    } finally {
      for (const contexte of contextes) await contexte.close()
    }
  })

  test("liens croisés voiles : extraction → Confiance… → fiche source → fiche métier", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    try {
      const page = await ouvrirPoste(browser, contextes, APPROBATEUR_2)
      const reponse = await page.request.get("/api/extractions-voile?limite=100")
      const corps = (await reponse.json()) as {
        extractions: { id_extraction: number; code_fiche: string | null }[]
      }
      // Candidate LOT5-POP-2 : sa population (5) contient la vraie fiche
      // 7792-SO, seule source rattachée à une fiche métier — donc la seule
      // rendue comme lien (jamais de lien mort).
      const candidate = corps.extractions.find((e) => (e.code_fiche ?? "") === "LOT5-POP-2")
      expect(candidate, "extraction LOT5-POP-2 dans la base live").toBeTruthy()

      await page.goto(`/extractions`)
      await page.getByTestId("recherche-extractions").fill("LOT5-POP-2")
      const ligne = page.getByTestId("file-extractions").getByTestId("extraction-item").first()
      await expect(ligne).toContainText("LOT5-POP-2", { timeout: 30000 })
      await ligne.click()
      const lienConfiance = page.getByTestId("lien-confiance")
      await expect(lienConfiance).toBeVisible({ timeout: 30000 })
      await lienConfiance.click()
      await page.waitForURL(new RegExp(`/confiance\\?id=${candidate!.id_extraction}$`), { timeout: 20000 })

      await page.getByRole("button", { name: "Évaluer la confiance" }).click()
      const resultat = page.getByTestId("confiance-resultat")
      await expect(resultat).toBeVisible({ timeout: 30000 })
      // Une fiche source de la population ouvre la fiche métier.
      await resultat.getByText(/Fiches sources de la population/).click()
      await resultat.getByRole("link", { name: new RegExp(CODE_REFERENCE) }).first().click()
      await expect(page.getByTestId("fiche-code")).toHaveText(CODE_REFERENCE, { timeout: 30000 })
    } finally {
      for (const contexte of contextes) await contexte.close()
    }
  })
})
