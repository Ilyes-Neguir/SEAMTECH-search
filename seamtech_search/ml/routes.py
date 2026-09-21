"""Routes /ml — modèles, entraînement, peuplement (Lot F, plan §17.5).

- ``GET /ml/modeles``  : registre ``ml_modele`` + état de l'encodeur.
- ``POST /ml/modeles`` : enregistrement manuel d'un modèle entraîné hors API.
- ``POST /ml/entrainer`` : entraînement du classifieur du type de voile —
  EXÉCUTION UNIQUE À LA FOIS (verrou fichier exclusif, 409 sinon) ; la
  version précédente est conservée (jamais supprimée), la nouvelle est
  activée ; les métriques sont publiées AVEC celles des règles.
- ``POST /ml/peupler`` : production des vecteurs (active la source dormante).

Postes 8 Go : entraînement synchrone court (centroïdes sur quelques centaines
d'exemples) — pas de file de travaux supplémentaire.
"""

from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Header, HTTPException
from pydantic import BaseModel, Field

from seamtech_search.ml.classifieur import VERSION_ALGORITHME, ClassifieurCentroïdes, mesurer
from seamtech_search.ml.corpus import (
    cas_pieges,
    generer_corpus_synthetique,
    noyau_reel,
    partager,
)
from seamtech_search.ml.encodeur import EncodeurDeterministe, charger_encodeur
from seamtech_search.ml.peuplement import peupler_vecteurs

LOGGER = logging.getLogger(__name__)

FICHIER_VERROU = ".entrainement.lock"
VERITE_7792_RELATIF = Path("docs/verite_terrain/7792-SO_ffab.json")


class CorpsModele(BaseModel):
    version: str = Field(min_length=1, max_length=120)
    algorithme: str = Field(min_length=1, max_length=120)
    chemin: str = Field(min_length=1, max_length=1000)
    hyperparametres: dict[str, Any] = Field(default_factory=dict)
    metriques: dict[str, Any] = Field(default_factory=dict)
    actif: bool = False


class CorpsEntrainement(BaseModel):
    par_classe: int = Field(default=40, ge=8, le=500)
    graine: int = Field(default=20260921)
    encodeur: str = Field(default="auto", pattern="^(auto|repli-deterministe)$")


class CorpsPeuplement(BaseModel):
    encodeur: str = Field(default="auto", pattern="^(auto|repli-deterministe)$")


def _verifier_auth(config: Any, verifier: Callable[[Any, str | None], None], token: str | None) -> None:
    verifier(config, token)


def _exiger_postgres(index: Any) -> None:
    """503 explicite hors PostgreSQL — ml_modele/ml_run sont des tables métier
    (migration 008) sans plan SQLite (§17.1)."""
    if not index.is_postgres:
        raise HTTPException(
            status_code=503,
            detail="Les endpoints /ml exigent la couche métier PostgreSQL (§17.1).",
        )


def _choisir_encodeur(modeles_dir: Path, demande: str) -> tuple[Any, str | None]:
    """L'encodeur demandé + l'avertissement éventuel à publier dans la réponse."""
    if demande == "repli-deterministe":
        return EncodeurDeterministe(), (
            "AVERTISSEMENT : encodeur de repli déterministe — les chiffres produits "
            "prouvent le câblage, PAS les performances de e5-small. Publier comme « repli »."
        )
    encodeur = charger_encodeur(modeles_dir)
    if encodeur is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Encodeur e5 absent dans {modeles_dir}. Action opérateur : "
                "python -m seamtech_search.ml.telecharger (aucun téléchargement au runtime). "
                "Pour un essai de câblage sans poids : encodeur='repli-deterministe' — "
                "les chiffres seront étiquetés repli."
            ),
        )
    return encodeur, None


