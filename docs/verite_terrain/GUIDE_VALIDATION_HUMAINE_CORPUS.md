# Guide de validation humaine — corpus réel (7 dossiers REF-001..REF-007)

Statut au 2026-09-28 : le Lot 1 (gabarits + classification automatique) et le
Lot 2 (job CI `recette-corpus-reel`) sont livrés sur la branche de travail.
**Aucune fiche n'est validée** : les 7 fiches extraites du corpus réel sont
`a_valider` (RG3) et le resteront jusqu'à décision humaine. Ce guide prépare
cette décision — il ne la remplace jamais.

> **Cadre de confidentialité.** Les ZIP du corpus sont commités à la racine du
> dépôt par le commanditaire ; ce guide n'y ajoute AUCUNE valeur métier. Les
> fiches se repèrent par leurs codes REF-001..REF-007 (ordre alphabétique des
> dossiers extraits) et par le nom public du ZIP. Les valeurs des champs ne
> figurent nulle part dans ce document : le validateur les lit dans le PDF.

> **Le tableau `SUIVI_VALIDATION_REFERENCE.csv` reste le modèle synthétique
> contractuel** (`docs/templates/SUIVI_VALIDATION_REFERENCE.csv`, colonnes
> décrites dans `docs/templates/SUIVI_VALIDATION_REFERENCE.colonnes.md`).
> Le corpus réel EST le fonds réel annoncé par la RECETTE_HUMAINE.md : les
> lignes de suivi de ce corpus doivent être saisies dans ce même format, avec
> attribution nominative et contrôle secondaire (protocole §12, quatre yeux).

---

## 1. Lancer la stack locale (docker compose — chemin validé)

Le chemin prouvé est celui de MISE_EN_SERVICE.md §1 (rejoué à chaque CI par le
job `integration`) :

```bash
git clone https://github.com/Ilyes-Neguir/SEAMTECH-search.git
cd SEAMTECH-search

cp .env.example .env
# éditer .env : SEAMTECH_AUTH_TOKEN, SEAMTECH_UI_PASSWORD, SEAMTECH_SESSION_SECRET

docker compose up -d --build
docker compose ps          # postgres, minio, redis, web, frontend : running (healthy)
```

**Comptes nominatifs obligatoires avant toute validation** (MISE_EN_SERVICE.md
§1.4) : chaque validation/correction doit être attribuée à une personne, jamais
au compte de secours partagé.

```bash
docker compose exec web python -m seamtech_search.comptes.cli creer \
    --identifiant prenom.x --nom "Prénom X." --role validateur
docker compose exec web python -m seamtech_search.comptes.cli lister
```

Interface : `http://127.0.0.1:3000` (connexion nominative).

## 2. Déposer les 7 dossiers du corpus

