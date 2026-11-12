"""Lot 19, priorité 2 — le recueil de verdicts humains sur la réutilisation.

La propriété qui fait tenir tout le dispositif tient en une phrase :
**l'humain ne doit pas voir le score.** Montrer « 82 % — cette voile vous
semble-t-elle réutilisable ? » ne mesure qu'une docilité : l'approbateur
répète la machine, et le seuil qu'on en tire ne fait que retrouver celui
qu'on lui a soufflé. Ces épreuves vérifient l'aveuglement d'abord, le
dépouillement ensuite, et pincent enfin le comportement qui doit rester
inchangé tant que le calibrage n'est pas fait.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.confiance_reutilisation import CONFIANCE_SEUIL_ALERTE
from seamtech_search.extractions_voile import ExtractionVoile
from seamtech_search.indexer import SearchIndex
from tests.helpers_postgres_isole import url_base_jetable

pytestmark = pytest.mark.postgres

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
RACINE = Path(__file__).resolve().parent.parent


def _charger_module() -> Any:
    chemin = RACINE / "scripts" / "recueillir_verdicts_reutilisation.py"
    spec = importlib.util.spec_from_file_location("recueillir_verdicts", chemin)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Aveuglement — épreuves pures, sans base
# ---------------------------------------------------------------------------


def test_la_purge_supprime_tout_ce_qui_revele_le_score() -> None:
    """score, verdict, seuil, z, hétérogénéité : rien ne doit subsister."""
    module = _charger_module()
    brut = {
        "n_population": 6,
        "seuil_alerte": 75,
        "seuil_min_echantillons": 5,
        "dimensions": [
            {
                "champ": "slu_m",
                "n": 6,
                "mediane": 12.1,
                "q1": 11.9,
                "q3": 12.6,
                "valeur_candidate": 12.4,
                "score_dim": 88.0,
                "z_robuste": 0.4,
                "heterogene": False,
            }
        ],
        "materiaux": {"candidat": ["Dacron"], "population": ["Dacron"]},
    }
    purgé = module.purger(brut)

    assert "seuil_alerte" not in purgé
    assert "seuil_min_echantillons" not in purgé
    dimension = purgé["dimensions"][0]
    assert "score_dim" not in dimension
    assert "z_robuste" not in dimension
    assert "heterogene" not in dimension
    # … mais les MESURES restent : c'est sur elles qu'on juge.
    assert dimension["mediane"] == 12.1
    assert dimension["valeur_candidate"] == 12.4
    assert purgé["n_population"] == 6


def test_le_texte_presente_ne_contient_pas_de_score() -> None:
    """La chaîne montrée à l'humain ne doit porter aucune note."""
    module = _charger_module()
    dimensions = [
        {"champ": "slu_m", "n": 6, "mediane": 12.1, "q1": 11.9, "q3": 12.6,
         "valeur_candidate": 12.4, "score_dim": 91.0},
        {"champ": "sf_m", "n": 6, "mediane": 3.7, "q1": 3.6, "q3": 3.9,
         "valeur_candidate": 3.71, "score_dim": 95.0},
    ]
    candidat = module._formater_mesures(dimensions, candidat=True)
    population = module._formater_mesures(dimensions, candidat=False)

    assert "91" not in candidat and "95" not in candidat
    assert "score" not in candidat.lower()
    assert "slu_m=12.4" in candidat
    assert "médiane 12.1" in population
    assert "score" not in population.lower()


# ---------------------------------------------------------------------------
# Dépouillement — épreuves pures
# ---------------------------------------------------------------------------


def _couples(score_bas: float, score_haut: float, par_classe: int) -> list[dict[str, Any]]:
    return [
        {"score": score_bas, "verdict_humain": "non_reutilisable", "verdict_machine": "a_examiner"}
        for _ in range(par_classe)
    ] + [
        {"score": score_haut, "verdict_humain": "reutilisable", "verdict_machine": "compatible"}
        for _ in range(par_classe)
    ]


def test_le_depouillement_propose_un_seuil_qui_separe() -> None:
    """Classes nettement séparées : un seuil est proposé, et il sépare."""
    module = _charger_module()
    couples = _couples(score_bas=40.0, score_haut=90.0, par_classe=15)
    rapport = module.depouiller(
        [{"id_aveugle": str(i), "verdict_humain": c["verdict_humain"]} for i, c in enumerate(couples)],
        {"sel": "x", "entrees": {str(i): {"id_extraction": i, "score": c["score"],
         "verdict_machine": c["verdict_machine"]} for i, c in enumerate(couples)}},
    )

    assert rapport["verdicts_recueillis"] == 30
    assert rapport["pret"] is True
    assert rapport["erreurs_au_seuil_propose"] == 0
    assert 40.0 < rapport["seuil_propose"] <= 90.0


