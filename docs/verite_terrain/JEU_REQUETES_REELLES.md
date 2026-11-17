# Jeu de requêtes RÉEL — recherche hybride (Tâche 3, plan §10)

Statut : **REJOUÉ ET MESURÉ le 21/09/2026** (voir « Compte rendu de rejeu » en
bas de page). Les requêtes ci-dessous sont écrites comme un opérateur les
taperait, à partir du contenu CONNU de la vraie fiche 1042-EX (texte relevé par
l'audit indépendant du 21/09 et vérité terrain §13) — pas d'invention : chaque
terme provient du document.

Ce jeu est rejoué en continu par `tests/test_recherche_fonds_reel.py`
(marque `-m postgres`). Les 50 requêtes de `tests/test_recherche_hybride.py`
restent ce qu'elles sont : un test d'ingénierie écrit sur un corpus
SYNTHÉTIQUE de 12 fiches semées en fixture — le rappel 50/50 du lot E prouve
l'architecture, pas le fonds réel.

## Prérequis au rejeu

1. La vraie fiche (SHA-256 `43afc51e…`) est au dépôt et le gabarit est réglé
   dessus (Tâche 2, cible ≥ 90 % des champs lus).
2. La fiche est entrée par le pipeline normal (dépôt → extraction → `a_valider`,
   RG3) puis **validée par un humain** — c'est la validation qui rafraîchit le
   `search_vector` ; à défaut, un rafraîchissement global explicite
   (`rafraichir_texte_recherche_toutes()`) tient lieu de substitute de test.
3. Portée de recherche : `valide` par défaut ; pendant la pré-validation,
   `inclure_a_valider=true`.

## Les requêtes (ce qui doit remonter : la fiche 1042-EX)

| # | Requête | Nature | Attendu |
|---|---|---|---|
| 1 | `1042-EX` | code exact (séparateurs) | 1042-EX en tête |
| 2 | `1042` | code partiel | 1042-EX présente |
| 3 | `exemplia` | client | 1042-EX présente |
| 4 | `demo` | client (raison sociale) | 1042-EX présente |
| 5 | `29er` | bateau | 1042-EX présente |
| 6 | `spi` | type de voile | 1042-EX présente |
| 7 | `spi asymétrique` | type complet | 1042-EX présente |
| 8 | `monofilm` | matière | 1042-EX présente |
| 9 | `monofilm k903` | matière précise | 1042-EX présente |
| 10 | `spi exemplia 2026` | combinaison opérateur | 1042-EX présente |
| 11 | `spi 29er` | type + bateau | 1042-EX présente |
| 12 | `monofime` | FAUTE sur matière | 1042-EX présente (filet trigrammes) |
| 13 | `voile de portant` | titre | 1042-EX présente |

## Compte rendu de rejeu — MESURÉ le 21/09/2026

Fonds réel : **1 fiche** (1042-EX), entrée par le pipeline complet réglé
(extraction gabarit v2 du registre → écriture → validation, qui rafraîchit le
`search_vector`). Mesures : `tests/test_recherche_fonds_reel.py`.

### Rappel avant / après

| Étape | Rappel | Détail |
|---|---|---|
| Index nourri par le gabarit **synthétique** (état Lot E) | non re-mesuré : l'index du Lot E était semé en SQL direct, pas par extraction | — |
| Index nourri par l'extraction réglée, vecteur Lot E d'origine | **11/13** (mesuré) | absentes : « demo » (chantier client hors agrégat) et « spi exemplia 2026 » (année hors agrégat, AND lexical) |
| Index nourri par l'extraction réglée + **migration 013** (chantier + année dans le texte pondéré B) | **13/13**, toutes au rang 1 | les 11 autres requêtes non dégradées |

Les deux absences n'étaient pas des défauts d'extraction : le chantier du
client et la date d'édition ÉTAIENT extraits et présents en base ; ils
n'étaient simplement pas agrégés au `search_vector`. La migration 013 ajoute
`coalesce(c.chantier,'')` et `to_char(f.date_edition,'YYYY')` aux deux
fonctions de rafraîchissement (poids B). Architecture RRF / facettes / A-B-C
inchangée.

### Par requête (état final, migration 013)

| # | Requête | Résultat | Rang |
|---|---|---|---|
| 1 | `1042-EX` | présente | 1 |
| 2 | `1042` | présente | 1 |
| 3 | `exemplia` | présente | 1 |
| 4 | `demo` | présente | 1 |
| 5 | `29er` | présente | 1 |
| 6 | `spi` | présente | 1 |
| 7 | `spi asymétrique` | présente | 1 |
| 8 | `monofilm` | présente | 1 |
| 9 | `monofilm k903` | présente | 1 |
| 10 | `spi exemplia 2026` | présente | 1 |
| 11 | `spi 29er` | présente | 1 |
| 12 | `monofime` | présente | 1 |
| 13 | `voile de portant` | présente | 1 |

Avec un fonds d'une seule fiche, « présente au rang 1 » est trivial dès qu'elle
est trouvée ; le critère discriminant est la colonne Résultat. Le test asserte
les deux.

### Facettes sur la requête vide (exactement les valeurs de la fiche)

| Axe | Valeur mesurée | Effectif |
|---|---|---|
| type_voile | Spi Asymétrique | 1 |
| client | Exemplia | 1 |
| bateau | 29er 15' | 1 |
| gamme | Medium Régate | 1 |
| annee | 2026 | 1 |
| matiere | Monofilm K903 | 1 |

### Limites connues

- Fonds de 1 fiche : aucun test de classement relatif possible ; le rappel est
  binaire. La montée en charge (50 requêtes synthétiques à 12 fiches, puis les
  10 000 fiches réelles) reste à re-mesurer quand le fonds existera.
- « monofime » est rattrapée par les trigrammes (similitude ≥ 0,30) ; une faute
  sans accents (« genios ») ne l'est pas — index expression `unaccent()` hors
  périmètre, documenté au Lot E.
