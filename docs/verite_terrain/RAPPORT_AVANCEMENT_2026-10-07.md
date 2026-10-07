# Rapport d'avancement — 2026-10-07

**Ce qui a été fait, ce qui est prouvé, ce qui reste ouvert.**

| | |
|---|---|
| Branche de session | `arena/7da80c2f-seamtech-search` |
| Commit de tête | voir l'en-tête de `docs/verite_terrain/TRACABILITE_LIVRAISON.md`, section « Revue indépendante 2026-10-07 » (SHA exact + liens de runs) |
| Pull request | [#35](https://github.com/Ilyes-Neguir/SEAMTECH-search/pull/35) — ouverte, **non fusionnée** |
| Base de comparaison | `main` = `4e374f6` (« docs: record CI diagnoses and green validation ») |
| Volume de la passe | 42 fichiers, **+7 005 / −332** lignes depuis `4e374f6` |

> **Règle de lecture de ce rapport.** Chaque affirmation est marquée
> **PROUVÉ (local)**, **PROUVÉ (CI)**, ou **PENDING**. Les preuves brutes
> (commandes + sorties) sont dans `REVUE_INDEPENDANTE_2026-10-07.md` ; les
> écarts corrigés sont numérotés **E-1 → E-27** dans
> `ECARTS_ET_CORRECTIFS_2026-10-06.md`. Aucune preuve qui n'aurait pu venir que
> de la CI n'est présentée ici comme acquise avant d'avoir lu le job
> correspondant.

---

## 1. Résumé

La mission demandait dix chantiers. Cette passe a livré le **socle de
durabilité** (file Redis, worker séparé, reprise après crash, lettre morte,
idempotence), la **vérification d'intégrité du stockage objet** (preuve par
relecture des octets, purge interdite sans preuve), la **preuve de partage
web/worker** (fichiers, quarantaine, modèles, permissions), et surtout des
**tests d'exécution réelle** : vrais processus worker tués au SIGKILL, vrai
serveur Redis redémarré, vrai PostgreSQL, vraie pile Compose en CI.

**Cinq défauts réels ont été trouvés et corrigés**, dont trois qui rendaient la
CI rouge sans rapport avec le code applicatif :

| # | Défaut | Effet | Corrigé |
|---|---|---|---|
| E-22 | Le job CI `integration` ne démarrait jamais le conteneur `worker` | les preuves multi-conteneurs ne s'exécutaient pas | nouveaux tests + inventaire CI |
| E-23 | `SEAMTECH_STORAGE_VERIFY_REREAD` mal lue (`KeyError`) | le conteneur `web` du compose documenté **ne démarrait plus** | `config.py` + variable déclarée |
| E-24 | `max_task_attempts` offrait une tentative de trop | 3 tentatives réglées ⇒ 4 réellement | garde corrigée |
| E-25 | `pytest.skip(allow_module_level=True)` compté comme « sauté » même quand la sélection `-m` a désélectionné le module | 2 jobs rouges pour des tests jamais demandés (`sauvegarde`, `backend`) | fixture autouse par test |
| E-26 | `./data` / `./logs` créés par Docker en root, application en `seamtech` | imports/rapports impossibles en conteneur (échec du job `integration`) | préparation + **preuve d'écriture** en CI, exigence documentée |
| E-27 | Verrou pnpm en retard : 2 avis HIGH (`sharp`, `source-map-js`) | jobs `frontend` et `securite-dependances` rouges | verrou régénéré, audit à 0 |
| E-28 | Téléchargements redirigés (302) vers l'endpoint S3 **interne** (`minio:9000`), non résolvable depuis un poste | rapport/original **intéléchargeables depuis un autre poste** | octets servis par l'API par défaut ; redirection seulement vers un endpoint public déclaré |
| E-29 | Les politiques MinIO étaient lues par `mc` depuis un chemin de l'**hôte** | provisionnement impossible (`recette-locale` rouge) | politiques copiées DANS le conteneur (`docker cp`) |
| E-30 | Le helper e2e supposait le poste déjà sur la file | 2 scénarios de concurrence rouges | le helper navigue lui-même et attend la révision armée |
| E-32 | La CI n'exposait pas `MINIO_ROOT_*` à l'**étape** de provisionnement | `sauvegarde` et `recette-corpus-reel` rouges avant tout test | variables au niveau de l'étape (jamais du job), tests de contrat CI |
| E-33 | Test e2e figé sur `champ-materiau`, champ inexistant (familles préfixées) | scénario de concurrence rouge | champ réel choisi dynamiquement, unique à l'écran |
| E-34 | Le tableau des champs gardait le brouillon local après « Recharger la fiche à jour » | l'opérateur relisait sa saisie périmée en croyant lire celle du collègue | brouillons oubliés (un champ après enregistrement, tous après rechargement) |
| E-35 | La recette écrivait la sauvegarde avec l'identité **applicative** (et retombait sur `minioadmin` en silence) | `recette-corpus-reel` rouge ; faux ALLOW masqué par la racine | identité choisie par bucket, **aucun** repli, échec clair |

État de la CI au moment de la rédaction : voir §5 (les runs du commit de tête
sont les seuls qui font foi). **Dernier état lu** : `37618670336` sur `3e13c7f`
— `sauvegarde` redevenue VERTE (correctif E-32), `e2e` et `recette-corpus-reel`
rouges pour les causes E-34 et E-35, **corrigées depuis** ; la CI de clôture est
relancée par le commit qui porte ces corrections.

**Verdicts séparés demandés par la mission :**

* **Tests développeur** : large couverture et exécution réelle, y compris
  multi-processus ; ~1 100 tests exécutables localement, suites sans service /
  PostgreSQL / file durable vertes. **Atteint, à l'exception des preuves qui
  exigent Docker** (§6).
* **Pilote atelier contrôlé** : **NON atteint** — exige un drill de restauration
  humain indépendant, une destination de sauvegarde de production, et la
  validation humaine du corpus réel (0 fiche sur 7 validée à ce jour).
* **Production** : **NON revendiquée** — voir §6, liste exhaustive.

---

## 2. Chantier par chantier (les 10 workstreams de la mission)

