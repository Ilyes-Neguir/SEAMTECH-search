"""Qualification A étendue : chemins HTTP réels, données pré-023, imports, volume.

N'est PAS une clôture atelier. Corpus SYNTHÉTIQUE (pas le fonds réel).
"""

from __future__ import annotations

import os
import statistics
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.recherche import rechercher_fiches

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.postgres

N_VOLUME = 800
FAMILLES_TITRES = (
    "Génois lourd n°{n}",
    "Façade atelier n°{n}",
    "Réparation voile n°{n}",
    "Contrôle été n°{n}",
    "Cœur de spi n°{n}",
    "Câble d'écoute n°{n}",
    "Client Müller n°{n}",
    "Navire São n°{n}",
)
REQUETES_EQUIV = (
    ("GENOIS", "GÉNOIS", "génois"),
    ("FACADE", "FAÇADE", "façade"),
    ("REPARATION", "RÉPARATION", "réparation"),
    ("ETE", "ÉTÉ", "été"),
    ("COEUR", "CŒUR", "cœur"),
)


def _url_base(nom: str) -> str:
    principale, sep, requete = DATABASE_URL.partition("?")
    return principale.rsplit("/", 1)[0] + "/" + nom + (f"?{requete}" if sep else "")


def _creer_index() -> tuple[str, SearchIndex]:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom = f"qvol_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(DATABASE_URL)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()
    index = SearchIndex(Path(f"/tmp/unused-{nom}.db"), _url_base(nom))
    index.initialize()
    return nom, index


def _detruire(nom: str, index: SearchIndex) -> None:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    index.close()
    admin = psycopg2.connect(DATABASE_URL)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
    admin.close()


def _appliquer_jusqua_022(index: SearchIndex) -> None:
    etapes = [
        ("001_initial", index._migration_001_initial),
        ("002_object_storage_columns", index._migration_002_object_storage_columns),
        ("003_category_backfill_guard", index._migration_003_category_backfill_guard),
        ("004_uploaded_at_epoch", index._migration_004_uploaded_at_epoch),
        ("005_unaccent_search_vector", index._migration_005_unaccent_search_vector),
        ("006_fiche_technique", index._migration_006_fiche_technique),
        ("007_recherche_index", index._migration_007_recherche_index),
        ("008_ml_corpus", index._migration_008_ml_corpus),
        ("009_qualite_et_gabarits", index._migration_009_qualite_et_gabarits),
        ("010_lots_ingestion", index._migration_010_lots_ingestion),
        ("011_pieces_catalogue_documents", index._migration_011_pieces_catalogue_documents),
        ("012_recherche_hybride", index._migration_012_recherche_hybride),
        ("013_recherche_fonds_reel", index._migration_013_recherche_fonds_reel),
        ("014_facette_dimension", index._migration_014_facette_dimension),
        ("015_qualite_gabarit_brouillon", index._migration_015_qualite_gabarit_brouillon),
        ("016_dedup_comptes_nominatifs", index._migration_016_dedup_comptes_nominatifs),
        ("017_ocr_etage3", index._migration_017_ocr_etage3),
        ("018_recherche_a_valider", index._migration_018_recherche_a_valider),
        ("019_recherche_dimension", index._migration_019_recherche_dimension),
        ("020_file_durable", index._migration_020_file_durable),
        ("021_revision_fiche", index._migration_021_revision_fiche),
        ("022_selections_durables", index._migration_022_selections_durables),
    ]
    with index.connect() as connexion:
        index._ensure_migrations_table(connexion)
        deja = index._get_applied_migrations(connexion)
        for version, fonction in etapes:
            if version not in deja:
                fonction(connexion)
                index._record_migration(connexion, version)


def _semer_referentiels(cursor: Any) -> None:
    cursor.execute("INSERT INTO type_voile (code, libelle, famille) VALUES ('GEN', 'Génois', 'voile_avant')")
    cursor.execute("INSERT INTO client (nom, chantier) VALUES ('Voilerie Atlantique', 'Brest')")
    cursor.execute("INSERT INTO bateau (nom, taille) VALUES ('First 30', '30 pieds')")


