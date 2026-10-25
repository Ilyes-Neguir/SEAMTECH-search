"""Approbations métier instantanées — Lot 1.

Un ``utilisateur`` ne modifie pas directement une fiche et ne télécharge pas
une pièce sans autorisation : il crée une demande. Un ``approbateur`` — et
lui seul — la décide. L'approbation d'une modification ou d'une suppression
exécute l'action dans la même transaction PostgreSQL que le changement de
statut de la demande et la notification du demandeur. L'approbation d'un
téléchargement ouvre au contraire une fenêtre bornée de 24 heures : les
requêtes HTTP Range et les reprises ne doivent pas consommer l'autorisation
au premier octet.

Tout est local : PostgreSQL porte les demandes et notifications, aucun
service externe n'est appelé.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query

from .audit import record_audit_event
from .comptes.comptes import ROLE_APPROBATEUR, ROLE_UTILISATEUR
from .fiches.corrections_canoniques import resoudre_cible
from .fiches.routes import corriger_champ_sous_curseur, supprimer_fiche_sous_curseur
from .securite_requetes import ActeurRequete, exiger_acteur_nominatif

LOGGER = logging.getLogger("seamtech_search.approbations")

TYPES_DEMANDE = ("telechargement", "modification", "suppression")
STATUTS_DEMANDE = ("en_attente", "approuvee", "refusee", "annulee", "executee", "expiree")
DUREE_TELECHARGEMENT_HEURES = 24
_LONGUEUR_MOTIF_MAX = 1000
_LONGUEUR_COMMENTAIRE_MAX = 1000
_LONGUEUR_CHAMP_MAX = 200
_LONGUEUR_VALEUR_MAX = 2000

_SQL_DEMANDE = """
SELECT a.id_demande, a.type_demande, a.statut, a.motif,
       a.id_demandeur, du.identifiant, du.nom,
       a.id_fiche, f.code, f.statut, f.revision,
       a.id_piece, d.name,
       a.champ, a.rang, a.valeur_proposee, a.revision_fiche,
       a.id_decideur, de.identifiant, a.decidee_le, a.commentaire_decision,
       a.executee_le, a.expire_le, a.resultat_execution, a.cree_le, a.maj_le
FROM approval_requests a
JOIN utilisateur du ON du.id_utilisateur = a.id_demandeur
LEFT JOIN fiche f ON f.id_fiche = a.id_fiche
LEFT JOIN documents d ON d.id = a.id_piece
LEFT JOIN utilisateur de ON de.id_utilisateur = a.id_decideur
"""

_SQL_NOTIFICATION = """
SELECT n.id_notification, n.id_destinataire, n.id_demande, n.type_notification,
       n.titre, n.message, n.lue, n.lue_le, n.cree_le,
       a.type_demande, a.statut, f.code
FROM in_app_notifications n
LEFT JOIN approval_requests a ON a.id_demande = n.id_demande
LEFT JOIN fiche f ON f.id_fiche = a.id_fiche
"""


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Approbations disponibles sur PostgreSQL uniquement (décision de couche "
                "§17.1) : les tables approval_requests/in_app_notifications n'existent pas côté SQLite."
            ),
        )


def _iso(valeur: Any) -> str | None:  # noqa: ANN401
    if valeur is None:
        return None
    if isinstance(valeur, datetime):
        return valeur.isoformat()
    return str(valeur)


def _json_sure(valeur: Any) -> Any:  # noqa: ANN401
    """Valeur JSON persistable, sans Decimal/date opaques pour l'API."""
    if isinstance(valeur, dict):
        return {str(cle): _json_sure(item) for cle, item in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_json_sure(item) for item in valeur]
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, datetime):
        return valeur.isoformat()
    return valeur


def _demande_depuis_ligne(ligne: tuple[Any, ...]) -> dict[str, Any]:
    demande: dict[str, Any] = {
        "id_demande": int(ligne[0]),
        "type_demande": str(ligne[1]),
        "statut": str(ligne[2]),
        "motif": ligne[3],
        "id_demandeur": int(ligne[4]),
        "demandeur": ligne[5],
        "nom_demandeur": ligne[6],
        "id_fiche": int(ligne[7]) if ligne[7] is not None else None,
        "code_fiche": ligne[8],
        "statut_fiche": ligne[9],
        "revision_fiche_actuelle": int(ligne[10]) if ligne[10] is not None else None,
        "id_piece": int(ligne[11]) if ligne[11] is not None else None,
        "nom_piece": ligne[12],
        "champ": ligne[13],
        "rang": int(ligne[14]) if ligne[14] is not None else None,
        "valeur_proposee": ligne[15],
        "revision_fiche": int(ligne[16]) if ligne[16] is not None else None,
        "id_decideur": int(ligne[17]) if ligne[17] is not None else None,
        "decideur": ligne[18],
        "decidee_le": _iso(ligne[19]),
        "commentaire_decision": ligne[20],
        "executee_le": _iso(ligne[21]),
        "expire_le": _iso(ligne[22]),
        "resultat_execution": _json_sure(ligne[23] or {}),
        "cree_le": _iso(ligne[24]),
        "maj_le": _iso(ligne[25]),
    }
    if demande["type_demande"] == "modification":
        demande["modification"] = {
            "champ": demande["champ"],
            "rang": demande["rang"],
            "valeur": demande["valeur_proposee"],
            "revision": demande["revision_fiche"],
        }
    return demande


