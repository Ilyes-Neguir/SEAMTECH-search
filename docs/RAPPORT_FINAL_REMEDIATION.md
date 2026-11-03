# Rapport final de remédiation — 3 novembre 2026

**Branche** : `arena/b936b3c0-seamtech-search` (jamais fusionnée dans `main` :
aucune fusion n'a lieu sans accord explicite).
**Objet** : exécuter et terminer le plan de remédiation des deux rapports du
28/10/2026 — P0 (sauvegarde/restauration), P1 (OCR, rotations, plateformes,
volume), P2 (matrice, multipart, pannes, sécurité, accessibilité, endurance).
**Méthode** : chaque exigence est prouvée par un test automatisé réel, rejoué,
et cité avec sa commande, son résultat et le SHA du commit. Ce qui n'est pas
prouvé n'est pas déclaré prêt. Les limites sont dites, pas contournées.

---

## 1. État d'ensemble

| Axe | État | Motif |
|---|---|---|
| **P0** — sauvegarde, restauration, hors-site, rétention, migration | **PRÊT** | toutes les exigences sont prouvées, y compris la restauration coordonnée **par la commande** (dernier point ouvert, fermé ce jour) |
| **P1** — OCR, rotations, PostgreSQL 16/pgvector/Redis 7 | **PRÊT** | jobs verts, `xfail` retirés, campagnes rejouées |
| **P1** — Chrome / Edge | **PRÊT** | run 41499874660 : `attendus=2 passes=4 echecs=0`, Chrome 156.0.8078.80 et Edge 156.0.4314.56 réels, 5 écrans chacun, **0 violation** WCAG 2.1 A/AA |
| **P1** — Windows natif | **NON PRÊT (décision produit, assumée)** | suite mesurée ENTIÈREMENT (run 41578726322, `e109bb0`) : **369 échecs, TOUS nommés**, plus aucun échec sans cause démontrée — voir §5 |
| **P1** — Docker Desktop | **NON QUALIFIABLE ICI** | un runner GitHub ne fournit pas de conteneurs Linux ; la preuve devra être faite sur le poste réel, jamais en premier |
| **P1** — volume annuel | **MESURÉ, PARTIEL** | banc synthétique 10 000 fiches, p95 ≤ 211,68 ms pour une cible indicative de 250 ms ; ni PDF réels, ni réseau, ni stockage objet |
| **P2** — matrice, multipart, pannes, sécurité, accessibilité | **PRÊT** | énumération automatique des routes, coupure réelle du corps, panne injectée, traversée neutralisée, 77 écrans scannés |
| **P2** — endurance « plusieurs jours » | **PARTIEL** | invariants tenus à 25 et 300 cycles avec panne injectée ; **aucune exécution de plusieurs jours d'horloge** |

**Intégration continue** : run `41578726332`, commit `e109bb0` — **13 jobs sur
13 verts** (`frontend`, `backend` 3.11/3.12/3.13, `e2e`, `ocr`, `sauvegarde`,
`integration`, `docker`, `recette-corpus-reel`, `recette-locale`,
`hors-ligne-reel`, `securite-dependances`), et **run `41582475951`, commit
`4f8ed46` — 13 jobs sur 13 verts** également. Qualifications parallèles, mêmes
commits : `native` (Red Hat 10) ✅, `reserves` (PostgreSQL 16 + Redis 7) ✅,
`endurance` ✅, `perf` ✅.

`e109bb0` est le **dernier commit qui touche du code** ; `4f8ed46` et les
suivants ne touchent que `docs/`. La suite Windows citée au §5 a été mesurée
deux fois sur ce même code (runs `41578726322` et, à `4f8ed46`, `565,96 s`,
mêmes chiffres) : la mesure est donc celle du code livré.

**Suite locale complète** (sélection exacte, `PYTHONPATH=$PWD .venv/bin/python
-m pytest -q -p no:randomly --tb=line -rf -m "not postgres and not s3 and not
perf and not redis_queue and not ocr_suite"`, hors sept fichiers de banc
lourd) : **2 354 passés, 47 sautés, 3 xfail, 0 échec** en 200,7 s. Frontend :
`pnpm exec tsc --noEmit` **sans aucune erreur**, `node --test lib/*.test.ts`
**15 tests, 15 passés**.

---

## 2. P0 — sauvegarde, restauration, hors-site, rétention, migration

| Exigence | État | Commande / fichier | Résultat |
|---|---|---|---|
| Sauvegarde PostgreSQL + `./data` + objet, un seul `sauver` à la fois | PRÊT | `pytest tests/test_sauvegarde_complete.py tests/test_sauvegarde_unites.py` | 94 passés / 3 écarts nommés |
| Verrou opposable (« une sauvegarde est déjà en cours ») | PRÊT | `pytest tests/test_verrou_sauvegarde_plateforme.py` | 5 tests ; errnos EACCES/EDEADLK/EWOULDBLOCK traduits |
| Verrou **sans `flock`** (Windows : `msvcrt.locking`) | PRÊT | même fichier | ordre parent→dossier, libération inverse ; POSIX inchangé (test de frontière) |
| Restauration : dump vérifié avant toute écriture, base partielle signalée | PRÊT | `pytest tests/test_restauration_dump_stable_pg.py` | 7 tests |
| Restauration des fichiers locaux et des objets | PRÊT | `pytest tests/test_sauvegarde_references.py tests/test_restauration_bundle_s3rver.py` | verts |
| Hors site | PRÊT | `pytest tests/test_sauvegarde_hors_site_integree.py tests/test_sauvegarde_manifeste_distant.py` | verts |
| Rétention / purge locale et worker | PRÊT | `pytest tests/test_purge_locale.py tests/test_purge_worker.py` | politique : aucun artefact sans clé ni non vérifié |
| Migration et retour arrière sur copie représentative | PRÊT | `pytest tests/test_migration_rollback.py` | 4 passés |
| **Restauration COORDONNÉE par la commande** | **PRÊT** | `pytest tests/test_sauvegarde_cli_coordonnee.py` | **2 passés** — voir §2.1 |

### 2.1 Le dernier point ouvert de P0, fermé

`restaurer_complet` était testée **comme fonction** ; elle ne l'était pas
**comme commande** — c'est-à-dire par le chemin que l'exploitante prendra.
`tests/test_sauvegarde_cli_coordonnee.py` comble ce trou :

1. **deux processus réels** (`subprocess`) : `sauver --sans-s3 --data`,
   destruction de l'arborescence, puis `restaurer --coordonnee --data-cible`.
   Le manifeste est écrit par le premier processus et **relu sur le disque** par
   le second — plus rien n'est partagé en mémoire. Base + `./data` + contrôle
   contre le manifeste ;
2. **le périmètre complet** : `--avec-references` puis
   `restaurer --coordonnee --restaurer-objets` (base + données + fichiers
   référencés + objets). Le manifeste ne devient « protection globale prouvée »
   qu'**après** le contrôle final, et la rétention n'est autorisée qu'ensuite.

Commit `973d74c`, corrigé sur `eb23449` (découverte de `pg_dump` : le test
utilise désormais `sauvegarde._trouver_binaire`, celle du produit, au lieu de
supposer `pgserver`).

---

## 3. P1 — OCR, rotations, plateformes, volume

| Exigence | État | Preuve |
|---|---|---|
| OCR exact en CI, **sans assertion affaiblie ni réessai salvateur** | PRÊT | job `ocr` vert sur `48e8be2` ; provenance OCR corrigée (§23 du journal de bord) |
| Rotations PDF 90/180/270 avec tableaux et coordonnées, `xfail` retirés | PRÊT | `pytest tests/test_pdf_orientation.py tests/test_pdf_orientations_mixtes_tables.py` — 95 passés |
| PostgreSQL 16 + pgvector + Redis 7 réels | PRÊT | CI Linux (services conteneurs) **et** runner Windows (installation hôte, PG 16 + pgvector + Redis 7 épinglés) |
| Chrome / Edge | **PARTIEL** | voir §3.1 |
| Docker Desktop | **NON QUALIFIABLE ICI** | voir §6 |
| Volume annuel | MESURÉ, PARTIEL | `python scripts/scale_bench.py` — 10 000 fiches, p95 max **211,68 ms** pour une cible indicative de **250 ms** (`/tmp/scale-bench-local.json`) |
| Windows natif | **NON PRÊT** | voir §5 |

### 3.0 Un défaut réel trouvé en cherchant ailleurs : sonder un PID ne doit pas le tuer

En cherchant pourquoi la suite de qualification Windows s'arrêtait en route
(quatre runs consécutifs à `rc=2`, ~380 tests sur 2 369 qui ne tournaient pas),
la fouille a mis au jour un défaut indépendant, et grave :

`os.kill(pid, 0)` est l'idiome POSIX pour demander « ce PID vit-il encore ? » —
le signal 0 n'est pas délivré. **Sur Windows, `os.kill` ne connaît pas le signal
0 : il appelle `TerminateProcess` avec la valeur reçue comme code de sortie.**
Un test de vie y devient une mise à mort, et comme les PID sont recyclés, le
processus tué n'est pas forcément celui dont on a lu l'identifiant dans le
fichier de verrou. L'idiome était employé dans le verrou de scan SQLite
(`indexer.scan_lock`) et dans le verrou de la nuit OCR (`ocr.etat`) : « reprendre
un scan après un crash » pouvait tuer un processus vivant.

Correction (commit `1abd6b2`) : `plateforme.pid_vivant` interroge sans jamais
tuer — idiome POSIX conservé là où il est exact, `OpenProcess` +
`GetExitCodeProcess` par `ctypes` sur Windows, réponse « vivant » sur une
plateforme inconnue (un verrou périmé reste un verrou ; supprimer le verrou d'un
processus vivant ne l'est pas). `tests/test_pid_vivant.py` verrouille le
contrat : sur la branche non POSIX, **`os.kill` n'est jamais appelé** —
réintroduire l'idiome fait échouer le test au lieu de tuer un processus.

Effet mesuré, et il n'était pas cherché : **la suite Windows s'est remise à
aller au bout** (`rc=1`, 393 échecs, 1 920 passés) dès l'exécution suivante,
après quatre interruptions consécutives. Le mécanisme exact de l'interruption
n'est pas démontré ; le lien de causalité, lui, est établi par la répétition —
quatre runs interrompus avec l'idiome, un run complet sans lui, sans autre
changement de produit.

### 3.1 Chrome et Edge : qualifiés, après une violation trouvée et corrigée

Workflow `qualification-navigateurs-windows.yml`, runner `windows-latest`,
**binaires réels** de la machine (`channel: "chrome"`, `channel: "msedge"`).

* Run `41467111620` (`48e8be2`) : **2 échecs** — l'écran `/fiches`, règle
  `color-contrast`, dans les deux navigateurs.
* Run `41497594641` (`d037bcf`) : la violation est enfin **nommée** (la spec
  publie règle, gravité et cibles dans le message d'échec) — cibles
  `span[data-testid="nav-groupe-…"]`.
* Run `41499874660` (`6f2355d`) : **`attendus=2 passes=4 echecs=0`, `rc=0`**.
  Chrome 156.0.8078.80 et Edge 156.0.4314.56 (agent-utilisateur
  `Edg/153.0.8010.12`), cinq écrans chacun, **0 violation** WCAG 2.1 A/AA,
  aucune erreur de console produit.

Le défaut était réel, mesuré hors navigateur (composition alpha sRGB) :
`text-muted-foreground/80` donnait **3,52:1** en thème jour (seuil AA : 4,5), et
`text-primary/80` **3,46:1** — ce dernier n'était signalé par personne, son
élément n'apparaissant que si le backend sert un jeu d'exemple. L'opacité est
retirée : le token plein donne 5,30:1 et 5,04:1. Trois trous sont fermés avec
(commit `6f2355d`) : un test unitaire qui mesure la **source**
(`frontend/lib/contraste.test.ts`, 15 tests, en CI), l'ajout de `/fiches` à
l'audit Linux (écran non scanné = non vérifié, pas conforme), et le figement
des animations avant analyse — une mesure prise pendant la révélation (opacité
0 → 1) fabrique de faux échecs de contraste.

Deux détails publiés sans être assertés, en toute honnêteté : 5 erreurs de
console dont des `503` anonymes. La spec publie désormais le couple
**statut + chemin** ; la prochaine exécution nommera les routes concernées.

---

## 4. P2 — matrice, multipart, pannes, sécurité, accessibilité, endurance

| Exigence | État | Preuve |
|---|---|---|
| Matrice route × rôle × session × état métier | PRÊT | `tests/test_matrice_roles_routes.py` (routes énumérées depuis OpenAPI, six identités, aucun 500), `test_matrice_routes_auth.py`, `test_passe_finale_sessions.py`, `test_bff_multipart_borne.py` |
| Multipart en flux : charge, interruption, concurrence | PRÊT | `tests/test_multipart_flux.py` — 8 passés : coupure réelle du corps à 40 %, refus 413, deux envois concurrents, profondeur 1/8/32 |
| Injections de pannes | PRÊT | `tests/test_chaos.py`, `test_worker_crash_terminal.py`, endurance avec panne tous les 7 cycles |
| Sécurité offensive | PRÊT | `tests/test_securite_offensive.py` — 8 passés ; traversée neutralisée prouvée par sonde hors test |
| Accessibilité axe-core étendue | PRÊT | **82 écrans scannés, 0 violation** WCAG 2.1 A/AA (job `e2e`, run 41499874961, `/fiches` désormais inclus) + 5 écrans × 2 navigateurs réels (Windows) ; garde-fou de palette `frontend/lib/contraste.test.ts` |
| Endurance « plusieurs jours » | **PARTIEL** | `tests/test_endurance.py` : invariants tenus à 25 et 300 cycles avec panne injectée ; **aucune exécution de plusieurs jours d'horloge** |

### 4.1 Un défaut grave trouvé et corrigé pendant cette campagne

En simulant une plateforme sans primitives POSIX, un test ne rendait plus la
main : l'hôte était tué par épuisement de mémoire (rc=137), sans message.
Cause : `api.py` lisait le corps distant avec
`iter(lambda: corps.read(1 Mio), b"")`. Dès que `read()` rend autre chose que
`b""` — `None`, une chaîne, un objet, ou des octets sans fin — **la boucle ne
s'arrête jamais**.

Correctif : nouveau module `seamtech_search/flux_bornes.py`
(`lire_par_blocs`), qui refuse un bloc non-octets, applique une borne et
refuse une taille de bloc nulle ou négative. Les **dix** idiomes
`iter(lambda…)` du produit y passent ; la route API impose une borne (taille
annoncée par le stockage, sinon taille maximale de pièce). Preuve :
`tests/test_flux_bornes.py` — **11 tests** ; le corps aberrant lève au bout de
quatre lectures, donc le test **échoue** au lieu de bloquer la suite. Le
fichier qui ne rendait jamais la main s'exécute en **4,94 s** (commit
`a338f21`).

---

## 5. Windows natif : 369 échecs, et plus un seul sans cause

**Run `41578726322`, commit `e109bb0`, suite « sans service »** : `369 failed,
1939 passed, 79 skipped, 859 deselected, 2 xfailed, 16 errors` (`rc=1`,
500 s). Verdict publié par le workflow lui-même :

> `369 echec(s), TOUS nommes dans ci/ecarts-windows.txt — limite de plateforme
> connue, pas une regression. A revoir a chaque correctif.`

Le nombre d'échecs **sans cause démontrée** est passé de 94 à **zéro** en cinq
runs :

| Run | Commit | Échecs | Sans cause |
|---|---|---|---|
| `41523086709` | `a0f5efd` | 393 | 94 |
| `41551125074` | `55940c6` | 390 | 37 |
| `41564776168` | `c172f87` | 382 | 9 |
| `41574825812` | `b02aff1` | 370 | 1 |
| **`41578726322`** | **`e109bb0`** | **369** | **0** |

Chacune des 300 lignes d'écart de `ci/ecarts-windows.txt` (283 écarts par test,
17 causes transverses) porte une raison **démontrée**, jamais supposée, par
l'une de ces quatre méthodes :

1. **246 tests** dont la dépendance aux primitives POSIX est prouvée : la suite
   est rejouée avec toutes les primitives déclarées absentes
   (`PYTHONPATH=.:ci python -m pytest -p plugin_simulation_sans_primitives …`).
   Un test qui échoue **sous** la simulation et passe **sans** elle dépend de
   ces primitives — c'est une raison établie, pas une opinion ;
2. **17 causes transverses** lues dans le rapport JUnit (`ci/resumer_echecs.py`)
   : 156 `verrou_flock`, 66 « lecture confinée du bundle », 24
   `scan_ancre_descripteur`, 16 `ModuleNotFoundError: fcntl`… ;
3. **des traces ciblées** : le job `diagnostic-causes` publie, pour les tests
   dont la cause lue ne suffit pas, les cadres **PRODUIT** de la trace
   (`ci/publier_traces.py`, filtre `_utile`) — le milieu d'une trace tombe dans
   Starlette et n'apprend rien, on ne le publie plus ;
4. **la reproduction sans Windows**, chaque fois qu'elle est possible. Exemple
   décisif : vider `os.supports_dir_fd` déclenche, sur n'importe quelle
   machine, exactement le refus que produit Windows
   (`tests/test_plateforme_sans_dir_fd.py`, 4 tests). Ce qui ne se prouve que
   sur Windows est dit « non démontré » ; ce qui se reproduit ailleurs est
   prouvé.

### 5.1 Ce que cette qualification a rapporté de concret

Elle n'a pas seulement nommé des écarts : elle a fait corriger des défauts
**produit** qu'un banc Linux seul n'aurait jamais vus. Cinq, dans cette
campagne :

1. **Un manifeste écrit avec le séparateur de la machine qui le produit.**
   `sauvegarde.py` écrivait `str(chemin.relative_to(racine))` →
   `CLIENT-A\\fiche.pdf` sur Windows. Restauré sous Linux, l'antislash n'est
   pas un séparateur : on obtenait un fichier **dont le nom contient un
   antislash**. Une sauvegarde illisible ailleurs que sur la machine qui l'a
   produite est l'inverse d'une sauvegarde hors site. Les quatre points
   d'écriture passent en `as_posix()` (commit `74f03e8`, preuve
   `test_inventaire_archive_chemins_portables`) ;
