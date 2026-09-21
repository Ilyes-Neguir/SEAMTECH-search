"""Lot E — facettes avec compteurs et suggestions au fil de la frappe
(plan v3.0 §17.2, §17.5). Fichier imposé par §17.11.

Comme un moteur généraliste : chaque facette compte sur l'ensemble filtré
par le texte ET par les autres filtres (jamais par elle-même) ; les
suggestions ne proposent QUE des valeurs réellement présentes en base.
"""

from __future__ import annotations

from typing import Any

import pytest

from seamtech_search.recherche import FACETTE_LIMITE, rechercher_fiches, suggerer
from tests.conftest import FICHES_CORPUS

pytestmark = pytest.mark.postgres

# Attendus calculés sur le corpus semé par tests/conftest.py (12 fiches
# validées — les a_valider et rejetées n'entrent JAMAIS dans les compteurs).
FACETTES_CORPUS = {
    "type_voile": {"Grand-voile": 5, "Génois": 4, "Spinnaker": 2, "Tourmentin": 1},
    "client": {"Voilerie Atlantique": 7, "Chantier Méditerranée": 5},
    "bateau": {"First 30 30 pieds": 5, "Sun Fast 36 36 pieds": 4, "Figaro 3 29 pieds": 3},
    "gamme": {"Régate": 6, "Croisière": 5, "Course": 1},
    "annee": {"2023": 4, "2024": 4, "2025": 3, "2022": 1},
    "matiere": {"Dacron": 5, "Mylar": 4, "Monofilm": 3},
}


def en_dict(facette: list[dict[str, Any]]) -> dict[str, int]:
    return {entree["valeur"]: entree["effectif"] for entree in facette}


@pytest.mark.postgres
def test_facettes_compteurs_du_corpus(base_recherche: dict[str, Any]) -> None:
    """Sans filtre ni texte : les compteurs sont exactement ceux du corpus
    validé (aucun a_valider, aucun rejeté ne gonfle un effectif)."""
    reponse = rechercher_fiches(base_recherche["index"], requete="")
    for nom, attendu in FACETTES_CORPUS.items():
        assert en_dict(reponse["facettes"][nom]) == attendu, f"facette {nom}"
        assert len(reponse["facettes"][nom]) <= FACETTE_LIMITE
    assert sum(reponse["facettes"]["type_voile"][i]["effectif"] for i in range(4)) == 12


@pytest.mark.postgres
def test_facettes_filtres_croises(base_recherche: dict[str, Any]) -> None:
    """Une facette se calcule avec les AUTRES filtres appliqués : filtrer sur
    le client réduit les compteurs des types de voile en conséquence."""
    reponse = rechercher_fiches(
        base_recherche["index"], requete="", filtres={"client": "Voilerie Atlantique"}
    )
    assert en_dict(reponse["facettes"]["type_voile"]) == {
        "Grand-voile": 3, "Génois": 2, "Spinnaker": 1, "Tourmentin": 1,
    }
    # La facette client, elle, n'est pas filtrée par elle-même : les deux
    # clients restent proposés avec leurs compteurs (comportement standard).
    assert en_dict(reponse["facettes"]["client"]) == FACETTES_CORPUS["client"]

    croise = rechercher_fiches(
        base_recherche["index"],
        requete="",
        filtres={"client": "Voilerie Atlantique", "type_voile": "Grand-voile"},
    )
    # Les 3 GV de la Voilerie Atlantique : GV-001 (2024), GV-002 et GV-005 (2023).
    assert en_dict(croise["facettes"]["annee"]) == {"2023": 2, "2024": 1}
    assert croise["nb_resultats"] == 3


@pytest.mark.postgres
def test_facettes_matiere_et_annee_depuis_les_tables_enfants(base_recherche: dict[str, Any]) -> None:
    """La facette matière vient de fiche_materiau → materiau (pas d'un champ
    de la fiche) : le MLD honnête du Lot A se lit dans la recherche."""
    reponse = rechercher_fiches(
        base_recherche["index"], requete="", filtres={"matiere": "Monofilm"}
    )
    assert en_dict(reponse["facettes"]["matiere"]) == FACETTES_CORPUS["matiere"]
    assert en_dict(reponse["facettes"]["type_voile"]) == {"Grand-voile": 3}
    # GV-001 (2024), GV-003 et GV-004 (2025).
    assert en_dict(reponse["facettes"]["annee"]) == {"2025": 2, "2024": 1}


