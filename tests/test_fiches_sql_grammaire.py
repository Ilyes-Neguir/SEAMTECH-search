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


def test_le_statut_arrivee_vient_de_la_regle_et_non_d_un_litteral() -> None:
    """Le statut d'arrivée est DÉCIDÉ, plus jamais écrit en dur.

    Anciennement RG3 : le littéral « a_valider » était posé dans le tuple
    d'insertion, et le test vérifiait sa présence. Depuis l'auto-validation
    au seuil de 90 % (décision du commanditaire du 2026-11-14), le statut
    dépend du score et des anomalies : vérifier un littéral n'aurait plus
    de sens — et en laisser un revenir serait réintroduire une règle
    concurrente, silencieuse.

    L'invariant qui reste : aucun statut d'arrivée n'est écrit directement,
    et la promotion à « valide » ne passe QUE par la règle nommée.
    """
    import inspect

    # ecrire_fiche est une enveloppe fine (lot C : connexion externe) — la
    # règle vit dans le corps réel d'écriture, _ecrire_fiche_dans.
    source = inspect.getsource(persistance._ecrire_fiche_dans)

    # 1. La règle est bien appelée, et une seule fois.
    assert source.count("decider_statut_arrivee(") >= 1, (
        "le statut d'arrivée n'est plus décidé par `decider_statut_arrivee` : "
        "une écriture directe réintroduirait une règle non tracée"
    )

    # 2. Dans le tuple d'insertion de la fiche, le statut vient de la
    #    CONSTANTE NOMMÉE — pas d'un littéral.
    #    (On ne vérifie pas le fichier entier : « "valide" » y figure
    #    légitimement dans le garde-fou RG11, qui LIT un statut, et dans
    #    l'action « conservee_validee ». Seule l'ÉCRITURE du statut
    #    d'arrivée doit être passée par la règle.)
    debut = source.index("parametres_fiche = (")
    fin = source.index(")", source.index("fiche.score_qualite()", debut))
    insertion = source[debut:fin]
    assert "STATUT_ATTENTE_HUMAINE" in insertion, (
        "le statut d'arrivée inséré n'est pas la constante nommée"
    )
    for litteral in ('"a_valider"', "'a_valider'", '"valide"', "'valide'"):
        assert litteral not in insertion, (
            f"statut « {litteral} » écrit en dur dans l'insertion de la fiche"
        )

    # 3. La promotion n'existe qu'au travers de l'instruction prévue, qui
    #    vérifie que la fiche est encore en attente avant de la promouvoir.
    assert "_SQL_PROMOUVOIR_AUTO" in source
    assert "STATUT_ATTENTE_HUMAINE" in source


def test_la_promotion_refuse_de_s_appliquer_a_une_fiche_deja_validee() -> None:
    """L'instruction de promotion est conditionnée au statut d'attente.

    Sans ce `AND statut = ...`, une ré-écriture promouvrait une fiche déjà
    validée par un humain — et RG11 serait contourné par la porte de
    service.
    """
    from seamtech_search.fiches.persistance import _SQL_PROMOUVOIR_AUTO

    assert "AND statut = %s" in _SQL_PROMOUVOIR_AUTO, (
        "la promotion doit vérifier le statut courant, sinon elle écrase "
        "une décision humaine"
    )
