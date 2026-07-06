"""
youtube_montaj.py
------------------
V7: AV PLAN destekli montaj modulu.

images_manifest.json icindeki AV plan metadata'sini kullanir:
- media_items: sadece gercek MP4 video klipleri
- on_screen_text: zamanli ekran yazilari
- music_mood / music_intensity: sahne bazli arka fon
- sfx: sahne bazli efekt zamanlari

Eger assets/audio/music ve assets/audio/sfx icinde gercek dosya yoksa bile,
ffmpeg ile otomatik dusuk seviyeli ambient fon ve temel efektler uretir. Boylece
videoda anlatim altinda hep bir atmosfer sesi olur.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import config

AUDIO_SAMPLE_RATE = 44100
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]


def parse_time_to_seconds(value: str | int | float | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value:
        return 0.0
    if ":" not in value:
        return float(value)
    parts = [float(p) for p in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Gecersiz zaman formati: {value}")


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
        print(result.stderr[-5000:])
        raise RuntimeError(f"ffmpeg basarisiz oldu: {description}")


def _font_file() -> str:
    for font in FONT_CANDIDATES:
        if Path(font).exists():
            return font
    return FONT_CANDIDATES[0]


# =========================================================
# Video klip hazirlama
# =========================================================
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
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
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
    if scene.get("media_path"):
        return [{
            "media_path": scene.get("media_path"),
            "media_type": scene.get("media_type"),
            "media_source": scene.get("media_source"),
            "media_query": scene.get("media_query"),
        }]
    return []


# =========================================================
# Ekran yazilari
# =========================================================
def _style_to_drawtext(style: str) -> dict:
    style = (style or "").lower()
    base = {
        "fontsize": 68,
        "fontcolor": "white",
        "borderw": 4,
        "bordercolor": "black",
        "x": "(w-text_w)/2",
        "y": "(h-text_h)/2",
    }
    if "small" in style:
        base["fontsize"] = 54
        base["y"] = "h*0.78"
    if "place" in style:
        base["fontsize"] = 74
        base["y"] = "h*0.16"
    if "large" in style:
        base["fontsize"] = 82
    if "single_word" in style:
        base["fontsize"] = 96
    if "soft" in style:
        base["fontsize"] = 60
        base["y"] = "h*0.70"
    return base


def _drawtext_filter(text_file: Path, start: float, end: float, style: str) -> str:
    st = _style_to_drawtext(style)
    fontfile = _font_file()
    # Not: textfile kullanmak Turkce karakter/quote kacis problemlerini azaltir.
    return (
        "drawtext="
        f"fontfile='{fontfile}':"
        f"textfile='{text_file.as_posix()}':"
        f"fontsize={st['fontsize']}:"
        f"fontcolor={st['fontcolor']}:"
        f"borderw={st['borderw']}:"
        f"bordercolor={st['bordercolor']}:"
        f"x={st['x']}:"
        f"y={st['y']}:"
        f"enable='between(t,{start:.3f},{end:.3f})'"
    )


def apply_text_overlays(video_in: Path, scenes: list[dict], scale_factor: float, work_dir: Path) -> Path:
    text_entries = []
    for scene in scenes:
        scene_start = parse_time_to_seconds(scene.get("start"))
        scene_end = parse_time_to_seconds(scene.get("end"))
        for item in scene.get("on_screen_text") or []:
            raw_start = item.get("start")
            raw_end = item.get("end")
            start = parse_time_to_seconds(raw_start) if raw_start is not None else scene_start + 1
            end = parse_time_to_seconds(raw_end) if raw_end is not None else min(scene_end, start + 4)
            if end <= start:
                continue
            text_entries.append({
                "text": str(item.get("text", "")).strip(),
                "start": start * scale_factor,
                "end": end * scale_factor,
                "style": item.get("style", "center"),
            })

    text_entries = [e for e in text_entries if e["text"]]
    if not text_entries:
        return video_in

    text_dir = work_dir / "textfiles"
    text_dir.mkdir(parents=True, exist_ok=True)

    filters = []
    for i, entry in enumerate(text_entries):
        text_file = text_dir / f"text_{i:03d}.txt"
        text_file.write_text(entry["text"], encoding="utf-8")
        filters.append(_drawtext_filter(text_file, entry["start"], entry["end"], entry["style"]))

    out_path = work_dir / "video_texted.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_in),
        "-vf", ",".join(filters),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Ekran yazilari ekleniyor ({len(text_entries)} adet)")
    return out_path


# =========================================================
# Ses tasarimi: muzik bed + SFX
# =========================================================
def _find_audio_asset(folder: Path, name: str) -> Path | None:
    safe = str(name or "").strip()
    if not safe:
        return None
    candidates = [
        folder / safe,
        folder / f"{safe}.mp3",
        folder / f"{safe}.wav",
        folder / f"{safe}.m4a",
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 1000:
            return path
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def build_music_segment(mood: str, intensity: float, duration: float, out_path: Path):
    music_dir = config.ASSETS_DIR / "audio" / "music"
    asset = _find_audio_asset(music_dir, mood)
    intensity = _clamp(float(intensity or 0.45), 0.05, 1.0)
    volume = round(0.045 + intensity * 0.11, 3)
    fade_out_start = max(duration - 1.2, 0.0)

    if asset:
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(asset),
            "-t", str(duration),
            "-af",
            f"volume={volume},afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.3f}:d=1.0,"
            f"aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo",
            "-c:a", "pcm_s16le",
            str(out_path),
        ]
        run_ffmpeg(cmd, f"Muzik segmenti hazirlaniyor: {mood} ({duration:.1f}s)")
        return

    # Fallback: assets yoksa otomatik atmosfer uret.
    # Bu profesyonel muzik yerine gecmez ama sessiz/slayt hissini kirar.
    mood_l = (mood or "").lower()
    color = "brown"
    lowpass = 420
    if "dark" in mood_l or "burial" in mood_l or "karahantepe" in mood_l:
        color = "brown"
        lowpass = 330
    elif "pulse" in mood_l or "tribal" in mood_l:
        color = "pink"
        lowpass = 520
    elif "final" in mood_l:
        color = "pink"
        lowpass = 300

    amp = round(0.08 + intensity * 0.08, 3)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anoisesrc=color={color}:amplitude={amp}:duration={duration}:sample_rate={AUDIO_SAMPLE_RATE}",
        "-af",
        f"lowpass=f={lowpass},highpass=f=30,volume={volume},"
        f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.3f}:d=1.0,"
        f"aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Sentetik ambient muzik uretiliyor: {mood} ({duration:.1f}s)")


def build_music_bed(scenes: list[dict], scale_factor: float, work_dir: Path) -> Path:
    music_dir = work_dir / "music_segments"
    music_dir.mkdir(parents=True, exist_ok=True)

    segment_paths = []
    for scene in scenes:
        idx = int(scene.get("index", len(segment_paths)))
        duration = max(float(scene.get("estimated_seconds", 0)) * scale_factor, 0.2)
        mood = scene.get("music_mood", "ancient_mystery_drone")
        intensity = float(scene.get("music_intensity", 0.45))
        out_path = music_dir / f"music_{idx:03d}.wav"
        build_music_segment(mood, intensity, duration, out_path)
        segment_paths.append(out_path)

    list_path = music_dir / "music_concat.txt"
    with list_path.open("w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    bed_path = work_dir / "music_bed.wav"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c:a", "pcm_s16le",
        str(bed_path),
    ]
    run_ffmpeg(cmd, "Sahne bazli muzik bed birlestiriliyor")
    return bed_path


def _sfx_duration(name: str) -> float:
    n = (name or "").lower()
    if "wind" in n:
        return 4.0
    if "torch" in n or "fire" in n:
        return 4.5
    if "soil" in n:
        return 3.0
    if "rumble" in n:
        return 2.6
    if "whoosh" in n:
        return 1.0
    if "cave" in n or "reverb" in n:
        return 3.0
    return 1.1


def build_sfx_clip(name: str, volume: float, out_path: Path):
    sfx_dir = config.ASSETS_DIR / "audio" / "sfx"
    asset = _find_audio_asset(sfx_dir, name)
    dur = _sfx_duration(name)
    vol = _clamp(float(volume or 0.12), 0.01, 0.8)

    if asset:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(asset),
            "-t", str(dur),
            "-af",
            f"volume={vol},afade=t=out:st={max(dur-0.4,0):.3f}:d=0.4,"
            f"aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo",
            "-c:a", "pcm_s16le",
            str(out_path),
        ]
        run_ffmpeg(cmd, f"SFX asset hazirlaniyor: {name}")
        return dur

    n = (name or "").lower()
    if "boom" in n or "hit" in n:
        src = f"sine=frequency=55:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}"
        af = f"volume={vol},afade=t=out:st=0.05:d={max(dur-0.05,0.1)},aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo"
    elif "whoosh" in n:
        src = f"anoisesrc=color=pink:amplitude=0.25:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}"
        af = f"highpass=f=350,lowpass=f=2500,volume={vol},afade=t=in:st=0:d=0.2,afade=t=out:st={max(dur-0.35,0):.3f}:d=0.35,aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo"
    elif "fire" in n or "torch" in n:
        src = f"anoisesrc=color=brown:amplitude=0.20:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}"
        af = f"highpass=f=500,lowpass=f=4200,volume={vol},afade=t=in:st=0:d=0.2,afade=t=out:st={max(dur-0.4,0):.3f}:d=0.4,aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo"
    elif "wind" in n:
        src = f"anoisesrc=color=pink:amplitude=0.18:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}"
        af = f"lowpass=f=1100,highpass=f=80,volume={vol},afade=t=in:st=0:d=0.4,afade=t=out:st={max(dur-0.5,0):.3f}:d=0.5,aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo"
    else:
        src = f"anoisesrc=color=brown:amplitude=0.22:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}"
        af = f"lowpass=f=900,highpass=f=70,volume={vol},afade=t=out:st={max(dur-0.4,0):.3f}:d=0.4,aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", src,
        "-af", af,
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Sentetik SFX uretiliyor: {name}")
    return dur


def build_sfx_mix(scenes: list[dict], scale_factor: float, real_duration: float, work_dir: Path) -> Path | None:
    entries = []
    for scene in scenes:
        for item in scene.get("sfx") or []:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            at = parse_time_to_seconds(item.get("at")) * scale_factor
            if at >= real_duration:
                continue
            entries.append({
                "name": name,
                "at": max(at, 0.0),
                "volume": float(item.get("volume", 0.12)),
            })

    if not entries:
        return None

    sfx_dir = work_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)

    clip_paths = []
    for i, entry in enumerate(entries):
        clip_path = sfx_dir / f"sfx_{i:03d}_{entry['name']}.wav"
        build_sfx_clip(entry["name"], entry["volume"], clip_path)
        clip_paths.append((clip_path, int(entry["at"] * 1000)))

    # Tek komutta silence + delayed efektleri mixle.
    inputs = [
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}:duration={real_duration}"
    ]
    for clip, _ in clip_paths:
        inputs += ["-i", str(clip)]

    filter_parts = []
    labels = ["[0:a]"]
    for i, (_, delay_ms) in enumerate(clip_paths, start=1):
        label = f"[d{i}]"
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms},apad,atrim=0:{real_duration:.3f}{label}")
        labels.append(label)

    filter_parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0[aout]")
    out_path = work_dir / "sfx_mix.wav"
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    run_ffmpeg(cmd, f"SFX mix uretiliyor ({len(entries)} efekt)")
    return out_path


# =========================================================
# Ana montaj
# =========================================================
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
        f"(AV plan {estimated_total:.1f}s -> ses {real_duration:.1f}s)"
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

        print(f"[youtube_montaj] Segment {idx}: {len(items)} video parcasi, sure {scene_duration:.1f}s")

        if not items:
            # Normalde image_fetch video yoksa zaten durur. Bu sadece emniyet.
            clip_out = clips_dir / f"clip_{global_clip_index:04d}_segment_{idx:03d}_fallback.mp4"
            build_fallback_clip(scene_duration, clip_out)
            clip_paths.append(clip_out)
            global_clip_index += 1
            continue

        item_duration = max(scene_duration / len(items), 1.0 / config.VIDEO_FPS)

        for item_no, item in enumerate(items):
            media_path = item.get("media_path")
            media_type = item.get("media_type")
            clip_out = clips_dir / f"clip_{global_clip_index:04d}_segment_{idx:03d}_{item_no:02d}.mp4"

            try:
                if not media_path:
                    build_fallback_clip(item_duration, clip_out)
                else:
                    full_media_path = config.BASE_DIR / media_path
                    if media_type != "video":
                        raise RuntimeError(f"V7 video-only modda video disi medya geldi: {media_type}")
                    build_video_clip(full_media_path, item_duration, clip_out)
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
    run_ffmpeg(cmd_concat, "Tum video klipler birlestiriliyor")

    # Ekran yazilari
    video_with_text_path = apply_text_overlays(silent_video_path, scenes, scale_factor, clips_dir)

    # Ses tasarimi
    music_bed_path = build_music_bed(scenes, scale_factor, clips_dir)
    sfx_mix_path = build_sfx_mix(scenes, scale_factor, real_duration, clips_dir)

    final_video_path = video_dir / f"video_{day:02d}.mp4"

    inputs = ["-i", str(video_with_text_path), "-i", str(voiceover_path), "-i", str(music_bed_path)]
    filter_inputs = "[1:a][2:a]"
    input_count = 2

    if sfx_mix_path and sfx_mix_path.exists():
        inputs += ["-i", str(sfx_mix_path)]
        filter_inputs += "[3:a]"
        input_count = 3

    filter_complex = (
        f"{filter_inputs}amix=inputs={input_count}:duration=first:dropout_transition=2[aout]"
    )

    cmd_mux = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(final_video_path),
    ]
    run_ffmpeg(cmd_mux, "Seslendirme + sahne muzikleri + SFX video ile birlestiriliyor")

    # Debug icin cok yer kaplamasin.
    shutil.rmtree(clips_dir, ignore_errors=True)
    silent_video_path.unlink(missing_ok=True)
    if video_with_text_path != silent_video_path:
        video_with_text_path.unlink(missing_ok=True)

    print(f"[youtube_montaj] Gun {day} tamamlandi -> {final_video_path}")
    print("[youtube_montaj] AV PLAN SES TASARIMI: muzik bed + sfx aktif")
    return final_video_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        montage_day(int(sys.argv[1]))
    else:
        print("Kullanim: python youtube_montaj.py <gun_numarasi>")
        print("Ornek: python youtube_montaj.py 1")
