"""Lot I — assistant sourcé : gardes SANS PostgreSQL (suite SQLite).

Ce fichier porte notamment LE TEST AUTOMATISÉ DU REFUS (exigence du
commanditaire) : sur une base vide simulée, une question sans réponse dans la
base produit un refus explicite — ``citations`` vide, aucune valeur inventée
(aucun nombre à unité dans la réponse). C'est ce test qu'on montre EN ROUGE
(repli « invention » volontairement introduit puis retiré) puis EN VERT dans
``docs/verite_terrain/RAPPORT_20260922_LOT_I.md``.

Sans PostgreSQL ici : la couche métier fiche n'existe que sur PostgreSQL
(§17.1) — les états de route (503 indisponible, 202-style « occupé ») et la
compréhension de question (regex, formats) sont testés sur des faux index ;
les preuves en base sont dans ``tests/test_assistant_postgres.py``.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.assistant import (
    _VERROU_ANALYSE,
    CITATIONS_MAX,
    ETAT_AMBIGU,
    ETAT_OCCUPE,
    ETAT_OK,
    ETAT_SANS_SOURCE,
    analyser_question,
    enregistrer_routes_assistant,
    formater_valeur,
    normaliser_question,
    poser,
)
from tests.conftest import JEU_8_QUESTIONS

# ---------------------------------------------------------------------------
# Faux index : une base « vide » — toute lecture ne rend aucune ligne.
# ---------------------------------------------------------------------------

class _CurseurVide:
    def __init__(self) -> None:
        self.derniere: tuple[str, Any] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.derniere = (sql, params)

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def __enter__(self) -> "_CurseurVide":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class _ConnexionVide:
    def cursor(self) -> _CurseurVide:
        return _CurseurVide()

    def __enter__(self) -> "_ConnexionVide":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class _IndexVide:
    """Index prétendument PostgreSQL dont la base est VIDE."""

    is_postgres = True

    def connect(self) -> _ConnexionVide:
        return _ConnexionVide()

    def _postgres_ts_config(self, connexion: Any) -> str:  # noqa: ANN401
        return "simple"


# ---------------------------------------------------------------------------
# LE garde-fou du refus (rouge quand on le sabote, vert sinon)
# ---------------------------------------------------------------------------

MOTIF_VALEUR_INVENTEE = re.compile(r"(\d+(?:,\d+)?)\s?(?:m|m²|kg|cm|mm)\b")


def _nombres(texte: str) -> set[float]:
    return {float(m.group(1).replace(",", ".")) for m in re.finditer(r"(\d+(?:,\d+)?)", texte)}


def test_refus_base_vide_n_invente_rien() -> None:
    """SUR UNE BASE VIDE, les 8 questions ne doivent produire AUCUNE valeur :
    refus explicite (« je ne trouve pas »), zéro citation — ou, unique
    exception admise, un comptage affiché à zéro (aucune valeur affirmée).

    Toute réponse qui affirme une valeur doit porter une citation ; sur base
    vide il n'existe aucune citation possible, donc aucune valeur ne peut
    sortir — le test échoue dès qu'un repli « plausible » est introduit.
    Les seuls nombres à unité tolérés dans une réponse sont ceux DE LA
    QUESTION elle-même (écho des bornes, ex. « entre 6,50 m et 6,70 m ») :
    tout nombre qui ne vient ni de la question ni d'une citation est une
    invention."""
    for question, _etiquette in JEU_8_QUESTIONS:
        reponse = poser(_IndexVide(), question)
        assert reponse["etat"] in (ETAT_OK, ETAT_AMBIGU, ETAT_SANS_SOURCE), question
        if reponse["citations"] or reponse["interpretations"]:
            # Réponse affirmée ⇒ il FAUT des citations ; sur base vide, aucun
            # résolveur ne peut en produire — on ne doit jamais passer ici.
            for citation in reponse["citations"]:
                assert citation.get("code_fiche"), question
            pytest.fail(
                f"VALEUR SANS SOURCE sur base vide pour {question!r} : "
                f"{reponse['reponse']!r} citations={reponse['citations']}"
            )
        assert "ne trouve pas" in reponse["reponse"] or reponse["reponse"].startswith(("0 fiche", "Aucune fiche")), (
            f"{question!r} → {reponse['reponse']!r}"
        )
        nombres_de_la_question = _nombres(question)
        for m in MOTIF_VALEUR_INVENTEE.finditer(reponse["reponse"]):
            assert float(m.group(1).replace(",", ".")) in nombres_de_la_question, (
                f"valeur plausible inventée pour {question!r} : {m.group(0)!r} dans {reponse['reponse']!r}"
            )


def test_refus_question_inconnue_nomme_le_refus() -> None:
    """La formule canonique du refus est présente (exigence commanditaire :
    « je ne trouve pas dans les fiches »), et les pistes ne sont pas des
    réponses — ce sont des façons de reformuler."""
    reponse = poser(_IndexVide(), "quelle est la couleur du chou de la fiche 7792-SO ?")
    assert reponse["etat"] == ETAT_SANS_SOURCE
    assert "ne trouve pas" in reponse["reponse"]
    assert reponse["citations"] == []
    for piste in reponse["pistes"]:
        assert not MOTIF_VALEUR_INVENTEE.search(piste), piste


def test_question_vide_reponse_usage_sans_appel() -> None:
    """Question vide : message d'usage, aucun appel base, aucun état « ok »."""
    reponse = poser(_IndexVide(), "   ")
    assert reponse["etat"] == ETAT_SANS_SOURCE
    assert reponse["citations"] == []


