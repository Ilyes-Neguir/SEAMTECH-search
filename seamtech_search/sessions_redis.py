"""Sessions serveur adossées à Redis — Lot 0a.

Pourquoi Redis, et pas le cookie : un jeton signé auto-porté reste valide
jusqu'à son expiration, même après une déconnexion. Ici, le cookie ne porte
qu'un identifiant de session OPAQUE (``sid``, 256 bits de CSPRNG) ; la vérité
est dans Redis. Une session absente, révoquée, inactive depuis plus de
``SEAMTECH_SESSION_IDLE_MINUTES`` (défaut 30) ou ouverte depuis plus de
``SEAMTECH_SESSION_HOURS`` (défaut 8) vaut 401.

Redis indisponible = refus EXPLICITE (503), jamais d'acceptation silencieuse
d'une session non vérifiable. C'est la même règle que
``SEAMTECH_REQUIRE_DURABLE_QUEUE=true`` : une garantie qu'on ne peut pas
vérifier n'est pas une garantie.

Clés :

* ``seamtech:session:<sid>``       → JSON {user_id, identifiant, nom, role,
  mode, issued_at, last_seen_at, expires_at, user_agent} avec TTL absolu ;
* ``seamtech:user_sessions:<uid>`` → ensemble des ``sid`` de l'utilisateur,
  pour « Déconnecter tous mes appareils » et la révocation après changement
  de mot de passe / de rôle.

Aucune migration SQL n'est nécessaire : Redis suffit (mission Lot 0a). Le
``sid`` n'est jamais journalisé ; il n'est pas stocké en clair ailleurs que
dans Redis, qui est déjà le magasin d'état.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

LOGGER = logging.getLogger("seamtech_search.sessions_redis")

SESSION_KEY_PREFIX = "seamtech:session"
USER_SESSIONS_KEY_PREFIX = "seamtech:user_sessions"

#: 32 octets de CSPRNG encodés en base64url = 43 caractères, soit 256 bits.
SID_OCTETS = 32
_SID_VALIDE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")

MODE_NOMINATIF = "nominatif"
MODE_SECOURS = "secours"


class SessionStoreIndisponible(RuntimeError):
    """Redis absent ou injoignable : la session ne peut pas être vérifiée.

    L'appelant DOIT répondre 503. Ne jamais transformer cette exception en
    « session invalide » (401) : l'opérateur doit pouvoir distinguer « votre
    session est morte » de « le magasin de sessions est en panne ».
    """


@dataclass(frozen=True)
class SessionRecord:
    sid: str
    user_id: str
    identifiant: str
    nom: str | None
    role: str
    mode: str
    issued_at: float
    last_seen_at: float
    expires_at: float
    user_agent: str | None = None
    #: Ligne ``session_ui`` correspondante (traçabilité), si elle existe. Ce
    #: n'est PAS une autorité : la révocation de la session navigateur passe
    #: par la clé Redis. Il sert à tenir l'audit SQL cohérent au logout.
    db_session_id: int | None = None

    def vers_public(self) -> dict[str, Any]:
        """Vue renvoyée à l'API : jamais le ``sid`` (le client le connaît déjà)."""
        donnees = asdict(self)
        donnees.pop("sid", None)
        return donnees


def _cle_session(sid: str) -> str:
    return f"{SESSION_KEY_PREFIX}:{sid}"


def _cle_utilisateur(user_id: str) -> str:
    return f"{USER_SESSIONS_KEY_PREFIX}:{user_id}"


def sid_valide(sid: str | None) -> bool:
    """Un ``sid`` utilisable comme clé Redis, et assez long pour ne pas être deviné."""
    return bool(sid) and bool(_SID_VALIDE.fullmatch(str(sid)))


