"""Lot I — assistant sourcé : preuves PostgreSQL (plan v3.0 §11, Phase 4).

Contre une base jetable semée par ``tests/conftest.py::base_assistant``
(corpus Lot E + cotes SYNTHÉTIQUES étiquetées + VRAIE fiche 7792-SO entrée par
le pipeline réglé), ce fichier prouve :

- le jeu d'essai des 8 questions du commanditaire, avec la réponse obtenue
  (rendue dans ``docs/verite_terrain/RAPPORT_20260922_LOT_I.md``) ;
- l'INVARIANT CENTRAL : toute réponse qui affirme une valeur porte au moins
  une citation vérifiable — et un refus n'affirme rien (le même garde existe
  hors PostgreSQL dans ``tests/test_assistant.py``, c'est lui qu'on voit
  rougir puis verdir dans le rapport) ;
- le champ « non applicable » (RG5) : l'assistant dit « sans objet », il
  n'invente pas de valeur ;
- la journalisation « comme les recherches » (même table ``recherche_log``,
  canal distinct) ;
- RG13 : l'assistant n'écrit RIEN dans l'archive (empreinte SHA-256 de
  l'arbre ``sample_data`` inchangée avant/après les 8 questions) ;
- la mesure p50/p95 sur le jeu des 8 (marqueur ``perf`` : exécutée hors
  instrumentation dans l'étape CI dédiée, publiée en ::notice).

⚠️ ÉTIQUETAGE (même règle que le Lot E) : le corpus contient 12 fiches
SYNTHÉTIQUES + UNE fiche réelle. Les mesures portent sur ce mélange, étiqueté
tel quel ; la fiche réelle seule est identifiée dans chaque réponse par son
code (7792-SO).
"""

from __future__ import annotations

import hashlib
import statistics
import time
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.assistant import (
    CITATIONS_MAX,
    ETAT_AMBIGU,
    ETAT_OK,
    ETAT_SANS_SOURCE,
    poser,
)
from tests.conftest import JEU_8_QUESTIONS, publier_mesure_perf, seuil_perf_p95_ms

pytestmark = pytest.mark.postgres

RACINE = Path(__file__).resolve().parents[1]
ARCHIVE = RACINE / "sample_data"

# Réponses attendues — chacune issue de la vérité terrain (fonds réel) ou du
# semis déterministe (synthétique), écrites EN CLAIR.
ATTENDU_SLU_7792 = 6.6          # 'cotes.finie.slu_m' — vérité terrain réelle
ATTENDU_GALON_BORDURE = "Nylon"  # 'galon.bordure.matiere' — vérité terrain réelle
ATTENDU_COMPTAGE_PORTANT_2024 = 1          # 0812-SPI-001 (synthétique)
ATTENDU_PLAGE_SLU = {"0701-GV-001", "0702-GV-003", "7792-SO"}  # 2 synth + 1 réel


def _empreinte_archive() -> str:
    """SHA-256 de l'arbre archive (chemins + contenus) — RG13."""
    empreinte = hashlib.sha256()
    for chemin in sorted(p for p in ARCHIVE.rglob("*") if p.is_file()):
        empreinte.update(str(chemin.relative_to(ARCHIVE)).encode())
        empreinte.update(chemin.read_bytes())
    return empreinte.hexdigest()


def _question_cible(index: Any, question: str, inclure_a_valider: bool = False) -> dict[str, Any]:
    reponse = poser(index, question, inclure_a_valider)
    assert "etat" in reponse and "reponse" in reponse and "citations" in reponse, (
        f"contrat violé pour {question!r} : {sorted(reponse)}"
    )
    return reponse


@pytest.mark.postgres
def test_q1_slu_fiche_reelle(base_assistant: dict[str, Any]) -> None:
    """Q1 — « quelle est la SLU de la fiche 7792-SO ? » → 6,60 m, citation
    vérifiable avec la ZONE PDF de la trace réelle."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[0][0])
    assert reponse["etat"] == ETAT_OK, reponse
    assert "6,60 m" in reponse["reponse"], reponse["reponse"]
    assert reponse["citations"], "aucune réponse sans citation"
    citation = reponse["citations"][0]
    assert citation["code_fiche"] == "7792-SO"
    assert citation["champ"] == "cotes.finie.slu_m"
    assert citation["table_cible"] == "fiche_cotes" and citation["colonne_cible"] == "slu_m"
    assert float(citation["valeur_normalisee"]) == ATTENDU_SLU_7792
    assert citation["zone"] is not None, "la trace réelle porte la zone PDF"
    assert citation["lien"].startswith("/dossier/7792-SO?champ=cotes.finie.slu_m")


@pytest.mark.postgres
def test_q2_voiles_par_bateau(base_assistant: dict[str, Any]) -> None:
    """Q2 — « quelles voiles pour le bateau 29er ? » → le type RÉELLEMENT
    enregistré pour le seul bateau 29er du fonds."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[1][0])
    assert reponse["etat"] == ETAT_OK, reponse
    assert "Spi Asymétrique" in reponse["reponse"], reponse["reponse"]
    assert reponse["citations"], "aucune réponse sans citation"
    assert reponse["citations"][0]["code_fiche"] == "7792-SO"


