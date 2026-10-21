"""Journal d'audit nominatif Lot 0b — table audit_logs.

Colonnes contractuelles: id, created_at, actor_user_id, actor_username,
actor_role, action, target_type, target_id, ip_address, user_agent, outcome,
details_json.

- actor_* provient TOUJOURS de la session serveur validée (jamais du corps)
- Ne journalise jamais: mot de passe, empreinte, cookie, jeton, secret
- details_json: JSON valide, borné <=4ko, sans secret
- Échec d'écriture: log warning, ne bloque jamais l'opération métier (documenté)
- Requêtes paramétrées, pas de concaténation
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.audit_logs")

# Actions contractuelles Lot0b
ACTIONS_AUTORISEES = frozenset(
    [
        "AUTH_LOGIN_SUCCESS",
        "AUTH_LOGIN_FAILURE",
        "AUTH_LOGOUT",
        "AUTH_LOGOUT_ALL",
        "SESSION_REVOKED",
        "PIECE_DOWNLOAD",
        "ARTIFACT_DOWNLOAD",
        "IMPORT_CREATE",
        # ACCOUNT_* réellement offertes
        "ACCOUNT_CREATE",
        "ACCOUNT_UPDATE",
        "ACCOUNT_DISABLE",
        "ACCOUNT_PASSWORD_RESET",
        "ACCOUNT_PASSWORD_CHANGE",
    ]
)

# Colonnes autorisées pour filtrage/tri (évite injection)
COLONNES_FILTRE = {"action", "actor_role", "target_type", "outcome"}
COLONNES_TRI = {"created_at", "id", "action"}


def _tronquer_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Borne details à 4ko, nettoie secrets."""
    if not details:
        return {}
    # Filtre secrets par clé
    interdits = {"mot_de_passe", "password", "empreinte", "cookie", "jeton", "token", "secret", "sid"}
    nettoye: dict[str, Any] = {}
    for k, v in details.items():
        if k.lower() in interdits:
            continue
        # Convertit en string tronquée
        try:
            val = str(v)
        except Exception:
            val = "<unrepresentable>"
        if len(val) > 500:
            val = val[:500] + "…"
        nettoye[k] = val
    # Vérifie taille JSON totale
    try:
        j = json.dumps(nettoye, ensure_ascii=False)
        if len(j.encode("utf-8")) > 4096:
            # Tronque au plus simple
            nettoye = {"truncated": j[:4000] + "…"}
    except Exception:
        nettoye = {}
    return nettoye


