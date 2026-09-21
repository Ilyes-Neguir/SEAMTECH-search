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
from seamtech_search.fiches.extraction import ExtractionImpossible, analyser_pdf, texte_normalise
from seamtech_search.fiches.gabarits import GabaritInconnu, charger_gabarits, detecter_gabarit

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
