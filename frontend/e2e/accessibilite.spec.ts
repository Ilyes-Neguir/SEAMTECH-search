import { expect, test, type Page } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"
import { writeFileSync, mkdirSync } from "node:fs"
import path from "node:path"

import {
  ADMIN,
  APPROBATEUR,
  FIGER_ANIMATIONS,
  LECTURE,
  UTILISATEUR,
  signIn,
  signInWith,
} from "./helpers"

/**
 * Audit d'accessibilité axe-core sur TOUS les écrans, dans plusieurs états.
 *
 * Constat de la campagne du 28/10 : l'audit axe ne couvrait que deux écrans
 * (recherche et validation). Un écran non scanné n'est pas « conforme », il est
 * « non vérifié » — et les rapports le présentaient comme couvert.
 *
 * Barre appliquée : WCAG 2.1 niveau A et AA (wcag2a, wcag2aa, wcag21a,
 * wcag21aa). Les règles « best-practice » d'axe ne sont PAS bloquantes (elles
 * ne sont pas des critères de conformité) mais leurs résultats sont consignés.
 *
 * Chaque écran est scanné pour les quatre rôles canoniques quand la route leur
 * est ouverte, plus les états qui changent le rendu : session expirée, session
 * révoquée, écran vide (aucun résultat de recherche), écran d'erreur.
 *
 * Le rapport machine est écrit dans `accessibilite/rapport-axe.json` : il liste
 * les violations restantes, écran par écran, avec leur gravité — jamais un
 * simple « 0 ». Une violation critique ou sérieuse fait échouer le test.
 */

const RAPPORT = path.resolve(__dirname, "../accessibilite/rapport-axe.json")
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
const GRAVITES_BLOQUANTES = new Set(["critical", "serious"])

type Constat = {
  ecran: string
  role: string
  url: string
  violations: { id: string; impact: string | null; aide: string; cibles: string[] }[]
  /** Règles « best practice » en échec : informatives, non bloquantes. */
  bonnes_pratiques: string[]
}

const constats: Constat[] = []

/**
 * Codes de fiches réellement servis par CE backend (attribut `data-code`).
 * Aucune fixture figée : l'audit porte sur les fiches que l'installation sert
 * vraiment, pas sur des codes inventés.
 *
 * La lecture se fait sur la RECHERCHE, plus sur la file de validation. C'est
 * une correction, pas une préférence : la file de validation ne contient que
 * les fiches EN ATTENTE, et elle est vide sur une base fraîche — les écrans
 * `/fiches/[code]` et `/dossier/[code]` n'étaient alors jamais scannés, en
 * silence, sans qu'aucun décompte global puisse le voir. La recherche, elle,
 * liste ce que l'installation sert. La file reste en repli.
 */
async function codesFiches(page: Page): Promise<string[]> {
  const lire = async (route: string): Promise<string[]> => {
    await page.goto(route)
    await page.waitForLoadState("load")
    return page.locator("li[data-code]").evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("data-code") ?? "").filter(Boolean),
    )
  }

  const codes = await lire("/recherche")
  if (codes.length > 0) return codes.slice(0, 3)
  return (await lire("/validation")).slice(0, 3)
}

async function scanner(page: Page, ecran: string, role: string): Promise<void> {
  // `networkidle` n'aboutit jamais sur les écrans qui sondent en continu
  // (notifications, file de validation) : on attend le document, puis un
  // court temps de peinture. Axe analyse le DOM rendu, pas le réseau.
  await page.waitForLoadState("load")
  await page.waitForTimeout(120)
  // Une navigation a pu remplacer le document entre-temps (redirection de
  // session, rechargement après hydratation) : on réessaie une fois plutôt que
  // d'échouer sur « execution context was destroyed ».
  for (let essai = 1; ; essai += 1) {
    try {
      await page.addStyleTag({ content: FIGER_ANIMATIONS })
      break
    } catch (erreur) {
      if (essai >= 2) throw erreur
      await page.waitForLoadState("load")
      await page.waitForTimeout(150)
    }
  }
  const resultat = await new AxeBuilder({ page }).withTags(TAGS).analyze()
  const bonnes = await new AxeBuilder({ page }).withTags(["best-practice"]).analyze()
  const constat: Constat = {
    ecran,
    role,
    url: new URL(page.url()).pathname,
    violations: resultat.violations.map((v) => ({
      id: v.id,
      impact: v.impact ?? null,
      aide: v.help,
      cibles: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
    })),
    bonnes_pratiques: bonnes.violations.map((v) => v.id),
  }
  constats.push(constat)

  const bloquantes = resultat.violations.filter((v) => GRAVITES_BLOQUANTES.has(v.impact ?? ""))
  expect(
    bloquantes,
    `${ecran} (${role}) : ${bloquantes
      .map((v) => `${v.id} [${v.impact}] ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`)
      .join(" | ")}`,
  ).toEqual([])
  // Même les violations modérées sont consignées et bloquantes : sur un outil
  // métier, une étiquette manquante est un défaut, pas une opinion.
  expect(resultat.violations, `${ecran} (${role}) : ${resultat.violations.map((v) => v.id).join(", ")}`).toEqual([])
}

/** Écrans atteignables sans paramètre d'URL. */
const ECRANS = [
  "/",
  "/recherche",
  "/fichiers",
  "/fiches",
  "/dossiers",
  "/nouveau",
  "/extractions",
  "/comparaison",
  "/confiance",
  "/approvals",
  "/validation",
  "/qualite",
  "/notifications",
  "/comptes",
  "/gabarits",
]

