"""Lot 19 — le détecteur d'axes effacés (``scripts/detecter_axes_effaces.py``).

L'enjeu de ces épreuves n'est pas le comptage, c'est la DISCRIMINATION. Une
fiche qui n'a jamais eu d'axe n'est pas une victime : la signaler noierait les
deux vraies victimes sous une centaine de faux positifs (le corpus porte 109
fiches sans axe). Le détecteur ne doit retenir que les fiches sur lesquelles le
mécanisme d'effacement a réellement tourné, c'est-à-dire ``revision > 1``.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.fiches.modeles import FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche
from seamtech_search.indexer import SearchIndex
from tests.helpers_postgres_isole import url_base_jetable

pytestmark = pytest.mark.postgres

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

RACINE = Path(__file__).resolve().parent.parent


def _charger_module() -> Any:
    """Charge le script (ce n'est pas un module importable : tiret ni paquet)."""
    chemin = RACINE / "scripts" / "detecter_axes_effaces.py"
    spec = importlib.util.spec_from_file_location("detecter_axes_effaces", chemin)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def base() -> Iterator[tuple[SearchIndex, str]]:
    """Base jetable : une fiche saine, une jamais mesurée, une effacée."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"detection_axe_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        administrateur.close()
    url = url_base_jetable(DATABASE_URL, nom_base)
    index = SearchIndex(Path(f"/tmp/unused-detection-{nom_base}.db"), url)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)

    # Saine : un axe mesuré, une seule écriture.
    ecrire_fiche(index, FicheExtraite(code="SAINE-1", type_voile_libelle="Trinquette", titre="Voile saine"))
    # Jamais mesurée : aucun axe, jamais réécrite. CE N'EST PAS UNE VICTIME.
    ecrire_fiche(index, FicheExtraite(code="JAMAIS-1", type_voile_libelle=None, titre="Jamais d'axe"))
    # Victime : axe mesuré, puis arraché comme le faisait l'écriture d'avant.
    ecrire_fiche(
        index,
        FicheExtraite(code="VICTIME-1", type_voile_libelle="Génois", titre="Génois 2024 sur enrouleur"),
    )
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE fiche SET id_type_voile = NULL, revision = revision + 1 WHERE code = 'VICTIME-1'"
            )

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


def test_une_fiche_jamais_mesuree_nest_pas_signalee(base: tuple[SearchIndex, str]) -> None:
    """Le faux positif à éviter : pas d'axe depuis toujours ≠ axe effacé."""
    _, url = base
    module = _charger_module()
    rapport = module.analyser(url)

    codes_exposes = {entree["code"] for entree in rapport["exposees"]}
    assert "JAMAIS-1" not in codes_exposes, (
        "une fiche jamais réécrite n'a pas pu perdre son axe : la signaler "
        "noierait les vraies victimes sous les 109 fiches sans axe du corpus"
    )


def test_la_victime_est_detectee_et_qualifiee(base: tuple[SearchIndex, str]) -> None:
    """L'axe arraché est vu, et le texte qui le nomme en fait un candidat fort."""
    _, url = base
    module = _charger_module()
    rapport = module.analyser(url)

    codes_exposes = {entree["code"] for entree in rapport["exposees"]}
    assert "VICTIME-1" in codes_exposes
    # La fiche saine n'est pas concernée : elle a toujours son axe.
    assert "SAINE-1" not in codes_exposes

    candidates = {entree["code"]: entree["libelle_mentionne"] for entree in rapport["candidates"]}
    assert "VICTIME-1" in candidates
    assert candidates["VICTIME-1"] == "Génois"


def test_le_script_rend_un_code_exploitable(base: tuple[SearchIndex, str]) -> None:
    """0 = rien à traiter, 1 = des fiches exposées, 2 = entrée invalide."""
    _, url = base
    module = _charger_module()

    import io
    from contextlib import redirect_stderr, redirect_stdout

    # Sans URL : refus de deviner, code 2.
    url_initiale = os.environ.pop("SEAMTECH_DATABASE_URL", None)
    try:
        sortie = io.StringIO()
        with redirect_stdout(sortie), redirect_stderr(sortie):
            assert module.main([]) == 2
    finally:
        if url_initiale is not None:
            os.environ["SEAMTECH_DATABASE_URL"] = url_initiale

    # Avec la base du jeu d'essai : des fiches exposées, code 1.
    os.environ["SEAMTECH_DATABASE_URL"] = url
    try:
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            assert module.main([]) == 1
        assert "VICTIME-1" in sortie.getvalue()
    finally:
        if url_initiale is not None:
            os.environ["SEAMTECH_DATABASE_URL"] = url_initiale
        else:
            os.environ.pop("SEAMTECH_DATABASE_URL", None)


def test_une_base_vierge_ne_signale_rien(base: tuple[SearchIndex, str]) -> None:
    """Aucune fiche exposée ⇒ code 0. Le vrai négatif doit être silencieux."""
    index, url = base
    module = _charger_module()

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("UPDATE fiche SET revision = 1 WHERE id_type_voile IS NULL")

    rapport = module.analyser(url)
    assert rapport["exposees"] == []
    assert rapport["candidates"] == []
    # … mais les fiches sans axe, elles, sont toujours comptées : c'est un fait,
    # pas une alerte.
    assert rapport["sans_axe"] >= 2
