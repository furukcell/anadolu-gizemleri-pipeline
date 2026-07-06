"""
image_fetch.py
---------------
HIBRIT MOD: her sahne icin ya FOTO (Wikimedia/Pexels) ya da VIDEO KLIP
(Pexels Videos) bulunur. Karar mantigi:

  1) Once Wikimedia Commons'ta sahneye ozgu GERCEK/TARIHI foto aranir.
     GitHub Actions icinde 403 yememek icin Wikimedia isteklerine User-Agent eklenir.
  2) Bulunamazsa Pexels FOTO aranir. Tarih belgeseli icin gercek/fotografik
     gorsel, alakasiz stok videodan daha guvenli oldugu icin video oncesine alindi.
  3) Bulunamazsa Pexels VIDEO API'sinde "AI generated / cinematic" tarzinda
     atmosferik video aranir. Bu adim sifirdan video URETMEZ; AI/cinematic stok
     video BULMAYA calisir.
  4) O da bulunamazsa normal atmosferik Pexels video aranir.
  5) Hicbiri olmazsa jenerik fallback sorgularla foto/video sirayla denenir.

Boylece: spesifik sahneler gercek tarihi fotografla, fotograf bulunamayan
atmosferik/genel sahneler ise once AI/cinematic stok video, sonra normal stok
video ile desteklenir.

Cikti: output/video_NN/media/scene_XX.jpg|mp4 + images_manifest.json

Not: estimated_seconds burada kelime sayisina gore KABA bir tahmindir.
youtube_montaj.py gercek voiceover suresine gore bunu yeniden olcekler.
Video klipler de o sureye gore trim/loop edilecek.
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

import config

# =========================================================
# AYARLAR
# =========================================================
# Ortalama konusma hizi: Google TTS speaking_rate=0.97 icin
# tr-TR Wavenet sesler ~2.4 kelime/saniye civarindadir (kaba tahmin).
WORDS_PER_SECOND = 2.4 * config.GOOGLE_TTS_SPEAKING_RATE

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"

WIKIMEDIA_HEADERS = {
    "User-Agent": config.WIKIMEDIA_USER_AGENT,
    "Accept": "application/json",
}

FALLBACK_QUERIES = [
    "ancient Anatolia ruins",
    "Anatolian archaeological site",
    "Turkey ancient civilization",
    "neolithic archaeological site",
]

FALLBACK_AI_VIDEO_QUERIES = [
    "ai generated cinematic ancient ruins",
    "ai generated archaeological excavation",
    "cinematic ancient temple mysterious",
]

FALLBACK_VIDEO_QUERIES = [
    "ancient ruins fog",
    "mysterious ancient temple",
    "torch flame night",
    "archaeological excavation",
]

REQUEST_TIMEOUT = 15
RETRY_COUNT = 2
RETRY_SLEEP = 2

# Cok genel / TTS'e sizmis sahne notu kaliplarini gorsel sorgusuna
# katmadan once temizlemek icin
NOISE_WORDS = {
    "ekrana", "gelir", "gorunur", "goruntusu", "sahne", "kamera",
    "yakin", "cekim", "gecis", "efekt", "yavasca", "hafif", "baslar",
    "eski", "bir", "ve", "ile", "olan", "gelen", "derinden",
}

# Anadolu Gizemleri bolumlerinde gecen spesifik yerleri API'lerin daha iyi
# anlayacagi Ingilizce/ascii sorgulara cevirir.
KNOWN_SITE_QUERY_MAP = {
    "gobeklitepe": "gobekli tepe archaeological site stone pillars",
    "gobekli tepe": "gobekli tepe archaeological site stone pillars",
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
    "atalhoyuk": "catalhoyuk neolithic settlement archaeology",
}

TURKISH_TERM_MAP = {
    "siyah": "dark",
    "ekran": "screen",
    "ruzgar": "wind",
    "golge": "shadow",
    "tas": "stone",
    "sutun": "pillar",
    "sutunlar": "pillars",
    "dikilitas": "standing stone",
    "dikilitaslar": "standing stones",
    "kalinti": "ruins",
    "kalintilar": "ruins",
    "kazi": "excavation",
    "alan": "site",
    "tapinak": "temple",
    "oda": "chamber",
    "odalar": "chambers",
    "karanlik": "dark",
    "sis": "mist",
    "ates": "fire",
    "mesale": "torch",
    "hayvan": "animal",
    "kabartma": "relief carving",
    "kabartmalari": "relief carvings",
    "bas": "head",
    "insan": "human",
    "figür": "figure",
    "figur": "figure",
    "toprak": "earth",
    "arkeolojik": "archaeological",
    "havadan": "aerial view",
    "animasyon": "cinematic reconstruction",
}


# =========================================================
# YARDIMCI FONKSIYONLAR
# =========================================================
def _turkish_to_ascii(text: str) -> str:
    """Turkce karakterleri arama sorgusu icin sadelestirir (API'ler ascii'de daha iyi calisir)."""
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = text.translate(tr_map)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text


def _normalized(text: str) -> str:
    return _turkish_to_ascii(text or "").lower()


def _known_site_query(text: str) -> str | None:
    text_norm = _normalized(text)
    for key, query in KNOWN_SITE_QUERY_MAP.items():
        if key in text_norm:
            return query
    return None


def _map_visual_words(words):
    mapped = []
    for w in words:
        mapped_term = TURKISH_TERM_MAP.get(w, w)
        if mapped_term not in mapped:
            mapped.append(mapped_term)
    return mapped


def build_query_from_scene_note(scene_note: str, day_title: str) -> str:
    """
    Sahne notundan kisa ve oz bir Ingilizce/ascii arama sorgusu uretir.
    Spesifik Anadolu alanlari icin known-site map kullanir; boylece
    "siyah ekran / ruzgar" gibi genel sahnelerde bile bolumun asil mekanina
    ait daha dogru fotograf aramasi yapilir.
    """
    combined = f"{day_title or ''} {scene_note or ''}"
    known_query = _known_site_query(combined)

    if not scene_note:
        return known_query or _turkish_to_ascii(day_title)

    note = _normalized(scene_note)

    # Gurultu kelimelerini at
    words = re.findall(r"[a-z0-9]+", note)
    words = [w for w in words if w not in NOISE_WORDS and len(w) > 2]
    mapped_words = _map_visual_words(words)

    # Known-site varsa ana mekan sorgusunu one al; sahneye ozgu 2-3 kelime ekle.
    if known_query:
        visual_tail = " ".join(mapped_words[:3]).strip()
        if visual_tail:
            return f"{known_query} {visual_tail}"
        return known_query

    # En fazla 7 kelimeyle sinirla, cok uzun sorgu API'lerde kotu sonuc verir
    query = " ".join(mapped_words[:7]).strip()

    if not query:
        query = _turkish_to_ascii(day_title)

    return query


def build_video_query_from_scene_note(scene_note: str) -> str:
    """
    Normal video aramalari icin JENERIK/atmosferik bir sorgu uretir.
    Spesifik yer adlari Pexels video'da hemen hiç sonuc vermedigi icin,
    sahne notundaki atmosfer kelimelerini yakalayip kisa bir sorgu kurar.
    """
    atmosphere_map = {
        "sis": "fog mist",
        "gece": "night",
        "ates": "fire torch",
        "mesale": "torch flame",
        "tas": "stone ruins",
        "kalinti": "ancient ruins",
        "karanlik": "dark mysterious",
        "gizem": "mysterious ancient",
        "golge": "shadow silhouette",
        "ay": "moonlight night",
        "ruzgar": "windy landscape",
        "toprak": "ancient earth excavation",
        "kazi": "archaeological excavation",
        "tapinak": "ancient temple",
        "mezar": "ancient tomb",
        "yildiz": "starry sky",
        "sutun": "stone pillars",
        "kabartma": "ancient carving",
        "animasyon": "cinematic reconstruction",
    }

    if not scene_note:
        return "ancient ruins mist"

    note_ascii = _normalized(scene_note)
    hits = [eng for tr, eng in atmosphere_map.items() if tr in note_ascii]

    if hits:
        return " ".join(hits[:2])
    return "ancient ruins mist"


def build_ai_video_query_from_scene_note(scene_note: str, day_title: str) -> str:
    """
    AI/cinematic stok video aramasi icin sorgu uretir.
    Bu fonksiyon video URETMEZ; Pexels gibi stok kaynaklarda
    "AI generated / cinematic" anahtar kelimeleriyle arama yapar.
    """
    known_query = _known_site_query(f"{day_title or ''} {scene_note or ''}")
    atmosphere_query = build_video_query_from_scene_note(scene_note)

    if known_query:
        # Pexels'te spesifik mekan videosu zor bulundugu icin mekan adini degil,
        # o mekanin turunu ve atmosferi one cikariyoruz.
        if "gobekli" in known_query or "karahantepe" in known_query:
            base = "neolithic stone temple archaeological reconstruction"
        elif "hattusa" in known_query:
            base = "ancient hittite city ruins reconstruction"
        elif "troy" in known_query:
            base = "ancient city ruins cinematic reconstruction"
        else:
            base = "ancient anatolia ruins cinematic reconstruction"
        return f"ai generated {base} {atmosphere_query}"

    return f"ai generated cinematic {atmosphere_query}"


def estimate_seconds(word_count: int) -> float:
    if word_count <= 0:
        return config.MIN_SCENE_SECONDS
    seconds = word_count / WORDS_PER_SECOND
    return max(config.MIN_SCENE_SECONDS, min(seconds, config.MAX_SCENE_SECONDS))


def _get_with_retry(url, params=None, headers=None):
    last_err = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        time.sleep(RETRY_SLEEP)
    print(f"[image_fetch] Istek basarisiz ({url}): {last_err}")
    return None


# =========================================================
# WIKIMEDIA COMMONS
# =========================================================
def search_wikimedia(query: str):
    """Wikimedia Commons'ta gorsel arar, ilk uygun sonucun URL'sini doner."""
    search_params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": f"{query} filetype:bitmap",
        "srnamespace": 6,  # File namespace
        "srlimit": 5,
    }
    resp = _get_with_retry(WIKIMEDIA_API, params=search_params, headers=WIKIMEDIA_HEADERS)
    if not resp:
        return None

    results = resp.json().get("query", {}).get("search", [])
    if not results:
        return None

    for result in results:
        title = result.get("title")
        if not title:
            continue

        info_params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|size",
            "iiurlwidth": config.VIDEO_WIDTH,
        }
        info_resp = _get_with_retry(WIKIMEDIA_API, params=info_params, headers=WIKIMEDIA_HEADERS)
        if not info_resp:
            continue

        pages = info_resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            imageinfo = page.get("imageinfo")
            if imageinfo:
                url = imageinfo[0].get("thumburl") or imageinfo[0].get("url")
                if url:
                    return url

    return None


# =========================================================
# PEXELS
# =========================================================
def search_pexels_photo(query: str):
    """Pexels FOTO API'sinde arar, ilk sonucun buyuk boyutlu URL'sini doner."""
    if not config.PEXELS_API_KEY:
        print("[image_fetch] PEXELS_API_KEY tanimli degil, Pexels atlaniyor.")
        return None

    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {"query": query, "per_page": 5, "orientation": "landscape"}
    resp = _get_with_retry(PEXELS_PHOTO_API, params=params, headers=headers)
    if not resp:
        return None

    photos = resp.json().get("photos", [])
    if not photos:
        return None

    photo = photos[0]
    src = photo.get("src", {})
    return src.get("large2x") or src.get("large") or src.get("original")


def search_pexels_video(query: str):
    """
    Pexels VIDEO API'sinde arar. 1920x1080'e en yakin, asiri uzun olmayan
    (ideal: 5-25 sn) bir video dosyasi secip URL'sini doner.
    """
    if not config.PEXELS_API_KEY:
        print("[image_fetch] PEXELS_API_KEY tanimli degil, Pexels video atlaniyor.")
        return None

    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {"query": query, "per_page": 5, "orientation": "landscape"}
    resp = _get_with_retry(PEXELS_VIDEO_API, params=params, headers=headers)
    if not resp:
        return None

    videos = resp.json().get("videos", [])
    if not videos:
        return None

    # Ilk videonun dosya varyantlari icinden HD (1920 genislik civari) sec
    video = videos[0]
    video_files = video.get("video_files", [])
    if not video_files:
        return None

    # Genislige gore 1920'ye en yakin dosyayi tercih et
    def _width_score(vf):
        w = vf.get("width") or 0
        return abs(w - config.VIDEO_WIDTH)

    best = sorted(video_files, key=_width_score)[0]
    return best.get("link")


def search_pexels_ai_video(query: str):
    """
    AI/cinematic stok video arar. Pexels uzerinden yapildigi icin
    bulunan videonun gercekten AI ile uretilmis oldugunu garanti etmez;
    ama "ai generated / cinematic / reconstruction" etiketli sonuclari
    yakalamayi hedefler.
    """
    if not config.AI_VIDEO_ENABLED:
        return None
    return search_pexels_video(query)


# =========================================================
# INDIRME
# =========================================================
def download_media(url: str, dest_path: Path) -> bool:
    resp = _get_with_retry(url)
    if not resp:
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    return True


# Geriye uyumluluk icin eski isim kalsin
download_image = download_media


# =========================================================
# ANA MANTIK: bir segment icin HIBRIT medya bul (foto ya da video)
# =========================================================
def fetch_media_for_segment(
    query: str,
    video_query: str,
    ai_video_query: str,
    dest_dir: Path,
    base_name: str,
):
    """
    Hibrit karar zinciri:
      1) Wikimedia'da GERCEK foto ara
      2) Bulunamazsa Pexels FOTO ara
      3) Bulunamazsa Pexels'te AI/cinematic stok VIDEO ara
      4) Bulunamazsa normal Pexels VIDEO ara
      5) Hicbiri olmazsa fallback foto/video sorgulariyla sirayla dene

    Doner: (media_type, media_source, used_query, dest_path) ya da
           (None, None, query, None) basarisizlik durumunda.
    """
    # 1) Wikimedia - gercek/tarihi foto
    url = search_wikimedia(query)
    if url:
        dest_path = dest_dir / f"{base_name}.jpg"
        if download_media(url, dest_path):
            return "image", "wikimedia", query, dest_path

    # 2) Pexels foto - video oncesi; tarih belgeselinde alakasiz stok videodan daha guvenli
    url = search_pexels_photo(query)
    if url:
        dest_path = dest_dir / f"{base_name}.jpg"
        if download_media(url, dest_path):
            return "image", "pexels_photo", query, dest_path

    # 3) AI/cinematic stok video
    url = search_pexels_ai_video(ai_video_query)
    if url:
        dest_path = dest_dir / f"{base_name}.mp4"
        if download_media(url, dest_path):
            return "video", "pexels_ai_video", ai_video_query, dest_path

    # 4) Normal Pexels video - atmosferik/genel sahne
    url = search_pexels_video(video_query)
    if url:
        dest_path = dest_dir / f"{base_name}.mp4"
        if download_media(url, dest_path):
            return "video", "pexels_video", video_query, dest_path

    # 5) Fallback - once foto sorgulariyla wikimedia+pexels foto
    for fallback_query in FALLBACK_QUERIES:
        url = search_wikimedia(fallback_query)
        if url:
            dest_path = dest_dir / f"{base_name}.jpg"
            if download_media(url, dest_path):
                return "image", "fallback-wikimedia", fallback_query, dest_path

        url = search_pexels_photo(fallback_query)
        if url:
            dest_path = dest_dir / f"{base_name}.jpg"
            if download_media(url, dest_path):
                return "image", "fallback-pexels_photo", fallback_query, dest_path

    # Sonra AI/cinematic video fallbackleri
    if config.AI_VIDEO_ENABLED:
        for fallback_ai_query in FALLBACK_AI_VIDEO_QUERIES:
            url = search_pexels_ai_video(fallback_ai_query)
            if url:
                dest_path = dest_dir / f"{base_name}.mp4"
                if download_media(url, dest_path):
                    return "video", "fallback-pexels_ai_video", fallback_ai_query, dest_path

    # En son normal video fallbackleri
    for fallback_video_query in FALLBACK_VIDEO_QUERIES:
        url = search_pexels_video(fallback_video_query)
        if url:
            dest_path = dest_dir / f"{base_name}.mp4"
            if download_media(url, dest_path):
                return "video", "fallback-pexels_video", fallback_video_query, dest_path

    return None, None, query, None


# =========================================================
# GUN ISLEME
# =========================================================
def process_day(day: int, save_json: bool = True) -> dict:
    parsed_path = config.OUTPUT_DIR / f"video_{day:02d}" / "script_parsed.json"
    if not parsed_path.exists():
        raise FileNotFoundError(
            f"script_parsed.json bulunamadi: {parsed_path}. "
            f"Once script_parse.py calistirilmali."
        )

    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    day_title = parsed["title"]
    segments = parsed["segments"]

    media_dir = config.OUTPUT_DIR / f"video_{day:02d}" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    scenes = []
    for idx, seg in enumerate(segments):
        scene_note = seg.get("scene_note")
        narration = seg.get("narration", "")
        word_count = len(narration.split())

        photo_query = build_query_from_scene_note(scene_note, day_title)
        video_query = build_video_query_from_scene_note(scene_note)
        ai_video_query = build_ai_video_query_from_scene_note(scene_note, day_title)
        base_name = f"scene_{idx:02d}"

        media_type, source, used_query, dest_path = fetch_media_for_segment(
            photo_query, video_query, ai_video_query, media_dir, base_name
        )

        if source is None:
            print(f"[image_fetch] Gun {day} sahne {idx}: MEDYA BULUNAMADI ({photo_query})")
            media_rel_path = None
        else:
            media_rel_path = str(dest_path.relative_to(config.BASE_DIR))
            print(f"[image_fetch] Gun {day} sahne {idx}: [{media_type}] {source} -> {used_query}")

        scenes.append({
            "index": idx,
            "scene_note": scene_note,
            "narration": narration,
            "word_count": word_count,
            "estimated_seconds": round(estimate_seconds(word_count), 2),
            "media_path": media_rel_path,
            "media_type": media_type or "none",
            "media_source": source or "none",
            "media_query": used_query,
            "photo_query": photo_query,
            "video_query": video_query,
            "ai_video_query": ai_video_query,
        })

    result = {"day": day, "title": day_title, "scenes": scenes}

    if save_json:
        manifest_path = config.OUTPUT_DIR / f"video_{day:02d}" / "images_manifest.json"
        manifest_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[image_fetch] Gun {day} tamamlandi -> {manifest_path}")

    missing = [s for s in scenes if s["media_path"] is None]
    if missing:
        print(f"[image_fetch] UYARI: {len(missing)} sahne medyasiz kaldi (gun {day}).")

    video_count = sum(1 for s in scenes if s["media_type"] == "video")
    image_count = sum(1 for s in scenes if s["media_type"] == "image")
    ai_video_count = sum(1 for s in scenes if "ai_video" in s["media_source"])
    print(
        f"[image_fetch] Gun {day}: {image_count} foto, {video_count} video klip "
        f"({ai_video_count} AI/cinematic arama sonucu) kullanildi."
    )

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        gun = int(sys.argv[1])
        process_day(gun)
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
