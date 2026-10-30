#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if command -v cloudflared >/dev/null 2>&1; then
  rm -f tunnel_url.txt
  (
    cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT:-8891}" 2>&1 | \
    while IFS= read -r line; do
      if echo "$line" | grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' >/dev/null 2>&1; then
        URL=$(echo "$line" | grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -n 1)
        echo "$URL" > tunnel_url.txt
        echo "🌍 Güvenli Mobil Mikrofon Adresi (Cloudflare HTTPS): $URL"
      fi
    done
  ) &
fi

exec python3 server.py --port "${PORT:-8891}" --https-port "${HTTPS_PORT:-8892}" --no-browser
