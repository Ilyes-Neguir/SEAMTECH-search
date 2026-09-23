"""Routes ``/auth`` — Lot L.2.

Chemin de la requête, à ne pas confondre :

    navigateur ──cookie signé──► Next.js (/api/auth/*) ──X-SEAMTECH-TOKEN──► ici

Le navigateur ne parle JAMAIS directement à ces routes et ne détient JAMAIS
``SEAMTECH_AUTH_TOKEN``. Toutes les routes de ce module exigent ce jeton de
service : sans lui, 401 — y compris pour se connecter.

Ce que ces routes ne renvoient JAMAIS, quelle que soit la route : l'empreinte
de mot de passe, le sel, l'``empreinte_jeton``. Le jeton de session lui-même
n'est renvoyé qu'une fois, à l'ouverture de la session (c'est sa définition : il
n'existe nulle part en clair côté serveur, seulement son SHA-256).

Gestion des comptes : réservée au rôle ``administrateur``. Un opérateur
authentifié reçoit 403 (et non 401 : il EST identifié, il n'a simplement pas le
droit). Sans session valide : 401.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException

from .comptes import (
    changer_mot_de_passe,
    correspondance_jeton,
    creer_utilisateur,
    desactiver_utilisateur,
    lister_utilisateurs,
    ouvrir_session,
    reinitialiser_mot_de_passe,
    revoquer_session,
    session_valide,
    utilisateur_dune_session,
    verifier_identifiants,
)

LOGGER = logging.getLogger("seamtech_search.comptes.routes")


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    """Les comptes nominatifs n'existent que sur PostgreSQL (schéma métier)."""
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche "
                "§17.1 du plan v3.0) : utilisateur/session_ui n'existent pas côté SQLite."
            ),
        )


def _session_authentifiee(
    index: Any,  # noqa: ANN401
    id_session: str | None,
    jeton: str | None,
) -> dict[str, Any]:
    """Résout la session présentée par les en-têtes, ou lève 401.

    Deux conditions, non négociables : le jeton doit correspondre à l'empreinte
    stockée, ET la session ne doit être ni révoquée ni expirée. C'est ce second
    point qui rend ``/auth/deconnexion`` réellement effectif.
    """
    if not id_session or not jeton:
        raise HTTPException(
            status_code=401,
            detail="Session absente : en-têtes X-SEAMTECH-SESSION et X-SEAMTECH-SESSION-JETON requis.",
        )
    try:
        identifiant_session = int(id_session)
    except (TypeError, ValueError) as erreur:
        raise HTTPException(status_code=401, detail="Identifiant de session illisible.") from erreur

    if not session_valide(index, identifiant_session, jeton):
        raise HTTPException(
            status_code=401,
            detail="Session invalide : expirée, révoquée, ou jeton non conforme.",
        )
    utilisateur = utilisateur_dune_session(index, identifiant_session)
    if utilisateur is None or not utilisateur.get("actif", False):
        raise HTTPException(status_code=401, detail="Compte inconnu ou désactivé.")
    utilisateur["id_session"] = identifiant_session
    return utilisateur


def _exiger_administrateur(index: Any, id_session: str | None, jeton: str | None) -> dict[str, Any]:  # noqa: ANN401
    """Comme ``_session_authentifiee``, plus le rôle. 403 pour un opérateur."""
    utilisateur = _session_authentifiee(index, id_session, jeton)
    if utilisateur.get("role") != "administrateur":
        raise HTTPException(
            status_code=403,
            detail=(
                "Gestion des comptes réservée au rôle « administrateur » "
                f"(rôle de la session : « {utilisateur.get('role')} »)."
            ),
        )
    return utilisateur