| # | Chantier | État dans cette passe |
|---|---|---|
| 1 | Relecture baseline, écarts doc/code, puis implémentation | **fait** — registre `ECARTS_ET_CORRECTIFS_2026-10-06.md` (E-1 → E-27), conversions sous `seamtech_search/` |
| 2 | Durabilité Redis (file, worker séparé, états, reprise, lettre morte, idempotence) | **fait et prouvé** — `redis_store.py`, `jobs.py`, `worker.py`, `worker_service.py` ; 37 tests réels + 5 tests multi-processus |
| 3 | S3/MinIO (fournisseur unique, intégrité, clés, quarantaine, moindre privilège) | **code + tests écrits** ; exécution contre un MinIO vivant : **CI uniquement** ; moindre privilège **PENDING** |
| 4 | Glisser-déposer / comptabilité des imports | **fait** — comptage par fichier, motifs d'échec par fichier, refus 507, anti-traversée, reprise de lot |
| 5 | Recherche (indexation, visibilité après import, accents, dimensions, filtres) | **vérifié sur socle** — suites SQLite + PostgreSQL ; pertinence sur archives réelles **PENDING** (exige le jeu de requêtes étiqueté par un humain) |
| 6 | Protection du flux de fabrication (validé / non validé / révisé) | **vérifié sur socle** — états de fiche, badge « Non vérifiée », journal de validation, verrous optimistes ; acceptation atelier **PENDING** |
| 7 | Sauvegarde / restauration | **automatisée** (job CI `sauvegarde`, 19+ tests, dont 50 000 fiches) ; drill humain indépendant + destination de production **PENDING** |
| 8 | Déploiement local (compose durci, non-root, secrets, liveness) | **fait** — 6 services, conteneurs non-root, endpoints privés, `/health` complet ; preuve « réseau externe bloqué » **PENDING** (à exécuter sur le serveur) |
| 9 | Scénario trois postes au navigateur | **spécifié + exécuté en CI** (`e2e`, Playwright) ; non exécutable dans ce bac à sable (navigateurs non installables) |
| 10 | Livrables (correctifs, tests, docs, rapport, portes restantes) | **ce document** + `REVUE_INDEPENDANTE_2026-10-07.md` + traçabilité |

### 2.1 Durabilité des imports (chantier 2) — détail

* **Worker séparé** : service `worker` du compose (`python -m
  seamtech_search.worker_service`), healthcheck `--verifier`, l'API web ne
  traite plus les imports en arrière-plan
  (`SEAMTECH_WEB_WORKER_ENABLED=false`).
* **États explicites** : `pending → running → completed | failed |
  upload_incomplete | needs_review | cancelled`, plus **lettre morte**
  (`deadletter_task`, `replay_deadletters`) — `jobs.py` l.420‑421.
* **Revendication atomique** : scripts Lua dans `redis_store.py`
  (`revendiquer_tache`, `rafraichir_revendications`,
  `revendication_appartient_a`, `liberer_revendication`) — un worker ne peut
  pas voler la tâche d'un autre, et un worker « zombie » ne peut ni libérer ni
  écrire l'état terminal après reprise (test dédié).
* **TTL de revendication** (300 s) + battement de cœur (`_BattementDeCoeur`) :
  un worker tué laisse une revendication expirante, la reprise
  (`reprendre_taches_orphelines`, `reconcilier_file`) remet la tâche en file
  sans dupliquer documents ni rapports.
* **Tentatives bornées** : `SEAMTECH_MAX_TASK_ATTEMPTS` (défaut 3, **total**),
  backoff `2**n`, raison lisible en base (`failure_reason`), et le worker
  **n'écrase pas** un état terminal déjà écrit par la reprise (garde E-24).
* **Idempotence** : les points de crash testés sont « avant envoi », « après
  envoi objet, avant écriture en base », « après indexation sans marquage » —
  dans tous les cas, reprise sans doublon de fiche ni de document
  (`test_file_durable.py`, 23 tests).
* **Redis indisponible** : en production
  (`SEAMTECH_REQUIRE_DURABLE_QUEUE=true`), l'API répond **503 et ne crée pas de
  job** (jamais d'acceptation « durable » pour une tâche qui ne vit qu'en
  mémoire) ; hors production, la réponse porte explicitement
  `durability: "process_memory"`.
* **Annulation inter-processus** : drapeau Redis + `doit_continuer()` ; le
  worker vérifie l'annulation entre chaque lot.

Preuves multi-processus (`tests/test_relais_file_base.py`, 5 tests) : **vrais
sous-processus `worker_service --une-passe`**, base PostgreSQL jetable par test,
base Redis 1 vidée avant chaque test, SIGKILL en plein import, expiration de
revendication, refus de double revendication par deux workers simultanés.

### 2.2 Stockage objet et intégrité (chantier 3) — détail

Réponse à la question de la revue — *« le SHA-256 des métadonnées est-il validé
contre les octets stockés ? une vérification par la taille autorise-t-elle la
suppression locale ? »* :

* `S3StorageClient.verifier_integrite` renvoie un
  `ResultatVerification(integrite_prouvee, methode, detail)` avec quatre
  méthodes possibles : `relecture_sha256` (l'objet est **relu par l'API** et
  l'empreinte recalculée sur les octets reçus), `checksum_serveur` (empreinte
  calculée par le fournisseur sur l'**objet entier** : `ChecksumSHA256` **et**
  `ChecksumType == FULL_OBJECT`), `metadata_seule`, `taille_seule`.
* **Seules les deux premières prouvent l'intégrité.** `metadata_seule` et
  `taille_seule` sont explicitement **non preuves** : elles ne valident pas les
  octets stockés, et **interdisent la purge** de la copie locale
  (`integrite_prouvee=False`). Donc : **non, une vérification par la taille
  seule n'autorise pas la suppression locale** — c'est ce que verrouille
  `tests/test_verification_integrite.py` (10 tests).
* L'upload demande `ChecksumAlgorithm: SHA256` ; la purge n'a lieu qu'après
  `UploadBatch.all_verified()`.
* Le double de test `tests/s3_en_memoire.py` **conserve les vrais octets**, ce
  qui rend la corruption détectable : trois scénarios prouvés — objet tronqué
  avec métadonnée intacte, objet altéré **de même taille**, objet absent.
* Clés collision-safe (`artifact_object_key`, `first_free_key`), nom/chemin
  d'origine conservés en métadonnées, bucket privé, quarantaine sur upload
  incomplet.
