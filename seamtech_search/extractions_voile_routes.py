"""API des extractions de voiles — Lot 3b (écran approbateur côte à côte).

Lecture ouverte à tout compte nominatif ; toute mutation (résolution,
liaison au modèle canonique, correction de valeur) est réservée à
l'``approbateur`` — le seul rôle qui décide et corrige (Lot 1). Le service
métier vit dans `seamtech_search.liaison_voile` : ce module ne fait que
l'authentification, la validation HTTP et la traduction d'erreurs.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query

from .audit import record_audit_event
from .comptes.comptes import ROLE_APPROBATEUR
from .liaison_voile import (
    CorrectionRefusee,
    DemandeInvalide,
    ExtractionIntrouvable,
    LiaisonRefusee,
    LiaisonVoileError,
    ModeleBateauIntrouvable,
    corriger_extraction,
    detail_extraction,
    lier_extraction,
    lister_extractions,
    resoudre_extraction_curseur,
    resoudre_extractions,
)
from .referentiel_bateaux import AliasBateauEnConflit
from .securite_requetes import ActeurRequete, exiger_acteur_nominatif

LOGGER = logging.getLogger("seamtech_search.extractions_voile_routes")


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Extractions de voiles disponibles sur PostgreSQL uniquement (décision "
                "de couche §17.1) : les tables sail_extractions n'existent pas côté SQLite."
            ),
        )


def _traduire_erreur(erreur: Exception) -> HTTPException:
    if isinstance(erreur, (ExtractionIntrouvable, ModeleBateauIntrouvable)):
        return HTTPException(status_code=404, detail=str(erreur))
    if isinstance(erreur, AliasBateauEnConflit):
        return HTTPException(status_code=409, detail=str(erreur))
    if isinstance(erreur, (CorrectionRefusee, LiaisonRefusee, DemandeInvalide)):
        return HTTPException(status_code=422, detail=str(erreur))
    if isinstance(erreur, LiaisonVoileError):
        return HTTPException(status_code=503, detail=str(erreur))
    raise erreur


def _auditer(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    action: str,
    id_extraction: int | None,
    details: dict[str, Any],
) -> None:
    record_audit_event(
        index,
        action=action,
        actor=acteur.libelle_audit,
        resource=str(id_extraction) if id_extraction is not None else "extractions-voile",
        status="200",
        details=details,
        actor_type=acteur.type_acteur,
        id_utilisateur=acteur.id_utilisateur,
        identifiant=acteur.identifiant,
        role=acteur.role,
        id_session=acteur.id_session,
        resource_type="sail_extraction",
        resource_id=str(id_extraction) if id_extraction is not None else "",
        code_statut=200,
    )


def enregistrer_routes_extractions_voile(
    app: Any,  # noqa: ANN401
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    verifier_auth: Any,  # noqa: ANN401
) -> None:
    """Monte `/extractions-voile` sur l'application FastAPI."""

    def _acteur(
        token: str | None,
        utilisateur: str | None,
        role: str | None,
        session: str | None,
        jeton: str | None,
        roles: tuple[str, ...] | None = None,
    ) -> ActeurRequete:
        return exiger_acteur_nominatif(
            index,
            config,
            roles,
            token=token,
            entete_utilisateur=utilisateur,
            entete_role=role,
            entete_session=session,
            entete_jeton=jeton,
        )

    @app.get("/extractions-voile")
    def route_lister_extractions(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        statut_liaison: str | None = Query(default=None, max_length=50),
        statut_extraction: str | None = Query(default=None, max_length=50),
        recherche: str | None = Query(default=None, max_length=200),
        limite: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return lister_extractions(
                index,
                statut_liaison=statut_liaison,
                statut_extraction=statut_extraction,
                recherche=recherche,
                limite=limite,
                offset=offset,
            )
        except (LiaisonVoileError, AliasBateauEnConflit) as erreur:
            raise _traduire_erreur(erreur) from erreur

    @app.post("/extractions-voile/resoudre-lot")
    def route_resoudre_lot(
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        limite_brute = (corps or {}).get("limite", 200)
        try:
            limite = int(limite_brute)
        except (TypeError, ValueError) as erreur:
            raise HTTPException(
                status_code=422, detail=f"« limite » doit être un entier (reçu : {limite_brute!r})."
            ) from erreur
        try:
            bilan = resoudre_extractions(index, limite=limite)
        except (LiaisonVoileError, AliasBateauEnConflit) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(index, acteur, "extraction_voile.resoudre_lot", None, dict(bilan))
        return bilan

    @app.get("/extractions-voile/{id_extraction}")
    def route_detail_extraction(
        id_extraction: int,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return detail_extraction(index, id_extraction)
        except (LiaisonVoileError, AliasBateauEnConflit) as erreur:
            raise _traduire_erreur(erreur) from erreur

    @app.post("/extractions-voile/{id_extraction}/resoudre")
    def route_resoudre_extraction(
        id_extraction: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        try:
            with index.connect() as connexion:
                with connexion.cursor() as curseur:
                    decision = resoudre_extraction_curseur(curseur, id_extraction)
        except (LiaisonVoileError, AliasBateauEnConflit) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "extraction_voile.resoudre", id_extraction,
            {"statut_liaison": decision["statut_liaison"], "boat_model_id": decision["boat_model_id"]},
        )
        return decision

    @app.post("/extractions-voile/{id_extraction}/lier")
    def route_lier_extraction(
        id_extraction: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        donnees = corps or {}
        id_modele_brut = donnees.get("id_modele")
        try:
            id_modele = int(id_modele_brut) if id_modele_brut is not None else None
        except (TypeError, ValueError) as erreur:
            raise HTTPException(
                status_code=422, detail=f"« id_modele » doit être un entier (reçu : {id_modele_brut!r})."
            ) from erreur
        assert acteur.id_utilisateur is not None  # garanti par exiger_acteur_nominatif
        try:
            resultat = lier_extraction(
                index,
                id_extraction,
                id_utilisateur=acteur.id_utilisateur,
                id_modele=id_modele,
                nouveau_modele=(
                    str(donnees["nouveau_modele"]) if donnees.get("nouveau_modele") is not None else None
                ),
                apprendre_alias=bool(donnees.get("apprendre_alias", True)),
            )
        except (LiaisonVoileError, AliasBateauEnConflit) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "extraction_voile.lier", id_extraction,
            {
                "id_modele": resultat["modele"]["id_modele"],
                "methode_liaison": resultat["methode_liaison"],
                "alias_appris": resultat["alias_appris"]["id_alias"] if resultat["alias_appris"] else None,
            },
        )
        return resultat

    @app.post("/extractions-voile/{id_extraction}/corriger")
    def route_corriger_extraction(
        id_extraction: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        donnees = corps or {}
        champ = donnees.get("champ")
        if not isinstance(champ, str) or not champ:
            raise HTTPException(status_code=422, detail="Préciser le champ à corriger.")
        if "valeur" not in donnees:
            raise HTTPException(status_code=422, detail="Préciser la valeur corrigée.")
        revision_brute = donnees.get("revision_fiche")
        try:
            revision = int(revision_brute) if revision_brute is not None else None
        except (TypeError, ValueError) as erreur:
            raise HTTPException(
                status_code=422, detail=f"« revision_fiche » doit être un entier (reçu : {revision_brute!r})."
            ) from erreur
        assert acteur.id_utilisateur is not None  # garanti par exiger_acteur_nominatif
        try:
            # HTTPException du noyau de correction fiche (428/409/RG11) remonte
            # telle quelle : c'est déjà le langage des autres écrans.
            resultat = corriger_extraction(
                index,
                id_extraction,
                id_utilisateur=acteur.id_utilisateur,
                champ=champ,
                valeur=donnees["valeur"],
                motif=donnees.get("motif"),
                revision_fiche=revision,
            )
        except (LiaisonVoileError, AliasBateauEnConflit) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "extraction_voile.corriger", id_extraction,
            {
                "champ": champ,
                "propagation": resultat["propagation"]["statut"],
                "statut_plausibilite": resultat["statut_plausibilite"],
            },
        )
        return resultat
