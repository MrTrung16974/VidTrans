#!/usr/bin/env bash

# One-time production setup for a Linux VPS. Subsequent deployments reuse the generated files.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STATE_FILE="deploy/.vps-configured"
PRODUCTION_ENV=".env.production"
APP_ENV=".env"
RUNTIME_DIR="deploy/nginx/runtime"
RUNTIME_TEMPLATE="$RUNTIME_DIR/default.conf.template"
FORCE_SETUP=0

if [[ "${1:-}" == "--force" ]]; then
    FORCE_SETUP=1
elif [[ -n "${1:-}" ]]; then
    printf 'Unknown option: %s\n' "$1" >&2
    exit 2
fi

if [[ -f "$STATE_FILE" && "$FORCE_SETUP" -eq 0 ]]; then
    if [[ -s "$APP_ENV" && -s "$PRODUCTION_ENV" && -s "$RUNTIME_TEMPLATE" ]]; then
        printf '%s\n' "VPS/Nginx was configured previously; keeping the existing domain and secrets."
        exit 0
    fi
    printf '%s\n' "The VPS marker exists but generated configuration is incomplete; repairing setup."
fi

if [[ "$(uname -s)" != "Linux" ]]; then
    printf '%s\n' "VPS setup must run on the target Linux server, not on the development machine." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' "Docker Engine with the Compose plugin is required." >&2
    exit 1
fi
if ! docker image inspect vidtrans-vidtrans >/dev/null 2>&1; then
    printf '%s\n' "Build vidtrans-vidtrans before running VPS setup." >&2
    exit 1
fi

