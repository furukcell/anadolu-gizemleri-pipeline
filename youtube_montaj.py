"""
youtube_montaj.py
------------------
image_fetch.py'nin urettigi images_manifest.json ve google_tts_generate.py
(veya voice_postprocess.py) tarafindan uretilen voiceover.mp3'u kullanarak
nihai belgesel videosunu (video_XX.mp4) uretir.

Adimlar:
  1) voiceover.mp3'un GERCEK suresini ffprobe ile olcer
  2) images_manifest.json'daki tahmini sahne surelerinin toplamina gore
     bir OLCEK FAKTORU hesaplar -> her sahne gercek seslendirmeye
     orantili sekilde ekranda kalir
  3) Her sahne icin ayri bir klip uretir:
       - media_type == "image" -> zoompan (Ken Burns) filtresi
       - media_type == "video" -> gerekli sureye trim/loop + scale/crop
  4) Tum klipleri ffmpeg concat demuxer ile birlestirir
  5) voiceover.mp3'u video uzerine bindirir
  6) Varsa background_music.mp3'u dusuk sesle (config.BACKGROUND_MUSIC_VOLUME)
     altina ekler
  7) output/video_NN/video_NN.mp4 olarak kaydeder

Kullanim:
  python youtube_montaj.py <gun_numarasi>
"""

import json
import shutil
import subprocess
from pathlib import Path

import config


# =========================================================
# YARDIMCI: ffprobe ile sure olcme
# =========================================================
def get_media_duration(path: Path) -> float:
    """ffprobe ile bir medya dosyasinin suresini saniye olarak doner."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def run_ffmpeg(cmd: list, description: str):
    """ffmpeg komutunu calistirir, hata olursa aciklayici mesaj basar."""
    print(f"[youtube_montaj] {description}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise RuntimeError(f"ffmpeg basarisiz oldu: {description}")


# =========================================================
# SAHNE KLIPLERI URETME
# =========================================================
def build_image_clip(image_path: Path, duration: float, out_path: Path):
    """
    Statik fotoyu Ken Burns efektiyle (yavas zoom) video klibe cevirir.
    zoompan filtresi: config.KEN_BURNS_ZOOM_RATIO kadar zoom yapar.
    """
    fps = config.VIDEO_FPS
    total_frames = max(int(duration * fps), 1)
    zoom_ratio = config.KEN_BURNS_ZOOM_RATIO if config.KEN_BURNS_ENABLED else 1.0

    # zoompan: baslangictan zoom_ratio'ya kadar frame frame zoom yapar,
    # sonra WxH'e (video boyutuna) scale eder.
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
    """
    Video klibi 1920x1080'e scale/crop eder ve gerekli sureye
    trim/loop eder (klip kisa ise loop, uzun ise trim).
    """
    fps = config.VIDEO_FPS

    # Once klibin gercek suresini olc
    try:
        src_duration = get_media_duration(video_path)
    except Exception:
        src_duration = duration  # olculemezse loop'a gerek yok say

    vf = (
        f"scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT},"
        f"fps={fps},format=yuv420p"
    )

    if src_duration < duration:
        # Klip gerekenden kisa -> stream_loop ile tekrarlat
        loop_count = int(duration // src_duration) + 1
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
    """media_path None olan (hicbir kaynaktan gorsel/video bulunamayan)
    sahneler icin duz siyah ekran klibi uretir - pipeline hic durmasin."""
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


# =========================================================
# ANA MONTAJ
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

    # 1) Gercek ses suresini olc
    real_duration = get_media_duration(voiceover_path)
    print(f"[youtube_montaj] Gercek seslendirme suresi: {real_duration:.2f}s")

    # 2) Olcek faktoru hesapla
    estimated_total = sum(s["estimated_seconds"] for s in scenes)
    if estimated_total <= 0:
        raise ValueError("Sahnelerin tahmini toplam suresi 0 - manifest hatali olabilir.")
    scale_factor = real_duration / estimated_total
    print(f"[youtube_montaj] Olcek faktoru: {scale_factor:.4f} "
          f"(tahmini toplam {estimated_total:.1f}s -> gercek {real_duration:.1f}s)")

    # 3) Calisma klasoru
    clips_dir = video_dir / "clips"
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True)

    clip_paths = []
    for scene in scenes:
        idx = scene["index"]
        final_duration = round(scene["estimated_seconds"] * scale_factor, 3)
        final_duration = max(final_duration, 1.0 / config.VIDEO_FPS)  # 0'a dusmesin

        media_path = scene.get("media_path")
        media_type = scene.get("media_type")
        clip_out = clips_dir / f"clip_{idx:03d}.mp4"

        if not media_path:
            build_fallback_clip(final_duration, clip_out)
        else:
            full_media_path = config.BASE_DIR / media_path
            if media_type == "image":
                build_image_clip(full_media_path, final_duration, clip_out)
            elif media_type == "video":
                build_video_clip(full_media_path, final_duration, clip_out)
            else:
                build_fallback_clip(final_duration, clip_out)

        clip_paths.append(clip_out)

    # 4) Concat listesi olustur ve klipleri birlestir
    concat_list_path = clips_dir / "concat_list.txt"
    with concat_list_path.open("w", encoding="utf-8") as f:
        for clip in clip_paths:
            # ffmpeg concat demuxer icin yol tirnak icinde ve forward-slash olmali
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

    # 5) Ses ekle (voiceover + opsiyonel arka muzik)
    final_video_path = video_dir / f"video_{day:02d}.mp4"
    bg_music_path = config.BACKGROUND_MUSIC_PATH

    if bg_music_path.exists():
        # voiceover + dusuk sesli arka muzik mix edilir
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

    # 6) Temizlik - ara dosyalari sil (disk yeri icin)
    shutil.rmtree(clips_dir, ignore_errors=True)
    silent_video_path.unlink(missing_ok=True)

    print(f"[youtube_montaj] Gun {day} tamamlandi -> {final_video_path}")
    return final_video_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        gun = int(sys.argv[1])
        montage_day(gun)
    else:
        print("Kullanim: python youtube_montaj.py <gun_numarasi>")
        print("Ornek: python youtube_montaj.py 1")
      
