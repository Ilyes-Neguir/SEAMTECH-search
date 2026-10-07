# Écarts trouvés et correctifs livrés — 2026-10-06

Ce document répond à la première exigence de la mission (« établir la liste des
écarts entre la documentation et le code, puis corriger »). Il ne remplace pas
`docs/RELEASE_CANDIDATE_CHECKLIST.md` (cases à cocher sur le poste cible) ni le
rapport de fin : il dit **ce qui était faux, ce qui a été fait, et ce qui reste
ouvert**, preuves à l'appui.

Chaque ligne porte un état :

* **CORRIGÉ** — le code et/ou la documentation sont modifiés, un test mesure le
  comportement ;
* **OUVERT** — constaté, non corrigé, avec la raison et ce qu'il faudrait pour
  le fermer. Un point OUVERT n'est jamais présenté comme résolu.

## 1. Écarts de durabilité des imports (workstream « Redis »)

| # | Écart constaté | État | Preuve / remarque |
|---|---|---|---|
| E-1 | La documentation affirmait « Background worker thread (not separate service) » : un redémarrage de l'API tuait les imports en cours et leur tâche Redis restait dans `processing` pour toujours | **CORRIGÉ** | Service `worker` (`python -m seamtech_search.worker_service`) dans `docker-compose.yml` ; reprise par revendication à TTL ; `tests/test_worker_process.py` tue un VRAI processus (SIGKILL) et exige la reprise sans doublon |
| E-2 | Le lot multi-dossiers tournait dans un fil `daemon` du processus web : interrompu = lot bloqué « en_cours » à jamais, aucun état terminal | **CORRIGÉ** | `kind: "lot"` remis à la file Redis ; le worker appelle `executer_lot`, qui ne reprend que les lignes `en_attente` ; test `test_lot_interrompu_reprend_sans_refaire_les_dossiers_traites` (3 dossiers, 2 contenus ⇒ 2 fiches, 0 doublon) |
| E-3 | Le prédicat d'annulation était **inversé** dans `_process_lot_task` : tout lot durable était annulé au premier dossier | **CORRIGÉ** | Défaut trouvé par le test ci-dessus (le lot sortait `cancelled`) ; `doit_continuer` renvoie désormais `not is_job_cancelled(...)` |
| E-4 | Aucun état terminal ni raison lisible pour un job dont le worker meurt | **CORRIGÉ** | Colonnes `attempts`, `claimed_by`, `heartbeat_at`, `failure_reason`, `durability` (migration 020) ; lettre morte avec raison ; `/health` expose `queue` et `jobs` |
| E-5 | Une base **antérieure à la 020** ne redémarrait plus : `initialize()` créait un index sur `heartbeat_at` avant que la colonne n'existe (`UndefinedColumn`) | **CORRIGÉ** | Index partiel créé sous garde `information_schema` ; migration 020 ajoute colonne puis index ; test `test_base_anterieure_a_la_020_demarre_puis_se_migre` |
| E-6 | « Accepté » ne distinguait pas un job réellement en file d'un job en mémoire | **CORRIGÉ** | `import_jobs.durability` + `durable`/`durability` dans la réponse ; en production `SEAMTECH_REQUIRE_DURABLE_QUEUE=true` ⇒ **503 sans création de job** quand Redis est absent |
| E-7 | Persistance Redis implicite (AOF sans fsync explicite, pas de redémarrage testé) | **CORRIGÉ** | `--appendonly yes --appendfsync everysec`, volume `redis-data` ; test `test_redemarrage_de_redis_conserve_la_tache_acceptee` (serveur Redis réel tué puis relancé : tâche ET revendication conservées) |
| E-8 | Les tests de file durable n'existaient pas dans la CI : ils auraient pu être verts en sautant | **CORRIGÉ** | Marqueur `redis_queue` + étape CI dédiée avec garde-fou `passed > 0` **et** `skipped == 0` ; `tests/test_selection_ci.py` fige la commande |
| E-9 | Aucun plafond de tentatives/backoff distinct de l'ancien comportement ; pas de visibilité des workers vivants | **CORRIGÉ** | `SEAMTECH_MAX_TASK_ATTEMPTS` (3), backoff `2**n`, `seamtech:worker:*` et `workers_vivants` exposés par `/health` |

## 2. Écarts de stockage objet

