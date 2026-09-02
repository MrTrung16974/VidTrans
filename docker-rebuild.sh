#!/usr/bin/env bash

# Rebuild VidTrans code without entering apt/pip dependency layers.
set -Eeuo pipefail

cd "$(dirname "$0")"

APP_IMAGE="vidtrans-vidtrans"
DEPS_IMAGE="vidtrans-deps:local"
FULL_REBUILD=0
PRODUCTION=0
FORCE_LOCAL=0

for argument in "$@"; do
    case "$argument" in
        --full) FULL_REBUILD=1 ;;
        --production|--vps) PRODUCTION=1 ;;
        --local) FORCE_LOCAL=1 ;;
        --help|-h)
            printf '%s\n' "Usage: bash docker-rebuild.sh [--full] [--production|--vps] [--local]"
            printf '%s\n' "  --production  Run the one-time domain/HTTPS setup, then deploy behind Nginx."
            printf '%s\n' "  --full        Rebuild dependencies instead of only copying changed code."
            printf '%s\n' "  --local       Ignore an existing VPS marker and run only the localhost service."
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$argument" >&2
            exit 2
            ;;
    esac
done

# Once a VPS is configured, a plain rebuild must keep Nginx and Certbot online.
if [[ -f deploy/.vps-configured && "$FORCE_LOCAL" -eq 0 ]]; then
    PRODUCTION=1
fi

if [[ "$FULL_REBUILD" -eq 1 ]]; then
    printf '%s\n' "Building a completely fresh image (dependencies will be downloaded again)..."
    docker compose build --no-cache --pull vidtrans
    docker image tag "$APP_IMAGE" "$DEPS_IMAGE"
else
    if ! docker image inspect "$DEPS_IMAGE" >/dev/null 2>&1; then
        if docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
            printf '%s\n' "Creating the reusable dependency base from the current VidTrans image..."
            docker image tag "$APP_IMAGE" "$DEPS_IMAGE"
        else
            printf '%s\n' "No local dependency image exists; running the one-time full build..."
            docker compose build vidtrans
            docker image tag "$APP_IMAGE" "$DEPS_IMAGE"
        fi
    fi
    printf '%s\n' "Building only changed backend/frontend code; apt and pip are skipped..."
    docker build --file Dockerfile.code --tag "$APP_IMAGE" .
fi

# The interactive Douyin browser has its own dependency image. Docker keeps
# this layer cached, so normal code-only rebuilds remain fast after first use.
docker compose build douyin-browser

if [[ "$PRODUCTION" -eq 1 ]]; then
    bash deploy/vps-first-run.sh
    # Keep an already-configured VPS in sync when routes are added to the
    # versioned HTTPS template (vps-first-run intentionally runs only once).
    mkdir -p deploy/nginx/runtime
    cp deploy/nginx/https.conf.template deploy/nginx/runtime/default.conf.template
    compose=(
        docker compose
        --env-file .env
        --env-file .env.production
        -f docker-compose.yml
        -f docker-compose.production.yml
    )
    public_host="$(awk -F= '$1 == "VIDTRANS_PUBLIC_HOST" { print substr($0, index($0, "=") + 1); exit }' .env.production)"
    "${compose[@]}" build nginx
    printf 'Applying the new image behind Nginx at https://%s...\n' "$public_host"
    "${compose[@]}" up -d --force-recreate --remove-orphans douyin-browser vidtrans nginx certbot
    "${compose[@]}" ps douyin-browser vidtrans nginx certbot
else
    printf '%s\n' "Applying the new image at http://localhost:5200..."
    docker compose up -d --force-recreate --remove-orphans douyin-browser vidtrans
    docker compose ps douyin-browser vidtrans
fi
