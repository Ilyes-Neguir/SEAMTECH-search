"""Journal d'audit — Lot 0b.

Deux substrats coexistent volontairement :

- ``audit_log`` (singulier, historique) : présent en SQLite et PostgreSQL,
  il reste lu par le verrouillage des connexions nominatives. Il peut être
  élagué par la politique de rétention historique ;
- ``audit_logs`` (pluriel, migration 025, PostgreSQL) : journal canonique
  append-only, protégé par trigger et chaîné par empreinte SHA-256. La
  rétention historique ne le supprime pas.

Un événement PostgreSQL est écrit dans les deux tables, dans deux
transactions séparées : un incident du journal canonique ne doit jamais faire
disparaître l'événement de verrouillage de connexion écrit dans la table
historique. Les détails sont expurgés avant persistance : aucun mot de passe,
jeton, cookie, en-tête d'autorisation ou secret ne doit entrer dans un journal.
Les écritures restent best-effort vis-à-vis du parcours métier, mais chaque
échec est journalisé par le logger applicatif.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.audit")

TYPES_ACTEUR_AUDIT = ("utilisateur", "secours", "service", "systeme", "anonyme")
_VERROU_CHAINE_AUDIT = 748_322
_FRAGMENTS_CLES_SECRETES = (
    "password",
    "mot_de_passe",
    "motdepasse",
    "token",
    "jeton",
    "secret",
    "authorization",
    "autorisation",
    "cookie",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "empreinte_mot_de_passe",
    "empreinte_jeton",
)
_LONGUEUR_VALEUR_MAX = 2_000
_LONGUEUR_DETAILS_MAX = 4_096
_MOTIF_SECRET_TEXTE = re.compile(
    r"(?i)\b(password|mot_de_passe|motdepasse|token|jeton|secret|api[_-]?key|"
    r"access[_-]?key|secret[_-]?key|authorization|autorisation|cookie)(\s*[:=]\s*)([^\s,;\]]+)"
)
_MOTIF_URL_IDENTIFIANTS = re.compile(r"([a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s/]+@", re.IGNORECASE)
_MOTIF_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")


def actor_fingerprint(token: str | None, client_host: str | None = None) -> str:
    """Generate a safe, irreversible fingerprint for the actor.

    Never exposes raw tokens or secrets.
    """
    if token:
        digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:12]
        return f"token:{digest}"
    if client_host in ("127.0.0.1", "localhost", "::1", "testclient"):
        return "local"
    return client_host or "anonymous"


def _cle_secrete(cle: Any) -> bool:  # noqa: ANN401
    texte = str(cle).strip().lower().replace("-", "_")
    return any(fragment in texte for fragment in _FRAGMENTS_CLES_SECRETES)


def _texte_sur(valeur: str) -> str:
    """Expurge aussi les secrets glissés dans un texte libre (erreur, DSN…)."""
    texte = _MOTIF_URL_IDENTIFIANTS.sub(r"\1:[REDACTED]@", valeur)
    texte = _MOTIF_BEARER.sub(r"\1 [REDACTED]", texte)
    texte = _MOTIF_SECRET_TEXTE.sub(r"\1\2[REDACTED]", texte)
    return texte[:_LONGUEUR_VALEUR_MAX]


def _valeur_sure(valeur: Any, *, cle: Any = None, profondeur: int = 0) -> Any:  # noqa: ANN401
    """Copie défensive des détails d'audit, sans secret et de taille bornée."""
    if _cle_secrete(cle):
        return "[REDACTED]"
    if profondeur > 6:
        return "[TRUNCATED]"
    if isinstance(valeur, dict):
        return {
            str(k): _valeur_sure(v, cle=k, profondeur=profondeur + 1)
            for k, v in list(valeur.items())[:100]
        }
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return [_valeur_sure(v, profondeur=profondeur + 1) for v in list(valeur)[:50]]
    if isinstance(valeur, str):
        return _texte_sur(valeur)
    if valeur is None or isinstance(valeur, (bool, int, float)):
        return valeur
    return _texte_sur(str(valeur))


