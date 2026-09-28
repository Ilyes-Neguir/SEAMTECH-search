> **Mise à jour :** ce rapport est conservé comme compte rendu initial. Ses
> mesures préflight/OCR et ses conclusions opérationnelles ont été dépassées
> par la reprise avec environnement isolé et parseurs réels. Pour le résultat
> actuel, l'API locale, les tests et la décision finale, consulter
> [RAPPORT_FLUX_COMPLET_CORPUS_REEL_2026-09-28.md](RAPPORT_FLUX_COMPLET_CORPUS_REEL_2026-09-28.md).

# Rapport final — corpus réel des ZIP fournis (2026-09-28)

## Décision synthétique

**NON VALIDABLE pour une mise en production sur ce corpus.** Les 7 ZIP et leurs
ZIP internes ont été inventoriés, contrôlés puis extraits hors du dépôt sans
modifier les originaux. En revanche, le préflight opérationnel refuse le
traitement (code 6 : Tesseract, langue française et Poppler manquants), et les
parseurs PDF Python ainsi que les outils de test et l'environnement Docker ne
sont pas installés. Il n'y a donc pas de preuve d'OCR réel, d'extraction métier,
d'import, de recherche sur ces données, de validation humaine ou de
sauvegarde/restauration sur ces données. Cette décision reflète les moyens
locaux de cette exécution ; elle ne signifie pas que les ZIP sont absents ou
corrompus.

> **Périmètre impératif :** les chiffres de ce rapport concernent uniquement
> les ZIP fournis dans ce dépôt et ne représentent pas une archive complète non
> fournie.

## 1. État initial post-merge

- Date d'exécution : 2026-09-28 (UTC).
- Branche : `arena/01a0e94b-seamtech-search`.
- SHA de base : `cbbe405b31107ea36032da5a2c44810475b40fce`.
- `origin/main` : même SHA `cbbe405b31107ea36032da5a2c44810475b40fce`.
- État Git initial : propre (`git status --short --branch` ne montrait aucun
  changement).