@pytest.mark.postgres
def test_facettes_respectent_la_requete_texte(base_recherche: dict[str, Any]) -> None:
    """Le texte filtre aussi les facettes : « kevlar » n'existe que dans le
    PDF de la 0812-SPI-002 → toutes les facettes s'effondrent à cette fiche."""
    reponse = rechercher_fiches(base_recherche["index"], requete="kevlar")
    assert en_dict(reponse["facettes"]["type_voile"]) == {"Spinnaker": 1}
    assert en_dict(reponse["facettes"]["client"]) == {"Chantier Méditerranée": 1}
    assert en_dict(reponse["facettes"]["annee"]) == {"2023": 1}


@pytest.mark.postgres
def test_filtre_annee_bornes(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    reponse = rechercher_fiches(index, requete="", filtres={"annee_min": 2024, "annee_max": 2024})
    assert reponse["nb_resultats"] == 4
    assert {r["annee"] for r in reponse["resultats"]} == {2024}
    # Comportement moteur généraliste : la facette année n'est pas filtrée
    # par le filtre d'année (toutes les années restent proposées), tandis que
    # les autres facettes reflètent le filtre.
    assert en_dict(reponse["facettes"]["annee"]) == FACETTES_CORPUS["annee"]
    # Les 4 fiches 2024 : GV-001, GEN-002, GEN-004, SPI-001.
    assert en_dict(reponse["facettes"]["type_voile"]) == {
        "Génois": 2, "Grand-voile": 1, "Spinnaker": 1,
    }


@pytest.mark.postgres
def test_suggestions_valeurs_reellement_presentes(base_recherche: dict[str, Any]) -> None:
    """Jamais de valeur inventée : chaque suggestion existe dans les
    référentiels semés (l'ensemble de contrôle est écrit en clair)."""
    index = base_recherche["index"]
    valeurs_connues = {
        "Grand-voile", "Génois", "Spinnaker", "Tourmentin",
        "Monofilm", "Dacron", "Mylar",
        "Voilerie Atlantique", "Chantier Méditerranée",
        "First 30", "Sun Fast 36", "Figaro 3",
        "Régate", "Croisière", "Course",
    } | {code for code, *_ in FICHES_CORPUS}

    for prefixe in ("mon", "grand", "voil", "fig", "rég", "0701"):
        reponse = suggerer(index, prefixe)
        assert reponse["suggestions"], f"aucune suggestion pour {prefixe!r}"
        for suggestion in reponse["suggestions"]:
            assert suggestion["valeur"] in valeurs_connues, (
                f"suggestion inventée : {suggestion!r} pour {prefixe!r}"
            )
            assert suggestion["nature"] and suggestion["valeur"]

    mon = {s["valeur"] for s in suggerer(index, "mon")["suggestions"]}
    assert "Monofilm" in mon
    grand = {s["valeur"] for s in suggerer(index, "grand")["suggestions"]}
    assert "Grand-voile" in grand


@pytest.mark.postgres
def test_suggestions_tolerance_fautes(base_recherche: dict[str, Any]) -> None:
    """« monofime » propose Monofilm via le volet trigrammes de la 012."""
    reponse = suggerer(base_recherche["index"], "monofime")
    valeurs = [s["valeur"] for s in reponse["suggestions"]]
    assert "Monofilm" in valeurs


@pytest.mark.postgres
def test_suggestions_vide_et_bornees(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    assert suggerer(index, "") == {"prefixe": "", "suggestions": []}
    assert suggerer(index, "   ")["suggestions"] == []
    courte = suggerer(index, "e", limite=3)
    assert len(courte["suggestions"]) <= 3


@pytest.mark.postgres
def test_endpoint_suggestions_forme_reponse(base_recherche: dict[str, Any]) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[Path("/tmp")], database_url=base_recherche["url"], min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        reponse = client.get("/recherche/suggestions", params={"prefix": "mon"})
        assert reponse.status_code == 200
        corps = reponse.json()
        assert corps["prefixe"] == "mon"
        assert {"nature", "valeur"} <= set(corps["suggestions"][0])


def test_endpoint_suggestions_503_sans_postgres(tmp_path: Any) -> None:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[tmp_path], database_url=None, min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        assert client.get("/recherche/suggestions", params={"prefix": "mon"}).status_code == 503