def test_sous_le_minimum_aucun_seuil_nest_propose() -> None:
    """5 verdicts ne prouvent rien : le seuil actuel reste strictement inchangé."""
    module = _charger_module()
    couples = _couples(score_bas=40.0, score_haut=90.0, par_classe=3)
    rapport = module.depouiller(
        [{"id_aveugle": str(i), "verdict_humain": c["verdict_humain"]} for i, c in enumerate(couples)],
        {"sel": "x", "entrees": {str(i): {"id_extraction": i, "score": c["score"],
         "verdict_machine": c["verdict_machine"]} for i, c in enumerate(couples)}},
    )

    assert rapport["verdicts_recueillis"] == 6
    assert rapport["seuil_propose"] is None
    assert rapport["pret"] is False
    assert rapport["seuil_actuel"] == 75


def test_les_verdicts_incertains_ne_comptent_pas_pour_le_seuil() -> None:
    """« incertain » dit que le cas est ambigu, pas de quel côté il tombe."""
    module = _charger_module()
    lignes = [{"id_aveugle": str(i), "verdict_humain": "incertain"} for i in range(40)]
    cle = {"sel": "x", "entrees": {str(i): {"id_extraction": i, "score": 50.0 + i,
           "verdict_machine": "a_examiner"} for i in range(40)}}
    rapport = module.depouiller(lignes, cle)

    assert rapport["verdicts_recueillis"] == 40
    assert rapport["par_classe"]["incertain"] == 40
    assert rapport["seuil_propose"] is None, "40 « incertain » ne calibrent rien"
    assert rapport["pret"] is False


# ---------------------------------------------------------------------------
# Le comportement qui doit rester inchangé
# ---------------------------------------------------------------------------


def test_une_seule_dimension_aberrante_est_absorbee_par_la_mediane() -> None:
    """Propriété MESURÉE du score, figée ici pour qu'elle ne surprenne personne.

    Le score global est la MÉDIANE des scores par dimension (plan directeur v2 :
    « robuste, jamais une moyenne de moyennes »). Conséquence directe, mesurée
    sur un jeu d'essai : une voile de 16 m contre une population à 12 m — un
    écart de 33 %, énorme en voilerie — obtient **100 %** et le verdict
    « compatible », parce que les trois autres dimensions, conformes,
    emportent la médiane. Il faut DEUX dimensions fausses pour tomber sous le
    seuil (50 %), et quatre pour atteindre 0 %.

    Ce n'est pas un défaut qu'on corrige à la volée : c'est un choix de
    robustesse assumé, et le modifier changerait le comportement de tous les
    verdicts déjà rendus — précisément ce que la priorité 2 interdit sans
    verdicts humains. On le FIGE donc par une épreuve, pour que quiconque
    touchera à l'agrégation se pose la question explicitement. Et c'est
    exactement le genre de désaccord que le recueil aveugle est fait pour
    mettre en chiffres : si l'approbateur répond « non_réutilisable » là où la
    machine dit 100 %, le seuil ne sera pas le problème — l'agrégation si.
    """
    from seamtech_search.confiance_reutilisation import evaluer_population

    population = [
        ExtractionVoile(
            id_extraction=i, type_voile="Génois", slu_m=12.0, sf_m=3.7, shw_m=2.1, spa_m2=40.0
        )
        for i in range(1, 7)
    ]

    def score_de(**kwargs: float) -> float | None:
        return evaluer_population(
            ExtractionVoile(id_extraction=99, type_voile="Génois", **kwargs), population
        )["score"]

    # Une seule dimension aberrante : absorbée.
    assert score_de(slu_m=16.0, sf_m=3.7, shw_m=2.1, spa_m2=40.0) == 100.0
    # Deux : la médiane bascule.
    assert score_de(slu_m=16.0, sf_m=5.0, shw_m=2.1, spa_m2=40.0) == 50.0
    # Quatre : effondrement.
    assert score_de(slu_m=16.0, sf_m=5.0, shw_m=4.0, spa_m2=70.0) == 0.0


