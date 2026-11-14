"""API de confiance de réutilisation — Lot 5 (Fonction B).

Évaluer = une décision métier : POST réservé à l'``approbateur``, persistée
(migration 030) et auditée. Les lectures (historique des évaluations,
population de référence) exigent une session nominative. Le service vit dans
`seamtech_search.confiance_reutilisation` ; ce module ne fait que
l'authentification, la validation HTTP et la traduction d'erreurs.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query
from pydantic import ValidationError

from .audit import record_audit_event
from .comptes.comptes import ROLE_APPROBATEUR
from .confiance_reutilisation import (
    ConfianceRefusee,
    evaluer_cible_population,
    evaluer_extraction,
    lister_evaluations,
    population_par_modele,
)
from .extractions_voile import ExtractionVoile
from .liaison_voile import ExtractionIntrouvable
from .securite_requetes import ActeurRequete, exiger_acteur_nominatif

#: Dimensions qu'une cible peut annoncer, dans l'ordre du métier.
DIMENSIONS_CIBLE = ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_m", "poids_kg")

LOGGER = logging.getLogger("seamtech_search.confiance_reutilisation_routes")


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Confiance de réutilisation disponible sur PostgreSQL uniquement "
                "(décision de couche §17.1) : les tables sail_extractions et "
                "evaluations_confiance n'existent pas côté SQLite."
            ),
        )


def enregistrer_routes_confiance_reutilisation(
    app: Any,  # noqa: ANN401
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    verifier_auth: Any,  # noqa: ANN401
) -> None:
    """Monte `/confiance-reutilisation` sur l'application FastAPI."""

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

    @app.post("/confiance-reutilisation/evaluer")
    def route_evaluer_confiance(
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
        identifiant_brut = donnees.get("id_extraction")
        try:
            id_extraction = int(identifiant_brut)
        except (TypeError, ValueError) as erreur:
            raise HTTPException(
                status_code=422,
                detail=f"« id_extraction » doit être un entier (reçu : {identifiant_brut!r}).",
            ) from erreur
        assert acteur.id_utilisateur is not None  # garanti par exiger_acteur_nominatif
        try:
            resultat = evaluer_extraction(
                index, id_extraction, id_utilisateur=acteur.id_utilisateur
            )
        except ExtractionIntrouvable as erreur:
            raise HTTPException(status_code=404, detail=str(erreur)) from erreur
        except ConfianceRefusee as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur
        record_audit_event(
            index,
            action="confiance_reutilisation.evaluer",
            actor=acteur.libelle_audit,
            resource=str(id_extraction),
            status="200",
            details={
                "verdict": resultat["verdict"],
                "score": resultat["score"],
                "n_echantillons": resultat["n_echantillons"],
            },
            actor_type=acteur.type_acteur,
            id_utilisateur=acteur.id_utilisateur,
            identifiant=acteur.identifiant,
            role=acteur.role,
            id_session=acteur.id_session,
            resource_type="sail_extraction",
            resource_id=str(id_extraction),
            code_statut=200,
        )
        return resultat

    @app.post("/confiance-reutilisation/cible")
    def route_evaluer_cible(
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """Évalue une CIBLE — une voile à fabriquer — contre l'archive.

        Lecture seule : rien n'est persisté, aucune décision n'est prise, donc
        aucun acquittement d'approbateur n'est demandé (contrairement à
        ``/evaluer``, qui engage et s'audit). Une session nominative suffit.

        La différence avec ``/evaluer`` est la question posée : ici on ne juge
        pas une extraction existante, on demande « pour cette voile que je m'
        apprête à faire, que dit l'archive ? ». Les écarts mesurés sont
        renvoyés dès une seule fiche de référence ; le score statistique, lui,
        reste soumis aux cinq échantillons et le nombre manquant est écrit.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        donnees = corps or {}
        brut_modele = donnees.get("boat_model_id")
        try:
            id_modele = int(brut_modele)
        except (TypeError, ValueError) as erreur:
            raise HTTPException(
                status_code=422,
                detail=f"« boat_model_id » doit être un entier (reçu : {brut_modele!r}).",
            ) from erreur
        if id_modele < 1:
            raise HTTPException(status_code=422, detail="« boat_model_id » doit être un entier positif.")

        champs: dict[str, Any] = {
            "boat_model_id": id_modele,
            "type_voile": donnees.get("type_voile"),
            "gamme": donnees.get("gamme"),
            "modele_bateau_brut": donnees.get("modele_bateau_brut"),
        }
        for nom in DIMENSIONS_CIBLE:
            if donnees.get(nom) is not None:
                champs[nom] = donnees[nom]
        try:
            cible = ExtractionVoile(**champs)
        except ValidationError as erreur:
            raise HTTPException(
                status_code=422,
                detail=f"Cible invalide : {erreur.errors()[0].get('msg', 'valeur refusée') if erreur.errors() else 'valeur refusée'}.",
            ) from erreur
        try:
            return evaluer_cible_population(index, cible, gamme=donnees.get("gamme"))
        except ConfianceRefusee as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur

    @app.get("/confiance-reutilisation/evaluations")
    def route_evaluations_confiance(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        id_extraction: int = Query(ge=1),
        limite: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return lister_evaluations(index, id_extraction, limite=limite)
        except ExtractionIntrouvable as erreur:
            raise HTTPException(status_code=404, detail=str(erreur)) from erreur
        except ConfianceRefusee as erreur:
            raise HTTPException(status_code=503, detail=str(erreur)) from erreur

    @app.get("/confiance-reutilisation/population")
    def route_population_confiance(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        boat_model_id: int = Query(ge=1),
        type_voile: str = Query(min_length=1, max_length=200),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return population_par_modele(index, boat_model_id, type_voile)
        except ConfianceRefusee as erreur:
            raise HTTPException(status_code=503, detail=str(erreur)) from erreur
