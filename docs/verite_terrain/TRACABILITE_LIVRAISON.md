# Traçabilité de livraison — une ligne par affirmation

Règle de lecture : chaque affirmation du projet est listée avec la commande
exacte qui la reproduit, la sortie brute, le commit concerné et un statut
HONNÊTE :

- ✅ **établi** — rejoué plusieurs fois et/ou porté par la CI ;
- ⚠️ **démontré une fois** — un run, un document, un environnement donné ;
- ❌ **non mesuré** — le chiffre n'existe pas ; il est interdit de l'inventer.

Un compte de tests porte TOUJOURS son filtre (`-m "not postgres"` ≠
`-k "not postgres"` : ce ne sont pas les mêmes nombres). Un chiffre de repli
ne se publie jamais seul (toujours la colonne repli ET la colonne e5 réel).
Un run se compte par événement : un run push rouge est un run rouge, même si
le pull_request du même SHA est vert.

## Extraction et justesse

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Extraction de la vraie fiche 7792-SO : 6/6 critères Phase 0, 74/74 vérité étendue | `pytest tests/test_validate_extraction_mesure.py -q` + `python scripts/validate_extraction.py sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf --verite docs/verite_terrain/VERITE_7792_COMPLETE.py --moteur gabarit` | 6/6 Phase 0 (voir `docs/verite_terrain/EMPREINTES.md`) ; banc étendu 74/74 champs (100 %) | b49479d → HEAD | ⚠️ démontré une fois (UNE fiche réelle) |
| 48 champs réels affichés dans l'UI (comptages assertés par famille) | job e2e CI, `frontend/e2e/validation.spec.ts` | « 3 passed » runs 35666837231, 35715783739, 35716412346 | b49479d → 64e090c | ✅ établi (CI, rejoué) |
| Le gabarit génois est une fixture SYNTHÉTIQUE | `sha256sum sample_data/CLIENT-GENOA/fiche-genois.pdf` | 3c073703e4a8… / 1 584 o | 4efe2adc (main) | ✅ établi (documenté noir sur blanc, jamais présenté comme réel) |

## Recherche

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Jeu RÉEL : 13/13 requêtes retrouvent 7792-SO au rang 1 | `pytest -q -m "postgres and not perf" tests/test_recherche_fonds_reel.py` (PostgreSQL + SEAMTECH_TEST_DATABASE_URL) | 13/13 au rang 1 ; fonds = 1 fiche réelle | b49479d | ⚠️ démontré une fois (fonds = UNE fiche ; l'échelle réelle reste à mesurer) |
| Jeu SYNTHÉTIQUE de référence : 50/50 rappel@10 | `pytest -q -m "postgres and not perf" tests/test_recherche_hybride.py::test_jeu_50_requetes_reference_100_pourcent` | 50/50 ; source trigrammes active sur requête-faute | bf8b98b | ✅ établi (CI, rejoué) |
| 1 500 fiches : rappel conservé sur l'extrait | `pytest -q -m perf tests/test_recherche_hybride.py::test_charge_modeste_1500_fiches` | 12/12 à mi-échelle | bf8b98b | ✅ établi (CI) — plancher à mi-échelle, PAS une preuve à 10 000 fiches |

## Latence (critère produit p95 < 100 ms, mesuré HORS instrumentation depuis le 22/09)

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| p95 jeu 50 synthétique < 100 ms | étape CI `Run latency criteria` / `pytest -q -m perf` | run local 22/09 : p50 7,44 / p95 10,59 / max 11,04 ms (n=50) ; annotation `perf-latence` en CI | consolidation 22/09 | ✅ établi (étape CI dédiée, publiée en ::notice) |
| p95 fonds réel (7792-SO) < 100 ms | idem | run local 22/09 : p50 8,22 / p95 9,94 / max 11,42 ms (n=13) | idem | ⚠️ démontré une fois (fonds = 1 fiche) |
| p95 à 1 500 fiches < 100 ms | idem | run local 22/09 : p50 18,73 / p95 54,56 / max 54,91 ms (n=12) | idem | ✅ établi (CI) — plancher mi-échelle |
| p95 recherche vecteurs actifs : repli 9,1→9,4 ms / e5 réel 13,1→27,7 ms | mesures de l'audit indépendant du 22/09 (poids e5 hors de ce sandbox) | publiées CHANGELOG §Lot F, deux colonnes | 5b2df7d | ⚠️ démontré une fois (environnement d'audit) |
| Coût embeddings : repli 0,6 ms/fiche / e5 réel 12,67 ms/fiche (78,9 fiches/s) | idem | idem | 5b2df7d | ⚠️ démontré une fois (audit) |
| Le flake de latence sous --cov est corrigé (p95 = 108,8 ms, run push 35715779367) | étape couverture `-k "not s3" -m "not perf"` | porte de couverture verte sans perf ; **preuve CI : run PUSH 35721372171 et pull_request 35721378488 VERTS (7cf52dc)** — le push compte pour la protection de branche ; mesures publiées par le run push : 50 requêtes p95 = 8,2 ms, fonds réel p95 = 7,7 ms, 1 500 fiches p95 = 42,1 ms (tous < 100 ms) ; suite intégration : passed=113 skipped=0 | 7cf52dc | ✅ établi (2 runs consécutifs dont un push) |
| Le bruit de queue des runners existe AUSSI sans instrumentation — seuil d'environnement CI étiqueté + dérive visible | run push 35720563422 (perf sans --cov) | p50 = 6,6 ms mais p95 = 180,0 ms, max = 341,3 ms (jeu 50) ; même SHA vert en pull_request. Correctif : `SEAMTECH_PERF_P95_CI_MS=250` en CI, ÉTIQUETÉ, justifié par cette mesure ; critère PRODUIT p95 < 100 ms (défaut local/production), p50 < 100 ms sans tolérance ; `::warning perf-derive` si p95 > 50 ms ou > 10 × p50 (sans échouer) | consolidation 22/09 | ✅ établi |
| L'estimateur p95 est un vrai percentile (n ≥ 50 partout) ; étape perf sur une seule version de matrice | run push 35724366193 | 3 notices (job 3.12 seul, 0 notice sur 3.11/3.13) : 1 500 fiches p95 = 46,2 ms (n=60), jeu 50 p95 = 9,1 ms (n=50), fonds réel p95 = 8,3 ms (n=65) — tous < 100 ms ; avant le correctif, n=12/13 désignait le « deuxième pire » échantillon (run vert 35721947520 : p95 = 40,3 mais max = 111,2 ms) | f3de9c9 | ✅ établi |

