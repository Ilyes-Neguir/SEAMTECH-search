# Matrice des contrôles (SHA `a3506c8`)

Preuve_id : `qualif-2026-10-13-a3506c8`. Ne pas sommer des lots qui se recoupent.

| ID | Contrôle | Exécuté où | Résultat |
|---|---|---|---|
| A-01 | Parité IDs+ordre | qualification-reserves 38405546902 + postgres 279/0 | PASS |
| A-02 | GET /recherche et /search | idem `test_http_recherche_et_search_memes_identifiants` | PASS (dans les 74, skip=0) |
| A-03 | avant/après 023 | sandbox JSON A-03-* | defaut_observe true→false |
| A-04 | Données pré-023 | `test_donnees_existantes_avant_023_deviennent_paritaires` | PASS |
| A-05 | Mutation UPDATE | `test_mutation_document_existant_et_fonction_immutable` | PASS |
| A-06 | Filtres / pagination / arabe | `test_filtres_codes_pagination_non_latin` | PASS |
| A-07 | Volume 800 | `test_volume_parite_et_perf_indicative` | PASS (indicatif) |
| A-08 | Volume 2000 | `test_volume_2000_generate_series_indicatif` | PASS (indicatif, Seq Scan possible) |
| A-09 | Dédup sans_accents | postgres suite + garde-fou dédup VERT | PASS |
| B-01 | Locaux manquants → incomplet | qualification + sauvegarde | PASS |
| B-02 | Restore base+fichiers distincts | notice `…_restauree` job sauvegarde | PASS |
| B-03 | Objets MinIO sources coupées | `test_aller_retour_objets_minio_sources_indisponibles` ; notice `…_obj` ; garde-fou 0 skip | PASS |
| B-04 | Objet-only ≠ manquant local | unitaire + B-03 | PASS |
| C-01 | OCR fixtures | job ocr 115324073055 | PASS taux 1.000 / 0.955 |
| C-02 | OCR sandbox local | tesseract absent | BLOQUÉ |
| C-03 | crawl→upsert→GET /search→POST /open | `test_parcours_import_ocr_index_recherche_telechargement` dans 74 skip=0 | PASS (pas l'import navigateur) |
| C-04 | Scan anglais | `test_scan_anglais_retrouve_les_mots` dans 74 skip=0 | PASS |
| D-01 | Install propre Debian native | job Qualification native 38425712093 SHA `6ccc383` notice native-summary | PASS essai (pas production) |
| P-01 | Perf produit 100 ms | CI backend 3.12 `ee776d0` run 38422805119 p50 5–14 ms p95 7–35 ms | PASS sur ce run CI ; comparaison same-runner encore ouverte |
| P-02 | a3506c8 vs 8dd5ae7 même runner | job 38426744727 3 runs | RÉGRESSION a3506c8 fonds réel p50 203 vs 4,7 ms |
