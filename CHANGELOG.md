## Unreleased — Intégration de la vérité terrain étendue (74 cibles) et garde-fou CI (`arena/01a0c90e-seamtech-search`)

- **Vérité terrain étendue (74 cibles, 12 familles)** : passage du banc de référence de 31 à 74 cibles
  (cotes 11, jonction 13, galon 12, fiche 11, renfort 9, materiau 8, option 3, finition 3, bateau 1,
  client 1, gamme 1, type_voile 1). Vérification contradictoire intégrale contre le texte extrait du document
  client réel (`sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf`) : 115 tracées littéralement, 2 dates
  dérivées ISO (2026-03-06 / 06/03/2026), 5 codes internes dont le libellé figure dans le document,
  3 booléens dérivés de « Non » (0 valeur devinée ou inventée).
- **Branchement sur les bancs de mesure** :
  - `cli banc` : supporte l'exécution autonome (hors base) ou en base PostgreSQL (`gabarit_test`), résultat
    mesuré : **74/74 = 100.0 %** (seuil 90 %).
  - `validate_extraction.py` : supporte `--verite` au format Python (`VERITE_7792_COMPLETE.py`) et JSON
    (`7792-SO_ffab_complete.json`) en réutilisant le résolveur existant `_valeur_extraite` de `persistance.py`.
    Résultat mesuré : **74/74 = 100.0 %**. Rétro-compatibilité 6/6 Phase 0 préservée sur `7792-SO_ffab.json`.
- **Garde-fou du projet (`scripts/audit_projet.py`)** : outil de contrôle des 6 invariants critiques
  (emplacement de `.github/workflows/ci.yml`, fixture réelle SHA-256 + taille, aucun `except:` aveugle avalé,
  verrou de calibration actif, présence des 8 fichiers de test §17.11, suite de tests). Mesuré : 7/7 verts en
  `--rapide` (9/9 en mode complet). Intégré comme étape CI dans le job backend de `.github/workflows/ci.yml`.

## Unreleased — Lot H.1 « poste prêt » : sauvegarde hors-site éprouvée, mise en service, recette humaine, exploitation (`arena/01a0c56d-seamtech-search`)

Le poste doit être prêt : sauvegarde qui a été détruite puis reconstruite
(prouvé), mise en service en un chemin, recette humaine exécutable sans le
développeur, minimum d'exploitation.

