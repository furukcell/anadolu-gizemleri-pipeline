"""
image_fetch.py
---------------
HIBRIT MOD: her sahne icin ya FOTO (Wikimedia/Pexels) ya da VIDEO KLIP
(Pexels Videos) bulunur. Karar mantigi:

  1) Once Wikimedia Commons'ta sahneye ozgu GERCEK/TARIHI foto aranir
     (yer adi, obje, kalinti gecen spesifik sahneler icin uygundur).
  2) Bulunamazsa (genelde soyut/atmosferik sahnelerde olur - "gizem",
     "bilinmeyen", "sis" gibi) Pexels VIDEO API'sinden kisa klip aranir.
  3) O da bulunamazsa Pexels FOTO'ya dusulur.
  4) O da bulunamazsa jenerik fallback sorgularla foto/video sirayla
     denenir. Hicbir sahne medyasiz kalmaz.

Boylece: spesifik sahneler gercek tarihi fotoğrafla, atmosferik/genel
sahneler ise hareketli video kliple desteklenir.

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
from urllib.parse import quote

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

FALLBACK_QUERIES = [
    "ancient Anatolia ruins",
    "Anatolian archaeological site",
    "Turkey ancient civilization",
]

FALLBACK_VIDEO_QUERIES = [
    "ancient ruins fog",
    "mysterious ancient temple",
    "torch flame night",
]

REQUEST_TIMEOUT = 15
RETRY_COUNT = 2
RETRY_SLEEP = 2

# Cok genel / TTS'e sizmis sahne notu kaliplarini gorsel sorgusuna
# katmadan once temizlemek icin
NOISE_WORDS = {
    "ekrana", "gelir", "gorunur", "goruntusu", "sahne", "kamera",
    "yakin", "cekim", "gecis", "efekt",
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


def build_query_from_scene_note(scene_note: str, day_title: str) -> str:
    """
    Sahne notundan (or: '[Ekrana Gobeklitepe'nin T-sekilli dikilitaslari gelir]')
    kisa ve oz bir Ingilizce/ascii arama sorgusu uretir.
    """
    if not scene_note:
        return _turkish_to_ascii(day_title)

    note = scene_note.lower()
    note = _turkish_to_ascii(note)

    # Gurultu kelimelerini at
    words = re.findall(r"[a-z0-9]+", note)
    words = [w for w in words if w not in NOISE_WORDS and len(w) > 2]

    # En fazla 6 kelimeyle sinirla, cok uzun sorgu API'lerde kotu sonuc verir
    query = " ".join(words[:6]).strip()

    if not query:
        query = _turkish_to_ascii(day_title)

    return query


def build_video_query_from_scene_note(scene_note: str) -> str:
    """
    Video aramalari icin JENERIK/atmosferik bir sorgu uretir. Spesifik yer
    adlari (Gobeklitepe, Catalhoyuk vb.) Pexels video'da hemen hiç sonuç
    vermedigi icin, sahne notundaki ATMOSFER kelimelerini (sis, gece, ates,
    tas, kalinti, karanlik, gizem...) yakalayip onlarla kisa bir sorgu
    kurar. Hicbir atmosfer kelimesi bulunamazsa genel bir "ancient ruins
    mist" sorgusuna duser.
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
    }

    if not scene_note:
        return "ancient ruins mist"

    note_ascii = _turkish_to_ascii(scene_note.lower())
    hits = [eng for tr, eng in atmosphere_map.items() if tr in note_ascii]

    if hits:
        return " ".join(hits[:2])
    return "ancient ruins mist"


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
    resp = _get_with_retry(WIKIMEDIA_API, params=search_params)
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
        info_resp = _get_with_retry(WIKIMEDIA_API, params=info_params)
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

    # Genislige gore 1920'ye en yakin ama en fazla onu asan dosyayi tercih et
    def _width_score(vf):
        w = vf.get("width") or 0
        return abs(w - config.VIDEO_WIDTH)

    best = sorted(video_files, key=_width_score)[0]
    return best.get("link")


# =========================================================
# INDIRME
# =========================================================
def download_image(url: str, dest_path: Path) -> bool:
    resp = _get_with_retry(url)
    if not resp:
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    return True


# =========================================================
# ANA MANTIK: bir segment icin HIBRIT medya bul (foto ya da video)
# =========================================================
def fetch_media_for_segment(query: str, video_query: str, dest_dir: Path, base_name: str):
    """
    Hibrit karar zinciri:
      1) Wikimedia'da GERCEK foto ara (spesifik sahneler burada bulunur)
      2) Bulunamazsa Pexels VIDEO ara (atmosferik/genel sahneler icin)
      3) Bulunamazsa Pexels FOTO ara
      4) Hicbiri olmazsa fallback foto/video sorgulariyla sirayla dene

    Doner: (media_type, media_source, used_query, dest_path) ya da
           (None, None, query, None) basarisizlik durumunda.
    """
    # 1) Wikimedia - gercek/tarihi foto
    url = search_wikimedia(query)
    if url:
        dest_path = dest_dir / f"{base_name}.jpg"
        if download_image(url, dest_path):
            return "image", "wikimedia", query, dest_path

    # 2) Pexels video - atmosferik/genel sahne
    url = search_pexels_video(video_query)
    if url:
        dest_path = dest_dir / f"{base_name}.mp4"
        if download_image(url, dest_path):
            return "video", "pexels_video", video_query, dest_path

    # 3) Pexels foto
    url = search_pexels_photo(query)
    if url:
        dest_path = dest_dir / f"{base_name}.jpg"
        if download_image(url, dest_path):
            return "image", "pexels_photo", query, dest_path

    # 4) Fallback - once foto sorgulariyla wikimedia+pexels foto, sonra video
    for fallback_query in FALLBACK_QUERIES:
        url = search_wikimedia(fallback_query)
        if url:
            dest_path = dest_dir / f"{base_name}.jpg"
            if download_image(url, dest_path):
                return "image", "fallback-wikimedia", fallback_query, dest_path

        url = search_pexels_photo(fallback_query)
        if url:
            dest_path = dest_dir / f"{base_name}.jpg"
            if download_image(url, dest_path):
                return "image", "fallback-pexels_photo", fallback_query, dest_path

    for fallback_video_query in FALLBACK_VIDEO_QUERIES:
        url = search_pexels_video(fallback_video_query)
        if url:
            dest_path = dest_dir / f"{base_name}.mp4"
            if download_image(url, dest_path):
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
        base_name = f"scene_{idx:02d}"

        media_type, source, used_query, dest_path = fetch_media_for_segment(
            photo_query, video_query, media_dir, base_name
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
    print(f"[image_fetch] Gun {day}: {image_count} foto, {video_count} video klip kullanildi.")

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        gun = int(sys.argv[1])
        process_day(gun)
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
      
