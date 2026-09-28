# Reprise — flux réel sur les 7 ZIP (2026-09-28)

> Ce rapport complète et remplace pour les mesures de parseur/pipeline le premier
> `RAPPORT_FINAL_CORPUS_REEL_2026-09-28.md`. Le travail concerne exclusivement
> les ZIP disponibles ; il ne prétend pas couvrir une archive complète non
> fournie.

## Conclusion

# NON VALIDABLE

Des segments réels ont maintenant été exécutés : parse des PDF, extraction
structurée sur les 7 candidats, upload/scan/confirmation via l'API locale,
recherche, ouverture et téléchargement local des sources, puis sauvegarde et
restauration d'une base SQLite de recette contenant 7 imports réels. Mais
l'import métier PostgreSQL, le stockage objet, les URL présignées, la
validation humaine, le Tesseract réel et le flux navigateur n'ont pas été
prouvés. En particulier, 0/7 fiches a été signalée automatiquement comme
technique par le parcours upload, 6/7 n'ont pas de gabarit reconnu par le
parseur de fiches et les 7 imports API sont restés `needs_review`.

La conclusion ne signifie pas que le corpus manque : **7/7 ZIP ont été
ré-examinés, leurs empreintes concordent avec le rapport initial, et les
fichiers PDF natifs ont été réellement analysés.** Le résultat n'autorise ni
mise en production, ni calibration, ni affirmation de couverture complète.

## 1. Départ, dépendances et environnement

- SHA de départ : `cbbe405b31107ea36032da5a2c44810475b40fce` ; branche
  `arena/01a0e94b-seamtech-search` ; `origin/main` était au même SHA ; PR #29
  était la dernière PR fusionnée ; aucune PR n'était ouverte depuis cette
  branche.
- Modifications locales de reprise inspectées : garde-fou dans
  `seamtech_search/ocr/inventaire.py` et test associé. Le garde-fou est conservé :
  il fait échouer explicitement l'inventaire OCR si `pdfplumber` **et** `pypdf`
  sont absents, au lieu de déclarer silencieusement des PDF sans texte. Le test
  correspondant passe dans la suite locale.
- Environnement temporaire hors dépôt :
  `/tmp/seamtech-rc-20260928/venv`, Python **3.11.2**. Installation réussie de
  `requirements-dev.txt` (qui inclut `requirements.txt`) : pytest **9.1.1**,
  pdfplumber **0.11.10**, pypdf **6.19.0**, FastAPI **0.141.1**, httpx
  **0.28.1**, Ruff **0.16.9** et dépendances déclarées. `requirements-local.txt`
  est un alias vers `requirements-dev.txt`, donc aucun troisième pin distinct
  n'était nécessaire. Les paquets ont été installés avant les tests métier.
- Système : `tesseract`, langue `fra`, `pdftoppm`, Docker, Docker Compose,
  PostgreSQL et Redis ne sont pas disponibles ; aucun socket Docker ni service
  local sur les ports 5432/5433/6379/9000. `apt-get update/install` n'a pas pu
  joindre les miroirs Debian, donc Tesseract/Poppler n'ont pas été installés.
  Aucun binaire OCR factice n'a été utilisé pour les mesures réelles.
- Les essais applicatifs ont utilisé `TestClient` ASGI en mémoire, sans service
  externe. Pas de valeurs métier, secrets, ni contenu PDF dans les commentaires
  GitHub.

## 2. ZIP réinventoriés et extraction fraîche

Inventaire refait depuis le dépôt — pas à partir de l'ancienne extraction — puis
expansion récursive dans `/tmp/seamtech-reprise-20260928`, hors dépôt. Les 7 ZIP
internes ont aussi été testés puis extraits. Contrôles : chemins absolus,
`..`, liens symboliques, chiffrement, CRC, limites de 100 000 entrées/2 Gio et
vérification du répertoire cible avant écriture. Les archives ont passé ces
contrôles.

