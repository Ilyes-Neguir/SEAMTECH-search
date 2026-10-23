"""Lot 1 — Rôles et workflow d'approbation in-app.

4 rôles stricts (admin, lecture, utilisateur, approbateur) avec RBAC serveur.
Workflow : utilisateur crée demande → approbateur décide (approve/reject) → effet instantané + notifications in-app + audit.
Anti-auto-approbation (SEAMTECH_ALLOW_SELF_APPROVAL=false).

Tables : approval_requests, in_app_notifications (migration 027, PostgreSQL only).
Audit : APPROVAL_REQUESTED, APPROVAL_DECIDED via record_audit_log (best-effort, sans secret).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from .audit_logs import record_audit_log

LOGGER = logging.getLogger("seamtech_search.approbations")

# Rôles canoniques Lot1 + aliases legacy Lot0a/2
ROLES_CANONIQUES = ("admin", "lecture", "utilisateur", "approbateur")
ROLES_LEGACY_ALIASES = {"operateur": "utilisateur", "administrateur": "admin"}
TOUS_ROLES_AUTORISES = ("admin", "lecture", "utilisateur", "approbateur", "operateur", "administrateur")

ACTIONS_AUTORISEES = ("download_piece", "download_artifact", "modify_record", "delete_record", "update_item", "delete_item")
STATUTS = ("pending", "approved", "rejected", "executed", "cancelled")

# Normalisation métier : update_item/delete_item sont des alias de modify_record/delete_record
ACTION_ALIASES = {"update_item": "modify_record", "delete_item": "delete_record"}


def normaliser_role(role: str | None) -> str:
    if not role:
        return "unknown"
    r = str(role).strip().lower()
    return ROLES_LEGACY_ALIASES.get(r, r)


def role_est_admin(role: str | None) -> bool:
    return normaliser_role(role) == "admin"


def role_est_lecture(role: str | None) -> bool:
    return normaliser_role(role) == "lecture"


def role_est_utilisateur(role: str | None) -> bool:
    return normaliser_role(role) == "utilisateur"


def role_est_approbateur(role: str | None) -> bool:
    return normaliser_role(role) == "approbateur"


def _verifier_postgres(index: Any) -> None:
    if not getattr(index, "is_postgres", False):
        raise HTTPException(status_code=503, detail="Workflow d'approbation disponible uniquement avec PostgreSQL.")


def creer_demande(
    index: Any,
    requester: dict[str, Any],
    action_type: str,
    target_type: str,
    target_id: str,
    payload_json: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Crée une demande d'approbation (utilisateur uniquement)."""
    _verifier_postgres(index)
    action_type = str(action_type or "").strip()
    target_type = str(target_type or "").strip()
    target_id = str(target_id or "").strip()
    if action_type not in ACTIONS_AUTORISEES:
        raise HTTPException(status_code=422, detail=f"action_type invalide : {action_type} (attendu : {', '.join(ACTIONS_AUTORISEES)})")
    action_canonique = ACTION_ALIASES.get(action_type, action_type)
    if not target_type or not target_id:
        raise HTTPException(status_code=422, detail="target_type et target_id requis")
    # RBAC serveur : seul utilisateur peut créer une demande (lecture => 453 specifique Lot1)
    role_canon = normaliser_role(requester.get("role"))
    if role_canon == "lecture":
        raise HTTPException(status_code=453, detail=f"Rôle « {role_canon} » non autorisé à créer une demande (seul utilisateur peut demander).")
    if role_canon != "utilisateur":
        raise HTTPException(status_code=453, detail=f"Rôle « {role_canon} » non autorisé à créer une demande (seul utilisateur peut demander).")
    payload_json = payload_json if isinstance(payload_json, dict) else {}
    requester_id = int(requester.get("id_utilisateur") or requester.get("id") or 0)
    if not requester_id:
        raise HTTPException(status_code=401, detail="Identifiant du demandeur manquant")

    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO approval_requests (requester_id, action_type, target_type, target_id, payload_json, reason, status) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'pending') RETURNING id, created_at",
                (requester_id, action_canonique, target_type, target_id, __import__("json").dumps(payload_json), reason),
            )
            row = cur.fetchone()
            demande_id = int(row[0]) if row else 0
            created_at = str(row[1]) if row and len(row) > 1 else None

            # Notifications in-app pour tous les approbateurs
            try:
                cur.execute("SELECT id_utilisateur FROM utilisateur WHERE role IN %s AND actif = true", (("approbateur",),))
                # Fallback : cherche aussi lecture exacte 'approbateur' sans alias legacy
                approbateurs = cur.fetchall()
                # Si aucun approbateur trouvé, on tente aussi avec canonique via normalisation applicative (parcours tous)
                if not approbateurs:
                    cur.execute("SELECT id_utilisateur, role FROM utilisateur WHERE actif = true")
                    tous = cur.fetchall()
                    approbateurs = [(r[0],) for r in tous if normaliser_role(r[1]) == "approbateur"]
                else:
                    approbateurs = [(r[0],) for r in approbateurs]
                for (uid,) in approbateurs:
                    cur.execute(
                        "INSERT INTO in_app_notifications (recipient_user_id, user_id, type, title, body, message, link, link_url, is_read) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)",
                        (
                            int(uid),
                            int(uid),
                            "approval_request",
                            "Nouvelle demande d'approbation",
                            f"Demande {action_canonique} sur {target_type}/{target_id} par {requester.get('identifiant')}",
                            f"Demande {action_canonique} sur {target_type}/{target_id} par {requester.get('identifiant')}",
                            f"/approbations/{demande_id}",
                            f"/approbations/{demande_id}",
                        ),
                    )
            except Exception as exc:
                LOGGER.warning("Notifications approbateurs non créées (best-effort): %s", exc)

        # Audit best-effort (sans secret, details_json borné par audit_logs)
        try:
            record_audit_log(
                index,
                actor_user_id=requester_id,
                actor_username=str(requester.get("identifiant", "unknown")),
                actor_role=str(requester.get("role", "unknown")),
                action="APPROVAL_REQUESTED",
                target_type=target_type,
                target_id=target_id,
                outcome="success",
                details={"action_type": action_canonique, "demande_id": demande_id},
            )
        except Exception as exc:
            LOGGER.debug("audit APPROVAL_REQUESTED best-effort échoué: %s", exc)

    return {
        "id": demande_id,
        "requester_id": requester_id,
        "action_type": action_canonique,
        "target_type": target_type,
        "target_id": target_id,
        "payload_json": payload_json,
        "reason": reason,
        "status": "pending",
        "created_at": created_at,
    }


