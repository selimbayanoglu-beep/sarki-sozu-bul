#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CasaOS Sıfır-Özel-Karakter Önyükleyici (setup.py)

CasaOS Web arayüzünde tırnak (' / "), parantez ( / ) veya noktalı virgül (;) içeren
komutlar kabuk ayrıştırıcısı tarafından bozulabildiği için, konteyner komutu olarak
hiçbir özel karakter içermeyen düz comando kullanılır:
  pip install --no-build-isolation --no-deps --break-system-packages https://codeload.github.com/selimbayanoglu-beep/sarki-sozu-bul/zip/refs/heads/main

pip arşivi indirip açtıktan sonra bu setup.py dosyasını çalıştırır; bu dosya da
tüm uygulama dosyalarını /app dizinine kopyalayıp anında server.py'yi başlatır.
"""

import os
import shutil
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = "/app" if os.access("/", os.W_OK) else os.environ.get("SARKI_APP_DIR", "/tmp/sarki-app")
os.makedirs(APP_DIR, exist_ok=True)

for fname in os.listdir(SRC_DIR):
    src_path = os.path.join(SRC_DIR, fname)
    if not os.path.isfile(src_path):
        continue
    dst_path = os.path.join(APP_DIR, fname)
    if fname == "history.json" and os.path.exists(dst_path):
        continue
    if os.path.abspath(src_path) != os.path.abspath(dst_path):
        shutil.copy2(src_path, dst_path)

server_script = os.path.join(APP_DIR, "server.py")
port = os.environ.get("PORT", "8891")
https_port = os.environ.get("HTTPS_PORT", "8892")

os.execv(
    sys.executable,
    [sys.executable, server_script, "--port", str(port), "--https-port", str(https_port), "--no-browser"],
)
