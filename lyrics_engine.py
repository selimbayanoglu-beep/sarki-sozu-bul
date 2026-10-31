#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Şarkı Sözü Bul — Çok Kaynaklı Şarkı Sözü, Senkronize Karaoke (LRC) ve Çeviri Motoru.
Her dildeki (Türkçe, İngilizce, İspanyolca, Fransızca vb.) şarkının:
1. Tam sözlerini ve varsa saniye saniye senkronize (LRC) karaoke satırlarını bulur.
2. Müziğin içinden yakalanan birkaç kelimelik vokal kesitinden hangi şarkı olduğunu tespit eder
   ve şarkının tam o an söylenen satırını (matched_line_index) işaretler.
3. Yabancı şarkılar için satır satır Türkçe çeviri desteği sunar.
"""

import html
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _build_ssl_context() -> ssl.SSLContext:
    for cafile in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(cafile):
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl._create_unverified_context()


_SSL_CTX = _build_ssl_context()
_YTMUSIC = None
_YT_DIAGNOSTIC = "not_run"


def _get_ytmusic():
    global _YTMUSIC, _YT_DIAGNOSTIC
    if _YTMUSIC is None:
        try:
            from ytmusicapi import YTMusic
            _YTMUSIC = YTMusic()
            _YT_DIAGNOSTIC = "ytmusicapi imported and initialized successfully"
        except ImportError as ie:
            _YT_DIAGNOSTIC = f"ImportError: {ie}"
            try:
                import subprocess, sys
                cp = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--break-system-packages", "ytmusicapi"],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                _YT_DIAGNOSTIC += f" | pip code: {cp.returncode}, out: {cp.stdout[:120]}, err: {cp.stderr[:120]}"
                from ytmusicapi import YTMusic
                _YTMUSIC = YTMusic()
                _YT_DIAGNOSTIC += " | initialized after pip!"
            except Exception as pe:
                _YT_DIAGNOSTIC += f" | pip/init error: {pe}"
        except Exception as e:
            _YT_DIAGNOSTIC = f"YTMusic() init Exception: {e}"
    return _YTMUSIC


def search_ytmusic_song_and_lyrics(query: str) -> Optional[Dict[str, Any]]:
    """
    YouTube Music motoru ile şarkı, sanatçı, kapak görseli ve resmi şarkı sözlerini arar.
    LRCLIB üzerinde senkronize karaoke (LRC) varsa otomatik olarak birleştirir.
    """
    if not query or len(query.strip()) < 2:
        return None
    yt = _get_ytmusic()
    if not yt:
        return None

    try:
        results = yt.search(query.strip(), filter="songs")
        if not results:
            return None

        best = results[0]
        title = best.get("title") or ""
        artists = [a.get("name") for a in best.get("artists", []) if a.get("name")]
        artist = ", ".join(artists) if artists else (best.get("author") or "")
        video_id = best.get("videoId") or ""
        duration_sec = None
        duration_str = best.get("duration") or ""
        if duration_str and ":" in duration_str:
            parts = duration_str.split(":")
            if len(parts) == 2:
                duration_sec = int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

        cover = ""
        thumbnails = best.get("thumbnails") or []
        if thumbnails:
            cover = thumbnails[-1].get("url") or ""

        # 1. Önce LRCLIB'de senkronize karaoke sözü var mı bakalım!
        lrc_match = search_lrclib_by_track(title, artist)
        if lrc_match and lrc_match.get("lines"):
            if not lrc_match.get("cover") and cover:
                lrc_match["cover"] = cover
            lrc_match["source"] = "YouTube Music + LRCLIB (Senkronize)"
            return lrc_match

        # 2. LRCLIB yoksa YouTube Music'in kendi resmi şarkı sözlerini çekelim
        if video_id:
            try:
                watch = yt.get_watch_playlist(videoId=video_id)
                lyrics_browse_id = watch.get("lyrics")
                if lyrics_browse_id:
                    lyr_data = yt.get_lyrics(lyrics_browse_id)
                    raw_lyrics = (lyr_data.get("lyrics") or "").strip()
                    if raw_lyrics and len(raw_lyrics) > 20:
                        lines = plain_to_lines(raw_lyrics)
                        return {
                            "title": title,
                            "artist": artist,
                            "album": best.get("album", {}).get("name", "") if isinstance(best.get("album"), dict) else "",
                            "cover": cover,
                            "duration": duration_sec,
                            "synced": False,
                            "lines": lines,
                            "plain_lyrics": raw_lyrics,
                            "source": "YouTube Music Resmi Sözler",
                        }
            except Exception:
                pass

        # 3. YouTube Music resmi sözü yoksa Genius'tan şarkı sözlerini çekmeyi dene
        try:
            genius_match = search_genius_lyrics(f"{artist} {title}", prefer_lyric_match=False)
            if genius_match and genius_match.get("lines"):
                genius_match["source"] = "YouTube Music + Genius Sözleri"
                if not genius_match.get("cover") and cover:
                    genius_match["cover"] = cover
                return genius_match
        except Exception:
            pass

        # 4. Söz çekilemediyse bile şarkı meta verisini döndür
        return {
            "title": title,
            "artist": artist,
            "album": best.get("album", {}).get("name", "") if isinstance(best.get("album"), dict) else "",
            "cover": cover,
            "duration": duration_sec,
            "synced": False,
            "lines": [],
            "plain_lyrics": "",
            "source": "YouTube Music",
        }
    except Exception:
        return None


def _http_get_json(url: str, timeout: float = 6.0, headers: Optional[Dict[str, str]] = None) -> Any:
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def _http_get_text(url: str, timeout: float = 6.0, headers: Optional[Dict[str, str]] = None) -> str:
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    """Karşılaştırma için metni küçük harfe çevirir ve noktalama işaretlerini temizler."""
    if not text:
        return ""
    t = text.lower()
    tr_map = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    t = t.translate(tr_map)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_track_title(title: str) -> str:
    """Şarkı adındaki '(feat. ...)', '[Remastered]', '- Radio Edit' gibi ekleri temizler."""
    if not title:
        return ""
    cleaned = re.sub(r"\s*[\(\[\-]\s*(feat\.?|ft\.?|remaster|live|radio edit|version|original mix|official|acoustic|remix).*$", "", title, flags=re.IGNORECASE)
    return cleaned.strip() or title.strip()


def parse_lrc(synced_lyrics: str) -> List[Dict[str, Any]]:
    """
    [01:23.45] formatındaki LRC metnini [{time: 83.45, text: "..."}] listesine dönüştürür.
    """
    if not synced_lyrics:
        return []
    lines: List[Dict[str, Any]] = []
    pattern = re.compile(r"\[(\d{1,2}):(\d{2})(?:[\.:](\d{1,3}))?\](.*)")
    for raw_line in synced_lyrics.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        frac_str = match.group(3) or "0"
        frac = int(frac_str) / (1000.0 if len(frac_str) == 3 else 100.0 if len(frac_str) == 2 else 10.0)
        total_sec = round(minutes * 60 + seconds + frac, 2)
        text = match.group(4).strip()
        if text:
            lines.append({"time": total_sec, "text": text})
    lines.sort(key=lambda x: x["time"])
    return lines


def plain_to_lines(plain_lyrics: str) -> List[Dict[str, Any]]:
    """Düz şarkı sözlerini satır listesine çevirir."""
    if not plain_lyrics:
        return []
    result = []
    for idx, line in enumerate(plain_lyrics.splitlines()):
        stripped = line.strip()
        result.append({
            "index": idx,
            "time": None,
            "text": stripped,
        })
    return result


def find_matching_line_index(lines: List[Dict[str, Any]], snippet: str) -> Tuple[int, Optional[float]]:
    """
    Yakalanan söz kesitinin (snippet) şarkının hangi satırında geçtiğini bulur.
    Hem satır indeksini hem de varsa saniyesini döndürür.
    """
    if not lines or not snippet:
        return -1, None
    norm_snippet = normalize_text(snippet)
    if len(norm_snippet) < 3:
        return -1, None

    best_idx = -1
    best_score = 0.0
    snippet_words = set(norm_snippet.split())

    for idx, item in enumerate(lines):
        line_text = item.get("text", "")
        norm_line = normalize_text(line_text)
        if not norm_line:
            continue
        if norm_snippet in norm_line or (len(norm_line) > 6 and norm_line in norm_snippet):
            return idx, item.get("time")

        line_words = set(norm_line.split())
        if snippet_words and line_words:
            overlap = len(snippet_words & line_words) / max(1, len(snippet_words))
        else:
            overlap = 0.0
        seq_ratio = SequenceMatcher(None, norm_snippet, norm_line).ratio()
        score = max(overlap * 0.85, seq_ratio)
        if score > best_score:
            best_score = score
            best_idx = idx

    if best_score >= 0.45 and best_idx >= 0:
        return best_idx, lines[best_idx].get("time")
    return -1, None


def search_lrclib_by_track(title: str, artist: str = "") -> Optional[Dict[str, Any]]:
    """LRCLIB üzerinden şarkı adı ve sanatçıya göre senkronize/düz söz arar."""
    queries = []
    clean_t = clean_track_title(title)
    clean_a = clean_track_title(artist)
    if clean_t and clean_a:
        queries.append({"track_name": clean_t, "artist_name": clean_a})
        queries.append({"q": f"{clean_a} {clean_t}"})
    if clean_t:
        queries.append({"q": clean_t})

    for params in queries:
        try:
            qs = urllib.parse.urlencode(params)
            url = f"https://lrclib.net/api/search?{qs}"
            data = _http_get_json(url, timeout=5.5)
            if not isinstance(data, list) or not data:
                continue
            # Önce senkronize sözü (syncedLyrics) olan ve sanatçısı uyuşan kaydı seç
            best_item = None
            best_score = -1.0
            for item in data:
                plain = item.get("plainLyrics") or ""
                synced = item.get("syncedLyrics") or ""
                if not plain and not synced:
                    continue
                item_title = normalize_text(item.get("trackName") or "")
                item_artist = normalize_text(item.get("artistName") or "")
                score = SequenceMatcher(None, normalize_text(clean_t), item_title).ratio()
                if clean_a:
                    artist_score = SequenceMatcher(None, normalize_text(clean_a), item_artist).ratio()
                    score = score * 0.6 + artist_score * 0.4
                if synced:
                    score += 0.25
                if score > best_score:
                    best_score = score
                    best_item = item

            if best_item and best_score >= 0.35:
                synced_raw = best_item.get("syncedLyrics") or ""
                plain_raw = best_item.get("plainLyrics") or ""
                synced_lines = parse_lrc(synced_raw) if synced_raw else []
                return {
                    "title": best_item.get("trackName") or title,
                    "artist": best_item.get("artistName") or artist,
                    "album": best_item.get("albumName") or "",
                    "duration": best_item.get("duration"),
                    "synced": bool(synced_lines),
                    "lines": synced_lines if synced_lines else plain_to_lines(plain_raw),
                    "plain_lyrics": plain_raw or "\n".join(x["text"] for x in synced_lines),
                    "source": "LRCLIB (Senkronize Karaoke)" if synced_lines else "LRCLIB",
                }
        except Exception:
            continue
    return None


def search_lyrics_ovh(title: str, artist: str) -> Optional[Dict[str, Any]]:
    """Lyrics.ovh yedek API üzerinden şarkı sözü arar."""
    if not title or not artist:
        return None
    try:
        clean_t = urllib.parse.quote(clean_track_title(title))
        clean_a = urllib.parse.quote(clean_track_title(artist))
        url = f"https://api.lyrics.ovh/v1/{clean_a}/{clean_t}"
        data = _http_get_json(url, timeout=5.0)
        lyrics = (data.get("lyrics") or "").strip()
        if lyrics:
            # Lyrics.ovh bazen ilk satırda "Paroles de la chanson..." verir, temizleyelim
            lyrics = re.sub(r"^Paroles de la chanson.*?\n", "", lyrics, flags=re.IGNORECASE).strip()
            return {
                "title": title,
                "artist": artist,
                "album": "",
                "duration": None,
                "synced": False,
                "lines": plain_to_lines(lyrics),
                "plain_lyrics": lyrics,
                "source": "Lyrics.ovh",
            }
    except Exception:
        pass
    return None


def _scrape_genius_page_lyrics(url: str) -> Optional[str]:
    """Genius şarkı sayfasından sözleri temiz ve eksiksiz ayıklar."""
    try:
        page_html = _http_get_text(url, timeout=6.0)
        chunks = []
        pos = 0
        while True:
            idx = page_html.find('data-lyrics-container="true"', pos)
            if idx == -1:
                break
            tag_end = page_html.find(">", idx)
            if tag_end == -1:
                break
            open_divs = 1
            curr = tag_end + 1
            while open_divs > 0 and curr < len(page_html):
                next_open = page_html.find("<div", curr)
                next_close = page_html.find("</div>", curr)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    open_divs += 1
                    curr = next_open + 4
                else:
                    open_divs -= 1
                    if open_divs == 0:
                        chunk = page_html[tag_end + 1:next_close]
                        chunks.append(chunk)
                        pos = next_close + 6
                        break
                    curr = next_close + 6
            if open_divs != 0:
                break

        if not chunks:
            return None

        full = "\n\n".join(chunks)
        full = re.sub(r"<br\s*/?>", "\n", full, flags=re.IGNORECASE)
        full = re.sub(r"<[^>]+>", "", full)
        full = html.unescape(full)
        # Başlıktaki reklam ve katkıda bulunanlar çöpünü temizle
        full = re.sub(r"^\s*\d+\s+Contributors.*?(?:Read More|\n\[|\bLyrics\b)", "", full, flags=re.DOTALL).strip()
        full = re.sub(r"\d*Embed$", "", full).strip()
        return full if len(full) > 30 else None
    except Exception:
        return None


def search_genius_lyrics(query: str, prefer_lyric_match: bool = False) -> Optional[Dict[str, Any]]:
    """
    Genius genel arama uç noktasından hem şarkı adına göre hem de
    müziğin içinden yakalanan söz parçasına göre şarkı ve sözlerini bulur.
    """
    if not query or len(query.strip()) < 2:
        return None
    try:
        qs = urllib.parse.urlencode({"q": query.strip()})
        url = f"https://genius.com/api/search/multi?{qs}"
        data = _http_get_json(url, timeout=6.0)
        sections = (data.get("response") or {}).get("sections") or []
        sec_map = {sec.get("type"): (sec.get("hits") or []) for sec in sections}
        order = ("lyric", "top_hit", "song") if prefer_lyric_match else ("top_hit", "song", "lyric")

        seen_urls = set()
        candidates = []
        for sec_type in order:
            for hit in sec_map.get(sec_type, []):
                res = hit.get("result") or {}
                u = res.get("url")
                if u and res.get("title") and u not in seen_urls and res.get("_type") in ("song", None):
                    seen_urls.add(u)
                    candidates.append(res)

        for res in candidates[:3]:
            song_title = res.get("title") or ""
            artist_name = (res.get("primary_artist") or {}).get("name") or res.get("artist_names") or ""
            cover_art = res.get("song_art_image_thumbnail_url") or res.get("header_image_thumbnail_url") or ""
            page_url = res.get("url") or ""

            # Önce bu şarkının LRCLIB'de senkronize karaoke sözü var mı diye bakalım!
            lrc_res = search_lrclib_by_track(song_title, artist_name)
            if lrc_res:
                if cover_art and not lrc_res.get("cover"):
                    lrc_res["cover"] = cover_art
                return lrc_res

            # Yoksa doğrudan Genius sayfasından sözleri çekelim
            scraped = _scrape_genius_page_lyrics(page_url)
            if scraped:
                return {
                    "title": song_title,
                    "artist": artist_name,
                    "album": "",
                    "cover": cover_art,
                    "duration": None,
                    "synced": False,
                    "lines": plain_to_lines(scraped),
                    "plain_lyrics": scraped,
                    "source": "Genius",
                }
    except Exception:
        pass
    return None


def enrich_metadata_itunes(title: str, artist: str = "") -> Dict[str, str]:
    """Şarkının kapak fotoğrafı, albüm ve önizleme bilgisini iTunes/Apple Music'ten tamamlar."""
    try:
        term = f"{artist} {title}".strip()
        qs = urllib.parse.urlencode({"term": term, "entity": "song", "limit": 3})
        url = f"https://itunes.apple.com/search?{qs}"
        data = _http_get_json(url, timeout=4.0)
        results = data.get("results") or []
        if results:
            item = results[0]
            art = (item.get("artworkUrl100") or "").replace("100x100bb", "500x500bb")
            return {
                "cover": art,
                "album": item.get("collectionName") or "",
                "preview_url": item.get("previewUrl") or "",
                "genre": item.get("primaryGenreName") or "",
            }
    except Exception:
        pass
    return {}


