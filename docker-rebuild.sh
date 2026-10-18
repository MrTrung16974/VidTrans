#!/usr/bin/env bash

# Rebuild VidTrans code without entering apt/pip dependency layers.
set -Eeuo pipefail

cd "$(dirname "$0")"

APP_IMAGE="vidtrans-vidtrans"
DEPS_IMAGE="vidtrans-deps:local"
FULL_REBUILD=0
PRODUCTION=0
FORCE_LOCAL=0
CORPORATE_CA=0

# Older setup versions wrote PBKDF2 hashes with raw dollar signs. Docker
# Compose treats those as variable references (for example $600000 and the
# salt), truncating the password hash passed to the application. Repair that
# line in place while leaving every other secret untouched.
escape_password_hash_for_compose() {
    local file="$1"
    local line value escaped temporary changed=0
    [[ -f "$file" ]] || return 0
    temporary="$(mktemp "${file}.XXXXXX")"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == VIDTRANS_AUTH_PASSWORD_HASH=* ]]; then
            value="${line#VIDTRANS_AUTH_PASSWORD_HASH=}"
            if [[ "$value" == *'$'* && "$value" != *'$$'* ]]; then
                escaped="$(printf '%s' "$value" | sed 's/\$/$$/g')"
                line="VIDTRANS_AUTH_PASSWORD_HASH=$escaped"
                changed=1
            fi
        fi
        printf '%s\n' "$line"
    done < "$file" > "$temporary"
    if [[ "$changed" -eq 1 ]]; then
        chmod --reference="$file" "$temporary" 2>/dev/null || chmod 600 "$temporary"
        mv "$temporary" "$file"
        printf 'Escaped literal dollar signs in %s for Docker Compose.\n' "$file"
    else
        rm -f "$temporary"
    fi
}

# A cancelled `docker compose run` can keep the application's bind-mounted
# SQLite files open. Remove only one-off containers belonging to this exact
# Compose working directory before replacing the service containers.
cleanup_compose_oneoffs() {
    local -a containers=()
    mapfile -t containers < <(docker ps -aq \
        --filter "label=com.docker.compose.oneoff=True" \
        --filter "label=com.docker.compose.project.working_dir=$PWD")
    if (( ${#containers[@]} > 0 )); then
        printf 'Removing %d stale Compose one-off container(s)...\n' "${#containers[@]}"
        docker rm -f "${containers[@]}" >/dev/null
    fi
}

ensure_runtime_secret() {
    local file="$1" key="$2" value="$3"
    touch "$file"
    chmod 600 "$file"
    if grep -q "^${key}=." "$file"; then
        return 0
    fi
    printf '%s=%s\n' "$key" "$value" >> "$file"
    printf 'Generated missing production secret: %s\n' "$key"
}

escape_password_hash_for_compose .env
escape_password_hash_for_compose .env.production

for argument in "$@"; do
    case "$argument" in
        --full) FULL_REBUILD=1 ;;
        --production|--vps) PRODUCTION=1 ;;
        --local) FORCE_LOCAL=1 ;;
        --corporate-ca) CORPORATE_CA=1 ;;
        --help|-h)
            printf '%s\n' "Usage: bash docker-rebuild.sh [--full] [--production|--vps] [--local] [--corporate-ca]"
            printf '%s\n' "  --production  Run the one-time domain/HTTPS setup, then deploy behind Nginx."
            printf '%s\n' "  --full        Rebuild dependencies instead of only copying changed code."
            printf '%s\n' "  --local       Ignore an existing VPS marker and run only the localhost service."
            printf '%s\n' "  --corporate-ca Build HTTPS clients with VIDTRANS_CORPORATE_CA_FILE."
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$argument" >&2
            exit 2
            ;;
    esac
done

compose=(docker compose)
if [[ "$CORPORATE_CA" -eq 1 ]]; then
    compose+=( -f docker-compose.yml -f docker-compose.corporate-ca.yml )
fi

# Once a VPS is configured, a plain rebuild must keep Nginx and Certbot online.
if [[ -f deploy/.vps-configured && "$FORCE_LOCAL" -eq 0 ]]; then
    PRODUCTION=1
fi

if [[ "$FULL_REBUILD" -eq 1 ]]; then
    printf '%s\n' "Building a completely fresh image (dependencies will be downloaded again)..."
    "${compose[@]}" build --no-cache --pull vidtrans
    docker image tag "$APP_IMAGE" "$DEPS_IMAGE"
else
    if ! docker image inspect "$DEPS_IMAGE" >/dev/null 2>&1; then
        if docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
            printf '%s\n' "Creating the reusable dependency base from the current VidTrans image..."
            docker image tag "$APP_IMAGE" "$DEPS_IMAGE"
        else
            printf '%s\n' "No local dependency image exists; running the one-time full build..."
            "${compose[@]}" build vidtrans
            docker image tag "$APP_IMAGE" "$DEPS_IMAGE"
        fi
    fi
    printf '%s\n' "Building only changed backend/frontend code; apt and pip are skipped..."
    docker build --file Dockerfile.code --tag "$APP_IMAGE" .
fi

# The interactive source browser has its own dependency image. Docker keeps
# this layer cached, so normal code-only rebuilds remain fast after first use.
"${compose[@]}" build social-browser tiktok-browser

cleanup_compose_oneoffs

if [[ "$PRODUCTION" -eq 1 ]]; then
    credential_key="$(docker run --rm --entrypoint python "$APP_IMAGE" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    media_secret="$(docker run --rm --entrypoint python "$APP_IMAGE" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    ensure_runtime_secret .env VIDTRANS_CREDENTIAL_ENCRYPTION_KEY "$credential_key"
    ensure_runtime_secret .env VIDTRANS_MEDIA_SIGNING_SECRET "$media_secret"
    unset credential_key media_secret
    bash deploy/vps-first-run.sh
    # Keep an already-configured VPS in sync when routes are added to the
    # versioned HTTPS template (vps-first-run intentionally runs only once).
    mkdir -p deploy/nginx/runtime
    cp deploy/nginx/https.conf.template deploy/nginx/runtime/default.conf.template
    compose=(docker compose --env-file .env --env-file .env.production -f docker-compose.yml)
    if [[ "$CORPORATE_CA" -eq 1 ]]; then
        compose+=( -f docker-compose.corporate-ca.yml )
    fi
    compose+=( -f docker-compose.production.yml )
    public_host="$(awk -F= '$1 == "VIDTRANS_PUBLIC_HOST" { print substr($0, index($0, "=") + 1); exit }' .env.production)"
    "${compose[@]}" build nginx
    printf 'Applying the new image behind Nginx at https://%s...\n' "$public_host"
    "${compose[@]}" up -d --force-recreate --remove-orphans social-browser tiktok-browser vidtrans nginx certbot
    "${compose[@]}" ps social-browser tiktok-browser vidtrans nginx certbot
else
    printf '%s\n' "Applying the new image at http://localhost:5200..."
    "${compose[@]}" up -d --force-recreate --remove-orphans social-browser tiktok-browser vidtrans
    "${compose[@]}" ps social-browser tiktok-browser vidtrans
fi