* **Identités restreintes — FERMÉ par le correctif du défaut bloquant** (revue
  du 2026-10-07, constat n° 1) : le déploiement documenté ne donne **plus** le
  compte racine MinIO à `web`/`worker`. `docker-compose.yml` exige désormais
  une identité APPLICATIVE dédiée (`:?` : la composition refuse de démarrer si
  elle manque), `scripts/provisionner_stockage.sh` crée les buckets, le
  versioning et **deux identités distinctes** (application = son bucket
  seulement ; sauvegarde = le sien), et la sauvegarde utilise
  `SEAMTECH_BACKUP_ACCESS_KEY`/`SECRET_KEY` — jamais `MINIO_ROOT_*`. Aucun
  repli silencieux : une identité absente fait échouer clairement
  (`credentials_presentes()`, `/health` → `s3_credentials: absent|dedie|root_like`).
  Preuves : `tests/test_credentials_s3_restreintes.py` (**18 tests locaux + 4
  tests Docker ALLOW/DENY**) ; exécution contre MinIO RÉEL : **CI** (jobs
  `integration`, `sauvegarde`, `recette-corpus-reel`, `recette-locale`).
  Deux défauts de plus ont été trouvés **par la CI** dans cette famille et
  corrigés — la CI ne donnait pas au provisionnement les identifiants dont il a
  besoin (E-32, job `sauvegarde` **redevenu vert**), et la recette écrivait la
  sauvegarde avec l'identité applicative en s'appuyant, en silence, sur un repli
  `minioadmin` (E-35) : le moindre privilège ne se prouve que si les tests ne
  peuvent PAS retomber sur l'administrateur.
* **R2 — optionnel, hors périmètre local-first** : MinIO reste le fournisseur
  unique ; R2/AWS S3 n'est ni testé ni revendiqué, et ne sera traité que si le
  propriétaire le demande explicitement.
* **Exécution réelle contre MinIO : CI seulement** (pas de Docker dans la
  session d'implémentation) — et cette phrase reste vraie après le correctif.

### 2.3 Téléchargements, navigateur, conception d'interface

* Les téléchargements passent par l'API (`…/pieces/{id}/telecharger`,
  `GET /imports/{id}/artifacts/{artifact}`, `POST /open`) : les octets sont
  servis par le serveur, jamais par une redirection vers un nom d'hôte interne
  au conteneur ni vers `localhost` du navigateur. C'est ce qui les rend
  utilisables **depuis un autre poste de l'atelier** — et c'est l'écart E-28,
  corrigé dans cette passe : la redirection présignée par défaut pointait vers
  `minio:9000`, injoignable hors du réseau Docker. Si l'exploitant expose MinIO
  sur le réseau de l'atelier, il peut déclarer
  `SEAMTECH_S3_PUBLIC_ENDPOINT_URL` et retrouver la redirection (moins de charge
  serveur) ; sinon tout passe par l'API.
* L'aperçu et le téléchargement sont distingués (aperçu ≠ téléchargement) ;
  les originaux restent téléchargeables.
* **La conception UI de l'atelier n'a pas été modifiée** : les changements sont
  comportementaux (états, messages d'erreur, motifs par fichier, badges de
  validation) et non visuels.

### 2.4 Édition concurrente (chantier 2 de la revue) — verrou optimiste

Défaut corrigé : `corriger_champ` écrivait sans comparer l'état lu (« dernier
écrivain gagne »). Deux postes ouvrant la même fiche s'écrasaient donc
mutuellement, sans trace ni avertissement.

* migration `021_revision_fiche` : `fiche.revision INTEGER NOT NULL DEFAULT 1`
  (colonne seulement ; les 33 tables sont inchangées) ;
* `POST /fiches/{code}/corriger` accepte `revision` et arbitre par
  **compare-and-swap** (`UPDATE … WHERE revision = %s`) ; une correction fondée
  sur un état périmé reçoit **409 `conflit_revision`** avec la valeur du
  collègue, son auteur, sa date et la règle appliquée — et **aucune écriture**
  n'a lieu (un refus ne crée même pas la ligne `utilisateur` de l'auteur) ;
* les décisions (valider, rejeter, rouvrir, validation en lot) incrémentent
  aussi la révision : un poste resté ouvert ne peut plus écrire après une
  décision ;
* un client qui n'envoie **pas** de révision garde l'ancien comportement, mais
  l'absence de verrou est **journalisée** (WARNING « SANS révision fournie ») :
  elle n'est jamais présentée comme une protection ;
* l'écran Validation envoie la révision, affiche le conflit et propose
  « Recharger la fiche à jour ». Design conservé : les deux seuls ajouts sont
  des attributs **non visuels** (`data-fiche`, `data-revision`) qui rendent
  observable l'état dont dépend la justesse.

