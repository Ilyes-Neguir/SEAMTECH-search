"""Routes ``/auth`` — Lot L.2, sessions serveur Redis du Lot 0a.

Chemin de la requête, à ne pas confondre :

    navigateur ──cookie signé (sid)──► Next.js (/api/auth/*) ──X-SEAMTECH-TOKEN──► ici

Le navigateur ne parle JAMAIS directement à ces routes et ne détient JAMAIS
``SEAMTECH_AUTH_TOKEN``. Toutes les routes de ce module exigent ce jeton de
service : sans lui, 401 — y compris pour se connecter.

Deux familles de sessions coexistent, et une seule est exposée au navigateur :

* **session navigateur (Lot 0a)** — le cookie ne porte qu'un ``sid`` opaque ;
  l'état (user_id, issued_at, last_seen_at, expires_at) vit dans Redis. C'est
  la seule voie utilisée par le frontend. Toute session absente/révoquée/
  inactive/expirée répond 401 ; Redis indisponible répond 503 (fail-closed).
* **sessions internes historiques** — ``X-SEAMTECH-SESSION`` + jeton, adossées
  à ``session_ui``. Conservées pour les outils/scripts qui les utilisent déjà
  et pour la traçabilité, jamais émises dans un cookie par le frontend actuel.

Ce que ces routes ne renvoient JAMAIS, quelle que soit la route : l'empreinte
de mot de passe, le sel, l'``empreinte_jeton``. Le jeton de session historique
n'est renvoyé qu'une fois, à l'ouverture de la session. Le ``sid`` n'est pas
journalisé.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Header, HTTPException

from ..sessions_redis import (
    MODE_NOMINATIF,
    MODE_SECOURS,
    SessionRecord,
    SessionStore,
    SessionStoreIndisponible,
)
from .comptes import (
    changer_mot_de_passe,
    correspondance_jeton,
    creer_utilisateur,
    desactiver_utilisateur,
    id_utilisateur_par_identifiant,
    lister_utilisateurs,
    ouvrir_session,
    profil_utilisateur,
    reinitialiser_mot_de_passe,
    revoquer_session,
    revoquer_sessions_utilisateur,
    session_valide,
    utilisateur_dune_session,
    verifier_identifiants,
)

LOGGER = logging.getLogger("seamtech_search.comptes.routes")

#: Message unique : on ne révèle jamais si un sid a existé ou non.
_DETAIL_SESSION_REFUSEE = "Session absente, révoquée, inactive ou expirée."
_DETAIL_REDIS = (
    "Magasin de sessions indisponible (Redis) : la session ne peut pas être vérifiée. "
    "Refus explicite (fail-closed), aucun repli n'est autorisé."
)

#: Champs d'un compte publiables par l'API. C'est une LISTE BLANCHE : tout ce
#: qui n'y figure pas ne sort JAMAIS, y compris si un jour un champ interne est
#: ajouté au dictionnaire interne. `etat_identifiants` — la génération dérivée
#: de l'empreinte du mot de passe — en est volontairement absent (revue Lot 0a,
#: B7 : il fuitait par `**utilisateur`).
CHAMPS_PUBLICS_UTILISATEUR = (
    "id_utilisateur",
    "identifiant",
    "nom",
    "role",
    "doit_changer_mot_de_passe",
)

#: Champs strictement internes : jamais dans une réponse, quelle qu'elle soit.
CHAMPS_INTERNES = (
    "etat_identifiants",
    "empreinte_mot_de_passe",
    "sessions_invalidees_le",
)


def _champs_publics(utilisateur: dict[str, Any], champs: tuple[str, ...]) -> dict[str, Any]:
    """Ne garde QUE les champs demandés (liste blanche, jamais une liste noire)."""
    return {champ: utilisateur.get(champ) for champ in champs}


def _sans_champs_internes(donnees: dict[str, Any]) -> dict[str, Any]:
    """Retire les champs internes d'un dictionnaire déjà construit ailleurs."""
    return {cle: valeur for cle, valeur in donnees.items() if cle not in CHAMPS_INTERNES}


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


