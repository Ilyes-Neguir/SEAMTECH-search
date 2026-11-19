/**
 * Contrastes de la palette — WCAG 2.1, critère 1.4.3 « Contraste (minimum) »,
 * niveau AA : 4,5:1 pour le texte courant, 3:1 pour le texte « grand »
 * (≥ 24 px, ou ≥ 18,66 px en gras).
 *
 * Pourquoi ce fichier existe.
 *
 * La campagne d'accessibilité (``e2e/accessibilite.spec.ts``) analyse le DOM
 * rendu avec axe-core : c'est la bonne mesure, mais elle a deux angles morts.
 * D'abord elle ne voit qu'un écran à la fois, dans l'état où il se trouve —
 * un composant qui n'apparaît que sur un écran non scanné, ou seulement
 * quand le backend sert un jeu d'exemple, n'est jamais mesuré. Ensuite elle
 * s'exécute en bout de chaîne : quand elle échoue, on sait QUEL écran, pas
 * QUELLE couleur, et le correctif se fait à l'aveugle.
 *
 * Ce test mesure la SOURCE : la palette déclarée dans ``app/globals.css``
 * (thèmes jour et nuit) et chaque classe utilitaire ``text-<token>/<opacité>``
 * réellement écrite dans les composants. Il n'y a pas d'angle mort d'écran et
 * pas d'angle mort d'état : une couleur qui n'est pas conforme est vue ici,
 * même si aucun test de navigateur ne l'affiche.
 *
 * Ce qu'il a trouvé, et qu'axe n'avait pas vu (qualification Chrome/Edge du
 * 02/11/2026, run 41497594641, écran ``/fiches``, gravité « serious ») :
 *
 *   - ``text-muted-foreground/80`` (libellés de groupe de la barre de
 *     navigation) : 3,52:1 en thème jour, au lieu des 5,30:1 du token plein.
 *   - ``text-primary/80`` (mention « données d'exemple ») : 3,46:1 en thème
 *     jour, au lieu des 5,04:1 du token plein.
 *
 * La leçon est dans le nom du test : ``/80`` n'est pas une nuance
 * typographique, c'est une DIVISION du contraste. La couleur réellement
 * peinte est le token composé à cette opacité sur le fond, et c'est elle qui
 * doit passer le seuil — pas le token dont elle porte le nom.
 *
 * Deux niveaux de contrôle :
 *   1. les couples canoniques « texte sur fond » de la palette ;
 *   2. chaque classe ``text-<token>/<opacité>`` trouvée dans ``app/`` et
 *      ``components/``.
 *
 * Une exemption n'est JAMAIS silencieuse : elle est écrite ci-dessous avec son
 * motif, et le test vérifie que le code exempté existe encore — une exemption
 * dont le motif a disparu fait échouer le test au lieu de pourrir.
 */

import assert from "node:assert/strict"
import { readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"
import { test } from "node:test"

const RACINE = path.resolve(import.meta.dirname, "..")
const CSS = path.join(RACINE, "app", "globals.css")

/** Seuil WCAG AA pour le texte courant (tout ce qui n'est pas « grand »). */
const SEUIL_TEXTE = 4.5

type Rgb = [number, number, number]

function canalLineaire(valeur: number): number {
  const canal = valeur / 255
  return canal <= 0.03928 ? canal / 12.92 : ((canal + 0.055) / 1.055) ** 2.4
}

function luminance(couleur: Rgb): number {
  const [rouge, vert, bleu] = couleur.map(canalLineaire)
  return 0.2126 * rouge + 0.7152 * vert + 0.0722 * bleu
}

/** Rapport de contraste WCAG, arrondi au centième (comme le rapporte axe). */
function contraste(premier: Rgb, second: Rgb): number {
  const clair = Math.max(luminance(premier), luminance(second))
  const sombre = Math.min(luminance(premier), luminance(second))
  return Math.round(((clair + 0.05) / (sombre + 0.05)) * 100) / 100
}

function hexVersRgb(hex: string): Rgb {
  const court = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex)
  const nuance: string[] = court
    ? [court[1], court[2], court[3]].map((chiffre: string) => `${chiffre}${chiffre}`)
    : [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)]
  return [parseInt(nuance[0], 16), parseInt(nuance[1], 16), parseInt(nuance[2], 16)]
}

/**
 * Couleur effectivement peinte : un texte à 80 % d'opacité est MÉLANGÉ au
 * fond, il n'est pas « un peu plus pâle ». C'est la composition alpha, en
 * sRGB, que fait le navigateur — et c'est elle que mesure axe.
 */