@pytest.mark.postgres
def test_q3_comptage_type_annee(base_assistant: dict[str, Any]) -> None:
    """Q3 — « combien de fiches de type portant en 2024 ? » → comptage sourcé :
    chaque fiche comptée est citée, le critère est rappelé."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[2][0])
    assert reponse["etat"] == ETAT_OK, reponse
    assert f"{ATTENDU_COMPTAGE_PORTANT_2024} fiche" in reponse["reponse"], reponse["reponse"]
    codes = {citation["code_fiche"] for citation in reponse["citations"]}
    assert "0812-SPI-001" in codes, codes
    assert len(reponse["citations"]) == ATTENDU_COMPTAGE_PORTANT_2024


@pytest.mark.postgres
def test_q4_plage_slu(base_assistant: dict[str, Any]) -> None:
    """Q4 — « quelles fiches ont une SLU entre 6,5 et 6,7 m ? » → 3 fiches
    (2 synthétiques + la réelle), chaque valeur citée ; le jeu par défaut
    (mesures finies) est étiqueté dans la réponse."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[3][0])
    assert reponse["etat"] == ETAT_OK, reponse
    assert "mesures finies" in reponse["reponse"], reponse["reponse"]
    codes = {citation["code_fiche"] for citation in reponse["citations"]}
    assert codes == ATTENDU_PLAGE_SLU, codes
    valeurs = {citation["code_fiche"]: float(citation["valeur_normalisee"]) for citation in reponse["citations"]}
    assert valeurs["7792-SO"] == ATTENDU_SLU_7792
    assert 6.5 <= min(valeurs.values()) and max(valeurs.values()) <= 6.7


@pytest.mark.postgres
def test_q5_matiere_galon_bordure(base_assistant: dict[str, Any]) -> None:
    """Q5 — « quelle matière pour le galon de bordure ? » → Nylon (fonds
    réel), citation pointant fiche_galon + zone PDF de la trace."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[4][0])
    assert reponse["etat"] == ETAT_OK, reponse
    assert ATTENDU_GALON_BORDURE in reponse["reponse"], reponse["reponse"]
    citation = reponse["citations"][0]
    assert citation["table_cible"] == "fiche_galon"
    assert citation["code_fiche"] == "7792-SO"
    assert citation["zone"] is not None, "la trace réelle porte la zone PDF"


@pytest.mark.postgres
def test_q6_refus_sans_invention(base_assistant: dict[str, Any]) -> None:
    """Q6 — question SANS réponse dans la base → refus explicite, zéro
    citation, aucune valeur fabriquée (double garde : ici en base réelle,
    et hors PostgreSQL dans tests/test_assistant.py)."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[5][0])
    assert reponse["etat"] == ETAT_SANS_SOURCE, reponse
    assert reponse["citations"] == [], "un refus ne cite rien"
    assert "ne trouve pas" in reponse["reponse"], reponse["reponse"]
    # Aucune valeur plausible inventée : pas de nombre à unité dans le refus.
    import re as _re

    assert not _re.search(r"\d+(?:,\d+)?\s?(?:m|m²|kg|cm|mm)\b", reponse["reponse"]), reponse["reponse"]


