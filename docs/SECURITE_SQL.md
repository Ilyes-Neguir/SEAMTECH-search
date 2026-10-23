# Sécurité SQL — périmètre et limites

**Contexte** : Lot 0b exige « aucune SQL concat/f-string (valeurs liées) ». Le script `scripts/verif_sql_parametrise.py` vérifie le périmètre `seamtech_search/audit_logs.py` (12 définitions allowlistées, `execute(sql, params)` liés, `limit/offset/sort/order/outcome` validés).

**Limite documentée** : `seamtech_search/fiches/routes.py:443-445` contient un fragment hérité :

```python
conditions = "id_fiche = %s AND revision = %s"  # ou "id_fiche = %s AND statut = %s AND revision = %s"
cursor.execute(f"UPDATE fiche SET statut = %s, revision = revision + 1, updated_at = now() "
               f"WHERE {conditions} RETURNING revision", tuple(parametres))
```

`conditions` est une constante parmi 2 chaînes hardcodées, `parametres` est un tuple de valeurs liées (`%s`). Les valeurs utilisateur (`statut_cible`, `id_fiche`, `revision_attendue`, `statut_attendu`) ne sont **jamais interpolées** — seules 2 colonnes/filtres statiques le sont. Ce pattern est hérité (pré-Lot0b) et non étendu ; Lot 0b n'ajoute aucune construction dynamique.

**Preuve** : `scripts/verif_sql_parametrise.py` scope `audit_logs.py` seul — preuve globale via revue manuelle + `test_injection_reelle.py` (injection comme valeur → 0 ligne, table intacte, `pglast` non seul).

**Action** : ne pas étendre l'usage de `f"WHERE {conditions}"` ; toute nouvelle requête doit être 100% statique + paramétrée.
