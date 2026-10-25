"""Gestion des comptes nominatifs et des sessions — Lot L.2.

Ce module est la seule autorité sur « qui est qui » et « qui a validé quoi ».
Il s'appuie sur deux tables qui existaient DÉJÀ pour l'attribution :

- ``utilisateur`` (006) — jusqu'ici jamais alimentée ;
- ``fiche_validation.id_utilisateur`` et ``fiche_champ_extrait.corrige_par``
  (006) — les colonnes d'attribution, jusqu'ici toujours vides ;

plus la table ``session_ui`` ajoutée par la migration 016.

FRONTIÈRE DE CONFIANCE (à ne pas déplacer, elle est testée) : le navigateur ne
détient JAMAIS ``SEAMTECH_AUTH_TOKEN``. Les en-têtes ``X-SEAMTECH-UTILISATEUR``
et ``X-SEAMTECH-ROLE`` ne sont posés QUE par le proxy serveur Next.js, à partir
du cookie signé. Côté Python, ils ne sont honorés QUE si ``X-SEAMTECH-TOKEN``
est valide sur la même requête : une requête qui porte l'attribution sans le
jeton de service répond 401 (``resoudre_attribution``).

Rôles Lot 1 : ``admin`` (technique et comptes, jamais décideur métier),
``lecture`` (lecture seule), ``utilisateur`` (demandes) et ``approbateur``
(correction et décision). Toute autre valeur est refusée à la création ; les
anciens libellés ne sont convertis que par la migration 027.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from ..audit import record_audit_event
from ..sessions_redis import SessionsRedisIndisponible
from .securite import (
    empreinte_jeton,
    empreinte_mot_de_passe,
    nouveau_jeton,
    verifier_mot_de_passe,
)

LOGGER = logging.getLogger("seamtech_search.comptes")

if TYPE_CHECKING:  # pragma: no cover - typage seul, aucun cycle d'import
    from ..sessions_redis import RegistreSessionsRedis

ROLE_ADMIN = "admin"
ROLE_LECTURE = "lecture"
ROLE_UTILISATEUR = "utilisateur"
ROLE_APPROBATEUR = "approbateur"
ROLES = (ROLE_ADMIN, ROLE_LECTURE, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
# Défaut fermé : omettre un rôle ne doit jamais créer un compte capable
# d'écrire. L'élévation est toujours une décision explicite d'un admin.
ROLE_DEFAUT = ROLE_LECTURE
# Lot 0a (plan directeur v2 §3) : 8 h absolu + 30 min d'inactivité, contrôlés
# CÔTÉ SERVEUR. Les routes passent les valeurs de AppConfig ; ces constantes
# ne sont que le défaut des appels directs (tests, CLI).
DUREE_SESSION_DEFAUT_H = 8
INACTIVITE_SESSION_DEFAUT_MIN = 30
# Verrouillage des connexions : 5 échecs sur un identifiant en 5 minutes ⇒ 429
# pendant 5 minutes. Demandé « souhaitable » par la mission, et donc implémenté.
#
# Le compteur vit dans la table `audit_log`, PAS dans une table dédiée ni en
# mémoire :
#   * en mémoire → un redémarrage du process remettrait le compteur à zéro ;
#   * table dédiée → une 33e table métier, alors que la mission fixe 32 tables
#     après L.2 (et que les 3 tables d'attribution existent déjà depuis la 006).
# Une connexion refusée EST un événement d'audit : c'est sa place, et ça la rend
# consultable après coup (`SELECT … FROM audit_log WHERE action = 'connexion_refusee'`).
# Aucune purge n'est faite : on ne supprime jamais automatiquement de données.
ACTION_ECHEC = "connexion_refusee"
MAX_ECHECS = 5
FENETRE_ECHECS_MIN = 5
VERROU_MIN = 5

# Rôle du compte de secours partagé (SEAMTECH_UI_PASSWORD). Il reste admin
# technique pour ouvrir une session et créer les comptes nominatifs quand
# aucun n'existe encore — sinon on se verrouille dehors. Lot 1 lui retire en
# revanche toute capacité métier : il ne corrige, ne valide, ne télécharge
# et ne décide aucune demande.
IDENTIFIANT_SECOURS = "secours"
ROLE_SECOURS = ROLE_ADMIN


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401 - SearchIndex réel
    if not getattr(index, "is_postgres", False):
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(
            status_code=503,
            detail=(
                "Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche "
                "§17.1 du plan v3.0) : les tables utilisateur/session_ui n'existent pas côté SQLite."
            ),
        )


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Comptes
# ---------------------------------------------------------------------------


def creer_utilisateur(
    index: Any,  # noqa: ANN401
    identifiant: str,
    nom: str,
    mot_de_passe: str,
    role: str = ROLE_DEFAUT,
    doit_changer_mot_de_passe: bool = True,
) -> dict[str, Any]:
    """Crée un compte nominatif. Refuse les doublons et les rôles inconnus.

    ``doit_changer_mot_de_passe`` vaut ``True`` par défaut : un mot de passe
    choisi par un tiers (le CLI, à la création) est provisoire par construction.
    """
    _exiger_postgres(index)
    identifiant = (identifiant or "").strip()
    if not identifiant:
        raise HTTPException(status_code=422, detail="Identifiant obligatoire.")
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"Rôle invalide : « {role} » (attendu : {', '.join(ROLES)}).")
    if not mot_de_passe:
        raise HTTPException(status_code=422, detail="Mot de passe obligatoire (lu sur STDIN, jamais en argument).")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_utilisateur FROM utilisateur WHERE identifiant = %s", (identifiant,))
            if cursor.fetchone() is not None:
                raise HTTPException(status_code=409, detail=f"Un compte « {identifiant} » existe déjà.")
            cursor.execute(
                "INSERT INTO utilisateur (identifiant, nom, role, actif, empreinte_mot_de_passe, "
                "mot_de_passe_modifie_le, doit_changer_mot_de_passe) "
                "VALUES (%s, %s, %s, true, %s, now(), %s) RETURNING id_utilisateur",
                (identifiant, nom, role, empreinte_mot_de_passe(mot_de_passe), doit_changer_mot_de_passe),
            )
            id_utilisateur = int(cursor.fetchone()[0])
    LOGGER.info("Compte « %s » créé (rôle %s).", identifiant, role)
    return {
        "id_utilisateur": id_utilisateur,
        "identifiant": identifiant,
        "nom": nom,
        "role": role,
        "doit_changer_mot_de_passe": doit_changer_mot_de_passe,
    }


def lister_utilisateurs(index: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Tous les comptes. L'empreinte et le sel ne sortent JAMAIS d'ici."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT id_utilisateur, identifiant, nom, role, actif, created_at, derniere_connexion, "
                "doit_changer_mot_de_passe, (empreinte_mot_de_passe IS NOT NULL) "
                "FROM utilisateur ORDER BY role, identifiant"
            )
            return [
                {
                    "id_utilisateur": int(ligne[0]),
                    "identifiant": str(ligne[1]),
                    "nom": ligne[2],
                    "role": str(ligne[3]),
                    "actif": bool(ligne[4]),
                    "cree_le": ligne[5].isoformat() if ligne[5] else None,
                    "derniere_connexion": ligne[6].isoformat() if ligne[6] else None,
                    "doit_changer_mot_de_passe": bool(ligne[7]),
                    # Un booléen « un mot de passe est défini », jamais sa valeur.
                    "mot_de_passe_defini": bool(ligne[8]),
                }
                for ligne in cursor.fetchall()
            ]


def changer_role_utilisateur(
    index: Any,  # noqa: ANN401
    identifiant: str,
    nouveau_role: str,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Change le rôle d'un compte et révoque immédiatement ses sessions.

    Le compte est verrouillé (``FOR UPDATE``), le rôle et la révocation
    PostgreSQL sont commis dans la même transaction, puis le miroir Redis est
    révoqué au mieux. Même si Redis est momentanément injoignable, la ligne
    PostgreSQL révoquée reste l'autorité et les gardes échouent fermées.
    Redemander le rôle actuel est un no-op explicite : aucune session n'est
    coupée pour une opération qui n'a rien changé.
    """
    _exiger_postgres(index)
    identifiant = (identifiant or "").strip()
    role = (nouveau_role or "").strip().lower()
    if not identifiant:
        raise HTTPException(status_code=422, detail="Identifiant obligatoire.")
    if role not in ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Rôle invalide : « {nouveau_role} » (attendu : {', '.join(ROLES)}).",
        )

    revoquees = 0
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT id_utilisateur, role FROM utilisateur WHERE identifiant = %s FOR UPDATE",
                (identifiant,),
            )
            ligne = cursor.fetchone()
            if ligne is None:
                raise HTTPException(status_code=404, detail=f"Compte « {identifiant} » inconnu.")
            id_utilisateur = int(ligne[0])
            ancien_role = str(ligne[1])
            if ancien_role != role:
                cursor.execute(
                    "UPDATE utilisateur SET role = %s WHERE id_utilisateur = %s",
                    (role, id_utilisateur),
                )
                cursor.execute(
                    "UPDATE session_ui SET revoque_le = now() "
                    "WHERE id_utilisateur = %s AND revoque_le IS NULL",
                    (id_utilisateur,),
                )
                revoquees = int(cursor.rowcount)

    if ancien_role != role and registre is not None:
        try:
            registre.revoquer_utilisateur(id_utilisateur)
        except SessionsRedisIndisponible:
            LOGGER.warning(
                "Redis injoignable pendant la révocation post-changement de rôle "
                "(sessions déjà révoquées en base)."
            )
    LOGGER.info(
        "Rôle du compte « %s » : %s -> %s, %d session(s) révoquée(s).",
        identifiant,
        ancien_role,
        role,
        revoquees,
    )
    return {
        "id_utilisateur": id_utilisateur,
        "identifiant": identifiant,
        "ancien_role": ancien_role,
        "nouveau_role": role,
        "role": role,
        "sessions_revoquees": revoquees,
    }


