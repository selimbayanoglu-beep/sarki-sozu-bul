#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Şarkı Sözü Bul — 4 Katmanlı Akıllı Müzik & Vokal Tanıma Motoru (recognition_engine.py)

Katman 1 (Spektral Korumalı DSP Ses Hazırlama):
  - Ham sesi Shazam spektrogram tepe noktalarını bozmadan standart 44.1kHz mono PCM WAV'a dönüştürür.
  - Gürültülü ortamlar için yumuşak bant geçiren (70Hz-10kHz) müzik kopyası ve son 5.5sn kesiti üretir.
  - Konuşma/vokal yakalama (STT) için doğal 16kHz ve vokal-odaklı 16kHz ses kopyaları hazırlar.

Katman 2 (Shazam V5 Akustik Ses İzi Motoru):
  - Resmi Shazam v5 discovery protokolü ve çoklu ülke rotasyonu ile hız sınırına takılmadan
    şarkıyı ve tam o anki çalma saniyesini (offset_seconds) tespit eder.

Katman 3 (Çift Kanallı AI Vokal & Tarayıcı Canlı Söz Yakalama):
  - Canlı müzik, akustik cover veya mırıldanma/söyleme durumunda hem tarayıcıdan gelen
    canlı metni hem de sunucu STT motorunu kullanarak söylenen sözlerden veya şarkı adından parçayı bulur.

Katman 4 (Çok Kaynaklı Senkronize Karaoke Söz Eşleştirme):
  - Bulunan şarkının milisaniye senkronlu (LRC / YouTube Music) veya tam metin sözlerini getirir.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from random import choice
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
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
    from shazamio import Shazam
    from shazamio_core import Recognizer
    HAS_SHAZAMIO = True
except Exception:
    HAS_SHAZAMIO = False

try:
    from shazamio.user_agent import USER_AGENTS
except Exception:
    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    ]

try:
    import speech_recognition as sr
    HAS_SPEECH_REC = True
except Exception:
    HAS_SPEECH_REC = False

FFMPEG_BIN = shutil.which("ffmpeg")
if not FFMPEG_BIN:
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths(weak=True)
        FFMPEG_BIN = shutil.which("ffmpeg")
    except Exception:
        pass

_EXECUTOR = ThreadPoolExecutor(max_workers=8)


def refresh_optional_dependencies() -> None:
    """Arka planda kurulan shazamio, SpeechRecognition ve ffmpeg araçlarını canlı olarak devreye alır."""
    global HAS_SHAZAMIO, HAS_SPEECH_REC, FFMPEG_BIN, Shazam, Recognizer, sr, _CA_PATH
    if not _CA_PATH:
        try:
            import certifi
            _CA_PATH = certifi.where()
            os.environ["SSL_CERT_FILE"] = _CA_PATH
            os.environ["REQUESTS_CA_BUNDLE"] = _CA_PATH
        except Exception:
            pass
    if not HAS_SHAZAMIO:
        try:
            from shazamio import Shazam as _Shazam
            from shazamio_core import Recognizer as _Recognizer
            Shazam = _Shazam
            Recognizer = _Recognizer
            HAS_SHAZAMIO = True
        except Exception:
            pass
    if not HAS_SPEECH_REC:
        try:
            import speech_recognition as _sr
            sr = _sr
            HAS_SPEECH_REC = True
        except Exception:
            pass
    if not FFMPEG_BIN:
        FFMPEG_BIN = shutil.which("ffmpeg")