| ZIP utilisé | SHA-256 revalidé | Octets |
|---|---|---:|
| `AQUILA 250216AJA-20260928T182324Z-1-001.zip` | `fd1fca0930494328687ecf5d6905af139c24f93a8b9e2e51b2f6075772446be6` | 573860 |
| `ATTALIA 250121JA-20260928T182325Z-1-001.zip` | `a71ec77d6d5abf3c55f5ca8aa5c79d3347d3b3765b91f54950065b9c2161d699` | 594852 |
| `BAVARIA 32 - 250604JA-20260928T182326Z-1-001.zip` | `a9ca298cae78ccbacfaab69260cede299062ef0d20e0a7fba62c3539ac3a538f` | 745910 |
| `BAVARIA 34 - 250323JA-20260928T182327Z-1-001.zip` | `483f89716b953734315b7bbb4ba81050dc24b33a162e2a2623604eee268b8f6f` | 618247 |
| `DAMIEN 4 - 250821JA-20260928T182330Z-1-001.zip` | `2f1ecb0205b817c3dbdb324bf444aad9fb5ddce1d3f415859698fbb21fb428bf` | 846621 |
| `DEHLER 39 - 250329AJA-20260928T182330Z-1-001.zip` | `b3171d070f6b332366263626a665e7f7985bb8406062c2957895401850df6a06` | 919707 |
| `GIB SEA 284 - 250328AJA-20260928T182331Z-1-001.zip` | `103359b561ef32f5a8ce04b373864c26a0b496156efc1f728e75f0c0911b4aa3` | 581169 |
| **Total** | **7 ZIP ; empreintes égales au relevé initial et aux blobs Git** | **4880366** |

- Expansion récursive : 14 archives traitées (7 ZIP du dépôt + 7 ZIP internes),
  13 927 768 octets décompressés cumulés. L'extraction fraîche contient 815
  fichiers au total : 808 entrées listées comme fichiers de contenu et 7
  conteneurs ZIP internes ; les chemins et empreintes détaillés restent hors
  Git.
- Avant/après pipeline/API : comparaison stricte du chemin relatif, taille,
  `mtime_ns` et SHA-256 : **815/815 inchangés**. SHA-256 et taille des ZIP
  originaux : **7/7 inchangés**. Aucun résultat OCR ni rapport n'a été écrit
  dans la source ; les extractions, rapports et bases de recette sont hors dépôt.

## 3. Parse PDF réel et inventaire applicatif

Commandes d'exécution principales :

```bash
python scripts/inventaire_archive.py /tmp/seamtech-reprise-20260928/extracted \
  --sortie /tmp/seamtech-reprise-20260928/archive-inventory --limite-empreinte 100 --silencieux
python -m seamtech_search.ocr.cli inventaire \
  --dossier /tmp/seamtech-reprise-20260928/extracted --json
```

Mesures issues des parseurs/installations réels, et non du diagnostic regex
précédent :