Preuves : `tests/test_revision_optimiste.py` — **6 tests, 6 passés**, sur
PostgreSQL réel et par HTTP réel, dont un enregistrement **simultané** de deux
clients (barrière de départ, deux threads) : exactement un 200 et un 409, la
base porte la valeur du gagnant, révision +1 exactement.
`frontend/e2e/concurrence.spec.ts` — 4 scénarios multi-navigateurs
(deux postes même fiche et reprise après conflit ; import pendant recherche +
téléchargement d'octets PDF réels ; session expirée sans faux succès ; refus
d'administrateur par le **backend**), exécutés par une étape CI dédiée dont le
garde-fou exige `4 passés, 0 sauté, 0 flaky`. **Statut de cette preuve : la
première exécution CI (run `37613980912`) a trouvé un défaut du TEST lui-même**
(E-30 : le poste B n'avait pas navigué vers la file), puis la deuxième
(run `37617039519`) un SECOND défaut de test (E-33 : nom de champ figé,
`champ-materiau` inexistant sur les fiches réelles), puis la troisième
(run `37618670336`) un défaut **du PRODUIT** (E-34 : le tableau des champs
conservait le brouillon de l'opérateur après « Recharger la fiche à jour » —
B relisait sa propre saisie périmée en croyant lire celle du collègue).
Après correction des trois : les trois scénarios voisins passent
(`3 passed`) et le scénario de conflit passe toutes ses assertions jusqu'à la
reprise — le statut final est consigné au §5. Aucun de ces scénarios n'est
revendiqué comme prouvé avant lecture du job correspondant.

### 2.5 Vérification hors ligne (chantier 3 de la revue) — exécutée, pas lue

`scripts/verifier_hors_ligne.py` :

* `--inventaire` : classe chaque capacité en REQUISE ou OPTIONNELLE et dit la
  conséquence exacte d'une absence (OCR étage 3 sans `tesseract -l fra`, rendu
  image sans `pdftoppm`, recherche vectorielle sans modèles e5, images
  conteneurs sans Docker) ; il vérifie en outre que les points de terminaison
  configurés (base, Redis, S3) sont loopback/privés ;
* `--executer` : installe un **garde réseau** qui fait échouer toute connexion
  Python hors réseau privé en **nommant** la cible, puis exécute sur la pile
  réelle : démarrage, connexion nominative, import d'un dossier réel
  (comptabilité des fichiers), rapport PDF, dépôt d'une fiche + pièces, levée
  **explicite** du verrou RG11 par le chemin documenté (`rouvrir` +
  `effacer_corrections`), correction + verrou optimiste, recherche (visibilité
  de la fiche déposée), aperçu + original PDF, `/open` ;
* `--autoriser-externe` : diagnostic (compte sans bloquer).

Premier passage réel : **échecs 0**, « aucune connexion hors réseau privé
tentée », capacités absentes dites (7). Limites énoncées par l'outil : il
intercepte les connexions **Python** (une bibliothèque C, comme libpq, appelle
`connect(2)` directement — d'où le contrôle d'adresses), et **il ne remplace
pas** l'acceptation en atelier (serveur réel, réseau débranché, postes réels).
`tests/test_verification_hors_ligne.py` (5 tests) prouve que le garde bloque
vraiment : adresse publique refusée et nommée, loopback accepté, nom non
déclaré refusé, mode diagnostic qui compte sans bloquer.

### 2.6 Exploitation

`/health` expose désormais, pour l'exploitant : file (profondeur, workers
vivants, lettres mortes), jobs par état + jobs actifs, état de sauvegarde,
espace disque, type d'identifiants S3 (`s3_credential_kind`) et statut du
versioning du bucket. `worker_service --verifier` rend un diagnostic exploitable
et **refuse de démarrer sans Redis**.

---

## 3. Preuves d'exécution locales (mesurées, rejouables)

Environnement de la session : Linux sans Docker, sans MinIO, sans navigateur
(CDN Playwright bloqué) ; PostgreSQL 16.2 + pgvector 0.8.0 + `unaccent`/`pg_trgm`
(contrib compilés à la main), Redis 7.2.5 et venv Python montés à la main — la
session a été **remise à zéro en cours de passe** et l'outillage a été
reconstruit (voir §4, note de reprise).

| Suite | Sélection | Résultat |
|---|---|---|
| **Suite COMPLÈTE** (dernière exécution, arbre de clôture) | aucune sélection | **1180 passés, 38 sautés, 0 échec** (264 s) |
| Sans service | `-m "not redis_queue and not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not sauvegarde"` | **825 passés, 3 sautés, 315 désélectionnés** |
| PostgreSQL réel | `-m "postgres and not perf and not sauvegarde and not redis_queue"` | **220 passés, 1 sauté** |
| File durable (Redis + PostgreSQL réels) | `-m redis_queue` | **37 passés, 0 sauté** |
| S3 vivant (MinIO) | `-m s3` | **0 passé, 29 sautés** — aucun service S3 ici (§6) |
| Compose 2 conteneurs | `tests/test_compose_partage_worker.py` | **1 sauté** — Docker absent (§6) |
| Contrat de sélection CI | `tests/test_selection_ci.py` | **14 passés** |
| Front (revérifié sur l'arbre de clôture) | `pnpm install --frozen-lockfile` / `pnpm exec tsc --noEmit` / `pnpm build` | **OK / OK / OK** |
| Vérification hors ligne | `python scripts/verifier_hors_ligne.py --executer` | **0 échec**, 7 capacités optionnelles dites, **aucune connexion externe tentée** |
| Lint + compilation | `ruff check .` / `python -m compileall seamtech_search` | **propre / OK** |
| Mise en forme `ruff format` (non exigée par la CI) | `ruff format --diff` sur les fichiers touchés, version d'avant vs version d'après | **aucun écart ajouté** (mêmes 5/14/12 hunks pré-existants) — `ruff format` n'est pas une porte du dépôt, `ruff check` l'est et passe |
| Contrat de provisionnement CI (nouveau) | `tests/test_credentials_s3_restreintes.py` | **18 passés, 4 sautés** (Docker) ; les 2 tests de recette sont ROUGES sur la version d'avant correctif |
| Porte de couverture | `pytest -m "not s3 and not perf" --cov=seamtech_search --cov-report=json` puis `python scripts/coverage_gate.py coverage.json` | **1174 passés, 5 sautés, 39 désélectionnés** (325 s) ; **porte franchie** : global **86,65 %** (plancher 85 %), `api` 90,5 %, `indexer` 95,3 %, `storage` 98,2 %, `worker` 96,5 %, `jobs` 96,5 % — aucun seuil abaissé |

**Comptabilité `skipped` vs `deselected`** (correction demandée par la revue, et
cause du défaut E-25) : `deselected` = tests écartés par la sélection `-m` ;
`skipped` = tests **demandés** qui se sont déclarés inaptes. Un saut au niveau
du **module** (`pytest.skip(allow_module_level=True)`) est compté comme « sauté »
**même lorsque `-m` a désélectionné le fichier** — démontré par une expérience
minimale reproduite à l'identique (`1 passed, 1 skipped, 1 deselected` alors que
le module fautif n'était pas sélectionné). C'est ce qui faisait échouer deux
garde-fous CI sur des tests jamais demandés.

Mécanisme exact, mesuré sur la même commande avant/après correction : les deux
modules fautifs (`tests/test_file_durable.py`, `tests/test_worker_process.py`)
comptent **26 tests**. Avant correction, l'abandon au niveau du module
interrompait la collecte : ces 26 tests n'apparaissaient **nulle part** (ni
exécutés, ni désélectionnés) et le module était reporté comme **2 sauts**.
Après correction (saut par test via fixture autouse) : **5 sauts → 3 sauts**,
**289 → 315 désélectionnés** (+26 = les 26 tests désormais collectés puis
écartés par `-m`). Après la passe « téléchargement » (§4, E-28), la même
sélection compte **825 passés** (8 tests ajoutés). Les 3 sauts restants sont les
sauts OCR légitimes (tesseract absent ici). Le saut unique restant dans la
sélection PostgreSQL correspond à un test conditionné à un service vivant
(bucket S3 / sauvegarde), exécuté par les jobs CI dédiés ; le poids de modèle e5
est, lui, téléchargé explicitement par la CI.

