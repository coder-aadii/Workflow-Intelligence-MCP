"""Thread-safe DuckDB adapter for deterministic analytical metric computation.

Provides:
- Thread-local DuckDB connections (DuckDB connections carry thread affinity, so
  each thread gets its own connection) with lazy, per-connection view
  registration, making the adapter safe to share across threads.
- Safe parameterized query execution with an optional execution time limit.
- Read-only registration of local CSV / Parquet files as tables/views.
- File hashing and size utilities for data provenance footnotes.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from workflow_intelligence.security.file_validator import (
    SecurityError,
    validate_safe_path,
)


class DuckDBAdapterError(Exception):
    """Raised when DuckDB adapter operations fail."""


class DuckDBAdapterTimeoutError(DuckDBAdapterError):
    """Raised when a DuckDB query exceeds the configured execution time limit."""


class DuckDBAdapter:
    """Thread-safe DuckDB connection manager.

    Because a DuckDB connection is bound to the thread that created it, this
    adapter keeps a thread-local connection for every calling thread and
    replicates registered CSV / Parquet views onto each connection on first
    use. All shared catalog mutations are guarded by a lock.

    Attributes:
        database: DuckDB database path or ``":memory:"`` for in-memory.
        read_only: Open databases in read-only mode.
        query_timeout_ms: Default query execution timeout in milliseconds.
    """

    def __init__(
        self,
        database: str = ":memory:",
        read_only: bool = False,
        query_timeout_ms: int | None = None,
    ) -> None:
        self._database = database
        self._read_only = read_only
        self._lock = threading.Lock()
        self._local = threading.local()
        # table_name -> (kind, path) for views replicated to every connection.
        self._registered_views: dict[str, tuple[str, str]] = {}
        if query_timeout_ms is not None:
            self._query_timeout_ms = query_timeout_ms
        else:
            try:
                from workflow_intelligence.config import get_settings

                self._query_timeout_ms = get_settings().query_timeout_ms
            except Exception:
                self._query_timeout_ms = 30000

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Return the current thread's DuckDB connection, registering views."""
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = duckdb.connect(database=self._database, read_only=self._read_only)
            self._local.connection = conn
            with self._lock:
                views = dict(self._registered_views)
            for table_name, (kind, path) in views.items():
                safe_table = self._sanitize_table_name(table_name)
                escaped_path = self._escape_sql_string(path)
                if kind == "csv":
                    ddl = (
                        f"CREATE OR REPLACE VIEW {safe_table} AS "
                        f"SELECT * FROM read_csv_auto('{escaped_path}')"
                    )
                else:
                    ddl = (
                        f"CREATE OR REPLACE VIEW {safe_table} AS "
                        f"SELECT * FROM read_parquet('{escaped_path}')"
                    )
                self._execute_ddl_on(conn, ddl)
        return conn

    def close(self) -> None:
        """Close the current thread's DuckDB connection if one is open."""
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - best effort close
                pass
            self._local.connection = None

    @contextmanager
    def _timed_session(
        self, time_limit_ms: int | None = None
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        """Yield the connection with reliable query cancellation via interrupt."""
        conn = self.connection
        limit_ms = self._query_timeout_ms if time_limit_ms is None else time_limit_ms
        timeout_sec = (limit_ms / 1000.0) if (limit_ms and limit_ms > 0) else None
        timer: threading.Timer | None = None
        if timeout_sec is not None:
            timer = threading.Timer(timeout_sec, conn.interrupt)
            timer.daemon = True
            timer.start()
        try:
            yield conn
        except duckdb.InterruptException as exc:
            raise DuckDBAdapterTimeoutError(
                f"DuckDB query exceeded execution time limit of {limit_ms}ms"
            ) from exc
        finally:
            if timer is not None:
                timer.cancel()

    def execute_query(
        self,
        sql: str,
        parameters: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None,
        time_limit_ms: int | None = None,
    ) -> list[tuple[Any, ...]]:
        """Execute a parameterized query and return all result rows.

        Args:
            sql: SQL statement using ``?`` positional or ``$name`` named
                placeholders. Identifiers are never interpolated from user
                input.
            parameters: Query parameters bound through DuckDB's parameter API.
            time_limit_ms: Maximum statement execution time in milliseconds
                (defaults to configured QUERY_TIMEOUT_MS).

        Returns:
            List of result rows, each row as a tuple of column values.

        Raises:
            DuckDBAdapterTimeoutError: If execution exceeds the time limit.
            DuckDBAdapterError: If the query cannot be executed.
        """
        try:
            with self._timed_session(time_limit_ms=time_limit_ms) as conn:
                if parameters is None:
                    cursor = conn.execute(sql)
                else:
                    cursor = conn.execute(sql, parameters)
                return cursor.fetchall()
        except DuckDBAdapterTimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap adapter errors
            raise DuckDBAdapterError("DuckDB query failed: " + str(exc)) from exc

    def execute_query_df(
        self,
        sql: str,
        parameters: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None,
        time_limit_ms: int | None = None,
    ) -> Any:
        """Execute a parameterized query returning a pandas DataFrame."""

        try:
            with self._timed_session(time_limit_ms=time_limit_ms) as conn:
                if parameters is None:
                    return conn.execute(sql).df()
                return conn.execute(sql, parameters).df()
        except DuckDBAdapterTimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap adapter errors
            raise DuckDBAdapterError("DuckDB query failed: " + str(exc)) from exc

    def _execute_ddl_on(self, conn: duckdb.DuckDBPyConnection, sql: str) -> None:
        """Execute a DDL statement on a specific connection."""
        try:
            conn.execute(sql)
        except Exception as exc:  # noqa: BLE001 - wrap adapter errors
            raise DuckDBAdapterError("DuckDB DDL failed: " + str(exc)) from exc

    def _sanitize_table_name(self, table_name: str) -> str:
        """Return a SQL-safe identifier derived from a table name."""
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in table_name)
        if not safe or safe[0].isdigit():
            safe = "t_" + safe
        return safe

    def _escape_sql_string(self, value: str) -> str:
        """Escape a string for safe embedding as a SQL single-quoted literal."""
        return value.replace("'", "''")

    def register_csv(self, table_name: str, path: str | Path) -> None:
        """Register a local CSV file as a read-only DuckDB view.

        The view is replicated to every thread-local connection on first use.
        File path is validated against directory traversal and allowlisted directories.

        Args:
            table_name: Logical name to expose the data under.
            path: Filesystem path to the CSV file.

        Raises:
            DuckDBAdapterError: If security validation fails or registration fails.
        """
        try:
            safe_path = validate_safe_path(str(path), supported_extensions={".csv"})
        except SecurityError as exc:
            raise DuckDBAdapterError(
                f"Security validation failed for CSV file '{path}': {exc}"
            ) from exc

        resolved = str(safe_path)
        safe_table = self._sanitize_table_name(table_name)
        with self._lock:
            self._registered_views[safe_table] = ("csv", resolved)
        self._execute_ddl_on(
            self.connection,
            f"CREATE OR REPLACE VIEW {safe_table} AS "
            f"SELECT * FROM read_csv_auto('{self._escape_sql_string(resolved)}')",
        )

    def register_parquet(self, table_name: str, path: str | Path) -> None:
        """Register a local Parquet file as a read-only DuckDB view."""
        try:
            safe_path = validate_safe_path(
                str(path), supported_extensions={".parquet", ".pq"}
            )
        except SecurityError as exc:
            raise DuckDBAdapterError(
                f"Security validation failed for Parquet file '{path}': {exc}"
            ) from exc

        resolved = str(safe_path)
        safe_table = self._sanitize_table_name(table_name)
        with self._lock:
            self._registered_views[safe_table] = ("parquet", resolved)
        self._execute_ddl_on(
            self.connection,
            f"CREATE OR REPLACE VIEW {safe_table} AS "
            f"SELECT * FROM read_parquet('{self._escape_sql_string(resolved)}')",
        )

    def table_exists(self, table_name: str) -> bool:
        """Return True when a table or view with the given name exists."""
        rows = self.execute_query(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            parameters=[table_name],
            time_limit_ms=1000,
        )
        return bool(rows and rows[0][0] > 0)

    def table_row_count(self, table_name: str) -> int:
        """Return the number of rows in a table or view."""
        rows = self.execute_query(
            f"SELECT count(*) FROM {self._sanitize_table_name(table_name)}",
            time_limit_ms=1000,
        )
        return int(rows[0][0]) if rows else 0

    @staticmethod
    def file_hash(path: str | Path) -> str:
        """Return the SHA-256 hex digest of a file for provenance tracking."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise DuckDBAdapterError(f"File not found for hashing: {resolved}")
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def file_size(path: str | Path) -> int:
        """Return the size of a file in bytes."""
        return Path(path).expanduser().resolve().stat().st_size