def desactiver_utilisateur(
    index: Any,  # noqa: ANN401
    identifiant: str,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Passe ``actif=false`` ET révoque les sessions ouvertes.

    Désactiver sans révoquer laisserait un cookie valide continuer à travailler :
    la désactivation doit être effective IMMÉDIATEMENT, pas à l'expiration.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE utilisateur SET actif = false WHERE identifiant = %s RETURNING id_utilisateur",
                ((identifiant or "").strip(),),
            )
            ligne = cursor.fetchone()
            if ligne is None:
                raise HTTPException(status_code=404, detail=f"Compte « {identifiant} » inconnu.")
            id_utilisateur = int(ligne[0])
            cursor.execute(
                "UPDATE session_ui SET revoque_le = now() WHERE id_utilisateur = %s AND revoque_le IS NULL",
                (id_utilisateur,),
            )
            revoquees = int(cursor.rowcount)
    if registre is not None:
        try:
            registre.revoquer_utilisateur(id_utilisateur)
        except SessionsRedisIndisponible:
            LOGGER.warning("Redis injoignable pendant la révocation post-désactivation (base déjà révoquée).")
    # Pas de compte de secours désactivable : ce serait le seul moyen de se
    # verrouiller définitivement dehors (il n'a même pas de ligne en base).
    LOGGER.info("Compte « %s » désactivé, %d session(s) révoquée(s).", identifiant, revoquees)
    return {"identifiant": identifiant, "actif": False, "sessions_revoquees": revoquees}