function composer(couleur: Rgb, fond: Rgb, opacite: number): Rgb {
  return [0, 1, 2].map((indice) => couleur[indice] * opacite + fond[indice] * (1 - opacite)) as Rgb
}

function forme(hex: string): boolean {
  return /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(hex)
}

/** Extrait le bloc de déclarations d'un sélecteur, en comptant les accolades. */
function blocVariables(css: string, selecteur: string): Map<string, string> {
  const depart = css.indexOf(`${selecteur} {`)
  assert.notEqual(depart, -1, `bloc « ${selecteur} » introuvable dans app/globals.css`)
  let profondeur = 0
  let fin = depart
  for (let indice = depart + selecteur.length; indice < css.length; indice += 1) {
    if (css[indice] === "{") profondeur += 1
    if (css[indice] === "}") {
      profondeur -= 1
      if (profondeur === 0) {
        fin = indice
        break
      }
    }
  }
  const declarations = new Map<string, string>()
  const motif = /--([a-z0-9-]+)\s*:\s*([^;]+);/gi
  const corps = css.slice(depart, fin)
  for (const [, nom, valeur] of corps.matchAll(motif)) {
    declarations.set(nom, valeur.trim())
  }
  return declarations
}

/** Palette résolue : les ``var(--x)`` sont remplacés, thème de nuit compris. */
function palette(css: string, selecteur: string, secours: Map<string, string>): Map<string, Rgb> {
  const brut = blocVariables(css, selecteur)
  const resolues = new Map<string, Rgb>()
  const resoudre = (nom: string, vus: Set<string>): Rgb | undefined => {
    if (vus.has(nom)) return undefined // cycle : jamais silencieux, voir le test
    const valeur = brut.get(nom) ?? secours.get(nom)
    if (!valeur) return undefined
    vus.add(nom)
    if (valeur.startsWith("#")) return forme(valeur) ? hexVersRgb(valeur) : undefined
    const reference = /^var\(--([a-z0-9-]+)\)$/.exec(valeur)
    if (reference) return resoudre(reference[1], vus)
    return undefined // rgba(), color-mix(), oklch() : mesurés ailleurs, voir ci-dessous
  }
  for (const nom of brut.keys()) {
    const couleur = resoudre(nom, new Set())
    if (couleur) resolues.set(nom, couleur)
  }
  return resolues
}

const css = readFileSync(CSS, "utf-8")
const brutJour = blocVariables(css, ":root")
const jour = palette(css, ":root", brutJour)
const nuit = palette(css, ".dark", brutJour)

/** Couples « texte sur fond » réellement utilisés par l'interface. */
const COUPLES: { texte: string; fond: string; motif: string }[] = [
  { texte: "foreground", fond: "background", motif: "corps de texte sur fond de page" },
  { texte: "muted-foreground", fond: "background", motif: "libellés secondaires, hors carte" },
  { texte: "muted-foreground", fond: "card", motif: "libellés secondaires en carte" },
  { texte: "muted-foreground", fond: "muted", motif: "libellés secondaires sur plage atténuée" },
  { texte: "muted-foreground", fond: "secondary", motif: "libellés secondaires sur plage secondaire" },
  { texte: "muted-foreground", fond: "accent", motif: "libellés secondaires sur plage d'accent" },
  { texte: "card-foreground", fond: "card", motif: "texte de carte" },
  { texte: "popover-foreground", fond: "popover", motif: "texte de surgissement" },
  { texte: "secondary-foreground", fond: "secondary", motif: "boutons secondaires" },
  { texte: "accent-foreground", fond: "accent", motif: "élément actif / survol" },
  { texte: "primary-foreground", fond: "primary", motif: "bouton principal" },
  { texte: "primary", fond: "background", motif: "liens et insigne en laiton" },
  { texte: "destructive", fond: "background", motif: "message d'échec" },
  { texte: "success-texte", fond: "card", motif: "mention de succès" },
  { texte: "warning-texte", fond: "card", motif: "mention d'avertissement" },
  { texte: "info-texte", fond: "card", motif: "mention d'information" },
  { texte: "corrige-texte", fond: "card", motif: "mention de correction" },
]