@pytest.mark.postgres
def test_q7_ambigu_liste_lectures_sourcees(base_assistant: dict[str, Any]) -> None:
    """Q7 — « la matière de la fiche 7792-SO ? » → la question est AMBIGUE :
    l'assistant liste les lectures (tissu / galons / renforts), chacune avec
    ses propres citations."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[6][0])
    assert reponse["etat"] == ETAT_AMBIGU, reponse
    assert len(reponse["interpretations"]) >= 2, reponse["interpretations"]
    lectures = " ".join(i["lecture"] for i in reponse["interpretations"])
    assert "tissu" in lectures and "galon" in lectures
    for interpretation in reponse["interpretations"]:
        assert interpretation["citations"], f"lecture non sourcée : {interpretation['lecture']}"
    assert "Monofilm K903" in " ".join(i["reponse"] for i in reponse["interpretations"])
    assert ATTENDU_GALON_BORDURE in " ".join(i["reponse"] for i in reponse["interpretations"])


@pytest.mark.postgres
def test_q8_non_applicable_rg5(base_assistant: dict[str, Any]) -> None:
    """Q8 — champ consigné « ~ » (RG5) → l'assistant DIT « sans objet »,
    cite la fiche, et n'invente aucun chiffre."""
    reponse = _question_cible(base_assistant["index"], JEU_8_QUESTIONS[7][0])
    assert reponse["etat"] == ETAT_OK, reponse
    assert "sans objet" in reponse["reponse"], reponse["reponse"]
    assert "~" in reponse["reponse"], reponse["reponse"]
    citation = reponse["citations"][0]
    assert citation["table_cible"] == "fiche_jonction" and citation["colonne_cible"] == "surplus"
    assert citation["zone"] is not None, "la trace réelle porte la zone PDF"
    import re as _re

    assert not _re.search(r"\d+(?:,\d+)?\s?(?:m|m²|kg|cm|mm)\b", reponse["reponse"]), reponse["reponse"]


@pytest.mark.postgres
def test_invariant_reponses_sourcees_100_pourcent(base_assistant: dict[str, Any]) -> None:
    """INVARIANT CENTRAL sur le jeu des 8 : toute réponse qui affirme une
    valeur (ok / ambigu) porte ≥ 1 citation ; tout refus n'en porte aucune.
    Taux de réponses sourcées = 100 % des réponses effectives."""
    sourcées = 0
    effectives = 0
    for question, _etiquette in JEU_8_QUESTIONS:
        reponse = poser(base_assistant["index"], question)
        if reponse["etat"] in (ETAT_OK, ETAT_AMBIGU):
            effectives += 1
            if reponse["etat"] == ETAT_AMBIGU:
                assert all(i["citations"] for i in reponse["interpretations"]), question
                ok = len(reponse["interpretations"]) > 0
            else:
                ok = len(reponse["citations"]) >= 1
                assert ok, f"réponse sans citation : {question!r} → {reponse['reponse']!r}"
            sourcées += int(ok)
        else:
            assert reponse["etat"] == ETAT_SANS_SOURCE, question
            assert reponse["citations"] == [], question
    assert effectives == 7, f"7 réponses effectives attendues (1 refus), mesuré {effectives}"
    assert sourcées == effectives, "taux de réponses sourcées < 100 %"
    assert sourcées / effectives == 1.0


@pytest.mark.postgres
def test_journalisation_comme_recherches(base_assistant: dict[str, Any]) -> None:
    """Chaque question est journalisée dans recherche_log (même table que la
    recherche hybride, canal « assistant ») : question, nb de citations,
    durée — la matière première de la mesure d'usage réel."""
    index = base_assistant["index"]
    comptes: dict[str, int] = {}
    for question, _etiquette in JEU_8_QUESTIONS:
        reponse = poser(index, question)
        comptes[question] = len(reponse["citations"])
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT requete, filtres, nb_resultats FROM recherche_log "
                "WHERE filtres->>'canal' = 'assistant' ORDER BY id_recherche"
            )
            lignes = cursor.fetchall()
    assert len(lignes) == len(JEU_8_QUESTIONS)
    for requete, filtres, nb_resultats in lignes:
        assert requete in comptes, requete
        assert filtres["canal"] == "assistant"
        assert filtres["etat"] in (ETAT_OK, ETAT_AMBIGU, ETAT_SANS_SOURCE, "occupe", "indisponible")
        assert isinstance(filtres["duree_ms"], (int, float)) and filtres["duree_ms"] >= 0
        assert nb_resultats == comptes[requete], f"{requete!r} : journal {nb_resultats} ≠ citations {comptes[requete]}"


@pytest.mark.postgres
def test_rg13_archive_intacte_apres_assistant(base_assistant: dict[str, Any]) -> None:
    """RG13 — l'assistant est en LECTURE sur l'archive : l'empreinte SHA-256
    de l'arbre sample_data est inchangée après le jeu des 8 questions."""
    avant = _empreinte_archive()
    for question, _etiquette in JEU_8_QUESTIONS:
        poser(base_assistant["index"], question)
    assert _empreinte_archive() == avant, "l'archive a été modifiée : violation RG13"