def _notification_depuis_ligne(ligne: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id_notification": int(ligne[0]),
        "id_destinataire": int(ligne[1]),
        "id_demande": int(ligne[2]) if ligne[2] is not None else None,
        "type_notification": str(ligne[3]),
        "titre": ligne[4],
        "message": ligne[5],
        "lue": bool(ligne[6]),
        "lue_le": _iso(ligne[7]),
        "cree_le": _iso(ligne[8]),
        "type_demande": ligne[9],
        "statut_demande": ligne[10],
        "code_fiche": ligne[11],
    }


def _texte_borne(
    valeur: Any,  # noqa: ANN401
    nom: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    texte = str(valeur or "").strip()
    if "\x00" in texte:
        raise HTTPException(status_code=422, detail=f"« {nom} » contient un octet NUL.")
    if len(texte) < minimum:
        raise HTTPException(status_code=422, detail=f"« {nom} » est obligatoire ({minimum} caractère(s) minimum).")
    if len(texte) > maximum:
        raise HTTPException(status_code=422, detail=f"« {nom} » dépasse {maximum} caractères.")
    return texte


def _entier(
    valeur: Any,  # noqa: ANN401
    nom: str,
    *,
    minimum: int = 0,
    maximum: int = 2_147_483_647,
) -> int:
    if isinstance(valeur, bool):
        raise HTTPException(status_code=422, detail=f"« {nom} » doit être un entier.")
    try:
        entier = int(valeur)
    except (TypeError, ValueError) as erreur:
        raise HTTPException(status_code=422, detail=f"« {nom} » doit être un entier.") from erreur
    if entier < minimum or entier > maximum:
        raise HTTPException(status_code=422, detail=f"« {nom} » doit être entre {minimum} et {maximum}.")
    return entier


def _auditer(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    action: str,
    demande: dict[str, Any],
    *,
    code_statut: int = 200,
) -> None:
    record_audit_event(
        index,
        action=action,
        actor=acteur.libelle_audit,
        resource=str(demande.get("code_fiche") or demande.get("id_piece") or demande.get("id_demande")),
        status=str(code_statut),
        details={
            "type_demande": demande.get("type_demande"),
            "statut": demande.get("statut"),
            "id_fiche": demande.get("id_fiche"),
            "id_piece": demande.get("id_piece"),
            "id_demandeur": demande.get("id_demandeur"),
            "id_decideur": demande.get("id_decideur"),
        },
        actor_type=acteur.type_acteur,
        id_utilisateur=acteur.id_utilisateur,
        identifiant=acteur.identifiant,
        role=acteur.role,
        id_session=acteur.id_session,
        resource_type="approval_request",
        resource_id=str(demande.get("id_demande")),
        code_statut=code_statut,
    )


def _notifier(
    curseur: Any,  # noqa: ANN401
    *,
    id_destinataire: int,
    id_demande: int,
    type_notification: str,
    titre: str,
    message: str,
) -> None:
    curseur.execute(
        "INSERT INTO in_app_notifications "
        "(id_destinataire, id_demande, type_notification, titre, message) VALUES (%s, %s, %s, %s, %s)",
        (int(id_destinataire), int(id_demande), type_notification, titre[:200], message[:1000]),
    )


def _relire_demande(curseur: Any, id_demande: int) -> dict[str, Any]:  # noqa: ANN401
    curseur.execute(_SQL_DEMANDE + " WHERE a.id_demande = %s", (int(id_demande),))
    ligne = curseur.fetchone()
    if ligne is None:
        raise HTTPException(status_code=404, detail="Demande inconnue.")
    return _demande_depuis_ligne(ligne)


def _expirer_telechargements(curseur: Any) -> None:  # noqa: ANN401
    curseur.execute(
        "UPDATE approval_requests SET statut = 'expiree', maj_le = now() "
        "WHERE type_demande = 'telechargement' AND statut = 'approuvee' "
        "AND expire_le IS NOT NULL AND expire_le <= now()"
    )


def _fiche_par_code(curseur: Any, code: str, *, verrou: bool = False) -> tuple[int, str, int]:  # noqa: ANN401
    suffixe = " FOR UPDATE" if verrou else ""
    curseur.execute(
        f"SELECT id_fiche, statut, revision FROM fiche WHERE code = %s{suffixe}",
        (code,),
    )
    ligne = curseur.fetchone()
    if ligne is None:
        raise HTTPException(status_code=404, detail=f"Fiche « {code} » inconnue.")
    return int(ligne[0]), str(ligne[1]), int(ligne[2] if ligne[2] is not None else 1)


def _resoudre_piece(
    curseur: Any,  # noqa: ANN401
    id_piece: int,
    code_fiche: str | None,
) -> tuple[int | None, str | None, str | None]:
    curseur.execute(
        "SELECT d.id, d.name, COALESCE(d.id_fiche, p.id_fiche), f.code, f.statut "
        "FROM documents d "
        "LEFT JOIN fiche_piece_jointe p ON p.id_document = d.id "
        "LEFT JOIN fiche f ON f.id_fiche = COALESCE(d.id_fiche, p.id_fiche) "
        "WHERE d.id = %s AND NOT d.is_dir ORDER BY f.id_fiche NULLS LAST",
        (int(id_piece),),
    )
    lignes = curseur.fetchall()
    if not lignes:
        raise HTTPException(status_code=404, detail="Pièce inconnue.")
    if code_fiche:
        correspondante = next((ligne for ligne in lignes if ligne[3] == code_fiche), None)
        if correspondante is None:
            raise HTTPException(
                status_code=422,
                detail=f"La pièce {id_piece} n'est pas rattachée à la fiche « {code_fiche} ».",
            )
        ligne = correspondante
    else:
        ligne = lignes[0]
    if ligne[4] == "supprimee":
        raise HTTPException(
            status_code=409,
            detail="La fiche rattachée à cette pièce est supprimée logiquement.",
        )
    return (
        int(ligne[2]) if ligne[2] is not None else None,
        str(ligne[3]) if ligne[3] is not None else None,
        str(ligne[1]) if ligne[1] is not None else None,
    )


def _valider_modification(
    curseur: Any,  # noqa: ANN401
    *,
    id_fiche: int,
    code: str,
    statut: str,
    revision_actuelle: int,
    modification: dict[str, Any],
) -> tuple[str, int | None, str, int]:
    if statut == "valide":
        raise HTTPException(
            status_code=409,
            detail="RG11 : une fiche validée ne peut recevoir une proposition de modification ; un approbateur doit d'abord la rouvrir.",
        )
    if statut == "supprimee":
        raise HTTPException(status_code=409, detail="Fiche supprimée logiquement : aucune modification ne peut être demandée.")
    champ = _texte_borne(modification.get("champ"), "modification.champ", minimum=1, maximum=_LONGUEUR_CHAMP_MAX)
    valeur_brute = modification.get("valeur")
    if valeur_brute is None:
        raise HTTPException(status_code=422, detail="« modification.valeur » est obligatoire.")
    valeur = str(valeur_brute)
    if "\x00" in valeur or len(valeur) > _LONGUEUR_VALEUR_MAX:
        raise HTTPException(status_code=422, detail=f"« modification.valeur » est invalide ou dépasse {_LONGUEUR_VALEUR_MAX} caractères.")
    rang = modification.get("rang")
    rang = _entier(rang, "modification.rang", minimum=0, maximum=100000) if rang is not None else None
    revision = _entier(modification.get("revision"), "modification.revision")
    if revision != revision_actuelle:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Fiche {code} modifiée depuis l'ouverture (révision {revision_actuelle}, "
                f"proposition fondée sur {revision}) : rechargez-la avant de demander une modification."
            ),
        )
    curseur.execute(
        "SELECT rang, table_cible, colonne_cible FROM fiche_champ_extrait "
        "WHERE id_fiche = %s AND champ = %s ORDER BY rang",
        (id_fiche, champ),
    )
    lignes = curseur.fetchall()
    if not lignes:
        raise HTTPException(status_code=404, detail=f"Champ « {champ} » inconnu sur la fiche {code}.")
    if rang is None and len(lignes) > 1:
        rangs = [int(ligne[0]) if ligne[0] is not None else None for ligne in lignes]
        raise HTTPException(status_code=422, detail=f"Champ « {champ} » présent {len(lignes)} fois — préciser rang ({rangs}).")
    if rang is not None:
        correspondante = next((ligne for ligne in lignes if ligne[0] is not None and int(ligne[0]) == rang), None)
        if correspondante is None:
            raise HTTPException(status_code=404, detail=f"Champ « {champ} » rang {rang} inconnu.")
    else:
        correspondante = lignes[0]
    cible, _raison = resoudre_cible(champ, correspondante[1], correspondante[2])
    if cible is not None:
        try:
            cible.convertir(valeur)
        except ValueError as erreur:
            raise HTTPException(status_code=422, detail=f"Valeur proposée refusée pour « {champ} » : {erreur}") from erreur
    return champ, rang, valeur, revision


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def creer_demande(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    corps: dict[str, Any],
) -> dict[str, Any]:
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.role not in {ROLE_UTILISATEUR, ROLE_APPROBATEUR} or acteur.id_utilisateur is None:
        raise HTTPException(status_code=403, detail="Seuls les rôles utilisateur et approbateur nominatifs peuvent créer une demande.")
    type_demande = _texte_borne(corps.get("type_demande"), "type_demande", minimum=1, maximum=50).lower()
    if type_demande not in TYPES_DEMANDE:
        raise HTTPException(status_code=422, detail=f"Type de demande invalide : « {type_demande} ».")
    motif = _texte_borne(corps.get("motif"), "motif", minimum=3, maximum=_LONGUEUR_MOTIF_MAX)
    code_fiche = str(corps.get("code_fiche") or corps.get("code") or "").strip() or None

    id_fiche: int | None = None
    id_piece: int | None = None
    champ: str | None = None
    rang: int | None = None
    valeur: str | None = None
    revision: int | None = None

    try:
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                _expirer_telechargements(curseur)
                if type_demande == "telechargement":
                    id_piece = _entier(corps.get("id_piece"), "id_piece", minimum=1)
                    id_fiche, code_resolu, _nom = _resoudre_piece(curseur, id_piece, code_fiche)
                    if code_fiche and code_resolu != code_fiche:
                        raise HTTPException(status_code=422, detail="La pièce et la fiche déclarées ne correspondent pas.")
                else:
                    if not code_fiche:
                        raise HTTPException(status_code=422, detail="« code_fiche » est obligatoire pour cette demande.")
                    id_fiche, statut, revision_actuelle = _fiche_par_code(curseur, code_fiche, verrou=True)
                    if type_demande == "modification":
                        modification = corps.get("modification")
                        if not isinstance(modification, dict):
                            raise HTTPException(status_code=422, detail="« modification » doit être un objet {champ, valeur, revision}.")
                        champ, rang, valeur, revision = _valider_modification(
                            curseur,
                            id_fiche=id_fiche,
                            code=code_fiche,
                            statut=statut,
                            revision_actuelle=revision_actuelle,
                            modification=modification,
                        )
                    else:
                        if statut == "supprimee":
                            raise HTTPException(status_code=409, detail="Fiche déjà supprimée logiquement.")
                        revision = _entier(corps.get("revision"), "revision")
                        if revision != revision_actuelle:
                            raise HTTPException(
                                status_code=409,
                                detail=f"Fiche {code_fiche} modifiée depuis l'ouverture : rechargez-la avant de demander sa suppression.",
                            )

                curseur.execute(
                    "INSERT INTO approval_requests "
                    "(id_demandeur, type_demande, id_fiche, id_piece, champ, rang, valeur_proposee, revision_fiche, motif) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id_demande",
                    (
                        int(acteur.id_utilisateur),
                        type_demande,
                        id_fiche,
                        id_piece,
                        champ,
                        rang,
                        valeur,
                        revision,
                        motif,
                    ),
                )
                id_demande = int(curseur.fetchone()[0])

                curseur.execute(
                    "SELECT id_utilisateur FROM utilisateur "
                    "WHERE role = %s AND actif AND id_utilisateur <> %s ORDER BY identifiant",
                    (ROLE_APPROBATEUR, int(acteur.id_utilisateur)),
                )
                libelle_fiche = code_fiche or (f"pièce #{id_piece}" if id_piece else "demande")
                for (id_approbateur,) in curseur.fetchall():
                    _notifier(
                        curseur,
                        id_destinataire=int(id_approbateur),
                        id_demande=id_demande,
                        type_notification="nouvelle_demande",
                        titre=f"Nouvelle demande : {type_demande}",
                        message=f"{acteur.identifiant} demande « {type_demande} » pour {libelle_fiche}. Motif : {motif}",
                    )
                resultat = _relire_demande(curseur, id_demande)
    except HTTPException:
        raise
    except Exception as erreur:
        if getattr(erreur, "pgcode", None) == "23505":
            raise HTTPException(
                status_code=409,
                detail="Une demande identique est déjà en attente pour ce compte et cet objet.",
            ) from erreur
        raise

    _auditer(index, acteur, "approval_creee", resultat, code_statut=201)
    return resultat


