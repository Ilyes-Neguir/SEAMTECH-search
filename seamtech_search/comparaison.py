"""Lot 4b — Fonction A : comparaison bloc-par-bloc Δcm/Δ% entre 2 voiles même modèle+type.

Contraintes : même boat_model_id + même sail_type_normalized, sinon 422.
Sortie : delta_json {field: {a, b, delta_cm, delta_pct, flag}}
Hétérogénéité signalée si >75% hors bande (seuil Lot5) — mais ici on compare 2 pièces.
"""
from __future__ import annotations

from typing import Any

CHAMPS_COMPARABLES = ["luff_length_cm", "leech_length_cm", "foot_length_cm", "luff_perpendicular_cm", "sail_area_sqm"]

def comparer_sails(sail_a: dict[str, Any], sail_b: dict[str, Any]) -> dict[str, Any]:
    """Compare deux dicts d'extraction (même modèle+type)."""
    if sail_a.get("boat_model_id") != sail_b.get("boat_model_id"):
        raise ValueError("Fonction A : les deux voiles doivent avoir le même boat_model_id")
    if sail_a.get("sail_type_normalized") != sail_b.get("sail_type_normalized"):
        raise ValueError("Fonction A : les deux voiles doivent avoir le même sail_type_normalized")
    delta: dict[str, Any] = {}
    for champ in CHAMPS_COMPARABLES:
        a = sail_a.get(champ)
        b = sail_b.get(champ)
        if a is None or b is None:
            delta[champ] = {"a": a, "b": b, "delta_cm": None, "delta_pct": None, "flag": "MANQUANT"}
            continue
        # a,b en cm (ou m2 pour surface — traité pareil mais unité m2)
        unit = "cm" if champ != "sail_area_sqm" else "m2"
        d_cm = float(b) - float(a)
        # delta_pct = (b-a)/a *100 ; si a==0 -> None
        if float(a) == 0:
            d_pct = None
        else:
            d_pct = round((float(b) - float(a)) / float(a) * 100, 2)
        flag = "OK"
        if d_pct is not None and abs(d_pct) > 10:
            flag = "ECART_SIGNIFICATIF"
        if champ == "sail_area_sqm" and d_pct is not None and abs(d_pct) > 15:
            flag = "AIRE_ECART"
        delta[champ] = {"a": a, "b": b, "delta_cm": round(d_cm, 2), "delta_pct": d_pct, "unit": unit, "flag": flag}
    # Synthèse
    ecarts = [v for v in delta.values() if v.get("delta_pct") is not None]
    nb_significatifs = sum(1 for v in ecarts if v.get("flag") != "OK")
    synthese = "CONFORME" if nb_significatifs == 0 else "ECARTS_DETECTES"
    return {"delta_json": delta, "synthese": synthese, "nb_champs_comparés": len(CHAMPS_COMPARABLES), "nb_ecarts": nb_significatifs}

def fetch_sail(cursor: Any, sail_id: int) -> dict[str, Any]:
    cursor.execute("SELECT id, boat_model_id, sail_type_normalized, luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm FROM sail_extractions WHERE id = %s", (sail_id,))
    row = cursor.fetchone()
    if not row:
        raise LookupError(f"sail_extraction {sail_id} introuvable")
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))

def inserer_comparaison(cursor: Any, boat_model_id: int, sail_type_normalized: str, sail_a_id: int, sail_b_id: int, delta_json: dict, created_by: int | None) -> int:
    cursor.execute(
        "INSERT INTO comparaisons_sails (boat_model_id, sail_type_normalized, sail_a_id, sail_b_id, delta_json, created_by) VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
        (boat_model_id, sail_type_normalized, sail_a_id, sail_b_id, __import__("json").dumps(delta_json), created_by),
    )
    r = cursor.fetchone()
    return int(r[0]) if r else 0
