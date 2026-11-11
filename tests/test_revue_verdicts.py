"""Lot 10 — verdicts humains explicites : l'assiette du calibrage.

Ces épreuves défendent une idée unique et facile à perdre : **un doute n'est
une preuve dans aucun sens.** Le verdict « probable » doit pouvoir être
consigné — sinon le relecteur n'a nulle part où écrire son hésitation — mais
il ne doit pas nourrir le calcul du seuil, parce que le compter parmi les
justes gonflerait l'exactitude mesurée et le compter parmi les faux la
plomberait.

Deuxième idée défendue ici : un verdict n'écrase pas le précédent. Revenir
sur un jugement est un droit ; l'historique des revirements est une donnée.
"""

from __future__ import annotations

import pytest

from seamtech_search.fiches.calibration import (
    MIN_FICHES_CALIBRATION,
    VERDICT_PREUVE,
    Observation,
    bilan_verdicts,
    famille_du_champ,
    nombre_fiches_verdictees,
    observations_depuis_verdicts,
    paliers_exactitude,
    seuil_recommande,
)
from seamtech_search.schema_metier import (
    MIGRATIONS_METIER,
    SQL_033_VERDICTS_REVUE,
    VERSION_SCHEMA_METIER,
)

# --- Partie pure : aucune base requise -------------------------------------


def test_le_doute_n_est_pas_une_preuve() -> None:
    """« probable » est consigné, jamais compté. C'est le cœur du Lot 10."""
    assert VERDICT_PREUVE == {"certain": True, "rejet": False}
    assert "probable" not in VERDICT_PREUVE, (
        "si « probable » entre dans VERDICT_PREUVE, un doute devient une preuve : "
        "l'exactitude mesurée sera fausse dans un sens ou dans l'autre."
    )


def test_trois_verdicts_et_un_seul_ecarte() -> None:
    """L'énumération de la table accepte les trois, le calcul n'en garde que deux."""
    import re

    alternatives = re.search(r"verdict IN \(([^)]*)\)", SQL_033_VERDICTS_REVUE)
    assert alternatives is not None, "la contrainte CHECK sur `verdict` a disparu"
    admis = {v.strip().strip("'") for v in alternatives.group(1).split(",")}
    assert admis == {"certain", "probable", "rejet"}
    assert admis - set(VERDICT_PREUVE) == {"probable"}, (
        "la table admet exactement un verdict de plus que la preuve : le doute."
    )


def test_un_seuil_reste_toujours_une_confiance_observee() -> None:
    """Un verdict explicite n'autorise pas davantage l'invention qu'un implicite."""
    observations = [
        Observation(confiance=0.82, correct=True),
        Observation(confiance=0.86, correct=True),
        Observation(confiance=0.91, correct=True),
        Observation(confiance=0.91, correct=True),
        Observation(confiance=0.93, correct=True),
    ]
    seuil = seuil_recommande(observations, effectif_min=5)
    assert seuil is not None
    assert seuil in {observation.confiance for observation in observations}, (
        "un seuil inventé (0,87 « parce qu'il tombe bien ») serait une mesure fabriquée ; "
        "le seuil doit toujours être une confiance réellement observée."
    )


def test_un_palier_trop_peu_peuple_ne_donne_pas_d_exactitude() -> None:
    """Trois champs sur trois justes n'est pas 100 % : c'est trois champs."""
    observations = [Observation(confiance=0.99, correct=True) for _ in range(3)]
    paliers = paliers_exactitude(observations, bornes=(0.95,))
    assert paliers[0]["nb"] == 3
    assert paliers[0]["effectif_suffisant"] is False
    assert paliers[0]["exactitude"] is None, (
        "une exactitude calculée sur un effectif insuffisant est une impression chiffrée."
    )


def test_la_migration_033_est_enregistree() -> None:
    """Une migration non enregistrée ferait échouer le job `sauvegarde`."""
    assert VERSION_SCHEMA_METIER == "033_verdicts_revue"
    assert MIGRATIONS_METIER[-1][0] == "033_verdicts_revue"
    # 032 (identité des octets) est une migration de la couche DOCUMENTS, pas
    # du schéma métier : elle vit dans l'indexeur et n'entre pas dans cette
    # liste. Dans MIGRATIONS_METIER, 033 suit donc directement 031.
    assert MIGRATIONS_METIER[-2][0] == "031_accessoires"
    assert "032_identite_octets_documents" not in [version for version, _ in MIGRATIONS_METIER]


