# Matrice des contrôles (lots 3–4)

Preuve_id préfixe : `qualif-2026-10-13`.

Ne pas sommer des lots qui se recoupent.

| ID | Contrôle | Exécuté où | Résultat |
|---|---|---|---|
| A-01 | Parité IDs+ordre familles latin | sandbox PG 16.2 | passed lot ciblé |
| A-02 | GET /recherche et /search | sandbox TestClient | passed |
| A-03 | avant 8dd5ae7 / après 023 | JSON A-03-* | defaut_observe true→false |
| A-04 | Données pré-023 puis migration | sandbox | passed |
| A-05 | Mutation UPDATE titre | sandbox | passed |
| A-06 | Filtres / pagination / arabe | sandbox | passed |
| A-07 | Volume 800 | sandbox | p50 11.5 / p95 17.8 ms (indicatif) |
| A-08 | Volume 2000 | sandbox | 13.2 ms, Seq Scan possible (indicatif) |
| A-09 | Parité dédup sans_accents | correctif lot 3 | œufs : sans_accents inchangé |
| B-01 | Fichiers locaux manquants → incomplet | sandbox | passed |
| B-02 | Restore base+fichiers distincts | sandbox | passed |
| B-03 | Objets MinIO sources coupées | CI `sauvegarde` + qualification-reserves | à relire sur SHA lot 4 (échec 0665a68) |
| B-04 | Objet-only ≠ manquant local | unitaire sqlite | passed local lot 4 |
| C-01 | OCR fixtures tesseract | CI job `ocr` run 38402755760 | SUCCESS SHA 0665a68 |
| C-02 | OCR sandbox local | tesseract absent | BLOQUÉ |
| C-03 | import→index→recherche→open | qualification-reserves (PG+OCR) | à relire sur SHA lot 4 |
| C-04 | Scan anglais | job `ocr` + qualification (tesseract-ocr-eng) | à relire sur SHA lot 4 |
| D-01 | Install propre Debian native | — | PRÉPARÉ NON EXÉCUTÉ |
