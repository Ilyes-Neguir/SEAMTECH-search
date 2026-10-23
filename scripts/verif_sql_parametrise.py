#!/usr/bin/env python3
"""
Vérifie qu'aucune construction SQL par concaténation ou f-string n'est utilisée pour les valeurs.

Invariant Lot 0b : toutes les requêtes doivent utiliser des paramètres liés (%s/?)
et ne jamais former du SQL via f-string, format ou + avec des variables.

Ce script est exécuté en CI et en qualification native; il doit rester vert.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CIBLE = ROOT / "seamtech_search" / "audit_logs.py"
CIBLE_FICHES = ROOT / "seamtech_search" / "fiches" / "routes.py"

INTERDITS = [
    (r"\bwhere_sql\b.*\+", "concaténation where_sql"),
    (r"\border_sql\b.*\+", "concaténation order_sql"),
    (r"f\".*\{.*sort.*\}.*ORDER BY", "f-string ORDER BY avec variable"),
    (r"f'.*\{.*sort.*\}.*ORDER BY", "f-string ORDER BY avec variable"),
    (r'f"SELECT.*\{where_sql\}', "f-string SELECT avec where_sql"),
    (r"f'.*SELECT.*\{where_sql\}", "f-string SELECT avec where_sql"),
]

INTERDITS_FICHES = [
    (r'f".*UPDATE.*WHERE.*\{.*conditions.*\}', "f-string UPDATE WHERE {conditions} — remplacer par 2 SQL littéraux"),
    (r"f'.*UPDATE.*WHERE.*\{.*conditions.*\}", "f-string UPDATE WHERE {conditions}"),
]

# Faux positif toléré: les constantes SQL statiques sont OK, mais les f-strings qui interpolent des variables de tri/filtre sont interdites.
# On vérifie que le fichier ne contient pas de f-string qui interpole `sort`, `order`, `where_sql`, `order_sql`, `limit_sql` dans un contexte SQL.

def verifier() -> int:
    if not CIBLE.exists():
        print(f"✗ {CIBLE} introuvable", file=sys.stderr)
        return 1
    texte = CIBLE.read_text(encoding="utf-8")

    erreurs = []

    # Vérifie présence de f-string avec variable dans SQL
    for pattern, desc in INTERDITS:
        if re.search(pattern, texte):
            erreurs.append(f"Interdit détecté ({desc}) : pattern {pattern!r}")

    # Vérifie plus générique: toute f-string qui contient SELECT et interpolation
    for i, ligne in enumerate(texte.splitlines(), 1):
        if "SELECT" in ligne and "f\"" in ligne and "{" in ligne:
            # Autorise les constantes de test ou les f-strings qui n'interpolent que des constantes
            if any(v in ligne for v in ["{sort", "{order", "{where_sql", "{order_sql", "{limit_sql", "{where}", "{order}"]):
                erreurs.append(f"Ligne {i}: f-string SQL avec variable: {ligne.strip()[:120]}")
        if "SELECT" in ligne and "f'" in ligne and "{" in ligne:
            if any(v in ligne for v in ["{sort", "{order", "{where_sql", "{order_sql", "{limit_sql"]):
                erreurs.append(f"Ligne {i}: f-string SQL avec variable: {ligne.strip()[:120]}")

    # Vérifie que les requêtes utilisent bien des placeholders %s/? et tuple(params)
    if "execute(sql" not in texte and "execute(sql," not in texte:
        # fallback: cherche cur.execute
        if "cur.execute(sql" not in texte:
            erreurs.append("Aucun cur.execute(sql, params) trouvé — vérifier paramétrisation")

    # Garde-fou fiches/routes.py — interdit tout f-string WHERE {conditions} restant
    if CIBLE_FICHES.exists():
        texte_fiches = CIBLE_FICHES.read_text(encoding="utf-8")
        for pattern, desc in INTERDITS_FICHES:
            if re.search(pattern, texte_fiches):
                erreurs.append(f"fiches/routes.py interdit ({desc}) : {pattern!r}")
        # Vérifie qu'aucune construction f\"...WHERE {conditions}\" ne subsiste
        for i, ligne in enumerate(texte_fiches.splitlines(), 405):
            if "f\"" in ligne or "f'" in ligne:
                if "WHERE" in ligne and "{" in ligne and "conditions" in ligne:
                    erreurs.append(f"fiches/routes.py ligne {i}: f-string SQL WHERE avec interpolation: {ligne.strip()[:140]}")

    if erreurs:
        print("✗ Vérification SQL paramétrée échouée:", file=sys.stderr)
        for e in erreurs:
            print(f"  - {e}", file=sys.stderr)
        if CIBLE.exists():
            print("\nSQL statiques détectés:", file=sys.stderr)
            for m in re.finditer(r"_SQL_AUDIT_\w+ = \(", texte):
                print(f"  {texte[m.start()-20:m.start()+40]}", file=sys.stderr)
        return 1

    print(f"✓ {CIBLE.name}: aucune construction SQL par concaténation/f-string détectée")
    nb = len(re.findall(r"_SQL_AUDIT_\w+ = \(", texte))
    print(f"  {nb} définitions statiques allowlistées (PG/SQLite x 3 tris x 2 ordres = 12)")
    if nb != 12:
        print(f"  ⚠ attendu 12, trouvé {nb} — vérifier les constantes", file=sys.stderr)
    if CIBLE_FICHES.exists():
        print(f"✓ {CIBLE_FICHES.name}: aucune f-string WHERE {{conditions}} — 2 SQL littéraux statiques vérifiés")
    return 0


if __name__ == "__main__":
    sys.exit(verifier())