def test_la_table_des_verdicts_existe_dans_le_schema() -> None:
    assert "CREATE TABLE IF NOT EXISTS verdict_revue" in SQL_033_VERDICTS_REVUE
    # L'auteur et la date ne sont pas négociables : un seuil non attributable
    # n'est pas opposable.
    assert "acteur" in SQL_033_VERDICTS_REVUE
    assert "decide_le" in SQL_033_VERDICTS_REVUE
    # La confiance est FIGÉE, sinon un recalcul réécrirait l'histoire.
    assert "confiance_moteur" in SQL_033_VERDICTS_REVUE


def test_la_famille_d_un_champ_est_stable() -> None:
    """La calibration regroupe par famille : un champ inconnu ne crée pas une famille."""
    a = famille_du_champ("guindant")
    b = famille_du_champ("guindant")
    assert a == b
    assert isinstance(a, str) and a


# --- Partie PostgreSQL ------------------------------------------------------


def _semer_verdict(
    index: object,
    *,
    id_fiche: int,
    champ: str,
    verdict: str,
    confiance: float,
    acteur: str = "relecteur-de-test",
    motif: str | None = None,
    decalage_secondes: int = 0,
) -> None:
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                """
                INSERT INTO verdict_revue
                    (id_fiche, champ, verdict, confiance_moteur, acteur, motif, decide_le)
                VALUES (%s, %s, %s, %s, %s, %s, now() + (%s * INTERVAL '1 second'))
                """,
                (id_fiche, champ, verdict, confiance, acteur, motif, decalage_secondes),
            )
        connexion.commit()


@pytest.mark.postgres
def test_un_verdict_probable_est_consigne_mais_ne_nourrit_pas_le_calcul(base_recherche: dict) -> None:
    """Le doute apparaît dans le bilan, jamais dans l'assiette du seuil."""
    index = base_recherche["index"]
    id_fiche = next(iter(base_recherche["fiches"].values()))

    _semer_verdict(index, id_fiche=id_fiche, champ="guindant", verdict="certain", confiance=0.90)
    _semer_verdict(index, id_fiche=id_fiche, champ="bordure", verdict="probable", confiance=0.90)

    observations = observations_depuis_verdicts(index)
    champs = {
        famille: [observation for observation in liste]
        for famille, liste in observations.items()
    }
    total = sum(len(liste) for liste in champs.values())
    assert total == 1, (
        "deux verdicts consignés, un seul exploitable : « probable » doit être lu "
        "puis écarté, pas compté."
    )

    # Et pourtant le bilan le montre : cacher le doute reviendrait à faire
    # croire qu'il n'a pas été exprimé.
    bilan = bilan_verdicts(index)
    assert bilan.get("certain") == 1
    assert bilan.get("probable") == 1


@pytest.mark.postgres
def test_revenir_sur_un_jugement_est_un_droit(base_recherche: dict) -> None:
    """Le DERNIER verdict gagne, et l'ancien reste en table."""
    index = base_recherche["index"]
    id_fiche = next(iter(base_recherche["fiches"].values()))

    _semer_verdict(index, id_fiche=id_fiche, champ="guindant", verdict="certain", confiance=0.90, decalage_secondes=-10)
    _semer_verdict(index, id_fiche=id_fiche, champ="guindant", verdict="rejet", confiance=0.90)

    observations = observations_depuis_verdicts(index)
    plats = [observation for liste in observations.values() for observation in liste]
    assert len(plats) == 1, "un seul verdict COURANT par (fiche, champ)"
    assert plats[0].correct is False, "le dernier verdict prononcé est un rejet : il doit gagner"

    # L'historique n'est pas un déchet.
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT COUNT(*) FROM verdict_revue")
            assert int(curseur.fetchone()[0]) == 2


@pytest.mark.postgres
def test_une_fiche_qui_ne_doute_que_ne_compte_pas(base_recherche: dict) -> None:
    """Des doutes seuls ne font pas une assiette de calibration."""
    index = base_recherche["index"]
    id_fiche = next(iter(base_recherche["fiches"].values()))

    _semer_verdict(index, id_fiche=id_fiche, champ="guindant", verdict="probable", confiance=0.90)
    assert nombre_fiches_verdictees(index) == 0

    _semer_verdict(index, id_fiche=id_fiche, champ="bordure", verdict="rejet", confiance=0.90)
    assert nombre_fiches_verdictees(index) == 1


@pytest.mark.postgres
def test_l_assiette_reste_en_deça_du_minimum_sans_verdicts(base_recherche: dict) -> None:
    """Le refus de calibrer n'est pas une panne : c'est la réponse correcte."""
    index = base_recherche["index"]
    assert nombre_fiches_verdictees(index) == 0
    assert nombre_fiches_verdictees(index) < MIN_FICHES_CALIBRATION
