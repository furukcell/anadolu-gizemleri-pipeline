"""
script_parse.py
----------------
content/ klasorundeki NN_..._UTF8.md dosyalarini okur.
Her dosyada hem TR hem EN senaryo var; sadece TR bolumunu cekeriz
(config.TR_SECTION_MARKER ile EN_SECTION_MARKER arasi).

TR bolumu iki turlu icerik barindirir:
  - Sahne yonlendirmeleri: [Ekrana ... gelir.] gibi kose parantez notlari
  - Anlatici metni: TTS'e gidecek asil seslendirme metni

Bu modul, sahne notu + kendisinden sonra gelen anlatim metnini
sirali bir liste halinde ayirir. Boylece:
  - Tum anlatim metni birlestirilip TTS'e gonderilebilir
  - Her sahne notu, kendisinden sonraki anlatim bloguyla eslesir
    (timestamp_estimate.py / image_fetch.py bunu gorsel sorgusuna cevirecek)
"""

import json
import re
from pathlib import Path

import config


def list_content_files():
    """content/ klasorundeki .md dosyalarini gun numarasina gore siralar."""
    files = sorted(config.CONTENT_DIR.glob("*.md"))
    # README gibi numarasiz dosyalari disla
    numbered = []
    for f in files:
        m = re.match(r"^(\d+)_", f.name)
        if m:
            numbered.append((int(m.group(1)), f))
    numbered.sort(key=lambda x: x[0])
    return numbered


def find_file_for_day(day: int) -> Path:
    """Verilen gun numarasina (1, 2, 3...) karsilik gelen md dosyasini bulur."""
    prefix = f"{day:02d}_"
    matches = list(config.CONTENT_DIR.glob(f"{prefix}*.md"))
    if not matches:
        raise FileNotFoundError(
            f"Gun {day} icin content/ klasorunde dosya bulunamadi (beklenen on ek: {prefix})"
        )
    return matches[0]


def extract_tr_section(full_text: str) -> str:
    """Dosyanin TR SENARYO ile ENGLISH SCRIPT basliklari arasindaki kismini doner."""
    start_idx = full_text.find(config.TR_SECTION_MARKER)
    end_idx = full_text.find(config.EN_SECTION_MARKER)

    if start_idx == -1:
        raise ValueError(f"'{config.TR_SECTION_MARKER}' basligi bulunamadi.")
    if end_idx == -1:
        # EN bolumu yoksa dosyanin sonuna kadar al
        end_idx = len(full_text)

    return full_text[start_idx:end_idx]


def extract_title(tr_section: str) -> str:
    """TR bolumundeki '## N. Gun: Baslik' satirindan sadece basligi ceker."""
    match = re.search(r"^##\s*\d+\.\s*Gün:\s*(.+)$", tr_section, re.MULTILINE)
    if match:
        return match.group(1).strip()
    # Bulunamazsa ilk '#' basligini fallback olarak kullan
    fallback = re.search(r"^#{1,2}\s*(.+)$", tr_section, re.MULTILINE)
    return fallback.group(1).strip() if fallback else "Baslik Bulunamadi"


def split_into_segments(tr_section: str):
    """
    TR bolumunu sirali segmentlere ayirir:
    [{"scene_note": "..." | None, "narration": "..."}]

    Her segment, bir sahne notu (varsa) ve o notu takip eden anlatim
    metnini icerir. Basliklar, yatay cizgiler ("---") ve bos satirlar atlanir.
    """
    # Basliklari (# / ##) ve "---" ayiraclarini temizle
    body = re.sub(r"^#{1,2}.*$", "", tr_section, flags=re.MULTILINE)
    body = re.sub(r"^---\s*$", "", body, flags=re.MULTILINE)

    # Metni sahne notlarina gore parcala: [...] iceren kisimlar ayri yakalanir
    parts = re.split(r"(\[[^\]]*\])", body)

    segments = []
    current_scene_note = None
    current_narration = []

    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        if part_stripped.startswith("[") and part_stripped.endswith("]"):
            # Yeni sahne notu geldi -> onceki segmenti kapat
            if current_narration:
                segments.append({
                    "scene_note": current_scene_note,
                    "narration": " ".join(current_narration).strip(),
                })
                current_narration = []
            current_scene_note = part_stripped[1:-1].strip()
        else:
            # ANLATICI: gibi etiketleri temizle, satir sonlarini normallestir
            cleaned = re.sub(r"^\*\*ANLATICI:\*\*", "", part_stripped, flags=re.MULTILINE)
            cleaned = cleaned.replace("\n", " ")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                current_narration.append(cleaned)

    # Son segmenti de ekle
    if current_narration or current_scene_note:
        segments.append({
            "scene_note": current_scene_note,
            "narration": " ".join(current_narration).strip(),
        })

    # Anlatimi olmayan bos segmentleri ele (sadece sahne notu olup metni olmayanlar)
    segments = [s for s in segments if s["narration"]]

    return segments


def get_full_narration_text(segments) -> str:
    """TTS'e gonderilecek tam anlatim metnini, sahne notlari olmadan dondurur."""
    return " ".join(s["narration"] for s in segments).strip()


def parse_day(day: int, save_json: bool = True) -> dict:
    """Bir gunun md dosyasini tam olarak isler ve sozluk olarak doner."""
    file_path = find_file_for_day(day)
    full_text = file_path.read_text(encoding="utf-8")

    tr_section = extract_tr_section(full_text)
    title = extract_title(tr_section)
    segments = split_into_segments(tr_section)
    full_narration = get_full_narration_text(segments)

    result = {
        "day": day,
        "source_file": file_path.name,
        "title": title,
        "segment_count": len(segments),
        "segments": segments,
        "full_narration": full_narration,
        "word_count": len(full_narration.split()),
    }

    if save_json:
        out_dir = config.OUTPUT_DIR / f"video_{day:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "script_parsed.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[script_parse] Gun {day} islendi -> {out_path}")

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        gun = int(sys.argv[1])
        data = parse_day(gun)
        print(f"Baslik: {data['title']}")
        print(f"Segment sayisi: {data['segment_count']}")
        print(f"Kelime sayisi: {data['word_count']}")
    else:
        # Argman verilmezse content/ klasorundeki tum gunleri listele (test amacli)
        files = list_content_files()
        print(f"content/ klasorunde {len(files)} dosya bulundu:")
        for day_num, f in files:
            print(f"  Gun {day_num:02d}: {f.name}")
          