| # | Écart constaté | État | Preuve / remarque |
|---|---|---|---|
| E-10 | **R-10 de l'audit** : après envoi, la vérification était une preuve d'**existence** (`HeadObject`), jamais d'**intégrité** | **CORRIGÉ** | Chaque envoi dépose `Metadata{seamtech-sha256, seamtech-taille}` ; la relecture compare **taille + empreinte**. Volontairement indépendant de l'ETag : un ETag multipart (ou R2) n'est pas un MD5 — c'est le piège que la mission signale. Un objet sans métadonnée applicative est accepté **sur la taille seule** et le dit (`verification: taille_seule`). Le **chemin relatif d'origine** est en outre conservé avec l'objet (`seamtech-chemin`, encodé en pourcentage parce qu'un en-tête S3 n'accepte pas d'octets non-ASCII) : un objet reste traçable jusqu'à son dossier sans consulter la base — exigence « conserver le nom et le chemin d'origine » |
| E-11 | La purge locale était autorisée par la seule absence d'exception d'envoi | **CORRIGÉ** | `verifier_integrite` renvoie `(ok, détail)` ; `artifact.verified` ne peut plus être vrai sur une simple existence ; test `test_echec_partiel_interdit_la_purge_locale` |
| E-12 | Identifiants applicatifs = identifiants **root** MinIO (R-7), pas de compte restreint | **OUVERT** | Non corrigé : créer l'utilisateur applicatif exige un MinIO vivant (`mc admin user add` + politique de bucket) et je ne peux pas l'exécuter ici (ni MinIO, ni Docker). À faire sur le poste cible ; la variable existe déjà (`SEAMTECH_S3_ACCESS_KEY` ≠ `MINIO_ROOT_USER`), c'est un changement de `.env` + provisionnement |
| E-13 | Image MinIO plus distribuée par aucun registre (R-1) | **OUVERT (documenté + scripté)** | `scripts/construire_image_minio.sh` reconstruit l'image depuis les sources du tag épinglé ; ce script exige Docker + Go + GitHub, absents ici ⇒ **non exécuté dans ce bac à sable**, à exécuter sur la machine cible (case 1.6 de la checklist) |
| E-14 | Compatibilité R2/AWS jamais prouvée par exécution (R-13) | **OUVERT** | Aucun test réel contre un second fournisseur. La demande de la mission était : ne pas installer deux composants en local ; c'est respecté. La validation R2 reste une décision séparée |
| E-15 | `storage_backend=local` n'empêche aucun envoi (R-3) | **OUVERT (figé par test)** | Comportement inchangé et **volontairement figé** : un futur backend local ne pourra pas être ajouté en silence (`test_storage_backend_local_ne_desactive_pas_le_client_objet`). Ce n'est pas un correctif, c'est un garde-fou |

## 3. Écarts de documentation

| # | Écart constaté | État | Preuve / remarque |
|---|---|---|---|
| E-16 | README : « Single-user token auth, no RBAC » — **faux** : comptes nominatifs `utilisateur` + sessions `session_ui`, deux rôles, `/auth/utilisateurs*` réservé à `administrateur` (`_exiger_administrateur`) | **CORRIGÉ** | README remplacé par la description réelle (et par ce qui manque : pas d'ACL par projet, pas de RBAC « entreprise ») |
| E-17 | README : « No separate worker service… worker is thread inside web » | **CORRIGÉ** | README, `docs/DEPLOYMENT.md` et `.env.example` décrivent le service `worker`, ses variables et ses commandes |
| E-18 | `docs/FILE_DURABLE.md` était **cité par le code** (migration 020, `worker.py`) mais n'existait pas | **CORRIGÉ** | Fichier écrit : promesses, non-promesses (« pas d'*exactly once* »), anatomie de la file, exploitation, limites |
| E-19 | README : `/imports/dossier/lot` décrit comme « in-process thread, no Redis » | **CORRIGÉ** | Décrit comme tâche durable annulable ; `X-SEAMTECH-BACKGROUND: false` reste synchrone |
| E-20 | Checklist RC : 19 migrations, `019_recherche_dimension 33` | **CORRIGÉ** | 20 lignes, `020_file_durable 33` ; tests `test_ocr_comportement`, `test_stockage_release_candidate` et `test_recette_corpus_reel` alignés |
| E-21 | `docs/RELEASE_CANDIDATE_CHECKLIST.md` n'avait aucune case pour le worker, la persistance Redis, la reprise après kill, le refus 503, l'annulation de lot | **CORRIGÉ** | Nouvelles cases 4.5–4.7, section 7 bis (7b.1–7b.7), case 11.8 |
| E-22 | Le job CI `integration` démarrait `web` et `frontend` mais **jamais le service `worker`** : la pile documentée à deux processus n'était donc prouvée par aucune exécution continue | **CORRIGÉ** | `tests/test_compose_partage_worker.py` lance `docker compose up -d --build web worker`, téléverse par l'API, exige `durability == "durable"`, attend que le **conteneur worker** termine, puis vérifie partage des volumes (brouillon, rapports, quarantaine, modèles), worker non-root, archive intacte et purge après envoi vérifié ; déclaré dans `INVENTAIRE_S3_REEL` |

## 4. Ce qui n'a PAS été traité (et pourquoi)

Ces points restent **ouverts** ; ils figurent dans le rapport de fin comme portes
à franchir avant toute promesse de production.

1. **MinIO réel** : aucun conteneur Docker dans cet environnement (pas de
   démon, pas de toolchain Go, registres bloqués). Tout ce qui touche le stockage
   objet est prouvé sur doubles (`MagicMock` boto3) et par lecture de code —
   jamais contre un MinIO vivant. La CI le fait (job `integration`, job
   `sauvegarde`) ; ces jobs ne tournent pas ici.
2. **Comptes applicatifs S3 restreints (E-12)** : nécessite MinIO vivant.
3. **Recette navigateur à trois postes** (un importe pendant qu'un autre
   cherche/télécharge, deux éditions concurrentes d'une même fiche, expiration
   de session en cours de travail, opération d'administration refusée à un
   opérateur) : aucun navigateur ni second poste ici. Les garde-fous unitaires
   existent (`tests/test_comptes.py`, `tests/test_optimistic_locking*.py`),
   l'épreuve d'atelier reste à faire.
4. **Sauvegarde/restauration indépendante** : le test d'aller-retour passe
   (`tests/test_sauvegarde_restauration.py`, suite PostgreSQL verte), mais un
   exercice de restauration **humain**, sur une machine distincte, avec la
   destination de sauvegarde de production, n'a jamais été réalisé.
5. **Hors-ligne réel** : rien ici ne prouve qu'un poste sans Internet sert les
   parcours métier (l'installation des poids ML et l'image MinIO exigent un
   accès réseau **explicite**, en amont). À tester réseau coupé sur le poste
   cible.
6. **Échelle et pertinence** : le corpus du dépôt (7 ZIP) n'est pas l'archive
   réelle ; aucune mesure de recherche n'a été validée par un humain sur des
   requêtes réelles.
7. **Verrouillage de calibration (F-2)** : inchangé, `calibre: false`.

## 5. Comment ces correctifs sont vérifiés

```bash
# 1. Suite sans service (SQLite, pas de réseau)
pytest -q -m "not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue"

# 2. Suite PostgreSQL réelle
SEAMTECH_TEST_DATABASE_URL=postgresql://seamtech@127.0.0.1:5433/seamtech_search \
  pytest -q -m "postgres and not perf and not redis_queue"

# 3. File durable : Redis réel + PostgreSQL réel (+ un vrai processus tué)
SEAMTECH_TEST_REDIS_URL=redis://:motdepasse@127.0.0.1:6379/1 \
SEAMTECH_TEST_DATABASE_URL=postgresql://seamtech@127.0.0.1:5433/seamtech_search \
  pytest -q -m "redis_queue"
```

Résultats du 2026-10-06 sur ce bac à sable (PostgreSQL 16 + pgvector réels,
Redis 7.2.5 réel) : **821 passés / 3 sautés**, **226 passés / 2 sautés**,
**25 passés / 0 sauté**. Aucun test n'a été supprimé ni rendu « vert » en le
sautant ; les 2 sauts de la suite PostgreSQL sont des tests `s3` et `sauvegarde`
qui exigent un bucket vivant (exclus par marqueur, exécutés par les jobs CI
dédiés).

## 6. Écarts trouvés et corrigés le 2026-10-07 (passe de revue indépendante)

Numérotation suite du registre (§3) : les deux écarts ci-dessous sont **E-23** et
**E-24**, dans l'ordre du fichier (le registre principal s'arrête à **E-22**,
ajouté ci-dessus).