def enregistrer_routes_ml(
    app: Any,  # noqa: ANN401 - FastAPI
    index: Any,  # noqa: ANN401 - SearchIndex
    config: Any,  # noqa: ANN401 - AppConfig
    verifier_auth: Callable[[Any, str | None], None],
    modeles_dir: Path,
    racine_verite: Path,
) -> None:
    modeles_dir = Path(modeles_dir)
    # PAS de mkdir ici : au démarrage le dossier des modèles peut être en
    # lecture seule (conteneur non-root, volume monté) — mesuré en échec le
    # 21/09 (PermissionError /app/data/modeles au boot Docker). La création se
    # fait au moment d'écrire, avec un refus explicite si non inscriptible.

    @app.get("/ml/modeles")
    def route_ml_modeles(token: str | None = Header(None, alias="X-SEAMTECH-TOKEN")) -> dict[str, Any]:
        _verifier_auth(config, verifier_auth, token)
        _exiger_postgres(index)
        encodeur = charger_encodeur(modeles_dir)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT version, algorithme, hyperparametres, metriques, chemin, actif, created_at "
                    "FROM ml_modele ORDER BY created_at DESC"
                )
                lignes = cursor.fetchall()
        return {
            "encodeur": (
                {"nom": encodeur.nom, "dimension": encodeur.dimension, "dossier": str(modeles_dir)}
                if encodeur is not None
                else {"nom": None, "dossier": str(modeles_dir), "note": "poids e5 absents — python -m seamtech_search.ml.telecharger"}
            ),
            "modeles": [
                {
                    "version": ligne[0],
                    "algorithme": ligne[1],
                    "hyperparametres": ligne[2],
                    "metriques": ligne[3],
                    "chemin": ligne[4],
                    "actif": ligne[5],
                    "cree_le": ligne[6].isoformat() if ligne[6] is not None else None,
                }
                for ligne in lignes
            ],
        }

    @app.post("/ml/modeles", status_code=201)
    def route_ml_modeles_creer(
        corps: CorpsModele, token: str | None = Header(None, alias="X-SEAMTECH-TOKEN")
    ) -> dict[str, Any]:
        _verifier_auth(config, verifier_auth, token)
        _exiger_postgres(index)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                try:
                    cursor.execute(
                        "INSERT INTO ml_modele (version, algorithme, hyperparametres, metriques, chemin, actif) "
                        "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING id_modele",
                        (
                            corps.version,
                            corps.algorithme,
                            _json(corps.hyperparametres),
                            _json(corps.metriques),
                            corps.chemin,
                            corps.actif,
                        ),
                    )
                except Exception as exc:
                    raise HTTPException(status_code=409, detail=f"Version déjà enregistrée ou refusée : {exc}") from exc
                if corps.actif:
                    cursor.execute(
                        "UPDATE ml_modele SET actif = FALSE WHERE version <> %s", (corps.version,)
                    )
                id_modele = cursor.fetchone()[0]
        return {"id_modele": id_modele, "version": corps.version}

    @app.post("/ml/entrainer")
    def route_ml_entrainer(
        corps: CorpsEntrainement = Body(default_factory=CorpsEntrainement),
        token: str | None = Header(None, alias="X-SEAMTECH-TOKEN"),
    ) -> dict[str, Any]:
        _verifier_auth(config, verifier_auth, token)
        _exiger_postgres(index)
        chemin_verrou = modeles_dir / FICHIER_VERROU
        try:
            modeles_dir.mkdir(parents=True, exist_ok=True)
            handle_verrou = chemin_verrou.open("w")
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Le dossier des modèles {modeles_dir} n'est pas inscriptible — "
                    "un entraînement ne peut pas y être sauvegardé. Montez un volume "
                    "inscriptible ou définissez SEAMTECH_ML_MODELE_DIR."
                ),
            ) from exc
        with handle_verrou as verrou:
            try:
                fcntl.flock(verrou.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise HTTPException(status_code=409, detail="Un entraînement est déjà en cours (verrou exclusif).") from exc
            try:
                return _entrainer(index, modeles_dir, racine_verite, corps)
            except OSError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Échec d'écriture du modèle dans {modeles_dir} : {exc}",
                ) from exc
            finally:
                fcntl.flock(verrou.fileno(), fcntl.LOCK_UN)

    @app.post("/ml/peupler")
    def route_ml_peupler(
        corps: CorpsPeuplement = Body(default_factory=CorpsPeuplement),
        token: str | None = Header(None, alias="X-SEAMTECH-TOKEN"),
    ) -> dict[str, Any]:
        _verifier_auth(config, verifier_auth, token)
        _exiger_postgres(index)
        encodeur, avertissement = _choisir_encodeur(modeles_dir, corps.encodeur)
        resultat = peupler_vecteurs(index, encodeur)
        if avertissement:
            resultat["avertissement"] = avertissement
        return resultat


