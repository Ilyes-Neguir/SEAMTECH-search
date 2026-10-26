"""Lot 5 — Fonction B : confiance de réutilisation d'une voile.

Question métier : « pour ce modèle de bateau et ce type de voile, les voiles
déjà produites sont-elles assez cohérentes — et cette candidate assez proche
de leur médiane — pour envisager une réutilisation ? » La réponse est un
score robuste (médiane/MAD/IQR), JAMAIS une moyenne :

* moins de ``CONFIANCE_MIN_ECHANTILLONS`` (5) voiles comparables :
  « Données insuffisantes (N/5) » — aucun pourcentage, même partiel ;
* population hétérogène (dispersion relative forte sur la majorité des
  dimensions) : aucun score global — les statistiques par dimension restent
  affichées, la moyenne d'une population éclatée ne veut rien dire ;
* score ≥ ``CONFIANCE_SEUIL_ALERTE`` (75) : verdict « compatible » ;
  en dessous : le % est montré AVEC sa justification complète, mais aucun
  verdict positif (« à examiner ») ;
* justification obligatoire dès qu'un chiffre existe : N, médiane/IQR/MAD
  par dimension, écart de la candidate à la médiane, matériaux, et les
  fiches sources de la population.

Chaque évaluation d'approbateur est persistée (migration 030) : qui a
évalué, quand, sur quelle population — la décision reste traçable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.comptes import creer_utilisateur, ouvrir_session
from seamtech_search.confiance_reutilisation import (
    CONFIANCE_MIN_ECHANTILLONS,
    CONFIANCE_SEUIL_ALERTE,
    VERDICTS,
    ConfianceRefusee,
    evaluer_extraction,
    evaluer_population,
    lister_evaluations,
    statistiques,
)
from seamtech_search.config import AppConfig
from seamtech_search.extractions_voile import (
    ExtractionVoile,
    MateriauExtractionVoile,
    enregistrer_extraction_voile,
)
from seamtech_search.referentiel_bateaux import assurer_modele
from seamtech_search.schema_metier import (
    MIGRATIONS_METIER,
    SQL_030_EVALUATIONS_CONFIANCE,
    TABLES_METIER,
    VERSION_SCHEMA_METIER,
)

JETON_SERVICE = "jeton-service-lot5"


def _voile(**remplacements: Any) -> ExtractionVoile:  # noqa: ANN401
    donnees: dict[str, Any] = {
        "code_fiche": "LOT5-X",
        "nom_fichier_source": "x.pdf",
        "methode_principale": "fiche_extraite",
        "type_voile": "Spi Asymétrique",
        "modele_bateau_brut": "Bavaria 45",
        "slu_m": 6.6,
        "sf_m": 3.08,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
        "boat_model_id": 11,
        "statut_liaison": "lie_automatique",
    }
    donnees.update(remplacements)
    return ExtractionVoile(**donnees)


def _population_serree() -> list[ExtractionVoile]:
    slus = [6.55, 6.6, 6.6, 6.65, 6.7]
    sfs = [3.05, 3.08, 3.08, 3.1, 3.12]
    return [
        _voile(code_fiche=f"LOT5-P{i}", slu_m=slu, sf_m=sf)
        for i, (slu, sf) in enumerate(zip(slus, sfs, strict=True))
    ]


# ---------------------------------------------------------------------------
# Contrats purs
# ---------------------------------------------------------------------------


def test_constantes_verdicts_et_migration_030() -> None:
    assert CONFIANCE_MIN_ECHANTILLONS == 5
    assert CONFIANCE_SEUIL_ALERTE == 75
    assert VERDICTS == ("donnees_insuffisantes", "heterogene", "a_examiner", "compatible")
    assert VERSION_SCHEMA_METIER == "030_evaluations_confiance"
    assert MIGRATIONS_METIER[-1][0] == "030_evaluations_confiance"
    assert MIGRATIONS_METIER[-2][0] == "029_extraction_liaison_corrections"
    sql = SQL_030_EVALUATIONS_CONFIANCE
    for fragment in (
        "CREATE TABLE IF NOT EXISTS evaluations_confiance",
        "REFERENCES sail_extractions(id_extraction)",
        "REFERENCES boat_models(id_modele)",
        "verdict IN ('donnees_insuffisantes', 'heterogene', 'a_examiner', 'compatible')",
        "evaluations_confiance_score_coherent",
        "score IS NULL",
    ):
        assert fragment in sql, fragment
    assert len(TABLES_METIER) == 44
    assert TABLES_METIER[-1] == "evaluations_confiance"


def test_statistiques_robustes_mediane_mad_iqr() -> None:
    stats = statistiques([6.5, 6.6, 6.6, 6.7, 6.9])
    assert stats["n"] == 5
    assert stats["mediane"] == pytest.approx(6.6)
    assert stats["q1"] == pytest.approx(6.6)
    assert stats["q3"] == pytest.approx(6.7)
    assert stats["iqr"] == pytest.approx(0.1)
    assert stats["mad"] == pytest.approx(0.1)
    assert stats["min"] == 6.5 and stats["max"] == 6.9
    vide = statistiques([])
    assert vide["n"] == 0 and vide["mediane"] is None


def test_donnees_insuffisantes_jamais_de_pourcentage() -> None:
    population = _population_serree()[:3]
    resultat = evaluer_population(_voile(), population)
    assert resultat["verdict"] == "donnees_insuffisantes"
    assert resultat["score"] is None
    assert resultat["n_echantillons"] == 3
    assert resultat["libelle_verdict"] == "Données insuffisantes (3/5)"


def test_population_homogene_candidate_a_la_mediane_compatible() -> None:
    resultat = evaluer_population(_voile(slu_m=6.6, sf_m=3.08), _population_serree())
    assert resultat["verdict"] == "compatible"
    assert resultat["score"] is not None and resultat["score"] >= 99.0
    assert resultat["libelle_verdict"] != ""


def test_candidate_loin_de_la_mediane_a_examiner_sans_verdict_positif() -> None:
    resultat = evaluer_population(_voile(slu_m=7.4, sf_m=3.08), _population_serree())
    assert resultat["verdict"] == "a_examiner"
    assert resultat["score"] is not None and resultat["score"] < CONFIANCE_SEUIL_ALERTE
    dimensions = {d["champ"]: d for d in resultat["justification"]["dimensions"]}
    assert dimensions["slu_m"]["score_dim"] < 10.0
    assert dimensions["sf_m"]["score_dim"] >= 99.0


def test_population_heterogene_jamais_de_score_global() -> None:
    population = [
        _voile(code_fiche=f"LOT5-H{i}", slu_m=slu, sf_m=sf)
        for i, (slu, sf) in enumerate(
            zip([5.0, 6.0, 7.0, 8.0, 9.0], [2.0, 3.0, 4.0, 5.0, 6.0], strict=True)
        )
    ]
    resultat = evaluer_population(_voile(slu_m=7.0, sf_m=4.0), population)
    assert resultat["verdict"] == "heterogene"
    assert resultat["score"] is None
    dimensions = {d["champ"]: d for d in resultat["justification"]["dimensions"]}
    assert dimensions["slu_m"]["heterogene"] is True
    # Les statistiques restent visibles : c'est le score qui est refusé.
    assert dimensions["slu_m"]["mediane"] == pytest.approx(7.0)


def test_justification_complete_sources_et_materiaux() -> None:
    population = _population_serree()
    for voile in population:
        voile.materiaux = [
            MateriauExtractionVoile(role="panneau", niveau=1, designation="Monofilm K903")
        ]
    candidate = _voile(
        materiaux=[MateriauExtractionVoile(role="panneau", niveau=1, designation="Monofilm K903")]
    )
    resultat = evaluer_population(candidate, population)
    justification = resultat["justification"]
    assert justification["n_population"] == 5
    assert len(justification["sources"]) == 5
    assert {s["code_fiche"] for s in justification["sources"]} == {
        f"LOT5-P{i}" for i in range(5)
    }
    assert justification["materiaux"]["part_meme_materiau"] == pytest.approx(1.0)
    dimensions = {d["champ"]: d for d in justification["dimensions"]}
    for champ in ("slu_m", "sf_m"):
        assert dimensions[champ]["n"] == 5
        assert dimensions[champ]["mediane"] is not None
        assert dimensions[champ]["iqr"] is not None
        assert dimensions[champ]["valeur_candidate"] is not None


# ---------------------------------------------------------------------------
# Fixture PostgreSQL réelle
# ---------------------------------------------------------------------------


@pytest.fixture()
def monde_lot5(base_recherche: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    index = base_recherche["index"]
    comptes: dict[str, dict[str, Any]] = {}
    for cle, identifiant, role in (
        ("lecture", "lot5-lecture", "lecture"),
        ("approbateur", "lot5-approbateur", "approbateur"),
    ):
        compte = creer_utilisateur(
            index,
            identifiant,
            f"Compte {identifiant}",
            f"MotDePasse-{identifiant}-2026",
            role=role,
            doit_changer_mot_de_passe=False,
        )
        session = ouvrir_session(index, int(compte["id_utilisateur"]))
        comptes[cle] = {**compte, **session}

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            assurer_modele(curseur, "Bavaria 45")

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused-lot5.db",
        database_url=base_recherche["url"],
        auth_token=JETON_SERVICE,
        storage_backend="local",
        web_worker_enabled=False,
    )
    return {
        **base_recherche,
        "client": TestClient(create_app(config)),
        "comptes": comptes,
        "compteur": 0,
    }


def _entetes(compte: dict[str, Any]) -> dict[str, str]:
    return {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-UTILISATEUR": str(compte["identifiant"]),
        "X-SEAMTECH-ROLE": str(compte["role"]),
        "X-SEAMTECH-SESSION": str(compte["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(compte["jeton"]),
    }


def _enregistrer(monde: dict[str, Any], brut: str | None, **remplacements: Any) -> int:  # noqa: ANN401
    monde["compteur"] += 1
    numero = monde["compteur"]
    donnees: dict[str, Any] = {
        "code_fiche": f"LOT5-{numero:03d}",
        "nom_fichier_source": f"fiche-lot5-{numero}.pdf",
        "empreinte_sha256": hashlib.sha256(f"lot5-{numero}-{brut}".encode()).hexdigest(),
        "gabarit_code": "FICHE_PORTANT_V1",
        "methode_principale": "fiche_extraite",
        "type_voile": "Spi Asymétrique",
        "modele_bateau_brut": brut,
        "slu_m": 6.6,
        "sf_m": 3.08,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
    }
    donnees.update(remplacements)
    return enregistrer_extraction_voile(monde["index"], ExtractionVoile(**donnees))


def _semer_population(monde: dict[str, Any]) -> int:
    """5 voiles serrées + la candidate ; rend l'id de la candidate."""
    for slu in (6.55, 6.6, 6.6, 6.65, 6.7):
        _enregistrer(monde, "Bavaria 45", slu_m=slu)
    return _enregistrer(monde, "Bavaria 45", slu_m=6.6)


