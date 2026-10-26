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

Gestion des comptes : réservée au rôle ``admin``. Tout autre rôle authentifié
reçoit 403 (et non 401 : il EST identifié, il n'a simplement pas le droit).
Sans session valide : 401.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException

from ..audit import record_audit_event
from ..sessions_redis import (
    RegistreSessionsRedis,
    SessionsRedisIndisponible,
    registre_depuis_config,
)
from .comptes import (
    ROLE_ADMIN,
    ROLE_DEFAUT,
    changer_mot_de_passe,
    changer_role_utilisateur,
    correspondance_jeton,
    creer_utilisateur,
    desactiver_utilisateur,
    lister_utilisateurs,
    ouvrir_session,
    reinitialiser_mot_de_passe,
    revoquer_session,
    revoquer_sessions_utilisateur,
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
    idle_minutes: int = 30,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Résout la session présentée par les en-têtes, ou lève 401.

    Deux conditions, non négociables : le jeton doit correspondre à l'empreinte
    stockée, ET la session ne doit être ni révoquée ni expirée. C'est ce second
    point qui rend ``/auth/deconnexion`` réellement effectif.

    Lot 0a : ``session_valide`` applique en plus le timeout d'inactivité
    (``idle_minutes``) et le registre Redis (503 fail-closed si indisponible).
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

    if not session_valide(index, identifiant_session, jeton, idle_minutes=idle_minutes, registre=registre):
        raise HTTPException(
            status_code=401,
            detail="Session invalide : expirée, révoquée, ou jeton non conforme.",
        )
    utilisateur = utilisateur_dune_session(index, identifiant_session)
    if utilisateur is None or not utilisateur.get("actif", False):
        raise HTTPException(status_code=401, detail="Compte inconnu ou désactivé.")
    utilisateur["id_session"] = identifiant_session
    return utilisateur


def _exiger_administrateur(
    index: Any,  # noqa: ANN401
    id_session: str | None,
    jeton: str | None,
    idle_minutes: int = 30,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Comme ``_session_authentifiee``, plus le rôle. 403 hors admin."""
    utilisateur = _session_authentifiee(index, id_session, jeton, idle_minutes=idle_minutes, registre=registre)
    if utilisateur.get("role") != ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail=(
                "Gestion des comptes réservée au rôle « admin » "
                f"(rôle de la session : « {utilisateur.get('role')} »)."
            ),
        )
    return utilisateur


def enregistrer_routes_auth(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:  # noqa: ANN401
    """Monte les routes d'authentification nominative sur ``app``."""

    _cache_registre: dict[str, RegistreSessionsRedis | None] = {}

    def _registre() -> RegistreSessionsRedis | None:
        """Registre Redis des sessions (Lot 0a), construit une fois.

        Redis exigé mais non configuré => 503 fail-closed : aucune route
        d'authentification ne doit accepter une session invérifiable.
        """
        if "registre" not in _cache_registre:
            try:
                _cache_registre["registre"] = registre_depuis_config(config)
            except SessionsRedisIndisponible as erreur:
                raise HTTPException(
                    status_code=503,
                    detail="Service de session indisponible (Redis) : authentification refusée.",
                ) from erreur
        return _cache_registre["registre"]

    def _idle() -> int:
        return int(getattr(config, "session_idle_minutes", 30))

    def _auditer_compte(
        action: str,
        acteur: dict[str, Any],
        cible: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Événement de compte Lot 0b — jamais de mot de passe ni de jeton."""
        record_audit_event(
            index,
            action=action,
            actor=str(acteur.get("identifiant") or "inconnu"),
            resource=cible,
            status="200",
            details=details or {},
            actor_type="utilisateur",
            id_utilisateur=int(acteur["id_utilisateur"]) if acteur.get("id_utilisateur") else None,
            identifiant=str(acteur.get("identifiant")) if acteur.get("identifiant") else None,
            role=str(acteur.get("role")) if acteur.get("role") else None,
            id_session=int(acteur["id_session"]) if acteur.get("id_session") else None,
            resource_type="utilisateur",
            resource_id=cible,
            code_statut=200,
        )


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
        session = ouvrir_session(
            index,
            utilisateur["id_utilisateur"],
            duree_heures=int(getattr(config, "session_hours", 8)),
            user_agent=agent,
            registre=_registre(),
        )
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
        utilisateur = utilisateur_dune_session(index, identifiant_session) or {}
        if utilisateur:
            utilisateur["id_session"] = identifiant_session
        resultat = revoquer_session(index, identifiant_session, registre=_registre())
        if utilisateur:
            _auditer_compte("deconnexion", utilisateur, str(utilisateur.get("identifiant") or identifiant_session))
        return {"ok": True, **resultat}

    @app.post("/auth/deconnexion-tout")
    def route_deconnexion_tout(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """« Déconnecter tous mes appareils » : révoque TOUTES les sessions du compte.

        Exige une session vivante (on ne révoque pas les sessions d'autrui
        avec un simple identifiant devinable), puis coupe tout — y compris
        la session appelante. Le front efface ensuite le cookie.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        utilisateur = _session_authentifiee(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
        resultat = revoquer_sessions_utilisateur(index, utilisateur["id_utilisateur"], registre=_registre())
        _auditer_compte("deconnexion_tout", utilisateur, str(utilisateur["identifiant"]), resultat)
        return {"ok": True, **resultat}

    @app.get("/auth/session")
    def route_session(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """Qui est connecté ? Jamais d'empreinte, de sel ni de jeton."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        utilisateur = _session_authentifiee(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
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
        utilisateur = _session_authentifiee(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
        resultat = changer_mot_de_passe(
            index,
            utilisateur["id_utilisateur"],
            str(corps.get("mot_de_passe_actuel") or ""),
            str(corps.get("nouveau_mot_de_passe") or ""),
            registre=_registre(),
        )
        _auditer_compte("mot_de_passe_change", utilisateur, str(utilisateur["identifiant"]))
        return {"ok": True, **resultat}

    # -----------------------------------------------------------------------
    # Gestion des comptes — admin uniquement
    # -----------------------------------------------------------------------

    @app.get("/auth/utilisateurs")
    def route_lister_utilisateurs(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _exiger_administrateur(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
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
        administrateur = _exiger_administrateur(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
        if not corps.get("mot_de_passe"):
            raise HTTPException(
                status_code=422,
                detail="« mot_de_passe » requis (jamais journalisé, jamais renvoyé).",
            )
        LOGGER.info(
            "Création du compte « %s » demandée par « %s ».",
            corps.get("identifiant"), administrateur["identifiant"],
        )
        resultat = creer_utilisateur(
            index,
            str(corps.get("identifiant") or ""),
            str(corps.get("nom") or ""),
            str(corps["mot_de_passe"]),
            str(corps.get("role") or ROLE_DEFAUT),
        )
        _auditer_compte(
            "compte_cree",
            administrateur,
            str(resultat["identifiant"]),
            {"role_cible": resultat.get("role"), "id_utilisateur_cible": resultat.get("id_utilisateur")},
        )
        return resultat

    @app.post("/auth/utilisateurs/{identifiant}/role")
    def route_changer_role_utilisateur(
        identifiant: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        administrateur = _exiger_administrateur(
            index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre()
        )
        resultat = changer_role_utilisateur(
            index,
            identifiant,
            str(corps.get("role") or ""),
            registre=_registre(),
        )
        _auditer_compte(
            "compte_role_change",
            administrateur,
            identifiant,
            {
                "ancien_role": resultat.get("ancien_role"),
                "nouveau_role": resultat.get("nouveau_role"),
                "sessions_revoquees": resultat.get("sessions_revoquees"),
            },
        )
        return {"ok": True, **resultat}

    @app.post("/auth/utilisateurs/{identifiant}/desactiver")
    def route_desactiver_utilisateur(
        identifiant: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        administrateur = _exiger_administrateur(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
        resultat = desactiver_utilisateur(index, identifiant, registre=_registre())
        _auditer_compte("compte_desactive", administrateur, identifiant, resultat)
        return {"ok": True, **resultat}

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
        administrateur = _exiger_administrateur(index, entete_session, entete_jeton, idle_minutes=_idle(), registre=_registre())
        if not corps.get("mot_de_passe"):
            raise HTTPException(status_code=422, detail="« mot_de_passe » requis.")
        resultat = reinitialiser_mot_de_passe(index, identifiant, str(corps["mot_de_passe"]), registre=_registre())
        _auditer_compte("mot_de_passe_reinitialise", administrateur, identifiant)
        return {"ok": True, **resultat}
