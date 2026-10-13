# Limites — ce qui n'est pas clos

- **Quatre points non présentés comme corrigés.**
- **OCR réel** : tessdata présent dans `/tmp` si le clone a réussi ; binaire
  tesseract absent (CDN Releases). Pas un PASS.
- **MinIO / R2 / S3** : non exécutés. CDN Releases bloqué ; pas de Go.
  s3rver ≠ MinIO (non utilisé comme substitut).
- **Choix d’exploitation** : OS, mode de déploiement, fournisseur objets,
  volume, langues OCR — non tranchés.
- **Windows, Docker Compose, 50 000 documents** : non exécutés et **non
  exigés** tant que ce ne sont pas des besoins.
- **Perf atelier / fonds réel** : mesure sandbox ~800 fiches synthétiques
  seulement. Critère produit 100 ms non déclaré PASS.
- **RPO/RTO, gel des écrivains** : décisions d’exploitation.
- **Recette historique** (`3304b91`) : non rejouée, non réécrite.
- **Installation entreprise** : non touchée. PR #37 non fusionnée.