def enregistrer_routes_auth(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:  # noqa: ANN401
    """Monte les routes d'authentification nominative sur ``app``."""

    @app.post("/auth/connexion")
    def route_connexion(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        adresse: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
        agent: Annotated[str | None, Header(alias="User-Agent")] = None,
    ) -> dict[str, Any]:
        """Vérifie les identifiants et ouvre une session nominative.

        Réponse : ``{ok, id_utilisateur, identifiant, nom, role, id_session,
        jeton_session, expire_le}``. 401 si les identifiants sont faux, 429 si
        cinq tentatives ont échoué en cinq minutes (verrou en base).
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        identifiant = str(corps.get("identifiant") or "").strip()
        mot_de_passe = corps.get("mot_de_passe")
        if not identifiant or not mot_de_passe:
            raise HTTPException(status_code=422, detail="« identifiant » et « mot_de_passe » sont requis.")
        utilisateur = verifier_identifiants(
            index, identifiant, str(mot_de_passe), adresse=(adresse or "").split(",")[0].strip() or None
        )
        session = ouvrir_session(index, utilisateur["id_utilisateur"], user_agent=agent)
        return {
            "ok": True,
            **utilisateur,
            "id_session": session["id_session"],
            # Renvoyé UNE fois : le front le place dans le cookie signé, le
            # serveur n'en garde que l'empreinte. C'est un secret de session.
            "jeton_session": session["jeton"],
            "expire_le": session["expire_le"],
        }

    @app.post("/auth/deconnexion")
    def route_deconnexion(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Pose ``revoque_le`` : le cookie, même encore signé, ne vaut plus rien."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        id_session = corps.get("id_session")
        jeton = corps.get("jeton_session")
        if not id_session or not jeton:
            raise HTTPException(status_code=422, detail="« id_session » et « jeton_session » sont requis.")
        try:
            identifiant_session = int(id_session)
        except (TypeError, ValueError) as erreur:
            raise HTTPException(status_code=422, detail="« id_session » doit être un entier.") from erreur
        if not correspondance_jeton(index, identifiant_session, str(jeton)):
            raise HTTPException(
                status_code=401,
                detail="Jeton de session non conforme : révocation refusée.",
            )
        return {"ok": True, **revoquer_session(index, identifiant_session)}

    @app.get("/auth/session")
    def route_session(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """Qui est connecté ? Jamais d'empreinte, de sel ni de jeton."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        utilisateur = _session_authentifiee(index, entete_session, entete_jeton)
        return {"ok": True, **utilisateur}

    @app.post("/auth/mot-de-passe")
    def route_mot_de_passe(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """Changement par l'intéressé : exige le mot de passe provisoire/actuel."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        utilisateur = _session_authentifiee(index, entete_session, entete_jeton)
        return {
            "ok": True,
            **changer_mot_de_passe(
                index,
                utilisateur["id_utilisateur"],
                str(corps.get("mot_de_passe_actuel") or ""),
                str(corps.get("nouveau_mot_de_passe") or ""),
            ),
        }

    # -----------------------------------------------------------------------
    # Gestion des comptes — administrateur uniquement
    # -----------------------------------------------------------------------

    @app.get("/auth/utilisateurs")
    def route_lister_utilisateurs(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _exiger_administrateur(index, entete_session, entete_jeton)
        return lister_utilisateurs(index)

    @app.post("/auth/utilisateurs")
    def route_creer_utilisateur(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        administrateur = _exiger_administrateur(index, entete_session, entete_jeton)
        if not corps.get("mot_de_passe"):
            raise HTTPException(
                status_code=422,
                detail="« mot_de_passe » requis (jamais journalisé, jamais renvoyé).",
            )
        LOGGER.info(
            "Création du compte « %s » demandée par « %s ».",
            corps.get("identifiant"), administrateur["identifiant"],
        )
        return creer_utilisateur(
            index,
            str(corps.get("identifiant") or ""),
            str(corps.get("nom") or ""),
            str(corps["mot_de_passe"]),
            str(corps.get("role") or "operateur"),
        )

    @app.post("/auth/utilisateurs/{identifiant}/desactiver")
    def route_desactiver_utilisateur(
        identifiant: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _exiger_administrateur(index, entete_session, entete_jeton)
        return {"ok": True, **desactiver_utilisateur(index, identifiant)}

    @app.post("/auth/utilisateurs/{identifiant}/mot-de-passe")
    def route_reinitialiser_mot_de_passe(
        identifiant: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _exiger_administrateur(index, entete_session, entete_jeton)
        if not corps.get("mot_de_passe"):
            raise HTTPException(status_code=422, detail="« mot_de_passe » requis.")
        return {
            "ok": True,
            **reinitialiser_mot_de_passe(index, identifiant, str(corps["mot_de_passe"])),
        }