def _details_surs(details: dict[str, Any] | None) -> dict[str, Any]:
    """Détails sûrs et bornés : la représentation JSON ne dépasse jamais 4 Kio.

    Au-delà, on ne persiste qu'un résumé de troncature. Un journal d'audit doit
    prouver l'événement, pas devenir un second stockage de documents ou de
    charges utiles arbitrairement grandes.
    """
    if not isinstance(details, dict):
        return {}
    nettoyes = {str(cle): _valeur_sure(valeur, cle=cle) for cle, valeur in details.items()}
    encode = json.dumps(nettoyes, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if len(encode.encode("utf-8")) <= _LONGUEUR_DETAILS_MAX:
        return nettoyes
    return {
        "_tronque": True,
        "taille_originale": len(encode.encode("utf-8")),
        "cles": sorted(nettoyes)[:50],
    }


def _code_statut_depuis_status(status: str, code_statut: int | None) -> int | None:
    if code_statut is not None:
        try:
            code = int(code_statut)
        except (TypeError, ValueError):
            return None
        return code if 100 <= code <= 599 else None
    try:
        code = int(str(status).strip())
    except (TypeError, ValueError):
        return None
    return code if 100 <= code <= 599 else None


def _type_acteur(
    actor: str,
    actor_type: str | None,
    identifiant: str | None,
) -> str:
    if actor_type in TYPES_ACTEUR_AUDIT:
        return str(actor_type)
    if identifiant == "secours" or actor == "secours":
        return "secours"
    if actor.startswith("token:"):
        return "service"
    if actor in {"", "anonymous", "local"} and not identifiant:
        return "anonyme"
    if identifiant:
        return "utilisateur"
    return "systeme"


def _payload_empreinte(evenement: dict[str, Any], empreinte_precedente: str | None) -> dict[str, Any]:
    cree_le = evenement["cree_le"]
    return {
        "cree_le": cree_le.isoformat() if isinstance(cree_le, datetime) else str(cree_le),
        "request_id": evenement.get("request_id"),
        "actor_type": evenement.get("actor_type"),
        "id_utilisateur": evenement.get("id_utilisateur"),
        "identifiant": evenement.get("identifiant"),
        "role": evenement.get("role"),
        "id_session": evenement.get("id_session"),
        "actor": evenement.get("actor"),
        "action": evenement.get("action"),
        "resource_type": evenement.get("resource_type") or "",
        "resource_id": evenement.get("resource_id") or "",
        "resource": evenement.get("resource") or "",
        "status": evenement.get("status"),
        "code_statut": evenement.get("code_statut"),
        "adresse_ip": evenement.get("adresse_ip"),
        "user_agent": evenement.get("user_agent"),
        "details": evenement.get("details") or {},
        "empreinte_precedente": empreinte_precedente,
    }


def _empreinte_evenement(evenement: dict[str, Any], empreinte_precedente: str | None) -> str:
    canonique = json.dumps(
        _payload_empreinte(evenement, empreinte_precedente),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


def _inserer_audit_logs(cursor: Any, evenement: dict[str, Any]) -> int:  # noqa: ANN401
    """Insère un événement canonique en sérialisant la tête de chaîne.

    Le verrou consultatif est transactionnel : deux processus ne peuvent pas
    lire la même empreinte précédente et créer deux successeurs concurrents.
    """
    import psycopg2.extras

    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_VERROU_CHAINE_AUDIT,))
    cursor.execute("SELECT empreinte FROM audit_logs ORDER BY id_audit DESC LIMIT 1")
    precedente = cursor.fetchone()
    empreinte_precedente = str(precedente[0]) if precedente else None
    empreinte = _empreinte_evenement(evenement, empreinte_precedente)
    cursor.execute(
        """
        INSERT INTO audit_logs (
            cree_le, request_id, actor_type, id_utilisateur, identifiant, role,
            id_session, actor, action, resource_type, resource_id, resource,
            status, code_statut, adresse_ip, user_agent, details,
            empreinte_precedente, empreinte
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s
        ) RETURNING id_audit
        """,
        (
            evenement["cree_le"],
            evenement.get("request_id"),
            evenement["actor_type"],
            evenement.get("id_utilisateur"),
            evenement.get("identifiant"),
            evenement.get("role"),
            evenement.get("id_session"),
            evenement["actor"],
            evenement["action"],
            evenement.get("resource_type") or "",
            evenement.get("resource_id") or "",
            evenement.get("resource") or "",
            evenement["status"],
            evenement.get("code_statut"),
            evenement.get("adresse_ip"),
            evenement.get("user_agent"),
            psycopg2.extras.Json(evenement.get("details") or {}),
            empreinte_precedente,
            empreinte,
        ),
    )
    ligne = cursor.fetchone()
    return int(ligne[0]) if ligne else 0


