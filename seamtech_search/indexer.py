from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import schema_metier
from .models import Document

logger = logging.getLogger("seamtech_search.indexer")


# ---------------------------------------------------------------------------
# Postgres accent folding (audit issue #5b)
# ---------------------------------------------------------------------------
# The SQLite backend indexes into an FTS5 table with the DEFAULT tokenizer,
# which folds diacritics: a search for "lattee" finds a document containing
# "lattée" and vice versa (verified on SQLite 3.40.1; `unicode61
# remove_diacritics 0` would NOT fold, but that is not what the schema uses).
#
# Postgres was using `to_tsvector('simple', ...)`, and the `simple`
# configuration only lowercases — it never strips accents. So the same query
# returned different rows depending on which backend the deployment used:
# accented French fabric sheets ("lattée", "élève", "référence") were
# unfindable by their unaccented spelling on Postgres but findable on SQLite.
#
# The fix is a text-search configuration that chains the `unaccent` dictionary
# in front of `simple`, rather than calling `unaccent()` on the text directly.
# Doing it as a configuration means `ts_headline` still lexizes the ORIGINAL
# text, so snippets keep their accents in the UI while matching folded query
# terms — calling `unaccent()` on the text would have stripped accents out of
# every displayed snippet.
PG_UNACCENT_CONFIG = "seamtech_unaccent"
PG_FALLBACK_CONFIG = "simple"
# Both names are internal constants interpolated into SQL; they never carry
# user input. `_postgres_ts_config` returns one of exactly these two values.
PG_TS_CONFIGS = (PG_UNACCENT_CONFIG, PG_FALLBACK_CONFIG)


SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_at REAL NOT NULL,
    is_dir INTEGER NOT NULL,
    extractor_version INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL DEFAULT 'extracted',
    extraction_detail TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'storage_direct',
    object_key TEXT,
    object_bucket TEXT,
    uploaded_at REAL,
    upload_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    name,
    path,
    extension,
    content
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    scanned INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    error TEXT,
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    size BIGINT NOT NULL,
    modified_at DOUBLE PRECISION NOT NULL,
    is_dir BOOLEAN NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector,
    extractor_version INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL DEFAULT 'extracted',
    extraction_detail TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'storage_direct',
    object_key TEXT,
    object_bucket TEXT,
    uploaded_at DOUBLE PRECISION,
    upload_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_documents_path_key ON documents(path_key);

CREATE TABLE IF NOT EXISTS scan_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    scanned BIGINT NOT NULL DEFAULT 0,
    changed BIGINT NOT NULL DEFAULT 0,
    removed BIGINT NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    error TEXT,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
