#!/usr/bin/env bash

# Rebuild VidTrans from scratch and start it at http://localhost:5200.
set -Eeuo pipefail

cd "$(dirname "$0")"

echo "Stopping the existing VidTrans container..."
docker compose down --remove-orphans

echo "Building a fresh image without Docker layer cache..."
docker compose build --no-cache --pull

echo "Starting VidTrans on http://localhost:5200..."
docker compose up -d --force-recreate

docker compose ps
