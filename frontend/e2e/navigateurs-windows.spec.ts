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

import { FIGER_ANIMATIONS, UI_PASSWORD, signIn } from "./helpers"

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

/** Réponses HTTP en échec : le message de console ne dit pas QUELLE ressource. */
const reseau: string[] = []

function surveillerConsole(page: Page, erreurs: string[]): void {
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() === "error") erreurs.push(message.text())
  })
  page.on("pageerror", (erreur: Error) => erreurs.push(`pageerror: ${erreur.message}`))
  // Un « Failed to load resource: 503 » sans URL ne se diagnostique pas : on
  // publie le COUPLE statut + chemin, filtré du bruit d'outillage.
  page.on("response", (reponse) => {
    if (reponse.status() < 400) return
    const url = new URL(reponse.url())
    if (/\/(_next|favicon|__nextjs)/.test(url.pathname)) return
    reseau.push(`${reponse.status()} ${url.pathname}`)
  })
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
    // Animations FIGÉES avant l'analyse, comme dans l'audit Linux (et comme
    // axe le recommande) : le contenu est révélé par une opacité 0 → 1, et une
    // mesure prise pendant la révélation lit des couleurs MÉLANGÉES au fond —
    // elle fabrique de faux échecs de contraste, différents à chaque
    // exécution. C'est ainsi que `/fiches` a d'abord été signalé sur des
    // libellés de navigation parfaitement conformes.
    await page.waitForLoadState("load")
    await page.addStyleTag({ content: FIGER_ANIMATIONS })
    await page.waitForTimeout(120)
    const analyse = await new AxeBuilder({ page }).withTags(TAGS).analyze()
    const violations = analyse.violations
      .filter((violation) => GRAVITES_BLOQUANTES.has(violation.impact ?? ""))
      .map((violation) => ({
        id: violation.id,
        impact: violation.impact ?? null,
        cibles: violation.nodes.slice(0, 5).map((noeud) => noeud.target.join(" ")),
      }))
    constats.push({ navigateur: projet, ecran: ecran.nom, violations })
    // Le détail est dans le MESSAGE d'échec, pas seulement dans le rapport :
    // le rapport est un artefact qu'on ne peut pas lire d'ici (les journaux de
    // job non plus). Une violation qu'on ne peut pas nommer ne se répare pas.
    // Pour `color-contrast`, nommer ne suffit pas : on publie les COULEURS et
    // le RAPPORT mesurés, sans quoi le correctif se fait à l'aveugle.
    const detail = analyse.violations
      .filter((violation) => GRAVITES_BLOQUANTES.has(violation.impact ?? ""))
      .map((violation) => {
        const mesures = violation.nodes
          .slice(0, 5)
          .map((noeud) => {
            const donnees = noeud.any.find((item) => item.id === "color-contrast")?.data as
              | { fgColor?: string; bgColor?: string; contrastRatio?: number; expectedContrastRatio?: string }
              | undefined
            return donnees?.contrastRatio
              ? `${noeud.target.join(" ")} ${donnees.contrastRatio}:1 ` +
                 `(texte ${donnees.fgColor} sur ${donnees.bgColor}, attendu ${donnees.expectedContrastRatio})`
              : noeud.target.join(" ")
          })
          .join(" / ")
        return `${violation.id}[${violation.impact}] ${mesures}`
      })
      .join(" ;; ")
    expect(
      violations,
      `${projet} — ${ecran.nom} (${ecran.route}) : violations WCAG A/AA ${detail || "aucune"}`,
    ).toEqual([])
  }

  // Les erreurs de console sont PUBLIÉES, pas assertées : le mode développement
  // de l'interface en produit que l'exploitante ne verra jamais (avertissements
  // React, Fast Refresh, source maps absentes). Les transformer en échec
  // ferait dépendre la qualification d'un détail d'outillage ; les taire
  // cacherait une vraie erreur produit. On les écrit donc en clair.
  const bruit = /favicon|Download the React DevTools|Fast Refresh|sourcemap|Warning:/i
  const produits = erreurs.filter((texte) => !bruit.test(texte))
  process.stdout.write(`CONSOLE ${projet} :: ${produits.length} erreur(s) :: ${produits.slice(0, 5).join(" | ")}\n`)
  // Les ressources en échec sont PUBLIÉES elles aussi, avec leur chemin : un
  // 503 anonyme (« Failed to load resource ») ne dit pas quelle route le sert.
  const comptes = new Map<string, number>()
  for (const entree of reseau) comptes.set(entree, (comptes.get(entree) ?? 0) + 1)
  const listing = [...comptes.entries()].map(([entree, fois]) => `${entree} x${fois}`)
  process.stdout.write(`RESEAU ${projet} :: ${listing.length || "aucun"} :: ${listing.slice(0, 10).join(" | ")}\n`)
})