def get_full_lyrics_for_track(
    title: str,
    artist: str = "",
    shazam_lyrics: Optional[List[str]] = None,
    cover: str = "",
    offset_seconds: Optional[float] = None,
    matched_snippet: str = "",
) -> Dict[str, Any]:
    """
    Tanınan bir şarkı için en iyi (tercihen zaman senkronizasyonlu LRC) şarkı sözünü getirir.
    """
    result = search_lrclib_by_track(title, artist)

    if not result:
        # YouTube Music resmi şarkı ve söz veritabanı
        result = search_ytmusic_song_and_lyrics(f"{artist} {title}".strip())

    if not result:
        result = search_genius_lyrics(f"{artist} {title}".strip())

    if not result and artist:
        result = search_lyrics_ovh(title, artist)

    if not result and shazam_lyrics:
        joined = "\n".join(shazam_lyrics)
        result = {
            "title": title,
            "artist": artist,
            "album": "",
            "duration": None,
            "synced": False,
            "lines": plain_to_lines(joined),
            "plain_lyrics": joined,
            "source": "Shazam Lyrics",
        }

    if not result:
        result = {
            "title": title,
            "artist": artist,
            "album": "",
            "duration": None,
            "synced": False,
            "lines": [],
            "plain_lyrics": "",
            "source": "Bulunamadı",
        }

    if not cover and not result.get("cover"):
        meta = enrich_metadata_itunes(result.get("title") or title, result.get("artist") or artist)
        if meta.get("cover"):
            result["cover"] = meta["cover"]
        if meta.get("album") and not result.get("album"):
            result["album"] = meta["album"]
    elif cover:
        result["cover"] = cover

    # Şarkının o anki çalma saniyesi (offset) veya yakalanan söz kesiti varsa aktif satırı işaretle
    # Karaoke öncü gösterim: Şarkıcı vuruşu kaçırmasın diye sözleri 1.1 saniye önceden hazırla
    matched_idx = -1
    if offset_seconds is not None and result.get("synced") and result.get("lines"):
        for idx, line in enumerate(result["lines"]):
            t = line.get("time")
            if t is not None and t <= offset_seconds + 1.1:
                matched_idx = idx
            elif t is not None and t > offset_seconds + 1.1:
                break
    elif offset_seconds is not None and not result.get("synced") and result.get("lines"):
        total_d = result.get("duration") or 210.0
        ratio = max(0.0, min(0.95, offset_seconds / total_d))
        matched_idx = int(len(result["lines"]) * ratio)
    elif matched_snippet and result.get("lines"):
        matched_idx, inferred_time = find_matching_line_index(result["lines"], matched_snippet)
        if offset_seconds is None and inferred_time is not None:
            offset_seconds = inferred_time

    result["offset_seconds"] = offset_seconds
    result["matched_line_index"] = matched_idx
    result["matched_snippet"] = matched_snippet
    return result


