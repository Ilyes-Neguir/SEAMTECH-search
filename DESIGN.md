# SEAMTECH Search — Design System

## Concept visuel
**« L'atelier prend le large »** — Deux quarts, une seule voilerie.
- **Quart de jour** : toile à voile chaude, encre marine, laiton vieilli
- **Quart de nuit** : encre de mer profonde, ambre de signal qui veille

L'univers est celui de la voilerie technique : toile de voile et fibres (Dyneema, carbone, laminés), plans de forme et courbes de creux, lignes de vent, isobares, dessin technique de plan de voile (guindant, chute, point d'écoute, lattes), cartes marines de nuit.

## Palette

### Quart de jour (clair)
| Rôle | Couleur | Usage |
|------|---------|-------|
| Fond | `#f6f1e6` | Toile à voile chaude |
| Carte | `#fffdf7` | Surfaces |
| Encre | `#152730` | Texte principal |
| Ambre | `#8f5c07` | Accent unique (fil d'insigne) |
| Laiton | `#eae1cd` | Surfaces secondaires |
| Alerte | `#b3261e` | Fanaux (danger) |
| Succès | `#1e7a4c` | Validation |
| Info | `#2e6f8e` | Information |

### Quart de nuit (sombre)
| Rôle | Couleur | Usage |
|------|---------|-------|
| Fond | `#08111a` | Encre de mer profonde |
| Carte | `#0d1b28` | Surfaces |
| Texte | `#edf2ee` | Texte principal |
| Ambre | `#f2a93b` | Accent unique (signal qui veille) |
| Alerte | `#ff6b5e` | Fanaux |
| Succès | `#4cc38a` | Validation |

**Règle** : un seul accent (ambre). Pas de dégradé violet/bleu. Contrastes ≥ 4,5:1.

## Typographie
- **Titres** : **Barlow Condensed** (lettrage de numéros de voile, condensé technique) — `@fontsource/barlow-condensed`, bundlé localement via npm
- **Texte** : **IBM Plex Sans** (lisible, technique, conçu pour l'UI) — `@fontsource/ibm-plex-sans`, bundlé localement
- **Cotes/données** : **IBM Plex Mono** (tabulaire, technique) — `@fontsource/ibm-plex-mono`, bundlé localement
- **Hiérarchie** : titres condensés en capitales espacées (`titre-voile`), texte courant en Plex Sans, chiffres en Plex Mono
- **Hors-ligne** : polices embarquées via npm (aucun CDN, aucun appel externe)

## Motion
- **Transitions** : 150-300ms, ease-out
- **Micro-interactions** : survol, focus, états de chargement
- **Rose des vents** : rotation lente (décorative, `aria-hidden`)
- **Reduced-motion** : désactive toutes les animations, affiche l'état final statique

## Signature visuelle
- **Couture double aiguille** : deux rangs de points (zigzag des laizes), séparateurs horizontaux et verticaux
- **Rose des vents** : décorative sur login et états vides
- **Plan de voile** : traits fins (guindant, chute, lattes) en en-têtes et séparateurs
- **Lignes de flux** : courbes de vent animées (subtiles, jamais sur les tableaux)

## Composants
- **Boutons** : primaire (ambre), secondaire (bordure), fantôme ; états hover/focus/disabled
- **Champs** : fond `bg-input`, texte `text-foreground`, bordure, focus ring ambre
- **Tableaux** : en-tête `bg-muted/50`, lignes `border-b`, chiffres tabulaires, écarts colorés (≤1 % vert, ≤5 % ambre, >5 % rouge)
- **Badges** : `badge-attention` (ambre), succès (vert), alerte (rouge)
- **Onglets** : `fichiers-page` (Catalogue / Recherche avancée)
- **Modales/Toasts** : à définir en Phase 2
- **États** : vides (rose des vents), chargement (skeletons), erreur (message + retry)

## Icônes
Lucide React (SVG cohérent). Pas d'emojis comme icônes.

## Interdits
- Cartes dans des cartes
- Tuiles d'icône arrondie au-dessus de chaque titre
- Glassmorphism partout
- Emojis comme icônes
- Ombres génériques
- 3D sur les tableaux de données

## 3D et décor (Phase 4)
- **Pièce maîtresse** : voile 3D (Three.js), maillage procédural, vertex shader (ondulation au vent, plis, tissage), sur login et états vides. Réaction douce à la souris. < 200 ko gzip, 60 fps, fallback SVG si WebGL indisponible/mobile/reduced-motion.
- **Décor 2D** : lignes de flux, courbes de niveau, plan de voile en traits fins (en-têtes, séparateurs, loaders).
- **Garde-fous** : lazy loading, aucun modèle externe, aucune 3D sur les données.

## Accessibilité
- Contraste WCAG AA (≥ 4,5:1)
- Focus visible (ring ambre)
- Navigation clavier complète
- `prefers-reduced-motion` respecté
- Repères ARIA (`nav`, `main`, `aria-hidden` pour le décor)
- Lien d'évitement « Aller au contenu principal »

## Responsive
375px, 768px, 1024px, 1440px vérifiés sur chaque page. Captures avant/après.
