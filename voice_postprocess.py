"""
voice_postprocess.py
---------------------
Kullanicinin kendi sesiyle kaydettigi HAM anlatim dosyasini isler.

V6:
- Bos/bozuk/kisa dosyayi hata sayar.
- Eski voiceover.mp3'u temizleyip yeniden uretir.
- voice_source.json yazar; boylece videoda hangi sesin kullanildigi takip edilir.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import config

AUDIO_FILTER_CHAIN = (
    "highpass=f=100,"
    "afftdn=nr=18:nf=-30,"
    "acompressor=threshold=-20dB:ratio=2.5:attack=15:release=300:makeup=2,"
    "equalizer=f=120:width_type=o:width=1.5:g=2,"
    "equalizer=f=500:width_type=o:width=1.5:g=-2,"
    "equalizer=f=3500:width_type=o:width=1.5:g=2,"
    "equalizer=f=7500:width_type=o:width=1.5:g=-3,"
    "loudnorm=I=-16:TP=-1.5:LRA=7"
)

OUTPUT_SAMPLE_RATE = 44100
MIN_VALID_DURATION = 30.0
MIN_VALID_BYTES = 100_000


def run_ffmpeg(cmd: list, description: str):
    print(f"[voice_postprocess] {description}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise RuntimeError(f"ffmpeg basarisiz oldu: {description}")


def validate_raw_audio(raw_path: Path) -> float:
    if not raw_path.exists():
        raise FileNotFoundError(f"Ham ses dosyasi bulunamadi: {raw_path}")
    if raw_path.stat().st_size < MIN_VALID_BYTES:
        raise RuntimeError(
            f"Ham ses dosyasi cok kucuk/bozuk gorunuyor: {raw_path} "
            f"({raw_path.stat().st_size} byte)"
        )

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(raw_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ham ses dosyasi okunamadi (bozuk olabilir): {raw_path}")

    duration = float(result.stdout.strip())
    if duration < MIN_VALID_DURATION:
        raise RuntimeError(
            f"Ham ses dosyasi cok kisa ({duration:.1f}s). Yanlis dosya yuklenmis olabilir: {raw_path}"
        )

    print(f"[voice_postprocess] Ham ses dogrulandi: {raw_path.name} ({duration:.1f}s)")
    return duration


def process_voice(raw_audio_path: str, day: int) -> Path:
    raw_path = Path(raw_audio_path)
    duration = validate_raw_audio(raw_path)

    out_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "voiceover.mp3"
    source_path = out_dir / "voice_source.json"

    out_path.unlink(missing_ok=True)
    source_path.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_path),
        "-af", AUDIO_FILTER_CHAIN,
        "-ar", str(OUTPUT_SAMPLE_RATE),
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Gun {day}: KULLANICI SESI isleniyor ({duration:.1f}s) -> {out_path.name}")

    if not out_path.exists() or out_path.stat().st_size < 100_000:
        raise RuntimeError(f"voiceover.mp3 olusmadi veya cok kucuk: {out_path}")

    source_info = {
        "day": day,
        "mode": "user_raw_audio_only",
        "tts_fallback": False,
        "source_file": str(raw_path),
        "source_name": raw_path.name,
        "source_duration_seconds": round(duration, 3),
        "output_file": str(out_path),
    }
    source_path.write_text(json.dumps(source_info, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[voice_postprocess] Gun {day} tamamlandi -> {out_path}")
    print(f"[voice_postprocess] Ses kaynagi kaydedildi -> {source_path}")
    return out_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2:
        process_voice(sys.argv[1], int(sys.argv[2]))
    else:
        print("Kullanim: python voice_postprocess.py <ham_ses_dosyasi> <gun_numarasi>")
        print("Ornek: python voice_postprocess.py content/raw_audio/01.mp3 1")
