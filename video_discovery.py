"""
video_discovery.py
------------------
V9: Konuya özel lisanslı/indirilebilir video adayı avcısı.

DEĞİŞİKLİK (V8 -> V9):
- V8'de bir aday, konu adı (Göbeklitepe/Karahantepe vb.) hiç geçmese bile
  "temple", "reconstruction", "animation", "documentary" gibi genel
  terimlerden topladığı puanla eşiği (min_topic_score) geçip kabul
  edilebiliyordu. Bu da alakasız videoların sisteme sızmasına sebep oluyordu.
- V9'da kabul kuralı sıkılaştırıldı: bir aday, search_profile'da tanımlı
  "strong_topic_terms" listesinden EN AZ BİRİNİ içermiyorsa, puanı ne
  olursa olsun REDDEDİLİR. Genel terimler artık tek başına yeterli değil.
- STRONG_TOPIC_TERMS artık dosya içine sabit kodlanmıyor; her günün kendi
  search_profile JSON'ında "strong_topic_terms" alanı olarak tanımlanmalı.
  Bu alan yoksa/boşsa o gün için hiçbir aday kabul edilmez (sessizce yanlış
  video geçmesindense pipeline durur).

Amaç:
- Pexels/Pixabay'dan rastgele "ancient stone" videosu çekip alakasız sonuç üretmeyi bitirmek.
- Önce konuya özel (örn. Göbeklitepe/Karahantepe) aday havuzu kurmak.
- Konu adı geçmiyorsa veya lisans/skor düşükse video üretimini durdurmak.

Kaynaklar:
- YouTube Creative Commons: aday listesi için kullanılır, otomatik indirme yapmaz.
- Wikimedia Commons: video dosyası URL verirse otomatik indirilebilir.
- Internet Archive: mp4/webm dosya varsa otomatik indirilebilir.
- Pexels/Pixabay: sadece gerçekten konuya yakın görünüyorsa düşük öncelikli aday olur.

Çıktı:
- output/video_NN/video_candidates.json
- output/video_NN/video_candidates_report.md
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
    "AnadoluGizemleriVideoDiscovery/2.0 "
    "(https://github.com/furukcell/anadolu-gizemleri-pipeline)"
)

# Bunlar artık sadece search_profile'da "strong_topic_terms" TANIMLANMAMIŞSA
# kullanılan bir uyarı/varsayılan örnektir - gerçek kullanımda her profile
# kendi strong_topic_terms listesini vermeli.
DEFAULT_STRONG_TOPIC_TERMS_EXAMPLE = ["gobekli", "göbekli", "karahantepe"]

DEFAULT_POSITIVE_TERMS = [
    "neolithic", "neolitik", "temple", "tapinak", "tapınak",
    "archaeology", "arkeoloji", "excavation", "kazı", "kazi",
    "reconstruction", "rekonstrüksiyon", "3d", "animation", "animasyon",
    "cgi", "ai", "cinematic", "belgesel", "documentary",
]

DEFAULT_NEGATIVE_TERMS = [
    "lycia", "lycian", "pinara", "ephesus", "efes", "roman", "rome", "greek",
    "pamukkale", "hierapolis", "perge", "side", "miletus", "troy", "troya", "truva",
    "hattusa", "hattuşa", "ani", "nemrut", "gordion", "istanbul", "cappadocia",
    "beach", "resort", "hotel", "vlog", "travel vlog", "tourist", "modern city",
    "cars", "football", "game", "minecraft",
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
        raise FileNotFoundError(
            f"Search profile bulunamadi: {profile_dir}/{day:02d}_*_queries.json\n"
            "V9 mod, konu adi hic gecmeyen genel videolarin kabul edilmesini "
            "engellemek icin her gun kendi 'strong_topic_terms' listesini "
            "tanimlayan bir search_profile ister. Varsayilan Göbeklitepe "
            "sorgularina otomatik geri donmuyor - bu, alakasiz video sizmasinin "
            "asil sebebiydi."
        )
    path = candidates[0]
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["_profile_path"] = str(path)
    profile.setdefault("min_topic_score", 8)
    profile.setdefault("max_results_per_query", 8)
    profile.setdefault("positive_terms", DEFAULT_POSITIVE_TERMS)
    profile.setdefault("negative_terms", DEFAULT_NEGATIVE_TERMS)
    profile.setdefault("discovery_queries", [])
    strong_terms = profile.get("strong_topic_terms") or []
    if not strong_terms:
        raise ValueError(
            f"Search profile icinde 'strong_topic_terms' bos/yok: {path}\n"
            "Bu liste olmadan hicbir aday kabul edilemez (genel kelimelerle "
            "puan toplayip gecmesin diye). Profile dosyasina konunun kendi "
            "adini/varyasyonlarini icak bir 'strong_topic_terms' dizisi ekle."
        )
    profile["strong_topic_terms"] = strong_terms
    print(f"[video_discovery] Search profile yuklendi -> {path}")
    return profile


def score_candidate(c: dict, profile: dict) -> tuple[int, bool]:
    """Puan ve 'konu adi gecti mi' bilgisini birlikte dondurur."""
    text = _norm(_safe_text(
        c.get("title"),
        c.get("description"),
        c.get("source_url"),
        c.get("download_url"),
        c.get("license"),
        c.get("creator"),
    ))

    strong_terms = profile.get("strong_topic_terms") or []
    positive_terms = profile.get("positive_terms") or DEFAULT_POSITIVE_TERMS
    negative_terms = profile.get("negative_terms") or DEFAULT_NEGATIVE_TERMS

    score = 0
    strong_hit = False

    # Konunun kendisi en onemli sey - ve artik ZORUNLU.
    normalized_strong = [_norm(t) for t in strong_terms]
    for t in normalized_strong:
        if t and t in text:
            score += 8
            strong_hit = True

    for term in positive_terms:
        t = _norm(term)
        if not t or t in normalized_strong:
            continue
        if t in text:
            score += 2

    for term in negative_terms:
        t = _norm(term)
        if t and t in text:
            score -= 10

    # Rekonstruksiyon/AI/3D gibi kelimeler ekstra degerli - ama tek basina
    # kabul icin yeterli degil, strong_hit sarti asagida ayrica kontrol edilir.
    for term in ["reconstruction", "rekonstruksiyon", "3d", "animation", "animasyon", "cgi", "ai"]:
        if term in text:
            score += 3

    # Indirilebilir ve lisans bilgisi olan kaynaklar daha kullanisli.
    if c.get("usable_for_auto_download"):
        score += 2
    if c.get("license"):
        score += 1

    return score, strong_hit


def _finalize_candidate(c: dict, profile: dict) -> dict:
    score, strong_hit = score_candidate(c, profile)
    c["topic_score"] = score
    c["strong_topic_match"] = strong_hit
    min_score = int(profile.get("min_topic_score", 8))
    # V9: strong_hit olmadan hicbir aday kabul edilmez - puan ne olursa olsun.
    c["accepted"] = bool(strong_hit) and score >= min_score
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
    q = f"({query}) AND mediatype:movies"
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
            "note": "Sadece strong_topic_terms eslesirse ve topic_score yeterliyse kullanilir.",
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
            "note": "Sadece strong_topic_terms eslesirse ve topic_score yeterliyse kullanilir.",
        })
    return out


def discover_candidates(day: int) -> dict:
    profile = load_search_profile(day)
    queries = _unique(profile.get("discovery_queries") or [])
    if not queries:
        raise RuntimeError("Search profile icinde discovery_queries bos.")

    print(f"[video_discovery] V9 konu videosu avcisi basladi. Sorgu sayisi: {len(queries)}")
    print(f"[video_discovery] Zorunlu strong_topic_terms: {profile.get('strong_topic_terms')}")

    raw_candidates = []
    for query in queries:
        print(f"[video_discovery] Araniyor -> {query}")
        raw_candidates.extend(search_youtube_cc(query, profile))
        raw_candidates.extend(search_wikimedia_commons(query, profile))
        raw_candidates.extend(search_internet_archive(query, profile))
        raw_candidates.extend(search_pexels_topic(query, profile))
        raw_candidates.extend(search_pixabay_topic(query, profile))

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

    rejected_but_high_score = [
        c for c in all_candidates
        if not c.get("accepted") and c.get("topic_score", 0) >= int(profile.get("min_topic_score", 8))
    ]
    if rejected_but_high_score:
        print(
            f"[video_discovery] Bilgi: {len(rejected_but_high_score)} aday yuksek puan aldi "
            "ama konu adi (strong_topic_terms) gecmedigi icin reddedildi - "
            "V8'de bunlar yanlislikla kabul ediliyordu."
        )

    result = {
        "day": day,
        "mode": "topic_video_discovery_v9",
        "profile_path": profile.get("_profile_path"),
        "min_topic_score": profile.get("min_topic_score", 8),
        "strong_topic_terms": profile.get("strong_topic_terms"),
        "query_count": len(queries),
        "candidate_count": len(all_candidates),
        "accepted_count": len(accepted),
        "auto_downloadable_count": len(auto_downloadable),
        "rejected_high_score_no_topic_match": len(rejected_but_high_score),
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
    lines.append(f"- Zorunlu strong_topic_terms: `{result.get('strong_topic_terms')}`")
    lines.append(f"- Min topic score: `{result.get('min_topic_score')}`")
    lines.append(f"- Total candidates: `{result.get('candidate_count')}`")
    lines.append(f"- Accepted candidates: `{result.get('accepted_count')}`")
    lines.append(f"- Auto-downloadable accepted: `{result.get('auto_downloadable_count')}`")
    lines.append(f"- Reddedilen (yuksek puan ama konu adi yok): `{result.get('rejected_high_score_no_topic_match')}`")
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
            "Konuya yakın (strong_topic_terms eslesen) otomatik indirilebilir video bulunamadi.\n"
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
