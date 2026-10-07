# Revue indépendante — réponses point par point (2026-10-07)

Ce document répond aux six demandes de la revue indépendante, avec **les
commandes exécutées et leurs résultats bruts**. Ce qui n'a pas pu être exécuté
dans cet environnement est écrit noir sur blanc, avec le motif exact, plutôt que
présenté comme vérifié.

Environnement d'exécution de cette session : conteneur Linux sans Docker, sans
MinIO, sans navigateur ; PostgreSQL 16.2 + pgvector 0.8.0, Redis 7.2.5 et le
venv Python montés à la main. Les compilations front-end (`tsc`, `next build`)
et Playwright y sont traités séparément (§5).

---

## 0. Résultats d'exécution (ce qui a réellement tourné ici)

| Suite | Sélection pytest | Résultat |
|---|---|---|
| SQLite / sans service | `-m "not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue"` | **833 passed, 3 skipped, 299 deselected**, 84 s |
| PostgreSQL réel | `-m "postgres and not perf and not redis_queue"` | **226 passed, 2 skipped**, 75 s |
| File durable (Redis + PostgreSQL réels) | `-m "redis_queue"` | **37 passed, 0 skipped**, 62 s |
| S3 vivant (MinIO) | `-m "s3"` | **0 passed, 29 skipped** (aucun service S3 ici — voir §4) |
| Lint + compilation | `ruff check .` / `python -m compileall seamtech_search` | **All checks passed / COMPILE_OK** |

**Comptabilité corrigée `skipped` vs `deselected`** (correction demandée) :
`deselected` = tests écartés par la sélection `-m` (ils n'ont pas été demandés et
ne sont pas un problème) ; `skipped` = tests *demandés* qui se sont déclarés
inaptes. Les deux nombres ci-dessus ne sont pas comparables et ne doivent pas
être additionnés comme s'il s'agissait de tests « non exécutés ». Les 3 skips de
la suite SQLite sont des sauts conditionnels légitimes (service absent). La
lecture antérieure « 5 skips / 33 non exécutés » mélangeait un artefact
d'environnement (`SEAMTECH_TEST_REDIS_URL` non posé ⇒ la suite file durable
s'auto-sautait au lieu d'échouer) avec de vraies sélections CI : elle est
abandonnée. Depuis, la variable est posée pour toutes les mesures, et le job CI
`redis_queue` **échoue** si `skipped != 0` — un job vert où tout serait sauté
n'existe plus.

**Deux vrais défauts trouvés et corrigés pendant cette passe** (tous deux par
les tests que la revue demandait) :

1. **`max_task_attempts` hors d'un cran** (`seamtech_search/worker.py`) : la
   garde comparait `attempt < max_attempts` au lieu de `attempt + 1 < max_attempts`,
   si bien qu'un réglage à 3 tentatives en autorisait 4. Corrigé, et le
   commentaire du champ dit désormais explicitement que c'est un nombre **total**
   de tentatives.
2. **Démarrage du conteneur `web` cassé par une variable d'environnement mal
   lue** (`seamtech_search/config.py`) : la surcharge testait
   `SEAMTECH_REQUIRE_DURABLE_QUEUE` puis indexait `SEAMTECH_STORAGE_VERIFY_REREAD`,
   d'où un `KeyError` dès que la durabilité était exigée sans que l'autre
   variable soit posée — exactement la configuration du compose documenté. Le
   garde-fou existant `tests/test_compose_web_boot.py` l'a attrapé (2 échecs),
   la surcharge est corrigée, un test de régression dédié a été ajouté
   (`tests/test_config.py::test_variables_durables_et_relecture_ne_dependent_pas_l_une_de_l_autre`)
   et la variable est maintenant déclarée dans `docker-compose.yml` et
   `.env.example`.

---

## 1. Vérification du stockage et purge : le SHA-256 des métadonnées est-il validé contre les octets stockés ? La taille seule autorise-t-elle la suppression locale ?

