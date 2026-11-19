"""Lot 19, priorité 1 — « ajouter » effaçait l'axe mesuré (perte de données).

Le défaut, constaté sur le corpus réel et documenté au §3.4 du rapport
NO-GO/GO : l'écriture remplace la LIGNE ENTIÈRE de la fiche. Réimporter un
dossier dont l'extraction ne retrouve pas le type de voile écrit donc NULL
dans ``fiche.id_type_voile`` — l'axe mesuré précédemment disparaît, sans
qu'aucune erreur ne soit signalée. Deux fiches en ont été victimes
(« génois », « voile d'avant »), rétablies à la main depuis leur mesure
d'origine.

Arbitrage retenu : FUSIONNER. Une ré-extraction qui n'apporte rien ne doit pas
effacer ce qui était déjà mesuré ; elle complète.

Ces deux épreuves sont la REPRODUCTION. La première échoue avant le correctif
et passe après — c'est la preuve que le défaut était réel et qu'il est fermé.
La seconde existe pour que la fusion ne devienne pas un gel : une valeur
effectivement relue doit toujours remplacer l'ancienne.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.fiches.modeles import FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche
from seamtech_search.indexer import SearchIndex
from tests.helpers_postgres_isole import url_base_jetable

pytestmark = pytest.mark.postgres

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


@pytest.fixture(scope="module")
def base() -> Iterator[SearchIndex]:
    """Base PostgreSQL jetable, migrée, avec les gabarits."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"fusion_axe_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        administrateur.close()
    url_base = url_base_jetable(DATABASE_URL, nom_base)
    index = SearchIndex(Path(f"/tmp/unused-fusion-axe-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    try:
        yield index
    finally:
        index.close()  # rend les connexions du pool avant de supprimer la base
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


def _axe_de(index: SearchIndex, code: str) -> tuple[int | None, str | None]:
    """``(id_type_voile, libellé)`` de la fiche ``code``."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT f.id_type_voile, tv.libelle FROM fiche f "
                "LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile "
                "WHERE f.code = %s",
                (code,),
            )
            ligne = cursor.fetchone()
    return (None, None) if ligne is None else (ligne[0], ligne[1])


def test_le_reimport_sans_axe_nefface_pas_laxe_mesure(base: SearchIndex) -> None:
    """Reproduction : l'axe mesuré survit à un réimport qui n'en lit aucun."""
    code = "LOT19-AXE-1"
    ecrire_fiche(base, FicheExtraite(code=code, type_voile_libelle="Génois", fichier_pdf="a.pdf"))
    id_avant, libelle_avant = _axe_de(base, code)
    assert id_avant is not None, "la première écriture doit mesurer un axe"
    assert libelle_avant == "Génois"

    # Ré-extraction du même dossier : cette fois le gabarit ne lit pas le type.
    ecrire_fiche(base, FicheExtraite(code=code, type_voile_libelle=None, fichier_pdf="a.pdf"))

    id_apres, libelle_apres = _axe_de(base, code)
    assert id_apres is not None, (
        "RÉGRESSION : le réimport sans axe a EFFACÉ l'axe mesuré "
        f"(« {libelle_avant} » → NULL). C'est la perte de données silencieuse du §3.4."
    )
    assert libelle_apres == "Génois"


def test_un_axe_effectivement_relu_est_mis_a_jour(base: SearchIndex) -> None:
    """La fusion ne doit pas CONGELER le champ : une valeur relue remplace l'ancienne."""
    code = "LOT19-AXE-2"
    ecrire_fiche(base, FicheExtraite(code=code, type_voile_libelle="Génois", fichier_pdf="b.pdf"))
    assert _axe_de(base, code)[1] == "Génois"

    ecrire_fiche(base, FicheExtraite(code=code, type_voile_libelle="Trinquette", fichier_pdf="b.pdf"))
    assert _axe_de(base, code)[1] == "Trinquette"
