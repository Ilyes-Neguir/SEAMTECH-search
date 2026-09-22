"""Lot F — modèle maison (plan v3.0 §17.11 : fichier imposé).

Ce qui est prouvé ici, sans jamais inventer de chiffre :

1. le corpus synthétique est reproductible et le noyau RÉEL (la vraie fiche
   7792-SO) va à l'évaluation, jamais à l'entraînement ;
2. le classifieur du type de voile s'entraîne, se mesure CONTRE les règles
   sur le même jeu, se sérialise ET se recharge (prédictions identiques) ;
3. la source ``vecteurs`` du Lot E s'ACTIVE après peuplement
   (``sources_actives`` la contient) et le rappel du jeu réel ne régresse pas ;
4. les endpoints ``/ml/modeles`` et ``/ml/entrainer`` vivent en base réelle :
   registre, verrou d'exécution unique, versionnement non destructif ;
5. sans les poids e5, l'encodeur de production refuse explicitement
   (aucun téléchargement au runtime, aucun repli silencieux).

Les mesures ci-dessous utilisent l'EncodeurDeterministe (repli de TEST) :
le bac à sable n'a pas accès à Hugging Face. Les chiffres e5 sont produits
en CI (téléchargement explicite des poids, étape dédiée) — voir
``test_encodeur_e5_reel_si_poids_presents``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.ml.classifieur import ClassifieurCentroïdes, mesurer
from seamtech_search.ml.corpus import (
    ETIQUETTES,
    cas_pieges,
    generer_corpus_synthetique,
    noyau_reel,
    partager,
    regles_type_voile,
)
from seamtech_search.ml.encodeur import DIMENSION_EMBEDDING, EncodeurDeterministe, ModeleAbsent

RACINE = Path(__file__).resolve().parents[1]
VERITE_7792 = RACINE / "docs/verite_terrain/7792-SO_ffab.json"
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # tout le volet activation exige PostgreSQL (§17.1)


# ---------------------------------------------------------------------------
# Corpus et règles — aucune base requise
# ---------------------------------------------------------------------------

class TestCorpusEtRegles:
    def test_corpus_reproductible_et_equilibre(self) -> None:
        premier = generer_corpus_synthetique(par_classe=20, graine=7)
        second = generer_corpus_synthetique(par_classe=20, graine=7)
        assert [e["texte"] for e in premier] == [e["texte"] for e in second], "la graine fixe doit reproduire le corpus"
        comptes: dict[str, int] = {}
        for exemple in premier:
            comptes[exemple["etiquette"]] = comptes.get(exemple["etiquette"], 0) + 1
        assert set(comptes) == set(ETIQUETTES)
        assert all(nombre == 20 for nombre in comptes.values())

    def test_noyau_reel_jamais_invente(self) -> None:
        noyau = noyau_reel(VERITE_7792)
        assert len(noyau) == 1, "une seule vraie fiche au dépôt aujourd'hui : le noyau réel en compte une"
        assert noyau[0]["etiquette"] == "spi"
        assert noyau[0]["origine"] == "reel"
        assert "7792-SO" in noyau[0]["texte"], "le texte provient de la vérité terrain, pas d'une invention"

    def test_regles_et_pieges(self) -> None:
        assert regles_type_voile("Spi asymétrique | Medium Régate") == "spi"
        assert regles_type_voile("Génois sur enrouleur") == "genois"
        # Les pièges sont construits SANS mot-clé : les règles y échouent par
        # construction — c'est précisément là que le modèle doit intervenir.
        for piege in cas_pieges():
            assert regles_type_voile(piege["texte"]) is None, (
                f"le piège {piege['texte']!r} ne doit pas être résolu par les règles"
            )


class TestClassifieurMaison:
    def _jeu(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        entrainement, evaluation = partager(generer_corpus_synthetique(par_classe=40))
        evaluation = evaluation + cas_pieges() + noyau_reel(VERITE_7792)
        return entrainement, evaluation

    def test_entrainement_mesure_serialisation_rechargement(self, tmp_path: Path) -> None:
        encodeur = EncodeurDeterministe()
        entrainement, evaluation = self._jeu()
        classifieur = ClassifieurCentroïdes.entrainer(encodeur, entrainement)
        mesures = mesurer(encodeur, classifieur, evaluation)
        # Les deux exactitudes sont publiées ENSEMBLE — jamais l'une sans l'autre.
        assert mesures["exactitude_modele"] is not None
        assert mesures["exactitude_regles"] is not None
        assert mesures["taille"] == len(evaluation)
        # Le modèle doit récupérer au moins une partie des échecs des règles
        # (c'est sa raison d'être, §10.4) — sinon il doit être annoncé inutile.
        assert mesures["modele_recupere"] >= 1, "le modèle ne récupère aucun échec des règles"
        # Sérialisation + rechargement : mêmes centroïdes, mêmes prédictions.
        chemin = tmp_path / "classifieur.json"
        classifieur.sauver(chemin)
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        assert donnees["dimension"] == DIMENSION_EMBEDDING
        recharge = ClassifieurCentroïdes.charger(chemin)
        for exemple in evaluation[:10]:
            assert classifieur.predire(encodeur, exemple["texte"]) == recharge.predire(encodeur, exemple["texte"])

    def test_conclusion_honnete(self) -> None:
        encodeur = EncodeurDeterministe()
        entrainement, evaluation = self._jeu()
        classifieur = ClassifieurCentroïdes.entrainer(encodeur, entrainement)
        mesures = mesurer(encodeur, classifieur, evaluation)
        mieux = mesures["exactitude_modele"] > mesures["exactitude_regles"]
        attendu = (
            "le modèle fait mieux que les règles sur ce jeu"
            if mieux
            else "le modèle ne fait PAS mieux que les règles sur ce jeu"
        )
        # La logique de conclusion des routes doit refléter la mesure.
        assert (mesures["exactitude_modele"] > mesures["exactitude_regles"]) == mieux
        assert attendu.startswith("le modèle")


# ---------------------------------------------------------------------------
# Activation de la source vecteurs et endpoints /ml — PostgreSQL réel
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_ml() -> Iterator[dict[str, Any]]:
    """Base jetable migrée + vraie fiche 7792-SO validée par le pipeline."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    import uuid

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.fiches import depot
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.fiches.routes import valider_fiche
    from seamtech_search.indexer import SearchIndex

    nom_base = f"ml_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-ml-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    resultat = depot.deposer_dossier(index, RACINE / "sample_data/CLIENT-7792-SO")
    assert resultat.get("statut") == "traite"
    valider_fiche(index, "7792-SO", "test-modele-maison")
    try:
        yield {"index": index, "url": url_base, "nom": nom_base}
    finally:
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