**Réponses : (a) non, et le code le dit explicitement ; (b) non, la purge locale
est refusée.**

### (a) Ce qui compte comme preuve

`seamtech_search/storage.py` → `S3StorageClient.verifier_integrite()` renvoie
`ResultatVerification(integrite_prouvee, methode, detail)` :

| Méthode | Quand | Preuve d'intégrité ? |
|---|---|---|
| `relecture_sha256` | **défaut** (`storage_verify_reread: true`) : GET de l'objet, SHA-256 recalculé sur les octets **reçus** | **OUI** |
| `checksum_serveur` | réponse `ChecksumSHA256` du fournisseur **et** `ChecksumType == FULL_OBJECT` | **OUI** |
| `metadata_seule` | seule l'empreinte `seamtech-sha256` que **nous** avons écrite revient | **NON** — le détail dit littéralement que cela ne prouve pas les octets stockés |
| `taille_seule` | `ContentLength` seulement | **NON** |
| `echec` | empreinte ou taille divergente | non — **et c'est un échec bloquant** |

Autrement dit : une métadonnée fournie par l'application n'est **jamais**
présentée comme une vérification de contenu indépendante. Les envois demandent
en plus au fournisseur de valider de son côté (`ChecksumAlgorithm: SHA256` dans
`upload_file`/`upload_bytes`), avec repli **journalisé** si le fournisseur
refuse.

Preuve exécutée : `tests/test_verification_integrite.py` (10 tests, dans la
suite SQLite) s'appuie sur `tests/s3_en_memoire.py`, un magasin S3 en mémoire
qui **conserve les vrais octets** : `corrompre()` modifie le corps du document
sans toucher aux métadonnées, et le test exige que la vérification le voie
(`echec`), tandis qu'une corruption de **même longueur** doit honnêtement
retomber sur `metadata_seule` (non prouvé) — c'est le cas qui piégeait l'ancienne
implémentation.

### (b) La purge locale

Chemin de code : `seamtech_search/worker.py` (~l. 205-240). La purge du brouillon
n'a lieu que si

```python
truly_uploaded = status == "uploaded" and all_verified and files_have_keys
```

où `all_verified` ne vaut que pour les méthodes **preuves** ci-dessus. Donc :

* taille seule ⇒ `all_verified = False` ⇒ **pas de purge**, article conservé ;
* `SEAMTECH_STORAGE_VERIFY_REREAD=false` ⇒ tout est `metadata_seule` ⇒ **pas de
  purge**, avec le motif visible côté opérateur
  (`error="intégrité non prouvée (…) — copie locale conservée"`). Désactiver la
  relecture ne fait donc pas « gagner du temps », cela **interdit** de libérer le
  disque : c'est écrit dans la description du champ de configuration.

Preuves exécutées : `tests/test_storage.py`, `tests/test_storage_coverage.py`,
`tests/test_stockage_release_candidate.py` (29 tests, dont un magasin qui stocke
un `tableau.xlsx` tronqué et un envoi interrompu → quarantaine récupérable),
plus les 10 tests d'intégrité.

---

## 2. Relais file/base, revendications atomiques, propriété expirée, idempotence à plusieurs points de crash

Tous les tests ci-dessous s'exécutent contre **Redis et PostgreSQL réels**
(suite `redis_queue`, 37 passés). Les cinq derniers lancent de **vrais
processus** `python -m seamtech_search.worker_service` et les tuent par
`SIGKILL` : ce n'est plus une simulation de worker dans le processus de test.

