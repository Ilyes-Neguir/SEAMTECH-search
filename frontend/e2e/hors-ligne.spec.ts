/**
 * HORS LIGNE — PLEINE PILE, sortie réseau EXTERNE réellement bloquée.
 *
 * Revue indépendante du 2026-10-07 (constat n°4) : le garde socket de
 * `scripts/verifier_hors_ligne.py` ne couvre QUE le processus Python d'un
 * diagnostic ; il ne dit rien du navigateur, de Next.js, du worker séparé ni
 * des bibliothèques natives. Cette épreuve-ci exerce le VRAI parcours d'atelier
 * pendant que la sortie réseau de la pile est bloquée au niveau du système
 * (chaîne iptables dédiée au compte applicatif, job CI `hors-ligne-reel`) :
 *
 *   1. connexion nominative par le formulaire ;
 *   2. import d'un dossier réel (comptabilité des fichiers, extraction) par le
 *      WORKER SÉPARÉ, avec stockage objet ;
 *   3. RAPPORT GÉNÉRÉ relu en octets par la session du navigateur ;
 *   4. visibilité en RECHERCHE DOCUMENTAIRE du fichier importé (« Fichiers »,
 *      `/api/search` : c'est cet index-là que l'import alimente), avec sa
 *      visionneuse ;
 *   5. fiche réelle du jeu de données retrouvée par la RECHERCHE MÉTIER
 *      (`/recherche`) puis ouverte : PDF RENDU par le navigateur (pdf.js local,
 *      aucune ressource distante) et ORIGINAL relu en octets
 *      (`/api/pieces/{id}/telecharger`) ;
 *   6. décision de VALIDATION prise sur la révision lue (verrou optimiste).
 *
 * Le job CI vérifie en plus, APRÈS ces parcours, que le compteur de paquets
 * REJETÉS du compte applicatif est resté à ZÉRO : aucune dépendance externe
 * cachée n'a été tentée pendant les parcours. Le contrôle NÉGATIF (une requête
 * sortante doit ÉCHOUER) est exécuté avant, sans quoi « zéro paquet rejeté » ne
 * prouverait rien.
 *
 * PORTÉE, DITE SANS EMBELLISSEMENT : le blocage `iptables` s'applique aux
 * processus du COMPTE APPLICATIF (web, worker séparé, front). Le NAVIGATEUR,
 * lui, tourne sous l'utilisateur du runner et n'est pas visé par ce blocage :
 * ses requêtes sont donc surveillées DANS le test — toute ressource demandée
 * hors de la boucle locale fait échouer le parcours (aucun CDN, aucune police
 * distante, aucun service en ligne ne peut se cacher derrière l'interface).
 *
 * Exécution : `SEAMTECH_E2E_HORS_LIGNE=1` (jamais dans la suite par défaut : la
 * pile doit tourner avec stockage objet et migrations, et la sortie réseau doit
 * être bloquée pour que l'épreuve ait un sens).
 */

import { expect, test } from "@playwright/test"
import path from "node:path"
import { OPERATEUR, signInWith } from "./helpers"