2. **L'inventaire d'archive hachait les verrous qu'il pose lui-même.**
   Sur POSIX, `flock` est consultatif : lire un fichier verrouillé passe, le
   défaut est invisible. Sur Windows une plage verrouillée est **exclusive**,
   y compris pour un autre descripteur du même processus : l'inventaire se
   prenait `PermissionError` sur son propre verrou et **la sauvegarde
   échouait**. Les deux noms de verrou sont écartés de l'inventaire (commit
   `55940c6`, preuve `test_inventaire_archive_ignore_les_verrous`) ;
3. **Sonder un PID le tuait** (`os.kill(pid, 0)` — voir §3.0) ;
4. **Une lecture non bornée** qui ne rendait jamais la main (§4.1) ;
5. **Une violation WCAG 2.1 A/AA sur `/fiches`**, trouvée par les vrais
   navigateurs et corrigée (§3.1).

### 5.2 Les neuf derniers échecs, un par un (2 novembre → 3 novembre)

Neuf échecs résistaient encore au run `41564776168`. Tous ont reçu une cause :

* **cinq** (`test_suppression_verrou…`, `test_verrou_absence_fcntl…`,
  `test_verrou_meme_objet…`, `test_verrou_ne_perd_pas_exclusion…`,
  `test_verrou_remplace…`) échouaient avec « une sauvegarde est déjà en cours ».
  Mécanique démontrée : six tests **simulent** `fcntl.flock` en le remplaçant
  par une panne ; sous Windows `fcntl` est `None`, la panne simulée n'a jamais
  lieu, et le verrou est **laissé pris**. Comme le verrou parent est posé dans
  le répertoire temporaire **commun** du banc, tous les tests suivants du même
  fichier échouent à l'acquisition — avec une panne qui n'a rien à voir avec ce
  qu'ils mesurent. Ces tests sautent désormais, raison écrite, là où la
  primitive simulée n'existe pas (commits `869db9c`, `e109bb0`) ;
