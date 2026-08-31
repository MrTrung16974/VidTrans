#!/bin/sh
set -eu

# The official Nginx entrypoint renders /etc/nginx/templates first. This hook keeps
# the original `nginx -g daemon off` command intact and only schedules TLS reloads.
(
    while :; do
        sleep 21600
        nginx -s reload || true
    done
) &