def reinitialiser_mot_de_passe(
    index: Any,  # noqa: ANN401
    identifiant: str,
    mot_de_passe: str,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Remplace l'empreinte et force le changement à la prochaine connexion."""
    _exiger_postgres(index)
    if not mot_de_passe:
        raise HTTPException(status_code=422, detail="Mot de passe obligatoire (lu sur STDIN).")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE utilisateur SET empreinte_mot_de_passe = %s, mot_de_passe_modifie_le = now(), "
                "doit_changer_mot_de_passe = true WHERE identifiant = %s RETURNING id_utilisateur",
                (empreinte_mot_de_passe(mot_de_passe), (identifiant or "").strip()),
            )
            ligne = cursor.fetchone()
            if ligne is None:
                raise HTTPException(status_code=404, detail=f"Compte « {identifiant} » inconnu.")
            id_utilisateur = int(ligne[0])
            cursor.execute(
                "UPDATE session_ui SET revoque_le = now() WHERE id_utilisateur = %s AND revoque_le IS NULL",
                (id_utilisateur,),
            )
    if registre is not None:
        try:
            registre.revoquer_utilisateur(id_utilisateur)
        except SessionsRedisIndisponible:
            LOGGER.warning("Redis injoignable pendant la révocation post-réinitialisation (base déjà révoquée).")
    LOGGER.info("Mot de passe de « %s » réinitialisé (changement forcé).", identifiant)
    return {"identifiant": identifiant, "doit_changer_mot_de_passe": True}


