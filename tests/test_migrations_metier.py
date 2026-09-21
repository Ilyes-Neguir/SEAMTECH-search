"""Lot A — migrations 006-009 contre un vrai PostgreSQL.

Ces tests exigent un serveur réel avec pgvector (image ``pgvector/pgvector:pg16``
en CI, ``postgresql-XX-pgvector`` en local) : les curseurs simulés ne
distinguent pas du SQL valide d'une invention, et les capacités (``::vector``,
``pg_trgm``, ``seamtech_unaccent``) ne se prouvent que sur le serveur.
Ils s'auto-ignorent sans ``SEAMTECH_TEST_DATABASE_URL``, comme le reste de la
suite ``-m postgres``.

Chaque test crée sa PROPRE base jetable (suffixe unique) puis la supprime :
les migrations tournent toujours sur une base réellement vide, et aucune
exécution ne dépend d'une exécution précédente sur la base partagée.

Schéma métier : PostgreSQL UNIQUEMENT (décision §17.1 du plan v3.0).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search import schema_metier
from seamtech_search.indexer import SearchIndex

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
TABLES_HERITEES = {"documents", "scan_runs", "imports", "import_jobs", "audit_log", "schema_migrations"}


@pytest.fixture()
def base_metier() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable, migrée ; rend {index, url, nom, duree_migrations_s}."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"metier_test_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom_base)))
    finally:
        administrateur.close()

    partie = DATABASE_URL.rsplit("/", 1)
    url_base = f"{partie[0]}/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    debut = time.perf_counter()
    index.initialize()
    index.run_migrations()
    duree = time.perf_counter() - debut

    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "duree_migrations_s": duree}
    finally:
        index.close()
        nettoie = psycopg2.connect(DATABASE_URL)
        nettoie.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with nettoie.cursor() as cursor:
                cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom_base)))
        finally:
            nettoie.close()


@pytest.mark.postgres
def test_migrations_sur_base_vide(base_metier: dict[str, Any]) -> None:
    """Base vide → migrations → toutes les tables métier, zéro erreur."""
    index: SearchIndex = base_metier["index"]
    with index.connect() as connection:
        with connection.cursor() as cursor:
            manquantes = []
            for table in schema_metier.TABLES_METIER:
                cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cursor.fetchone()[0] is None:
                    manquantes.append(table)
    assert manquantes == [], f"tables manquantes après migrations : {manquantes}"
    # 21 tables en 006 (6 référentiels + gabarit, fiche et 13 tables filles, chunk)
    # + 2 (007) + 3 (008) + 1 (009) = 27, + 3 (010 : lot_import, lot_dossier,
    # fiche_piece_jointe) = 30.
    assert len(schema_metier.TABLES_METIER) == 30
    print(f"\n[mesure] migrations 006-009 sur base vide : {base_metier['duree_migrations_s']:.2f} s")


@pytest.mark.postgres
def test_idempotence_rejeu_sans_erreur(base_metier: dict[str, Any]) -> None:
    """Rejouer run_migrations ne fait rien, ne lève pas, ne duplique pas."""
    index: SearchIndex = base_metier["index"]

    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
            avant = [row[0] for row in cursor.fetchall()]
    assert {"006_fiche_technique", "007_recherche_index", "008_ml_corpus", "009_qualite_et_gabarits"} <= set(avant)

    index.run_migrations()  # ne doit ni lever, ni dupliquer

    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
            apres = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public'")
            nb_tables = int(cursor.fetchone()[0])
    assert apres == avant
    # pg_tables ne compte pas les vues (v_fiche_recherche, v_qualite).
    assert nb_tables == len(schema_metier.TABLES_METIER) + len(TABLES_HERITEES) == 36


@pytest.mark.postgres
def test_capacites_reellement_disponibles(base_metier: dict[str, Any]) -> None:
    """vector, pg_trgm et seamtech_unaccent fonctionnent après migrations."""
    index: SearchIndex = base_metier["index"]
    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT '[1,2,3]'::vector")
            assert cursor.fetchone()[0] == "[1,2,3]"

            cursor.execute("SELECT similarity('monofim', 'monofilm')")
            assert float(cursor.fetchone()[0]) > 0.0, "pg_trgm non opérationnel"

            cursor.execute(
                "SELECT to_tsvector('seamtech_unaccent', 'lattée') @@ to_tsquery('seamtech_unaccent', 'lattee')"
            )
            assert cursor.fetchone()[0] is True, "l'existant (seamtech_unaccent) est cassé"


@pytest.mark.postgres
def test_insertion_fiche_vue_recherche_et_rafraichissement(base_metier: dict[str, Any]) -> None:
    """Fiche minimale : v_fiche_recherche la lit et la fonction 007 remplit la recherche."""
    index: SearchIndex = base_metier["index"]
    code = f"TEST-{uuid.uuid4().hex[:8]}"

    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO client (nom, chantier) VALUES ('Sailonet', 'Cruette') RETURNING id_client")
            id_client = cursor.fetchone()[0]
            cursor.execute("INSERT INTO bateau (nom, taille) VALUES ('29er', '15''') RETURNING id_bateau")
            id_bateau = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO type_voile (code, libelle, famille, sous_type) VALUES (%s, %s, 'spi', 'asymetrique') RETURNING id_type_voile",
                (f"SPI_ASYM_{code}", "Spi Asymétrique"),
            )
            id_type = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO fiche (code, titre, id_type_voile, id_client, id_bateau, notes)
                   VALUES (%s, %s, %s, %s, %s, 'voile légère pour vent fort') RETURNING id_fiche""",
                (code, "Fiche de fabrication test", id_type, id_client, id_bateau),
            )
            id_fiche = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m, sle_m, sf_m, spa_m2) VALUES (%s, 'finie', 6.60, 5.50, 3.08, 15.71)",
                (id_fiche,),
            )
            cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))

            cursor.execute("SELECT champs_texte, search_vector FROM fiche WHERE id_fiche = %s", (id_fiche,))
            champs_texte, vecteur = cursor.fetchone()
            assert "Sailonet" in champs_texte and "29er" in champs_texte
            assert vecteur is not None and str(vecteur) != ""

            cursor.execute("SELECT code, client, bateau, slu_m FROM v_fiche_recherche WHERE id_fiche = %s", (id_fiche,))
            ligne = cursor.fetchone()
            assert ligne[0] == code and ligne[1] == "Sailonet" and "29er" in ligne[2]
            assert float(ligne[3]) == 6.60

            cursor.execute("SELECT passage_direct, nb_champs_corriges FROM v_qualite WHERE id_fiche = %s", (id_fiche,))
            passage_direct, nb_corriges = cursor.fetchone()
            assert passage_direct is True and nb_corriges == 0

            cursor.execute("DELETE FROM fiche WHERE id_fiche = %s", (id_fiche,))
            cursor.execute("DELETE FROM bateau WHERE id_bateau = %s", (id_bateau,))
            cursor.execute("DELETE FROM type_voile WHERE id_type_voile = %s", (id_type,))
            cursor.execute("DELETE FROM client WHERE id_client = %s", (id_client,))


