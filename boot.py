#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CasaOS / Docker 1-Saniye Önyükleyici (boot.py)
Hiçbir apt-get veya git kurulumunu beklemeden uygulama dosyalarını /app dizinine
indirir ve anında server.py sürecini başlatır.
"""

import io
import os
import ssl
import sys
import urllib.request
import zipfile

APP_DIR = "/app" if os.path.exists("/app") or os.access("/", os.W_OK) else os.getcwd()
os.makedirs(APP_DIR, exist_ok=True)

ZIP_URL = "https://codeload.github.com/selimbayanoglu-beep/sarki-sozu-bul/zip/refs/heads/main"

try:
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "SarkiSozuBul-Boot/1.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=25) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            if member.endswith("/") or member.count("/") != 1:
                continue
            fname = os.path.basename(member)
            if not fname:
                continue
            target = os.path.join(APP_DIR, fname)
            if fname == "history.json" and os.path.exists(target):
                continue
            with open(target, "wb") as fp:
                fp.write(zf.read(member))
    print(f"[BOOT] Uygulama dosyaları {APP_DIR} dizinine başarıyla indirildi.", flush=True)
except Exception as exc:
    sys.stderr.write(f"[BOOT UYARI] Zip indirme hatası: {exc}\n")

server_script = os.path.join(APP_DIR, "server.py")
port = os.environ.get("PORT", "8891")
https_port = os.environ.get("HTTPS_PORT", "8892")

try:
    import subprocess
    subprocess.Popen(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--quiet", "shazamio", "ytmusicapi", "SpeechRecognition"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except Exception:
    pass

os.execv(
    sys.executable,
    [sys.executable, server_script, "--port", str(port), "--https-port", str(https_port), "--no-browser"],
)

