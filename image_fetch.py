"""
image_fetch.py
---------------
VIDEO-ONLY medya toplama modulu.

Bu dosya ismi eski pipeline ile uyum icin "image_fetch.py" olarak kalir ama
artik fotograf/gorsel toplamaz. Amac:
- Ken Burns / fotograf zoom hissini tamamen bitirmek
- Wikimedia ve Pexels photo kullanmamak
- Sadece gercek MP4 video klipleri indirmek
- Once AI/cinematic/reconstruction stock video aramak
- Pexels ve opsiyonel Pixabay video API kullanmak

Cikti:
  output/video_NN/media/scene_XX_YY.mp4
  output/video_NN/images_manifest.json

Opsiyonel ek secret:
  PIXABAY_API_KEY
"""

import json
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

import config

WORDS_PER_SECOND = 2.4 * config.GOOGLE_TTS_SPEAKING_RATE

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"

REQUEST_TIMEOUT = 20
RETRY_COUNT = 2
RETRY_SLEEP = 1.5

VIDEO_ONLY_MODE = True
ALLOW_PHOTOS = False

# Sahne basina video parca hedefleri.
INTRO_VIDEO_COUNT = 4
LONG_SCENE_VIDEO_COUNT = 4
MEDIUM_SCENE_VIDEO_COUNT = 3
SHORT_SCENE_VIDEO_COUNT = 2

# Çok az video bulunursa fail et. Yoksa yine dandik/eksik video cikar.
MIN_TOTAL_VIDEOS = int(os.environ.get("MIN_TOTAL_VIDEOS", "18"))
MAX_MISSING_SCENES = int(os.environ.get("MAX_MISSING_SCENES", "2"))

OFFTOPIC_TERMS = {
    "pinara", "lycia", "lycian", "efes", "ephesus", "troy", "troya", "truva",
    "ani", "hattusa", "hattusas", "pamukkale", "nemrut", "gordion",
    "hierapolis", "side", "perge", "miletus", "milet", "aspendos",
    "patara", "xanthos", "letuon", "myra", "olympos",
}

KNOWN_TOPICS = {
    "gobeklitepe": {
        "detect": ["gobekli", "gobeklitepe", "gobekli tepe", "karahantepe"],
        "title": "gobekli tepe karahantepe",
        "ai_stock_queries": [
            "ai generated gobekli tepe reconstruction",
            "gobekli tepe cinematic reconstruction",
            "karahantepe cinematic reconstruction",
            "neolithic stone temple cinematic reconstruction",
            "prehistoric stone pillars cinematic reconstruction",
            "ancient stone temple ai generated video",
            "ancient ritual fire cinematic temple",
            "buried ancient temple cinematic reconstruction",
            "mysterious stone pillars fog cinematic",
            "underground ancient temple cinematic",
        ],
        "stock_queries": [
            "neolithic stone pillars",
            "ancient stone pillars cinematic",
            "archaeological excavation cinematic",
            "ancient temple ruins cinematic",
            "mysterious ancient ruins fog",
            "stone temple ruins",
            "torch fire ancient ruins",
            "dark ancient temple",
            "ancient underground ruins",
        ],
    },
    "catalhoyuk": {
        "detect": ["catalhoyuk", "catal hoyuk", "çatalhöyük"],
        "title": "catalhoyuk",
        "ai_stock_queries": [
            "ai generated neolithic village reconstruction",
            "neolithic settlement cinematic reconstruction",
            "ancient mud brick village cinematic",
        ],
        "stock_queries": [
            "archaeological excavation cinematic",
            "ancient village ruins",
            "mud brick ruins",
        ],
    },
}

GENERIC_AI_STOCK_QUERIES = [
    "ai generated cinematic ancient ruins",
    "cinematic ancient temple reconstruction",
    "mysterious ancient ruins cinematic",
    "ancient ritual fire cinematic",
    "ancient underground temple cinematic",
    "archaeological mystery cinematic",
    "dark stone ruins cinematic",
]

GENERIC_STOCK_QUERIES = [
    "ancient ruins cinematic",
    "archaeological excavation",
    "mysterious ruins fog",
    "torch flame night",
    "stone ruins landscape",
]