read_env_value() {
    local file="$1"
    local key="$2"
    local value=""
    if [[ -f "$file" ]]; then
        value="$(awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, index($0, "=") + 1); exit }' "$file")"
        if [[ "$value" == \'*\' || "$value" == \"*\" ]]; then
            value="${value:1:${#value}-2}"
        fi
    fi
    printf '%s' "$value"
}

prompt_required() {
    local variable_name="$1"
    local prompt="$2"
    local current_value="${!variable_name:-}"
    if [[ -z "$current_value" ]]; then
        if [[ ! -t 0 ]]; then
            printf 'Missing %s in a non-interactive setup.\n' "$variable_name" >&2
            exit 1
        fi
        read -r -p "$prompt" current_value
    fi
    printf -v "$variable_name" '%s' "$current_value"
}

existing_public_host="$(read_env_value "$PRODUCTION_ENV" VIDTRANS_PUBLIC_HOST)"
existing_email="$(read_env_value "$PRODUCTION_ENV" VIDTRANS_LETSENCRYPT_EMAIL)"
existing_username="$(read_env_value "$APP_ENV" VIDTRANS_AUTH_USERNAME)"
existing_password_hash="$(read_env_value "$APP_ENV" VIDTRANS_AUTH_PASSWORD_HASH)"
existing_jwt_secret="$(read_env_value "$APP_ENV" VIDTRANS_JWT_SECRET)"
existing_auth_enabled="$(read_env_value "$APP_ENV" VIDTRANS_AUTH_ENABLED)"

public_host="${VIDTRANS_SETUP_DOMAIN:-$existing_public_host}"
letsencrypt_email="${VIDTRANS_SETUP_EMAIL:-$existing_email}"
admin_username="${VIDTRANS_SETUP_ADMIN_USERNAME:-${existing_username:-admin}}"
admin_password="${VIDTRANS_SETUP_ADMIN_PASSWORD:-}"
unset VIDTRANS_SETUP_ADMIN_PASSWORD
reuse_existing_auth=0
if [[ "$FORCE_SETUP" -eq 0 && -z "$admin_password" && "$existing_auth_enabled" == "1" \
    && "$existing_password_hash" == pbkdf2_sha256\$* && ${#existing_jwt_secret} -ge 32 ]]; then
    reuse_existing_auth=1
fi

prompt_required public_host "Public domain or IPv4 of this VPS (example: video.example.com): "
prompt_required letsencrypt_email "Email for Let's Encrypt expiry notices: "
if [[ -t 0 && "$reuse_existing_auth" -eq 0 ]]; then
    read -r -p "VidTrans admin username [$admin_username]: " entered_username
    admin_username="${entered_username:-$admin_username}"
fi
if [[ "$reuse_existing_auth" -eq 1 ]]; then
    printf '%s\n' "Reusing the existing admin password hash and JWT secret from the interrupted setup."
elif [[ -z "$admin_password" ]]; then
    if [[ ! -t 0 ]]; then
        printf '%s\n' "Missing VIDTRANS_SETUP_ADMIN_PASSWORD in a non-interactive setup." >&2
        exit 1
    fi
    read -r -s -p "VidTrans admin password (minimum 12 characters): " admin_password
    printf '\n'
    read -r -s -p "Repeat the admin password: " password_confirmation
    printf '\n'
    if [[ "$admin_password" != "$password_confirmation" ]]; then
        printf '%s\n' "Passwords do not match." >&2
        exit 1
    fi
fi

public_host="$(printf '%s' "$public_host" | tr '[:upper:]' '[:lower:]')"
host_type="domain"
if [[ "$public_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    host_type="ipv4"
    IFS='.' read -r -a address_octets <<< "$public_host"
    for octet in "${address_octets[@]}"; do
        if (( ${#octet} > 3 )) || [[ "$octet" != "0" && "$octet" == 0* ]] || (( 10#$octet > 255 )); then
            printf '%s\n' "The public IPv4 address is invalid." >&2
            exit 1
        fi
    done
elif [[ ! "$public_host" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; then
    printf '%s\n' "Enter a domain or public IPv4 without http://, https://, a port, or a path." >&2
    exit 1
fi
if [[ ! "$letsencrypt_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
    printf '%s\n' "The Let's Encrypt email address is invalid." >&2
    exit 1
fi
if [[ ! "$admin_username" =~ ^[A-Za-z0-9_.-]{3,64}$ ]]; then
    printf '%s\n' "The admin username must contain 3-64 letters, digits, dot, underscore, or dash." >&2
    exit 1
fi
if [[ "$reuse_existing_auth" -eq 0 ]] && (( ${#admin_password} < 12 )); then
    printf '%s\n' "The admin password must contain at least 12 characters." >&2
    exit 1
fi

update_env_line() {
    local file="$1"
    local key="$2"
    local replacement="$3"
    local temporary
    temporary="$(mktemp "${file}.XXXXXX")"
    awk -v key="$key" -v replacement="$replacement" '
        BEGIN { replaced = 0 }
        index($0, key "=") == 1 {
            if (!replaced) print replacement
            replaced = 1
            next
        }
        { print }
        END { if (!replaced) print replacement }
    ' "$file" > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$file"
}

touch "$APP_ENV"
chmod 600 "$APP_ENV"
if [[ "$reuse_existing_auth" -eq 1 ]]; then
    password_hash="$existing_password_hash"
    jwt_secret="$existing_jwt_secret"
else
    password_hash="$(printf '%s' "$admin_password" | docker run --rm -i --entrypoint python vidtrans-vidtrans -c 'import sys; from infrastructure.auth import hash_password; print(hash_password(sys.stdin.read()))')"
    jwt_secret="$(docker run --rm --entrypoint python vidtrans-vidtrans -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi
unset admin_password password_confirmation

update_env_line "$APP_ENV" VIDTRANS_AUTH_ENABLED "VIDTRANS_AUTH_ENABLED=1"
update_env_line "$APP_ENV" VIDTRANS_AUTH_USERNAME "VIDTRANS_AUTH_USERNAME=$admin_username"
update_env_line "$APP_ENV" VIDTRANS_AUTH_PASSWORD_HASH "VIDTRANS_AUTH_PASSWORD_HASH='$password_hash'"
update_env_line "$APP_ENV" VIDTRANS_JWT_SECRET "VIDTRANS_JWT_SECRET=$jwt_secret"
update_env_line "$APP_ENV" VIDTRANS_AUTH_COOKIE_SECURE "VIDTRANS_AUTH_COOKIE_SECURE=1"

touch "$PRODUCTION_ENV"
chmod 600 "$PRODUCTION_ENV"
update_env_line "$PRODUCTION_ENV" VIDTRANS_PUBLIC_HOST "VIDTRANS_PUBLIC_HOST=$public_host"
update_env_line "$PRODUCTION_ENV" VIDTRANS_LETSENCRYPT_EMAIL "VIDTRANS_LETSENCRYPT_EMAIL=$letsencrypt_email"
update_env_line "$PRODUCTION_ENV" VIDTRANS_AUTH_COOKIE_SECURE "VIDTRANS_AUTH_COOKIE_SECURE=1"

mkdir -p "$RUNTIME_DIR"
cp deploy/nginx/http.conf.template "$RUNTIME_TEMPLATE"

compose=(
    docker compose
    --env-file "$APP_ENV"
    --env-file "$PRODUCTION_ENV"
    -f docker-compose.yml
    -f docker-compose.production.yml
)

printf '%s\n' "Starting the private app and HTTP-only ACME bootstrap..."
"${compose[@]}" up -d --build --force-recreate vidtrans nginx

nginx_ready=0
for attempt in {1..30}; do
    if "${compose[@]}" exec -T nginx wget -qO- http://127.0.0.1/nginx-health >/dev/null 2>&1; then
        nginx_ready=1
        break
    fi
    sleep 2
done
if [[ "$nginx_ready" -ne 1 ]]; then
    printf '%s\n' "Nginx did not become healthy. Check: docker compose logs nginx" >&2
    exit 1
fi

printf '%s\n' "Requesting the first Let's Encrypt certificate for $public_host..."
certificate_identifier=(--domain "$public_host")
if [[ "$host_type" == "ipv4" ]]; then
    certificate_identifier=(--ip-address "$public_host" --preferred-profile shortlived)
fi
if ! "${compose[@]}" run --rm --no-deps --entrypoint certbot certbot \
    certonly --webroot --webroot-path /var/www/certbot \
    "${certificate_identifier[@]}" --email "$letsencrypt_email" \
    --agree-tos --no-eff-email --non-interactive; then
    printf '%s\n' "Certificate request failed. Verify DNS when using a domain and inbound TCP ports 80/443, then rerun with --production." >&2
    exit 1
fi

cp deploy/nginx/https.conf.template "$RUNTIME_TEMPLATE"
"${compose[@]}" up -d --force-recreate nginx certbot
printf 'VIDTRANS_PUBLIC_HOST=%s\nCONFIGURED_AT=%s\n' "$public_host" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_FILE"
chmod 600 "$STATE_FILE"

printf '%s\n' "VPS setup completed: https://$public_host"
printf '%s\n' "Future deployments only need: bash docker-rebuild.sh"
