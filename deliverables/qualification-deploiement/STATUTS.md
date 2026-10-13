# Statuts A–D

**Synthèse unique :** [`SYNTHESE_FINALE.md`](SYNTHESE_FINALE.md)

**SHA applicatif retenu : `d202f0b0c371ab5659e7615d7fee80c99f0ae0dd`** (PR #37, non fusionnée).

`51d2772`, `ee776d0` et `6ccc383` sont des **ancêtres**. `seamtech_search/` de HEAD = celui de `51d2772`. Les runs cités dans la synthèse sont ceux de **`d202f0b`**.

| | Statut | Preuve `d202f0b` |
|---|---|---|
| A Recherche | CORRIGÉ ET TESTÉ / attente humaine | postgres 279/0 ; qual 74/0 |
| A Perf | CORRIGÉ ET TESTÉ (CI, pas atelier) | p50 6–13 ms, p95 8–42 ms ; same-runner HEAD &lt; 100 ms |
| B Sauvegarde | CORRIGÉ ET TESTÉ | job sauvegarde + B-03 `…_obj` |
| C OCR | CORRIGÉ ET TESTÉ (crawl/index/`/search`/`/open` + tesseract hôte) | **pas** dépôt navigateur de scans |
| D Natif | CORRIGÉ ET TESTÉ (essai ubuntu-24.04) | native-summary PASS |

Pas un PASS production. Recette historique intacte. **EN ATTENTE DE VALIDATION HUMAINE.**