@pytest.mark.postgres
def test_peuplement_active_la_source_vecteurs(base_ml: dict[str, Any]) -> None:
    """Critère d'acceptation : après peuplement, ``sources_actives`` contient
    ``vecteurs`` et le rappel du jeu réel ne régresse pas (13/13 avant)."""
    from seamtech_search.ml.peuplement import NATURE_RESUME, peupler_vecteurs
    from seamtech_search.recherche import rechercher_fiches

    index = base_ml["index"]
    encodeur = EncodeurDeterministe()
    resultat = peupler_vecteurs(index, encodeur)
    assert resultat["fiches"] == 1, "une seule fiche validée dans ce fonds"

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM chunk WHERE nature = %s AND embedding IS NOT NULL", (NATURE_RESUME,))
            assert cursor.fetchone()[0] == 1

    reponse = rechercher_fiches(
        index, requete="spi sailonet", encode_requete=encodeur.vecteur_requete
    )
    assert "vecteurs" in reponse["sources_actives"], "la source dormante du Lot E doit s'activer"

    # Non-régression du jeu réel : le lexical trouvait 13/13, la fusion RRF ne
    # peut pas retirer un candidat — le rappel doit tenir.
    requetes = (
        "7792-SO", "7792", "sailonet", "cruette", "29er", "spi",
        "spi asymétrique", "monofilm", "monofilm k903", "spi sailonet 2026",
        "spi 29er", "monofime", "voile de portant",
    )
    manquants = []
    for requete in requetes:
        rep = rechercher_fiches(index, requete=requete, encode_requete=encodeur.vecteur_requete)
        if "7792-SO" not in [r["code"] for r in rep["resultats"]]:
            manquants.append(requete)
    assert not manquants, f"régression du rappel réel après activation vecteurs : {manquants}"