def _run_ffmpeg(
    input_path: str,
    output_path: str,
    audio_filter: Optional[str] = None,
    sample_rate: int = 44100,
    tail_seconds: Optional[float] = None,
) -> bool:
    """ffmpeg ile ses dosyasını dönüştürür ve belirtilen ses filtresini uygular."""
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
        "-acodec",
        "pcm_s16le",
    ])
    if audio_filter:
        cmd.extend(["-af", audio_filter])
    cmd.append(output_path)
    try:
        subprocess.run(cmd, check=True, timeout=8.0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            return True
    except Exception:
        pass

    # Eğer kapsayıcıda süre bilgisi yoksa (-sseof başarısız olduysa), -sseof olmadan tekrar dene
    if tail_seconds:
        cmd_fallback = [
            FFMPEG_BIN,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            input_path,
            "-t",
            str(tail_seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-acodec",
            "pcm_s16le",
        ]
        if audio_filter:
            cmd_fallback.extend(["-af", audio_filter])
        cmd_fallback.append(output_path)
        try:
            subprocess.run(cmd_fallback, check=True, timeout=8.0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 1024
        except Exception:
            return False
    return False


def prepare_dsp_variants(raw_audio_path: str, work_dir: str, wave: int = 1) -> Dict[str, str]:
    """
    Katman 1: Kayıttan hem spektrogram-korumalı akustik parmak izi kopyaları
    hem de vokal konuşma tanıma (STT) kopyaları üretir.
    """
    variants: Dict[str, str] = {"raw": raw_audio_path}
    if not FFMPEG_BIN:
        return variants

    # 1. Doğrusal (Spektrogram Korumalı) 44.1kHz PCM WAV (Son 11.5 sn)
    # Shazam'ın frekans tepe noktalarını (constellation map) bozmamak için agresif dynaudnorm uygulanmaz.
    raw_wav_path = os.path.join(work_dir, "raw_pcm.wav")
    if _run_ffmpeg(raw_audio_path, raw_wav_path, audio_filter=None, sample_rate=44100, tail_seconds=11.5):
        variants["raw"] = raw_wav_path

    # 2. Yumuşak Bar/Ortam Dengeli Müzik Kopyası (Geniş 400ms pencereli, distorsiyonsuz)
    clean_music_path = os.path.join(work_dir, "clean_music.wav")
    clean_filter = "highpass=f=70,lowpass=f=10000,dynaudnorm=f=400:g=31:p=0.92:m=6"
    if _run_ffmpeg(raw_audio_path, clean_music_path, clean_filter, sample_rate=44100, tail_seconds=11.5):
        variants["clean_music"] = clean_music_path

    # 3. Son 5.5 Saniye Odaklı Kesit (İlk saniyelerdeki sessizliği veya konuşmayı atar)
    tail_path = os.path.join(work_dir, "tail_focus.wav")
    if _run_ffmpeg(raw_audio_path, tail_path, clean_filter, sample_rate=44100, tail_seconds=5.5):
        variants["tail_focus"] = tail_path

    # 4. STT Kanal 1: Doğal 16kHz Ses (Net söylenen sözler ve şarkı adları için)
    stt_clean_path = os.path.join(work_dir, "stt_clean.wav")
    stt_clean_filter = "highpass=f=90,lowpass=f=7500,dynaudnorm=f=300:g=21:p=0.92:m=8"
    if _run_ffmpeg(raw_audio_path, stt_clean_path, stt_clean_filter, sample_rate=16000, tail_seconds=10.5):
        variants["stt_clean"] = stt_clean_path

    # 5. STT Kanal 2: Vokal Frekansları Güçlendirilmiş 16kHz Ses (Enstrüman üstü vokal için)
    vocal_path = os.path.join(work_dir, "vocal_boost.wav")
    vocal_filter = (
        "highpass=f=170,lowpass=f=4500,"
        "equalizer=f=1400:t=q:w=1.0:g=4,"
        "equalizer=f=2600:t=q:w=1.0:g=3,"
        "dynaudnorm=f=250:g=15:p=0.92:m=10"
    )
    if _run_ffmpeg(raw_audio_path, vocal_path, vocal_filter, sample_rate=16000, tail_seconds=10.5):
        variants["vocal_boost"] = vocal_path

    # 6. Dalga 2 ve sonrasında DJ Tempo / Pitch kaymalarına karşı (-3% ve +3%) varyasyonlar
    if wave >= 2:
        pitch_down_path = os.path.join(work_dir, "pitch_down.wav")
        pitch_down_filter = "asetrate=44100*0.97,aresample=44100,highpass=f=75"
        if _run_ffmpeg(raw_audio_path, pitch_down_path, pitch_down_filter, sample_rate=44100, tail_seconds=9.5):
            variants["pitch_down"] = pitch_down_path

        pitch_up_path = os.path.join(work_dir, "pitch_up.wav")
        pitch_up_filter = "asetrate=44100*1.03,aresample=44100,highpass=f=75"
        if _run_ffmpeg(raw_audio_path, pitch_up_path, pitch_up_filter, sample_rate=44100, tail_seconds=9.5):
            variants["pitch_up"] = pitch_up_path

    return variants


_SHAZAM_LOCALES = [
    ("tr-TR", "TR"),
    ("en-US", "US"),
    ("en-GB", "GB"),
    ("de-DE", "DE"),
    ("fr-FR", "FR"),
    ("es-ES", "ES"),
    ("it-IT", "IT"),
    ("nl-NL", "NL"),
]
_SHAZAM_LOCALE_IDX = 0


def create_shazam_client() -> Any:
    """Geriye dönük uyumluluk için Shazam istemcisi döndürür."""
    return Shazam(language="tr-TR", endpoint_country="TR")


async def _shazam_recognize_single(file_path: str, variant_label: str) -> Optional[Dict[str, Any]]:
    """
    Tek bir ses dosyasını Shazam'ın Rust çekirdeğiyle (shazamio_core) parmak izine çevirir
    ve tam Shazam v5 discovery uç noktası ile sorgular.
    """
    global _SHAZAM_LOCALE_IDX
    if not HAS_SHAZAMIO or not os.path.exists(file_path):
        return None
    try:
        import aiohttp
        from shazamio.converter import Converter

        rec = Recognizer(segment_duration_seconds=12)
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
        timeout_cfg = aiohttp.ClientTimeout(total=5.5)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx), timeout=timeout_cfg) as session:
            for attempt in range(3):
                idx = (_SHAZAM_LOCALE_IDX + attempt) % len(_SHAZAM_LOCALES)
                _SHAZAM_LOCALE_IDX = (_SHAZAM_LOCALE_IDX + 1) % len(_SHAZAM_LOCALES)
                lang, country = _SHAZAM_LOCALES[idx]
                u1, u2 = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
                url = (
                    f"https://amp.shazam.com/discovery/v5/{lang}/{country}/iphone/-/tag/{u1}/{u2}"
                    "?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3"
                    "&sharehub=true&hubv5minorversion=v5.1&hidelb=true&video=v3"
                )
                headers = {
                    "X-Shazam-Platform": "IPHONE",
                    "X-Shazam-AppVersion": "14.1.0",
                    "Accept": "*/*",
                    "Accept-Language": lang,
                    "Accept-Encoding": "gzip, deflate",
                    "Content-Type": "application/json",
                    "User-Agent": choice(USER_AGENTS),
                }
                try:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            out = await resp.json(content_type=None)
                            break
                        elif resp.status == 429:
                            await asyncio.sleep(0.15)
                            continue
                except Exception:
                    continue

        track = (out or {}).get("track")
        if not track:
            return None

        title = (track.get("title") or "").strip()
        artist = (track.get("subtitle") or "").strip()
        if not title:
            return None

        images = track.get("images") or {}
        cover = images.get("coverarthq") or images.get("coverart") or images.get("background") or ""

        offset_seconds: Optional[float] = None
        matches = (out or {}).get("matches") or []
        if matches and isinstance(matches[0], dict):
            raw_offset = matches[0].get("offset")
            if isinstance(raw_offset, (int, float)):
                sample_dur_sec = float(getattr(sig.signature, "samples", 0) or 0) / 1000.0
                offset_seconds = round(float(raw_offset) + sample_dur_sec, 2)

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
    Katman 2: Önce doğrusal ham sesi ve yumuşak dengelenmiş sesi dener;
    eşleşme çıkmazsa son kesit ve DJ pitch varyasyonlarını tarar.
    """
    if not HAS_SHAZAMIO:
        return None

    # Aşama 1: En yüksek doğruluklu 'raw' ve 'clean_music' varyasyonları
    primary_keys = [k for k in ("raw", "clean_music") if k in variants]
    if primary_keys:
        tasks = [asyncio.create_task(_shazam_recognize_single(variants[k], k)) for k in primary_keys]
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

    # Aşama 2: Yardımcı varyasyonlar (tail_focus, pitch_down, pitch_up)
    secondary_keys = [k for k in ("tail_focus", "pitch_down", "pitch_up") if k in variants]
    if secondary_keys:
        tasks = [asyncio.create_task(_shazam_recognize_single(variants[k], k)) for k in secondary_keys]
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


def _extract_vocals_text_sync(wav_paths: List[str], languages: List[str]) -> List[Tuple[str, str]]:
    """
    Katman 3: Hem doğal 16kHz sesten hem de vokal-güçlendirilmiş WAV dosyasından
    şarkıcının veya kullanıcının söylediği kelimeleri çok dilli olarak ayıklar.
    """
    if not HAS_SPEECH_REC:
        return []

    valid_paths = [p for p in wav_paths if p and os.path.exists(p)]
    if not valid_paths:
        return []

    audio_samples: List[Any] = []
    for p in valid_paths:
        try:
            rec = sr.Recognizer()
            rec.energy_threshold = 120
            rec.dynamic_energy_threshold = True
            with sr.AudioFile(p) as source:
                audio_samples.append(rec.record(source))
        except Exception:
            continue

    if not audio_samples:
        return []

    def _recognize_pair(audio_data: Any, lang_code: str) -> List[Tuple[str, str]]:
        out_list: List[Tuple[str, str]] = []
        try:
            recognizer = sr.Recognizer()
            res = recognizer.recognize_google(audio_data, language=lang_code, show_all=True)
            if isinstance(res, dict) and res.get("alternative"):
                for alt in res["alternative"][:2]:
                    transcript = (alt.get("transcript") or "").strip()
                    if len(transcript.split()) >= 2 or len(transcript) >= 6:
                        out_list.append((lang_code, transcript))
            elif isinstance(res, str) and len(res.strip()) >= 5:
                out_list.append((lang_code, res.strip()))
        except Exception:
            pass
        return out_list

    detected: List[Tuple[str, str]] = []
    seen_norm = set()
    futures = [
        _EXECUTOR.submit(_recognize_pair, aud, lang)
        for aud in audio_samples
        for lang in languages
    ]
    for fut in futures:
        try:
            items = fut.result(timeout=5.5) or []
            for lang_code, txt in items:
                norm = lyrics_engine.normalize_text(txt)
                if norm and norm not in seen_norm:
                    seen_norm.add(norm)
                    detected.append((lang_code, txt))
        except Exception:
            continue
    return detected


async def run_layer3_vocal_lyrics_extraction(
    variants: Dict[str, str],
    preferred_lang: str = "auto",
    browser_transcript: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Katman 3: Canlı müzik / cover / mırıldanma veya söz söyleme durumunda
    yakalanan kelimelerden şarkıyı ve o anki satırını bulur.
    """
    detected_phrases: List[Tuple[str, str]] = []

    if browser_transcript and len(browser_transcript.strip()) >= 4:
        detected_phrases.append(("tarayıcı-canlı", browser_transcript.strip()))

    wav_candidates: List[str] = []
    if variants.get("stt_clean"):
        wav_candidates.append(variants["stt_clean"])
    if variants.get("vocal_boost"):
        wav_candidates.append(variants["vocal_boost"])
    if not wav_candidates and variants.get("raw"):
        wav_candidates.append(variants["raw"])

    if preferred_lang == "tr":
        langs = ["tr-TR", "en-US"]
    elif preferred_lang == "en":
        langs = ["en-US", "tr-TR"]
    else:
        langs = ["tr-TR", "en-US", "es-ES", "fr-FR"]

    loop = asyncio.get_running_loop()
    if wav_candidates:
        stt_phrases = await loop.run_in_executor(
            _EXECUTOR, _extract_vocals_text_sync, wav_candidates, langs
        )
        for item in stt_phrases:
            detected_phrases.append(item)

    if not detected_phrases:
        return None

    for lang_code, phrase in detected_phrases:
        cleaned_phrase = phrase.strip()
        words = cleaned_phrase.split()
        if len(words) < 2 and len(cleaned_phrase) < 8:
            continue

        song_match = await loop.run_in_executor(
            _EXECUTOR, lyrics_engine.search_song_by_lyrics_snippet, cleaned_phrase
        )
        if not song_match or not song_match.get("title"):
            continue

        lines = song_match.get("lines") or []
        matched_idx, line_time = lyrics_engine.find_matching_line_index(lines, cleaned_phrase)

        # Doğrulama 1: Yakalanan söz öbeği şarkının sözlerinde doğrudan geçiyor mu?
        if matched_idx is not None and matched_idx >= 0:
            song_match["layer_used"] = f"Katman 3: Vokalden Söz Yakalama ({lang_code})"
            song_match["detected_vocal_phrase"] = cleaned_phrase
            song_match["matched_line_index"] = matched_idx
            if line_time is not None:
                song_match["offset_seconds"] = line_time
            return song_match

        # Doğrulama 2: Kullanıcı şarkının adını / sanatçısını söylemiş olabilir mi?
        title_sim = lyrics_engine.title_artist_similarity(
            cleaned_phrase,
            "",
            f"{song_match.get('artist', '')} {song_match.get('title', '')}",
            "",
        )
        if title_sim >= 0.62 and lines:
            song_match["layer_used"] = f"Katman 3: Sesli Şarkı Adı Tanıma ({lang_code})"
            song_match["detected_vocal_phrase"] = cleaned_phrase
            song_match["matched_line_index"] = 0
            return song_match

    return {
        "partial_vocal_only": True,
        "detected_phrases": detected_phrases,
    }


async def recognize_music_pipeline(
    audio_bytes: bytes,
    file_ext: str = ".wav",
    wave: int = 1,
    preferred_lang: str = "auto",
    browser_transcript: str = "",
) -> Dict[str, Any]:
    """
    Ana 4 Katmanlı Müzik & Vokal Tanıma Hattı.
    """
    if not HAS_SHAZAMIO or not HAS_SPEECH_REC or not FFMPEG_BIN:
        refresh_optional_dependencies()
    start_time = time.time()
    work_dir = tempfile.mkdtemp(prefix="sarki_bul_")

    try:
        raw_path = os.path.join(work_dir, f"input{file_ext}")
        with open(raw_path, "wb") as f:
            f.write(audio_bytes)

        loop = asyncio.get_running_loop()

        # KATMAN 1: Spektrogram Korumalı DSP Varyasyonları + STT Ses Hazırlığı
        variants = await loop.run_in_executor(
            _EXECUTOR, prepare_dsp_variants, raw_path, work_dir, wave
        )

        # KATMAN 2 (Shazam Akustik Ses İzi) ve KATMAN 3 (AI Vokal Söz Dinleme) paralel başlar
        fp_task = asyncio.create_task(run_layer2_fingerprinting(variants))
        vocal_task = asyncio.create_task(
            run_layer3_vocal_lyrics_extraction(variants, preferred_lang, browser_transcript)
        )

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

            # Telefondan gelen canlı vokal sözü varsa kontrol et
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
                            if len(ph.strip()) >= 8:
                                shazam_off = full_lyrics.get("offset_seconds")
                                v_idx, v_time = lyrics_engine.find_matching_line_index(
                                    full_lyrics["lines"], ph, hint_offset=shazam_off
                                )
                                if v_idx is not None and v_idx >= 0:
                                    full_lyrics["detected_vocal_phrase"] = ph
                                    if shazam_off is None:
                                        full_lyrics["matched_line_index"] = v_idx
                                        if v_time is not None:
                                            full_lyrics["offset_seconds"] = round(v_time + 1.2, 2)
                                    break
                except Exception:
                    if not vocal_task.done():
                        vocal_task.cancel()

            variant_map = {
                "raw": "Katman 2: Doğrudan Akustik Ses İzi (Shazam)",
                "clean_music": "Katman 1+2: Gürültü Filtreli Ses İzi (Shazam)",
                "tail_focus": "Katman 1+2: Son Kesit Ses İzi (Shazam)",
                "pitch_down": "Katman 1+2: DJ Hız/Pitch (-3%) Ses İzi",
                "pitch_up": "Katman 1+2: DJ Hız/Pitch (+3%) Ses İzi",
            }
            full_lyrics["recognized"] = True
            full_lyrics["layer_used"] = variant_map.get(
                fp_result.get("variant_used", ""), "Katman 2: Ses İzi Motoru"
            )
            full_lyrics["wave"] = wave
            full_lyrics["elapsed_ms"] = int((time.time() - start_time) * 1000)
            return full_lyrics

        # Akustik ses izi eşleşmediyse Katman 3 (AI Vokalden Söz / Şarkı Yakalama) sonucunu al
        vocal_result = None
        if vocal_task:
            try:
                vocal_result = await vocal_task
            except asyncio.CancelledError:
                pass
            if vocal_result and not vocal_result.get("partial_vocal_only") and vocal_result.get("title"):
                if isinstance(vocal_result.get("offset_seconds"), (int, float)):
                    vocal_result["offset_seconds"] = round(float(vocal_result["offset_seconds"]) + 1.5, 2)
                vocal_result["recognized"] = True
                vocal_result["wave"] = wave
                vocal_result["elapsed_ms"] = int((time.time() - start_time) * 1000)
                return vocal_result

        detected_words: List[str] = []
        if vocal_result and vocal_result.get("detected_phrases"):
            detected_words = [p[1] for p in vocal_result["detected_phrases"]]

        return {
            "recognized": False,
            "wave": wave,
            "detected_words": detected_words,
            "elapsed_ms": int((time.time() - start_time) * 1000),
            "message": (
                f"Dalga {wave} tarandı ({', '.join(detected_words) if detected_words else 'müzik/vokal dinleniyor'}), "
                "aramaya devam ediliyor..."
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
    Şarkı sözleri ekranda akarken telefon mikrofonundan gelen canlı sesi analiz ederek
    sözleri müziğin gerçek saniyesine hizalar.
    """
    if not HAS_SHAZAMIO or not HAS_SPEECH_REC or not FFMPEG_BIN:
        refresh_optional_dependencies()
    work_dir = tempfile.mkdtemp(prefix="sarki_sync_")
    try:
        raw_path = os.path.join(work_dir, f"sync{file_ext}")
        with open(raw_path, "wb") as f:
            f.write(audio_bytes)

        loop = asyncio.get_running_loop()
        pcm_path = os.path.join(work_dir, "pcm.wav")
        vocal_path = os.path.join(work_dir, "vocal.wav")

        def _prep():
            if FFMPEG_BIN:
                _run_ffmpeg(
                    raw_path,
                    pcm_path,
                    None,
                    sample_rate=44100,
                    tail_seconds=4.5,
                )
                _run_ffmpeg(
                    raw_path,
                    vocal_path,
                    "highpass=f=100,lowpass=f=7000,dynaudnorm=f=250:g=15:p=0.92:m=8",
                    sample_rate=16000,
                    tail_seconds=4.5,
                )

        await loop.run_in_executor(_EXECUTOR, _prep)
        target_pcm = pcm_path if os.path.exists(pcm_path) else raw_path
        target_vocal = vocal_path if os.path.exists(vocal_path) else target_pcm

        fp_task = asyncio.create_task(_shazam_recognize_single(target_pcm, "live_sync"))
        stt_task = loop.run_in_executor(_EXECUTOR, _extract_vocals_text_sync, [target_vocal], ["tr-TR", "en-US"])

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

        if stt_phrases and lines:
            for _lang, phrase in stt_phrases:
                cleaned = phrase.strip()
                if len(cleaned) >= 10 and len(cleaned.split()) >= 2:
                    m_idx, m_time = lyrics_engine.find_matching_line_index(
                        lines, cleaned, hint_offset=current_offset
                    )
                    if m_idx is not None and m_idx >= 0:
                        if (
                            current_offset is not None
                            and current_offset > 0
                            and isinstance(m_time, (int, float))
                        ):
                            if float(m_time) < current_offset - 3.5 or float(m_time) > current_offset + 9.0:
                                continue
                        return {
                            "synced": True,
                            "method": "vocal_live",
                            "matched_line_index": m_idx,
                            "offset_seconds": round(float(m_time) + 1.4, 2) if m_time is not None else None,
                            "detected_phrase": cleaned,
                        }

        return {
            "synced": False,
            "detected_phrase": stt_phrases[0][1] if stt_phrases else "",
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