def lister_demandes(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    *,
    vue: str | None = None,
    statut: str | None = None,
    type_demande: str | None = None,
    code_fiche: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(status_code=401, detail="Un compte nominatif connecté est requis.")
    vue = (vue or ("a_decider" if acteur.role == ROLE_APPROBATEUR else "miennes")).strip().lower()
    if vue not in {"a_decider", "file", "miennes", "toutes"}:
        raise HTTPException(status_code=422, detail=f"Vue invalide : « {vue} ».")
    if vue in {"a_decider", "file", "toutes"} and acteur.role != ROLE_APPROBATEUR:
        raise HTTPException(status_code=403, detail="La file de décision est réservée aux approbateurs.")
    if statut and statut not in STATUTS_DEMANDE:
        raise HTTPException(status_code=422, detail=f"Statut invalide : « {statut} ».")
    if type_demande and type_demande not in TYPES_DEMANDE:
        raise HTTPException(status_code=422, detail=f"Type de demande invalide : « {type_demande} ».")
    limit = _entier(limit, "limit", minimum=1, maximum=100)
    offset = _entier(offset, "offset", minimum=0, maximum=1000000)

    conditions: list[str] = []
    parametres: list[Any] = []
    if vue == "miennes":
        conditions.append("a.id_demandeur = %s")
        parametres.append(int(acteur.id_utilisateur))
    elif vue in {"a_decider", "file"} and not statut:
        conditions.append("a.statut = 'en_attente'")
    if statut:
        conditions.append("a.statut = %s")
        parametres.append(statut)
    if type_demande:
        conditions.append("a.type_demande = %s")
        parametres.append(type_demande)
    if code_fiche:
        conditions.append("f.code = %s")
        parametres.append(code_fiche)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    ordre = "a.cree_le ASC, a.id_demande ASC" if vue in {"a_decider", "file"} else "a.cree_le DESC, a.id_demande DESC"

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            _expirer_telechargements(curseur)
            curseur.execute(
                "SELECT COUNT(*) FROM approval_requests a LEFT JOIN fiche f ON f.id_fiche = a.id_fiche" + where,
                tuple(parametres),
            )
            total = int(curseur.fetchone()[0])
            curseur.execute(
                _SQL_DEMANDE + where + f" ORDER BY {ordre} LIMIT %s OFFSET %s",
                tuple([*parametres, limit, offset]),
            )
            demandes = [_demande_depuis_ligne(ligne) for ligne in curseur.fetchall()]
    return {"demandes": demandes, "total": total, "limit": limit, "offset": offset, "vue": vue}


def detail_demande(index: Any, acteur: ActeurRequete, id_demande: int) -> dict[str, Any]:  # noqa: ANN401
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(status_code=401, detail="Un compte nominatif connecté est requis.")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            _expirer_telechargements(curseur)
            curseur.execute(_SQL_DEMANDE + " WHERE a.id_demande = %s", (int(id_demande),))
            ligne = curseur.fetchone()
    if ligne is None:
        raise HTTPException(status_code=404, detail="Demande inconnue.")
    demande = _demande_depuis_ligne(ligne)
    if acteur.role != ROLE_APPROBATEUR and int(demande["id_demandeur"]) != int(acteur.id_utilisateur):
        # Anti-IDOR : l'existence d'une demande d'autrui n'est pas confirmée.
        raise HTTPException(status_code=404, detail="Demande inconnue.")
    return demande


def annuler_demande(index: Any, acteur: ActeurRequete, id_demande: int) -> dict[str, Any]:  # noqa: ANN401
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(status_code=401, detail="Un compte nominatif connecté est requis.")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT id_demandeur, statut FROM approval_requests WHERE id_demande = %s FOR UPDATE",
                (int(id_demande),),
            )
            ligne = curseur.fetchone()
            if ligne is None:
                raise HTTPException(status_code=404, detail="Demande inconnue.")
            if int(ligne[0]) != int(acteur.id_utilisateur):
                raise HTTPException(status_code=403, detail="Seul le demandeur peut annuler sa demande.")
            if str(ligne[1]) != "en_attente":
                raise HTTPException(status_code=409, detail="Seule une demande en attente peut être annulée.")
            curseur.execute(
                "UPDATE approval_requests SET statut = 'annulee', maj_le = now() WHERE id_demande = %s",
                (int(id_demande),),
            )
            resultat = _relire_demande(curseur, int(id_demande))
    _auditer(index, acteur, "approval_annulee", resultat)
    return resultat


