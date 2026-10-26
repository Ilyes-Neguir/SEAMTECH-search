"""API des extractions d'accessoires — Lot 7.

Même contrat que les extractions de voiles (Lot 3b) : lecture ouverte à
tout compte nominatif, mutations réservées à l'``approbateur`` — le seul
rôle qui décide, corrige et reclasse (Lot 1). Le service métier vit dans
`seamtech_search.accessoires` ; ce module ne fait que l'authentification,
la validation HTTP et la traduction d'erreurs.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query

from .accessoires import (
    AccessoireIntrouvable,
    AccessoireRefuse,
    FicheIntrouvable,
    changer_categorie_fiche,
    corriger_accessoire,
    detail_extraction_accessoire,
    lier_accessoire,
    lister_extractions_accessoire,
    resoudre_accessoire_curseur,
)
from .audit import record_audit_event
from .comptes.comptes import ROLE_APPROBATEUR
from .securite_requetes import ActeurRequete, exiger_acteur_nominatif

LOGGER = logging.getLogger("seamtech_search.accessoires_routes")


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Accessoires disponibles sur PostgreSQL uniquement : les tables "
                "accessory_extractions n'existent pas côté SQLite."
            ),
        )


def _traduire_erreur(erreur: Exception) -> HTTPException:
    if isinstance(erreur, (AccessoireIntrouvable, FicheIntrouvable)):
        return HTTPException(status_code=404, detail=str(erreur))
    if isinstance(erreur, AccessoireRefuse):
        # Une reclassification bloquée par des corrections humaines est un
        # conflit d'état (409) ; les autres refus sont des demandes invalides.
        statut = 409 if "corrections humaines" in str(erreur) else 422
        return HTTPException(status_code=statut, detail=str(erreur))
    raise erreur


def _auditer(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    action: str,
    ressource: str,
    details: dict[str, Any],
) -> None:
    record_audit_event(
        index,
        action=action,
        actor=acteur.libelle_audit,
        resource=ressource,
        status="200",
        details=details,
        actor_type=acteur.type_acteur,
        id_utilisateur=acteur.id_utilisateur,
        identifiant=acteur.identifiant,
        role=acteur.role,
        id_session=acteur.id_session,
        resource_type="accessory_extraction",
        resource_id=ressource,
        code_statut=200,
    )


def enregistrer_routes_accessoires(
    app: Any,  # noqa: ANN401
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    verifier_auth: Any,  # noqa: ANN401
) -> None:
    """Monte `/accessoires/extractions` sur l'application FastAPI."""

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

    _entetes = (
        Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")],
        Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")],
        Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")],
        Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")],
        Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")],
    )

    @app.get("/accessoires/extractions")
    def route_lister_accessoires(
        token: _entetes[0] = None,
        entete_utilisateur: _entetes[1] = None,
        entete_role: _entetes[2] = None,
        entete_session: _entetes[3] = None,
        entete_jeton: _entetes[4] = None,
        statut_liaison: str | None = Query(default=None, max_length=50),
        recherche: str | None = Query(default=None, max_length=200),
        limite: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return lister_extractions_accessoire(
            index,
            statut_liaison=statut_liaison,
            recherche=recherche,
            limite=limite,
            offset=offset,
        )

    @app.get("/accessoires/extractions/{id_extraction}")
    def route_detail_accessoire(
        id_extraction: int,
        token: _entetes[0] = None,
        entete_utilisateur: _entetes[1] = None,
        entete_role: _entetes[2] = None,
        entete_session: _entetes[3] = None,
        entete_jeton: _entetes[4] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return detail_extraction_accessoire(index, id_extraction)
        except (AccessoireIntrouvable, FicheIntrouvable, AccessoireRefuse) as erreur:
            raise _traduire_erreur(erreur) from erreur

    @app.post("/accessoires/extractions/{id_extraction}/resoudre")
    def route_resoudre_accessoire(
        id_extraction: int,
        corps: dict[str, Any] | None = None,
        token: _entetes[0] = None,
        entete_utilisateur: _entetes[1] = None,
        entete_role: _entetes[2] = None,
        entete_session: _entetes[3] = None,
        entete_jeton: _entetes[4] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        try:
            with index.connect() as connexion, connexion.cursor() as curseur:
                decision = resoudre_accessoire_curseur(curseur, id_extraction)
        except (AccessoireIntrouvable, FicheIntrouvable, AccessoireRefuse) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "accessoire.resoudre", str(id_extraction),
            {"statut_liaison": decision["statut_liaison"], "boat_model_id": decision["boat_model_id"]},
        )
        return decision

    @app.post("/accessoires/extractions/{id_extraction}/lier")
    def route_lier_accessoire(
        id_extraction: int,
        corps: dict[str, Any] | None = None,
        token: _entetes[0] = None,
        entete_utilisateur: _entetes[1] = None,
        entete_role: _entetes[2] = None,
        entete_session: _entetes[3] = None,
        entete_jeton: _entetes[4] = None,
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
            resultat = lier_accessoire(
                index,
                id_extraction,
                id_utilisateur=acteur.id_utilisateur,
                id_modele=id_modele,
                nouveau_modele=(
                    str(donnees["nouveau_modele"]) if donnees.get("nouveau_modele") is not None else None
                ),
            )
        except (AccessoireIntrouvable, FicheIntrouvable, AccessoireRefuse) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "accessoire.lier", str(id_extraction),
            {"id_modele": resultat.get("boat_model_id"), "methode_liaison": resultat.get("methode_liaison")},
        )
        return resultat

    @app.post("/accessoires/extractions/{id_extraction}/corriger")
    def route_corriger_accessoire(
        id_extraction: int,
        corps: dict[str, Any] | None = None,
        token: _entetes[0] = None,
        entete_utilisateur: _entetes[1] = None,
        entete_role: _entetes[2] = None,
        entete_session: _entetes[3] = None,
        entete_jeton: _entetes[4] = None,
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
        assert acteur.id_utilisateur is not None  # garanti par exiger_acteur_nominatif
        try:
            resultat = corriger_accessoire(
                index,
                id_extraction,
                id_utilisateur=acteur.id_utilisateur,
                champ=champ,
                valeur=donnees["valeur"],
                motif=donnees.get("motif"),
            )
        except (AccessoireIntrouvable, FicheIntrouvable, AccessoireRefuse) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "accessoire.corriger", str(id_extraction), {"champ": champ}
        )
        return resultat

    @app.post("/accessoires/fiches/{id_fiche}/categorie")
    def route_changer_categorie_fiche(
        id_fiche: int,
        corps: dict[str, Any] | None = None,
        token: _entetes[0] = None,
        entete_utilisateur: _entetes[1] = None,
        entete_role: _entetes[2] = None,
        entete_session: _entetes[3] = None,
        entete_jeton: _entetes[4] = None,
    ) -> dict[str, Any]:
        """Reclassification d'une fiche — décision approbateur (source humain)."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        categorie = (corps or {}).get("categorie")
        if categorie not in ("voile", "accessoire"):
            raise HTTPException(
                status_code=422, detail="« categorie » vaut « voile » ou « accessoire »."
            )
        assert acteur.id_utilisateur is not None  # garanti par exiger_acteur_nominatif
        try:
            resultat = changer_categorie_fiche(
                index, id_fiche, categorie=categorie, id_utilisateur=acteur.id_utilisateur
            )
        except (AccessoireIntrouvable, FicheIntrouvable, AccessoireRefuse) as erreur:
            raise _traduire_erreur(erreur) from erreur
        _auditer(
            index, acteur, "fiche.categorie", str(id_fiche), {"categorie": categorie}
        )
        return resultat