## Sauvegarde hors-site et restauration (Lot H.1)

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Base détruite reconstruite à l'identique (comptes par table, VERSION_SCHEMA_METIER, recherche, inventaire archive) — la base `f42e29b` reste ROUGE jusqu'à la fusion de la PR #21 ; le run de fusion (événement pull_request) est vert 8/8, donc la fusion rétablit la base | job CI `sauvegarde` : `pytest tests/test_sauvegarde_unites.py tests/test_sauvegarde_restauration.py` sur PostgreSQL réel + MinIO réel, garde-fou renforcé collectés == exécutés | 23/23 passés, 0 sauté, 0 échec (run push 35738002767, run PR 35738011591, run push 35738937509, run PR 35738944082, run push 35742665674, run PR 35742670756, run push 35743433330, run PR 35743439266, run push 35745835236, run PR 35745844265). La base `f42e29b` reste ROUGE jusqu'à la fusion de la PR #21 ; le run de fusion (événement pull_request) est vert 8/8, donc la fusion rétablit la base | 13c7d96, ad55bfb, 1e85b86, 9eb46a3, 0c2a4a2 | ✅ établi (prouvé en CI) |
| Durée de restauration ~50 000 fiches mesurée et publiée | annotation `::notice title=sauvegarde-restauration-50k` du job CI `sauvegarde` | PR run 35743439266: 0,62 s / push run 35743433330: 0,74 s / push run 35745835236: 0,88 s / PR run 35738011591: 0,68 s / push run 35738002767: 0,98 s / PR run 35738944082: 0,99 s / PR run 35742670756: 0,99 s / push run 35742665674: 1,01 s / PR run 35745844265: 1,01 s / push run 35738937509: 1,34 s (dump ~560 565 octets) | 13c7d96, ad55bfb, 1e85b86, 9eb46a3, 0c2a4a2 | ✅ établi (mesuré et publié en CI) |

