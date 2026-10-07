# Checklist de release candidate — SEAMTECH Search

**Photographie historique** : les sections opérateur ci-dessous ont été amorcées sur `main` `f8befb5` (25/09) et comportent des sorties encore à rejouer sur la machine cible. **Mise à jour de production-readiness** : base attendue `a7ffe7d` (PR #32) vérifiée le 29/09 ; preuves, commandes et SHA de cette mission en §19. Les éléments historiques ne valent pas preuve sur le VPS01 du commanditaire.

À quoi sert ce document : c'est la **liste des cases à cocher sur le poste cible**, dans
l'ordre, avec pour chaque case **la commande exacte**, **la sortie attendue** et **la
preuve à archiver**. Une case non cochée est un « non prouvé », jamais un « sans doute
bon ». Reportez la sortie brute dans la colonne *Preuve* : ce document est destiné à être
rempli, daté et signé.

> **Ce qui reste bloqué et ne peut donc PAS être coché ici**
>
> - **Échelle de production non mesurée** : le corpus fourni de 7 ZIP est traité par la recette CI, mais les 200 dossiers/volumes du poste cible et leur durée d'import restent à mesurer sur l'infrastructure du commanditaire.
> - **F-2 bloqué — mais SEULEMENT la promesse de confiance calibrée** : les 300 à 500
>   fiches validées nécessaires au réentraînement ne sont pas disponibles, donc
>   `calibre: false` et aucune confiance calibrée n'est annoncée à l'opérateur. Cela ne
>   bloque **pas** la mise en service de l'extraction assistée (champs extraits + file de
>   validation) : l'atelier reste la référence, et c'est lui qui valide. À rejouer quand
>   les fiches validées existeront — pas avant.
> - **Métriques de production indisponibles** : tous les chiffres cités viennent de la CI
>   ou du poste de développement, jamais d'une exploitation réelle.
> - **Fixtures du dépôt non représentatives** : `sample_data/` = 1 fiche client réelle +
>   quelques fichiers synthétiques. Ni l'échelle, ni la variété de noms, ni les scans de
>   l'archive réelle. Un import de test vert ici ne dit **rien** du jour J.
>
> Documents liés : `docs/verite_terrain/MISE_EN_SERVICE.md` (chemin validé),
> `docs/verite_terrain/RUNBOOK_RESTAURATION.md` (restauration),
> `docs/verite_terrain/QUE_FAIRE_SI.md` (incidents), `docs/CHECKLIST_POSTE_WINDOWS.md` (poste Windows),
> `docs/ARRIVEE_ARCHIVE.md` (jour J de l'archive),
> `docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md` (stockage objet).

---

## 1. Prérequis machine

| # | Contrôle | Commande | Attendu | Preuve |
|---|---|---|---|---|
| 1.1 | Docker + Compose v2 | `docker --version && docker compose version` | deux versions affichées | |
| 1.2 | Espace disque libre | `df -h .` (Windows : `Get-PSDrive C`) | **≥ 50 Go — chiffre PROVISOIRE de départ** ; 5 Go pour une recette | |
| 1.3 | RAM / cœurs | `free -h`, `nproc` | **≥ 8 Go et ≥ 2 cœurs — chiffres PROVISOIRES de départ** | |
| 1.4 | Ports libres | `ss -lntp \| grep -E ':(3000\|8000\|9000\|9001\|6379\|5433)'` | aucune ligne | |
| 1.5 | Git + horloge système | `git --version`, `date -u` | horloge juste (les clés de sauvegarde sont horodatées UTC) | |
| 1.6 | **Image MinIO disponible** — plus aucun registre ne la distribue | `bash scripts/construire_image_minio.sh` | `Image quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z prête` | |
| 1.7 | (option Windows) Docker Desktop + WSL 2 | `docs/CHECKLIST_POSTE_WINDOWS.md` | checklist Windows cochée | |

> **1.2 / 1.3 — ce que « provisoire » veut dire (revue du 2026-10-07).** Ces chiffres
> sont des points de départ à confirmer par la MESURE sur le serveur retenu, jamais une
> taille validée. Le volume doit couvrir, ENSEMBLE : les originaux de l'archive (lecture
> seule, taille de l'archive livrée), les copies stockées (bucket MinIO), l'espace de
> traitement (OCR, rendus PDF, fichiers temporaires), la quarantaine, la base PostgreSQL
> et ses index, les sauvegardes (dump + archive), et la croissance — prévoir une marge.
> Le besoin mémoire/cœurs se mesure pendant le pilote (import réel + recherche + validation
> concurrente), puis se fixe. Voir `docs/verite_terrain/DECISION_MATERIEL.md`.
>
> 1.6 exige Docker **et** un accès réseau à GitHub le temps de la compilation (voir
> `docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md` §4). À faire **une fois**, avant le
> premier `docker compose up`. Sur un poste sans réseau : exporter l'image depuis un
> poste connecté (`docker save` / `docker load`).

## 2. Variables obligatoires

`cp .env.example .env`, puis remplir. Compose **refuse de démarrer** si l'une des
variables `:?` manque — c'est voulu.

| Variable | Obligatoire | Valeur | Contrôle |
|---|---|---|---|
| `POSTGRES_PASSWORD` | oui | secret long | `docker compose config --quiet` |
| `SEAMTECH_AUTH_TOKEN` | oui | secret long (serveur↔serveur, jamais envoyé au navigateur) | idem |
| `SEAMTECH_UI_PASSWORD` | oui au 1ᵉʳ démarrage | compte de **secours** ; **à vider** dès que les comptes nominatifs existent | idem |
| `SEAMTECH_SESSION_SECRET` | oui | secret long (signature du cookie) | idem |
| `REDIS_PASSWORD` | oui | secret long | idem |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | oui | **ne pas laisser `minioadmin`** — identifiants d'ADMINISTRATION, utilisés par le provisionnement seul | idem |
| `SEAMTECH_S3_ACCESS_KEY` / `SEAMTECH_S3_SECRET_KEY` | **oui** | identité APPLICATIVE **dédiée** (aucun repli administrateur ; créée par `scripts/provisionner_stockage.sh`) | `docker compose config --quiet`, `/health` → `s3_credentials: dedie` |
| `SEAMTECH_BACKUP_ACCESS_KEY` / `SEAMTECH_BACKUP_SECRET_KEY` | oui pour l'envoi hors-site | identité **distincte** de l'identité applicative, limitée au bucket de sauvegarde | `python -m seamtech_search.sauvegarde sauver …` |
| `SEAMTECH_S3_BUCKET` | non (défaut `seamtech-documents`) | — | `/health` |
| `SEAMTECH_BACKUP_BUCKET` | non (défaut `seamtech-backups`) | — | provisionnement, sauvegarde |
| `SEAMTECH_ROOT_PATHS` | oui en production | dossier d'archive **monté en lecture seule** | `/health`, `/preview` |
| `SEAMTECH_BEHIND_TLS_PROXY` | selon exposition | `true` derrière un terminateur TLS | `docs/TLS.md` |
| `SEAMTECH_SESSION_HOURS` | non (12) | durée de session | — |

- [ ] **2.1** `docker compose config --quiet` ne renvoie **rien** (aucune variable manquante).
- [ ] **2.2** Aucune valeur `change-me` ne subsiste : `grep -n "change-me\|minioadmin" .env` → **vide**.
- [ ] **2.3** Le dossier d'archive est monté **en lecture seule** dans `web` (`:ro`) — RG13.
- [ ] **2.4** Le stockage est **provisionné AVANT tout usage normal** — pas seulement
      « `docker compose up` » : le provisionnement (`bash scripts/provisionner_stockage.sh`)
      précède le premier import réel, et il est VÉRIFIÉ (dépôt puis relecture d'un objet de
      test avec l'identité applicative ; `/health` le confirme). `bash scripts/provisionner_stockage.sh` a créé les
      buckets (`seamtech-documents`, `seamtech-backups`), activé leur versioning et créé les deux
      identités **restreintes**. Contrôles : `/health` renvoie `s3_credentials: "dedie"` (jamais
      `root_like`), et les variables `MINIO_ROOT_*` ne sont **pas** présentes dans
      `docker compose exec -T web printenv`.
- [ ] **2.5** Les journaux des services ne contiennent aucun secret :
      `docker compose logs --no-color web worker | grep -F "$(grep -E 'SEAMTECH_(S3|BACKUP)_SECRET_KEY|MINIO_ROOT_PASSWORD' .env | cut -d= -f2)"` → **vide**.

## 3. Génération et protection des secrets

- [ ] **3.1** Générer (jamais de secret « inventé à la main », jamais réutilisé d'un autre service) :
  ```bash
  openssl rand -base64 48   # SEAMTECH_AUTH_TOKEN
  openssl rand -base64 48   # SEAMTECH_SESSION_SECRET
  openssl rand -base64 24   # SEAMTECH_UI_PASSWORD (secours)
  openssl rand -hex 24      # POSTGRES_PASSWORD / REDIS_PASSWORD / MINIO_ROOT_PASSWORD
  ```
- [ ] **3.1 bis** Les secrets qui finissent dans une **URL de connexion** (`POSTGRES_PASSWORD`,
      `REDIS_PASSWORD` — `docker-compose.yml` construit `SEAMTECH_DATABASE_URL` et
      `SEAMTECH_REDIS_URL` à partir d'eux) sont **URL-safe** : `openssl rand -hex 24` ou
      `openssl rand -base64 24 | tr '+/' '-_'`. Un `base64` brut peut contenir « / » : dans
      une URL Redis il devient le sélecteur de base, et la connexion échoue sans dire
      pourquoi. Si un secret existe déjà en base64 : l'encoder (`/` → `%2F`). L'application
      **refuse de démarrer** avec un message explicite quand un mot de passe d'URL n'est pas
      encodé (`AppConfig.validate_connection_urls`, testé dans `tests/test_config.py`).
- [ ] **3.2** Droits du fichier : `chmod 600 .env` → `ls -l .env` affiche `-rw-------`
      (Windows : n'autoriser que le compte d'exploitation).
- [ ] **3.3** `.env` **n'est pas** dans Git : `git check-ignore -v .env` renvoie une règle ;
      `git status --porcelain | grep -c '\.env$'` → `0`.
- [ ] **3.4** Aucun secret en argument de ligne de commande (visible dans `ps`) : les mots de
      passe de comptes se saisissent **sur STDIN** (`comptes.cli`).
- [ ] **3.5** Aucun secret dans les journaux : après le démarrage,
      `docker compose logs | grep -F "$(grep SEAMTECH_AUTH_TOKEN .env | cut -d= -f2)"` → **vide**
      (garde-fou automatisé : `tests/test_stockage_release_candidate.py`,
      `tests/test_garde_fous_preparation.py` §D).
- [ ] **3.6** Sauvegarde des secrets **hors machine** (coffre / enveloppe scellée) : sans eux,
      une restauration ne redonne pas l'accès.
- [ ] **3.7** **Décision du commanditaire 2026-09-29 : le dépôt reste public** ; aucune demande de changement de visibilité n'est lancée. Le PDF racine doublon a été retiré, mais la copie canonique client 7792-SO et l'historique Git demeurent publics. **NON MESURÉ** ici : état de révocation de tout ancien jeton GitHub côté propriétaire. Mesure opérateur requise : vérifier les jetons dans les paramètres du compte, sans les copier dans Git ni le chat.

## 4. Lancement Docker

```bash
docker compose up -d --build
docker compose ps
```

- [ ] **4.1** **6 services** : `postgres`, `minio`, `redis`, `web` (API), **`worker`**
      (exécute les imports — service SÉPARÉ, cf. 4.5/7b) et `frontend`.
- [ ] **4.2** Tous en `running (healthy)` (les healthchecks font foi ; `web` attend
      `service_healthy` sur les trois autres).
- [ ] **4.3** Ports publiés **en loopback uniquement** : `docker compose ps --format '{{.Name}} {{.Ports}}'`
      ne montre que des `127.0.0.1:…` (garde-fou : `tests/test_compose_hardening.py`).
- [ ] **4.4** Redémarrage automatique : `restart: unless-stopped` sur les **6** services.

| 4.5 | **Service `worker` démarré** (exécute les imports) | `docker compose ps worker` | `running` / `healthy` (le healthcheck lance `--verifier`) | |
| 4.6 | `web` n'exécute PAS d'import | `docker compose exec -T web printenv SEAMTECH_WEB_WORKER_ENABLED` | `false` | |
| 4.7 | File durable exigée | `docker compose exec -T web printenv SEAMTECH_REQUIRE_DURABLE_QUEUE` | `true` | |

## 5. Migrations

Les migrations tournent **au démarrage de l'API** (`lifespan` → `_initialize_schema`) :
il n'y a pas d'étape manuelle, mais il y a une **vérification obligatoire**.

- [ ] **5.1** Séquence complète appliquée, sans trou :
  ```bash
  docker compose exec -T postgres psql -U seamtech -d seamtech_search \
    -c "SELECT version FROM schema_migrations ORDER BY version;"
  ```
  Attendu : **21 lignes**, de `001_initial` à `021_revision_fiche`.
- [ ] **5.2** Version métier alignée :
  ```bash
  docker compose exec -T web python -c "from seamtech_search.schema_metier import VERSION_SCHEMA_METIER, TABLES_METIER; print(VERSION_SCHEMA_METIER, len(TABLES_METIER))"
  ```
  Attendu : `021_revision_fiche 33` (la 021 n'ajoute que la colonne `revision` à
  `fiche` — verrou optimiste —, aucune table).
- [ ] **5.3** Extensions réellement présentes : `/health` renvoie `vector`, `pg_trgm`, `unaccent`.
- [ ] **5.4** Rejeu sans effet : redémarrer `web` ne rejoue ni ne duplique aucune migration
      (garde-fou : `test_migrations_sequentielles_001_a_017_sur_base_vide` (identifiant historique conservé ; assertions vérifient `001..021`)).

> Une migration écrite mais **non enregistrée** est l'incident des Lots K puis M : elle
> fait échouer la sauvegarde (qui exige `VERSION_SCHEMA_METIER` dans `schema_migrations`).
> 5.1 + 5.2 sont donc non négociables.

## 6. Création du compte administrateur

```bash
docker compose exec web python -m seamtech_search.comptes.cli creer \
    --identifiant <prenom> --nom "<Nom complet>" --role administrateur
# mot de passe lu sur STDIN, jamais en argument
docker compose exec web python -m seamtech_search.comptes.cli lister
```

- [ ] **6.1** Au moins **un** compte nominatif `administrateur` actif.
- [ ] **6.2** Connexion réussie depuis l'interface avec ce compte (pas le compte de secours).
- [ ] **6.3** `SEAMTECH_UI_PASSWORD` **vidé** dans `.env` puis `docker compose up -d frontend` :
      le chemin « secours » est alors refusé (ses actions seraient attribuées à
      « secours », donc non traçables — limitation assumée).
- [ ] **6.4** Révocation vérifiée : `… comptes.cli sessions --identifiant <prenom>` puis
      `… revoquer-session --id-session N` → le cookie copié ne vaut plus rien.

## 7. Healthchecks

| # | Contrôle | Commande | Attendu |
|---|---|---|---|
| 7.1 | Vivacité front (seule route sans authentification) | `curl -fsS http://127.0.0.1:3000/api/health` | JSON de vivacité |
| 7.2 | Vivacité API | `curl -fsS http://127.0.0.1:8000/live` | 200 |
| 7.3 | Disponibilité API (base joignable) | `curl -fsS http://127.0.0.1:8000/ready` | 200 |
| 7.4 | Santé complète | `curl -fsS -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" http://127.0.0.1:8000/health` | `status: ok` |
| 7.5 | Stockage objet | même réponse | `s3_configured: true`, `versioning_available: true` (MinIO) — `false` = endpoint sans versioning (R2), `null` = **indéterminé, à investiguer** |
| 7.6 | File d'attente | même réponse | `redis_connected: true`, `upload_dead_letters: 0` |
| 7.7 | Documentation API fermée en production | `curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs` | `404` dès qu'un token est configuré |

- [ ] 7.1 – 7.7 verts, sortie de `/health` archivée.

## 7 bis. File d'attente durable (imports)

Contrat complet : `docs/FILE_DURABLE.md`. Ce qui se coche ici :

| # | Contrôle | Commande | Attendu | Preuve |
|---|---|---|---|---|
| 7b.1 | Le worker voit sa file | `docker compose exec -T worker python -m seamtech_search.worker_service --verifier` | `durable_ready: true`, code de sortie 0 | |
| 7b.2 | Persistance Redis (AOF) | `docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" config get appendonly` | `appendonly` → `yes` | |
| 7b.3 | **Preuve de survie au redémarrage** : accepter un import, redémarrer `web`, l'import se termine | `docker compose restart web` puis `docker compose logs --tail 20 worker` | l'import se termine, `import_jobs.status = completed` | |
| 7b.4 | Refus honnête sans Redis | `docker compose stop redis`, puis déposer un import | HTTP **503** « file d'attente durable indisponible » + **aucun** job créé (`SELECT count(*) FROM import_jobs`) | |
| 7b.5 | Reprise d'une tâche orpheline | `docker compose kill -s SIGKILL worker` pendant un import, puis `docker compose start worker` | journal « Reprise au démarrage : … » ; l'import se termine une seule fois (pas de doublon de documents) | |
| 7b.6 | Raisons d'échec lisibles | `SELECT id, attempts, claimed_by, failure_reason FROM import_jobs WHERE status='failed';` | chaque échec porte une raison non vide | |
| 7b.7 | Annulation d'un lot | `POST /lots/{id}/annuler` pendant un lot | lot `annule`, dossiers restants `en_attente` (reprenables), aucun dossier bloqué « en_cours » | |

## 8. Import de test

Utiliser **un dossier de recette**, jamais l'archive réelle (RG13 : l'archive ne se
modifie pas, et le jour J passe par `scripts/preflight_archive.py`).

- [ ] **8.1** Interface → écran **Dépôt** → dossier contenant au moins un PDF de fiche
      (ex. copie de `sample_data/CLIENT-7792-SO/`).
- [ ] **8.2** La fiche arrive en statut **`a_valider`** (RG3 : jamais validée automatiquement).
- [ ] **8.3** Les artefacts sont **dans le bucket** :
  ```bash
  curl -fsS -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" http://127.0.0.1:8000/imports/<id> \
    | python -m json.tool | grep -E '"upload_status"|"all_verified"|"object_key"'
  ```
  Attendu : `upload_status: "uploaded"`, `all_verified: true`, un `object_key` par fichier.
- [ ] **8.4** Rien en quarantaine : `docker compose exec web ls data/quarantine` → vide.
- [ ] **8.5** Téléchargement d'un artefact (`report_pdf`) : le fichier arrive et s'ouvre.
      Avec S3 configuré, la réponse attendue est un **200** : les octets passent par
      l'API, donc le téléchargement fonctionne depuis n'importe quel poste de l'atelier
      (`curl -i` ne montre **aucun** `Location`). Une **302** vers une URL présignée
      (valable 900 s) n'apparaît que si `SEAMTECH_S3_PUBLIC_ENDPOINT_URL` déclare un
      endpoint réellement joignable par les navigateurs. *Correctif du 2026-10-07 —
      rediriger vers l'endpoint interne du réseau des conteneurs cassait le
      téléchargement depuis un autre poste ; voir l'écart E-28.*
- [ ] **8.6** Idempotence : rejouer le même dépôt → `deja_traite`, aucun doublon créé.
- [ ] **8.7** **Import PROGRESSIF (revue du 2026-10-07)** : on n'importe PAS l'archive
      complète d'entrée. D'abord un **échantillon représentatif** (chaque type de document et
      de nommage de l'archive) puis les **cas difficiles** (scans anciens en OCR, fichiers
      sans texte, doublons, chemins accentués, gros PDF), et seulement ensuite le reste.
      À chaque palier : chaque fichier est **comptabilisé** (importés + en quarantaine +
      écartés = total du palier), les recherches attendues trouvent leurs fiches, et les
      originaux se téléchargent. Un palier rouge arrête la montée en charge.

## 9. Recherche de test

- [ ] **9.1** `curl -fsS -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" "http://127.0.0.1:8000/recherche?q=grand-voile"`
      → `nb_resultats ≥ 1`, `duree_ms` renseigné.
- [ ] **9.2** Depuis le contrat du 29/09, les fiches `a_valider` sont incluses par défaut et portent le badge « Non vérifiée » ; `inclure_a_valider=false` les retire (archive de confiance uniquement). Ne jamais masquer leur statut.
- [ ] **9.3** Suggestions : `/recherche/suggestions?prefix=gr` renvoie des valeurs
      **réellement présentes**.
- [ ] **9.4** Recherche depuis l'interface (avec session) : résultats identiques.
- [ ] **9.5** Latence observée notée telle quelle (le critère produit p95 < 100 ms est
      mesuré **hors instrumentation** en CI ; sur poste atelier, c'est une observation,
      pas une preuve).

## 10. Validation

- [ ] **10.1** Écran **Validation** : la fiche de test apparaît avec ses champs extraits et
      le PDF à droite.
- [ ] **10.2** Correction d'un champ → tracée (`fiche_champ_extrait.corrige`, `corrige_par`).
- [ ] **10.3** Validation explicite → statut `valide`, fiche cherchable, action attribuée au
      **compte nominatif** (jamais « secours »).
- [ ] **10.4** Rejet : motif **obligatoire** (un rejet sans raison est refusé).
- [ ] **10.5** Validation en lot : **refusée** tant que les seuils ne sont pas calibrés
      (`calibre: false`) — c'est le verrou attendu, pas une panne. Il ne sera levé qu'avec
      des fiches réelles validées (**F-2 bloqué**).
- [ ] **10.6** L'écran mesure maintenant localement le temps par fiche et médiane/min/max/compteur en fin de session. **NON MESURÉ** : temps d'un opérateur/ouvrier lisant et décidant sur 3 fiches (ou sur le corpus). Noter la mesure humaine dans `docs/verite_terrain/MESURE_VALIDATION_2MIN.md` ; le chronomètre machine CI ne la remplace pas.

## 11. Sauvegarde

```bash
docker compose exec web python -m seamtech_search.sauvegarde sauver \
  --base-url "postgresql://seamtech:$POSTGRES_PASSWORD@postgres:5432/seamtech_search" \
  --archive "$SEAMTECH_ROOT_PATHS" \
  --dossier data/backups --retention 5
```

- [ ] **11.1** Sortie JSON : `manifeste`, `dump_sha256`, `cle_dump_s3`, `cle_manifeste_s3`.
- [ ] **11.2** Le dump est **relu depuis le bucket** et son empreinte correspond (la commande
      échoue sinon — aucune confiance aveugle).
- [ ] **11.3** Le manifeste contient : date, commit, `version_schema_metier`, comptes par
      table, fiches par statut, nombre de documents, **inventaire de l'archive**
      (chemins + tailles + sha256).
- [ ] **11.4** RG13 : l'archive n'a **pas** été modifiée (inventaire en lecture seule ;
      garde-fou `test_inventaire_archive_complet_et_lecture_seule`).
- [ ] **11.5** Rétention : au plus 5 sauvegardes, **jamais** la dernière supprimée.
- [ ] **11.6** Sauvegarde **planifiée** (cron / Tâches planifiées Windows) et sa sortie
      journalisée.
- [ ] **11.7** Sans S3 configuré, la commande le **dit** (`sauvegarde LOCALE seule`) — une
      sauvegarde locale sur la même machine n'est pas une protection.

- [ ] **11.8** État des imports non terminés inclus dans la sauvegarde (le worker doit
      pouvoir reprendre) :
      `docker compose exec -T postgres psql -U seamtech -d seamtech_search -c "SELECT id, status, attempts FROM import_jobs WHERE status IN ('pending','running');"`
      Attendu : la liste est conservée avec la sauvegarde ; au redémarrage, ces jobs sont
      repris ou marqués en échec **avec raison** — jamais « running » pour toujours.

## 12. Restauration

**Une sauvegarde non restaurée n'est pas une sauvegarde.** Restaurer dans une base
**neuve**, jamais par-dessus la production.

```bash
docker compose exec web python -m seamtech_search.sauvegarde restaurer \
  --cle-manifeste "backups/seamtech-search-<horodatage>.dump.manifest.json" \
  --base-cible "postgresql://seamtech:$POSTGRES_PASSWORD@postgres:5432/seamtech_restore_test"

docker compose exec web python -m seamtech_search.sauvegarde verifier \
  --manifeste data/backups/seamtech-search-<horodatage>.dump.manifest.json \
  --base-url  "postgresql://seamtech:$POSTGRES_PASSWORD@postgres:5432/seamtech_restore_test" \
  --archive "$SEAMTECH_ROOT_PATHS"
```

- [ ] **12.1** L'empreinte du dump est vérifiée **avant** toute écriture.
- [ ] **12.2** La restauration refuse une base cible **déjà existante**.
- [ ] **12.3** `verifier` renvoie `{"ok": true, "ecarts": []}`.
- [ ] **12.4** Durée mesurée et notée (référence CI : **50 000 fiches restaurées en 1,33 s**,
      dump 579 312 octets, run `36168258103` — chiffre CI, **pas** une mesure de production).
- [ ] **12.5** Base de test supprimée après contrôle.
- [ ] **12.6** Procédure rejouée par **une deuxième personne**, en suivant uniquement
      `docs/verite_terrain/RUNBOOK_RESTAURATION.md`.

## 13. Arrêt / redémarrage

- [ ] **13.1** `docker compose stop` puis `docker compose up -d` → **6** services `healthy`.
- [ ] **13.2** Après redémarrage : `/health` conserve le nombre de documents, les
      `object_key` restent exploitables (garde-fou
      `test_references_objets_survivent_au_redemarrage`), la recherche renvoie les mêmes
      résultats.
- [ ] **13.3** Reprise des travaux : un import interrompu est **repris ou mis en
      quarantaine**, jamais perdu (`recover_stale_jobs` au démarrage).
- [ ] **13.4** Redémarrage machine complet (coupure de courant simulée) : même contrôle.
- [ ] **13.5** Volumes nommés (`postgres-data`, `minio-data`, `redis-data`) intacts :
      `docker volume ls`.

## 14. Contrôle des logs

- [ ] **14.1** `docker compose logs --tail 200 web frontend postgres minio redis` : aucune
      trace `ERROR`/`Traceback` inattendue.
- [ ] **14.2** Rotation bornée : 10 Mo × 5 fichiers par conteneur (`x-logging` dans compose)
      — un service bavard ne peut pas remplir le disque.
- [ ] **14.3** **Aucun secret** dans les journaux : ni token, ni mot de passe, ni clé S3, ni
      URL présignée (une URL présignée porte `X-Amz-Credential` et vaut 900 s) :
      ```bash
      docker compose logs | grep -E "X-Amz-Credential|SECRET|password=|token=" | head
      ```
      Attendu : **vide**.
- [ ] **14.4** Journal d'audit alimenté : `/audit` renvoie les actions
      (`search`, `open`, `artifact_download`, validations) avec acteur et horodatage.
- [ ] **14.5** Rétention de l'audit connue et assumée : `audit_retention_days` (défaut 365) —
      la table d'audit **n'est pas immuable**, elle est élaguée.

## 15. Rollback

- [ ] **15.1** Le SHA en service est noté : `git rev-parse HEAD` (et l'étiquette de version).
- [ ] **15.2** Sauvegarde **fraîche et vérifiée** avant toute mise à jour (§11 + §12).
- [ ] **15.3** Retour arrière applicatif :
      `git checkout <SHA précédent> && docker compose up -d --build` → `/health` vert.
- [ ] **15.4** Cas d'une migration nouvelle : le retour arrière du **code** ne défait pas le
      **schéma**. Si la version précédente ne sait pas lire le schéma migré → restaurer la
      sauvegarde de l'étape 15.2 dans une base neuve et basculer `SEAMTECH_DATABASE_URL`.
- [ ] **15.5** Critère de décision écrit **avant** la mise à jour (« on revient si : … ») et
      personne désignée.
- [ ] **15.6** Retour arrière **répété à blanc** au moins une fois hors production.

## 16. Contrôle des permissions

- [ ] **16.1** Conteneur `web` en **utilisateur non root** (`USER seamtech` dans le Dockerfile) :
      `docker compose exec web id` → pas `uid=0`.
- [ ] **16.2** Archive montée **en lecture seule** ; test d'écriture refusé :
      `docker compose exec web touch "$SEAMTECH_ROOT_PATHS/_test" ` → **Permission denied** (RG13).
- [ ] **16.3** `.env` en `600`, propriétaire = compte d'exploitation.
- [ ] **16.4** Aucun port autre que `127.0.0.1` publié (§4.3) ; exposition LAN uniquement
      derrière TLS (`docs/TLS.md`) avec `SEAMTECH_BEHIND_TLS_PROXY=true`.
- [ ] **16.5** Console MinIO (9001) non exposée hors loopback ; identifiants **≠** `minioadmin`.
- [ ] **16.6** Rôles applicatifs : un compte `operateur` ne peut pas créer de comptes
      (403 sur `/auth/utilisateurs`).
- [ ] **16.7** Identifiants S3 : aujourd'hui ce sont les identifiants **root** de MinIO
      (risque R-7 de l'audit stockage) — décision D-4 attendue du commanditaire.

## 17. Contrôle RG13 / RG14

**RG13 — l'archive est la source de vérité et ne se modifie jamais** :

- [ ] **17.1** Montage `:ro` effectif (§16.2).
- [ ] **17.2** Empreintes avant/après une session de travail identiques :
      `python3 scripts/preflight_archive.py empreintes --manifeste <rapport>/echantillon.json --source <ARCHIVE>`
      → aucun fichier `MODIFIÉ`/`PERDU`.
- [ ] **17.3** Répertoire de travail OCR **hors archive** : `SEAMTECH_OCR_TRAVAIL_DIR`
      (défaut `data/ocr_travail`).
- [ ] **17.4** État connu : la copie canonique de la fiche réelle 7792-SO et les 7 ZIP du corpus sont déjà suivis/historiques dans le dépôt public ; le doublon `7792-SO_ffab.pdf` à la racine est retiré du tree courant. Le fichier subsiste dans l'historique Git. Aucune donnée client réelle nouvelle n'est ajoutée par cette mission. Décision du commanditaire : visibilité du dépôt reste publique ; maintenir les garde-fous de confidentialité.
- [ ] **17.5** Les sauvegardes n'écrivent **jamais** dans l'archive (inventaire seul).

**RG14 — aucun appel réseau dans le code de traitement** :

- [ ] **17.6** Garde-fou automatisé vert :
      `pytest -q tests/test_garde_fous_preparation.py` (analyse AST de `seamtech_search/` et
      `scripts/`, exceptions documentées).
- [ ] **17.7** Le préflight d'archive s'exécute **sans réseau** (débrancher pour le prouver) :
      `python3 scripts/preflight_archive.py preflight --source <ARCHIVE> --travail <T> --sortie <S>`
      → code 0.
- [ ] **17.8** Aucun modèle ni binaire téléchargé au runtime : les poids e5-small sont
      déployés par une action **explicite** de l'opérateur, jamais par l'application.
- [ ] **17.9** Exception connue et assumée : le stockage objet appelle l'endpoint S3 (c'est
      le service, pas un appel externe implicite) ; si l'endpoint est hors atelier, cela
      relève de la décision D-3 de l'audit stockage.

**Vérification hors ligne AVANT le départ en atelier** (revue du 2026-10-07, constat n° 3) —
l'inventaire seul ne prouve rien, c'est l'exécution qui compte :

- [ ] **17.10** Inventaire du provisionnement : `python scripts/verifier_hors_ligne.py --inventaire`
      → chaque capacité REQUISE est `OK` et chaque capacité OPTIONNELLE absente est **dite**
      (OCR étage 3, rendu image, modèles e5, images conteneurs).
- [ ] **17.11** Parcours essentiels avec réseau externe BLOQUÉ :
      `python scripts/verifier_hors_ligne.py --executer --rapport /tmp/hors-ligne.json`
      → `échecs : 0` et ligne `aucune dépendance externe` = `OK`
      (si une sortie est tentée, l'hôte:port est nommé dans le rapport).
- [ ] **17.12** Le rapport JSON est conservé avec la fiche d'acceptation ; il ne remplace
      **pas** l'acceptation atelier (serveur réel, réseau débranché, trois postes, humains).

---

## 18. Photographie historique de la première candidate (2026-09-25)

> Cette photographie précède les PR #32/#33 et n'est pas l'état courant. Voir §19 pour la mesure de cette mission.

| Élément | État historique au 2026-09-25 |
|---|---|
| CI sur `main` après fusion de la PR #28 | ✅ verte, 9/9 (`36168258103`) |
| Migrations 001→017 | ✅ séquence complète, rejeu idempotent |
| Sauvegarde → destruction → restauration | ✅ prouvée en CI (MinIO réel) |
| Stockage objet | ⚠️ **VALIDÉ AVEC RÉSERVES** — D-1 et R-14 corrigés le 25/09 ; restent R-1 (MinIO archivé), R-2/R-3 (réglages trompeurs), R-13 (aucun second fournisseur éprouvé) — voir `AUDIT_STOCKAGE_S3_MINIO.md` |
| Chemin Windows | ❌ non exécuté depuis l'environnement de développement (Linux) |
| Chrono humain de validation | ❌ 0/3 fiches mesurées |
| Échelle réelle | ❌ **Lot G réel bloqué** — archive non livrée |
| Réentraînement | ❌ **F-2 bloqué** — 300 à 500 fiches validées manquantes |

**Nom / date / signature de l'opérateur** : ............................................

**Cases non cochées, avec la raison** (à recopier ici, une ligne par case) :
............................................................................................

## 19. Portail de la candidate Lots 1–3 (2026-09-29)

État arrêté sur la branche `arena/01a0ed7b-seamtech-search`, checkpoint de code/tests
`887beba5b5d2e36ce99e405d99a5e23b2e5a53b9` (PR #33 ouverte, non fusionnée; code applicatif évalué en `984e2db`).

| Contrôle | Statut / preuve |
|---|---|
| ZIP corpus : empreintes courantes | ✅ Recalculées le 29/09; aucune modification locale. Sorties brutes (SHA-256) :<br>`AQUILA 250216AJA-20260928T182324Z-1-001.zip` — `fd1fca0930494328687ecf5d6905af139c24f93a8b9e2e51b2f6075772446be6`<br>`ATTALIA 250121JA-20260928T182325Z-1-001.zip` — `a71ec77d6d5abf3c55f5ca8aa5c79d3347d3b3765b91f54950065b9c2161d699`<br>`BAVARIA 32 - 250604JA-20260928T182326Z-1-001.zip` — `a9ca298cae78ccbacfaab69260cede299062ef0d20e0a7fba62c3539ac3a538f`<br>`BAVARIA 34 - 250323JA-20260928T182327Z-1-001.zip` — `483f89716b953734315b7bbb4ba81050dc24b33a162e2a2623604eee268b8f6f`<br>`DAMIEN 4 - 250821JA-20260928T182330Z-1-001.zip` — `2f1ecb0205b817c3dbdb324bf444aad9fb5ddce1d3f415859698fbb21fb428bf`<br>`DEHLER 39 - 250329AJA-20260928T182330Z-1-001.zip` — `b3171d070f6b332366263626a665e7f7985bb8406062c2957895401850df6a06`<br>`GIB SEA 284 - 250328AJA-20260928T182331Z-1-001.zip` — `103359b561ef32f5a8ce04b373864c26a0b496156efc1f728e75f0c0911b4aa3` |
| PDF racine / copie canonique | ✅ SHA-256 identique à l'empreinte de la copie canonique : `43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40`. Le doublon racine est retiré du tree; il subsiste dans l'historique Git. |
| Suite locale sélectionnée | ✅ `766 passed, 3 skipped, 234 deselected`; sélection par marqueurs `-m`. Vérifications précédemment réussies : Ruff, TypeScript, build frontend, compilation Python, YAML et `git diff --check`. |
| Benchmark PostgreSQL jetable, 10 000 fiches | ✅ **SYNTHÉTIQUE** — run initial [36594937291](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36594937291) (1 min 5 s) et rerun final [36598278884](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36598278884) (1 min 11 s), 50 échantillons/scénario. Percentiles et trois plans EXPLAIN complets des deux runs sont archivés : `docs/benchmarks/scale-bench-synthetique-36594937291.{json,md}` et `docs/benchmarks/scale-bench-synthetique-36598278884.{json,md}`. |
| Échec initial du benchmark | Diagnostiqué : `ModuleNotFoundError: seamtech_search` lors de l'exécution directe du script. Corrigé par `PYTHONPATH=.`; le run synthétique réussi ci-dessus valide ce correctif. |
| CI finale checkpoint `887beba` | ✅ Push [36598273856](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36598273856) et PR [36598278897](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36598278897) verts; 11/11 jobs chacun. Les runs antérieurs (156ab88: 36596936501/36596943586; 01484d0: 36595978800/36595984492) étaient aussi verts. Benchmark final [36598278884](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36598278884) vert; rapport JSON et plans EXPLAIN complets archivés sous `docs/benchmarks/scale-bench-synthetique-36598278884.{json,md}`. |
| Répétition humaine REF-001..007, VPS01 / R2, Windows atelier | **NON MESURÉ / À VALIDER** — portes de déploiement non levées. MinIO reste le backend production; R2 est conditionné à D-2. |

Percentiles **SYNTHÉTIQUES** du run 36594937291 (millisecondes; ne représentent ni VPS01/R2 ni utilisateurs réels) :

| Scénario | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| mot_simple | 42.59 | 48.11 | 48.26 | 48.26 |
| multi_mots | 52.83 | 66.45 | 86.05 | 86.05 |
| code | 6.70 | 6.83 | 15.48 | 15.48 |
| facette_dimension | 50.71 | 62.05 | 129.36 | 129.36 |
| filtres | 53.44 | 80.11 | 150.62 | 150.62 |
| suggestions | 4.28 | 4.58 | 4.67 | 4.67 |

Toutes les valeurs p95 sont sous la cible **indicative SYNTHÉTIQUE** de 250 ms. Les plans EXPLAIN ANALYZE/BUFFERS des 3 requêtes lentes sont enregistrés intégralement dans le JSON et le rapport lié ci-dessus.

Répétition benchmark **SYNTHÉTIQUE** du checkpoint `887beba`, run 36598278884 (ms; annotation GitHub conservée en JSON et plans EXPLAIN archivés en Markdown/JSON) :

| Scénario | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| mot_simple | 81.13 | 84.81 | 88.42 | 88.42 |
| multi_mots | 107.86 | 109.83 | 133.43 | 133.43 |
| code | 11.70 | 12.43 | 12.57 | 12.57 |
| facette_dimension | 92.97 | 94.00 | 95.18 | 95.18 |
| filtres | 98.04 | 99.76 | 111.70 | 111.70 |
| suggestions | 8.47 | 8.76 | 8.82 | 8.82 |

Valeurs issues d'un PostgreSQL jetable et de 10 000 fiches synthétiques; elles ne mesurent ni VPS01, ni R2, ni le temps réel de l'opérateur.

**Verdict :** candidate techniquement enrichie, suite locale et CI finale vertes; **pas une autorisation de mise en production**. Ne pas merger ni déployer tant que l'opérateur n'a pas validé REF-001..007, que les contrôles réels VPS01/sauvegarde/restauration ne sont pas documentés et que les décisions D-2/D-4 restent ouvertes. Les valeurs de benchmark sont mesurées en environnement **SYNTHÉTIQUE**, jamais à présenter comme réelles.

## 20. Audit pré-merge PR #33 (2026-09-29)

Arbre code/tests audité `6ea4bee364d01c4a351a23c0c68c765d6b719976`, base `a7ffe7d2800ed697f306a93b0564b4ad8c87eb74`. PR #33 est ouverte et non fusionnée. Le rapport détaillé avec commandes, sorties, inventaire et catégories RÉEL / SYNTHÉTIQUE / NON MESURÉ est [`RAPPORT_FINAL_OPTIMISATION_2026-09-29.md`](verite_terrain/RAPPORT_FINAL_OPTIMISATION_2026-09-29.md).

| Contrôle | Résultat |
|---|---|
| Suite sélectionnée main / PR | Main : 764 passed, 3 skipped, 234 deselected. PR : 771 passed, 3 skipped, 234 deselected. |
| Nodeids | Main 767; PR 774; zéro nodeid main perdu/renommé; 7 ajouts. |
| Corpus | 7/7 SHA-256 identiques à main; ZIP inchangés. Détails de hachage existants au §19. |
| Seuils / sélection | Couverture globale 85% et planchers module inchangés; aucun `pragma: no cover` ajouté; sélection pytest CI par `-m` uniquement. |
| CI push / PR | Runs 36607480324 et 36607487809 verts, chacun 11/11 jobs, sur `6ea4bee`. Benchmark 36607487218 vert, SYNTHÉTIQUE, 10 000 fiches. |
| Verdict de l’auto-audit | **PR PRÊTE À MERGER** pour les contrôles pré-merge demandés; cela ne vaut pas décision de fusion. Aucune fusion effectuée. Mise en production non approuvée : les mesures opérateur/VPS01/R2 restent NON MESURÉES. |

## 21. Passe de revue indépendante (2026-10-07)

État arrêté sur la branche `arena/7da80c2f-seamtech-search`. Les chiffres
ci-dessus (§19-20) décrivent des checkpoints antérieurs ; ce qui suit les
remplace pour la tête de branche.

| Contrôle | Statut / preuve |
|---|---|
| Suite sans service | ✅ **833 passed, 3 skipped, 299 deselected** (`-m "not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue"`, 84 s) |
| Suite PostgreSQL réelle | ✅ **226 passed, 2 skipped** |
| File durable (Redis + PostgreSQL réels) | ✅ **37 passed, 0 skipped** — dont 5 tests lançant de **vrais processus** worker (`python -m seamtech_search.worker_service`) tués par `SIGKILL` puis repris |
| Suite S3 vivant | ⛔ **0 passed, 29 skipped** dans cet environnement (pas de Docker, pas de MinIO). Exécution : job CI `integration` (+ `sauvegarde`, `recette-corpus-reel`) |
| Front-end : types | ✅ `npx tsc --noEmit` → exit 0 (les 11 specs `e2e/*.ts` sont dans le programme) |
| Front-end : build production | ✅ `npx next build` → exit 0 |
| Navigateur Playwright | ⛔ **non exécuté ici** : `cdn.playwright.dev` injoignable (ECONNRESET), aucun navigateur système. Exécution : job CI `e2e` |
| Lint / compilation | ✅ `ruff check .` → All checks passed ; `python -m compileall seamtech_search` → OK |
| Intégrité du stockage | ✅ 10 tests (magasin S3 en mémoire conservant les vrais octets) : métadonnées ≠ preuve, taille seule ⇒ **purge refusée** |
| Limite honnête | Le partage web/worker est prouvé **au niveau processus et contrat** ici ; il n'est **pas** prouvé au niveau conteneurs avant le run CI `integration` de `tests/test_compose_partage_worker.py` |

Détail : `docs/verite_terrain/REVUE_INDEPENDANTE_2026-10-07.md`.

## 22. Deuxième passe de revue indépendante (2026-10-07) — corrections du plan

Ce qui suit corrige la façon de DÉPLOYER (ne pas exécuter l'ancien plan mot à mot).
Les points d'ingénierie sont fermés dans le code (E-40 : `index --rebuild` sûr,
prouvé sur PostgreSQL réel par `tests/test_rebuild_index_postgres.py`) ; ici, c'est
la mise en service qui est recadrée.

| # | Correction | Où c'est appliqué |
|---|---|---|
| 22.1 | **MinIO est le fournisseur retenu** pour cette release (R2/S3 restent optionnels) — la question n'est pas rouverte | ce document §1.6, `docs/DEPLOYMENT.md` (en-tête) |
| 22.2 | Les **identités S3 dédiées** ne sont plus une décision mais du **provisionnement + vérification sur le serveur cible** | §2.4, `scripts/provisionner_stockage.sh`, `docs/DEPLOYMENT.md` |
| 22.3 | **Six services** : `frontend`, `web` (API), `worker`, `postgres`, `redis`, `minio` | §4.1, §4.4, §13.1, `docs/verite_terrain/MISE_EN_SERVICE.md` |
| 22.4 | Migrations vérifiées contre la **constante de schéma de la release** (`VERSION_SCHEMA_METIER`, liste finissant à `021_revision_fiche`) | §5.1–5.2 |
| 22.5 | **50 Go / 8 Go / 2 cœurs = provisoires** ; le stockage doit couvrir originaux, copies stockées, traitement, quarantaine, base, sauvegardes et croissance | §1.2–1.3, `docs/ARRIVEE_ARCHIVE.md`, `docs/verite_terrain/DECISION_MATERIEL.md` |
| 22.6 | **Sécurité AVANT les données réelles et les workers** : secrets, archive en lecture seule, exposition réseau, TLS | §2, §3, §16, `docs/DEPLOYMENT.md` (section TLS/accès) |
| 22.7 | **Import progressif** : échantillon représentatif + cas difficiles avant l'archive entière | §8.7, `docs/ARRIVEE_ARCHIVE.md` |
| 22.8 | La **calibration ML** ne bloque que la promesse de confiance calibrée, pas l'extraction assistée | en-tête (« Ce qui reste bloqué ») |
| 22.9 | **Secrets base64 → URL-safe** (ou encodage) pour les URL PostgreSQL/Redis ; **provisionnement du stockage vérifié avant l'usage normal** | §3.1 bis, §2.4, `AppConfig.validate_connection_urls` |

**Ordre recommandé** (détaillé dans `docs/verite_terrain/MISE_EN_SERVICE.md` §2 bis) :
(1) fermer les points d'ingénierie et FIGER le commit de release ; (2) confirmer la
conception (serveur d'atelier local ou VM vs VPS distant — un VPS fait dépendre
l'atelier de la liaison Internet —, MinIO, destination de sauvegarde indépendante,
perte de données acceptable / durée de reprise, qui maintient comptes, mises à jour,
sauvegardes et alertes) ; (3) provisionner SÉCURISÉ avant toute donnée réelle et
avant les workers ; (4) pilote réduit représentatif, cas difficiles compris, le
processus de fabrication existant restant la référence ; (5) prouver la reprise dans
un environnement isolé (un dump restauré seul ≠ archive récupérée) ; (6) augmenter la
taille de l'archive, accepter en atelier, mesurer, go/no-go explicite.
