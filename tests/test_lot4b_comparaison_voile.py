"""Lot 4b — Fonction A : comparaison de voiles bloc par bloc.

Deux extractions (Lot 3a), résolues vers le référentiel bateau (Lot 3b),
sont comparées bloc par bloc : identification, dimensions canoniques
(écarts en unité, en cm pour les longueurs, en % par rapport à la voile de
référence A), matériaux appariés par rôle/niveau, qualité et traçabilité.

Règles d'honnêteté héritées du plan directeur :

* « comparable » (même modèle) exige le MÊME `boat_model_id` canonique ET le
  même type de voile normalisé — jamais une ressemblance de libellés ;
* des modèles différents (ou non liés) restent affichables côte à côte, mais
  signalés explicitement : on ne fait pas semblant ;
* une dimension manquante d'un côté reste manquante : aucun écart inventé,
  et un chevauchement < 2 dimensions communes est averti ;
* aucun verdict de réutilisation ici (c'est la Fonction B, Lot 5) : des
  écarts, des preuves, pas de pourcentage de confiance déguisé ;
* aucune migration : lecture seule sur `sail_extractions`.

Aucun PDF réel n'est versionné pour ce lot : les mesures sur fiches
d'entreprise transmises le 2026-10-25 ont été faites localement (hors Git)
et seules des valeurs synthétiques vivent dans ces tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.comparaison_voile import (
    DIMENSIONS_COMPAREES,
    ComparaisonRefusee,
    candidats_comparaison,
    comparaison_entre,
    comparer_extractions,
    normaliser_type_voile,
)
from seamtech_search.comptes import creer_utilisateur, ouvrir_session
from seamtech_search.config import AppConfig
from seamtech_search.extractions_voile import (
    ExtractionVoile,
    MateriauExtractionVoile,
    enregistrer_extraction_voile,
)
from seamtech_search.liaison_voile import corriger_extraction
from seamtech_search.referentiel_bateaux import assurer_modele

JETON_SERVICE = "jeton-service-lot4b"


def _extraction(**remplacements: Any) -> ExtractionVoile:  # noqa: ANN401
    donnees: dict[str, Any] = {
        "code_fiche": "LOT4B-A",
        "nom_fichier_source": "a.pdf",
        "gabarit_code": "FICHE_PORTANT_V1",
        "methode_principale": "fiche_extraite",
        "type_voile": "Spi Asymétrique",
        "gamme": "Medium Régate",
        "modele_bateau_brut": "Bavaria 45",
        "slu_m": 6.6,
        "sle_m": 5.5,
        "sf_m": 3.08,
        "shw_m": 3.14,
        "spa_m2": 15.71,
        "tetiere_m": 0.03,
        "poids_kg": 0.7,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
        "boat_model_id": 11,
        "statut_liaison": "lie_automatique",
    }
    donnees.update(remplacements)
    return ExtractionVoile(**donnees)


# ---------------------------------------------------------------------------
# Contrats purs
# ---------------------------------------------------------------------------


def test_vocabulaire_dimensions_et_normalisation_type() -> None:
    assert DIMENSIONS_COMPAREES == (
        "slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_m", "poids_kg",
    )
    assert normaliser_type_voile("Spi Asymétrique") == "spi asymetrique"
    assert normaliser_type_voile("  GÉNOIS ") == "genois"
    assert normaliser_type_voile("Grand-voile") == "grand voile"
    assert normaliser_type_voile(None) == ""


def test_deltas_dimensions_en_cm_et_pourcentages() -> None:
    a = _extraction()
    b = _extraction(
        code_fiche="LOT4B-B", slu_m=8.2, sf_m=3.9, spa_m2=18.2, tetiere_m=None,
    )
    comparaison = comparer_extractions(a, b)
    dimensions = {ligne["champ"]: ligne for ligne in comparaison["blocs"]["dimensions"]}

    slu = dimensions["slu_m"]
    assert slu["etat"] == "comparable"
    assert slu["delta"] == pytest.approx(1.6)
    assert slu["delta_cm"] == pytest.approx(160.0)
    assert slu["delta_pct"] == pytest.approx(24.24, abs=0.01)

    spa = dimensions["spa_m2"]
    assert spa["delta"] == pytest.approx(2.49)
    assert spa["delta_cm"] is None  # une surface ne se compare pas en cm
    assert spa["delta_pct"] == pytest.approx(15.85, abs=0.01)

    tetiere = dimensions["tetiere_m"]
    assert tetiere["etat"] == "manquant_b"
    assert tetiere["delta"] is None and tetiere["delta_pct"] is None

    poids = dimensions["poids_kg"]
    assert poids["delta"] == 0.0 and poids["delta_pct"] == 0.0

    synthese = comparaison["synthese"]
    assert synthese["dimensions_communes"] == 6
    # SF : (3,90 − 3,08) / 3,08 = +26,62 % — plus que la SLU (+24,24 %).
    assert synthese["ecart_max_champ"] == "sf_m"
    assert synthese["ecart_max_pct"] == pytest.approx(26.62, abs=0.01)


def test_comparable_exige_meme_modele_canonique_et_meme_type() -> None:
    a = _extraction()
    b = _extraction(code_fiche="LOT4B-B", type_voile="spi asymetrique")
    comparaison = comparer_extractions(a, b)
    assert comparaison["meme_modele_bateau"] is True
    assert comparaison["meme_type_voile"] is True
    assert comparaison["comparable"] is True
    assert not any("DIFFÉRENTS" in texte for texte in comparaison["avertissements"])

    autre_modele = comparer_extractions(a, _extraction(boat_model_id=12))
    assert autre_modele["meme_modele_bateau"] is False
    assert autre_modele["comparable"] is False
    assert any("DIFFÉRENTS" in texte for texte in autre_modele["avertissements"])

    non_lie = comparer_extractions(
        a, _extraction(boat_model_id=None, statut_liaison="non_resolu")
    )
    assert non_lie["comparable"] is False
    assert any("pas liée" in texte for texte in non_lie["avertissements"])

    autre_type = comparer_extractions(a, _extraction(type_voile="Génois"))
    assert autre_type["meme_type_voile"] is False
    assert autre_type["comparable"] is False

    faible = comparer_extractions(
        a,
        _extraction(sle_m=None, sf_m=None, shw_m=None, spa_m2=None, tetiere_m=None, poids_kg=None),
    )
    assert faible["synthese"]["dimensions_communes"] == 1
    assert any("Chevauchement insuffisant" in texte for texte in faible["avertissements"])

    implausible = comparer_extractions(a, _extraction(statut_plausibilite="implausible"))
    assert any("implausible" in texte for texte in implausible["avertissements"])


def test_materiaux_apparies_par_role_et_niveau() -> None:
    a = _extraction(materiaux=[
        MateriauExtractionVoile(role="panneau", niveau=1, designation="Dacron 260", grammage_g_m2=260.0),
        MateriauExtractionVoile(role="renfort", niveau=1, designation="Toile X", grammage_g_m2=None),
    ])
    b = _extraction(materiaux=[
        MateriauExtractionVoile(role="Panneau", niveau=1, designation="Dacron 280", grammage_g_m2=280.0),
    ])
    lignes = comparer_extractions(a, b)["blocs"]["materiaux"]
    panneau = next(ligne for ligne in lignes if ligne["role"] == "panneau")
    assert panneau["etat"] == "commun"
    assert panneau["a"]["designation"] == "Dacron 260"
    assert panneau["b"]["designation"] == "Dacron 280"
    assert panneau["delta_grammage_pct"] == pytest.approx(7.69, abs=0.01)
    renfort = next(ligne for ligne in lignes if ligne["role"] == "renfort")
    assert renfort["etat"] == "a_seul"
    assert renfort["b"] is None


def test_refus_de_comparer_une_extraction_a_elle_meme() -> None:
    a = _extraction(id_extraction=42)
    with pytest.raises(ComparaisonRefusee):
        comparer_extractions(a, _extraction(id_extraction=42))


# ---------------------------------------------------------------------------
# Fixture PostgreSQL réelle
# ---------------------------------------------------------------------------


@pytest.fixture()
def monde_lot4b(base_recherche: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    index = base_recherche["index"]
    comptes: dict[str, dict[str, Any]] = {}
    for cle, identifiant, role in (
        ("lecture", "lot4b-lecture", "lecture"),
        ("approbateur", "lot4b-approbateur", "approbateur"),
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
            assurer_modele(curseur, "Sun 2000")

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused-lot4b.db",
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
        "code_fiche": f"LOT4B-{numero:03d}",
        "nom_fichier_source": f"fiche-lot4b-{numero}.pdf",
        "empreinte_sha256": hashlib.sha256(f"lot4b-{numero}-{brut}".encode()).hexdigest(),
        "gabarit_code": "FICHE_PORTANT_V1",
        "gabarit_version": 2,
        "methode_principale": "fiche_extraite",
        "type_voile": "Spi Asymétrique",
        "modele_bateau_brut": brut,
        "slu_m": 6.6,
        "sle_m": 5.5,
        "sf_m": 3.08,
        "shw_m": 3.14,
        "spa_m2": 15.71,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
    }
    donnees.update(remplacements)
    return enregistrer_extraction_voile(monde["index"], ExtractionVoile(**donnees))


# ---------------------------------------------------------------------------
# Candidats et comparaison en base réelle
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_candidats_meme_modele_et_meme_type_uniquement(monde_lot4b: dict[str, Any]) -> None:
    index = monde_lot4b["index"]
    id_a = _enregistrer(monde_lot4b, "Bavaria 45")
    id_meme = _enregistrer(monde_lot4b, "bavaria 45", type_voile="SPI ASYMETRIQUE", slu_m=6.7)
    _enregistrer(monde_lot4b, "Bavaria 45", type_voile="Génois")          # autre type : exclu
    _enregistrer(monde_lot4b, "Sun 2000")                                  # autre modèle : exclu
    id_non_liee = _enregistrer(monde_lot4b, "Prototype inconnu")           # non résolue

    resultat = candidats_comparaison(index, id_a)
    ids = [int(c["extraction"]["id_extraction"]) for c in resultat["candidats"]]
    assert ids == [id_meme]
    assert resultat["candidats"][0]["dimensions_communes"] == 5
    assert resultat["raison"] is None

    sans_modele = candidats_comparaison(index, id_non_liee)
    assert sans_modele["candidats"] == []
    assert sans_modele["raison"] == "non_liee"


@pytest.mark.postgres
def test_comparaison_enrichie_noms_canoniques_et_corrections(monde_lot4b: dict[str, Any]) -> None:
    index = monde_lot4b["index"]
    approbateur = monde_lot4b["comptes"]["approbateur"]
    id_a = _enregistrer(monde_lot4b, "Bavaria 45")
    id_b = _enregistrer(monde_lot4b, "Bavaria 45", slu_m=6.9)
    corriger_extraction(
        index,
        id_a,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        champ="sf_m",
        valeur=3.1,
        motif="Valeur relue pour le test Lot 4b.",
    )

    comparaison = comparaison_entre(index, id_a, id_b)
    assert comparaison["comparable"] is True
    assert comparaison["a"]["modele_lie"] == "Bavaria 45"
    assert comparaison["b"]["modele_lie"] == "Bavaria 45"
    qualite = {ligne["champ"]: ligne for ligne in comparaison["blocs"]["qualite"]}
    assert qualite["corrections"]["a"] == 1
    assert qualite["corrections"]["b"] == 0
    dimensions = {ligne["champ"]: ligne for ligne in comparaison["blocs"]["dimensions"]}
    # La correction approbateur est la valeur comparée, pas la valeur machine.
    assert dimensions["sf_m"]["a"] == pytest.approx(3.1)
    assert dimensions["slu_m"]["delta_cm"] == pytest.approx(30.0)


@pytest.mark.postgres
def test_api_comparaison_roles_et_erreurs(monde_lot4b: dict[str, Any]) -> None:
    client: TestClient = monde_lot4b["client"]
    comptes = monde_lot4b["comptes"]
    id_a = _enregistrer(monde_lot4b, "Bavaria 45")
    id_b = _enregistrer(monde_lot4b, "Sun 2000", slu_m=8.2)

    # Jeton de service seul : aucune identité nominative, 401.
    reponse = client.get(
        "/comparaison-voiles", params={"a": id_a, "b": id_b},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert reponse.status_code == 401

    # Lecture : capacité de consultation, ouverte à tout compte nominatif.
    reponse = client.get(
        "/comparaison-voiles", params={"a": id_a, "b": id_b},
        headers=_entetes(comptes["lecture"]),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["comparable"] is False  # modèles différents, dit franchement
    assert any("DIFFÉRENTS" in texte for texte in corps["avertissements"])
    dimensions = {ligne["champ"]: ligne for ligne in corps["blocs"]["dimensions"]}
    assert dimensions["slu_m"]["delta_cm"] == pytest.approx(160.0)

    reponse = client.get(
        "/comparaison-voiles/candidats", params={"id_extraction": id_a},
        headers=_entetes(comptes["lecture"]),
    )
    assert reponse.status_code == 200
    assert reponse.json()["candidats"] == []  # aucun autre Bavaria 45 / Spi

    reponse = client.get(
        "/comparaison-voiles", params={"a": id_a, "b": id_a},
        headers=_entetes(comptes["lecture"]),
    )
    assert reponse.status_code == 422

    reponse = client.get(
        "/comparaison-voiles", params={"a": id_a, "b": 99999999},
        headers=_entetes(comptes["lecture"]),
    )
    assert reponse.status_code == 404
