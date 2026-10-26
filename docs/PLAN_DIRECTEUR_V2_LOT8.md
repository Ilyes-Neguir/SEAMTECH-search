# Plan directeur v2 — Lot 8 : « L'atelier prend le large » (refonte visuelle)

> Branche : `arena/82958474-seamtech-search` · RED : `5619734` · GREEN : `9e80da9` → prouvé : `bd91fe7`
> · Skin uniquement — **aucune migration, aucun parcours métier modifié**.
> Vérifié avec les workflows ECC (`verification-loop`, `frontend-a11y`).

## Objectif du plan

Donner à la voilerie une interface qui lui ressemble : simple, dynamique,
belle — des détails d'atelier et de mer travaillés, rien de générique. Deux
thèmes complets (clair **et** sombre) avec bascule persistée, navigation
regroupée par domaine, contrastes AA prouvés dans le navigateur, et toutes
les recettes e2e des lots précédents toujours vertes (mêmes libellés, mêmes
testids, mêmes hauteurs).

## Direction artistique — les deux quarts

Comme à bord, on travaille par **quarts** :

| | Quart de jour | Quart de nuit |
|---|---|---|
| Fond | toile à voile ivoire `#f6f1e6` | encre de mer profonde `#08111a` |
| Encre | marine `#152730` (13,7:1) | écume `#edf2ee` (16,8:1) |
| Accent (fil d'insigne) | laiton vieilli `#8f5c07` (5,0:1) | ambre de signal `#f2a93b` (9,5:1) |
| Alerte | orange-rouge des fanaux `#c2410c` / encre `#93330a` | `#ff7a45` / `#ffa07a` |

- **L'ambre est l'accent de marque unique** (le fil d'insigne cousu sur la
  voile) ; l'avertissement passe à l'orange-rouge — plus aucune confusion.
- **Gestes d'atelier** : coutures double aiguille en séparateurs (l'« horizon »
  sous la navigation, la couture verticale entre groupes), laizes de toile en
  trame de fond (filet tous les 76 px), lettres d'insigne (`titre-voile`),
  hissage des écrans à l'arrivée, rose des vents qui cherche son nord en
  240 s, vague d'horizon qui dérive sur l'accueil. Tout mouvement s'éteint
  sous `prefers-reduced-motion`.
- **Badges sémantiques** : `badge-attention`, `badge-succes`, `badge-danger`,
  `badge-info` (bleu de carte marine, remplace le cyan hérité `#5bc0de`),
  `badge-corrige` (violet d'encre de retouche pour la main de l'approbateur) —
  chacun ≥ 4,5:1 dans les deux quarts, vérifié numériquement.
- Aucun webfont, aucune ressource externe : 100 % local, comme toujours.

## Contrat livré

### Bascule de quart (persistée, sans clignotement)
- Composant `BasculeTheme` (`data-testid="bascule-theme"`) dans la navigation,
  visible dès `/login` ; `aria-label` + `aria-pressed`.
- Choix persisté dans `localStorage` (`seamtech-quart` = `jour`/`nuit`) et
  appliqué **avant la première peinture** par un script d'amorçage du layout
  (`suppressHydrationWarning`, `themeColor` par média). Repli : préférence
  système, sinon le quart de nuit historique.

### Navigation par domaines (hauteur `h-14` conservée)
- **Explorer** : Recherche, Dossiers, Fichiers · **Atelier** : Nouveau dossier,
  Validation, Extractions, Comparaison, Confiance · **Pilotage** : Demandes,
  Qualité, Gabarits, Comptes. Libellés, hrefs et visibilités par rôle
  **inchangés** — la navigation ne promet jamais un écran refusé.
- Emblème : rose des vents SVG (`currentColor`, tourne de 45° au survol) ;
  lien actif souligné au fil d'insigne + `aria-current="page"`.

