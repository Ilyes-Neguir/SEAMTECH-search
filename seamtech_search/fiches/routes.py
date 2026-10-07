"""Routes HTTP de traçabilité et du registre de gabarits (Lot B.2 — §17.11).

End-points (LIRE et PUBLIER uniquement — aucune écriture de fiche ici) :
- ``GET /fiches/{code}/champs`` : les lignes de ``fiche_champ_extrait`` d'une
  fiche (valeur brute, normalisée, méthode, confiance, page, zone, version de
  gabarit, corrections) — l'écran Fiche (lot D) consommera exactement cette forme ;
- ``GET /gabarits`` : registre (dernière version par code) ;
- ``GET /gabarits/{code}/versions`` : toutes les versions d'un code ;
- ``POST /gabarits/{code}/versions`` : publie une NOUVELLE version (max+1) —
  jamais destructive : une version publiée reste lisible ;
- ``POST /gabarits/detecter`` : détection sur un PDF (corps brut
  ``application/pdf``) SANS rien écrire en base.

Couche : PostgreSQL uniquement (§17.1) — les tables ``fiche*``/``gabarit``
n'existent pas côté SQLite, les routes répondent 503 avec l'explication.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.responses import Response

from seamtech_search.comptes.comptes import resoudre_attribution
from seamtech_search.fiches.depot import (
    DepotImpossible,
    creer_lot,
    deposer_dossier,
    etat_lot,
    executer_lot,
    lister_lots,
)
from seamtech_search.fiches.extraction import (
    CONFIANCE_CERTAIN,
    CONFIANCE_LUE,
    ExtractionImpossible,
    analyser_pdf,
    texte_normalise,
)
from seamtech_search.fiches.gabarits import GabaritInconnu, charger_gabarits, detecter_gabarit
from seamtech_search.fiches.persistance import verifier_autorisation_validation_lot
from seamtech_search.import_pipeline import staging_root
from seamtech_search.jobs import create_job, register_job_cancel
from seamtech_search.redis_store import RedisStore
from seamtech_search.storage import S3StorageClient, StorageError

LOGGER = logging.getLogger("seamtech_search.fiches.routes")

TAILLE_PDF_MAX = 20 * 1024 * 1024  # 20 Mo : une fiche technique ne dépasse pas ça

_SQL_CHAMPS = (
    "SELECT e.champ, e.rang, e.table_cible, e.colonne_cible, e.valeur_brute, e.valeur_normalisee, "
    "e.methode, e.confiance, e.page, e.zone, e.version_gabarit, e.corrige, e.corrige_par, e.corrige_le "
    "FROM fiche_champ_extrait e JOIN fiche f ON f.id_fiche = e.id_fiche "
    "WHERE f.code = %s ORDER BY e.id_champ"
)
_SQL_FICHE_EXISTE = "SELECT id_fiche FROM fiche WHERE code = %s"
_SQL_GABARITS_DERNIERES = (
    "SELECT DISTINCT ON (code) code, version, description, ancres_detection, actif, nb_fiches "
    "FROM gabarit ORDER BY code, version DESC"
)
_SQL_VERSIONS = (
    "SELECT version, description, ancres_detection, actif, nb_fiches, created_at "
    "FROM gabarit WHERE code = %s ORDER BY version"
)
_SQL_PROCHAINE_VERSION = "SELECT COALESCE(MAX(version), 0) + 1 FROM gabarit WHERE code = %s"
_SQL_PUBLIER = (
    "INSERT INTO gabarit (code, version, description, regles, ancres_detection, actif) "
    "VALUES (%s, %s, %s, %s, %s, true) RETURNING version"
)
_SQL_RETIRED_VERSIONS = "UPDATE gabarit SET actif = false WHERE code = %s AND version < %s"


def _exiger_postgres(index: Any) -> None:
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de "
                "couche §17.1 du plan v3.0) : configurer database_url (migrations 006-009)."
            ),
        )


def champs_de_fiche(index: Any, code: str) -> list[dict[str, Any]]:
    """Trace de tous les champs extraits d'une fiche (→ écran Fiche, lot D)."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_FICHE_EXISTE, (code,))
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail=f"Fiche « {code} » inconnue.")
            cursor.execute(_SQL_CHAMPS, (code,))
            colonnes = [
                "champ",
                "rang",
                "table_cible",
                "colonne_cible",
                "valeur_brute",
                "valeur_normalisee",
                "methode",
                "confiance",
                "page",
                "zone",
                "version_gabarit",
                "corrige",
                "corrige_par",
                "corrige_le",
            ]
            return [dict(zip(colonnes, ligne)) for ligne in cursor.fetchall()]


def lister_gabarits(index: Any) -> list[dict[str, Any]]:
    """Registre : la dernière version de chaque gabarit."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_GABARITS_DERNIERES)
            return [
                {
                    "code": code,
                    "version": version,
                    "description": description,
                    "nb_ancres": len(ancres or []),
                    "ancres_detection": list(ancres or []),
                    "actif": actif,
                    "nb_fiches": nb_fiches,
                }
                for code, version, description, ancres, actif, nb_fiches in cursor.fetchall()
            ]


def versions_de_gabarit(index: Any, code: str) -> list[dict[str, Any]]:
    """Toutes les versions d'un gabarit (une version publiée reste lisible)."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_VERSIONS, (code,))
            lignes = cursor.fetchall()
    if not lignes:
        raise HTTPException(status_code=404, detail=f"Gabarit « {code} » inconnu.")
    return [
        {
            "version": version,
            "description": description,
            "nb_ancres": len(ancres or []),
            "actif": actif,
            "nb_fiches": nb_fiches,
            "cree_le": None if cree_le is None else cree_le.isoformat(),
        }
        for version, description, ancres, actif, nb_fiches, cree_le in lignes
    ]


def publier_nouvelle_version(
    index: Any,
    code: str,
    description: str,
    ancres_detection: list[str],
    regles: dict[str, Any],
) -> dict[str, Any]:
    """Publie une NOUVELLE version (max+1) — JAMAIS destructive : les versions
    précédentes restent consultables (actif=false, jamais supprimées) ; la
    nouvelle devient la seule active (détection/extraction non ambiguës)."""
    _exiger_postgres(index)
    if not ancres_detection:
        raise HTTPException(status_code=422, detail="ancres_detection ne peut pas être vide : un gabarit sans ancre ne détecte rien.")
    if not isinstance(regles, dict) or not regles:
        raise HTTPException(status_code=422, detail="regles (JSONB) est requis : au moins une règle de champ.")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_PROCHAINE_VERSION, (code,))
            version = int(cursor.fetchone()[0])
            import json as _json

            cursor.execute(
                _SQL_PUBLIER,
                (code, version, description, _json.dumps(regles, ensure_ascii=False), list(ancres_detection)),
            )
            version_publiee = int(cursor.fetchone()[0])
            cursor.execute(_SQL_RETIRED_VERSIONS, (code, version_publiee))
    LOGGER.info("Gabarit %s : version %d publiée (les versions précédentes restent lisibles).", code, version_publiee)
    return {"code": code, "version": version_publiee, "nb_ancres": len(ancres_detection), "nb_regles": len(regles)}


def detecter_pdf(index: Any, chemin_pdf: Path) -> dict[str, Any]:
    """Détection seule sur un PDF — RIEN n'est écrit en base (§17.11).

    Une non-détection est un RÉSULTAT (voie « reprise complète »), pas une
    erreur : réponse 200 avec ``detecte: false`` et son explication.
    """
    _exiger_postgres(index)
    gabarits = charger_gabarits(index)
    pages = analyser_pdf(chemin_pdf)
    texte = texte_normalise(pages)
    scores = {gabarit.code: gabarit.score_detection(texte) for gabarit in gabarits}
    try:
        gabarit = detecter_gabarit(texte, gabarits)
    except GabaritInconnu as erreur:
        return {"detecte": False, "voie": "reprise_complete", "scores": scores, "detail": str(erreur)}
    ancres_trouvees = [ancre for ancre in gabarit.ancres_detection if any(ancre.lower() in page_brute for page_brute in [texte])]
    return {
        "detecte": True,
        "gabarit": gabarit.code,
        "version": gabarit.version,
        "score": scores.get(gabarit.code, 0),
        "ancres_trouvees": ancres_trouvees,
        "scores": scores,
        "pages": len(pages),
    }