def _turkish_to_ascii(text: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = (text or "").translate(tr_map)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def _normalized(text: str) -> str:
    return _turkish_to_ascii(text or "").lower()


def _unique(items):
    seen = set()
    out = []
    for item in items:
        item = (item or "").strip()
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def detect_topic(day_title: str, segments: list[dict]) -> str | None:
    text = _normalized(day_title)
    for seg in segments:
        text += " " + _normalized(seg.get("scene_note", ""))
        text += " " + _normalized(seg.get("narration", ""))

    for topic, data in KNOWN_TOPICS.items():
        if any(word in text for word in data["detect"]):
            return topic
    return None


def classify_scene(index: int, scene_note: str, narration: str, day_title: str) -> str:
    text = _normalized(f"{day_title} {scene_note} {narration}")

    if index == 0:
        return "intro"

    historical_terms = [
        "gobekli", "karahantepe", "catalhoyuk", "sutun", "dikilitas",
        "kabartma", "kazi", "arkeolojik", "kalinti", "tas oda", "insan basi",
    ]
    atmosphere_terms = [
        "siyah ekran", "ruzgar", "sis", "gece", "karanlik", "ates", "mesale",
        "golge", "yildiz", "ekran kararir", "topragin", "gizemli",
    ]
    theory_terms = [
        "neden", "bilerek", "gomdu", "gomuldu", "ritu", "inanc", "soru",
        "belki", "korku", "sembol", "bilinmeyen", "sir", "sirlar",
    ]

    hist_score = sum(1 for t in historical_terms if t in text)
    atm_score = sum(1 for t in atmosphere_terms if t in text)
    theory_score = sum(1 for t in theory_terms if t in text)

    if atm_score >= 2 and hist_score == 0:
        return "atmosphere"
    if theory_score >= 2 and hist_score <= 1:
        return "theory"
    if hist_score >= 1:
        return "historical_detail"
    if atm_score >= 1:
        return "atmosphere"
    return "mixed"


def estimate_seconds(word_count: int) -> float:
    if word_count <= 0:
        return config.MIN_SCENE_SECONDS
    seconds = word_count / WORDS_PER_SECOND
    return max(config.MIN_SCENE_SECONDS, min(seconds, config.MAX_SCENE_SECONDS))


def target_video_count(word_count: int, scene_type: str) -> int:
    if scene_type == "intro":
        return INTRO_VIDEO_COUNT
    if word_count >= 55:
        return LONG_SCENE_VIDEO_COUNT
    if word_count >= 22:
        return MEDIUM_SCENE_VIDEO_COUNT
    return SHORT_SCENE_VIDEO_COUNT


def _scene_keywords(scene_note: str, narration: str) -> list[str]:
    text = _normalized(f"{scene_note} {narration}")
    mapping = {
        "sis": "fog", "gece": "night", "karanlik": "dark", "ates": "fire",
        "mesale": "torch flame", "ruzgar": "wind", "toprak": "earth",
        "kazi": "archaeological excavation", "tapinak": "ancient temple",
        "sutun": "stone pillars", "dikilitas": "standing stones",
        "kabartma": "stone carving", "giz": "mysterious",
        "gom": "buried temple", "ritu": "ancient ritual",
        "magara": "cave", "oda": "stone chamber",
    }
    hits = []
    for tr, en in mapping.items():
        if tr in text:
            hits.append(en)
    return hits[:3]


def build_video_queries(scene_note: str, narration: str, day_title: str, scene_type: str, topic: str | None) -> list[str]:
    topic_data = KNOWN_TOPICS.get(topic) if topic else None
    scene_hits = _scene_keywords(scene_note, narration)
    scene_tail = " ".join(scene_hits)

    queries = []

    if topic_data:
        base_ai = topic_data["ai_stock_queries"]
        base_stock = topic_data["stock_queries"]
    else:
        base_ai = GENERIC_AI_STOCK_QUERIES
        base_stock = GENERIC_STOCK_QUERIES

    # AI/cinematic stock aramasi ONCELIKLI.
    for q in base_ai:
        queries.append(f"{q} {scene_tail}".strip())

    if scene_type == "intro":
        queries = [
            "ai generated cinematic ancient stone temple night",
            "cinematic ancient stone temple night fog",
            "mysterious stone pillars cinematic night",
            "ancient temple reconstruction camera movement",
        ] + queries
    elif scene_type == "theory":
        queries = [
            "ai generated ancient ritual fire cinematic",
            "mysterious ancient symbols cinematic video",
            "buried temple cinematic reconstruction",
        ] + queries
    elif scene_type == "atmosphere":
        queries = [
            "dark ancient ruins fog cinematic",
            "torch flame ancient ruins cinematic",
            "mysterious stone temple fog video",
        ] + queries
    elif scene_type == "historical_detail":
        # Tarihi detayda bile foto yok; yine video ariyoruz.
        queries = [
            "stone pillars archaeological site video",
            "ancient stone carvings cinematic video",
            "archaeological excavation cinematic video",
        ] + queries

    for q in base_stock:
        queries.append(f"{q} {scene_tail}".strip())

    return _unique(queries)


def _get_with_retry(url, params=None, headers=None, stream=False):
    last_err = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream
            )
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_SLEEP)

    # Logu bogmamak icin sadece kisa bas.
    print(f"[image_fetch] Istek basarisiz: {last_err} -> {url}")
    return None