@pytest.mark.postgres
def test_rg14_aucun_reseau_sous_namespace_isole(base_assistant: dict[str, Any]) -> None:
    """RG14 (partie intégration) — sous un namespace réseau vide (``unshare
    -n``, cf. scripts/mesure_assistant.py --sans-reseau qui le met en œuvre),
    le jeu des 8 répond identiquement : la preuve COMPLÈTE (sortie brute) est
    produite hors pytest. Ici : la garde structurelle — le module assistant
    n'importe aucun client réseau."""
    import seamtech_search.assistant as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for interdit in ("import requests", "import urllib", "import socket", "import httpx", "boto3", "redis"):
        assert interdit not in source, f"le module assistant ne doit pas importer {interdit}"


@pytest.mark.postgres
def test_borne_citations() -> None:
    """Garde-fou budgétaire : le nombre de citations par réponse est borné."""
    assert CITATIONS_MAX == 20


@pytest.mark.postgres
@pytest.mark.perf
def test_perf_assistant_jeu_8(base_assistant: dict[str, Any]) -> None:
    """MESURE (§17.13) — p50/p95 du temps de réponse sur les 8 questions,
    n exact et étiqueté : 1 échauffement + 10 passages mesurés = n = 80
    (8 questions × 10). Publiée via SEAMTECH_PERF_JSON (étape CI dédiée,
    sans instrumentation). Le critère PRODUIT p95 < 100 ms est asserté avec
    le seuil d'environnement standard du projet."""
    index = base_assistant["index"]
    for question, _etiquette in JEU_8_QUESTIONS:
        poser(index, question)  # échauffement : plans PostgreSQL, connexions

    durees: list[float] = []
    par_question: dict[str, list[float]] = {}
    for _passage in range(10):
        for question, _etiquette in JEU_8_QUESTIONS:
            debut = time.perf_counter()
            reponse = poser(index, question)
            duree_ms = (time.perf_counter() - debut) * 1000.0
            durees.append(duree_ms)
            par_question.setdefault(question, []).append(duree_ms)
            assert reponse["etat"] in (ETAT_OK, ETAT_AMBIGU, ETAT_SANS_SOURCE)

    n = len(durees)
    p50 = statistics.median(durees)
    p95 = sorted(durees)[int(n * 0.95) - 1]
    maximum = max(durees)
    publier_mesure_perf("assistant_jeu_8_corpus_mixte", p50, p95, maximum, n)
    print(
        f"\n[mesure Lot I — assistant] jeu des 8 questions, n = {n} "
        f"(8 questions × 10 passages, 1 échauffement, corpus étiqueté synthétique + réel 7792-SO) : "
        f"p50 = {p50:.1f} ms, p95 = {p95:.1f} ms, max = {maximum:.1f} ms"
    )
    for question, valeurs in sorted(par_question.items()):
        print(
            f"  p50 = {statistics.median(valeurs):7.1f} ms | p95 = {sorted(valeurs)[9]:7.1f} ms | {question[:60]}"
        )
    assert p95 < seuil_perf_p95_ms(), f"p95 = {p95:.1f} ms ≥ seuil {seuil_perf_p95_ms()} ms"


# ---------------------------------------------------------------------------
# Intentions secondaires (hors jeu des 8) — mêmes invariants
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_option_consignee_non(base_assistant: dict[str, Any]) -> None:
    """Option consignée « Non » (faux) dans la fiche : l'assistant répond Non
    avec la citation fiche_option — il n'interprète pas un silence."""
    reponse = _question_cible(base_assistant["index"], "la fiche 7792-SO a-t-elle une protection anti-uv ?")
    assert reponse["etat"] == ETAT_OK, reponse
    assert "Non" in reponse["reponse"], reponse["reponse"]
    citation = reponse["citations"][0]
    assert citation["table_cible"] == "fiche_option" and citation["colonne_cible"] == "valeur_bool"
    assert citation["valeur_normalisee"] in ("False", "false")


@pytest.mark.postgres
def test_champ_fiche_client(base_assistant: dict[str, Any]) -> None:
    """Champ de tête par code : le client RÉEL de la fiche (vérité terrain)."""
    reponse = _question_cible(base_assistant["index"], "quel est le client de la fiche 7792-SO ?")
    assert reponse["etat"] == ETAT_OK, reponse
    assert "Sailonet" in reponse["reponse"], reponse["reponse"]
    assert reponse["citations"][0]["code_fiche"] == "7792-SO"


