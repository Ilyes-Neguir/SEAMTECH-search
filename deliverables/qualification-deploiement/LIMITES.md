# Limites — ce qui n'est pas clos

- **OCR réel** : tesseract / ocrmypdf / poppler absents. Réserve C
  BLOQUÉE. Le code v5 n'est pas une preuve d'atelier.
- **MinIO / R2** : non installés. `s3rver` n'est pas MinIO. Copies
  d'objets S3 : code présent, **non exécuté** contre un vrai bucket.
- **Windows, Docker Compose, image de production** : non exécutés.
- **PostgreSQL compilé `--without-icu`** : `unaccent` contrib utilisé ;
  comportement ICU non comparé.
- **Volume** : corpus de recette (≤ 15 fiches + 1 PDF local). Pas de
  50 000 fiches ici. Le test historique 50k n'a pas tourné (harnais
  DSN socket).
- **Perf atelier** : pas de p95 < 100 ms publié sur ce correctif
  (mesure indicative corpus minuscule seulement).
- **Langues non latines** : hors couverture de `seamtech_texte_recherche`.
- **RPO/RTO, gel des écrivains, rétention hors-site** : décisions
  d'exploitation, pas des constantes du code.
- **Recette historique** (`3304b91`) : non rejouée, non réécrite.
- **Installation entreprise** : non touchée.
- **Tests `test_migrations_metier` / `test_sauvegarde_restauration`**
  avec DSN `?host=/tmp/…` : le harnais historique découpe le chemin du
  socket. Ce n'est **pas** une clôture produit ; les tests complémentaires
  utilisent `partition("?")`.
