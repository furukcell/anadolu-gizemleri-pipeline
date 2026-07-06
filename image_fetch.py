"""
image_fetch.py
--------------
V6: Plan tabanli VIDEO-ONLY medya toplama modulu.

Bu surum rasgele foto/stock aramaz. Once content/visual_plans/NN_..._visual_plan.json
dosyasini okur ve her sahne icin sadece GERCEK mp4 video klip indirir.

Kurallar:
- Foto yok.
- Wikimedia yok.
- Ken Burns'e gidecek image item yok.
- Sadece Pexels/Pixabay video API kaynaklari kullanilir.
- Her sahne icin plan dosyasindaki search_queries kullanilir.
- Video bulunamazsa pipeline durur; kotu/slayt video uretmez.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Iterable

import requests

import config

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"

REQUEST_TIMEOUT = 20
RETRY_COUNT = 2
RETRY_SLEEP = 2
MIN_VIDEO_BYTES = 80_000
MIN_VIDEO_DURATION = 2.0
DEFAULT_PER_PAGE = 12

VIDEO_ONLY_MODE = True
ALLOW_PHOTOS = False

USER_AGENT = (
    "AnadoluGizemleriPipeline/2.0 "
    "(https://github.com/furukcell/anadolu-gizemleri-pipeline)"
)


def _turkish_to_ascii(text: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = (text or "").translate(tr_map)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def _normalized(text: str) -> str:
    return _turkish_to_ascii(text or "").lower()


def _slug(text: str, max_len: int = 60) -> str:
    norm = _normalized(text)
    norm = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
    return norm[:max_len].strip("_") or "scene"


def _unique(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        item = (item or "").strip()
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def parse_time_to_seconds(value: str | int | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if ":" not in value:
        return float(value)
    parts = [float(p) for p in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Gecersiz zaman formati: {value}")


def _get_with_retry(url: str, params=None, headers=None, stream: bool = False):
    last_err = None
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                stream=stream,
            )
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_SLEEP)

    print(f"[image_fetch] Istek basarisiz: {url} -> {last_err}")
    return None


def find_visual_plan(day: int) -> Path:
    plan_dir = config.CONTENT_DIR / "visual_plans"
    candidates = sorted(plan_dir.glob(f"{day:02d}_*_visual_plan.json"))
    if not candidates:
        raise FileNotFoundError(
            f"Gorsel plan bulunamadi: {plan_dir}/{day:02d}_*_visual_plan.json\n"
            "V6 VIDEO-ONLY mod plan dosyasi olmadan calismaz."
        )
    return candidates[0]


def load_visual_plan(day: int) -> dict:
    plan_path = find_visual_plan(day)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan.get("scenes", [])
    if not scenes:
        raise ValueError(f"Gorsel plan bos veya hatali: {plan_path}")

    for i, scene in enumerate(scenes):
        scene.setdefault("index", i)
        scene.setdefault("desired_media_count", 2)
        scene.setdefault("allow_photo", False)
        scene.setdefault("must_be_video", True)
        scene.setdefault("search_queries", [])
        scene.setdefault("fallback_queries", [])
        scene.setdefault("avoid", [])

        start = parse_time_to_seconds(scene["start"])
        end = parse_time_to_seconds(scene["end"])
        if end <= start:
            raise ValueError(f"Gorsel planda sahne suresi hatali: index={i}, {scene['start']}->{scene['end']}")
        scene["_duration"] = round(end - start, 3)

    print(f"[image_fetch] Gorsel plan yuklendi -> {plan_path}")
    print("[image_fetch] VIDEO-ONLY MOD AKTIF: fotograf/Wikimedia/Ken Burns yok.")
    print(f"[image_fetch] Plan sahne sayisi: {len(scenes)}, hedef sure: {sum(s['_duration'] for s in scenes):.1f}s")
    return plan


def get_video_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])
    return float(result.stdout.strip())


def validate_video(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size < MIN_VIDEO_BYTES:
            return False
        duration = get_video_duration(path)
        return duration >= MIN_VIDEO_DURATION
    except Exception:
        return False


def _offtopic(text: str, avoid_terms: list[str]) -> bool:
    norm = _normalized(text)
    for bad in avoid_terms:
        if _normalized(bad) in norm:
            return True
    return False


def _best_pexels_video_link(video: dict) -> str | None:
    files = video.get("video_files", [])
    if not files:
        return None

    # Oncelik: yatay, genisligi 1280+ ve 1920'ye yakin MP4.
    def score(vf: dict):
        w = vf.get("width") or 0
        h = vf.get("height") or 0
        file_type = vf.get("file_type") or ""
        is_mp4 = 0 if "mp4" in file_type.lower() else 1000
        landscape_penalty = 0 if w >= h else 500
        width_penalty = abs((w or config.VIDEO_WIDTH) - config.VIDEO_WIDTH) / 10
        too_small_penalty = 300 if w and w < 1280 else 0
        return is_mp4 + landscape_penalty + width_penalty + too_small_penalty

    best = sorted(files, key=score)[0]
    return best.get("link")


def search_pexels_videos(query: str, avoid_terms: list[str]) -> list[dict]:
    if not config.PEXELS_API_KEY:
        print("[image_fetch] PEXELS_API_KEY tanimli degil, Pexels video atlaniyor.")
        return []

    headers = {"Authorization": config.PEXELS_API_KEY, "User-Agent": USER_AGENT}
    params = {
        "query": query,
        "per_page": DEFAULT_PER_PAGE,
        "orientation": "landscape",
    }
    resp = _get_with_retry(PEXELS_VIDEO_API, params=params, headers=headers)
    if not resp:
        return []

    out = []
    for video in resp.json().get("videos", []):
        meta_text = " ".join([
            str(video.get("url", "")),
            str(video.get("user", {}).get("name", "")),
            query,
        ])
        if _offtopic(meta_text, avoid_terms):
            continue
        link = _best_pexels_video_link(video)
        if link:
            out.append({
                "url": link,
                "source": "pexels_video",
                "query": query,
                "meta_url": video.get("url", ""),
            })
    return out


def _best_pixabay_video_link(video: dict) -> str | None:
    videos = video.get("videos", {})
    # large yoksa medium, o da yoksa small.
    for key in ("large", "medium", "small", "tiny"):
        item = videos.get(key) or {}
        url = item.get("url")
        if url:
            return url
    return None


def search_pixabay_videos(query: str, avoid_terms: list[str]) -> list[dict]:
    api_key = os.environ.get("PIXABAY_API_KEY", "")
    if not api_key:
        return []

    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "safesearch": "true",
        "per_page": DEFAULT_PER_PAGE,
        "min_width": 1280,
    }
    resp = _get_with_retry(PIXABAY_VIDEO_API, params=params)
    if not resp:
        return []

    out = []
    for video in resp.json().get("hits", []):
        meta_text = " ".join([
            str(video.get("tags", "")),
            str(video.get("user", "")),
            query,
        ])
        if _offtopic(meta_text, avoid_terms):
            continue
        link = _best_pixabay_video_link(video)
        if link:
            out.append({
                "url": link,
                "source": "pixabay_video",
                "query": query,
                "meta_url": f"https://pixabay.com/videos/id-{video.get('id')}/",
            })
    return out


def iter_video_candidates(query: str, avoid_terms: list[str]):
    # Once Pexels; Pixabay key varsa ikinci kaynak olarak dener.
    for item in search_pexels_videos(query, avoid_terms):
        yield item
    for item in search_pixabay_videos(query, avoid_terms):
        yield item


def download_video(url: str, dest_path: Path) -> bool:
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)

    resp = _get_with_retry(url, stream=True)
    if not resp:
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with tmp_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                f.write(chunk)
                # Cok dev dosyalar workflow'u bogmasin; 250 MB ustunu kes.
                if total > 250 * 1024 * 1024:
                    print(f"[image_fetch] Video cok buyuk, atlandi: {url}")
                    tmp_path.unlink(missing_ok=True)
                    return False

        if total < MIN_VIDEO_BYTES:
            tmp_path.unlink(missing_ok=True)
            return False

        tmp_path.replace(dest_path)
        if not validate_video(dest_path):
            dest_path.unlink(missing_ok=True)
            return False
        return True
    except Exception as e:
        print(f"[image_fetch] Video indirme/validasyon hatasi: {e}")
        tmp_path.unlink(missing_ok=True)
        dest_path.unlink(missing_ok=True)
        return False


def fetch_videos_for_scene(scene: dict, media_dir: Path, used_urls: set[str], global_avoid: list[str]) -> list[dict]:
    scene_index = int(scene["index"])
    desired_count = int(scene.get("desired_media_count", 2))
    label = scene.get("label") or f"sahne_{scene_index}"
    avoid_terms = list(global_avoid) + list(scene.get("avoid", []))

    queries = _unique(scene.get("search_queries", []) + scene.get("fallback_queries", []))
    if not queries:
        raise ValueError(f"Sahne {scene_index} icin search_queries yok: {label}")

    items = []
    query_offset = 0

    while len(items) < desired_count and query_offset < len(queries):
        query = queries[query_offset]
        query_offset += 1

        for candidate in iter_video_candidates(query, avoid_terms):
            url = candidate["url"]
            if url in used_urls:
                continue

            base_name = f"scene_{scene_index:02d}_{len(items):02d}_{_slug(label, 32)}"
            dest_path = media_dir / f"{base_name}.mp4"

            if download_video(url, dest_path):
                used_urls.add(url)
                item = {
                    "media_path": str(dest_path.relative_to(config.BASE_DIR)),
                    "media_type": "video",
                    "media_source": candidate["source"],
                    "media_query": candidate["query"],
                    "source_url": candidate.get("meta_url") or url,
                    "direct_url": url,
                }
                items.append(item)
                print(
                    f"[image_fetch] Sahne {scene_index}.{len(items)-1}: "
                    f"[video] {item['media_source']} -> {item['media_query']}"
                )
                break

        # Bir query'den birden fazla klip de kullanabilmek icin tekrar sona ekle ama
        # ilk turda farkli query'ler denensin.
        if len(items) < desired_count and query_offset >= len(queries):
            # Ikinci tur: ayni query'lerden baska candidate cikarsa dene.
            break

    # Ikinci pas: ilk pas yetmezse tum query'leri tekrar tara.
    if len(items) < desired_count:
        for query in queries:
            if len(items) >= desired_count:
                break
            for candidate in iter_video_candidates(query, avoid_terms):
                url = candidate["url"]
                if url in used_urls:
                    continue
                base_name = f"scene_{scene_index:02d}_{len(items):02d}_{_slug(label, 32)}"
                dest_path = media_dir / f"{base_name}.mp4"
                if download_video(url, dest_path):
                    used_urls.add(url)
                    item = {
                        "media_path": str(dest_path.relative_to(config.BASE_DIR)),
                        "media_type": "video",
                        "media_source": candidate["source"],
                        "media_query": candidate["query"],
                        "source_url": candidate.get("meta_url") or url,
                        "direct_url": url,
                    }
                    items.append(item)
                    print(
                        f"[image_fetch] Sahne {scene_index}.{len(items)-1}: "
                        f"[video] {item['media_source']} -> {item['media_query']}"
                    )
                    break

    if not items:
        raise RuntimeError(
            f"Sahne {scene_index} icin hic GERCEK mp4 video bulunamadi: {label}\n"
            f"Denenen sorgular: {queries[:5]}"
        )

    return items


def process_day(day: int, save_json: bool = True) -> dict:
    plan = load_visual_plan(day)
    scenes_plan = plan["scenes"]

    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    media_dir = video_dir / "media"

    if media_dir.exists():
        shutil.rmtree(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    global_avoid = plan.get("global_rules", {}).get("avoid_global", [])
    used_urls: set[str] = set()
    scenes = []

    for idx, scene in enumerate(scenes_plan):
        scene["index"] = idx
        items = fetch_videos_for_scene(scene, media_dir, used_urls, global_avoid)
        first = items[0]

        scenes.append({
            "index": idx,
            "scene_type": "planned_video",
            "scene_note": scene.get("label", ""),
            "visual_description": scene.get("description", ""),
            "start": scene.get("start"),
            "end": scene.get("end"),
            "estimated_seconds": round(float(scene["_duration"]), 3),
            "word_count": 0,
            "narration": "",
            "must_be_video": True,
            "allow_photo": False,
            "media_items": items,
            "media_path": first.get("media_path"),
            "media_type": "video",
            "media_source": first.get("media_source"),
            "media_query": first.get("media_query"),
            "overlay_text": scene.get("overlay_text"),
            "overlay_text_sequence": scene.get("overlay_text_sequence"),
            "search_queries": scene.get("search_queries", []),
            "fallback_queries": scene.get("fallback_queries", []),
        })

    result = {
        "day": day,
        "title": plan.get("title", f"Gun {day}"),
        "visual_plan": True,
        "video_only": True,
        "allow_photos": False,
        "target_duration_seconds": plan.get("target_duration_seconds"),
        "scenes": scenes,
    }

    if save_json:
        manifest_path = video_dir / "images_manifest.json"
        manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[image_fetch] Gun {day} tamamlandi -> {manifest_path}")

    all_items = [item for scene in scenes for item in scene.get("media_items", [])]
    video_count = sum(1 for item in all_items if item.get("media_type") == "video")
    image_count = sum(1 for item in all_items if item.get("media_type") == "image")

    print(f"[image_fetch] Gun {day}: {image_count} foto, {video_count} GERCEK MP4 video klip, toplam {len(all_items)} medya.")
    if image_count != 0:
        raise RuntimeError("VIDEO-ONLY modda foto uretilmemeliydi; manifest hatali.")
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        process_day(int(sys.argv[1]))
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
