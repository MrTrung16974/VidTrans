#!/usr/bin/env bash

# Rebuild VidTrans code without entering apt/pip dependency layers.
set -Eeuo pipefail

cd "$(dirname "$0")"

APP_IMAGE="vidtrans-vidtrans"
DEPS_IMAGE="vidtrans-deps:local"

if [[ "${1:-}" == "--full" ]]; then
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

printf '%s\n' "Applying the new image at http://localhost:5200..."
docker compose up -d --force-recreate --remove-orphans vidtrans

docker compose ps vidtrans
