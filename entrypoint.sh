#!/usr/bin/env bash
set -e

rm -f /app/tunnel_url.txt 2>/dev/null || true
exec python3 /app/server.py --port "${PORT:-8891}" --https-port "${HTTPS_PORT:-8892}" --no-browser
