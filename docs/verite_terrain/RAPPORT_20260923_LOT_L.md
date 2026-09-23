# Rapport de vérité terrain — Lot L « doublons avant validation + comptes nominatifs » (23/09/2026)

- **Dépôt** : `Ilyes-Neguir/SEAMTECH-search`
- **Branche** : `arena/01a0cda8-seamtech-search` — **pull request #26** vers `main`, **prête à relire** (`draft=false`, passée en revue le 23/09), **jamais fusionnée**
- **Base** : `82231fa7ddf80f0d637a89a5deaac63587af2856` (`origin/main` au 23/09)
- **HEAD de code** : `d8b2b7f4c3bf92c48ebd74e4e6c40a4bf3f330bf` (dernier commit qui touche au code du lot)
- **HEAD de branche** : `470e0d7` (première version de ce rapport) puis le **commit de micro-correctifs d'audit** qui contient la version que vous lisez — un fichier ne peut pas citer son propre SHA : celui-là est `git rev-parse HEAD`, affiché en tête de la PR #26
- **Règle zéro de ce rapport** : chaque affirmation est accompagnée de la COMMANDE et de la SORTIE BRUTE copiée telle quelle. Aucun chiffre recopié d'un message antérieur, aucun arrondi présenté comme une mesure. Les quatre sabotages sont montrés **ROUGE ET VERT**.

```console
$ git fetch origin && git log -1 --format='%H %s' origin/main
82231fa7ddf80f0d637a89a5deaac63587af2856 Merge pull request #25 from Ilyes-Neguir/arena/01a0ca57-seamtech-search

$ git log -1 --format='%H %s' HEAD          # HEAD de code : dernier commit qui touche au code
d8b2b7f4c3bf92c48ebd74e4e6c40a4bf3f330bf fix(e2e L.2): session morte = 401 (cause du rouge CI) + assertions de doublon non ambiguës

$ git status --short | wc -l
0
```

Après ce rapport, deux commits **de documentation seulement** se sont ajoutés sur la même branche :
`470e0d7` (première version de ce fichier) puis le commit de micro-correctifs d'audit du 23/09 (le présent,
qui corrige les trois énoncés non reproductibles listés en §3.2 et §5.3). Aucun fichier de code n'est touché :
le HEAD de code reste `d8b2b7f`, et les mesures de ce rapport sont celles de ce SHA.

---

## 1. Ce qui a été fait (et ce qui relève du rattrapage de l'audit)

Le lot L se compose de **deux livraisons neuves** (L.1, L.2) et d'un **rattrapage de l'audit du 23/09** (0.4 + C.1, C.2, C.3, C.4). La distinction est maintenue partout ci-dessous : un rattrapage n'est pas une nouveauté.

| # | Objet | Type | Commit | Preuve |
|---|---|---|---|---|
| 0.4 | Cotes de recherche chargées **conditionnellement** (contrat décrit dans `docs/API.md` + test qui le fige) | rattrapage (audit Lot K) | `cd6941b` | §2.1 |
| L.1 | **Détection de doublons AVANT validation** : exacte (SHA-256 du PDF) + probable (titres), 100 % propositive, jamais de fusion, `dry_run=True` par défaut, 4 routes, CLI | **neuf (lot L)** | `f5e1016` | §2.4 |
| L.2 | **Comptes nominatifs** : scrypt stdlib, sessions en base, connexion nominative, frontière de confiance (`X-SEAMTECH-*` seulement avec `X-SEAMTECH-TOKEN`), rôles 401/403, révocation, **attribution « qui a validé quoi »** (`taux_par_utilisateur`) | **neuf (lot L)** | `d2142b6` | §2.5 |
| C.1 | Tableau de bord qualité : test de **forme** (égalité exacte des 9 clés), test de **valeurs** (doublons, attribution), test de **route** (`TestClient`) | rattrapage audit 23/09 | `d2142b6` | §2.3 |
| C.2 | e2e live du **bandeau de doublon** avec un doublon **réel** dans le seed (`BIS-7792` partage le PDF de `7792-SO`) — aucun `test.skip` ajouté | rattrapage audit 23/09 | `d2142b6` + `81fcbbb` | §2.6 |
| C.3 | `docs/API.md` : routes L.1, routes `/auth/*`, **frontière de confiance**, verrou `audit_log`, **9 indicateurs** documentés | rattrapage audit 23/09 | `d2142b6` | §2.6 |
| C.4 | Schéma : `TABLES_METIER` **contient 32 tables** (dont `session_ui`) — jamais écrit « = 32 » | rattrapage audit 23/09 | `d2142b6` | §2.2 |
| — | 4 gardes de sabotage exécutées **en CI** (dimension, cotes, dédup, attribution) avec preuves en annotations | porte de sortie | `d2142b6` + `02821b6` | §2.7 |
| — | Correctifs d'e2e trouvés par la CI : sélection des lignes par `data-code` exact ; **session morte = 401** ; assertions de doublon non ambiguës | correctifs de la CI | `81fcbbb` + `d8b2b7f` | §2.8 |