def _is_offtopic(text: str) -> bool:
    norm = _normalized(text)
    return any(term in norm for term in OFFTOPIC_TERMS)


def _best_pexels_video_link(video: dict):
    video_files = video.get("video_files", [])
    if not video_files:
        return None

    # Sadece mp4 ve yataya yakin dosyalari tercih et.
    filtered = []
    for vf in video_files:
        link = vf.get("link")
        width = vf.get("width") or 0
        height = vf.get("height") or 0
        if not link:
            continue
        if "mp4" not in (vf.get("file_type") or "video/mp4"):
            continue
        if width and height and width < height:
            continue
        filtered.append(vf)

    if not filtered:
        filtered = [vf for vf in video_files if vf.get("link")]

    def score(vf):
        w = vf.get("width") or 0
        h = vf.get("height") or 0
        # 1920x1080'e yakinlik
        return abs(w - config.VIDEO_WIDTH) + abs(h - config.VIDEO_HEIGHT)

    best = sorted(filtered, key=score)[0]
    return best.get("link")


def search_pexels_video_candidates(query: str):
    if not config.PEXELS_API_KEY:
        return []

    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {"query": query, "per_page": 12, "orientation": "landscape"}
    resp = _get_with_retry(PEXELS_VIDEO_API, params=params, headers=headers)
    if not resp:
        return []

    urls = []
    for video in resp.json().get("videos", []):
        meta_text = " ".join([
            str(video.get("url", "")),
            str(video.get("user", {}).get("name", "")),
        ])
        if _is_offtopic(meta_text):
            continue

        url = _best_pexels_video_link(video)
        if url:
            urls.append(url)
    return _unique(urls)


def _best_pixabay_video_url(hit: dict):
    videos = hit.get("videos", {})
    # Large cok agir olabilir; medium genelde yeterli ve hizli.
    for key in ("large", "medium", "small", "tiny"):
        item = videos.get(key) or {}
        url = item.get("url")
        width = item.get("width") or 0
        height = item.get("height") or 0
        if url and (not width or width >= height):
            return url
    return None


def search_pixabay_video_candidates(query: str):
    api_key = os.environ.get("PIXABAY_API_KEY", "")
    if not api_key:
        return []

    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "orientation": "horizontal",
        "per_page": 12,
        "safesearch": "true",
    }
    resp = _get_with_retry(PIXABAY_VIDEO_API, params=params)
    if not resp:
        return []

    urls = []
    for hit in resp.json().get("hits", []):
        meta_text = " ".join([
            str(hit.get("tags", "")),
            str(hit.get("pageURL", "")),
            str(hit.get("user", "")),
        ])
        if _is_offtopic(meta_text):
            continue
        url = _best_pixabay_video_url(hit)
        if url:
            urls.append(url)
    return _unique(urls)


def download_video(url: str, dest_path: Path) -> bool:
    resp = _get_with_retry(url, stream=True)
    if not resp:
        return False

    content_type = (resp.headers.get("content-type") or "").lower()
    # Bazı CDN'ler content-type bos donebilir; sadece text/html ise reddet.
    if "text/html" in content_type:
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".tmp")

    total = 0
    with tmp_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            f.write(chunk)

    if total < 100_000:
        tmp_path.unlink(missing_ok=True)
        return False

    tmp_path.replace(dest_path)
    return True


def video_candidates_for_queries(queries):
    # Once Pixabay varsa onu dene, sonra Pexels. Ikisi de sadece MP4 video.
    for query in queries:
        for url in search_pixabay_video_candidates(query):
            yield "video", "pixabay_video", query, url
        for url in search_pexels_video_candidates(query):
            yield "video", "pexels_video", query, url


