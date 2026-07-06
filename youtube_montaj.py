"""
youtube_montaj.py
------------------
images_manifest.json icindeki planli VIDEO-ONLY veya eski media_items yapisini
okuyup nihai YouTube videosunu uretir.

V6 notu:
- media_items varsa her sahneyi kendi icinde 1+ klibe boler.
- image gelirse destekler ama VIDEO-ONLY planlarda image zaten uretilmez.
- Tum ara klipler ayni codec/fps/size ile uretilir; concat daha stabil olur.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import config


def get_media_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def run_ffmpeg(cmd: list, description: str):
    print(f"[youtube_montaj] {description}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-4000:])
        raise RuntimeError(f"ffmpeg basarisiz oldu: {description}")


def build_image_clip(image_path: Path, duration: float, out_path: Path):
    fps = config.VIDEO_FPS
    total_frames = max(int(duration * fps), 1)
    zoom_ratio = config.KEN_BURNS_ZOOM_RATIO if config.KEN_BURNS_ENABLED else 1.0
    zoom_expr = f"1+({zoom_ratio}-1)*on/{total_frames}"

    vf = (
        f"scale=8000:-1,"
        f"zoompan=z='{zoom_expr}':d={total_frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:fps={fps},"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-vf", vf,
        "-t", str(duration),
        "-r", str(fps),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"UYARI: image klip uretiliyor: {image_path.name} ({duration:.1f}s)")


def build_video_clip(video_path: Path, duration: float, out_path: Path):
    fps = config.VIDEO_FPS
    try:
        src_duration = get_media_duration(video_path)
    except Exception:
        src_duration = duration

    vf = (
        f"scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT},"
        f"fps={fps},format=yuv420p"
    )

    cmd = ["ffmpeg", "-y"]
    if src_duration < duration:
        loop_count = max(int(duration // max(src_duration, 0.1)) + 1, 1)
        cmd += ["-stream_loop", str(loop_count)]

    cmd += [
        "-i", str(video_path),
        "-vf", vf,
        "-t", str(duration),
        "-r", str(fps),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Video klip hazirlaniyor: {video_path.name} ({duration:.1f}s)")


def build_fallback_clip(duration: float, out_path: Path):
    fps = config.VIDEO_FPS
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:r={fps}",
        "-t", str(duration),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"UYARI: fallback siyah ekran ({duration:.1f}s)")


def _scene_media_items(scene: dict) -> list[dict]:
    items = scene.get("media_items") or []
    if items:
        return items

    # Eski manifest uyumlulugu
    if scene.get("media_path"):
        return [{
            "media_path": scene.get("media_path"),
            "media_type": scene.get("media_type"),
            "media_source": scene.get("media_source"),
            "media_query": scene.get("media_query"),
        }]
    return []


def montage_day(day: int):
    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    manifest_path = video_dir / "images_manifest.json"
    voiceover_path = video_dir / "voiceover.mp3"

    if not manifest_path.exists():
        raise FileNotFoundError(f"images_manifest.json bulunamadi: {manifest_path}")
    if not voiceover_path.exists():
        raise FileNotFoundError(f"voiceover.mp3 bulunamadi: {voiceover_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenes = manifest["scenes"]

    real_duration = get_media_duration(voiceover_path)
    print(f"[youtube_montaj] Gercek seslendirme suresi: {real_duration:.2f}s")

    estimated_total = sum(float(s.get("estimated_seconds", 0)) for s in scenes)
    if estimated_total <= 0:
        raise ValueError("Sahnelerin tahmini toplam suresi 0 - manifest hatali.")
    scale_factor = real_duration / estimated_total

    print(
        f"[youtube_montaj] Olcek faktoru: {scale_factor:.4f} "
        f"(plan/tahmin {estimated_total:.1f}s -> ses {real_duration:.1f}s)"
    )

    clips_dir = video_dir / "clips"
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True)

    clip_paths = []
    global_clip_index = 0

    for scene in scenes:
        idx = int(scene["index"])
        scene_duration = round(float(scene["estimated_seconds"]) * scale_factor, 3)
        scene_duration = max(scene_duration, 1.0 / config.VIDEO_FPS)
        items = _scene_media_items(scene)

        print(f"[youtube_montaj] Sahne {idx}: {len(items)} medya parcasi, sure {scene_duration:.1f}s")

        if not items:
            clip_out = clips_dir / f"clip_{global_clip_index:04d}_scene_{idx:03d}_fallback.mp4"
            build_fallback_clip(scene_duration, clip_out)
            clip_paths.append(clip_out)
            global_clip_index += 1
            continue

        item_duration = max(scene_duration / len(items), 1.0 / config.VIDEO_FPS)

        for item_no, item in enumerate(items):
            media_path = item.get("media_path")
            media_type = item.get("media_type")
            clip_out = clips_dir / f"clip_{global_clip_index:04d}_scene_{idx:03d}_{item_no:02d}.mp4"

            try:
                if not media_path:
                    build_fallback_clip(item_duration, clip_out)
                else:
                    full_media_path = config.BASE_DIR / media_path
                    if media_type == "image":
                        build_image_clip(full_media_path, item_duration, clip_out)
                    elif media_type == "video":
                        build_video_clip(full_media_path, item_duration, clip_out)
                    else:
                        build_fallback_clip(item_duration, clip_out)
            except Exception as e:
                print(f"[youtube_montaj] UYARI: klip patladi, fallback kullaniliyor: {e}")
                build_fallback_clip(item_duration, clip_out)

            clip_paths.append(clip_out)
            global_clip_index += 1

    concat_list_path = clips_dir / "concat_list.txt"
    with concat_list_path.open("w", encoding="utf-8") as f:
        for clip in clip_paths:
            f.write(f"file '{clip.resolve().as_posix()}'\n")

    silent_video_path = video_dir / "video_silent.mp4"
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-c", "copy",
        str(silent_video_path),
    ]
    run_ffmpeg(cmd_concat, "Tum klipler birlestiriliyor")

    final_video_path = video_dir / f"video_{day:02d}.mp4"
    bg_music_path = config.BACKGROUND_MUSIC_PATH

    if bg_music_path.exists():
        cmd_mux = [
            "ffmpeg", "-y",
            "-i", str(silent_video_path),
            "-i", str(voiceover_path),
            "-stream_loop", "-1", "-i", str(bg_music_path),
            "-filter_complex",
            f"[2:a]volume={config.BACKGROUND_MUSIC_VOLUME}[bg];"
            f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(final_video_path),
        ]
        run_ffmpeg(cmd_mux, "Seslendirme + arka muzik video ile birlestiriliyor")
    else:
        cmd_mux = [
            "ffmpeg", "-y",
            "-i", str(silent_video_path),
            "-i", str(voiceover_path),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(final_video_path),
        ]
        run_ffmpeg(cmd_mux, "Seslendirme video ile birlestiriliyor (arka muzik yok)")

    shutil.rmtree(clips_dir, ignore_errors=True)
    silent_video_path.unlink(missing_ok=True)

    print(f"[youtube_montaj] Gun {day} tamamlandi -> {final_video_path}")
    return final_video_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        montage_day(int(sys.argv[1]))
    else:
        print("Kullanim: python youtube_montaj.py <gun_numarasi>")
        print("Ornek: python youtube_montaj.py 1")