---

## 4. Défauts trouvés par ces tests (détail des plus coûteux)

* **E-25 — sauts fantômes.** Deux modules faisaient
  `pytest.skip(..., allow_module_level=True)`. Le garde-fou du job `sauvegarde`
  (« GARDE-FOU — every round-trip test ran, none skipped ») et celui du job
  `backend` (« 2 Postgres test(s) skipped even though
  SEAMTECH_TEST_DATABASE_URL is set ») échouaient donc à cause de tests jamais
  demandés. Corrigé par une fixture autouse qui ne saute que si le test est
  sélectionné. **Bénéfice mesurable : les garde-fous redeviennent
  informatifs** — un saut inattendu signalera désormais un vrai problème.
* **E-26 — permissions des volumes applicatifs.** `./data` et `./logs` sont
  ignorés par git : quand Docker crée ces dossiers pour un montage, ils
  appartiennent à `root:root`, alors que l'application tourne en `seamtech`.
  Toute écriture (brouillon téléversé, rapport généré, quarantaine) échoue
  alors par permission — **c'est une exigence de déploiement réelle**, pas un
  détail de CI. Le job `integration` prépare désormais ces dossiers pour
  l'utilisateur applicatif et **vérifie que l'écriture fonctionne** dans
  `/app/data` et `/app/logs` ; `docs/DEPLOYMENT.md` le documente pour le
  serveur d'atelier.
* **E-27 — avis de sécurité front.** `sharp < 0.35.5`
  (GHSA-wq5f-xc86-pv6w) et `source-map-js < 1.2.2` (GHSA-68fv-2mgg-jv7q) sont
  deux avis HIGH **corrigibles en amont** : le verrou pnpm était simplement en
  retard. Régénéré avec dépassements explicites ; `pnpm audit --prod` → 0.
* **Instrumentation d'échec.** Un échec du test Compose émet maintenant une
  **annotation GitHub** contenant la cause, l'état des deux conteneurs et leurs
  40 dernières lignes de journal. Cela répond à une contrainte constatée : les
  journaux bruts de job ne sont pas téléchargeables depuis tous les
  environnements (y compris celui de cette session), seules les annotations le
  sont — et c'est grâce à elles que la cause suivante a été lue en une minute
  au lieu d'être devinée.
* **Un défaut du test lui-même, trouvé par cette instrumentation.** La première
  exécution réelle du test Compose (run `37596092737`, job `integration`) a
  échoué avec `error: 'Selected PDF does not exist'`, alors que le
  téléversement, l'acceptation durable (`durability: durable`), la lecture du
  brouillon par le worker et le partage des volumes fonctionnaient (journal du
  web : `POST /imports/upload 200`, `POST /imports/confirm 202 Accepted`). Le
  test **supposait** que le fichier téléversé se trouverait à
  `<brouillon>/fiche.pdf` ; or un téléversement de dossier conserve
  l'arborescence envoyée par le navigateur
  (`<brouillon>/AFFAIRE-PARTAGE-CI/fiche.pdf`). **Le serveur a donc eu raison de
  refuser** — un chemin inexistant ne doit jamais produire un faux succès.
  Le test désigne maintenant le PDF par le chemin rendu par le **scan** (comme
  le fait l'interface) et vérifie, depuis le conteneur worker, que ce chemin
  exact existe et est lisible avant de confirmer l'import.

* **E-29 — le provisionnement lisait une politique restée sur l'hôte.**
  Job `recette-locale`, run `37613980912` :
  `mc: <ERROR> Unable to get policy: open /tmp/tmp.RFNzptK6JB/politique-app.json:
  no such file or directory`. Les fichiers de politique étaient écrits par
  `mktemp -d` sur l'HÔTE puis passés par leur chemin à `mc`, qui s'exécute
  **dans** le conteneur MinIO : il ne voit pas le `/tmp` de l'hôte. Les deux
  politiques sont désormais **copiées dans le conteneur** (`docker cp`, modes
  conteneur nommé et compose) puis lues là. Le défaut n'était pas visible en
  relisant le script : il fallait l'exécuter. Régression verrouillée par
  `test_script_de_provisionnement_lit_les_politiques_DANS_le_conteneur`.
* **E-30 — un test de concurrence qui n'ouvrait pas la fiche sur le poste B.**
  Première exécution du nouveau scénario multi-sessions (e2e, run
  `37613980912`) : `2 failed, 2 passed` — les deux échecs pointaient le helper
  `ouvrirFiche` (`li[data-code="0901-MM"]` introuvable). Cause : le helper
  supposait le poste déjà sur `/validation` ; le poste B venait de se connecter
  et restait sur l'accueil. Corrigé (le helper navigue lui-même) et renforcé :
  il attend désormais que l'écran ait **chargé la révision** avant
  d'enregistrer, sinon le scénario prouverait une écriture sans verrou.
  Le test a donc fait son travail : il a trouvé un défaut de test, pas de
  produit — mais un test faux aurait fait déclarer « concurrence prouvée » à
  tort.
* **E-32 — la CI ne donnait pas au provisionnement les identifiants dont il a
  besoin.** Run `37617039519` : les jobs `sauvegarde` et `recette-corpus-reel`
  échouaient à l'étape « Provisionner les identités restreintes », AVANT le
  moindre test (les étapes de test restaient « skipped » — d'où un `exit 1`
  sans aucune annotation exploitable). Cause : le conteneur MinIO était bien
  démarré avec `docker run -e MINIO_ROOT_USER=…`, mais ces variables n'étaient
  pas exposées à l'**étape** qui appelle `scripts/provisionner_stockage.sh` :
  le script s'arrêtait sur son garde-fou `:?` (« MINIO_ROOT_USER requis »).
  Autrement dit, le refus d'un repli administrateur fonctionnait — c'était
  l'appelant qui était en faute. Correctif : identifiants fournis au niveau de
  l'ÉTAPE (et non du job), pour que les suites de tests de ces jobs continuent
  de tourner avec l'identité **restreinte** et n'héritent jamais du root.
  Vérifié : job `sauvegarde` **vert** au run suivant (`37618670336`).
  Régression verrouillée par trois tests sans service
  (`test_ci_fournit_au_provisionnement_toutes_les_variables_exigees`,
  `test_ci_pointe_le_provisionnement_sur_le_conteneur_minio_hors_compose`,
  `test_ci_garde_l_administrateur_hors_des_jobs_a_identite_restreinte`),
  démontrés ROUGES sur le workflow d'avant correctif.
  Dans le même geste, l'étape `always()` « Check corpus ZIPs (RG13, après
  recette) » dit maintenant `rg13-non-verifie` au lieu de lever un
  `FileNotFoundError` illisible quand l'étape « avant » a été sautée : un
  invariant non vérifié ne doit jamais ressembler à un incident de
  restauration.