Les 7 ZIP sont à la racine du dépôt. Extraire chaque ZIP dans un dossier local
temporaire (par ex. `~/corpus/`), PUIS dans l'interface : écran **Dépôt** →
déposer le dossier extrait (un dépôt par dossier d'affaire). Le dépôt Lot C
détecte le PDF fiche (candidat technique), l'extrait, et rattache les pièces
jointes ; la fiche arrive `a_valider` dans la file de validation.

Pour chaque dossier, le système doit proposer **le même PDF fiche que la
recette CI** (annexe A : « PDF à ouvrir »). Si le candidat proposé diffère,
noter l'écart dans le suivi — ne pas forcer.

| Code | Dossier (nom public du ZIP, ordre alphabétique) | Famille de format | Points d'attention propres |
|---|---|---|---|
| REF-001 | `AQUILA 250216AJA-20260928T182324Z-1-001.zip` | JADE | tissu absent de la fiche (champ honnêtement vide) |
| REF-002 | `ATTALIA 250121JA-20260928T182325Z-1-001.zip` | JADE | génois : vérifier le type de voile et son accent |
| REF-003 | `BAVARIA 32 - 250604JA-20260928T182326Z-1-001.zip` | JADE | **tissu présent** (colonne centrale, sous la ligne DATE) |
| REF-004 | `BAVARIA 34 - 250323JA-20260928T182327Z-1-001.zip` | JADE | génois ; bande de visu |
| REF-005 | `DAMIEN 4 - 250821JA-20260928T182330Z-1-001.zip` | JADE | **tissu + remarques (DIVERS) + renforts** présents |
| REF-006 | `DEHLER 39 - 250329AJA-20260928T182330Z-1-001.zip` | **GV FULLBATTEN** | fiche la plus riche (40 propositions) : ris 1-3, goussets L1-L7, lattes, bôme, numéro de voile, montage zigzag + fil |
| REF-007 | `GIB SEA 284 - 250328AJA-20260928T182331Z-1-001.zip` | JADE | génois ; 3e fiche GSE de la même mise en page |

Dans chaque dossier : **ouvrir le PDF proposé comme candidat technique**
(l'unique « fiche de fabrication » du dossier — les autres PDF sont des plans,
classés `plan_pdf`, et les DXF/CSV/ZIP sont des pièces jointes RG12).

## 3. Contrôler chaque fiche (par REF)

Principe : pour CHAQUE proposition machine (annexe A : clé, page, zone,
confiance), ouvrir le PDF à la page indiquée, regarder la zone surlignée, et
statuer `OK` / `CORRIGEE` / `ABSENTE` / `NON_APPLICABLE` / `ANOMALIE` dans le
format du suivi contractuel. La confiance machine (0,7 à 0,99) est un **ordre
de lecture suggéré** (basse confiance d'abord), jamais une décision.

### 3.1 Champs communs aux 7 fiches (familles JADE et GV)

- **En-tête (confiance 0,99)** : `fiche.titre`, `fiche.code` (référence de
  commande — attention aux initiales en fin de code), `fiche.client`,
  `fiche.bateau`, `fiche.designation` (type de voile — confiance 0,70 :
  **accent à vérifier**, ex. « Génois » avec É), `fiche.date_edition`
  (format ISO reconstruit — vérifier jour/mois/année non inversés),
  `fiche.surface` (`spa_m2`).
- **Cotes « finies » (0,99)** : `cotes.finie.slu_m` (guindant), `sle_m`
  (chute), `sf_m` (bordure), `spa_m2` (surface). **Vigilance unités** : le
  document porte des décimales françaises (virgule) en mètres ; la machine
  propose une valeur normalisée en m — contrôler la virgule et l'unité
  affichée dans le PDF (« à la corde », « a plat », « pliée » ne sont PAS des
  cotes finies : ce sont des galons).
- **Galons BDF typés (0,85)** : `galon.<bord>.<variante>` pour
  `<bord>` ∈ {guindant, chute, bordure} et `<variante>` ∈ {a_plat, pliee,
  decalee}. Vigilance : une même bande « a plat / pliée / décalée » doit se
  retrouver du bon bord (le libellé du bord est AU-DESSUS de la bande).
- **Finitions / points d'ancrage (0,85)** : `finition.amure`,
  `finition.ecoute`, `finition.drisse`, `finition.ris_chute`,
  `finition.ris_gt`. Vigilance : « Non Sanglé » (négation, souvent avec un
  espace) doit donner une finition NON sanglée — si la machine propose
  « sanglé » face à un « Non Sanglé » imprimé, corriger et noter l'anomalie.
- **Montage (0,85)** : `fiche.montage_type` (points zigzag × temps) et le fil
  (« | fil NNN » accolé à la valeur).

### 3.2 Champs propres à certaines fiches

- **Tissu** (`materiau.tissu_principal`, 0,85) : présent pour REF-003, REF-005
  et REF-006 seulement. Il vit dans la **colonne centrale, dans la bande sous
  la ligne DATE** — la valeur peut être répartie sur DEUX lignes (matière puis
  couleur). Pour REF-001/002/004/007 la machine ne propose RIEN : c'est
  l'absence honnête (ne pas « compléter » sans le lire soi-même dans le PDF ;
  si le PDF porte réellement une matière, c'est une `ANOMALIE` à signaler).
- **Remarques** (`fiche.notes`) : REF-005 (ligne DIVERS).
- **Renforts** (`renfort.*`) : REF-005 (ligne RENFORTS au-dessus du planning).
- **GV uniquement (REF-006)** : `libre.ris_1/2/3` (longueurs de ris),
  `libre.gousset_l1..l7` (vigilance : les goussets vont **par paires**
  « L7 = L4 = valeur » — chaque paire donne DEUX clés, vérifier que la valeur
  n'est pas dupliquée sur la mauvaise clé), `libre.lattes`, `libre.bome`,
  `libre.numero_voile`, `galon.guindant.pliee`, `finition.ris` /
  `finition.ris_chute` ( négation « Non Sanglé » fréquente ici).

### 3.3 Limites connues à ne PAS traiter comme des anomalies de lecture

- **« Bande UV »** : non extraite — hors périmètre des champs cibles du Lot 1
  (limite documentée, pas un défaut).
- **Confiance 0,70 sur `fiche.designation`** : le libellé du type de voile
  est le champ d'en-tête le moins certain (accents, abréviations atelier) —
  c'est le PREMIER champ à relire.
- **Rien n'est validé automatiquement** : le statut `a_valider` de chaque
  fiche est le comportement attendu (RG3), pas un retard.

## 4. Saisir une correction (attribution nominative + contrôle secondaire)

Protocole §10/§12 — même mécanique que la RECETTE_HUMAINE.md :

1. Ouvrir la fiche dans l'écran **Validation** (le PDF s'affiche à côté des
   propositions ; les zones d'origine sont visibles — annexe A).
2. Pour chaque champ à corriger : modifier la valeur **depuis la session
   nominative** — l'attribution est portée par la session (en-têtes posés par
   le proxy depuis le compte ; un script ne peut plus usurper une session).
   La correction est journalisée avec l'auteur.
3. Recopier la décision dans `SUIVI_VALIDATION_REFERENCE.csv` :
   `resultat=CORRIGEE` ⇒ `valeur_attendue` non vide et `commentaire` au
   format `CORRECTION: <ancienne> → <nouvelle> — <cause>`.
4. **Contrôle secondaire obligatoire** dès qu'une fiche porte au moins une
   `CORRIGEE` ou une `ANOMALIE` : une SECONDE personne relit et signe la
   colonne `controle_secondaire` (`Prénom N. (identifiant) AAAA-MM-JJ`) —
   garde quatre yeux.
5. Un champ corrigé puis validé est **verrouillé** (RG11) : pour y revenir il
   faut rouvrir la fiche (POST `/fiches/{code}/rouvrir`) — c'est voulu.

## 5. Passer la fiche de `a_valider` à `validée`

- Dans l'écran Validation, après relecture de TOUTES les propositions (aucune
  ligne sans décision) : action **Valider** (statut `valide`).
- La fiche validée devient cherchable dans la recherche générale (les fiches
  `valide` seulement y figurent ; `inclure_a_valider` est l'option de la file
  de travail, pas de l'usage courant).
- Recopier `statut_final` (`VALIDEE` / `A_CORRIGER` / `REJETEE`) sur chaque
  ligne du suivi — la décision reste au périmètre exact du commanditaire.

## 6. Rappels de cadre

- **Calibration verrouillée** : aucun seuil de confiance n'est ajusté pendant
  la validation ; une proposition douteuse se corrige ou s'anomalie, elle ne
  se « règle » pas.
- **F-2 verrouillé** : pas d'extrapolation au-delà des 7 dossiers fournis —
  0 validation humaine à ce jour, la décision « product-ready » attend la
  sortie de ce guide exécuté.
- Le tableau synthétique contractuel reste `SUIVI_VALIDATION_REFERENCE.csv`
  (encodage utf-8-sig, `;`) — le présent guide est le mode opératoire, pas le
  registre.

---

## Annexe A — Propositions machine par REF (liste de contrôle anonymisée)

Généré depuis l'extraction Lot 1 (branche de travail, 2026-09-28) : **clés de
champs, pages, zones d'origine (points PDF, repère pdfplumber) et confiances
uniquement — aucune valeur métier**. Page 1 = première page du lecteur PDF.

### REF-001 — gabarit FICHE_JADE_V1 — 22 champs, confiance moyenne 0.90

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "15,242,177,252" | 0.99 |
| `cotes.finie.sle_m` | 1 | "15,168,177,178" | 0.99 |
| `cotes.finie.slu_m` | 1 | "15,106,89,116" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "323,82,556,92" | 0.99 |
| `fiche.bateau` | 1 | "273,57,309,67" | 0.99 |
| `fiche.client` | 1 | "70,57,127,67" | 0.99 |
| `fiche.code` | 1 | "357,45,465,55" | 0.99 |
| `fiche.date_edition` | 1 | "70,82,119,92" | 0.99 |
| `fiche.designation` | 1 | "506,57,527,67" | 0.7 |
| `fiche.montage_type` | 1 | "15,316,502,326" | 0.85 |
| `fiche.titre` | 1 | "133,20,250,30" | 0.99 |
| `finition.amure` | 1 | "70,390,505,400" | 0.85 |
| `finition.drisse` | 1 | "70,440,505,450" | 0.85 |
| `finition.ecoute` | 1 | "70,415,450,425" | 0.85 |
| `finition.ris_chute` | 1 | "70,464,241,474" | 0.85 |
| `finition.ris_gt` | 1 | "70,489,241,499" | 0.85 |
| `galon.bordure.a_plat` | 1 | "70,254,300,264" | 0.85 |
| `galon.bordure.pliee` | 1 | "70,267,482,276" | 0.85 |
| `galon.chute.a_plat` | 1 | "70,180,300,190" | 0.85 |
| `galon.chute.decalee` | 1 | "70,205,539,215" | 0.85 |
| `galon.chute.pliee` | 1 | "70,192,575,202" | 0.85 |
| `galon.guindant.a_plat` | 1 | "70,118,365,128" | 0.85 |

### REF-002 — gabarit FICHE_JADE_V1 — 22 champs, confiance moyenne 0.90

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "9,242,171,252" | 0.99 |
| `cotes.finie.sle_m` | 1 | "9,168,171,178" | 0.99 |
| `cotes.finie.slu_m` | 1 | "9,106,83,116" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "310,82,556,92" | 0.99 |
| `fiche.bateau` | 1 | "273,57,314,67" | 0.99 |
| `fiche.client` | 1 | "64,57,122,67" | 0.99 |
| `fiche.code` | 1 | "357,45,458,55" | 0.99 |
| `fiche.date_edition` | 1 | "64,82,114,92" | 0.99 |
| `fiche.designation` | 1 | "507,57,528,67" | 0.7 |
| `fiche.montage_type` | 1 | "64,328,435,338" | 0.85 |
| `fiche.titre` | 1 | "127,20,245,30" | 0.99 |
| `finition.amure` | 1 | "64,390,506,400" | 0.85 |
| `finition.drisse` | 1 | "64,440,506,450" | 0.85 |
| `finition.ecoute` | 1 | "64,415,387,425" | 0.85 |
| `finition.ris_chute` | 1 | "64,464,242,474" | 0.85 |
| `finition.ris_gt` | 1 | "64,489,242,499" | 0.85 |
| `galon.bordure.a_plat` | 1 | "64,254,300,264" | 0.85 |
| `galon.bordure.pliee` | 1 | "64,267,505,277" | 0.85 |
| `galon.chute.a_plat` | 1 | "64,180,300,190" | 0.85 |
| `galon.chute.decalee` | 1 | "64,205,565,215" | 0.85 |
| `galon.chute.pliee` | 1 | "64,192,505,202" | 0.85 |
| `galon.guindant.a_plat` | 1 | "64,118,366,128" | 0.85 |

### REF-003 — gabarit FICHE_JADE_V1 — 22 champs, confiance moyenne 0.92

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "11,240,86,249" | 0.99 |
| `cotes.finie.sle_m` | 1 | "11,165,86,175" | 0.99 |
| `cotes.finie.slu_m` | 1 | "11,104,86,114" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "319,79,552,89" | 0.99 |
| `fiche.bateau` | 1 | "269,59,314,67" | 0.99 |
| `fiche.client` | 1 | "66,57,124,67" | 0.99 |
| `fiche.code` | 1 | "406,45,455,55" | 0.99 |
| `fiche.date_edition` | 1 | "66,79,116,89" | 0.99 |
| `fiche.designation` | 1 | "503,57,575,67" | 0.99 |
| `fiche.notes` | 1 | "129,551,425,561" | 0.99 |
| `fiche.titre` | 1 | "129,20,247,30" | 0.99 |
| `finition.amure` | 1 | "66,388,294,398" | 0.85 |
| `finition.drisse` | 1 | "66,437,294,447" | 0.85 |
| `finition.ecoute` | 1 | "66,413,396,423" | 0.85 |
| `galon.bordure.a_plat` | 1 | "66,252,378,262" | 0.85 |
| `galon.bordure.pliee` | 1 | "66,264,522,274" | 0.85 |
| `galon.chute.a_plat` | 1 | "66,178,368,188" | 0.85 |
| `galon.chute.decalee` | 1 | "66,202,362,212" | 0.85 |
| `galon.chute.pliee` | 1 | "66,190,522,200" | 0.85 |
| `galon.guindant.a_plat` | 1 | "66,116,430,126" | 0.85 |
| `materiau.tissu_principal` | 1 | "196,80,257,100" | 0.85 |
| `renforts.ligne` | 1 | "11,326,446,336" | 0.85 |

### REF-004 — gabarit FICHE_JADE_V1 — 22 champs, confiance moyenne 0.91

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "9,242,171,252" | 0.99 |
| `cotes.finie.sle_m` | 1 | "9,168,171,178" | 0.99 |
| `cotes.finie.slu_m` | 1 | "9,106,83,116" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "310,82,556,92" | 0.99 |
| `fiche.bateau` | 1 | "273,57,330,67" | 0.99 |
| `fiche.client` | 1 | "64,57,122,67" | 0.99 |
| `fiche.code` | 1 | "357,45,459,55" | 0.99 |
| `fiche.date_edition` | 1 | "64,82,114,92" | 0.99 |
| `fiche.designation` | 1 | "507,57,545,67" | 0.99 |
| `fiche.montage_type` | 1 | "64,328,506,338" | 0.85 |
| `fiche.titre` | 1 | "127,20,245,30" | 0.99 |
| `finition.amure` | 1 | "64,390,506,400" | 0.85 |
| `finition.drisse` | 1 | "64,440,506,450" | 0.85 |
| `finition.ecoute` | 1 | "64,415,390,425" | 0.85 |
| `finition.ris_chute` | 1 | "64,464,242,474" | 0.85 |
| `finition.ris_gt` | 1 | "64,489,242,499" | 0.85 |
| `galon.bordure.a_plat` | 1 | "64,254,300,264" | 0.85 |
| `galon.bordure.pliee` | 1 | "64,267,505,277" | 0.85 |
| `galon.chute.a_plat` | 1 | "64,180,300,190" | 0.85 |
| `galon.chute.decalee` | 1 | "64,205,565,215" | 0.85 |
| `galon.chute.pliee` | 1 | "64,192,505,202" | 0.85 |
| `galon.guindant.a_plat` | 1 | "64,118,366,128" | 0.85 |

### REF-005 — gabarit FICHE_JADE_V1 — 23 champs, confiance moyenne 0.92

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "11,243,86,252" | 0.99 |
| `cotes.finie.sle_m` | 1 | "11,168,91,178" | 0.99 |
| `cotes.finie.slu_m` | 1 | "11,107,91,117" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "319,82,556,92" | 0.99 |
| `fiche.bateau` | 1 | "269,57,316,67" | 0.99 |
| `fiche.client` | 1 | "66,57,124,67" | 0.99 |
| `fiche.code` | 1 | "410,45,459,55" | 0.99 |
| `fiche.date_edition` | 1 | "66,82,116,92" | 0.99 |
| `fiche.designation` | 1 | "507,57,579,67" | 0.99 |
| `fiche.notes` | 1 | "129,554,512,564" | 0.99 |
| `fiche.titre` | 1 | "129,20,247,30" | 0.99 |
| `finition.amure` | 1 | "66,391,185,401" | 0.85 |
| `finition.drisse` | 1 | "66,440,186,450" | 0.85 |
| `finition.ecoute` | 1 | "66,416,400,426" | 0.85 |
| `galon.bordure.a_plat` | 1 | "66,255,296,265" | 0.85 |
| `galon.bordure.pliee` | 1 | "66,267,574,277" | 0.85 |
| `galon.bordure.pliee` | 1 | "66,577,516,586" | 0.85 |
| `galon.chute.a_plat` | 1 | "66,181,296,191" | 0.85 |
| `galon.chute.decalee` | 1 | "66,205,366,215" | 0.85 |
| `galon.chute.pliee` | 1 | "66,193,574,203" | 0.85 |
| `galon.guindant.a_plat` | 1 | "66,119,563,129" | 0.85 |
| `materiau.tissu_principal` | 1 | "129,93,235,103" | 0.85 |
| `renforts.ligne` | 1 | "11,329,435,339" | 0.85 |

### REF-006 — gabarit FICHE_GV_FULLBATTEN_V1 — 40 champs, confiance moyenne 0.84

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "11,301,115,311" | 0.99 |
| `cotes.finie.sle_m` | 1 | "11,215,121,225" | 0.99 |
| `cotes.finie.slu_m` | 1 | "11,389,435,399" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "290,82,512,92" | 0.99 |
| `fiche.bateau` | 1 | "269,57,323,67" | 0.99 |
| `fiche.client` | 1 | "66,57,124,67" | 0.99 |
| `fiche.code` | 1 | "339,45,393,55" | 0.99 |
| `fiche.date_edition` | 1 | "66,82,116,92" | 0.99 |
| `fiche.designation` | 1 | "504,57,569,67" | 0.99 |
| `fiche.montage_type` | 1 | "66,770,544,780" | 0.85 |
| `fiche.notes` | 1 | "66,783,440,793" | 0.99 |
| `fiche.titre` | 1 | "129,20,247,30" | 0.99 |
| `finition.amure` | 1 | "66,475,476,485" | 0.85 |
| `finition.drisse` | 1 | "66,525,387,535" | 0.85 |
| `finition.ecoute` | 1 | "66,500,535,510" | 0.85 |
| `finition.ris` | 1 | "11,574,542,584" | 0.85 |
| `finition.ris_chute` | 1 | "11,549,488,559" | 0.85 |
| `galon.bordure.a_plat` | 1 | "66,314,359,324" | 0.85 |
| `galon.bordure.pliee` | 1 | "66,326,382,336" | 0.85 |
| `galon.chute.a_plat` | 1 | "66,227,443,237" | 0.85 |
| `galon.chute.decalee` | 1 | "66,252,362,262" | 0.85 |
| `galon.chute.pliee` | 1 | "66,240,507,250" | 0.85 |
| `galon.guindant.a_plat` | 1 | "66,401,530,411" | 0.85 |
| `galon.guindant.pliee` | 1 | "66,413,512,423" | 0.85 |
| `libre.bome` | 1 | "408,512,490,522" | 0.7 |
| `libre.gousset_l1` | 1 | "66,165,561,175" | 0.7 |
| `libre.gousset_l2` | 1 | "66,178,528,188" | 0.7 |
| `libre.gousset_l3` | 1 | "66,165,561,175" | 0.7 |
| `libre.gousset_l4` | 1 | "66,153,569,163" | 0.7 |
| `libre.gousset_l5` | 1 | "66,178,528,188" | 0.7 |
| `libre.gousset_l6` | 1 | "66,165,561,175" | 0.7 |
| `libre.gousset_l7` | 1 | "66,153,569,163" | 0.7 |
| `libre.lattes` | 1 | "11,128,545,138" | 0.7 |
| `libre.lattes` | 1 | "66,807,375,817" | 0.7 |
| `libre.numero_voile` | 1 | "133,264,378,274" | 0.7 |
| `libre.ris_1` | 1 | "11,104,417,114" | 0.7 |
| `libre.ris_2` | 1 | "11,104,417,114" | 0.7 |
| `libre.ris_3` | 1 | "11,104,417,114" | 0.7 |
| `materiau.tissu_principal` | 1 | "196,92,250,101" | 0.85 |
| `renforts.ligne` | 1 | "66,438,508,448" | 0.85 |

### REF-007 — gabarit FICHE_JADE_V1 — 22 champs, confiance moyenne 0.90

| Clé de champ | Page | Zone (pt) | Confiance |
|---|---|---|---|
| `cotes.finie.sf_m` | 1 | "9,242,171,252" | 0.99 |
| `cotes.finie.sle_m` | 1 | "9,168,171,178" | 0.99 |
| `cotes.finie.slu_m` | 1 | "9,106,78,116" | 0.99 |
| `cotes.finie.spa_m2` | 1 | "310,82,556,92" | 0.99 |
| `fiche.bateau` | 1 | "273,58,326,67" | 0.99 |
| `fiche.client` | 1 | "64,57,122,67" | 0.99 |
| `fiche.code` | 1 | "357,45,466,55" | 0.99 |
| `fiche.date_edition` | 1 | "64,82,114,92" | 0.99 |
| `fiche.designation` | 1 | "507,57,528,67" | 0.7 |
| `fiche.montage_type` | 1 | "64,328,435,338" | 0.85 |
| `fiche.titre` | 1 | "127,20,245,30" | 0.99 |
| `finition.amure` | 1 | "64,390,506,400" | 0.85 |
| `finition.drisse` | 1 | "64,440,506,450" | 0.85 |
| `finition.ecoute` | 1 | "64,415,387,425" | 0.85 |
| `finition.ris_chute` | 1 | "64,464,242,474" | 0.85 |
| `finition.ris_gt` | 1 | "64,489,242,499" | 0.85 |
| `galon.bordure.a_plat` | 1 | "64,254,300,264" | 0.85 |
| `galon.bordure.pliee` | 1 | "64,267,505,277" | 0.85 |
| `galon.chute.a_plat` | 1 | "64,180,300,190" | 0.85 |
| `galon.chute.decalee` | 1 | "64,205,565,215" | 0.85 |
| `galon.chute.pliee` | 1 | "64,192,505,202" | 0.85 |
| `galon.guindant.a_plat` | 1 | "64,118,366,128" | 0.85 |