# ---------------------------------------------------------------------------
# Verrouillage des connexions (compteur en base, dans audit_log)
# ---------------------------------------------------------------------------


def _derniere_reussite(cursor: Any, identifiant: str) -> Any:  # noqa: ANN401
    """Instant de la dernière connexion RÉUSSIE, pour ne compter que les échecs qui la suivent."""
    cursor.execute(
        "SELECT max(timestamp) FROM audit_log WHERE actor = %s AND action = %s",
        (identifiant, "connexion"),
    )
    return cursor.fetchone()[0]


def _echecs_recents(cursor: Any, identifiant: str) -> list[Any]:  # noqa: ANN401
    """Horodatages des échecs récents, du plus récent au plus ancien.

    Un échec ANTÉRIEUR à la dernière connexion réussie n'est pas compté : sans
    cela, cinq fautes de frappe étalées sur la vie du compte finiraient par
    verrouiller un utilisateur légitime qui se connecte tous les jours.
    """
    apres = _derniere_reussite(cursor, identifiant)
    cursor.execute(
        "SELECT timestamp FROM audit_log WHERE actor = %s AND action = %s "
        "AND timestamp > now() - make_interval(mins => %s) AND (%s IS NULL OR timestamp > %s) "
        "ORDER BY timestamp DESC LIMIT %s",
        (identifiant, ACTION_ECHEC, FENETRE_ECHECS_MIN, apres, apres, MAX_ECHECS),
    )
    return [ligne[0] for ligne in cursor.fetchall()]


def verrou_actif(index: Any, identifiant: str) -> int:  # noqa: ANN401
    """Secondes de verrouillage restantes, ou 0.

    Le verrou est une CONSÉQUENCE du compteur d'échecs, pas un état séparé :
    à partir de ``MAX_ECHECS`` échecs dans la fenêtre, il court sur
    ``VERROU_MIN`` minutes à compter du DERNIER échec.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            echecs = _echecs_recents(cursor, identifiant)
    if len(echecs) < MAX_ECHECS:
        return 0
    fin = echecs[0] + timedelta(minutes=VERROU_MIN)
    return max(0, int((fin - _maintenant()).total_seconds()))


def _noter_tentative(index: Any, identifiant: str, adresse: str | None) -> None:  # noqa: ANN401
    """Journalise une connexion refusée (best-effort, via l'audit existant)."""
    record_audit_event(
        index,
        action=ACTION_ECHEC,
        actor=identifiant,
        resource="/auth/connexion",
        status="401",
        details={"adresse": adresse} if adresse else {},
    )


def _noter_connexion(index: Any, identifiant: str, role: str, adresse: str | None) -> None:  # noqa: ANN401
    """Journalise une connexion réussie (sert aussi de remise à zéro du compteur)."""
    record_audit_event(
        index,
        action="connexion",
        actor=identifiant,
        resource="/auth/connexion",
        status="200",
        details={"role": role, "adresse": adresse} if adresse else {"role": role},
    )


# ---------------------------------------------------------------------------
# Connexion / sessions
# ---------------------------------------------------------------------------


def verifier_identifiants(
    index: Any,  # noqa: ANN401
    identifiant: str,
    mot_de_passe: str,
    adresse: str | None = None,
) -> dict[str, Any]:
    """Vérifie un couple identifiant/mot de passe.

    Rend ``{id_utilisateur, identifiant, nom, role, doit_changer_mot_de_passe}``
    ou lève 401 (identifiants faux / compte inconnu / compte désactivé) ou 429
    (trop d'échecs récents). Un compte INCONNU répond exactement comme un mot de
    passe faux — on ne révèle pas quels identifiants existent.
    """
    _exiger_postgres(index)
    identifiant = (identifiant or "").strip()
    restant = verrou_actif(index, identifiant)
    if restant > 0:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Trop de tentatives échouées pour « {identifiant} » : nouvelle tentative dans "
                f"{restant} s (verrou en base, il survit à un redémarrage)."
            ),
            headers={"Retry-After": str(restant)},
        )
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT id_utilisateur, nom, role, actif, empreinte_mot_de_passe, doit_changer_mot_de_passe "
                "FROM utilisateur WHERE identifiant = %s",
                (identifiant,),
            )
            ligne = cursor.fetchone()
            if ligne is None or not bool(ligne[3]) or not verifier_mot_de_passe(mot_de_passe, ligne[4]):
                _noter_tentative(index, identifiant, adresse)
                LOGGER.warning("Connexion refusée pour « %s » (échec journalisé dans audit_log).", identifiant)
                raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")
            id_utilisateur = int(ligne[0])
            cursor.execute("UPDATE utilisateur SET derniere_connexion = now() WHERE id_utilisateur = %s", (id_utilisateur,))
    _noter_connexion(index, identifiant, str(ligne[2]), adresse)
    LOGGER.info("Connexion de « %s » (rôle %s).", identifiant, ligne[2])
    return {
        "id_utilisateur": id_utilisateur,
        "identifiant": identifiant,
        "nom": ligne[1],
        "role": str(ligne[2]),
        "doit_changer_mot_de_passe": bool(ligne[5]),
    }


