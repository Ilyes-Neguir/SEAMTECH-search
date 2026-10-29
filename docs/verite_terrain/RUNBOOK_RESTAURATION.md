# Runbook de restauration — sauvegarde hors-site éprouvée (Lot H.1)

Statut au 22/09/2026 : **aller-retour PROUVÉ par exécution** — le job CI
`sauvegarde` sauvegarde une base semée, la SUPPRIME (DROP DATABASE), supprime
les copies locales, puis la restaure DEPUIS LE BUCKET SEUL et prouve
l'égalité : comptes par table, VERSION_SCHEMA_METIER, recherche fonctionnelle,
inventaire d'archive. Ce n'est pas « sauvegarde configurée » : c'est « base
détruite reconstruite à l'identique, prouvé par exécution en CI ».

Preuves : job `sauvegarde` de `.github/workflows/ci.yml` ; tests
`tests/test_sauvegarde_restauration.py` (marqueurs `postgres`/`sauvegarde`) ;
mesures publiées en annotations `::notice title=sauvegarde-*`.

## 1. Ce qui est sauvegardé

| Objet | Où | Preuve |
|---|---|---|
| Base PostgreSQL (pg_dump -Fc) | local + bucket S3/MinIO (`backups/seamtech-search-<horodatage>.dump`) | re-lecture du dump depuis le bucket et comparaison d'empreinte APRÈS chaque envoi |
| État de l'archive (jamais l'archive elle-même — RG13 : lecture seule, aucune copie) | dans le manifeste : chemins + tailles + sha256 de chaque fichier | `verifier` re-parcourt l'archive et signale tout fichier PERDU, ALTÉRÉ ou ajouté |
| Manifeste | `<dump>.manifest.json` local + bucket | date, taille et sha256 du dump, VERSION_SCHEMA_METIER, comptes par table, fiches par statut, nb documents, commit de l'application |
| **Répertoires de données** (`./data` : lots téléversés, quarantaine, rapports générés) — option `--data` | copies vérifiées (SHA-256) sous `<bundle>/data/<racine>/<chemin relatif>` | clé `data` du manifeste : `nb_copies`, `nb_echecs`, `ignores_volatiles` (verrous/`.tmp`), `liens_sortants`, `absentes`, et la liste des **échecs** ; `complet` passe à `false` au moindre échec |
| **Objets du stockage objet** (MinIO/R2) référencés par `documents` | copies sous `<bundle>/objets/…` | clé `objets_s3` du manifeste : nombre d'objets copiés, absents et échecs |

> **Ce que la sauvegarde NE fait pas** : elle ne sauvegarde l'archive
> d'origine que sous forme d'INVENTAIRE (RG13, lecture seule) — voir
> `restaurer --fichiers-cible` pour remettre les copies référencées ailleurs
> que sous les racines d'archive.

## 1 bis. Sauvegarde COMPLÈTE (base + données + objets) — commande de référence

```bash
python -m seamtech_search.sauvegarde sauver \
  --base-url "postgresql://seamtech:<motdepasse>@127.0.0.1:5432/seamtech_search" \
  --archive "D:/SEAMTECH/DesignFiles" \
  --data ./data \
  --avec-references \
  --dossier data/backups --retention 5
```

`--data` est **nécessaire** dès que des dossiers ont été téléversés depuis le
navigateur : leurs octets vivent sous `uploads/lots-televerses/…` (hors
archive). Une sauvegarde qui l'omet n'est pas « complète » : le manifeste le
signale (`data` absent) et la restauration ne pourra pas restituer ces pièces.

Le manifeste publie `complet: true|false`. **Une sauvegarde n'est utilisable
que si `complet` vaut `true`** — sinon les échecs sont listés dans
`data.echecs` / `fichiers_locaux.absents` et doivent être traités avant de
considérer que le poste est protégé.

## 2. Commandes (CLI portable — Linux, macOS, Windows/PowerShell)

