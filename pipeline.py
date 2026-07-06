"""
pipeline.py
------------
Tum adimlari (script_parse -> ses -> image_fetch -> youtube_montaj ->
youtube_upload) tek komutla sirayla calistiran orkestrator.

SES KAYNAGI SECIMI:
  Bu surumda TTS fallback tamamen kapali.
  Once content/raw_audio/NN.mp3 aranir.
  Bulunamazsa kolaylik icin content/raw_audio/N.mp3 de aranir.
  Ikisi de yoksa pipeline DURUR; Google TTS'e dusmez.

Kullanim:
  python pipeline.py <gun_numarasi>
  python pipeline.py <gun_numarasi> --no-upload
"""

import json
import sys
import time
import traceback
from pathlib import Path

import config
import script_parse
import image_fetch
import youtube_montaj

try:
    import youtube_upload
except ImportError:
    youtube_upload = None


# =========================================================
# ADIM: SES ISLEME - SADECE KULLANICI SESI, TTS YOK
# =========================================================
def _candidate_raw_audio_paths(day: int) -> list[Path]:
    """Day 1 icin hem 01.mp3 hem 1.mp3 desteklenir."""
    raw_dir = config.CONTENT_DIR / "raw_audio"
    return [
        raw_dir / f"{day:02d}.mp3",
        raw_dir / f"{day}.mp3",
    ]


def _find_raw_audio(day: int) -> Path:
    for path in _candidate_raw_audio_paths(day):
        if path.exists():
            return path

    expected = ", ".join(str(p) for p in _candidate_raw_audio_paths(day))
    raise FileNotFoundError(
        "KULLANICI SES KAYDI BULUNAMADI. TTS bilerek kapali.\n"
        f"Gun {day} icin su dosyalardan biri gerekli: {expected}\n"
        "GitHub'da Add file -> Upload files ile gercek mp3 dosyasini yukle. "
        "Create new file ile bos .mp3 olusturma."
    )


def run_voice_step(day: int) -> Path:
    """
    Kullanici ham ses kaydini isler.
    TTS fallback YOKTUR. Ses yoksa pipeline hata verip durur.
    """
    raw_audio_path = _find_raw_audio(day)
    expected_output = config.OUTPUT_DIR / f"video_{day:02d}" / "voiceover.mp3"
    voice_source_path = config.OUTPUT_DIR / f"video_{day:02d}" / "voice_source.json"

    print("[pipeline] SES MODU: SADECE KULLANICI SESI")
    print("[pipeline] TTS FALLBACK: KAPALI")
    print(f"[pipeline] Gun {day}: kullanici ses kaydi bulundu -> {raw_audio_path}")

    # Onceki denemeden kalmis TTS/voiceover dosyasi varsa sil.
    # Boylece isleme basarisiz olursa eski otomatik ses yanlislikla montaja girmez.
    expected_output.unlink(missing_ok=True)
    voice_source_path.unlink(missing_ok=True)

    import voice_postprocess

    voiceover_path = voice_postprocess.process_voice(str(raw_audio_path), day)

    source_info = {
        "source": "user_raw_audio",
        "raw_audio_path": str(raw_audio_path.relative_to(config.BASE_DIR)),
        "output_path": str(voiceover_path.relative_to(config.BASE_DIR)),
        "tts_fallback": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    voice_source_path.parent.mkdir(parents=True, exist_ok=True)
    voice_source_path.write_text(json.dumps(source_info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pipeline] SES KAYNAGI KAYDI -> {voice_source_path}")

    if not voiceover_path.exists():
        raise RuntimeError(f"Kullanici sesi islendi deniyor ama cikti yok: {voiceover_path}")

    return voiceover_path


# =========================================================
# ANA PIPELINE
# =========================================================
def run_pipeline(day: int, do_upload: bool = True):
    steps_completed = []
    start_time = time.time()

    print(f"\n{'='*60}")
    print(f"  PIPELINE BASLIYOR - Gun {day}")
    print(f"{'='*60}\n")

    try:
        # 1) Senaryo ayristirma
        print("[pipeline] ADIM 1/5: script_parse.py")
        parsed = script_parse.parse_day(day)
        steps_completed.append("script_parse")
        print(f"[pipeline] OK -> Baslik: '{parsed['title']}', "
              f"{parsed['segment_count']} sahne, {parsed['word_count']} kelime\n")

        # 2) Kullanici sesini isleme
        print("[pipeline] ADIM 2/5: ses isleme (SADECE kullanici kaydi, TTS yok)")
        voiceover_path = run_voice_step(day)
        steps_completed.append("voice")
        print(f"[pipeline] OK -> {voiceover_path}\n")

        # 3) Gorsel/video toplama
        print("[pipeline] ADIM 3/5: image_fetch.py")
        image_fetch.process_day(day)
        steps_completed.append("image_fetch")
        print("[pipeline] OK -> medya toplama tamamlandi\n")

        # 4) Video montaj
        print("[pipeline] ADIM 4/5: youtube_montaj.py")
        video_path = youtube_montaj.montage_day(day)
        steps_completed.append("youtube_montaj")
        print(f"[pipeline] OK -> {video_path}\n")

        # 5) YouTube'a yukleme
        if do_upload:
            print("[pipeline] ADIM 5/5: youtube_upload.py")
            if youtube_upload is None:
                raise RuntimeError(
                    "youtube_upload.py import edilemedi. requirements.txt'i kontrol et."
                )
            video_id = youtube_upload.upload_video(day)
            steps_completed.append("youtube_upload")
            print(f"[pipeline] OK -> https://www.youtube.com/watch?v={video_id}\n")
        else:
            print(f"[pipeline] ADIM 5/5: ATLANDI (--no-upload verildi). "
                  f"Video hazir ama YouTube'a yuklenmedi: {video_path}\n")

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