| Ce qui est prouvé | Test | Mécanisme |
|---|---|---|
| Relais complet base → file → **processus worker séparé** → base | `tests/test_relais_file_base.py::test_relais_pending_vers_completed_entre_processus` | job `pending` en base, tâche en file, worker externe `--une-passe` ; état terminal relu en base, verrou relâché, `attempts == 1`, 2 documents indexés, file vide |
| Revendication **atomique** : deux workers ne peuvent pas prendre la même tâche | `…::test_revendication_non_volable_entre_deux_processus` | script Lua `_SCRIPT_REVENDIQUER` (clé Redis réelle) ; le second obtient `False` et ne peut pas écrire l'état terminal |
| **Crash en plein travail** (`SIGKILL`) | `…::test_crash_puis_reprise_par_un_autre_processus` | dossier de 41 fichiers pour garantir la mort *pendant* le traitement ; le job reste `running` avec propriétaire identifié ; le verrou expire par **TTL réel** ; un second processus reprend et termine ; le nombre de documents indexés est **exactement** le nombre de fichiers (aucun doublon) |
| Reprise **sans vol de verrou** d'un pair vivant | `tests/test_file_durable.py` (worker qui refuse une tâche détenue par un pair) | `reprendre=False` par défaut + avertissement journalisé |
| Zombie dont le verrou a expiré : ni libération, ni prolongation, ni écriture terminale | `…::test_zombie_ne_peut_ni_liberer_ni_ecrire_l_etat_terminal` + `tests/test_file_durable.py` | `_SCRIPT_LIBERER` renvoie `1/0/-1` ; `revendication_appartient_a()` garde **chaque** écriture terminale ; le repreneur garde son verrou |
| Redélivrance après « documents indexés mais job non marqué » ⇒ **0 doublon** | `tests/test_file_durable.py::test_livraison_dupliquee_apres_indexation_sans_marquage_ne_duplique_pas` | point de crash forcé après l'indexation, avant l'état terminal (`process_import_task` lève) ; le job reste volontairement `running` ; second passage → comptage identique |
| Crash **après envoi objet, avant écriture base** : deux clés/objets distincts, aucun écrasement | `tests/test_file_durable.py::test_point_de_crash_apres_envoi_objet_avant_ecriture_en_base` | clés collision-safe ; les deux objets restent intacts |
| Épuisement des tentatives → **lettre morte avec raison lisible** | `tests/test_relais_file_base.py::test_epuisement_des_tentatives_va_en_lettre_morte_avec_raison_entre_processus` | source inexistante ; `status=failed`, `failure_reason` non vide, `deadletter >= 1` |
| Job orphelin repris après mort du worker | `tests/test_file_durable.py` (reprise `reprendre=True`) | — |
| Lot interrompu : reprise sans perte | `tests/test_file_durable.py` (lot durable) | — |

Ce qui **n'est pas** promis : « exactement une fois ». La garantie est
l'**idempotence** + la lettre morte + l'état terminal unique.

---

## 3. Test Compose : le web et un worker distinct partagent-ils fichiers, brouillons, quarantaine et modèles ?

Deux niveaux, l'un exécuté ici, l'autre exécutable seulement avec Docker.

### 3.1 Exécuté ici (3 passés) : `tests/test_partage_web_worker.py`

* `test_contrat_compose_web_worker` lit le **vrai** `docker-compose.yml` et exige :
  même image pour `web` et `worker`, mêmes volumes montés **aux mêmes chemins**
  (`data`, `logs`, `sample_data`), archive `:ro` des deux côtés, mêmes
  `SEAMTECH_REDIS_URL`/`SEAMTECH_DATABASE_URL`, **aucun port publié** par le
  worker, `USER seamtech` (non-root) dans le `Dockerfile`. Une omission de volume
  casse ce test.
* `test_web_et_worker_partagent_fichiers_scratch_rapports_et_modeles` démarre un
  **vrai serveur** (`python -m seamtech_search serve`, processus séparé) et un
  **vrai worker** (processus séparé), puis : téléversement multipart → le
  brouillon atterrit sous `<data>/uploads/…` ; acceptation durable
  (`durability == "durable"`) ; le worker traite ; les **rapports générés par le
  worker** sont lus côté web ; le brouillon est purgé après envoi vérifié ; le
  dossier de modèles ML est résolu au **même chemin** par les deux processus et
  lisible par les deux ; l'**archive est bit-à-bit identique** (empreinte
  SHA-256 de l'arbre avant/après, permissions `0555`/`0444` imitant le `:ro`).