def ouvrir_session(
    index: Any,  # noqa: ANN401
    id_utilisateur: int,
    duree_heures: int = DUREE_SESSION_DEFAUT_H,
    user_agent: str | None = None,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Ouvre une session nominative et rend son jeton EN CLAIR — une seule fois.

    Le jeton n'est pas stocké : seule son empreinte SHA-256 l'est. Il ne peut
    donc pas être relu depuis la base, seulement présenté de nouveau par le
    client — c'est le principe même d'un jeton de session.

    Lot 0a : le jeton est régénéré à CHAQUE connexion (anti-fixation) et, si
    un registre Redis est fourni, la session y est publiée immédiatement.
    Redis exigé mais injoignable ⇒ la ligne en base est révoquée et la
    connexion échoue en 503 (fail-closed) : aucune session invérifiable.
    """
    _exiger_postgres(index)
    jeton = nouveau_jeton()
    maintenant = _maintenant()
    expire_le = maintenant + timedelta(hours=max(1, int(duree_heures)))
    empreinte = empreinte_jeton(jeton)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO session_ui (id_utilisateur, empreinte_jeton, expire_le, user_agent) "
                "VALUES (%s, %s, %s, %s) RETURNING id_session",
                (int(id_utilisateur), empreinte, expire_le, user_agent),
            )
            id_session = int(cursor.fetchone()[0])
    if registre is not None:
        try:
            registre.enregistrer(
                id_session,
                int(id_utilisateur),
                empreinte,
                maintenant.timestamp(),
                expire_le.timestamp(),
            )
        except SessionsRedisIndisponible as erreur:
            # Fail-closed : on ne laisse pas vivre une session que le registre
            # n'a pas publiée (elle serait acceptée sans état serveur).
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    cursor.execute(
                        "UPDATE session_ui SET revoque_le = now() WHERE id_session = %s",
                        (id_session,),
                    )
            raise HTTPException(
                status_code=503,
                detail="Service de session indisponible (Redis) : connexion refusée, réessayez.",
            ) from erreur
    return {"id_session": id_session, "jeton": jeton, "expire_le": expire_le.isoformat()}


def session_valide(
    index: Any,  # noqa: ANN401
    id_session: int,
    jeton: str,
    idle_minutes: int = INACTIVITE_SESSION_DEFAUT_MIN,
    registre: RegistreSessionsRedis | None = None,
) -> bool:
    """La session est-elle encore valable ? (non révoquée, non expirée, jeton conforme)

    C'est CE contrôle qui rend une déconnexion effective : un cookie signé
    parfaitement valide ne vaut plus rien dès que ``revoque_le`` est posé.

    Lot 0a — contrôles serveur additionnels, dans cet ordre :
    1. expiration ABSOLUE (``expire_le``) ;
    2. timeout d'INACTIVITÉ : ``derniere_activite`` plus vieille que
       ``idle_minutes`` ⇒ la session est révoquée en base et refusée ;
    3. registre Redis (si fourni) : clé absente ⇒ refus (logout, logout-all
       ou session antérieure au Lot 0a — reconnexion exigée) ; Redis
       injoignable ⇒ 503 fail-closed, jamais d'acceptation silencieuse.
    """
    _exiger_postgres(index)
    maintenant = _maintenant()
    limite_inactivite = maintenant - timedelta(minutes=max(1, int(idle_minutes)))
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT revoque_le, expire_le, empreinte_jeton, derniere_activite, id_utilisateur "
                "FROM session_ui WHERE id_session = %s",
                (int(id_session),),
            )
            ligne = cursor.fetchone()
            if ligne is None:
                return False
            if ligne[0] is not None or ligne[1] is None or ligne[1] <= maintenant:
                return False
            if ligne[2] != empreinte_jeton(jeton or ""):
                return False
            derniere_activite = ligne[3]
            if derniere_activite is not None and derniere_activite <= limite_inactivite:
                # Inactivité dépassée : on révoque pour que l'état soit visible
                # et définitif, pas seulement calculé à la volée.
                cursor.execute(
                    "UPDATE session_ui SET revoque_le = now() WHERE id_session = %s AND revoque_le IS NULL",
                    (int(id_session),),
                )
                if registre is not None:
                    try:
                        registre.revoquer(int(id_session), int(ligne[4]))
                    except SessionsRedisIndisponible:
                        LOGGER.warning("Redis injoignable pendant la révocation pour inactivité (base révoquée).")
                return False
            if registre is not None:
                try:
                    etat = registre.etat(int(id_session))
                except SessionsRedisIndisponible as erreur:
                    raise HTTPException(
                        status_code=503,
                        detail="Service de session indisponible (Redis) : requête refusée, réessayez.",
                    ) from erreur
                if etat is None or etat["empreinte_jeton"] != ligne[2]:
                    return False
                if etat["derniere_activite"] <= limite_inactivite.timestamp():
                    cursor.execute(
                        "UPDATE session_ui SET revoque_le = now() WHERE id_session = %s AND revoque_le IS NULL",
                        (int(id_session),),
                    )
                    try:
                        registre.revoquer(int(id_session), int(ligne[4]))
                    except SessionsRedisIndisponible:
                        LOGGER.warning("Redis injoignable pendant la révocation pour inactivité (base révoquée).")
                    return False
                try:
                    registre.toucher(int(id_session), maintenant.timestamp())
                except SessionsRedisIndisponible as erreur:
                    raise HTTPException(
                        status_code=503,
                        detail="Service de session indisponible (Redis) : requête refusée, réessayez.",
                    ) from erreur
            cursor.execute(
                "UPDATE session_ui SET derniere_activite = now() WHERE id_session = %s", (int(id_session),)
            )
    return True


def correspondance_jeton(index: Any, id_session: int, jeton: str) -> bool:  # noqa: ANN401
    """Le jeton présenté est-il bien celui de cette session ? (état ignoré)

    Sert à la déconnexion : on refuse de révoquer une session avec un simple
    identifiant devinable (``id_session`` est un BIGSERIAL, donc public) ; il
    faut présenter le jeton, exactement comme pour s'en servir.
    """
    _exiger_postgres(index)
    if not jeton:
        return False
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT empreinte_jeton FROM session_ui WHERE id_session = %s", (int(id_session),))
            ligne = cursor.fetchone()
    return ligne is not None and ligne[0] == empreinte_jeton(jeton)


def utilisateur_dune_session(index: Any, id_session: int) -> dict[str, Any] | None:  # noqa: ANN401
    """Compte porté par une session + expiration. Ne sort JAMAIS d'empreinte."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT u.id_utilisateur, u.identifiant, u.nom, u.role, u.actif, s.expire_le, "
                "s.derniere_activite, u.doit_changer_mot_de_passe "
                "FROM session_ui s JOIN utilisateur u ON u.id_utilisateur = s.id_utilisateur "
                "WHERE s.id_session = %s",
                (int(id_session),),
            )
            ligne = cursor.fetchone()
    if ligne is None:
        return None
    return {
        "id_utilisateur": int(ligne[0]),
        "identifiant": str(ligne[1]),
        "nom": ligne[2],
        "role": str(ligne[3]),
        "actif": bool(ligne[4]),
        "expire_le": ligne[5].isoformat() if ligne[5] else None,
        "derniere_activite": ligne[6].isoformat() if ligne[6] else None,
        "doit_changer_mot_de_passe": bool(ligne[7]),
    }


