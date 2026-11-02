/**
 * Qualification des NAVIGATEURS de l'atelier : Chrome et Edge, sur Windows.
 *
 * Pourquoi ce fichier existe.
 *
 * La campagne d'accessibilité (``e2e/accessibilite.spec.ts``) et les parcours
 * complets tournent sur Linux, pilotés par le Chromium fourni par Playwright.
 * Ce n'est pas la même chose que « l'interface marche dans le navigateur de
 * l'atelier » : l'opératrice ouvrira Chrome ou Edge, sur Windows, et parlera à
 * un serveur qui, lui, tourne dans un conteneur Linux. Tant que ce cas n'est
 * pas exécuté, il n'est pas qualifié — il est supposé.
 *
 * Ce qu'il prouve, pour CHAQUE navigateur (un projet Playwright par
 * navigateur, lancé avec ``channel: "chrome"`` / ``channel: "msedge"``, donc
 * les binaires RÉELS de la machine et non ceux de Playwright) :
 *
 * 1. le navigateur annoncé est bien celui qui tourne : l'agent-utilisateur est
 *   vérifié et publié (Chrome porte « Chrome/ » sans « Edg/ », Edge porte
 *   « Edg/ ») — sinon un vert ne voudrait rien dire ;
 * 2. l'authentification par session fonctionne : la page d'accueil se charge
 *   après connexion ;
 * 3. les écrans de lecture sont conformes WCAG 2.1 A/AA (axe-core), y compris
 *   dans leur état VIDE ;
 * 4. aucune erreur de console n'est émise au chargement.
 *
 * Ce qu'il ne prouve PAS, et qui reste non qualifié : l'IMPORT d'un dossier
 * depuis Windows. Le produit refuse l'import faute de primitives POSIX de
 * confinement (``seamtech_search/plateforme.py``) ; les écrans d'import sont
 * donc vus ici dans leur état vide ou refusé, jamais en succès. Le parcours
 * d'import n'est qualifié que dans un conteneur Linux (cible de déploiement
 * Windows = Docker Desktop), où la CI Linux le couvre déjà.
 */

import { expect, test, type ConsoleMessage, type Page } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"
import { writeFileSync } from "node:fs"
import path from "node:path"

import { UI_PASSWORD, signIn } from "./helpers"

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
const GRAVITES_BLOQUANTES = new Set(["critical", "serious"])
const RAPPORT = path.resolve(__dirname, "../navigateurs-rapport.json")

/** Écrans de LECTURE : accessibles à un compte d'atelier, sans import. */
const ECRANS = [
  { nom: "accueil", route: "/" },
  { nom: "recherche", route: "/recherche" },
  { nom: "fiches", route: "/fiches" },
  { nom: "validation", route: "/validation" },
  { nom: "dossiers", route: "/dossiers" },
]

const agents: { navigateur: string; projet: string; agent: string }[] = []
const constats: {
  navigateur: string
  ecran: string
  violations: { id: string; impact: string | null; cibles: string[] }[]
}[] = []

function surveillerConsole(page: Page, erreurs: string[]): void {
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() === "error") erreurs.push(message.text())
  })
  page.on("pageerror", (erreur: Error) => erreurs.push(`pageerror: ${erreur.message}`))
}

test.afterAll(() => {
  writeFileSync(
    RAPPORT,
    JSON.stringify({ agents, constats }, null, 2),
    "utf-8",
  )
})

test("le navigateur annoncé est bien celui qui exécute les tests", async ({ page }) => {
  const agent = await page.evaluate(() => navigator.userAgent)
  const projet = test.info().project.name
  agents.push({ navigateur: projet, projet, agent })
  // Sans cette vérification, un vert pourrait venir du Chromium de Playwright
  // au lieu du navigateur de l'atelier : la qualification ne vaudrait rien.
  if (projet === "chrome") {
    expect(agent, `agent-utilisateur inattendu pour Chrome : ${agent}`).toContain("Chrome/")
    expect(agent).not.toContain("Edg/")
  } else if (projet === "msedge") {
    expect(agent, `agent-utilisateur inattendu pour Edge : ${agent}`).toContain("Edg/")
  } else {
    throw new Error(`projet de navigateur inattendu : ${projet}`)
  }
  // Publié dans le journal : la version exacte du navigateur qualifié.
  process.stdout.write(`QUALIFIE ${projet} :: ${agent}\n`)
})

test("connexion, écrans de lecture et accessibilité WCAG 2.1 A/AA", async ({ page }) => {
  const projet = test.info().project.name
  const erreurs: string[] = []
  surveillerConsole(page, erreurs)

  await signIn(page, UI_PASSWORD)

  for (const ecran of ECRANS) {
    await page.goto(ecran.route)
    // L'écran doit réellement se rendre : un écran vide n'est pas un écran.
    await expect(page.locator("body")).toBeVisible()
    const analyse = await new AxeBuilder({ page }).withTags(TAGS).analyze()
    const violations = analyse.violations
      .filter((violation) => GRAVITES_BLOQUANTES.has(violation.impact ?? ""))
      .map((violation) => ({
        id: violation.id,
        impact: violation.impact ?? null,
        cibles: violation.nodes.slice(0, 3).map((noeud) => noeud.target.join(" ")),
      }))
    constats.push({ navigateur: projet, ecran: ecran.nom, violations })
    expect(
      violations,
      `${projet} — ${ecran.nom} (${ecran.route}) : violations WCAG A/AA`,
    ).toEqual([])
  }

  // Les erreurs de console « réseau » du mode développement sont écartées :
  // ce sont celles de l'outil, pas du produit.
  const produits = erreurs.filter((texte) => !/favicon|Download the React DevTools/i.test(texte))
  expect(produits, `${projet} — erreurs de console`).toEqual([])
})
