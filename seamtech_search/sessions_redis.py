"""Registre Redis des sessions nominatives — Lot 0a (plan directeur v2).

Rôle : rendre l'invalidation de session IMMÉDIATE et partagée entre
processus (OWASP A07 / CWE-613). La ligne PostgreSQL ``session_ui``
(migration 016) reste l'historique durable et auditable ; Redis porte
l'état « vivant ou mort » consulté à chaque requête :

- ``seamtech:session:<id_session>`` — hash ``{id_utilisateur,
  empreinte_jeton, cree_le, derniere_activite, expire_le}`` (epochs),
  TTL = expiration absolue de la session ;
- ``seamtech:user_sessions:<id_utilisateur>`` — ensemble des
  ``id_session`` vivants du compte (logout-all, révocation sur
  changement de mot de passe / de rôle / désactivation).

Politique d'échec — FAIL-CLOSED : quand un registre est exigé (Redis
configuré ou ``require_durable_queue``), toute indisponibilité Redis
lève :class:`SessionsRedisIndisponible`, traduite en HTTP 503 par les
routes. Une session dont on ne peut pas vérifier l'état n'est JAMAIS
acceptée en silence. Quand Redis n'est pas configuré du tout (Poste de
développement SQLite, où les comptes nominatifs n'existent de toute
façon pas — §17.1), :func:`registre_depuis_config` rend ``None`` et le
contrôle retombe sur PostgreSQL seul, comme avant le Lot 0a : ce repli
est explicite et journalisé, jamais silencieux en production (la
composition Docker de production configure toujours Redis et
``SEAMTECH_REQUIRE_DURABLE_QUEUE=true``).

Aucun secret en clair dans Redis : on n'y stocke que l'EMPREINTE
SHA-256 du jeton (``empreinte_jeton``), exactement comme en base.
"""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger("seamtech_search.sessions_redis")

PREFIXE_SESSION = "seamtech:session:"
PREFIXE_UTILISATEUR = "seamtech:user_sessions:"


class SessionsRedisIndisponible(RuntimeError):
    """Redis des sessions injoignable alors qu'il est exigé (fail-closed)."""


def cle_session(id_session: int) -> str:
    return f"{PREFIXE_SESSION}{int(id_session)}"


def cle_utilisateur(id_utilisateur: int) -> str:
    return f"{PREFIXE_UTILISATEUR}{int(id_utilisateur)}"