# ---------------------------------------------------------------------------
# Lot D — workflow de validation (§17.5) : corriger / valider / rejeter /
# rouvrir, file de validation, validation groupée (verrou de calibration).
# Règles non négociables : comptes PAR PALIER (jamais de « confiance moyenne »),
# aucune écriture « valide » sans décision explicite (RG3), une valeur corrigée
# par un humain n'est jamais écrasée (RG11).
# ---------------------------------------------------------------------------

_SQL_JOURNAL = (
    "INSERT INTO fiche_validation (id_fiche, id_utilisateur, action, etat_avant, etat_apres, commentaire) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


def _attribution(
    config: Any,
    token: str | None,
    corps_utilisateur: Any,  # noqa: ANN401 - valeur brute du corps JSON
    entete_utilisateur: str | None,
    entete_role: str | None,
) -> str | None:
    """Qui agit ? — L.2 : la SESSION prime, le corps n'est qu'un repli.

    Jusqu'à L.2, l'attribution venait EXCLUSIVEMENT de ``corps["utilisateur"]``,
    c'est-à-dire du navigateur : n'importe qui pouvait valider une fiche au nom
    d'un autre en changeant un champ JSON. Désormais l'identité vient des
    en-têtes posés par le proxy serveur depuis la session nominative
    (``X-SEAMTECH-UTILISATEUR`` / ``X-SEAMTECH-ROLE``), et ``resoudre_attribution``
    refuse (401) ces en-têtes si ``X-SEAMTECH-TOKEN`` n'est pas valide sur la
    même requête. Le corps reste accepté en repli pour l'outillage existant
    (scripts, tests), mais il ne peut plus usurper une session.
    """
    identifiant, _role = resoudre_attribution(
        config, token, entete_utilisateur, entete_role, corps_utilisateur
    )
    return identifiant


def _resoudre_utilisateur(cursor: Any, identifiant: str | None) -> int | None:
    """Résout (ou crée, rôle opérateur) l'identifiant d'opérateur : le journal
    de validation doit porter QUI a décidé (traçabilité §10.1)."""
    if not identifiant:
        return None
    cursor.execute("SELECT id_utilisateur FROM utilisateur WHERE identifiant = %s", (identifiant,))
    ligne = cursor.fetchone()
    if ligne is not None:
        return int(ligne[0])
    cursor.execute("INSERT INTO utilisateur (identifiant) VALUES (%s) RETURNING id_utilisateur", (identifiant,))
    LOGGER.info("Utilisateur « %s » créé (rôle opérateur) — première action de validation.", identifiant)
    return int(cursor.fetchone()[0])


def _jouter_journal(
    cursor: Any, id_fiche: int, id_utilisateur: int | None, action: str,
    avant: str | None, apres: str | None, commentaire: str | None,
) -> None:
    cursor.execute(_SQL_JOURNAL, (id_fiche, id_utilisateur, action, avant, apres, commentaire))


def _fiche_statut(cursor: Any, code: str) -> tuple[int, str]:
    cursor.execute("SELECT id_fiche, statut FROM fiche WHERE code = %s", (code,))
    ligne = cursor.fetchone()
    if ligne is None:
        raise HTTPException(status_code=404, detail=f"Fiche {code} inconnue.")
    return int(ligne[0]), str(ligne[1])


def corriger_champ(
    index: Any, code: str, champ: str, valeur: str, utilisateur: str | None,
    rang: int | None = None, commentaire: str | None = None,
) -> dict[str, Any]:
    """Corrige UN champ (RG11 : refusé sur une fiche validée ; la correction
    est tracée sur la ligne fiche_champ_extrait ET au journal)."""
    if not champ:
        raise HTTPException(status_code=422, detail="Préciser le champ à corriger.")
    if valeur is None:
        raise HTTPException(status_code=422, detail="Préciser la valeur corrigée.")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            id_fiche, statut = _fiche_statut(cursor, code)
            if statut == "valide":
                raise HTTPException(
                    status_code=409,
                    detail="RG11 : fiche validée — rouvrez-la d'abord (POST /fiches/{code}/rouvrir) avant de corriger.",
                )
            cursor.execute(
                "SELECT id_champ, rang, valeur_brute, valeur_normalisee FROM fiche_champ_extrait "
                "WHERE id_fiche = %s AND champ = %s ORDER BY rang",
                (id_fiche, champ),
            )
            lignes = cursor.fetchall()
            if not lignes:
                raise HTTPException(status_code=404, detail=f"Champ « {champ} » inconnu sur la fiche {code}.")
            rangs = [None if x[1] is None else int(x[1]) for x in lignes]
            if rang is None:
                if len(lignes) > 1:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Champ « {champ} » présent {len(lignes)} fois — préciser rang ({rangs}).",
                    )
                ligne_cible = lignes[0]
            else:
                correspondantes = [x for x in lignes if x[1] is not None and int(x[1]) == rang]
                if not correspondantes:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Champ « {champ} » rang {rang} inconnu (rangs : {rangs}).",
                    )
                ligne_cible = correspondantes[0]
            rang_cible = None if ligne_cible[1] is None else int(ligne_cible[1])
            id_champ, brute, avant = int(ligne_cible[0]), ligne_cible[2], ligne_cible[3]
            id_utilisateur = _resoudre_utilisateur(cursor, utilisateur)
            cursor.execute(
                "UPDATE fiche_champ_extrait SET valeur_normalisee = %s, corrige = TRUE, corrige_par = %s, corrige_le = now() "
                "WHERE id_champ = %s",
                (str(valeur), id_utilisateur, id_champ),
            )
            _jouter_journal(cursor, id_fiche, id_utilisateur, "corriger", statut, statut,
                            commentaire or f"{champ} (rang {rang_cible if rang_cible is not None else '—'}) : {avant!r} → {str(valeur)!r}")
            LOGGER.info(
                "Champ %s (rang %s) de %s corrigé : %r → %r — conséquence : corrige=TRUE, verrou RG11 armé sur cette fiche.",
                champ, rang_cible, code, avant, str(valeur),
            )
            return {"code": code, "champ": champ, "rang": rang_cible, "valeur_brute": brute, "avant": avant, "apres": str(valeur)}


