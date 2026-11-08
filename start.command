#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

rm -f tunnel_url.txt 2>/dev/null || true
exec python3 server.py --port "${PORT:-8891}" --https-port "${HTTPS_PORT:-8892}"
