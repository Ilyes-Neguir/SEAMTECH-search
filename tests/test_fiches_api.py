"""Lot B.2 — endpoints de traçabilité et registre de gabarits (§17.11).

Trois niveaux : unitaire sur un index simulé (les SQL sont par ailleurs
validés par pglast), HTTP via TestClient contre SQLite (→ 503 attendu :
tables métier PostgreSQL uniquement, §17.1) et authentification, puis live
PostgreSQL marqué ``postgres`` (flux complet, gabarits et fiche réels).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.config import AppConfig
from seamtech_search.fiches import routes

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
URL_PG = __import__("os").environ.get("SEAMTECH_TEST_DATABASE_URL", "")


# ---------------------------------------------------------------------------
# Index simulé : répond aux requêtes du module routes (SQL validé par pglast).
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, donnees: dict[str, list[tuple]]) -> None:
        self._donnees = donnees
        self._derniere: list[tuple] = []

    def execute(self, sql: str, parametres: tuple = ()) -> None:
        for cle, valeurs in self._donnees.items():
            if cle in sql:
                self._derniere = valeurs
                return
        self._derniere = []

    def fetchone(self) -> tuple | None:
        return self._derniere[0] if self._derniere else None

    def fetchall(self) -> list[tuple]:
        return list(self._derniere)

    def close(self) -> None:
        pass

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _FakeConnexion:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def __enter__(self) -> "_FakeConnexion":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _FakeIndex:
    is_postgres = True

    def __init__(self, donnees: dict[str, list[tuple]]) -> None:
        self._connexion = _FakeConnexion(_FakeCursor(donnees))

    def connect(self) -> _FakeConnexion:
        return self._connexion


CHAMPS_7792 = [
    ("fiche.code", None, "fiche", "code", "7792-SO", "7792-SO", "gabarit", 0.99, 0, {"x0": 101.8, "page": 0}, 1, False, None, None),
    ("cotes.finie.slu_m", None, "fiche_cotes", "slu_m", "6,60 m", "6.6", "gabarit", 0.99, 0, {"x0": 234.0, "page": 0}, 1, True, 7, "2026-09-21T10:00:00+00:00"),
]


class TestChampsDeFicheUnitaire:
    def test_trace_complete_des_champs(self) -> None:
        index = _FakeIndex({"id_fiche FROM fiche": [(1,)], "FROM fiche_champ_extrait": CHAMPS_7792})
        champs = routes.champs_de_fiche(index, "7792-SO")
        assert len(champs) == 2
        assert champs[0]["champ"] == "fiche.code" and champs[0]["confiance"] == 0.99
        assert champs[0]["zone"]["page"] == 0 and champs[0]["version_gabarit"] == 1
        # la ligne corrigée porte ses informations de correction (écran lot D)
        assert champs[1]["corrige"] is True and champs[1]["corrige_par"] == 7

    def test_fiche_inconnue_404(self) -> None:
        index = _FakeIndex({})
        with pytest.raises(Exception) as attrape:
            routes.champs_de_fiche(index, "INCONNU")
        assert getattr(attrape.value, "status_code", None) == 404

    def test_sqlite_503(self) -> None:
        class IndexSQLite:
            is_postgres = False

        with pytest.raises(Exception) as attrape:
            routes.champs_de_fiche(IndexSQLite(), "X")
        assert getattr(attrape.value, "status_code", None) == 503
        assert "PostgreSQL" in str(attrape.value.detail)


class TestRegistreGabaritsUnitaire:
    def test_lister_dernieres_versions(self) -> None:
        lignes = [("FICHE_PORTANT_V1", 1, "Portant", ["voile de portant"], True, 3), ("FICHE_GENOIS_V1", 2, "Génois", ["génois"], True, 1)]
        index = _FakeIndex({"FROM gabarit ORDER BY code": lignes})
        gabarits = routes.lister_gabarits(index)
        assert [g["code"] for g in gabarits] == ["FICHE_PORTANT_V1", "FICHE_GENOIS_V1"]
        assert gabarits[1]["version"] == 2 and gabarits[0]["nb_ancres"] == 1

    def test_versions_inconnues_404(self) -> None:
        index = _FakeIndex({})
        with pytest.raises(Exception) as attrape:
            routes.versions_de_gabarit(index, "INCONNU")
        assert getattr(attrape.value, "status_code", None) == 404

    def test_publier_cree_version_max_plus_un(self) -> None:
        index = _FakeIndex({"COALESCE(MAX(version)": [(3,)], "INSERT INTO gabarit": [(4,)]})
        resultat = routes.publier_nouvelle_version(index, "FICHE_PORTANT_V1", "v2", ["ancre"], {"cible": {}})
        assert resultat["version"] == 4  # jamais destructive : max + 1

    def test_publier_refuse_sans_ancre_ni_regles(self) -> None:
        index = _FakeIndex({})
        for appel in (
            lambda: routes.publier_nouvelle_version(index, "G", "d", [], {"a": {}}),
            lambda: routes.publier_nouvelle_version(index, "G", "d", ["ancre"], {}),
        ):
            with pytest.raises(Exception) as attrape:
                appel()
            assert getattr(attrape.value, "status_code", None) == 422


class TestDetectionUnitaire:
    def test_detecter_pdf_inconnu_est_un_resultat(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Aucun gabarit en base → 200 « reprise_complete », jamais un crash."""
        pdf = tmp_path / "fiche.pdf"
        pdf.write_bytes(PDF_7792.read_bytes())
        monkeypatch.setattr(routes, "charger_gabarits", lambda index: [])
        resultat = routes.detecter_pdf(_FakeIndex({}), pdf)
        assert resultat["detecte"] is False and resultat["voie"] == "reprise_complete"
        assert isinstance(resultat["detail"], str)