def valider_fiche(index: Any, code: str, utilisateur: str | None, commentaire: str | None = None) -> dict[str, Any]:
    """a_valider → valide (RG3 : c'est une DÉCISION explicite, jamais un effet de bord)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            id_fiche, statut = _fiche_statut(cursor, code)
            if statut != "a_valider":
                conseil = " — rouvrez-la d'abord (POST /fiches/{code}/rouvrir)." if statut == "rejete" else ""
                raise HTTPException(status_code=409, detail=f"Fiche {code} en statut « {statut} » : seule une fiche a_valider peut être validée{conseil}")
            id_utilisateur = _resoudre_utilisateur(cursor, utilisateur)
            cursor.execute("UPDATE fiche SET statut = 'valide', updated_at = now() WHERE id_fiche = %s", (id_fiche,))
            # Lot E, articulé avec la migration 018 : le texte de recherche est
            # rempli dès l'ÉCRITURE (fiche badgée « non vérifiée ») ; la VALIDATION
            # le rafraîchit dans la même transaction et fait passer la fiche dans
            # l'archive de confiance (le badge disparaît).
            cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))
            _jouter_journal(cursor, id_fiche, id_utilisateur, "valider", statut, "valide", commentaire)
            LOGGER.info(
                "Fiche %s VALIDÉE par %s — conséquence : verrou RG11 (aucune ré-extraction ne l'écrase) "
                "et texte de recherche pondéré rafraîchi (visible par GET /recherche).",
                code, utilisateur,
            )
            return {"code": code, "statut": "valide"}


def rejeter_fiche(index: Any, code: str, utilisateur: str | None, motif: str) -> dict[str, Any]:
    """a_valider → rejete (motif OBLIGATOIRE — un rejet sans raison n'est pas traçable)."""
    if not motif or not motif.strip():
        raise HTTPException(status_code=422, detail="Motif OBLIGATOIRE pour rejeter une fiche (traçabilité).")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            id_fiche, statut = _fiche_statut(cursor, code)
            if statut != "a_valider":
                raise HTTPException(status_code=409, detail=f"Fiche {code} en statut « {statut} » : seule une fiche a_valider peut être rejetée.")
            id_utilisateur = _resoudre_utilisateur(cursor, utilisateur)
            cursor.execute("UPDATE fiche SET statut = 'rejete', updated_at = now() WHERE id_fiche = %s", (id_fiche,))
            _jouter_journal(cursor, id_fiche, id_utilisateur, "rejeter", statut, "rejete", motif.strip())
            LOGGER.info("Fiche %s rejetée par %s (motif : %s).", code, utilisateur, motif.strip())
            return {"code": code, "statut": "rejete"}


def rouvrir_fiche(
    index: Any, code: str, utilisateur: str | None, commentaire: str | None = None,
    effacer_corrections: bool = False,
) -> dict[str, Any]:
    """valide/rejete → a_valider. Avec effacer_corrections=true : lève le verrou
    RG11 en EFFAÇANT explicitement les corrections humaines (acquittement)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            id_fiche, statut = _fiche_statut(cursor, code)
            if statut == "a_valider" and not effacer_corrections:
                raise HTTPException(status_code=409, detail=f"Fiche {code} déjà a_valider.")
            corrections_effacees = False
            if effacer_corrections:
                cursor.execute(
                    "UPDATE fiche_champ_extrait SET corrige = FALSE, corrige_par = NULL, corrige_le = NULL WHERE id_fiche = %s AND corrige",
                    (id_fiche,),
                )
                corrections_effacees = cursor.rowcount > 0
            if statut != "a_valider":
                cursor.execute("UPDATE fiche SET statut = 'a_valider', updated_at = now() WHERE id_fiche = %s", (id_fiche,))
            id_utilisateur = _resoudre_utilisateur(cursor, utilisateur)
            motif_journal = commentaire or ""
            if corrections_effacees:
                motif_journal = (motif_journal + " ; " if motif_journal else "") + "corrections humaines effacées (verrou RG11 levé explicitement)"
            _jouter_journal(cursor, id_fiche, id_utilisateur, "rouvrir", statut, "a_valider", motif_journal or None)
            LOGGER.info("Fiche %s rouverte (%s → a_valider) par %s%s — conséquence : ré-extraction possible.", code, statut, utilisateur, " avec effacement des corrections" if corrections_effacees else "")
            return {"code": code, "statut": "a_valider", "corrections_effacees": corrections_effacees}


def fichier_validation(index: Any, gabarit: str | None = None, anomalie: str | None = None) -> list[dict[str, Any]]:
    """File des fiches a_valider, triée par confiance croissante (les plus
    incertaines d'abord). Comptes PAR PALIER — JAMAIS de « confiance moyenne »."""
    # ordre exact des %s dans le SQL : certain (>= CERTAIN), lu (>= LUE ET < CERTAIN),
    # décomposé (>= 0,85 ET < LUE), partiel (< 0,85 ou sans confiance)
    parametres: list[Any] = [CONFIANCE_CERTAIN, CONFIANCE_LUE, CONFIANCE_CERTAIN, 0.85, CONFIANCE_LUE, 0.85]
    filtres = ""
    if gabarit:
        filtres += " AND g.code = %s"
        parametres.append(gabarit)
    if anomalie:
        filtres += " AND EXISTS (SELECT 1 FROM fiche_anomalie a WHERE a.id_fiche = f.id_fiche AND a.code = %s)"
        parametres.append(anomalie)
    sql = (
        # mêmes sémantiques que compter_par_palier : seuls les champs NOTÉS
        # (valeur_normalisee présente) comptent ; confiance absente = palier partiel
        "SELECT f.code, COALESCE(f.titre, ''), f.score_qualite, COALESCE(g.code, ''), "
        "COUNT(c.id_champ) FILTER (WHERE c.valeur_normalisee IS NOT NULL), "
        "COUNT(c.id_champ) FILTER (WHERE c.valeur_normalisee IS NOT NULL AND c.confiance >= %s), "
        "COUNT(c.id_champ) FILTER (WHERE c.valeur_normalisee IS NOT NULL AND c.confiance >= %s AND c.confiance < %s), "
        # palier « décomposé » : même borne 0,85 que compter_par_palier (échelle ordinale §4)
        "COUNT(c.id_champ) FILTER (WHERE c.valeur_normalisee IS NOT NULL AND c.confiance >= %s AND c.confiance < %s), "
        "COUNT(c.id_champ) FILTER (WHERE c.valeur_normalisee IS NOT NULL AND (c.confiance < %s OR c.confiance IS NULL)), "
        "MIN(c.confiance) FILTER (WHERE c.valeur_normalisee IS NOT NULL), "
        "EXISTS (SELECT 1 FROM fiche_anomalie a WHERE a.id_fiche = f.id_fiche AND a.statut = 'a_traiter') "
        "FROM fiche f "
        "LEFT JOIN fiche_champ_extrait c ON c.id_fiche = f.id_fiche "
        "LEFT JOIN gabarit g ON g.id_gabarit = f.id_gabarit "
        "WHERE f.statut = 'a_valider'" + filtres + " "
        "GROUP BY f.id_fiche, g.code "
        "ORDER BY MIN(c.confiance) ASC NULLS LAST, f.code"
    )
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(sql, tuple(parametres))
            lignes = cursor.fetchall()
    return [
        {
            "code": ligne[0],
            "titre": ligne[1],
            "score_qualite": float(ligne[2]) if ligne[2] is not None else None,
            "gabarit": ligne[3],
            "nb_champs": int(ligne[4]),
            # échelle ORDINALE : des comptes par palier, jamais une moyenne
            "paliers": {"certain": int(ligne[5]), "lu": int(ligne[6]), "decompose": int(ligne[7]), "partiel": int(ligne[8])},
            "confiance_min": float(ligne[9]) if ligne[9] is not None else None,
            "a_anomalies": bool(ligne[10]),
        }
        for ligne in lignes
    ]


def valider_lot(
    index: Any, codes: list[str], utilisateur: str | None,
    acquittement_humain: bool = False, commentaire: str | None = None,
) -> dict[str, Any]:
    """Validation GROUPÉE — la seule opération qui peut entériner une erreur
    systématique sur 10 000 fiches : verrou de calibration OBLIGATOIRE
    (409 tant que calibre:false, sauf acquittement humain explicite).
    Un code ignoré est un RÉSULTAT avec raison ; le lot n'est pas cassé."""
    autorise, message = verifier_autorisation_validation_lot(None, acquittement_humain)
    if not autorise:
        raise HTTPException(status_code=409, detail=message)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            id_utilisateur = _resoudre_utilisateur(cursor, utilisateur)
            validees: list[str] = []
            ignorees: list[dict[str, str]] = []
            for code in codes:
                cursor.execute("SELECT id_fiche, statut FROM fiche WHERE code = %s", (code,))
                ligne = cursor.fetchone()
                if ligne is None:
                    ignorees.append({"code": code, "raison": "fiche inconnue"})
                    continue
                id_fiche, statut = int(ligne[0]), str(ligne[1])
                if statut != "a_valider":
                    ignorees.append({"code": code, "raison": f"statut « {statut} » — rouvrez d'abord"})
                    continue
                cursor.execute("UPDATE fiche SET statut = 'valide', updated_at = now() WHERE id_fiche = %s", (id_fiche,))
                # Lot E : chaque fiche validée en lot devient cherchable (même
                # rafraîchissement que la validation individuelle, même transaction).
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))
                _jouter_journal(cursor, id_fiche, id_utilisateur, "valider", statut, "valide", commentaire or "validation en lot")
                validees.append(code)
    LOGGER.info(
        "Validation en lot : %d validée(s), %d ignorée(s) (utilisateur %s, acquittement=%s) — conséquence : les fiches validées sont verrouillées (RG11).",
        len(validees), len(ignorees), utilisateur, acquittement_humain,
    )
    return {"nb_validees": len(validees), "validees": validees, "nb_ignorees": len(ignorees), "ignorees": ignorees}