* **trois** (`test_copie_dump_refuse_parent_substitue…`,
  `test_erreur_seconde_publication…`, `test_restore_cache_le_mot_de_passe…`)
  sont trois conséquences d'**un seul refus** : `_ouvrir_source_bundle`
  (`sauvegarde.py:877`) lève « Lecture confinée du bundle indisponible sur
  cette plateforme » dès que `os.open` n'est pas dans `os.supports_dir_fd` —
  donc **avant toute ouverture**. D'où `assert change` faux, `IndexError` sur
  une liste d'appels vide, et un `XPASS` qui réussit pour la mauvaise raison.
  Reproduit sans Windows par `tests/test_plateforme_sans_dir_fd.py` ;
* **un** (`test_declencheur_invalide_meme_les_reecritures_identiques_sqlite`)
  était un **test faux** : il interrogeait la base par `str(chemin)` alors que
  le produit range `path_key = normcase(resolve(chemin))` (`models.py:61-64`) —
  donc en minuscules sur les systèmes qui ignorent la casse. Corrigé côté test.

### 5.3 Position produit, inchangée

Windows natif n'est **pas** une cible de déploiement. Le produit refuse avec un
message qui nomme la capacité manquante, la conséquence et la plateforme
(`seamtech_search/plateforme.py`) ; le déploiement Windows se fait dans un
**conteneur Linux** (Docker Desktop), où ces tests passent et sont couverts par
la CI Linux. La différence avec le rapport précédent : il ne reste **aucun
échec dont on ignore la cause**. Ce qui est refusé est refusé, nommément.

