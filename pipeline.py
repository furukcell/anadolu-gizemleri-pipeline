"""
pipeline.py
------------
V8 pipeline:
script_parse -> kullanici sesi -> video_discovery -> image_fetch -> youtube_montaj -> youtube_upload

- TTS fallback kapali.
- Sadece kullanicinin ham ses kaydi kabul edilir.
- content/raw_audio/01.mp3 veya content/raw_audio/1.mp3 desteklenir.
- Video tarafinda once konuya ozel lisansli/indirilebilir aday havuzu kurulur.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

import config
import script_parse
import video_discovery
import image_fetch
import youtube_montaj

try:
    import youtube_upload
except ImportError:
    youtube_upload = None


def find_raw_audio(day: int) -> Path:
    candidates = [
        config.CONTENT_DIR / "raw_audio" / f"{day:02d}.mp3",
        config.CONTENT_DIR / "raw_audio" / f"{day}.mp3",
    ]

    for path in candidates:
        if path.exists() and path.stat().st_size > 1024:
            return path

    raw_dir = config.CONTENT_DIR / "raw_audio"
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = [p.name for p in raw_dir.glob("*.mp3")]
    raise FileNotFoundError(
        f"KULLANICI SESI BULUNAMADI.\n"
        f"Beklenen dosya: content/raw_audio/{day:02d}.mp3 "
        f"(veya content/raw_audio/{day}.mp3)\n"
        f"Mevcut mp3 dosyalari: {existing}\n"
        "TTS fallback kapali; otomatik ses uretilmeyecek."
    )


def run_voice_step(day: int) -> Path:
    print("[pipeline] SES MODU: SADECE KULLANICI SESI")
    print("[pipeline] TTS FALLBACK: KAPALI")

    raw_audio_path = find_raw_audio(day)
    expected_output = config.OUTPUT_DIR / f"video_{day:02d}" / "voiceover.mp3"

    # Eski TTS/kotu ses karismasin.
    expected_output.unlink(missing_ok=True)

    print(f"[pipeline] Gun {day}: kullanici ses kaydi bulundu -> {raw_audio_path.name}")
    import voice_postprocess
    return voice_postprocess.process_voice(str(raw_audio_path), day)


def run_pipeline(day: int, do_upload: bool = True):
    steps_completed = []
    start_time = time.time()

    print(f"\n{'='*60}")
    print(f"  PIPELINE BASLIYOR - Gun {day}")
    print(f"{'='*60}\n")

    try:
        print("[pipeline] ADIM 1/6: script_parse.py")
        parsed = script_parse.parse_day(day)
        steps_completed.append("script_parse")
        print(
            f"[pipeline] OK -> Baslik: '{parsed['title']}', "
            f"{parsed['segment_count']} sahne, {parsed['word_count']} kelime\n"
        )

        print("[pipeline] ADIM 2/6: kullanici ses kaydi")
        voiceover_path = run_voice_step(day)
        steps_completed.append("voice")
        print(f"[pipeline] OK -> {voiceover_path}\n")

        print("[pipeline] ADIM 3/6: konu videosu aday havuzu (video_discovery.py)")
        candidates_path = video_discovery.process_day(day)
        steps_completed.append("video_discovery")
        print(f"[pipeline] OK -> {candidates_path}\n")

        print("[pipeline] ADIM 4/6: aday havuzundan planli video secimi (image_fetch.py)")
        image_fetch.process_day(day)
        steps_completed.append("image_fetch")
        print("[pipeline] OK -> konu video medya secimi tamamlandi\n")

        print("[pipeline] ADIM 5/6: youtube_montaj.py")
        video_path = youtube_montaj.montage_day(day)
        steps_completed.append("youtube_montaj")
        print(f"[pipeline] OK -> {video_path}\n")

        if do_upload:
            print("[pipeline] ADIM 6/6: youtube_upload.py")
            if youtube_upload is None:
                raise RuntimeError(
                    "youtube_upload.py import edilemedi. requirements.txt'i kontrol et."
                )
            video_id = youtube_upload.upload_video(day)
            steps_completed.append("youtube_upload")
            print(f"[pipeline] OK -> https://www.youtube.com/watch?v={video_id}\n")
        else:
            print(
                f"[pipeline] ADIM 6/6: ATLANDI (--no-upload). "
                f"Video hazir: {video_path}\n"
            )

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n{'!'*60}")
        print(f"  PIPELINE BASARISIZ OLDU - Gun {day}")
        print(f"  Tamamlanan adimlar: {steps_completed}")
        print(f"  Hata: {e}")
        print(f"  Gecen sure: {elapsed:.1f}s")
        print(f"{'!'*60}\n")
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  PIPELINE TAMAMLANDI - Gun {day} ({elapsed:.1f}s)")
    print(f"  Tamamlanan adimlar: {steps_completed}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Kullanim: python pipeline.py <gun_numarasi> [--no-upload]")
        print("Ornek:    python pipeline.py 1")
        print("          python pipeline.py 1 --no-upload")
        sys.exit(1)

    gun = int(sys.argv[1])
    no_upload = "--no-upload" in sys.argv
    run_pipeline(gun, do_upload=not no_upload)