_EXTENSIONS_EXCEL = {".csv", ".xls", ".xlsx", ".xlsm", ".ods", ".xlsb"}
_EXTENSIONS_MACHINE = {".dxf", ".dwg", ".step", ".stp", ".igs", ".iges", ".xin", ".plx", ".plt", ".nc", ".cnc"}
_EXTENSIONS_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_EXTENSIONS_TEXTE = {".txt", ".csv", ".md", ".log", ".json", ".xml"}


def _type_piece(extension: str) -> str:
    ext = extension.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in _EXTENSIONS_EXCEL:
        return "excel"
    if ext in _EXTENSIONS_MACHINE:
        return "machine"
    return "other"


def _apercu_possible(extension: str) -> bool:
    ext = extension.lower()
    return ext == ".pdf" or ext in _EXTENSIONS_IMAGE or ext in _EXTENSIONS_TEXTE


def _content_type_piece(name: str, extension: str) -> str:
    ext = extension.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in _EXTENSIONS_TEXTE:
        return "text/csv; charset=utf-8" if ext == ".csv" else "text/plain; charset=utf-8"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _nom_fichier_sure(nom: str) -> str:
    nom = str(nom).replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[\r\n\x00]", "_", nom) or "fichier"


def _content_disposition(nom: str, *, inline: bool) -> str:
    nom = _nom_fichier_sure(nom)
    ascii_nom = nom.encode("ascii", "ignore").decode("ascii")
    ascii_nom = re.sub(r"[^A-Za-z0-9._-]", "_", ascii_nom).strip("._") or "fichier"
    encoded = quote(nom, safe="!#$&-.^_`|~")
    disposition = "inline" if inline else "attachment"
    return f'{disposition}; filename="{ascii_nom}"; filename*=UTF-8\'\'{encoded}'


def _path_autorise(path: str | Path, config: Any) -> bool:
    try:
        cible = Path(path).expanduser().resolve()
        racines = [Path(root).expanduser().resolve() for root in getattr(config, "root_paths", [])]
        racines.append(staging_root(config).expanduser().resolve())
        return any(cible == racine or racine in cible.parents for racine in racines)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _echapper_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where_archive(index: Any, config: Any, *, q: str = "", extension: str = "", dossier: str = "") -> tuple[str, list[Any]]:
    marqueur = "%s" if getattr(index, "is_postgres", False) else "?"
    conditions = ["is_dir = FALSE"]
    valeurs: list[Any] = []
    racines = {Path(root).expanduser().resolve() for root in getattr(config, "root_paths", [])}
    racines.add(staging_root(config).expanduser().resolve())
    clauses_racines: list[str] = []
    for racine in sorted(racines, key=str):
        cle = os.path.normcase(str(racine))
        motif = _echapper_like(cle.rstrip("/\\") + os.sep) + "%"
        clauses_racines.append(f"(path_key = {marqueur} OR path_key LIKE {marqueur} ESCAPE '\\')")
        valeurs.extend((cle, motif))
    if clauses_racines:
        conditions.append("(" + " OR ".join(clauses_racines) + ")")
    if q.strip():
        motif = f"%{_echapper_like(q.strip().lower())}%"
        conditions.append(
            f"(LOWER(name) LIKE {marqueur} ESCAPE '\\' OR LOWER(path) LIKE {marqueur} ESCAPE '\\' "
            f"OR LOWER(parent_path) LIKE {marqueur} ESCAPE '\\')"
        )
        valeurs.extend((motif, motif, motif))
    if extension.strip():
        ext = extension.strip().lower()
        ext = ext if ext.startswith(".") else f".{ext}"
        conditions.append(f"LOWER(extension) = {marqueur}")
        valeurs.append(ext)
    if dossier.strip():
        motif = f"%{_echapper_like(dossier.strip().lower())}%"
        conditions.append(f"LOWER(parent_path) LIKE {marqueur} ESCAPE '\\'")
        valeurs.append(motif)
    return " AND ".join(conditions), valeurs


def _nom_dossier(parent: str | None) -> str:
    if not parent:
        return ""
    return PureWindowsPath(parent).name if "\\" in parent and "/" not in parent else Path(parent).name


def _piece_payload(
    piece_id: int,
    name: str,
    extension: str,
    size: int | None,
    *,
    is_primary_pdf: bool = False,
    dossier: str = "",
    role: str | None = None,
    legacy_path: str | None = None,
    digest: str | None = None,
    fiche_code: str | None = None,
) -> dict[str, Any]:
    ext = extension.lower() or Path(name).suffix.lower()
    payload: dict[str, Any] = {
        "id": int(piece_id),
        "name": name,
        "extension": ext,
        "size": int(size) if size is not None else None,
        "kind": _type_piece(ext),
        "is_primary_pdf": bool(is_primary_pdf),
        "previewable": _apercu_possible(ext),
        "dossier": dossier,
        # Champs conservés pendant la transition des écrans existants.
        "id_document": int(piece_id),
        "nom": name,
        "taille_octets": int(size) if size is not None else None,
    }
    if role is not None:
        payload["role"] = role
    # Le paramètre legacy_path reste accepté pour ne pas casser les appels
    # internes, mais aucun chemin local n'est renvoyé par l'API.
    if digest is not None:
        payload["empreinte_sha256"] = digest
    if fiche_code:
        payload["fiche_code"] = fiche_code
    return payload


def _tranche_octets(range_header: str | None, size: int) -> tuple[int, int, int]:
    if not range_header:
        return 0, max(size - 1, -1), 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip(), flags=re.IGNORECASE)
    if not match or size <= 0 or (not match.group(1) and not match.group(2)):
        raise HTTPException(status_code=416, detail="Plage d’octets invalide.", headers={"Content-Range": f"bytes */{size}"})
    if match.group(1):
        debut = int(match.group(1))
        fin = int(match.group(2)) if match.group(2) else size - 1
    else:
        suffixe = int(match.group(2))
        if suffixe <= 0:
            raise HTTPException(status_code=416, detail="Plage d’octets invalide.", headers={"Content-Range": f"bytes */{size}"})
        debut = max(0, size - suffixe)
        fin = size - 1
    if debut >= size or fin < debut:
        raise HTTPException(status_code=416, detail="Plage d’octets invalide.", headers={"Content-Range": f"bytes */{size}"})
    return debut, min(fin, size - 1), 206


def _flux_fichier(path: Path, debut: int, fin: int):
    with path.open("rb") as fichier:
        fichier.seek(debut)
        restant = max(0, fin - debut + 1)
        while restant:
            bloc = fichier.read(min(restant, 1024 * 1024))
            if not bloc:
                break
            restant -= len(bloc)
            yield bloc


