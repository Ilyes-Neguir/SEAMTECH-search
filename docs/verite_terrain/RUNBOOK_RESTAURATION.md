# Runbook de restauration — sauvegarde hors-site éprouvée (Lot H.1)

> **Avertissement de remédiation : NON PRÊT pour une reprise complète.** Les
> essais historiques ci-dessous portent sur un périmètre limité et ne prouvent
> ni la sauvegarde intégrale de `data`, ni la cohérence entre le dump et les
> octets des fichiers/objets copiés, ni la restauration coordonnée des composants.
> Les références SQL sont lues dans le même snapshot que le dump. `sauver`
> produit encore une copie partielle, signale `protection_globale_prouvee=false`
> et sort avec le code 2. La rétention automatique est suspendue pour éviter
> d'effacer d'anciennes copies ; surveiller l'espace disponible. Ne pas utiliser
> ce runbook comme validation de production.
>
> Pour chaque copie de pièce demandée, le manifeste indique désormais
> `identite_catalogue=verifiee` lorsque les octets copiés correspondent au SHA
> de capture connu dans le snapshot PostgreSQL, ou `inconnue` pour les lignes
> historiques sans SHA. Une divergence est listée comme manquante, la copie
> divergente n'est pas publiée et `complet_perimetre_demande=false`. Cette
> comparaison ne prouve ni l'authenticité du manifeste, ni la cohérence de
> toutes les données ou leur restauration coordonnée.

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

## 2. Commandes (CLI portable — Linux, macOS, Windows/PowerShell)

