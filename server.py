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
import importlib
import json
import os
import platform
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request
import webbrowser

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
_TUNNEL_LOCK = threading.Lock()
_TUNNEL_URL = ""
_TUNNEL_LAST_VERIFIED = 0.0
_TUNNEL_PROC: Optional[subprocess.Popen] = None


def _is_tunnel_alive(url: str) -> bool:
    """Cloudflare tünelinin gerçekten aktif olup olmadığını doğrular."""
    if not url or not url.startswith("https://"):
        return False
    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            f"{url.rstrip('/')}/icon.svg",
            headers={"User-Agent": "SarkiSozuBul-HealthCheck/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3.5, context=ctx) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_tunnel_url() -> str:
    """Hiçbir ağ isteği yapmadan (0ms bloklamasız) mevcut aktif tünel adresini döndürür."""
    return _TUNNEL_URL or os.environ.get("PUBLIC_HTTPS_URL", "").strip()


def _ensure_cloudflared_binary() -> Optional[str]:
    cf_bin = shutil.which("cloudflared")
    if cf_bin:
        return cf_bin
    candidate = os.path.join(BASE_DIR, "cloudflared")
    if os.path.exists(candidate) and os.access(candidate, os.X_OK):
        return candidate
    try:
        arch = platform.machine().lower()
        cf_arch = "arm64" if ("arm" in arch or "aarch" in arch) else "amd64"
        os_name = "darwin" if "darwin" in platform.system().lower() else "linux"
        if os_name == "darwin":
            # macOS için tgz arşivi veya doğrudan binary kontrolü
            return None
        download_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-{os_name}-{cf_arch}"
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40, context=ctx) as resp, open(candidate, "wb") as fp:
            shutil.copyfileobj(resp, fp)
        os.chmod(candidate, 0o755)
        return candidate
    except Exception:
        return None