def test_sous_le_seuil_le_verdict_est_a_examiner() -> None:
    """Tant que rien n'est calibré : pas de verdict automatique positif sous 75 %.

    C'est le comportement que le patron a aujourd'hui — il déduit lui-même à
    partir de la justification. Cette épreuve le fige : si quelqu'un bouge le
    seuil sans verdicts humains, elle casse.
    """
    from seamtech_search.confiance_reutilisation import evaluer_population

    assert CONFIANCE_SEUIL_ALERTE == 75

    # Population de référence : six voiles identiques.
    population = [
        ExtractionVoile(
            id_extraction=i, type_voile="Génois", slu_m=12.0, sf_m=3.7, shw_m=2.1, spa_m2=40.0
        )
        for i in range(1, 7)
    ]
    # Candidate conforme : au-dessus du seuil ⇒ verdict positif.
    conforme = evaluer_population(
        ExtractionVoile(id_extraction=99, type_voile="Génois", slu_m=12.0, sf_m=3.7, shw_m=2.1, spa_m2=40.0),
        population,
    )
    assert conforme["verdict"] == "compatible"
    assert conforme["score"] is not None and conforme["score"] >= CONFIANCE_SEUIL_ALERTE

    # Candidate très éloignée : sous le seuil ⇒ « à examiner », jamais « compatible ».
    eloignee = evaluer_population(
        ExtractionVoile(id_extraction=100, type_voile="Génois", slu_m=20.0, sf_m=8.0, shw_m=6.0, spa_m2=95.0),
        population,
    )
    assert eloignee["verdict"] == "a_examiner"
    assert eloignee["score"] is not None and eloignee["score"] < CONFIANCE_SEUIL_ALERTE


# ---------------------------------------------------------------------------
# L'export réel, sur une base ensemencée
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base_echantillon() -> Iterator[tuple[SearchIndex, str]]:
    """Base avec un modèle de bateau et sept génois de mesures connues."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"verdicts_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        administrateur.close()
    url = url_base_jetable(DATABASE_URL, nom_base)
    index = SearchIndex(Path(f"/tmp/unused-verdicts-{nom_base}.db"), url)
    index.initialize()
    index.run_migrations()

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO boat_models (nom_canonique, cle_normalisee) "
                "VALUES ('Bateau étalon', 'bateau_etalon') RETURNING id_modele"
            )
            id_modele = int(cursor.fetchone()[0])
            for i in range(7):
                cursor.execute(
                    # `statut_liaison` est COHÉRENT avec boat_model_id : la
                    # contrainte sail_extractions_liaison_coherente interdit un
                    # modèle renseigné sans liaison déclarée (et réciproquement).
                    "INSERT INTO sail_extractions "
                    "(cle_idempotence, version_extracteur, methode_principale, "
                    " statut_extraction, statut_plausibilite, statut_liaison, "
                    " boat_model_id, type_voile, slu_m, sf_m, shw_m, spa_m2) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        f"etalon-{i}",
                        "1.0",
                        "gabarit",
                        "complete",
                        "plausible",
                        "lie_automatique",
                        id_modele,
                        "Génois",
                        12.0 + 0.01 * i,
                        3.7,
                        2.1,
                        40.0,
                    ),
                )
        connexion.commit()

    try:
        yield index, url
    finally:
        index.close()
        administrateur = psycopg2.connect(DATABASE_URL)
        administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with administrateur.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            administrateur.close()


def test_lechantillon_exporte_ne_divulgue_aucun_score(base_echantillon: tuple[SearchIndex, str]) -> None:
    """Épreuve maîtresse : le fichier donné à l'humain ne porte aucun score."""
    index, _ = base_echantillon
    module = _charger_module()

    lignes, cle = module.construire_echantillon(index, limite=10)
    assert lignes, "le jeu d'essai doit produire au moins une comparaison"

    scores_connus = {
        entree["score"] for entree in cle["entrees"].values() if entree["score"] is not None
    }
    assert scores_connus, "le jeu d'essai doit produire au moins un score"

    for ligne in lignes:
        for champ, valeur in ligne.items():
            if champ == "verdict_humain":
                assert valeur == "", "la colonne à remplir doit partir vide"
                continue
            texte = str(valeur)
            # Aucun score connu ne doit apparaître tel quel dans le CSV.
            for score in scores_connus:
                assert f"{score}" not in texte, (
                    f"le score {score} FUITE dans la colonne {champ} : {texte!r} — "
                    "l'approbateur verrait l'évaluation de la machine et ne jugerait plus rien"
                )
            assert "score" not in texte.lower()
            assert "seuil" not in texte.lower()
            assert "verdict" not in texte.lower()