def _codes(reponse: dict[str, Any]) -> list[str]:
    return [r["code"] for r in reponse.get("resultats") or []]


@pytest.fixture()
def base_volume() -> Iterator[dict[str, Any]]:
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    nom, index = _creer_index()
    index.run_migrations()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _semer_referentiels(cursor)
            cursor.execute("SELECT id_type_voile FROM type_voile LIMIT 1")
            id_type = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_client FROM client LIMIT 1")
            id_client = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_bateau FROM bateau LIMIT 1")
            id_bateau = int(cursor.fetchone()[0])
            for n in range(N_VOLUME):
                titre = FAMILLES_TITRES[n % len(FAMILLES_TITRES)].format(n=n)
                code = f"VOL-{n:04d}"
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, id_client, id_bateau, statut) "
                    "VALUES (%s, %s, %s, %s, %s, 'valide') RETURNING id_fiche",
                    (code, titre, id_type, id_client, id_bateau),
                )
                ident = int(cursor.fetchone()[0])
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (ident,))
    try:
        yield {"index": index, "url": _url_base(nom), "nom": nom}
    finally:
        _detruire(nom, index)


def test_donnees_existantes_avant_023_deviennent_paritaires() -> None:
    """Fiches écrites sous 022, puis migration 023, sans réécrire les titres."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    nom, index = _creer_index()
    try:
        _appliquer_jusqua_022(index)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_proc WHERE proname = 'seamtech_texte_recherche'"
                )
                assert cursor.fetchone() is None
                _semer_referentiels(cursor)
                cursor.execute("SELECT id_type_voile FROM type_voile LIMIT 1")
                id_type = int(cursor.fetchone()[0])
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, statut) "
                    "VALUES ('PRE-023', 'Génois existant', %s, 'valide') RETURNING id_fiche",
                    (id_type,),
                )
                ident = int(cursor.fetchone()[0])
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (ident,))
                cursor.execute("SELECT titre FROM fiche WHERE id_fiche = %s", (ident,))
                titre_avant = cursor.fetchone()[0]
        assert titre_avant == "Génois existant"

        with index.connect() as connexion:
            index._migration_023_recherche_normalisee(connexion)
            index._record_migration(connexion, "023_recherche_normalisee")
            with connexion.cursor() as cursor:
                cursor.execute("SELECT titre FROM fiche WHERE code = 'PRE-023'")
                assert cursor.fetchone()[0] == "Génois existant"
                cursor.execute(
                    "SELECT 1 FROM pg_proc WHERE proname = 'seamtech_texte_recherche'"
                )
                assert cursor.fetchone() is not None

        a = _codes(rechercher_fiches(index, requete="GENOIS"))
        b = _codes(rechercher_fiches(index, requete="GÉNOIS"))
        assert "PRE-023" in a
        assert a == b
    finally:
        _detruire(nom, index)


def test_nouvel_import_apres_023_est_paritaire() -> None:
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    nom, index = _creer_index()
    try:
        index.run_migrations()
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                _semer_referentiels(cursor)
                cursor.execute("SELECT id_type_voile FROM type_voile LIMIT 1")
                id_type = int(cursor.fetchone()[0])
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, statut) "
                    "VALUES ('POST-023', 'Cœur neuf', %s, 'valide') RETURNING id_fiche",
                    (id_type,),
                )
                ident = int(cursor.fetchone()[0])
                cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (ident,))
        a = _codes(rechercher_fiches(index, requete="coeur"))
        b = _codes(rechercher_fiches(index, requete="cœur"))
        assert "POST-023" in a
        assert a == b
    finally:
        _detruire(nom, index)


def test_http_recherche_et_search_memes_identifiants(base_volume: dict[str, Any]) -> None:
    """GET /recherche (fiches) et GET /search (fichiers) — chemins applicatifs."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    index: SearchIndex = base_volume["index"]
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, "
                "modified_at, is_dir, content, search_vector) "
                "VALUES ('k-genois', '/archive/Génois-plan.pdf', 'Génois-plan.pdf', '/archive', "
                "'.pdf', 10, 1, false, 'plan de Génois lourd', "
                "to_tsvector('seamtech_unaccent', 'Génois-plan.pdf /archive/Génois-plan.pdf .pdf plan de Génois lourd'))"
            )

    config = AppConfig(root_paths=[Path("/tmp")], database_url=base_volume["url"], min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        r1 = client.get("/recherche", params={"q": "GENOIS", "limit": 50})
        r2 = client.get("/recherche", params={"q": "GÉNOIS", "limit": 50})
        assert r1.status_code == 200 and r2.status_code == 200
        c1 = [x["code"] for x in r1.json()["resultats"]]
        c2 = [x["code"] for x in r2.json()["resultats"]]
        assert c1 == c2
        assert any(c.startswith("VOL-") for c in c1)

        s1 = client.get("/search", params={"q": "GENOIS"})
        s2 = client.get("/search", params={"q": "GÉNOIS"})
        assert s1.status_code == 200 and s2.status_code == 200
        i1 = [x["id"] for x in s1.json()["results"]]
        i2 = [x["id"] for x in s2.json()["results"]]
        assert i1 == i2
        assert i1, "GET /search doit trouver le PDF Génois"

        sug_a = client.get("/recherche/suggestions", params={"prefix": "GE"})
        sug_b = client.get("/recherche/suggestions", params={"prefix": "GÉ"})
        assert sug_a.status_code == 200
        va = {s["valeur"] for s in sug_a.json()["suggestions"]}
        vb = {s["valeur"] for s in sug_b.json()["suggestions"]}
        assert "Génois" in va
        assert va == vb


def test_volume_parite_et_perf_indicative(base_volume: dict[str, Any]) -> None:
    """~800 fiches accentuées : mêmes IDs, mesure p50/p95 (pas un SLA atelier)."""
    index = base_volume["index"]
    for groupe in REQUETES_EQUIV:
        ensembles = []
        for q in groupe:
            reponse = rechercher_fiches(index, requete=q, limit=50)
            ensembles.append(_codes(reponse))
        assert ensembles[0], f"aucun hit pour {groupe[0]!r}"
        for autre in ensembles[1:]:
            assert autre == ensembles[0], f"parité cassée {groupe[0]!r} vs autre"

    # Originals inchangés après recherches.
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT titre FROM fiche WHERE code = 'VOL-0000'")
            assert "Génois" in cursor.fetchone()[0]

    requetes = [g[0] for g in REQUETES_EQUIV] + [g[1] for g in REQUETES_EQUIV]
    for q in requetes:  # chauffe
        rechercher_fiches(index, requete=q, limit=20)
    durees: list[float] = []
    for _ in range(4):
        for q in requetes:
            debut = time.perf_counter()
            rechercher_fiches(index, requete=q, limit=20)
            durees.append((time.perf_counter() - debut) * 1000.0)
    p50 = statistics.median(durees)
    p95 = sorted(durees)[int(len(durees) * 0.95) - 1]
    print(
        f"\n[qualif A volume] n_fiches={N_VOLUME} n={len(durees)} "
        f"p50={p50:.1f} ms p95={p95:.1f} ms max={max(durees):.1f} ms "
        f"(critère produit 100 ms — indicatif sandbox, pas un PASS atelier)"
    )
    # Garde-fou anti-régression absurde, pas un SLA.
    assert p95 < 2000.0, f"p95={p95:.1f} ms hors garde-fou sandbox"


def test_nfc_nfd_sur_volume(base_volume: dict[str, Any]) -> None:
    index = base_volume["index"]
    nfc = "Génois"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    a = _codes(rechercher_fiches(index, requete=nfc, limit=30))
    b = _codes(rechercher_fiches(index, requete=nfd, limit=30))
    assert a == b and a