### Voile d'accueil (`/login`)
- Écran partagé : à gauche la voile (deux roses des vents contraires, mer qui
  dérive, « L'atelier prend le large »), purement décorative (`aria-hidden`) ;
  à droite le poste de quart. Logique, ids, handlers et testids du formulaire
  **strictement inchangés**.

### Balayage chromatique
- Toutes les couleurs « sombre seulement » (`text-amber-200/300`,
  `text-emerald-300`, `text-red-300`, `text-orange-300`, `text-violet-300`,
  hex `#5bc0de/#8fd6e8`, survols `bg-white/5` invisibles en clair…) remplacées
  par tokens et badges sémantiques — 25 fichiers, remplacements exacts
  assertés un à un. Les boutons pleins (emerald-600, violet-600…) et les
  teintes de fond portant l'encre par défaut restent : lisibles des deux
  quarts.

## Preuves

- **RED** `5619734` : `frontend/e2e/lot8-theme.spec.ts` (3 tests) commité avant
  tout habillage ; 0 hook présent → échec franc.
- **GREEN** `9e80da9` : build de production ✓, `tsc --noEmit` ✓, utilités du
  design system présentes dans le CSS compilé ✓.
- **Recette navigateur** (suite e2e par défaut, SQLite) : bascule persistée
  (classe `dark` + localStorage + survie au rechargement), **contrastes AA
  mesurés dans le navigateur** sur `/login` dans les deux quarts
  (texte/fond, texte/carte, bouton ≥ 4,5), groupes de navigation visibles
  après connexion, lien « Recherche » toujours là.
- **CI au SHA exact `bd91fe7` — 4/4 verts** : CI `40344514549` (13 jobs dont
  e2e complet), Qualification native `40344515232`, Qualification reserves
  `40344514593`, Perf comparaison `40344514120`. Le premier passage (`9e80da9`)
  avait échoué sur le seul job e2e — collision de nom accessible, voir
  « Échecs honnêtes » ; corrigé par `bd91fe7`.
- **Vérification ECC locale** : build ✓, types ✓, ruff ✓, 1058 tests Python
  passés (442 live ignorés dans le bac à sable), scan de secrets du diff
  propre, diff relu = peau uniquement. Revue `frontend-a11y` : bascule
  étiquetée et à état, héros décoratif masqué aux lecteurs d'écran,
  `aria-current`, anneaux de focus au token primaire (≥ 5:1), couleur jamais
  seule porteuse d'information (les badges gardent leurs libellés),
  `prefers-reduced-motion` respecté partout.

## Échecs honnêtes rencontrés (et ce qu'ils ont produit)

- **Fichiers réécrits de mémoire, puis restaurés** : une première version de
  `login-form.tsx`/`nav.tsx` écrite sans relire le code réel (le login réel
  poste `{identifiant, mot_de_passe}` avec compte de secours) a été détectée
  par `tsc`, restaurée par `git checkout`, puis habillée chirurgicalement.
  Règle réaffirmée : toujours relire le fichier avant de le toucher.
- **Éditions parallèles sur un même fichier écrasées** : plusieurs
  modifications de `nav.tsx`/`globals.css` lancées dans le même message se
  sont mutuellement annulées en silence — détectées par l'absence d'une
  utilité dans le CSS compilé, puis par grep de contrôle après chaque edit.
  Règle : un edit par message et par fichier, vérification systématique.
- **Collision de nom accessible** : le lien de marque « SEAMTECH — retour à
  la recherche » répondait, comme le lien « Recherche », au localisateur par
  rôle/nom (sous-chaîne) → violation de mode strict en CI (`9e80da9`, job e2e
  seul rouge). Ambigu aussi pour la commande vocale : la marque devient
  « accueil de l'atelier » et la recette vise le nom exact (`bd91fe7`, 4/4).
- **`/login` sans `SEAMTECH_UI_PASSWORD`** affiche volontairement l'écran
  « fail closed » : le premier aperçu semblait vide — comportement de sécurité
  correct, confirmé, contourné pour l'aperçu seulement.

## Limites assumées et suite

- La recette navigateur mesure les contrastes sur `/login` (les deux quarts) ;
  les écrans métier partagent les mêmes tokens et badges — leur preuve reste
  les recettes fonctionnelles inchangées des lots 1 à 7.
- Le choix de quart est par navigateur (localStorage), pas par compte : le
  porter côté profil serait un lot métier, hors enveloppe « peau ».
- Suite possible : calibration accessoires sur échantillons réels (pistes au
  rapport Lot 7), puis fusion sur autorisation.
