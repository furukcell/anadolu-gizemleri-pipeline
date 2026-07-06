"""
image_fetch.py
--------------
V7: AV PLAN tabanli VIDEO-ONLY medya toplama modulu.

Bu surum artik content/visual_plans degil, content/av_plans okur.
AV plan icindeki segments[] alanini kullanir:
- search_queries -> hangi MP4 videolar aranacak
- on_screen_text -> montajda ekrana gelecek yazilar
- music_mood / music_intensity / sfx -> montajda ses tasarimi

Kurallar:
- Foto yok.
- Wikimedia yok.
- Ken Burns yok.
- Sadece gercek MP4 video indirilir.
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

REQUEST_TIMEOUT = 25
RETRY_COUNT = 2
RETRY_SLEEP = 2
MIN_VIDEO_BYTES = 80_000
MIN_VIDEO_DURATION = 2.0
DEFAULT_PER_PAGE = 15

USER_AGENT = (
    "AnadoluGizemleriPipeline/3.0 "
    "(https://github.com/furukcell/anadolu-gizemleri-pipeline)"
)

DEFAULT_AVOID_TERMS = [
    "lycia", "lycian", "pinara", "ephesus", "efes", "roman ruins",
    "greek ruins", "pamukkale", "hierapolis", "perge", "side", "miletus",
    "troy", "troya", "truva", "hattusa", "ani", "modern tourists",
    "modern city", "cars", "logo", "watermark", "beach", "resort",
]


# =========================================================
# Yardimci fonksiyonlar
# =========================================================
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


# =========================================================
# AV plan okuma
# =========================================================
def find_av_plan(day: int) -> Path:
    plan_dir = config.CONTENT_DIR / "av_plans"
    candidates = sorted(plan_dir.glob(f"{day:02d}_*_av_plan.json"))
    if not candidates:
        raise FileNotFoundError(
            f"AV plan bulunamadi: {plan_dir}/{day:02d}_*_av_plan.json\n"
            "V7 AV PLAN modu bu dosya olmadan calismaz.\n"
            "Ornek: content/av_plans/01_gobeklitepe_karahantepe_av_plan.json"
        )
    return candidates[0]


def load_av_plan(day: int) -> dict:
    plan_path = find_av_plan(day)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segments = plan.get("segments") or plan.get("scenes") or []
    if not segments:
        raise ValueError(f"AV plan bos veya hatali: {plan_path} (segments[] yok)")

    global_rules = plan.get("global_rules") or {}
    visual_rules = global_rules.get("visual") or {}
    global_avoid = list(visual_rules.get("avoid") or []) + DEFAULT_AVOID_TERMS

    for i, seg in enumerate(segments):
        seg.setdefault("index", i)
        seg.setdefault("id", f"segment_{i:02d}")
        seg.setdefault("must_be_video", True)
        seg.setdefault("allow_photo", False)
        seg.setdefault("search_queries", [])
        seg.setdefault("fallback_queries", [])
        seg.setdefault("avoid", [])
        seg.setdefault("on_screen_text", [])
        seg.setdefault("music_mood", "ancient_mystery_drone")
        seg.setdefault("music_intensity", 0.45)
        seg.setdefault("sfx", [])

        start = parse_time_to_seconds(seg["start"])
        end = parse_time_to_seconds(seg["end"])
        if end <= start:
            raise ValueError(
                f"AV planda sahne suresi hatali: index={i}, {seg['start']}->{seg['end']}"
            )
        seg["_duration"] = round(end - start, 3)
        seg["_avoid"] = _unique(list(seg.get("avoid") or []) + global_avoid)

        # 0-14 sn -> 1 video, 15-27 sn -> 2 video, 28+ sn -> 3 video
        if "desired_media_count" not in seg:
            if seg["_duration"] >= 28:
                seg["desired_media_count"] = 3
            elif seg["_duration"] >= 15:
                seg["desired_media_count"] = 2
            else:
                seg["desired_media_count"] = 1

    plan["segments"] = segments
    plan["_plan_path"] = str(plan_path)

    print(f"[image_fetch] AV plan yuklendi -> {plan_path}")
    print("[image_fetch] V7 AV PLAN MOD AKTIF: av_plans + video-only + audio metadata")
    print("[image_fetch] Fotograf/Wikimedia/Ken Burns KAPALI.")
    print(
        f"[image_fetch] Plan segment sayisi: {len(segments)}, "
        f"hedef sure: {sum(s['_duration'] for s in segments):.1f}s"
    )
    return plan


# =========================================================
# Video arama / indirme
# =========================================================
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

    def score(vf: dict):
        w = vf.get("width") or 0
        h = vf.get("height") or 0
        file_type = vf.get("file_type") or ""
        is_mp4_penalty = 0 if "mp4" in file_type.lower() else 1000
        landscape_penalty = 0 if w >= h else 500
        width_penalty = abs((w or config.VIDEO_WIDTH) - config.VIDEO_WIDTH) / 10
        too_small_penalty = 300 if w and w < 1280 else 0
        return is_mp4_penalty + landscape_penalty + width_penalty + too_small_penalty

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
        "per_page": DEFAULT_PER_PAGE,
        "video_type": "film",
        "safesearch": "true",
    }
    resp = _get_with_retry(PIXABAY_VIDEO_API, params=params)
    if not resp:
        return []

    out = []
    for video in resp.json().get("hits", []):
        meta_text = " ".join([
            str(video.get("tags", "")),
            str(video.get("pageURL", "")),
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
                "meta_url": video.get("pageURL", ""),
            })
    return out


def download_video(item: dict, out_path: Path) -> bool:
    url = item["url"]
    resp = _get_with_retry(url, stream=True)
    if not resp:
        return False

    tmp_path = out_path.with_suffix(".download")
    with tmp_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    tmp_path.replace(out_path)
    if validate_video(out_path):
        return True

    out_path.unlink(missing_ok=True)
    return False


def enhanced_queries(base_queries: list[str]) -> list[str]:
    queries = []
    for q in base_queries:
        q = (q or "").strip()
        if not q:
            continue
        queries.append(q)
        # Pexels/Pixabay bazen cok spesifik sorguda bos donuyor; bu eklemeler
        # yine planin ruhunda kalip daha fazla gercek MP4 buldurur.
        if "cinematic" not in q.lower():
            queries.append(f"{q} cinematic documentary")
        if "stock footage" not in q.lower():
            queries.append(f"{q} stock footage")
    return _unique(queries)


def collect_segment_videos(segment: dict, media_dir: Path, used_urls: set[str]) -> list[dict]:
    idx = int(segment["index"])
    segment_id = segment.get("id") or f"segment_{idx:02d}"
    target_count = int(segment.get("desired_media_count", 2))
    avoid_terms = segment.get("_avoid", DEFAULT_AVOID_TERMS)
    queries = enhanced_queries(list(segment.get("search_queries") or []) + list(segment.get("fallback_queries") or []))

    if not queries:
        raise RuntimeError(f"Segment {idx} icin search_queries bos: {segment_id}")

    downloaded = []
    candidate_no = 0

    print(f"[image_fetch] Segment {idx:02d}: hedef {target_count} video -> {segment_id}")

    for query in queries:
        if len(downloaded) >= target_count:
            break

        candidates = []
        # Pexels once, Pixabay varsa yedek.
        candidates.extend(search_pexels_videos(query, avoid_terms))
        candidates.extend(search_pixabay_videos(query, avoid_terms))

        if not candidates:
            print(f"[image_fetch]   Bos sonuc -> {query}")
            continue

        for item in candidates:
            if len(downloaded) >= target_count:
                break
            if item["url"] in used_urls:
                continue
            used_urls.add(item["url"])

            file_name = f"segment_{idx:02d}_{candidate_no:02d}_{_slug(segment_id)}.mp4"
            out_path = media_dir / file_name
            candidate_no += 1

            print(f"[image_fetch]   indiriliyor: [{item['source']}] {query}")
            if not download_video(item, out_path):
                print("[image_fetch]   indirilen dosya gecersiz/kisa, atlandi")
                continue

            rel_path = out_path.relative_to(config.BASE_DIR).as_posix()
            duration = get_video_duration(out_path)
            downloaded.append({
                "media_type": "video",
                "media_path": rel_path,
                "media_source": item["source"],
                "media_query": query,
                "source_url": item.get("meta_url") or item.get("url"),
                "duration_seconds": round(duration, 3),
            })
            print(f"[image_fetch]   OK -> {out_path.name} ({duration:.1f}s)")

    if not downloaded:
        raise RuntimeError(
            f"Segment {idx} icin hic gercek MP4 video bulunamadi: {segment_id}\n"
            f"Sorgular: {queries[:6]}\n"
            "Kotu foto/slayt uretmemek icin pipeline durduruldu."
        )

    if len(downloaded) < target_count:
        print(
            f"[image_fetch] UYARI: Segment {idx} hedef {target_count} video idi, "
            f"{len(downloaded)} bulundu. Devam ediliyor."
        )

    return downloaded


# =========================================================
# Ana islem
# =========================================================
def process_day(day: int):
    plan = load_av_plan(day)
    segments = plan["segments"]

    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    media_dir = video_dir / "media"

    if media_dir.exists():
        shutil.rmtree(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    scenes = []
    used_urls: set[str] = set()

    for seg in segments:
        items = collect_segment_videos(seg, media_dir, used_urls)
        scenes.append({
            "index": int(seg["index"]),
            "id": seg.get("id"),
            "start": seg.get("start"),
            "end": seg.get("end"),
            "estimated_seconds": float(seg["_duration"]),
            "narrative": seg.get("narrative", ""),
            "visual_direction": seg.get("visual_direction", ""),
            "media_items": items,
            "on_screen_text": seg.get("on_screen_text", []),
            "music_mood": seg.get("music_mood", "ancient_mystery_drone"),
            "music_intensity": float(seg.get("music_intensity", 0.45)),
            "music_notes": seg.get("music_notes", ""),
            "sfx": seg.get("sfx", []),
        })

    manifest = {
        "day": day,
        "mode": "av_plan_video_only_v7",
        "av_plan_path": plan.get("_plan_path"),
        "title": plan.get("title", ""),
        "duration_target": plan.get("duration_target", ""),
        "global_rules": plan.get("global_rules", {}),
        "music_pool_suggestions": plan.get("music_pool_suggestions", {}),
        "scenes": scenes,
    }

    manifest_path = video_dir / "images_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total_videos = sum(len(s.get("media_items", [])) for s in scenes)
    total_duration = sum(float(s.get("estimated_seconds", 0)) for s in scenes)

    print(f"[image_fetch] Gun {day} tamamlandi -> {manifest_path}")
    print(
        f"[image_fetch] Gun {day}: 0 foto, {total_videos} GERCEK MP4 video klip, "
        f"plan sure {total_duration:.1f}s."
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        process_day(int(sys.argv[1]))
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
