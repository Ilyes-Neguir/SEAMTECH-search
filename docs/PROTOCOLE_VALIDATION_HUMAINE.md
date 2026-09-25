# Protocole de validation humaine — fiches de référence

**Objet** : définir comment un humain valide les valeurs extraites des fiches
techniques **dès réception des PDF de production**. Ce protocole s'applique à
l'échantillon de référence (20 à 30 fiches) tiré de l'archive réelle ; il est
rédigé avant l'arrivée des données et ne contient **aucune fiche réelle**.

**Principes (non négociables)** :

- la machine propose, l'humain dispose — aucune valeur n'est « validée »
  par le logiciel (RG3) ;
- toute valeur extraite est tracée : méthode, confiance, page, zone PDF
  (`seamtech_search/fiches/modeles.py` — `ChampExtrait`, `Zone`) ;
- aucune donnée client ne circule hors des outils prévus ; les modèles
  ci-dessous ne sont jamais remplis avec des données réelles dans le dépôt
  (uniquement `EXEMPLE_SYNTHETIQUE`).

Modèles associés :

- `docs/templates/FICHE_VALIDATION_REFERENCE.md` — une fiche par document ;
- `docs/templates/SUIVI_VALIDATION_REFERENCE.csv` — suivi de campagne
  (import contrôlé futur).

---

## 1. Choix des 20 à 30 fiches de référence

1. Partir du manifeste produit par le préflight
   (`scripts/preflight_archive.py preflight` → `echantillon.json`,
   voir `docs/ARRIVEE_ARCHIVE.md`).
2. Constituer un échantillon **stratifié de 20 à 30 fiches** couvrant :
   - chaque famille de gabarit recensée par l'inventaire
     (`scripts/inventaire_archive.py` — familles de gabarits) : au moins
     2 fiches par famille majoritaire, 1 par famille rare ;
   - les périodes (années) présentes dans l'archive ;
   - les types de support : PDF texte natif ET PDF scannés (OCR étage 3) ;
   - les cas difficiles : valeurs avec accents (« Épaisseur »), unités
     variées (m/cm/mm), cotes décimales à la virgule, galons, jonctions,
     matériaux à épaisseurs numérotées ;
   - au moins 2 fiches incomplètes (champs absents) et 1 fiche avec anomalie
     RG16 si le tri automatique en signale.
3. Tirage reproductible : liste triée par code de fiche, sélection pas-à-pas
   répartie (même algorithme que l'échantillonnage du préflight), plus les
   cas atypiques imposés ci-dessus. Le résultat est figé dans le suivi CSV
   (une ligne par champ contrôlé) **avant** le premier contrôle.
4. Jamais plus de 30 fiches par campagne : une campagne courte et complète
   vaut mieux qu'un contrôle bâclé (chronomètre humain cible : 2 min/fiche —
   voir `docs/verite_terrain/MESURE_VALIDATION_2MIN.md`, non encore mesuré).

## 2. Vérification de chaque champ