def record_audit_log(
    index: SearchIndex,
    *,
    actor_user_id: int | str | None,
    actor_username: str,
    actor_role: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    """Écrit un événement dans audit_logs. Best-effort, log warning si échec.

    Ne lève jamais. Documenté: l'échec d'écriture est loggé mais n'interrompt
    pas l'opération métier (pas de fail-open sur l'autorisation).
    """
    if outcome not in ("success", "failure"):
        logger.warning("Audit outcome invalide %r ignoré", outcome)
        return
    # Normalise actor
    if actor_user_id is not None:
        try:
            actor_user_id_int = int(actor_user_id)
        except Exception:
            actor_user_id_int = None
    else:
        actor_user_id_int = None

    details_borne = _tronquer_details(details)

    try:
        with index.connect() as conn:
            if index.is_postgres:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO audit_logs
                            (actor_user_id, actor_username, actor_role, action,
                             target_type, target_id, ip_address, user_agent, outcome, details_json)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            actor_user_id_int,
                            actor_username,
                            actor_role,
                            action,
                            target_type,
                            target_id,
                            ip_address,
                            user_agent,
                            outcome,
                            json.dumps(details_borne, ensure_ascii=False),
                        ),
                    )
            else:
                # SQLite: même schéma mais sans FK
                conn.execute(
                    """
                    INSERT INTO audit_logs
                        (actor_user_id, actor_username, actor_role, action,
                         target_type, target_id, ip_address, user_agent, outcome, details_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        actor_user_id_int,
                        actor_username,
                        actor_role,
                        action,
                        target_type,
                        target_id,
                        ip_address,
                        user_agent,
                        outcome,
                        json.dumps(details_borne, ensure_ascii=False),
                    ),
                )
    except Exception as exc:
        # Documenté: échec d'écriture loggé, pas masqué silencieusement
        logger.warning(
            "Échec écriture audit_logs action=%s actor=%s outcome=%s: %s",
            action,
            actor_username,
            outcome,
            exc,
        )


_SQL_AUDIT_PG_CREATED_AT_DESC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (%s::text IS NULL OR action = %s) "
    "AND (%s::text IS NULL OR actor_role = %s) "
    "AND (%s::text IS NULL OR target_type = %s) "
    "AND (%s::text IS NULL OR outcome = %s) "
    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
)
_SQL_AUDIT_PG_CREATED_AT_ASC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (%s::text IS NULL OR action = %s) "
    "AND (%s::text IS NULL OR actor_role = %s) "
    "AND (%s::text IS NULL OR target_type = %s) "
    "AND (%s::text IS NULL OR outcome = %s) "
    "ORDER BY created_at ASC, id ASC LIMIT %s OFFSET %s"
)
_SQL_AUDIT_PG_ID_DESC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (%s::text IS NULL OR action = %s) "
    "AND (%s::text IS NULL OR actor_role = %s) "
    "AND (%s::text IS NULL OR target_type = %s) "
    "AND (%s::text IS NULL OR outcome = %s) "
    "ORDER BY id DESC, created_at DESC LIMIT %s OFFSET %s"
)
_SQL_AUDIT_PG_ID_ASC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (%s::text IS NULL OR action = %s) "
    "AND (%s::text IS NULL OR actor_role = %s) "
    "AND (%s::text IS NULL OR target_type = %s) "
    "AND (%s::text IS NULL OR outcome = %s) "
    "ORDER BY id ASC, created_at ASC LIMIT %s OFFSET %s"
)
_SQL_AUDIT_PG_ACTION_DESC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (%s::text IS NULL OR action = %s) "
    "AND (%s::text IS NULL OR actor_role = %s) "
    "AND (%s::text IS NULL OR target_type = %s) "
    "AND (%s::text IS NULL OR outcome = %s) "
    "ORDER BY action DESC, id DESC LIMIT %s OFFSET %s"
)
_SQL_AUDIT_PG_ACTION_ASC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (%s::text IS NULL OR action = %s) "
    "AND (%s::text IS NULL OR actor_role = %s) "
    "AND (%s::text IS NULL OR target_type = %s) "
    "AND (%s::text IS NULL OR outcome = %s) "
    "ORDER BY action ASC, id ASC LIMIT %s OFFSET %s"
)
_SQL_AUDIT_SQLITE_CREATED_AT_DESC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (? IS NULL OR action = ?) "
    "AND (? IS NULL OR actor_role = ?) "
    "AND (? IS NULL OR target_type = ?) "
    "AND (? IS NULL OR outcome = ?) "
    "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
)
_SQL_AUDIT_SQLITE_CREATED_AT_ASC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (? IS NULL OR action = ?) "
    "AND (? IS NULL OR actor_role = ?) "
    "AND (? IS NULL OR target_type = ?) "
    "AND (? IS NULL OR outcome = ?) "
    "ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?"
)
_SQL_AUDIT_SQLITE_ID_DESC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (? IS NULL OR action = ?) "
    "AND (? IS NULL OR actor_role = ?) "
    "AND (? IS NULL OR target_type = ?) "
    "AND (? IS NULL OR outcome = ?) "
    "ORDER BY id DESC, created_at DESC LIMIT ? OFFSET ?"
)
_SQL_AUDIT_SQLITE_ID_ASC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (? IS NULL OR action = ?) "
    "AND (? IS NULL OR actor_role = ?) "
    "AND (? IS NULL OR target_type = ?) "
    "AND (? IS NULL OR outcome = ?) "
    "ORDER BY id ASC, created_at ASC LIMIT ? OFFSET ?"
)
_SQL_AUDIT_SQLITE_ACTION_DESC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (? IS NULL OR action = ?) "
    "AND (? IS NULL OR actor_role = ?) "
    "AND (? IS NULL OR target_type = ?) "
    "AND (? IS NULL OR outcome = ?) "
    "ORDER BY action DESC, id DESC LIMIT ? OFFSET ?"
)
_SQL_AUDIT_SQLITE_ACTION_ASC = (
    "SELECT id, created_at, actor_user_id, actor_username, actor_role, action, target_type, "
    "target_id, ip_address, user_agent, outcome, details_json FROM audit_logs "
    "WHERE (? IS NULL OR action = ?) "
    "AND (? IS NULL OR actor_role = ?) "
    "AND (? IS NULL OR target_type = ?) "
    "AND (? IS NULL OR outcome = ?) "
    "ORDER BY action ASC, id ASC LIMIT ? OFFSET ?"
)


def get_audit_logs_filtered(
    index: SearchIndex,
    *,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    outcome: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
) -> list[dict[str, Any]]:
    """Lecture paramétrée, bornée, tri validé pour GET /api/admin/audit-logs."""
    # Validation stricte
    if limit < 1 or limit > 200:
        raise ValueError("limit doit être 1..200")
    if offset < 0 or offset > 1_000_000:
        raise ValueError("offset doit être 0..1000000")
    if sort not in COLONNES_TRI:
        raise ValueError("sort invalide: " + sort)
    if order not in ("asc", "desc"):
        raise ValueError("order doit être asc/desc")
    if outcome and outcome not in ("success", "failure"):
        raise ValueError("outcome invalide")

    # Choix SQL statique (aucune concaténation, aucune f-string avec variable)
    if index.is_postgres:
        if sort == "created_at" and order == "desc":
            sql = _SQL_AUDIT_PG_CREATED_AT_DESC
        elif sort == "created_at" and order == "asc":
            sql = _SQL_AUDIT_PG_CREATED_AT_ASC
        elif sort == "id" and order == "desc":
            sql = _SQL_AUDIT_PG_ID_DESC
        elif sort == "id" and order == "asc":
            sql = _SQL_AUDIT_PG_ID_ASC
        elif sort == "action" and order == "desc":
            sql = _SQL_AUDIT_PG_ACTION_DESC
        elif sort == "action" and order == "asc":
            sql = _SQL_AUDIT_PG_ACTION_ASC
        else:
            raise ValueError("tri invalide")
    else:
        if sort == "created_at" and order == "desc":
            sql = _SQL_AUDIT_SQLITE_CREATED_AT_DESC
        elif sort == "created_at" and order == "asc":
            sql = _SQL_AUDIT_SQLITE_CREATED_AT_ASC
        elif sort == "id" and order == "desc":
            sql = _SQL_AUDIT_SQLITE_ID_DESC
        elif sort == "id" and order == "asc":
            sql = _SQL_AUDIT_SQLITE_ID_ASC
        elif sort == "action" and order == "desc":
            sql = _SQL_AUDIT_SQLITE_ACTION_DESC
        elif sort == "action" and order == "asc":
            sql = _SQL_AUDIT_SQLITE_ACTION_ASC
        else:
            raise ValueError("tri invalide")

    # Paramètres liés uniquement (aucune valeur injectée en SQL)
    params: tuple[Any, ...] = (
        action,
        action,
        actor_role,
        actor_role,
        target_type,
        target_type,
        outcome,
        outcome,
        limit,
        offset,
    )

    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                out: list[dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    if isinstance(d.get("created_at"), datetime):
                        d["created_at"] = d["created_at"].isoformat()
                    # details_json peut être déjà dict (psycopg2 JSONB)
                    dj = d.get("details_json")
                    if isinstance(dj, str):
                        try:
                            d["details_json"] = json.loads(dj)
                        except Exception:
                            d["details_json"] = {}
                    out.append(d)
                return out
        else:
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            out = []
            for row in rows:
                d = dict(row)
                dj = d.get("details_json")
                if isinstance(dj, str):
                    try:
                        d["details_json"] = json.loads(dj)
                    except Exception:
                        d["details_json"] = {}
                out.append(d)
            return out