def migrer_audit_log_postgres(connection: Any) -> int:  # noqa: ANN401
    """Recopie l'historique ``audit_log`` dans ``audit_logs`` (migration 025).

    La recopie n'a lieu que si le journal canonique est vide. Elle est
    ordonnée et chaînée exactement comme les écritures ultérieures, afin que
    ``GET /audit`` ne perde pas l'historique antérieur au Lot 0b.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM audit_logs")
        if int(cursor.fetchone()[0]) > 0:
            return 0
        cursor.execute(
            """
            SELECT timestamp, actor, action, resource, status, details
            FROM audit_log
            ORDER BY timestamp ASC, id ASC
            """
        )
        lignes = cursor.fetchall()

    copies = 0
    with connection.cursor() as cursor:
        for timestamp, actor, action, resource, status, details in lignes:
            details_dict: dict[str, Any]
            if isinstance(details, dict):
                details_dict = _details_surs(details)
            elif isinstance(details, str):
                try:
                    brut = json.loads(details)
                except (TypeError, ValueError):
                    brut = {}
                details_dict = _details_surs(brut if isinstance(brut, dict) else {})
            else:
                details_dict = {}
            details_dict.setdefault("migration", "025_audit_logs")
            details_dict = _details_surs(details_dict)
            evenement = {
                "cree_le": timestamp,
                "request_id": None,
                "actor_type": _type_acteur(str(actor), None, None),
                "id_utilisateur": None,
                "identifiant": None,
                "role": None,
                "id_session": None,
                "actor": str(actor),
                "action": str(action),
                "resource_type": "",
                "resource_id": "",
                "resource": str(resource or ""),
                "status": str(status),
                "code_statut": _code_statut_depuis_status(str(status), None),
                "adresse_ip": None,
                "user_agent": None,
                "details": details_dict,
            }
            _inserer_audit_logs(cursor, evenement)
            copies += 1
    return copies


def record_audit_event(
    index: SearchIndex,
    action: str,
    actor: str,
    resource: str = "",
    status: str = "success",
    details: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    actor_type: str | None = None,
    id_utilisateur: int | None = None,
    identifiant: str | None = None,
    role: str | None = None,
    id_session: int | None = None,
    resource_type: str = "",
    resource_id: str = "",
    code_statut: int | None = None,
    adresse_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Record an audit event. Best-effort: catches all exceptions and logs a warning."""
    maintenant = datetime.now(timezone.utc)
    details_surs = _details_surs(details)
    evenement: dict[str, Any] = {
        "cree_le": maintenant,
        "request_id": str(request_id)[:200] if request_id else None,
        "actor_type": _type_acteur(str(actor), actor_type, identifiant),
        "id_utilisateur": int(id_utilisateur) if id_utilisateur is not None else None,
        "identifiant": str(identifiant)[:200] if identifiant else None,
        "role": str(role)[:100] if role else None,
        "id_session": int(id_session) if id_session is not None else None,
        "actor": str(actor),
        "action": str(action),
        "resource_type": str(resource_type or "")[:100],
        "resource_id": str(resource_id or "")[:500],
        "resource": str(resource or ""),
        "status": str(status),
        "code_statut": _code_statut_depuis_status(str(status), code_statut),
        "adresse_ip": str(adresse_ip)[:200] if adresse_ip else None,
        "user_agent": str(user_agent)[:500] if user_agent else None,
        "details": details_surs,
    }

    try:
        details_json = json.dumps(details_surs, ensure_ascii=False)
        with index.connect() as conn:
            if getattr(index, "is_postgres", False):
                import psycopg2.extras

                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO audit_log (timestamp, actor, action, resource, status, details)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            maintenant,
                            evenement["actor"],
                            evenement["action"],
                            evenement["resource"],
                            evenement["status"],
                            psycopg2.extras.Json(details_surs),
                        ),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO audit_log (timestamp, actor, action, resource, status, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        maintenant.isoformat(),
                        evenement["actor"],
                        evenement["action"],
                        evenement["resource"],
                        evenement["status"],
                        details_json,
                    ),
                )
    except Exception as exc:
        # Ne pas abandonner ici : si la table historique est indisponible mais
        # le journal canonique répond, l'événement doit quand même être prouvé.
        logger.warning("Failed to record audit log event (action=%s, actor=%s): %s", action, actor, exc)

    if not getattr(index, "is_postgres", False):
        return

    try:
        with index.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.audit_logs')")
                ligne = cursor.fetchone()
                if ligne is not None and ligne[0] is not None:
                    _inserer_audit_logs(cursor, evenement)
    except Exception as exc:
        logger.warning(
            "Failed to record canonical audit event (action=%s, actor=%s): %s",
            action,
            actor,
            exc,
        )


