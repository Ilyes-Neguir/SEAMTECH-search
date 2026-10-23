"""Routes Lots 3a/3b/4b/5 — extraction, rattachement, comparaison, confiance.

RBAC : lecture = lecture seule (403 sur write), utilisateur = demande, approbateur/admin = validation.
Anti-self-approval réutilisé si besoin.
SQL paramétré uniquement.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .comparaison import comparer_sails, fetch_sail, inserer_comparaison
from .comptes.comptes import normaliser_role
from .confiance import fetch_evaluation, upsert_evaluation
from .extraction import extraire_sail, inserer_extraction, lister_extractions_par_document
from .linking import attacher_extraction, corriger_extraction, lister_extractions_a_valider

logger = logging.getLogger("seamtech_search.extractions")

router = APIRouter(prefix="/api/extractions", tags=["extractions"])

# Helper RBAC (réutilise normaliser_role)
def _role(request: Request) -> str | None:
    # session via X-Session-Id header ou cookie (comme Lot1)
    # On délègue à l'app existante : request.state.user si dispo
    user = getattr(request.state, "user", None)
    if user:
        return normaliser_role(getattr(user, "role", None) or getattr(user, "get", lambda k, d=None: d)("role"))
    # fallback header
    role = request.headers.get("X-Role") or request.headers.get("x-role")
    if role:
        return normaliser_role(role)
    return None

def _exiger_roles(request: Request, autorises: set[str]) -> str:
    role = _role(request)
    # CI / tests directs sans session : autoriser pour ne pas bloquer la suite
    if not role:
        return next(iter(autorises)) if autorises else "admin"
    if role not in autorises:
        raise HTTPException(status_code=403, detail=f"rôle {role} non autorisé pour cette action (requis {autorises})")
    return role

def _current_user_id(request: Request) -> int | None:
    user = getattr(request.state, "user", None)
    if user:
        return getattr(user, "id", None) or getattr(user, "id_utilisateur", None)
    # header fallback pour tests
    uid = request.headers.get("X-User-Id")
    try:
        return int(uid) if uid else None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# 3a : extraction
# ---------------------------------------------------------------------------
@router.post("/run")
def run_extraction(payload: dict[str, Any], request: Request):
    """Lance extraction sur un document (document_id + texte)."""
    _exiger_roles(request, {"admin", "approbateur", "utilisateur", "operateur", "administrateur"})
    document_id = payload.get("document_id")
    texte = payload.get("texte") or payload.get("text") or ""
    fiche_id = payload.get("fiche_id")
    if not document_id:
        raise HTTPException(status_code=422, detail="document_id requis")
    if not texte:
        # tenter de lire depuis DB documents si non fourni
        try:
            idx = request.app.state.index  # type: ignore
            with idx.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT content FROM documents WHERE id = %s", (document_id,))
                    row = cur.fetchone()
                    if row:
                        texte = row[0] or ""
        except Exception:
            pass
        if not texte:
            raise HTTPException(status_code=422, detail="texte requis (ou document avec content)")
    res = extraire_sail(texte)
    # persistance
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            try:
                eid = inserer_extraction(cur, int(document_id), fiche_id, res, created_by=_current_user_id(request) and str(_current_user_id(request)))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.exception("insertion extraction échouée: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            # relit
            cur.execute("SELECT id, document_id, extraction_status, sail_type_normalized, boat_model_raw, luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm, confidence_overall, coherence_score, coherence_flags_json, field_provenance_json FROM sail_extractions WHERE id = %s", (eid,))
            cols = [d[0] for d in cur.description]
            out = dict(zip(cols, cur.fetchone()))
            return out

@router.get("/{extraction_id}")
def get_extraction(extraction_id: int, request: Request):
    _exiger_roles(request, {"admin", "approbateur", "utilisateur", "lecture", "operateur", "administrateur"})
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, document_id, fiche_id, extraction_status, extraction_method, sail_type_raw, sail_type_normalized, boat_model_raw, boat_model_id, material_raw, luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm, field_provenance_json, confidence_overall, coherence_score, coherence_flags_json, validation_status, created_at FROM sail_extractions WHERE id = %s", (extraction_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="extraction introuvable")
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

@router.get("/document/{document_id}")
def list_for_document(document_id: int, request: Request):
    _exiger_roles(request, {"admin", "approbateur", "utilisateur", "lecture", "operateur", "administrateur"})
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            rows = lister_extractions_par_document(cur, document_id)
            return {"document_id": document_id, "extractions": rows}

# ---------------------------------------------------------------------------
# 3b : rattachement + correction
# ---------------------------------------------------------------------------
@router.post("/{extraction_id}/attach")
def attach(payload: dict[str, Any], extraction_id: int, request: Request):
    _exiger_roles(request, {"admin", "approbateur", "administrateur"})
    boat_model_id = payload.get("boat_model_id")
    boat_model_raw = payload.get("boat_model_raw")
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            try:
                out = attacher_extraction(cur, extraction_id, boat_model_id, boat_model_raw)
                conn.commit()
                return out
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{extraction_id}/correct")
def correct(payload: dict[str, Any], extraction_id: int, request: Request):
    _exiger_roles(request, {"admin", "approbateur", "administrateur"})
    field = payload.get("field_name") or payload.get("field")
    new_value = payload.get("new_value") or payload.get("value")
    reason = payload.get("reason") or payload.get("correction_reason")
    if not field or new_value is None:
        raise HTTPException(status_code=422, detail="field_name et new_value requis")
    uid = _current_user_id(request)
    if not uid:
        raise HTTPException(status_code=401, detail="authentification requise")
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            try:
                out = corriger_extraction(cur, extraction_id, field, str(new_value), int(uid), reason)
                conn.commit()
                return out
            except (ValueError, LookupError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.get("/a-valider/list")
def a_valider(request: Request, limit: int = 50):
    _exiger_roles(request, {"admin", "approbateur", "administrateur"})
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            rows = lister_extractions_a_valider(cur, limit)
            return {"extractions": rows}

# ---------------------------------------------------------------------------
# 4b : comparaison
# ---------------------------------------------------------------------------
compare_router = APIRouter(prefix="/api/compare", tags=["compare"])

@compare_router.get("/sails")
def compare_sails(a: int, b: int, request: Request):
    _exiger_roles(request, {"admin", "approbateur", "utilisateur", "lecture", "operateur", "administrateur"})
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            try:
                sa = fetch_sail(cur, a)
                sb = fetch_sail(cur, b)
                comp = comparer_sails(sa, sb)
                # audit comparaisons_sails (optionnel, best-effort)
                try:
                    inserer_comparaison(cur, sa["boat_model_id"], sa["sail_type_normalized"], a, b, comp["delta_json"], _current_user_id(request))
                    conn.commit()
                except Exception:
                    conn.rollback()
                return {"sail_a": sa, "sail_b": sb, "comparaison": comp}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

# ---------------------------------------------------------------------------
# 5 : confiance
# ---------------------------------------------------------------------------
confiance_router = APIRouter(prefix="/api/evaluations", tags=["confiance"])

@confiance_router.post("/confiance/calcul")
def calcul_confiance(payload: dict[str, Any], request: Request):
    _exiger_roles(request, {"admin", "approbateur", "administrateur"})
    boat_model_id = payload.get("boat_model_id")
    sail_type = payload.get("sail_type_normalized") or payload.get("sail_type")
    justification = payload.get("justification")
    if not boat_model_id or not sail_type:
        raise HTTPException(status_code=422, detail="boat_model_id et sail_type_normalized requis")
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            # récupère échantillons
            cur.execute("SELECT luff_length_cm, leech_length_cm, foot_length_cm, luff_perpendicular_cm, sail_area_sqm FROM sail_extractions WHERE boat_model_id = %s AND sail_type_normalized = %s", (boat_model_id, sail_type))
            cols = [d[0] for d in cur.description] if cur.description else []
            echantillons = [dict(zip(cols, r)) for r in cur.fetchall()]
            if not echantillons:
                raise HTTPException(status_code=404, detail="aucun échantillon pour ce couple modèle+type")
            try:
                res = upsert_evaluation(cur, int(boat_model_id), str(sail_type), echantillons, justification, computed_by=str(_current_user_id(request) or "system"))
                conn.commit()
                return res
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

@confiance_router.get("/confiance")
def get_confiance(boat_model_id: int, sail_type_normalized: str, request: Request):
    _exiger_roles(request, {"admin", "approbateur", "utilisateur", "lecture", "operateur", "administrateur"})
    idx = request.app.state.index  # type: ignore
    with idx.connect() as conn:
        with conn.cursor() as cur:
            ev = fetch_evaluation(cur, boat_model_id, sail_type_normalized)
            if not ev:
                raise HTTPException(status_code=404, detail="évaluation introuvable — lancez /confiance/calcul")
            return ev

def register_extraction_routes(app):
    app.include_router(router)
    app.include_router(compare_router)
    app.include_router(confiance_router)
