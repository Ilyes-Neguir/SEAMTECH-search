# Rapport final d’optimisation — audit pré-merge PR #33

**Date :** 2026-09-29  
**Branche :** `arena/01a0ed7b-seamtech-search`  
**Base comparée :** `origin/main` = `a7ffe7d2800ed697f306a93b0564b4ad8c87eb74`  
**Arbre code/tests audité :** `6ea4bee364d01c4a351a23c0c68c765d6b719976`  
**PR :** #33, ouverte, non fusionnée. Ce rapport n’autorise pas à fusionner : aucune fusion n’a été effectuée.

> Le commit portant ce rapport et la checklist est une mise à jour documentaire postérieure à l’arbre code/tests `6ea4bee`. Les runs finaux sur le HEAD documentaire sont à lire sur les checks GitHub de la PR; le rapport conserve aussi les preuves de l’arbre code/tests audité ci-dessus.

## Verdict de l’auto-vérification

**PR PRÊTE À MERGER au regard des six contrôles pré-merge demandés ci-dessous.** Cela ne constitue ni une décision de fusion du commanditaire, ni une autorisation de mise en production. Aucun merge n’a été lancé. Les portes de réception opérateur, VPS01 et bascule R2 restent non mesurées et séparées de cet audit de code.

## 1. Suite pytest et conservation des nodeids

Commande exécutée depuis deux worktrees détachés représentant exactement les refs comparées :

```text
pytest -m "not postgres and not s3 and not perf" -q
```

Sorties brutes conservées : `/tmp/seamtech-main-pytest.log` et `/tmp/seamtech-pr-pytest.log`.

```text
MAIN (a7ffe7d2800ed697f306a93b0564b4ad8c87eb74)
764 passed, 3 skipped, 234 deselected, 1 warning in 58.98s

PR (6ea4bee364d01c4a351a23c0c68c765d6b719976)
771 passed, 3 skipped, 234 deselected, 1 warning in 57.02s
```

Le warning est la dépréciation Starlette/httpx de `fastapi.testclient`, non un échec de test.

Collection avec le même marqueur :

```text
MAIN: 767/1001 tests collected (234 deselected)
PR:   774/1008 tests collected (234 deselected)
Nodeids main absents de la PR: 0
Nodeids PR supplémentaires: 7
```

La comparaison des ensembles de nodeids établit donc que **les 767 nodeids de main sont tous présents à l’identique sur la PR**. Les quatre identifiants historiques d’empreinte/doublon relevés pendant le premier audit ont été restaurés. Les sept ajouts sont les nouveaux gardes de retrait du doublon, les nouveaux contrôles de fixtures OCR, les deux tests du drapeau optionnel et un test du parseur de comptage CI. Aucun test de main n’a été supprimé ou renommé.

## 2. Inventaire du diff et vérifications fonctionnelles