* **E-33 — un test qui figeait un nom de champ inexistant.** Depuis E-30, le
  scénario de concurrence ouvrait la bonne fiche, mais cherchait encore
  `input[data-testid="champ-materiau"]` : les fiches réelles portent des champs
  **préfixés par famille** (`materiau.tissu_principal`, `cotes.finie.slu_m`,
  `fiche.designation`…). Vérifié sur la base de recette réelle : la fiche
  `0901-MM` n'a **aucun** champ nommé `materiau`. Correctif : `choisirChamp()`
  choisit un champ réellement présent ET unique à l'écran (un champ de PDF
  `data-zone="true"` d'abord — un nom de champ peut apparaître plusieurs fois,
  une fois par **rang** de zone, et partager alors le même `data-testid` :
  la preuve « la valeur du collègue est intacte » y serait ambiguë). Le
  scénario 1 attend aussi `validation-app[data-revision]` **après** le
  rechargement (le bouton fait deux requêtes : champs puis révision) avant que
  B ne rejoue — sans cette attente, la seconde correction partait sans verrou et
  le test prouvait moins que ce qu'il annonçait.
* **E-34 — l'opérateur relisait sa propre saisie périmée (défaut PRODUIT).**
  Run `37618670336`, étape « Run multi-session E2E » : `1 failed, 3 passed`.
  Le scénario va jusqu'au bout du conflit (bandeau `conflit-revision`, valeur
  du collègue affichée, aucune écriture de B, aucun faux succès, base
  inchangée) puis échoue à une seule assertion :
  `expect(posteB.locator(selecteur)).toHaveValue("MATERIAU-POSTE-A")` reçoit
  `"MATERIAU-POSTE-B"` après le clic sur « Recharger la fiche à jour ».
  Cause réelle : le tableau des champs garde un **brouillon local** par champ,
  état React qui survit au rafraîchissement des données ; l'écran continuait
  donc d'afficher la saisie de B **après** un rechargement pourtant demandé
  explicitement — B croyait lire l'état enregistré, et rejouer sa correction
  périmée était le comportement induit. Correctif (comportemental, aucun
  changement visuel) : le parent demande au tableau d'**oublier** les
  brouillons — un seul champ après un enregistrement réussi (les autres
  saisies non envoyées sont CONSERVÉES : ne jamais perdre de travail en
  silence), tous après un rechargement explicite (l'opérateur demande l'état
  enregistré), et un remontage par fiche pour qu'aucun brouillon ne traverse
  un changement de fiche.