* `test_worker_tourne_sans_le_volume_du_web_est_refuse` fixe la conséquence
  inverse : sans accès au fichier partagé, l'import échoue.

### 3.2 Écrit, **non exécuté ici** : `tests/test_compose_partage_worker.py`

C'est le test Compose proprement dit, sur la vraie pile : `docker compose up -d
--build web worker`, téléversement par l'API `web`, acceptation **non
bloquante** (`/imports/confirm?wait=false`, `durability == "durable"`), puis
exécution par le conteneur `worker` et vérifications :

| # | Vérification |
|---|---|
| a | le conteneur `worker` est `running`, sa sonde `--verifier` répond, et `id -u` ≠ 0 (non-root) |
| b | le brouillon écrit par `web` est **vu et lisible** depuis `worker` (volume partagé + permissions) |
| c | les rapports écrits par `worker` existent sur le volume et sont vus par `web` au même chemin |
| d | le rapport est **téléchargeable en HTTP** (`/imports/{id}/artifacts/report_pdf`, `Content-Disposition: attachment`) — donc depuis un autre poste de l'atelier, sans URL présignée pointant sur un nom d'hôte interne |
| e | fichier témoin écrit par `worker` et relu par `web` (partage prouvé dans les deux sens) |
| f | `quarantine_root`/`staging_root` résolus à `/app/data/quarantine` et `/app/data/uploads` **dans le worker**, jamais dans l'archive |
| g | archive inchangée (SHA-256 de l'arbre avant/après) et brouillon purgé après envoi vérifié |

Statut d'exécution ici : `pytest -m integration_docker` →
**`1 skipped` avec le motif « Docker/Compose absents »** (vérifié : `docker`
introuvable dans cet environnement, aucune image ne peut être construite). Le
test est **déclaré dans l'inventaire** `INVENTAIRE_S3_REEL` de
`tests/test_selection_ci.py` pour qu'il ne puisse pas se cacher des sélections.

**Donc : le partage web/worker est prouvé au niveau processus et contrat dans
cette session ; il n'est PAS prouvé au niveau conteneurs** — c'est précisément ce
que le job CI `integration` exécute (`pytest -m integration_docker`), sur un
runner GitHub avec Docker. Lien du run : voir §7.

---

## 4. Résultats RÉELS S3 et restauration

**Réponse franche : aucun résultat contre un S3 réel ni aucune restauration
complète n'ont pu être produits dans cet environnement.** Motifs, vérifiés :

* pas de Docker ⇒ ni MinIO ni la pile Compose ne peuvent démarrer ;
* `dl.min.io` renvoie `410 Gone` / est bloqué, et le dépôt `minio` exige une
  chaîne d'outils Go absente : aucun binaire MinIO exécutable ici ;
* `-m "s3"` ⇒ **29 skipped, 0 passed** (liste : `tests/test_integration_docker.py`
  (2), `tests/test_storage.py` (1), `tests/test_sauvegarde_restauration.py` (1),
  `tests/test_recette_corpus_reel.py` (24), `tests/test_compose_partage_worker.py` (1)).

Ce qui a été exécuté **sur doubles** — et qui ne doit pas être maquillé en test
S3 réel — c'est `tests/s3_en_memoire.py` + `tests/test_verification_integrite.py`
(10 passés) : un magasin en mémoire qui conserve les vrais octets et permet de
prouver les verdicts de vérification. Cela prouve la **logique** de vérification,
pas l'interopérabilité avec MinIO.

Responsabilité de la suite : les tests qui exigent un service vivant sont ceux
inventoriés dans `tests/test_selection_ci.py` (garde-fou par marqueurs), et la CI
les exécute dans trois jobs : `integration` (MinIO sur la pile complète),
`sauvegarde` (aller-retour hors-site vers un vrai bucket), `recette-corpus-reel`
(corpus réel, téléchargements et sauvegarde). Ces jobs produisent les preuves
manquantes ; ils tournent sur le commit de cette branche (§7).

Restaurations : la même limite s'applique. Ce qui existe est la **procédure**
(`docs/verite_terrain/RUNBOOK_RESTAURATION.md`) et ses tests automatisés ; le
**drill humain indépendant** et la **destination de sauvegarde de production**
restent **PENDING** — ils exigent un deuxième emplacement de stockage réel, hors
de ce sandbox, et ne peuvent pas être simulés honnêtement.

---

## 5. Front-end : build, vérification de types, tests navigateur

Exécuté ici, dans `frontend/` :

| Commande | Résultat |
|---|---|
| `npx tsc --noEmit` | **exit 0** — et les **11 fichiers de specs e2e** sont bien couverts (`--listFiles` en liste 11, tsconfig inclut `**/*.ts`) |
| `npx next build` | **exit 0** — build de production complet (toutes les routes `/api/*`, `/recherche`, `/validation`, `/fichiers`, `/dossiers`, `/qualite`, `/login`…) |

**Non exécuté : les tests navigateur.** Motif vérifié :
`npx playwright install chromium` échoue sur `cdn.playwright.dev`
(`ECONNRESET` TLS), `storage.googleapis.com` est également bloqué, aucun
navigateur système n'est installé et `apt-get download chromium` ne trouve aucun
paquet. Sans binaire de navigateur, Playwright ne peut pas démarrer : le dire est
plus utile que d'inventer un équivalent.

Ce qui couvre la demande « tests navigateur multi-sessions » malgré tout :
le job CI `e2e` installe Playwright (`pnpm exec playwright install --with-deps
chromium`) et rejoue 11 specs, dont le double parcours de session
(`e2e/auth.spec.ts` pour l'expiration de session), l'import par glisser-déposer
(`e2e/import.spec.ts`, `e2e/parcours-fichiers.spec.ts`), la validation
concurrente (`e2e/validation.spec.ts`, `e2e/doublons.spec.ts`) et la recherche
(`e2e/search.spec.ts`, `e2e/recherche-*.spec.ts`), puis un passage « live »
PostgreSQL + `next start` avec garde-fous de porte (0 flaky, 0 saut, chrono
publié). Exécution : voir le lien du job `e2e` au §7.

---

## 6. Tableau d'état — exigences d'origine

Statuts : **VÉRIFIÉ** (exécuté et observé), **CI** (exécuté par le job CI nommé,
lien au §7), **ÉCRIT** (code + tests présents, non exécutables ici), **PENDING**
(exige le serveur/le client).

| Exigence d'origine | État | Preuve / reste à faire |
|---|---|---|
| Recherche sur les projets existants | VÉRIFIÉ (socle) | suite SQLite 833P + PG 226P : recherches exactes/partielles, accents via `unaccent`, casse/ponctuation, dimensions/unités, filtres, résultats vides |
| Recherche : pertinence sur archives réelles | PENDING | aucun jugement de pertinence sur données clients réelles : exige le jeu de requêtes étiqueté par un humain (`docs/verite_terrain/JEU_REQUETES_REELLES.md`) + corpus réel (job `recette-corpus-reel`) |
| Import de dossiers anciens/nouveaux | VÉRIFIÉ | worker durable, lots, quarantaine, reprise ; 37 tests de file réels dont 5 multi-processus |
| Comptabilité du glisser-déposer (chaque fichier compté) | VÉRIFIÉ | `MAX_UPLOAD_FILES`, caps agrégés, `507` quand le disque manque, `_safe_relative_path` (anti-traversée), motifs d'échec par fichier ; specs `e2e/import.spec.ts`, `e2e/parcours-fichiers.spec.ts` |
| Formats supportés / indexé vs métadonnées / téléchargeable / prévisualisable | VÉRIFIÉ (doc) | `docs/STRUCTURE.md` + `docs/FILE_DURABLE.md` : contrat par format ; l'aperçu et le téléchargement passent par le proxy authentifié |
| Relecture/correction/validation humaine | VÉRIFIÉ | écrans de validation, journal d'audit `qui a validé quoi et quand`, garde-fous CI « ROUGE puis VERT » sur l'attribution |
| Distinguer non vérifié / incertain / validé / remplacé (révision) | VÉRIFIÉ | badge « Non vérifiée », états de fiche, test `e2e/recherche-badge.spec.ts` |
| Rien d'unvalidé ne peut passer pour une instruction de fabrication approuvée | VÉRIFIÉ (socle) | les rapports générés portent l'état de validation ; **acceptation atelier (humaine) PENDING** |
| Édition concurrente sans perte silencieuse | VÉRIFIÉ (socle) | détection de conflit/optimistic locking + dédup ; **scénario 3 postes simultanés : CI `e2e`** |
| Corrections après validation | VÉRIFIÉ | tests de correction post-validation (réouverture → nouvelle validation, révisions) |
| Téléchargement des originaux | VÉRIFIÉ (local) / CI (objet) | proxy authentifié `…/telecharger`, `Content-Disposition: attachment` ; téléchargement depuis un autre poste prouvé au §3.2 d |
| Stockage objet S3/MinIO, clés, quarantaine, intégrité | ÉCRIT | §1 ; exécution contre MinIO : CI `integration` |
| Alternatives R2 | NON TRAITÉ (par conception) | un seul fournisseur supporté localement ; R2 ne sera validé que sur demande explicite |
| Sauvegarde / restauration | ÉCRIT + PENDING | test automatisé présent ; **drill humain indépendant et destination de production PENDING** |
| Fonctionnement hors ligne après installation | ÉCRIT | aucun téléchargement implicite au démarrage ; modèles lus sur disque (`<data>/modeles`) ; **test « réseau externe bloqué » : PENDING** (à exécuter sur le serveur) |
| Déploiement local serveur/VM (non-root, endpoints privés, TLS, secrets, liveness/readiness) | ÉCRIT / CI | compose durci, `--verifier`, `/health` (file, jobs, sauvegarde, espace disque, identifiants S3) ; boot documenté testé par `tests/test_compose_web_boot.py` (9P) ; TLS via `docs/TLS.md` |
| Scénario 3 postes (import + recherche + 2 éditions + session expirée + redémarrage + opération réservée admin) | ÉCRIT / CI | specs e2e + tests multi-processus ; exécution navigateur : CI `e2e` |
| Validation ML calibrée | **NON exigée** | décision maintenue : la validation humaine obligatoire satisfait le besoin produit. Aucun « gate » de calibration ML en production ; l'incertitude non calibrée est affichée comme telle, jamais comme une confiance chiffrée |

---

## 7. Commit, branche, PR et exécutions CI

* Branche de session : `arena/7da80c2f-seamtech-search`.
* Commit de ces travaux : **rempli juste après le commit** (voir l'en-tête du
  fichier `docs/verite_terrain/TRACABILITE_LIVRAISON.md`, section « Revue
  indépendante 2026-10-07 », qui porte le SHA exact et les liens).
* Pull request : ouverte depuis cette même branche (lien dans la traçabilité).
* Exécutions CI : les jobs `backend`, `frontend`, `docker`, `integration`,
  `e2e`, `sauvegarde`, `recette-corpus-reel`, `recette-locale`, `ocr`,
  `securite-dependances` sont déclenchés par le push ; les liens exacts sont
  consignés dans la traçabilité. Les preuves qui **ne peuvent venir que de là**
  sont : MinIO réel (§4), restauration hors-site (§4), navigateur Playwright
  (§5) et pile à deux conteneurs (§3.2).

Aucune de ces preuves CI n'est présentée dans ce document comme déjà acquise :
elles le seront quand le run correspondant au SHA ci-dessus sera vert.