def search_song_by_lyrics_snippet(snippet: str) -> Optional[Dict[str, Any]]:
    """
    Müziğin içinden yapay zekayla (veya sesle) yakalanan söz kesitinden
    (herhangi bir dilde: Türkçe, İngilizce, İspanyolca vb.) şarkıyı ve tam sözlerini bulur.
    """
    cleaned = snippet.strip()
    if cleaned == "diag":
        _get_ytmusic()
        return {
            "title": "DIAGNOSTIC",
            "artist": _YT_DIAGNOSTIC,
            "album": "",
            "cover": "",
            "duration": None,
            "synced": False,
            "lines": [{"index": 0, "time": None, "text": _YT_DIAGNOSTIC}],
            "plain_lyrics": _YT_DIAGNOSTIC,
            "source": "Diagnostic",
        }
    if len(cleaned) < 3:
        return None

    # 1. YouTube Music motoru (Google'ın en kapsamlı şarkı ve söz arama sistemi)
    yt_res = search_ytmusic_song_and_lyrics(cleaned)
    if yt_res and yt_res.get("lines"):
        idx, inferred_time = find_matching_line_index(yt_res["lines"], cleaned)
        yt_res["matched_line_index"] = idx
        yt_res["offset_seconds"] = inferred_time
        yt_res["matched_snippet"] = cleaned
        return yt_res

    # 2. Genius çok-dilli popüler şarkı + söz eşleştirme motoru (ardından otomatik LRCLIB senkronize söz çeker)
    genius_res = search_genius_lyrics(cleaned, prefer_lyric_match=(len(cleaned.split()) >= 4))
    if genius_res and genius_res.get("lines"):
        idx, inferred_time = find_matching_line_index(genius_res["lines"], cleaned)
        genius_res["matched_line_index"] = idx
        genius_res["offset_seconds"] = inferred_time
        genius_res["matched_snippet"] = cleaned
        if not genius_res.get("cover"):
            meta = enrich_metadata_itunes(genius_res.get("title") or "", genius_res.get("artist") or "")
            genius_res["cover"] = meta.get("cover") or ""
        return genius_res

    # 2. Yedek olarak doğrudan LRCLIB üzerinde tam söz araması yap
    try:
        qs = urllib.parse.urlencode({"q": cleaned})
        url = f"https://lrclib.net/api/search?{qs}"
        data = _http_get_json(url, timeout=5.5)
        if isinstance(data, list) and data:
            best_item = None
            best_score = -1.0
            for item in data[:8]:
                plain = item.get("plainLyrics") or ""
                synced = item.get("syncedLyrics") or ""
                if not plain and not synced:
                    continue
                lines = parse_lrc(synced) if synced else plain_to_lines(plain)
                idx, _ = find_matching_line_index(lines, cleaned)
                score = 1.0 if idx >= 0 else 0.3
                if synced:
                    score += 0.25
                if score > best_score:
                    best_score = score
                    best_item = (item, lines, idx)

            if best_item:
                item, lines, idx = best_item
                title = item.get("trackName") or ""
                artist = item.get("artistName") or ""
                meta = enrich_metadata_itunes(title, artist)
                inferred_time = lines[idx].get("time") if (0 <= idx < len(lines)) else None
                return {
                    "title": title,
                    "artist": artist,
                    "album": item.get("albumName") or meta.get("album") or "",
                    "cover": meta.get("cover") or "",
                    "duration": item.get("duration"),
                    "synced": bool(item.get("syncedLyrics")),
                    "lines": lines,
                    "plain_lyrics": item.get("plainLyrics") or "\n".join(x["text"] for x in lines),
                    "source": "LRCLIB (Söz Eşleşmesi)",
                    "matched_line_index": idx,
                    "offset_seconds": inferred_time,
                    "matched_snippet": cleaned,
                }
    except Exception:
        pass

    return None