* **E-35 — la recette écrivait la sauvegarde avec l'identité applicative.**
  Même run, job `recette-corpus-reel` :
  `test_10_sauvegarde_restauration_base_neuve` échoue sur
  `StorageError: Could not check whether backups/seamtech-search-…dump exists
  in bucket seamtech-…`. Le client S3 du test était construit avec
  `SEAMTECH_S3_ACCESS_KEY` **quelle que soit la cible** — y compris pour
  `seamtech-backups`, sur lequel l'identité applicative restreinte n'a
  **aucun** droit. Deux défauts pour le prix d'un : (a) le client visait le
  bucket de sauvegarde avec la mauvaise identité ; (b) les constantes du test
  retombaient en silence sur `minioadmin`/`minioadmin123` quand la variable
  d'environnement manquait — un repli administrateur qui rendait la recette
  verte pour de mauvaises raisons (l'ALLOW n'existait que parce que l'identité
  en place était encore la racine MinIO). Correctif : identité choisie d'après
  le bucket visé (sauvegarde → `SEAMTECH_BACKUP_ACCESS_KEY`/`_SECRET_KEY`),
  **aucun** repli (défaut vide → échec clair et nommé), et refus explicite si
  l'identité de sauvegarde manque. Le `StorageError` de `first_free_key` n'est
  **pas** affaibli : il protège l'écriture sur une clé peut-être occupée.
  Régression verrouillée par deux tests sans service (dont un contrôle AST que
  les défauts d'environnement sont vides).
* **E-31 — le nouvel outil hors ligne violait RG14.** Le garde-fou RG14
  interdit `socket`/`urllib` dans `scripts/` ; l'outil de vérification les
  importe **par fonction** (intercepter et bloquer les connexions). Exception
  documentée dans le garde-fou lui-même + marqueur dans le module +
  `docs/DEPLOYMENT.md` — jamais pour le service, qui continue de ne rien
  importer de réseau (c'est ce que vérifie le garde-fou).

* **E-28 — téléchargements inaccessibles depuis un autre poste.** La première
  exécution *réussie* de l'import Compose a laissé apparaître la vraie panne :
  le rapport était bien généré, mais `GET …/artifacts/report_pdf` répondait
  **302 vers l'endpoint interne** (`minio:9000`) et le navigateur d'un poste
  d'atelier ne peut pas résoudre ce nom. Le test le plus précieux ici est celui
  qui a *échoué* : sans la pile réelle, le défaut restait invisible (boto3
  doublé ne résout rien). Correctif : par défaut l'API sert les octets
  (fichier local, sinon le cache est reconstitué depuis le stockage, sinon
  l'objet est transmis en flux) ; la redirection n'est émise que vers un
  endpoint **public** déclaré. `/open` sert en plus un original dont la copie
  locale a disparu **sans jamais réécrire dans l'archive** (cas d'une
  restauration). Cinq tests dédiés (`tests/test_telechargement_navigateur.py`)
  plus la mise à jour du garde-fou historique `tests/test_url_presignee_302.py`
  (15 tests) figent ce contrat.

---

## 5. Intégration continue

Les liens exacts et l'état de chaque job pour le commit de tête sont consignés
dans `docs/verite_terrain/TRACABILITE_LIVRAISON.md` (§ « Revue indépendante
2026-10-07 »), avec le SHA. Ce que chaque job apporte et **ne peut apporter
qu'à lui** :

| Job | Preuve exclusive |
|---|---|
| `backend` (3.11/3.12/3.13) | suites PostgreSQL + perf, garde-fou « aucun saut inattendu » |
| `frontend` | `tsc`, `pnpm audit --prod`, `pnpm build` |
| `docker` | images construites + démarrage réel + sonde `/live` |
| `integration` | **MinIO réel, PostgreSQL réel, Redis réel, pile Compose à deux conteneurs** (dont la preuve de partage web/worker) |
| `e2e` | navigateur Playwright (parcours complets, y compris multi-sessions) |
| `sauvegarde` | aller-retour sauvegarde→destruction→restauration, y compris 50 000 fiches |
| `recette-corpus-reel` | ingestion du corpus réel de démonstration |
| `recette-locale` | recette locale du poste cible |
| `ocr` | chaîne OCR (tesseract) |
| `securite-dependances` | pip-audit + pnpm audit selon la politique du dépôt |

**Historique de la passe** (chaque ligne = un run LU, pas supposé) :

1. `37613980912` — `recette-locale` rouge : les politiques MinIO étaient lues
   par `mc` depuis un chemin de l'hôte (E-29). Corrigé, plus
   `e2e` rouge sur le helper de navigation (E-30).
2. `37617039519` (push `948130d`) — 4 verts (`docker`, `ocr`, `frontend`,
   `securite-dependances`), **`e2e`** rouge (E-33 : sélecteur `champ-materiau`
   inexistant), **`sauvegarde`** et **`recette-corpus-reel`** rouges (E-32 :
   `MINIO_ROOT_*` absent de l'étape de provisionnement),
   `integration`/`recette-locale`/`backend` verts.
3. `37618670336` (push `3e13c7f`) — **`sauvegarde` VERTE** (E-32 corrigé) ;
   restent rouges **`e2e`** (`1 failed, 3 passed` : E-34, défaut produit du
   brouillon conservé après rechargement) et **`recette-corpus-reel`**
   (`test_10` : E-35, identité applicative sur le bucket de sauvegarde + repli
   `minioadmin` silencieux). Les autres jobs sont verts, y compris `backend`
   3.11/3.12/3.13, `integration` (MinIO réel) et `recette-locale`.
4. Commit de clôture (E-34 + E-35 corrigés, plus 5 tests de régression) — le
   run correspondant est relancé en fin de passe ; **c'est lui qui fait foi**,
   et son résultat est consigné dans `TRACABILITE_LIVRAISON.md` dès lecture.

**Aucun résultat CI n'est revendiqué ici avant lecture du job.** Les jobs verts
cités sont ceux **lus** aux points 2 et 3 ; le statut du point 4 est ouvert au
moment de la rédaction.

---

## 5 bis. Périmètre produit — chaque scénario et SA preuve

Règle de lecture : **un nom de job vert ne prouve pas un scénario que ce job
n'exerce pas.** Chaque ligne cite l'épreuve qui exerce réellement le scénario.

| Scénario du périmètre | Épreuve qui l'exerce | État |
|---|---|---|
| Glisser-déposer + comptabilité COMPLÈTE des fichiers d'un dossier | `frontend/e2e/import.spec.ts` (dépôt de dossier, `REF-2026-CLIENT123`) ; `tests/test_import_pipeline.py`, `tests/test_lot_ingestion.py` ; harness hors ligne (« 3 fichier(s) détecté(s), 1 analysé(s) ») | **PROUVÉ** (navigateur : CI) |
| Imports unitaires ET par lot, récupérables (un échec ne bloque pas le lot) | `tests/test_file_durable_operations.py`, `tests/test_lot_ingestion.py` | **PROUVÉ** (local) |
| File durable + reprise après crash du worker (relais en base) | `tests/test_file_durable.py` (23), `tests/test_relais_file_base.py` (vrais sous-processus, SIGKILL), `tests/test_jobs_resilience.py` | **PROUVÉ** (local, Redis réel) |
| Visibilité en recherche après import ET après correction | harness hors ligne (fiche retrouvée après dépôt) ; `tests/test_recherche_hybride.py`, `tests/test_recherche_dimension.py` ; sur corpus réel : job `recette-corpus-reel` | **PROUVÉ** (local) ; corpus réel : **CI** |
| Validation / révision : la fabrication ne peut pas écraser une correction humaine | `tests/test_fiches_persistance.py` + `tests/test_lot_ingestion.py` (RG11) ; harness (levée explicite du verrou) | **PROUVÉ** |
| Récupération de l'original ET du rapport généré | `tests/test_telechargement_navigateur.py` (5), `tests/test_url_presignee_302.py` (15), harness (aperçu + original + rapport) | **PROUVÉ** (local) ; conteneurs : **CI** |
| Édition concurrente : deux postes, même fiche, aucun écrasement silencieux | `tests/test_revision_optimiste.py` (6, dont 2 clients SIMULTANÉS) ; `frontend/e2e/concurrence.spec.ts` (4 scénarios multi-navigateurs) | backend **PROUVÉ** ; navigateur : **CI** — 3 défauts (2 de test, 1 de produit) trouvés par ces exécutions et corrigés (E-30, E-33, E-34) ; run de clôture en cours |
| Permissions restreintes : l'opérateur est refusé par le BACKEND | `tests/test_comptes.py`, `tests/test_credentials_s3_restreintes.py` (ALLOW/DENY réels contre MinIO) ; scénario 4 de la suite concurrence (403 backend) | **PROUVÉ** (local) ; MinIO réel + navigateur : **CI** |
| Sauvegarde + restauration en environnement propre | `tests/test_sauvegarde_restauration.py`, `tests/test_sauvegarde_unites.py` ; job `sauvegarde` (dont 50 000 fiches) | **PROUVÉ** en CI ; **drill HUMAIN : OUVERT** |
| Fonctionnement hors ligne | `scripts/verifier_hors_ligne.py --executer` (réseau externe bloqué, 0 échec) ; `tests/test_verification_hors_ligne.py` (5) | **PROUVÉ automatiquement** (local) ; **acceptation atelier : OUVERTE** |
| Identités de stockage restreintes (applicative ≠ sauvegarde ≠ racine) | `tests/test_credentials_s3_restreintes.py` (**18** locaux + 4 Docker), `tests/test_construire_image_minio.py` | **PROUVÉ** (local) ; MinIO réel : **CI** (`sauvegarde` verte, `integration` verte) ; la recette écrit désormais la sauvegarde avec l'identité de sauvegarde (E-35) |
| Trois postes réels sur le serveur d'atelier, réseau coupé | — | **OUVERT** (portes §6) |

Rappel de méthode (revue du 2026-10-07) : la revue de ce travail par l'agent
d'implémentation n'est **pas** une approbation indépendante. Une revue externe
reste pendante ; aucune ligne ci-dessus ne la remplace.

## 6. Ce qui n'est PAS prouvé (liste exhaustive, avec ce qui le fermerait)

| Point non prouvé | Pourquoi | Ce qui le ferme |
|---|---|---|
| MinIO réel : intégrité, quarantaine, upload, téléchargement | pas de Docker/MinIO dans cette session ; 29 tests `s3` sautés **localement** | job CI `integration` (+ `sauvegarde`) sur le commit de tête — **`integration` et `sauvegarde` lus VERTS** sur `3e13c7f` ; à revérifier sur le commit de clôture |
| Pile Compose à deux conteneurs (partage fichiers/quarantaine/modèles/permissions) | idem | `tests/test_compose_partage_worker.py` dans le job `integration` |
| Navigateurs (scénario trois postes) | navigateurs Playwright non installables ici (CDN bloqué) | job CI `e2e` |
| Sauvegarde → restauration réelle de bout en bout | dépend de MinIO | job CI `sauvegarde` (19+ tests, dont 50 000 fiches) |
| **Drill de restauration humain indépendant** | aucun humain n'a restauré une sauvegarde | procédure `RUNBOOK_RESTAURATION.md`, exécutée et signée par l'exploitant |
| **Destination de sauvegarde de production** | non choisie/validée par le propriétaire | décision du propriétaire + test d'écriture hors serveur |
| **Pertinence de la recherche sur archives réelles** | aucune étiquette humaine sur corpus réel ; 7 fiches de référence, 0 validée | jeu de requêtes étiqueté (`JEU_REQUETES_REELLES.md`) + validation humaine |
| **Moindre privilège S3 — contre MinIO RÉEL** | les tests Docker correspondants ne tournent qu'en CI (pas de Docker ici) | job CI `integration` sur le commit de tête (§5) |
| **Fonctionnement hors ligne — sur le SERVEUR d'atelier** | le harnais prouve l'absence de dépendance externe **de cette machine** ; il ne prouve ni les postes réels, ni le serveur cible | exécution du harnais sur le serveur cible + acceptation trois postes réseau coupé |
| **Concurrence vue du NAVIGATEUR** | les navigateurs ne s'installent pas dans cette session (CDN `cdn.playwright.dev` bloqué — vérifié à nouveau le 2026-10-07) | job CI `e2e`, étape « concurrence » (§5) : les 3 échecs successifs (E-30 test, E-33 test, E-34 produit) sont corrigés ; **le verdict appartient au run de clôture** |
| **Acceptation atelier (fabrication)** | décision humaine | trois postes, un import pendant une recherche, validation d'une fiche |

Aucun de ces points n'est présenté comme résolu, et la revendication de
production est **explicitement repoussée** tant qu'ils ne sont pas fermés.

Quatre précisions de périmètre, pour éviter les glissements de sens :

* **Calibration ML ≠ porte de sortie générale.** Le produit exige une
  **validation humaine obligatoire** des fiches ; la calibration statistique
  des modèles n'est donc pas une condition de mise en service, et aucune ligne
  de ce rapport ne la présente comme telle.
* **R2 reste optionnel et hors périmètre local-first** : il ne sera traité que
  sur demande explicite du propriétaire.
* **« Hors-site » ≠ « stockage séparé ».** Une sauvegarde écrite depuis le même
  serveur vers un bucket voisin n'est pas une copie indépendante : il faut une
  destination distincte (et, pour une vraie résilience, hors du site). La
  configuration actuelle documente la **séparation des identités**, pas
  l'indépendance de la destination — celle-ci est une porte ouverte (§6).
* **Toutes les tâches restantes n'exigent pas un humain ou un serveur de
  production** : les preuves automatisables ont été exécutées ici (suite
  complète, base réelle, Redis réel, harnais hors ligne) ; ce qui exige
  réellement un humain ou le serveur cible est limité aux lignes du tableau
  ci-dessus et aux portes de l'atelier.

---

## 7. Reproductibilité

```bash
# Outillage (session sans Docker) : voir l'en-tête de REVUE_INDEPENDANTE_2026-10-07.md
export SEAMTECH_TEST_DATABASE_URL="postgresql://seamtech@127.0.0.1:5433/seamtech_search"
export SEAMTECH_TEST_REDIS_URL="redis://:sandboxredis@127.0.0.1:6379/1"

# Suites
.venv/bin/python -m pytest -q -m "not redis_queue and not postgres and not s3 and not perf \
  and not recette_corpus and not integration_docker and not sauvegarde"
.venv/bin/python -m pytest -q -m "postgres and not perf and not sauvegarde and not redis_queue"
.venv/bin/python -m pytest -q -m redis_queue
.venv/bin/python -m pytest -q tests/test_relais_file_base.py      # vrais processus worker

# Front
cd frontend && pnpm install --frozen-lockfile && pnpm exec tsc --noEmit && pnpm audit --prod && pnpm build

# Compose (poste avec Docker)
docker compose up -d --build web worker && pytest -m integration_docker -v
```

---

## 8. Prochaines étapes, dans l'ordre

1. Lire le résultat des jobs longs du commit de tête et le consigner (§5).
2. Fermer **E-26** côté déploiement : décision sur le compte applicatif S3
   (moindre privilège) et destination de sauvegarde de production.
3. Organiser le **drill de restauration humain** sur le serveur d'atelier.
4. Faire valider humainement le corpus de référence (0/7 aujourd'hui) et jouer
   le jeu de requêtes réelles.
5. Recette atelier trois postes, puis décision de pilote.