"""


@dataclass(frozen=True)
class IndexStats:
    total_documents: int
    files: int
    folders: int


class ScanAlreadyRunningError(RuntimeError):
    """Raised when another indexing process already owns the scan lock."""


class SearchIndex:
    def __init__(
        self,
        database_path: Path,
        database_url: str | None = None,
        pool_min: int = 1,
        pool_max: int = 10,
        pool_timeout: float = 30.0,
        statement_timeout_ms: int = 5000,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_url = database_url
        self.pool_min = pool_min
        self.pool_max = pool_max
        self.pool_timeout = pool_timeout
        self.statement_timeout_ms = statement_timeout_ms
        self._pool: Any = None
        # Resolved Postgres text-search configuration name, cached per index
        # instance. See `_postgres_ts_config`.
        self._pg_ts_config: str | None = None
        if not self.is_postgres:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._init_pool()

    @property
    def is_postgres(self) -> bool:
        return bool(self.database_url)

    def _init_pool(self) -> None:
        if self.is_postgres and self.database_url:
            try:
                import psycopg2.pool

                options = f"-c statement_timeout={self.statement_timeout_ms}"
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=self.pool_min,
                    maxconn=self.pool_max,
                    dsn=self.database_url,
                    options=options,
                )
            except Exception as exc:
                logger.warning("Could not initialize psycopg2 ThreadedConnectionPool: %s", exc)
                self._pool = None

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.is_postgres:
            if self._pool is None and self.database_url:
                self._init_pool()
            if self._pool is not None:
                conn = self._pool.getconn()
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    self._pool.putconn(conn)
                return
            else:
                import psycopg2

                conn = psycopg2.connect(
                    self.database_url,
                    connect_timeout=10,
                    options=f"-c statement_timeout={self.statement_timeout_ms}",
                )
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
                return

        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        if self._pool is not None:
            try:
                self._pool.closeall()
            except Exception as exc:
                logger.debug("Error while closing Postgres connection pool: %s", exc)
            self._pool = None

    # ------------------------------------------------------------------
    # Versioned migrations — runs once at startup, never from request handlers
    # ------------------------------------------------------------------

    def _ensure_migrations_table(self, connection: Any) -> None:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def _get_applied_migrations(self, connection: Any) -> set[str]:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version FROM schema_migrations")
                return {row[0] for row in cursor.fetchall()}
        else:
            rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
            return {row["version"] for row in rows}

    def _record_migration(self, connection: Any, version: str) -> None:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
                    (version,),
                )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    def _migration_001_initial(self, connection: Any) -> None:
        # Base tables already created by POSTGRES_SCHEMA / SQLITE_SCHEMA
        pass

    def _migration_002_object_storage_columns(self, connection: Any) -> None:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_key TEXT")
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_bucket TEXT")
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_at DOUBLE PRECISION")
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS upload_status TEXT NOT NULL DEFAULT 'pending'"
                )
                # For backward compat, keep not_configured default if already exists, but ensure column exists
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor_version INTEGER NOT NULL DEFAULT 0"
                )
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''")
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'extracted'"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_detail TEXT NOT NULL DEFAULT ''"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'storage_direct'"
                )
        else:
            self._ensure_documents_columns(connection)

    def _migration_003_category_backfill_guard(self, connection: Any) -> None:
        # Fix for 1.4: never overwrite technical_pdf / plan_pdf etc.
        # Only backfill where category is null/empty/default.
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE documents SET category = CASE
                        WHEN is_dir = false THEN
                            CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                        ELSE 'folder'
                    END
                    WHERE category IS NULL OR category = '' OR category = 'storage_direct'
                      AND path_key NOT IN (SELECT path_key FROM documents WHERE category IN ('technical_pdf','plan_pdf','excel_sheet','storage_direct','analyzed','folder'))
                    """
                )
                # Safer: only where category is null/empty
                cursor.execute(
                    """
                    UPDATE documents SET category = CASE
                        WHEN is_dir = false THEN
                            CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                        ELSE 'folder'
                    END
                    WHERE category IS NULL OR category = ''
                    """
                )
                cursor.execute(
                    """
                    UPDATE documents
                    SET search_vector = to_tsvector(
                        'simple',
                        concat_ws(E'\\n', name, path, extension, content)
                    )
                    WHERE search_vector = ''::tsvector
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING GIN(search_vector)"
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_path_key ON documents(path_key)")
                cursor.execute(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'imports' AND column_name = 'payload'
                    """
                )
                row = cursor.fetchone()
                payload_type = (row[0] if row else "jsonb")
                if payload_type == "text":
                    cursor.execute("ALTER TABLE imports ALTER COLUMN payload TYPE JSONB USING payload::jsonb")
        else:
            # SQLite: ensure FTS and backfill guarded
            self._ensure_fts_schema(connection)
            # Only backfill where category is null/empty, not overwriting technical_pdf etc.
            try:
                connection.execute(
                    """
                    UPDATE documents SET category = CASE
                        WHEN is_dir = 0 THEN
                            CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                        ELSE 'folder'
                    END
                    WHERE category IS NULL OR category = ''
                    """
                )
            except Exception as exc:
                # A failed backfill leaves categories wrong on disk; that is a
                # data problem a startup must announce, not a silent pass.
                logger.warning("Category backfill failed during migration 003 (SQLite): %s", exc)

    def _migration_004_uploaded_at_epoch(self, connection: Any) -> None:
        """Store upload times as Unix epoch seconds, matching Document.uploaded_at."""
        if not self.is_postgres:
            return

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'documents'
                  AND column_name = 'uploaded_at'
                """
            )
            row = cursor.fetchone()
            if row and row[0] in {"timestamp with time zone", "timestamp without time zone"}:
                cursor.execute(
                    """
                    ALTER TABLE documents
                    ALTER COLUMN uploaded_at TYPE DOUBLE PRECISION
                    USING EXTRACT(EPOCH FROM uploaded_at)
                    """
                )

    def _ensure_unaccent_config(self, connection: Any) -> bool:
        """Idempotently install `unaccent` and a text-search config that uses it.

        Returns True when the accent-folding configuration is available.

        Everything here is wrapped in a SAVEPOINT. Two reasons: `CREATE
        EXTENSION` needs privileges the database role may not have (managed
        Postgres), and a DDL failure aborts the surrounding transaction, which
        would leave every later statement in the same transaction failing. On
        any failure this logs at ERROR and degrades to the plain `simple`
        configuration: accent parity with SQLite is lost, but the app still
        starts and search still works. tests/test_indexer_accent_parity.py
        fails loudly if CI ever takes that degraded path.
        """
        with connection.cursor() as cursor:
            cursor.execute("SAVEPOINT seamtech_unaccent")
            try:
                # `unaccent` is a trusted extension, so a non-superuser with
                # CREATE privilege on the database can install it.
                cursor.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

                cursor.execute(
                    "SELECT 1 FROM pg_ts_config WHERE cfgname = %s AND cfgnamespace = current_schema()::regnamespace",
                    (PG_UNACCENT_CONFIG,),
                )
                if cursor.fetchone() is None:
                    # Postgres has no CREATE TEXT SEARCH CONFIGURATION IF NOT
                    # EXISTS, hence the catalog check. COPY = simple keeps the
                    # same parser and stopword behaviour; the ALTER then chains
                    # `unaccent` in front of `simple` for word-like tokens.
                    #
                    # This is the exact recipe from the PostgreSQL 16 docs
                    # (F.48. unaccent, "Usage"). The grammar is
                    # `ALTER MAPPING FOR <token types> WITH <dictionaries>`;
                    # there is no `FOR ... TO ...` form, and `ALTER MAPPING`
                    # (not `ADD MAPPING`) is required because COPY = simple
                    # already installed mappings for these token types.
                    cursor.execute(
                        f"CREATE TEXT SEARCH CONFIGURATION {PG_UNACCENT_CONFIG} (COPY = simple)"
                    )
                    cursor.execute(
                        f"ALTER TEXT SEARCH CONFIGURATION {PG_UNACCENT_CONFIG} "
                        "ALTER MAPPING FOR hword, hword_part, word "
                        "WITH unaccent, simple"
                    )
                    logger.info(
                        "Created text-search configuration %s (unaccent + simple)", PG_UNACCENT_CONFIG
                    )
                cursor.execute("RELEASE SAVEPOINT seamtech_unaccent")
            except Exception as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT seamtech_unaccent")
                cursor.execute("RELEASE SAVEPOINT seamtech_unaccent")
                logger.error(
                    "Could not set up accent-folding full-text search (%s). Postgres search "
                    "falls back to the %r configuration, so accented and unaccented queries "
                    "will NOT return the same rows as the SQLite backend.",
                    exc,
                    PG_FALLBACK_CONFIG,
                )
                self._pg_ts_config = PG_FALLBACK_CONFIG
                return False

            # Read the catalog back rather than trusting the branch above, so
            # the cached configuration name is always one Postgres agrees
            # exists. This is what `_postgres_ts_config` will return.
            cursor.execute(
                "SELECT 1 FROM pg_ts_config WHERE cfgname = %s AND cfgnamespace = current_schema()::regnamespace",
                (PG_UNACCENT_CONFIG,),
            )
            self._pg_ts_config = PG_UNACCENT_CONFIG if cursor.fetchone() is not None else PG_FALLBACK_CONFIG
            return self._pg_ts_config == PG_UNACCENT_CONFIG

    def _postgres_ts_config(self, connection: Any) -> str:
        """Name of the text-search configuration to use, resolved once and cached.

        Falls back to `simple` when the accent-folding configuration is absent
        (unprivileged role, or a database created before migration 005).
        """
        if self._pg_ts_config is not None:
            return self._pg_ts_config
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_ts_config WHERE cfgname = %s AND cfgnamespace = current_schema()::regnamespace",
                (PG_UNACCENT_CONFIG,),
            )
            available = cursor.fetchone() is not None
        self._pg_ts_config = PG_UNACCENT_CONFIG if available else PG_FALLBACK_CONFIG
        if not available:
            logger.warning(
                "Text-search configuration %s is missing; using %r, so accented and unaccented "
                "queries will NOT return the same rows as the SQLite backend.",
                PG_UNACCENT_CONFIG,
                PG_FALLBACK_CONFIG,
            )
        return self._pg_ts_config

    def _migration_005_unaccent_search_vector(self, connection: Any) -> None:
        """Make Postgres accent parity match SQLite's accent-folding FTS5 index."""
        if not self.is_postgres:
            return

        if not self._ensure_unaccent_config(connection):
            # Nothing to rebuild: the vectors are already `simple`, and
            # `_postgres_ts_config` will keep resolving to `simple`.
            return

        with connection.cursor() as cursor:
            # Rebuild EVERY vector, not just the empty ones: rows indexed before
            # this migration carry `simple` lexemes and would otherwise stay
            # unfindable by their unaccented spelling forever.
            cursor.execute(
                f"""
                UPDATE documents
                SET search_vector = to_tsvector(
                    '{PG_UNACCENT_CONFIG}',
                    concat_ws(E'\\n', name, path, extension, content)
                )
                """
            )
            logger.info("Rebuilt %d search vector(s) with accent folding", cursor.rowcount)

    # ------------------------------------------------------------------
    # Migrations 006-009 — couche métier « fiche technique » (Lot A).
    # DÉCISION §17.1 du plan v3.0 : PostgreSQL UNIQUEMENT. Sur SQLite ces
    # migrations ne font rien (warning journalisé, migration enregistrée) :
    # le mode SQLite reste supporté pour l'indexation de fichiers héritée,
    # pas pour les fiches. Ne pas « rétablir la parité SQLite ».
    # ------------------------------------------------------------------

    def _migration_006_fiche_technique(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 006_fiche_technique ignorée : la couche métier est PostgreSQL uniquement "
                "(décision §17.1) — conséquence : aucune table de fiches en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            # Constat 1 de revue : la configuration de recherche effective est
            # résolue ICI (dégradation gracieuse vers 'simple' si le rôle ne
            # peut pas installer unaccent — même choix que la migration 005)
            # et injectée dans le DDL de chunk.tsv. Le script ne suppose jamais
            # que 'seamtech_unaccent' existe.
            self._ensure_unaccent_config(connection)
            config = self._postgres_ts_config(connection)
            script = schema_metier.SQL_006_FICHE_TECHNIQUE.replace(
                schema_metier.MARQUEUR_TS_CONFIG, config
            )
            try:
                cursor.execute(script)
            except Exception as exc:
                # `vector` n'est pas une extension « trusted » : sans
                # superutilisateur ni préinstallation, le CREATE EXTENSION
                # échoue au niveau du serveur. La couche métier ne peut pas se
                # dégrader silencieusement (colonnes vector(384) obligatoires),
                # donc l'échec reste fatal — mais il porte l'action exacte.
                if getattr(exc, "sqlstate", None) == "42501" or "permission denied" in str(exc).lower():
                    logger.error("Migration 006 bloquée par les privilèges PostgreSQL : %s", exc)
                    raise RuntimeError(
                        "Migration 006_fiche_technique impossible : le rôle PostgreSQL n'a pas le "
                        "privilège de créer l'extension 'vector' (requis par les colonnes vector(384) "
                        "de la couche métier). Actions possibles : (1) utiliser l'image "
                        "pgvector/pgvector:pg16 (compose et CI la fournissent) avec le rôle "
                        "superutilisateur du conteneur ; ou (2) demander à l'administrateur de "
                        "préinstaller l'extension dans la base : CREATE EXTENSION vector;. "
                        f"Erreur serveur : {exc}"
                    ) from exc
                raise

    def _migration_007_recherche_index(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 007_recherche_index ignorée : la couche métier est PostgreSQL uniquement "
                "(décision §17.1) — conséquence : aucun index de recherche de fiches en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            # Constat 1 de revue (volet pg_trgm) : les index trigrammes sont
            # dégradables — sans le privilège CREATE sur la base, pg_trgm ne
            # s'installe pas et seuls les index de tolérance aux fautes sont
            # omis (avertissement + conséquence journalisés). Le cœur (colonne
            # de recherche pondérée, fonction, synonymes, journal) s'applique.
            config = self._postgres_ts_config(connection)
            # D'abord le cœur : les index trigrammes ciblent fiche.champs_texte,
            # qui n'existe pas avant lui.
            cursor.execute(
                schema_metier.SQL_007_RECHERCHE_INDEX.replace(schema_metier.MARQUEUR_TS_CONFIG, config)
            )
            cursor.execute("SAVEPOINT seamtech_pg_trgm")
            try:
                cursor.execute(schema_metier.SQL_007_TRGM)
                cursor.execute("RELEASE SAVEPOINT seamtech_pg_trgm")
            except Exception as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT seamtech_pg_trgm")
                logger.warning(
                    "pg_trgm indisponible (%s) — conséquence : index trigrammes omis, la tolérance "
                    "aux fautes (« monofim » → Monofilm) est désactivée ; la recherche plein-texte "
                    "et la recherche par mots-clés restent opérationnelles.",
                    exc,
                )

    def _migration_008_ml_corpus(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 008_ml_corpus ignorée : la couche métier est PostgreSQL uniquement "
                "(décision §17.1) — conséquence : aucun corpus ML en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_008_ML_CORPUS)

    def _migration_009_qualite_et_gabarits(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 009_qualite_et_gabarits ignorée : la couche métier est PostgreSQL uniquement "
                "(décision §17.1) — conséquence : aucune vue qualité en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_009_QUALITE_ET_GABARITS)

    def _migration_010_lots_ingestion(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 010_lots_ingestion ignorée : la couche métier est PostgreSQL uniquement "
                "(décision §17.1) — conséquence : aucun lot d'ingestion en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_010_LOTS_INGESTION)

    def _migration_011_pieces_catalogue_documents(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 011_pieces_catalogue_documents ignorée : la couche métier est "
                "PostgreSQL uniquement (décision §17.1) — conséquence : les pièces jointes "
                "restent hors catalogue documents en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_011_PIECES_CATALOGUE_DOCUMENTS)

    def _migration_012_recherche_hybride(self, connection: Any) -> None:
        if not self.is_postgres:
            logger.warning(
                "Migration 012_recherche_hybride ignorée : la couche métier est "
                "PostgreSQL uniquement (décision §17.1) — conséquence : aucune recherche "
                "hybride de fiches en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            # Même dégradation gracieuse que 007 : le cœur (index de facettes,
            # suivi des recherches sans résultat, rafraîchissement global,
            # backfill) s'applique toujours ; les index trigrammes des
            # référentiels (suggestions tolérantes aux fautes) sont omis si
            # pg_trgm ne peut pas s'installer, avec avertissement journalisé.
            config = self._postgres_ts_config(connection)
            cursor.execute(
                schema_metier.SQL_012_RECHERCHE_HYBRIDE.replace(schema_metier.MARQUEUR_TS_CONFIG, config)
            )
            cursor.execute("SAVEPOINT seamtech_012_trgm")
            try:
                cursor.execute(schema_metier.SQL_012_TRGM)
                cursor.execute("RELEASE SAVEPOINT seamtech_012_trgm")
            except Exception as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT seamtech_012_trgm")
                logger.warning(
                    "pg_trgm indisponible (%s) — conséquence : index trigrammes des référentiels "
                    "omis ; les suggestions par préfixe restent opérationnelles, la tolérance aux "
                    "fautes dans les suggestions est désactivée.",
                    exc,
                )

    def _migration_013_recherche_fonds_reel(self, connection: Any) -> None:
        """Tâche 3 : le rejeu du jeu de requêtes RÉEL sur la vraie fiche
        7792-SO a mesuré deux angles morts du vecteur — la raison sociale du
        client (« cruette ») et l'année d'édition (« spi sailonet 2026 »).
        Les deux sont ajoutées au texte pondéré (poids B)."""
        if not self.is_postgres:
            logger.info(
                "Migration 013 (recherche fonds réel) ignorée en mode SQLite — PostgreSQL uniquement (§17.1)."
            )
            return
        with connection.cursor() as cursor:
            config = self._postgres_ts_config(connection)
            cursor.execute(
                schema_metier.SQL_013_RECHERCHE_FONDS_REEL.replace(schema_metier.MARQUEUR_TS_CONFIG, config)
            )

    def _migration_014_facette_dimension(self, connection: Any) -> None:
        """Lot J : facette dimension — vue v_fiche_recherche complète avec
        tetiere_cm + index sur les 7 cotes pour le filtre par plage."""
        if not self.is_postgres:
            logger.info(
                "Migration 014 (facette dimension) ignorée en mode SQLite — PostgreSQL uniquement (§17.1)."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_014_FACETTE_DIMENSION)

    def _migration_015_qualite_gabarit_brouillon(self, connection: Any) -> None:
        """Lot K : index qualité + table gabarit_brouillon (brouillons non actifs)."""
        if not self.is_postgres:
            logger.info(
                "Migration 015 (qualité + brouillon) ignorée en mode SQLite — PostgreSQL uniquement (§17.1)."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_015_QUALITE_GABARIT_BROUILLON)

    def _migration_016_dedup_comptes_nominatifs(self, connection: Any) -> None:
        """Lot L : doublons (L.1) + comptes nominatifs (L.2).

        Le cœur (fonction de normalisation, index ``idx_pj_empreinte``,
        ``idx_fiche_lien_type``, colonnes d'attribution) s'applique toujours.
        Les index TRIGRAMMES sur ``fiche.titre`` sont **dégradables**, exactement
        comme au 007 : sans le privilège d'installer ``pg_trgm``, ils sont omis
        avec un avertissement + la conséquence écrite, et la détection de
        doublons bascule sur son repli déterministe au lieu d'échouer.
        """
        if not self.is_postgres:
            logger.warning(
                "Migration 016_dedup_comptes_nominatifs ignorée : la couche métier est PostgreSQL "
                "uniquement (§17.1) — conséquence : ni fiche_lien ni session_ui en mode SQLite."
            )
            return
        with connection.cursor() as cursor:
            cursor.execute(schema_metier.SQL_016_DEDUP_COMPTES_NOMINATIFS)
            cursor.execute("SAVEPOINT seamtech_trgm_dedup")
            try:
                cursor.execute(schema_metier.SQL_016_TRGM_TITRE)
                cursor.execute("RELEASE SAVEPOINT seamtech_trgm_dedup")
            except Exception as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT seamtech_trgm_dedup")
                logger.warning(
                    "pg_trgm indisponible pour la déduplication (%s) — conséquence : pas d'index "
                    "trigrammes sur fiche.titre, la détection des doublons PROBABLES bascule sur le "
                    "repli déterministe documenté (titre identique à la casse/accents près ET même "
                    "client+bateau+gamme+année) ; la détection des doublons EXACTS (SHA-256) n'est "
                    "pas affectée.",
                    exc,
                )

    def run_migrations(self) -> None:
        """Run pending schema migrations once at startup."""
        with self.connect() as connection:
            self._ensure_migrations_table(connection)
            applied = self._get_applied_migrations(connection)

            migrations = [
                ("001_initial", self._migration_001_initial),
                ("002_object_storage_columns", self._migration_002_object_storage_columns),
                ("003_category_backfill_guard", self._migration_003_category_backfill_guard),
                ("004_uploaded_at_epoch", self._migration_004_uploaded_at_epoch),
                ("005_unaccent_search_vector", self._migration_005_unaccent_search_vector),
                # Couche métier « fiches » — PostgreSQL uniquement (§17.1).
                ("006_fiche_technique", self._migration_006_fiche_technique),
                ("007_recherche_index", self._migration_007_recherche_index),
                ("008_ml_corpus", self._migration_008_ml_corpus),
                ("009_qualite_et_gabarits", self._migration_009_qualite_et_gabarits),
                ("010_lots_ingestion", self._migration_010_lots_ingestion),
                ("011_pieces_catalogue_documents", self._migration_011_pieces_catalogue_documents),
                ("012_recherche_hybride", self._migration_012_recherche_hybride),
                ("013_recherche_fonds_reel", self._migration_013_recherche_fonds_reel),
                ("014_facette_dimension", self._migration_014_facette_dimension),
                ("015_qualite_gabarit_brouillon", self._migration_015_qualite_gabarit_brouillon),
                # Lot L — une migration NON ENREGISTRÉE ici ferait échouer le job
                # `sauvegarde` (l'échec du Lot K) : 016 est la version attendue
                # par VERSION_SCHEMA_METIER, elle doit être APPLIQUÉE.
                ("016_dedup_comptes_nominatifs", self._migration_016_dedup_comptes_nominatifs),
            ]

            for version, func in migrations:
                if version not in applied:
                    logger.info("Applying schema migration %s", version)
                    try:
                        func(connection)
                        self._record_migration(connection, version)
                        logger.info("Migration %s applied", version)
                    except Exception as exc:
                        logger.error("Migration %s failed: %s", version, exc)
                        raise

    def _ensure_documents_columns_postgres(self, cursor: Any) -> None:
        """Legacy helper kept for backward compat, now delegates to migration."""
        # This is now a no-op for new code; migrations handle it.
        # Kept to avoid breaking existing tests that call it indirectly.
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector"
        )
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor_version INTEGER NOT NULL DEFAULT 0"
        )
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'extracted'"
        )
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_detail TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'storage_direct'"
        )
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_key TEXT")
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_bucket TEXT")
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_at DOUBLE PRECISION")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS upload_status TEXT NOT NULL DEFAULT 'pending'"
        )

    def initialize(self, rebuild: bool = False) -> None:
        """Create tables if not exists — no DDL backfill, no category overwrite."""
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    if rebuild:
                        cursor.execute("DROP TABLE IF EXISTS documents")
                        cursor.execute("DROP TABLE IF EXISTS schema_migrations")
                    cursor.execute(POSTGRES_SCHEMA)
                    # Ensure migrations table exists, but do not run migrations here
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS schema_migrations (
                            version TEXT PRIMARY KEY,
                            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                        """
                    )
                # Idempotent, so it is safe (and necessary) outside
                # run_migrations: the Postgres integration tests call
                # initialize() alone, and a database restored from a dump may
                # already have the table but not the extension. Without this
                # the accent-parity behaviour would silently depend on which
                # entry point happened to run first. It also caches the
                # resolved configuration name for `_postgres_ts_config`.
                self._ensure_unaccent_config(connection)
            else:
                if rebuild:
                    connection.executescript(
                        """
                        DROP TABLE IF EXISTS documents_fts;
                        DROP TABLE IF EXISTS documents;
                        DROP TABLE IF EXISTS schema_migrations;
                        """
                    )
                connection.executescript(SQLITE_SCHEMA)
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                # For SQLite, ensure FTS exists but do not overwrite category
                self._ensure_fts_schema(connection)

    @contextmanager
    def scan_lock(self) -> Iterator[None]:
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(748321)")
                    acquired = bool(cursor.fetchone()[0])
                    if not acquired:
                        raise ScanAlreadyRunningError("Another indexing scan is already running")
                    try:
                        yield
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(748321)")
            return

        lock_path = self.database_path.with_suffix(self.database_path.suffix + ".scan.lock")
        while True:
            try:
                lock_handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                try:
                    lock_pid = int(lock_path.read_text(encoding="ascii"))
                    os.kill(lock_pid, 0)
                except (FileNotFoundError, ProcessLookupError, ValueError):
                    lock_path.unlink(missing_ok=True)
                    continue
                except PermissionError:
                    pass
                raise ScanAlreadyRunningError("Another indexing scan is already running") from exc
        try:
            os.write(lock_handle, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(lock_handle)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def scan_snapshot(self) -> Iterator[None]:
        """Snapshot documents table for rollback on scan failure.

        Note: this does a full table copy (CREATE TABLE AS / sqlite backup).
        Acceptable for <100k docs (current scale), but at larger scale should
        be replaced by a transaction with savepoint instead of full copy.
        Documented limit: O(N) storage/time per scan.
        """
        if self.is_postgres:
            backup_table = f"scan_backup_documents_{os.getpid()}"
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DROP TABLE IF EXISTS " + backup_table)
                    cursor.execute("SELECT to_regclass('public.documents')")
                    has_documents = cursor.fetchone()[0] is not None
                    if has_documents:
                        cursor.execute(f"CREATE TABLE {backup_table} AS TABLE documents")
            try:
                yield
            except Exception:
                if has_documents:
                    with self.connect() as connection:
                        with connection.cursor() as cursor:
                            cursor.execute("TRUNCATE TABLE documents")
                            cursor.execute(f"INSERT INTO documents SELECT * FROM {backup_table}")
                raise
            finally:
                with self.connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("DROP TABLE IF EXISTS " + backup_table)
            return

        backup_path = self.database_path.with_suffix(self.database_path.suffix + ".scan-backup")
        backup_path.unlink(missing_ok=True)
        with sqlite3.connect(self.database_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        try:
            yield
        except Exception:
            with self.connect() as connection:
                scan_runs = connection.execute("SELECT * FROM scan_runs ORDER BY id").fetchall()
            source = sqlite3.connect(backup_path)
            target = sqlite3.connect(self.database_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            with self.connect() as connection:
                connection.execute("DELETE FROM scan_runs")
                connection.executemany(
                    "INSERT INTO scan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [tuple(row) for row in scan_runs],
                )
            raise
        finally:
            backup_path.unlink(missing_ok=True)

    def start_scan(self) -> int:
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO scan_runs (status) VALUES ('running') RETURNING id")
                    return int(cursor.fetchone()[0])
            cursor = connection.execute(
                "INSERT INTO scan_runs (started_at, status) VALUES (datetime('now'), 'running')"
            )
            return int(cursor.lastrowid)

    def finish_scan(
        self, scan_id: int, status: str, scanned: int, changed: int, removed: int, error: str | None = None
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("scan status must be completed or failed")
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE scan_runs
                        SET finished_at = now(), status = %s, scanned = %s, changed = %s, removed = %s, error = %s
                        WHERE id = %s
                        """,
                        (status, scanned, changed, removed, error, scan_id),
                    )
                return
            connection.execute(
                """
                UPDATE scan_runs
                SET finished_at = datetime('now'), status = ?, scanned = ?, changed = ?, removed = ?, error = ?
                WHERE id = ?
                """,
                (status, scanned, changed, removed, error, scan_id),
            )

    def latest_scan(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            if self.is_postgres:
                import psycopg2.extras

                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
                    row = cursor.fetchone()
                    if not row:
                        return None
                    return {
                        "id": row["id"],
                        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
                        "status": row["status"],
                        "scanned": row["scanned"],
                        "changed": row["changed"],
                        "removed": row["removed"],
                        "error": row["error"],
                    }

            row = connection.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            return dict(row)

    def stored_manifest(self) -> dict[str, tuple[int, float, int]]:
        """Return a mapping of path_key -> (size, modified_at, extractor_version)."""
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT path_key, size, modified_at, extractor_version FROM documents WHERE is_dir = false"
                    )
                    return {row[0]: (int(row[1]), float(row[2]), int(row[3])) for row in cursor.fetchall()}
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT path_key, size, modified_at, extractor_version FROM documents WHERE is_dir = 0"
            ).fetchall()
            return {
                row["path_key"]: (int(row["size"]), float(row["modified_at"]), int(row["extractor_version"]))
                for row in rows
            }

    def _ensure_documents_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
        if "extractor_version" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extractor_version INTEGER NOT NULL DEFAULT 0")
        if "content_hash" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
        if "extraction_status" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extraction_status TEXT NOT NULL DEFAULT 'extracted'")
        if "extraction_detail" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extraction_detail TEXT NOT NULL DEFAULT ''")
        if "category" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN category TEXT NOT NULL DEFAULT 'storage_direct'")
            connection.execute(
                """
                UPDATE documents SET category = CASE
                    WHEN is_dir = 0 THEN
                        CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                    ELSE 'folder'
                END
                """
            )
        if "object_key" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN object_key TEXT")
        if "object_bucket" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN object_bucket TEXT")
        if "uploaded_at" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN uploaded_at REAL")
        if "upload_status" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN upload_status TEXT NOT NULL DEFAULT 'pending'")

    def _ensure_fts_schema(self, connection: sqlite3.Connection) -> None:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents_fts'"
        ).fetchone()
        if schema and "content" not in schema[0]:
            connection.executescript(
                """
                DROP TABLE documents_fts;
                CREATE VIRTUAL TABLE documents_fts USING fts5(
                    name,
                    path,
                    extension,
                    content
                );
                INSERT INTO documents_fts(rowid, name, path, extension, content)
                SELECT id, name, path, extension, '' FROM documents;
                """
            )

    def upsert_document(self, document: Document) -> bool:
        return self.upsert_documents([document]) > 0

    def upsert_documents(self, documents: list[Document]) -> int:
        if not documents:
            return 0
        if self.is_postgres:
            return self._upsert_documents_postgres(documents)
        return self._upsert_documents_sqlite(documents)

    def _upsert_documents_sqlite(self, documents: list[Document]) -> int:
        changed = 0
        with self.connect() as connection:
            for document in documents:
                existing = connection.execute(
                    "SELECT id, size, modified_at, extractor_version FROM documents WHERE path_key = ?",
                    (document.path_key,),
                ).fetchone()
                if (
                    existing
                    and existing["size"] == document.size
                    and existing["modified_at"] == document.modified_at
                    and existing["extractor_version"] == document.extractor_version
                ):
                    continue

                if existing:
                    row_id = int(existing["id"])
                    connection.execute(
                        """
                        UPDATE documents
                        SET path = ?, name = ?, parent_path = ?, extension = ?, size = ?,
                            modified_at = ?, is_dir = ?, extractor_version = ?, content_hash = ?,
                            extraction_status = ?, extraction_detail = ?, category = ?,
                            object_key = ?, object_bucket = ?, uploaded_at = ?, upload_status = ?
                        WHERE id = ?
                        """,
                        _document_values(document) + (row_id,),
                    )
                    connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO documents (
                            path_key, path, name, parent_path, extension, size, modified_at, is_dir,
                            extractor_version, content_hash, extraction_status, extraction_detail, category,
                            object_key, object_bucket, uploaded_at, upload_status
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (document.path_key,) + _document_values(document),
                    )
                    row_id = int(cursor.lastrowid)

                connection.execute(
                    """
                    INSERT INTO documents_fts(rowid, name, path, extension, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        document.name,
                        str(document.path),
                        document.extension,
                        document.searchable_text,
                    ),
                )
                changed += 1
        return changed

    def _upsert_documents_postgres(self, documents: list[Document]) -> int:
        values = [
            (
                document.path_key,
                str(document.path),
                document.name,
                str(document.parent_path),
                document.extension,
                document.size,
                document.modified_at,
                document.is_dir,
                document.searchable_text,
                document.searchable_text,
                document.extractor_version,
                document.content_hash,
                document.extraction_status,
                document.extraction_detail,
                document.category,
                document.object_key,
                document.object_bucket,
                document.uploaded_at,
                document.upload_status,
            )
            for document in documents
        ]
        with self.connect() as connection:
            with connection.cursor() as cursor:
                import psycopg2.extras

                # One of the two internal constants in PG_TS_CONFIGS, never
                # user input, so interpolating it into the template is safe.
                ts_config = self._postgres_ts_config(connection)
                assert ts_config in PG_TS_CONFIGS, f"unexpected ts config {ts_config!r}"

                psycopg2.extras.execute_values(
                    cursor,
                    """
                    INSERT INTO documents (
                        path_key, path, name, parent_path, extension, size, modified_at, is_dir, content, search_vector,
                        extractor_version, content_hash, extraction_status, extraction_detail, category,
                        object_key, object_bucket, uploaded_at, upload_status
                    )
                    VALUES %s
                    ON CONFLICT (path_key) DO UPDATE SET
                        path = EXCLUDED.path,
                        name = EXCLUDED.name,
                        parent_path = EXCLUDED.parent_path,
                        extension = EXCLUDED.extension,
                        size = EXCLUDED.size,
                        modified_at = EXCLUDED.modified_at,
                        is_dir = EXCLUDED.is_dir,
                        content = EXCLUDED.content,
                        search_vector = EXCLUDED.search_vector,
                        extractor_version = EXCLUDED.extractor_version,
                        content_hash = EXCLUDED.content_hash,
                        extraction_status = EXCLUDED.extraction_status,
                        extraction_detail = EXCLUDED.extraction_detail,
                        category = EXCLUDED.category,
                        object_key = EXCLUDED.object_key,
                        object_bucket = EXCLUDED.object_bucket,
                        uploaded_at = EXCLUDED.uploaded_at,
                        upload_status = EXCLUDED.upload_status
                    """,
                    values,
                    template=(
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        f"to_tsvector('{ts_config}', %s), "
                        "%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    ),
                )
                return cursor.rowcount

    def remove_missing(self, seen_path_keys: set[str], scan_complete: bool = False) -> int:
        if not seen_path_keys and not scan_complete:
            return 0
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT path_key FROM documents")
                    missing = [row[0] for row in cursor.fetchall() if row[0] not in seen_path_keys]
                    if missing:
                        cursor.execute("DELETE FROM documents WHERE path_key = ANY(%s)", (missing,))
                    return len(missing)
        with self.connect() as connection:
            rows = connection.execute("SELECT id, path_key FROM documents").fetchall()
            missing = [(row["id"], row["path_key"]) for row in rows if row["path_key"] not in seen_path_keys]
            for row_id, _path_key in missing:
                connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                connection.execute("DELETE FROM documents WHERE id = ?", (row_id,))
            return len(missing)

    def search(self, query: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []

        if offset < 0:
            raise ValueError("offset must be non-negative")
        if self.is_postgres:
            return self._search_postgres(clean_query, limit, offset)
        return self._search_sqlite(clean_query, limit, offset)

    def _search_sqlite(self, clean_query: str, limit: int, offset: int) -> list[dict[str, Any]]:
        fts_query = _build_fts_query(clean_query)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    d.path,
                    d.name,
                    d.parent_path AS parent,
                    d.extension,
                    d.size,
                    d.modified_at AS modified,
                    d.is_dir,
                    d.extraction_status,
                    d.extraction_detail,
                    d.category,
                    snippet(documents_fts, 3, '<mark>', '</mark>', '...', 24) AS snippet,
                    CASE
                        WHEN lower(d.name) = lower(?) THEN 'exact_name'
                        WHEN lower(d.name) LIKE lower(?) THEN 'name'
                        WHEN lower(d.path) LIKE lower(?) THEN 'path'
                        ELSE 'content'
                    END AS match_type,
                    bm25(documents_fts) AS score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ?
                ORDER BY
                    CASE
                        WHEN lower(d.name) = lower(?) THEN 0
                        WHEN lower(d.name) LIKE lower(?) THEN 1
                        WHEN lower(d.path) LIKE lower(?) THEN 2
                        ELSE 3
                    END,
                    score
                LIMIT ? OFFSET ?
                """,
                (
                    clean_query,
                    f"%{clean_query}%",
                    f"%{clean_query}%",
                    fts_query,
                    clean_query,
                    f"%{clean_query}%",
                    f"%{clean_query}%",
                    limit,
                    offset,
                ),
            ).fetchall()

        return [dict(row) for row in rows]

    def _search_postgres(self, clean_query: str, limit: int, offset: int) -> list[dict[str, Any]]:
        like_query = f"%{clean_query}%"
        # Build prefix-matching OR query for Postgres to match SQLite semantics (4.1)
        # e.g. "voile bleue" -> "voile:* | bleue:*" with rank boost for all terms
        terms = [t for t in re.findall(r"[\w]+", clean_query, flags=re.UNICODE) if t.upper() not in {"AND", "OR", "NOT"}]
        if not terms:
            return []
        # OR with prefix — avoid backslash in f-string expression
        def _clean_term(t: str) -> str:
            return re.sub(r"[^\w]", "", t)

        ts_query_or = " | ".join(f"{_clean_term(term)}:*" for term in terms)
        # AND for boost
        ts_query_and = " & ".join(f"{_clean_term(term)}:*" for term in terms)

        with self.connect() as connection:
            import psycopg2.extras

            # Accent-folding configuration when available, else 'simple'. Both
            # the query and the headline must use the SAME configuration as the
            # stored search_vector or nothing matches.
            ts_config = self._postgres_ts_config(connection)
            assert ts_config in PG_TS_CONFIGS, f"unexpected ts config {ts_config!r}"

            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    f"""
                    WITH search AS (
                        SELECT
                            to_tsquery('{ts_config}', %s) AS query_or,
                            to_tsquery('{ts_config}', %s) AS query_and
                    )
                    SELECT
                        d.path,
                        d.name,
                        d.parent_path AS parent,
                        d.extension,
                        d.size,
                        d.modified_at AS modified,
                        d.is_dir,
                        d.extraction_status,
                        d.extraction_detail,
                        d.category,
                        -- Headline the content column only: it is the exact
                        -- text the search_vector was built from (searchable
                        -- text = name + path + extension + text), which is
                        -- also what SQLite headlines via snippet(documents_fts, 3, ...).
                        -- Prepending name/path/extension again duplicated those
                        -- tokens and made the ts_headline fragment selector pick
                        -- a fragment that cut off before the content match,
                        -- silently dropping the matched (accented) words.
                        -- Note: keep this comment free of apostrophes; the
                        -- grammar test walks string literals naively.
                        ts_headline(
                            '{ts_config}',
                            d.content,
                            search.query_or,
                            'StartSel=<mark>, StopSel=</mark>, MaxWords=24, MinWords=8, ShortWord=2'
                        ) AS snippet,
                        CASE
                            WHEN lower(d.name) = lower(%s) THEN 'exact_name'
                            WHEN lower(d.name) LIKE lower(%s) THEN 'name'
                            WHEN lower(d.path) LIKE lower(%s) THEN 'path'
                            ELSE 'content'
                        END AS match_type,
                        -- Rank boost for docs matching all terms
                        ts_rank_cd(d.search_vector, search.query_or) +
                        CASE WHEN d.search_vector @@ search.query_and THEN 0.5 ELSE 0 END AS score
                    FROM documents d, search
                    WHERE d.search_vector @@ search.query_or
                    ORDER BY
                        CASE
                            WHEN lower(d.name) = lower(%s) THEN 0
                            WHEN lower(d.name) LIKE lower(%s) THEN 1
                            WHEN lower(d.path) LIKE lower(%s) THEN 2
                            ELSE 3
                        END,
                        score DESC
                    LIMIT %s OFFSET %s
                    """,
                    (
                        ts_query_or,
                        ts_query_and,
                        clean_query,
                        like_query,
                        like_query,
                        clean_query,
                        like_query,
                        like_query,
                        limit,
                        offset,
                    ),
                )
                return [dict(row) for row in cursor.fetchall()]

    def stats(self) -> IndexStats:
        if self.is_postgres:
            with self.connect() as connection:
                import psycopg2.extras

                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        """
                        SELECT
                            COUNT(*) AS total_documents,
                            SUM(CASE WHEN is_dir = false THEN 1 ELSE 0 END) AS files,
                            SUM(CASE WHEN is_dir = true THEN 1 ELSE 0 END) AS folders
                        FROM documents
                        """
                    )
                    row = cursor.fetchone()
            return IndexStats(
                total_documents=int(row["total_documents"] or 0),
                files=int(row["files"] or 0),
                folders=int(row["folders"] or 0),
            )
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_documents,
                    SUM(CASE WHEN is_dir = 0 THEN 1 ELSE 0 END) AS files,
                    SUM(CASE WHEN is_dir = 1 THEN 1 ELSE 0 END) AS folders
                FROM documents
                """
            ).fetchone()
        return IndexStats(
            total_documents=int(row["total_documents"] or 0),
            files=int(row["files"] or 0),
            folders=int(row["folders"] or 0),
        )

    def health_details(self) -> dict[str, Any]:
        if self.is_postgres:
            with self.connect() as connection:
                import psycopg2.extras

                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute("SELECT version() AS version")
                    version = cursor.fetchone()["version"]
                    cursor.execute("SELECT pg_database_size(current_database()) AS database_bytes")
                    database_bytes = int(cursor.fetchone()["database_bytes"])
                    # Real integrity check: verify documents table exists and indexes are valid (4.2)
                    try:
                        cursor.execute("SELECT COUNT(*) AS c FROM documents")
                        doc_count = cursor.fetchone()["c"]
                        cursor.execute(
                            "SELECT indexname FROM pg_indexes WHERE tablename = 'documents' AND schemaname = 'public'"
                        )
                        indexes = cursor.fetchall()
                        integrity = "ok" if doc_count >= 0 and len(indexes) >= 1 else "degraded"
                        # Check for invalid indexes
                        cursor.execute(
                            "SELECT COUNT(*) AS invalid FROM pg_index WHERE NOT indisvalid"
                        )
                        invalid = cursor.fetchone()["invalid"]
                        if invalid > 0:
                            integrity = f"invalid_indexes:{invalid}"
                    except Exception as exc:
                        integrity = f"check_failed:{exc}"

            # Diagnostic Lot A : version de schéma appliquée + présence
            # EFFECTIVE des extensions (pg_extension, pas la configuration).
            metier: dict[str, Any] = {"schema_migrations": [], "schema_metier_a_jour": False, "extensions": {}}
            try:
                with connection.cursor() as cursor:
                    metier = schema_metier.diagnostic_metier(cursor)
            except Exception as exc:
                # Conséquence : /health reste répondant, mais signale que le
                # diagnostic métier est indisponible (base non migrée ?).
                logger.warning("Diagnostic schéma métier indisponible dans /health : %s", exc)
                metier = {"diagnostic": "indisponible", **metier}

            return {
                "backend": "postgresql",
                "database_url_configured": True,
                "database_bytes": database_bytes,
                "database_integrity": integrity,
                "version": version,
                "schema_migrations": metier.get("schema_migrations", []),
                "schema_metier_a_jour": metier.get("schema_metier_a_jour", False),
                "extensions": metier.get("extensions", {}),
            }

        with self.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            versions = [row["version"] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()]
        return {
            "backend": "sqlite",
            "database_path": str(self.database_path),
            "database_exists": self.database_path.exists(),
            "database_bytes": self.database_path.stat().st_size if self.database_path.exists() else 0,
            "database_integrity": integrity,
            "schema_migrations": versions,
            # Couche métier PostgreSQL uniquement (§17.1) : extensions non applicables.
            "schema_metier_a_jour": False,
            "extensions": {"vector": False, "pg_trgm": False, "unaccent": False, "applicables": False},
        }


def _document_values(
    document: Document,
) -> tuple[str, str, str, str, int, float, int, int, str, str, str, str, str | None, str | None, float | None, str]:
    return (
        str(document.path),
        document.name,
        str(document.parent_path),
        document.extension,
        document.size,
        document.modified_at,
        1 if document.is_dir else 0,
        document.extractor_version,
        document.content_hash,
        document.extraction_status,
        document.extraction_detail,
        document.category,
        document.object_key,
        document.object_bucket,
        document.uploaded_at,
        document.upload_status,
    )


def _build_fts_query(query: str) -> str:
    # FTS5 syntax is not exposed to users; tokenize input and quote every token.
    terms = [term for term in re.findall(r"[\w]+", query, flags=re.UNICODE) if term.upper() not in {"AND", "OR", "NOT"}]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"*' for term in terms)