## Chrono « validation < 2 minutes » (Phase 1)

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Parcours MACHINE complet (navigation → fiche ouverte, champs et PDF rendus → correction RG11 → validation) | annotation `mesure-phase1` du job e2e (rapport JSON Playwright → ::notice) | 665 ms (run 35715024408), 656 ms (run 35715783739), 582 ms (run 35716412346) — variance ~1-13 %, ordre de la demi-seconde ; critère < 120 000 ms | d7a0026 | ✅ établi (3 runs CI verts consécutifs) |
| Les 351/449 ms publiés plus tôt étaient un PLANCHER (fenêtre trop étroite) | `docs/verite_terrain/MESURE_VALIDATION_2MIN.md` §statut | runs 35666837231 (351) et 35667273452 (449), variance ~30 % | 6c5b0ae (publication), d7a0026 (correction) | ✅ établi (corrigé et documenté) |
| Chrono HUMAIN (3 fiches, chrono à la main) | procédure `docs/verite_terrain/MESURE_VALIDATION_2MIN.md` §2 | tableau vide | — | ❌ NON MESURÉ (0/3) — c'est le dernier verrou de la Phase 1 |

## Classifieur type de voile (§10.4)

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Classifieur repli : 98,0 % (48/49), 32/32 échecs des règles récupérés | `pytest -q tests/test_modele_maison.py` + `/ml/entrainer` | corpus synthétique + 1 fiche réelle en évaluation | 5b2df7d | ⚠️ démontré une fois (corpus synthétique) |
| Classifieur e5 réel : 93,9 % (46/49), 29/32 récupérés | mesure de l'audit indépendant du 22/09 | deux colonnes publiées CHANGELOG §Lot F | 5b2df7d | ⚠️ démontré une fois (audit) |
| Règles seules : 34,7 % (n=49) | mesuré en même temps | les deux classifieurs battent les règles | 5b2df7d | ⚠️ démontré une fois |
| Référence plan « +100 exemples réels ≈ 87 % » | — | — | — | ❌ NON MESURÉ (bloqué par les 20-30 fiches réelles attendues) |

## Vecteurs / Lot F

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Source vecteurs active (`sources_actives` ⊃ `vecteurs`) | `pytest -q -m "postgres and not perf" tests/test_recherche_hybride.py` | actif avec repli ET avec e5 | 5b2df7d | ✅ établi (CI) |
| Rappel 50/50 et 13/13 conservé aux deux encodeurs | idem + `tests/test_recherche_fonds_reel.py` | idem | 5b2df7d | ✅ établi (CI pour le synthétique) / ⚠️ une fois (réel) |
| Poids e5-small : 465 Mo (modèle non quantifié 448 Mo + tokenizer 17 Mo) | mesure de l'audit indépendant du 22/09 | le « ~130 Mo » publié auparavant était faux, corrigé | 98bea0f | ⚠️ démontré une fois (audit ; HF injoignable depuis ce sandbox) |
| Peuplement e5 : 234,1 ms / 12 fiches | mesure audit | idem | 5b2df7d | ⚠️ démontré une fois |
| Assistant 7B écarté (postes 8 Go, §17.14 Phase 4) | décision documentée CHANGELOG §Lot F | — | 5b2df7d | ✅ établi (décision) |

## Qualité / CI

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| CI 8/8 verte avec sauvegarde, audit_projet et vérité étendue | `gh run view <id>` | run push 35738002767 = success 8/8, run PR 35738011591 = success 8/8 (docker, backend 3.11/3.12/3.13, integration, e2e, frontend, sauvegarde) ; audit_projet 10/10 vert en CI | 13c7d96 | ✅ établi (prouvé en CI) |
| Porte garde-fou audit_projet | `python3 scripts/audit_projet.py` | 10/10 contrôles verts (10/10 en --rapide) : CI.yml, fixture réelle, 0 except aveugle, calibration, 8 tests §17.11, vérité unique 74 cibles, ré-export docs | 13c7d96 | ✅ établi |
| Vérité terrain étendue 74 cibles | `python -m seamtech_search.fiches.cli banc sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` | 74/74 champs corrects (100.0 %) | 76cb9ee → 13c7d96 | ✅ établi |
| CI 7/7 verte (historique des consolidations) | `gh run view <id>` | runs 35666837231, 35667273452, 35716412346 = success 7/7 ; run push 35715779367 ROUGE (flake latence sous --cov, corrigé depuis) | 64e090c → consolidation | ✅ établi / flake corrigé, preuve CI en cours |
| Suite SQLite (défaut) | `pnpm test:e2e` (frontend) | 22 passed / 3 skipped, inchangée | b49479d | ✅ établi (CI) |
| Comptes pytest avec filtre (mesures locales du 22/09, PostgreSQL 16.2 + pgvector + pg_trgm + unaccent) | voir colonne commande | `-m "not postgres"` = 487/3 · `-k "not postgres"` = 468/3 · `-k "not postgres and not s3"` = 459/2 · `-m "postgres and not perf"` = 112/1 (le saut = test e5 sans poids hors CI ; 113/0 en CI) · `-m postgres` = 115/1 · `-m perf` = 3/0 · `-k "not s3"` = 593/3 | consolidation 22/09 | ✅ établi (rejoué ce jour) |
| Porte de couverture | `pytest -k "not s3" -m "not perf" --cov=seamtech_search … && python scripts/coverage_gate.py coverage.json` | « Coverage gate passed » (locale 22/09) | idem | ✅ établi (CI + local) |
| Empreintes des documents épinglées | `pytest -q tests/test_empreintes_fixtures.py` | 4 passés (sha256 + tailles des 4 PDF) | consolidation 22/09 | ✅ établi |
| pip-audit | `pip-audit -r requirements.txt` | « No known vulnerabilities found » | idem | ✅ établi (CI) |

