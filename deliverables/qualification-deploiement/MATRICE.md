# Matrice des contrôles (lot 3)

Preuve_id préfixe : `qualif-2026-10-13`.

| ID | Contrôle | Exécuté où | Résultat |
|---|---|---|---|
| A-01 | Parité IDs+ordre familles latin | sandbox PG 16.2 | 51 passed lot ciblé |
| A-02 | GET /recherche et /search | sandbox TestClient | passed |
| A-03 | avant 8dd5ae7 / après 023 | JSON A-03-* | defaut_observe true→false |
| A-04 | Données pré-023 puis migration | sandbox | passed |
| A-05 | Mutation UPDATE titre | sandbox | passed |
| A-06 | Filtres / pagination / arabe | sandbox | passed |
| A-07 | Volume 800 | sandbox | p50 11.5 / p95 17.8 ms |
| A-08 | Volume 2000 | sandbox | 13.2 ms, Seq Scan |
| A-09 | Parité dédup sans_accents | CI (après correctif) | régression œufs corrigée |
| B-01 | Fichiers locaux manquants → incomplet | sandbox + marqueur sauvegarde | passed sandbox |
| B-02 | Restore base+fichiers distincts | sandbox | passed |
| B-03 | Objets MinIO sources coupées | test écrit, MinIO absent ici | non exécuté sandbox |
| C-01 | OCR fixtures tesseract | CI job `ocr` run 38399629077 | taux 1.000 / 0.955 |
| C-02 | OCR sandbox local | tesseract absent | BLOQUÉ |
| C-03 | import→index→recherche | job ocr sans PG | non exécuté |
| D-01 | Install propre Debian native | — | non exécuté |
