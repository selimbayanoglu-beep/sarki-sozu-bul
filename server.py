#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Şarkı Sözü Bul — Web & API Sunucusu (server.py)
4 Katmanlı Müzik Tanıma Motoru + Canlı Senkronize Karaoke Sözler + Çok Dilli Çeviri
"""

import argparse
import asyncio
import datetime
import http.server
import json
import os
import re
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from typing import Any, Dict, List, Optional

if os.path.exists("/etc/ssl/cert.pem"):
    os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")
elif os.path.exists("/etc/ssl/certs/ca-certificates.crt"):
    os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")

import lyrics_engine
import recognition_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
CERT_FILE = os.path.join(BASE_DIR, "cert.pem")
KEY_FILE = os.path.join(BASE_DIR, "key.pem")
TUNNEL_URL_FILE = os.path.join(BASE_DIR, "tunnel_url.txt")

_HISTORY_LOCK = threading.Lock()


def load_history() -> List[Dict[str, Any]]:
    with _HISTORY_LOCK:
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []


def save_to_history(song_data: Dict[str, Any]) -> None:
    if not song_data or not song_data.get("title"):
        return
    with _HISTORY_LOCK:
        items: List[Dict[str, Any]] = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        items = loaded
            except Exception:
                items = []

        title_norm = (song_data.get("title") or "").strip().lower()
        artist_norm = (song_data.get("artist") or "").strip().lower()

        # Aynı şarkı son kayıtta zaten varsa tekrar ekleme, zamanını güncelle
        items = [
            x for x in items
            if not (
                (x.get("title") or "").strip().lower() == title_norm
                and (x.get("artist") or "").strip().lower() == artist_norm
            )
        ]

        entry = {
            "id": int(datetime.datetime.now().timestamp() * 1000),
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "title": song_data.get("title") or "",
            "artist": song_data.get("artist") or "",
            "album": song_data.get("album") or "",
            "cover": song_data.get("cover") or "",
            "synced": bool(song_data.get("synced")),
            "layer_used": song_data.get("layer_used") or "",
            "source": song_data.get("source") or "",
            "lines": song_data.get("lines") or [],
            "plain_lyrics": song_data.get("plain_lyrics") or "",
        }
        items.insert(0, entry)
        items = items[:50]
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def get_tunnel_url() -> str:
    if os.path.exists(TUNNEL_URL_FILE):
        try:
            with open(TUNNEL_URL_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return os.environ.get("PUBLIC_HTTPS_URL", "").strip()


def ensure_self_signed_cert() -> bool:
    """HTTPS üzerinden doğrudan mikrofon erişimi için sertifika üretir."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return True
    try:
        cmd = [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            KEY_FILE,
            "-out",
            CERT_FILE,
            "-days",
            "3650",
            "-nodes",
            "-subj",
            "/CN=sarki-sozu-bul.local",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)
    except Exception:
        return False


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class SarkiSozuHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Gereksiz statik dosya loglarını azalt
        if self.path.startswith("/api/"):
            sys.stderr.write(f"[API] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Wave, X-File-Ext, X-Lang")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Wave, X-File-Ext, X-Lang")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/status":
            self._send_json({
                "ok": True,
                "engines": {
                    "ffmpeg_dsp": bool(recognition_engine.FFMPEG_BIN),
                    "shazamio_fp": bool(recognition_engine.HAS_SHAZAMIO),
                    "vocal_ai_stt": bool(recognition_engine.HAS_SPEECH_REC),
                    "lrclib_genius": True,
                },
                "https_port": int(os.environ.get("HTTPS_PORT", "8892")),
                "tunnel_url": get_tunnel_url(),
            })
            return

        if path == "/api/history":
            self._send_json({"ok": True, "items": load_history()})
            return

        if path == "/api/search":
            q = (qs.get("q", [""])[0] or "").strip()
            if not q:
                self._send_json({"ok": False, "error": "Arama metni boş olamaz."}, 400)
                return
            res = lyrics_engine.search_song_by_lyrics_snippet(q)
            if res and res.get("title"):
                res["recognized"] = True
                res["layer_used"] = "Doğrudan Söz / Şarkı Arama"
                save_to_history(res)
                self._send_json({"ok": True, "result": res})
            else:
                self._send_json({"ok": False, "error": "Eşleşen şarkı veya söz bulunamadı."})
            return

        return super().do_GET()

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/history":
            with _HISTORY_LOCK:
                if os.path.exists(HISTORY_FILE):
                    try:
                        os.remove(HISTORY_FILE)
                    except Exception:
                        pass
            self._send_json({"ok": True})
            return
        self._send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_len) if content_len > 0 else b""

        if path == "/api/recognize":
            if not body or len(body) < 200:
                self._send_json({"ok": False, "error": "Ses verisi çok kısa veya boş."}, 400)
                return

            wave_str = self.headers.get("X-Wave", "1")
            file_ext = self.headers.get("X-File-Ext", ".wav")
            lang = self.headers.get("X-Lang", "auto")
            try:
                wave = max(1, int(wave_str))
            except ValueError:
                wave = 1

            if not file_ext.startswith("."):
                file_ext = "." + file_ext

            try:
                result = asyncio.run(
                    recognition_engine.recognize_music_pipeline(
                        audio_bytes=body,
                        file_ext=file_ext,
                        wave=wave,
                        preferred_lang=lang,
                    )
                )
                if result.get("recognized") and result.get("title"):
                    save_to_history(result)
                self._send_json({"ok": True, "result": result})
            except Exception as exc:
                self._send_json({"ok": False, "error": f"Tanıma hatası: {exc}"}, 500)
            return

        if path == "/api/translate":
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
                lines = payload.get("lines") or []
                target_lang = payload.get("target_lang") or "tr"
                translated = lyrics_engine.translate_lyrics_to_turkish(lines, target_lang=target_lang)
                self._send_json({"ok": True, "translated_lines": translated})
            except Exception as exc:
                self._send_json({"ok": False, "error": f"Çeviri hatası: {exc}"}, 500)
            return

        self._send_json({"ok": False, "error": "Bilinmeyen uç nokta"}, 404)


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_https_server(https_port: int) -> None:
    if not ensure_self_signed_cert():
        return
    try:
        httpd = ThreadedHTTPServer(("0.0.0.0", https_port), SarkiSozuHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        print(f"🔒 HTTPS Mikrofon Sunucusu aktif: https://0.0.0.0:{https_port}")
        httpd.serve_forever()
    except Exception as exc:
        sys.stderr.write(f"[UYARI] HTTPS sunucusu başlatılamadı ({https_port}): {exc}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Şarkı Sözü Bul Sunucusu")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8891")))
    parser.add_argument("--https-port", type=int, default=int(os.environ.get("HTTPS_PORT", "8892")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # Arka planda HTTPS sunucusunu da başlat (mobil tarayıcı mikrofon izni için)
    https_thread = threading.Thread(target=start_https_server, args=(args.https_port,), daemon=True)
    https_thread.start()

    httpd = ThreadedHTTPServer(("0.0.0.0", args.port), SarkiSozuHandler)
    local_ip = get_local_ip()
    print("=" * 64)
    print("🎵 ŞARKI SÖZÜ BUL — 4 Katmanlı Müzik & Karaoke Sunucusu")
    print(f"🌐 Yerel Adres : http://localhost:{args.port}")
    print(f"📱 Ağ Adresi   : http://{local_ip}:{args.port}")
    print(f"🔒 HTTPS Adresi: https://{local_ip}:{args.https_port}")
    print("=" * 64)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nSunucu kapatılıyor...")
        httpd.server_close()


if __name__ == "__main__":
    main()
