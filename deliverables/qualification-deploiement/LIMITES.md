# Limites — ce qui n'est pas clos

- **Quatre points non présentés comme tous corrigés.**
- **Perf** : critère produit p95 < 100 ms **non démontré**. Échec CI
  `a3506c8` (p50 fonds réel 275 ms). Ne pas attribuer au runner sans
  comparaison 8dd5ae7 vs a3506c8 sur le même runner. Seuil 250 ms non
  relevé, test non retiré, garde-fou non neutralisé.
- **OCR réel atelier** : le PASS C est le chemin crawl+upsert+/search+/open
  en CI. Pas l'import navigateur. Sandbox local sans binaire tesseract
  (CDN Releases fermé).
- **Sauvegarde atelier** : MinIO officiel CI, pas R2, pas le bucket
  d'exploitation. Restore conjoint PG+fichiers+objets prouvé en CI.
- **D native** : procédure écrite ; non exécutée tant que le job
  `Qualification native` n'a pas un rapport sans FAIL. Docker Compose
  (recette-locale, integration) ≠ D. Pas le PC opérateur.
- **Choix d'exploitation** : OS atelier, fournisseur objets, volume,
  langues OCR métier — non tranchés.
- **Windows, 50 000 documents** : non exécutés et non exigés.
- **RPO/RTO, gel des écrivains** : décisions d'exploitation.
- **Recette historique** (`deliverables/recette-virtuelle/`) : non
  rejouée, non réécrite.
- **Installation entreprise** : non touchée. PR #37 non fusionnée.