Pour chaque fiche : ouvrir le PDF **à la page indiquée**, regarder la **zone
PDF** (rectangle d'origine de la valeur), puis comparer :

| Question | Décision |
|---|---|
| La valeur lue dans le PDF est-elle égale à la valeur extraite ? | Résultat `OK` |
| Le champ existe sur le document mais n'a pas été lu ? | Résultat `ABSENTE` + commentaire |
| Le champ n'a aucun objet sur cette fiche (ex. spinnaker sans génois) ? | Résultat `NON_APPLICABLE` + motif |
| La valeur extraite est fausse (erreur de lecture, unité, confusion) ? | Résultat `CORRIGEE` + valeur attendue + commentaire |
| La valeur est lue mais techniquement incohérente (RG16) ? | Résultat `ANOMALIE` + code d'anomalie |

La comparaison porte sur le **sens**, pas sur la frappe : voir §4 (accents)
et §3 (unités). Contrôler systématiquement les champs de cotes, matériaux,
galons et jonctions (§5-§8) — ce sont les champs structurés les plus sensibles.

Ordre de vérification conseillé (limite le temps/fiche) : identité de la
fiche (§ champ `fiche.code`) → cotes → matériaux → galons → jonctions →
finitions/options/renforts → anomalies RG16 signalées.

## 3. Valeur correcte, valeur absente, valeur non applicable

- **`OK` (valeur correcte)** : ce qui est dans la zone PDF est ce qui est dans
  « valeur extraite », après conversion d'unité légitime (§3) et indifférence
  aux accents (§4). Écrire la **valeur attendue telle qu'elle apparaît dans le
  document** (accents et ponctuation inclus).
- **`ABSENTE` (valeur absente)** : le document contient l'information à
  l'endroit attendu (ou ailleurs dans la page) et l'extraction ne l'a pas
  lue. Ne JAMAIS inventer une valeur de remplacement. Commentaire obligatoire :
  où se trouve la valeur dans le document.
- **`NON_APPLICABLE` (valeur non applicable)** : le champ n'existe pas pour ce
  type de fiche (documenté dans le gabarit du type), ou le document porte
  explicitement « sans objet ». Motif obligatoire. Une valeur non applicable
  n'est PAS une valeur absente — ne pas mélanger les deux.

## 4. Unités et accents

**Unités** (règles de `seamtech_search/fiches/normalisation.py`) :

- longueurs grandes (SLU, SLE, SF, SHW…) : **mètres** en base, admettre dans
  le document `m`, `cm`, `mm` — la valeur normalisée convertit (`6,60 m` =
  `660 cm` = `6600 mm`). Vérifier la valeur normalisée, pas la frappe ;
- petites mesures (épaisseurs, largeurs de galons, œillets) : **millimètres**
  en base ;
- surface SPA : **m²** ; poids : **kg** ; grammage : **g/m²** ;
- virgule décimale française admise (`6,60`) autant que le point (`6.60`) ;
- si l'unité du document est ambiguë ou illisible (scan) → `ABSENTE` avec
  commentaire `unite_illisible`, jamais de devinette.

**Accents** :

- la comparaison d'égalité utilise la forme sans accents, minuscules, espaces
  comprimés (`sans_accents()`) : « Monofilón »/« monofilm » désigne la même
  valeur si le document l'écrit ainsi ;
- en revanche la **valeur attendue** est transcrite avec les accents du
  document (« Épaisseur 3 ») — la saisie de correction doit être fidèle au
  document, pas normalisée à la place du lecteur ;
- les majuscules accentuées des PDF scannés (« TETIERE » pour « Tétière »)
  ne sont pas une erreur d'extraction si la forme sans accents coïncide.

## 5. Cotes

- Deux jeux existent : **dessin** et **finie** (`fiche_cotes.jeu`) — ne jamais
  les confondre : vérifier l'en-tête du tableau dans le PDF.
- Champs : SLU, SLE, SF, SHW (m), SPA (m²), tétière (cm), poids (kg).
- Contrôles d'cohérence (déjà automatisés en RG16, revérifier à l'œil) :
  surface plausible `SPA ≈ 0,43 à 1,30 × ½·SLU·SLE` (bande de tolérance
  nommée, `docs/CONTROLES_RG16.md`) ; ordre SLU ≥ SLE ; cotes positives.
- Une cote recopiée dans le mauvais jeu (dessin ↔ finie) est `CORRIGEE`, pas
  `ANOMALIE`.

## 6. Galons

- Bande concernée : `guindant`, `chute`, `bordure` (autre → `ANOMALIE`
  `galon_bande_inconnue`).
- Champs : couleur, largeur (mm), matière, grammage (g/m²).
- Un galon présent sur le document et non extrait = `ABSENTE` par champ
  (pas une ligne `NON_APPLICABLE`).

## 7. Matériaux

- Rôles : `tissu_principal`, `cache_insignia`, `epaisseur` (niveaux 1..10).
- Désignation : garder la **valeur brute du document** (« Monofilm K903 ») ;
  le contrôle porte sur la désignation exacte, pas sur la classe matière.
- Épaisseurs : vérifier le rang (épaisseur 3 ≠ épaisseur 4) autant que la
  valeur (mm).
- Grammage en g/m² ; une matière sans grammage sur le document = champ
  `ABSENTE`, pas `NON_APPLICABLE` (la colonne existe pour tout tissu).

## 8. Jonctions

- Natures : `laizes`, `horizontale`, `verticale` (+ ordre de couture).
- Champs : description, nombre de zigzag, nombre de points, espacement (mm),
  surplus.
- Vérifier l'ordre des jonctions (1, 2, 3…) : une inversion d'ordre est
  `CORRIGEE` sur chaque ligne concernée.

## 9. Pages et zones PDF