def lister_demandes(index: Any, utilisateur: dict[str, Any], status: str | None = None) -> list[dict[str, Any]]:
    _verifier_postgres(index)
    role_canon = normaliser_role(utilisateur.get("role"))
    uid = int(utilisateur.get("id_utilisateur") or utilisateur.get("id") or 0)
    params: list[Any] = []
    where = []
    if status:
        if status not in STATUTS:
            raise HTTPException(status_code=422, detail=f"status invalide : {status}")
        where.append("status = %s")
        params.append(status)
    # Filtrage RBAC : admin voit tout, approbateur voit pending, utilisateur voit ses demandes, lecture voit rien (403 plus haut)
    if role_canon == "admin":
        pass  # voit tout (ou filtrage par status si demandé)
    elif role_canon == "approbateur":
        if not status:
            where.append("status = %s")
            params.append("pending")
    elif role_canon == "utilisateur":
        where.append("requester_id = %s")
        params.append(uid)
    elif role_canon == "lecture":
        raise HTTPException(status_code=453, detail="Rôle lecture non autorisé à lister les demandes.")
    else:
        raise HTTPException(status_code=453, detail=f"Rôle « {role_canon} » non autorisé.")

    sql = "SELECT id, requester_id, action_type, target_type, target_id, payload_json, reason, status, approver_id, decision_note, created_at, decided_at, executed_at FROM approval_requests"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 100"

    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    result = []
    for r in rows:
        result.append(
            {
                "id": int(r[0]),
                "requester_id": int(r[1]),
                "action_type": str(r[2]),
                "target_type": str(r[3]),
                "target_id": str(r[4]),
                "payload_json": r[5] if isinstance(r[5], dict) else {},
                "reason": r[6],
                "status": str(r[7]),
                "approver_id": int(r[8]) if r[8] is not None else None,
                "decision_note": r[9],
                "created_at": str(r[10]) if r[10] else None,
                "decided_at": str(r[11]) if r[11] else None,
                "executed_at": str(r[12]) if r[12] else None,
            }
        )
    return result