def _iso(horodatage: float | None) -> str | None:
    if horodatage is None:
        return None
    return datetime.fromtimestamp(float(horodatage), tz=timezone.utc).isoformat()


def _session_authentifiee(
    index: Any,  # noqa: ANN401
    id_session: str | None,
    jeton: str | None,
    *,
    inactivite_minutes: int | None = None,
) -> dict[str, Any]:
    """Résout une session interne historique, ou lève 401.

    Deux conditions, non négociables : le jeton doit correspondre à l'empreinte
    stockée, ET la session ne doit être ni révoquée, ni expirée, ni inactive.
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

    if not session_valide(index, identifiant_session, jeton, inactivite_minutes=inactivite_minutes):
        raise HTTPException(status_code=401, detail=_DETAIL_SESSION_REFUSEE)
    utilisateur = utilisateur_dune_session(index, identifiant_session)
    if utilisateur is None or not utilisateur.get("actif", False):
        raise HTTPException(status_code=401, detail="Compte inconnu ou désactivé.")
    utilisateur["id_session"] = identifiant_session
    return utilisateur


def _exiger_administrateur(utilisateur: dict[str, Any]) -> dict[str, Any]:
    """403 pour un opérateur identifié (401 est réservé à « non identifié »)."""
    if utilisateur.get("role") != "administrateur":
        raise HTTPException(
            status_code=403,
            detail=(
                "Gestion des comptes réservée au rôle « administrateur » "
                f"(rôle de la session : « {utilisateur.get('role')} »)."
            ),
        )
    return utilisateur


def _session_sid(
    index: Any,  # noqa: ANN401
    sessions: SessionStore,
    sid: str | None,
) -> SessionRecord:
    """Valide un sid Redis et relit le compte en base (rôle/actif à jour)."""
    try:
        record = sessions.valider_session(sid)
    except SessionStoreIndisponible as exc:
        LOGGER.error("Session non vérifiable : %s", exc)
        raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
    if record is None:
        raise HTTPException(status_code=401, detail=_DETAIL_SESSION_REFUSEE)

    if record.mode == MODE_NOMINATIF:
        try:
            profil = profil_utilisateur(index, int(record.user_id))
        except (TypeError, ValueError):
            profil = None
        if profil is None or not profil.get("actif", False):
            # Compte supprimé/désactivé : la session ne doit plus servir.
            try:
                sessions.revoquer_session(record.sid)
            except SessionStoreIndisponible:
                pass
            raise HTTPException(status_code=401, detail="Compte inconnu ou désactivé.")
        # INVALIDATION DURABLE (revue Lot 0a, blocages 1 et 2). La session
        # porte la GÉNÉRATION D'IDENTIFIANTS capturée au moment de la
        # vérification du mot de passe ; on la compare à la génération
        # courante, recalculée depuis la base à chaque requête.
        #
        # C'est une ÉGALITÉ, pas une comparaison d'instants :
        #  * une réinitialisation validée ENTRE la vérification et la création
        #    de la session ne passe plus — la session porte l'ancienne
        #    génération, même si son ``issued_at`` est postérieur ;
        #  * deux répliques sans horloges synchronisées rendent le même verdict,
        #    puisque la valeur comparée vient de PostgreSQL et non d'une horloge
        #    locale ;
        #  * désactivation puis réactivation comprises : ``actif`` entre dans la
        #    génération.
        # Une session émise par une version antérieure ne porte pas de
        # génération : elle est refusée (fail-closed), jamais tolérée.
        generation = profil.get("etat_identifiants")
        if not generation or not record.etat_identifiants or not hmac.compare_digest(
            str(record.etat_identifiants), str(generation)
        ):
            try:
                sessions.revoquer_session(record.sid)
            except SessionStoreIndisponible:
                pass
            raise HTTPException(
                status_code=401,
                detail=(
                    "Session invalidée : les identifiants du compte ont changé "
                    "(mot de passe, réinitialisation ou désactivation). Reconnectez-vous."
                ),
            )
        # La base est l'autorité sur le rôle : un changement de rôle prend effet
        # immédiatement, même si Redis porte encore l'ancien instantané.
        record = replace(
            record,
            identifiant=str(profil["identifiant"]),
            nom=profil.get("nom"),
            role=str(profil["role"]),
        )
    return record


def _utilisateur_requete(
    index: Any,  # noqa: ANN401
    sessions: SessionStore,
    *,
    entete_sid: str | None,
    entete_session: str | None,
    entete_jeton: str | None,
    inactivite_minutes: int,
) -> dict[str, Any]:
    """Identité de l'appelant : sid Redis d'abord, session interne ensuite."""
    if entete_sid:
        record = _session_sid(index, sessions, entete_sid)
        return {
            "id_utilisateur": int(record.user_id) if record.user_id.isdigit() else None,
            "identifiant": record.identifiant,
            "nom": record.nom,
            "role": record.role,
            "mode": record.mode,
            "sid": record.sid,
            "expire_le": _iso(record.expires_at),
            "issued_at": _iso(record.issued_at),
            "last_seen_at": _iso(record.last_seen_at),
            "doit_changer_mot_de_passe": False,
        }
    _exiger_postgres(index)
    utilisateur = _session_authentifiee(
        index, entete_session, entete_jeton, inactivite_minutes=inactivite_minutes
    )
    utilisateur["mode"] = MODE_NOMINATIF
    return utilisateur


def _vue_session(record: SessionRecord, sessions: SessionStore, index: Any) -> dict[str, Any]:  # noqa: ANN401
    """Vue publique d'une session serveur — jamais le sid, jamais un secret."""
    doit_changer = False
    if record.mode == MODE_NOMINATIF:
        profil = profil_utilisateur(index, int(record.user_id))
        doit_changer = bool(profil and profil.get("doit_changer_mot_de_passe"))
    return {
        "ok": True,
        "identifiant": record.identifiant,
        "nom": record.nom,
        "role": record.role,
        "mode": record.mode,
        "id_utilisateur": int(record.user_id) if record.user_id.isdigit() else None,
        "issued_at": _iso(record.issued_at),
        "last_seen_at": _iso(record.last_seen_at),
        "expire_le": _iso(record.expires_at),
        "session_idle_minutes": sessions.idle_minutes,
        "session_hours": sessions.session_hours,
        "doit_changer_mot_de_passe": doit_changer,
    }