def lister_pieces_archive(
    index: Any,
    config: Any,
    *,
    q: str = "",
    extension: str = "",
    dossier: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Catalogue consultable de tous les fichiers accessibles à l'utilisateur."""
    limit = min(max(1, limit), 200)
    offset = min(max(0, offset), 1_000_000)
    where, valeurs = _where_archive(index, config, q=q, extension=extension, dossier=dossier)
    marqueur = "%s" if getattr(index, "is_postgres", False) else "?"
    with index.connect() as connexion:
        if index.is_postgres:
            with connexion.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM documents WHERE {where}", tuple(valeurs))
                total = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT d.id, d.name, d.extension, d.size, d.parent_path, "
                    "COALESCE((SELECT f.code FROM fiche f WHERE f.fichier_source = d.path LIMIT 1), "
                    "(SELECT f.code FROM fiche_piece_jointe p JOIN fiche f ON f.id_fiche = p.id_fiche "
                    "WHERE p.id_document = d.id LIMIT 1)) "
                    "FROM documents d WHERE "
                    f"{where} ORDER BY LOWER(d.name), d.id LIMIT {marqueur} OFFSET {marqueur}",
                    tuple(valeurs) + (limit, offset),
                )
                lignes = cursor.fetchall()
                where_base, valeurs_base = _where_archive(index, config)
                cursor.execute(
                    "SELECT DISTINCT extension, parent_path FROM documents WHERE " + where_base,
                    tuple(valeurs_base),
                )
                facettes = cursor.fetchall()
        else:
            total = int(connexion.execute(f"SELECT COUNT(*) FROM documents WHERE {where}", tuple(valeurs)).fetchone()[0])
            lignes = connexion.execute(
                "SELECT id, name, extension, size, parent_path, NULL "
                "FROM documents WHERE "
                f"{where} ORDER BY LOWER(name), id LIMIT {marqueur} OFFSET {marqueur}",
                tuple(valeurs) + (limit, offset),
            ).fetchall()
            where_base, valeurs_base = _where_archive(index, config)
            facettes = connexion.execute(
                "SELECT DISTINCT extension, parent_path FROM documents WHERE " + where_base,
                tuple(valeurs_base),
            ).fetchall()
    pieces = [
        _piece_payload(
            row[0],
            str(row[1]),
            str(row[2] or Path(str(row[1])).suffix).lower(),
            row[3],
            dossier=_nom_dossier(row[4]),
            fiche_code=str(row[5]) if row[5] else None,
        )
        for row in lignes
    ]
    extensions = sorted({str(row[0]).lower() for row in facettes if row[0]})
    dossiers = sorted({_nom_dossier(str(row[1])) for row in facettes if row[1]})
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(pieces) < total,
        "pieces": pieces,
        "extensions": extensions,
        "dossiers": dossiers,
    }


def liste_fiches(index: Any, statut: str | None = None, page: int = 1, taille: int = 50) -> dict[str, Any]:
    """Écran Dossiers (lot D) : liste paginée + facettes (compteurs par statut)."""
    page = max(1, page)
    taille = min(max(1, taille), 200)
    filtre = "WHERE f.statut = %s" if statut else ""
    parametres: tuple[Any, ...] = (statut, taille, (page - 1) * taille) if statut else (taille, (page - 1) * taille)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT f.code, COALESCE(f.titre, ''), f.statut, f.score_qualite, COALESCE(g.code, ''), "
                "COALESCE(c.nom, ''), COALESCE(b.nom, ''), COALESCE(b.taille, ''), "
                "(SELECT COUNT(*) FROM fiche_piece_jointe p WHERE p.id_fiche = f.id_fiche) "
                "+ CASE WHEN f.fichier_source IS NULL THEN 0 ELSE 1 END AS nb_fichiers, "
                "CASE WHEN f.fichier_source IS NULL THEN FALSE ELSE TRUE END AS a_pdf "
                "FROM fiche f LEFT JOIN gabarit g ON g.id_gabarit = f.id_gabarit "
                "LEFT JOIN client c ON c.id_client = f.id_client "
                "LEFT JOIN bateau b ON b.id_bateau = f.id_bateau "
                + filtre
                + " ORDER BY f.updated_at DESC, f.code LIMIT %s OFFSET %s",
                parametres,
            )
            lignes = cursor.fetchall()
            cursor.execute("SELECT statut, COUNT(*) FROM fiche GROUP BY statut")
            facettes = {str(s): int(n) for s, n in cursor.fetchall()}
            where_total = "WHERE statut = %s" if statut else ""
            cursor.execute(f"SELECT COUNT(*) FROM fiche {where_total}", (statut,) if statut else ())
            total = int(cursor.fetchone()[0])
    return {
        "total": total,
        "page": page,
        "facettes": facettes,
        "fiches": [
            {
                "code": ligne[0], "titre": ligne[1], "statut": ligne[2],
                "score_qualite": float(ligne[3]) if ligne[3] is not None else None,
                "gabarit": ligne[4], "client": ligne[5], "bateau": ligne[6], "bateau_taille": ligne[7],
                "nb_fichiers": int(ligne[8]), "a_pdf": bool(ligne[9]),
            }
            for ligne in lignes
        ],
    }


def _document_fiche_par_chemin(cursor: Any, chemin: str, role: str, config: Any | None = None) -> tuple[Any, ...] | None:
    """Trouve un document du catalogue, avec reprise des anciennes fiches sans id_document."""
    if not chemin:
        return None
    path = Path(chemin).expanduser()
    try:
        resolu = path.resolve()
    except (OSError, RuntimeError):
        resolu = path
    if config is not None and not _path_autorise(resolu, config):
        return None
    cle = os.path.normcase(str(resolu))
    cursor.execute(
        "SELECT id, name, extension, size, path, object_key, role FROM documents WHERE path_key = %s LIMIT 1",
        (cle,),
    )
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "SELECT id, name, extension, size, path, object_key, role FROM documents WHERE path = %s LIMIT 1",
            (str(path),),
        )
        row = cursor.fetchone()
    if row is not None:
        return row
    if not resolu.is_file():
        return None

    stat = resolu.stat()
    extension = resolu.suffix.lower()
    categorie = "analyzed" if extension == ".pdf" else "storage_direct"
    cursor.execute(
        "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir, "
        "content, content_hash, extraction_status, category, role) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, '', '', 'metadata', %s, %s) "
        "ON CONFLICT (path_key) DO UPDATE SET role = COALESCE(documents.role, EXCLUDED.role) "
        "RETURNING id, name, extension, size, path, object_key, role",
        (
            cle,
            str(resolu),
            resolu.name,
            str(resolu.parent),
            extension,
            stat.st_size,
            stat.st_mtime,
            categorie,
            role,
        ),
    )
    return cursor.fetchone()


