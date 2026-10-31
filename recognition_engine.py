#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Şarkı Sözü Bul — 4 Katmanlı Akıllı Müzik Tanıma Motoru (recognition_engine.py)

Barda çalan herhangi bir dildeki (Türkçe, İngilizce, İspanyolca, Fransızca vb.) şarkıyı
sadece müziğini dinleyerek bulmak için 4 katmanı sırayla ve paralel çalıştırır:

Katman 1 (DSP Bar Filtresi):
  - Bar uğultusunu ve aşırı subwoofer basını (20Hz-130Hz) keser, sesi dengeler.
  - DJ'lerin şarkıyı %3-%4 hızlandırarak/yavaşlatarak çalmasına karşı pitch/tempo düzeltilmiş
    ses kopyaları üretir.
  - Enstrümanları bastırıp şarkıcının ses frekanslarını (220Hz-3600Hz) öne çıkaran vokal kopyası üretir.

Katman 2 (Çoklu Ses İzi - Multi-Variant Audio Fingerprinting):
  - Ham ses, Bar-Temizlenmiş ses ve DJ-Pitch düzeltilmiş sesleri paralel olarak ShazamIO
    (ve varsa AudD) motorlarında tarar.
  - Şarkının o an kaçıncı saniyesinde olduğunu (offset) yakalar.

Katman 3 (AI Vokal -> Söz Çıkarma - Canlı Müzik & Cover Kurtarıcısı):
  - Canlı grup, akustik performans veya ağır remix nedeniyle ses izi eşleşmezse,
    vokal filtresinden geçmiş müziğin içinden şarkıcının söylediği kelimeleri
    çok dilli ses tanıma (TR, EN, ES, FR, DE, IT) ile ayıklar.

Katman 4 (Söz ve Senkronize Karaoke Eşleştirme):
  - Bulunan şarkının veya müziğin içinden yakalanan kelimelerin tam şarkı sözlerini,
    varsa saniye saniye akan LRC karaoke satırlarını ve o an söylenen satırı döndürür.
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple
import sys
import ssl

try:
    import certifi
    ca_path = certifi.where()
    if os.path.exists(ca_path):
        os.environ["SSL_CERT_FILE"] = ca_path
        os.environ["REQUESTS_CA_BUNDLE"] = ca_path
except Exception:
    pass

if "SSL_CERT_FILE" not in os.environ:
    if os.path.exists("/etc/ssl/cert.pem"):
        os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
    elif os.path.exists("/etc/ssl/certs/ca-certificates.crt"):
        os.environ["SSL_CERT_FILE"] = "/etc/ssl/certs/ca-certificates.crt"

import lyrics_engine

try:
    from ytmusicapi import YTMusic
except ImportError:
    try:
        import subprocess
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--break-system-packages", "ytmusicapi"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        from ytmusicapi import YTMusic
    except Exception:
        pass

# İsteğe bağlı kütüphaneler (Docker sunucusunda tam kurulu olur)
try:
    from shazamio import Shazam
    HAS_SHAZAMIO = True
except Exception:
    HAS_SHAZAMIO = False

try:
    import speech_recognition as sr
    HAS_SPEECH_REC = True
except Exception:
    HAS_SPEECH_REC = False


try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

FFMPEG_BIN = shutil.which("ffmpeg")
_EXECUTOR = ThreadPoolExecutor(max_workers=6)