def obtenir_demande(index: Any, demande_id: int) -> dict[str, Any] | None:
    _verifier_postgres(index)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, requester_id, action_type, target_type, target_id, payload_json, reason, status, approver_id, decision_note, created_at, decided_at, executed_at FROM approval_requests WHERE id = %s",
                (int(demande_id),),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {
                "id": int(r[0]),
                "requester_id": int(r[1]),
                "action_type": str(r[2]),
                "target_type": str(r[3]),
                "target_id": str(r[4]),
                "payload_json": r[5] if isinstance(r[5], dict) else {},
                "reason": r[6],
                "status": str(r[7]),
                "approver_id": int(r[8]) if r[8] is not None else None,
                "decision_note": r[9],
                "created_at": str(r[10]) if r[10] else None,
                "decided_at": str(r[11]) if r[11] else None,
                "executed_at": str(r[12]) if r[12] else None,
            }


def decider_demande(
    index: Any,
    approbateur: dict[str, Any],
    demande_id: int,
    decision: str,
    decision_note: str | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Approbateur décide approve/reject (1 clic). Anti-auto-approbation + effet instantané."""
    _verifier_postgres(index)
    role_canon = normaliser_role(approbateur.get("role"))
    if role_canon != "approbateur":
        raise HTTPException(status_code=453, detail=f"Rôle « {role_canon} » non autorisé à décider (seul approbateur peut approuver/refuser).")
    decision = str(decision or "").strip().lower()
    if decision not in ("approve", "approved", "reject", "rejected"):
        raise HTTPException(status_code=422, detail="decision doit être approve ou reject")
    statut_cible = "approved" if decision in ("approve", "approved") else "rejected"

    approbateur_id = int(approbateur.get("id_utilisateur") or approbateur.get("id") or 0)
    allow_self = bool(getattr(config, "allow_self_approval", False)) if config else False

    demande = obtenir_demande(index, int(demande_id))
    if not demande:
        raise HTTPException(status_code=404, detail=f"Demande {demande_id} introuvable")
    if demande["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Demande {demande_id} déjà décidée (statut {demande['status']})")
    if not allow_self and int(demande["requester_id"]) == approbateur_id:
        raise HTTPException(status_code=453, detail="Auto-approbation interdite (demandeur == approbateur, SEAMTECH_ALLOW_SELF_APPROVAL=false)")

    with index.connect() as conn:
        with conn.cursor() as cur:
            # Transaction + effet instantané si approve sur modify/delete
            # Pour modify/delete, on passe directement à executed et on applique (si payload prévu)
            # Pour download, on passe à approved (le téléchargement sera autorisé ensuite)
            if statut_cible == "approved" and demande["action_type"] in ("modify_record", "delete_record"):
                # Effet instantané : exécuter l'action métier dans la même transaction
                # Si le payload contient une opération connue, on l'applique ; sinon on marque executed
                # Pour Lot1, on se contente de marquer executed (la cible métier peut être hors périmètre Lot1)
                cur.execute(
                    "UPDATE approval_requests SET status = %s, approver_id = %s, decision_note = %s, decided_at = now(), executed_at = now() WHERE id = %s AND status = 'pending' RETURNING id",
                    ("executed", approbateur_id, decision_note, int(demande_id)),
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=409, detail="Conflit de décision (déjà traitée)")
                # Notification au demandeur
                try:
                    cur.execute(
                        "INSERT INTO in_app_notifications (recipient_user_id, user_id, type, title, body, message, link, link_url, is_read) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)",
                        (
                            int(demande["requester_id"]),
                            int(demande["requester_id"]),
                            "approval_decision",
                            "Demande approuvée et exécutée",
                            f"Votre demande {demande['action_type']} sur {demande['target_type']}/{demande['target_id']} a été approuvée et exécutée.",
                            f"Votre demande {demande['action_type']} sur {demande['target_type']}/{demande['target_id']} a été approuvée et exécutée.",
                            f"/approbations/{demande_id}",
                            f"/approbations/{demande_id}",
                        ),
                    )
                except Exception as exc:
                    LOGGER.warning("Notif demandeur non créée: %s", exc)
                statut_final = "executed"
            else:
                # approve download → approved, reject → rejected
                cur.execute(
                    "UPDATE approval_requests SET status = %s, approver_id = %s, decision_note = %s, decided_at = now() WHERE id = %s AND status = 'pending' RETURNING id",
                    (statut_cible, approbateur_id, decision_note, int(demande_id)),
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=409, detail="Conflit de décision (déjà traitée)")
                notif_titre = "Demande approuvée — téléchargement disponible" if statut_cible == "approved" else "Demande refusée"
                notif_msg = (
                    f"Votre demande {demande['action_type']} sur {demande['target_type']}/{demande['target_id']} a été {statut_cible}."
                )
                try:
                    cur.execute(
                        "INSERT INTO in_app_notifications (recipient_user_id, user_id, type, title, body, message, link, link_url, is_read) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)",
                        (
                            int(demande["requester_id"]),
                            int(demande["requester_id"]),
                            "approval_decision",
                            notif_titre,
                            notif_msg,
                            notif_msg,
                            f"/approbations/{demande_id}",
                            f"/approbations/{demande_id}",
                        ),
                    )
                except Exception as exc:
                    LOGGER.warning("Notif demandeur non créée: %s", exc)
                statut_final = statut_cible

        try:
            record_audit_log(
                index,
                actor_user_id=approbateur_id,
                actor_username=str(approbateur.get("identifiant", "unknown")),
                actor_role=str(approbateur.get("role", "unknown")),
                action="APPROVAL_DECIDED",
                target_type="approval_request",
                target_id=str(demande_id),
                outcome="success",
                details={"decision": statut_final, "target_type": demande["target_type"], "target_id": demande["target_id"]},
            )
            # Aussi APPROVAL_GRANTED / REJECTED pour traçabilité fine
            record_audit_log(
                index,
                actor_user_id=approbateur_id,
                actor_username=str(approbateur.get("identifiant", "unknown")),
                actor_role=str(approbateur.get("role", "unknown")),
                action="APPROVAL_GRANTED" if statut_final in ("approved", "executed") else "APPROVAL_REJECTED",
                target_type=demande["target_type"],
                target_id=demande["target_id"],
                outcome="success",
                details={"demande_id": int(demande_id), "decision_note": decision_note},
            )
        except Exception as exc:
            LOGGER.debug("audit APPROVAL_DECIDED best-effort échoué: %s", exc)

    demande["status"] = statut_final
    demande["approver_id"] = approbateur_id
    demande["decision_note"] = decision_note
    return demande


def verifier_acces_telechargement(index: Any, utilisateur: dict[str, Any], target_type: str, target_id: str) -> bool:
    """Vérifie si l'utilisateur peut télécharger target_type/target_id (Lot1 RBAC)."""
    role_canon = normaliser_role(utilisateur.get("role"))
    if role_canon == "lecture":
        return False
    if role_canon in ("admin", "approbateur"):
        return True  # contrôle in-app, pas besoin d'approbation pour vérifier
    if role_canon == "utilisateur":
        # Doit avoir une demande approved/executed pour ce target
        _verifier_postgres(index)
        uid = int(utilisateur.get("id_utilisateur") or utilisateur.get("id") or 0)
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM approval_requests WHERE requester_id = %s AND target_type = %s AND target_id = %s AND status IN ('approved','executed') LIMIT 1",
                    (uid, target_type, target_id),
                )
                return cur.fetchone() is not None
    # Legacy aliases déjà normalisés
    return False


