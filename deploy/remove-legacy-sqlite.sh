#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SOURCE_PATH="backend/work/jobs.sqlite3"
CONFIRMATION=""

usage() {
    cat <<'EOF'
Usage:
  bash deploy/remove-legacy-sqlite.sh --confirm DELETE_SQLITE [--source PATH]

Permanently removes the legacy SQLite database and its WAL/SHM sidecars only
after verifying that Docker Compose is configured for PostgreSQL and the target
database contains the core VidTrans tables.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)
            [[ $# -ge 2 ]] || { printf '%s\n' "--source requires a path" >&2; exit 2; }
            SOURCE_PATH="$2"
            shift 2
            ;;
        --confirm)
            [[ $# -ge 2 ]] || { printf '%s\n' "--confirm requires DELETE_SQLITE" >&2; exit 2; }
            CONFIRMATION="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$CONFIRMATION" != "DELETE_SQLITE" ]]; then
    printf '%s\n' "Refusing deletion: pass --confirm DELETE_SQLITE" >&2
    exit 2
fi

case "$SOURCE_PATH" in
    /*) source_absolute="$SOURCE_PATH" ;;
    *) source_absolute="$ROOT_DIR/$SOURCE_PATH" ;;
esac

source_directory="$(cd "$(dirname "$source_absolute")" 2>/dev/null && pwd -P)" || {
    printf 'Source directory does not exist: %s\n' "$(dirname "$source_absolute")" >&2
    exit 2
}
source_absolute="$source_directory/$(basename "$source_absolute")"
work_root="$(cd "$ROOT_DIR/backend/work" && pwd -P)"
case "$source_absolute" in
    "$work_root"/*) ;;
    *)
        printf 'Refusing to delete outside backend/work: %s\n' "$source_absolute" >&2
        exit 2
        ;;
esac

if [[ ! -f "$source_absolute" ]]; then
    printf 'Legacy SQLite database is already absent: %s\n' "$source_absolute"
    exit 0
fi

database_url="$(docker compose run --rm --no-deps --entrypoint python vidtrans -c '
from infrastructure.database import database_target
from pathlib import Path
print(database_target(Path("work/jobs.sqlite3")))
')"
if [[ "$database_url" != postgresql://* && "$database_url" != postgres://* ]]; then
    printf '%s\n' "Refusing deletion: vidtrans is not configured with a PostgreSQL URL" >&2
    exit 1
fi

docker compose run --rm --no-deps --entrypoint python vidtrans -c '
from infrastructure.database import connect
import os

required = {"admin_accounts", "jobs", "batches", "workflow_events"}
db = connect(os.environ["VIDTRANS_DATABASE_URL"])
try:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=current_schema()"
    ).fetchall()
    present = {str(row["table_name"]) for row in rows}
    missing = sorted(required - present)
    if missing:
        raise SystemExit("PostgreSQL is missing required tables: " + ", ".join(missing))
    db.execute("SELECT COUNT(*) FROM admin_accounts").fetchone()
    db.execute("SELECT COUNT(*) FROM jobs").fetchone()
finally:
    db.close()
print("PostgreSQL verification passed")
'

targets=("$source_absolute" "$source_absolute-wal" "$source_absolute-shm" "$source_absolute-journal")
removed=0
for target in "${targets[@]}"; do
    if [[ -f "$target" ]]; then
        rm -- "$target"
        printf 'Removed: %s\n' "$target"
        removed=$((removed + 1))
    fi
done

printf 'Legacy SQLite cleanup complete; removed %d file(s).\n' "$removed"