def changer_mot_de_passe(
    index: Any,  # noqa: ANN401
    id_utilisateur: int,
    mot_de_passe_actuel: str,
    nouveau_mot_de_passe: str,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Change le mot de passe d'un compte après vérification de l'ancien.

    ``doit_changer_mot_de_passe`` retombe à ``false`` : c'est le seul chemin qui
    l'éteint, et il exige de connaître le mot de passe provisoire.

    Lot 0a : toutes les sessions du compte sont révoquées dans la même
    transaction (y compris celle qui vient de changer le mot de passe) —
    reconnexion exigée partout, aucun ancien cookie ne survit.
    """
    _exiger_postgres(index)
    if not nouveau_mot_de_passe:
        raise HTTPException(status_code=422, detail="Nouveau mot de passe obligatoire.")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT identifiant, empreinte_mot_de_passe, actif FROM utilisateur WHERE id_utilisateur = %s",
                (int(id_utilisateur),),
            )
            ligne = cursor.fetchone()
            if ligne is None or not bool(ligne[2]):
                raise HTTPException(status_code=401, detail="Compte inconnu ou désactivé.")
            if not verifier_mot_de_passe(mot_de_passe_actuel, ligne[1]):
                raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
            cursor.execute(
                "UPDATE utilisateur SET empreinte_mot_de_passe = %s, mot_de_passe_modifie_le = now(), "
                "doit_changer_mot_de_passe = false WHERE id_utilisateur = %s",
                (empreinte_mot_de_passe(nouveau_mot_de_passe), int(id_utilisateur)),
            )
            cursor.execute(
                "UPDATE session_ui SET revoque_le = now() WHERE id_utilisateur = %s AND revoque_le IS NULL",
                (int(id_utilisateur),),
            )
            revoquees = int(cursor.rowcount)
    if registre is not None:
        try:
            registre.revoquer_utilisateur(int(id_utilisateur))
        except SessionsRedisIndisponible:
            LOGGER.warning("Redis injoignable pendant la révocation post-changement (base déjà révoquée).")
    LOGGER.info("Mot de passe changé par « %s » (%d session(s) révoquée(s)).", ligne[0], revoquees)
    return {"identifiant": str(ligne[0]), "doit_changer_mot_de_passe": False, "sessions_revoquees": revoquees}


def revoquer_session(
    index: Any,  # noqa: ANN401
    id_session: int,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Pose ``revoque_le`` + supprime la clé Redis (idempotent).

    La base fait foi : si la suppression Redis échoue, la session reste
    refusée par ``session_valide`` (qui lit ``revoque_le`` avant Redis, et
    qui échoue en 503 si Redis exigé est injoignable) — l'échec Redis est
    donc journalisé, jamais masquant une révocation déjà effective.
    """
    _exiger_postgres(index)
    id_utilisateur: int | None = None
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE session_ui SET revoque_le = now() WHERE id_session = %s AND revoque_le IS NULL "
                "RETURNING id_utilisateur",
                (int(id_session),),
            )
            ligne = cursor.fetchone()
            if ligne is None:
                cursor.execute(
                    "SELECT id_utilisateur FROM session_ui WHERE id_session = %s", (int(id_session),)
                )
                ligne = cursor.fetchone()
                if ligne is None:
                    raise HTTPException(status_code=404, detail=f"Session {id_session} inconnue.")
                id_utilisateur = int(ligne[0])
                if registre is not None:
                    try:
                        registre.revoquer(int(id_session), id_utilisateur)
                    except SessionsRedisIndisponible:
                        LOGGER.warning("Redis injoignable pendant la révocation (base déjà révoquée).")
                return {"id_session": int(id_session), "revoquee": False, "deja_revoquee": True}
            id_utilisateur = int(ligne[0])
    if registre is not None:
        try:
            registre.revoquer(int(id_session), id_utilisateur)
        except SessionsRedisIndisponible:
            LOGGER.warning("Redis injoignable pendant la révocation (base déjà révoquée).")
    LOGGER.info("Session %d révoquée.", int(id_session))
    return {"id_session": int(id_session), "revoquee": True, "deja_revoquee": False}