def _entrainer(index: Any, modeles_dir: Path, racine_verite: Path, corps: CorpsEntrainement) -> dict[str, Any]:
    import json as json_module

    encodeur, avertissement = _choisir_encodeur(modeles_dir, corps.encodeur)
    synthetiques = generer_corpus_synthetique(par_classe=corps.par_classe, graine=corps.graine)
    entrainement, evaluation = partager(synthetiques, graine=corps.graine)
    # Les pièges et le noyau RÉEL vont à l'évaluation — jamais à l'entraînement.
    evaluation = evaluation + cas_pieges() + noyau_reel(racine_verite / VERITE_7792_RELATIF)
    classifieur = ClassifieurCentroïdes.entrainer(encodeur, entrainement)
    mesures = mesurer(encodeur, classifieur, evaluation)
    mesures["encodeur"] = encodeur.nom
    mesures["entrainement_taille"] = len(entrainement)
    mieux_que_les_regles = (
        mesures["exactitude_modele"] is not None
        and mesures["exactitude_regles"] is not None
        and mesures["exactitude_modele"] > mesures["exactitude_regles"]
    )
    mesures["conclusion"] = (
        "le modèle fait mieux que les règles sur ce jeu"
        if mieux_que_les_regles
        else "le modèle ne fait PAS mieux que les règles sur ce jeu — annoncé comme tel (§10.4)"
    )

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM ml_modele WHERE algorithme = %s", (VERSION_ALGORITHME,))
            numero = int(cursor.fetchone()[0]) + 1
            version = f"classifieur_type_voile_v{numero}"
            chemin_fichier = modeles_dir / f"{version}.json"
            classifieur.sauver(chemin_fichier)
            cursor.execute(
                "INSERT INTO ml_modele (version, algorithme, hyperparametres, metriques, chemin, actif) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, TRUE) RETURNING id_modele",
                (
                    version,
                    VERSION_ALGORITHME,
                    _json({"par_classe": corps.par_classe, "graine": corps.graine}),
                    _json(mesures),
                    str(chemin_fichier),
                ),
            )
            id_modele = cursor.fetchone()[0]
            # La version précédente est CONSERVÉE (lisible, désactivée) — jamais supprimée.
            cursor.execute("UPDATE ml_modele SET actif = FALSE WHERE id_modele <> %s", (id_modele,))
            cursor.execute(
                "INSERT INTO ml_run (id_modele, statut, taille_corpus, scores, fini_le, note) "
                "VALUES (%s, 'termine', %s, %s::jsonb, now(), %s)",
                (
                    id_modele,
                    len(entrainement),
                    _json(mesures),
                    avertissement,
                ),
            )
    reponse = {"version": version, "chemin": str(chemin_fichier), "mesures": mesures}
    if avertissement:
        reponse["avertissement"] = avertissement
    LOGGER.info("Entraînement %s terminé : %s", version, json_module.dumps(mesures, ensure_ascii=False))
    return reponse


def _json(objet: dict[str, Any]) -> str:
    import json as json_module

    return json_module.dumps(objet, ensure_ascii=False)