### Règle de couverture écrite au dépôt (audit 23/09)

> Tout nouvel indicateur arrive avec **(a)** sa clé dans le test de forme `CLES_TABLEAU_DE_BORD`, **(b)** son test de valeurs, **(c)** sa ligne dans `docs/API.md`.

Elle est écrite **dans le code** à l'endroit où l'on ajoute un indicateur, et non seulement dans ce rapport :

```console
$ sed -n '205,226p' tests/test_qualite_tableau.py
# ---------------------------------------------------------------------------
# Règle de couverture (audit du 23/09, constat C.1) — tout NOUVEL indicateur
# arrive avec :
#   (a) sa clé dans CLES_TABLEAU_DE_BORD ci-dessous,
#   (b) son test de valeurs,
#   (c) sa ligne dans docs/API.md.
# ---------------------------------------------------------------------------

from seamtech_search.qualite.tableau import doublons_detectes, tableau_de_bord  # noqa: E402

# Les 9 indicateurs réellement exposés après L.2 (8 en L.1 + taux_par_utilisateur).
CLES_TABLEAU_DE_BORD = {
    "taux_extraction_auto",
    "taux_correction_par_champ",
    "temps_validation",
    "anomalies_frequentes",
    "volume_par_statut",
    "usage_recherches",
    "lots",
    "doublons_detectes",
    "taux_par_utilisateur",
}
```

---

## 2. Preuves brutes — commande + sortie copiée

### 2.1 Garde « cotes conditionnelles » (0.4) — ROUGE puis VERT