def start_cloudflared_watchdog(local_port: int) -> None:
    """
    Cloudflare HTTPS tünelini arka planda başlatır; süreç kapanırsa otomatik yeniden başlatır.
    Ana HTTP sunucusunu hiçbir zaman bloklamaz.
    """
    def _watchdog_loop():
        global _TUNNEL_URL, _TUNNEL_LAST_VERIFIED, _TUNNEL_PROC
        time.sleep(2.0)
        cf_bin = _ensure_cloudflared_binary()
        if not cf_bin:
            return

        while True:
            try:
                with _TUNNEL_LOCK:
                    if _TUNNEL_PROC is not None:
                        try:
                            _TUNNEL_PROC.terminate()
                            _TUNNEL_PROC.wait(timeout=2)
                        except Exception:
                            try:
                                _TUNNEL_PROC.kill()
                            except Exception:
                                pass
                        _TUNNEL_PROC = None

                    _TUNNEL_URL = ""
                    try:
                        if os.path.exists(TUNNEL_URL_FILE):
                            os.remove(TUNNEL_URL_FILE)
                    except Exception:
                        pass

                    proc = subprocess.Popen(
                        [cf_bin, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{local_port}"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    _TUNNEL_PROC = proc

                if proc.stdout:
                    for line in proc.stdout:
                        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                        if match:
                            found_url = match.group(0)
                            _TUNNEL_URL = found_url
                            _TUNNEL_LAST_VERIFIED = time.time()
                            try:
                                with open(TUNNEL_URL_FILE, "w", encoding="utf-8") as f:
                                    f.write(found_url)
                            except Exception:
                                pass
                            print("=" * 64)
                            print(f"🌍 CANLI GÜVENLİ MOBİL HTTPS ADRESİ: {found_url}")
                            print("=" * 64)
                proc.wait()
            except Exception as exc:
                sys.stderr.write(f"⚠️ Cloudflare Tunnel döngü hatası: {exc}\n")
            time.sleep(5)

    t = threading.Thread(target=_watchdog_loop, daemon=True)
    t.start()


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
            "preview_url": song_data.get("preview_url") or "",
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


_RECENT_LOGS: List[str] = []


def record_log(msg: str) -> None:
    global _RECENT_LOGS
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    _RECENT_LOGS.append(line)
    if len(_RECENT_LOGS) > 120:
        _RECENT_LOGS.pop(0)
    sys.stderr.write(line + "\n")


CORS_HEADERS = (
    "Content-Type, X-Wave, X-File-Ext, X-Lang, X-Browser-Transcript, "
    "X-Track-Title, X-Track-Artist, X-Current-Offset"
)


class SarkiSozuHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path.startswith("/api/"):
            record_log(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", CORS_HEADERS)
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", CORS_HEADERS)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/status":
            self._send_json({
                "ok": True,
                "version": "2026.11.08-v2",
                "engines": {
                    "ffmpeg_dsp": bool(recognition_engine.FFMPEG_BIN),
                    "shazamio_fp": bool(recognition_engine.HAS_SHAZAMIO),
                    "vocal_ai_stt": bool(recognition_engine.HAS_SPEECH_REC),
                    "ytmusic": bool(lyrics_engine._get_ytmusic()),
                    "lrclib_genius": True,
                },
                "https_port": int(os.environ.get("HTTPS_PORT", "8892")),
                "tunnel_url": get_tunnel_url(),
            })
            return

        if path == "/api/debug":
            self._send_json({
                "ok": True,
                "version": "2026.11.08-v2",
                "engines": {
                    "ffmpeg": bool(recognition_engine.FFMPEG_BIN),
                    "shazam": bool(recognition_engine.HAS_SHAZAMIO),
                    "speech_rec": bool(recognition_engine.HAS_SPEECH_REC),
                    "ytmusic": bool(lyrics_engine._get_ytmusic()),
                },
                "tunnel_url": _TUNNEL_URL,
                "logs": _RECENT_LOGS[-60:],
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
                self._send_json({
                    "ok": True,
                    "result": res,
                    "candidates": res.get("candidates") or [],
                })
            else:
                self._send_json({"ok": False, "error": "Eşleşen şarkı veya söz bulunamadı."})
            return

        if path == "/api/lyrics":
            title = (qs.get("title", [""])[0] or "").strip()
            artist = (qs.get("artist", [""])[0] or "").strip()
            cover = (qs.get("cover", [""])[0] or "").strip()
            if not title:
                self._send_json({"ok": False, "error": "Şarkı adı gerekli."}, 400)
                return
            res = lyrics_engine.get_full_lyrics_for_track(title, artist, cover=cover)
            res["recognized"] = True
            res["layer_used"] = "Seçilen Şarkı"
            save_to_history(res)
            self._send_json({"ok": True, "result": res})
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
            raw_transcript = self.headers.get("X-Browser-Transcript", "") or ""
            browser_transcript = urllib.parse.unquote(raw_transcript).strip()
            try:
                wave = max(1, int(wave_str))
            except ValueError:
                wave = 1

            if not file_ext.startswith("."):
                file_ext = "." + file_ext

            record_log(
                f"🎙️ Gelen ses: {len(body)} bayt, Wave={wave_str}, Ext={file_ext}"
                + (f", CanlıMetin='{browser_transcript}'" if browser_transcript else "")
            )

            try:
                result = asyncio.run(
                    recognition_engine.recognize_music_pipeline(
                        audio_bytes=body,
                        file_ext=file_ext,
                        wave=wave,
                        preferred_lang=lang,
                        browser_transcript=browser_transcript,
                    )
                )
                if result.get("recognized") and result.get("title"):
                    record_log(
                        f"🎉 Şarkı bulundu: {result.get('title')} - {result.get('artist')} [{result.get('layer_used')}]"
                    )
                    save_to_history(result)
                else:
                    record_log(f"🔍 Dalga {wave_str} arandı ({result.get('elapsed_ms')}ms): Eşleşme yok.")
                self._send_json({"ok": True, "result": result})
            except Exception as exc:
                record_log(f"❌ Tanıma hatası: {exc}")
                self._send_json({"ok": False, "error": f"Tanıma hatası: {exc}"}, 500)
            return

        if path == "/api/sync-audio":
            if not body or len(body) < 200:
                self._send_json({"ok": False, "error": "Ses verisi boş."}, 400)
                return
            file_ext = self.headers.get("X-File-Ext", ".wav")
            if not file_ext.startswith("."):
                file_ext = "." + file_ext
            raw_title = urllib.parse.unquote(self.headers.get("X-Track-Title", "") or "")
            raw_artist = urllib.parse.unquote(self.headers.get("X-Track-Artist", "") or "")
            cur_off_str = self.headers.get("X-Current-Offset", "")
            current_offset: Optional[float] = None
            try:
                if cur_off_str:
                    current_offset = float(cur_off_str)
            except ValueError:
                current_offset = None

            lines_for_sync: List[Dict[str, Any]] = []
            for h_item in load_history():
                if (h_item.get("title") or "").strip().lower() == raw_title.strip().lower():
                    lines_for_sync = h_item.get("lines") or []
                    break

            try:
                sync_res = asyncio.run(
                    recognition_engine.sync_live_audio_to_lyrics(
                        audio_bytes=body,
                        file_ext=file_ext,
                        title=raw_title,
                        artist=raw_artist,
                        lines=lines_for_sync,
                        current_offset=current_offset,
                    )
                )
                if sync_res.get("synced"):
                    record_log(
                        f"🎯 Canlı Ses Senkronu [{sync_res.get('method')}]: offset={sync_res.get('offset_seconds')}s, idx={sync_res.get('matched_line_index')}"
                    )
                self._send_json({"ok": True, "result": sync_res})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
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

        if path == "/api/pull-github":
            try:
                target_ref = "main"
                if body:
                    try:
                        body_json = json.loads(body.decode("utf-8", errors="replace"))
                        if isinstance(body_json, dict) and body_json.get("ref"):
                            target_ref = str(body_json["ref"]).strip()
                    except Exception:
                        pass

                try:
                    gh_api_url = f"https://api.github.com/repos/selimbayanoglu-beep/sarki-sozu-bul/commits/{target_ref}"
                    gh_req = urllib.request.Request(
                        gh_api_url,
                        headers={"User-Agent": "SarkiSozuBul-Deploy/2.0", "Accept": "application/vnd.github.v3+json"},
                    )
                    with urllib.request.urlopen(gh_req, timeout=8) as gh_resp:
                        c_info = json.loads(gh_resp.read().decode("utf-8"))
                        if "sha" in c_info:
                            target_ref = c_info["sha"]
                except Exception:
                    pass

                files_to_sync = [
                    "index.html",
                    "style.css",
                    "app.js",
                    "server.py",
                    "boot.py",
                    "setup.py",
                    "docker-compose.yml",
                    "recognition_engine.py",
                    "lyrics_engine.py",
                    "icon.svg",
                    "manifest.json",
                    "requirements.txt",
                ]
                repo_base = f"https://raw.githubusercontent.com/selimbayanoglu-beep/sarki-sozu-bul/{target_ref}/"
                updated_files = []
                errors = []
                for fname in files_to_sync:
                    try:
                        url = f"{repo_base}{fname}"
                        req = urllib.request.Request(
                            url,
                            headers={
                                "Cache-Control": "no-cache, no-store, must-revalidate",
                                "Pragma": "no-cache",
                                "User-Agent": "Mozilla/5.0",
                            },
                        )
                        with urllib.request.urlopen(req, timeout=12) as resp:
                            data = resp.read()
                        if data:
                            target_path = os.path.join(BASE_DIR, fname)
                            with open(target_path, "wb") as f:
                                f.write(data)
                            updated_files.append(fname)
                    except Exception as exc:
                        errors.append(f"{fname}: {exc}")

                req_file = os.path.join(BASE_DIR, "requirements.txt")
                if os.path.exists(req_file):
                    try:
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--break-system-packages", "-r", req_file],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=60,
                        )
                    except Exception:
                        pass

                try:
                    importlib.reload(lyrics_engine)
                    importlib.reload(recognition_engine)
                except Exception:
                    pass

                self._send_json({
                    "ok": len(errors) == 0,
                    "ref": target_ref,
                    "updated": updated_files,
                    "errors": errors,
                })

                def _restart_process():
                    time.sleep(0.6)
                    try:
                        record_log("🔄 Sunucu süreci yeni kod ile yeniden başlatılıyor...")
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    except Exception as re_err:
                        record_log(f"Restart hatası: {re_err}")

                threading.Thread(target=_restart_process, daemon=True).start()
                return
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
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


def _auto_bootstrap_environment(http_port: int, https_port: int) -> None:
    """
    HTTP sunucusu port 8891'i açtıktan hemen sonra arka planda çalışır:
    Eksik Python veya sistem paketlerini (shazamio, ytmusicapi, ffmpeg vb.) sunucuyu
    bloklamadan kurar, ardından HTTPS ve Cloudflare tünel servislerini başlatır.
    """
    def _worker():
        # 1. Eksik Python paketleri varsa arka planda kur
        need_pip = (
            not recognition_engine.HAS_SHAZAMIO
            or not recognition_engine.HAS_SPEECH_REC
            or not lyrics_engine._get_ytmusic()
        )
        req_file = os.path.join(BASE_DIR, "requirements.txt")
        if need_pip and os.path.exists(req_file):
            record_log("📦 Arka planda Python ses/söz kütüphaneleri hazırlanıyor...")
            for extra_args in ([], ["--break-system-packages"]):
                try:
                    cp = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-q", *extra_args, "-r", req_file],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=180,
                    )
                    if cp.returncode == 0:
                        break
                except Exception:
                    pass
            recognition_engine.refresh_optional_dependencies()
            lyrics_engine._get_ytmusic()
            record_log("✅ Python ses/söz kütüphaneleri hazır.")

        # 2. Linux/Docker ortamında ffmpeg veya openssl eksikse arka planda kur
        if platform.system().lower() == "linux" and (
            not shutil.which("ffmpeg") or not shutil.which("openssl")
        ):
            record_log("🛠️ Arka planda ffmpeg ve sistem araçları kuruluyor...")
            try:
                env = os.environ.copy()
                env["DEBIAN_FRONTEND"] = "noninteractive"
                subprocess.run(
                    ["sh", "-c", "dpkg --configure -a >/dev/null 2>&1 || true"],
                    env=env,
                    timeout=60,
                )
                subprocess.run(
                    [
                        "sh",
                        "-c",
                        "apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg flac openssl ca-certificates curl >/dev/null 2>&1",
                    ],
                    env=env,
                    timeout=240,
                )
                recognition_engine.refresh_optional_dependencies()
                record_log("✅ ffmpeg DSP motoru hazır.")
            except Exception as exc:
                record_log(f"⚠️ Sistem paketi kurulum uyarısı: {exc}")

        # 3. Yerel HTTPS sunucusunu ve Cloudflare tünel bekçisini başlat
        threading.Thread(target=start_https_server, args=(https_port,), daemon=True).start()
        start_cloudflared_watchdog(http_port)

    threading.Thread(target=_worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Şarkı Sözü Bul Sunucusu")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8891")))
    parser.add_argument("--https-port", type=int, default=int(os.environ.get("HTTPS_PORT", "8892")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # ÖNCE HTTP sunucusunu 0.0.0.0:8891 üzerinde anında aç (CasaOS sağlık kontrolü 0. saniyede başarılı olsun)
    httpd = ThreadedHTTPServer(("0.0.0.0", args.port), SarkiSozuHandler)
    local_ip = get_local_ip()
    print("=" * 64)
    print("🎵 ŞARKI SÖZÜ BUL — 4 Katmanlı Müzik & Karaoke Sunucusu")
    print(f"🌐 Yerel Adres : http://localhost:{args.port}")
    print(f"📱 Ağ Adresi   : http://{local_ip}:{args.port}")
    print(f"🔒 HTTPS Adresi: https://{local_ip}:{args.https_port}")
    print("=" * 64)

    # Arka planda bağımlılıkları kontrol et, HTTPS ve Cloudflare tünelini başlat
    _auto_bootstrap_environment(args.port, args.https_port)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nSunucu kapatılıyor...")
        httpd.server_close()


if __name__ == "__main__":
    main()
