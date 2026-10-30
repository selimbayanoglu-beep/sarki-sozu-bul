#!/usr/bin/env bash
set -euo pipefail

SERVER_IP="159.195.255.168"
SERVER_USER="root"
REMOTE_DIR="/DATA/AppData/sarki-sozu-bul"
SOURCE_DIR="/Users/selim/Documents/sarki-sozu-bul"

cd "$SOURCE_DIR"

if [ ! -f "$HOME/.ssh/id_ed25519.pub" ]; then
  ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519"
fi

PUBKEY=$(cat "$HOME/.ssh/id_ed25519.pub")

echo "=================================================================="
echo "🚀 Şarkı Sözü Bul (4 Katmanlı Müzik & Karaoke Motoru)"
echo "📡 ${SERVER_IP} sunucusuna (${REMOTE_DIR}) kuruluyor..."
echo "=================================================================="

tar \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='history.json' \
  --exclude='cert.pem' \
  --exclude='key.pem' \
  --exclude='tunnel_url.txt' \
  -C "$SOURCE_DIR" -czf - \
  index.html style.css app.js server.py recognition_engine.py lyrics_engine.py \
  icon.svg manifest.json requirements.txt Dockerfile entrypoint.sh | \
ssh -i "$HOME/.ssh/id_ed25519" -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new "${SERVER_USER}@${SERVER_IP}" "
  set -eu
  chmod 700 /root 2>/dev/null || true
  mkdir -p /root/.ssh
  chmod 700 /root/.ssh
  grep -qxF '${PUBKEY}' /root/.ssh/authorized_keys 2>/dev/null || echo '${PUBKEY}' >> /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  chown -R root:root /root/.ssh 2>/dev/null || true

  mkdir -p '${REMOTE_DIR}'
  tar -xzf - -C '${REMOTE_DIR}'
  cd '${REMOTE_DIR}'
  touch history.json
  chmod +x entrypoint.sh

  echo '🐳 Docker imajı derleniyor (ffmpeg + shazamio + SpeechRecognition + cloudflared)...'
  docker build -t sarki-sozu-bul-app .

  docker rm -f sarki-sozu-bul 2>/dev/null || true
  docker run -d \
    --name sarki-sozu-bul \
    --restart unless-stopped \
    -p 8891:8891 \
    -p 8892:8892 \
    -v '${REMOTE_DIR}/history.json:/app/history.json' \
    sarki-sozu-bul-app

  sleep 5
  echo ''
  echo '✅ Kurulum tamamlandı!'
  echo '🌐 Web Adresi (HTTP)  : http://${SERVER_IP}:8891'
  echo '🔒 Mikrofon (HTTPS)   : https://${SERVER_IP}:8892'
  TUNNEL=\$(docker exec sarki-sozu-bul cat /app/tunnel_url.txt 2>/dev/null || true)
  if [ -n \"\$TUNNEL\" ]; then
    echo \"🌍 Otomatik SSL Adresi: \$TUNNEL\"
  fi
"
