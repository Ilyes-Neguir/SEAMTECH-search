"""Acteur d'une requête backend — Lot 0b (anti-IDOR et réduction de surface).

Le backend FastAPI n'est jamais joint directement par le navigateur : Next.js
présente le jeton de service et, pour un humain connecté, les en-têtes de
session. Jusqu'au Lot 0b, seules les routes ``/auth`` revérifiaient cette
session ; les autres routes se contentaient du jeton de service et, pour
l'attribution, d'en-têtes déclaratifs.

Ce module centralise la règle désormais appliquée aux surfaces sensibles :

1. si des en-têtes de session sont présentés, la session doit être vivante
   (PostgreSQL + registre Redis du Lot 0a) et l'identité est RELUE en base ;
   un en-tête utilisateur/rôle incompatible avec la session vaut 401 ;
2. le compte de secours reste possible, mais uniquement derrière un jeton de
   service valide et avec le rôle ``admin`` documenté ;
3. en PostgreSQL, une identité nominative sans jeton de session ne suffit pas
   pour les gardes de ce module ;
4. en SQLite, les comptes nominatifs n'existent pas (§17.1) : les gardes
   laissent passer l'acteur de service déjà authentifié par la route, afin de
   ne pas casser l'outillage et les tests du mode fichiers. Ce repli est
   explicite et ne concerne jamais le déploiement PostgreSQL de production.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import HTTPException

from .audit import actor_fingerprint
from .comptes.comptes import (
    IDENTIFIANT_SECOURS,
    ROLE_ADMIN,
    ROLE_APPROBATEUR,
    ROLE_SECOURS,
    session_valide,
    utilisateur_dune_session,
)
from .sessions_redis import SessionsRedisIndisponible, registre_depuis_config


@dataclass(frozen=True)
class ActeurRequete:
    """Identité résolue d'une requête, prête pour autorisation et audit."""

    type_acteur: str
    libelle_audit: str
    identifiant: str | None = None
    role: str | None = None
    id_utilisateur: int | None = None
    id_session: int | None = None

    @property
    def est_humain(self) -> bool:
        return self.type_acteur in {"utilisateur", "secours"}

    @property
    def est_administrateur(self) -> bool:
        return self.est_humain and self.role == ROLE_ADMIN

    @property
    def est_admin(self) -> bool:
        return self.est_administrateur

    @property
    def est_approbateur(self) -> bool:
        return self.type_acteur == "utilisateur" and self.role == ROLE_APPROBATEUR

    def contexte_audit(self) -> dict[str, Any]:
        """Champs structurés acceptés par ``record_audit_event``."""
        return {
            "actor_type": self.type_acteur,
            "id_utilisateur": self.id_utilisateur,
            "identifiant": self.identifiant,
            "role": self.role,
            "id_session": self.id_session,
        }


def _jeton_service_attendu(config: Any) -> str | None:  # noqa: ANN401
    valeur = getattr(config, "auth_token", None)
    return str(valeur) if valeur else None


def _jeton_service_valide(config: Any, token: str | None) -> bool:  # noqa: ANN401
    attendu = _jeton_service_attendu(config)
    if attendu is None:
        return False
    return bool(token) and hmac.compare_digest(str(token), attendu)


def _exiger_jeton_pour_entetes_humains(config: Any, token: str | None) -> None:  # noqa: ANN401
    if _jeton_service_attendu(config) is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "En-têtes d'identité refusés : aucun jeton de service n'est configuré "
                "(SEAMTECH_AUTH_TOKEN), donc leur origine ne peut pas être établie."
            ),
        )
    if not _jeton_service_valide(config, token):
        raise HTTPException(
            status_code=401,
            detail="En-têtes d'identité refusés : X-SEAMTECH-TOKEN invalide ou absent.",
        )


def _registre_sessions(config: Any, registre: Any | None) -> Any | None:  # noqa: ANN401
    if registre is not None:
        return registre
    try:
        return registre_depuis_config(config)
    except SessionsRedisIndisponible as erreur:
        raise HTTPException(
            status_code=503,
            detail="Service de session indisponible (Redis) : requête refusée.",
        ) from erreur