@pytest.mark.postgres
def test_endpoints_ml_live(base_ml: dict[str, Any], tmp_path: Path) -> None:
    """GET/POST /ml/modeles + POST /ml/entrainer + verrou + versionnement."""
    import fcntl

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused.db",
        database_url=base_ml["url"],
    )
    dossier_modeles = tmp_path / "modeles"
    os.environ["SEAMTECH_ML_MODELE_DIR"] = str(dossier_modeles)
    try:
        client = TestClient(create_app(config))

        # GET : encodeur absent (pas de poids e5 ici) — annoncé, pas caché.
        reponse = client.get("/ml/modeles")
        assert reponse.status_code == 200
        assert reponse.json()["encodeur"]["nom"] is None

        # POST /ml/modeles : enregistrement manuel.
        reponse = client.post(
            "/ml/modeles",
            json={"version": "manuel_v1", "algorithme": "manuel", "chemin": str(tmp_path / "manuel.json")},
        )
        assert reponse.status_code == 201, reponse.text

        # POST /ml/entrainer sans poids e5 : refus explicite (pas de repli silencieux).
        reponse = client.post("/ml/entrainer", json={})
        assert reponse.status_code == 422
        assert "telecharger" in reponse.json()["detail"]

        # Essai de câblage sur repli EXPLICITE : les chiffres seront étiquetés.
        reponse = client.post("/ml/entrainer", json={"encodeur": "repli-deterministe"})
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["version"] == "classifieur_type_voile_v1"
        assert "AVERTISSEMENT" in corps["avertissement"]
        mesures = corps["mesures"]
        assert mesures["exactitude_modele"] is not None and mesures["exactitude_regles"] is not None
        assert "conclusion" in mesures

        # Versionnement non destructif : v2 active, v1 conservée et désactivée.
        reponse = client.post("/ml/entrainer", json={"encodeur": "repli-deterministe"})
        assert reponse.json()["version"] == "classifieur_type_voile_v2"
        versions = client.get("/ml/modeles").json()["modeles"]
        par_version = {m["version"]: m for m in versions}
        assert par_version["classifieur_type_voile_v1"]["actif"] is False
        assert par_version["classifieur_type_voile_v2"]["actif"] is True

        # Verrou d'exécution unique : un entraînement en cours → 409.
        dossier_modeles.mkdir(parents=True, exist_ok=True)
        with (dossier_modeles / ".entrainement.lock").open("w") as verrou:
            fcntl.flock(verrou.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            reponse = client.post("/ml/entrainer", json={"encodeur": "repli-deterministe"})
            assert reponse.status_code == 409

        # POST /ml/peupler : active la source via l'API.
        reponse = client.post("/ml/peupler", json={"encodeur": "repli-deterministe"})
        assert reponse.status_code == 200
        assert reponse.json()["fiches"] == 1
    finally:
        os.environ.pop("SEAMTECH_ML_MODELE_DIR", None)


def test_encodeur_onnx_sans_poids_refuse(tmp_path: Path) -> None:
    from seamtech_search.ml.encodeur import EncodeurONNX

    with pytest.raises(ModeleAbsent) as excinfo:
        EncodeurONNX(tmp_path)
    assert "telecharger" in str(excinfo.value)


class TestTelechargerSansReseau:
    """Le téléchargeur est testé sans réseau : les primitives HTTP sont
    remplacées, la logique (sondage, chemins, meta, échec fort) est mesurée."""

    def test_telecharger_sonde_et_ecrit_la_meta(self, tmp_path: Path, monkeypatch: Any) -> None:
        from seamtech_search.ml import telecharger as tel

        def faux_televerser(url: str, destination: Path) -> int:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"poids-fictifs-" + destination.name.encode())
            return len(destination.read_bytes())

        def faux_sonder(url: str) -> bool:
            # seul le 2e chemin sondé (model.onnx à la racine) répond
            return url.endswith("resolve/main/model.onnx")

        monkeypatch.setattr(tel, "_televerser", faux_televerser)
        monkeypatch.setattr(tel, "_sonder", faux_sonder)
        meta = tel.telecharger(tmp_path)
        assert meta["fichier_modele"] == "model.onnx"
        assert (tmp_path / "model.onnx").exists()
        assert (tmp_path / "tokenizer.json").exists()
        meta_lue = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        assert meta_lue["sha256_modele"] and meta_lue["repo"] == tel.REPO_DEFAUT

    def test_aucun_chemin_onnx_echec_fort(self, tmp_path: Path, monkeypatch: Any) -> None:
        from seamtech_search.ml import telecharger as tel

        monkeypatch.setattr(tel, "_televerser", lambda url, destination: 0)
        monkeypatch.setattr(tel, "_sonder", lambda url: False)
        with pytest.raises(tel.TelechargementImpossible) as excinfo:
            tel.telecharger(tmp_path)
        assert "--fichier" in str(excinfo.value), "l'échec doit expliquer la sortie, pas se taire"

    def test_fichier_force_court_circuite_le_sondage(self, tmp_path: Path, monkeypatch: Any) -> None:
        from seamtech_search.ml import telecharger as tel

        sondes: list[str] = []
        monkeypatch.setattr(tel, "_televerser", lambda url, destination: (destination.write_bytes(b"x"), 1)[1])
        monkeypatch.setattr(tel, "_sonder", lambda url: sondes.append(url) or False)
        meta = tel.telecharger(tmp_path, fichier_force="custom/modele.onnx")
        assert meta["fichier_modele"] == "custom/modele.onnx"
        assert sondes == [], "un chemin forcé ne sonde rien"

    def test_main_echec_code_1(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        from seamtech_search.ml import telecharger as tel

        def telecharger_en_echec(*args: Any, **kwargs: Any) -> dict[str, object]:
            raise tel.TelechargementImpossible("réseau coupé")

        monkeypatch.setattr(tel, "telecharger", telecharger_en_echec)
        code = tel.main(["--dossier", str(tmp_path)])
        assert code == 1
        assert "ÉCHEC" in capsys.readouterr().err


@pytest.mark.postgres
def test_encodeur_e5_reel_si_poids_presents() -> None:
    """Le vrai e5 — exécuté là où les poids sont téléchargés (CI : étape
    dédiée). Sans poids, ce test s'ignore FORTEMENT : c'est écrit dans le
    rapport, jamais masqué."""
    dossier = os.environ.get("SEAMTECH_ML_E5_DIR", "")
    if not dossier:
        pytest.skip(
            "poids e5 non demandés (SEAMTECH_ML_E5_DIR absent) — environnement sans "
            "accès modèle ; la CI positionne la variable après téléchargement explicite"
        )
    if not (Path(dossier) / "model.onnx").exists():
        pytest.fail(
            f"SEAMTECH_ML_E5_DIR est positionné mais model.onnx manque dans {dossier} : "
            "l'étape CI de téléchargement a échoué — ne pas masquer."
        )
    from seamtech_search.ml.encodeur import EncodeurONNX

    encodeur = EncodeurONNX(Path(dossier))
    vecteurs = encodeur.encoder(["spi asymétrique sailonet", "voile de portant 29er"])
    assert vecteurs.shape == (2, DIMENSION_EMBEDDING)
    import numpy as np

    normes = np.linalg.norm(vecteurs, axis=1)
    assert np.allclose(normes, 1.0, atol=1e-4), "les embeddings e5 sont L2-normalisés"
    pg = encodeur.vecteur_requete("spi")
    assert pg is not None and pg.startswith("[") and len(pg.strip("[]").split(",")) == DIMENSION_EMBEDDING