# ---------------------------------------------------------------------------
# États « occupé » et « indisponible »
# ---------------------------------------------------------------------------

def test_etat_occupe_reponse_immediate() -> None:
    """Verrou pris ⇒ état « occupé » immédiat (le poste cible est mono-cœur :
    l'analyse est sérialisée, l'état est observable, jamais un silence)."""
    assert _VERROU_ANALYSE.acquire(blocking=False)
    try:
        reponse = poser(_IndexVide(), JEU_8_QUESTIONS[0][0])
        assert reponse["etat"] == ETAT_OCCUPE
        assert reponse["citations"] == []
        assert reponse["duree_ms"] == 0.0
    finally:
        _VERROU_ANALYSE.release()


def test_apres_occupation_le_verrou_est_rendu() -> None:
    """Après une analyse (même en échec), le verrou est relâché : la question
    suivante n'hérite pas d'un « occupé » fantôme."""
    poser(_IndexVide(), JEU_8_QUESTIONS[5][0])
    assert _VERROU_ANALYSE.acquire(blocking=False)
    _VERROU_ANALYSE.release()


def _app_assistant(index: Any):  # noqa: ANN401
    from fastapi import FastAPI

    app = FastAPI()
    enregistrer_routes_assistant(app, index, SimpleNamespace(), lambda _config, _token: None)
    return app


def test_route_occupe_200_json() -> None:
    """La route expose l'état « occupé » en JSON (contrat commanditaire :
    ok / aucune source / occupé — pas une erreur opaque)."""
    assert _VERROU_ANALYSE.acquire(blocking=False)
    try:
        with TestClient(_app_assistant(SimpleNamespace(is_postgres=True))) as client:
            reponse = client.post("/assistant", json={"question": "quelle est la SLU de la fiche 7792-SO ?"})
        assert reponse.status_code == 200
        corps = reponse.json()
        assert corps["etat"] == ETAT_OCCUPE
        assert corps["citations"] == []
    finally:
        _VERROU_ANALYSE.release()


