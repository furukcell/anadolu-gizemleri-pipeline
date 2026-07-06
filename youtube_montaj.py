"""
youtube_montaj.py
------------------
images_manifest.json ve voiceover.mp3'u kullanarak nihai belgesel videosunu uretir.

Yeni surum media_items listesini destekler. Boylece bir sahne tek fotografla
gecmek zorunda kalmaz; sahne suresi 2-3 medya parcasina bolunerek daha
dinamik, belgesel gibi akan bir kurgu uretilir.
"""

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
        print(result.stderr[-3000:])
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
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Ken Burns klip uretiliyor: {image_path.name} ({duration:.1f}s)")


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

    if src_duration < duration:
        loop_count = int(duration // max(src_duration, 0.1)) + 1
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", str(loop_count),
            "-i", str(video_path),
            "-vf", vf,
            "-t", str(duration),
            "-r", str(fps),
            "-an",
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", vf,
            "-t", str(duration),
            "-r", str(fps),
            "-an",
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
        str(out_path),
    ]
    run_ffmpeg(cmd, f"UYARI: fallback siyah ekran klibi uretiliyor ({duration:.1f}s)")


def media_items_for_scene(scene: dict):
    items = scene.get("media_items") or []
    items = [i for i in items if i.get("media_path")]
    if items:
        return items

    # Eski manifest ile geriye uyumluluk
    media_path = scene.get("media_path")
    media_type = scene.get("media_type")
    if media_path:
        return [{"media_path": media_path, "media_type": media_type or "image", "media_source": scene.get("media_source", "legacy")}]
    return []


def build_clip_for_item(item: dict, duration: float, out_path: Path):
    media_path = item.get("media_path")
    media_type = item.get("media_type")
    if not media_path:
        build_fallback_clip(duration, out_path)
        return

    full_media_path = config.BASE_DIR / media_path
    if not full_media_path.exists():
        print(f"[youtube_montaj] UYARI: medya dosyasi yok -> {full_media_path}")
        build_fallback_clip(duration, out_path)
        return

    try:
        if media_type == "image":
            build_image_clip(full_media_path, duration, out_path)
        elif media_type == "video":
            build_video_clip(full_media_path, duration, out_path)
        else:
            build_fallback_clip(duration, out_path)
    except Exception as e:
        # Tek bir bozuk stok video/foto tum pipeline'i patlatmasin.
        print(f"[youtube_montaj] UYARI: medya klibi bozuk olabilir, fallback kullaniliyor: {e}")
        build_fallback_clip(duration, out_path)


def split_scene_duration(total_duration: float, item_count: int):
    if item_count <= 1:
        return [total_duration]
    base = total_duration / item_count
    durations = [round(base, 3) for _ in range(item_count)]
    # Yuvarlama farkini son klibe ekle
    diff = round(total_duration - sum(durations), 3)
    durations[-1] = max(durations[-1] + diff, 1.0 / config.VIDEO_FPS)
    return durations


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

    estimated_total = sum(s["estimated_seconds"] for s in scenes)
    if estimated_total <= 0:
        raise ValueError("Sahnelerin tahmini toplam suresi 0 - manifest hatali olabilir.")
    scale_factor = real_duration / estimated_total
    print(f"[youtube_montaj] Olcek faktoru: {scale_factor:.4f} (tahmini toplam {estimated_total:.1f}s -> gercek {real_duration:.1f}s)")

    clips_dir = video_dir / "clips"
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True)

    clip_paths = []
    clip_counter = 0

    for scene in scenes:
        idx = scene["index"]
        final_duration = round(scene["estimated_seconds"] * scale_factor, 3)
        final_duration = max(final_duration, 1.0 / config.VIDEO_FPS)
        items = media_items_for_scene(scene)

        if not items:
            clip_out = clips_dir / f"clip_{clip_counter:04d}.mp4"
            build_fallback_clip(final_duration, clip_out)
            clip_paths.append(clip_out)
            clip_counter += 1
            continue

        durations = split_scene_duration(final_duration, len(items))
        print(f"[youtube_montaj] Sahne {idx}: {len(items)} medya parcasi, toplam {final_duration:.1f}s")

        for item_idx, (item, item_duration) in enumerate(zip(items, durations)):
            clip_out = clips_dir / f"clip_{clip_counter:04d}_s{idx:02d}_{item_idx:02d}.mp4"
            build_clip_for_item(item, item_duration, clip_out)
            clip_paths.append(clip_out)
            clip_counter += 1

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
    run_ffmpeg(cmd_concat, "Tum sahne klipleri birlestiriliyor")

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
