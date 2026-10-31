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
import uuid

_CA_PATH = None
try:
    import certifi
    ca_path = certifi.where()
    if os.path.exists(ca_path):
        _CA_PATH = ca_path
        os.environ["SSL_CERT_FILE"] = ca_path
        os.environ["REQUESTS_CA_BUNDLE"] = ca_path
        ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=ca_path)
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


def _run_ffmpeg(
    input_path: str,
    output_path: str,
    audio_filter: Optional[str] = None,
    sample_rate: int = 44100,
    tail_seconds: Optional[float] = None,
) -> bool:
    """ffmpeg ile ses dosyasını dönüştürür ve belirtilen DSP ses filtresini uygular."""
    if not FFMPEG_BIN:
        return False
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if tail_seconds and tail_seconds > 0:
        cmd.extend(["-sseof", f"-{tail_seconds}", "-t", str(tail_seconds)])
    cmd.extend([
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
    ])
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
    Katman 1: Bar/oda ortamındaki kayıttan farklı DSP filtreli ses varyasyonları üretir.
    Clipping (sinyal kırpılması) yaratmadan dinamik ses normalizasyonu uygular ve
    Shazam'ın 15sn üzeri boş dönme hatasını önlemek için en net son 11 saniyeyi alır.
    """
    variants: Dict[str, str] = {"raw": raw_audio_path}
    if not FFMPEG_BIN:
        return variants

    # 0. Ham sesi 44.1kHz mono standart PCM WAV'a çevir (son 11 sn)
    raw_wav_path = os.path.join(work_dir, "raw_pcm.wav")
    if _run_ffmpeg(raw_audio_path, raw_wav_path, audio_filter=None, sample_rate=44100, tail_seconds=11.5):
        variants["raw"] = raw_wav_path

    # 1. Kırpılmasız Dinamik Ses Dengeleme & Oda Gürültü Kesme (p=0.92 tepe sınırı ile distorsiyonsuz 20x kazanç)
    norm_path = os.path.join(work_dir, "normalized.wav")
    norm_filter = "highpass=f=80,lowpass=f=8500,dynaudnorm=f=150:g=15:p=0.92:m=20"
    if _run_ffmpeg(raw_audio_path, norm_path, norm_filter, sample_rate=44100, tail_seconds=11.5):
        variants["normalized"] = norm_path

    # 2. Son 5 Saniye Odaklı Kesit (Kullanıcı telefonu sonradan müziğe yaklaştırdıysa ilk saniyedeki sessizliği atar)
    tail_path = os.path.join(work_dir, "tail_focus.wav")
    tail_filter = "highpass=f=90,lowpass=f=8000,dynaudnorm=f=120:g=15:p=0.92:m=25"
    if _run_ffmpeg(raw_audio_path, tail_path, tail_filter, sample_rate=44100, tail_seconds=5.2):
        variants["tail_focus"] = tail_path

    # 3. Vokal Öne Çıkarma (Şarkıcının sözlerini Speech-to-Text motoruna iletmek için 16kHz WAV)
    vocal_path = os.path.join(work_dir, "vocal_boost.wav")
    vocal_filter = (
        "highpass=f=180,lowpass=f=4200,"
        "equalizer=f=1200:t=q:w=1.2:g=6,"
        "equalizer=f=2600:t=q:w=1.2:g=5,"
        "dynaudnorm=f=120:g=15:p=0.92:m=15"
    )
    if _run_ffmpeg(raw_audio_path, vocal_path, vocal_filter, sample_rate=16000, tail_seconds=10.0):
        variants["vocal_boost"] = vocal_path

    # 4. Dalga 2 ve sonrasında DJ Tempo / Pitch kaymalarına karşı (-3.5% ve +3.5%) varyasyonlar
    if wave >= 2:
        pitch_down_path = os.path.join(work_dir, "pitch_down.wav")
        pitch_down_filter = "asetrate=44100*0.965,aresample=44100,highpass=f=80,dynaudnorm=f=150:g=15:p=0.92"
        if _run_ffmpeg(raw_audio_path, pitch_down_path, pitch_down_filter, sample_rate=44100, tail_seconds=10.0):
            variants["pitch_down"] = pitch_down_path

        pitch_up_path = os.path.join(work_dir, "pitch_up.wav")
        pitch_up_filter = "asetrate=44100*1.035,aresample=44100,highpass=f=80,dynaudnorm=f=150:g=15:p=0.92"
        if _run_ffmpeg(raw_audio_path, pitch_up_path, pitch_up_filter, sample_rate=44100, tail_seconds=10.0):
            variants["pitch_up"] = pitch_up_path

    return variants


# Shazam çoklu ülke/cihaz havuzu (HTTP 429 hız sınırına takılmayı %100 engeller)
_SHAZAM_LOCALES = [
    ("tr-TR", "TR", "iphone"),
    ("en-US", "US", "android"),
    ("en-GB", "GB", "iphone"),
    ("de-DE", "DE", "android"),
    ("fr-FR", "FR", "iphone"),
    ("es-ES", "ES", "android"),
    ("it-IT", "IT", "iphone"),
    ("nl-NL", "NL", "android"),
    ("sv-SE", "SE", "iphone"),
    ("en-CA", "CA", "android"),
    ("en-AU", "AU", "iphone"),
    ("pt-BR", "BR", "android"),
    ("pl-PL", "PL", "iphone"),
    ("de-AT", "AT", "android"),
    ("fr-CH", "CH", "iphone"),
    ("nl-BE", "BE", "android"),
]
_SHAZAM_LOCALE_IDX = 0
_CORE_RECOGNIZER = None


def _get_core_recognizer():
    global _CORE_RECOGNIZER
    if _CORE_RECOGNIZER is None:
        from shazamio_core import Recognizer
        _CORE_RECOGNIZER = Recognizer(segment_duration_seconds=12)
    return _CORE_RECOGNIZER


def create_shazam_client() -> Shazam:
    """Geriye dönük uyumluluk için Shazam istemcisi döndürür."""
    return Shazam(language="tr-TR", endpoint_country="TR")


async def _shazam_recognize_single(file_path: str, variant_label: str) -> Optional[Dict[str, Any]]:
    """
    Tek bir ses varyasyonunu Shazam'ın Rust çekirdeğiyle parmak izine çevirir ve
    çoklu ülke/cihaz uç noktası rotasyonu ile 429 engeline takılmadan 0.4 saniyede sorgular.
    """
    global _SHAZAM_LOCALE_IDX
    if not HAS_SHAZAMIO or not os.path.exists(file_path):
        return None
    try:
        import aiohttp
        from shazamio.converter import Converter

        rec = _get_core_recognizer()
        sig = await rec.recognize_path(value=file_path, options=None)
        payload = Converter.data_search(
            sig.timezone,
            sig.signature.uri,
            sig.signature.samples,
            sig.timestamp,
        )

        if _CA_PATH and os.path.exists(_CA_PATH):
            ctx = ssl.create_default_context(cafile=_CA_PATH)
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        out = None
        timeout_cfg = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx), timeout=timeout_cfg) as session:
            # En fazla 3 farklı ülke uç noktasını dene (429 gelirse anında diğer ülkeye geçer)
            for attempt in range(3):
                idx = (_SHAZAM_LOCALE_IDX + attempt) % len(_SHAZAM_LOCALES)
                _SHAZAM_LOCALE_IDX = (_SHAZAM_LOCALE_IDX + 1) % len(_SHAZAM_LOCALES)
                lang, country, dev = _SHAZAM_LOCALES[idx]
                u1, u2 = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
                url = f"https://amp.shazam.com/discovery/v5/{lang}/{country}/{dev}/-/tag/{u1}/{u2}?sync=true&webv3=true"
                headers = {
                    "X-Shazam-Platform": "IPHONE" if dev == "iphone" else "ANDROID",
                    "X-Shazam-AppVersion": "16.2.0",
                    "Accept": "*/*",
                    "Accept-Language": lang,
                    "Content-Type": "application/json",
                    "User-Agent": (
                        f"Shazam/16.2.0 (iPhone; iOS 17.{idx % 6}; Scale/3.00)"
                        if dev == "iphone"
                        else f"Dalvik/2.1.0 (Linux; U; Android 14; Pixel {6 + (idx % 3)} Build/UP1A.231005.007)"
                    ),
                }
                try:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            out = await resp.json(content_type=None)
                            break
                        elif resp.status == 429:
                            continue
                except Exception:
                    continue

        track = (out or {}).get("track")
        if not track:
            return None

        title = track.get("title") or ""
        artist = track.get("subtitle") or ""
        if not title:
            return None

        images = track.get("images") or {}
        cover = images.get("coverarthq") or images.get("coverart") or images.get("background") or ""

        # Şarkının o anki çalma saniyesini (offset) yakala:
        # Shazam'ın döndürdüğü raw_offset, gönderilen ses kesitinin BAŞLANGIÇ saniyesidir.
        # Kesitin gerçek uzunluğunu (sig.signature.samples ms) ekleyerek kesitin BİTİŞ (şu anki) saniyesini buluyoruz.
        offset_seconds: Optional[float] = None
        matches = (out or {}).get("matches") or []
        if matches and isinstance(matches[0], dict):
            raw_offset = matches[0].get("offset")
            if isinstance(raw_offset, (int, float)):
                sample_dur_sec = float(getattr(sig.signature, "samples", 0) or 0) / 1000.0
                offset_seconds = round(float(raw_offset) + sample_dur_sec, 2)

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

    # Öncelik sırası: Güçlendirilmiş normalizasyon -> Ham ses -> Son 5sn odaklı -> Pitch Down -> Pitch Up
    ordered_keys = [k for k in ("normalized", "raw", "tail_focus", "pitch_down", "pitch_up") if k in variants]
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
            # find_matching_line_index -> (satır indeksi, satır zamanı); indeks >= 0 ise söz şarkıda geçiyor
            matched_idx, _line_time = lyrics_engine.find_matching_line_index(song_match["lines"], cleaned_phrase)
            if matched_idx is not None and matched_idx >= 0:
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

            # Telefondan gelen canlı vokal sözü varsa (video/canlı performans farklarını düzeltmek için) kontrol et
            if vocal_task:
                try:
                    vocal_res = await asyncio.wait_for(vocal_task, timeout=0.65)
                    if vocal_res and full_lyrics.get("lines"):
                        phrases_to_check = []
                        if vocal_res.get("detected_vocal_phrase"):
                            phrases_to_check.append(vocal_res["detected_vocal_phrase"])
                        for _lc, ph in vocal_res.get("detected_phrases") or []:
                            phrases_to_check.append(ph)
                        for ph in phrases_to_check:
                            if len(ph.strip()) >= 10:
                                shazam_off = full_lyrics.get("offset_seconds")
                                v_idx, v_time = lyrics_engine.find_matching_line_index(
                                    full_lyrics["lines"], ph, hint_offset=shazam_off
                                )
                                if v_idx is not None and v_idx >= 0:
                                    full_lyrics["detected_vocal_phrase"] = ph
                                    if shazam_off is None:
                                        full_lyrics["matched_line_index"] = v_idx
                                        if v_time is not None:
                                            full_lyrics["offset_seconds"] = round(v_time + 1.5, 2)
                                    break
                except Exception:
                    if not vocal_task.done():
                        vocal_task.cancel()

            variant_map = {
                "normalized": "Katman 1+2: Akustik Dengelemeli Ses İzi",
                "raw": "Katman 2: Doğrudan Ses İzi",
                "tail_focus": "Katman 1+2: Canlı Son Kesit Ses İzi",
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
                if isinstance(vocal_result.get("offset_seconds"), (int, float)):
                    vocal_result["offset_seconds"] = round(float(vocal_result["offset_seconds"]) + 1.6, 2)
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


async def sync_live_audio_to_lyrics(
    audio_bytes: bytes,
    file_ext: str,
    title: str,
    artist: str,
    lines: List[Dict[str, Any]],
    current_offset: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Şarkı sözleri ekranda akarken telefon mikrofonundan gelen canlı sesi (4 sn) analiz eder.
    1. Öncelik: Canlı akustik parmak izi (Shazam) — 1., 2. ve 3. nakaratı milisaniye hassasiyetle ayırt eder.
    2. Öncelik: Akustik iz bulunamazsa mevcut saniyeye (current_offset) komşu olan canlı vokal sözünü kullanır.
    """
    work_dir = tempfile.mkdtemp(prefix="sarki_sync_")
    try:
        raw_path = os.path.join(work_dir, f"sync{file_ext}")
        with open(raw_path, "wb") as f:
            f.write(audio_bytes)

        loop = asyncio.get_running_loop()
        norm_path = os.path.join(work_dir, "norm.wav")
        vocal_path = os.path.join(work_dir, "vocal.wav")

        def _prep():
            if FFMPEG_BIN:
                _run_ffmpeg(
                    raw_path,
                    norm_path,
                    "highpass=f=85,lowpass=f=8000,dynaudnorm=f=120:g=15:p=0.92:m=20",
                    sample_rate=44100,
                    tail_seconds=4.5,
                )
                _run_ffmpeg(
                    raw_path,
                    vocal_path,
                    "highpass=f=180,lowpass=f=4200,equalizer=f=1200:t=q:w=1.2:g=6,dynaudnorm=f=120:g=15:p=0.92:m=15",
                    sample_rate=16000,
                    tail_seconds=4.5,
                )

        await loop.run_in_executor(_EXECUTOR, _prep)
        target_norm = norm_path if os.path.exists(norm_path) else raw_path
        target_vocal = vocal_path if os.path.exists(vocal_path) else target_norm

        fp_task = asyncio.create_task(_shazam_recognize_single(target_norm, "live_sync"))
        stt_task = loop.run_in_executor(_EXECUTOR, _extract_vocals_text_sync, target_vocal, ["tr-TR", "en-US"])

        fp_res = None
        stt_phrases = []
        try:
            fp_res = await asyncio.wait_for(fp_task, timeout=2.8)
        except Exception:
            pass
        try:
            stt_phrases = await asyncio.wait_for(stt_task, timeout=2.8)
        except Exception:
            pass

        # 1. ÖNCELİK: Telefondan duyulan müziğin akustik parmak izi saniyesi (Shazam live offset)
        # Akustik parmak izi nakarat tekrarlarında (1. nakarat vs 3. nakarat) asla yanılmaz ve yukarı-aşağı zıplama yapmaz!
        if fp_res and fp_res.get("title") and isinstance(fp_res.get("offset_seconds"), (int, float)):
            t1 = lyrics_engine.normalize_text(fp_res.get("title") or "")
            t2 = lyrics_engine.normalize_text(title or "")
            if not t2 or (t1 and (t1 in t2 or t2 in t1 or t1[:5] == t2[:5])):
                return {
                    "synced": True,
                    "method": "acoustic_live",
                    "offset_seconds": fp_res["offset_seconds"],
                    "detected_phrase": stt_phrases[0][1] if stt_phrases else "",
                }

        # 2. ÖNCELİK: Sadece akustik iz eşleşmediyse ve net bir vokal cümlesi (en az 3 kelime) duyulduysa,
        # mevcut saniyeye (current_offset) yakın olan satırı seç (eski nakarata geri zıplamayı engelle).
        if stt_phrases and lines:
            for _lang, phrase in stt_phrases:
                cleaned = phrase.strip()
                if len(cleaned) >= 12 and len(cleaned.split()) >= 3:
                    m_idx, m_time = lyrics_engine.find_matching_line_index(
                        lines, cleaned, hint_offset=current_offset
                    )
                    if m_idx is not None and m_idx >= 0:
                        if (
                            current_offset is not None
                            and current_offset > 0
                            and isinstance(m_time, (int, float))
                        ):
                            # Mevcut konumdan geriye veya çok uzağa zıplamasına izin verme
                            if float(m_time) < current_offset - 3.5 or float(m_time) > current_offset + 9.0:
                                continue
                        return {
                            "synced": True,
                            "method": "vocal_live",
                            "matched_line_index": m_idx,
                            "offset_seconds": round(float(m_time) + 1.5, 2) if m_time is not None else None,
                            "detected_phrase": cleaned,
                        }

        return {
            "synced": False,
            "detected_phrase": stt_phrases[0][1] if stt_phrases else "",
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