@pytest.mark.postgres
def test_health_expose_schema_et_extensions(base_metier: dict[str, Any]) -> None:
    """/health (health_details) indique la version de schéma et la présence des extensions."""
    index: SearchIndex = base_metier["index"]
    details = index.health_details()

    assert details["backend"] == "postgresql"
    migrations = details["schema_migrations"]
    assert {"006_fiche_technique", "007_recherche_index", "008_ml_corpus", "009_qualite_et_gabarits"} <= set(migrations)
    assert details["schema_metier_a_jour"] is True
    assert details["extensions"] == {"vector": True, "pg_trgm": True, "unaccent": True, "applicables": True}


@pytest.mark.postgres
def test_mesure_taille_schema(base_metier: dict[str, Any]) -> None:
    """Taille du schéma métier créé (mesure de PR), tables vides, index inclus."""
    index: SearchIndex = base_metier["index"]
    tailles: dict[str, int] = {}
    with index.connect() as connection:
        with connection.cursor() as cursor:
            for table in schema_metier.TABLES_METIER:
                cursor.execute("SELECT COALESCE(pg_total_relation_size(%s), 0)", (f"public.{table}",))
                tailles[table] = int(cursor.fetchone()[0])
    total = sum(tailles.values())
    print(f"\n[mesure] schéma métier (tables vides, index inclus) : {total / 1024:.0f} ko au total")
    assert total > 0


