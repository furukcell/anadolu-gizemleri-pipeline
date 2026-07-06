"""
pipeline.py
------------
Tum adimlari (script_parse -> ses -> image_fetch -> youtube_montaj ->
youtube_upload) tek komutla sirayla calistiran orkestrator.

SES KAYNAGI SECIMI:
  Once content/raw_audio/NN.mp3 (kullanicinin kendi kaydi) aranir.
  Bulunursa voice_postprocess.py ile islenir.
  Bulunamazsa google_tts_generate.py ile TTS uretilir (fallback).

Herhangi bir adim basarisiz olursa pipeline durur, hangi adimda ve
neden basarisiz oldugu acikca yazdirilir - boylece hatanin nerede
oldugu telefondan bile anlasilir.

Kullanim:
  python pipeline.py <gun_numarasi>              # tum adimlar + upload
  python pipeline.py <gun_numarasi> --no-upload   # upload'siz test (video
                                                      dosyasini uretir ama
                                                      YouTube'a yuklemez)

Ornek:
  python pipeline.py 1
  python pipeline.py 1 --no-upload
"""

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
# ADIM: SES URETIMI (kullanici kaydi ONCELIKLI, TTS fallback)
# =========================================================
def run_voice_step(day: int) -> Path:
    """
    Once content/raw_audio/NN.mp3 (kullanicinin kendi kaydi) aranir.
    Varsa voice_postprocess.py ile islenir. Yoksa google_tts_generate.py
    ile TTS uretilir.

    Doner: output/video_NN/voiceover.mp3 yolu
    """
    raw_audio_path = config.CONTENT_DIR / "raw_audio" / f"{day:02d}.mp3"
    expected_output = config.OUTPUT_DIR / f"video_{day:02d}" / "voiceover.mp3"

    if raw_audio_path.exists():
        print(f"[pipeline] Gun {day}: kullanici ses kaydi bulundu -> {raw_audio_path.name}")
        import voice_postprocess
        return voice_postprocess.process_voice(str(raw_audio_path), day)

    print(f"[pipeline] Gun {day}: kullanici ses kaydi yok, TTS kullanilacak.")
    try:
        import google_tts_generate
    except ImportError as e:
        raise RuntimeError(
            "Ne kullanici kaydi (content/raw_audio/NN.mp3) ne de "
            "google_tts_generate.py modulu bulunamadi. Ses uretilemedi."
        ) from e

    # google_tts_generate.py'nin tam fonksiyon adini bilmiyoruz - birkac
    # olasi adi deneriz. Hicbiri calismazsa acik bir hata verir.
    candidate_function_names = [
        "generate_voiceover",
        "generate_tts",
        "synthesize_day",
        "generate_day",
        "main",
    ]
    for fn_name in candidate_function_names:
        fn = getattr(google_tts_generate, fn_name, None)
        if callable(fn):
            print(f"[pipeline] google_tts_generate.{fn_name}({day}) cagriliyor...")
            fn(day)
            if expected_output.exists():
                return expected_output
            break

    if not expected_output.exists():
        raise RuntimeError(
            f"TTS calistirildi ama beklenen dosya olusmadi: {expected_output}. "
            f"google_tts_generate.py'nin fonksiyon adini kontrol et."
        )
    return expected_output


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
        print(f"[pipeline] ADIM 1/5: script_parse.py")
        parsed = script_parse.parse_day(day)
        steps_completed.append("script_parse")
        print(f"[pipeline] OK -> Baslik: '{parsed['title']}', "
              f"{parsed['segment_count']} sahne, {parsed['word_count']} kelime\n")

        # 2) Ses uretimi
        print(f"[pipeline] ADIM 2/5: ses uretimi (kullanici kaydi / TTS)")
        voiceover_path = run_voice_step(day)
        steps_completed.append("voice")
        print(f"[pipeline] OK -> {voiceover_path}\n")

        # 3) Gorsel/video toplama
        print(f"[pipeline] ADIM 3/5: image_fetch.py")
        image_fetch.process_day(day)
        steps_completed.append("image_fetch")
        print(f"[pipeline] OK -> medya toplama tamamlandi\n")

        # 4) Video montaj
        print(f"[pipeline] ADIM 4/5: youtube_montaj.py")
        video_path = youtube_montaj.montage_day(day)
        steps_completed.append("youtube_montaj")
        print(f"[pipeline] OK -> {video_path}\n")

        # 5) YouTube'a yukleme (opsiyonel)
        if do_upload:
            print(f"[pipeline] ADIM 5/5: youtube_upload.py")
            if youtube_upload is None:
                raise RuntimeError(
                    "youtube_upload.py import edilemedi (google-api-python-client "
                    "kurulu olmayabilir). requirements.txt'i kontrol et."
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
  