def revoquer_sessions_utilisateur(
    index: Any,  # noqa: ANN401
    id_utilisateur: int,
    registre: RegistreSessionsRedis | None = None,
) -> dict[str, Any]:
    """Révoque TOUTES les sessions vivantes d'un compte (Lot 0a).

    ``POST /auth/logout-all`` (« Déconnecter tous mes appareils »), mais
    aussi changement de mot de passe, réinitialisation, désactivation et —
    au Lot 1 — changement de rôle : aucun ancien cookie ne survit.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE session_ui SET revoque_le = now() "
                "WHERE id_utilisateur = %s AND revoque_le IS NULL RETURNING id_session",
                (int(id_utilisateur),),
            )
            ids = sorted(int(ligne[0]) for ligne in cursor.fetchall())
    if registre is not None:
        try:
            registre.revoquer_utilisateur(int(id_utilisateur))
        except SessionsRedisIndisponible:
            LOGGER.warning("Redis injoignable pendant la révocation globale (base déjà révoquée).")
    LOGGER.info("%d session(s) révoquée(s) pour l'utilisateur %d.", len(ids), int(id_utilisateur))
    return {"id_utilisateur": int(id_utilisateur), "sessions_revoquees": len(ids), "id_sessions": ids}


def sessions_dun_utilisateur(index: Any, identifiant: str) -> list[dict[str, Any]]:  # noqa: ANN401
    """Sessions d'un compte. Aucune empreinte de jeton ne sort."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT s.id_session, s.cree_le, s.expire_le, s.derniere_activite, s.revoque_le, s.user_agent "
                "FROM session_ui s JOIN utilisateur u ON u.id_utilisateur = s.id_utilisateur "
                "WHERE u.identifiant = %s ORDER BY s.cree_le DESC",
                ((identifiant or "").strip(),),
            )
            return [
                {
                    "id_session": int(ligne[0]),
                    "cree_le": ligne[1].isoformat() if ligne[1] else None,
                    "expire_le": ligne[2].isoformat() if ligne[2] else None,
                    "derniere_activite": ligne[3].isoformat() if ligne[3] else None,
                    "revoquee": ligne[4] is not None,
                    "user_agent": ligne[5],
                }
                for ligne in cursor.fetchall()
            ]


