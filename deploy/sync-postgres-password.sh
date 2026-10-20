#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

read_env_value() {
    local key="$1"
    local value
    value="$(awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' .env)"
    if [[ "$value" == \'*\' || "$value" == \"*\" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

[[ -f .env ]] || { printf '%s\n' "Missing .env" >&2; exit 1; }

database_user="$(read_env_value VIDTRANS_POSTGRES_USER)"
database_user="${database_user:-vidtrans}"
database_password="$(read_env_value VIDTRANS_POSTGRES_PASSWORD)"

[[ "$database_user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    printf '%s\n' "Invalid VIDTRANS_POSTGRES_USER" >&2
    exit 1
}
[[ -n "$database_password" ]] || {
    printf '%s\n' "VIDTRANS_POSTGRES_PASSWORD is empty" >&2
    exit 1
}
[[ "$database_password" != *$'\n'* && "$database_password" != *$'\r'* ]] || {
    printf '%s\n' "PostgreSQL password cannot contain newlines" >&2
    exit 1
}

escaped_password="${database_password//\'/\'\'}"
printf "ALTER ROLE \"%s\" WITH PASSWORD '%s';\n" "$database_user" "$escaped_password" \
    | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres

docker compose up -d --force-recreate vidtrans
docker compose run --rm --no-deps --entrypoint python vidtrans -c '
import os
from infrastructure.database import connect, database_target
from pathlib import Path
db = connect(database_target(Path("work/jobs.sqlite3")))
try:
    row = db.execute("SELECT current_database(),current_user").fetchone()
    print(f"Connected to PostgreSQL database={row[0]} user={row[1]}")
finally:
    db.close()
'

printf '%s\n' "PostgreSQL password synchronized and VidTrans recreated."
