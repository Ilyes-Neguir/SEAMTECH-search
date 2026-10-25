import {
  expect,
  test,
  type APIResponse,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test"
import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import {
  ADMIN,
  APPROBATEUR,
  APPROBATEUR_2,
  LECTURE,
  UTILISATEUR,
  csrfHeadersForPage,
  signIn,
  signInWith,
  type Identifiants,
} from "./helpers"

/**
 * Lot 1 — les quatre rôles et le circuit d'approbation, éprouvés dans le
 * navigateur contre PostgreSQL réel.
 *
 * Les tests unitaires et d'API prouvent les gardes; ce fichier prouve le
 * parcours complet qu'une personne suit réellement :
 *
 *   utilisateur → demande → notification/cloche → approbateur → décision
 *   → notification de retour → effet métier relu par l'API et à l'écran.
 *
 * Il couvre aussi les refus qui font la sécurité du lot : lecture ne peut ni
 * demander ni télécharger, admin/secours ne décident jamais, un approbateur
 * ne peut pas s'auto-approuver, et un changement de rôle révoque aussitôt les
 * sessions existantes.
 */

const CODE_REFERENCE = "7792-SO"
const CODE_SUPPRESSION = "0902-MM"
const CHAMP_DESIGNATION = "fiche.designation"

let compteurSuffixe = 0
function suffixe(prefixe: string): string {
  compteurSuffixe += 1
  return `${prefixe}-${Date.now()}-${compteurSuffixe}`
}

type SessionApi = {
  authenticated: boolean
  identifiant: string | null
  role: string
  mode: string | null
}

type ChampEtat = {
  champ: string
  rang: number | null
  valeur_brute: string | null
  valeur_normalisee: string | null
  corrige?: boolean
  corrige_par?: string | null
}

type EtatFiche = {
  code: string
  statut: string
  revision: number
  champs: ChampEtat[]
}

type Piece = {
  id: number
  name: string
  kind: string
  is_primary_pdf: boolean
  previewable: boolean
}

type Demande = {
  id_demande: number
  type_demande: string
  statut: string
  motif: string
  demandeur: string
  decideur: string | null
  commentaire_decision: string | null
  code_fiche: string | null
  id_piece: number | null
  champ: string | null
  rang: number | null
  valeur_proposee: string | null
  expire_le: string | null
}

type ListeDemandes = {
  demandes: Demande[]
  total: number
}

type Notification = {
  id_notification: number
  id_demande: number | null
  type_notification: string
  titre: string
  message: string
  lue: boolean
  type_demande: string | null
  statut_demande: string | null
  code_fiche: string | null
}

type ListeNotifications = {
  notifications: Notification[]
  non_lues: number
}

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

async function fermerPostes(contextes: BrowserContext[]): Promise<void> {
  const aFermer = contextes.splice(0).reverse()
  await Promise.all(aFermer.map(async (contexte) => contexte.close()))
}

async function lireJson<T>(page: Page, url: string, statutAttendu = 200): Promise<T> {
  const reponse = await page.request.get(url)
  const texte = await reponse.text()
  expect(reponse.status(), texte).toBe(statutAttendu)
  return JSON.parse(texte) as T
}

async function posterJson<T>(
  page: Page,
  url: string,
  data: Record<string, unknown>,
  statutAttendu = 200,
): Promise<T> {
  const reponse = await page.request.post(url, {
    headers: await csrfHeadersForPage(page),
    data,
  })
  const texte = await reponse.text()
  expect(reponse.status(), texte).toBe(statutAttendu)
  return JSON.parse(texte) as T
}

async function statutDe(reponse: APIResponse): Promise<number> {
  return reponse.status()
}

async function sessionDe(page: Page): Promise<SessionApi> {
  return lireJson<SessionApi>(page, "/api/auth/session")
}

async function etatFiche(page: Page, code: string): Promise<EtatFiche> {
  return lireJson<EtatFiche>(page, `/api/fiches/${encodeURIComponent(code)}/etat`)
}

async function piecesDeFiche(page: Page, code: string): Promise<Piece[]> {
  const corps = await lireJson<{ pieces: Piece[] }>(
    page,
    `/api/fiches/${encodeURIComponent(code)}/pieces`,
  )
  return corps.pieces
}

async function piecePrincipale(page: Page, code: string): Promise<Piece> {
  const pieces = await piecesDeFiche(page, code)
  const piece =
    pieces.find((item) => item.is_primary_pdf) ?? pieces.find((item) => item.kind === "pdf")
  expect(piece, `aucune pièce PDF rattachée à ${code}`).toBeTruthy()
  return piece as Piece
}

async function mesDemandes(
  page: Page,
  parametres: Record<string, string> = {},
): Promise<ListeDemandes> {
  const query = new URLSearchParams({ vue: "miennes", limit: "100", ...parametres })
  return lireJson<ListeDemandes>(page, `/api/approvals?${query.toString()}`)
}

async function trouverDemandeParMotif(page: Page, motif: string): Promise<Demande> {
  const liste = await mesDemandes(page)
  const demande = liste.demandes.find((item) => item.motif === motif)
  expect(demande, `demande introuvable pour le motif « ${motif} »`).toBeTruthy()
  return demande as Demande
}

async function detailDemande(page: Page, idDemande: number): Promise<Demande> {
  return lireJson<Demande>(page, `/api/approvals/${idDemande}`)
}

async function annulerDemandeSiEnAttente(page: Page, idDemande: number): Promise<void> {
  const detail = await detailDemande(page, idDemande)
  if (detail.statut !== "en_attente") return
  await posterJson<Demande>(page, `/api/approvals/${idDemande}/annuler`, {})
}

async function nettoyerDemandesEnAttente(
  page: Page,
  correspondance: (demande: Demande) => boolean,
): Promise<void> {
  const liste = await mesDemandes(page, { statut: "en_attente" })
  for (const demande of liste.demandes.filter(correspondance)) {
    await posterJson<Demande>(page, `/api/approvals/${demande.id_demande}/annuler`, {})
  }
}

async function notificationsDe(page: Page): Promise<ListeNotifications> {
  return lireJson<ListeNotifications>(page, "/api/notifications?limit=100")
}

async function notificationPourDemande(
  page: Page,
  idDemande: number,
  typeNotification?: string,
): Promise<Notification> {
  const liste = await notificationsDe(page)
  const notification = liste.notifications.find(
    (item) =>
      item.id_demande === idDemande &&
      (!typeNotification || item.type_notification === typeNotification),
  )
  expect(
    notification,
    `aucune notification ${typeNotification ?? ""} pour la demande #${idDemande}`,
  ).toBeTruthy()
  return notification as Notification
}

async function ramenerAValider(page: Page, code: string): Promise<EtatFiche> {
  let etat = await etatFiche(page, code)
  if (etat.statut !== "a_valider") {
    await posterJson(page, `/api/fiches/${encodeURIComponent(code)}/rouvrir`, {
      revision: etat.revision,
      effacer_corrections: false,
    })
    etat = await etatFiche(page, code)
  }
  expect(etat.statut).toBe("a_valider")
  return etat
}

async function ouvrirDemande(page: Page, idDemande: number): Promise<void> {
  await page.goto(`/approvals?id=${idDemande}`)
  await expect(page.getByTestId("approvals-app")).toBeVisible()
  await expect(page.getByTestId("approval-detail")).toContainText(`Demande #${idDemande}`)
}

async function deciderDetailOuvert(
  page: Page,
  decision: "approuver" | "refuser",
  commentaire: string,
  messageAttendu: string | RegExp,
): Promise<void> {
  const formulaire = page.getByTestId("decision-form")
  await expect(formulaire).toBeVisible()
  await formulaire
    .getByRole("button", { name: decision === "approuver" ? "Approuver" : "Refuser", exact: true })
    .click()
  if (commentaire) await formulaire.locator("textarea").fill(commentaire)
  await formulaire.getByRole("button", { name: "Enregistrer la décision" }).click()
  await expect(page.locator('[role="status"]', { hasText: messageAttendu })).toBeVisible()
}

async function deciderDemande(
  page: Page,
  idDemande: number,
  decision: "approuver" | "refuser",
  commentaire: string,
  messageAttendu: string | RegExp,
): Promise<void> {
  await ouvrirDemande(page, idDemande)
  await deciderDetailOuvert(page, decision, commentaire, messageAttendu)
}

function champDesignation(etat: EtatFiche): ChampEtat {
  const champ = etat.champs.find((item) => item.champ === CHAMP_DESIGNATION)
  expect(champ, `${CHAMP_DESIGNATION} absent de l'état de ${etat.code}`).toBeTruthy()
  return champ as ChampEtat
}

async function creerDemandeModificationDepuisFiche(
  pageDemandeur: Page,
  code: string,
  valeurProposee: string,
  motif: string,
): Promise<Demande> {
  const etat = await etatFiche(pageDemandeur, code)
  const champ = champDesignation(etat)
  await nettoyerDemandesEnAttente(
    pageDemandeur,
    (demande) =>
      demande.type_demande === "modification" &&
      demande.code_fiche === code &&
      demande.champ === champ.champ &&
      demande.rang === champ.rang,
  )

  await pageDemandeur.goto(`/fiches/${encodeURIComponent(code)}`)
  await expect(pageDemandeur.getByTestId("fiche-code")).toHaveText(code)
  const formulaire = pageDemandeur.getByTestId("demande-modification-form")
  await expect(formulaire).toBeVisible()
  await formulaire.locator("select").selectOption(`${champ.champ}#${champ.rang ?? ""}`)
  await formulaire.locator("input").fill(valeurProposee)
  await formulaire.locator("textarea").fill(motif)
  await formulaire.getByRole("button", { name: "Envoyer la demande de modification" }).click()
  await expect(
    pageDemandeur.locator('[role="status"]', {
      hasText: "Demande de modification envoyée à un approbateur.",
    }),
  ).toBeVisible()
  return trouverDemandeParMotif(pageDemandeur, motif)
}

test.describe("Lot 1 — rôles, approbations et notifications", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "Lot 1 navigateur : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL et comptes nominatifs)",
  )

  test("utilisateur → notification → approbateur → téléchargement approuvé relu en octets", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    try {
      const utilisateur = await ouvrirPoste(browser, contextes, UTILISATEUR)
      expect(await sessionDe(utilisateur)).toMatchObject({
        identifiant: UTILISATEUR.identifiant,
        role: "utilisateur",
        mode: "nominatif",
      })

      const piece = await piecePrincipale(utilisateur, CODE_REFERENCE)
      await nettoyerDemandesEnAttente(
        utilisateur,
        (demande) => demande.type_demande === "telechargement" && demande.id_piece === piece.id,
      )

      const avantApprobation = await utilisateur.request.get(
        `/api/pieces/${piece.id}/telecharger`,
      )
      expect(await statutDe(avantApprobation), await avantApprobation.text()).toBe(403)

      const motif = `Besoin du PDF pour preparer la decoupe (${suffixe("telechargement")}).`
      await utilisateur.goto(`/fiches/${CODE_REFERENCE}`)
      await expect(utilisateur.getByTestId("fiche-code")).toHaveText(CODE_REFERENCE)
      const listePieces = utilisateur.getByTestId("pieces-jointes")
      await expect(listePieces.getByTestId(`piece-download-${piece.id}`)).toHaveCount(0)
      await listePieces.getByTestId(`piece-download-request-${piece.id}`).click()
      const formulaire = listePieces.getByTestId(`piece-download-form-${piece.id}`)
      await formulaire.locator("textarea").fill(motif)
      await formulaire.getByRole("button", { name: "Envoyer la demande" }).click()
      await expect(listePieces.getByTestId(`piece-download-pending-${piece.id}`)).toBeVisible()

      const demande = await trouverDemandeParMotif(utilisateur, motif)
      expect(demande).toMatchObject({
        type_demande: "telechargement",
        statut: "en_attente",
        code_fiche: CODE_REFERENCE,
        id_piece: piece.id,
        demandeur: UTILISATEUR.identifiant,
      })

      const approbateur2 = await ouvrirPoste(browser, contextes, APPROBATEUR_2)
      await expect(approbateur2.getByTestId("notifications-non-lues")).toBeVisible()
      expect(Number(await approbateur2.getByTestId("notifications-non-lues").textContent())).toBeGreaterThan(0)

      const notificationApprobateur = await notificationPourDemande(
        approbateur2,
        demande.id_demande,
        "nouvelle_demande",
      )
      expect(notificationApprobateur.lue).toBe(false)
      await approbateur2.goto("/notifications")
      const ligneNotification = approbateur2.getByTestId(
        `notification-${notificationApprobateur.id_notification}`,
      )
      await expect(ligneNotification).toContainText(motif)
      await expect(ligneNotification).toContainText(CODE_REFERENCE)
      await ligneNotification.getByRole("link", { name: "Ouvrir la demande" }).click()
      await expect(approbateur2).toHaveURL(new RegExp(`/approvals\\?id=${demande.id_demande}$`))
      await expect(approbateur2.getByTestId("approval-detail")).toContainText(
        `Demande #${demande.id_demande}`,
      )
      await expect
        .poll(async () => (await notificationPourDemande(
          approbateur2,
          demande.id_demande,
          "nouvelle_demande",
        )).lue)
        .toBe(true)

      await deciderDetailOuvert(
        approbateur2,
        "approuver",
        "Besoin atelier confirme sur le document source.",
        "Demande approuvée ; le téléchargement est ouvert pour 24 heures.",
      )
      const approuvee = await detailDemande(approbateur2, demande.id_demande)
      expect(approuvee.statut).toBe("approuvee")
      expect(approuvee.decideur).toBe(APPROBATEUR_2.identifiant)
      expect(Date.parse(approuvee.expire_le ?? "")).toBeGreaterThan(Date.now())

      const octetsDirects = await approbateur2.request.get(`/api/pieces/${piece.id}/telecharger`)
      expect(octetsDirects.status()).toBe(200)
      const corpsDirect = await octetsDirects.body()
      expect(corpsDirect.subarray(0, 4).toString("utf8")).toBe("%PDF")
      const empreinteDirecte = createHash("sha256").update(corpsDirect).digest("hex")

      const notificationUtilisateur = await notificationPourDemande(
        utilisateur,
        demande.id_demande,
        "decision",
      )
      await utilisateur.goto("/notifications")
      const ligneUtilisateur = utilisateur.getByTestId(
        `notification-${notificationUtilisateur.id_notification}`,
      )
      await expect(ligneUtilisateur).toContainText("Téléchargement approuvé")
      await ligneUtilisateur.getByRole("button", { name: "Marquer comme lue" }).click()
      await expect(
        ligneUtilisateur.getByRole("button", { name: "Marquer comme lue" }),
      ).toHaveCount(0)
      await expect
        .poll(async () => (await notificationPourDemande(
          utilisateur,
          demande.id_demande,
          "decision",
        )).lue)
        .toBe(true)

      await ouvrirDemande(utilisateur, demande.id_demande)
      const lienApprouve = utilisateur.getByRole("link", {
        name: "Télécharger le fichier approuvé",
      })
      await expect(lienApprouve).toHaveAttribute("href", `/api/pieces/${piece.id}/telecharger`)
      const [telechargement] = await Promise.all([
        utilisateur.waitForEvent("download"),
        lienApprouve.click(),
      ])
      const cheminTelecharge = await telechargement.path()
      expect(cheminTelecharge).toBeTruthy()
      const empreinteUtilisateur = createHash("sha256")
        .update(await readFile(cheminTelecharge as string))
        .digest("hex")
      expect(empreinteUtilisateur).toBe(empreinteDirecte)

      const partiel = await utilisateur.request.get(`/api/pieces/${piece.id}/telecharger`, {
        headers: { Range: "bytes=0-3" },
      })
      expect(partiel.status()).toBe(206)
      expect((await partiel.body()).subarray(0, 4).toString("utf8")).toBe("%PDF")
      expect((await detailDemande(utilisateur, demande.id_demande)).statut).toBe("approuvee")

      await utilisateur.goto(`/fiches/${CODE_REFERENCE}`)
      await expect(
        utilisateur
          .getByTestId("pieces-jointes")
          .getByTestId(`piece-download-${piece.id}`),
      ).toHaveAttribute("href", `/api/pieces/${piece.id}/telecharger`)

      // L'autre approbateur a aussi été notifié de la création; « tout marquer
      // lu » doit vider son compteur sans toucher à la décision déjà prise.
      const approbateur1 = await ouvrirPoste(browser, contextes, APPROBATEUR)
      const notificationAutreApprobateur = await notificationPourDemande(
        approbateur1,
        demande.id_demande,
        "nouvelle_demande",
      )
      expect(notificationAutreApprobateur.lue).toBe(false)
      await approbateur1.goto("/notifications")
      await expect(approbateur1.getByTestId("notifications-app")).toContainText(
        /[1-9]\d* notification\(s\) non lue\(s\)/,
      )
      await approbateur1.getByRole("button", { name: "Tout marquer lu" }).click()
      await expect(approbateur1.getByTestId("notifications-app")).toContainText(
        "0 notification(s) non lue(s)",
      )
      expect((await notificationsDe(approbateur1)).non_lues).toBe(0)
    } finally {
      await fermerPostes(contextes)
    }
  })

  test("une modification demandée par utilisateur est exécutée seulement après approbation", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    let idDemande: number | null = null
    let utilisateur: Page | null = null
    try {
      const approbateur = await ouvrirPoste(browser, contextes, APPROBATEUR_2)
      await ramenerAValider(approbateur, CODE_REFERENCE)
      utilisateur = await ouvrirPoste(browser, contextes, UTILISATEUR)

      const avant = await etatFiche(utilisateur, CODE_REFERENCE)
      const valeurAvant = champDesignation(avant).valeur_normalisee
      const valeurProposee = `Designation-Lot1-${suffixe("modification")}`
      const motif = `Designation relue sur le PDF source (${suffixe("motif-modification")}).`
      const demande = await creerDemandeModificationDepuisFiche(
        utilisateur,
        CODE_REFERENCE,
        valeurProposee,
        motif,
      )
      idDemande = demande.id_demande
      expect(demande).toMatchObject({
        type_demande: "modification",
        statut: "en_attente",
        champ: CHAMP_DESIGNATION,
        valeur_proposee: valeurProposee,
      })
      expect(champDesignation(await etatFiche(utilisateur, CODE_REFERENCE)).valeur_normalisee).toBe(
        valeurAvant,
      )

      await deciderDemande(
        approbateur,
        demande.id_demande,
        "approuver",
        "Valeur confirmee ligne par ligne sur le document.",
        "Demande approuvée et exécutée.",
      )
      const executee = await detailDemande(approbateur, demande.id_demande)
      expect(executee.statut).toBe("executee")
      expect(executee.decideur).toBe(APPROBATEUR_2.identifiant)

      const apres = await etatFiche(utilisateur, CODE_REFERENCE)
      expect(apres.revision).toBe(avant.revision + 1)
      expect(champDesignation(apres).valeur_normalisee).toBe(valeurProposee)

      await utilisateur.goto(`/fiches/${CODE_REFERENCE}`)
      await expect(utilisateur.getByTestId(`valeur-${CHAMP_DESIGNATION}`)).toContainText(
        valeurProposee,
      )
      const notification = await notificationPourDemande(
        utilisateur,
        demande.id_demande,
        "execution",
      )
      expect(notification.statut_demande).toBe("executee")
    } finally {
      if (utilisateur && idDemande !== null) {
        await annulerDemandeSiEnAttente(utilisateur, idDemande)
      }
      await fermerPostes(contextes)
    }
  })

  test("auto-approbation refusée par le serveur, puis refus motivé par le second approbateur", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    let idDemande: number | null = null
    let demandeur: Page | null = null
    try {
      demandeur = await ouvrirPoste(browser, contextes, APPROBATEUR)
      const avant = await ramenerAValider(demandeur, CODE_REFERENCE)
      const valeurAvant = champDesignation(avant).valeur_normalisee
      const valeurProposee = `Designation-auto-${suffixe("auto-approbation")}`
      const motif = `Proposition a faire trancher par un pair (${suffixe("motif-auto")}).`
      const demande = await creerDemandeModificationDepuisFiche(
        demandeur,
        CODE_REFERENCE,
        valeurProposee,
        motif,
      )
      idDemande = demande.id_demande

      await ouvrirDemande(demandeur, demande.id_demande)
      await expect(
        demandeur.locator('[role="alert"]', { hasText: "Vous êtes le demandeur" }),
      ).toContainText("l’auto-approbation est interdite")
      const formulaire = demandeur.getByTestId("decision-form")
      await formulaire.locator("textarea").fill("Je tente de decider ma propre demande.")
      await formulaire.getByRole("button", { name: "Enregistrer la décision" }).click()
      await expect(
        demandeur.locator('[role="alert"]', { hasText: "Auto-approbation interdite" }),
      ).toBeVisible()
      const toujoursEnAttente = await detailDemande(demandeur, demande.id_demande)
      expect(toujoursEnAttente.statut).toBe("en_attente")
      expect(toujoursEnAttente.decideur).toBeNull()
      expect(champDesignation(await etatFiche(demandeur, CODE_REFERENCE)).valeur_normalisee).toBe(
        valeurAvant,
      )

      const secondApprobateur = await ouvrirPoste(browser, contextes, APPROBATEUR_2)
      await ouvrirDemande(secondApprobateur, demande.id_demande)
      const formulaireRefus = secondApprobateur.getByTestId("decision-form")
      await formulaireRefus.getByRole("button", { name: "Refuser", exact: true }).click()
      await expect(
        formulaireRefus.getByRole("button", { name: "Enregistrer la décision" }),
      ).toBeDisabled()
      const commentaireRefus = `Valeur non confirmee sur le document (${suffixe("refus")}).`
      await formulaireRefus.locator("textarea").fill(commentaireRefus)
      await formulaireRefus.getByRole("button", { name: "Enregistrer la décision" }).click()
      await expect(
        secondApprobateur.locator('[role="status"]', {
          hasText: "Demande refusée et demandeur notifié.",
        }),
      ).toBeVisible()

      const refusee = await detailDemande(secondApprobateur, demande.id_demande)
      expect(refusee.statut).toBe("refusee")
      expect(refusee.decideur).toBe(APPROBATEUR_2.identifiant)
      expect(refusee.commentaire_decision).toBe(commentaireRefus)
      expect(champDesignation(await etatFiche(demandeur, CODE_REFERENCE)).valeur_normalisee).toBe(
        valeurAvant,
      )
      const notification = await notificationPourDemande(demandeur, demande.id_demande, "decision")
      expect(notification.statut_demande).toBe("refusee")
      expect(notification.message).toContain(commentaireRefus)
    } finally {
      if (demandeur && idDemande !== null) {
        await annulerDemandeSiEnAttente(demandeur, idDemande)
      }
      await fermerPostes(contextes)
    }
  })

  test("une suppression approuvée est logique, visible dans l'état, puis la fiche est rouverte", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    let approbateur: Page | null = null
    let utilisateur: Page | null = null
    let idDemande: number | null = null
    let suppressionExecutee = false
    try {
      approbateur = await ouvrirPoste(browser, contextes, APPROBATEUR)
      const avant = await ramenerAValider(approbateur, CODE_SUPPRESSION)
      utilisateur = await ouvrirPoste(browser, contextes, UTILISATEUR)
      await nettoyerDemandesEnAttente(
        utilisateur,
        (demande) => demande.type_demande === "suppression" && demande.code_fiche === CODE_SUPPRESSION,
      )

      const motif = `Fiche synthetique en double (${suffixe("suppression")}).`
      await utilisateur.goto(`/fiches/${CODE_SUPPRESSION}`)
      await expect(utilisateur.getByTestId("fiche-code")).toHaveText(CODE_SUPPRESSION)
      const formulaire = utilisateur.getByTestId("demande-suppression-form")
      await formulaire.locator("textarea").fill(motif)
      await formulaire.locator('input[type="checkbox"]').check()
      await formulaire.getByRole("button", { name: "Envoyer la demande de suppression" }).click()
      await expect(
        utilisateur.locator('[role="status"]', {
          hasText: "Demande de suppression logique envoyée à un approbateur.",
        }),
      ).toBeVisible()
      const demande = await trouverDemandeParMotif(utilisateur, motif)
      idDemande = demande.id_demande
      expect((await etatFiche(utilisateur, CODE_SUPPRESSION)).statut).toBe("a_valider")

      await deciderDemande(
        approbateur,
        demande.id_demande,
        "approuver",
        "Doublon synthetique confirme; archive conservee.",
        "Demande approuvée et exécutée.",
      )
      const executee = await detailDemande(approbateur, demande.id_demande)
      suppressionExecutee = executee.statut === "executee"
      expect(suppressionExecutee).toBe(true)

      const apres = await etatFiche(approbateur, CODE_SUPPRESSION)
      expect(apres.statut).toBe("supprimee")
      expect(apres.revision).toBeGreaterThan(avant.revision)
      const file = await lireJson<Array<{ code: string }>>(approbateur, "/api/validation/file")
      expect(file.some((fiche) => fiche.code === CODE_SUPPRESSION)).toBe(false)
      const notification = await notificationPourDemande(
        utilisateur,
        demande.id_demande,
        "execution",
      )
      expect(notification.statut_demande).toBe("executee")
    } finally {
      if (approbateur && suppressionExecutee) {
        await ramenerAValider(approbateur, CODE_SUPPRESSION)
      } else if (utilisateur && idDemande !== null) {
        await annulerDemandeSiEnAttente(utilisateur, idDemande)
      }
      await fermerPostes(contextes)
    }
  })

  test("lecture consulte et prévisualise, mais ne demande, ne télécharge et ne dépose rien", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    try {
      const lecture = await ouvrirPoste(browser, contextes, LECTURE)
      expect(await sessionDe(lecture)).toMatchObject({
        identifiant: LECTURE.identifiant,
        role: "lecture",
        mode: "nominatif",
      })
      const navigation = lecture.getByTestId("nav")
      await expect(navigation.getByRole("link", { name: /Validation/ })).toHaveCount(0)
      await expect(navigation.getByRole("link", { name: /Nouveau dossier/ })).toHaveCount(0)
      await expect(navigation.getByTestId("session-identite")).toContainText("Lecture seule")

      const piece = await piecePrincipale(lecture, CODE_REFERENCE)
      await lecture.goto(`/fiches/${CODE_REFERENCE}`)
      await expect(lecture.getByTestId("fiche-code")).toHaveText(CODE_REFERENCE)
      await expect(lecture.getByTestId("demandes-fiche")).toHaveCount(0)
      const listePieces = lecture.getByTestId("pieces-jointes")
      await expect(listePieces.getByTestId(`piece-download-${piece.id}`)).toHaveCount(0)
      await expect(listePieces.getByTestId(`piece-download-request-${piece.id}`)).toHaveCount(0)
      await expect(
        lecture
          .getByTestId("visionneuse-piece")
          .getByRole("link", { name: "Ouvrir l’aperçu dans un nouvel onglet" }),
      ).toHaveAttribute("href", `/api/pieces/${piece.id}/apercu`)

      const telechargement = await lecture.request.get(`/api/pieces/${piece.id}/telecharger`)
      expect(telechargement.status(), await telechargement.text()).toBe(403)
      const creation = await lecture.request.post("/api/approvals", {
        headers: await csrfHeadersForPage(lecture),
        data: {
          type_demande: "telechargement",
          code_fiche: CODE_REFERENCE,
          id_piece: piece.id,
          motif: "Tentative interdite depuis le role lecture.",
        },
      })
      expect(creation.status(), await creation.text()).toBe(403)
      const fileDecision = await lecture.request.get("/api/approvals?vue=a_decider")
      expect(fileDecision.status(), await fileDecision.text()).toBe(403)

      await lecture.goto("/approvals")
      await expect(lecture.getByTestId("approvals-app")).toBeVisible()
      await expect(lecture.getByTestId("vue-a-decider")).toHaveCount(0)
      await expect(lecture.getByTestId("decision-form")).toHaveCount(0)

      await lecture.goto("/validation")
      await expect(lecture.getByTestId("acces-refuse")).toContainText("approbateur nominatif")
      await lecture.goto("/nouveau")
      await expect(lecture.getByTestId("acces-refuse")).toContainText("rôle lecture")

      await lecture.goto("/notifications")
      await expect(lecture.getByTestId("notifications-app")).toBeVisible()
      expect((await notificationsDe(lecture)).notifications).toEqual([])
    } finally {
      await fermerPostes(contextes)
    }
  })

  test("admin nominatif et secours ne décident jamais; seul le demandeur annule sa demande", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    let utilisateur: Page | null = null
    let idDemande: number | null = null
    try {
      utilisateur = await ouvrirPoste(browser, contextes, UTILISATEUR)
      const piece = await piecePrincipale(utilisateur, CODE_REFERENCE)
      await nettoyerDemandesEnAttente(
        utilisateur,
        (demande) => demande.type_demande === "telechargement" && demande.id_piece === piece.id,
      )
      const motif = `Demande temoin pour les refus admin (${suffixe("admin-refus")}).`
      const demande = await posterJson<Demande>(
        utilisateur,
        "/api/approvals",
        {
          type_demande: "telechargement",
          code_fiche: CODE_REFERENCE,
          id_piece: piece.id,
          motif,
        },
        201,
      )
      idDemande = demande.id_demande

      const admin = await ouvrirPoste(browser, contextes, ADMIN)
      expect(await sessionDe(admin)).toMatchObject({
        identifiant: ADMIN.identifiant,
        role: "admin",
        mode: "nominatif",
      })
      await admin.goto(`/fiches/${CODE_REFERENCE}`)
      await expect(admin.getByTestId("demandes-fiche")).toHaveCount(0)
      const listeAdmin = admin.getByTestId("pieces-jointes")
      await expect(listeAdmin.getByTestId(`piece-download-${piece.id}`)).toHaveCount(0)
      await expect(listeAdmin.getByTestId(`piece-download-request-${piece.id}`)).toHaveCount(0)

      await admin.goto("/approvals")
      await expect(admin.getByTestId("approvals-app")).toBeVisible()
      await expect(admin.getByTestId("vue-a-decider")).toHaveCount(0)
      await expect(admin.getByTestId("vue-toutes")).toHaveCount(0)
      await expect(admin.getByTestId("decision-form")).toHaveCount(0)

      const detailAdmin = await admin.request.get(`/api/approvals/${demande.id_demande}`)
      expect(detailAdmin.status(), await detailAdmin.text()).toBe(404)
      const decisionAdmin = await admin.request.post(
        `/api/approvals/${demande.id_demande}/decide`,
        {
          headers: await csrfHeadersForPage(admin),
          data: { decision: "approuver", commentaire: "Tentative admin interdite." },
        },
      )
      expect(decisionAdmin.status(), await decisionAdmin.text()).toBe(403)
      const creationAdmin = await admin.request.post("/api/approvals", {
        headers: await csrfHeadersForPage(admin),
        data: {
          type_demande: "telechargement",
          code_fiche: CODE_REFERENCE,
          id_piece: piece.id,
          motif: "Tentative de demande par admin technique.",
        },
      })
      expect(creationAdmin.status(), await creationAdmin.text()).toBe(403)
      const telechargementAdmin = await admin.request.get(`/api/pieces/${piece.id}/telecharger`)
      expect(telechargementAdmin.status(), await telechargementAdmin.text()).toBe(403)

      const secours = await ouvrirPoste(browser, contextes)
      expect(await sessionDe(secours)).toMatchObject({
        identifiant: "secours",
        role: "admin",
        mode: "secours",
      })
      await expect(secours.getByTestId("nav").getByRole("link", { name: /Demandes/ })).toHaveCount(0)
      await expect(secours.getByTestId("notifications-bell")).toHaveCount(0)
      await secours.goto("/approvals")
      await expect(secours.getByTestId("acces-refuse")).toContainText("compte de secours")
      await secours.goto("/notifications")
      await expect(secours.getByTestId("acces-refuse")).toContainText("compte de secours")
      const listeSecours = await secours.request.get("/api/approvals")
      expect(listeSecours.status(), await listeSecours.text()).toBe(403)
      const decisionSecours = await secours.request.post(
        `/api/approvals/${demande.id_demande}/decide`,
        {
          headers: await csrfHeadersForPage(secours),
          data: { decision: "approuver", commentaire: "Tentative secours interdite." },
        },
      )
      expect(decisionSecours.status(), await decisionSecours.text()).toBe(403)

      const approbateur = await ouvrirPoste(browser, contextes, APPROBATEUR)
      expect((await detailDemande(approbateur, demande.id_demande)).statut).toBe("en_attente")
      const annulationParApprobateur = await approbateur.request.post(
        `/api/approvals/${demande.id_demande}/annuler`,
        {
          headers: await csrfHeadersForPage(approbateur),
          data: {},
        },
      )
      expect(annulationParApprobateur.status(), await annulationParApprobateur.text()).toBe(403)

      await ouvrirDemande(utilisateur, demande.id_demande)
      await utilisateur.getByRole("button", { name: "Annuler ma demande" }).click()
      await utilisateur.getByRole("button", { name: "Confirmer l’annulation" }).click()
      await expect(
        utilisateur.locator('[role="status"]', { hasText: "Demande annulée." }),
      ).toBeVisible()
      expect((await detailDemande(utilisateur, demande.id_demande)).statut).toBe("annulee")
      idDemande = null
    } finally {
      if (utilisateur && idDemande !== null) {
        await annulerDemandeSiEnAttente(utilisateur, idDemande)
      }
      await fermerPostes(contextes)
    }
  })

  test("admin change un rôle depuis Comptes et révoque immédiatement les sessions du compte", async ({ browser }) => {
    const contextes: BrowserContext[] = []
    let admin: Page | null = null
    let roleRestaure = false

    async function changerRoleDepuisComptes(
      role: "utilisateur" | "lecture",
      message: RegExp,
    ): Promise<void> {
      if (!admin) throw new Error("poste admin absent")
      await admin.goto("/comptes")
      await expect(admin.getByTestId("comptes-app")).toBeVisible()
      const ligne = admin.getByTestId(`compte-${LECTURE.identifiant}`)
      await expect(ligne).toBeVisible()
      await ligne.locator("select").selectOption(role)
      const bouton = ligne.getByRole("button", { name: "Changer le rôle" })
      await expect(bouton).toBeEnabled()
      await bouton.click()
      await expect(admin.locator('[role="status"]', { hasText: message })).toBeVisible()
      await expect(ligne.locator("select")).toHaveValue(role)
    }

    try {
      const victimeAvant = await ouvrirPoste(browser, contextes, LECTURE)
      expect(await sessionDe(victimeAvant)).toMatchObject({
        identifiant: LECTURE.identifiant,
        role: "lecture",
        mode: "nominatif",
      })

      admin = await ouvrirPoste(browser, contextes, ADMIN)
      await admin.goto("/comptes")
      const ligneAdmin = admin.getByTestId(`compte-${ADMIN.identifiant}`)
      await expect(ligneAdmin.locator("select")).toBeDisabled()
      await expect(ligneAdmin.getByRole("button", { name: "Changer le rôle" })).toBeDisabled()
      await expect(ligneAdmin).toContainText("Vous ne pouvez pas changer votre propre rôle")

      await changerRoleDepuisComptes(
        "utilisateur",
        /Lecture seule → Utilisateur\. [1-9]\d* session\(s\) révoquée\(s\) immédiatement\./,
      )
      const ancienneSession = await victimeAvant.request.get("/api/auth/session")
      expect(ancienneSession.status(), await ancienneSession.text()).toBe(401)

      const victimeUtilisateur = await ouvrirPoste(browser, contextes, LECTURE)
      expect(await sessionDe(victimeUtilisateur)).toMatchObject({
        identifiant: LECTURE.identifiant,
        role: "utilisateur",
        mode: "nominatif",
      })
      await expect(victimeUtilisateur.getByTestId("session-identite")).toContainText("Utilisateur")

      await changerRoleDepuisComptes(
        "lecture",
        /Utilisateur → Lecture seule\. [1-9]\d* session\(s\) révoquée\(s\) immédiatement\./,
      )
      roleRestaure = true
      const sessionUtilisateurRevoquee = await victimeUtilisateur.request.get("/api/auth/session")
      expect(sessionUtilisateurRevoquee.status(), await sessionUtilisateurRevoquee.text()).toBe(401)

      const victimeRestauree = await ouvrirPoste(browser, contextes, LECTURE)
      expect(await sessionDe(victimeRestauree)).toMatchObject({
        identifiant: LECTURE.identifiant,
        role: "lecture",
        mode: "nominatif",
      })
      const comptes = await lireJson<Array<{ identifiant: string; role: string }>>(
        admin,
        "/api/auth/utilisateurs",
      )
      expect(comptes.find((compte) => compte.identifiant === LECTURE.identifiant)?.role).toBe(
        "lecture",
      )
    } finally {
      if (admin && !roleRestaure) {
        await posterJson(
          admin,
          `/api/auth/utilisateurs/${encodeURIComponent(LECTURE.identifiant)}/role`,
          { role: "lecture" },
        )
      }
      await fermerPostes(contextes)
    }
  })
})
