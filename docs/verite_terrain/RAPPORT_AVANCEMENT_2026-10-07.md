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

État de la CI au moment de la rédaction : voir §5 (les runs du commit de tête
sont les seuls qui font foi).

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
* **Ce qui reste ouvert** : identifiants applicatifs de moindre privilège
  (le déploiement documenté utilise encore le compte racine MinIO) ; R2 non
  traité (par conception : un seul fournisseur supporté) ; exécution réelle
  contre MinIO : **CI seulement**.

### 2.3 Téléchargements, navigateur, conception d'interface

* Les téléchargements passent par un **proxy authentifié** de l'API
  (`…/pieces/{id}/telecharger`), pas par une URL présignée pointant vers un nom
  d'hôte interne au conteneur ni vers `localhost` du navigateur : c'est ce qui
  les rend utilisables **depuis un autre poste de l'atelier**.
* L'aperçu et le téléchargement sont distingués (aperçu ≠ téléchargement) ;
  les originaux restent téléchargeables.
* **La conception UI de l'atelier n'a pas été modifiée** : les changements sont
  comportementaux (états, messages d'erreur, motifs par fichier, badges de
  validation) et non visuels.

### 2.4 Exploitation

`/health` expose désormais, pour l'exploitant : file (profondeur, workers
vivants, lettres mortes), jobs par état + jobs actifs, état de sauvegarde,
espace disque, type d'identifiants S3 (`s3_credential_kind`) et statut du
versioning du bucket. `worker_service --verifier` rend un diagnostic exploitable
et **refuse de démarrer sans Redis**.

---

## 3. Preuves d'exécution locales (mesurées, rejouables)

Environnement de la session : Linux sans Docker, sans MinIO, sans navigateur ;
PostgreSQL 16.2 + pgvector 0.8.0, Redis 7.2.5 et venv Python montés à la main.

| Suite | Sélection | Résultat |
|---|---|---|
| Sans service | `-m "not redis_queue and not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not sauvegarde"` | **817 passés, 3 sautés, 315 désélectionnés** |
| PostgreSQL réel | `-m "postgres and not perf and not sauvegarde and not redis_queue"` | **220 passés, 1 sauté** |
| File durable (Redis + PostgreSQL réels) | `-m redis_queue` | **37 passés, 0 sauté** |
| S3 vivant (MinIO) | `-m s3` | **0 passé, 29 sautés** — aucun service S3 ici (§6) |
| Compose 2 conteneurs | `tests/test_compose_partage_worker.py` | **1 sauté** — Docker absent (§6) |
| Contrat de sélection CI | `tests/test_selection_ci.py` | **14 passés** |
| Front | `pnpm exec tsc --noEmit` / `pnpm build` / `pnpm audit --prod` | **OK / OK / 0 vulnérabilité** (avant : 2 HIGH) |
| Lint + compilation | `ruff check .` / `python -m compileall seamtech_search` | **propre / OK** |

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
écartés par `-m`), à **817 passés constants**. Les 3 sauts restants sont les
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
  sont.

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

**Historique de la passe** : les quatre échecs constatés sur `512b6046`
(`integration`, `sauvegarde`, `frontend`, `securite-dependances`) étaient
**antérieurs** (`bf62783`) et de deux natures : deux défauts réels de CI
(E-25 sauts fantômes, E-26 permissions) et deux avis de dépendances (E-27).
Après correction, les jobs repassent au vert : `frontend`, `docker`, `ocr`,
`e2e` et `securite-dependances` sont **verts** sur le commit de tête, les jobs
lourds (`backend`, `integration`, `sauvegarde`, `recette-*`) sont plus longs par
construction. **Aucun résultat CI n'est revendiqué ici avant lecture du job.**

---

## 6. Ce qui n'est PAS prouvé (liste exhaustive, avec ce qui le fermerait)

| Point non prouvé | Pourquoi | Ce qui le ferme |
|---|---|---|
| MinIO réel : intégrité, quarantaine, upload, téléchargement | pas de Docker/MinIO dans cette session ; 29 tests `s3` sautés **localement** | job CI `integration` (+ `sauvegarde`) sur le commit de tête |
| Pile Compose à deux conteneurs (partage fichiers/quarantaine/modèles/permissions) | idem | `tests/test_compose_partage_worker.py` dans le job `integration` |
| Navigateurs (scénario trois postes) | navigateurs Playwright non installables ici (CDN bloqué) | job CI `e2e` |
| Sauvegarde → restauration réelle de bout en bout | dépend de MinIO | job CI `sauvegarde` (19+ tests, dont 50 000 fiches) |
| **Drill de restauration humain indépendant** | aucun humain n'a restauré une sauvegarde | procédure `RUNBOOK_RESTAURATION.md`, exécutée et signée par l'exploitant |
| **Destination de sauvegarde de production** | non choisie/validée par le propriétaire | décision du propriétaire + test d'écriture hors serveur |
| **Pertinence de la recherche sur archives réelles** | aucune étiquette humaine sur corpus réel ; 7 fiches de référence, 0 validée | jeu de requêtes étiqueté (`JEU_REQUETES_REELLES.md`) + validation humaine |
| **Moindre privilège S3** | identifiants racine MinIO dans le déploiement documenté | création d'un compte applicatif restreint + test de refus |
| **Fonctionnement hors ligne** | non testé « réseau externe bloqué » | exécution sur le serveur cible, `unshare -n` ou pare-feu, parcours complets |
| **Acceptation atelier (fabrication)** | décision humaine | trois postes, un import pendant une recherche, validation d'une fiche |

Aucun de ces points n'est présenté comme résolu, et la revendication de
production est **explicitement repoussée** tant qu'ils ne sont pas fermés.

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
