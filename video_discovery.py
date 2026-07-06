"""
video_discovery.py
------------------
V8: Konuya özel lisanslı/indirilebilir video adayı avcısı.

Amaç:
- Pexels/Pixabay'dan rastgele "ancient stone" videosu çekip alakasız sonuç üretmeyi bitirmek.
- Önce Göbeklitepe/Karahantepe/rekonstrüksiyon konulu aday havuzu kurmak.
- Lisans ve konu puanı düşükse video üretimini durdurmak.

Kaynaklar:
- YouTube Creative Commons: aday listesi için kullanılır, otomatik indirme yapmaz.
- Wikimedia Commons: video dosyası URL verirse otomatik indirilebilir.
- Internet Archive: mp4/webm dosya varsa otomatik indirilebilir.
- Pexels/Pixabay: sadece gerçekten konuya yakın görünüyorsa düşük öncelikli aday olur.

Çıktı:
  output/video_NN/video_candidates.json
  output/video_NN/video_candidates_report.md
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests

import config


YOUTUBE_SEARCH_API = "https://www.googleapis.com/youtube/v3/search"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ARCHIVE_ADVANCED_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA = "https://archive.org/metadata"
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"

REQUEST_TIMEOUT = 25
RETRY_COUNT = 2
RETRY_SLEEP = 2

USER_AGENT = (
    "AnadoluGizemleriVideoDiscovery/1.0 "
    "(https://github.com/furukcell/anadolu-gizemleri-pipeline)"
)

DEFAULT_POSITIVE_TERMS = [
    "gobekli", "göbekli", "gobeklitepe", "göbeklitepe", "gobekli tepe", "göbekli tepe",
    "karahantepe", "sanliurfa", "şanlıurfa", "urfa",
    "neolithic", "neolitik", "temple", "tapinak", "tapınak",
    "archaeology", "arkeoloji", "excavation", "kazı", "kazi",
    "reconstruction", "rekonstrüksiyon", "reconstruction", "3d", "animation", "animasyon",
    "cgi", "ai", "cinematic", "belgesel", "documentary"
]

STRONG_TOPIC_TERMS = [
    "gobekli", "göbekli", "gobeklitepe", "göbeklitepe", "gobekli tepe", "göbekli tepe",
    "karahantepe"
]

DEFAULT_NEGATIVE_TERMS = [
    "lycia", "lycian", "pinara", "ephesus", "efes", "roman", "rome", "greek",
    "pamukkale", "hierapolis", "perge", "side", "miletus", "troy", "troya", "truva",
    "hattusa", "hattuşa", "ani", "nemrut", "gordion", "istanbul", "cappadocia",
    "beach", "resort", "hotel", "vlog", "travel vlog", "tourist", "modern city",
    "cars", "football", "game", "minecraft"
]


def _turkish_to_ascii(text: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = (text or "").translate(tr_map)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def _norm(text: str) -> str:
    return _turkish_to_ascii(text or "").lower()


def _unique(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        item = (item or "").strip()
        key = item.lower()
        if item and key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _get(url: str, params=None, headers=None):
    last_err = None
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as e:
            last_err = str(e)

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_SLEEP)

    print(f"[video_discovery] Istek basarisiz: {url} -> {last_err}")
    return None


def _safe_text(*parts) -> str:
    return " ".join(str(p or "") for p in parts)


def candidate_identity(c: dict) -> str:
    return c.get("download_url") or c.get("source_url") or c.get("url") or c.get("title") or ""


def load_search_profile(day: int) -> dict:
    profile_dir = config.CONTENT_DIR / "search_profiles"
    candidates = sorted(profile_dir.glob(f"{day:02d}_*_queries.json"))
    if not candidates:
        print("[video_discovery] Search profile yok, varsayilan Göbeklitepe/Karahantepe sorgulari kullanilacak.")
        return {
            "day": day,
            "topic": "gobekli tepe karahantepe",
            "min_topic_score": 8,
            "max_results_per_query": 8,
            "discovery_queries": [
                "gobekli tepe reconstruction",
                "gobekli tepe 3d reconstruction",
                "gobekli tepe animation",
                "gobekli tepe ai reconstruction",
                "göbeklitepe rekonstrüksiyon",
                "karahantepe reconstruction",
                "karahantepe excavation",
                "neolithic temple reconstruction anatolia",
                "prehistoric temple reconstruction"
            ],
            "positive_terms": DEFAULT_POSITIVE_TERMS,
            "negative_terms": DEFAULT_NEGATIVE_TERMS,
        }

    path = candidates[0]
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["_profile_path"] = str(path)
    profile.setdefault("min_topic_score", 8)
    profile.setdefault("max_results_per_query", 8)
    profile.setdefault("positive_terms", DEFAULT_POSITIVE_TERMS)
    profile.setdefault("negative_terms", DEFAULT_NEGATIVE_TERMS)
    profile.setdefault("discovery_queries", [])
    print(f"[video_discovery] Search profile yuklendi -> {path}")
    return profile


def score_candidate(c: dict, profile: dict) -> int:
    text = _norm(_safe_text(
        c.get("title"),
        c.get("description"),
        c.get("source_url"),
        c.get("download_url"),
        c.get("license"),
        c.get("creator"),
    ))

    positive_terms = profile.get("positive_terms") or DEFAULT_POSITIVE_TERMS
    negative_terms = profile.get("negative_terms") or DEFAULT_NEGATIVE_TERMS

    score = 0

    # Konunun kendisi en onemli sey.
    for term in STRONG_TOPIC_TERMS:
        t = _norm(term)
        if t and t in text:
            score += 8

    for term in positive_terms:
        t = _norm(term)
        if not t:
            continue
        if t in text:
            if t in [_norm(x) for x in STRONG_TOPIC_TERMS]:
                continue
            score += 2

    for term in negative_terms:
        t = _norm(term)
        if t and t in text:
            score -= 10

    # Rekonstruksiyon/AI/3D gibi kelimeler ekstra degerli.
    for term in ["reconstruction", "rekonstruksiyon", "3d", "animation", "animasyon", "cgi", "ai"]:
        if term in text:
            score += 3

    # Indirilebilir ve lisans bilgisi olan kaynaklar daha kullanisli.
    if c.get("usable_for_auto_download"):
        score += 2
    if c.get("license"):
        score += 1

    return score


def _finalize_candidate(c: dict, profile: dict) -> dict:
    c["topic_score"] = score_candidate(c, profile)
    c["accepted"] = c["topic_score"] >= int(profile.get("min_topic_score", 8))
    return c


def search_youtube_cc(query: str, profile: dict) -> list[dict]:
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print("[video_discovery] YOUTUBE_API_KEY yok, YouTube CC aramasi atlandi.")
        return []

    params = {
        "part": "snippet",
        "type": "video",
        "videoLicense": "creativeCommon",
        "maxResults": int(profile.get("max_results_per_query", 8)),
        "q": query,
        "key": api_key,
        "safeSearch": "strict",
        "relevanceLanguage": "tr",
    }
    resp = _get(YOUTUBE_SEARCH_API, params=params)
    if not resp:
        return []

    out = []
    for item in resp.json().get("items", []):
        video_id = item.get("id", {}).get("videoId")
        sn = item.get("snippet", {})
        if not video_id:
            continue
        url = f"https://www.youtube.com/watch?v={video_id}"
        out.append({
            "source": "youtube_cc",
            "title": sn.get("title", ""),
            "description": sn.get("description", ""),
            "source_url": url,
            "download_url": None,
            "license": "YouTube Creative Commons Attribution",
            "creator": sn.get("channelTitle", ""),
            "query": query,
            "usable_for_auto_download": False,
            "note": "YouTube CC adayi. API direkt mp4 indirme URL'i vermez; manuel inceleme/kaynak olarak kullan.",
        })
    return out


def _commons_file_info(title: str) -> dict | None:
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
    }
    resp = _get(COMMONS_API, params=params)
    if not resp:
        return None
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = info.get("mime", "")
        url = info.get("url", "")
        if not (mime.startswith("video/") or url.lower().endswith((".mp4", ".webm", ".ogv", ".ogg"))):
            return None
        meta = info.get("extmetadata") or {}
        license_short = (meta.get("LicenseShortName") or {}).get("value", "")
        artist = re.sub("<.*?>", "", (meta.get("Artist") or {}).get("value", ""))
        description = re.sub("<.*?>", "", (meta.get("ImageDescription") or {}).get("value", ""))
        return {
            "download_url": url,
            "mime": mime,
            "license": license_short,
            "creator": artist,
            "description": description,
            "size": info.get("size"),
        }
    return None


def search_wikimedia_commons(query: str, profile: dict) -> list[dict]:
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srnamespace": 6,  # File namespace
        "srlimit": int(profile.get("max_results_per_query", 8)),
    }
    resp = _get(COMMONS_API, params=params)
    if not resp:
        return []

    out = []
    for item in resp.json().get("query", {}).get("search", []):
        title = item.get("title", "")
        if not title:
            continue
        info = _commons_file_info(title)
        if not info:
            continue
        out.append({
            "source": "wikimedia_commons",
            "title": title,
            "description": info.get("description", ""),
            "source_url": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
            "download_url": info.get("download_url"),
            "license": info.get("license", "Wikimedia Commons"),
            "creator": info.get("creator", ""),
            "query": query,
            "usable_for_auto_download": True,
            "mime": info.get("mime", ""),
        })
    return out


def _archive_download_url(identifier: str, file_name: str) -> str:
    return f"https://archive.org/download/{quote(identifier)}/{quote(file_name)}"


def search_internet_archive(query: str, profile: dict) -> list[dict]:
    q = f'({query}) AND mediatype:movies'
    params = {
        "q": q,
        "fl[]": ["identifier", "title", "description", "licenseurl", "creator"],
        "rows": int(profile.get("max_results_per_query", 8)),
        "page": 1,
        "output": "json",
    }
    resp = _get(ARCHIVE_ADVANCED_SEARCH, params=params)
    if not resp:
        return []

    out = []
    docs = resp.json().get("response", {}).get("docs", [])
    for doc in docs:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        meta_resp = _get(f"{ARCHIVE_METADATA}/{identifier}")
        if not meta_resp:
            continue
        meta = meta_resp.json()
        files = meta.get("files", []) or []
        video_file = None
        for f in files:
            name = f.get("name", "")
            fmt = (f.get("format") or "").lower()
            if name.lower().endswith((".mp4", ".webm", ".ogv", ".mov")) or "mpeg4" in fmt or "h.264" in fmt:
                # Turetilmis kucuk thumb/mp4 yerine ana dosyaya yakin olanlari tercih et.
                if "thumb" in name.lower() or "sample" in name.lower():
                    continue
                video_file = name
                break
        if not video_file:
            continue

        title = doc.get("title") or meta.get("metadata", {}).get("title") or identifier
        desc = doc.get("description") or meta.get("metadata", {}).get("description") or ""
        license_url = doc.get("licenseurl") or meta.get("metadata", {}).get("licenseurl") or ""
        creator = doc.get("creator") or meta.get("metadata", {}).get("creator") or ""

        out.append({
            "source": "internet_archive",
            "title": title,
            "description": desc if isinstance(desc, str) else json.dumps(desc, ensure_ascii=False),
            "source_url": f"https://archive.org/details/{identifier}",
            "download_url": _archive_download_url(identifier, video_file),
            "license": license_url or "Internet Archive item license not explicit",
            "creator": creator if isinstance(creator, str) else ", ".join(creator),
            "query": query,
            "usable_for_auto_download": True,
            "identifier": identifier,
            "file_name": video_file,
        })
    return out


def _best_pexels_video_link(video: dict) -> str | None:
    files = video.get("video_files", [])
    if not files:
        return None

    def score(vf: dict):
        w = vf.get("width") or 0
        h = vf.get("height") or 0
        file_type = vf.get("file_type") or ""
        is_mp4_penalty = 0 if "mp4" in file_type.lower() else 1000
        landscape_penalty = 0 if w >= h else 500
        width_penalty = abs((w or config.VIDEO_WIDTH) - config.VIDEO_WIDTH) / 10
        too_small_penalty = 300 if w and w < 1280 else 0
        return is_mp4_penalty + landscape_penalty + width_penalty + too_small_penalty

    return sorted(files, key=score)[0].get("link")


def search_pexels_topic(query: str, profile: dict) -> list[dict]:
    if not config.PEXELS_API_KEY:
        return []

    headers = {"Authorization": config.PEXELS_API_KEY, "User-Agent": USER_AGENT}
    params = {
        "query": query,
        "per_page": int(profile.get("max_results_per_query", 8)),
        "orientation": "landscape",
    }
    resp = _get(PEXELS_VIDEO_API, params=params, headers=headers)
    if not resp:
        return []

    out = []
    for video in resp.json().get("videos", []):
        link = _best_pexels_video_link(video)
        if not link:
            continue
        meta_url = video.get("url", "")
        # Pexels sonucunun kendisinde konu gecmiyorsa kabul etme. Query'yi skora katmiyoruz.
        out.append({
            "source": "pexels_video",
            "title": meta_url.split("/")[-2].replace("-", " ") if "/" in meta_url else "Pexels video",
            "description": meta_url,
            "source_url": meta_url,
            "download_url": link,
            "license": "Pexels License",
            "creator": video.get("user", {}).get("name", ""),
            "query": query,
            "usable_for_auto_download": True,
            "note": "Sadece topic_score yeterliyse kullanilir; query skora katilmaz.",
        })
    return out


def _best_pixabay_video_link(video: dict) -> str | None:
    videos = video.get("videos", {})
    for key in ("large", "medium", "small", "tiny"):
        item = videos.get(key) or {}
        if item.get("url"):
            return item.get("url")
    return None


def search_pixabay_topic(query: str, profile: dict) -> list[dict]:
    api_key = os.environ.get("PIXABAY_API_KEY", "")
    if not api_key:
        return []

    params = {
        "key": api_key,
        "q": query,
        "per_page": int(profile.get("max_results_per_query", 8)),
        "video_type": "film",
        "safesearch": "true",
    }
    resp = _get(PIXABAY_VIDEO_API, params=params)
    if not resp:
        return []

    out = []
    for video in resp.json().get("hits", []):
        link = _best_pixabay_video_link(video)
        if not link:
            continue
        out.append({
            "source": "pixabay_video",
            "title": video.get("tags", "Pixabay video"),
            "description": video.get("tags", ""),
            "source_url": video.get("pageURL", ""),
            "download_url": link,
            "license": "Pixabay Content License",
            "creator": str(video.get("user", "")),
            "query": query,
            "usable_for_auto_download": True,
            "note": "Sadece topic_score yeterliyse kullanilir.",
        })
    return out


def discover_candidates(day: int) -> dict:
    profile = load_search_profile(day)
    queries = _unique(profile.get("discovery_queries") or [])
    if not queries:
        raise RuntimeError("Search profile icinde discovery_queries bos.")

    print(f"[video_discovery] V8 konu videosu avcisi basladi. Sorgu sayisi: {len(queries)}")

    raw_candidates = []
    for query in queries:
        print(f"[video_discovery] Araniyor -> {query}")

        # YouTube CC: otomatik indirme yok, ama konu adayi ve manuel kaynak listesi.
        raw_candidates.extend(search_youtube_cc(query, profile))

        # Otomatik indirilebilir kaynaklar.
        raw_candidates.extend(search_wikimedia_commons(query, profile))
        raw_candidates.extend(search_internet_archive(query, profile))
        raw_candidates.extend(search_pexels_topic(query, profile))
        raw_candidates.extend(search_pixabay_topic(query, profile))

    # Tekilleştir + skorla.
    by_key = {}
    for cand in raw_candidates:
        cand = _finalize_candidate(cand, profile)
        key = candidate_identity(cand)
        if not key:
            continue
        old = by_key.get(key)
        if old is None or cand.get("topic_score", 0) > old.get("topic_score", 0):
            by_key[key] = cand

    all_candidates = sorted(by_key.values(), key=lambda c: c.get("topic_score", 0), reverse=True)
    accepted = [c for c in all_candidates if c.get("accepted")]
    auto_downloadable = [
        c for c in accepted
        if c.get("usable_for_auto_download") and c.get("download_url")
    ]

    result = {
        "day": day,
        "mode": "topic_video_discovery_v8",
        "profile_path": profile.get("_profile_path"),
        "min_topic_score": profile.get("min_topic_score", 8),
        "query_count": len(queries),
        "candidate_count": len(all_candidates),
        "accepted_count": len(accepted),
        "auto_downloadable_count": len(auto_downloadable),
        "queries": queries,
        "accepted_candidates": accepted,
        "auto_downloadable_candidates": auto_downloadable,
        "all_candidates": all_candidates,
    }
    return result


def write_report(result: dict, report_path: Path):
    lines = []
    lines.append(f"# Video Discovery Report - Day {result['day']}")
    lines.append("")
    lines.append(f"- Mode: `{result.get('mode')}`")
    lines.append(f"- Min topic score: `{result.get('min_topic_score')}`")
    lines.append(f"- Total candidates: `{result.get('candidate_count')}`")
    lines.append(f"- Accepted candidates: `{result.get('accepted_count')}`")
    lines.append(f"- Auto-downloadable accepted: `{result.get('auto_downloadable_count')}`")
    lines.append("")
    lines.append("## Queries")
    for q in result.get("queries", []):
        lines.append(f"- {q}")
    lines.append("")
    lines.append("## Accepted auto-downloadable candidates")
    autos = result.get("auto_downloadable_candidates", [])
    if not autos:
        lines.append("_Yok. Bu durumda pipeline video üretmemeli; önce kaynak bulunmalı._")
    for c in autos[:30]:
        lines.append("")
        lines.append(f"### {c.get('title') or c.get('source_url')}")
        lines.append(f"- Source: `{c.get('source')}`")
        lines.append(f"- Score: `{c.get('topic_score')}`")
        lines.append(f"- License: `{c.get('license')}`")
        lines.append(f"- Source URL: {c.get('source_url')}")
        lines.append(f"- Download URL: {c.get('download_url')}")
    lines.append("")
    lines.append("## Accepted YouTube CC / manual candidates")
    yt = [c for c in result.get("accepted_candidates", []) if c.get("source") == "youtube_cc"]
    if not yt:
        lines.append("_Yok._")
    for c in yt[:30]:
        lines.append("")
        lines.append(f"### {c.get('title')}")
        lines.append(f"- Score: `{c.get('topic_score')}`")
        lines.append(f"- Channel: `{c.get('creator')}`")
        lines.append(f"- URL: {c.get('source_url')}")
        lines.append(f"- License: `{c.get('license')}`")
        lines.append(f"- Note: {c.get('note')}")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def process_day(day: int) -> Path:
    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    video_dir.mkdir(parents=True, exist_ok=True)

    result = discover_candidates(day)
    out_path = video_dir / "video_candidates.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = video_dir / "video_candidates_report.md"
    write_report(result, report_path)

    print(f"[video_discovery] Aday dosyasi -> {out_path}")
    print(f"[video_discovery] Rapor -> {report_path}")
    print(
        f"[video_discovery] {result['candidate_count']} aday, "
        f"{result['accepted_count']} kabul, "
        f"{result['auto_downloadable_count']} otomatik indirilebilir."
    )

    if result["auto_downloadable_count"] <= 0:
        raise RuntimeError(
            "Konuya yakın otomatik indirilebilir video bulunamadi.\n"
            f"Raporu incele: {report_path}\n"
            "YouTube CC adaylari varsa manuel incelenebilir; otomatik mp4 indirme yok."
        )

    return out_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        process_day(int(sys.argv[1]))
    else:
        print("Kullanim: python video_discovery.py <gun_numarasi>")
        print("Ornek: python video_discovery.py 1")