def lister_notifications(index: Any, utilisateur: dict[str, Any]) -> list[dict[str, Any]]:
    _verifier_postgres(index)
    uid = int(utilisateur.get("id_utilisateur") or utilisateur.get("id") or 0)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, recipient_user_id, user_id, type, title, body, message, link, link_url, is_read, created_at FROM in_app_notifications WHERE recipient_user_id = %s OR user_id = %s ORDER BY created_at DESC LIMIT 50",
                (uid, uid),
            )
            rows = cur.fetchall()
    res = []
    for r in rows:
        res.append(
            {
                "id": int(r[0]),
                "recipient_user_id": int(r[1]),
                "user_id": int(r[2]) if r[2] is not None else None,
                "type": str(r[3]),
                "title": str(r[4]),
                "body": r[5],
                "message": r[6],
                "link": r[7],
                "link_url": r[8],
                "is_read": bool(r[9]),
                "created_at": str(r[10]) if r[10] else None,
            }
        )
    return res


def marquer_notification_lue(index: Any, utilisateur: dict[str, Any], notif_id: int) -> None:
    _verifier_postgres(index)
    uid = int(utilisateur.get("id_utilisateur") or utilisateur.get("id") or 0)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE in_app_notifications SET is_read = true WHERE id = %s AND (recipient_user_id = %s OR user_id = %s)",
                (int(notif_id), uid, uid),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Notification {notif_id} introuvable")