def test_sqlite_ne_recoit_pas_la_couche_metier(tmp_path: Path) -> None:
    """Sur SQLite, les migrations métier ne font rien et le diagnostic le dit.

    Sans marqueur ``postgres`` : c'est un test de DÉCISION (§17.1) qui doit
    tourner partout, y compris dans la suite SQLite de la CI.
    """
    index = SearchIndex(tmp_path / "sqlite.db")
    index.initialize()
    index.run_migrations()  # 006-009 : warnings journalisés, aucun effet

    with index.connect() as connection:
        noms = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "fiche" not in noms and "chunk" not in noms
    details = index.health_details()
    assert details["schema_metier_a_jour"] is False
    assert details["extensions"]["applicables"] is False

@pytest.mark.postgres
def test_demarrage_application_sur_base_metier(base_metier: dict[str, Any]) -> None:
    """L'application démarre réellement sur une base métier : /ready et /health répondent."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    index: SearchIndex = base_metier["index"]
    config = AppConfig(root_paths=[Path("/tmp")], database_url=base_metier["url"], min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        pret = client.get("/ready")
        assert pret.status_code == 200
        sante = client.get("/health")
        assert sante.status_code == 200
        corps = sante.json()
        assert corps["backend"] == "postgresql"
        assert {"006_fiche_technique", "007_recherche_index", "008_ml_corpus", "009_qualite_et_gabarits"} <= set(
            corps["schema_migrations"]
        )
        assert corps["extensions"] == {"vector": True, "pg_trgm": True, "unaccent": True, "applicables": True}
        _ = index  # la base jetable est nettoyée par le fixture

# ---------------------------------------------------------------------------
# Constat 1 de revue — privilèges PostgreSQL.
# Sur ce serveur de référence : `vector` n'est PAS une extension « trusted »
# (refusée à un rôle non superutilisateur), `pg_trgm` et `unaccent` le sont.
# Les deux tests live prouvent les deux branches attendues par la revue :
# (a) sans privilège ni préinstallation → échec AVEC message actionnable ;
# (b) vector préinstallé par l'administrateur → les migrations passent, et la
#     configuration de recherche se résout dynamiquement (`seamtech_unaccent`
#     si le rôle peut l'installer, sinon dégradation `simple` — même choix de
#     conception que la migration 005) au lieu de faire échouer le démarrage.
# ---------------------------------------------------------------------------

ROLE_LIMITE = "seamtech_limite"
MOT_DE_PASSE_LIMITE = "limite_test"


def _role_limite_existe(url_admin: str) -> None:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(url_admin)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE_LIMITE,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER").format(sql.Identifier(ROLE_LIMITE)),
                    (MOT_DE_PASSE_LIMITE,),
                )
    finally:
        admin.close()


def _base_limite(url_admin: str, revoke_create: bool) -> str:
    """Base possédée par le rôle limité ; révoque éventuellement CREATE (base)."""
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"limite_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url_admin)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(nom_base), sql.Identifier(ROLE_LIMITE)
                )
            )
            if revoke_create:
                cursor.execute(
                    sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
                        sql.Identifier(nom_base), sql.Identifier(ROLE_LIMITE)
                    )
                )
    finally:
        admin.close()
    return nom_base


def _installer_extension(url_admin: str, nom_base: str, extension: str) -> None:
    """(Super)administrateur : préinstalle une extension DANS la base jetable."""
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(f"{url_admin.rsplit('/', 1)[0]}/{nom_base}")
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))
    finally:
        admin.close()


def _detruire_base(url_admin: str, nom_base: str) -> None:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(url_admin)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom_base)))
    finally:
        admin.close()


def _url_limite(url_admin: str, nom_base: str) -> str:
    hote = url_admin.split("@", 1)[1].split("/", 1)[0]
    return f"postgresql://{ROLE_LIMITE}:{MOT_DE_PASSE_LIMITE}@{hote}/{nom_base}"


@pytest.mark.postgres
def test_role_sans_privilege_echec_actionnable() -> None:
    """(a) Rôle sans privilège, vector non préinstallé : échec avec l'action exacte."""
    url_admin = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
    if not url_admin:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    _role_limite_existe(url_admin)
    nom_base = _base_limite(url_admin, revoke_create=False)
    try:
        index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), _url_limite(url_admin, nom_base))
        index.initialize()
        with pytest.raises(RuntimeError) as attrape:
            index.run_migrations()
        message = str(attrape.value)
        assert "CREATE EXTENSION vector" in message
        assert "pgvector/pgvector:pg16" in message, "le message doit nommer l'image à utiliser"
        assert "administrateur" in message
    finally:
        _detruire_base(url_admin, nom_base)


