"""
google_tts_generate.py
-----------------------
script_parse.py'nin urettigi output/video_XX/script_parsed.json dosyasindaki
"full_narration" metnini Google Cloud Text-to-Speech ile sese cevirir.

Google TTS API'nin tek istekte ~5000 karakter siniri oldugu icin, uzun
senaryo metni once cumle sinirlarina saygi duyarak parcalara bolunur,
her parca ayri ayri seslendirilir, sonra ffmpeg ile tek dosyada birlestirilir.

Cikti: output/video_XX/voiceover.mp3
"""

import json
import os
import re
import subprocess
from pathlib import Path

import config

# Google Cloud kutuphanesinin credential dosyasini bulabilmesi icin
# ortam degiskenini garanti altina al.
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(config.GOOGLE_APPLICATION_CREDENTIALS)

from google.cloud import texttospeech  # noqa: E402  (env var ayarlandiktan sonra import)


MAX_CHUNK_CHARS = 3500  # Google'in 5000 karakter sinirina karsi guvenli pay


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS):
    """
    Metni cumle sinirlarini bozmadan max_chars'i asmayan parcalara boler.
    Boylece TTS her parcayi dogal cumle sonlarinda kesip seslendirir.
    """
    # Cumleleri nokta/unlem/soru isaretinden sonra bosluk gelen yerlerden ayir
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    chunks = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(current.strip())

    return chunks


def synthesize_chunk(client, text: str, output_path: Path):
    """Tek bir metin parcasini Google TTS ile seslendirip mp3 olarak kaydeder."""
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code=config.GOOGLE_TTS_LANGUAGE_CODE,
        name=config.GOOGLE_TTS_VOICE_NAME,
    )

    # Chirp 3 HD / Studio ses ailesi "pitch" parametresini desteklemiyor.
    # Bu ailelerde pitch gonderilmezse API hata verir, o yuzden kosullu ekliyoruz.
    is_pitch_supported = not any(
        tag in config.GOOGLE_TTS_VOICE_NAME for tag in ("Chirp3-HD", "Studio")
    )

    audio_config_kwargs = {
        "audio_encoding": texttospeech.AudioEncoding.MP3,
        "speaking_rate": config.GOOGLE_TTS_SPEAKING_RATE,
    }
    if is_pitch_supported:
        audio_config_kwargs["pitch"] = config.GOOGLE_TTS_PITCH

    audio_config = texttospeech.AudioConfig(**audio_config_kwargs)

    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )

    output_path.write_bytes(response.audio_content)


def concatenate_mp3s(part_paths, final_output_path: Path):
    """ffmpeg concat demuxer ile birden fazla mp3 parcasini tek dosyada birlestirir."""
    list_file = final_output_path.parent / "tts_parts_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in part_paths:
            # ffmpeg concat formati: file 'yol'
            f.write(f"file '{p.resolve().as_posix()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(final_output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg birlestirme hatasi:\n{result.stderr}")

    list_file.unlink(missing_ok=True)


def generate_voiceover(day: int) -> Path:
    """
    Bir gunun script_parsed.json dosyasini okuyup voiceover.mp3 uretir.
    Donen deger: uretilen ses dosyasinin yolu.
    """
    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    parsed_path = video_dir / "script_parsed.json"

    if not parsed_path.exists():
        raise FileNotFoundError(
            f"{parsed_path} bulunamadi. Once script_parse.py {day} calistirilmali."
        )

    data = json.loads(parsed_path.read_text(encoding="utf-8"))
    full_text = data["full_narration"]

    if not full_text.strip():
        raise ValueError(f"Gun {day} icin anlatim metni bos.")

    chunks = split_text_into_chunks(full_text)
    print(f"[google_tts] Gun {day}: {len(chunks)} parcaya bolundu "
          f"(toplam {len(full_text)} karakter)")

    client = texttospeech.TextToSpeechClient()

    parts_dir = video_dir / "tts_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    part_paths = []
    for i, chunk in enumerate(chunks):
        part_path = parts_dir / f"part_{i:03d}.mp3"
        synthesize_chunk(client, chunk, part_path)
        part_paths.append(part_path)
        print(f"[google_tts]   parca {i + 1}/{len(chunks)} seslendirildi "
              f"({len(chunk)} karakter)")

    final_path = video_dir / "voiceover.mp3"

    if len(part_paths) == 1:
        # Tek parca varsa birlestirmeye gerek yok, direkt kopyala
        final_path.write_bytes(part_paths[0].read_bytes())
    else:
        concatenate_mp3s(part_paths, final_path)

    print(f"[google_tts] Gun {day} seslendirme tamamlandi -> {final_path}")
    return final_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Kullanim: python google_tts_generate.py <gun_numarasi>")
        sys.exit(1)

    gun = int(sys.argv[1])
    generate_voiceover(gun)
    