def translate_lyrics_to_turkish(lines: List[Dict[str, Any]], target_lang: str = "tr") -> List[str]:
    """
    Yabancı dildeki şarkı sözlerini hızlıca satır satır Türkçeye çevirir.
    Ücretsiz Google Translate GTX uç noktasını kullanır (API anahtarı gerektirmez).
    """
    if not lines:
        return []
    original_texts = [line.get("text", "").strip() for line in lines]
    joined = "\n".join(original_texts)
    if not joined.strip():
        return ["" for _ in lines]

    try:
        # Çok uzun şarkıları 2500 karakterlik bloklar halinde çevirelim
        chunks: List[str] = []
        current_chunk: List[str] = []
        current_len = 0
        for line in original_texts:
            if current_len + len(line) + 1 > 2400 and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        translated_lines: List[str] = []
        for chunk in chunks:
            qs = urllib.parse.urlencode({
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "dt": "t",
                "q": chunk,
            })
            url = f"https://translate.googleapis.com/translate_a/single?{qs}"
            data = _http_get_json(url, timeout=6.0)
            if isinstance(data, list) and data and isinstance(data[0], list):
                chunk_trans = "".join(part[0] for part in data[0] if part and part[0])
                translated_lines.extend(chunk_trans.split("\n"))
        # Satır sayısını eşitle
        while len(translated_lines) < len(original_texts):
            translated_lines.append("")
        return translated_lines[: len(original_texts)]
    except Exception:
        return ["" for _ in lines]


# Eğer bu modül çalışma esnasında reload ediliyorsa (örn. /api/pull-github çağrısı ile)
# ana sunucu sürecini yeni kodla anında yeniden başlatır.
if globals().get("_IS_INITIALIZED_LYRICS_ENGINE"):
    def _on_reload_trigger():
        import time, os, sys
        time.sleep(0.8)
        try:
            target_script = os.path.join(os.path.dirname(__file__), "server.py")
            if not os.path.exists(target_script):
                target_script = "/app/server.py"
            os.execv(sys.executable, [sys.executable, target_script, "--port", os.environ.get("PORT", "8891"), "--https-port", os.environ.get("HTTPS_PORT", "8892"), "--no-browser"])
        except Exception:
            os._exit(0)

    import threading
    threading.Thread(target=_on_reload_trigger, daemon=True).start()
else:
    _IS_INITIALIZED_LYRICS_ENGINE = True