def test_route_503_indisponible_sans_postgres(tmp_path: Any) -> None:
    """Sans couche métier PostgreSQL (§17.1) : 503 avec état « indisponible »
    explicite — pas un résultat vide menteur."""
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[tmp_path], database_url=None, min_free_bytes=0)
    with TestClient(create_app(config)) as client:
        reponse = client.post("/assistant", json={"question": "quelle est la SLU de la fiche 7792-SO ?"})
        assert reponse.status_code == 503
        detail = reponse.json()["detail"]
        assert detail["etat"] == "indisponible"
        assert "PostgreSQL" in detail["message"]
        etat = client.get("/assistant/etat")
        assert etat.status_code == 200
        assert etat.json()["postgres"] is False


def test_route_valide_la_question(tmp_path: Any) -> None:
    """Question vide ou trop longue : 422 de validation (avant tout appel)."""
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[tmp_path], database_url=None, min_free_bytes=0)
    with TestClient(create_app(config)) as client:
        assert client.post("/assistant", json={"question": ""}).status_code == 422
        assert client.post("/assistant", json={"question": "x" * 501}).status_code == 422


# ---------------------------------------------------------------------------
# Compréhension déterministe (pure, sans base)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("question", "attendu"),
    [
        ("quelle est la SLU de la fiche 7792-SO ?", {"code": "7792-SO", "cote": "slu"}),
        ("quelle est la SLU en mesures dessin de 7792-SO ?", {"code": "7792-SO", "cote": "slu", "jeu": "dessin"}),
        ("quelles voiles pour le bateau 29er ?", {"bateau_terme": "29er", "veut_voiles": True}),
        ("combien de fiches de type portant en 2024 ?", {"combien": True, "terme_type": "portant", "annee": 2024}),
        ("quelles fiches ont une SLU entre 6,5 et 6,7 m ?", {"cote": "slu", "entre": True, "borne_min": 6.5, "borne_max": 6.7}),
        ("quelle matière pour le galon de bordure ?", {"bande_galon": "bordure", "attribut_galon": "matiere"}),
        ("quelle est la matière du galon de bordure de la fiche 7792-SO ?", {"code": "7792-SO", "bande_galon": "bordure"}),
        ("quel est le surplus de jonction de la fiche 7792-SO ?", {"code": "7792-SO", "demande_surplus": True}),
        ("quelle est la longueur du mât de la fiche 7792-SO ?", {"code": "7792-SO", "cote": None}),
        ("fiche 0701-GV-001 : quel est le poids ?", {"code": "0701-GV-001", "cote": "poids"}),
    ],
)
def test_analyser_question_jeu_8(question: str, attendu: dict[str, Any]) -> None:
    ctx = analyser_question(question)
    for cle, valeur in attendu.items():
        assert getattr(ctx, cle) == valeur, f"{cle} pour {question!r} : {getattr(ctx, cle)!r} ≠ {valeur!r}"


def test_analyser_question_reponse_complete_peut_etre_rejouee() -> None:
    """Le contexte des 8 questions est STABLE (aucun état global) : deux
    analyses donnent le même décodage — condition du cache et des mesures."""
    for question, _etiquette in JEU_8_QUESTIONS:
        assert vars(analyser_question(question)) == vars(analyser_question(question))


def test_normaliser_question_sans_accents() -> None:
    assert normaliser_question("Quelle matière pour le GÉNOIS ?") == "quelle matiere pour le genois ?"


def test_formater_valeur_francais() -> None:
    assert formater_valeur(6.6, "m", 2) == "6,60 m"
    assert formater_valeur(15.71, "m²", 2) == "15,71 m²"
    assert formater_valeur(0.7, "kg", 2) == "0,70 kg"
    assert formater_valeur(50.0, "mm", 1) == "50,0 mm"
    assert formater_valeur("Nylon") == "Nylon"
    assert formater_valeur(None) == "—"


def test_borne_citations_budgetaire() -> None:
    """Chaque réponse borne ses citations : pas de liste infinie."""
    assert CITATIONS_MAX == 20