def pieces_de_fiche(index: Any, code: str, config: Any | None = None) -> dict[str, Any]:
    """Liste par identifiant toutes les pièces, PDF source inclus.

    ``pdf_source`` et ``chemin`` restent temporairement présents pour les anciens
    clients ; les nouveaux écrans utilisent uniquement ``id`` pour ouvrir les
    fichiers. Les clés d'objet S3 ne sont jamais renvoyées au navigateur.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_fiche, fichier_source FROM fiche WHERE code = %s", (code,))
            fiche = cursor.fetchone()
            if fiche is None:
                raise HTTPException(status_code=404, detail=f"Fiche {code} inconnue.")
            id_fiche = int(fiche[0])
            cursor.execute(
                "SELECT split_part(cle_idempotence, '|', 1) FROM lot_dossier "
                "WHERE id_fiche = %s AND statut = 'traite' AND cle_idempotence IS NOT NULL "
                "ORDER BY traite_le DESC LIMIT 1",
                (id_fiche,),
            )
            ligne_pdf = cursor.fetchone()
            pdf_source = (ligne_pdf[0] if ligne_pdf and ligne_pdf[0] else None) or fiche[1]
            principal = _document_fiche_par_chemin(cursor, str(pdf_source or ""), "fiche_pdf", config)
            cursor.execute(
                "SELECT p.chemin, p.role, p.empreinte_sha256, p.taille_octets, d.id, d.name, "
                "d.extension, d.size, d.path, d.object_key "
                "FROM fiche_piece_jointe p LEFT JOIN documents d ON d.id = p.id_document "
                "WHERE p.id_fiche = %s ORDER BY p.chemin",
                (id_fiche,),
            )
            lignes_pieces = cursor.fetchall()

            pieces: list[dict[str, Any]] = []
            if principal is not None:
                id_document, nom, extension, taille, chemin, _object_key, _role = principal
                pieces.append(
                    _piece_payload(
                        int(id_document),
                        str(nom),
                        str(extension or Path(str(nom)).suffix).lower(),
                        int(taille) if taille is not None else None,
                        is_primary_pdf=True,
                        dossier=_nom_dossier(str(Path(str(chemin)).parent)) if chemin else "",
                        role="fiche_pdf",
                        legacy_path=str(chemin or pdf_source),
                    )
                )
            for piece in lignes_pieces:
                chemin_piece, role, empreinte, taille, id_document, nom, extension, taille_doc, chemin_doc, _object_key = piece
                if id_document is None:
                    document = _document_fiche_par_chemin(cursor, str(chemin_piece), str(role or "piece_jointe"), config)
                    if document is None:
                        continue
                    id_document, nom, extension, taille_doc, chemin_doc, _object_key, _role_doc = document
                nom_piece = str(nom or Path(str(chemin_piece)).name)
                pieces.append(
                    _piece_payload(
                        int(id_document),
                        nom_piece,
                        str(extension or Path(nom_piece).suffix).lower(),
                        int(taille_doc if taille_doc is not None else taille) if (taille_doc is not None or taille is not None) else None,
                        dossier=_nom_dossier(str(Path(str(chemin_piece)).parent)),
                        role=str(role or "piece_jointe"),
                        legacy_path=str(chemin_piece),
                        digest=str(empreinte) if empreinte is not None else None,
                    )
                )
    return {
        # Champs de compatibilité conservés mais expurgés : l'ouverture se fait
        # exclusivement par l'identifiant numérique du catalogue.
        "fichier_source": None,
        "pdf_source": None,
        "pieces": pieces,
    }


def detail_fiche(index: Any, code: str) -> dict[str, Any]:
    """Métadonnées d'en-tête d'une fiche, sans chemin de fichier."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT f.code, COALESCE(f.titre, ''), f.statut, COALESCE(g.code, ''), "
                "COALESCE(c.nom, ''), COALESCE(b.nom, ''), COALESCE(b.taille, ''), f.date_edition, "
                "(SELECT COUNT(*) FROM fiche_piece_jointe p WHERE p.id_fiche = f.id_fiche) "
                "+ CASE WHEN f.fichier_source IS NULL THEN 0 ELSE 1 END "
                "FROM fiche f LEFT JOIN gabarit g ON g.id_gabarit = f.id_gabarit "
                "LEFT JOIN client c ON c.id_client = f.id_client "
                "LEFT JOIN bateau b ON b.id_bateau = f.id_bateau WHERE f.code = %s",
                (code,),
            )
            row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Fiche {code} inconnue.")
    date_edition = row[7].isoformat() if hasattr(row[7], "isoformat") else row[7]
    return {
        "code": row[0],
        "titre": row[1],
        "statut": row[2],
        "gabarit": row[3],
        "client": row[4],
        "bateau": row[5],
        "bateau_taille": row[6],
        "date_edition": date_edition,
        "nb_fichiers": int(row[8]),
    }