| Point audité | Vérification / constat |
|---|---|
| Runners | Les 9 définitions de job de `.github/workflows/ci.yml` sont sur `ubuntu-24.04`; la matrice backend produit 3 exécutions. Le workflow de benchmark utilise aussi `ubuntu-24.04`. |
| PDF racine | Le doublon racine est supprimé du tree courant; la copie canonique reste dans `sample_data/`. Le garde exige l’absence de PDF racine et conserve l’empreinte/taille de la copie canonique. Le blob historique n’est pas effacé de Git. |
| Tests historiques PDF/OCR | Les nodeids d’empreinte du PDF racine et des deux fixtures OCR sont conservés. Le nodeid racine vérifie maintenant l’absence du doublon et l’empreinte canonique; les fixtures OCR sont vérifiées structurellement plutôt que par des octets PDF non déterministes. Les fichiers PDF des fixtures ne sont ni ajoutés ni modifiés par ce diff. |
| Actions GitHub | Bumps vérifiés en amont : `actions/checkout@v7`, `actions/setup-python@v7`, `actions/setup-node@v7`, `actions/upload-artifact@v7`, `actions/cache@v6`, `docker/setup-buildx-action@v4`, `pnpm/action-setup@v6`. |
| Audit dépendances | `securite-dependances` audite `requirements.txt` (runtime Python) et `pnpm audit --prod` (frontend production). La politique échoue sur une vulnérabilité HIGH/CRITICAL corrigeable; les avis sans fix sont signalés. L’exit code 1 de pip-audit pour des avis est capturé afin que le JSON soit classifié; JSON absent/invalide ou code outil inattendu reste bloquant. L’enrichissement OSV est une exception réseau CI documentée (`RG14_EXCEPTION`), pas une dépendance runtime. |
| Phase 2.2 | La validation ajoute tri par confiance puis code, raccourcis clavier avec interaction humaine explicite, focus/zone PDF, préchargement du document suivant et chrono local de session. Les anomalies demandent confirmation avant validation; aucune décision métier n’est automatisée. |
| Garde E2E live | Le nombre combiné du parcours validation + badge passe de 4 à 8 : 3 tests de validation préexistants, 1 badge « Non vérifiée », 4 tests Phase 2.2. La porte exige 8 réussites au premier essai, 0 flaky, 0 skip et une annotation de chrono. Les gardes séparées restent à 2 tests doublons et 23 tests d’authentification. |
| Runbooks | `docs/deploiement/poste-atelier.md` et `docs/deploiement/vps01-r2.md` documentent les usages atelier et VPS01. Le second garde MinIO comme backend effectif; R2 est seulement une option après D-2. Sources montées en lecture seule (RG13). |
| Benchmark | `.github/workflows/scale-bench.yml` et `scripts/scale_bench.py` exécutent un PostgreSQL jetable, 10 000 fiches synthétiques et 50/100 échantillons/scénario, percentiles et plans EXPLAIN archivables. Le benchmark est identifié comme **SYNTHÉTIQUE**, jamais comme mesure VPS01/R2. |
| Drapeau assistant/ML | `SEAMTECH_OPTIONAL_FEATURES_ENABLED` garde l’état actif par défaut; `false` masque les routes Assistant/ML et évite de charger le modèle. Recherche classique, validation humaine et verrou de validation groupée restent inchangés. |
| Checklist/rapport | Checklist mise à jour en §20 et présent rapport daté. Le rapport projet historique distingue les anciens checkpoints du nouvel audit. |

Les jobs CI exécutent maintenant toutes leurs sélections pytest par `-m` uniquement : marqueurs standards et marqueurs ciblés `ci_guard_*`, `integration_docker`, `sauvegarde`, `recette_corpus`, `ocr_suite`. Aucun appel pytest actif du workflow ne sélectionne par `-k`, fichier ou nodeid. Un test de garde vérifie cette règle. Les commentaires décrivant d’anciens filtres `-k` sont historiques, pas des commandes actives.

## 3. Intégrité des archives et confidentialité du diff

Les sept fichiers ZIP suivis ont été hachés dans le worktree de `main` et dans celui de la PR : **7/7 noms et SHA-256 identiques**. Les sorties de référence sont consignées dans `docs/RELEASE_CANDIDATE_CHECKLIST.md` §19. Aucun ZIP n’a été modifié.

Inventaire des ajouts binaires/fichiers : aucun PDF, aucune archive ZIP de corpus, aucun manifeste métier, aucune table de valeurs client n’est ajouté. Les deux JSON ajoutés sous `docs/benchmarks/` contiennent des percentiles et plans d’exécution du benchmark **synthétique** uniquement. Le PDF racine apparaît comme suppression.

La recherche des noms de bateaux/clients connus dans le diff trouve les noms de fichiers de ZIP dans la table d’empreintes déjà documentée, ainsi que des références au code d’essai historique utilisé pour retirer le doublon racine. Elle ne révèle pas de nouvelles fiches, de champs métier, de contenus PDF ou de valeurs client. La copie canonique et les ZIP sont déjà suivis dans la base; aucune donnée client réelle nouvelle n’a été ajoutée.