---

## 6. Limites du bac à sable, dites sans détour

* **Docker Desktop** : un runner GitHub Windows ne fournit pas de conteneurs
  Linux. La qualification de la cible de déploiement Windows ne peut donc pas
  être faite ici ; elle devra l'être sur le poste réel, **jamais en premier**.
* **Journaux de job et artefacts non téléchargeables** depuis ce bac à sable
  (`…/jobs/<id>/logs` → EOF ; le téléchargement d'artefact échoue sur le
  stockage blob). D'où l'obligation de tout faire passer par des annotations,
  et le plafond de dix annotations par étape — contourné en publiant les
  familles de causes par **tranches** (étapes dédiées).
* **Pas d'interpréteur PowerShell** : `ci/verdict-windows.ps1` et les scripts
  associés sont écrits sans pouvoir être exécutés ici. Leur logique est donc
  volontairement simple, et chaque sortie est vérifiable dans les annotations.
* **Pas de banc PostgreSQL avec `contrib`** : `pg_trgm` et `unaccent` manquent
  localement (37 à 39 échecs locaux qui n'existent pas en CI, où PostgreSQL 16
  complet est fourni).
* **Aucune exécution de plusieurs jours** : l'endurance est prouvée par
  comptage de fuites sur 25 et 300 cycles, pas par une horloge.