Sabotage : `avec_cotes_page = True` (chargement inconditionnel, état d'avant 0.4).

```console
$ bash /tmp/sabotage_cotes.sh            # script local rejoué le 23/09 sur d8b2b7f
=== [1] SABOTAGE : retour au chargement INCONDITIONNEL des cotes ===
avec_cotes_page = True
=== [2] Le test doit ROUGIR ===
>           assert facettes_defaut[cote]["effectif"] == 0, (
E           AssertionError: slu_m : la réponse par défaut ne doit PAS charger les facettes de cotes (effectif=4) — voir docs/API.md « Chargement conditionnel des cotes »
E           assert 4 == 0
tests/test_recherche_dimension_tri.py:152: AssertionError
FAILED tests/test_recherche_dimension_tri.py::test_contrat_cotes_conditionnelles_documente
rc_rouge=1
=== [4] Le test doit REVERDIR ===
1 passed in 0.54s
rc_vert=0
GARDE-FOU COTES CONDITIONNELLES : ROUGE (rc=1) puis VERT (rc=0) PROUVE
```

### 2.2 C.4 — schéma métier (sortie brute exigée mot pour mot)

```console
$ python3 -c "from seamtech_search.schema_metier import TABLES_METIER as T; print(len(T), 'session_ui' in T)"
32 True
```

### 2.3 C.1 — tableau de bord : forme, valeurs, route

```console
$ SEAMTECH_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55432/postgres' \
  /home/user/.venv/bin/python -m pytest -q -m postgres tests/test_qualite_tableau.py --basetemp=/home/user/bt
15 passed, 1 warning in 4.26s
```

Le test de forme est une **égalité exacte** — ajouter un indicateur sans le déclarer casse ici :

```console
$ sed -n '228,236p' tests/test_qualite_tableau.py
def test_tableau_de_bord_expose_tous_les_indicateurs_declares(base_recherche):
    """C.1.1 — égalité EXACTE des clés : ajouter un indicateur sans le déclarer casse ici."""
    tableau = tableau_de_bord(base_recherche["index"])
    assert set(tableau.keys()) == CLES_TABLEAU_DE_BORD, (
        f"indicateurs manquants : {CLES_TABLEAU_DE_BORD - set(tableau.keys())} ; "
        f"indicateurs non déclarés : {set(tableau.keys()) - CLES_TABLEAU_DE_BORD}"
    )
```

### 2.4 L.1 — détection de doublons : garde ROUGE puis VERT

Sabotage : la clé de rapprochement exact devient une constante (`p.empreinte_sha256` → `'x' || ''`, 5 occurrences).

```console
$ bash /tmp/sabotage_dedup.sh
=== [1] SABOTAGE : la cle de rapprochement exact devient une constante ===
occurrences remplacees : 5
=== [2] Le test doit ROUGIR ===
>       assert groupe["empreinte_sha256"] == empreinte_reelle
E       AssertionError: assert 'x' == '43afc51e55ae...31b4dbf1c1f40'
E         
E         - 43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40
E         + x
tests/test_dedup.py:187: AssertionError
FAILED tests/test_dedup.py::test_doublons_exacts_le_pdf_reel_en_double - Asse...
rc_rouge=1
=== [4] Le test doit REVERDIR ===
1 passed in 0.38s
rc_vert=0
GARDE-FOU DEDUP : ROUGE (rc=1) puis VERT (rc=0) PROUVE
```

### 2.5 L.2 — comptes nominatifs

**Ligne de commande (7 sous-commandes, mot de passe toujours lu sur STDIN) :**

```console
$ /home/user/.venv/bin/python -m seamtech_search.comptes.cli --help
usage: comptes [-h] [--database-url DATABASE_URL] [--json]
               {creer,lister,desactiver,reinitialiser-mot-de-passe,sessions,revoquer-session,verifier}
               ...

Gestion des comptes nominatifs (PostgreSQL) — Lot L.2.

positional arguments:
  {creer,lister,desactiver,reinitialiser-mot-de-passe,sessions,revoquer-session,verifier}
    creer               Crée un compte nominatif.
    lister              Liste les comptes (jamais les empreintes).
    desactiver          Désactive un compte et révoque ses sessions.
    reinitialiser-mot-de-passe
```

**Garde « attribution » (la nouvelle) — ROUGE, sur ASSERTION et non sur une erreur SQL :**

```console
$ bash /tmp/sabotage_attribution.sh
E       AssertionError: attribution = 1 (attendu 2, premier compte = 1) : la session doit primer sur le corps.
E       assert 1 == 2
tests/test_comptes.py:597: AssertionError
FAILED tests/test_comptes.py::test_validation_attribuee_au_compte_connecte - ...
GARDE-FOU ATTRIBUTION : ROUGE puis VERT — preuve complète (rc rouge=1, rc vert=0).
```

**Preuves HTTP contre le vrai backend + PostgreSQL + `next start` (23/09, HEAD `d8b2b7f`) :**

```console
$ # backend : python -m seamtech_search serve --config frontend/e2e/backend.config.json   (port 8123)
$ # front    : next start -p 3123 (SEAMTECH_API_URL=http://127.0.0.1:8123)
$ curl -s -c cj_op.txt -H 'Content-Type: application/json' \
    -d '{"identifiant":"e2e-operateur","mot_de_passe":"e2e-operateur-password"}' \
    http://127.0.0.1:3123/api/auth/login
{"ok":true,"mode":"nominatif","identifiant":"e2e-operateur","nom":"Opérateur e2e","role":"operateur","doit_changer_mot_de_passe":true,"redirectTo":"/"}
HTTP 200

$ curl ... /api/auth/session            (cookie nominal vivant)
{"authenticated":true,"configured":true,"identifiant":"e2e-operateur","nom":"Opérateur e2e","role":"operateur","mode":"nominatif","doit_changer_mot_de_passe":true}
HTTP 200

$ curl ... /api/auth/utilisateurs       (opérateur puis admin)
operateur -> HTTP 403
admin     -> HTTP 200

$ curl -X POST /api/auth/logout         (déconnexion)
HTTP 200

$ # APRÈS déconnexion, le même cookie (toujours signé) :
/api/auth/session        -> HTTP 401
/api/search?q=CLIENT     -> HTTP 401
/api/validation/file     -> HTTP 401

$ curl -s -b cj_op.txt -w '\nHTTP %{http_code}\n' http://127.0.0.1:3123/api/auth/session
{"authenticated":false,"configured":true,"reason":"session_revoquee"}
HTTP 401
```

**Empreintes restaurées après les quatre sabotages (aucune écriture laissée derrière) :**

```console
$ sha256sum seamtech_search/recherche.py seamtech_search/dedup/detection.py seamtech_search/fiches/routes.py
ce061928981c889af1b70f2533d4d3ea2568be04ab4168779a56689568ee4b7b  seamtech_search/recherche.py
9ed59f5159f7a8b2885f212988aba6133fda0b99f989e76fb13ec0c14af7979e  seamtech_search/dedup/detection.py
00388561070907736dee7347aa3320d4c60d28fc0bc068705a40fd37a30ae1e1  seamtech_search/fiches/routes.py
$ diff <(cat /tmp/hash_avant.txt) <(sha256sum ...)   # avant/après sabotage
IDENTIQUE
```

### 2.6 C.2 / C.3 — e2e doublons et documentation

Le seed live contient désormais un **vrai** doublon (même PDF, même SHA-256) :

```console
$ SEAMTECH_E2E_DATABASE_URL=… /home/user/.venv/bin/python frontend/e2e/seed-live-pg.py
Seed e2e : fiche BIS-7792 (id 4) partage le PDF de 7792-SO — scan propositif : 1 lien(s) créé(s), 0 déjà présent(s), 2 fiche(s) concernée(s).
Seed e2e : compte nominatif « e2e-operateur » (operateur) créé.
Seed e2e : compte nominatif « e2e-admin » (administrateur) créé.
```

Annotation publiée par l'étape CI (run `35851520903`) :

```console
[notice] e2e-doublons :: 2 tests verts (bandeau visible, aucune fusion proposée)
```

Documentation (C.3) — 35 titres/entrées de routes dans `docs/API.md`, dont la frontière de confiance et le 9e indicateur :

```console
$ grep -cE '^### |^\| `' docs/API.md
35
$ grep -nE "X-SEAMTECH-UTILISATEUR|verrou|taux_par_utilisateur" docs/API.md | cut -c1-120 | head -4
204:`X-SEAMTECH-UTILISATEUR` et `X-SEAMTECH-ROLE` depuis le cookie signé. Côté Python ces
227:attribuable (`X-SEAMTECH-UTILISATEUR: secours`), et il sert uniquement à créer les premiers
252:| `taux_par_utilisateur` | Lot L.2 — `actions_total`, `actions_attribuees`, `actions_sans_utilisateur`, `part_attr
```
(coupe à 120 colonnes par `cut` : la ligne 252 continue dans le fichier.)

### 2.7 Les quatre gardes de sabotage, exécutées EN CI, preuves en annotations

Run `35851520903` (SHA `d8b2b7f`), job `backend (3.12)` — les 10 annotations publiées :

```console
$ gh api repos/Ilyes-Neguir/SEAMTECH-search/check-runs/107150073614/annotations \
    --paginate -q '.[] | select(.title | test("garde-fou")) | "\(.annotation_level)\t\(.title)"'
failure	garde-fou-attribution-rouge
failure	garde-fou-cotes-rouge
failure	garde-fou-dedup-rouge
failure	garde-fou-dimension-rouge
notice	garde-fou-attribution-vert
notice	garde-fou-cotes-vert
notice	garde-fou-dedup-rouge-tail
notice	garde-fou-dedup-vert
notice	garde-fou-dimension-rouge-tail
notice	garde-fou-dimension-vert
```

Le job est **vert** : ces annotations de niveau `error` sont la *publication* de la preuve ROUGE, pas un échec (le garde ne sort en erreur que si le test reste VERT sous sabotage ou ROUGE après restauration). Extrait d'une annotation ROUGE, lisible sans le journal complet :

```console
$ gh api repos/Ilyes-Neguir/SEAMTECH-search/check-runs/107150073614/annotations --paginate \
    -q '.[] | select(.title=="garde-fou-attribution-rouge") | .message[0:200]'
        compte, l'assertion porte sur une valeur calculée — jamais sur une;        assert premier["id_utilisateur"] != second["id_utilisateur"];        assert cookie["id_utilisateur"] == second["id_ut
```
(les `;` sont les retours à la ligne convertis par le script de la garde ; la coupe à 200 caractères est la mienne,
faite dans le `jq` ci-dessus.)

### 2.8 e2e live — le ROUGE, sa cause, puis le VERT

**AVANT** (run `35850813351`, SHA `02821b6`) : les 10 premières étapes vertes, l'étape auth rouge. Les annotations ajoutées par `02821b6` ont donné la cause exacte (elles remplacent `gh run view --log`, inaccessible : `EOF` du blob host) :

```console
[failure] e2e-auth-tail ::     > 323 |           expect(refus.status(), `${chemin} doit refuser une session révoquée`).toBe(401);
```

La boucle de révocation commence par `/api/auth/session`, et cette route répondait **200** `{authenticated:false, reason:"session_revoquee"}` : un cookie que le serveur refuse s'annonçait « encore valide ». C'était un **vrai défaut**, pas un test trop strict — corrigé dans `d8b2b7f` (§2.5, dernier bloc).

Au même run, une deuxième fragilité était visible en annotation :

```console
[failure] e2e-doublons-count :: attendu 2 tests passés au premier essai, 0 flaky, 0 sautés — mesuré 1 passés, 1 flaky, 0 sautés
```

Cause : le test « aucune commande de fusion n'est proposée » passait **avant** l'arrivée du bandeau (succès de timing) et son motif non ancré `/fusionner|fusion/i` tombait dans le nom accessible des **lignes** de la file. Corrigé : attente explicite du bandeau, assertion ancrée `/^\s*fusionner/i`, vérification que le mot « Fusionner » n'apparaît nulle part, et reprise unique de la requête des liens côté UI.

**APRÈS** (run `35851520903`, SHA `d8b2b7f`) — les trois étapes live, toutes vertes :

```console
[notice] mesure-phase1 :: parcours machine complet de la vraie fiche 7792-SO (navigation → fiche ouverte, champs et PDF rendus → correction RG11 → validation) : 675 ms ; lecture humaine hors chrono (critère < 120 000 ms)
[notice] e2e-doublons :: 2 tests verts (bandeau visible, aucune fusion proposée)
[notice] e2e-auth-nominatif :: 23 tests verts (dont 4 sur les comptes nominatifs)
```

---

## 3. Mesures

### 3.1 Suites de tests (commandes et sorties brutes)

| Suite | Commande | Baseline (avant lot L) | HEAD `d8b2b7f` |
|---|---|---|---|
| Locale | `pytest -q -m "not postgres"` | `532 passed, 3 skipped, 165 deselected` | `532 passed, 3 skipped, 203 deselected, 1 warning in 51.32s` |
| PostgreSQL (local) | `pytest -q -m "postgres and not perf and not sauvegarde"` | `153 passed, 1 skipped` | `190 passed, 1 skipped, 547 deselected, 1 warning in 49.78s` |
| PostgreSQL (CI) | idem, env CI (annotation) | — | `passed=191 skipped=0` |
| Perf | `pytest -q -m perf` | — | `5 passed, 733 deselected, 1 warning in 5.43s` — **0 alerte `perf-derive`** |

```console
$ SEAMTECH_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55432/postgres' TMPDIR=/home/user/bt \
  /home/user/.venv/bin/python -m pytest -q -m "postgres and not perf and not sauvegarde" --basetemp=/home/user/bt
190 passed, 1 skipped, 547 deselected, 1 warning in 49.78s

$ TMPDIR=/home/user/bt /home/user/.venv/bin/python -m pytest -q -m "not postgres" --basetemp=/home/user/bt
532 passed, 3 skipped, 203 deselected, 1 warning in 51.32s

$ SEAMTECH_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55432/postgres' TMPDIR=/home/user/bt \
  /home/user/.venv/bin/python -m pytest -q -m perf --basetemp=/home/user/bt | tail -1
5 passed, 733 deselected, 1 warning in 5.43s
$ grep -c "perf-derive" <sortie perf>
0
```

Détail des fichiers neufs du lot L : `tests/test_comptes.py` **18 tests**, `tests/test_qualite_tableau.py` **15 tests** (dont C.1), `tests/test_dedup.py` **14 tests** (L.1), `tests/test_migrations_metier.py` (16/16, `TABLES_METIER` 32 tables / 38 objets).

La suite locale ne bouge pas (532) : les tests du lot L sont marqués `postgres` — leur place est la base réelle, pas un SQLite de complaisance.

### 3.2 Latences et longueurs mesurées

| Mesure | Où | Valeur |
|---|---|---|
| Parcours machine complet de la vraie fiche `7792-SO` | CI, annotation `mesure-phase1` | **666 ms** (run `35850813351`) puis **675 ms** (run `35851520903`) — critère < 120 000 ms |
| Recherche 1 500 fiches (mi-échelle) | CI, `perf-latence` | `p50 = 15.7 ms, p95 = 47.1 ms, max = 47.9 ms (n = 60)` — critère produit p95 < 100 ms |
| Qualité, 1 000 fiches | CI, `perf-latence` | `p50 = 2.1 ms, p95 = 2.2 ms, max = 2.7 ms` |
| Restauration de sauvegarde (50 000 fiches) | CI, job `sauvegarde` | `restauration de 50000 fiches en 1.27 s (dump 575857 octets)` |
| Longueur du schéma métier | `python3 -c … print(len(T), 'session_ui' in T)` | **`32 True`** — `TABLES_METIER` **contient 32 tables** |
| Sections du CHANGELOG | `grep -c '^## ' CHANGELOG.md` | **27 sections `## `** (porte de la mission) — **28** en comptant le titre de niveau 1 historique `# Unreleased — Micro-correctifs de la revue du 21/09` |

```console
$ grep -c '^## ' CHANGELOG.md
27
$ grep -E '^(#|##) ' CHANGELOG.md | grep -vc '^# Changelog$'
28
$ grep -n '^# ' CHANGELOG.md
532:# Unreleased — Micro-correctifs de la revue du 21/09 (`fix/micro-correctifs-revue-2109`)
665:# Changelog
```
(la ligne 665 est le titre du document, exclue par le filtre `grep -vc '^# Changelog$'` ; la ligne 532 est
la seule section de niveau 1 comptée en plus des 27 sections `## `.)

Les deux nombres sont vrais, mais pas pour la même commande : **27** est le compte de sections du CHANGELOG
(porte de la mission, `grep -c '^## '`), **28** celui du second compte, qui inclut le titre de niveau 1
historique. Écrire « 28 sections » sous la commande `grep -c '^## '` était donc un chiffre NON reproductible
par la commande citée — relevé par l'audit indépendant du 23/09 et corrigé ici. Le compte de sections est
inchangé depuis `f5e1016` : la section « Lot L » a été enrichie sur place (une section par lot) au lieu d'en
créer une seconde.

### 3.3 Qualité statique et sécurité

```console
$ /home/user/.venv/bin/python -m ruff check .
All checks passed!

$ /home/user/.venv/bin/python -m pip_audit -r requirements.txt --desc   # commande exacte de la CI
No known vulnerabilities found

$ corepack pnpm audit --prod          (frontend/)
No known vulnerabilities found

$ ./node_modules/.bin/tsc --noEmit    (frontend/)
tsc --noEmit : 0 erreur

$ ./node_modules/.bin/next build      (frontend/)
✓ (build de production terminé — mode live de la CI)
```

Invariants du projet, script d'audit :

```console
$ SEAMTECH_TEST_DATABASE_URL=… /home/user/.venv/bin/python scripts/audit_projet.py --rapide
==========================================================================
BILAN : 12/12 contrôles verts
==========================================================================
```

RG13 / RG14 (exigences dures du projet) :

```console
$ pytest -q -m postgres tests/test_depot_transactionnel.py::TestArchiveIntacteRG13 tests/test_assistant_postgres.py -k "RG13 or rg13 or archive"
2 passed, 21 deselected in 1.22s        # RG13 : empreinte SHA-256 de l'archive inchangée

$ pytest -q -m postgres tests/test_dedup.py -k "reseau or RG14 or network" tests/test_comptes.py -k "reseau or RG14"
2 passed, 30 deselected, 1 warning in 0.39s   # RG14 : aucun appel réseau dans dedup et dans comptes
```

---

## 4. Ce qui n'est PAS prouvé — limites assumées

1. **Compte de secours partagé (`SEAMTECH_UI_PASSWORD`) — limite de sécurité ASSUMÉE.** Il reste possible de se connecter avec un mot de passe unique partagé ; l'action est alors attribuée à `secours` et **ne permet pas** l'attribution nominative. Déclaré à trois endroits : `.env.example`, `docs/API.md`, `docs/verite_terrain/MISE_EN_SERVICE.md` + `QUE_FAIRE_SI.md` §7-8, et ici. Aucune mesure ne peut la supprimer sans supprimer le dépannage hors ligne : c'est un choix, pas un oubli.
2. **PostgreSQL et e2e ne sont prouvés QU'EN CI.** Le navigateur Playwright n'est pas téléchargeable dans l'environnement de développement de cette session : les 3 étapes e2e live (validation, doublons, auth) ne s'exécutent qu'en CI. Les preuves locales de L.2 sont des appels HTTP (`curl`) contre le vrai backend et le vrai `next start`, ce qui couvre le contrat d'API mais **pas** le rendu écran.
3. **L'artefact `playwright-report-live` n'est pas téléchargeable** (le blob host des artefacts répond `EOF`). Canal de preuve utilisé à la place : les **annotations** (`::notice`/`::error`), qui sont lisibles via l'API GitHub. Conséquence : le rapport HTML Playwright n'a pas été relu, seules les annotations et les compteurs JSON le sont.
4. **Deux rouges CI ont été publiés avant d'être compris** : le run `35849810461` (sélection de ligne par texte) et le run `35850813351` (session morte = 200). Chacun a été diagnostiqué par annotations puis corrigé (`81fcbbb`, `d8b2b7f`) ; aucun n'a été masqué par un `test.skip`, une exclusion ou un retry.
5. **Politique « flaky ».** Les gardes de comptage acceptent désormais un test vert seulement à la reprise, mais le publient en `::warning` (`e2e-doublons-flaky` / `e2e-auth-flaky`) : un flaky ne fait plus échouer la porte comme un vrai rouge, mais il n'est jamais silencieux. Sur le run `35851520903`, **aucune annotation de flaky n'a été émise**.
6. **Le passage 154 → 153 tests PostgreSQL (marqueur `perf`, commit `967d82f`) n'a pas été « réparé »** : la baisse est voulue et documentée, la retoucher aurait faussé la mesure.
7. **Poids e5 ONNX non téléchargés** ici (action opérateur explicite, RG14) : les mesures d'embedding restent celles de la CI.
8. **Chrono humain.** Le critère « lecture humaine < 120 000 ms » n'est pas mesuré : aucun opérateur n'a relu la fiche dans cette session. Seul le parcours machine (675 ms) est mesuré, et l'annotation le dit explicitement (« lecture humaine hors chrono »).
9. **Lot M (OCR) hors périmètre** : non commencé, non évoqué comme fait.

---

## 5. SHA, commits et statut des 8 jobs

### 5.1 Commits de code (du plus ancien au plus récent)

Sortie brute obtenue sur `d8b2b7f` ; rejouée sur le HEAD de branche, la même commande liste en plus les deux
commits de documentation (`470e0d7` puis le présent).

```console
$ git log --oneline 82231fa7ddf80f0d637a89a5deaac63587af2856..HEAD
d8b2b7f fix(e2e L.2): session morte = 401 (cause du rouge CI) + assertions de doublon non ambiguës
02821b6 ci(e2e live): journaliser l'échec en annotations (le log complet est inaccessible)
81fcbbb fix(e2e L.1/L.2): sélection des lignes de la file par code EXACT (data-code)
d2142b6 feat(Lot L.2): comptes nominatifs « qui a validé quoi » + correctifs de couverture C.1–C.3
f5e1016 feat(Lot L.1): détection de doublons AVANT validation — exacte (SHA-256) et probable (titres)
cd6941b fix(Lot L 0.4): docs/API.md décrit le chargement conditionnel réel des cotes + test qui le fige
```

Tous poussés sur `arena/01a0cda8-seamtech-search` — jamais de `force-push`, jamais d'autre branche.

### 5.2 Statut des 8 jobs — run `35851520903` (SHA `d8b2b7f`, arbre poussé)

```console
$ gh run view 35851520903
JOBS
✓ docker in 1m17s (ID 107150073256)
✓ sauvegarde in 1m5s (ID 107150073391)
✓ integration in 2m12s (ID 107150073491)
✓ backend (3.11) in 4m39s (ID 107150073555)
✓ backend (3.13) in 4m0s (ID 107150073556)
✓ frontend in 31s (ID 107150073561)
✓ e2e in 2m27s (ID 107150073598)
✓ backend (3.12) in 5m38s (ID 107150073614)

$ gh run view 35851520903 --json status,conclusion -q '.status+" "+.conclusion'
completed success
```

Le run `pull_request` **du même SHA** (déclenché par la mise à jour de la PR) est vert lui aussi :

```console
$ gh run view 35851525135 --json status,conclusion -q '.status+" "+.conclusion'
completed success
$ gh run view 35851525135 | sed -n '5,13p'
JOBS
✓ docker in 1m28s (ID 107150087548)
✓ sauvegarde in 1m6s (ID 107150087638)
✓ frontend in 33s (ID 107150087668)
✓ backend (3.11) in 5m22s (ID 107150087698)
✓ integration in 1m53s (ID 107150087710)
✓ backend (3.13) in 5m18s (ID 107150087787)
✓ backend (3.12) in 4m50s (ID 107150087914)
✓ e2e in 2m22s (ID 107150087966)
```

**8/8 jobs verts** sur les deux runs. Ce que chaque job prouve, et le chiffre brut qui porte la preuve :

| Job | Preuve brute publiée |
|---|---|
| `backend (3.11)`, `backend (3.12)`, `backend (3.13)` | `suite-postgres :: pytest -m "postgres and not perf and not sauvegarde" : passed=191 skipped=0` ; 4 gardes ROUGE→VERT (10 annotations) ; `perf-latence` p95 = 47.1 ms ; `audit_projet` 12/12 |
| `frontend` | `Verify pnpm-lock.yaml is in sync` → `Type check` (`tsc --noEmit`) → `Pnpm audit (production dependencies)` (`--audit-level=high`) → `Build` |
| `e2e` | 3 étapes live vertes : `mesure-phase1 :: … 675 ms`, `e2e-doublons :: 2 tests verts`, `e2e-auth-nominatif :: 23 tests verts` |
| `integration` | pile réelle (postgres + front + backend) et tests d'intégration |
| `docker` | images construites et démarrage en conteneur |
| `sauvegarde` | `sauvegarde-restauration-50k :: restauration de 50000 fiches en 1.27 s` — aller-retour hors-site éprouvé |

### 5.3 Portes de sortie — état sur le HEAD de code `d8b2b7f`

| Porte | Exigence | Mesure | Verdict |
|---|---|---|---|
| Tests locaux | ≥ 532 passés, 3 sautés | `532 passed, 3 skipped` | ✅ |
| Tests PostgreSQL (CI) | ≥ 168 passés, 0 sauté | `passed=191 skipped=0` | ✅ |
| Schéma | sortie brute `32 True` | `32 True` | ✅ |
| Tableau de bord | 9 clés exactes | `set(tableau.keys()) == CLES_TABLEAU_DE_BORD` (15 tests verts) | ✅ |
| Perf | 0 alerte `perf-derive` | `5 passed`, `grep -c perf-derive` → `0` | ✅ |
| Audit projet | 12/12 | `BILAN : 12/12 contrôles verts` | ✅ |
| CHANGELOG | 27 sections `## ` (porte) | `grep -c '^## ' CHANGELOG.md` → `27` (**28** avec le titre `#` historique) | ✅ |
| Statique / sécurité | ruff, pip-audit, pnpm audit, tsc, build | `All checks passed!`, `No known vulnerabilities found` (×2), `0 erreur`, build ✓ | ✅ |
| CI | 8/8 jobs verts | runs `35851520903` (push) et `35851525135` (pull_request) : `completed success` | ✅ |
| Gardes de sabotage | 4 × ROUGE→VERT, en CI, preuves en annotations | 10 annotations `garde-fou-*` | ✅ |
| RG13 / RG14 | archive intacte / aucun appel réseau | `2 passed` + `2 passed` | ✅ |
| Sauvegarde | job vert, chiffre publié | restauration 50 000 fiches en 1.27 s | ✅ |

**Statut final : lot L livré — HEAD de code `d8b2b7f`, HEAD de branche = le commit de micro-correctifs d'audit
qui contient ce rapport ; PR #26 ouverte et prête à relire, jamais fusionnée par cette session (la fusion
appartient à l'utilisateur).**