## Calibration / véracité

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Verrou calibration `calibre: false` → 409 sur `/validation/lot` | e2e live `validation.spec.ts` (3e test) | « VERROU DE CALIBRATION » refusé sans acquittement | b49479d | ✅ établi (CI) |
| La calibration n'est PAS acquittée | — | `calibre: false` | — | ✅ établi (volontaire, documenté) |

## Ce qui n'est PAS prouvé (liste exhaustive au 22/09)

1. Chrono humain de validation : **non mesuré** (0/3 fiches).
2. Échelle réelle (20-30 fiches minimum, 10 000 à terme) : rappels, latence et
   classifieur à re-mesurer — bloqué par l'accès lecture seule à l'archive.
3. Sauvegarde testée par une restauration (R2/S3) : **ÉPROUVÉ en CI** avec MinIO réel et PostgreSQL 16 (Lot H.1, runs 35738002767, 35738011591, 35738937509, 35738944082, 35742665674, 35742670756, 35743433330, 35743439266, 35745835236 et 35745844265, 23/23 tests passés, restauration 50 000 fiches en 0,62 s - 1,34 s ; la base `f42e29b` reste ROUGE jusqu'à la fusion de la PR #21 ; le run de fusion (événement pull_request) est vert 8/8, donc la fusion rétablit la base).
4. Les mesures e5 réel (poids, latence, classifieur) proviennent de
   l'environnement d'audit du 22/09 : Hugging Face est injoignable depuis ce
   sandbox (000) — non re-mesurables ici, citées avec leur source.


## Re-mesure du runbook de fusion — 22/09/2026

Runbook de fusion re-mesuré le **22/09/2026** sur la pile **fc4f99e** —
répétition générale : **0 conflit, 272 fichiers, 510 passés / 3 sautés**
(`pytest -q -m "not postgres"`, 123 désélectionnés, Python 3.11.2).

Preuve : [FUSION_MAIN.md §4](FUSION_MAIN.md#4-preuves-brutes--répétition-du-22092026-sur-fc4f99e),
commandes et sorties brutes datées, commit d'essai local
`0055a91cc17509479a9cb84c5512ea520036185b` (jamais poussé).
Diff : 128 fichiers, +22953 / −28 ; 23 sections du CHANGELOG, les cinq sections
de main conservées intégralement ; PDF racine conservé et byte-identique.
**Écart : 52 commits côté pile / 1 côté main, pas les 41 annoncés.**

Le run push de la pile **35751552283** est `success` pour chacun de ses huit
jobs (JSON brut dans le runbook). Il ne valide pas le nouveau SHA documentaire.
Livraison sur **arena/01a0c9eb-seamtech-search** imposée par la session, pas
sur la base ; aucune PR créée/fermée, aucune fusion distante. CI du nouveau
SHA : voir le rapport de livraison, **NON PROUVÉE tant que ses huit jobs ne
sont pas terminés avec success**. Revalider toute tête différente de fc4f99e.

## Assistant sourcé (Lot I — 22/09/2026, `arena/01a0ca1a-seamtech-search`)

| Affirmation | Commande | Sortie brute | Commit | Statut |
|---|---|---|---|---|
| Le jeu des 8 questions répond comme attendu (6 sourcées, 1 refus, 1 ambigu qualifié) | `SEAMTECH_TEST_DATABASE_URL=… python scripts/mesure_assistant.py` | 8 réponses rendues dans `RAPPORT_20260922_LOT_I.md` (Q1 6,60 m zone PDF ; Q2 Spi Asymétrique ; Q3 1 fiche ; Q4 3 fiches ; Q5 Nylon ; Q6 refus 0 citation ; Q7 3 interprétations sourcées ; Q8 « ~ » sans objet) | 1d5df21 → HEAD | ✅ établi (local, re-jouable ; CI à confirmer sur la PR) |
| Taux de réponses sourcées = 100 % des réponses effectives | idem + `pytest tests/test_assistant_postgres.py::test_invariant_reponses_sourcees_100_pourcent` | 7/7 = 100 % ; invariant asserté en test | 1d5df21 → HEAD | ✅ établi |
| Latence assistant p50/p95 sur les 8 questions, n étiqueté | `scripts/mesure_assistant.py` (1 échauffement + 10 passages/question) | direct n=80 : p50 = 1,79 ms, p95 = 2,21 ms, max = 2,45 ms ; HTTP n=80 : p50 = 6,31 ms, p95 = 7,66 ms, max = 8,96 ms (sandbox 22/09) | 1d5df21 → HEAD | ⚠️ démontré une fois par environnement (CI publie les siens en ::notice) |
| LE REFUS est prouvé par un test automatisé, montré rouge puis vert | `pytest tests/test_assistant.py::test_refus_base_vide_n_invente_rien` | VERT 1 passed ; puis repli « plausible » introduit volontairement → ROUGE (« valeur plausible inventée … 6,55 m ») ; retiré → VERT (sorties brutes dans le rapport) | 1d5df21 → HEAD | ✅ établi |
| Champ « non applicable » dit sans objet, jamais chiffré (RG5) | `pytest tests/test_assistant_postgres.py::test_q8_non_applicable_rg5` | « consigné « ~ » dans la fiche — sans objet (non applicable) » + citation zone PDF | 1d5df21 → HEAD | ✅ établi |
| RG13 : aucune écriture dans l'archive | `scripts/mesure_assistant.py` (SHA-256 arbre sample_data avant/après) | `12e053f72f157decc68fd2ae2c4362a7d43d816066d87e03f16b1f99f6cde0ca` = `12e053f…` (IDENTIQUE) | 1d5df21 → HEAD | ✅ établi |
| RG14 : aucun appel réseau sortant | `sudo unshare -n python scripts/mesure_assistant.py --dans-namespace` (interfaces = lo seul, connexion sortante → Network is unreachable) | 8/8 réponses identiques sans réseau ; direct p50 = 2,05 ms / p95 = 3,16 ms (n=80) | 1d5df21 → HEAD | ✅ établi (namespace vide vérifié dans le script) |
| Journalisation « comme les recherches » | `pytest tests/test_assistant_postgres.py::test_journalisation_comme_recherches` | 8 lignes `recherche_log` `filtres->>'canal'='assistant'`, nb_resultats = citations | 1d5df21 → HEAD | ✅ établi |
| Pas de régression : suites existantes restent vertes + nouveaux tests | `pytest -q -m "not postgres"` → 532 passed, 3 skipped (510 d'avant + 22) ; `-m "postgres and not perf and not sauvegarde"` → 125 passed, 1 skipped (e5, env) ; `-m perf` → 4 passed | sorties brutes dans le rapport | 1d5df21 → HEAD | ✅ établi (local ; CI à confirmer) |
| Seuils tenus (audit 12/12, couverture, ruff, pip-audit) | `python scripts/audit_projet.py --rapide` ; `ruff check .` ; `pip-audit -r requirements.txt` ; `python scripts/coverage_gate.py coverage.json` | 12/12 ; All checks passed ; No known vulnerabilities ; « Coverage gate passed » (assistant.py 91,4 %) | 1d5df21 → HEAD | ✅ établi |
| CI 8 jobs verte sur la branche | `gh run list --branch arena/01a0ca1a-seamtech-search` | à relever après push — NON PROUVÉ tant que les jobs ne sont pas `success` | (push) | ❌ non mesuré au moment du rapport (PR ouverte, run en cours) |