@pytest.mark.postgres
def test_surplus_renseigne_n_est_pas_sans_objet(base_assistant: dict[str, Any]) -> None:
    """Un surplus chiffré consigné est rendu tel quel (RG5 ne s'applique qu'à
    « ~ ») : on sème un surplus synthétique sur une fiche du corpus."""
    index = base_assistant["index"]
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fiche_jonction (id_fiche, nature, ordre, surplus) "
                "VALUES ((SELECT id_fiche FROM fiche WHERE code = '0701-GV-001'), 'surplus', 1, '15 mm') "
                "ON CONFLICT DO NOTHING"
            )
    reponse = _question_cible(index, "quel est le surplus de jonction de la fiche 0701-GV-001 ?")
    assert reponse["etat"] == ETAT_OK, reponse
    assert "15 mm" in reponse["reponse"], reponse["reponse"]
    assert "sans objet" not in reponse["reponse"]
    assert reponse["citations"][0]["table_cible"] == "fiche_jonction"


@pytest.mark.postgres
def test_galon_bordure_avec_code(base_assistant: dict[str, Any]) -> None:
    """Question 5 formulée AVEC le code : même réponse sourcée (Nylon, réel)."""
    reponse = _question_cible(base_assistant["index"], "quelle matière pour le galon de bordure de la fiche 7792-SO ?")
    assert reponse["etat"] == ETAT_OK, reponse
    assert "Nylon" in reponse["reponse"], reponse["reponse"]
    assert reponse["citations"][0]["zone"] is not None


@pytest.mark.postgres
def test_cote_absente_fiche_existante_refus(base_assistant: dict[str, Any]) -> None:
    """La fiche existe mais la cote demandée n'y est pas : refus explicite
    (« non renseigné »), aucune valeur inventée."""
    reponse = _question_cible(base_assistant["index"], "quelle est la SLE de la fiche 0701-GV-001 ?")
    assert reponse["etat"] == ETAT_SANS_SOURCE, reponse
    assert reponse["citations"] == []
    assert "ne porte aucune valeur SLE" in reponse["reponse"] or "non renseigné" in reponse["reponse"]


@pytest.mark.postgres
def test_fiche_inconnue_refus_avec_rg3(base_assistant: dict[str, Any]) -> None:
    """Code inexistant (ou non validé) : refus qui dit RG3, pas de citation."""
    reponse = _question_cible(base_assistant["index"], "quelle est la SLU de la fiche 9999-XX ?")
    assert reponse["etat"] == ETAT_SANS_SOURCE, reponse
    assert reponse["citations"] == []
    assert "RG3" in reponse["reponse"] or "non validée" in reponse["reponse"]


@pytest.mark.postgres
def test_plage_slu_en_mesures_dessin_explicite(base_assistant: dict[str, Any]) -> None:
    """Le jeu de cotes est honoré quand la question le précise : en mesures
    dessin, seule la fiche réelle porte la cote dans l'intervalle."""
    reponse = _question_cible(base_assistant["index"], "quelles fiches ont une SLU entre 6,5 et 6,7 m en mesures dessin ?")
    assert reponse["etat"] == ETAT_OK, reponse
    assert "mesures dessin" in reponse["reponse"], reponse["reponse"]
    codes = {citation["code_fiche"] for citation in reponse["citations"]}
    assert codes == {"7792-SO"}, codes


@pytest.mark.postgres
def test_route_assistant_200_jeu_complet(base_assistant: dict[str, Any]) -> None:
    """La route HTTP rend le contrat complet sur les 8 questions (TestClient,
    auth désactivée) — même sémantique que poser() direct."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[RACINE],
        database_path=Path("/tmp/unused-route-assistant.db"),
        database_url=base_assistant["url"],
        min_free_bytes=0,
    )
    with TestClient(create_app(config)) as client:
        for question, _etiquette in JEU_8_QUESTIONS:
            reponse = client.post("/assistant", json={"question": question})
            assert reponse.status_code == 200, question
            corps = reponse.json()
            assert corps["etat"] in (ETAT_OK, ETAT_AMBIGU, ETAT_SANS_SOURCE), question
            assert "reponse" in corps and "citations" in corps and "duree_ms" in corps