def resoudre_acteur_requete(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    config: Any,  # noqa: ANN401 - AppConfig
    *,
    token: str | None,
    entete_utilisateur: str | None = None,
    entete_role: str | None = None,
    entete_session: str | None = None,
    entete_jeton: str | None = None,
    client_host: str | None = None,
    registre: Any | None = None,  # noqa: ANN401 - registre Redis optionnel/test
) -> ActeurRequete:
    """Résout l'acteur réel, ou lève 401/403/503 selon le défaut constaté."""
    utilisateur_entete = (entete_utilisateur or "").strip() or None
    role_entete = (entete_role or "").strip() or None
    session_entete = (entete_session or "").strip() or None
    jeton_session = (entete_jeton or "").strip() or None

    if session_entete or jeton_session:
        _exiger_jeton_pour_entetes_humains(config, token)
        if not session_entete or not jeton_session:
            raise HTTPException(
                status_code=401,
                detail="Session incomplète : X-SEAMTECH-SESSION et X-SEAMTECH-SESSION-JETON sont requis ensemble.",
            )
        if not getattr(index, "is_postgres", False):
            raise HTTPException(
                status_code=503,
                detail="Les sessions nominatives exigent PostgreSQL (décision de couche §17.1).",
            )
        try:
            id_session = int(session_entete)
        except ValueError as erreur:
            raise HTTPException(status_code=401, detail="Identifiant de session illisible.") from erreur

        registre_sessions = _registre_sessions(config, registre)
        if not session_valide(
            index,
            id_session,
            jeton_session,
            idle_minutes=int(getattr(config, "session_idle_minutes", 30)),
            registre=registre_sessions,
        ):
            raise HTTPException(
                status_code=401,
                detail="Session invalide : expirée, révoquée, ou jeton non conforme.",
            )
        utilisateur = utilisateur_dune_session(index, id_session)
        if utilisateur is None or not utilisateur.get("actif", False):
            raise HTTPException(status_code=401, detail="Compte inconnu ou désactivé.")

        identifiant_reel = str(utilisateur["identifiant"])
        role_reel = str(utilisateur["role"])
        if utilisateur_entete and utilisateur_entete != identifiant_reel:
            raise HTTPException(
                status_code=401,
                detail="En-tête utilisateur incompatible avec la session présentée.",
            )
        if role_entete and role_entete != role_reel:
            raise HTTPException(
                status_code=401,
                detail="En-tête de rôle incompatible avec la session présentée.",
            )
        return ActeurRequete(
            type_acteur="utilisateur",
            libelle_audit=identifiant_reel,
            identifiant=identifiant_reel,
            role=role_reel,
            id_utilisateur=int(utilisateur["id_utilisateur"]),
            id_session=id_session,
        )

    if utilisateur_entete or role_entete:
        _exiger_jeton_pour_entetes_humains(config, token)
        if not utilisateur_entete:
            raise HTTPException(
                status_code=401,
                detail="En-tête de rôle sans identifiant : attribution refusée.",
            )
        if utilisateur_entete == IDENTIFIANT_SECOURS:
            if role_entete not in {None, ROLE_SECOURS}:
                raise HTTPException(
                    status_code=401,
                    detail="Rôle du compte de secours incompatible avec la politique documentée.",
                )
            return ActeurRequete(
                type_acteur="secours",
                libelle_audit=IDENTIFIANT_SECOURS,
                identifiant=IDENTIFIANT_SECOURS,
                role=ROLE_SECOURS,
            )
        if getattr(index, "is_postgres", False):
            raise HTTPException(
                status_code=401,
                detail=(
                    "Session nominative requise : X-SEAMTECH-UTILISATEUR seul ne suffit plus "
                    "pour cette surface (Lot 0b)."
                ),
            )
        # SQLite n'a ni utilisateur ni session_ui. L'identité reste déclarative
        # dans ce mode de développement, mais elle n'est honorée que derrière
        # le jeton de service vérifié ci-dessus.
        return ActeurRequete(
            type_acteur="utilisateur",
            libelle_audit=utilisateur_entete,
            identifiant=utilisateur_entete,
            role=role_entete,
        )

    attendu = _jeton_service_attendu(config)
    if attendu is not None and not _jeton_service_valide(config, token):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return ActeurRequete(
        type_acteur="service",
        libelle_audit=actor_fingerprint(token, client_host),
    )


def acteur_session_si_presente(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    *,
    token: str | None,
    entete_utilisateur: str | None = None,
    entete_role: str | None = None,
    entete_session: str | None = None,
    entete_jeton: str | None = None,
    registre: Any | None = None,  # noqa: ANN401
) -> ActeurRequete | None:
    """Valide une session si elle est présentée ; sinon ne décide rien.

    Sert aux endpoints d'attribution historique : quand le proxy transmet une
    session, elle devient l'unique source d'identité ; quand il n'en transmet
    pas, l'ancien repli explicite (secours ou corps pour l'outillage) reste
    décidé par l'appelant.
    """
    if not (entete_session or entete_jeton):
        return None
    return resoudre_acteur_requete(
        index,
        config,
        token=token,
        entete_utilisateur=entete_utilisateur,
        entete_role=entete_role,
        entete_session=entete_session,
        entete_jeton=entete_jeton,
        registre=registre,
    )


def exiger_acteur_humain(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    **entetes: Any,  # noqa: ANN401
) -> ActeurRequete:
    """Exige un humain en PostgreSQL ; conserve le service authentifié en SQLite."""
    acteur = resoudre_acteur_requete(index, config, **entetes)
    if getattr(index, "is_postgres", False) and not acteur.est_humain:
        raise HTTPException(
            status_code=401,
            detail="Session humaine requise pour cette ressource (Lot 0b).",
        )
    return acteur