def _revoquer_sql_sans_echec(index: Any, id_session: int | None) -> None:  # noqa: ANN401
    if id_session is None:
        return
    try:
        revoquer_session(index, int(id_session))
    except HTTPException as erreur:
        if erreur.status_code != 404:  # déjà absente : rien à faire
            raise
    except Exception as exc:  # noqa: BLE001 - le logout Redis ne doit pas échouer pour l'audit SQL
        LOGGER.warning("Révocation SQL de la session %s impossible : %s", id_session, exc)


def enregistrer_routes_auth(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:  # noqa: ANN401
    """Monte les routes d'authentification nominative et de session Redis."""

    sessions = SessionStore(
        config.redis_url,
        idle_minutes=config.session_idle_minutes,
        session_hours=config.session_hours,
    )
    inactivite = int(config.session_idle_minutes)

    @app.post("/auth/connexion")
    def route_connexion(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        adresse: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
        agent: Annotated[str | None, Header(alias="User-Agent")] = None,
    ) -> dict[str, Any]:
        """Vérifie les identifiants et ouvre une session nominative.

        Réponse : ``{ok, id_utilisateur, identifiant, nom, role, id_session,
        jeton_session, sid, issued_at, last_seen_at, expire_le}``. Le ``sid``
        est le seul identifiant que le navigateur conserve ; il change à CHAQUE
        connexion (anti-fixation). 401 si les identifiants sont faux, 429 après
        cinq échecs en cinq minutes, 503 si Redis ne peut pas enregistrer la
        session (fail-closed).
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
        duree = max(1, int(round(float(getattr(config, "session_hours", 8.0)))))
        # GÉNÉRATION CAPTURÉE À LA VÉRIFICATION (revue Lot 0a, blocage 1) : la
        # valeur vient de ``verifier_identifiants``, pas d'une relecture ici.
        # Si un changement de mot de passe est validé entre les deux, les deux
        # sessions (SQL et Redis) portent l'ANCIENNE génération et seront
        # refusées à la première requête — c'est la fermeture de la course.
        generation = utilisateur.get("etat_identifiants")
        session = ouvrir_session(
            index,
            utilisateur["id_utilisateur"],
            duree_heures=duree,
            user_agent=agent,
            etat_identifiants_capture=generation,
        )
        try:
            record = sessions.creer_session(
                user_id=utilisateur["id_utilisateur"],
                identifiant=str(utilisateur["identifiant"]),
                nom=utilisateur.get("nom"),
                role=str(utilisateur["role"]),
                mode=MODE_NOMINATIF,
                user_agent=agent,
                db_session_id=int(session["id_session"]),
                etat_identifiants=generation,
            )
        except SessionStoreIndisponible as exc:
            # La session SQL ne doit pas survivre à un échec Redis : sinon une
            # ligne « ouverte » existerait sans session vérifiable.
            _revoquer_sql_sans_echec(index, int(session["id_session"]))
            LOGGER.error("Connexion refusée, Redis indisponible : %s", exc)
            raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc

        return {
            "ok": True,
            # LISTE BLANCHE (revue Lot 0a, B7) : `**utilisateur` renvoyait aussi
            # la génération d'identifiants, alors que la documentation affirme
            # qu'elle ne sort jamais. Seuls ces champs sont publics.
            **_champs_publics(utilisateur, CHAMPS_PUBLICS_UTILISATEUR),
            "id_session": session["id_session"],
            # Renvoyé UNE fois : le serveur n'en garde que l'empreinte. Ce
            # jeton n'est PAS placé dans le cookie du navigateur (le cookie ne
            # porte que le sid) ; il reste utile aux scripts internes.
            "jeton_session": session["jeton"],
            "sid": record.sid,
            "issued_at": _iso(record.issued_at),
            "last_seen_at": _iso(record.last_seen_at),
            "expire_le": _iso(record.expires_at),
            "session_idle_minutes": sessions.idle_minutes,
            "session_hours": sessions.session_hours,
        }

    @app.post("/auth/connexion-secours")
    def route_connexion_secours(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        agent: Annotated[str | None, Header(alias="User-Agent")] = None,
    ) -> dict[str, Any]:
        """Ouvre une session serveur pour le compte de secours partagé.

        Le mot de passe partagé est vérifié par le frontend ; cette route
        n'accepte AUCUNE identité fournie par le navigateur : l'identifiant et
        le rôle sont fixés ici (``secours`` / ``administrateur``). Elle n'exige
        pas PostgreSQL : le compte de secours existe même sur une installation
        SQLite, mais sa session est désormais révocable comme les autres.
        """
        del corps  # aucune donnée du client n'est utilisée
        verifier_auth(config, token)
        try:
            record = sessions.creer_session(
                user_id="secours",
                identifiant="secours",
                nom="Compte de secours",
                role="administrateur",
                mode=MODE_SECOURS,
                user_agent=agent,
            )
        except SessionStoreIndisponible as exc:
            LOGGER.error("Connexion de secours refusée, Redis indisponible : %s", exc)
            raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
        return {
            "ok": True,
            "mode": MODE_SECOURS,
            "identifiant": "secours",
            "nom": "Compte de secours",
            "role": "administrateur",
            "sid": record.sid,
            "issued_at": _iso(record.issued_at),
            "last_seen_at": _iso(record.last_seen_at),
            "expire_le": _iso(record.expires_at),
            "session_idle_minutes": sessions.idle_minutes,
            "session_hours": sessions.session_hours,
        }

    @app.post("/auth/deconnexion")
    def route_deconnexion(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Révoque la session présentée. Idempotent.

        Deux formes acceptées : ``{sid}`` (session navigateur Redis) ou
        ``{id_session, jeton_session}`` (session interne historique). Un
        ``sid`` déjà révoqué répond 200 : se déconnecter deux fois n'est pas
        une erreur. Redis indisponible répond 503 : on ne prétend pas avoir
        révoqué ce qu'on n'a pas pu supprimer.
        """
        verifier_auth(config, token)
        sid = corps.get("sid")
        if sid is not None:
            sid_txt = str(sid)
            db_session = None
            try:
                # Retrouve la ligne SQL éventuelle AVANT de supprimer la clé.
                existante = sessions.valider_session(sid_txt)
                if existante is not None:
                    db_session = existante.db_session_id
                revoquee = sessions.revoquer_session(sid_txt)
            except SessionStoreIndisponible as exc:
                LOGGER.error("Déconnexion impossible, Redis indisponible : %s", exc)
                raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
            _revoquer_sql_sans_echec(index, db_session)
            return {"ok": True, "revoquee": bool(revoquee)}

        _exiger_postgres(index)
        id_session = corps.get("id_session")
        jeton = corps.get("jeton_session")
        if not id_session or not jeton:
            raise HTTPException(
                status_code=422,
                detail="« sid » ou « id_session » + « jeton_session » sont requis.",
            )
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

    @app.post("/auth/deconnexion-tout")
    def route_deconnexion_tout(
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
    ) -> dict[str, Any]:
        """« Déconnecter tous mes appareils » : révoque toutes les sessions.

        Fonction interne réutilisable : les changements de mot de passe et les
        désactivations appellent le même ``revoquer_toutes_sessions``.
        """
        verifier_auth(config, token)
        sid = entete_sid or (corps or {}).get("sid")
        if not sid:
            raise HTTPException(
                status_code=401,
                detail="En-tête X-SEAMTECH-SESSION-SID requis pour révoquer toutes les sessions.",
            )
        record = _session_sid(index, sessions, str(sid))
        # MÊME ORDRE QUE POUR LE MOT DE PASSE (revue Lot 0a, blocage 1) : les
        # sessions historiques ``session_ui`` sont révoquées AVANT la tentative
        # Redis. Un échec de celle-ci ne doit pas laisser ces sessions
        # utilisables alors que l'utilisateur a demandé « tous mes appareils ».
        sql_revoquees = 0
        if record.mode == MODE_NOMINATIF and record.user_id.isdigit():
            sql_revoquees = revoquer_sessions_utilisateur(index, int(record.user_id))
        try:
            revoquees = sessions.revoquer_toutes_sessions(record.user_id)
        except SessionStoreIndisponible as exc:
            LOGGER.error("Révocation globale impossible, Redis indisponible : %s", exc)
            raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
        return {
            "ok": True,
            "sessions_revoquees": revoquees,
            "sessions_sql_revoquees": sql_revoquees,
        }

    @app.get("/auth/session")
    def route_session(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """Qui est connecté ? Jamais d'empreinte, de sel, de jeton ni de sid."""
        verifier_auth(config, token)
        if entete_sid:
            record = _session_sid(index, sessions, entete_sid)
            return _vue_session(record, sessions, index)
        _exiger_postgres(index)
        utilisateur = _session_authentifiee(
            index, entete_session, entete_jeton, inactivite_minutes=inactivite
        )
        return {
            "ok": True,
            **_sans_champs_internes(utilisateur),
            "mode": MODE_NOMINATIF,
            "session_idle_minutes": sessions.idle_minutes,
            "session_hours": sessions.session_hours,
        }

    @app.post("/auth/mot-de-passe")
    def route_mot_de_passe(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        """Changement par l'intéressé, puis révocation de TOUTES ses sessions."""
        verifier_auth(config, token)
        utilisateur = _utilisateur_requete(
            index,
            sessions,
            entete_sid=entete_sid,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
            inactivite_minutes=inactivite,
        )
        if utilisateur.get("id_utilisateur") is None:
            raise HTTPException(status_code=400, detail="Le compte de secours n'a pas de mot de passe nominatif.")
        resultat = changer_mot_de_passe(
            index,
            int(utilisateur["id_utilisateur"]),
            str(corps.get("mot_de_passe_actuel") or ""),
            str(corps.get("nouveau_mot_de_passe") or ""),
        )
        # ORDRE VOLONTAIRE (revue Lot 0a, blocages 1 et 2) : la mutation est
        # COMMITÉE — changement d'empreinte ET révocation des sessions
        # historiques ``session_ui`` dans la MÊME transaction — AVANT toute
        # tentative Redis. Il n'y a donc plus de seconde transaction
        # d'invalidation qui pourrait échouer seule : ``changer_mot_de_passe``
        # rend le compte de lignes qu'elle a réellement révoquées, et un échec
        # de la révocation SQL annule aussi le changement de mot de passe.
        sql_revoquees = int(resultat.pop("sessions_revoquees", 0))
        # Un mot de passe changé ferme aussi les sessions navigateur (Redis).
        # Si Redis échoue ici, la mutation reste valide et la génération
        # d'identifiants a changé : les sessions Redis d'avant répondront 401.
        try:
            revoquees = sessions.revoquer_toutes_sessions(utilisateur["id_utilisateur"])
        except SessionStoreIndisponible as exc:
            LOGGER.error("Mot de passe changé mais révocation Redis impossible : %s", exc)
            raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
        return {
            "ok": True,
            **resultat,
            "sessions_revoquees": revoquees,
            "sessions_sql_revoquees": sql_revoquees,
        }

    # -----------------------------------------------------------------------
    # Gestion des comptes — administrateur uniquement
    # -----------------------------------------------------------------------

    def _admin(
        token: str | None,
        entete_sid: str | None,
        entete_session: str | None,
        entete_jeton: str | None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        utilisateur = _utilisateur_requete(
            index,
            sessions,
            entete_sid=entete_sid,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
            inactivite_minutes=inactivite,
        )
        return _exiger_administrateur(utilisateur)

    @app.get("/auth/utilisateurs")
    def route_lister_utilisateurs(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> list[dict[str, Any]]:
        _admin(token, entete_sid, entete_session, entete_jeton)
        _exiger_postgres(index)
        return lister_utilisateurs(index)

    @app.post("/auth/utilisateurs")
    def route_creer_utilisateur(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        administrateur = _admin(token, entete_sid, entete_session, entete_jeton)
        _exiger_postgres(index)
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
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        _admin(token, entete_sid, entete_session, entete_jeton)
        _exiger_postgres(index)
        cible = id_utilisateur_par_identifiant(index, identifiant)
        mutation = desactiver_utilisateur(index, identifiant)
        # La révocation des sessions historiques est faite DANS la transaction
        # de la désactivation : le compteur vient de la mutation.
        sql_revoquees = int(mutation.get("sessions_revoquees", 0))
        resultat = {"ok": True, **mutation}
        redis_revoquees = 0
        if cible is not None:
            try:
                redis_revoquees = sessions.revoquer_toutes_sessions(cible)
            except SessionStoreIndisponible as exc:
                LOGGER.error("Compte désactivé mais révocation Redis impossible : %s", exc)
                raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
        return {
            **resultat,
            "sessions_redis_revoquees": redis_revoquees,
            "sessions_sql_revoquees": sql_revoquees,
        }

    @app.post("/auth/utilisateurs/{identifiant}/mot-de-passe")
    def route_reinitialiser_mot_de_passe(
        identifiant: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_sid: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-SID")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        _admin(token, entete_sid, entete_session, entete_jeton)
        _exiger_postgres(index)
        if not corps.get("mot_de_passe"):
            raise HTTPException(status_code=422, detail="« mot_de_passe » requis.")
        cible = id_utilisateur_par_identifiant(index, identifiant)
        mutation = reinitialiser_mot_de_passe(index, identifiant, str(corps["mot_de_passe"]))
        # La révocation des sessions historiques est faite DANS la transaction
        # de la réinitialisation : le compteur vient de la mutation.
        sql_revoquees = int(mutation.pop("sessions_revoquees", 0))
        resultat = {"ok": True, **mutation}
        redis_revoquees = 0
        if cible is not None:
            try:
                redis_revoquees = sessions.revoquer_toutes_sessions(cible)
            except SessionStoreIndisponible as exc:
                LOGGER.error("Mot de passe réinitialisé mais révocation Redis impossible : %s", exc)
                raise HTTPException(status_code=503, detail=_DETAIL_REDIS) from exc
        return {
            **resultat,
            "sessions_redis_revoquees": redis_revoquees,
            "sessions_sql_revoquees": sql_revoquees,
        }