La sauvegarde est un module Python : aucune dépendance PowerShell, mêmes
commandes partout. Les binaires `pg_dump`/`pg_restore` sont découverts via
`PATH`, `SEAMTECH_PG_BINDIR`, `/usr/lib/postgresql/*/bin` (sans binaire,
l'échec est explicite — jamais de repli silencieux). Configurer l'URL de la
base source via `SEAMTECH_DATABASE_URL` et celle de la base de restauration
neuve via `SEAMTECH_RESTORE_DATABASE_URL`, par le gestionnaire de secrets du
service. Ne pas les afficher ni les passer en arguments ; l'environnement
reste toutefois accessible selon les permissions du système.

### Sauvegarder (à planifier, ex. quotidienne)

```bash
python -m seamtech_search.sauvegarde sauver \
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
bucket configuré. La rétention automatique est actuellement suspendue :
aucune ancienne copie n'est purgée tant que la protection globale n'est pas
qualifiée. Le verrou de sauvegarde ne modifie pas le contenu du fichier
`sauvegarde.lock` : sa simple présence ne prouve pas qu'une sauvegarde tourne.
La version actuelle verrouille également le dossier ; elle résiste à la
suppression du fichier entre deux processus de même version sur Linux testé.
Il faut néanmoins conserver fichier et dossier en place : le remplacement du
dossier ou une coexistence avec une ancienne version peut contourner
l'exclusion. Sans primitive de verrouillage sûre, la sauvegarde refuse de
commencer ; Windows et les volumes réseau restent non qualifiés.

### Restaurer (base détruite ou corrompue)

```bash
# Depuis le bucket seul (recommandé — c'est le scénario prouvé en CI) :
python -m seamtech_search.sauvegarde restaurer \
  --cle-manifeste "backups/seamtech-search-<horodatage>.dump.manifest.json"

# Depuis un manifeste local :
python -m seamtech_search.sauvegarde restaurer \
  --manifeste data/backups/<…>.dump.manifest.json
```

Sortie attendue :
```json
{"restaure": "seamtech_search", "duree_s": <durée mesurée>}
```
Sécurité : la base cible doit être NEUVE (une base existante est refusée,
jamais écrasée par surprise). Le dump local est d'abord copié dans un
répertoire temporaire privé, puis cette copie est vérifiée et utilisée par
`pg_restore`. Les composants du chemin source sont ouverts sans suivre de
liens symboliques ; le dump doit être un fichier ordinaire. Le nom du dump
indiqué par un manifeste local doit être un nom simple, sans répertoire. Un
dump explicitement choisi avec `--dump` reste possible, mais ses composants
ne doivent pas être des liens. Si l'ouverture confinée n'est pas prise en
charge, la restauration refuse de continuer (Windows non qualifié).
Avant la création de la base, `pg_restore` lit l'archive et produit son SQL
vers une sortie supprimée, sans l'exécuter : cela détecte notamment une
version d'archive incompatible et la troncature de données testée. Cette
lecture supplémentaire consomme du temps et des ressources ; elle n'a pas de
délai limite et n'a pas été qualifiée sur le volume annuel réel. Elle ne
prouve pas que l'exécution SQL sur la base réussira, ni que l'archive ne sera
pas modifiée par un autre processus du même compte après contrôle.

Lors de la sauvegarde des pièces locales référencées, la source est hachée
avant et après copie et la copie est relue avant publication sans écrasement.
La source est ouverte par descripteurs sans suivre de liens symboliques : les
chemins de fichiers référencés contenant des liens ou `..` sont désormais
refusés. Les chemins du dump de restauration contenant `..` sont également
refusés, y compris lorsqu'ils sont fournis explicitement ; utiliser un chemin
sans traversée. Une
substitution de l'identité de la source est également recherchée après copie.
Une différence interrompt la sauvegarde ; cela ne fige pas les octets au
snapshot PostgreSQL, et une copie déjà publiée peut subsister sans manifeste
si la source change après sa publication. Ce mécanisme exige les primitives
POSIX de publication du fichier et ne qualifie pas Windows ou les volumes réseau.
Les objets référencés sont téléchargés d'abord dans un répertoire temporaire
privé, puis copiés et relus avant publication sans écrasement dans le bundle.
Il faut prévoir l'espace correspondant. La taille, l'ETag et, lorsqu'elle est
fournie, la version de l'objet sont contrôlés avant et après téléchargement ;
une taille incorrecte, un changement ou des métadonnées indisponibles signalent
un objet manquant. Si une version non `null` est fournie, elle est imposée au
téléchargement : le client et le fournisseur doivent prendre en charge cette
lecture, sinon l'objet est signalé manquant sans repli. La version S3 `null`
est mutable et ne fournit pas cette protection. L'épinglage dépend du respect de
la demande par le fournisseur et a été testé sur un MinIO historique isolé,
pas sur R2 ni toutes les versions MinIO. Un ETag est traité comme identifiant
opaque, pas comme un hachage des octets : ces contrôles ne prouvent pas
l'intégrité du contenu livré ni la version correspondant au snapshot PostgreSQL.

Prévoir au moins la taille du dump en espace libre temporaire supplémentaire ;
depuis S3, le téléchargement et la copie vérifiée coexistent (deux tailles de
dump temporaires). `SEAMTECH_SAUVEGARDE_TMP` permet de choisir leur répertoire
parent ; sinon, le téléchargement utilise `data/backups` et la copie le
répertoire temporaire du système. Le nettoyage normal a lieu au succès et en
cas d'erreur gérée, pas nécessairement après arrêt brutal. Les permissions
privées ne protègent pas contre un processus hostile du même compte ni un
administrateur. Une erreur après création de la base peut laisser une base
partielle : ne pas la considérer comme restaurée. La vérification d'une copie
ne prouve ni l'authenticité du manifeste ni la cohérence des autres composants.

Les pièces locales et objets demandés sont également copiés en zone privée et
revérifiés avant la création de la base. Leur lecture refuse les liens
symboliques dans les chemins internes du bundle et les fichiers non ordinaires ;
ce confinement par descripteurs est limité aux plateformes prises en charge.
Cela requiert autant d'espace libre
supplémentaire que leur taille totale, en plus du dump et, le cas échéant,
du téléchargement S3. Pour chaque fichier local, une copie temporaire est
écrite dans le dossier cible, relue et comparée à l'empreinte du manifeste,
puis publiée par lien dur sans écraser une cible existante : le nom final
n'apparaît pas pendant l'écriture. La relecture porte sur les octets lus par
le système à cet instant, sans preuve de durabilité physique. Ce mécanisme refuse
les systèmes ne prenant pas en charge les opérations nécessaires et les liens
dans les répertoires ; Windows et les volumes réseau ne sont pas qualifiés.
Un arrêt brutal peut laisser un fichier `.seamtech-restore-*` temporaire ; il
ne doit pas être supprimé pendant une restauration active. La publication n'est
pas garantie durable après coupure électrique, et l'accès concurrent hostile au
répertoire n'est pas couvert. La publication de plusieurs fichiers n'est pas
transactionnelle : une panne peut laisser les premiers fichiers. La restauration
d'objets non vide est désormais **refusée avec le client S3 actuel**, avant la
création de base ou la publication des fichiers dans la commande CLI : aucune
création exclusive atomique n'est qualifiée pour ce client. Ne pas contourner
ce refus par un HEAD préalable ou un PUT ordinaire. L'interface interne réservée
à un futur adaptateur exige une création atomique sur la clé exacte ; sa simple
présence ne constitue pas une qualification fournisseur et les seuls
adaptateurs testés pour la concurrence sont simulés. Plusieurs références à
une même clé doivent avoir des empreintes identiques. Une éventuelle future
création sera relue, mais un échec après écriture peut laisser un objet et la
relecture ne garantit pas qu'un autre processus ne le modifiera pas ensuite.
Ce mécanisme ne garantit ni rollback complet ni cohérence entre base, fichiers
et objets ; ne pas l'utiliser comme reprise de production.

### Vérifier (après restauration, ou en contrôle périodique)

Pour vérifier la base restaurée, configurer `SEAMTECH_DATABASE_URL` sur cette
base cible dans le contexte d'exécution, sans afficher l'URL.

```bash
python -m seamtech_search.sauvegarde verifier \
  --manifeste data/backups/<…>.dump.manifest.json \
  --archive "D:/SEAMTECH/DesignFiles"
```

Sortie attendue : `{"ok": true, "ecarts": []}` et code retour 0.

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

Les envois de fichiers et d'octets demandent un checksum d'ingestion SHA-256.
L'application ne relance plus un envoi sans checksum après une erreur : le
fournisseur pourrait déjà avoir écrit l'objet malgré une réponse perdue. Un
fournisseur ou une configuration qui ne prend pas en charge cet envoi échoue
désormais ; sa compatibilité doit être qualifiée avant usage. Une erreur laisse
l'état distant potentiellement ambigu. Le client S3 partagé utilise une seule
tentative botocore par opération, y compris pour les lectures : une panne
transitoire peut donc être remontée plus souvent. Le client distinct de sonde
reste configuré pour ses propres réessais. Cela ne couvre ni tous les niveaux
de réessai ni toute panne multipart ; la sélection d'une clé libre n'est pas une
création atomique. Ne pas considérer cette modification comme une preuve
d'absence d'écrasement concurrent.
