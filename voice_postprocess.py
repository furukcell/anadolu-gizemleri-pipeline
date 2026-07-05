"""
voice_postprocess.py
---------------------
Kullanicinin kendi sesiyle kaydettigi HAM anlatim dosyasini (ornek:
content/raw_audio/NN.mp3) alir, test edilip onaylanan ffmpeg zincirini
uygulayarak "temiz studyo + tok + net" belgesel anlatici tonuna cevirir.
Cikti: output/video_NN/voiceover.mp3

Zincir (test edilen v3 - echo/pitch degisikligi YOK, sadece temizlik +
netlik + sakin ton):
  1) highpass       -> ~100Hz alti (rumble/boom) temizlenir
  2) afftdn         -> arka plan gurultusu (nefes, oda hisi) azaltilir
  3) acompressor    -> ses seviyesi sakin/dogal sekilde esitlenir
  4) equalizer 120Hz -> hafif sicaklik/derinlik (+2dB)
  5) equalizer 500Hz -> "kutu icinde/boguk" hissi kirilir (-2dB)
  6) equalizer 3500Hz -> netlik/anlasilirlik artirilir (+2dB)
  7) equalizer 7500Hz -> sertlik/tislik yumusatilir (-3dB)
  8) loudnorm       -> YouTube standardina uygun loudness (-16 LUFS)

Not: echo/reverb ve pitch degisikligi KASITLI OLARAK yok - kullanicinin
onayladigi test (v3), pitch/echo eklenen versiyonlarin "bogu k/bos oda"
hissi verdigini gosterdi.

Kullanim:
  python voice_postprocess.py <ham_ses_dosyasi> <gun_numarasi>
  Ornek: python voice_postprocess.py content/raw_audio/01.mp3 1
"""

import subprocess
from pathlib import Path

import config

# =========================================================
# FFMPEG FILTRE ZINCIRI (test edilen v3, onaylandi)
# =========================================================
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


def run_ffmpeg(cmd: list, description: str):
    print(f"[voice_postprocess] {description}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise RuntimeError(f"ffmpeg basarisiz oldu: {description}")


def validate_raw_audio(raw_path: Path):
    if not raw_path.exists():
        raise FileNotFoundError(f"Ham ses dosyasi bulunamadi: {raw_path}")

    # ffprobe ile suresini kontrol et - cok kisa/bos dosya varsa erken uyar
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
    if duration < 10:
        print(f"[voice_postprocess] UYARI: ses dosyasi cok kisa ({duration:.1f}s). "
              f"Yanlis dosya yuklenmis olabilir.")
    return duration


def process_voice(raw_audio_path: str, day: int) -> Path:
    """
    Ham ses kaydini isler ve output/video_NN/voiceover.mp3 olarak kaydeder.
    Boylece pipeline'daki sonraki adim (youtube_montaj.py) TTS'ten gelen
    voiceover.mp3'u bekledigi gibi, kullanici kaydindan gelen voiceover.mp3'u
    da ayni sekilde bulur - iki kaynak birbirinin yerine gecebilir.
    """
    raw_path = Path(raw_audio_path)
    duration = validate_raw_audio(raw_path)

    out_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "voiceover.mp3"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_path),
        "-af", AUDIO_FILTER_CHAIN,
        "-ar", str(OUTPUT_SAMPLE_RATE),
        str(out_path),
    ]
    run_ffmpeg(cmd, f"Gun {day}: ham kayit isleniyor ({duration:.1f}s) -> {out_path.name}")

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
      