Les mesures de la veille sont rejouées ici : **833 passés / 3 sautés / 299
désélectionnés** (sans service), **226 passés / 2 sautés** (PostgreSQL réel),
**37 passés / 0 sauté** (file durable, réels Redis + PostgreSQL), `ruff check .`
propre. Deux défauts réels ont été trouvés **par les tests que la revue
demandait** — c'est-à-dire après les mesures du 2026-10-06 :

| # | Écart | Gravité | Correctif | Preuve |
|---|---|---|---|---|
| E-23 | `SEAMTECH_STORAGE_VERIFY_REREAD` était indexé après un test sur `SEAMTECH_REQUIRE_DURABLE_QUEUE` : la configuration du compose documenté (durabilité exigée, autre variable absente) levait `KeyError` et le conteneur `web` **ne démarrait plus** | bloquante (démarrage) | surcharge corrigée sur la bonne variable + variable déclarée dans `docker-compose.yml` et `.env.example` | `tests/test_compose_web_boot.py` (9 passés) l'a attrapé ; régression dédiée dans `tests/test_config.py` |
| E-24 | `max_task_attempts` offrait une tentative de plus que le réglage (`attempt < max` au lieu de `attempt + 1 < max`) : un opérateur réglant 3 tentatives en obtenait 4, et la lettre morte arrivait un cran trop tard | moyenne (compréhension opérateur, charge) | garde corrigée et documentée comme un nombre **total** de tentatives | `tests/test_relais_file_base.py::test_epuisement_des_tentatives_va_en_lettre_morte_avec_raison_entre_processus` (réel : 2ᵉ tentative refusée, `max_task_attempts=1` ⇒ lettre morte immédiate) |

Nouveaux tests ajoutés dans cette passe (tous exécutés, sauf mention) :
`tests/test_relais_file_base.py` (5, **vrais processus worker** tués puis
relancés), `tests/test_partage_web_worker.py` (3, serveur web + worker en
processus séparés et contrat de volumes du compose),
`tests/test_compose_partage_worker.py` (1, pile Compose réelle — **sauté ici
faute de Docker**, exécuté par le job CI `integration`),
`tests/test_verification_integrite.py` (10, sur un magasin S3 en mémoire qui
conserve les vrais octets). Détail complet : `docs/verite_terrain/REVUE_INDEPENDANTE_2026-10-07.md`.
