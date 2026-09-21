"""Grammaire pglast de chaque écriture SQL du Lot B (persistance de la fiche).

Même principe que ``test_postgres_sql_grammar.py`` : un curseur simulé ne
distingue pas un SQL valide d'une invention ; pglast (libpg_query, la
grammaire réelle de PostgreSQL) valide chaque instruction SANS serveur. Les
placeholders ``%s`` deviennent ``NULL`` — on valide la grammaire, pas les
types (les types passent par la suite ``-m postgres`` sur serveur réel).
"""

from __future__ import annotations

import pglast
import pytest

import seamtech_search.fiches.persistance as persistance
import seamtech_search.fiches.routes as routes


@pytest.mark.parametrize(
    "module, nom_constante",
    [(persistance, nom) for nom in dir(persistance) if nom.startswith("_SQL_") or nom.startswith("SQL_")]
    + [(routes, nom) for nom in dir(routes) if nom.startswith("_SQL_") or nom.startswith("SQL_")],
)
def test_instruction_valide_pglast(module: object, nom_constante: str) -> None:
    sql = getattr(module, nom_constante)
    assert isinstance(sql, str) and sql.strip(), f"{nom_constante} vide"
    grammaire = sql.replace("%s", "NULL")
    pglast.parse_sql(grammaire)


def test_toutes_les_ecritures_sont_presentes() -> None:
    """Garde-fou : chaque table enfant du Lot B a bien son INSERT validé."""
    attendus = (
        "_SQL_FICHE_INS",
        "_SQL_COTES_INS",
        "_SQL_MATERIAU_FICHE_INS",
        "_SQL_GALON_INS",
        "_SQL_JONCTION_INS",
        "_SQL_FINITION_INS",
        "_SQL_OPTION_INS",
        "_SQL_RENFORT_INS",
        "_SQL_MESURE_LIBRE_INS",
        "_SQL_CHAMP_INS",
        "_SQL_ANOMALIE_INS",
        "_SQL_GABARIT_TEST_INS",
    )
    for nom in attendus:
        assert hasattr(persistance, nom), f"{nom} manquant"


def test_le_statut_arrivee_est_a_valider_dans_le_code() -> None:
    """RG3 : le statut d'arrivée est écrit en dur comme a_valider — jamais valide."""
    import inspect

    source = inspect.getsource(persistance.ecrire_fiche)
    assert '"a_valider"' in source or "'a_valider'" in source
    assert '"valide"' not in source.replace("'valide'", "") or "conservee_validee" in source