# ---------------------------------------------------------------------------
# HTTP : 503 sans PostgreSQL (tables métier §17.1) + authentification.
# ---------------------------------------------------------------------------


def _client_sqlite(tmp_path: Path) -> TestClient:
    from seamtech_search.api import create_app

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "search.db")
    return TestClient(create_app(config))


@pytest.mark.parametrize(
    "methode,chemin",
    [
        ("get", "/fiches/7792-SO/champs"),
        ("get", "/gabarits"),
        ("get", "/gabarits/FICHE_PORTANT_V1/versions"),
        ("post", "/gabarits/FICHE_PORTANT_V1/versions"),
        ("post", "/gabarits/detecter"),
    ],
)
class TestRoutesSansPostgreSQL:
    def test_503_avec_explication(self, tmp_path: Path, methode: str, chemin: str) -> None:
        client = _client_sqlite(tmp_path)
        reponse = client.request(methode, chemin, json={} if methode == "post" else None)
        assert reponse.status_code == 503, reponse.text
        assert "PostgreSQL" in reponse.json()["detail"]


def test_routes_protegees_par_token(tmp_path: Path) -> None:
    from seamtech_search.api import create_app

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "search.db", auth_token="secret")
    client = TestClient(create_app(config))
    assert client.get("/gabarits").status_code == 401
    assert client.get("/gabarits", headers={"X-SEAMTECH-TOKEN": "secret"}).status_code == 503  # auth OK, PG absent


# ---------------------------------------------------------------------------
# Live PostgreSQL : flux complet (gabarits + fiche réels).
# ---------------------------------------------------------------------------


@pytest.mark.postgres
class TestRoutesLivePostgreSQL:
    @pytest.fixture()
    def base_live(self) -> Any:
        import uuid

        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        if not URL_PG:
            pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
        nom_base = f"b2_test_{uuid.uuid4().hex[:10]}"
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{nom_base}"')
        finally:
            admin.close()
        url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
        from seamtech_search.fiches.gabarits import initialiser_gabarits
        from seamtech_search.fiches.persistance import initialiser_verite_7792
        from seamtech_search.indexer import SearchIndex

        index = SearchIndex(Path(f"/tmp/unused-b2-{nom_base}.db"), url_base)
        index.initialize()
        index.run_migrations()
        initialiser_gabarits(index)
        initialiser_verite_7792(index, PDF_7792)
        try:
            yield {"index": index, "url": url_base}
        finally:
            index.close()
            admin = psycopg2.connect(URL_PG)
            admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            try:
                with admin.cursor() as cursor:
                    cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (nom_base,))
                    cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
            finally:
                admin.close()

    @pytest.fixture()
    def client_live(self, base_live: dict, tmp_path: Path) -> TestClient:
        from seamtech_search.api import create_app

        config = AppConfig(
            root_paths=[tmp_path],
            database_path=tmp_path / "unused.db",
            database_url=base_live["url"],
        )
        return TestClient(create_app(config))

    def test_registre_gabarits(self, client_live: TestClient) -> None:
        reponse = client_live.get("/gabarits")
        assert reponse.status_code == 200
        codes = {g["code"] for g in reponse.json()}
        assert {"FICHE_PORTANT_V1", "FICHE_GENOIS_V1"} <= codes

    def test_versions_et_publication_non_destructive(self, client_live: TestClient) -> None:
        avant = client_live.get("/gabarits/FICHE_PORTANT_V1/versions").json()
        assert len(avant) == 1
        reponse = client_live.post(
            "/gabarits/FICHE_PORTANT_V1/versions",
            json={"description": "v2 de test", "ancres_detection": ["voile de portant", "spi"], "regles": {"fiche.code": {"ancres": ["code fiche"]}}},
        )
        assert reponse.status_code == 201, reponse.text
        apres = client_live.get("/gabarits/FICHE_PORTANT_V1/versions").json()
        assert [v["version"] for v in apres] == [1, 2]
        assert apres[0]["actif"] is False  # v1 : lisible, désactivée — jamais supprimée
        assert apres[1]["actif"] is True  # v2 : la seule active

    def test_detection_sans_ecriture(self, client_live: TestClient, base_live: dict) -> None:
        reponse = client_live.post(
            "/gabarits/detecter",
            files={"fichier": ("fiche.pdf", PDF_7792.read_bytes(), "application/pdf")},
        )
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["detecte"] is True and corps["gabarit"] == "FICHE_PORTANT_V1"
        # rien n'a été écrit : ni fiche ni champ
        with base_live["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM fiche")
                assert cursor.fetchone()[0] == 0

    def test_champs_apres_ecriture(self, client_live: TestClient, base_live: dict) -> None:
        from seamtech_search.fiches.extraction import extraire_fiche
        from seamtech_search.fiches.gabarits import charger_gabarits
        from seamtech_search.fiches.persistance import ecrire_fiche

        fiche = extraire_fiche(PDF_7792, gabarits=charger_gabarits(base_live["index"]), gabarit_code="FICHE_PORTANT_V1")
        ecrire_fiche(base_live["index"], fiche)
        reponse = client_live.get("/fiches/7792-SO/champs")
        assert reponse.status_code == 200
        champs = reponse.json()
        assert any(c["champ"] == "fiche.code" and c["valeur_normalisee"] == "7792-SO" for c in champs)
        assert any(c["zone"] and c["zone"]["page"] == 0 for c in champs)
        inconnue = client_live.get("/fiches/ABSENTE/champs")
        assert inconnue.status_code == 404