@pytest.mark.postgres
def test_evaluation_persistee_relue_et_candidate_exclue(monde_lot5: dict[str, Any]) -> None:
    index = monde_lot5["index"]
    approbateur = monde_lot5["comptes"]["approbateur"]
    id_candidate = _semer_population(monde_lot5)

    evaluation = evaluer_extraction(
        index, id_candidate, id_utilisateur=int(approbateur["id_utilisateur"])
    )
    # La candidate ne fait pas partie de sa propre population : N = 5, pas 6.
    assert evaluation["n_echantillons"] == 5
    assert evaluation["verdict"] == "compatible"
    assert evaluation["score"] is not None and evaluation["score"] >= CONFIANCE_SEUIL_ALERTE
    assert evaluation["id_evaluation"] >= 1

    relues = lister_evaluations(index, id_candidate)
    assert len(relues["evaluations"]) == 1
    assert int(relues["evaluations"][0]["id_evaluation"]) == int(evaluation["id_evaluation"])
    assert relues["evaluations"][0]["verdict"] == "compatible"

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT verdict, score, n_echantillons FROM evaluations_confiance "
                "WHERE id_evaluation = %s",
                (int(evaluation["id_evaluation"]),),
            )
            ligne = curseur.fetchone()
    assert ligne is not None and ligne[0] == "compatible" and ligne[2] == 5


