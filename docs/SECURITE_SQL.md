# Sécurité SQL — périmètre et état

**Invariant Lot 0b** : aucune construction SQL par concaténation ou f-string avec valeurs — toutes les requêtes doivent utiliser des paramètres liés `%s / ?` via `execute(sql, params)`.

**État actuel (post-b0f7419 → correctif final)** :
- `seamtech_search/audit_logs.py` : 12 requêtes statiques allowlistées vérifiées par `scripts/verif_sql_parametrise.py` (`✓ audit_logs.py: aucune f-string` — 12 définitions).
- `seamtech_search/fiches/routes.py:443-445` : **f-string hérité supprimé**. Remplacé par 2 SQL littéraux statiques sélectionnés selon le cas :
  ```python
  if statut_attendu is not None:
      cursor.execute("UPDATE fiche SET statut=%s ... WHERE id_fiche=%s AND statut=%s AND revision=%s RETURNING revision", (...) )
  else:
      cursor.execute("UPDATE fiche SET statut=%s ... WHERE id_fiche=%s AND revision=%s RETURNING revision", (...) )
  ```
  Aucune interpolation `{conditions}` ne subsiste. Le garde-fou `scripts/verif_sql_parametrise.py` vérifie désormais `fiches/routes.py` (`WHERE {conditions}` interdit — `✓ fiches/routes.py: aucune f-string WHERE {conditions}`).

**Limite historique** : `verif_sql_parametrise.py` ne prouve **que** le périmètre `audit_logs.py` (+ fiches `WHERE {conditions}` depuis ce correctif), pas l’intégralité du dépôt. La conformité globale repose sur revue manuelle + `tests/test_injection_reelle.py` (injection comme valeur → 0 ligne, table intacte). Ne pas prétendre que le script prouve tout le dépôt — il est un garde-fou ciblé.