test("la palette déclare bien les deux thèmes, en couleurs exploitables", () => {
  for (const [nom, jeu] of [
    [":root", jour],
    [".dark", nuit],
  ] as [string, Map<string, Rgb>][]) {
    for (const token of ["background", "foreground", "muted-foreground", "card", "primary"]) {
      assert.ok(jeu.has(token), `${nom} : token « ${token} » absent ou non résoluble en hexadécimal`)
    }
  }
  // Les valeurs qui ne sont pas des couleurs hexadécimales (bordures,
  // entrées, halo, rayon) ne sont PAS mesurées ici : elles ne peignent pas de
  // texte. Le test les NOMME au lieu de les ignorer en silence — si un token
  // de COULEUR de texte devenait non mesurable, la liste changerait et le
  // test l'exigerait.
  const nonMesurees = [...brutJour.keys()].filter((nom) => !jour.has(nom)).sort()
  const attendues = ["border", "input", "radius"]
  assert.deepEqual(
    nonMesurees,
    attendues,
    "tokens non mesurables : attendu bordure/entrée/rayon uniquement",
  )
})

for (const [theme, jeu] of [
  ["jour", jour],
  ["nuit", nuit],
] as [string, Map<string, Rgb>][]) {
  test(`couples canoniques — thème ${theme} (seuil ${SEUIL_TEXTE}:1)`, () => {
    const manquants: string[] = []
    const insuffisants: string[] = []
    for (const { texte, fond, motif } of COUPLES) {
      const couleurTexte = jeu.get(texte)
      const couleurFond = jeu.get(fond)
      if (!couleurTexte || !couleurFond) {
        manquants.push(`${texte} sur ${fond}`)
        continue
      }
      const rapport = contraste(couleurTexte, couleurFond)
      if (rapport < SEUIL_TEXTE) insuffisants.push(`${texte} sur ${fond} (${motif}) : ${rapport}:1`)
    }
    assert.deepEqual(manquants, [], `couples non mesurables dans le thème ${theme}`)
    assert.deepEqual(insuffisants, [], `contraste insuffisant dans le thème ${theme}`)
  })
}

/** Tous les fichiers de composants et de pages, récursivement. */
function fichiersSource(dossier: string): string[] {
  const trouves: string[] = []
  for (const entree of readdirSync(dossier)) {
    const chemin = path.join(dossier, entree)
    if (statSync(chemin).isDirectory()) trouves.push(...fichiersSource(chemin))
    else if (chemin.endsWith(".tsx") || chemin.endsWith(".ts")) trouves.push(chemin)
  }
  return trouves
}

/**
 * Couleurs de la palette Tailwind employées TELLES QUELLES dans le code
 * (jamais redéfinies dans ``app/globals.css``). Elles sont recopiées ici avec
 * leur valeur exacte : si Tailwind ou le code change, le test le voit — une
 * couleur inconnue est signalée, jamais ignorée.
 */
const COULEURS_TAILWIND: Record<string, string> = {
  "amber-200": "#fde68a",
  "amber-500": "#f59e0b",
  "amber-900": "#78350f",
}

type Occurrence = {
  fichier: string
  ligne: number
  classe: string
  token: string
  opacite: number
  /** ``true`` si la classe est préfixée ``dark:`` — elle ne s'applique qu'au thème nuit. */
  nuitSeulement: boolean
  /** ``true`` si la ligne porte aussi une variante ``dark:`` (le jeton nu ne vaut que pour le jour). */
  jourSeulement: boolean
}

/**
 * Classes ``text-<token>/<opacité>`` écrites dans le code. Tailwind v4 lit
 * ``--color-<token>``, que ``app/globals.css`` rattache à ``--<token>``.
 */
function occurrences(): Occurrence[] {
  const motif = /(dark:)?text-([a-z0-9-]+)\/(\d{1,3})/g
  const trouvees: Occurrence[] = []
  for (const chemin of [...fichiersSource(path.join(RACINE, "app")), ...fichiersSource(path.join(RACINE, "components"))]) {
    const lignes = readFileSync(chemin, "utf-8").split("\n")
    lignes.forEach((contenu, indice) => {
      const porteVarianteSombre = /dark:text-/.test(contenu)
      for (const [classe, variante, token, opacite] of contenu.matchAll(motif)) {
        trouvees.push({
          fichier: path.relative(RACINE, chemin),
          ligne: indice + 1,
          classe,
          token,
          opacite: Number(opacite) / 100,
          nuitSeulement: variante === "dark:",
          jourSeulement: porteVarianteSombre && variante !== "dark:",
        })
      }
    })
  }
  return trouvees
}

/**
 * Fonds DÉCLARÉS : quand le texte ne pose pas sur le fond de page mais sur une
 * plage locale (``bg-<couleur>/<opacité>``), le contraste se mesure sur cette
 * plage composée, pas sur la page. Chaque entrée nomme la couche, lue dans le
 * code — si le code change, la mesure change avec lui.
 */