def exiger_administrateur_requete(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    **entetes: Any,  # noqa: ANN401
) -> ActeurRequete:
    """Exige un admin humain en PostgreSQL (surface d'exploitation)."""
    acteur = resoudre_acteur_requete(index, config, **entetes)
    if not getattr(index, "is_postgres", False):
        return acteur
    if not acteur.est_humain:
        raise HTTPException(
            status_code=401,
            detail="Session admin requise pour cette surface d'exploitation (Lot 0b).",
        )
    if not acteur.est_administrateur:
        raise HTTPException(
            status_code=403,
            detail="Surface réservée au rôle admin.",
        )
    return acteur


def _libelle_roles(roles: tuple[str, ...]) -> str:
    return ", ".join(roles)


def exiger_roles_requete(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    roles: tuple[str, ...],
    **entetes: Any,  # noqa: ANN401
) -> ActeurRequete:
    """Garde stricte Lot 1 pour une capacité métier réservée à des rôles.

    Un humain présenté est toujours arbitré sur le rôle relu en base. En
    PostgreSQL avec jeton de service configuré, le jeton seul ne devient pas
    un humain : une session nominative du bon rôle est requise. Le mode
    historique sans jeton de service (backend local explicitement non
    protégé) et SQLite conservent l'acteur de service pour l'outillage de
    développement ; ce repli ne s'applique jamais au déploiement protégé.
    """
    acteur = resoudre_acteur_requete(index, config, **entetes)
    if acteur.est_humain:
        if acteur.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Capacité réservée au(x) rôle(s) {_libelle_roles(roles)} (rôle actuel : {acteur.role}).",
            )
        return acteur
    if not getattr(index, "is_postgres", False) or _jeton_service_attendu(config) is None:
        return acteur
    raise HTTPException(
        status_code=401,
        detail=f"Session nominative requise avec le rôle {_libelle_roles(roles)}.",
    )


def exiger_approbateur_requete(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    **entetes: Any,  # noqa: ANN401
) -> ActeurRequete:
    """Seul un approbateur nominatif peut corriger ou décider directement."""
    return exiger_roles_requete(index, config, (ROLE_APPROBATEUR,), **entetes)


def exiger_role_si_humain(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    roles: tuple[str, ...],
    **entetes: Any,  # noqa: ANN401
) -> ActeurRequete:
    """Refuse un humain présenté hors rôles, sans retirer l'outillage service.

    Sert aux surfaces opérationnelles historiques aussi appelées par des
    scripts de confiance. Dès qu'une identité humaine est présentée, elle est
    revérifiée et bornée ; en particulier ``lecture`` ne peut plus traverser
    une mutation sous prétexte que la route acceptait le jeton de service.
    """
    acteur = resoudre_acteur_requete(index, config, **entetes)
    if acteur.est_humain and acteur.role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Capacité réservée au(x) rôle(s) {_libelle_roles(roles)} (rôle actuel : {acteur.role}).",
        )
    return acteur


def exiger_acteur_nominatif(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    roles: tuple[str, ...] | None = None,
    **entetes: Any,  # noqa: ANN401
) -> ActeurRequete:
    """Exige un compte nominatif réel (jamais secours ni service).

    Les demandes et notifications portent des clés étrangères vers
    ``utilisateur`` : le secours et le jeton de service n'ont pas d'identité
    persistable et sont donc refusés même lorsque leur rôle technique est
    ``admin``.
    """
    acteur = resoudre_acteur_requete(index, config, **entetes)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        statut = 403 if acteur.type_acteur == "secours" else 401
        raise HTTPException(
            status_code=statut,
            detail="Un compte nominatif connecté est requis pour cette capacité.",
        )
    if roles is not None and acteur.role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Capacité réservée au(x) rôle(s) {_libelle_roles(roles)} (rôle actuel : {acteur.role}).",
        )
    return acteur


def _entete(entetes: Mapping[str, str], nom: str) -> str | None:
    valeur = entetes.get(nom) or entetes.get(nom.lower()) or entetes.get(nom.title())
    return str(valeur).strip() if valeur else None



def resoudre_acteur_audit(
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    entetes: Mapping[str, str],
    client_host: str | None = None,
) -> ActeurRequete:
    """Acteur à écrire dans le journal pour une requête HTTP.

    Le journal est une preuve : il ne doit pas transformer des en-têtes
    déclaratifs en identité établie. On applique donc le même résolveur que
    les gardes (session revérifiée et relue en base). Si la résolution échoue,
    l'événement est attribué au service authentifié — ou à l'anonyme — sans
    identifiant ni session supposés ; la route elle-même reste responsable du
    refus HTTP approprié.
    """
    token = _entete(entetes, "X-SEAMTECH-TOKEN")
    try:
        return resoudre_acteur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=_entete(entetes, "X-SEAMTECH-UTILISATEUR"),
            entete_role=_entete(entetes, "X-SEAMTECH-ROLE"),
            entete_session=_entete(entetes, "X-SEAMTECH-SESSION"),
            entete_jeton=_entete(entetes, "X-SEAMTECH-SESSION-JETON"),
            client_host=client_host,
        )
    except HTTPException:
        libelle = actor_fingerprint(token, client_host)
        return ActeurRequete(
            type_acteur="service" if _jeton_service_valide(config, token) else "anonyme",
            libelle_audit=libelle,
        )
