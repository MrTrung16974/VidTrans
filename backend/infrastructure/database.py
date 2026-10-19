from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


DatabaseTarget = str | Path
IntegrityError = sqlite3.IntegrityError


def database_target(default_path: Path) -> DatabaseTarget:
    """Return the configured PostgreSQL DSN, with SQLite kept for local tests.

    Production/container deployments set ``VIDTRANS_DATABASE_URL``.  Accepting a
    path is intentionally retained so the fast, isolated repository unit tests
    do not need a running PostgreSQL server.
    """

    value = os.environ.get("VIDTRANS_DATABASE_URL", "").strip()
    return value or default_path


def prepare_target(target: DatabaseTarget) -> DatabaseTarget:
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    if target.startswith("postgres://"):
        return "postgresql://" + target.removeprefix("postgres://")
    return target


class HybridRow:
    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._mapping = dict(zip(self._columns, self._values))

    def __getitem__(self, key: str | int) -> Any:
        return self._values[key] if isinstance(key, int) else self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self):
        return self._mapping.keys()


class Cursor:
    def __init__(self, cursor: Any = None, rows: list[HybridRow] | None = None) -> None:
        self._cursor = cursor
        self._rows = rows
        self._offset = 0

    @property
    def rowcount(self) -> int:
        if self._cursor is not None:
            return int(self._cursor.rowcount)
        return len(self._rows or [])

    def _row(self, values: Sequence[Any]) -> HybridRow:
        columns = [column.name for column in self._cursor.description]
        return HybridRow(columns, values)

    def fetchone(self) -> HybridRow | None:
        if self._rows is not None:
            if self._offset >= len(self._rows):
                return None
            row = self._rows[self._offset]
            self._offset += 1
            return row
        values = self._cursor.fetchone()
        return self._row(values) if values is not None else None

    def fetchall(self) -> list[HybridRow]:
        if self._rows is not None:
            rows = self._rows[self._offset :]
            self._offset = len(self._rows)
            return rows
        return [self._row(values) for values in self._cursor.fetchall()]


class PostgresConnection:
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError("PostgreSQL requires psycopg; install requirements.txt") from exc
        self._psycopg = psycopg
        self._connection = psycopg.connect(dsn, connect_timeout=30)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    @staticmethod
    def _translate(sql: str) -> str | None:
        normalized = sql.strip()
        upper = normalized.upper()
        if upper.startswith("PRAGMA "):
            return None
        if upper.startswith("BEGIN IMMEDIATE"):
            return "BEGIN"
        if upper.startswith("CREATE TRIGGER"):
            # SQLite trigger bodies are replaced by PostgreSQL constraints and
            # repository validation; they cannot be sent to PostgreSQL verbatim.
            return None
        sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\s+COLLATE\s+NOCASE", "", sql, flags=re.IGNORECASE)
        if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", sql, re.IGNORECASE):
            sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.IGNORECASE)
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Cursor:
        pragma = re.match(r"\s*PRAGMA\s+table_info\(([^)]+)\)", sql, re.IGNORECASE)
        if pragma:
            cursor = self._connection.cursor()
            cursor.execute(
                "SELECT column_name,data_type,is_nullable,column_default "
                "FROM information_schema.columns WHERE table_schema=current_schema() "
                "AND table_name=%s ORDER BY ordinal_position",
                (pragma.group(1).strip(" '\""),),
            )
            rows = [
                HybridRow(
                    ("cid", "name", "type", "notnull", "dflt_value", "pk"),
                    (index, row[0], row[1], int(row[2] == "NO"), row[3], 0),
                )
                for index, row in enumerate(cursor.fetchall())
            ]
            return Cursor(rows=rows)
        if "sqlite_master" in sql:
            sql = re.sub(
                r"SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*['\"]table['\"]\s+AND\s+name\s*=\s*\?",
                "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?",
                sql,
                flags=re.IGNORECASE,
            )
            sql = re.sub(
                r"SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*['\"]table['\"]\s+AND\s+name\s*=\s*['\"]([^'\"]+)['\"]",
                r"SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='\1'",
                sql,
                flags=re.IGNORECASE,
            )
        translated = self._translate(sql)
        if translated is None:
            return Cursor(rows=[])
        cursor = self._connection.cursor()
        try:
            cursor.execute(translated, tuple(params or ()))
        except self._psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        return Cursor(cursor)

    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> Cursor:
        translated = self._translate(sql)
        if translated is None:
            return Cursor(rows=[])
        cursor = self._connection.cursor()
        try:
            cursor.executemany(translated, params)
        except self._psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        return Cursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect(target: DatabaseTarget):
    target = prepare_target(target)
    if isinstance(target, Path):
        connection = sqlite3.connect(target, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection
    if not str(target).startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("VIDTRANS_DATABASE_URL must be a PostgreSQL URL")
    return PostgresConnection(str(target).replace("postgresql+psycopg://", "postgresql://", 1))