const FONDS_DECLARES: {
  fichier: string
  classes: string[]
  couche: { token: string; opacite: number }
  motif: string
}[] = [
  {
    fichier: "components/validation-app.tsx",
    classes: ["text-amber-900/90", "dark:text-amber-200/90"],
    couche: { token: "amber-500", opacite: 0.1 },
    motif: "bandeau de doublon : le texte pose sur une plage ambre à 10 %, pas sur la page",
  },
]

function couleurDuToken(token: string, jeu: Map<string, Rgb>): Rgb | undefined {
  const nu = token.replace(/^color-/, "")
  const depuisPalette = jeu.get(token) ?? jeu.get(nu)
  if (depuisPalette) return depuisPalette
  const tailwind = COULEURS_TAILWIND[nu]
  return tailwind ? hexVersRgb(tailwind) : undefined
}

/**
 * Exemptions : du texte qu'on ne mesure pas, avec la raison. Chaque entrée
 * pointe un extrait de code qui doit EXISTER — sinon c'est une exemption
 * périmée, et le test échoue.
 */
const EXEMPTIONS: { fichier: string; extrait: string; motif: string }[] = [
  {
    fichier: "components/login-form.tsx",
    extrait: "text-primary/25",
    motif: "décoratif : SVG (rose des vents) dans un panneau aria-hidden — aucun nœud de texte",
  },
  {
    fichier: "components/login-form.tsx",
    extrait: "text-primary/15",
    motif: "décoratif : SVG (rose des vents) dans un panneau aria-hidden — aucun nœud de texte",
  },
  {
    fichier: "components/login-form.tsx",
    extrait: "text-primary/30",
    motif: "décoratif : SVG (vague, tracé sans texte) dans un panneau aria-hidden",
  },
]

test("chaque classe « text-<token>/<opacité> » garde au moins 4,5:1 sur le fond", () => {
  const trouvees = occurrences()
  // Un test qui ne trouve rien ne prouve rien : la collecte doit être vivante.
  assert.ok(trouvees.length >= 8, `collecte suspecte : ${trouvees.length} occurrence(s) seulement`)
  const exempts = new Set(EXEMPTIONS.map((exemption) => `${exemption.fichier}|${exemption.extrait}`))
  const insuffisants: string[] = []
  const inconnus: string[] = []
  for (const occurrence of trouvees) {
    if (exempts.has(`${occurrence.fichier}|${occurrence.classe}`)) continue
    const themes: [string, Map<string, Rgb>][] = []
    if (!occurrence.nuitSeulement) themes.push(["jour", jour])
    if (!occurrence.jourSeulement) themes.push(["nuit", nuit])
    for (const [theme, jeu] of themes) {
      const texte = couleurDuToken(occurrence.token, jeu)
      const fondPage = jeu.get("background")
      if (!texte || !fondPage) {
        inconnus.push(`${occurrence.fichier}:${occurrence.ligne} ${occurrence.classe} (thème ${theme})`)
        continue
      }
      const declare = FONDS_DECLARES.find(
        (entree) => entree.fichier === occurrence.fichier && entree.classes.includes(occurrence.classe),
      )
      const couche = couleurDuToken(declare?.couche.token ?? "", jeu)
      const fond = declare && couche ? composer(couche, fondPage, declare.couche.opacite) : fondPage
      const rapport = contraste(composer(texte, fond, occurrence.opacite), fond)
      if (rapport < SEUIL_TEXTE) {
        insuffisants.push(
          `${occurrence.fichier}:${occurrence.ligne} ${occurrence.classe} → ${rapport}:1 en thème ${theme}` +
            ` (token plein : ${contraste(texte, fond)}:1)`,
        )
      }
    }
  }
  assert.deepEqual(inconnus, [], "classes dont le token n'est pas dans la palette")
  assert.deepEqual(
    insuffisants,
    [],
    "contraste insuffisant — retirer l'opacité ou employer un token dédié :\n" + insuffisants.join("\n"),
  )
})

test("les exemptions pointent du code qui existe encore, et disent pourquoi", () => {
  for (const exemption of EXEMPTIONS) {
    assert.ok(
      exemption.motif.length >= 25,
      `motif trop vague pour « ${exemption.extrait} » : « ${exemption.motif} »`,
    )
    const contenu = readFileSync(path.join(RACINE, exemption.fichier), "utf-8")
    assert.ok(
      contenu.includes(exemption.extrait),
      `exemption périmée : « ${exemption.extrait} » n'est plus dans ${exemption.fichier}`,
    )
  }
  assert.ok(EXEMPTIONS.length <= 3, "toute nouvelle exemption doit être justifiée, pas empilée")
})

