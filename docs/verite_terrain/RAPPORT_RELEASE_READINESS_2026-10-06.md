# Rapport de préparation à la mise en service — 2026-10-06

Périmètre : mission « Finish SEAMTECH Search for a reliable local production
release ». Ce rapport remplace toute affirmation d'état antérieure non
accompagnée de preuve. Les écarts corrigés sont détaillés dans
`docs/verite_terrain/ECARTS_ET_CORRECTIFS_2026-10-06.md`.

## 1. Verdicts séparés (à lire séparément)

| Verdict | Statut | Ce qui le fonde | Ce qui l'empêcherait de tenir |
|---|---|---|---|
| **Prêt pour essais développeur** | **OUI** | 3 suites vertes le 2026-10-06 : 821 passés (sans service), 226 passés (PostgreSQL 16 + pgvector réels), 25 passés (Redis 7.2.5 réel + PostgreSQL réel, dont un vrai processus worker tué par SIGKILL). `ruff check .` propre. Migrations 001→020 rejouables, base d'avant la 020 testée | Rien de bloquant connu |
| **Prêt pour pilote atelier contrôlé** | **NON — 4 portes à franchir** | Le socle technique tient (files durables, reprise, stockage vérifié, sauvegarde testée) **sur doubles et en CI**, pas encore sur l'infrastructure cible | (1) image MinIO reconstruite sur le poste ; (2) comptes S3 applicatifs restreints ; (3) recette navigateur à 3 postes ; (4) un import réel + une recherche réelle jugés par un humain |
| **Prêt pour production** | **NON** | — | Tout ce qui précède, plus : restauration indépendante exécutée, destination de sauvegarde hors site en service, fonctionnement hors-ligne prouvé réseau coupé, échelle mesurée sur l'archive réelle, calibration non verrouillée |

Aucune de ces trois colonnes ne doit être lue comme une promesse au-delà de ce
qui est écrit.

### 1.1 Addendum du 2026-10-07 (passe de revue indépendante)

Les mesures ci-dessus restent vraies pour l'état du 2026-10-06. Depuis, la même
sélection a été rejouée et **étendue** ; chiffres les plus récents, avec la
comptabilité `skipped` / `deselected` corrigée (ce sont deux choses différentes :
`deselected` = écarté par la sélection `-m`, `skipped` = demandé puis inapte) :

| Sélection | 2026-10-06 | 2026-10-07 (après correctifs) |
|---|---|---|
| sans service | 821 passés / 3 sautés | **833 passés / 3 sautés / 299 désélectionnés** |
| PostgreSQL réel | 226 passés / 2 sautés | **226 passés / 2 sautés** |
| file durable (Redis + PostgreSQL réels) | 25 passés / 0 sauté | **37 passés / 0 sauté** (dont 5 tests à **vrais processus** worker tués par `SIGKILL`) |
| S3 vivant | 0 exécuté (aucun MinIO) | **0 exécuté : 29 sautés** — besoin toujours d'un job CI/réel |
| Front-end | non mesuré | `npx tsc --noEmit` **exit 0** (11 specs e2e couvertes), `npx next build` **exit 0** |
| Navigateur Playwright | non exécutable ici | **non exécutable ici** (téléchargement du navigateur bloqué) — CI `e2e` |