def decider_demande(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    id_demande: int,
    corps: dict[str, Any],
    *,
    allow_self_approval: bool = False,
    exiger_revision: bool = True,
) -> dict[str, Any]:
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.role != ROLE_APPROBATEUR or acteur.id_utilisateur is None:
        raise HTTPException(status_code=403, detail="Seul un approbateur nominatif peut décider une demande.")
    decision_brute = _texte_borne(corps.get("decision"), "decision", minimum=1, maximum=50).lower()
    if decision_brute in {"approuver", "approuvee", "approve", "approved"}:
        approuver = True
    elif decision_brute in {"refuser", "refusee", "reject", "rejected"}:
        approuver = False
    else:
        raise HTTPException(status_code=422, detail="« decision » doit valoir « approuver » ou « refuser ».")
    commentaire = str(corps.get("commentaire") or "").strip()
    if "\x00" in commentaire or len(commentaire) > _LONGUEUR_COMMENTAIRE_MAX:
        raise HTTPException(status_code=422, detail=f"« commentaire » est invalide ou dépasse {_LONGUEUR_COMMENTAIRE_MAX} caractères.")
    if not approuver and not commentaire:
        raise HTTPException(status_code=422, detail="Un refus exige un commentaire explicite pour le demandeur.")
    revision_confirmee = corps.get("revision_confirmee")
    if revision_confirmee is not None:
        revision_confirmee = _entier(revision_confirmee, "revision_confirmee")

    from psycopg2.extras import Json

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            _expirer_telechargements(curseur)
            curseur.execute(
                "SELECT a.id_demandeur, a.type_demande, a.statut, a.id_fiche, a.id_piece, "
                "a.champ, a.rang, a.valeur_proposee, a.revision_fiche, f.code "
                "FROM approval_requests a LEFT JOIN fiche f ON f.id_fiche = a.id_fiche "
                "WHERE a.id_demande = %s FOR UPDATE OF a",
                (int(id_demande),),
            )
            ligne = curseur.fetchone()
            if ligne is None:
                raise HTTPException(status_code=404, detail="Demande inconnue.")
            (
                id_demandeur,
                type_demande,
                statut,
                id_fiche,
                id_piece,
                champ,
                rang,
                valeur_proposee,
                revision_stockee,
                code_fiche,
            ) = ligne
            if str(statut) != "en_attente":
                raise HTTPException(status_code=409, detail="Cette demande a déjà été décidée ou annulée.")
            if int(id_demandeur) == int(acteur.id_utilisateur) and not allow_self_approval:
                raise HTTPException(
                    status_code=403,
                    detail="Auto-approbation interdite : un autre approbateur doit décider cette demande.",
                )

            if not approuver:
                curseur.execute(
                    "UPDATE approval_requests SET statut = 'refusee', id_decideur = %s, decidee_le = now(), "
                    "commentaire_decision = %s, maj_le = now() WHERE id_demande = %s",
                    (int(acteur.id_utilisateur), commentaire, int(id_demande)),
                )
                _notifier(
                    curseur,
                    id_destinataire=int(id_demandeur),
                    id_demande=int(id_demande),
                    type_notification="decision",
                    titre="Demande refusée",
                    message=f"Votre demande « {type_demande} » a été refusée par {acteur.identifiant}. Motif : {commentaire}",
                )
                resultat = _relire_demande(curseur, int(id_demande))
            elif type_demande == "telechargement":
                curseur.execute(
                    "UPDATE approval_requests SET statut = 'approuvee', id_decideur = %s, decidee_le = now(), "
                    "commentaire_decision = %s, expire_le = now() + (%s * interval '1 hour'), maj_le = now() "
                    "WHERE id_demande = %s",
                    (int(acteur.id_utilisateur), commentaire or None, DUREE_TELECHARGEMENT_HEURES, int(id_demande)),
                )
                _notifier(
                    curseur,
                    id_destinataire=int(id_demandeur),
                    id_demande=int(id_demande),
                    type_notification="decision",
                    titre="Téléchargement approuvé",
                    message=(
                        f"Votre demande de téléchargement a été approuvée par {acteur.identifiant} "
                        f"pour {DUREE_TELECHARGEMENT_HEURES} heures."
                    ),
                )
                resultat = _relire_demande(curseur, int(id_demande))
            else:
                if not code_fiche or id_fiche is None:
                    raise HTTPException(status_code=409, detail="La demande ne désigne plus de fiche exécutable.")
                revision_execution = revision_confirmee if revision_confirmee is not None else revision_stockee
                commentaire_execution = (
                    f"Demande #{int(id_demande)} approuvée par {acteur.identifiant}"
                    + (f" — {commentaire}" if commentaire else "")
                )
                if type_demande == "modification":
                    execution = corriger_champ_sous_curseur(
                        curseur,
                        index,
                        str(code_fiche),
                        str(champ),
                        str(valeur_proposee),
                        str(acteur.identifiant),
                        id_utilisateur_explicite=int(acteur.id_utilisateur),
                        rang=int(rang) if rang is not None else None,
                        commentaire=commentaire_execution,
                        revision=revision_execution,
                        exiger_revision=exiger_revision,
                    )
                else:
                    execution = supprimer_fiche_sous_curseur(
                        curseur,
                        str(code_fiche),
                        id_utilisateur=int(acteur.id_utilisateur),
                        commentaire=commentaire_execution,
                        revision=revision_execution,
                        exiger_revision=exiger_revision,
                    )
                curseur.execute(
                    "UPDATE approval_requests SET statut = 'executee', id_decideur = %s, decidee_le = now(), "
                    "commentaire_decision = %s, executee_le = now(), resultat_execution = %s, maj_le = now() "
                    "WHERE id_demande = %s",
                    (
                        int(acteur.id_utilisateur),
                        commentaire or None,
                        Json(_json_sure(execution)),
                        int(id_demande),
                    ),
                )
                _notifier(
                    curseur,
                    id_destinataire=int(id_demandeur),
                    id_demande=int(id_demande),
                    type_notification="execution",
                    titre="Demande approuvée et exécutée",
                    message=f"Votre demande « {type_demande} » sur {code_fiche} a été exécutée par {acteur.identifiant}.",
                )
                resultat = _relire_demande(curseur, int(id_demande))

    _auditer(
        index,
        acteur,
        "approval_refusee" if not approuver else ("approval_executee" if resultat["statut"] == "executee" else "approval_approuvee"),
        resultat,
    )
    return resultat


