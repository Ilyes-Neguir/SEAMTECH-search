# RAPPORT — Lot I « assistant sourcé » (22/09/2026)

Session : `arena/01a0ca1a-seamtech-search` (base : `main` @ c0d5e45).
Livraison : **assistant EXTRACTIF sourcé** — la dernière fonctionnalité promise
au commanditaire (plan v3.0 §11, Phase 4). **Aucun LLM génératif, aucun torch,
aucun modèle téléchargé** : la décision matérielle actée (postes 8 Go, CPU) est
respectée à la lettre — l'assistant construit ses réponses depuis les données
déjà en base et la recherche existante.

---

## 1) Ce qui a été fait

### 1.1 Route serveur — `POST /assistant` (nom documenté dans `docs/API.md`)

- Entrée : `{"question": str(1..500), "inclure_a_valider": bool=false}` ;
  auth identique au reste de l'API (`X-SEAMTECH-TOKEN`).
- Sortie JSON : `etat`, `reponse`, `citations`, `interpretations`, `pistes`,
  `duree_ms`. États : `ok` / `ambigu` / `sans_source` / `occupe` /
  `indisponible` (503 hors PostgreSQL, décision §17.1 — comme les autres
  routes métier).
- `occupe` : une seule analyse à la fois (verrou non bloquant) — le poste
  cible est mono-cœur ; l'état est observable et testé.
- Métriques : `assistant_requests`, `assistant_sans_source` (via `/metriques`).

### 1.2 Citations vérifiables

Chaque citation porte : `code_fiche`, `champ` (clé de `fiche_champ_extrait`),
`libelle`, `table_cible`, `colonne_cible`, `valeur`, `valeur_normalisee` et —
**quand la trace réelle existe** — `page` + `zone` (mêmes coordonnées que le
lot B : `x0/y0/x1/y1` points PDF, page 0-based). `lien` pointe
`/dossier/<code>?champ=<champ>` : l'écran Fiche a reçu le lien profond qui
**surligne directement la zone dans la visionneuse PDF**
(`frontend/components/fiche-app.tsx`). Sans trace (fiche synthétique), la
citation reste vérifiable par fiche+champ+valeur ; la zone est absente, pas
inventée.

### 1.3 Aucune réponse sans citation (comportement, pas slogan)

- Toute valeur affirmée (`etat ok`) porte ≥ 1 citation — **invariant asserté
  en test** (`test_invariant_reponses_sourcees_100_pourcent`) : 7/7 = 100 %.
- Question sans réponse dans la base → `sans_source` : « Je ne trouve pas
  cette information dans les fiches… » + pistes de requête **étiquetées
  « pas des réponses »** — le refus ne contient aucune valeur (garde
  automatique : tout nombre à unité qui ne vient ni de la question ni d'une
  citation fait échouer le test).
- Question ambiguë (« la matière de la fiche X ? ») → `ambigu` : les lectures
  (tissu / galons / renforts) sont **listées, chacune avec ses propres
  citations**.
- Champ consigné « ~ » (RG5) → rendu comme **« sans objet (non applicable) »**
  avec la citation de la fiche — jamais converti en chiffre.
- Comptage : le critère est affiché (« type portant » → famille
  voile_porteuse / libellé / titre contenant « portant » — traduction
  déterministe écrite dans le code, `MOTIFS_TYPE`) et **chaque fiche comptée
  est citée** ; comptage à zéro dit tel quel.

### 1.4 Panneau dans l'écran Recherche (pas un écran isolé)

`frontend/components/assistant-panneau.tsx`, intégré sous la barre de
`/recherche` (`recherche-fiches-app.tsx`) : question, réponse, citations
cliquables (→ fiche + zone PDF), interprétations, pistes, badges d'état
« Occupé — réessayez » / « Indisponible ». Proxy
`frontend/app/api/assistant/route.ts` (miroir du patron `/api/recherche`).
**Les 6 écrans existants ne sont pas touchés** (un seul import ajouté + le
lien profond de zone dans fiche-app) : `tsc --noEmit` et `pnpm build` verts.

### 1.5 Journalisation « comme les recherches »

Chaque question est écrite dans la table existante `recherche_log` :
`requete` = question, `filtres = {"canal": "assistant", "etat": …,
"duree_ms": …}`, `nb_resultats` = nombre de citations (0 pour un refus).
Aucune nouvelle table (30 tables inchangées), aucun fichier d'archive touché
(RG13), aucun appel réseau (RG14).

### 1.6 Ce qui n'a PAS été fait (décisions)

