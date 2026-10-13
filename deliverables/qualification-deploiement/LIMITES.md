# Limites — ce qui n'est pas clos

- **Quatre points non présentés comme tous corrigés.**
- **Perf** : sur `ee776d0` (CI backend 3.12 run 38422805119) p50 5–14 ms
  et p95 7–35 ms — critère 100 ms **tenu sur ce run**, pas un PASS atelier.
  `a3506c8` avait p50 275 ms (pas le flake p50 bas). Comparaison
  8dd5ae7/a3506c8/HEAD sur un **même** runner pas encore close. Seuil
  250 ms non relevé.
- **OCR réel atelier** : le PASS C est le chemin crawl+upsert+/search+/open
  en CI. Pas l'import navigateur. Sandbox local sans binaire tesseract
  (CDN Releases fermé).
- **Sauvegarde atelier** : MinIO officiel CI, pas R2, pas le bucket
  d'exploitation. Restore conjoint PG+fichiers+objets prouvé en CI.
- **D native** : essai PASS sur ubuntu-24.04 (38425712093). Pas le PC
  opérateur, pas Docker, pas la prod.
- **Choix d'exploitation** : OS atelier, fournisseur objets, volume,
  langues OCR métier — non tranchés.
- **Windows, 50 000 documents** : non exécutés et non exigés.
- **RPO/RTO, gel des écrivains** : décisions d'exploitation.
- **Recette historique** (`deliverables/recette-virtuelle/`) : non
  rejouée, non réécrite.
- **Installation entreprise** : non touchée. PR #37 non fusionnée.