def fetch_one_video(scene_index, item_index, queries, dest_dir, used_urls):
    base_name = f"scene_{scene_index:02d}_{item_index:02d}"
    for media_type, source, used_query, url in video_candidates_for_queries(queries):
        if url in used_urls:
            continue

        dest_path = dest_dir / f"{base_name}.mp4"
        if download_video(url, dest_path):
            used_urls.add(url)
            return {
                "media_path": str(dest_path.relative_to(config.BASE_DIR)),
                "media_type": "video",
                "media_source": source,
                "media_query": used_query,
                "source_url": url,
            }
    return None


def process_day(day: int, save_json: bool = True) -> dict:
    parsed_path = config.OUTPUT_DIR / f"video_{day:02d}" / "script_parsed.json"
    if not parsed_path.exists():
        raise FileNotFoundError(
            f"script_parsed.json bulunamadi: {parsed_path}. Once script_parse.py calistirilmali."
        )

    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    day_title = parsed["title"]
    segments = parsed["segments"]
    topic = detect_topic(day_title, segments)

    print("[image_fetch] VIDEO-ONLY MOD AKTIF: fotograf/Wikimedia/Ken Burns yok.")
    print(f"[image_fetch] Topic context: {topic or 'general'}")
    print("[image_fetch] Kaynaklar: Pixabay video API (varsa) + Pexels video API")
    print(f"[image_fetch] Minimum toplam video hedefi: {MIN_TOTAL_VIDEOS}")

    media_dir = config.OUTPUT_DIR / f"video_{day:02d}" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    used_urls = set()
    scenes = []

    for idx, seg in enumerate(segments):
        scene_note = seg.get("scene_note") or ""
        narration = seg.get("narration", "")
        word_count = len(narration.split())
        scene_type = classify_scene(idx, scene_note, narration, day_title)
        desired_count = target_video_count(word_count, scene_type)

        video_queries = build_video_queries(scene_note, narration, day_title, scene_type, topic)

        media_items = []
        for item_idx in range(desired_count):
            # Her parca icin query listesini kaydir; ayni klipleri azaltir.
            rotated = video_queries[item_idx:] + video_queries[:item_idx]
            item = fetch_one_video(idx, item_idx, rotated, media_dir, used_urls)
            if item:
                media_items.append(item)
                print(
                    f"[image_fetch] Gun {day} sahne {idx}.{item_idx}: "
                    f"[video] {item['media_source']} -> {item['media_query']}"
                )

        if not media_items:
            print(f"[image_fetch] Gun {day} sahne {idx}: VIDEO BULUNAMADI -> {video_queries[0]}")

        first = media_items[0] if media_items else {}
        scenes.append({
            "index": idx,
            "scene_type": scene_type,
            "scene_note": scene_note,
            "narration": narration,
            "word_count": word_count,
            "estimated_seconds": round(estimate_seconds(word_count), 2),
            "media_items": media_items,
            # Eski youtube_montaj uyumlulugu:
            "media_path": first.get("media_path"),
            "media_type": first.get("media_type", "none"),
            "media_source": first.get("media_source", "none"),
            "media_query": first.get("media_query", video_queries[0] if video_queries else ""),
            "video_queries": video_queries[:8],
            "photo_queries": [],
            "ai_video_queries": video_queries[:8],
        })

    all_items = [item for scene in scenes for item in scene.get("media_items", [])]
    missing = [s for s in scenes if not s.get("media_items")]
    video_count = len(all_items)

    if save_json:
        manifest_path = config.OUTPUT_DIR / f"video_{day:02d}" / "images_manifest.json"
        result = {
            "day": day,
            "title": day_title,
            "mode": "video_only",
            "topic": topic,
            "scenes": scenes,
        }
        manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[image_fetch] Gun {day} tamamlandi -> {manifest_path}")
    else:
        result = {"day": day, "title": day_title, "mode": "video_only", "topic": topic, "scenes": scenes}

    print(f"[image_fetch] Gun {day}: 0 foto, {video_count} GERCEK MP4 video klip kullanildi.")
    print(f"[image_fetch] Medyasiz sahne sayisi: {len(missing)}")

    if video_count < MIN_TOTAL_VIDEOS:
        raise RuntimeError(
            f"Yeterli video bulunamadi: {video_count}/{MIN_TOTAL_VIDEOS}. "
            f"PEXELS_API_KEY/PIXABAY_API_KEY ve query kalitesini kontrol et."
        )

    if len(missing) > MAX_MISSING_SCENES:
        raise RuntimeError(
            f"Cok fazla sahne videosuz kaldi: {len(missing)} sahne. "
            f"Bu haliyle fotograf/black screen hissi olusur; pipeline durduruldu."
        )

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        process_day(int(sys.argv[1]))
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