* **Aucune donnée client réelle** dans ce dépôt, ni dans un test, ni dans un
  journal, ni dans un commit. Les jeux d'essai sont synthétiques ; les
  rapports ne parlent qu'en indices, booléens, comptages et statuts.

---

## 7. Compétences ECC appliquées, nommément

* **`search-first`** — chaque comportement existant a été localisé avant
  modification (les dix idiomes `iter(lambda…)`, les quatre appels à
  `exiger()`, les quinze requêtes `WHERE path_key = %s`).
* **`tdd-workflow`** — rouge → vert : le test de la boucle infinie a été écrit
  pour échouer **sans bloquer** ; les deux causes Windows ont été reproduites
  par diagnostic ciblé avant correction.
* **`verification-loop`** — chaque correctif rejoué, jamais cru : simulation
  sans primitives, rejeu des 61 tests PostgreSQL, contrôle de non-régression
  (580 passés, 3 échecs environnementaux connus).
* **`security-review`** — traversée neutralisée, en-têtes de rôle, refus
  nommés, bornes sur tout flux qui n'est pas maîtrisé.
* **`postgres-patterns` / `redis-patterns`** — migrations, retour arrière sur
  copie, instantané MVCC, files durables.
* **`error-handling`** — messages opposables : capacité, conséquence,
  plateforme ; aucune panne anonyme.