- **Pas de LLM** : inutile pour ce périmètre (intents déterministes sur un
  schéma connu). Si un jour un LLM s'avérait indispensable, l'option et son
  coût restent documentés dans `docs/verite_terrain/DECISION_MATERIEL.md`
  (aucun apport de cette session : la décision actée tient).
- **Lexique métier non modifié** (interdit sans nécessité démontrée — il ne
  l'a pas été) ; **aucune entrée `matiere`** ajoutée (son absence est
  légitime) ; **aucune nouvelle dépendance** runtime ou dev.

---

## 2) Preuves brutes (commandes + sorties)

### 2.1 Base de travail

```
$ git log --oneline -1            # base de la session
c0d5e45 Merge pull request #23 from Ilyes-Neguir/arena/01a0c56d-seamtech-search
$ git merge --ff-only main        # session branch alignée sur main (fast-forward)
Updating f841db7..c0d5e45
Fast-forward
```

### 2.2 Les 8 questions du jeu d'essai — réponses obtenues

Corpus étiqueté : 12 fiches SYNTHÉTIQUES du Lot E (+2 a_valider, +1 rejetée),
cotes SLU SYNTHÉTIQUES ajoutées (méthode `synthetique` en trace), et la **VRAIE
fiche 7792-SO** entrée par le pipeline réglé (extraction gabarit v2 → écriture
RG3 → validation). Commande de reproduction :

```
SEAMTECH_TEST_DATABASE_URL=… SEAMTECH_MESURE_ASSISTANT_JSON=… \
  .venv/bin/python scripts/mesure_assistant.py
```

Sortie brute (extrait intégral du jeu, sandbox 22/09, exit=0) :

```
=== JEU D'ESSAI — 8 questions (réponses obtenues) ===

Q : quelle est la SLU de la fiche 7792-SO ?   [cote par fiche — fonds réel (6,60 m, vérité terrain)]
   état = ok · citations = 2 · interprétations = 0
   réponse : SLU de la fiche 7792-SO : 6,60 m (identiques en mesures dessin et finies).
   → 7792-SO · cotes.finie.slu_m · 6,60 m · zone PDF ✓
   → 7792-SO · cotes.dessin.slu_m · 6,60 m · zone PDF ✓

Q : quelles voiles pour le bateau 29er ?   [voiles par bateau — fonds réel (Spi Asymétrique)]
   état = ok · citations = 1 · interprétations = 0
   réponse : Voiles enregistrées pour le bateau 29er 15' : Spi Asymétrique (7792-SO).
   → 7792-SO · fiche.type_voile · Spi Asymétrique · sans zone (source = base, pas le PDF)

Q : combien de fiches de type portant en 2024 ?   [comptage — corpus synthétique (1 fiche : 0812-SPI-001)]
   état = ok · citations = 1 · interprétations = 0
   réponse : 1 fiche(s) pour ce comptage (type « portant » (famille voile_porteuse, libellé ou titre contenant « portant ») ; année 2024) : 0812-SPI-001.
   → 0812-SPI-001 · fiche.code · 0812-SPI-001 · sans zone (source = base, pas le PDF)

Q : quelles fiches ont une SLU entre 6,5 et 6,7 m ?   [intervalle — synthétique + réel (3 fiches)]
   état = ok · citations = 3 · interprétations = 0
   réponse : 3 fiche(s) avec SLU (mesures finies — jeu par défaut, précisez « en mesures dessin » pour l'autre jeu) entre 6,50 m et 6,70 m : 0701-GV-001 (6,62 m), 0702-GV-003 (6,55 m), 7792-SO (6,60 m).
   → 0701-GV-001 · cotes.finie.slu_m · 6,62 m · sans zone (source = base, pas le PDF)
   → 0702-GV-003 · cotes.finie.slu_m · 6,55 m · sans zone (source = base, pas le PDF)
   → 7792-SO · cotes.finie.slu_m · 6,60 m · zone PDF ✓

Q : quelle matière pour le galon de bordure ?   [attribut galon — fonds réel (Nylon)]
   état = ok · citations = 1 · interprétations = 0
   réponse : Matière du galon de bordure (seule fiche concernée, 7792-SO) : Nylon.
   → 7792-SO · galon.bordure · Nylon · zone PDF ✓

Q : quelle est la longueur du mât de la fiche 7792-SO ?   [SANS SOURCE → refus explicite]
   état = sans_source · citations = 0 · interprétations = 0
   réponse : Je ne trouve pas cette information dans les fiches : la base ne contient pas ce champ, je n'invente aucune valeur.

Q : quelle est la matière de la fiche 7792-SO ?   [AMBIGU → interprétations sourcées]
   état = ambigu · citations = 0 · interprétations = 3
   réponse : « Matière » est ambigu pour la fiche 7792-SO : précisez tissu, galon ou renfort — voici les valeurs consignées pour chaque lecture.

Q : quel est le surplus de jonction de la fiche 7792-SO ?   [non applicable RG5 (consigné « ~ »)]
   état = ok · citations = 1 · interprétations = 0
   réponse : Surplus de jonction de la fiche 7792-SO : consigné « ~ » dans la fiche — sans objet (non applicable), aucune valeur chiffrée.
   → 7792-SO · jonction.surplus · « ~ » (sans objet — RG5) · zone PDF ✓
```