class SessionStore:
    """Registre de sessions Redis, sans repli mémoire (fail-closed)."""

    def __init__(
        self,
        redis_url: str | None,
        *,
        idle_minutes: int = 30,
        session_hours: float = 8.0,
        client: Any | None = None,
        horloge: Any | None = None,
    ) -> None:
        self.redis_url = redis_url
        self.idle_minutes = int(idle_minutes)
        self.session_hours = float(session_hours)
        self._client = client
        self._horloge = horloge or time.time

    # ------------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------------

    def _maintenant(self, now: float | None = None) -> float:
        return float(now) if now is not None else float(self._horloge())

    def client(self) -> Any:  # noqa: ANN401 - client redis réel ou double de test
        if self._client is not None:
            return self._client
        if not self.redis_url:
            raise SessionStoreIndisponible(
                "SEAMTECH_REDIS_URL non configuré : la session ne peut pas être vérifiée. "
                "Refus explicite (fail-closed) — aucun repli mémoire n'est autorisé."
            )
        try:
            import redis

            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
        except Exception as exc:  # noqa: BLE001 - toute erreur = indisponible
            raise SessionStoreIndisponible(
                f"Client Redis impossible à créer pour la session : {exc}"
            ) from exc
        return self._client

    def verifier_disponibilite(self) -> None:
        """PING explicite ; lève ``SessionStoreIndisponible`` en cas d'échec."""
        try:
            if not self.client().ping():
                raise SessionStoreIndisponible("Redis a répondu faux au PING.")
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(f"Redis injoignable pour les sessions : {exc}") from exc

    # ------------------------------------------------------------------
    # Durées
    # ------------------------------------------------------------------

    @property
    def duree_inactivite_secondes(self) -> float:
        return float(self.idle_minutes) * 60.0

    @property
    def duree_absolue_secondes(self) -> float:
        return float(self.session_hours) * 3600.0

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------

    def creer_session(
        self,
        *,
        user_id: str | int,
        identifiant: str,
        nom: str | None,
        role: str,
        mode: str = MODE_NOMINATIF,
        user_agent: str | None = None,
        db_session_id: int | None = None,
        now: float | None = None,
    ) -> SessionRecord:
        """Crée une session avec un ``sid`` NEUF (anti-fixation).

        Chaque connexion appelle cette méthode : le ``sid`` change donc à
        chaque login, même si l'ancien cookie est encore présent.
        """
        if mode not in (MODE_NOMINATIF, MODE_SECOURS):
            raise ValueError(f"Mode de session inconnu : {mode!r}")
        uid = str(user_id).strip()
        if not uid:
            raise ValueError("user_id obligatoire pour ouvrir une session.")
        maintenant = self._maintenant(now)
        sid = secrets.token_urlsafe(SID_OCTETS)
        if not sid_valide(sid):  # pragma: no cover - garanti par token_urlsafe
            raise RuntimeError("Générateur de sid défaillant : identifiant non conforme.")
        expire = maintenant + self.duree_absolue_secondes
        record = SessionRecord(
            sid=sid,
            user_id=uid,
            identifiant=str(identifiant),
            nom=nom,
            role=str(role),
            mode=mode,
            issued_at=maintenant,
            last_seen_at=maintenant,
            expires_at=expire,
            user_agent=user_agent,
            db_session_id=int(db_session_id) if db_session_id is not None else None,
        )
        charge = asdict(record)
        charge.pop("sid", None)
        ttl = max(1, int(round(expire - maintenant)))
        try:
            client = self.client()
            pipe = client.pipeline()
            pipe.set(_cle_session(sid), json.dumps(charge, ensure_ascii=False), ex=ttl)
            pipe.sadd(_cle_utilisateur(uid), sid)
            pipe.expire(_cle_utilisateur(uid), ttl)
            pipe.execute()
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(
                f"Écriture de la session dans Redis impossible : {exc}"
            ) from exc
        LOGGER.info(
            "Session ouverte pour « %s » (mode %s, expire dans %ss).",
            record.identifiant,
            record.mode,
            ttl,
        )
        return record

    # ------------------------------------------------------------------
    # Lecture / validation
    # ------------------------------------------------------------------

    def valider_session(self, sid: str | None, *, now: float | None = None) -> SessionRecord | None:
        """Valide une session et rafraîchit ``last_seen_at``.

        Rend ``None`` (→ 401) si le ``sid`` est malformé, absent de Redis,
        révoqué (clé supprimée), expiré en absolu ou inactif trop longtemps.
        Lève ``SessionStoreIndisponible`` (→ 503) si Redis ne répond pas.
        """
        if not sid_valide(sid):
            return None
        cle = _cle_session(str(sid))
        try:
            client = self.client()
            brut = client.get(cle)
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(f"Lecture de session Redis impossible : {exc}") from exc

        if not brut:
            return None
        try:
            charge = json.loads(brut)
            record = SessionRecord(
                sid=str(sid),
                user_id=str(charge["user_id"]),
                identifiant=str(charge["identifiant"]),
                nom=charge.get("nom"),
                role=str(charge["role"]),
                mode=str(charge["mode"]),
                issued_at=float(charge["issued_at"]),
                last_seen_at=float(charge["last_seen_at"]),
                expires_at=float(charge["expires_at"]),
                user_agent=charge.get("user_agent"),
                db_session_id=(
                    int(charge["db_session_id"])
                    if charge.get("db_session_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # Charge illisible : on ne l'accepte jamais, on la retire.
            self._supprimer_cle(str(sid), supprimer_index=True)
            return None

        maintenant = self._maintenant(now)
        if maintenant >= record.expires_at:
            self._supprimer_cle(record.sid, supprimer_index=True)
            return None
        if maintenant - record.last_seen_at > self.duree_inactivite_secondes:
            self._supprimer_cle(record.sid, supprimer_index=True)
            return None

        # Rafraîchit l'activité sans prolonger l'expiration ABSOLUE.
        mis_a_jour = SessionRecord(
            sid=record.sid,
            user_id=record.user_id,
            identifiant=record.identifiant,
            nom=record.nom,
            role=record.role,
            mode=record.mode,
            issued_at=record.issued_at,
            last_seen_at=maintenant,
            expires_at=record.expires_at,
            user_agent=record.user_agent,
            db_session_id=record.db_session_id,
        )
        charge = asdict(mis_a_jour)
        charge.pop("sid", None)
        ttl = max(1, int(round(record.expires_at - maintenant)))
        try:
            client = self.client()
            pipe = client.pipeline()
            pipe.set(cle, json.dumps(charge, ensure_ascii=False), ex=ttl)
            pipe.execute()
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            # L'écriture du battement d'activité échoue : refuser est plus sûr
            # que servir une session dont on ne peut plus tracer l'activité.
            raise SessionStoreIndisponible(
                f"Mise à jour de l'activité de session impossible : {exc}"
            ) from exc
        return mis_a_jour

    def session_existe(self, sid: str | None) -> bool:
        """Présence brute (sans contrôle de délai) — utile aux tests/diagnostics."""
        if not sid_valide(sid):
            return False
        try:
            return bool(self.client().exists(_cle_session(str(sid))))
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(f"Lecture de session Redis impossible : {exc}") from exc

    # ------------------------------------------------------------------
    # Révocation
    # ------------------------------------------------------------------

    def revoquer_session(self, sid: str | None) -> bool:
        """Supprime la session. Idempotent : ``False`` si elle n'existait plus."""
        if not sid_valide(sid):
            return False
        sid_txt = str(sid)
        try:
            client = self.client()
            brut = client.get(_cle_session(sid_txt))
            uid: str | None = None
            if brut:
                try:
                    uid = str(json.loads(brut).get("user_id") or "")
                except (json.JSONDecodeError, TypeError):
                    uid = None
            pipe = client.pipeline()
            pipe.delete(_cle_session(sid_txt))
            if uid:
                pipe.srem(_cle_utilisateur(uid), sid_txt)
            resultat = pipe.execute()
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(f"Révocation de session Redis impossible : {exc}") from exc
        return bool(resultat and resultat[0])

    def revoquer_toutes_sessions(self, user_id: str | int) -> int:
        """Révoque TOUTES les sessions d'un utilisateur (tous appareils)."""
        uid = str(user_id).strip()
        if not uid:
            return 0
        try:
            client = self.client()
            sid_list = list(client.smembers(_cle_utilisateur(uid)) or [])
            if not sid_list:
                client.delete(_cle_utilisateur(uid))
                return 0
            pipe = client.pipeline()
            for sid in sid_list:
                pipe.delete(_cle_session(str(sid)))
            pipe.delete(_cle_utilisateur(uid))
            resultats = pipe.execute()
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(
                f"Révocation globale des sessions Redis impossible : {exc}"
            ) from exc
        # Le dernier résultat = DEL de l'index ; les précédents = DEL des sessions.
        return sum(1 for valeur in resultats[:-1] if valeur)

    def lister_sessions(self, user_id: str | int) -> list[dict[str, Any]]:
        """Sessions encore présentes d'un utilisateur (diagnostic, sans jeton)."""
        uid = str(user_id).strip()
        try:
            client = self.client()
            resultats: list[dict[str, Any]] = []
            for sid in client.smembers(_cle_utilisateur(uid)) or []:
                brut = client.get(_cle_session(str(sid)))
                if not brut:
                    continue
                try:
                    charge = json.loads(brut)
                except json.JSONDecodeError:
                    continue
                resultats.append(
                    {
                        "issued_at": charge.get("issued_at"),
                        "last_seen_at": charge.get("last_seen_at"),
                        "expires_at": charge.get("expires_at"),
                        "mode": charge.get("mode"),
                        "user_agent": charge.get("user_agent"),
                    }
                )
            return sorted(resultats, key=lambda item: item.get("issued_at") or 0)
        except SessionStoreIndisponible:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreIndisponible(f"Liste des sessions Redis impossible : {exc}") from exc

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _supprimer_cle(self, sid: str, *, supprimer_index: bool = False) -> None:
        try:
            client = self.client()
            brut = client.get(_cle_session(sid))
            pipe = client.pipeline()
            pipe.delete(_cle_session(sid))
            if brut:
                try:
                    uid = str(json.loads(brut).get("user_id") or "")
                except (json.JSONDecodeError, TypeError):
                    uid = ""
                if supprimer_index and uid:
                    pipe.srem(_cle_utilisateur(uid), sid)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001 - nettoyage best-effort après refus
            LOGGER.warning("Impossible de retirer la session expirée de Redis : %s", exc)


def revoquer_toutes_sessions_pour_identifiant(
    redis_url: str | None,
    user_id: str | int,
    *,
    idle_minutes: int = 30,
    session_hours: float = 8.0,
) -> int:
    """Fonction interne réutilisable (mot de passe, rôle, désactivation).

    Elle ne lève pas : un changement de mot de passe doit aboutir même si
    Redis est momentanément indisponible — mais l'appelant API, lui, décide
    explicitement quoi renvoyer. Le retour ``0`` signifie « rien révoqué ou
    magasin indisponible » ; le journal dit lequel.
    """
    try:
        magasin = SessionStore(redis_url, idle_minutes=idle_minutes, session_hours=session_hours)
        return magasin.revoquer_toutes_sessions(user_id)
    except SessionStoreIndisponible as exc:
        LOGGER.error("Révocation globale impossible (Redis) pour l'utilisateur %s : %s", user_id, exc)
        return 0