@pytest.mark.postgres
def test_role_limite_migrations_passent_si_vector_preinstalle() -> None:
    """(b) vector préinstallé : les migrations passent pour un rôle limité, et
    chunk.tsv utilise la configuration RÉSOLUE (simple en repli) au lieu de
    faire échouer le démarrage — le bug exact du constat 1."""
    url_admin = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
    if not url_admin:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    _role_limite_existe(url_admin)
    nom_base = _base_limite(url_admin, revoke_create=True)
    _installer_extension(url_admin, nom_base, "vector")
    try:
        index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), _url_limite(url_admin, nom_base))
        index.initialize()
        index.run_migrations()  # ne doit PAS lever (c'est le bug du constat 1)

        with index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT generation_expression FROM information_schema.columns "
                    "WHERE table_name = 'chunk' AND column_name = 'tsv'"
                )
                expression = cursor.fetchone()[0]
                cursor.execute("SELECT to_regclass('public.fiche') IS NOT NULL")
                assert cursor.fetchone()[0] is True
        config_utilisee = "seamtech_unaccent" if "seamtech_unaccent" in expression else "simple"
        print(f"\n[constat 1] migrations passées avec rôle limité ; chunk.tsv utilise {config_utilisee!r}")
        assert config_utilisee in ("seamtech_unaccent", "simple")
    finally:
        _detruire_base(url_admin, nom_base)


def test_injection_configuration_simple_valide_pglast() -> None:
    """Unitaire (sans serveur) : la variante dégradée `simple` est du SQL valide.

    Filet anti-régression : si quelqu'un recode la configuration en dur dans le
    SQL, ce test et le marqueur le font échouer à la grammaire ou au marqueur.
    """
    import pglast

    script = schema_metier.SQL_006_FICHE_TECHNIQUE.replace(schema_metier.MARQUEUR_TS_CONFIG, "simple")
    assert "to_tsvector('simple'" in script
    pglast.parse_sql(script)
    fonction = schema_metier.SQL_007_RECHERCHE_INDEX.replace(schema_metier.MARQUEUR_TS_CONFIG, "simple")
    pglast.parse_sql(fonction)
    pglast.parse_sql(
        schema_metier.SQL_006_FICHE_TECHNIQUE.replace(schema_metier.MARQUEUR_TS_CONFIG, "seamtech_unaccent")
    )