- Dernière PR fusionnée : [#29](https://github.com/Ilyes-Neguir/SEAMTECH-search/pull/29),
  fusionnée le 2026-09-28 à 18:31:41Z ; elle porte le correctif D-1 des URL
  présignées, R-14, l'audit stockage et la checklist release candidate.
- CI post-merge : run
  [36465815698](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36465815698),
  SHA de tête `cbbe405b31107ea36032da5a2c44810475b40fce`, terminé avec succès à
  18:38:36Z ; les 9 jobs (frontend, backend Python 3.11/3.12/3.13, Docker, E2E,
  OCR, intégration et sauvegarde) sont `success`. Ce run porte sur le SHA de
  base, avant les modifications locales décrites dans ce lot ; il ne valide
  donc pas ces dernières. Dernier run CI terminé visible au début de l'exécution :
  [36168258103](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36168258103),
  succès sur le commit `f8befb5da800c10d793e25addf9519cc63f5fd6c`.
- Présence vérifiée dans le dépôt : pipeline OCR par étages et CLI ; préflight
  archive RG13/RG14 ; protocole de validation humaine ; audit S3/MinIO ; appels
  présignés D-1 ; sélection CI par marqueurs R-14 ; schéma jusqu'à
  `017_ocr_etage3`. Le test de schéma affirme 33 tables métier ; ces éléments
  sont présents, mais leurs scénarios d'intégration n'ont pas été rejoués
  localement dans cet environnement.

## 2. Inventaire ZIP préalable à l'extraction

Les empreintes SHA-256 ont été calculées avant toute extraction. Limites de
sécurité appliquées par l'inventaire : au plus 100 000 entrées et 2 Gio
annoncés décompressés par ZIP ; refus des chemins absolus, `..`, liens
symboliques, entrées chiffrées, dépassements de limite et ZIP illisibles. Les
7 ZIP externes et les 7 ZIP internes détectés ont passé les contrôles de chemin,
limite, CRC et chiffrement ; aucune entrée suspecte n'a été relevée. Les ZIP
internes ont été examinés comme archives, pas ignorés comme simples fichiers.

| ZIP (chemin dépôt) | SHA-256 | Taille (octets) | Entrées | PDF directs | Décompressé annoncé (octets) | Décision |
|---|---|---:|---:|---:|---:|---|
| `AQUILA 250216AJA-20260928T182324Z-1-001.zip` | `fd1fca0930494328687ecf5d6905af139c24f93a8b9e2e51b2f6075772446be6` | 573860 | 46 | 2 | 1004984 | Sûr, extrait hors dépôt |
| `ATTALIA 250121JA-20260928T182325Z-1-001.zip` | `a71ec77d6d5abf3c55f5ca8aa5c79d3347d3b3765b91f54950065b9c2161d699` | 594852 | 48 | 2 | 1040815 | Sûr, extrait hors dépôt |
| `BAVARIA 32 - 250604JA-20260928T182326Z-1-001.zip` | `a9ca298cae78ccbacfaab69260cede299062ef0d20e0a7fba62c3539ac3a538f` | 745910 | 69 | 2 | 1329304 | Sûr, extrait hors dépôt |
| `BAVARIA 34 - 250323JA-20260928T182327Z-1-001.zip` | `483f89716b953734315b7bbb4ba81050dc24b33a162e2a2623604eee268b8f6f` | 618247 | 51 | 2 | 1076735 | Sûr, extrait hors dépôt |
| `DAMIEN 4 - 250821JA-20260928T182330Z-1-001.zip` | `2f1ecb0205b817c3dbdb324bf444aad9fb5ddce1d3f415859698fbb21fb428bf` | 846621 | 81 | 2 | 1450289 | Sûr, extrait hors dépôt |
| `DEHLER 39 - 250329AJA-20260928T182330Z-1-001.zip` | `b3171d070f6b332366263626a665e7f7985bb8406062c2957895401850df6a06` | 919707 | 88 | 2 | 1740225 | Sûr, extrait hors dépôt |
| `GIB SEA 284 - 250328AJA-20260928T182331Z-1-001.zip` | `103359b561ef32f5a8ce04b373864c26a0b496156efc1f728e75f0c0911b4aa3` | 581169 | 46 | 2 | 997561 | Sûr, extrait hors dépôt |
| **Total ZIP externes** | — | **4880366** | **429** | **14** | **8639913** | **7/7 sûrs** |

Les sept ZIP externes contiennent chacun un ZIP interne. Les ZIP internes
ajoutent 386 entrées ; l'expansion récursive a écrit 815 entrées de fichiers
au total (dont 7 conteneurs ZIP internes), soit 13 927 768 octets. Tous les
flux ont passé `ZipFile.testzip()`/CRC. Aucune archive n'a été contournée.

## 3. Inventaire réel après expansion contrôlée

- Dossiers/dossiers de travail représentant les ZIP externes : **7**.
- Répertoires créés par l'expansion : **23** au total.
- Fichiers matérialisés en comptant les 7 conteneurs ZIP internes : **815** ;
  fichiers non-ZIP : **808**.
- PDF présents après expansion, copies incluses : **21** ; PDF distincts par
  SHA-256 : **14** ; les 7 autres sont des copies exactes présentes également
  dans les ZIP internes.
- Taille totale des fichiers matérialisés, copies et conteneurs compris :
  **13 927 768 octets**.
- Répartition complète par extension (nombre / octets), copies comprises :

| Extension | Fichiers | Octets |
|---|---:|---:|
| `.dxf` | 730 | 7917664 |
| `.pdf` | 21 | 2700559 |
| `.plx` | 22 | 919619 |
| `.csv` | 14 | 27360 |
| `.xin` | 14 | 76034 |
| `.zip` | 7 | 2186964 |
| `.db` | 4 | 54272 |
| `.tis` | 2 | 850 |
| `.jpg` | 1 | 44446 |

- Doublons exacts : **387 groupes SHA-256 en double**, 387 copies excédentaires ;
  428 contenus distincts sur les 815 fichiers (ZIP internes inclus).
- Noms : 30 fichiers dont le nom contient des espaces ; aucun nom de fichier
  non-ASCII, aucun chemin de plus de 180 caractères et aucune forme de chemin
  non-NFC détectés dans l'arbre extrait.
- PDF : 21/21 ont un en-tête `%PDF-`, une marque `%%EOF` dans les 2 derniers
  Kio, et une taille non nulle ; aucun marqueur `/Encrypt` trouvé. Ce contrôle
  de signature **n'est pas** une preuve de lisibilité par un lecteur PDF.
- Diagnostic syntaxique bas niveau (recherche de `/Type /Page` et opérateurs de
  texte dans flux PDF, dont flux Flate décompressés) : 53 occurrences de pages
  sur les 21 fichiers physiques ; 30 sur les 14 contenus distincts. Des
  opérateurs texte sont présents dans les 21 fichiers physiques. Il s'agit
  d'indices non validés par un parseur PDF : le nombre de pages, le caractère
  natif/scanné, les fichiers vides au sens PDF et les erreurs de lecture restent
  **non confirmés par le pipeline**.
- Le diagnostic bas niveau indique 7 contenus distincts à 1 page et 7 contenus
  distincts à 3 ou 5 pages. Sept documents d'une page, un par ZIP, sont des
  candidats de fiches d'après l'organisation et les noms ; leur classification
  métier reste à confirmer par extraction.

Les empreintes, chemins relatifs complets, tailles et mtimes par fichier extrait
sont conservés hors Git sous `/tmp/seamtech-rc-20260928/files-private.json` et
`extraction-manifest.json` durant cette exécution. Les rapports partagés ne
reproduisent pas les noms internes ni le contenu des PDF.

## 4. Préflight, sécurité et intégrité des sources

Source : répertoire externe `/tmp/seamtech-rc-20260928/extracted` ; travail et
sortie sous `/tmp/seamtech-rc-20260928`, tous disjoints de la source et du dépôt.

- Commande de préflight existant exécutée :
  `python scripts/preflight_archive.py preflight --source /tmp/seamtech-rc-20260928/extracted --travail /tmp/seamtech-rc-20260928/work --sortie /tmp/seamtech-rc-20260928/preflight --min-libre-o 0 --echantillon-pdfs 25 --masquer-chemins --json`.
- Résultat : **code 6, refus prérequis**. Source accessible et contenant 815
  fichiers/21 PDF ; répertoires disjoints ; version dépôt reconnue ; schéma
  `017_ocr_etage3` reconnu ; espace libre annoncé 20,7 Gio. Refus causé par
  l'absence de `tesseract`, langue `fra` invérifiable et absence de
  `pdftoppm`/`pdftocairo`. Avertissement complémentaire : l'utilisateur courant
  peut écrire dans la copie extraite. Aucune écriture n'a été effectuée dans
  cette source.
- Rapport JSON et rapport texte du préflight, ainsi que les sorties stdout/stderr,
  restent hors Git sous `/tmp/seamtech-rc-20260928/preflight*`.
- Avant/après de l'arbre extrait comparé sur chemins, SHA-256, tailles et mtimes :
  **815/815 identiques**.
- SHA-256 après extraction des ZIP originaux comparés aux SHA initiaux :
  **7/7 identiques** ; tailles originales inchangées (4 880 366 octets au total).
- Les PDF n'ont pas été ouverts en écriture. Les extractions sont restées hors
  dépôt ; aucune extraction n'est suivie par Git. Aucun appel réseau n'a été
  effectué par les commandes de corpus et aucun paquet ni fichier n'a été
  téléchargé. GitHub a été interrogé uniquement pour la vérification post-merge
  et CI exigée à l'étape 0.
- RG13 : **intégrité observée identique** pour les ZIP originaux et les 815
  fichiers extraits sur SHA-256, taille et mtime ; préflight et inventaire sont
  des lecteurs. RG14 : le travail de corpus a été local ; les tests RG14 n'ont
  pas pu être exécutés localement faute de `pytest`.

## 5. OCR : pilote et traitement du corpus

- Budget explicite visé pour un pilote éventuel : 30 minutes et 7 candidats de
  fiche ; ce budget n'a pas été consommé, car le préflight a refusé avant OCR.
- **A. Pilote : non exécuté.** Aucune page OCRisée, moteur/version/langue,
  confiance, résolution, durée OCR, erreur Tesseract ou débit mesuré sur les
  vrais PDF n'est disponible.
- **B. Traitement complet : non exécuté.** Il n'est pas présenté comme une
  production complète.
- Commande d'inventaire OCR existante tentée :
  `python -m seamtech_search.ocr.cli inventaire --dossier /tmp/seamtech-rc-20260928/extracted --json`.
  Avant le garde-fou ajouté à cette branche, la commande pouvait produire un
  inventaire incomplet lorsque `pdfplumber` et `pypdf` étaient absents. Ce
  résultat n'a pas été utilisé comme mesure. Après le garde-fou, la commande
  sort explicitement en code 1 avec `parseur PDF indisponible` ; elle ne masque
  plus le corpus comme une extraction vide.
- La règle de non-OCR sur texte natif suffisant n'a pas pu être évaluée par le
  pipeline sur ces PDF. Aucun OCR systématique n'a été effectué.

## 6. Extraction structurée et jeu de référence

- Documents passés dans `seamtech_search.fiches.extraction` : **0**.
- Gabarits détectés, champs extraits, valeurs métier, confiances, anomalies,
  erreurs OCR changeant une valeur, pages/zones et taux de complétude : **non
  mesurés** (aucun parseur PDF installé, dépendance Pydantic absente).
- La détection bas niveau ne permet pas d'inférer référence, client, bateau,
  voile, dimensions, matériaux, galons ou jonctions ; aucune valeur n'est
  affirmée dans ce rapport.
- Jeu de référence préparé dans
  `docs/verite_terrain/JEU_REFERENCE_REEL_2026-09-28.md` : 7 codes anonymisés,
  un candidat par dossier, tous `A_VALIDER`. 0 validation humaine, 0 correction,
  0 rejet et 0 fiche utilisable en vérité terrain. Temps par fiche/champ et taux
  de passage/correction/rejet/absence : non mesurés.
- `docs/templates/SUIVI_VALIDATION_REFERENCE.csv` conserve exactement 15
  colonnes, encodage UTF-8 avec BOM (`utf-8-sig`) et séparateur `;`. Il ne
  contient aucune ligne de résultat inventée ; les chemins internes détaillés
  sont gardés hors dépôt.

## 7. Recette propre, import et sauvegarde/restauration

Non exécutés sur ce corpus : PostgreSQL neuf, Redis neuf, stockage objet de
recette, configuration/secrets séparés, montage lecture seule, import
idempotent, contrôle de doublons/pièces jointes/object_key, reprise,
quarantaine, recherche textuelle/dimensionnelle, suggestions, ouverture et
téléchargement PDF, URL présignée réellement téléchargeable, correction
attribuée, panneau de validation, assistant sourcé, sauvegarde, destruction et
restauration de la base, contrôles de comptes/fiches/documents/recherche après
restauration. Docker, PostgreSQL, Redis, `psycopg2`, `boto3`, `redis` et les
paquets runtime Python sont absents de cet environnement. Les tests CI/fixtures
ne remplacent pas cette recette réelle.

## 8. Stockage et risques inchangés

Aucun changement de backend n'a été effectué. Le backend demeure celui du
compose/configuration du dépôt et des validations CI existantes : MinIO.
L'audit existant (`docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md`) signale que
MinIO est archivé, qu'aucun second fournisseur S3 n'a été réellement éprouvé,
que les identifiants utilisés sont les identifiants root, et que TLS/chiffrement
au repos/rotation des secrets/sauvegardes hors site et réversibilité exigent une
décision et une qualification opérationnelle. Les URL présignées D-1 sont
couvertes par tests CI, mais un téléchargement réel sur cette recette n'a pas
eu lieu. Le backend n'est pas remplacé silencieusement ; le choix cible reste au
commanditaire.

## 9. Niveau de qualification

| Niveau | Conclusion sur cette exécution |
|---|---|
| A — démontré sur les ZIP fournis | Inventaire ZIP, contrôles de sûreté, extraction hors dépôt, SHA-256/mtimes et intégrité source : oui. OCR applicatif, extraction, import, recherche, validation, téléchargement et restauration réels : non. |
| B — CI/fixtures | Le run post-merge de la base a réussi sur 9/9 jobs ; les tests CI n'opèrent pas sur ces ZIP et ce run précède les modifications locales de ce lot. |
| C — non démontré | Archive complète, volume/diversité inconnus, tous gabarits, 100 000 dossiers, réentraînement à 300–500 fiches validées, poste Windows réel. |

**Décision : NON VALIDABLE.** Les preuves nécessaires pour « PRODUCT-READY POUR
LE CORPUS FOURNI » ne sont pas disponibles. La sélection réelle est amorcée et
l'intégrité des ZIP est établie, mais la qualification opérationnelle ne peut
être déduite de l'inventaire de fichiers ni des fixtures CI.

## 10. Commandes et preuves exécutées

Commandes principales (chemins de travail privés omis des artefacts partagés) :

```bash
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git status --short --branch
gh pr list --state merged --limit 5
gh run list --branch main --limit 15
python /tmp/seamtech-rc-20260928/inventory_zip.py . /tmp/seamtech-rc-20260928/zip-manifest.json
python /tmp/seamtech-rc-20260928/safe_extract.py /home/user/SEAMTECH-search /tmp/seamtech-rc-20260928
python scripts/preflight_archive.py preflight --source /tmp/seamtech-rc-20260928/extracted --travail /tmp/seamtech-rc-20260928/work --sortie /tmp/seamtech-rc-20260928/preflight --min-libre-o 0 --echantillon-pdfs 25 --masquer-chemins --json
python -m seamtech_search.ocr.cli inventaire --dossier /tmp/seamtech-rc-20260928/extracted --json
python scripts/inventaire_archive.py /tmp/seamtech-rc-20260928/extracted --sortie /tmp/seamtech-rc-20260928/inventory-out
PYTHONPYCACHEPREFIX=/tmp/seamtech-rc-20260928/pycache python -m compileall -q seamtech_search scripts tests
python -m pytest --version
```

Résultats locaux : extraction sûre terminée ; préflight code 6 ; inventaire OCR
après garde-fou code 1 (dépendance manquante, attendu et explicite) ; inventaire
structuré code 1 (`pydantic` absent) ; `compileall` code 0 ; vérification manuelle
du chemin du garde-fou code `PASS` ; CSV contractuel vérifié ; `pytest --version`
échoue car `pytest` est absent. **0 test pytest local exécuté** ; 1 test de
régression a été ajouté mais n'a pas pu être exécuté ici. Aucun résultat local
n'est compté comme test réussi.

Preuves privées de cette exécution : `zip-manifest.json`,
`extraction-manifest.json`, `files-private.json`, `tree-before.json`,
`corpus-summary.json`, `preflight.stdout.json`, sorties et erreurs OCR/inventaire,
dans `/tmp/seamtech-rc-20260928`. Ces fichiers contiennent des chemins internes
ou des noms de documents : ne pas les publier sans examen de confidentialité.

## 11. Erreurs restantes et prochaines étapes

1. Rejouer le préflight sur un poste recette isolé avec `pdfplumber`/`pypdf`,
   Tesseract avec `fra`, et Poppler installés depuis des sources approuvées par
   l'opérateur ; ne pas télécharger dans le cadre de cette livraison.
2. Exécuter un pilote borné, vérifier la liste de pages et la non-OCR des pages
   natives, puis terminer l'OCR du seul corpus fourni avec rapports et hashes.
3. Exécuter l'extraction structurée, classifier gabarits/champs et corriger le
   suivi CSV sans présumer de valeurs ; comparer les 7 candidats aux PDF.
4. Obtenir la validation humaine et le contrôle secondaire selon le protocole.
   Tant qu'elle n'a pas eu lieu, conserver `A_VALIDER` et ne pas calibrer.
5. Monter une recette propre PostgreSQL/Redis/stockage objet, importer les ZIP
   idempotemment, démontrer recherche/lecture/téléchargement, puis sauvegarder
   et restaurer sur une base neuve.
6. Attendre le résultat terminal de la CI post-merge ; résoudre toute régression
   introduite par le garde-fou OCR et le test ajouté.
7. Demander une décision explicite sur MinIO/second fournisseur, TLS, chiffrement,
   identifiants applicatifs, rotation et stratégie de sauvegarde avant une
   exposition de production.
8. Pour tout futur ZIP, recommencer l'inventaire sécurisé et recalculer les
   totaux ; ne jamais extrapoler les mesures présentes à une archive complète
   ou à une échelle supérieure.