@pytest.mark.postgres
def test_evaluation_refusee_sans_modele_lie(monde_lot5: dict[str, Any]) -> None:
    index = monde_lot5["index"]
    id_non_liee = _enregistrer(monde_lot5, "Prototype inconnu")
    with pytest.raises(ConfianceRefusee):
        evaluer_extraction(
            index,
            id_non_liee,
            id_utilisateur=int(monde_lot5["comptes"]["approbateur"]["id_utilisateur"]),
        )


@pytest.mark.postgres
def test_api_confiance_roles_et_parcours(monde_lot5: dict[str, Any]) -> None:
    client: TestClient = monde_lot5["client"]
    comptes = monde_lot5["comptes"]
    id_candidate = _semer_population(monde_lot5)

    reponse = client.post(
        "/confiance-reutilisation/evaluer",
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
        json={"id_extraction": id_candidate},
    )
    assert reponse.status_code == 401

    reponse = client.post(
        "/confiance-reutilisation/evaluer",
        headers=_entetes(comptes["lecture"]),
        json={"id_extraction": id_candidate},
    )
    assert reponse.status_code == 403

    reponse = client.post(
        "/confiance-reutilisation/evaluer",
        headers=_entetes(comptes["approbateur"]),
        json={"id_extraction": id_candidate},
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["verdict"] == "compatible"
    assert corps["justification"]["n_population"] == 5

    reponse = client.get(
        "/confiance-reutilisation/evaluations",
        params={"id_extraction": id_candidate},
        headers=_entetes(comptes["lecture"]),
    )
    assert reponse.status_code == 200
    assert len(reponse.json()["evaluations"]) == 1

    with monde_lot5["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT id_modele FROM boat_models")
            id_modele = int(curseur.fetchone()[0])
    reponse = client.get(
        "/confiance-reutilisation/population",
        params={"boat_model_id": id_modele, "type_voile": "Spi Asymétrique"},
        headers=_entetes(comptes["lecture"]),
    )
    assert reponse.status_code == 200
    population = reponse.json()
    assert population["n_echantillons"] == 6  # population seule : candidate incluse
    dimensions = {d["champ"]: d for d in population["dimensions"]}
    assert dimensions["slu_m"]["mediane"] == pytest.approx(6.6)

    reponse = client.post(
        "/confiance-reutilisation/evaluer",
        headers=_entetes(comptes["approbateur"]),
        json={"id_extraction": 99999999},
    )
    assert reponse.status_code == 404