- **`page`** : numéro de page **à partir de 1**, tel qu'affiché par le lecteur
  PDF (l'import contrôlé convertit en index 0 — `Zone.page`).
- **`zone_pdf`** : rectangle d'origine en points PDF, format
  `x0,y0,x1,y1` (repère pdfplumber, coin bas-gauche) — reprendre la zone de
  `ChampExtrait.zone` telle qu'affichée par l'outil de validation.
- Si la valeur lue par le validateur est HORS de la zone déclarée : cocher
  `ANOMALIE` `zone_incorrecte` (le modèle a lu la mauvaise case) même si le
  texte est juste.
- Une valeur répétée dans plusieurs zones (en-tête + tableau) : la zone de
  référence est celle du **tableau de valeurs** ; commenter si ambigu.

## 10. Noter une correction, noter une anomalie

**Correction** (`CORRIGEE`) :

- `valeur extraite` : laissée telle quelle (l'erreur doit rester visible) ;
- `valeur attendue` : valeur du document (§4 pour accents) ;
- `commentaire` : `CORRECTION: <ancienne> → <nouvelle> — <cause>`
  (ex. `CORRECTION: 6,06 → 6,60 — inversion de chiffres au scan`).
- chaque correction alimente l'apprentissage de gabarit — ne pas en perdre.

**Anomalie** (`ANOMALIE`) :

- `commentaire` : `ANOMALIE: <code> — <description>` avec code parmi
  `cote_hors_plage`, `surface_incoherente`, `cotes_incoherentes`,
  `champ_manquant`, `unite_suspecte`, `zone_incorrecte`, `lecture_illisible`
  (vocabulary des anomalies RG16 + extensions humaines documentées) ;
- gravité (faible/moyenne/forte) notée en commentaire ;
- une anomalie **bloque** la validation de la fiche (§12).

## 11. Identité du validateur

- Chaque fiche porte `validateur` : `Prénom N. (identifiant compte)` — le
  même identifiant que le compte nominatif (`seamtech_search.comptes`), pour
  que « qui a validé quoi » soit interrogeable en base.
- `date` : ISO `AAAA-MM-JJ` (jour de la décision finale).
- Toute manipulation d'extraction/correction **avant** validation est notée en
  `opérateur_extraction` du suivi CSV (« sans objet » si extraction
  100 % machine).

## 12. Décision de validation et contrôle à quatre yeux

Une fiche est `VALIDEE` si et seulement si :

1. chaque champ du modèle a un résultat dans {`OK`, `ABSENTE`,
   `NON_APPLICABLE`, `CORRIGEE`} — aucune case vide ;
2. aucune anomalie `ANOMALIE` ouverte ;
3. toute correction `CORRIGEE` a été **contre-vérifiée** (voir ci-dessous) ;
4. le statut final est signé par le `validateur` (nom + date).

**Contrôle à quatre yeux (anti-validation de sa propre erreur)** :

- si la fiche comporte au moins une `CORRIGEE` ou `ANOMALIE`, la validation
  finale exige un `controle_secondaire` (autre personne que l'opérateur qui a
  corrigé) : nom + date dans le suivi CSV ;
- l'opérateur qui a saisi/appliqué les corrections (`opérateur_extraction`)
  ne peut PAS être le seul signataire du statut `VALIDEE` ;
- si aucun second lecteur n'est disponible : statut final `A_CORRIGER` avec
  réserve `controle_secondaire_requis` — jamais `VALIDEE` par défaut ;
- un tirage de rattrapage (1 fiche sur 5, au hasard) est recontrôlé une
  semaine après la campagne par une autre personne.

Statuts finaux (et leur projection sur le workflow existant) :

| Statut final | Condition | Projection (statuts fiche existants) |
|---|---|---|
| `VALIDEE` | 4 conditions ci-dessus réunies | `valide` |
| `A_CORRIGER` | corrections à appliquer au gabarit, ou contrôle secondaire requis | `a_valider` |
| `REJETEE` | document inexploitable, fiche hors périmètre, anomalie forte non résolue | `rejete` |

## 13. Ce que ce protocole ne prétend pas

- Aucune donnée de ce document ni des modèles ne provient de l'archive
  réelle : tout exemple est marqué `EXEMPLE_SYNTHETIQUE`.
- Les chronomètres et taux de conformité ne seront mesurés qu'après arrivée
  des PDF — aucun chiffre de campagne n'existe à ce jour.
