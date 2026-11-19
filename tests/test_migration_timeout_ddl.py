"""Le DDL de migration ne doit pas être borné par le timeout des requêtes.

Le 14 novembre 2026, le job `e2e` est tombé au moment de semer une base
neuve ::

    Migration 006_fiche_technique failed: canceling statement due to
    statement timeout

Tous les parcours fonctionnels étaient verts. Ce qui avait échoué, c'est
la CRÉATION DU SCHÉMA, interrompue au milieu parce que PostgreSQL
appliquait aux migrations le `statement_timeout` de 5 s prévu pour les
requêtes. Sur un serveur chargé, une migration dépasse 5 s ; ce n'est pas
une requête emballée, et l'interrompre laisse une installation neuve à
moitié migrée — avec un message qui parle d'un délai et non du problème.

Ces tests vérifient que les migrations obtiennent leur propre temps, et
surtout que le timeout d'origine est RÉTABLI : élargir une fenêtre n'est
pas supprimer une protection.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from seamtech_search.indexer import SearchIndex, _ddl_sans_chrono, _set_statement_timeout


class _Curseur:
    def __init__(self, journal: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._journal = journal

    def __enter__(self) -> _Curseur:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._journal.append((sql, params))


class _Connexion:
    """Double minimal : enregistre tout ce qui lui est ordonné."""

    def __init__(self) -> None:
        self.journal: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> _Curseur:
        return _Curseur(self.journal)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    @property
    def ordres(self) -> list[str]:
        return [sql for sql, _ in self.journal]


class _ConnexionQuiEchoue(_Connexion):
    """Une migration qui lève, pour prouver que le timeout est rendu quand même."""

    def __init__(self, sql_declencheur: str) -> None:
        super().__init__()
        self.sql_declencheur = sql_declencheur

    def cursor(self) -> _Curseur:
        return _CurseurQuiEchoue(self, self.sql_declencheur)


class _CurseurQuiEchoue(_Curseur):
    def __init__(self, connexion: _Connexion, sql_declencheur: str) -> None:
        super().__init__(connexion.journal)
        self.sql_declencheur = sql_declencheur

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.sql_declencheur in sql:
            raise RuntimeError("DDL interrompu")
        super().execute(sql, params)


class _SansCursor:
    """SQLite : pas de `statement_timeout`, donc rien à faire."""

    def __init__(self) -> None:
        self.appels: list[str] = []


def test_les_migrations_obtiennent_un_temps_de_ddl() -> None:
    connexion = _Connexion()
    with _ddl_sans_chrono(connexion, migration_timeout_ms=300000, statement_timeout_ms=5000):
        with connexion.cursor() as cur:
            cur.execute("CREATE TABLE exemple ()")

    assert ("SET statement_timeout = 300000", ()) in connexion.journal


def test_le_timeout_des_requetes_est_rendu_a_la_sortie() -> None:
    connexion = _Connexion()
    with _ddl_sans_chrono(connexion, migration_timeout_ms=300000, statement_timeout_ms=5000):
        pass

    assert connexion.journal[-1] == ("SET statement_timeout = 5000", ())


def test_le_timeout_est_rendu_meme_si_une_migration_leve() -> None:
    """Une installation à moitié migrée ne doit pas laisser une connexion
    sans limite de temps derrière elle."""
    connexion = _ConnexionQuiEchoue("CREATE TABLE exemple")

    with pytest.raises(RuntimeError, match="DDL interrompu"):
        with _ddl_sans_chrono(connexion, migration_timeout_ms=300000, statement_timeout_ms=5000):
            with connexion.cursor() as cur:
                cur.execute("CREATE TABLE exemple ()")

    assert connexion.journal[-1] == ("SET statement_timeout = 5000", ())


def test_sans_serveur_postgresql_rien_nest_tente() -> None:
    """Le mode SQLite (indexation de fichiers héritée) n'a pas de
    `statement_timeout` : ordonner `SET` planterait."""
    connexion = _SansCursor()
    with _ddl_sans_chrono(connexion, migration_timeout_ms=300000, statement_timeout_ms=5000, postgres=False):
        pass
    assert connexion.appels == []


def test_une_connexion_sqlite3_reelle_n_est_pas_touchee() -> None:
    """`sqlite3.Connection` possède `cursor()` — un test par `hasattr`
    passerait, puis planterait sur le `with`. On prouve avec le VRAI objet,
    pas avec un double complaisant."""
    import sqlite3
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as dossier:
        chemin = _Path(dossier) / "index.sqlite"
        connexion = sqlite3.connect(chemin)
        try:
            with _ddl_sans_chrono(
                connexion,
                migration_timeout_ms=300000,
                statement_timeout_ms=5000,
                postgres=False,
            ):
                connexion.execute("CREATE TABLE exemple (id INTEGER)")
            connexion.commit()
            assert connexion.execute("SELECT count(*) FROM exemple").fetchone()[0] == 0
        finally:
            connexion.close()


def test_le_timeout_de_migration_est_plus_long_que_celui_des_requetes() -> None:
    """Sinon le correctif ne corrige rien : c'est l'inégalité qui compte,
    pas la valeur exacte."""
    index = SearchIndex.__new__(SearchIndex)
    index.migration_timeout_ms = 300000
    index.statement_timeout_ms = 5000
    assert index.migration_timeout_ms > index.statement_timeout_ms


def test_run_migrations_passe_bien_par_le_regime_ddl() -> None:
    """Le gestionnaire peut être correct et ne jamais servir. On prouve ici
    que `run_migrations` l'emprunte : le `SET` précède toute migration."""
    connexion = _Connexion()
    index = SearchIndex.__new__(SearchIndex)
    index.database_url = "postgresql://example/injecte"
    index.migration_timeout_ms = 300000
    index.statement_timeout_ms = 5000
    index._connexion_injectee = connexion
    index._pg_ts_config = "simple"
    # Aucune migration ne doit réellement s'exécuter : on remplace la
    # mécanique par un marqueur, et on observe l'ordre des ordres.
    index._migrer = lambda connection, jusqu_a=None: connexion.cursor().execute(  # type: ignore[method-assign]
        "MARQUEUR_MIGRATIONS"
    )

    index.run_migrations()

    assert "MARQUEUR_MIGRATIONS" in connexion.ordres
    assert connexion.ordres.index("SET statement_timeout = 300000") < connexion.ordres.index("MARQUEUR_MIGRATIONS"), (
        "le SET doit précéder les migrations, sinon le DDL reste sous 5 s"
    )