def autoriser_telechargement_piece(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    id_piece: int,
    *,
    methode: str = "GET",
) -> dict[str, Any]:
    """Décide un téléchargement direct de pièce selon la matrice Lot 1."""
    if acteur.type_acteur == "service":
        return {"mode": "service"}
    if acteur.role == ROLE_APPROBATEUR and acteur.type_acteur == "utilisateur":
        return {"mode": "approbateur"}
    if acteur.role != ROLE_UTILISATEUR or acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(
            status_code=403,
            detail="Téléchargement direct interdit pour ce rôle : seul un approbateur télécharge directement ; un utilisateur exige une demande approuvée.",
        )
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            _expirer_telechargements(curseur)
            curseur.execute(
                "SELECT id_demande, executee_le FROM approval_requests "
                "WHERE id_demandeur = %s AND id_piece = %s AND type_demande = 'telechargement' "
                "AND statut = 'approuvee' AND expire_le > now() "
                "ORDER BY expire_le DESC, id_demande DESC LIMIT 1 FOR UPDATE",
                (int(acteur.id_utilisateur), int(id_piece)),
            )
            ligne = curseur.fetchone()
            if ligne is None:
                raise HTTPException(
                    status_code=403,
                    detail="Téléchargement non autorisé : créez une demande et attendez la décision d'un approbateur.",
                )
            id_demande = int(ligne[0])
            if methode.upper() == "GET" and ligne[1] is None:
                curseur.execute(
                    "UPDATE approval_requests SET executee_le = now(), maj_le = now() WHERE id_demande = %s",
                    (id_demande,),
                )
    return {"mode": "demande_approuvee", "id_demande": id_demande}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def lister_notifications(
    index: Any,  # noqa: ANN401
    acteur: ActeurRequete,
    *,
    seulement_non_lues: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(status_code=401, detail="Un compte nominatif connecté est requis.")
    limit = _entier(limit, "limit", minimum=1, maximum=100)
    offset = _entier(offset, "offset", minimum=0, maximum=1000000)
    condition = " AND NOT n.lue" if seulement_non_lues else ""
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT COUNT(*) FROM in_app_notifications WHERE id_destinataire = %s AND NOT lue",
                (int(acteur.id_utilisateur),),
            )
            non_lues = int(curseur.fetchone()[0])
            curseur.execute(
                _SQL_NOTIFICATION
                + f" WHERE n.id_destinataire = %s{condition} ORDER BY n.cree_le DESC, n.id_notification DESC LIMIT %s OFFSET %s",
                (int(acteur.id_utilisateur), limit, offset),
            )
            notifications = [_notification_depuis_ligne(ligne) for ligne in curseur.fetchall()]
    return {"notifications": notifications, "non_lues": non_lues, "limit": limit, "offset": offset}


