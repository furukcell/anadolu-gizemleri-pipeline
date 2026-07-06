"""
image_fetch.py
---------------
Dinamik belgesel medya toplama modulu - v4.

Hedef:
- Tek fotograf slayt hissini kirar.
- Yaklasik %20 gercek fotograf / %80 AI-cinematic video agirligi hedefler.
- Gobeklitepe/Karahantepe gibi konu belliyse alakasiz antik kentleri eler.
- Wikimedia 403 hatalarini azaltmak icin dosya indirirken de User-Agent/Referer gonderir.
- Wikimedia 403 spam'ini loga basmaz; sessizce Pexels/AI video fallback'e duser.

Not:
Buradaki "AI video" sifirdan video uretimi degildir. Pexels gibi stok kaynaklarda
"ai generated / cinematic / reconstruction" sorgulari ile bulunan AI/cinematic
stok video klipleridir. Gercek AI video uretimi icin ayri bir Runway/Pika/Luma
tarzi API entegrasyonu gerekir.
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

import config

WORDS_PER_SECOND = 2.4 * config.GOOGLE_TTS_SPEAKING_RATE

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"

WIKIMEDIA_HEADERS = {
    "User-Agent": config.WIKIMEDIA_USER_AGENT,
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 15
RETRY_COUNT = 2
RETRY_SLEEP = 2

# Video dili: daha akici belgesel icin agirlikli AI/cinematic video.
TARGET_AI_VIDEO_RATIO = 0.80
TARGET_REAL_PHOTO_RATIO = 0.20

NOISE_WORDS = {
    "ekrana", "gelir", "gorunur", "goruntusu", "sahne", "kamera",
    "yakin", "cekim", "gecis", "efekt", "yavasca", "hafif", "baslar",
    "eski", "bir", "ve", "ile", "olan", "gelen", "derinden", "detay",
    "bicimli", "biçimli", "olarak", "arkadan", "uzaktan",
}

KNOWN_SITE_QUERY_MAP = {
    "gobeklitepe": "gobekli tepe archaeological site stone pillars",
    "gobekli tepe": "gobekli tepe archaeological site stone pillars",
    "gobekli": "gobekli tepe archaeological site stone pillars",
    "karahantepe": "karahantepe archaeological site stone chambers",
    "catalhoyuk": "catalhoyuk neolithic settlement archaeology",
    "catal hoyuk": "catalhoyuk neolithic settlement archaeology",
    "hattusa": "hattusa hittite ruins turkey",
    "hattusas": "hattusa hittite ruins turkey",
    "troya": "troy ancient city ruins turkey",
    "truva": "troy ancient city ruins turkey",
    "nemrut": "mount nemrut statues turkey",
    "efes": "ephesus ancient city ruins turkey",
    "ephesus": "ephesus ancient city ruins turkey",
    "pamukkale": "hierapolis pamukkale ancient city turkey",
    "ani": "ani ruins turkey medieval city",
    "gordion": "gordion ancient city tumulus turkey",
}

TURKISH_TERM_MAP = {
    "siyah": "dark", "ekran": "screen", "ruzgar": "wind",
    "golge": "shadow", "tas": "stone", "sutun": "pillar",
    "sutunlar": "pillars", "dikilitas": "standing stone",
    "dikilitaslar": "standing stones", "kalinti": "ruins",
    "kalintilar": "ruins", "kazi": "excavation", "alan": "site",
    "tapinak": "temple", "oda": "chamber", "odalar": "chambers",
    "karanlik": "dark", "sis": "mist", "ates": "fire",
    "mesale": "torch", "hayvan": "animal", "kabartma": "relief carving",
    "kabartmalari": "relief carvings", "bas": "head", "insan": "human",
    "figur": "figure", "figür": "figure", "toprak": "earth",
    "arkeolojik": "archaeological", "havadan": "aerial view",
    "animasyon": "cinematic reconstruction", "rekonstruksiyon": "cinematic reconstruction",
    "gizem": "mystery", "gizemli": "mysterious",
}

FALLBACK_PHOTO_QUERIES = [
    "neolithic stone pillars archaeology",
    "archaeological excavation turkey",
    "ancient stone relief carving",
]

FALLBACK_AI_VIDEO_QUERIES = [
    "ai generated cinematic ancient ruins",
    "ai generated archaeological excavation",
    "cinematic ancient temple mysterious",
    "neolithic stone temple cinematic reconstruction",
    "ancient mystery cinematic reconstruction",
    "dark archaeological ruins cinematic",
]

FALLBACK_VIDEO_QUERIES = [
    "ancient ruins fog",
    "mysterious ancient temple",
    "torch flame night",
    "archaeological excavation",
    "dark ancient ruins cinematic",
]

TOPIC_RULES = {
    "gobeklitepe": {
        "allow": {
            "gobekli", "gobekli tepe", "gobeklitepe", "karahantepe",
            "neolithic", "stone pillar", "stone pillars", "standing stone",
            "relief", "excavation", "urfa", "sanliurfa", "şanlıurfa",
            "tas tepe", "tepe", "archaeological",
        },
        "preferred_photo_queries": [
            "gobekli tepe archaeological site",
            "gobekli tepe stone pillars",
            "gobekli tepe excavation",
            "gobekli tepe animal relief carving",
            "gobekli tepe aerial view",
            "karahantepe archaeological site",
            "karahantepe stone chambers",
            "karahantepe human head sculpture",
            "neolithic stone pillars archaeology",
        ],
        "preferred_ai_video_queries": [
            "ai generated neolithic stone temple cinematic reconstruction",
            "ai generated gobekli tepe stone temple reconstruction",
            "cinematic neolithic stone pillars mysterious",
            "ancient stone temple cinematic reconstruction",
            "mysterious stone pillars night cinematic",
            "archaeological excavation cinematic reconstruction",
            "ancient ritual fire stone temple cinematic",
        ],
        "preferred_video_queries": [
            "neolithic stone pillars archaeology",
            "archaeological excavation neolithic",
            "ancient stone temple cinematic reconstruction",
            "mysterious stone pillars night cinematic",
        ],
    }
}

OFFTOPIC_TERMS = {
    "pinara", "lycia", "lycian", "efes", "ephesus", "troy", "troya", "truva",
    "ani", "hattusa", "hattusas", "pamukkale", "nemrut", "gordion",
    "hierapolis", "side", "perge", "miletus", "miletos", "priene",
    "assos", "aphrodisias", "sagalassos", "myra", "xanthos", "lettoon",
}


def _turkish_to_ascii(text: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = text.translate(tr_map)
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


def _known_site_query(text: str) -> str | None:
    norm = _normalized(text)
    for key, query in KNOWN_SITE_QUERY_MAP.items():
        if key in norm:
            return query
    return None


def detect_topic_context(day_title: str, segments: list[dict]) -> str | None:
    text = _normalized(day_title or "")
    for seg in segments:
        text += " " + _normalized(seg.get("scene_note", ""))
        text += " " + _normalized(seg.get("narration", ""))

    if "gobekli" in text or "karahantepe" in text:
        return "gobeklitepe"

    return None


def has_offtopic_terms(candidate_text: str) -> bool:
    norm = _normalized(candidate_text)
    return any(bad in norm for bad in OFFTOPIC_TERMS)


def is_wikimedia_candidate_relevant(candidate_text: str, topic_ctx: str | None) -> bool:
    norm = _normalized(candidate_text)

    if has_offtopic_terms(norm):
        return False

    if not topic_ctx:
        return True

    topic_rule = TOPIC_RULES.get(topic_ctx)
    if not topic_rule:
        return True

    # Wikimedia gercek fotograf kaynagi oldugu icin konu kilidi burada siki.
    for good in topic_rule["allow"]:
        if _normalized(good) in norm:
            return True

    return False


def is_stock_candidate_safe(candidate_text: str) -> bool:
    # Pexels gibi stok kaynaklarda URL/alt her zaman konu kelimesi tasimaz.
    # Bu yuzden sadece bariz alakasiz antik kentleri eliyoruz; sorguya guveniyoruz.
    return not has_offtopic_terms(candidate_text)


def _map_visual_words(words):
    mapped = []
    for w in words:
        mapped_term = TURKISH_TERM_MAP.get(w, w)
        if mapped_term not in mapped:
            mapped.append(mapped_term)
    return mapped


def classify_scene(index: int, scene_note: str, narration: str, day_title: str) -> str:
    text = _normalized(f"{day_title} {scene_note} {narration}")

    if index == 0:
        return "intro"

    historical_terms = [
        "gobekli", "karahantepe", "catalhoyuk", "hattusa", "troya", "truva",
        "sutun", "dikilitas", "kabartma", "kazi alani", "tas oda",
        "insan basi", "hayvan", "arkeolojik", "kalinti",
    ]
    atmosphere_terms = [
        "siyah ekran", "ruzgar", "sis", "gece", "karanlik", "ates",
        "mesale", "golge", "yildiz", "ekran kararir", "animasyon",
        "topragin", "kapattigi", "gizemli",
    ]
    theory_terms = [
        "neden", "bilerek", "gomdu", "gomuldu", "ritu", "rituel", "inanc", "soru",
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


def build_query_from_scene_note(scene_note: str, day_title: str) -> str:
    combined = f"{day_title or ''} {scene_note or ''}"
    known_query = _known_site_query(combined)

    if not scene_note:
        return known_query or _turkish_to_ascii(day_title)

    note = _normalized(scene_note)
    words = re.findall(r"[a-z0-9]+", note)
    words = [w for w in words if w not in NOISE_WORDS and len(w) > 2]
    mapped_words = _map_visual_words(words)

    if known_query:
        visual_tail = " ".join(mapped_words[:3]).strip()
        return f"{known_query} {visual_tail}".strip()

    query = " ".join(mapped_words[:7]).strip()
    return query or _turkish_to_ascii(day_title)


def build_photo_query_variants(scene_note: str, day_title: str, scene_type: str, topic_ctx: str | None = None):
    text = _normalized(f"{day_title} {scene_note}")
    base = build_query_from_scene_note(scene_note, day_title)
    known = _known_site_query(text)

    variants = [base, known]

    if "gobekli" in text:
        variants += [
            "gobekli tepe archaeological site",
            "gobekli tepe stone pillars",
            "gobekli tepe animal relief carving",
            "gobekli tepe excavation",
            "gobekli tepe aerial view",
        ]
    if "karahantepe" in text:
        variants += [
            "karahantepe archaeological site",
            "karahantepe stone chambers",
            "karahantepe human head sculpture",
            "karahantepe excavation",
        ]
    if "kabartma" in text or "hayvan" in text:
        variants += [
            "gobekli tepe animal relief",
            "neolithic animal relief carving",
            "ancient stone relief carving",
        ]
    if "kazi" in text or "toprak" in text:
        variants += [
            "archaeological excavation turkey",
            "neolithic archaeological excavation",
        ]

    if topic_ctx and topic_ctx in TOPIC_RULES:
        variants += TOPIC_RULES[topic_ctx]["preferred_photo_queries"]
    else:
        variants += FALLBACK_PHOTO_QUERIES

    return _unique(variants)


def build_video_query_from_scene_note(scene_note: str) -> str:
    atmosphere_map = {
        "sis": "fog mist", "gece": "night", "ates": "fire torch",
        "mesale": "torch flame", "tas": "stone ruins", "kalinti": "ancient ruins",
        "karanlik": "dark mysterious", "gizem": "mysterious ancient",
        "golge": "shadow silhouette", "ay": "moonlight night",
        "ruzgar": "windy landscape", "toprak": "ancient earth excavation",
        "kazi": "archaeological excavation", "tapinak": "ancient temple",
        "mezar": "ancient tomb", "yildiz": "starry sky",
        "sutun": "stone pillars", "kabartma": "ancient carving",
        "animasyon": "cinematic reconstruction",
    }
    if not scene_note:
        return "ancient ruins mist"
    note = _normalized(scene_note)
    hits = [eng for tr, eng in atmosphere_map.items() if tr in note]
    return " ".join(hits[:2]) if hits else "ancient ruins mist"


def build_video_query_variants(scene_note: str, day_title: str, scene_type: str, topic_ctx: str | None = None):
    base = build_video_query_from_scene_note(scene_note)
    variants = [base]

    if scene_type == "intro":
        variants += [
            "cinematic ancient ruins night stars",
            "mysterious stone temple night cinematic",
            "ancient ruins fog cinematic",
            "dark archaeological ruins cinematic",
        ]
    elif scene_type == "atmosphere":
        variants += [
            "mysterious ancient ruins fog",
            "torch flame ancient ruins",
            "dark stone ruins cinematic",
            "ancient temple night atmosphere",
        ]
    elif scene_type == "theory":
        variants += [
            "cinematic ancient ritual fire",
            "mysterious ancient symbols",
            "archaeological mystery cinematic",
        ]
    else:
        variants += [
            "archaeological excavation",
            "ancient ruins cinematic",
            "stone ruins landscape",
        ]

    if topic_ctx and topic_ctx in TOPIC_RULES:
        variants += TOPIC_RULES[topic_ctx]["preferred_video_queries"]
    else:
        variants += FALLBACK_VIDEO_QUERIES

    return _unique(variants)


def build_ai_video_query_variants(scene_note: str, day_title: str, scene_type: str, topic_ctx: str | None = None):
    text = _normalized(f"{day_title} {scene_note}")
    atmosphere = build_video_query_from_scene_note(scene_note)

    if topic_ctx == "gobeklitepe" or "gobekli" in text or "karahantepe" in text:
        base = "neolithic stone temple archaeological reconstruction"
    elif "hattusa" in text:
        base = "ancient hittite city ruins reconstruction"
    elif "troya" in text or "truva" in text:
        base = "ancient city ruins cinematic reconstruction"
    else:
        base = "ancient anatolia ruins cinematic reconstruction"

    variants = [
        f"ai generated {base} {atmosphere}",
        f"cinematic {base}",
        f"ancient mystery {atmosphere}",
    ]

    if scene_type == "intro":
        variants.insert(0, "ai generated cinematic ancient ruins night stars")
    if scene_type == "theory":
        variants.insert(0, "ai generated ancient ritual mysterious temple")

    if topic_ctx and topic_ctx in TOPIC_RULES:
        variants = TOPIC_RULES[topic_ctx]["preferred_ai_video_queries"] + variants
    else:
        variants += FALLBACK_AI_VIDEO_QUERIES

    return _unique(variants)


def estimate_seconds(word_count: int) -> float:
    if word_count <= 0:
        return config.MIN_SCENE_SECONDS
    seconds = word_count / WORDS_PER_SECOND
    return max(config.MIN_SCENE_SECONDS, min(seconds, config.MAX_SCENE_SECONDS))


def target_media_count(word_count: int, scene_type: str) -> int:
    if scene_type == "intro":
        return 3
    if word_count >= 55:
        return 3
    if word_count >= 22:
        return 2
    return 1


def _get_with_retry(url, params=None, headers=None, verbose=True):
    last_err = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_SLEEP)
    if verbose:
        print(f"[image_fetch] Istek basarisiz ({url}): {last_err}")
    return None


def search_wikimedia_candidates(query: str, topic_ctx: str | None = None):
    search_params = {
        "action": "query", "format": "json", "list": "search",
        "srsearch": f"{query} filetype:bitmap", "srnamespace": 6, "srlimit": 8,
    }
    resp = _get_with_retry(WIKIMEDIA_API, params=search_params, headers=WIKIMEDIA_HEADERS)
    if not resp:
        return []

    results = resp.json().get("query", {}).get("search", [])
    urls = []
    for result in results:
        title = result.get("title")
        if not title:
            continue

        if not is_wikimedia_candidate_relevant(title, topic_ctx):
            continue

        info_params = {
            "action": "query", "format": "json", "titles": title,
            "prop": "imageinfo", "iiprop": "url|size", "iiurlwidth": config.VIDEO_WIDTH,
        }
        info_resp = _get_with_retry(WIKIMEDIA_API, params=info_params, headers=WIKIMEDIA_HEADERS)
        if not info_resp:
            continue
        pages = info_resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            imageinfo = page.get("imageinfo")
            if imageinfo:
                url = imageinfo[0].get("thumburl") or imageinfo[0].get("url")
                if url and is_wikimedia_candidate_relevant(f"{title} {url}", topic_ctx):
                    urls.append(url)
    return _unique(urls)


def search_pexels_photo_candidates(query: str, topic_ctx: str | None = None):
    if not config.PEXELS_API_KEY:
        print("[image_fetch] PEXELS_API_KEY tanimli degil, Pexels foto atlaniyor.")
        return []
    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {"query": query, "per_page": 10, "orientation": "landscape"}
    resp = _get_with_retry(PEXELS_PHOTO_API, params=params, headers=headers)
    if not resp:
        return []
    urls = []
    for photo in resp.json().get("photos", []):
        candidate_text = " ".join([
            str(photo.get("alt", "")),
            str(photo.get("photographer", "")),
            str(photo.get("url", "")),
        ])
        if not is_stock_candidate_safe(candidate_text):
            continue
        src = photo.get("src", {})
        url = src.get("large2x") or src.get("large") or src.get("original")
        if url:
            urls.append(url)
    return _unique(urls)


def _best_video_link(video: dict):
    video_files = video.get("video_files", [])
    if not video_files:
        return None

    def _width_score(vf):
        w = vf.get("width") or 0
        return abs(w - config.VIDEO_WIDTH)

    best = sorted(video_files, key=_width_score)[0]
    return best.get("link")


def search_pexels_video_candidates(query: str, topic_ctx: str | None = None):
    if not config.PEXELS_API_KEY:
        print("[image_fetch] PEXELS_API_KEY tanimli degil, Pexels video atlaniyor.")
        return []
    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {"query": query, "per_page": 10, "orientation": "landscape"}
    resp = _get_with_retry(PEXELS_VIDEO_API, params=params, headers=headers)
    if not resp:
        return []
    urls = []
    for video in resp.json().get("videos", []):
        candidate_text = " ".join([
            str(video.get("url", "")),
            str(video.get("user", {}).get("name", "")),
        ])
        if not is_stock_candidate_safe(candidate_text):
            continue
        url = _best_video_link(video)
        if url:
            urls.append(url)
    return _unique(urls)


def download_media(url: str, dest_path: Path) -> bool:
    headers = None
    verbose = True

    if "wikimedia.org" in url or "wikipedia.org" in url:
        headers = dict(WIKIMEDIA_HEADERS)
        headers["Referer"] = "https://commons.wikimedia.org/"
        # Wikimedia bazen 403 verir; bunu logda yüzlerce kez basmayalim.
        verbose = False

    resp = _get_with_retry(url, headers=headers, verbose=verbose)
    if not resp or not resp.content:
        return False

    # Sacma / bos dosya inmesin.
    if len(resp.content) < 1024:
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    return True


def desired_provider_order(scene_type: str, item_index: int, stats: dict, targets: dict):
    """
    %80 AI/cinematic video, %20 gercek fotograf hedefi.
    - Intro/atmosfer/teori: kesinlikle AI/cinematic video agirlikli.
    - Historical/detail: bazen gercek fotografla kanit/zemin ver, sonra yine AI video.
    """
    ai_needed = stats["ai_video"] < targets["ai_video"]
    photo_needed = stats["image"] < targets["image"]

    # İlk sahne her zaman video enerjisiyle baslasin.
    if scene_type == "intro":
        return ["pexels_ai_video", "pexels_video", "wikimedia", "pexels_photo"]

    # Tarihi detay sahnesinde kotayi doldurmak icin az sayida gercek foto kullanalim.
    if scene_type == "historical_detail" and photo_needed and item_index == 0:
        return ["wikimedia", "pexels_photo", "pexels_ai_video", "pexels_video"]

    # Foto kotasi hâlâ dolmadiysa her 5 medyadan biri gerçek foto olsun.
    total_used = stats["image"] + stats["video"]
    if photo_needed and total_used % 5 == 0:
        return ["wikimedia", "pexels_photo", "pexels_ai_video", "pexels_video"]

    # Ana mod: AI/cinematic video.
    if ai_needed:
        return ["pexels_ai_video", "pexels_video", "wikimedia", "pexels_photo"]

    # AI hedefi dolduysa kalanlarda normal video/foto fallback.
    return ["pexels_video", "pexels_ai_video", "wikimedia", "pexels_photo"]


def candidates_for_provider(provider: str, photo_queries, video_queries, ai_video_queries, topic_ctx: str | None):
    if provider == "wikimedia":
        for query in photo_queries:
            for url in search_wikimedia_candidates(query, topic_ctx):
                yield "image", "wikimedia", query, url
    elif provider == "pexels_photo":
        for query in photo_queries:
            for url in search_pexels_photo_candidates(query, topic_ctx):
                yield "image", "pexels_photo", query, url
    elif provider == "pexels_video":
        for query in video_queries:
            for url in search_pexels_video_candidates(query, topic_ctx):
                yield "video", "pexels_video", query, url
    elif provider == "pexels_ai_video" and config.AI_VIDEO_ENABLED:
        for query in ai_video_queries:
            for url in search_pexels_video_candidates(query, topic_ctx):
                yield "video", "pexels_ai_video", query, url


def fetch_one_media_item(
    scene_index,
    item_index,
    scene_type,
    photo_queries,
    video_queries,
    ai_video_queries,
    dest_dir,
    used_urls,
    topic_ctx,
    stats,
    targets,
):
    base_name = f"scene_{scene_index:02d}_{item_index:02d}"

    for provider in desired_provider_order(scene_type, item_index, stats, targets):
        for media_type, source, used_query, url in candidates_for_provider(
            provider, photo_queries, video_queries, ai_video_queries, topic_ctx
        ):
            if url in used_urls:
                continue

            ext = "mp4" if media_type == "video" else "jpg"
            dest_path = dest_dir / f"{base_name}.{ext}"

            if download_media(url, dest_path):
                used_urls.add(url)

                if media_type == "image":
                    stats["image"] += 1
                else:
                    stats["video"] += 1
                    if "ai_video" in source:
                        stats["ai_video"] += 1

                return {
                    "media_path": str(dest_path.relative_to(config.BASE_DIR)),
                    "media_type": media_type,
                    "media_source": source,
                    "media_query": used_query,
                    "source_url": url,
                }

    return None


def process_day(day: int, save_json: bool = True) -> dict:
    parsed_path = config.OUTPUT_DIR / f"video_{day:02d}" / "script_parsed.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"script_parsed.json bulunamadi: {parsed_path}. Once script_parse.py calistirilmali.")

    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    day_title = parsed["title"]
    segments = parsed["segments"]
    topic_ctx = detect_topic_context(day_title, segments)

    media_dir = config.OUTPUT_DIR / f"video_{day:02d}" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    used_urls = set()
    scenes = []

    planned_counts = []
    for idx, seg in enumerate(segments):
        scene_type = classify_scene(idx, seg.get("scene_note"), seg.get("narration", ""), day_title)
        planned_counts.append(target_media_count(len(seg.get("narration", "").split()), scene_type))

    planned_total = max(sum(planned_counts), 1)
    targets = {
        "image": max(1, round(planned_total * TARGET_REAL_PHOTO_RATIO)),
        "ai_video": max(1, round(planned_total * TARGET_AI_VIDEO_RATIO)),
    }
    stats = {"image": 0, "video": 0, "ai_video": 0}

    print(f"[image_fetch] Gun {day} topic context: {topic_ctx or 'general'}")
    print(
        f"[image_fetch] Gun {day} hedef oran: "
        f"%{int(TARGET_REAL_PHOTO_RATIO * 100)} gercek foto / "
        f"%{int(TARGET_AI_VIDEO_RATIO * 100)} AI-cinematic video "
        f"(planlanan toplam {planned_total} medya)"
    )

    for idx, seg in enumerate(segments):
        scene_note = seg.get("scene_note")
        narration = seg.get("narration", "")
        word_count = len(narration.split())
        scene_type = classify_scene(idx, scene_note, narration, day_title)
        desired_count = target_media_count(word_count, scene_type)

        photo_queries = build_photo_query_variants(scene_note, day_title, scene_type, topic_ctx)
        video_queries = build_video_query_variants(scene_note, day_title, scene_type, topic_ctx)
        ai_video_queries = build_ai_video_query_variants(scene_note, day_title, scene_type, topic_ctx)

        media_items = []
        for item_idx in range(desired_count):
            item = fetch_one_media_item(
                idx, item_idx, scene_type,
                photo_queries[item_idx:] + photo_queries[:item_idx],
                video_queries[item_idx:] + video_queries[:item_idx],
                ai_video_queries[item_idx:] + ai_video_queries[:item_idx],
                media_dir, used_urls, topic_ctx, stats, targets,
            )
            if item:
                media_items.append(item)
                print(
                    f"[image_fetch] Gun {day} sahne {idx}.{item_idx}: "
                    f"[{item['media_type']}] {item['media_source']} -> {item['media_query']}"
                )

        if not media_items:
            print(f"[image_fetch] Gun {day} sahne {idx}: MEDYA BULUNAMADI ({photo_queries[0]})")

        first = media_items[0] if media_items else {}
        scenes.append({
            "index": idx,
            "scene_type": scene_type,
            "scene_note": scene_note,
            "narration": narration,
            "word_count": word_count,
            "estimated_seconds": round(estimate_seconds(word_count), 2),
            "media_items": media_items,
            "media_path": first.get("media_path"),
            "media_type": first.get("media_type", "none"),
            "media_source": first.get("media_source", "none"),
            "media_query": first.get("media_query", photo_queries[0] if photo_queries else ""),
            "photo_queries": photo_queries[:5],
            "video_queries": video_queries[:5],
            "ai_video_queries": ai_video_queries[:5],
        })

    result = {"day": day, "title": day_title, "topic_context": topic_ctx, "scenes": scenes}

    if save_json:
        manifest_path = config.OUTPUT_DIR / f"video_{day:02d}" / "images_manifest.json"
        manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[image_fetch] Gun {day} tamamlandi -> {manifest_path}")

    all_items = [item for scene in scenes for item in scene.get("media_items", [])]
    image_count = sum(1 for item in all_items if item["media_type"] == "image")
    video_count = sum(1 for item in all_items if item["media_type"] == "video")
    ai_video_count = sum(1 for item in all_items if "ai_video" in item["media_source"])
    missing = [s for s in scenes if not s.get("media_items")]

    if missing:
        print(f"[image_fetch] UYARI: {len(missing)} sahne medyasiz kaldi (gun {day}).")

    print(
        f"[image_fetch] Gun {day}: {image_count} foto, {video_count} video klip "
        f"({ai_video_count} AI/cinematic arama sonucu), toplam {len(all_items)} medya kullanildi."
    )
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        process_day(int(sys.argv[1]))
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