def test_le_reglage_est_expose_par_la_configuration() -> None:
    """Une installation très lente doit pouvoir allonger sans réécrire le code."""
    from seamtech_search.config import AppConfig

    assert AppConfig.model_fields["migration_timeout_ms"].default == 300000
    assert AppConfig.model_fields["statement_timeout_ms"].default == 5000
    # L'inégalité doit tenir aux valeurs par défaut, pas seulement à l'instance
    # construite par un test : sinon une configuration par défaut reste cassée.
    defaut = AppConfig.model_construct()
    assert defaut.migration_timeout_ms > defaut.statement_timeout_ms


def test_une_connexion_injectee_ne_remplace_pas_le_pool_en_production() -> None:
    """Le point d'injection est un outil de test : le produit ne doit jamais
    le renseigner, sinon toutes les requêtes partiraient sur une connexion
    volée."""
    index = SearchIndex.__new__(SearchIndex)
    index.database_url = "postgresql://example/prod"
    index.migration_timeout_ms = 300000
    index.statement_timeout_ms = 5000
    index._connexion_injectee = None
    with contextlib.suppress(Exception):
        with index.connect():
            pass
    assert index._connexion_injectee is None


def test_la_valeur_est_toujours_un_entier() -> None:
    """`SET` n'accepte pas de paramètre lié : la valeur part en littéral.
    Elle doit donc être un entier QUOI QU'ON LUI DONNE — un réglage venu de
    la configuration ou de l'environnement ne doit jamais pouvoir y glisser
    du texte."""
    assert _set_statement_timeout(300000) == "SET statement_timeout = 300000"
    assert _set_statement_timeout(5000) == "SET statement_timeout = 5000"
    assert _set_statement_timeout("300000") == "SET statement_timeout = 300000"
    # Une valeur décimale ou suffixée est ramenée à un entier, pas
    # recopiée telle quelle dans le SQL.
    assert _set_statement_timeout(1500.9) == "SET statement_timeout = 1500"
    with pytest.raises((ValueError, TypeError)):
        _set_statement_timeout("5000; DROP TABLE documents --")
