#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Şarkı Sözü Bul — Çok Kaynaklı Şarkı Sözü, Senkronize Karaoke (LRC) ve Çeviri Motoru (lyrics_engine.py)

Özellikler:
1. LRCLIB, YouTube Music, Genius (API + Sayfa + Embed) ve iTunes kataloglarını paralel tarar.
2. Türkçe karakter (ş, ı, ğ, ü, ö, ç) ve ASCII dönüşümlerini çift yönlü destekler.
3. Şarkı sözü kesitlerinden (tek satır veya ardışık iki satır) şarkıyı ve tam o anki satırı bulur.
4. Yabancı şarkılar için satır satır Türkçe çeviri desteği sunar.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
import html
import json
import os
import re
import ssl
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_POOL = ThreadPoolExecutor(max_workers=8)


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        ca = certifi.where()
        if os.path.exists(ca):
            return ssl.create_default_context(cafile=ca)
    except Exception:
        pass
    for cafile in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(cafile):
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
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
            _YT_DIAGNOSTIC = "ok"
        except Exception as e:
            _YT_DIAGNOSTIC = f"error: {e}"
    return _YTMUSIC


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


def tr_to_ascii(text: str) -> str:
    """Türkçe karakterleri ASCII karşılıklarına dönüştürür."""
    if not text:
        return ""
    tr_map = str.maketrans(
        "çğıöşüâîûÇĞİÖŞÜÂÎÛ",
        "cgiosuaiuCGIOSUAIU",
    )
    return text.translate(tr_map)