test.describe("hors ligne — pile réelle, sortie réseau bloquée", () => {
  test.skip(
    process.env.SEAMTECH_E2E_HORS_LIGNE !== "1",
    "hors ligne pleine pile : requiert SEAMTECH_E2E_HORS_LIGNE=1 (pile réelle + sortie réseau bloquée)",
  )

  const RACINE = path.resolve(__dirname, "../..")
  // Référence EXTRAITE du PDF importé (elle apparaît dans le panneau d'import,
  // mais ce n'est PAS un code de fiche) et code de la fiche RÉELLE du jeu de
  // données, seule fiche que la recherche métier peut rendre.
  const REFERENCE = "REF-2026-CLIENT123"
  const CODE_FICHE = "7792-SO"

  test("import, recherche, aperçu, téléchargements et validation sans réseau externe", async ({ page }) => {
    // Budget EXPLICITE (180 s) : ce parcours enchaîne un IMPORT réel (pipeline
    // complet par le WORKER SÉPARÉ, stockage objet configuré), un aperçu PDF,
    // deux téléchargements ET une décision. Le délai par défaut (30 s) n'est
    // pas une exigence du produit ; les attentes INTERNES restent bornées
    // (60 s pour l'import, 20 s ailleurs) — mesuré en CI : le parcours dépasse
    // le délai par défaut avant même la seconde moitié.
    test.setTimeout(180_000)
    // 0. GARDE-FOU NAVIGATEUR. Le blocage `iptables` du job couvre les
    //    processus du compte applicatif (web, worker séparé, front), PAS le
    //    navigateur (utilisateur du runner). On prouve donc ici que les seules
    //    ressources demandées par la page viennent de la boucle locale : une
    //    interface d'atelier ne doit dépendre d'aucun CDN ni service en ligne.
    //    (L'UI n'en déclare aucun — `app/layout.tsx` refuse même
    //    `next/font/google` ; ce contrôle le CONSTATE au lieu de le supposer.)
    const requetesExternes: string[] = []
    page.on("request", (requete) => {
      const url = requete.url()
      if (!/^https?:/i.test(url)) return
      const hote = new URL(url).hostname
      if (!["127.0.0.1", "localhost", "::1"].includes(hote)) requetesExternes.push(url)
    })
    // 1. CONNEXION nominative (formulaire réel, cookie signé).
    await signInWith(page, OPERATEUR)

    // 2. IMPORT d'un dossier réel par l'interface : le pipeline complet tourne
    //    (détection, extraction, écriture en base, stockage objet configuré).
    const dossier = path.join(RACINE, "sample_data", "CLIENT-123")
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    const champDossier = page.getByPlaceholder(/Chemin du dossier sur le serveur/i)
    await expect(champDossier).toBeVisible()
    await champDossier.fill(dossier)
    await page.getByRole("button", { name: /Importation rapide/i }).click()
    await expect(page.getByText(/État :/i)).toBeVisible({ timeout: 60000 })
    await expect(page.getByText(new RegExp(REFERENCE, "i"))).toBeVisible({ timeout: 60000 })
    // L'identifiant d'import est lu DANS L'ÉCRAN : c'est le lien du rapport
    // généré, tel qu'un opérateur le voit et le suit. Aucune attente d'URL
    // devinée : l'import part en file avec un identifiant **UUID** (hexadécimal,
    // pas un entier) et l'écran ne publie le lien qu'au résultat final.
    // (Mesure CI du premier passage réel : ce test attendait un GET
    // « /api/imports/<entier> » — motif impossible à satisfaire, attente morte
    // qui consommait tout le budget.)
    const lienRapport = page.getByRole("link", { name: /Télécharger le rapport PDF/i })
    await expect(lienRapport).toBeVisible({ timeout: 60000 })
    const hrefRapport = (await lienRapport.getAttribute("href")) ?? ""
    expect(hrefRapport, "lien du rapport généré").toMatch(
      /^\/api\/imports\/[0-9a-zA-Z]+\/artifacts\/report_pdf$/,
    )

    // 3. RAPPORT GÉNÉRÉ : octets vérifiés, servis à la session du navigateur
    //    (l'URL vient de l'écran — donc du résultat réellement produit).
    const rapport = await page.request.get(hrefRapport)
    expect(rapport.status(), await rapport.text()).toBe(200)
    const corps = await rapport.body()
    expect(corps.subarray(0, 4).toString()).toBe("%PDF")
    expect(corps.length).toBeGreaterThan(1000)

    // 4. VISIBILITÉ EN RECHERCHE DOCUMENTAIRE du fichier importé. C'est l'écran
    //    « Fichiers » (API `/api/search`) qui indexe les documents importés —
    //    la référence « REF-2026-CLIENT123 » est une référence EXTRAITE, elle
    //    n'est pas un code de fiche : l'épreuve cherche donc le fichier par son
    //    NOM, garanti indexé par l'import (et le navigateur le retrouve seul).
    await page.goto("/fichiers")
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    const champFichiers = page.getByPlaceholder(/Rechercher des fichiers/i)
    await expect(champFichiers).toBeVisible()
    await champFichiers.fill("fiche-technique.pdf")
    await champFichiers.press("Enter")
    const resultatFichier = page
      .locator('[role="button"][tabindex="0"]')
      .filter({ hasText: /fiche-technique\.pdf/i })
      .first()
    await expect(resultatFichier).toBeVisible({ timeout: 30000 })
    await resultatFichier.click()
    await expect(page.getByTestId("visionneuse-piece")).toBeVisible({ timeout: 20000 })
    await expect(page.getByRole("link", { name: "Télécharger le fichier" })).toBeVisible()

    // 5. FICHE RÉELLE du jeu de données, retrouvée par la RECHERCHE MÉTIER puis
    //    ouverte : aperçu PDF servi par l'API locale (aucun CDN, aucune police
    //    distante) et ORIGINAL relu EN OCTETS par la session du navigateur —
    //    c'est le chemin exact d'un poste d'atelier (l'API sert le fichier,
    //    personne ne lit le disque du serveur).
    await page.goto(`/recherche?q=${encodeURIComponent("7792")}`)
    const ouverture = page.getByRole("link", { name: `Ouvrir la fiche ${CODE_FICHE}` })
    await expect(ouverture).toBeVisible({ timeout: 20000 })
    await ouverture.click()
    await expect(page).toHaveURL(new RegExp(`/fiches/${CODE_FICHE}$`))
    await expect(page.getByTestId("fiche-code")).toHaveText(CODE_FICHE)
    await expect(page.getByTestId("pieces-jointes")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("visionneuse-piece")).toBeVisible({ timeout: 20000 })
    // PDF RÉELLEMENT RENDU (pdf.js chargé localement, « 1 / N ») — c'est ce que
    // regarde l'opérateur ; une visionneuse vide ne prouverait rien.
    await expect(page.getByTestId("pdf-page")).toHaveText(/\d+ \/ \d+/, { timeout: 30000 })
    await expect(page.getByTestId("pdf-canvas")).toBeVisible()

    const lienOriginal = page.getByRole("link", { name: /Télécharger .*\.pdf/i }).first()
    await expect(lienOriginal).toBeVisible({ timeout: 20000 })
    const hrefOriginal = (await lienOriginal.getAttribute("href")) ?? ""
    expect(hrefOriginal, "lien de téléchargement de l'original").toMatch(/^\/api\/pieces\/\d+\/telecharger$/)
    const origine = await page.request.get(hrefOriginal)
    expect(origine.status(), await origine.text()).toBe(200)
    const octets = await origine.body()
    expect(octets.subarray(0, 4).toString()).toBe("%PDF")
    expect(octets.length).toBeGreaterThan(1000)

    // 6. VALIDATION sur l'écran de validation : la révision est lue (instantané)
    //    puis la décision porte sur CETTE révision — sans réseau externe.
    await page.goto("/validation")
    const ligne = page.locator(`li[data-code="${CODE_FICHE}"]`)
    await expect(ligne).toBeVisible({ timeout: 20000 })
    await ligne.getByTestId("code-fiche").click()
    await expect(page.getByTestId("titre-fiche")).toHaveText(CODE_FICHE)
    await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", /^[0-9]+$/, {
      timeout: 15000,
    })
    await page.getByTestId("bouton-valider").click()
    // Le TEXTE de la décision, pas seulement le bandeau : `message-ok` porte
    // aussi le message de rechargement — l'attendre par son seul identifiant
    // validerait avant que la décision soit appliquée (leçon du flaky mesuré
    // dans les scénarios de concurrence).
    await expect(page.getByTestId("message-ok")).toHaveText(/valider enregistré au journal/, {
      timeout: 20000,
    })

    // 7. AUCUNE ressource externe demandée par le navigateur de tout le
    //    parcours (voir le garde-fou du début) : le poste d'atelier n'a besoin
    //    ni d'Internet, ni d'un CDN, ni d'un service en ligne.
    expect(requetesExternes, "ressources externes demandées par le navigateur").toEqual([])
  })
})