## 4. Seuils, gardes et politique de sélection

- Le plancher global de couverture reste **85,0 %**. Les planchers module `api=87`, `import_pipeline=90`, `indexer=90`, `jobs=94`, `redis_store=92`, `storage=97`, `worker=92`, OCR `inventaire=80`, `etat=55`, `pipeline=54`, `cli=40` sont inchangés; `scripts/coverage_gate.py` n’a reçu que des corrections de commentaires de commandes historiques.
- Les gardes E2E live sont renforcées par un compteur exact de 8, zéro skip et zéro flaky; les gardes séparées 2 et 23 restent exactes. Aucun compteur n’a été abaissé.
- Recherche des ajouts `pragma: no cover` : **aucun**.
- Collection main/PR : **zéro nodeid manquant**; aucun nodeid n’a été renommé.
- Les invocations actives de pytest CI sélectionnent toutes avec `-m`; le garde `tests/test_selection_ci.py` le contrôle.
- Le premier passage marker-only (`ad6367c`) a détecté un défaut du parseur du garde sauvegarde : il lisait le dénominateur du résumé `23/1008` au lieu du numérateur `23`. Le parseur a été corrigé et testé; le rerun sur `6ea4bee` est vert. Aucun seuil ni assertion de sauvegarde n’a été abaissé.

## 5. Runs GitHub sur l’arbre code/tests audité

Les trois runs suivants pointent tous vers le SHA exact `6ea4bee364d01c4a351a23c0c68c765d6b719976` :

- Push CI [36607480324](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36607480324) : **success, 11/11 jobs**.
- PR CI [36607487809](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36607487809) : **success, 11/11 jobs**.
- Benchmark [36607487218](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36607487218) : **success, 1/1 job SYNTHÉTIQUE**.

Noms exacts des 11 jobs CI : `backend (3.11)`, `backend (3.12)`, `backend (3.13)`, `frontend`, `docker`, `integration`, `e2e`, `sauvegarde`, `recette-corpus-reel`, `ocr`, `securite-dependances`. Chacun est vert sur les runs push et PR. Le run de benchmark n’est pas compté parmi ces onze jobs.

## 6. Vérité terrain — RÉEL / SYNTHÉTIQUE / NON MESURÉ

| Catégorie | Éléments et limites |
|---|---|
| **RÉEL** | Résultats pytest locaux sur les arbres exacts main/PR; comparaison de nodeids sans perte; SHA-256 des 7 ZIP identiques entre main et PR; push et PR CI verts sur l’arbre code/tests audité, y compris services de CI et recette de corpus. Cela ne constitue pas une mesure sur le VPS01 du commanditaire. |
| **SYNTHÉTIQUE** | Benchmark [36607487218](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36607487218) : base PostgreSQL jetable, 10 000 fiches générées, 50 échantillons/scénario. Les anciens rapports complets et plans EXPLAIN sont archivés sous `docs/benchmarks/scale-bench-synthetique-*.{json,md}`. Ces percentiles ne prouvent pas les performances réelles. |
| **NON MESURÉ** | Chronométrage d’opérateurs sur l’installation réelle; charge et latence VPS01; capacité et coûts R2; exercice de restauration sur l’infrastructure du commanditaire; calibration des seuils de confiance à partir de fiches réelles validées. Le backend objet reste MinIO jusqu’à décision D-2. Le verrou `calibre:false` et le HTTP 409 de validation groupée restent actifs. |

**Conclusion d’usage :** les critères d’auto-vérification pré-merge sont satisfaits. La production n’est pas certifiée par ce rapport; aucune métrique synthétique ne doit être présentée comme réelle. Aucun merge ni déploiement n’a été réalisé.