"""Passe finale — repli de recherche plein-texte quand `unaccent` est refusé.

L'indexeur documente une dégradation gracieuse : sans privilège pour
``CREATE EXTENSION unaccent``, la recherche retombe sur la configuration
``simple`` (parité d'accents perdue, application debout). Cette promesse n'est
tenue que si TOUS les écrivains de ``tsvector`` emploient la configuration
RÉSOLUE, et si le schéma métier (colonne générée ``chunk.tsv``, fonction
PL/pgSQL ``rafraichir_texte_recherche_fiche``) est créé avec elle.

Le dépôt de fiches écrivait ``to_tsvector('seamtech_unaccent', …)`` en dur :
sur une base sans ``unaccent``, chaque dépôt échouait en ``UndefinedObject``.
Constaté le 29/10/2026 sur un PostgreSQL 16.2 réellement dépourvu de contrib :
74 tests en échec / 32 erreurs, toutes en
``text search configuration "seamtech_unaccent" does not exist``.

La simulation est FIDÈLE au seul point qui compte — ``CREATE EXTENSION
unaccent`` échoue — et laisse tout le reste réel : vraie base PostgreSQL,
vraies migrations, vrai dépôt de dossier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from seamtech_search.fiches import depot
from seamtech_search.indexer import PG_FALLBACK_CONFIG, PG_UNACCENT_CONFIG, SearchIndex
from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

pytestmark = pytest.mark.postgres


@pytest.fixture()
def base_sans_unaccent() -> Any:  # noqa: ANN401
    """Base neuve migrée dans les conditions d'un `unaccent` indisponible."""
    nom, url = _creer_base_jetable()
    with mock.patch.object(
        SearchIndex,
        "_ensure_unaccent_config",
        lambda self, connexion: False,  # noqa: ARG005 - signature respectée
    ):
        index = SearchIndex(Path("/tmp/passe-finale-unused.db"), url)
        index.initialize()
        index.run_migrations()
    try:
        yield {"index": index, "url": url}
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_la_configuration_resolue_retombe_sur_simple(base_sans_unaccent: dict[str, Any]) -> None:
    index = base_sans_unaccent["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT count(*) FROM pg_ts_config WHERE cfgname = %s", (PG_UNACCENT_CONFIG,))
        assert int(curseur.fetchone()[0]) == 0, "le scénario exige l'absence de la configuration"
        assert index.config_recherche_texte(connexion) == PG_FALLBACK_CONFIG
        # Le cache est posé : la valeur ne dépend pas de l'ordre des appels.
        assert index.config_recherche_texte(connexion) == PG_FALLBACK_CONFIG


def test_le_schema_metier_est_cree_avec_la_configuration_de_repli(
    base_sans_unaccent: dict[str, Any],
) -> None:
    """La colonne générée et la fonction PL/pgSQL suivent, elles aussi, le repli."""
    index = base_sans_unaccent["index"]
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "SELECT pg_get_expr(adbin, adrelid) FROM pg_attrdef "
            "WHERE adrelid = 'chunk'::regclass AND adnum = "
            "(SELECT attnum FROM pg_attribute WHERE attrelid = 'chunk'::regclass AND attname = 'tsv')"
        )
        expression = str(curseur.fetchone()[0])
        assert PG_UNACCENT_CONFIG not in expression, expression
        curseur.execute("SELECT prosrc FROM pg_proc WHERE proname = 'rafraichir_texte_recherche_fiche'")
        ligne = curseur.fetchone()
        assert ligne is not None, "la fonction de rafraîchissement doit exister"
        source = str(ligne[0])
        assert PG_UNACCENT_CONFIG not in source, source[:200]


def test_le_depot_n_ecrit_plus_une_configuration_en_dur() -> None:
    """Contrat structurel : la configuration est un PARAMÈTRE, pas une constante."""
    assert "to_tsvector(%s::regconfig" in depot._SQL_DOCUMENT_VECTOR
    assert PG_UNACCENT_CONFIG not in depot._SQL_DOCUMENT_VECTOR


def test_un_depot_reel_traverse_le_repli(base_sans_unaccent: dict[str, Any], tmp_path: Path) -> None:
    """Dépôt de bout en bout : la configuration absente ne casse plus rien.

    Sans le correctif, ce dépôt levait ``UndefinedObject`` au moment d'écrire
    le vecteur du PDF de fiche, et la fiche était perdue.
    """
    from reportlab.pdfgen import canvas

    from seamtech_search.fiches.gabarits import initialiser_gabarits

    index = base_sans_unaccent["index"]
    initialiser_gabarits(index)
    dossier = tmp_path / "REF-REPLI"
    dossier.mkdir(parents=True)
    pdf = dossier / "a-technical.pdf"
    toile = canvas.Canvas(str(pdf))
    y = 750
    for ligne in (
        "FICHE DE FABRICATION",
        "Voile de portant",
        "Client : ATELIER DU VENT",
        "Référence : GV-REPLI-1",
        "Tissu : DACRON AZUR 380",
        "Support : Bavaria 45",
        "Guindant (SLU) 12.50 m",
        "Chute (SLE) 11.95 m",
        "Bordure (SF) 4.80 m",
        "Surface (SPA) 55.00 m2",
    ):
        toile.drawString(50, y, ligne)
        y -= 20
    toile.save()

    resultat = depot.deposer_dossier(index, dossier)
    assert resultat["statut"] == "traite", resultat
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT count(*) FROM documents WHERE search_vector IS NOT NULL")
        assert int(curseur.fetchone()[0]) >= 1, "le vecteur doit être écrit malgré le repli"
        # La recherche pondérée de la fiche a été rafraîchie par la fonction PL/pgSQL.
        curseur.execute("SELECT champs_texte FROM fiche WHERE code = %s", ("GV-REPLI-1",))
        ligne = curseur.fetchone()
        assert ligne is not None and ligne[0], "le texte de recherche de la fiche doit être rempli"