class RegistreSessionsRedis:
    """État vivant des sessions dans Redis (voir docstring du module)."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: Any = None

    # -- accès client -------------------------------------------------------
    def _redis(self) -> Any:  # noqa: ANN401 - client redis-py
        if self._client is None:
            try:
                import redis as redis_lib
            except ImportError as erreur:  # pragma: no cover - dépendance déclarée
                raise SessionsRedisIndisponible("Bibliothèque redis absente.") from erreur
            self._client = redis_lib.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
        return self._client

    def _executer(self, operation: str, fonction: Any) -> Any:  # noqa: ANN401
        """Exécute une opération Redis ; toute erreur réseau => fail-closed."""
        try:
            return fonction(self._redis())
        except SessionsRedisIndisponible:
            raise
        except Exception as erreur:  # redis.exceptions.* et erreurs socket
            raise SessionsRedisIndisponible(
                f"Redis des sessions indisponible pendant « {operation} » : {erreur}"
            ) from erreur

    def ping(self) -> bool:
        return bool(self._executer("ping", lambda client: client.ping()))

    # -- cycle de vie -------------------------------------------------------
    def enregistrer(
        self,
        id_session: int,
        id_utilisateur: int,
        empreinte: str,
        cree_ts: float,
        expire_ts: float,
    ) -> None:
        """Publie une session vivante. TTL aligné sur l'expiration absolue."""
        ttl = max(1, int(expire_ts - cree_ts))

        def _faire(client: Any) -> None:  # noqa: ANN401
            with client.pipeline(transaction=True) as tuyau:
                tuyau.hset(
                    cle_session(id_session),
                    mapping={
                        "id_utilisateur": int(id_utilisateur),
                        "empreinte_jeton": empreinte,
                        "cree_le": repr(float(cree_ts)),
                        "derniere_activite": repr(float(cree_ts)),
                        "expire_le": repr(float(expire_ts)),
                    },
                )
                tuyau.expire(cle_session(id_session), ttl)
                tuyau.sadd(cle_utilisateur(id_utilisateur), int(id_session))
                # L'ensemble utilisateur vit au moins aussi longtemps que sa
                # session la plus longue connue ; on ne fait que prolonger.
                tuyau.expire(cle_utilisateur(id_utilisateur), ttl)
                tuyau.execute()

        self._executer("enregistrer", _faire)

    def etat(self, id_session: int) -> dict[str, Any] | None:
        """État Redis d'une session, ou ``None`` si elle n'y vit plus."""
        brut: dict[str, Any] = self._executer(
            "etat", lambda client: client.hgetall(cle_session(id_session))
        )
        if not brut:
            return None
        try:
            return {
                "id_utilisateur": int(brut["id_utilisateur"]),
                "empreinte_jeton": str(brut["empreinte_jeton"]),
                "cree_le": float(brut["cree_le"]),
                "derniere_activite": float(brut["derniere_activite"]),
                "expire_le": float(brut["expire_le"]),
            }
        except (KeyError, TypeError, ValueError):
            # Une clé corrompue ne doit jamais ouvrir de session.
            LOGGER.warning("Clé Redis de session %s illisible — traitée comme absente.", id_session)
            return None

    def toucher(self, id_session: int, maintenant_ts: float) -> None:
        """Rafraîchit ``derniere_activite`` (timeout d'inactivité glissant)."""
        self._executer(
            "toucher",
            lambda client: client.hset(
                cle_session(id_session), "derniere_activite", repr(float(maintenant_ts))
            ),
        )

    def revoquer(self, id_session: int, id_utilisateur: int | None = None) -> None:
        """Supprime immédiatement la session du registre (logout effectif)."""
        if id_utilisateur is None:
            etat = self.etat(id_session)
            id_utilisateur = int(etat["id_utilisateur"]) if etat else None

        def _faire(client: Any) -> None:  # noqa: ANN401
            with client.pipeline(transaction=True) as tuyau:
                tuyau.delete(cle_session(id_session))
                if id_utilisateur is not None:
                    tuyau.srem(cle_utilisateur(id_utilisateur), int(id_session))
                tuyau.execute()

        self._executer("revoquer", _faire)

    def revoquer_utilisateur(self, id_utilisateur: int) -> list[int]:
        """« Déconnecter tous mes appareils » : supprime toutes les sessions."""
        identifiants: set[str] = self._executer(
            "revoquer-utilisateur-liste",
            lambda client: client.smembers(cle_utilisateur(id_utilisateur)),
        )

        def _faire(client: Any) -> None:  # noqa: ANN401
            with client.pipeline(transaction=True) as tuyau:
                for brut in identifiants:
                    tuyau.delete(cle_session(int(brut)))
                tuyau.delete(cle_utilisateur(id_utilisateur))
                tuyau.execute()

        self._executer("revoquer-utilisateur", _faire)
        return sorted(int(brut) for brut in identifiants)


def registre_depuis_config(config: Any) -> RegistreSessionsRedis | None:  # noqa: ANN401
    """Construit le registre selon la configuration (voir politique d'échec).

    - ``redis_url`` configurée -> registre Redis (exigé : fail-closed) ;
    - sinon, si ``require_durable_queue`` (ou ``require_redis_sessions``)
      est vrai -> :class:`SessionsRedisIndisponible` ;
    - sinon -> ``None`` (repli PostgreSQL seul, développement uniquement).
    """
    redis_url = getattr(config, "redis_url", None)
    if redis_url:
        return RegistreSessionsRedis(str(redis_url))
    exige = bool(
        getattr(config, "require_durable_queue", False)
        or getattr(config, "require_redis_sessions", False)
    )
    if exige:
        raise SessionsRedisIndisponible(
            "SEAMTECH_REDIS_URL absente alors que la file durable / les sessions "
            "Redis sont exigées : impossible de vérifier l'état des sessions."
        )
    LOGGER.warning(
        "Sessions nominatives SANS registre Redis (SEAMTECH_REDIS_URL absente) : "
        "contrôle par PostgreSQL seul. Acceptable en développement, jamais en production."
    )
    return None
