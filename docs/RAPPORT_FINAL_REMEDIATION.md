# Rapport final de remédiation — 2 novembre 2026

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
| **P1** — Windows natif | **NON PRÊT** | refus **nommé** du produit ; 392 échecs, **275 expliqués et nommés**, 117 encore sans cause publiée — voir §5 |
| **P1** — Docker Desktop | **NON QUALIFIABLE ICI** | un runner GitHub ne fournit pas de conteneurs Linux ; la preuve devra être faite sur le poste réel, jamais en premier |
| **P1** — volume annuel | **MESURÉ, PARTIEL** | banc synthétique 10 000 fiches, p95 ≤ 211,68 ms pour une cible indicative de 250 ms ; ni PDF réels, ni réseau, ni stockage objet |
| **P2** — matrice, multipart, pannes, sécurité, accessibilité | **PRÊT** | énumération automatique des routes, coupure réelle du corps, panne injectée, traversée neutralisée, 77 écrans scannés |
| **P2** — endurance « plusieurs jours » | **PARTIEL** | invariants tenus à 25 et 300 cycles avec panne injectée ; **aucune exécution de plusieurs jours d'horloge** |

**Intégration continue** : run `41467111707`, commit `48e8be2` — **13 jobs sur
13 verts** (`frontend`, `backend` 3.11/3.12/3.13, `e2e`, `ocr`, `sauvegarde`,
`integration`, `docker`, `recette-corpus-reel`, `recette-locale`,
`hors-ligne-reel`, `securite-dependances`).

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

## 5. Windows natif : ce qui est mesuré, nommé, et ce qui reste

Run `41467111570` (commit `48e8be2`), suite « sans service » :
**392 échecs, 1913 passés, 64 sautés** sur 3 230 tests collectés
(9 min 21 s). Les 314 échecs annoncés lors des runs précédents étaient un
résultat **tronqué** (suite interrompue avant la fin) : le chiffre réel est
392.

**277 de ces 392 échecs sont expliqués et nommés**, par trois méthodes :

1. **246 tests** dont la dépendance aux primitives POSIX est **démontrée** :
   la suite est rejouée avec toutes les primitives déclarées absentes
   (`PYTHONPATH=.:ci python -m pytest -p plugin_simulation_sans_primitives …`),
   moins ceux qui échouent aussi sans la simulation. Chaque ligne de
   `ci/ecarts-windows.txt` porte le constat mesuré ;
2. **11 causes** lues dans le rapport JUnit du run (`ci/resumer_echecs.py`) :
   156 échecs `verrou_flock`, 66 « Lecture confinée du bundle indisponible »,
   24 `scan_ancre_descripteur`, 16 `ModuleNotFoundError: fcntl`, etc.

3. **12 échecs OCR** dont la cause est désormais un **contrat testé** : hors
   POSIX, le produit refuse le rendu et l'extraction d'image bornés
   (`os.name != "posix"`), et `tests/test_ocr_refus_non_posix.py` le vérifie
   sur les deux plateformes — refus, aucun sous-processus, aucun fichier
   laissé, plateforme nommée. Le test d'arrêt de groupe (`os.killpg`), lui,
   était déjà nommé mais **ne se reconnaissait pas** : Windows écrit
   `<module 'os' (frozen)> has no attribute 'killpg'` alors que l'écart
   cherchait `module 'os' has no attribute …`.

**115 échecs restent non expliqués.** Leurs familles sont publiées à chaque run
et ne sont **pas** maquillées en écarts : faute de raison démontrée, les nommer
serait une dissimulation. Jusqu'ici le canal lui-même était tronqué — 34
identifiants publiés sur 128 — et le verdict remplit désormais des seaux
d'annotations : chaque échec inattendu sortira **avec la cause lue dans le
rapport JUnit**. On y trouve notamment des tests qui simulent des binaires
externes par des scripts `#!/bin/sh` (`test_garde_fous_preparation`, 20 échecs
— cause établie, deux identifiants nommés) et des tests de permissions
(`chmod` sans effet réel sur Windows).

Deux causes longtemps ouvertes ont été **élucidées et corrigées**
(commit `de3ecdf`) : le produit stocke `path_key = normcase(resolve(path))`,
donc **tout en minuscules** sous Windows, alors que les tests cherchaient par
le chemin brut — d'où `TypeError: 'NoneType' object is not subscriptable` et
« document introuvable : dossier-client/fiche-A.txt ». La « cible
diagnostique » du workflow, qui rejouait ces six tests un par un, est passée
**rouge → vert** sur ce run.

**Position produit, inchangée** : Windows natif n'est pas une cible de
déploiement. Le produit refuse avec un message qui nomme la capacité
manquante, la conséquence et la plateforme (`seamtech_search/plateforme.py`) ;
le déploiement Windows se fait dans un **conteneur Linux** (Docker Desktop), où
ces tests passent et sont couverts par la CI Linux.

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

# Restauration coordonnée, comme l'exploitante
python -m seamtech_search.sauvegarde sauver --archive <racine> --dossier <sauvegardes> \
  --data ./data --avec-references
python -m seamtech_search.sauvegarde restaurer --coordonnee --manifeste <manifeste> \
  --data-cible <cible> --fichiers-cible <cible> --restaurer-objets
```

**Journal de bord complet** (preuves, runs, SHA, échecs et corrections, §1 à
§50) : `docs/REMEDIATION_ETAT_EN_COURS.md`.