def historique_fiche(index: Any, code: str) -> list[dict[str, Any]]:
    """Journal en lecture seule des décisions de validation d'une fiche."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_fiche FROM fiche WHERE code = %s", (code,))
            fiche = cursor.fetchone()
            if fiche is None:
                raise HTTPException(status_code=404, detail=f"Fiche {code} inconnue.")
            cursor.execute(
                "SELECT action, etat_avant, etat_apres, commentaire, created_at "
                "FROM fiche_validation WHERE id_fiche = %s ORDER BY created_at DESC, id_validation DESC",
                (int(fiche[0]),),
            )
            lignes = cursor.fetchall()
    return [
        {
            "action": row[0],
            "etat_avant": row[1],
            "etat_apres": row[2],
            "commentaire": row[3],
            "created_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        }
        for row in lignes
    ]


def enregistrer_routes_fiches(
    app: Any,
    index: Any,
    config: Any,
    verifier_auth: Any,
    storage_client: S3StorageClient | None = None,
) -> None:
    """Branche les routes métier et l'API de fichiers par identifiant."""

    def charger_document(piece_id: int) -> dict[str, Any]:
        with index.connect() as connexion:
            if index.is_postgres:
                with connexion.cursor() as cursor:
                    cursor.execute(
                        "SELECT id, path, path_key, name, extension, size, parent_path, object_key, is_dir "
                        "FROM documents WHERE id = %s",
                        (piece_id,),
                    )
                    row = cursor.fetchone()
            else:
                row = connexion.execute(
                    "SELECT id, path, path_key, name, extension, size, parent_path, object_key, is_dir "
                    "FROM documents WHERE id = ?",
                    (piece_id,),
                ).fetchone()
                row = dict(row) if row is not None else None
        if row is None:
            raise HTTPException(status_code=404, detail="Fichier inconnu.")
        if index.is_postgres:
            row = dict(
                zip(
                    ("id", "path", "path_key", "name", "extension", "size", "parent_path", "object_key", "is_dir"),
                    row,
                )
            )
        if bool(row["is_dir"]):
            raise HTTPException(status_code=404, detail="Fichier inconnu.")
        chemin = str(row["path"] or row["path_key"] or "")
        if not _path_autorise(chemin, config):
            raise HTTPException(status_code=403, detail="Accès à ce fichier interdit.")
        return row

    def servir_piece(piece_id: int, request: Request, *, apercu: bool) -> Response:
        document = charger_document(piece_id)
        nom = _nom_fichier_sure(str(document["name"] or "fichier"))
        extension = str(document["extension"] or Path(nom).suffix).lower()
        if apercu and not _apercu_possible(extension):
            raise HTTPException(
                status_code=415,
                detail="L’aperçu n’est pas disponible pour ce type de fichier. Téléchargez le fichier pour l’ouvrir.",
            )

        chemin = Path(str(document["path"] or document["path_key"])).expanduser()
        object_key = document.get("object_key")
        inline = apercu
        disposition = _content_disposition(nom, inline=inline)
        media_type = _content_type_piece(nom, extension)
        headers = {
            "Content-Type": media_type,
            "Content-Disposition": disposition,
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        methode = request.method.upper()
        range_header = request.headers.get("range") if methode == "GET" else None

        if storage_client is not None and object_key:
            if methode == "HEAD":
                try:
                    metadata = storage_client.head_object(str(object_key))
                except StorageError as erreur:
                    raise HTTPException(status_code=502, detail="Le stockage du fichier est temporairement indisponible.") from erreur
                longueur = metadata.get("ContentLength", document.get("size"))
                if longueur is not None:
                    headers["Content-Length"] = str(int(longueur))
                if metadata.get("ETag"):
                    headers["ETag"] = str(metadata["ETag"])
                if metadata.get("LastModified"):
                    headers["Last-Modified"] = metadata["LastModified"].strftime("%a, %d %b %Y %H:%M:%S GMT")
                return Response(status_code=200, headers=headers)

            range_a_demander = range_header
            taille_connue = document.get("size")
            if range_header and taille_connue is not None:
                debut, fin, statut_range = _tranche_octets(range_header, int(taille_connue))
                if statut_range == 206:
                    range_a_demander = f"bytes={debut}-{fin}"
            try:
                objet = storage_client.get_object(str(object_key), range_header=range_a_demander)
            except StorageError as erreur:
                raise HTTPException(status_code=502, detail="Le stockage du fichier est temporairement indisponible.") from erreur
            if objet.get("ContentRange"):
                headers["Content-Range"] = str(objet["ContentRange"])
            if objet.get("ContentLength") is not None:
                headers["Content-Length"] = str(int(objet["ContentLength"]))
            if objet.get("ETag"):
                headers["ETag"] = str(objet["ETag"])
            if objet.get("LastModified"):
                headers["Last-Modified"] = objet["LastModified"].strftime("%a, %d %b %Y %H:%M:%S GMT")
            corps = objet["Body"].iter_chunks(chunk_size=1024 * 1024)
            return StreamingResponse(corps, status_code=206 if objet.get("ContentRange") else 200, headers=headers)

        try:
            stat = chemin.stat()
        except OSError as erreur:
            raise HTTPException(status_code=404, detail="Fichier non disponible.") from erreur
        taille = stat.st_size
        headers["Content-Length"] = str(taille)
        if methode == "HEAD":
            return Response(status_code=200, headers=headers)
        debut, fin, statut = _tranche_octets(range_header, taille)
        if statut == 206:
            headers["Content-Range"] = f"bytes {debut}-{fin}/{taille}"
            headers["Content-Length"] = str(fin - debut + 1)
        return StreamingResponse(_flux_fichier(chemin, debut, fin), status_code=statut, headers=headers)

    @app.get("/pieces")
    def route_lister_pieces(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        q: str = Query("", max_length=300),
        extension: str = Query("", max_length=24),
        dossier: str = Query("", max_length=300),
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0, le=1_000_000),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        return lister_pieces_archive(index, config, q=q, extension=extension, dossier=dossier, limit=limit, offset=offset)

    @app.api_route("/pieces/{piece_id}/apercu", methods=["GET", "HEAD"])
    def route_apercu_piece(
        piece_id: int,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        verifier_auth(config, token)
        return servir_piece(piece_id, request, apercu=True)

    @app.api_route("/pieces/{piece_id}/telecharger", methods=["GET", "HEAD"])
    def route_telecharger_piece(
        piece_id: int,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        verifier_auth(config, token)
        return servir_piece(piece_id, request, apercu=False)

    @app.get("/fiches/{code}")
    def route_detail_fiche(
        code: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        return detail_fiche(index, code)

    @app.get("/fiches/{code}/historique")
    def route_historique_fiche(
        code: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        return historique_fiche(index, code)

    @app.get("/fiches/{code}/champs")
    def route_champs_fiche(code: str, token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        return champs_de_fiche(index, code)

    @app.get("/gabarits")
    def route_gabarits(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        return lister_gabarits(index)

    @app.get("/gabarits/{code}/versions")
    def route_versions_gabarit(code: str, token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        return versions_de_gabarit(index, code)

    @app.post("/gabarits/{code}/versions", status_code=201)
    def route_publier_version(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        return publier_nouvelle_version(
            index,
            code,
            str(corps.get("description") or ""),
            list(corps.get("ancres_detection") or []),
            dict(corps.get("regles") or {}),
        )

    @app.post("/imports/dossier", status_code=201)
    def route_deposer_dossier(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Dépose UN dossier (immédiat, synchrone) : fiche extraite + pièces
        jointes rattachées + lot suivi. L'archive est LUE, jamais modifiée.
        Corps : {"dossier": "/chemin/du/dossier"}."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        dossier = str(corps.get("dossier") or "").strip()
        if not dossier:
            raise HTTPException(status_code=422, detail='Corps attendu : {"dossier": "/chemin/du/dossier"}.')
        try:
            resultat = deposer_dossier(index, Path(dossier))
        except DepotImpossible as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur
        lot = etat_lot(index, int(resultat.get("id_lot") or 0)) if resultat.get("id_lot") else None
        return {**resultat, "lot": lot}

    @app.post("/imports/dossier/lot", status_code=202)
    def route_creer_lot(
        corps: dict[str, Any],
        fond: Annotated[bool, Header(alias="X-SEAMTECH-BACKGROUND")] = True,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Crée un lot multi-dossiers (une racine, un sous-dossier par affaire).

        Trois modes, tous ANNONCÉS dans la réponse (jamais de « accepté » flou) :

        * ``file_durable`` — le lot est remis à la file Redis et exécuté par le
          service worker ; il survit à un redémarrage du serveur web ;
        * ``processus_memoire`` — repli de développement sans Redis : le lot
          tourne dans un fil du processus web et meurt avec lui (visible dans
          ``durability``) ;
        * ``synchrone`` — ``X-SEAMTECH-BACKGROUND: false`` : traitement dans la
          requête (petits lots, jeux de test).

        Avec ``require_durable_queue`` (production), l'absence de file durable
        est un 503 explicite : on préfère refuser que perdre un lot de 200
        dossiers en silence.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        racine = str(corps.get("racine") or "").strip()
        if not racine:
            raise HTTPException(status_code=422, detail='Corps attendu : {"racine": "/chemin/de/la/racine"}.')
        try:
            id_lot = creer_lot(index, Path(racine), notes=corps.get("notes"))
        except DepotImpossible as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur

        if not fond:
            return {"id_lot": id_lot, "statut": "termine", "traitement": "synchrone", "etat": executer_lot(index, id_lot)}

        job_id = f"lot-{id_lot}"
        redis_store = RedisStore(config=config)
        from seamtech_search.worker import file_durable_disponible

        durable = file_durable_disponible(redis_store)
        if not durable and config.require_durable_queue:
            # Le lot est déjà créé « en_attente » : l'opérateur peut le relancer
            # avec la même racine une fois Redis revenu (aucun dossier perdu).
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Lot #{id_lot} créé mais NON démarré : file d'attente durable indisponible "
                    "(Redis injoignable). Relancez le lot quand le service est revenu — "
                    "les dossiers restent « en_attente », rien n'est perdu."
                ),
            )

        create_job(
            index,
            job_id,
            racine,
            status="pending",
            stage="queued",
            durability="durable" if durable else "process_memory",
        )
        if durable:
            redis_store.set_job(
                job_id,
                {"id": job_id, "status": "pending", "progress": 0, "stage": "queued", "source_path": racine},
            )
            redis_store.enqueue_task("imports", {"job_id": job_id, "kind": "lot", "id_lot": id_lot})
            LOGGER.info("Lot #%d remis à la file durable (job %s).", id_lot, job_id)
            return {
                "id_lot": id_lot,
                "job_id": job_id,
                "statut": "en_cours",
                "traitement": "file_durable",
                "durability": "durable",
                "suivi": f"/lots/{id_lot}",
            }

        import threading

        thread = threading.Thread(
            target=executer_lot,
            args=(index, id_lot),
            name=f"lot-{id_lot}",
            daemon=True,
        )
        thread.start()
        LOGGER.warning(
            "Lot #%d traité en fil IN-PROCESS (Redis absent, repli de développement) : "
            "il ne survivra pas à un redémarrage du serveur web.",
            id_lot,
        )
        return {
            "id_lot": id_lot,
            "job_id": job_id,
            "statut": "en_cours",
            "traitement": "processus_memoire",
            "durability": "process_memory",
            "avertissement": "Repli de développement : ce lot ne survit pas à un redémarrage du serveur.",
            "suivi": f"/lots/{id_lot}",
        }

    @app.get("/lots")
    def route_lots(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> list[dict[str, Any]]:
        """Liste des lots d'ingestion (progression). NB : les chemins /imports/
        {id} sont déjà affectés aux imports de documents (Phase 0) — les lots du
        Lot C sont consultables sous /lots (écart documenté dans docs/API.md)."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        return lister_lots(index)

    @app.get("/lots/{id_lot}")
    def route_lot(id_lot: int, token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, Any]:
        """État complet d'un lot : progression, dossiers, échecs avec raisons,
        fichiers restants (consultable pendant le traitement en fond)."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        try:
            return etat_lot(index, id_lot)
        except DepotImpossible as erreur:
            raise HTTPException(status_code=404, detail=str(erreur)) from erreur

    @app.post("/lots/{id_lot}/annuler")
    def route_annuler_lot(
        id_lot: int,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Demande l'arrêt d'un lot en cours (annulation inter-processus).

        Le drapeau passe par Redis (TTL 24 h) : le worker — qui peut tourner dans
        un AUTRE conteneur — l'observe entre deux dossiers et marque le lot
        ``annule``. Les dossiers non traités restent ``en_attente`` : le lot est
        reprenable, et un dossier déjà traité n'est jamais refait (clé
        d'idempotence). Aucun fichier n'est supprimé.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        etat_avant = etat_lot(index, id_lot)
        if etat_avant["statut"] in {"termine", "annule"}:
            return {"id_lot": id_lot, "statut": etat_avant["statut"], "annulation": "sans_objet", "etat": etat_avant}
        redis_store = RedisStore(config=config)
        if not redis_store.is_configured():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Annulation impossible : le drapeau d'annulation passe par Redis, et il n'est pas "
                    "configuré. Attendez la fin du lot ou arrêtez le service worker."
                ),
            )
        register_job_cancel(f"lot-{id_lot}", redis_store)
        LOGGER.warning("Lot #%d : annulation demandée par l'opérateur.", id_lot)
        return {
            "id_lot": id_lot,
            "statut": "annulation_demandee",
            "annulation": "demandee",
            "consigne": "Le lot s'arrêtera entre deux dossiers ; les dossiers restants sont reprenables.",
        }

    @app.post("/gabarits/detecter")
    async def route_detecter(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        fichier: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        """Détection seule (aucune écriture) — PDF envoyé en multipart."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        if fichier is None:
            raise HTTPException(status_code=422, detail="Envoyer le PDF en multipart (champ « fichier »).")
        contenu = await fichier.read()
        if len(contenu) > TAILLE_PDF_MAX:
            raise HTTPException(status_code=413, detail=f"PDF trop gros ({len(contenu)} octets, maximum {TAILLE_PDF_MAX}).")
        if not contenu.startswith(b"%PDF"):
            raise HTTPException(status_code=422, detail="Le contenu reçu n'est pas un PDF (signature %PDF absente).")
        descripteur, chemin = tempfile.mkstemp(suffix=".pdf", prefix="detection-")
        try:
            with os.fdopen(descripteur, "wb") as sortie:
                sortie.write(contenu)
            return detecter_pdf(index, Path(chemin))
        except ExtractionImpossible as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur
        finally:
            try:
                os.unlink(chemin)
            except OSError:
                LOGGER.warning("Fichier temporaire de detection non supprimé : %s (conséquence : résidu disque).", chemin)

    @app.post("/fiches/{code}/corriger")
    def route_corriger_champ(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return corriger_champ(
            index, code,
            corps.get("champ"), corps.get("valeur"),
            _attribution(config, token, corps.get("utilisateur"), entete_utilisateur, entete_role),
            rang=corps.get("rang"), commentaire=corps.get("commentaire"),
        )

    @app.post("/fiches/{code}/valider")
    def route_valider_fiche(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return valider_fiche(
            index, code,
            _attribution(config, token, corps.get("utilisateur"), entete_utilisateur, entete_role),
            corps.get("commentaire"),
        )

    @app.post("/fiches/{code}/rejeter")
    def route_rejeter_fiche(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return rejeter_fiche(
            index, code,
            _attribution(config, token, corps.get("utilisateur"), entete_utilisateur, entete_role),
            corps.get("motif") or "",
        )

    @app.post("/fiches/{code}/rouvrir")
    def route_rouvrir_fiche(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return rouvrir_fiche(
            index, code,
            _attribution(config, token, corps.get("utilisateur"), entete_utilisateur, entete_role),
            corps.get("commentaire"), bool(corps.get("effacer_corrections")),
        )

    @app.get("/validation/file")
    def route_fichier_validation(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        gabarit: str | None = None,
        anomalie: str | None = None,
    ) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return fichier_validation(index, gabarit=gabarit, anomalie=anomalie)

    @app.post("/validation/lot")
    def route_valider_lot(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        codes = [str(c) for c in (corps.get("codes") or [])]
        if not codes:
            raise HTTPException(status_code=422, detail="Liste « codes » vide — rien à valider.")
        return valider_lot(
            index, codes,
            _attribution(config, token, corps.get("utilisateur"), entete_utilisateur, entete_role),
            acquittement_humain=bool(corps.get("acquittement_humain")), commentaire=corps.get("commentaire"),
        )
    @app.get("/fiches")
    def route_liste_fiches(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        statut: str | None = None,
        page: int = 1,
        taille: int = 50,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return liste_fiches(index, statut=statut, page=page, taille=taille)

    @app.get("/fiches/{code}/pieces")
    def route_pieces_fiche(code: str, token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, Any]:
        verifier_auth(config, token)
        return pieces_de_fiche(index, code, config)

    # Lot K.2 — brouillons de gabarits depuis PDF variante
    from seamtech_search.fiches.gabarit_brouillon import (
        enregistrer_brouillon,
        generer_brouillon_depuis_pdf,
        get_brouillon,
        lister_brouillons,
        valider_brouillon_vers_gabarit,
    )

    @app.post("/gabarits/brouillon/from-pdf")
    async def route_brouillon_from_pdf(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        fichier: UploadFile | None = File(default=None),
        code: str | None = None,
    ) -> dict[str, Any]:
        """Génère un brouillon de gabarit depuis un PDF variante inconnue.

        Le brouillon est stocké en table gabarit_brouillon (statut brouillon) et
        ne sert JAMAIS à l'extraction réelle — garde-fou : charger_gabarits()
        ne lit que gabarit WHERE actif.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        if fichier is None:
            raise HTTPException(status_code=422, detail="Envoyer le PDF en multipart (champ fichier).")
        contenu = await fichier.read()
        if len(contenu) > TAILLE_PDF_MAX:
            raise HTTPException(status_code=413, detail=f"PDF trop gros ({len(contenu)} octets, max {TAILLE_PDF_MAX}).")
        if not contenu.startswith(b"%PDF"):
            raise HTTPException(status_code=422, detail="Le contenu reçu n'est pas un PDF (signature %PDF absente).")
        descripteur, chemin = tempfile.mkstemp(suffix=".pdf", prefix="brouillon-")
        try:
            with os.fdopen(descripteur, "wb") as sortie:
                sortie.write(contenu)
            brouillon = generer_brouillon_depuis_pdf(Path(chemin), code_propose=code)
            enregistre = enregistrer_brouillon(index, brouillon)
            return {**brouillon, **enregistre}
        except Exception as exc:
            LOGGER.exception("Échec génération brouillon depuis PDF: %s", exc)
            raise HTTPException(status_code=500, detail=f"Échec génération brouillon: {exc}") from exc
        finally:
            try:
                os.unlink(chemin)
            except OSError:
                pass

    @app.get("/gabarits/brouillons")
    def route_liste_brouillons(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> list[dict[str, Any]]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return lister_brouillons(index)

    @app.get("/gabarits/brouillons/{id_brouillon}")
    def route_get_brouillon(
        id_brouillon: int,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return get_brouillon(index, id_brouillon)

    @app.post("/gabarits/brouillons/{id_brouillon}/valider", status_code=201)
    def route_valider_brouillon(
        id_brouillon: int,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Valide un brouillon → nouvelle version active dans registre gabarit existant."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        return valider_brouillon_vers_gabarit(index, id_brouillon)
