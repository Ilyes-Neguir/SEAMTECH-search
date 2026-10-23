"""Lot 3a — Extraction structurée des voiles (028).

Pipeline regex + cohérence géométrique, avec provenance par champ.
Chaque valeur extraite est tracée : page, bbox (si disponible), confiance, méthode.
Aucune SQL concat : tout paramétré côté appelant.
Niveau protégé par secret : hors dépôt, non journalisé.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

# Normalisation des clés : réutilise la fonction Lot2 si dispo, sinon fallback local.
try:
    from .schema_metier import SEAMTECH_TEXTE_RECHERCHE_SQL  # type: ignore
except Exception:
    SEAMTECH_TEXTE_RECHERCHE_SQL = None

try:
    from .recherche import normaliser_cle_recherche as _norm_recherche
except Exception:
    _norm_recherche = None  # type: ignore

def _norm(s: str | None) -> str:
    if not s:
        return ""
    if _norm_recherche:
        try:
            return _norm_recherche(s)
        except Exception:
            pass
    # fallback minimal : lower, strip
    return s.lower().strip()


# ---------------------------------------------------------------------------
# Regex extraction (Lot 3a)
# ---------------------------------------------------------------------------
SAIL_TYPE_PATTERNS: dict[str, re.Pattern] = {
    "gv": re.compile(r"\b(GV|grand[\s-]*voile)\b", re.I),
    "genois": re.compile(r"\b(GE|GEN|génois|genois)\b", re.I),
    "spi": re.compile(r"\b(SPI|spinnaker)\b", re.I),
    "trinquette": re.compile(r"\b(TRQ|trinquette)\b", re.I),
    "gennaker": re.compile(r"\b(gennaker|genn)\b", re.I),
    "code0": re.compile(r"\b(C0|code\s*0)\b", re.I),
}

# Cotes : formats "6.60 m", "660 cm", "Guindant: 12,5 m"
DIM_PATTERN = re.compile(
    r"(?P<label>guindant|guind|bordure|chute|LP|luff|leech|foot|longueur|largeur)\s*[:=]?\s*(?P<val>\d+[.,]\d+|\d+)\s*(?P<unit>m|cm|mm)\b",
    re.I,
)
# Fallback générique : toute dimension en cm/m
GENERIC_DIM = re.compile(r"(?P<val>\d+[.,]\d+|\d+)\s*(?P<unit>m|cm|mm)\b", re.I)

MATERIAL_PATTERNS: dict[str, re.Pattern] = {
    "dacron": re.compile(r"\bdacron\b", re.I),
    "monofilm": re.compile(r"\bmonofilm\b", re.I),
    "aramide": re.compile(r"\baramide\b", re.I),
}

BOAT_MODEL_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z0-9-]+)+)\b")

@dataclass
class ChampExtrait:
    value: Any
    page: int | None = None
    bbox: list[float] | None = None
    confidence: float = 0.0
    method: str = "regex_rules"
    raw: str | None = None

@dataclass
class ExtractionResult:
    sail_type_raw: str | None = None
    sail_type_normalized: str | None = None
    boat_model_raw: str | None = None
    boat_model_normalized: str | None = None
    material_raw: str | None = None
    luff_length_cm: int | None = None
    leech_length_cm: int | None = None
    foot_length_cm: int | None = None
    luff_perpendicular_cm: int | None = None
    sail_area_sqm: float | None = None
    provenance: dict[str, dict] | None = None
    confidence_overall: float = 0.0
    coherence_score: float = 1.0
    coherence_flags: list[str] | None = None
    raw_text_excerpt: str | None = None
    extraction_method: str = "regex_rules"
    extraction_status: str = "completed"

def _to_cm(val_str: str, unit: str) -> int:
    v = float(val_str.replace(",", "."))
    unit = unit.lower()
    if unit == "m":
        return int(round(v * 100))
    if unit == "cm":
        return int(round(v))
    if unit == "mm":
        return int(round(v / 10))
    return int(round(v))

def _extract_dimensions(texte: str) -> tuple[dict[str, int], dict[str, dict]]:
    dims: dict[str, int] = {}
    prov: dict[str, dict] = {}
    # Cherche les dimensions labellisées
    for m in DIM_PATTERN.finditer(texte):
        label = m.group("label").lower()
        val = m.group("val")
        unit = m.group("unit")
        cm = _to_cm(val, unit)
        key = None
        if label in ("guindant", "guind", "luff"):
            key = "luff_length_cm"
        elif label in ("chute", "leech"):
            key = "leech_length_cm"
        elif label in ("bordure", "foot"):
            key = "foot_length_cm"
        elif label in ("lp", "luff_perpendicular"):
            key = "luff_perpendicular_cm"
        if key and key not in dims:
            dims[key] = cm
            prov[key] = {"value": cm, "raw": m.group(0), "confidence": 0.85, "method": "regex_rules", "page": 1}
    # Aires : "surface 25,5 m2" / "12 m²"
    area_pat = re.compile(r"(?:surface|aire|area)\s*[:=]?\s*(\d+[.,]\d+|\d+)\s*m2|m²", re.I)
    for m in area_pat.finditer(texte):
        try:
            v = float(m.group(1).replace(",", "."))
            dims["sail_area_sqm"] = v  # type: ignore
            prov["sail_area_sqm"] = {"value": v, "raw": m.group(0), "confidence": 0.8, "method": "regex_rules", "page": 1}
        except Exception:
            pass
    return dims, prov

def extraire_sail(texte: str, page: int = 1) -> ExtractionResult:
    """Extrait les champs SAIL depuis un texte OCR / natif."""
    res = ExtractionResult(provenance={}, coherence_flags=[], raw_text_excerpt=texte[:500])
    prov: dict[str, dict] = {}

    # Type de voile
    sail_type = None
    sail_norm = None
    for norm, pat in SAIL_TYPE_PATTERNS.items():
        if pat.search(texte):
            sail_type = pat.search(texte).group(0)  # type: ignore
            sail_norm = norm
            prov["sail_type"] = {"value": sail_type, "normalized": sail_norm, "confidence": 0.9, "method": "regex_rules", "page": page, "raw": sail_type}
            break
    res.sail_type_raw = sail_type
    res.sail_type_normalized = sail_norm

    # Matériau
    for mat, pat in MATERIAL_PATTERNS.items():
        if pat.search(texte):
            res.material_raw = pat.search(texte).group(0)  # type: ignore
            prov["material"] = {"value": res.material_raw, "confidence": 0.85, "method": "regex_rules", "page": page}
            break

    # Modèle bateau (heuristique : première occurence capitale)
    m = BOAT_MODEL_PATTERN.search(texte)
    if m:
        res.boat_model_raw = m.group(0).strip()
        res.boat_model_normalized = _norm(res.boat_model_raw)
        prov["boat_model"] = {"value": res.boat_model_raw, "normalized": res.boat_model_normalized, "confidence": 0.6, "method": "regex_rules", "page": page}

    # Dimensions
    dims, dim_prov = _extract_dimensions(texte)
    for k, v in dims.items():
        setattr(res, k, v)
    prov.update(dim_prov)

    # Surface si non trouvée, on peut l'estimer via géométrie simple (triangle) si on a luff+foot
    if res.sail_area_sqm is None and res.luff_length_cm and res.foot_length_cm:
        # approximation : 0.5 * luff * foot (en m2)
        est = (res.luff_length_cm / 100) * (res.foot_length_cm / 100) * 0.5
        res.sail_area_sqm = round(est, 2)
        prov["sail_area_sqm"] = {"value": res.sail_area_sqm, "confidence": 0.5, "method": "geometry_estimate", "page": page}

    res.provenance = prov

    # Confiance globale : moyenne des confiances champs renseignés
    confs = [v.get("confidence", 0) for v in prov.values() if isinstance(v, dict) and "confidence" in v]
    res.confidence_overall = round(sum(confs) / len(confs), 3) if confs else 0.0

    # Cohérence géométrique
    flags, score = verifier_coherence(res)
    res.coherence_flags = flags
    res.coherence_score = score
    if flags:
        res.extraction_status = "needs_review"
    else:
        res.extraction_status = "completed"

    return res

def verifier_coherence(res: ExtractionResult) -> tuple[list[str], float]:
    """Règles géométriques simples : guindant vs chute, LP vs bordure, etc."""
    flags: list[str] = []
    score = 1.0
    # Guindant (luff) doit être > chute (leech) en général pour GV ? Non, mais on vérifie ordres
    if res.luff_length_cm and res.leech_length_cm:
        if res.luff_length_cm < 50 or res.leech_length_cm < 50:
            flags.append("GUINDANT_TROP_COURT")
            score -= 0.3
        if abs(res.luff_length_cm - res.leech_length_cm) > 500:  # >5m écart suspect
            flags.append("INCOHERENT_LUFF_LEECH")
            score -= 0.2
        if res.luff_length_cm < res.foot_length_cm or 0:
            # guindant ne doit pas être plus court que bordure pour une GV
            pass
    if res.luff_perpendicular_cm and res.foot_length_cm:
        if res.luff_perpendicular_cm > res.foot_length_cm:
            flags.append("LP_SUPERIEUR_BORDURE")
            score -= 0.2
    if res.sail_area_sqm and res.luff_length_cm and res.foot_length_cm:
        # aire théorique max : luff*foot/2 *1.2 (tolérance)
        aire_max = (res.luff_length_cm / 100) * (res.foot_length_cm / 100) * 0.6
        if res.sail_area_sqm > aire_max * 1.5:
            flags.append("AIRE_SURDIMENSIONNEE")
            score -= 0.25
        if res.sail_area_sqm < 1:
            flags.append("AIRE_TROP_PETITE")
            score -= 0.15
    score = max(0.0, min(1.0, round(score, 3)))
    return flags, score

# ---------------------------------------------------------------------------
# Persistence helpers (paramétré, jamais f-string SELECT)
# ---------------------------------------------------------------------------
def inserer_extraction(cursor: Any, document_id: int, fiche_id: int | None, resultat: ExtractionResult, created_by: str | None = None) -> int:
    """Insère une extraction et retourne son id. SQL paramétré."""
    cursor.execute(
        """
        INSERT INTO sail_extractions
            (document_id, fiche_id, extraction_status, extraction_method,
             sail_type_raw, sail_type_normalized, boat_model_raw, boat_model_id, material_raw,
             luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm,
             field_provenance_json, confidence_overall, coherence_score, coherence_flags_json, raw_text_excerpt,
             created_by)
        VALUES
            (%s, %s, %s, %s,
             %s, %s, %s, %s, %s,
             %s, %s, %s, %s, %s,
             %s::jsonb, %s, %s, %s::jsonb, %s,
             %s)
        RETURNING id
        """,
        (
            document_id, fiche_id, resultat.extraction_status, resultat.extraction_method,
            resultat.sail_type_raw, resultat.sail_type_normalized, resultat.boat_model_raw, None, resultat.material_raw,
            resultat.luff_length_cm, resultat.leech_length_cm, resultat.foot_length_cm, resultat.luff_perpendicular_cm, resultat.sail_area_sqm,
            __import__("json").dumps(resultat.provenance or {}), resultat.confidence_overall, resultat.coherence_score, __import__("json").dumps(resultat.coherence_flags or []), resultat.raw_text_excerpt,
            created_by,
        ),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def lister_extractions_par_document(cursor: Any, document_id: int) -> list[dict]:
    cursor.execute("SELECT id, document_id, fiche_id, extraction_status, sail_type_normalized, boat_model_raw, luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm, confidence_overall, coherence_score, coherence_flags_json, field_provenance_json, created_at FROM sail_extractions WHERE document_id = %s ORDER BY created_at DESC", (document_id,))
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]