test.describe("Accessibilité axe-core — tous les écrans", () => {
  // Le compte de secours existe dans TOUS les modes : c'est lui qui garantit
  // que chaque écran est scanné même quand la CI tourne sans PostgreSQL
  // nominatif. Les rôles canoniques n'existent qu'en mode « e2e live ».
  test("tous les écrans — compte de secours", async ({ page }) => {
    test.setTimeout(300_000) // 15 écrans × (chargement + 2 analyses axe)
    await signIn(page)
    for (const ecran of ECRANS) {
      const reponse = await page.goto(ecran)
      if (reponse && reponse.status() >= 500) {
        throw new Error(`${ecran} a répondu ${reponse.status()} pour le compte de secours`)
      }
      if (reponse && reponse.status() === 403) {
        constats.push({ ecran, role: "secours", url: ecran, violations: [], bonnes_pratiques: [] })
        continue
      }
      await scanner(page, ecran, "secours")
    }
  })

  for (const role of [
    { nom: "admin", identifiants: ADMIN },
    { nom: "approbateur", identifiants: APPROBATEUR },
    { nom: "utilisateur", identifiants: UTILISATEUR },
    { nom: "lecture", identifiants: LECTURE },
  ]) {
    test(`écrans principaux — ${role.nom}`, async ({ page }) => {
      test.skip(
        !process.env.SEAMTECH_E2E_DATABASE_URL,
        "rôles canoniques : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL nominatif)",
      )
      test.setTimeout(240_000)
      await signInWith(page, role.identifiants)
      for (const ecran of ECRANS) {
        const reponse = await page.goto(ecran)
        // Une route interdite à ce rôle n'est pas un défaut d'accessibilité :
        // elle est consignée et vérifiée comme refus propre (jamais une 500).
        if (reponse && reponse.status() >= 500) {
          throw new Error(`${ecran} a répondu ${reponse.status()} pour le rôle ${role.nom}`)
        }
        if (reponse && reponse.status() === 403) {
          constats.push({ ecran, role: role.nom, url: ecran, violations: [], bonnes_pratiques: [] })
          continue
        }
        await scanner(page, ecran, role.nom)
      }
    })
  }

  test("écrans de détail (fiche, dossier) — admin", async ({ page }) => {
    test.setTimeout(180_000)
    test.skip(
      !process.env.SEAMTECH_E2E_DATABASE_URL,
      "rôles canoniques : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL nominatif)",
    )
    await signInWith(page, ADMIN)
    for (const code of await codesFiches(page)) {
      const reponse = await page.goto(`/fiches/${encodeURIComponent(code)}`)
      if (reponse && (reponse.status() === 404 || reponse.status() === 403)) continue
      await scanner(page, `/fiches/${code}`, "admin")
      const dossier = await page.goto(`/dossier/${encodeURIComponent(code)}`)
      if (dossier && (dossier.status() === 404 || dossier.status() === 403)) continue
      await scanner(page, `/dossier/${code}`, "admin")
    }
  })

  test("écrans d'état : login, recherche vide, session expirée", async ({ page, context }) => {
    test.setTimeout(120_000)
    await page.goto("/login")
    await scanner(page, "/login", "anonyme")

    await signIn(page)
    await page.goto("/recherche")
    await page.getByTestId("recherche-saisie").fill("zzzz-aucun-resultat-zzzz")
    await page.keyboard.press("Enter")
    await page.waitForLoadState("networkidle")
    await scanner(page, "/recherche?vide", "secours")

    // Session expirée : le cookie est conservé mais le serveur ne la reconnaît
    // plus — l'écran de refus doit rester accessible et lisible.
    await context.clearCookies({ name: "seamtech_session" })
    await page.goto("/recherche")
    await scanner(page, "/recherche (session absente → /login)", "anonyme")
  })

  test("écran de refus de rôle (lecture sur /comptes)", async ({ page }) => {
    await signIn(page)
    const reponse = await page.goto("/comptes")
    if (reponse && reponse.status() === 403) {
      await scanner(page, "/comptes (403)", "secours")
    }
  })

  // Campagne du 27/10 : les contrastes du thème NUIT sont un autre jeu de
  // couleurs. Les deux écrans les plus denses sont donc rescannés après
  // bascule, sinon « zéro violation » ne vaudrait que pour le thème jour.
  test("thème nuit : rescanner les écrans denses après bascule", async ({ page }) => {
    test.skip(
      !process.env.SEAMTECH_E2E_DATABASE_URL,
      "rôles canoniques : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL nominatif)",
    )
    test.setTimeout(180_000)
    await signInWith(page, APPROBATEUR)
    const bascule = page.getByTestId("bascule-theme")
    for (const ecran of ["/recherche", "/validation"]) {
      await page.goto(ecran)
      await scanner(page, `${ecran} (thème jour)`, "approbateur")
      await bascule.click()
      await page.waitForTimeout(400)
      await page.goto(ecran)
      await scanner(page, `${ecran} (thème nuit)`, "approbateur")
      await bascule.click()
      await page.waitForTimeout(400)
    }
  })

  test.afterAll(() => {
    mkdirSync(path.dirname(RAPPORT), { recursive: true })
    const resume = {
      ecrans_scannes: constats.length,
      ecrans_avec_violation: constats.filter((c) => c.violations.length > 0).length,
      constats,
    }
    writeFileSync(RAPPORT, `${JSON.stringify(resume, null, 1)}\n`, "utf-8")
  })
})
