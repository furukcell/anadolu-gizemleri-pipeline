"""
image_fetch.py
--------------
V8: video_discovery.py tarafindan bulunan konuya özel aday havuzundan
sadece lisanslı/indirilebilir gerçek video seçer.

Bu dosya artık segment segment genel stok arama yapmaz.
Önce output/video_NN/video_candidates.json dosyasına bakar.
Konuya yakın otomatik indirilebilir aday yoksa pipeline durur.

Kurallar:
- Foto yok.
- Wikimedia foto yok.
- Ken Burns yok.
- Pexels/Pixabay genel fallback yok.
- Alakasız stok video yok.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

import config

REQUEST_TIMEOUT = 30
RETRY_COUNT = 2
RETRY_SLEEP = 2
MIN_VIDEO_BYTES = 80_000
MIN_VIDEO_DURATION = 2.0

USER_AGENT = (
    "AnadoluGizemleriImageFetchV8/1.0 "
    "(https://github.com/furukcell/anadolu-gizemleri-pipeline)"
)


def _turkish_to_ascii(text: str) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = (text or "").translate(tr_map)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def _norm(text: str) -> str:
    return _turkish_to_ascii(text or "").lower()


def _slug(text: str, max_len: int = 64) -> str:
    norm = _norm(text)
    norm = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
    return norm[:max_len].strip("_") or "video"


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


def _get_with_retry(url: str, headers=None, stream: bool = False):
    last_err = None
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = str(e)

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_SLEEP)

    print(f"[image_fetch] Indirme istegi basarisiz: {url} -> {last_err}")
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
            "V8 mod bu dosya olmadan calismaz."
        )
    return candidates[0]


def load_av_plan(day: int) -> dict:
    plan_path = find_av_plan(day)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError(f"AV plan bos veya hatali: {plan_path} (segments[] yok)")

    for i, seg in enumerate(segments):
        seg.setdefault("index", i)
        seg.setdefault("id", f"segment_{i:02d}")
        seg.setdefault("must_be_video", True)
        seg.setdefault("allow_photo", False)
        seg.setdefault("on_screen_text", [])
        seg.setdefault("music_mood", "ancient_mystery_drone")
        seg.setdefault("music_intensity", 0.45)
        seg.setdefault("sfx", [])

        start = parse_time_to_seconds(seg["start"])
        end = parse_time_to_seconds(seg["end"])
        if end <= start:
            raise ValueError(f"AV planda sahne suresi hatali: index={i}, {seg['start']}->{seg['end']}")
        seg["_duration"] = round(end - start, 3)

        # V8'de her segmentte 1 konu videosu yeterli. Aynı dosya ffmpeg ile süreye göre kırpılır/looplanır.
        # Çok fazla aday varsa desired_media_count verilebilir.
        seg.setdefault("desired_media_count", 1)

    plan["segments"] = segments
    plan["_plan_path"] = str(plan_path)

    print(f"[image_fetch] AV plan yuklendi -> {plan_path}")
    print("[image_fetch] V8 TOPIC-CANDIDATE MOD AKTIF: genel stok arama kapali.")
    print("[image_fetch] Fotograf/Wikimedia foto/Ken Burns KAPALI.")
    return plan


# =========================================================
# Candidate havuzu
# =========================================================
def load_candidates(day: int) -> list[dict]:
    path = config.OUTPUT_DIR / f"video_{day:02d}" / "video_candidates.json"
    if not path.exists():
        raise FileNotFoundError(
            f"video_candidates.json bulunamadi: {path}\n"
            "Once video_discovery.py calismali."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates = data.get("auto_downloadable_candidates") or []
    candidates = [
        c for c in candidates
        if c.get("download_url") and c.get("usable_for_auto_download") and c.get("accepted")
    ]
    candidates = sorted(candidates, key=lambda c: c.get("topic_score", 0), reverse=True)

    if not candidates:
        raise RuntimeError(
            f"Konuya yakin otomatik indirilebilir aday yok: {path}\n"
            "Alakasiz stok video kullanmamak icin pipeline durduruldu."
        )

    print(f"[image_fetch] Konu aday havuzu yuklendi: {len(candidates)} otomatik indirilebilir video")
    for c in candidates[:8]:
        print(f"[image_fetch]   aday score={c.get('topic_score')} source={c.get('source')} title={str(c.get('title'))[:80]}")
    return candidates


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


def _extension_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".mp4", ".webm", ".ogv", ".ogg", ".mov"):
        if path.endswith(ext):
            return ext
    return ".mp4"


def download_candidate(candidate: dict, media_dir: Path, cache: dict[str, dict]) -> dict | None:
    url = candidate["download_url"]
    if url in cache:
        return cache[url]

    source = candidate.get("source", "source")
    title_slug = _slug(candidate.get("title") or candidate.get("source_url") or source)
    ext = _extension_from_url(url)
    file_name = f"candidate_{len(cache):03d}_{source}_{title_slug}{ext}"
    out_path = media_dir / file_name

    print(f"[image_fetch] indiriliyor: [{source}] score={candidate.get('topic_score')} {candidate.get('title')}")
    resp = _get_with_retry(url, stream=True)
    if not resp:
        print("[image_fetch]   indirilemedi")
        return None

    tmp_path = out_path.with_suffix(out_path.suffix + ".download")
    with tmp_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    tmp_path.replace(out_path)

    if not validate_video(out_path):
        print("[image_fetch]   indirilen dosya gecersiz/kisa, atlandi")
        out_path.unlink(missing_ok=True)
        return None

    duration = get_video_duration(out_path)
    rel_path = out_path.relative_to(config.BASE_DIR).as_posix()
    media_item = {
        "media_type": "video",
        "media_path": rel_path,
        "media_source": candidate.get("source"),
        "media_query": candidate.get("query", ""),
        "source_url": candidate.get("source_url"),
        "download_url": candidate.get("download_url"),
        "license": candidate.get("license", ""),
        "creator": candidate.get("creator", ""),
        "topic_score": candidate.get("topic_score", 0),
        "duration_seconds": round(duration, 3),
    }
    cache[url] = media_item
    print(f"[image_fetch]   OK -> {out_path.name} ({duration:.1f}s)")
    return media_item


def _segment_text(segment: dict) -> str:
    return _norm(" ".join([
        segment.get("id", ""),
        segment.get("narrative", ""),
        segment.get("visual_direction", ""),
        " ".join(segment.get("search_queries") or []),
    ]))


def _candidate_text(candidate: dict) -> str:
    return _norm(" ".join([
        candidate.get("title", ""),
        candidate.get("description", ""),
        candidate.get("source_url", ""),
        candidate.get("license", ""),
    ]))


def score_for_segment(candidate: dict, segment: dict, used_count: int) -> float:
    score = float(candidate.get("topic_score", 0))
    st = _segment_text(segment)
    ct = _candidate_text(candidate)

    # Karahantepe bolumunde Karahantepe adayi varsa one al.
    if "karahantepe" in st and "karahantepe" in ct:
        score += 8
    if ("gobekli" in st or "gobeklitepe" in st) and ("gobekli" in ct or "gobeklitepe" in ct):
        score += 8

    # Rekonstruksiyon/animation adaylari genelde belgesel hissine daha uygun.
    for term in ("reconstruction", "3d", "animation", "animasyon", "cgi"):
        if term in ct:
            score += 2

    # Ayni videoyu cok tekrarlamamak icin ceza; ama baska konu videosu yoksa yine kullanilir.
    score -= used_count * 1.5
    return score


def choose_candidates_for_segment(segment: dict, candidates: list[dict], used_counts: dict[str, int]) -> list[dict]:
    target_count = int(segment.get("desired_media_count", 1))
    ranked = sorted(
        candidates,
        key=lambda c: score_for_segment(c, segment, used_counts.get(c.get("download_url", ""), 0)),
        reverse=True,
    )

    chosen = []
    seen = set()
    for c in ranked:
        url = c.get("download_url")
        if not url or url in seen:
            continue
        chosen.append(c)
        seen.add(url)
        if len(chosen) >= target_count:
            break

    if not chosen:
        raise RuntimeError(f"Segment icin secilebilir konu videosu yok: {segment.get('id')}")
    return chosen


# =========================================================
# Ana islem
# =========================================================
def process_day(day: int):
    plan = load_av_plan(day)
    segments = plan["segments"]
    candidates = load_candidates(day)

    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    media_dir = video_dir / "media"

    if media_dir.exists():
        shutil.rmtree(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    scenes = []
    download_cache: dict[str, dict] = {}
    used_counts: dict[str, int] = {}

    for seg in segments:
        idx = int(seg["index"])
        chosen = choose_candidates_for_segment(seg, candidates, used_counts)
        items = []

        print(f"[image_fetch] Segment {idx:02d}: {seg.get('id')} -> {len(chosen)} konu adayi secildi")

        for cand in chosen:
            media_item = download_candidate(cand, media_dir, download_cache)
            if not media_item:
                continue
            items.append(media_item)
            url = cand.get("download_url", "")
            used_counts[url] = used_counts.get(url, 0) + 1

        if not items:
            raise RuntimeError(
                f"Segment {idx} icin secilen adaylar indirilemedi: {seg.get('id')}\n"
                "Alakasiz stok fallback yok; pipeline durduruldu."
            )

        scenes.append({
            "index": idx,
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
        "mode": "topic_candidate_pool_v8",
        "av_plan_path": plan.get("_plan_path"),
        "title": plan.get("title", ""),
        "duration_target": plan.get("duration_target", ""),
        "global_rules": plan.get("global_rules", {}),
        "candidate_pool_summary": {
            "candidate_count": len(candidates),
            "downloaded_unique_video_count": len(download_cache),
        },
        "scenes": scenes,
    }

    manifest_path = video_dir / "images_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total_videos = sum(len(s.get("media_items", [])) for s in scenes)
    total_duration = sum(float(s.get("estimated_seconds", 0)) for s in scenes)

    print(f"[image_fetch] Gun {day} tamamlandi -> {manifest_path}")
    print(
        f"[image_fetch] Gun {day}: 0 foto, {total_videos} segment video atamasi, "
        f"{len(download_cache)} benzersiz konu videosu, plan sure {total_duration:.1f}s."
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        process_day(int(sys.argv[1]))
    else:
        print("Kullanim: python image_fetch.py <gun_numarasi>")
        print("Ornek: python image_fetch.py 1")
