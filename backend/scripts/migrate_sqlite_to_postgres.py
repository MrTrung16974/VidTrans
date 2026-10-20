from __future__ import annotations

import argparse
import os
import re
import sqlite3
from pathlib import Path

from infrastructure.database import connect, database_target


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLE_PRIORITY = {
    "admin_accounts": 0,
    "batches": 1,
    "jobs": 2,
    "social_account_connections": 3,
    "social_oauth_sessions": 4,
    "social_connection_audit_events": 5,
}
USER_ID_COLUMNS = {"user_id", "owner_user_id", "actor_user_id", "target_user_id"}


def _create_if_missing(sql: str) -> str:
    """Make SQLite schema DDL safe when the app already initialized PostgreSQL."""

    if re.match(r"^\s*CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)", sql, re.IGNORECASE):
        return re.sub(
            r"^(\s*CREATE\s+TABLE\s+)",
            r"\1IF NOT EXISTS ",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
    if re.match(r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS)", sql, re.IGNORECASE):
        return re.sub(
            r"^(\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+)",
            r"\1IF NOT EXISTS ",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
    return sql


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQLite identifier: {value!r}")
    return value


def migrate(source_path: Path, target_dsn: str) -> dict[str, int]:
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not target_dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("Target must be a PostgreSQL URL")

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = connect(target_dsn)
    copied: dict[str, int] = {}
    try:
        table_rows = source.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
        tables = sorted(
            ((str(row["name"]), str(row["sql"])) for row in table_rows),
            key=lambda item: (TABLE_PRIORITY.get(item[0], 100), item[0]),
        )
        for table, create_sql in tables:
            _identifier(table)
            target.execute(_create_if_missing(create_sql))

        index_rows = source.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
        for row in index_rows:
            target.execute(_create_if_missing(str(row["sql"])))

        user_id_map: dict[str, str] = {}
        source_tables = {table for table, _ in tables}
        if "admin_accounts" in source_tables:
            for account in source.execute(
                "SELECT user_id,email FROM admin_accounts"
            ).fetchall():
                old_user_id = str(account["user_id"])
                existing = target.execute(
                    "SELECT user_id FROM admin_accounts WHERE LOWER(email)=LOWER(?)",
                    (str(account["email"]),),
                ).fetchone()
                user_id_map[old_user_id] = (
                    str(existing["user_id"]) if existing is not None else old_user_id
                )

        for table, _ in tables:
            columns = [
                _identifier(str(row["name"]))
                for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            if not columns:
                continue
            placeholders = ",".join("?" for _ in columns)
            column_sql = ",".join(columns)
            rows = source.execute(f'SELECT {column_sql} FROM "{table}"').fetchall()
            if rows:
                values = []
                for row in rows:
                    migrated_row = []
                    for column in columns:
                        value = row[column]
                        if column in USER_ID_COLUMNS and value is not None:
                            value = user_id_map.get(str(value), value)
                        migrated_row.append(value)
                    values.append(tuple(migrated_row))
                target.executemany(
                    f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    values,
                )
            copied[table] = len(rows)
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()
        source.close()
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a VidTrans SQLite database to PostgreSQL")
    parser.add_argument("--source", type=Path, required=True, help="Path to jobs.sqlite3")
    parser.add_argument(
        "--target",
        default=os.environ.get("VIDTRANS_DATABASE_URL", ""),
        help="PostgreSQL URL (defaults to VIDTRANS_DATABASE_URL)",
    )
    args = parser.parse_args()
    target = args.target or database_target(Path("work/jobs.sqlite3"))
    if isinstance(target, Path):
        parser.error("--target, VIDTRANS_DATABASE_URL, or VIDTRANS_POSTGRES_HOST is required")
    result = migrate(args.source.resolve(), target)
    for table, count in result.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