def get_audit_logs(
    index: SearchIndex,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Retrieve recent audit logs in descending chronological order.

    En PostgreSQL, ``audit_logs`` est lu dès qu'il contient des événements ;
    la forme historique (``id``, ``timestamp``, ``actor``…) est conservée et
    enrichie des champs Lot 0b. Avant la migration — ou si la table vient
    d'être créée sans recopie — la table historique reste le repli exact.
    """
    with index.connect() as conn:
        if getattr(index, "is_postgres", False):
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        to_regclass('public.audit_logs') IS NOT NULL AS table_presente,
                        CASE
                            WHEN to_regclass('public.audit_logs') IS NULL THEN FALSE
                            ELSE EXISTS (SELECT 1 FROM audit_logs LIMIT 1)
                        END AS contient_evenements
                    """
                )
                etat = dict(cursor.fetchone() or {})
                if etat.get("table_presente") and etat.get("contient_evenements"):
                    cursor.execute(
                        """
                        SELECT
                            id_audit AS id,
                            cree_le AS timestamp,
                            cree_le,
                            request_id,
                            actor_type,
                            id_utilisateur,
                            identifiant,
                            role,
                            id_session,
                            actor,
                            action,
                            resource_type,
                            resource_id,
                            resource,
                            status,
                            code_statut,
                            adresse_ip,
                            user_agent,
                            details,
                            empreinte_precedente,
                            empreinte
                        FROM audit_logs
                        ORDER BY cree_le DESC, id_audit DESC
                        LIMIT %s OFFSET %s
                        """,
                        (limit, offset),
                    )
                    rows = cursor.fetchall()
                    results: list[dict[str, Any]] = []
                    for row in rows:
                        entry = dict(row)
                        for champ in ("timestamp", "cree_le"):
                            if isinstance(entry.get(champ), datetime):
                                entry[champ] = entry[champ].isoformat()
                        if not isinstance(entry.get("details"), dict):
                            entry["details"] = {}
                        results.append(entry)
                    return results

                cursor.execute(
                    """
                    SELECT id, timestamp, actor, action, resource, status, details
                    FROM audit_log
                    ORDER BY timestamp DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    entry = dict(row)
                    if isinstance(entry.get("timestamp"), datetime):
                        entry["timestamp"] = entry["timestamp"].isoformat()
                    results.append(entry)
                return results

        cursor = conn.execute(
            """
            SELECT id, timestamp, actor, action, resource, status, details
            FROM audit_log
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()
        results = []
        for row in rows:
            details_val = row["details"]
            try:
                details_parsed = json.loads(details_val) if isinstance(details_val, str) else details_val
            except Exception:
                details_parsed = {}
            results.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "resource": row["resource"],
                    "status": row["status"],
                    "details": details_parsed if isinstance(details_parsed, dict) else {},
                }
            )
        return results
