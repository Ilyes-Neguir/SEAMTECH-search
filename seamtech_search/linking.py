"""Lot 3b — Rattachement + correction + apprentissage (029).

- Rattachement : résolution boat_model_id via normalized_key / alias active.
- Correction : approbateur corrige champ → historisé dans extraction_corrections.
- Apprentissage : déduit learned_extraction_patterns (pending_review), jamais auto-écrasement.
"""
from __future__ import annotations

from typing import Any

def _norm(s: str) -> str:
    try:
        from .recherche import normaliser_cle_recherche
        return normaliser_cle_recherche(s)
    except Exception:
        return s.lower().strip()

def resoudre_boat_model(cursor: Any, raw_name: str) -> int | None:
    """Résout boat_model_id via canonical normalized_key ou alias active."""
    cle = _norm(raw_name)
    # 1) canonique
    cursor.execute("SELECT id FROM boat_models WHERE normalized_key = %s", (cle,))
    row = cursor.fetchone()
    if row:
        return int(row[0])
    # 2) alias active
    cursor.execute("SELECT boat_model_id FROM boat_model_aliases WHERE alias_normalized = %s AND status = 'active'", (cle,))
    row = cursor.fetchone()
    if row:
        return int(row[0])
    return None

def attacher_extraction(cursor: Any, extraction_id: int, boat_model_id: int | None = None, boat_model_raw: str | None = None) -> dict[str, Any]:
    """Attache une extraction à un modèle ; si boat_model_id non fourni, tente résolution via raw."""
    if boat_model_id is None and boat_model_raw:
        boat_model_id = resoudre_boat_model(cursor, boat_model_raw)
    if boat_model_id is None:
        raise ValueError(f"Impossible de résoudre boat_model pour '{boat_model_raw}' — créez d'abord le modèle ou alias.")
    cursor.execute("UPDATE sail_extractions SET boat_model_id = %s, boat_model_raw = COALESCE(%s, boat_model_raw), validation_status = 'pending' WHERE id = %s RETURNING id, boat_model_id", (boat_model_id, boat_model_raw, extraction_id))
    row = cursor.fetchone()
    if not row:
        raise LookupError(f"extraction {extraction_id} introuvable")
    return {"id": row[0], "boat_model_id": row[1]}

def corriger_extraction(cursor: Any, extraction_id: int, field_name: str, new_value: str, corrected_by: int, reason: str | None = None) -> dict[str, Any]:
    """Corrige un champ d'extraction (approbateur uniquement) + historise."""
    cursor.execute("SELECT id, sail_type_normalized, boat_model_raw, boat_model_id, luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm, material_raw FROM sail_extractions WHERE id = %s", (extraction_id,))
    row = cursor.fetchone()
    if not row:
        raise LookupError(f"extraction {extraction_id} introuvable")
    cols = [d[0] for d in cursor.description]
    cur = dict(zip(cols, row))
    old_val = cur.get(field_name)
    old_str = str(old_val) if old_val is not None else None
    # Met à jour (conversion type selon champ)
    type_map = {
        "luff_length_cm": int, "leech_length_cm": int, "foot_length_cm": int, "luff_perpendicular_cm": int,
        "sail_area_sqm": float,
        "sail_type_normalized": str, "boat_model_raw": str, "material_raw": str,
    }
    if field_name not in type_map and field_name not in cur:
        raise ValueError(f"Champ {field_name} non corrigeable")
    caster = type_map.get(field_name, str)
    try:
        new_typed: Any = caster(new_value) if new_value is not None else None
    except Exception as exc:
        raise ValueError(f"Valeur invalide pour {field_name}: {exc}") from exc
    # Update extraction
    cursor.execute(f"UPDATE sail_extractions SET {field_name} = %s, validation_status = 'corrected', validated_by = %s, validated_at = now() WHERE id = %s RETURNING id", (new_typed, corrected_by, extraction_id))
    # Historise
    cursor.execute(
        "INSERT INTO extraction_corrections (sail_extraction_id, field_name, old_value, new_value, old_normalized, new_normalized, correction_reason, corrected_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (extraction_id, field_name, old_str, str(new_typed) if new_typed is not None else None, _norm(old_str) if old_str else None, _norm(str(new_typed)) if new_typed else None, reason, corrected_by),
    )
    corr_id = cursor.fetchone()[0]
    # Propose pattern appris (jamais auto-écrasement) — simple heuristique : si old->new est un alias
    if field_name in ("boat_model_raw", "sail_type_normalized") and old_str and new_typed:
        try:
            cursor.execute(
                "INSERT INTO learned_extraction_patterns (pattern_type, field_target, pattern_raw, pattern_normalized, replacement_or_rule, confidence, occurrences, status, proposed_by_correction_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
                ("synonym" if field_name == "sail_type_normalized" else "unit_alias", field_name, old_str, _norm(old_str), str(new_typed), 0.6, 1, "pending_review", corr_id, corrected_by),
            )
        except Exception:
            pass  # pattern optionnel
    return {"extraction_id": extraction_id, "field": field_name, "old": old_str, "new": str(new_typed), "correction_id": corr_id}

def lister_extractions_a_valider(cursor: Any, limit: int = 50) -> list[dict]:
    cursor.execute("SELECT id, document_id, fiche_id, sail_type_normalized, boat_model_raw, boat_model_id, extraction_status, validation_status, confidence_overall, coherence_flags_json, created_at FROM sail_extractions WHERE validation_status = 'pending' ORDER BY created_at DESC LIMIT %s", (limit,))
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]
