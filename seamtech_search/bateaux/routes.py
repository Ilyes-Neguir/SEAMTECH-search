"""Routes admin pour le référentiel des modèles de bateaux — Lot 2."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query

from seamtech_search.bateaux.modeles import (
    get_or_create_boat_model,
    list_boat_aliases,
    list_boat_models,
    merge_boat_models,
    reject_alias,
    revert_merge,
    validate_alias,
)
from seamtech_search.bateaux.normalisation import normaliser_cle_bateau
from seamtech_search.comptes.comptes import resoudre_attribution

LOGGER = logging.getLogger("seamtech.bateaux.routes")


def _exiger_postgres(index: Any) -> None:
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail="Référentiel des modèles disponible sur PostgreSQL uniquement (migration 026).",
        )


def _exiger_admin(
    index: Any,
    config: Any,
    verifier_auth: Any,
    token: str | None,
    entete_session: str | None,
    entete_jeton: str | None,
) -> dict[str, Any]:
    """Vérifie le token de service ET le rôle administrateur via session."""
    verifier_auth(config, token)
    _exiger_postgres(index)
    # Résout l'attribution via la même fonction que les fiches
    # On exige une session valide avec rôle administrateur
    from seamtech_search.comptes.routes import _exiger_administrateur  # local import pour éviter cycle

    return _exiger_administrateur(index, entete_session, entete_jeton)


def enregistrer_routes_bateaux(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:  # noqa: ANN401
    # -----------------------------------------------------------------------
    # Modèles
    # -----------------------------------------------------------------------

    @app.get("/admin/boat-models")
    def route_list_models(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        modeles = list_boat_models(index, limit=limit, offset=offset)
        return {"count": len(modeles), "limit": limit, "offset": offset, "results": modeles}

    @app.get("/admin/boat-models/{model_id}")
    def route_get_model(
        model_id: int,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT id, canonical_name, normalized_key, builder, notes, created_at, updated_at FROM boat_models WHERE id = %s",
                    (model_id,),
                )
                r = cursor.fetchone()
                if r is None:
                    raise HTTPException(status_code=404, detail=f"Modèle {model_id} inconnu")
                return {
                    "id": int(r[0]),
                    "canonical_name": str(r[1]),
                    "normalized_key": str(r[2]),
                    "builder": r[3],
                    "notes": r[4],
                    "created_at": r[5].isoformat() if r[5] else None,
                    "updated_at": r[6].isoformat() if r[6] else None,
                }

    @app.post("/admin/boat-models", status_code=201)
    def route_create_model(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        admin = _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        # L'attribution passe par resoudre_attribution comme pour les fiches
        identifiant, _role = resoudre_attribution(config, token, entete_utilisateur, entete_role, corps.get("created_by"))
        # Fallback : admin.identifiant
        created_by = identifiant or admin.get("identifiant") or corps.get("created_by")
        canonical = str(corps.get("canonical_name") or "").strip()
        if not canonical:
            raise HTTPException(status_code=422, detail="canonical_name obligatoire")
        builder = corps.get("builder")
        notes = corps.get("notes")
        # Si alias_raw fourni, on crée aussi l'alias manuellement
        try:
            modele = get_or_create_boat_model(index, canonical, builder=builder, notes=notes, created_by=created_by)
            # Si alias_raw différent de canonical_name, crée alias admin_manual
            alias_raw = corps.get("alias_raw")
            if alias_raw and alias_raw.strip() != canonical:
                alias_raw = alias_raw.strip()
                alias_norm = normaliser_cle_bateau(alias_raw)
                with index.connect() as connexion:
                    with connexion.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO boat_model_aliases (boat_model_id, alias_raw, alias_normalized, status, source, created_by)
                            VALUES (%s, %s, %s, 'active', 'admin_manual', %s)
                            ON CONFLICT (alias_normalized) DO NOTHING
                            RETURNING id
                            """,
                            (modele["id"], alias_raw, alias_norm, created_by),
                        )
            return modele
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # -----------------------------------------------------------------------
    # Aliases
    # -----------------------------------------------------------------------

    @app.get("/admin/boat-model-aliases")
    def route_list_aliases(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        status: str | None = Query(None, max_length=20),
        boat_model_id: int | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        aliases = list_boat_aliases(index, status=status, boat_model_id=boat_model_id, limit=limit, offset=offset)
        return {"count": len(aliases), "limit": limit, "offset": offset, "results": aliases}

    @app.post("/admin/boat-model-aliases/{alias_id}/validate")
    def route_validate_alias(
        alias_id: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        admin = _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        identifiant, _role = resoudre_attribution(
            config, token, entete_utilisateur, entete_role, (corps or {}).get("actor")
        )
        actor = identifiant or admin.get("identifiant")
        try:
            return validate_alias(index, alias_id, actor=actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/admin/boat-model-aliases/{alias_id}/reject")
    def route_reject_alias(
        alias_id: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        admin = _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        identifiant, _role = resoudre_attribution(
            config, token, entete_utilisateur, entete_role, (corps or {}).get("actor")
        )
        actor = identifiant or admin.get("identifiant")
        try:
            return reject_alias(index, alias_id, actor=actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # -----------------------------------------------------------------------
    # Fusion / annulation
    # -----------------------------------------------------------------------

    @app.post("/admin/boat-models/merge")
    def route_merge_models(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        admin = _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        identifiant, _role = resoudre_attribution(
            config, token, entete_utilisateur, entete_role, corps.get("actor_username")
        )
        actor = identifiant or admin.get("identifiant") or corps.get("actor_username")
        if not actor:
            raise HTTPException(status_code=422, detail="actor_username obligatoire")
        source_id = corps.get("source_model_id")
        target_id = corps.get("target_model_id")
        if not source_id or not target_id:
            raise HTTPException(status_code=422, detail="source_model_id et target_model_id obligatoires")
        try:
            return merge_boat_models(index, source_id=int(source_id), target_id=int(target_id), actor_username=str(actor))
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/admin/boat-models/merge/{merge_id}/revert")
    def route_revert_merge(
        merge_id: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        admin = _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        identifiant, _role = resoudre_attribution(
            config, token, entete_utilisateur, entete_role, (corps or {}).get("actor_username")
        )
        actor = identifiant or admin.get("identifiant")
        try:
            return revert_merge(index, merge_id=merge_id, actor_username=actor)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/admin/boat-models/merge/history")
    def route_merge_history(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source_model_id, target_model_id, snapshot_json, actor_username, created_at, reverted_at
                    FROM boat_model_merge_history
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cursor.fetchall()
                return {
                    "count": len(rows),
                    "results": [
                        {
                            "id": int(r[0]),
                            "source_model_id": int(r[1]),
                            "target_model_id": int(r[2]),
                            "snapshot_json": r[3],
                            "actor_username": str(r[4]),
                            "created_at": r[5].isoformat() if r[5] else None,
                            "reverted_at": r[6].isoformat() if r[6] else None,
                        }
                        for r in rows
                    ],
                }

    # -----------------------------------------------------------------------
    # Recherche suggestions unifiée (Lot 2) — /api/search/suggestions?q=...
    # -----------------------------------------------------------------------

    @app.get("/api/search/suggestions")
    def route_search_suggestions(
        q: str = Query("", max_length=500),
        limit: int = Query(10, ge=1, le=25),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        from seamtech_search.bateaux.normalisation import normaliser_cle_bateau

        verifier_auth(config, token)
        _exiger_postgres(index)
        if not q or not q.strip():
            return {"query": q, "suggestions": []}
        prefixe = q.strip()
        prefixe_norm = normaliser_cle_bateau(prefixe)
        motif = prefixe_norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

        suggestions: list[dict[str, Any]] = []
        vues: set[str] = set()

        def _ajouter(nature: str, valeur: str, score: float = 1.0, correction: str | None = None) -> None:
            if not valeur or not valeur.strip():
                return
            cle = valeur.strip().lower()
            if cle in vues:
                return
            vues.add(cle)
            entry: dict[str, Any] = {"nature": nature, "valeur": valeur.strip(), "score": round(score, 3)}
            if correction:
                entry["correction_de"] = correction
            suggestions.append(entry)

        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                # 1) Modèles de bateaux (préfixe exact sur clé normalisée)
                try:
                    cursor.execute(
                        "SELECT canonical_name, similarity(normalized_key, %s) FROM boat_models WHERE normalized_key LIKE %s ESCAPE '\\' ORDER BY canonical_name LIMIT %s",
                        (prefixe_norm, motif, limit),
                    )
                    for canonical, _sim in cursor.fetchall():
                        _ajouter("bateau_modele", str(canonical), score=1.0)
                        if len(suggestions) >= limit:
                            break
                except Exception:
                    pass

                # 2) Aliases bateau (préfixe)
                if len(suggestions) < limit:
                    try:
                        cursor.execute(
                            "SELECT alias_raw, similarity(alias_normalized, %s) FROM boat_model_aliases WHERE status='active' AND alias_normalized LIKE %s ESCAPE '\\' ORDER BY alias_raw LIMIT %s",
                            (prefixe_norm, motif, limit),
                        )
                        for alias_raw, _sim in cursor.fetchall():
                            _ajouter("bateau_alias", str(alias_raw), score=0.9)
                            if len(suggestions) >= limit:
                                break
                    except Exception:
                        pass

                # 3) Synonymes voilerie (search_synonyms)
                if len(suggestions) < limit:
                    try:
                        cursor.execute(
                            "SELECT canonical_term, normalized_alias FROM search_synonyms WHERE active = true AND (normalized_alias LIKE %s ESCAPE '\\' OR seamtech_texte_recherche(canonical_term) LIKE %s ESCAPE '\\') LIMIT %s",
                            (motif, motif, limit),
                        )
                        for canonical, alias in cursor.fetchall():
                            _ajouter("terme_voilerie", str(canonical), score=0.9)
                            _ajouter("terme_abrege", str(alias), score=0.85)
                            if len(suggestions) >= limit:
                                break
                    except Exception:
                        pass

                # 4) Corrections orthographiques via trigrammes (boat_models + search_synonyms)
                if len(suggestions) < limit:
                    try:
                        cursor.execute(
                            """
                            SELECT canonical_name, similarity(normalized_key, %s) AS sim
                            FROM boat_models
                            WHERE similarity(normalized_key, %s) > 0.3
                            ORDER BY sim DESC LIMIT %s
                            """,
                            (prefixe_norm, prefixe_norm, 3),
                        )
                        for canonical, sim in cursor.fetchall():
                            if str(canonical).lower() != prefixe.lower() and float(sim) > 0.3:
                                _ajouter("correction", str(canonical), score=float(sim) * 0.5, correction=prefixe)
                                if len(suggestions) >= limit:
                                    break
                    except Exception:
                        pass
                    # Corrections pour termes voilerie
                    try:
                        cursor.execute(
                            """
                            SELECT canonical_term, similarity(seamtech_texte_recherche(canonical_term), seamtech_texte_recherche(%s)) AS sim
                            FROM search_synonyms
                            WHERE active = true AND similarity(seamtech_texte_recherche(canonical_term), seamtech_texte_recherche(%s)) > 0.3
                            ORDER BY sim DESC LIMIT 3
                            """,
                            (prefixe, prefixe),
                        )
                        for canonical, sim in cursor.fetchall():
                            if str(canonical).lower() != prefixe.lower() and float(sim) > 0.3:
                                _ajouter("correction", str(canonical), score=float(sim) * 0.5, correction=prefixe)
                                if len(suggestions) >= limit:
                                    break
                    except Exception:
                        pass

                # 5) Fallback : suggestions existantes de la recherche fiches (bateau, type_voile, etc.)
                if len(suggestions) < limit:
                    try:
                        # Réutilise la logique de recherche.suggerer mais directement via SQL
                        cursor.execute(
                            "SELECT nom FROM bateau WHERE seamtech_texte_recherche(nom) LIKE %s ESCAPE '\\' LIMIT %s",
                            (motif, 3),
                        )
                        for (nom,) in cursor.fetchall():
                            _ajouter("bateau", str(nom), score=0.8)
                            if len(suggestions) >= limit:
                                break
                    except Exception:
                        pass

        # Tri par score décroissant, puis alphabétique
        suggestions_sorted = sorted(suggestions, key=lambda x: (-x["score"], x["valeur"]))[:limit]
        return {"query": q, "prefixe_normalise": prefixe_norm, "suggestions": suggestions_sorted}

    # -----------------------------------------------------------------------
    # Réindexation idempotente + tableau comparatif AVANT/APRÈS
    # -----------------------------------------------------------------------

    @app.post("/admin/boat-models/reindex")
    def route_reindex(
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        _exiger_postgres(index)
        from seamtech_search.bateaux.recherche_etendue import reindexer_tous_modeles

        dry_run = bool((corps or {}).get("dry_run", False))
        try:
            rapport = reindexer_tous_modeles(index, dry_run=dry_run)
            return {"dry_run": dry_run, **rapport}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Réindexation échouée : {exc}") from exc

    @app.get("/admin/boat-models/comparatif")
    def route_comparatif(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        _exiger_admin(index, config, verifier_auth, token, entete_session, entete_jeton)
        _exiger_postgres(index)
        from seamtech_search.bateaux.recherche_etendue import generer_tableau_comparatif

        try:
            return generer_tableau_comparatif(index)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Comparatif échoué : {exc}") from exc