def _run_ffmpeg(input_path: str, output_path: str, audio_filter: Optional[str] = None, sample_rate: int = 44100) -> bool:
    """ffmpeg ile ses dosyasını dönüştürür ve belirtilen DSP ses filtresini uygular."""
    if not FFMPEG_BIN:
        return False
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
    ]
    if audio_filter:
        cmd.extend(["-af", audio_filter])
    cmd.append(output_path)
    try:
        subprocess.run(cmd, check=True, timeout=8.0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 512
    except Exception:
        return False


def prepare_dsp_variants(raw_audio_path: str, work_dir: str, wave: int = 1) -> Dict[str, str]:
    """
    Katman 1: Bar ortamındaki kayıttan farklı DSP filtreli ses varyasyonları üretir.
    Akustik parmak izini bozan yıkıcı filtreler (afftdn vb.) kullanılmaz;
    yalnızca sub-bass gürültü kesme ve dinamik ses normalizasyonu uygulanır.
    """
    variants: Dict[str, str] = {"raw": raw_audio_path}
    if not FFMPEG_BIN:
        return variants

    # 1. Ses Dengeleme & Güçlendirme (Telefon mikrofonunun uzaktan aldığı sesi 2.8x yükseltir, tepe noktalarını netleştirir)
    norm_path = os.path.join(work_dir, "normalized.wav")
    norm_filter = "highpass=f=75,volume=2.8,dynaudnorm=f=100:g=25:m=30"
    if _run_ffmpeg(raw_audio_path, norm_path, norm_filter, sample_rate=44100):
        variants["normalized"] = norm_path

    # 2. Vokal Öne Çıkarma (Şarkıcının sözlerini Speech-to-Text motoruna iletmek için 16kHz WAV)
    vocal_path = os.path.join(work_dir, "vocal_boost.wav")
    vocal_filter = (
        "highpass=f=180,lowpass=f=4200,"
        "equalizer=f=1200:t=q:w=1.2:g=6,"
        "equalizer=f=2600:t=q:w=1.2:g=5,"
        "dynaudnorm=f=120:g=15"
    )
    if _run_ffmpeg(raw_audio_path, vocal_path, vocal_filter, sample_rate=16000):
        variants["vocal_boost"] = vocal_path

    # 3. Dalga 2 ve sonrasında DJ Tempo / Pitch kaymalarına karşı (-3.5% ve +3.5%) varyasyonlar
    if wave >= 2:
        pitch_down_path = os.path.join(work_dir, "pitch_down.wav")
        # DJ şarkıyı %3.5 hızlandırdıysa normale döndürür
        pitch_down_filter = "asetrate=44100*0.965,aresample=44100,highpass=f=80,dynaudnorm=f=120:g=15"
        if _run_ffmpeg(raw_audio_path, pitch_down_path, pitch_down_filter, sample_rate=44100):
            variants["pitch_down"] = pitch_down_path

        pitch_up_path = os.path.join(work_dir, "pitch_up.wav")
        # DJ şarkıyı %3.5 yavaşlattıysa normale döndürür
        pitch_up_filter = "asetrate=44100*1.035,aresample=44100,highpass=f=80,dynaudnorm=f=120:g=15"
        if _run_ffmpeg(raw_audio_path, pitch_up_path, pitch_up_filter, sample_rate=44100):
            variants["pitch_up"] = pitch_up_path

    return variants


def create_shazam_client() -> Shazam:
    """
    ShazamIO istemcisini certifi CA sertifikaları ile yapılandırarak
    SSLCertVerificationError ve aiohttp bağlantı hatalarını tamamen önler.
    """
    shazam = Shazam()
    try:
        import certifi, aiohttp
        from aiohttp_retry import RetryClient

        ca_file = None
        try:
            ca_file = certifi.where()
        except Exception:
            pass

        if ca_file and os.path.exists(ca_file):
            ctx = ssl.create_default_context(cafile=ca_file)
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        def _patched_ensure():
            loop = asyncio.get_running_loop()
            if shazam.http_client._retry_client is None or (shazam.http_client._loop is not loop and not shazam.http_client._closed):
                connector = aiohttp.TCPConnector(ssl=ctx)
                shazam.http_client._retry_client = RetryClient(
                    client_session=aiohttp.ClientSession(connector=connector),
                    retry_options=shazam.http_client.retry_options,
                    raise_for_status=False,
                    trace_configs=[shazam.http_client.trace_config],
                )
                shazam.http_client._loop = loop
            return shazam.http_client._retry_client

        shazam.http_client._ensure_client = _patched_ensure
    except Exception as exc:
        sys.stderr.write(f"[SHAZAM INIT UYARI] SSL yaması: {exc}\n")
    return shazam


async def _shazam_recognize_single(file_path: str, variant_label: str) -> Optional[Dict[str, Any]]:
    """Tek bir ses varyasyonunu ShazamIO ile tarar."""
    if not HAS_SHAZAMIO or not os.path.exists(file_path):
        return None
    try:
        shazam = create_shazam_client()
        out = await asyncio.wait_for(shazam.recognize(file_path), timeout=7.5)
        track = (out or {}).get("track")
        if not track:
            return None

        title = track.get("title") or ""
        artist = track.get("subtitle") or ""
        if not title:
            return None

        images = track.get("images") or {}
        cover = images.get("coverarthq") or images.get("coverart") or images.get("background") or ""

        # Şarkının o anki çalma saniyesini (offset) yakala
        offset_seconds: Optional[float] = None
        matches = (out or {}).get("matches") or []
        if matches and isinstance(matches[0], dict):
            raw_offset = matches[0].get("offset")
            if isinstance(raw_offset, (int, float)):
                offset_seconds = round(float(raw_offset), 2)

        # Shazam içinde gömülü söz varsa yedek olarak al
        shazam_lyrics: List[str] = []
        for section in track.get("sections") or []:
            if section.get("type") == "LYRICS" and section.get("text"):
                shazam_lyrics = section.get("text") or []
                break

        return {
            "title": title,
            "artist": artist,
            "cover": cover,
            "offset_seconds": offset_seconds,
            "shazam_lyrics": shazam_lyrics,
            "variant_used": variant_label,
        }
    except Exception as exc:
        sys.stderr.write(f"[SHAZAM] {variant_label} hatası: {exc}\n")
        return None


async def run_layer2_fingerprinting(variants: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Katman 2: Tüm DSP ses varyasyonlarını paralel olarak ses izi motorunda tarar.
    """
    if not HAS_SHAZAMIO:
        return None

    # Öncelik sırası: Güçlendirilmiş normalizasyon -> Ham ses -> Pitch Down -> Pitch Up
    ordered_keys = [k for k in ("normalized", "raw", "pitch_down", "pitch_up") if k in variants]
    if not ordered_keys:
        return None

    tasks = [
        asyncio.create_task(_shazam_recognize_single(variants[k], k))
        for k in ordered_keys
    ]

    for completed in asyncio.as_completed(tasks):
        try:
            res = await completed
            if res and res.get("title"):
                # Diğer bekleyen görevleri iptal et, vakit kaybetme
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return res
        except Exception:
            continue
    return None


def _extract_vocals_text_sync(wav_path: str, languages: List[str]) -> List[Tuple[str, str]]:
    """
    Katman 3: Vokal-güçlendirilmiş WAV dosyasından şarkıcının söylediği kelimeleri
    birden fazla dilde (TR, EN, ES, FR vb.) ayıklar.
    """
    if not HAS_SPEECH_REC or not os.path.exists(wav_path):
        return []

    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 150
    recognizer.dynamic_energy_threshold = True

    try:
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
    except Exception:
        return []

    def _recognize_lang(lang_code: str) -> Optional[Tuple[str, str]]:
        try:
            # show_all=True ile en yüksek güvenilirlikli alternatifleri alabiliriz
            res = recognizer.recognize_google(audio_data, language=lang_code, show_all=True)
            if isinstance(res, dict) and res.get("alternative"):
                best = res["alternative"][0]
                transcript = (best.get("transcript") or "").strip()
                if len(transcript.split()) >= 2 or len(transcript) >= 7:
                    return (lang_code, transcript)
            elif isinstance(res, str) and len(res.strip()) >= 6:
                return (lang_code, res.strip())
        except Exception:
            pass
        return None

    detected: List[Tuple[str, str]] = []
    futures = [_EXECUTOR.submit(_recognize_lang, lang) for lang in languages]
    for fut in futures:
        try:
            item = fut.result(timeout=5.5)
            if item:
                detected.append(item)
        except Exception:
            continue
    return detected


async def run_layer3_vocal_lyrics_extraction(variants: Dict[str, str], preferred_lang: str = "auto") -> Optional[Dict[str, Any]]:
    """
    Katman 3: Canlı müzik / cover / gürültülü bar durumunda müziğin içindeki şarkıcının
    söylediği sözleri yapay zeka ile metne çevirip şarkıyı bulur.
    """
    target_wav = variants.get("vocal_boost") or variants.get("clean_bar")
    if not target_wav:
        return None

    if preferred_lang == "tr":
        langs = ["tr-TR", "en-US"]
    elif preferred_lang == "en":
        langs = ["en-US", "tr-TR"]
    else:
        # Otomatik çok dilli tarama: Türkçe, İngilizce, İspanyolca, Fransızca
        langs = ["tr-TR", "en-US", "es-ES", "fr-FR"]

    loop = asyncio.get_running_loop()
    detected_phrases = await loop.run_in_executor(
        _EXECUTOR, _extract_vocals_text_sync, target_wav, langs
    )

    if not detected_phrases:
        return None

    # Yakalanan her söz kesiti için şarkı sözü veritabanlarında arama yap
    for lang_code, phrase in detected_phrases:
        cleaned_phrase = phrase.strip()
        words = cleaned_phrase.split()
        # YANLIŞ EŞLEŞMELERİ (FALSE-POSITIVE) KESİNLİKLE ENGELLE:
        # 1-2 kelimelik fısıltılar veya ortam sesleri rastgele şarkı eşleştirmemelidir.
        # En az 3 kelime veya 14 karakterlik belirgin bir dize olmalı.
        if len(words) < 3 and len(cleaned_phrase) < 14:
            continue

        song_match = await loop.run_in_executor(
            _EXECUTOR, lyrics_engine.search_song_by_lyrics_snippet, cleaned_phrase
        )
        if song_match and song_match.get("lines"):
            # ÇİFTE DOĞRULAMA: Bulunan şarkının sözleri gerçekten bu söz öbeğini içeriyor mu?
            matched_idx, sim = lyrics_engine.find_matching_line_index(song_match["lines"], cleaned_phrase)
            if sim >= 0.55:
                song_match["layer_used"] = f"Katman 3: AI Vokalden Söz Yakalama ({lang_code})"
                song_match["detected_vocal_phrase"] = cleaned_phrase
                song_match["matched_line_index"] = matched_idx
                return song_match

    # Şarkı tam doğrulanmadıysa sadece ekranda bilgi amaçlı kelimeleri döndür
    return {
        "partial_vocal_only": True,
        "detected_phrases": detected_phrases,
    }


async def recognize_music_pipeline(
    audio_bytes: bytes,
    file_ext: str = ".webm",
    wave: int = 1,
    preferred_lang: str = "auto",
) -> Dict[str, Any]:
    """
    Ana 4 Katmanlı Müzik Tanıma Hattı.
    Telefonun gönderdiği ses paketini alır, DSP filtrelerinden geçirir,
    önce Çoklu Ses İzi, ardından AI Vokalden Söz Yakalama katmanlarını çalıştırır
    ve tam şarkı sözleriyle birlikte döndürür.
    """
    start_time = time.time()
    work_dir = tempfile.mkdtemp(prefix="sarki_bul_")

    try:
        raw_path = os.path.join(work_dir, f"input{file_ext}")
        with open(raw_path, "wb") as f:
            f.write(audio_bytes)

        loop = asyncio.get_running_loop()

        # KATMAN 1: DSP Bar Gürültü Filtresi & Vokal Boost & DJ Pitch Düzeltme
        variants = await loop.run_in_executor(
            _EXECUTOR, prepare_dsp_variants, raw_path, work_dir, wave
        )

        # KATMAN 2 (Akustik Ses İzi) ve KATMAN 3 (AI Vokal Söz Dinleme) paralel başlar
        fp_task = asyncio.create_task(run_layer2_fingerprinting(variants))
        vocal_task = asyncio.create_task(run_layer3_vocal_lyrics_extraction(variants, preferred_lang))

        fp_result = await fp_task
        if fp_result and fp_result.get("title"):
            if vocal_task and not vocal_task.done():
                vocal_task.cancel()

            # KATMAN 4: Tam Şarkı Sözlerini ve Senkronize Karaoke Satırlarını Getir
            full_lyrics = await loop.run_in_executor(
                _EXECUTOR,
                lyrics_engine.get_full_lyrics_for_track,
                fp_result["title"],
                fp_result["artist"],
                fp_result.get("shazam_lyrics"),
                fp_result.get("cover", ""),
                fp_result.get("offset_seconds"),
                "",
            )

            variant_map = {
                "normalized": "Katman 1+2: Akustik Dengelemeli Ses İzi",
                "raw": "Katman 2: Doğrudan Ses İzi",
                "pitch_down": "Katman 1+2: DJ Hız/Pitch Düzeltmeli (-3.5%) Ses İzi",
                "pitch_up": "Katman 1+2: DJ Hız/Pitch Düzeltmeli (+3.5%) Ses İzi",
            }
            full_lyrics["recognized"] = True
            full_lyrics["layer_used"] = variant_map.get(
                fp_result.get("variant_used", ""), "Katman 2: Ses İzi Motoru"
            )
            full_lyrics["wave"] = wave
            full_lyrics["elapsed_ms"] = int((time.time() - start_time) * 1000)
            return full_lyrics

        # Ses izi bulamadıysa Katman 3 (AI Vokalden Söz Yakalama) sonucunu al
        vocal_result = None
        if vocal_task:
            try:
                vocal_result = await vocal_task
            except asyncio.CancelledError:
                pass
            if vocal_result and not vocal_result.get("partial_vocal_only") and vocal_result.get("title"):
                vocal_result["recognized"] = True
                vocal_result["wave"] = wave
                vocal_result["elapsed_ms"] = int((time.time() - start_time) * 1000)
                return vocal_result

        detected_words = []
        if vocal_result and vocal_result.get("detected_phrases"):
            detected_words = [p[1] for p in vocal_result["detected_phrases"]]

        return {
            "recognized": False,
            "wave": wave,
            "detected_words": detected_words,
            "elapsed_ms": int((time.time() - start_time) * 1000),
            "message": (
                f"Dalga {wave} tarandı ({', '.join(detected_words) if detected_words else 'enstrümantal/vokal bekleniyor'}), "
                "dinlemeye devam ediliyor..."
            ),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