- étage 1 : 815 fichiers, 21 PDF, 13 927 768 octets ; doublons : 387 groupes,
  387 copies redondantes (5 288 280 octets rapportés par l'inventaire applicatif).
- 21/21 fichiers PDF ont du texte natif ; **0 PDF scanné probable** et
  **0 erreur d'extraction PDF** à l'inventaire. Les 14 contenus uniques ont
  30 pages ; les copies portent le total physique à **53 pages**. Les 53 pages
  physiques sont au-dessus du seuil natif de 20 caractères. `pypdf` a aussi
  ouvert les 14 contenus uniques sans erreur de page ; histogramme : 7 PDF
  distincts à 1 page, 6 à 3 pages, 1 à 5 pages.
- 1 image `.jpg` séparée : 1 page probable à OCRiser. Elle n'est pas comptée
  comme PDF.
- Détection structurelle : 7 candidats de fiches, 6 familles de mise en page.
  Le classifieur textuel indépendant a classé les 21 PDF comme plans (0
  `technical_pdf`) ; les 7 candidats structurels sont tous en désaccord avec
  cette classification. C'est une réserve mesurée, pas un motif de forcer le
  classement en production.
- Aucun texte métier ou nom interne de PDF n'est reproduit ici. Les fichiers
  `inventaire.json`, `inventaire_fichiers.csv` et `inventaire_doublons.csv`
  détaillés sont dans le répertoire privé de reprise.

## 4. Pilote OCR réel et passage complet

Tesseract réel était absent ; aucun moteur/langue ne peut donc être crédité.
Le budget a été fixé à 30 minutes. Le pilote utilisait 7 PDF réels (un candidat
par ZIP) et l'image réelle en diagnostic, via des liens symboliques dans le
répertoire privé.

| Mesure | Pilote réel | Corpus disponible, passage complet |
|---|---:|---:|
| Fichiers examinés | 8 | 22 (21 PDF + 1 image) |
| Fichiers traités / nouveaux | 8 | 15 traités, 7 doublons exacts déjà vus ignorés |
| Pages natives ignorées | 7 | 30 pages uniques |
| Pages OCRisées | 0 | 0 |
| Erreurs | 1 (image, Tesseract absent) | 1 (image, Tesseract absent) |
| Durée OCR rapportée | 0,574 s | 2,548 s |
| Débit OCR | 0 page/min | 0 page/min |
| Texte OCR produit | 0 octet | 0 octet |
| Langue/résolution demandées | `fra` / 300 dpi | `fra` / 300 dpi |
| Moteur/version/confiance | aucun/aucune | aucun/aucune |

Résultat : les 53 pages PDF natives ne sont pas OCRisées, conformément à la
règle par étages. La tentative sur l'image échoue faute de Tesseract. Ces runs
prouvent le chemin natif réel et le garde-fou anti-OCR inutile ; ils ne
constituent **pas** une mesure OCR réelle de la page scannée. `tesseract -l fra`
et `pdftoppm` restent à installer depuis un dépôt système approuvé avant de
reprendre cette page.

## 5. Extraction structurée sur les 7 candidats

Le parseur existant `seamtech_search.fiches.cli extraire` / `extraire_avec_filet`
a réellement été exécuté pour REF-001…REF-007. Résumé, sans valeurs métier :

- 7 analyses tentées ; **1 gabarit détecté** (`FICHE_GENOIS_V1`), **6 sans
  gabarit**, envoyées au filet de reprise.
- 8 enregistrements `ChampExtrait` : 7 valeurs brutes non vides, 8 valeurs
  normalisées ; 6 champs avec zone, 2 sans zone.
- 6 anomalies automatiques ; confiance des 8 enregistrements : min 0,60,
  moyenne 0,8325, médiane 0,945, max 0,99. Ces scores ne sont pas un taux de
  justesse : aucune vérité humaine n'existe pour comparer.
- Clés produites : `fiche.bateau`, `fiche.client`, `fiche.designation`,
  `cotes.finie.sf_m`, `cotes.finie.spa_m2`, `libre.commande`,
  `libre.6_45_np_surface`, `libre.7_45_np_surface`. Pas de champ de fiche
  reconnu dans les groupes matières, galons ou jonctions. Cette absence de
  proposition ne prouve pas l'absence du contenu dans les documents.
- Les extractions détaillées (valeurs brutes/normalisées, PDF, chemins, zones,
  anomalies et empreintes) restent sous
  `/tmp/seamtech-reprise-20260928/structured-real-private.json` et les rapports
  CLI `.txt`; aucun de ces fichiers n'est commité.

Le rapport de revue des candidats est
`docs/verite_terrain/JEU_REFERENCE_REEL_2026-09-28.md`. Les 7 codes restent
`A_VALIDER`. Il n'y a eu aucune comparaison humaine, correction, validation
secondaire ni calibration.

## 6. API locale : upload, confirmation, recherche et ouverture

Un essai en environnement propre SQLite a exercé le parcours API existant avec
les 7 PDF sources (contenu identique SHA-256), sans socket réseau ni service
externe : `TestClient(create_app(AppConfig(...)))`, base sous
`/tmp/seamtech-reprise-20260928/api-recipe/`, stockage S3 non configuré.

| Action réelle | Résultat |
|---|---:|
| `POST /imports/upload` avec chaque vrai PDF | 7/7 HTTP 200 |
| Candidats remontés par le scan upload | 7/7 ; 0 classé automatiquement `is_technical` |
| `POST /imports/confirm` avec sélection opérateur du candidat | 7/7 HTTP 200 |
| Statut d'import | 7/7 `needs_review` ; aucun statut validé automatiquement |
| Recherche locale `q=fabrication` après les imports | HTTP 200, 7 résultats |
| `POST /open` de la source réelle | 7/7 HTTP 200 ; contenu téléchargé SHA-256 identique à la source |
| Téléchargement `/artifacts/source_pdf` | 7/7 HTTP 200 ; SHA-256 identique |
| Téléchargement `/artifacts/report_pdf` | 7/7 HTTP 200 ; signature PDF vérifiée |
| `upload_status` stockage objet | 7/7 `not_configured` |

C'est une preuve réelle d'upload/scan/confirmation/recherche et de lecture PDF
par l'API legacy SQLite, avec sélection manuelle. Ce n'est **pas** le flux métier
PostgreSQL/pgvector complet, le drag-and-drop navigateur, un test S3, ni une
preuve d'URL présignée. Aucun validateur nominatif n'a été créé ; sans personne
qui lise les fiches, les imports restent `needs_review`.

## 7. Sauvegarde/restauration possible sur les données réelles

Comme Docker, PostgreSQL, Redis et S3 ne sont pas disponibles, le runbook
PostgreSQL hors-site ne pouvait pas être lancé. En complément partiel, une
sauvegarde/restauration locale SQLite de la base API contenant ces **7 imports
réels** a été effectuée avec `sqlite3.Connection.backup` hors dépôt :

- 12 tables dans la base legacy ; les comptes par table sont égaux avant/après,
  dont 7 `imports`, 7 `documents`, 42 `audit_log` et 17 lignes de
  `schema_migrations`.
- Après restauration dans un nouveau fichier SQLite : 7 imports lisibles,
  recherche `fabrication` toujours à 7 résultats et téléchargement du rapport
  PDF HTTP 200.
- Ce résultat **ne prouve pas** la sauvegarde/restauration PostgreSQL métier,
  l'intégrité d'objets S3, les `object_key`, ni la reprise d'une base métier
  `017_ocr_etage3`. Les scripts PowerShell existants n'ont pas été exécutés
  (`pwsh`/`powershell` absent).

## 8. Tests locaux et CI

| Vérification | Résultat |
|---|---|
| `ruff check .` | PASS — All checks passed |
| `git diff --check` | PASS |
| `python scripts/audit_projet.py --rapide` | PASS — 12/12 contrôles |
| `pytest -m 'not postgres and not s3 and not perf' -q` | **716 passed, 3 skipped, 207 deselected**, 55,27 s |
| OCR, workflow import, recherche fonds réels, URL présignées | **47 passed, 7 skipped** ; 3 skips Tesseract absent et 4 PostgreSQL URL absente |
| `tests/test_integration_docker.py` | 2 skipped : Docker absent |
| Tests migrations PostgreSQL | 9 skipped : `SEAMTECH_TEST_DATABASE_URL` absent |
| Sauvegarde/restauration PostgreSQL | 7 skipped : base/service absent |

Le run post-merge de la base `cbbe405b31107ea36032da5a2c44810475b40fce` a
réussi en CI sur ses 9 jobs. Il précède les changements locaux de ce lot ; la
CI du présent commit/PR doit être attendue après publication de cette branche.
Aucun seuil de test n'a été abaissé ; 207 tests dépendant des marqueurs
`postgres`/`s3`/`perf` n'ont pas été sélectionnés dans la suite locale.

## 9. RG13, RG14, stockage et limites

- RG13 : avant/après, 815/815 chemins, tailles, `mtime_ns` et SHA-256 égaux ;
  7/7 ZIP Git identiques. Les bases, rapports et extractions résident dans
  `/tmp`, non dans l'archive ni le dépôt.
- RG14 : extraction/parsing/OCR et essai API exécutés localement dans le
  processus ; aucune requête à un service externe pendant les tests métier.
  L'installation des paquets système a échoué faute de connectivité aux miroirs
  Debian, avant ces tests. La vérification RG14 par test est incluse dans la
  suite locale verte.
- Aucun backend de stockage n'a été changé. MinIO reste le backend du déploiement
  Docker du dépôt, mais il n'a pas pu être lancé ; audit stockage existant :
  MinIO archivé, identifiants root, risque TLS/chiffrement au repos et absence
  d'un second fournisseur S3 réellement testé. L'URL présignée D-1 n'a été
  exercée que par les tests unitaires/mockés, pas par ces imports réels.
- Non démontré : validation humaine, tous les gabarits, OCR sur le scan réel,
  objets S3 et présignatures, PostgreSQL neuf, restauration PostgreSQL/S3,
  application navigateur, compatibilité Windows, corpus plus grand, archive
  complète, 100 000 dossiers et calibration/réentraînement.

## 10. Livraison et reprise

Les valeurs métier détaillées, manifests à chemins internes, sorties OCR et
base SQLite sont hors dépôt dans `/tmp/seamtech-reprise-20260928`. Les fichiers
ZIP n'ont pas été modifiés et aucun PDF extrait n'est suivi par Git.

Suite nécessaire avant toute décision de mise en production :

1. Environnement approuvé avec Tesseract + `fra` + Poppler, puis OCR réel borné
   de l'image, vérification de qualité et rapport du moteur/confiance/durée.
2. Examiner par une personne les 7 fiches, corriger la classification « plan »
   et les 6 gabarits manquants sans automatiser de corrections, remplir le
   suivi privé par champ, effectuer le contrôle secondaire requis.
3. Recette propre Docker/PostgreSQL 16 pgvector/Redis/MinIO (ou backend décidé
   par le commanditaire), tests métier dans PostgreSQL, URL présignée réellement
   téléchargeable, puis sauvegarde/restauration PostgreSQL/S3.
4. N'importer au statut `valide` qu'après contrôle humain nominatif ; maintenir
   la calibration verrouillée tant que la vérité terrain ne satisfait pas le
   seuil prévu.
5. Répéter l'inventaire si de nouveaux ZIP arrivent ; ne pas extrapoler les
   présentes mesures aux archives futures.

**Périmètre de décision : `NON VALIDABLE` pour le corpus réel fourni**, malgré
les segments locaux réellement validés ci-dessus. L'échelle et la diversité
au-delà de ces 7 ZIP restent expressément non démontrées.