def marquer_notification_lue(index: Any, acteur: ActeurRequete, id_notification: int) -> dict[str, Any]:  # noqa: ANN401
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(status_code=401, detail="Un compte nominatif connecté est requis.")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "UPDATE in_app_notifications SET lue = true, lue_le = now() "
                "WHERE id_notification = %s AND id_destinataire = %s AND NOT lue",
                (int(id_notification), int(acteur.id_utilisateur)),
            )
            curseur.execute(
                _SQL_NOTIFICATION + " WHERE n.id_notification = %s AND n.id_destinataire = %s",
                (int(id_notification), int(acteur.id_utilisateur)),
            )
            ligne = curseur.fetchone()
    if ligne is None:
        raise HTTPException(status_code=404, detail="Notification inconnue.")
    return _notification_depuis_ligne(ligne)


def marquer_notifications_lues(index: Any, acteur: ActeurRequete) -> dict[str, Any]:  # noqa: ANN401
    _exiger_postgres(index)
    if acteur.type_acteur != "utilisateur" or acteur.id_utilisateur is None:
        raise HTTPException(status_code=401, detail="Un compte nominatif connecté est requis.")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "UPDATE in_app_notifications SET lue = true, lue_le = now() "
                "WHERE id_destinataire = %s AND NOT lue",
                (int(acteur.id_utilisateur),),
            )
            marquees = int(curseur.rowcount)
    return {"ok": True, "marquees_lues": marquees, "non_lues": 0}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def enregistrer_routes_approbations(
    app: Any,  # noqa: ANN401
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    verifier_auth: Any,  # noqa: ANN401
) -> None:
    """Monte `/approvals` et `/notifications` sur l'application FastAPI."""

    def _acteur(
        token: str | None,
        utilisateur: str | None,
        role: str | None,
        session: str | None,
        jeton: str | None,
        roles: tuple[str, ...] | None = None,
    ) -> ActeurRequete:
        return exiger_acteur_nominatif(
            index,
            config,
            roles,
            token=token,
            entete_utilisateur=utilisateur,
            entete_role=role,
            entete_session=session,
            entete_jeton=jeton,
        )

    @app.post("/approvals", status_code=201)
    def route_creer_demande(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token,
            entete_utilisateur,
            entete_role,
            entete_session,
            entete_jeton,
            (ROLE_UTILISATEUR, ROLE_APPROBATEUR),
        )
        return creer_demande(index, acteur, corps)

    @app.get("/approvals")
    def route_lister_demandes(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        vue: str | None = Query(default=None, max_length=50),
        statut: str | None = Query(default=None, max_length=50),
        type_demande: str | None = Query(default=None, max_length=50),
        code_fiche: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return lister_demandes(
            index,
            acteur,
            vue=vue,
            statut=statut,
            type_demande=type_demande,
            code_fiche=code_fiche,
            limit=limit,
            offset=offset,
        )

    @app.get("/approvals/{id_demande}")
    def route_detail_demande(
        id_demande: int,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return detail_demande(index, acteur, id_demande)

    @app.post("/approvals/{id_demande}/decide")
    def route_decider_demande(
        id_demande: int,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(
            token,
            entete_utilisateur,
            entete_role,
            entete_session,
            entete_jeton,
            (ROLE_APPROBATEUR,),
        )
        return decider_demande(
            index,
            acteur,
            id_demande,
            corps,
            allow_self_approval=bool(getattr(config, "allow_self_approval", False)),
            exiger_revision=bool(getattr(config, "require_revision", True)),
        )

    @app.post("/approvals/{id_demande}/annuler")
    def route_annuler_demande(
        id_demande: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return annuler_demande(index, acteur, id_demande)

    @app.get("/notifications")
    def route_lister_notifications(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        seulement_non_lues: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return lister_notifications(
            index,
            acteur,
            seulement_non_lues=seulement_non_lues,
            limit=limit,
            offset=offset,
        )

    @app.post("/notifications/tout-lire")
    def route_tout_lire(
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return marquer_notifications_lues(index, acteur)

    @app.post("/notifications/{id_notification}/lu")
    def route_notification_lue(
        id_notification: int,
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        return marquer_notification_lue(index, acteur, id_notification)