Les interprétations de Q7 (affichées par le panneau, chacune sourcée) :
tissu « Monofilm K903 », galons « Nylon (guindant/chute/bordure) », renforts
« Nylon / dacron » — toutes lues dans `fiche_materiau`, `fiche_galon`,
`fiche_renfort`.

### 2.3 Les 510 passés / 3 sautés restent verts + nouveaux tests

```
$ .venv/bin/pytest -q -m "not postgres"
532 passed, 3 skipped, 137 deselected, 2 warnings in 58.82s
```
(510 d'avant + 22 nouveaux — dont LE garde du refus — : **aucune régression**.)

```
$ SEAMTECH_TEST_DATABASE_URL=… .venv/bin/pytest -m "postgres and not perf and not sauvegarde" -q
125 passed, 1 skipped, 546 deselected, 2 warnings in 30.43s
   # 1 skipped = poids e5 non demandés (SEAMTECH_ML_E5_DIR absent) —
   # environnement local sans modèle ; la CI télécharge puis l'exécute.

$ SEAMTECH_TEST_DATABASE_URL=… .venv/bin/pytest -m "postgres and perf" -q
4 passed, 668 deselected, 2 warnings in 5.90s
   # dont test_perf_assistant_jeu_8 (marqueur perf, sans instrumentation)

$ SEAMTECH_TEST_DATABASE_URL=… .venv/bin/pytest -k "not s3" -m "not perf" -q --cov=seamtech_search …
660 passed, 3 skipped, 17 deselected, 2 warnings in 148.13s
Coverage JSON written to file coverage.json
```

### 2.4 LE TEST AUTOMATISÉ DU REFUS — rouge puis vert

**VERT (le garde livré)** :

```
$ .venv/bin/pytest tests/test_assistant.py::test_refus_base_vide_n_invente_rien -v
tests/test_assistant.py::test_refus_base_vide_n_invente_rien PASSED [100%]
======================== 1 passed, 2 warnings in 0.31s =========================
```

**ROUGE — sabotage volontaire** : on introduit dans `_resoudre_defaut` un
repli « plausible » (répondre quand même une valeur), exactement ce que le
commanditaire interdit :

```
$ .venv/bin/pytest tests/test_assistant.py::test_refus_base_vide_n_invente_rien -v
tests/test_assistant.py::test_refus_base_vide_n_invente_rien FAILED [100%]
E           AssertionError: 'quelle est la longueur du mât de la fiche 7792-SO ?' → "D'après la fiche la plus proche du corpus, la valeur cherchée est 6,55 m."
tests/test_assistant.py:122: AssertionError
FAILED tests/test_assistant.py::test_refus_base_vide_n_invente_rien - Asserti...
```

**VERT après retrait du sabotage** (`git checkout -- seamtech_search/assistant.py`) :

```
$ .venv/bin/pytest tests/test_assistant.py -q
22 passed, 2 warnings in 0.81s
```

Un garde jamais vu rouge ne prouve rien : le garde a attrapé la valeur
inventée « 6,55 m » (absente de la question, sans citation possible).

### 2.5 Portes qualité

```
$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/python -m compileall -q seamtech_search && echo OK
OK

$ .venv/bin/pip-audit -r requirements.txt --desc
No known vulnerabilities found

$ cd frontend && pnpm audit --prod --audit-level=high
No known vulnerabilities found

$ cd frontend && pnpm install --frozen-lockfile && pnpm exec tsc --noEmit && pnpm build
Done in 13.6s using pnpm v9.15.9
TSC OK
   (build : 21 routes, dont /api/assistant — voir §1.4)

$ .venv/bin/python scripts/audit_projet.py --rapide
BILAN : 12/12 contrôles verts

$ .venv/bin/python scripts/coverage_gate.py coverage.json
Coverage gate passed: overall floor and all per-module gates are met.
   # assistant.py : 91,4 % de lignes couvertes ; TOTAL projet 89 %
```

### 2.6 RG13 — archive intacte (paquet lecture seule)

```
RG13 empreinte archive avant = 12e053f72f157decc68fd2ae2c4362a7d43d816066d87e03f16b1f99f6cde0ca
RG13 empreinte archive après = 12e053f72f157decc68fd2ae2c4362a7d43d816066d87e03f16b1f99f6cde0ca (IDENTIQUE)
```
(SHA-256 de l'arbre `sample_data/` — chemins + contenus — avant/après le jeu
complet : l'assistant n'écrit RIEN dans l'archive ; il ne fait que lire la
base. De plus, aucun module de l'assistant n'ouvre de fichier.)

### 2.7 RG14 — aucun appel réseau sortant (environnement sans réseau)

La mesure complète a été rejouée dans un **namespace réseau vide**
(`unshare -n`), la base étant jointe par socket UNIX (objet du système de
fichiers, pas le réseau) :

```
[RG14] namespace sans réseau VÉRIFIÉ : interfaces = ['lo'], connexion sortante impossible ([Errno 101] Network is unreachable)
…
Q : … (les 8 questions) …
   état = ok / sans_source / ambigu — identiques à l'exécution avec réseau
Taux de réponses sourcées : 7/7 réponses effectives = 100%
GLOBAL direct : n = 80 → p50 = 2.05 ms, p95 = 3.16 ms, max = 4.32 ms
RG13 empreinte archive après = 12e053f… (IDENTIQUE)
exit=0
```

Double garde : le module `assistant.py` n'importe aucun client réseau
(`requests`/`urllib`/`socket`/`httpx`/`boto3`/`redis` interdits — testé par
`test_rg14_aucun_reseau_sous_namespace_isole`).

### 2.8 Journalisation (usage réel)

```
Journal assistant (recherche_log, canal 'assistant') : 344 lignes
```
Et le test dédié : `test_journalisation_comme_recherches` — 8 questions →
8 lignes, `requete` = question, `nb_resultats` = citations, `duree_ms`
présente. (344 = cumul des exécutions de la session ; chaque exécution de la
mesure en ajoute 80.)

---

## 3) Mesures (un lot sans chiffres n'est pas terminé — §17.13)

### 3.1 Temps de réponse sur les 8 questions — p50/p95, n exact, étiqueté

Étiquette du corpus : **synthétique (Lot E, 12 fiches + cotes ajoutées) +
fonds réel (1 fiche 7792-SO)** ; protocole : 1 passage d'échauffement puis
**10 passages mesurés par question** ; sandbox Debian 12, PostgreSQL 16.2,
postes locaux. Deux modes mesurés (n exact chacun) :

| Mode | n | p50 | p95 | max |
|---|---|---|---|---|
| **direct `poser()`** (l'analyse, hors HTTP) | **80** (8×10) | **1,79 ms** | **2,21 ms** | 2,45 ms |
| **HTTP `POST /assistant`** (route complète, TestClient in-process) | **80** (8×10) | **6,31 ms** | **7,66 ms** | 8,96 ms |

Détail par question (direct || HTTP, n = 10 chacune) :

```
  direct p50    1.89 ms | p95    2.11 ms | max    2.21 ms || HTTP p50    7.31 ms | p95    8.23 ms | quelle est la SLU de la fiche 7792-SO ?
  direct p50    2.19 ms | p95    2.87 ms | max    3.11 ms || HTTP p50    7.52 ms | p95    8.58 ms | quelles voiles pour le bateau 29er ?
  direct p50    2.43 ms | p95    4.05 ms | max    5.72 ms || HTTP p50    7.66 ms | p95   10.98 ms | combien de fiches de type portant en 2024 ?
  direct p50    1.88 ms | p95    1.97 ms | max    2.28 ms || HTTP p50    6.47 ms | p95    7.41 ms | quelles fiches ont une SLU entre 6,5 et 6,7 m ?
  direct p50    1.93 ms | p95    2.10 ms | max    2.35 ms || HTTP p50    7.04 ms | p95    7.33 ms | quelle matière pour le galon de bordure ?
  direct p50    1.47 ms | p95    1.86 ms | max    1.93 ms || HTTP p50    6.34 ms | p95    7.12 ms | quelle est la longueur du mât de la fiche 7792-SO ?
  direct p50    1.85 ms | p95    2.13 ms | max    2.35 ms || HTTP p50    6.47 ms | p95    7.00 ms | quelle est la matière de la fiche 7792-SO ?
  direct p50    1.65 ms | p95    1.79 ms | max    1.88 ms || HTTP p50    6.05 ms | p95    6.78 ms | quel est le surplus de jonction de la fiche 7792-SO ?
```

La même mesure tourne en CI (étape `perf` dédiée, **sans instrumentation**,
publication `::notice`) via `test_perf_assistant_jeu_8` : chiffres du run
collés au §5.

### 3.2 Taux de réponses sourcées

```
Taux de réponses sourcées : 7/7 réponses effectives = 100%
```
7 réponses effectives (6 `ok` + 1 `ambigu`), chacune avec ≥ 1 citation (ou
des interprétations chacune sourcée) ; 1 refus (`sans_source`) qui n'affirme
rien. **100 % des réponses effectives sont sourcées** — invariant permanent,
asserté par `test_invariant_reponses_sourcees_100_pourcent`.

### 3.3 Comptes de tests (filtres exacts, étiquetés)

| Suite | Commande | Compte |
|---|---|---|
| SQLite (comme CI) | `pytest -q -m "not postgres"` | **532 passed, 3 skipped** |
| PostgreSQL intégration | `pytest -m "postgres and not perf and not sauvegarde"` | **125 passed, 1 skipped** (e5 : env) |
| Perf (sans instrumentation) | `pytest -m "postgres and perf"` | **4 passed** |
| Couverture (sélection CI) | `pytest -k "not s3" -m "not perf" --cov=seamtech_search` | **660 passed, 3 skipped** ; gate **passed** ; assistant.py **91,4 %** |

---

## 4) Non prouvé / bloqué (liste honnête)

1. **CI sur la branche** : ❌ **NON PROUVÉ au moment où ce rapport est écrit** —
   la PR est ouverte, les 8 jobs tournent ; leurs statuts sont collés au §5
   (mis à jour après push, même document).
2. **Échelle réelle** : le fonds ne compte toujours qu'UNE fiche réelle
   (7792-SO). Les mesures portent sur le corpus étiqueté synthétique + réel ;
   la latence et la pertinence « grand volume » restent à re-mesurer quand les
   20-30 fiches réelles attendues seront importées.
3. **Latence HTTP de bout en bout via serveur réseau** (uvicorn + proxy
   Next.js sur un poste cible) : mesurée ici via TestClient in-process
   (route complète sans la pile TCP) ; le chiffre « poste cible » reste à
   prendre sur l'installation réelle.
4. **Chrono humain de validation** (Phase 1) : toujours 0/3 fiches mesurées —
   inchangé par ce lot (hors périmètre).
5. **Compréhension « naturelle » au-delà des formulations couvertes** : la
   compréhension est déterministe (regex + dictionnaires). Une formulation non
   prévue déclenche le refus avec pistes (comportement sûr) — c'est le prix
   du zéro-hallucination, assumé et documenté ; les formulations se
   généralisent en ajoutant des motifs (`MOTIFS_TYPE`, `CHAMPS_COTES`, …).
6. **PostgreSQL local de mesure** : sandbox sans accès apt — serveur PostgreSQL
   16.2 + pgvector installés via le paquet `pgserver` (même source que
   `sauvegarde.py` sait découvrir), `pg_trgm`/`unaccent` compilés depuis les
   sources officielles (REL_16_2). CI utilise l'image `pgvector/pgvector:pg16`
   officielle (les 125 tests d'intégration y passeront avec les extensions
   préinstallées ; localement les 2 tests trigrammes dépendants sont couverts
   car pg_trgm a été construit et activé).

---

## 5) SHA poussés et statut des jobs

| Élément | Valeur |
|---|---|
| Base de la session | `main` @ `c0d5e45` (« Merge pull request #23 », aligné par fast-forward) |
| Commit implémentation | `1d5df21` « feat(assistant): Lot I — assistant sourcé extractif… » |
| Commit documentation | (ce fichier + CHANGELOG + TRACABILITÉ + docs/API — SHA après commit, voir `git log`) |
| Branche poussée | `arena/01a0ca1a-seamtech-search` (push simple, jamais de force) |
| PR | ouverte vers `main`, **NON fusionnée** (l'agent ne fusionne jamais) |
| Statut des 8 jobs | relevé après push — collé ci-dessous avec les SHA de run `gh run list` (NON PROUVÉ tant que non `success`) |