* **`verification-loop` poussé jusqu'au bout** — la règle appliquée à chaque
  échec Windows : « une cause ne s'écrit qu'avec une preuve rejouable ». Quand
  Windows n'était pas joignable, la preuve a été refaite ailleurs
  (`tests/test_plateforme_sans_dir_fd.py`, `plugin_simulation_sans_primitives`) ;
  quand elle ne pouvait pas l'être, l'échec est resté « non démontré » plutôt
  que d'être rangé dans une case commode.
* **`architecture-decision-records`** — les décisions de plateforme (Windows
  natif hors cible, écarts, bornes) sont écrites ici et dans
  `docs/REMEDIATION_ETAT_EN_COURS.md`, pas implicites.

---

## 8. Reproduire

```bash
# Suite locale complète (bancs PostgreSQL, Redis, S3rver requis)
SEAMTECH_TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:55498/postgres" \
SEAMTECH_TEST_REDIS_URL="redis://127.0.0.1:6390/1" \
SEAMTECH_TEST_REDIS_SERVER=/tmp/redis-banc/redis-7.4.7/src/redis-server \
SEAMTECH_TEST_S3RVER_URL="http://127.0.0.1:14569" \
PYTHONPATH=$PWD .venv/bin/python -m pytest -q -rf -p no:randomly \
  -m "not perf and not endurance and not recette_corpus and not ocr_suite and not integration_docker and not minio_historique"

# Simulation d'une plateforme sans primitives POSIX (établit les écarts)
PYTHONPATH=$PWD:ci .venv/bin/python -m pytest -q -p no:randomly \
  -p plugin_simulation_sans_primitives <sélection>

# Lecture des causes d'un run Windows
python ci/resumer_echecs.py logs/junit-<suite>.xml logs/causes-<suite>.txt \
  --details logs/details-<suite>.txt --exemples 25

# Ce que le produit fait quand le système n'ouvre pas par descripteur (Windows)
PYTHONPATH=$PWD .venv/bin/python -m pytest -q -p no:randomly tests/test_plateforme_sans_dir_fd.py

# Une plateforme qui n'a PAS les primitives POSIX (établit les écarts Windows)
PYTHONPATH=$PWD:ci .venv/bin/python -m pytest -q -p no:randomly \
  -p plugin_simulation_sans_primitives <sélection>

# Frontend : types et tests unitaires
cd frontend && pnpm install --frozen-lockfile && pnpm exec tsc --noEmit && node --test lib/*.test.ts

# Restauration coordonnée, comme l'exploitante
python -m seamtech_search.sauvegarde sauver --archive <racine> --dossier <sauvegardes> \
  --data ./data --avec-references
python -m seamtech_search.sauvegarde restaurer --coordonnee --manifeste <manifeste> \
  --data-cible <cible> --fichiers-cible <cible> --restaurer-objets
```

**Journal de bord complet** (preuves, runs, SHA, échecs et corrections, §1 à
§66) : `docs/REMEDIATION_ETAT_EN_COURS.md`.