Deux défauts réels ont été trouvés puis corrigés dans cette passe (E-23, E-24 du
registre d'écarts) : un démarrage de conteneur `web` cassé par une variable
d'environnement mal lue, et un compteur de tentatives offrant un essai de plus
que le réglage. Détail complet, y compris ce qui n'a **pas** pu être exécuté et
pourquoi : `docs/verite_terrain/REVUE_INDEPENDANTE_2026-10-07.md`.

## 2. Ce qui a changé dans cette mission (preuves d'exécution)

### 2.1 Durabilité des imports — le point le plus grave corrigé

* Un **service `worker` séparé** exécute désormais les imports ; `web` les
  accepte seulement (`SEAMTECH_WEB_WORKER_ENABLED=false`). Redémarrer `web` ne
  tue plus un import en cours.
* **Refus honnête** : avec `SEAMTECH_REQUIRE_DURABLE_QUEUE=true`, Redis
  injoignable ⇒ **503, aucun job créé**. Le repli mémoire de développement reste
  possible et s'annonce (`durability: "process_memory"`), jamais déguisé en
  acceptation durable.
* **Reprise mesurée** : `tests/test_worker_process.py` démarre un vrai worker,
  tue son PID par `SIGKILL` en plein import, attend l'expiration de la
  revendication, relance un second worker et exige : job `completed`,
  `attempts ≥ 2`, **6 documents indexés une seule fois** (aucun doublon).
* **Redémarrage Redis réel** : `tests/test_file_durable_postgres.py` tue un
  serveur Redis dédié (AOF) et le relance : la tâche en attente **et** la
  revendication de la tâche en cours sont toujours là.
* **Lots multi-dossiers** : repris là où ils s'étaient arrêtés, annulables entre
  deux dossiers. Ce test a révélé et fait corriger un défaut réel : le prédicat
  d'annulation était inversé, si bien que **chaque lot durable était annulé au
  premier dossier**.

### 2.2 Intégrité du stockage objet (R-10 de l'audit)

La vérification après envoi comparait l'existence, jamais le contenu. Elle
compare maintenant **taille + SHA-256** lus dans les métadonnées applicatives de
l'objet, et **jamais l'ETag** (un ETag multipart, ou R2, n'est pas un MD5 : le
piège que la mission demandait explicitement d'éviter). Un objet sans empreinte
applicative est accepté **sur la taille seule**, et le dit. La purge locale reste
interdite tant que l'intégrité n'est pas établie.

### 2.3 Défaut de démarrage corrigé (aurait bloqué une mise à jour en production)

`initialize()` créait un index sur `heartbeat_at` **avant** que la migration 020
n'ajoute la colonne : une base existante ne redémarrait plus
(`UndefinedColumn`, mesuré). Corrigé et prouvé par un test qui fabrique une base
« d'avant la 020 » puis exige démarrage + migration.

### 2.4 Documentation remise en accord avec le code

* « Single-user token auth, no RBAC » était **faux** (comptes nominatifs, rôles
  `operateur`/`administrateur`, gestion des comptes réservée à
  l'administrateur) — corrigé, et l'absence d'ACL par projet est écrite.
* « worker is thread inside web » — remplacé partout (README, `DEPLOYMENT.md`,
  `.env.example`, checklist).
* `docs/FILE_DURABLE.md` était cité par le code mais n'existait pas ; il existe,
  et distingue les promesses des **non-promesses** (pas d'*exactly once*).

## 3. État vérifié, chiffre par chiffre

| Suite | Commande | Résultat 2026-10-06 |
|---|---|---|
| Sans service (SQLite) | `pytest -m "not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue"` | **821 passés, 3 sautés** |
| PostgreSQL 16 + pgvector réels | `pytest -m "postgres and not perf and not redis_queue"` | **226 passés, 2 sautés** |
| File durable (Redis 7.2.5 réel, worker réel) | `pytest -m "redis_queue"` | **25 passés, 0 sauté** |
| Lint | `ruff check .` | aucun diagnostic |
| Compilation | `python -m compileall -q seamtech_search` | OK |

Sauts justifiés : 2 tests OCR qui exigent `tesseract` (non installé),
2 tests qui exigent un bucket S3 **vivant** (exécutés par les jobs CI
`integration` et `sauvegarde`), 1 test de file durable désélectionné par
marqueur dans la suite « sans service » — il tourne dans la suite 3.

## 4. Portes restantes, par responsable

### Commanditaire (décisions)

1. **Destination de sauvegarde hors site** : où (bucket, serveur distinct) et
   qui la surveille. Tant qu'elle n'existe pas, une sauvegarde locale sur le
   même serveur n'est pas une protection — c'est déjà écrit par `/health`
   (`backup.copie_distante: null`).
2. **Choix du fournisseur objet** : MinIO reconstruit localement (aucun registre
   ne le distribue plus) ou R2/AWS. Deux fournisseurs ne doivent pas être
   installés.
3. **Calibration ML** : reste verrouillée (`calibre: false`) faute de 300–500
   fiches validées. Ce n'est pas un défaut du logiciel, c'est une décision à
   assumer : aucune confiance calibrée n'est affichée.

### Atelier (épreuves à faire faire par de vrais utilisateurs)

4. **Recette navigateur à 3 postes** : un importe pendant qu'un autre cherche et
   télécharge ; deux personnes corrigent la même fiche ; expiration de session
   en cours de travail ; un opérateur tente une opération d'administration
   (doit être refusée). Aucun de ces parcours n'a été joué ici (pas de
   navigateur, pas de second poste).
5. **Import de dossiers réels** : les 7 ZIP du dépôt ne sont pas l'archive de
   l'atelier. Mesurer la durée, les formats réellement rencontrés, les échecs
   légitimes, et vérifier que chaque fichier soumis est compté.
6. **Recherche jugée par un humain** : les requêtes de
   `docs/verite_terrain/JEU_REQUETES_REELLES.md` doivent être passées sur
   l'archive réelle et jugées pertinentes/inutiles, requête par requête.

### Serveur de production (opérations)

7. **Reconstruire l'image MinIO** : `bash scripts/construire_image_minio.sh`
   (Docker + Go + GitHub). Non exécutable ici ; c'est la case 1.6 de la
   checklist. Sans elle, `docker compose up` ne trouve pas d'image.
8. **Comptes S3 applicatifs restreints** : créer un utilisateur dédié au bucket
   (jamais les identifiants root) et renseigner `SEAMTECH_S3_ACCESS_KEY` /
   `SEAMTECH_S3_SECRET_KEY`. `/health` indique déjà `s3_credentials: root_like`
   tant que ce n'est pas fait — l'information est là, l'action reste à mener.
9. **Exercice de restauration indépendant** : restaurer dans un environnement
   isolé et vérifier connexion, projets, historique de validation, recherche,
   téléchargement des originaux et des rapports, intégrité d'un fichier
   représentatif, réconciliation des jobs en attente (le test automatisé existe,
   l'exercice humain non).
10. **Fonctionnement hors-ligne** : couper l'accès Internet et rejouer les
    parcours métier. Les poids ML et l'image MinIO doivent avoir été installés
    **explicitement** avant ; aucun téléchargement implicite ne doit se produire
    au runtime.
11. **Dimensionnement** : mesurer le volume de l'archive réelle + les artefacts
    générés + la croissance + la rétention des sauvegardes, et vérifier l'espace
    libre (`/health` remonte `min_free_bytes` et l'espace disponible).

## 5. Ce que ce rapport ne prétend pas

* Il ne prétend **pas** que la production est prête : quatre portes du pilote et
  cinq portes de production sont ouvertes (ci-dessus).
* Il ne prétend **pas** que le stockage objet est vérifié contre MinIO : toute
  la preuve S3 de ce bac à sable est faite sur doubles (boto3 remplacé), la CI
  exécute les épreuves réelles.
* Il ne prétend **pas** que la recherche est pertinente sur des requêtes réelles
  non jugées.
* Il ne prétend **pas** « exactement une fois » : la livraison Redis est
  *au moins une fois*, et c'est l'idempotence (garde de job terminé, clé
  d'idempotence de dossier) qui évite les doublons — mesuré, pas supposé.