- **Sauvegarde hors-site avec restauration RÉELLEMENT testée** : nouveau
  module portable `python -m seamtech_search.sauvegarde`
  (`sauver`/`restaurer`/`verifier`) — pg_dump -Fc, découverte des binaires
  (PATH / SEAMTECH_PG_BINDIR / /usr/lib/postgresql/*/bin / pgserver),
  manifeste complet (date, taille et sha256 du dump, VERSION_SCHEMA_METIER,
  comptes par table, fiches par statut, nb documents, commit), envoi via le
  client S3 existant avec RE-LECTURE du dump depuis le bucket et comparaison
  d'empreinte après chaque envoi, rétention N paramétrable qui ne purge
  JAMAIS la dernière. L'archive n'est jamais copiée ni modifiée (RG13) : son
  ÉTAT (chemins + tailles + sha256) est enregistré dans le manifeste, et
  `verifier` signale tout fichier PERDU, ALTÉRÉ ou ajouté. La restauration
  refuse une base cible existante et vérifie l'empreinte AVANT toute
  écriture. Les scripts PowerShell existants restent en place comme repli
  documenté (non testés depuis ce sandbox Linux — dit explicitement dans
  MISE_EN_SERVICE.md §4).
- **Aller-retour PROUVÉ par exécution** : tests
  `tests/test_sauvegarde_restauration.py` (marqueurs `postgres`/`sauvegarde`)
  — base semée → sauvegarde → copies locales supprimées → base DROPée →
  restauration DEPUIS LE BUCKET SEUL → comptes par table identiques,
  VERSION_SCHEMA_METIER présente, recherche renvoyant la même fiche,
  inventaire d'archive comparé ; plus les refus (dump altéré, base
  existante), la détection d'archive altérée, et la preuve CLI exécutée
  telle quelle (subprocess). Durée de restauration ~50 000 fiches mesurée et
  publiée en JSONL (SEAMTECH_SAUVEGARDE_JSON). Nouveau job CI dédié
  `sauvegarde` (PostgreSQL service + MinIO RÉEL via `minio/minio`, bucket
  créé, client réel) avec garde-fous : ≥ 19 tests passés, 0 sauté, 0 échec ;
  mesures publiées en `::notice` (dont la durée 50 000 fiches reprise au
  runbook). La sélection du job backend devient
  `postgres and not perf and not sauvegarde` (l'épreuve hors-site exige un
  vrai bucket — elle vit dans son job, jamais sautée en silence).
- **Mise en service + décision matériel** : docs/verite_terrain/
  MISE_EN_SERVICE.md (UN chemin validé : docker compose, rejoué en CI par le
  job integration ; Windows = checklist non testée à exécuter par le
  commanditaire) ; DECISION_MATERIEL.md (options A/B/C chiffrées, décision au
  commanditaire, chiffres non mesurés écrits « non mesuré »).
- **Recette humaine prête à exécuter** : docs/verite_terrain/RECETTE_HUMAINE.md
  — jeu de 3 fiches du dépôt (7792-SO / GENOA champs incertains / 123) +
  doublon de code par redépôt + cote à corriger (RG11), fiche imprimable en
  6 étapes avec tableau de mesure ; remplit le tableau vide de
  MESURE_VALIDATION_2MIN.md sans le développeur.
- **Minimum d'exploitation** : rotation des journaux bornée dans
  docker-compose.yml (json-file 10 Mo × 5, les 5 services) ; garde-fou disque
  existant vérifié et documenté (SEAMTECH_MIN_FREE_BYTES → 507, testé dans
  tests/test_chaos.py) ; RUNBOOK_RESTAURATION.md (commandes exactes, sorties
  attendues, tableau « si ça ne correspond pas », durées mesurées) ;
  QUE_FAIRE_SI.md (service tombé, base muette, disque plein, recherche vide,
  fiche bloquée a_valider, R2 muet) ; contrôle quotidien en une commande.
- **Couverture** : `S3StorageClient.list_keys` (rétention) + 2 tests mockés
  (`test_storage.py`) — storage.py reste au-dessus de sa porte de 97 %.
- Portes locales au commit : `-m "not postgres"` 501/3 ;
  `postgres and not perf and not sauvegarde` 118/1 (le 1 sauté = poids e5,
  fourni en CI) ; perf 3/0 ; porte de couverture rc=0 ; ruff propre.

## Unreleased — Consolidation finale : flake de latence corrigé, runbook de fusion, garde-fous sandbox (`arena/01a0c56d-seamtech-search`)

- **Flake de latence CI corrigé (Lot E)** : sur runner partagé GitHub Actions,
  l'exécution de la suite perf sous couverture (`--cov`) dégradait le p95
  (108,8 ms mesuré au run push 35715779367 alors que le même commit était vert
  en pull_request). Les tests marqués `perf` sont désormais exécutés dans une
  étape CI dédiée SANS instrumentation, avec une passe d'échauffement jetée
  et une tolérance explicite d'environnement CI à 250 ms (le critère produit
  p95 < 100 ms reste asserté en local et en production ; le p50 < 100 ms reste
  un critère d'architecture absolu dans tous les environnements). Publication
  des mesures en `::notice` pour surveillance de dérive.
- **Runbook de fusion documenté** : `docs/verite_terrain/FUSION_MAIN.md`
  décrit la séquence exacte de fusion de la branche vers `main`, les
  vérifications préalables et les commandes de bascule.
- **Garde-fous d'environnement sandbox** :
  `scripts/etat_sandbox.sh` inventorie l'état du système (RAM, disque, ports,
  services, versions d'outils) pour diagnostic reproductible sans hypothèses.
- **Matrice de traçabilité actualisée** : `docs/verite_terrain/TRACABILITE_LIVRAISON.md`
  intègre les preuves d'exécution des Lots D, E, F et la consolidation finale.