La sauvegarde est un module Python : aucune dépendance PowerShell, mêmes
commandes partout. Les binaires `pg_dump`/`pg_restore` sont découverts via
`PATH`, `SEAMTECH_PG_BINDIR`, `/usr/lib/postgresql/*/bin` (sans binaire,
l'échec est explicite — jamais de repli silencieux).

### Sauvegarder (à planifier, ex. quotidienne)

```bash
python -m seamtech_search.sauvegarde sauver \
  --base-url "postgresql://seamtech:<motdepasse>@127.0.0.1:5432/seamtech_search" \
  --archive "D:/SEAMTECH/DesignFiles" \
  --dossier data/backups --retention 5
```

Sortie attendue (une ligne JSON) :
```json
{"manifeste": "data/backups/seamtech-search-<horodatage>.dump.manifest.json",
 "dump_sha256": "<64 hex>", "cle_dump_s3": "backups/seamtech-search-<horodatage>.dump",
 "cle_manifeste_s3": "backups/seamtech-search-<horodatage>.dump.manifest.json"}
```
Sans configuration S3 (`SEAMTECH_S3_ENDPOINT_URL` absent) : sauvegarde LOCALE
seule, un avertissement est émis — la protection hors-site n'existe qu'avec le
bucket configuré. La rétention conserve N sauvegardes et purge les plus
anciennes ; la dernière n'est JAMAIS purgée (même avec N invalide).

### Restaurer (base détruite ou corrompue)

```bash
# Depuis le bucket seul (recommandé — c'est le scénario prouvé en CI) :
python -m seamtech_search.sauvegarde restaurer \
  --cle-manifeste "backups/seamtech-search-<horodatage>.dump.manifest.json" \
  --base-cible "postgresql://seamtech:<motdepasse>@127.0.0.1:5432/seamtech_search"

# Depuis un manifeste local :
python -m seamtech_search.sauvegarde restaurer \
  --manifeste data/backups/<…>.dump.manifest.json \
  --base-cible "postgresql://…"
```

Sortie attendue :
```json
{"restaure": "seamtech_search", "duree_s": <durée mesurée>}
```
Sécurité : la base cible doit être NEUVE (une base existante est refusée,
jamais écrasée par surprise) ; l'empreinte du dump est vérifiée AVANT toute
écriture (un dump altéré est refusé).

### Vérifier (après restauration, ou en contrôle périodique)

```bash
python -m seamtech_search.sauvegarde verifier \
  --manifeste data/backups/<…>.dump.manifest.json \
  --base-url "postgresql://…" \
  --archive "D:/SEAMTECH/DesignFiles"
```

Sortie attendue : `{"ok": true, "ecarts": []}` et code retour 0.

### Restauration COORDONNÉE (base + `./data` + objets) — le scénario de sinistre

Un sinistre ne détruit pas seulement PostgreSQL : le disque des téléversements
et le bucket peuvent être perdus en même temps. `--coordonnee` remet les trois
ensemble, dans cet ordre, puis **re-vérifie le manifeste** :

```bash
python -m seamtech_search.sauvegarde restaurer \
  --manifeste data/backups/<…>.dump.manifest.json \
  --base-cible "postgresql://seamtech:<motdepasse>@127.0.0.1:5432/seamtech_restore" \
  --data-cible /srv/seamtech/data \
  --restaurer-objets \
  --coordonnee
```

Sortie (une ligne JSON) : `{"base": …, "data": {"nb": <copies restaurées>},
"verification": {"ok": true, "ecarts": []}}`, code retour 0 seulement si
`verification.ok` est `true`.

Ordre appliqué, non modifiable : **base → données → fichiers → objets →
vérification**. Un échec à n'importe quelle étape arrête la restauration avec
une erreur explicite ; rien n'est déclaré « restauré » sans contrôle final.

- La base cible doit être NEUVE (même règle que ci-dessus).
- `--data-cible` doit être un répertoire distinct de `./data` en production :
  on restaure À CÔTÉ, on contrôle, puis on bascule.
- Chaque copie de données est revérifiée par SHA-256 **avant** écriture : un
  fichier altéré dans le bundle fait échouer la restauration au lieu de
  remettre en service des octets faux.

## 2 ter. Téléversements de lots : promotion vers le stockage objet

Depuis le correctif P0.2, les octets d'un dossier téléversé **remontent vers le
stockage objet** (MinIO/R2) juste après l'écriture de la fiche ; la copie locale
reste en place (aucune purge : les fiches déposées la référencent encore).

| Situation | État publié | Action |
|---|---|---|
| envoi réussi et relecture identique | `documents.upload_status = 'uploaded'`, `object_key` renseigné | aucune |
| stockage indisponible pendant le dépôt | `upload_status = 'failed'`, dépôt **réussi** (la fiche existe) | relancer `python -m seamtech_search.fiches promouvoir` quand le stockage revient |
| intégrité non prouvée (relecture désactivée) | `upload_status` ≠ `uploaded`, **aucune** `object_key` | ne pas considérer le document comme archivé hors-site |
| stockage non configuré | `upload_status = 'not_configured'` | configurer le stockage, puis `promouvoir` |

```bash
# Reprise idempotente : n'envoie que ce qui n'est pas encore promu.
python -m seamtech_search.fiches promouvoir --config config.json
```

Après une reprise, contrôler :
```sql
SELECT upload_status, COUNT(*) FROM documents GROUP BY upload_status ORDER BY 1;
```
Attendu après reprise : aucune ligne `failed` ou `pending` résiduelle.

## 2 quater. Montée de version et retour arrière (prouvés sur une copie)

La montée de version n'est **pas** autorisée sans filet. La procédure est un
module :

```bash
# 1. AVANT : photographie (versions de schéma, comptes, empreintes de contenu)
#    + dump de sécurité. Refuse de continuer si le dump échoue.
python -m seamtech_search.migration_securee avant \
  --base-url "postgresql://seamtech:<motdepasse>@127.0.0.1:5432/seamtech_search" \
  --dossier data/backups/migrations

# 2. Déployer la nouvelle version (les migrations s'appliquent au démarrage).

# 3. APRÈS : comparaison à la photographie. Code retour 1 si un écart est
#    constaté (table disparue, lignes perdues, contenu réécrit, schéma inchangé).
python -m seamtech_search.migration_securee apres \
  --base-url "postgresql://…/seamtech_search" \
  --etat-avant data/backups/migrations/<dump>.etat-avant.json

# 4. RETOUR ARRIÈRE (si l'étape 3 échoue) : le dump de sécurité est restauré
#    dans une base NEUVE, puis comparé à la photographie d'avant.
python -m seamtech_search.migration_securee rollback \
  --etat-avant data/backups/migrations/<dump>.etat-avant.json \
  --dossier data/backups/migrations \
  --base-cible "postgresql://…/seamtech_rollback_test"
```

Ce qui est prouvé par exécution (`tests/test_migration_rollback.py`, PostgreSQL
réel) : installation reconstituée à la version `025_audit_logs`, semée de
données métier, migrée jusqu'à la version courante — **aucune table perdue,
aucune ligne perdue, contenu inchangé** — puis **deux retours arrière
successifs** vers des bases neuves, chacun rendant exactement l'état d'avant
(versions de schéma, comptes et empreintes), et l'index de recherche
fonctionnel après restauration.

Ce qui n'est **pas** prouvé : la montée de version sur le poste Windows réel
(Docker Desktop), et un arrêt brutal PENDANT la migration.

## 3. Si la sortie ne correspond pas

| Symptôme | Cause probable | Action |
|---|---|---|
| `binaire « pg_dump » introuvable` | client PostgreSQL absent du poste | installer postgresql-client, ou `SEAMTECH_PG_BINDIR=<répertoire des binaires>` |
| `restauration REFUSÉE` (empreinte) | dump corrompu en transit/stockage | relancer avec la sauvegarde précédente du bucket (`list_keys` du job rétention) ; vérifier le bucket |
| `la base cible existe déjà` | la base vivante n'a pas été supprimée/renommée | décision explicite requise : renommer ou supprimer la base, puis relancer |
| `verifier` renvoie des écarts « table … lignes » | restauration incomplète ou version différente | ne PAS remettre en service ; comparer le manifeste à la version de l'application au commit indiqué, re-restaurer |
| écarts « archive : … PERDU/ALTÉRÉ » | l'archive a bougé depuis la sauvegarde | c'est l'archive qu'il faut traiter (restaurer les fichiers depuis le dépôt d'origine ; l'archive n'est jamais reconstruite depuis la base) |
| `pg_restore a échoué` | version pg_restore < version du dump | utiliser les binaires de la même version majeure que la base sauvegardée (ici PostgreSQL 16) |

## 4. Durées mesurées (à publier depuis la CI)

La restauration de ~50 000 fiches semées est mesurée à CHAQUE run du job CI
`sauvegarde` (annotation `::notice title=sauvegarde-restauration-50k`).

> **Note sur l'état de la branche de base vs notre PR #21** :
> Le dernier commit de la branche de base `origin/arena/01a0c56d-seamtech-search` (`f42e29b`)
> était **ROUGE** en CI (run push `35731753595` et run PR `35731759715` en échec à l'étape 10 GARDE-FOU car `test_sauvegarde_unites.py`
> n'avait pas de marqueur `sauvegarde`, causant une collecte de 7 tests < 19 requis).
> Notre PR #21 apporte la réparation de ce job (`pytestmark = pytest.mark.sauvegarde`, tests de rétention
> locale symétrique, suppression des filtres aveugles et garde-fou renforcé `collectés == exécutés`).
> **La base `f42e29b` reste ROUGE jusqu'à la fusion de la PR #21 ; le run de fusion (événement pull_request) est vert 8/8, donc la fusion rétablit la base.**

| Environnement | Restauration 50 000 fiches | Dump | Note |
|---|---|---|---|
| CI GitHub (ubuntu-latest, job `sauvegarde` — run PR 35745844265) | 1,01 s | 560 563 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `0c2a4a2`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run push 35745835236) | 0,88 s | 560 565 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `0c2a4a2`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run PR 35743439266) | 0,62 s | 560 566 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `9eb46a3`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run push 35743433330) | 0,74 s | 560 566 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `9eb46a3`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run PR 35742670756) | 0,99 s | 560 567 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `1e85b86`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run push 35742665674) | 1,01 s | 560 568 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `1e85b86`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run PR 35738944082) | 0,99 s | 560 566 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `ad55bfb`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run push 35738937509) | 1,34 s | 560 566 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `ad55bfb`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run PR 35738011591) | 0,68 s | 560 566 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `13c7d96`) |
| CI GitHub (ubuntu-latest, job `sauvegarde` — run push 35738002767) | 0,98 s | 560 566 octets | mesuré en CI contre MinIO réel et PostgreSQL 16 pgvector, 23/23 tests passés (commit `13c7d96`) |
| Sandbox de développement (pgserver local) | 0,82 s (mesure locale du 22/09/2026) | 560 769 octets | ordre de grandeur seulement — matériel non représentatif |

## 5. Garde-fous d'exploitation

- **Disque** : l'import refuse d'écrire sous `SEAMTECH_MIN_FREE_BYTES`
  (erreur 507, testé `tests/test_chaos.py` — aucun purge partielle). Valeur
  par défaut : voir `seamtech_search/config.py`. Les sauvegardes LOCALES dans
  `data/backups` sont éligibles à purge manuelle : seul le bucket fait foi.
- **Supervision minimale** : `curl -f http://127.0.0.1:8000/health`
  (contrôle quotidien — voir QUE_FAIRE_SI.md).
- **Rotation des journaux** : bornée dans `docker-compose.yml`
  (`json-file`, 10 Mo × 5 fichiers par service).
