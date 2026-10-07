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
 *   2. import d'un dossier réel (comptabilité des fichiers, extraction) ;
 *   3. visibilité en RECHERCHE après import ;
 *   4. ouverture de la fiche, révision lue, puis VALIDATION ;
 *   5. aperçu PDF servi par l'API ;
 *   6. téléchargement de l'ORIGINAL (octets %PDF vérifiés) ;
 *   7. téléchargement du RAPPORT GÉNÉRÉ (octets %PDF vérifiés).
 *
 * Le job CI vérifie en plus, APRÈS ces parcours, que le compteur de paquets
 * REJETÉS du compte applicatif est resté à ZÉRO : aucune dépendance externe
 * cachée n'a été tentée pendant les parcours. Le contrôle NÉGATIF (une requête
 * sortante doit ÉCHOUER) est exécuté avant, sans quoi « zéro paquet rejeté » ne
 * prouverait rien.
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
  const CODE = "REF-2026-CLIENT123"

  test("import, recherche, aperçu, téléchargements et validation sans réseau externe", async ({ page }) => {
    // 1. CONNEXION nominative (formulaire réel, cookie signé).
    await signInWith(page, OPERATEUR)

    // 2. IMPORT d'un dossier réel par l'interface : le pipeline complet tourne
    //    (détection, extraction, écriture en base, stockage objet configuré).
    const dossier = path.join(RACINE, "sample_data", "CLIENT-123")
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    const champDossier = page.getByPlaceholder(/Chemin du dossier sur le serveur/i)
    await expect(champDossier).toBeVisible()
    await champDossier.fill(dossier)
    // L'identifiant d'import sert à récupérer le RAPPORT GÉNÉRÉ plus loin, avec
    // la session du navigateur (donc par l'API authentifiée, pas par un script).
    const reponseImport = page.waitForResponse(
      (reponse) => /^\/api\/imports\/\d+$/.test(new URL(reponse.url()).pathname),
      { timeout: 30000 },
    )
    await page.getByRole("button", { name: /Importation rapide/i }).click()
    await expect(page.getByText(/État :/i)).toBeVisible({ timeout: 30000 })
    await expect(page.getByText(new RegExp(CODE, "i"))).toBeVisible({ timeout: 30000 })
    const corpsImport = (await (await reponseImport).json()) as { import_id?: number }
    const identifiantImport = corpsImport.import_id ?? null

    // 3. VISIBILITÉ EN RECHERCHE après import (index local, modèle local).
    await page.goto(`/recherche?q=${encodeURIComponent(CODE)}`)
    const ouverture = page.getByRole("link", { name: new RegExp(`Ouvrir la fiche ${CODE}`) }).first()
    await expect(ouverture).toBeVisible({ timeout: 20000 })

    // 4. OUVERTURE DE LA FICHE : aperçu PDF servi par l'API (aucun CDN, aucune
    //    police distante) + ORIGINAL téléchargeable depuis le poste.
    const apercu = page.waitForResponse(
      (reponse) => /\/api\/pieces\/\d+\/apercu/.test(new URL(reponse.url()).pathname) && reponse.status() === 200,
      { timeout: 20000 },
    )
    await ouverture.click()
    await expect(page.getByTestId("panneau-pdf")).toBeVisible({ timeout: 20000 })
    await apercu

    const lienOriginal = page.getByRole("link", { name: /Télécharger .*\.pdf/i }).first()
    await expect(lienOriginal).toBeVisible({ timeout: 20000 })
    const [telechargement] = await Promise.all([page.waitForEvent("download"), lienOriginal.click()])
    const chemin = await telechargement.path()
    expect(chemin, "le fichier téléchargé doit exister sur le disque du poste").toBeTruthy()
    const { readFileSync } = await import("node:fs")
    const octets = readFileSync(chemin!)
    expect(octets.subarray(0, 4).toString()).toBe("%PDF")
    expect(octets.length).toBeGreaterThan(1000)

    // 5. RAPPORT GÉNÉRÉ : octets vérifiés, servis à la session du navigateur.
    if (identifiantImport !== null) {
      const rapport = await page.request.get(`/api/imports/${identifiantImport}/artifacts/report_pdf`)
      expect(rapport.status(), await rapport.text()).toBe(200)
      const corps = await rapport.body()
      expect(corps.subarray(0, 4).toString()).toBe("%PDF")
      expect(corps.length).toBeGreaterThan(1000)
    }

    // 6. VALIDATION sur l'écran de validation : la révision est lue (instantané)
    //    puis la décision porte sur CETTE révision — sans réseau externe.
    await page.goto("/validation")
    const ligne = page.locator(`li[data-code="${CODE}"]`)
    await expect(ligne).toBeVisible({ timeout: 20000 })
    await ligne.getByTestId("code-fiche").click()
    await expect(page.getByTestId("titre-fiche")).toHaveText(CODE)
    await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", /^[0-9]+$/, {
      timeout: 15000,
    })
    await page.getByTestId("bouton-valider").click()
    await expect(page.getByTestId("message-ok")).toBeVisible({ timeout: 20000 })
  })
})