# ---------------------------------------------------------------------------
# Frontière de confiance — attribution depuis les en-têtes
# ---------------------------------------------------------------------------


def resoudre_attribution(
    config: Any,  # noqa: ANN401 - AppConfig
    token: str | None,
    entete_utilisateur: str | None,
    entete_role: str | None,
    utilisateur_corps: str | None = None,
) -> tuple[str | None, str | None]:
    """Qui agit ? Rend ``(identifiant, role)``.

    Règle de la frontière de confiance (L.2.3), dans cet ordre :

    1. Si des en-têtes d'attribution sont présents, ils ne sont honorés QUE si
       ``X-SEAMTECH-TOKEN`` est valide **sur la même requête**. Sinon → 401.
       Le navigateur ne détient jamais ce jeton : c'est ce qui rend l'en-tête
       digne de foi, alors que le corps de requête, lui, transite par le
       navigateur et reste donc déclaratif.
    2. L'en-tête PRIME sur ``corps["utilisateur"]``. Sans cette priorité, un
       appelant pourrait usurper n'importe quel nom en le glissant dans le
       corps — ce que faisait le code jusqu'ici (l'attribution venait
       exclusivement du corps, sans vérification).
    3. Sans en-tête, on retombe sur le corps, par compatibilité avec les clients
       existants (outillage, tests, scripts) : c'est dégradé mais explicite.

    Le rôle rendu n'est PAS utilisé pour autoriser quoi que ce soit ici : il
    vient d'un en-tête, il informe, il n'autorise pas.
    """
    if not entete_utilisateur and not entete_role:
        return (str(utilisateur_corps).strip() if utilisateur_corps else None), None

    jeton_attendu = getattr(config, "auth_token", None)
    if not jeton_attendu:
        raise HTTPException(
            status_code=401,
            detail=(
                "En-tête d'attribution refusé : X-SEAMTECH-UTILISATEUR n'est honoré que sur une requête "
                "portant un X-SEAMTECH-TOKEN valide, et aucun jeton de service n'est configuré "
                "(SEAMTECH_AUTH_TOKEN). Frontière de confiance : le navigateur ne détient pas ce jeton."
            ),
        )
    import hmac as _hmac

    if not token or not _hmac.compare_digest(str(token), str(jeton_attendu)):
        raise HTTPException(
            status_code=401,
            detail=(
                "En-tête d'attribution refusé : X-SEAMTECH-UTILISATEUR exige un X-SEAMTECH-TOKEN valide "
                "sur la même requête."
            ),
        )
    identifiant = (entete_utilisateur or "").strip() or None
    role = (entete_role or "").strip() or None
    return identifiant, role
