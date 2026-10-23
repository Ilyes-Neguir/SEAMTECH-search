"""Lot 5 — Fonction B : évaluations confiance (025+028+030).

Médiane/MAD par couple (boat_model_id, sail_type_normalized) sur N échantillons.
- INSUFFISANT si N < CONFIANCE_MIN_ECHANTILLONS (5)
- HETEROGENE si > SEUIL_ALERTE (75%) des échantillons hors bande [médiane ± 1.5*MAD]
- OK sinon
Justification obligatoire si HETEROGENE (sinon 422).

Seuils configurables via env mais gelés par défaut :
CONFIANCE_MIN_ECHANTILLONS=5, SEUIL_ALERTE=75
"""
from __future__ import annotations

import statistics
from typing import Any

CONFIANCE_MIN_ECHANTILLONS = 5
SEUIL_ALERTE = 75.0  # pourcentage d'échantillons hors bande déclenchant HETEROGENE

CHAMPS_CONFIANCE = ["luff_length_cm", "leech_length_cm", "foot_length_cm", "luff_perpendicular_cm", "sail_area_sqm"]

def _median_mad(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    med = float(statistics.median(values))
    deviations = [abs(v - med) for v in values]
    mad = float(statistics.median(deviations)) if deviations else 0.0
    # Pour éviter MAD=0 (toutes valeurs identiques) -> petite tolérance
    if mad == 0:
        mad = max(0.5, med * 0.02) if med else 0.5
    return med, mad

def calculer_stats(echantillons: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcule stats_json {champ: {median, mad, n, p25, p75, hors_bande_pct}}."""
    stats: dict[str, Any] = {}
    for champ in CHAMPS_CONFIANCE:
        vals = [float(e[champ]) for e in echantillons if e.get(champ) is not None]
        n = len(vals)
        if n == 0:
            stats[champ] = {"median": None, "mad": None, "n": 0, "p25": None, "p75": None, "hors_bande_pct": None}
            continue
        med, mad = _median_mad(vals)
        # bande = median ± 1.5*MAD
        borne_inf = med - 1.5 * mad
        borne_sup = med + 1.5 * mad
        hors = sum(1 for v in vals if v < borne_inf or v > borne_sup)
        hors_pct = round(hors / n * 100, 2) if n else 0
        # p25/p75
        try:
            qs = statistics.quantiles(vals, n=4)
            p25, p75 = float(qs[0]), float(qs[2])
        except Exception:
            p25 = p75 = med
        stats[champ] = {"median": round(med, 2), "mad": round(mad, 2), "n": n, "p25": round(p25, 2), "p75": round(p75, 2), "hors_bande_pct": hors_pct, "bande_inf": round(borne_inf, 2), "bande_sup": round(borne_sup, 2)}
    return stats

def evaluer_confiance(stats: dict[str, Any], n_global: int) -> tuple[str, list[str]]:
    """Détermine INSUFFISANT / HETEROGENE / OK + drapeaux hétérogénéité."""
    if n_global < CONFIANCE_MIN_ECHANTILLONS:
        return "INSUFFISANT", ["N_INSUFFISANT"]
    flags: list[str] = []
    # Si un champ a > SEUIL_ALERTE % hors bande, on est hétérogène
    for champ, s in stats.items():
        if s.get("hors_bande_pct") is not None and s["hors_bande_pct"] > SEUIL_ALERTE:
            flags.append(f"HETEROGENE_{champ.upper()}")
    if flags:
        return "HETEROGENE", flags
    return "OK", []

def upsert_evaluation(cursor: Any, boat_model_id: int, sail_type_normalized: str, echantillons: list[dict[str, Any]], justification: str | None = None, computed_by: str | None = None) -> dict[str, Any]:
    """Calcule et insère/met à jour evaluations_confiance."""
    n = len(echantillons)
    stats = calculer_stats(echantillons)
    status, flags = evaluer_confiance(stats, n)
    if status == "HETEROGENE" and not justification:
        raise ValueError("Justification obligatoire si HETEROGENE (seuil 75% dépassé)")
    # upsert
    cursor.execute(
        """
        INSERT INTO evaluations_confiance (boat_model_id, sail_type_normalized, stats_json, sample_size, heterogeneity_flags_json, confiance_status, alerte_seuil, justification, computed_by)
        VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s, %s)
        ON CONFLICT (boat_model_id, sail_type_normalized) DO UPDATE SET
            stats_json = EXCLUDED.stats_json,
            sample_size = EXCLUDED.sample_size,
            heterogeneity_flags_json = EXCLUDED.heterogeneity_flags_json,
            confiance_status = EXCLUDED.confiance_status,
            justification = EXCLUDED.justification,
            computed_by = EXCLUDED.computed_by,
            last_computed_at = now()
        RETURNING id, confiance_status, sample_size
        """,
        (
            boat_model_id, sail_type_normalized, __import__("json").dumps(stats), n, __import__("json").dumps(flags), status, SEUIL_ALERTE, justification, computed_by,
        ),
    )
    row = cursor.fetchone()
    cols = [d[0] for d in cursor.description] if cursor.description else []
    res = dict(zip(cols, row)) if row else {}
    res.update({"stats": stats, "flags": flags, "status": status, "n": n})
    return res

def fetch_evaluation(cursor: Any, boat_model_id: int, sail_type_normalized: str) -> dict[str, Any] | None:
    cursor.execute("SELECT id, boat_model_id, sail_type_normalized, stats_json, sample_size, heterogeneity_flags_json, confiance_status, justification, last_computed_at FROM evaluations_confiance WHERE boat_model_id = %s AND sail_type_normalized = %s", (boat_model_id, sail_type_normalized))
    row = cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))
