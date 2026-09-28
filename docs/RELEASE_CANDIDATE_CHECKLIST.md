# Checklist de release candidate — SEAMTECH Search

**Version auditée** : `main` = `f8befb5da800c10d793e25addf9519cc63f5fd6c` (PR #28 fusionnée,
CI post-fusion verte 9/9, run `36168258103`) · **Rédigée le** 2026-09-25.

À quoi sert ce document : c'est la **liste des cases à cocher sur le poste cible**, dans
l'ordre, avec pour chaque case **la commande exacte**, **la sortie attendue** et **la
preuve à archiver**. Une case non cochée est un « non prouvé », jamais un « sans doute
bon ». Reportez la sortie brute dans la colonne *Preuve* : ce document est destiné à être
rempli, daté et signé.

> **Ce qui reste bloqué et ne peut donc PAS être coché ici**
>
> - **Lot G réel bloqué** : l'archive de production n'a pas été livrée. Aucun traitement
>   de masse, aucune mesure de volumétrie, aucune durée d'import réelle.
> - **F-2 bloqué** : les 300 à 500 fiches validées nécessaires au réentraînement ne sont
>   pas disponibles ; la calibration reste volontairement verrouillée (`calibre: false`).
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
| 1.2 | Espace disque libre | `df -h .` (Windows : `Get-PSDrive C`) | **≥ 50 Go** pour l'archive réelle ; 5 Go pour une recette | |
| 1.3 | RAM / cœurs | `free -h`, `nproc` | ≥ 8 Go, ≥ 2 cœurs (postes 8 Go : pas de PyTorch, décision du 21/09) | |
| 1.4 | Ports libres | `ss -lntp \| grep -E ':(3000\|8000\|9000\|9001\|6379\|5433)'` | aucune ligne | |
| 1.5 | Git + horloge système | `git --version`, `date -u` | horloge juste (les clés de sauvegarde sont horodatées UTC) | |
| 1.6 | **Image MinIO disponible** — plus aucun registre ne la distribue | `bash scripts/construire_image_minio.sh` | `Image quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z prête` | |
| 1.7 | (option Windows) Docker Desktop + WSL 2 | `docs/CHECKLIST_POSTE_WINDOWS.md` | checklist Windows cochée | |

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
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | oui | **ne pas laisser `minioadmin`** | idem |
| `SEAMTECH_S3_BUCKET` | non (défaut `seamtech-documents`) | — | `/health` |
| `SEAMTECH_ROOT_PATHS` | oui en production | dossier d'archive **monté en lecture seule** | `/health`, `/preview` |
| `SEAMTECH_BEHIND_TLS_PROXY` | selon exposition | `true` derrière un terminateur TLS | `docs/TLS.md` |
| `SEAMTECH_SESSION_HOURS` | non (12) | durée de session | — |

- [ ] **2.1** `docker compose config --quiet` ne renvoie **rien** (aucune variable manquante).
- [ ] **2.2** Aucune valeur `change-me` ne subsiste : `grep -n "change-me\|minioadmin" .env` → **vide**.
- [ ] **2.3** Le dossier d'archive est monté **en lecture seule** dans `web` (`:ro`) — RG13.

## 3. Génération et protection des secrets

- [ ] **3.1** Générer (jamais de secret « inventé à la main », jamais réutilisé d'un autre service) :
  ```bash
  openssl rand -base64 48   # SEAMTECH_AUTH_TOKEN
  openssl rand -base64 48   # SEAMTECH_SESSION_SECRET
  openssl rand -base64 24   # SEAMTECH_UI_PASSWORD (secours)
  openssl rand -base64 24   # POSTGRES_PASSWORD / REDIS_PASSWORD / MINIO_ROOT_PASSWORD
  ```
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
- [ ] **3.7** Le jeton GitHub ayant circulé est **révoqué** et le dépôt est **privé** (le dépôt
      contient une vraie fiche client — RG13).

## 4. Lancement Docker

```bash
docker compose up -d --build
docker compose ps
```

- [ ] **4.1** 5 services : `postgres`, `minio`, `redis`, `web`, `frontend`.
- [ ] **4.2** Tous en `running (healthy)` (les healthchecks font foi ; `web` attend
      `service_healthy` sur les trois autres).
- [ ] **4.3** Ports publiés **en loopback uniquement** : `docker compose ps --format '{{.Name}} {{.Ports}}'`
      ne montre que des `127.0.0.1:…` (garde-fou : `tests/test_compose_hardening.py`).
- [ ] **4.4** Redémarrage automatique : `restart: unless-stopped` sur les 5 services.

## 5. Migrations

Les migrations tournent **au démarrage de l'API** (`lifespan` → `_initialize_schema`) :
il n'y a pas d'étape manuelle, mais il y a une **vérification obligatoire**.

- [ ] **5.1** Séquence complète appliquée, sans trou :
  ```bash
  docker compose exec -T postgres psql -U seamtech -d seamtech_search \
    -c "SELECT version FROM schema_migrations ORDER BY version;"
  ```
  Attendu : **17 lignes**, de `001_initial` à `017_ocr_etage3`.
- [ ] **5.2** Version métier alignée :
  ```bash
  docker compose exec -T web python -c "from seamtech_search.schema_metier import VERSION_SCHEMA_METIER, TABLES_METIER; print(VERSION_SCHEMA_METIER, len(TABLES_METIER))"
  ```
  Attendu : `017_ocr_etage3 33`.
- [ ] **5.3** Extensions réellement présentes : `/health` renvoie `vector`, `pg_trgm`, `unaccent`.
- [ ] **5.4** Rejeu sans effet : redémarrer `web` ne rejoue ni ne duplique aucune migration
      (garde-fou : `test_migrations_sequentielles_001_a_017_sur_base_vide`).

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
      Avec S3 configuré, la réponse attendue est un **302** vers une URL présignée
      valable 900 s (`curl -i` montre l'en-tête `Location`) ; sans clé d'objet, un `200`
      servi par l'API. *Correctif D-1 du 25/09/2026 — avant lui, le 302 n'était jamais
      servi ; voir l'audit stockage §6.*
- [ ] **8.6** Idempotence : rejouer le même dépôt → `deja_traite`, aucun doublon créé.

## 9. Recherche de test

- [ ] **9.1** `curl -fsS -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" "http://127.0.0.1:8000/recherche?q=grand-voile"`
      → `nb_resultats ≥ 1`, `duree_ms` renseigné.
- [ ] **9.2** Une fiche **`a_valider` n'apparaît pas** sans `inclure_a_valider=true`.
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
- [ ] **10.6** Chronométrer une validation humaine et **écrire la mesure** dans
      `docs/verite_terrain/MESURE_VALIDATION_2MIN.md` (0/3 fiches mesurées à ce jour).

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

- [ ] **13.1** `docker compose stop` puis `docker compose up -d` → 5 services `healthy`.
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
- [ ] **17.4** Aucun document client versionné dans Git (la fiche 7792-SO présente au dépôt
      impose le dépôt **privé** ; cf. §3.7).
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

---

## 18. Décision de mise en service

| Élément | État au 2026-09-25 |
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