/** Bloc de déclarations d'une règle CSS ordinaire, trouvée par son sélecteur. */
function blocRegle(css: string, selecteur: RegExp): Map<string, string> {
  const trouve = selecteur.exec(css)
  assert.ok(trouve, `règle « ${selecteur.source} » introuvable dans app/globals.css`)
  const depart = css.indexOf("{", trouve.index)
  let profondeur = 0
  let fin = depart
  for (let indice = depart; indice < css.length; indice += 1) {
    if (css[indice] === "{") profondeur += 1
    if (css[indice] === "}") {
      profondeur -= 1
      if (profondeur === 0) {
        fin = indice
        break
      }
    }
  }
  const declarations = new Map<string, string>()
  for (const [, nom, valeur] of css.slice(depart, fin).matchAll(/([a-z-]+)\s*:\s*([^;]+);/gi)) {
    declarations.set(nom.trim(), valeur.trim())
  }
  return declarations
}

/** Jeton de palette visé par une valeur « var(--x) », ou ``undefined``. */
function jetonDePalette(valeur: string | undefined): string | undefined {
  const reference = valeur ? /^var\(--([a-z0-9-]+)\)$/.exec(valeur) : null
  return reference ? reference[1] : undefined
}

/**
 * L'angle mort que ce fichier ne couvrait pas.
 *
 * Tout ce qui précède mesure des couleurs que le NAVIGATEUR peint à partir de
 * la palette. Un volet déroulant ouvert, lui, est peint par le SYSTÈME
 * (Windows), qui ignore ``.dark`` : il suit le thème du système. Tant que
 * l'utilisatrice est en thème clair côté Windows et en thème nuit côté
 * application, le volet reste blanc et les options, qui héritent de
 * l'``--foreground`` clair de la nuit, s'écrivent en blanc dessus. La palette
 * est irréprochable et l'écran est illisible : c'est précisément le défaut
 * que mesure ce test — non pas un couple de jetons, mais le fait que les deux
 * couleurs soient ÉCRITES sur le contrôle natif, seules à pouvoir l'atteindre.
 */
test("les contrôles natifs reçoivent des couleurs explicites : le système ne lit pas la palette", () => {
  const options = blocRegle(css, /select\s+option/)
  const fond = jetonDePalette(options.get("background-color"))
  const texte = jetonDePalette(options.get("color"))
  assert.ok(
    fond && texte,
    `« select option » doit peindre un fond et une encre issus de la palette — trouvé : ` +
      `${options.get("background-color") ?? "rien"} / ${options.get("color") ?? "rien"}`,
  )
  for (const [theme, jeu] of [
    ["jour", jour],
    ["nuit", nuit],
  ] as [string, Map<string, Rgb>][]) {
    const couleurFond = jeu.get(fond)
    const couleurTexte = jeu.get(texte)
    assert.ok(
      couleurFond && couleurTexte,
      `thème ${theme} : jetons « ${fond} » / « ${texte} » non résolubles en hexadécimal`,
    )
    const rapport = contraste(couleurTexte, couleurFond)
    assert.ok(
      rapport >= SEUIL_TEXTE,
      `thème ${theme} : le volet déroulant natif tombe à ${rapport}:1 — ` +
        `écrire les deux couleurs sur « option », c'est le seul moyen de l'atteindre`,
    )
  }

  // Cases à cocher et boutons radio natifs : peints par le système eux aussi.
  // « accent-color » est un composant d'interface, pas du texte courant : le
  // critère 1.4.11 « Contraste des éléments non textuels » demande 3:1.
  const cases = blocRegle(css, /input\[type="checkbox"\]/)
  const accent = jetonDePalette(cases.get("accent-color"))
  assert.ok(accent, "les cases natives doivent annoncer un « accent-color » issu de la palette")
  for (const [theme, jeu] of [
    ["jour", jour],
    ["nuit", nuit],
  ] as [string, Map<string, Rgb>][]) {
    const couleur = jeu.get(accent)
    const page = jeu.get("background")
    assert.ok(couleur && page, `thème ${theme} : « ${accent} » ou le fond de page n'est pas mesurable`)
    const rapport = contraste(couleur, page)
    assert.ok(
      rapport >= 3,
      `thème ${theme} : « accent-color » à ${rapport}:1, sous les 3:1 d'un composant d'interface`,
    )
  }
})
