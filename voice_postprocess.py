"""
voice_postprocess.py
---------------------
Kullanicinin kendi sesiyle kaydettigi HAM anlatim dosyasini
(content/raw_audio/NN.mp3 veya content/raw_audio/N.mp3) isler ve
output/video_NN/voiceover.mp3 olarak kaydeder.

Bu dosya kesinlikle TTS uretmez. Sadece verilen ham kullanici sesini temizler.
"""

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
MIN_RAW_AUDIO_BYTES = 100 * 1024      # bos/yanlis dosya yakalama
MIN_RAW_AUDIO_SECONDS = 30.0          # uzun video icin cok kisa kaydi hata say


def run_ffmpeg(cmd: list, description: str):
    print(f"[voice_postprocess] {description}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise RuntimeError(f"ffmpeg basarisiz oldu: {description}")


def validate_raw_audio(raw_path: Path) -> float:
    if not raw_path.exists():
        raise FileNotFoundError(f"Ham ses dosyasi bulunamadi: {raw_path}")

    file_size = raw_path.stat().st_size
    if file_size < MIN_RAW_AUDIO_BYTES:
        raise RuntimeError(
            f"Ham ses dosyasi cok kucuk/bozuk gorunuyor: {raw_path} "
            f"({file_size} byte). Gercek mp3 dosyasini Upload files ile yukle."
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

    try:
        duration = float(result.stdout.strip())
    except ValueError as e:
        raise RuntimeError(f"Ham ses suresi okunamadi: {raw_path}") from e

    if duration < MIN_RAW_AUDIO_SECONDS:
        raise RuntimeError(
            f"Ham ses dosyasi cok kisa ({duration:.1f}s): {raw_path}. "
            "Yanlis dosya yuklenmis olabilir."
        )

    print(f"[voice_postprocess] Ham ses dogrulandi: {raw_path.name}, {duration:.1f}s, {file_size} byte")
    return duration


def process_voice(raw_audio_path: str, day: int) -> Path:
    raw_path = Path(raw_audio_path)
    duration = validate_raw_audio(raw_path)

    out_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "voiceover.mp3"

    # Eski TTS/deneme dosyasi kalmasin.
    out_path.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_path),
        "-af", AUDIO_FILTER_CHAIN,
        "-ar", str(OUTPUT_SAMPLE_RATE),
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Gun {day}: KULLANICI SESI isleniyor ({duration:.1f}s) -> {out_path.name}")

    if not out_path.exists() or out_path.stat().st_size < MIN_RAW_AUDIO_BYTES:
        raise RuntimeError(f"Islenen voiceover olusmadi veya cok kucuk: {out_path}")

    print(f"[voice_postprocess] Gun {day} tamamlandi -> {out_path}")
    return out_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2:
        raw_file = sys.argv[1]
        gun = int(sys.argv[2])
        process_voice(raw_file, gun)
    else:
        print("Kullanim: python voice_postprocess.py <ham_ses_dosyasi> <gun_numarasi>")
        print("Ornek: python voice_postprocess.py content/raw_audio/01.mp3 1")
