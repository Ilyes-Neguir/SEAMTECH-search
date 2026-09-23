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
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import File, Header, HTTPException, UploadFile

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
            # Lot E : la validation est LE moment où la fiche devient cherchable —
            # le texte de recherche pondéré (A/B/C) est rempli ici, dans la même
            # transaction (migration 007 : « appelée à la VALIDATION d'une fiche »).
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
        "MIN(c.confiance) FILTER (WHERE c.valeur_normalisee IS NOT NULL) "
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
                "COALESCE(c.nom, ''), COALESCE(b.nom, ''), COALESCE(b.taille, '') "
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
            }
            for ligne in lignes
        ],
    }


def pieces_de_fiche(index: Any, code: str) -> dict[str, Any]:
    """Pièces jointes de la fiche, avec leur description `documents` (RG12 :
    une seule description par fichier — le lien porte id_document) ; et le
    chemin du PDF source de la fiche (visionneuse de l'écran Fiche/Validation)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_fiche, fichier_source FROM fiche WHERE code = %s", (code,))
            ligne = cursor.fetchone()
            if ligne is None:
                raise HTTPException(status_code=404, detail=f"Fiche {code} inconnue.")
            cursor.execute(
                "SELECT p.chemin, p.role, p.empreinte_sha256, p.taille_octets, d.id, d.name "
                "FROM fiche_piece_jointe p LEFT JOIN documents d ON d.id = p.id_document "
                "WHERE p.id_fiche = %s ORDER BY p.chemin",
                (int(ligne[0]),),
            )
            lignes = cursor.fetchall()
            # Le chemin d'archive du PDF de la fiche : première moitié de la clé
            # d'idempotence du dernier dépôt traite (normcase(chemin) | SHA-256).
            cursor.execute(
                "SELECT split_part(cle_idempotence, '|', 1) FROM lot_dossier "
                "WHERE id_fiche = %s AND statut = 'traite' AND cle_idempotence IS NOT NULL "
                "ORDER BY traite_le DESC LIMIT 1",
                (int(ligne[0]),),
            )
            pdf_source = cursor.fetchone()
    return {
        "fichier_source": ligne[1],
        "pdf_source": pdf_source[0] if pdf_source and pdf_source[0] else None,
        "pieces": [
            {
                "chemin": piece[0], "role": piece[1], "empreinte_sha256": piece[2],
                "taille_octets": int(piece[3]) if piece[3] is not None else None,
                "id_document": int(piece[4]) if piece[4] is not None else None,
                "nom": piece[5],
            }
            for piece in lignes
        ],
    }


def enregistrer_routes_fiches(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:
    """Branche les routes du Lot B.2 dans l'application FastAPI existante."""

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

        Sans Redis : traitement en tâche de fond IN-PROCESS (thread, état en
        base, consultable par GET /lots/{id}) par défaut — X-SEAMTECH-BACKGROUND:
        false force le traitement synchrone (jeux de test, petits lots)."""
        verifier_auth(config, token)
        _exiger_postgres(index)
        racine = str(corps.get("racine") or "").strip()
        if not racine:
            raise HTTPException(status_code=422, detail='Corps attendu : {"racine": "/chemin/de/la/racine"}.')
        try:
            id_lot = creer_lot(index, Path(racine), notes=corps.get("notes"))
        except DepotImpossible as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur
        if fond:
            import threading

            thread = threading.Thread(
                target=executer_lot,
                args=(index, id_lot),
                name=f"lot-{id_lot}",
                daemon=True,
            )
            thread.start()
            LOGGER.info("Lot #%d en tâche de fond (in-process, sans Redis).", id_lot)
            return {"id_lot": id_lot, "statut": "en_cours", "traitement": "fond"}
        return {"id_lot": id_lot, "statut": "termine", "traitement": "synchrone", "etat": executer_lot(index, id_lot)}

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
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return corriger_champ(
            index, code,
            corps.get("champ"), corps.get("valeur"), corps.get("utilisateur"),
            rang=corps.get("rang"), commentaire=corps.get("commentaire"),
        )

    @app.post("/fiches/{code}/valider")
    def route_valider_fiche(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return valider_fiche(index, code, corps.get("utilisateur"), corps.get("commentaire"))

    @app.post("/fiches/{code}/rejeter")
    def route_rejeter_fiche(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return rejeter_fiche(index, code, corps.get("utilisateur"), corps.get("motif") or "")

    @app.post("/fiches/{code}/rouvrir")
    def route_rouvrir_fiche(
        code: str,
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return rouvrir_fiche(index, code, corps.get("utilisateur"), corps.get("commentaire"), bool(corps.get("effacer_corrections")))

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
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        codes = [str(c) for c in (corps.get("codes") or [])]
        if not codes:
            raise HTTPException(status_code=422, detail="Liste « codes » vide — rien à valider.")
        return valider_lot(index, codes, corps.get("utilisateur"), acquittement_humain=bool(corps.get("acquittement_humain")), commentaire=corps.get("commentaire"))
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
        _exiger_postgres(index)
        return pieces_de_fiche(index, code)

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