def normalize_text(text: str) -> str:
    """Karşılaştırma için metni küçük harfe çevirir, Türkçe harfleri sadeleştirir ve noktalamayı temizler."""
    if not text:
        return ""
    t = text.replace("İ", "i").replace("I", "ı").lower()
    t = tr_to_ascii(t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_track_title(title: str) -> str:
    """Şarkı adındaki '(feat. ...)', '[Remastered]', '- Radio Edit' gibi ekleri temizler."""
    if not title:
        return ""
    cleaned = re.sub(
        r"\s*[\(\[\-]\s*(feat\.?|ft\.?|remaster|live|radio edit|version|original mix|official|acoustic|remix|single|deluxe|karaoke).*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return cleaned.strip() or title.strip()


def clean_artist_name(artist: str) -> str:
    """Sanatçı adındaki '&', 'feat.', ',' sonrası ikincil isimleri sadeleştirir."""
    if not artist:
        return ""
    cleaned = re.sub(r"\s*(?:feat\.?|ft\.?|&|,|x|×|with)\s+.*$", "", artist, flags=re.IGNORECASE)
    return cleaned.strip() or artist.strip()


def title_artist_similarity(
    target_title: str,
    target_artist: str,
    cand_title: str,
    cand_artist: str,
) -> float:
    """Hedef şarkı/sanatçı ile aday şarkı/sanatçı arasındaki benzerlik puanını (0.0 - 1.0) hesaplar."""
    nt1 = normalize_text(clean_track_title(target_title))
    nt2 = normalize_text(clean_track_title(cand_title))
    if not nt1 or not nt2:
        return 0.0

    if nt1 == nt2:
        t_score = 1.0
    elif nt1 in nt2 or nt2 in nt1:
        t_score = 0.88
    else:
        t_score = SequenceMatcher(None, nt1, nt2).ratio()

    na1 = normalize_text(clean_artist_name(target_artist))
    na2 = normalize_text(clean_artist_name(cand_artist))
    if not na1 or not na2:
        return t_score

    if na1 == na2 or na1 in na2 or na2 in na1:
        a_score = 1.0
    else:
        a_score = SequenceMatcher(None, na1, na2).ratio()

    return t_score * 0.65 + a_score * 0.35


def parse_lrc(synced_lyrics: str) -> List[Dict[str, Any]]:
    """[01:23.45] formatındaki LRC metnini [{index, time: 83.45, text: "..."}] listesine dönüştürür."""
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
        if text and text != "♪":
            lines.append({"time": total_sec, "text": text})
    lines.sort(key=lambda x: x["time"])
    for idx, item in enumerate(lines):
        item["index"] = idx
    return lines


def plain_to_lines(plain_lyrics: str) -> List[Dict[str, Any]]:
    """Düz şarkı sözlerini satır listesine çevirir (boş satırları ve [Nakarat] etiketlerini temizler)."""
    if not plain_lyrics:
        return []
    result: List[Dict[str, Any]] = []
    for raw_line in plain_lyrics.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        # [Verse 1], [Chorus], [Nakarat] gibi yapısal etiketleri atla
        if re.match(r"^\[.*?(verse|chorus|nakarat|intro|outro|bridge|solo|hook|bölüm|nakarat).*?\]$", stripped, flags=re.IGNORECASE):
            continue
        result.append({
            "index": len(result),
            "time": None,
            "text": stripped,
        })
    return result


def find_matching_line_index(
    lines: List[Dict[str, Any]],
    snippet: str,
    hint_offset: Optional[float] = None,
) -> Tuple[int, Optional[float]]:
    """
    Yakalanan söz kesitinin (snippet) şarkının hangi satırında geçtiğini bulur.
    Hem tekil satırları hem de ardışık 2 satırlık pencereleri (satır geçişlerini) kontrol eder.
    """
    if not lines or not snippet:
        return -1, None
    norm_snippet = normalize_text(snippet)
    if len(norm_snippet) < 3:
        return -1, None

    best_idx = -1
    best_score = 0.0
    snippet_words = [w for w in norm_snippet.split() if len(w) >= 2]
    snippet_word_set = set(snippet_words)

    norm_lines = [normalize_text(item.get("text", "")) for item in lines]

    for idx, norm_line in enumerate(norm_lines):
        if not norm_line:
            continue

        # 1. Tek satır karşılaştırması
        if norm_snippet in norm_line or (len(norm_line) >= 6 and norm_line in norm_snippet):
            text_score = 0.96
        else:
            line_words = set(w for w in norm_line.split() if len(w) >= 2)
            overlap = (
                len(snippet_word_set & line_words) / max(1, len(snippet_word_set))
                if snippet_word_set and line_words
                else 0.0
            )
            seq_ratio = SequenceMatcher(None, norm_snippet, norm_line).ratio()
            text_score = max(overlap * 0.88, seq_ratio)

        # 2. Ardışık iki satır birleşimi (şarkıcı iki satırın ortasında yakalandıysa)
        if text_score < 0.78 and idx + 1 < len(norm_lines) and norm_lines[idx + 1]:
            combined = f"{norm_line} {norm_lines[idx + 1]}"
            if norm_snippet in combined:
                text_score = max(text_score, 0.92)
            else:
                comb_words = set(w for w in combined.split() if len(w) >= 2)
                if snippet_word_set and comb_words:
                    comb_overlap = len(snippet_word_set & comb_words) / max(1, len(snippet_word_set))
                    text_score = max(text_score, comb_overlap * 0.85)

        if text_score < 0.48:
            continue

        line_time = lines[idx].get("time")
        total_score = text_score
        if hint_offset is not None and hint_offset > 0 and isinstance(line_time, (int, float)):
            time_diff = abs(float(line_time) - float(hint_offset))
            proximity_bonus = max(-0.35, 0.20 - (time_diff / 60.0))
            total_score += proximity_bonus

        if total_score > best_score:
            best_score = total_score
            best_idx = idx

    if best_score >= 0.48 and best_idx >= 0:
        return best_idx, lines[best_idx].get("time")
    return -1, None


def enrich_metadata_itunes(title: str, artist: str = "") -> Dict[str, Any]:
    """Şarkının kapak fotoğrafı, albüm ve ses önizleme (preview_url) bilgisini iTunes'tan tamamlar."""
    try:
        term = f"{artist} {title}".strip()
        if not term:
            return {}
        qs = urllib.parse.urlencode({"term": term, "entity": "song", "country": "TR", "limit": 5})
        url = f"https://itunes.apple.com/search?{qs}"
        data = _http_get_json(url, timeout=4.0)
        results = data.get("results") or []
        if not results:
            qs = urllib.parse.urlencode({"term": term, "entity": "song", "limit": 5})
            data = _http_get_json(f"https://itunes.apple.com/search?{qs}", timeout=4.0)
            results = data.get("results") or []
        if results:
            best = results[0]
            best_sim = -1.0
            for item in results:
                sim = title_artist_similarity(
                    title,
                    artist,
                    item.get("trackName") or "",
                    item.get("artistName") or "",
                )
                if sim > best_sim:
                    best_sim = sim
                    best = item
            art = (best.get("artworkUrl100") or "").replace("100x100bb", "500x500bb")
            dur_ms = best.get("trackTimeMillis")
            return {
                "title": best.get("trackName") or title,
                "artist": best.get("artistName") or artist,
                "cover": art,
                "album": best.get("collectionName") or "",
                "preview_url": best.get("previewUrl") or "",
                "genre": best.get("primaryGenreName") or "",
                "duration": round(dur_ms / 1000.0) if isinstance(dur_ms, (int, float)) else None,
            }
    except Exception:
        pass
    return {}


def search_lrclib_by_track(title: str, artist: str = "") -> Optional[Dict[str, Any]]:
    """
    LRCLIB üzerinden şarkı adı ve sanatçıya göre senkronize/düz söz arar.
    Hem Türkçe karakterli hem de ASCII sorgularını dener; ilk hata/zaman aşımında döngüyü kırmaz.
    """
    clean_t = clean_track_title(title)
    clean_a = clean_artist_name(artist)
    if not clean_t:
        return None

    queries: List[Dict[str, str]] = []
    if clean_t and clean_a:
        queries.append({"track_name": clean_t, "artist_name": clean_a})
        queries.append({"q": f"{clean_a} {clean_t}"})
        ascii_q = tr_to_ascii(f"{clean_a} {clean_t}")
        if ascii_q != f"{clean_a} {clean_t}":
            queries.append({"q": ascii_q})
    queries.append({"q": clean_t})
    ascii_t = tr_to_ascii(clean_t)
    if ascii_t != clean_t:
        queries.append({"q": ascii_t})

    best_overall: Optional[Dict[str, Any]] = None
    best_overall_score = -1.0

    for params in queries:
        try:
            qs = urllib.parse.urlencode(params)
            url = f"https://lrclib.net/api/search?{qs}"
            data = _http_get_json(url, timeout=4.5)
            if not isinstance(data, list) or not data:
                continue

            for item in data[:10]:
                plain = (item.get("plainLyrics") or "").strip()
                synced = (item.get("syncedLyrics") or "").strip()
                if not plain and not synced:
                    continue

                item_title = item.get("trackName") or ""
                item_artist = item.get("artistName") or ""
                sim = title_artist_similarity(clean_t, clean_a, item_title, item_artist)
                if sim < 0.38:
                    continue

                score = sim + (0.28 if synced else 0.0)
                if score > best_overall_score:
                    best_overall_score = score
                    best_overall = item

            # Eğer senkronize ve yüksek benzerlikli bir kayıt bulduysak hemen döndür
            if best_overall and best_overall_score >= 0.85 and best_overall.get("syncedLyrics"):
                break
        except Exception:
            continue

    if not best_overall or best_overall_score < 0.38:
        return None

    synced_raw = (best_overall.get("syncedLyrics") or "").strip()
    plain_raw = (best_overall.get("plainLyrics") or "").strip()
    synced_lines = parse_lrc(synced_raw) if synced_raw else []
    lines = synced_lines if synced_lines else plain_to_lines(plain_raw)
    if not lines:
        return None

    return {
        "title": best_overall.get("trackName") or title,
        "artist": best_overall.get("artistName") or artist,
        "album": best_overall.get("albumName") or "",
        "duration": best_overall.get("duration"),
        "synced": bool(synced_lines),
        "lines": lines,
        "plain_lyrics": plain_raw or "\n".join(x["text"] for x in synced_lines),
        "source": "LRCLIB (Senkronize Karaoke)" if synced_lines else "LRCLIB",
    }


def _fetch_ytmusic_lyrics_for_song_item(yt: Any, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Tek bir YouTube Music şarkı kaydının (videoId) sözlerini (varsa zaman damgalı) çeker."""
    video_id = item.get("videoId") or ""
    if not video_id:
        return None

    title = item.get("title") or ""
    artists = [a.get("name") for a in item.get("artists", []) if a.get("name")]
    artist = ", ".join(artists) if artists else (item.get("author") or "")
    duration_sec = item.get("duration_seconds")
    if not duration_sec:
        duration_str = item.get("duration") or ""
        if duration_str and ":" in duration_str:
            parts = duration_str.split(":")
            try:
                if len(parts) == 2:
                    duration_sec = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                pass

    cover = ""
    thumbnails = item.get("thumbnails") or []
    if thumbnails:
        cover = thumbnails[-1].get("url") or ""
    album_name = item.get("album", {}).get("name", "") if isinstance(item.get("album"), dict) else ""

    try:
        watch = yt.get_watch_playlist(videoId=video_id)
        lyrics_browse_id = watch.get("lyrics")
        if not lyrics_browse_id:
            return None

        lyr_data = None
        try:
            lyr_data = yt.get_lyrics(lyrics_browse_id, timestamps=True)
        except Exception:
            try:
                lyr_data = yt.get_lyrics(lyrics_browse_id)
            except Exception:
                lyr_data = None

        if not lyr_data:
            return None

        raw_items = lyr_data.get("lyrics")
        if lyr_data.get("hasTimestamps") and isinstance(raw_items, list):
            timed_lines = []
            plain_parts = []
            for entry in raw_items:
                txt = (
                    getattr(entry, "text", None)
                    or (entry.get("text") if isinstance(entry, dict) else "")
                    or ""
                ).strip()
                if not txt or txt == "♪":
                    continue
                start_ms = getattr(entry, "start_time", None)
                if start_ms is None and isinstance(entry, dict):
                    start_ms = entry.get("start_time")
                t_sec = round(float(start_ms) / 1000.0, 2) if start_ms is not None else None
                timed_lines.append({
                    "index": len(timed_lines),
                    "time": t_sec,
                    "text": txt,
                })
                plain_parts.append(txt)
            if timed_lines:
                return {
                    "title": title,
                    "artist": artist,
                    "album": album_name,
                    "cover": cover,
                    "duration": duration_sec,
                    "synced": True,
                    "lines": timed_lines,
                    "plain_lyrics": "\n".join(plain_parts),
                    "source": "YouTube Music (Canlı Karaoke Senkron)",
                }
        elif isinstance(raw_items, str) and len(raw_items.strip()) > 20:
            raw_lyrics = raw_items.strip()
            lines = plain_to_lines(raw_lyrics)
            if lines:
                return {
                    "title": title,
                    "artist": artist,
                    "album": album_name,
                    "cover": cover,
                    "duration": duration_sec,
                    "synced": False,
                    "lines": lines,
                    "plain_lyrics": raw_lyrics,
                    "source": "YouTube Music Resmi Sözler",
                }
    except Exception:
        pass
    return None


def search_ytmusic_by_track(title: str, artist: str = "") -> Optional[Dict[str, Any]]:
    """
    Belirli bir şarkı adı ve sanatçı için YouTube Music'te en uygun eşleşmeleri bulur
    ve ilk 3 adayın resmi şarkı sözlerini kontrol eder.
    """
    yt = _get_ytmusic()
    if not yt or not title:
        return None

    clean_t = clean_track_title(title)
    clean_a = clean_artist_name(artist)
    query = f"{clean_a} {clean_t}".strip()

    try:
        results = yt.search(query, filter="songs", limit=6)
        if not results:
            results = yt.search(query, limit=6)
        if not results:
            return None

        # Adayları hedef başlık ve sanatçı benzerliğine göre sırala
        scored_candidates: List[Tuple[float, Dict[str, Any]]] = []
        for r in results[:6]:
            r_title = r.get("title") or ""
            r_artists = ", ".join(a.get("name", "") for a in r.get("artists", []) if a.get("name"))
            sim = title_artist_similarity(clean_t, clean_a, r_title, r_artists)
            if sim >= 0.35:
                scored_candidates.append((sim, r))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        plain_fallback: Optional[Dict[str, Any]] = None

        for _sim, cand in scored_candidates[:3]:
            res = _fetch_ytmusic_lyrics_for_song_item(yt, cand)
            if res and res.get("lines"):
                if res.get("synced"):
                    return res
                if not plain_fallback:
                    plain_fallback = res

        return plain_fallback
    except Exception:
        return None


def _scrape_genius_embed_lyrics(song_id: Any) -> Optional[str]:
    """
    Genius'un resmi JS embed uç noktasından (genius.com/songs/{id}/embed.js) sözleri çeker.
    Standart HTML sayfası Cloudflare veya tasarım değişikliğine takılsa bile çalışır.
    """
    if not song_id:
        return None
    try:
        url = f"https://genius.com/songs/{song_id}/embed.js"
        js_text = _http_get_text(url, timeout=5.5)
        match = re.search(r"JSON\.parse\('(.+?)'\)\)", js_text, flags=re.DOTALL)
        if not match:
            return None
        escaped_json_str = match.group(1)
        # JS single-quote escape temizliği
        unescaped = escaped_json_str.encode("utf-8").decode("unicode_escape", errors="replace")
        # Alternatif olarak doğrudan rg_embed_body HTML içeriğini ayıkla
        html_body = unescaped
        try:
            html_body = json.loads(f'"{escaped_json_str}"')
        except Exception:
            pass
        html_body = re.sub(r"<br\s*/?>", "\n", html_body, flags=re.IGNORECASE)
        html_body = re.sub(r"<[^>]+>", "", html_body)
        html_body = html.unescape(html_body).strip()
        lines = [ln.strip() for ln in html_body.splitlines() if ln.strip()]
        cleaned = "\n".join(lines)
        return cleaned if len(cleaned) > 35 else None
    except Exception:
        return None


def _scrape_genius_page_lyrics(url: str, song_id: Any = None) -> Optional[str]:
    """Genius şarkı sayfasından veya embed.js uç noktasından sözleri ayıklar."""
    # 1. Önce hızlı ve hafif olan embed.js uç noktasını dene
    if song_id:
        embedded = _scrape_genius_embed_lyrics(song_id)
        if embedded:
            return embedded

    if not url:
        return None
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
                        chunk = page_html[tag_end + 1 : next_close]
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
        full = re.sub(r"^\s*\d+\s+Contributors.*?(?:Read More|\n\[|\bLyrics\b)", "", full, flags=re.DOTALL).strip()
        full = re.sub(r"\d*Embed$", "", full).strip()
        return full if len(full) > 30 else None
    except Exception:
        return None


def search_genius_candidates(query: str, prefer_lyric_match: bool = False) -> List[Dict[str, Any]]:
    """
    Genius API üzerinden şarkı ve söz araması yapar.
    Hem /api/search?q= hem de /api/search/multi?q= uç noktalarını destekler.
    """
    if not query or len(query.strip()) < 2:
        return []
    candidates: List[Dict[str, Any]] = []
    seen_ids = set()

    # 1. Hafif /api/search uç noktası (Cloudflare engeline en az takılan JSON API)
    try:
        qs = urllib.parse.urlencode({"q": query.strip()})
        data = _http_get_json(f"https://genius.com/api/search?{qs}", timeout=5.0)
        hits = (data.get("response") or {}).get("hits") or []
        for hit in hits:
            res = hit.get("result") or {}
            sid = res.get("id")
            if sid and res.get("title") and sid not in seen_ids:
                seen_ids.add(sid)
                candidates.append(res)
    except Exception:
        pass

    # 2. Çoklu /api/search/multi uç noktası (söz içi eşleşmeler için)
    if prefer_lyric_match or not candidates:
        try:
            qs = urllib.parse.urlencode({"q": query.strip()})
            data = _http_get_json(f"https://genius.com/api/search/multi?{qs}", timeout=5.0)
            sections = (data.get("response") or {}).get("sections") or []
            sec_map = {sec.get("type"): (sec.get("hits") or []) for sec in sections}
            order = ("lyric", "top_hit", "song") if prefer_lyric_match else ("top_hit", "song", "lyric")
            for sec_type in order:
                for hit in sec_map.get(sec_type, []):
                    res = hit.get("result") or {}
                    sid = res.get("id")
                    if sid and res.get("title") and sid not in seen_ids:
                        seen_ids.add(sid)
                        candidates.append(res)
        except Exception:
            pass

    return candidates


def search_genius_lyrics(query: str, prefer_lyric_match: bool = False) -> Optional[Dict[str, Any]]:
    """Genius üzerinden şarkıyı bulur, önce LRCLIB/YTMusic senkron sözlerini, yoksa Genius sözlerini döndürür."""
    candidates = search_genius_candidates(query, prefer_lyric_match=prefer_lyric_match)
    for res in candidates[:3]:
        song_id = res.get("id")
        song_title = res.get("title") or ""
        artist_name = (res.get("primary_artist") or {}).get("name") or res.get("artist_names") or ""
        cover_art = res.get("song_art_image_thumbnail_url") or res.get("header_image_thumbnail_url") or ""
        page_url = res.get("url") or ""

        # Önce bu şarkının LRCLIB veya YouTube Music'te senkronize karaoke sözü var mı kontrol et
        lrc_res = search_lrclib_by_track(song_title, artist_name)
        if lrc_res and lrc_res.get("lines"):
            if cover_art and not lrc_res.get("cover"):
                lrc_res["cover"] = cover_art
            return lrc_res

        yt_res = search_ytmusic_by_track(song_title, artist_name)
        if yt_res and yt_res.get("lines"):
            if cover_art and not yt_res.get("cover"):
                yt_res["cover"] = cover_art
            return yt_res

        scraped = _scrape_genius_page_lyrics(page_url, song_id=song_id)
        if scraped:
            lines = plain_to_lines(scraped)
            if lines:
                return {
                    "title": song_title,
                    "artist": artist_name,
                    "album": "",
                    "cover": cover_art,
                    "duration": None,
                    "synced": False,
                    "lines": lines,
                    "plain_lyrics": scraped,
                    "source": "Genius",
                }
    return None


def search_lyrics_ovh(title: str, artist: str) -> Optional[Dict[str, Any]]:
    """Lyrics.ovh yedek API üzerinden şarkı sözü arar."""
    if not title or not artist:
        return None
    try:
        clean_t = urllib.parse.quote(clean_track_title(title))
        clean_a = urllib.parse.quote(clean_artist_name(artist))
        url = f"https://api.lyrics.ovh/v1/{clean_a}/{clean_t}"
        data = _http_get_json(url, timeout=4.5)
        lyrics = (data.get("lyrics") or "").strip()
        if lyrics:
            lyrics = re.sub(r"^Paroles de la chanson.*?\n", "", lyrics, flags=re.IGNORECASE).strip()
            lines = plain_to_lines(lyrics)
            if lines:
                return {
                    "title": title,
                    "artist": artist,
                    "album": "",
                    "duration": None,
                    "synced": False,
                    "lines": lines,
                    "plain_lyrics": lyrics,
                    "source": "Lyrics.ovh",
                }
    except Exception:
        pass
    return None


def get_full_lyrics_for_track(
    title: str,
    artist: str = "",
    shazam_lyrics: Optional[List[str]] = None,
    cover: str = "",
    offset_seconds: Optional[float] = None,
    matched_snippet: str = "",
) -> Dict[str, Any]:
    """
    Tanınan veya seçilen bir şarkı için tüm kaynakları (LRCLIB, YouTube Music, Genius, Shazam, iTunes)
    paralel çalıştırarak en iyi (tercihen zaman senkronizasyonlu LRC) şarkı sözünü getirir.
    """
    fut_lrc = _POOL.submit(search_lrclib_by_track, title, artist)
    fut_yt = _POOL.submit(search_ytmusic_by_track, title, artist)
    fut_meta = _POOL.submit(enrich_metadata_itunes, title, artist)

    lrc_res: Optional[Dict[str, Any]] = None
    yt_res: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = {}

    try:
        lrc_res = fut_lrc.result(timeout=6.0)
    except Exception:
        pass
    try:
        yt_res = fut_yt.result(timeout=6.0)
    except Exception:
        pass
    try:
        meta = fut_meta.result(timeout=4.5) or {}
    except Exception:
        pass

    result: Optional[Dict[str, Any]] = None

    # Senkronize (zaman damgalı) sözlere birinci öncelik ver
    if yt_res and yt_res.get("synced") and yt_res.get("lines"):
        result = yt_res
    elif lrc_res and lrc_res.get("synced") and lrc_res.get("lines"):
        result = lrc_res
    elif yt_res and yt_res.get("lines"):
        result = yt_res
    elif lrc_res and lrc_res.get("lines"):
        result = lrc_res

    # Hala söz bulunamadıysa Genius ve Lyrics.ovh kaynaklarını dene
    if not result or not result.get("lines"):
        gen_res = search_genius_lyrics(f"{artist} {title}".strip(), prefer_lyric_match=False)
        if gen_res and gen_res.get("lines"):
            result = gen_res

    if (not result or not result.get("lines")) and artist:
        ovh_res = search_lyrics_ovh(title, artist)
        if ovh_res and ovh_res.get("lines"):
            result = ovh_res

    if (not result or not result.get("lines")) and shazam_lyrics:
        joined = "\n".join(shazam_lyrics)
        lines = plain_to_lines(joined)
        if lines:
            result = {
                "title": title,
                "artist": artist,
                "album": "",
                "duration": None,
                "synced": False,
                "lines": lines,
                "plain_lyrics": joined,
                "source": "Shazam Resmi Sözler",
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
            "source": "Söz Veritabanında Bulunamadı",
        }

    # Meta verileri (kapak, albüm, ses önizleme, süre) zenginleştir
    if cover and not result.get("cover"):
        result["cover"] = cover
    if not result.get("cover") and meta.get("cover"):
        result["cover"] = meta["cover"]
    if not result.get("album") and meta.get("album"):
        result["album"] = meta["album"]
    if not result.get("duration") and meta.get("duration"):
        result["duration"] = meta["duration"]
    if meta.get("preview_url"):
        result["preview_url"] = meta["preview_url"]

    # Şarkının o anki çalma saniyesi (offset) veya yakalanan söz kesiti varsa aktif satırı işaretle
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
    Arama kutusuna yazılan veya mikrofondan duyulan söz kesitinden / şarkı adından
    LRCLIB, YouTube Music, Genius ve iTunes motorlarını paralel tarayarak en doğru şarkıyı
    ve alternatif aday listesini (candidates) döndürür.
    """
    cleaned = (snippet or "").strip()
    if len(cleaned) < 2:
        return None

    # 1. LRCLIB doğrudan arama (hem şarkı adı hem tüm sözlerin içinde arar)
    def _task_lrclib() -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        queries = [cleaned]
        ascii_c = tr_to_ascii(cleaned)
        if ascii_c != cleaned:
            queries.append(ascii_c)
        seen_keys = set()
        for q_str in queries:
            try:
                qs = urllib.parse.urlencode({"q": q_str})
                data = _http_get_json(f"https://lrclib.net/api/search?{qs}", timeout=5.0)
                if not isinstance(data, list):
                    continue
                for item in data[:8]:
                    plain = (item.get("plainLyrics") or "").strip()
                    synced = (item.get("syncedLyrics") or "").strip()
                    if not plain and not synced:
                        continue
                    t_name = item.get("trackName") or ""
                    a_name = item.get("artistName") or ""
                    key = f"{normalize_text(t_name)}::{normalize_text(a_name)}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    lines = parse_lrc(synced) if synced else plain_to_lines(plain)
                    if not lines:
                        continue
                    idx, inferred_time = find_matching_line_index(lines, cleaned)
                    title_sim = title_artist_similarity(cleaned, "", f"{a_name} {t_name}", "")
                    score = 0.0
                    if idx >= 0:
                        score += 1.2
                    score += title_sim * 0.9
                    if synced:
                        score += 0.25
                    found.append({
                        "score": score,
                        "title": t_name,
                        "artist": a_name,
                        "album": item.get("albumName") or "",
                        "cover": "",
                        "duration": item.get("duration"),
                        "synced": bool(synced),
                        "lines": lines,
                        "plain_lyrics": plain or "\n".join(x["text"] for x in lines),
                        "source": "LRCLIB (Senkronize Karaoke)" if synced else "LRCLIB",
                        "matched_line_index": idx,
                        "offset_seconds": inferred_time,
                        "matched_snippet": cleaned,
                    })
            except Exception:
                continue
        return found

    # 2. YouTube Music şarkı & söz araması
    def _task_ytmusic() -> List[Dict[str, Any]]:
        yt = _get_ytmusic()
        if not yt:
            return []
        found: List[Dict[str, Any]] = []
        try:
            results = yt.search(cleaned, filter="songs", limit=5)
            if not results:
                results = yt.search(cleaned, limit=5)
            for idx_r, r in enumerate(results[:4]):
                r_title = r.get("title") or ""
                r_artists = ", ".join(a.get("name", "") for a in r.get("artists", []) if a.get("name"))
                if not r_title:
                    continue
                thumbs = r.get("thumbnails") or []
                r_cover = thumbs[-1].get("url", "") if thumbs else ""
                # İlk 2 sonuç için doğrudan YT Music sözlerini çekmeyi dene
                if idx_r < 2:
                    lyr_res = _fetch_ytmusic_lyrics_for_song_item(yt, r)
                    if lyr_res and lyr_res.get("lines"):
                        m_idx, m_time = find_matching_line_index(lyr_res["lines"], cleaned)
                        t_sim = title_artist_similarity(cleaned, "", f"{r_artists} {r_title}", "")
                        score = (1.25 if m_idx >= 0 else 0.55) + t_sim * 0.8 + (0.25 if lyr_res.get("synced") else 0.0)
                        lyr_res["score"] = score
                        lyr_res["matched_line_index"] = m_idx
                        lyr_res["offset_seconds"] = m_time
                        lyr_res["matched_snippet"] = cleaned
                        found.append(lyr_res)
                        continue
                # Söz henüz çekilmediyse aday olarak ekle
                found.append({
                    "score": 0.45 - idx_r * 0.05,
                    "title": r_title,
                    "artist": r_artists,
                    "album": r.get("album", {}).get("name", "") if isinstance(r.get("album"), dict) else "",
                    "cover": r_cover,
                    "duration": r.get("duration_seconds"),
                    "synced": False,
                    "lines": [],
                    "plain_lyrics": "",
                    "source": "YouTube Music",
                })
        except Exception:
            pass
        return found

    # 3. Genius şarkı ve söz içi arama
    def _task_genius() -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        cands = search_genius_candidates(cleaned, prefer_lyric_match=(len(cleaned.split()) >= 3))
        for idx_c, c in enumerate(cands[:4]):
            c_title = c.get("title") or ""
            c_artist = (c.get("primary_artist") or {}).get("name") or c.get("artist_names") or ""
            c_cover = c.get("song_art_image_thumbnail_url") or c.get("header_image_thumbnail_url") or ""
            if not c_title:
                continue
            found.append({
                "score": 0.60 - idx_c * 0.05,
                "title": c_title,
                "artist": c_artist,
                "album": "",
                "cover": c_cover,
                "duration": None,
                "synced": False,
                "lines": [],
                "plain_lyrics": "",
                "source": "Genius",
                "genius_id": c.get("id"),
                "genius_url": c.get("url"),
            })
        return found

    f_lrc = _POOL.submit(_task_lrclib)
    f_yt = _POOL.submit(_task_ytmusic)
    f_gen = _POOL.submit(_task_genius)

    all_items: List[Dict[str, Any]] = []
    for fut in (f_lrc, f_yt, f_gen):
        try:
            all_items.extend(fut.result(timeout=6.0) or [])
        except Exception:
            pass

    if not all_items:
        return None

    # En yüksek puanlıya göre sırala
    all_items.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    # Tekilleştirilmiş aday listesi (UI'da alternatif şarkılar olarak göstermek için)
    unique_candidates: List[Dict[str, Any]] = []
    seen_cand_keys = set()
    for item in all_items:
        k = f"{normalize_text(item.get('title', ''))}::{normalize_text(clean_artist_name(item.get('artist', '')))}"
        if not item.get("title") or k in seen_cand_keys:
            continue
        seen_cand_keys.add(k)
        unique_candidates.append({
            "title": item.get("title") or "",
            "artist": item.get("artist") or "",
            "album": item.get("album") or "",
            "cover": item.get("cover") or "",
            "synced": bool(item.get("synced")),
            "source": item.get("source") or "",
        })

    # En iyi adayın sözleri zaten doluysa (ve senkronize ise veya satır eşleşmesi varsa) doğrudan kullan
    best: Optional[Dict[str, Any]] = None
    for item in all_items:
        if item.get("lines"):
            best = item
            break

    # Eğer en üstteki adayın (örn. Genius veya YTMusic başlık eşleşmesi) sözleri henüz çekilmediyse
    # veya mevcut sözde satır eşleşmesi yoksa en üstteki aday için tam sözleri getir
    top_cand = all_items[0]
    if not best or (not top_cand.get("lines") and top_cand.get("score", 0) > best.get("score", 0)):
        resolved = get_full_lyrics_for_track(
            top_cand["title"],
            top_cand.get("artist", ""),
            cover=top_cand.get("cover", ""),
            matched_snippet=cleaned,
        )
        if resolved and resolved.get("lines"):
            best = resolved
        elif top_cand.get("genius_url") or top_cand.get("genius_id"):
            scraped = _scrape_genius_page_lyrics(top_cand.get("genius_url", ""), song_id=top_cand.get("genius_id"))
            if scraped:
                lines = plain_to_lines(scraped)
                m_idx, m_time = find_matching_line_index(lines, cleaned)
                best = {
                    "title": top_cand["title"],
                    "artist": top_cand.get("artist", ""),
                    "album": "",
                    "cover": top_cand.get("cover", ""),
                    "duration": None,
                    "synced": False,
                    "lines": lines,
                    "plain_lyrics": scraped,
                    "source": "Genius",
                    "matched_line_index": m_idx,
                    "offset_seconds": m_time,
                    "matched_snippet": cleaned,
                }

    if not best:
        # İkinci adayı dene
        for cand in unique_candidates[:3]:
            resolved = get_full_lyrics_for_track(
                cand["title"],
                cand.get("artist", ""),
                cover=cand.get("cover", ""),
                matched_snippet=cleaned,
            )
            if resolved and resolved.get("lines"):
                best = resolved
                break

    if not best:
        best = top_cand

    if not best.get("cover") or not best.get("preview_url"):
        meta = enrich_metadata_itunes(best.get("title") or "", best.get("artist") or "")
        if not best.get("cover") and meta.get("cover"):
            best["cover"] = meta["cover"]
        if not best.get("album") and meta.get("album"):
            best["album"] = meta["album"]
        if meta.get("preview_url"):
            best["preview_url"] = meta["preview_url"]

    best["candidates"] = unique_candidates[:6]
    return best


def translate_lyrics_to_turkish(lines: List[Dict[str, Any]], target_lang: str = "tr") -> List[str]:
    """Yabancı dildeki şarkı sözlerini satır satır Türkçeye çevirir."""
    if not lines:
        return []
    original_texts = [line.get("text", "").strip() for line in lines]
    joined = "\n".join(original_texts)
    if not joined.strip():
        return ["" for _ in lines]

    try:
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
        while len(translated_lines) < len(original_texts):
            translated_lines.append("")
        return translated_lines[: len(original_texts)]
    except Exception:
        return ["" for _ in lines]
