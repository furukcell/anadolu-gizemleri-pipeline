# Anadolu Gizemleri — Otomatik YouTube Video Pipeline

Bu repo, **Anadolu Gizemleri** YouTube kanalı için 7-8 dakikalık belgesel
tarzı videoları tamamen otomatik üreten bir pipeline'dır. Sistem, hazır
senaryolardan başlayarak seslendirme, görsel toplama, video montajı ve
YouTube'a yükleme adımlarının tamamını insan müdahalesi olmadan yapar.

Pipeline **GitHub Actions** üzerinde çalışacak şekilde tasarlandı — yani
bir bilgisayara ihtiyaç yok, her şey buluttadır. Tetikleme ve takip
telefondan yapılabilir.

---

## Bu repo ne değildir

- Bu bir Shorts (kısa video) pipeline'ı değildir. Videolar 7-8 dakikalık,
  yatay (1920x1080) formatta, belgesel/gizem tarzı içeriklerdir.
- Kod, script yazma yapay zekaya bırakılmamıştır — 30 günlük senaryonun
  tamamı önceden yazılmış ve `content/` klasörüne konmuştur. Pipeline
  sadece bu hazır metinleri sese ve görüntüye çevirir.
- Şu an sadece **Türkçe** kanal için çalışıyor. İngilizce versiyon
  ileride ayrı bir kanal olarak eklenebilir (senaryoların İngilizce
  hâli de dosyalarda mevcut ama pipeline şu an kullanmıyor).

---

## Genel Akış

```
content/NN_....md  (hazır senaryo, TR + EN birlikte)
        │
        ▼
script_parse.py        → TR bölümünü çeker, sahne notu + anlatım
        │                 metnini ayırır, script_parsed.json üretir
        ▼
google_tts_generate.py → Google Cloud TTS ile anlatımı seslendirir,
        │                 voiceover.mp3 üretir
        ▼
image_fetch.py          → Sahne notlarından görsel arama sorgusu
   (henüz yazılmadı)       çıkarır, Pexels + Wikimedia'dan görsel indirir
        ▼
youtube_montaj.py       → ffmpeg ile görselleri Ken Burns efektiyle
   (henüz yazılmadı)       birleştirir, sesle senkronlar, video_XX.mp4 üretir
        ▼
youtube_upload.py       → YouTube Data API v3 ile videoyu otomatik yükler
   (henüz yazılmadı)
        ▼
GitHub Actions cron     → her gün 1 video işlenip yayına alınır
   (henüz kurulmadı)       (30 günde seri tamamlanır)
```

---

## Dosya Yapısı

```
anadolu-gizemleri-pipeline/
├── config.py                  # Tüm ayarların tek merkezi
├── script_parse.py            # MD dosyalarını okuyup ayrıştırır
├── google_tts_generate.py     # Seslendirme modülü
├── requirements.txt           # Python bağımlılıkları
├── content/                   # 30 adet hazır senaryo (.md, TR+EN)
│   ├── 01_gobeklitepe_karahantepe_senaryo_tr_en_UTF8.md
│   ├── 02_catalhoyuk_senaryo_tr_en_UTF8.md
│   └── ... (30 dosya)
├── output/                    # Üretilen dosyalar (script/ses/görsel/video)
│   └── video_NN/
│       ├── script_parsed.json
│       ├── voiceover.mp3
│       └── ...
└── state/
    └── uploaded.json          # Hangi videoların yüklendiğinin kaydı
```

---

## Şu Ana Kadar Yapılanlar

- [x] 30 günlük senaryo paketi `content/` klasörüne yüklendi
- [x] `config.py` yazıldı — TTS, görsel, video, YouTube ayarlarının hepsi
      tek dosyada, hiçbir modülde hardcode değer yok
- [x] `script_parse.py` yazıldı ve test edildi — 30 dosyanın tamamı
      doğru şekilde ayrıştırılıyor (başlık, sahne notları, anlatım metni)
- [x] `google_tts_generate.py` yazıldı — uzun metni cümle sınırlarına
      saygılı şekilde parçalara bölüp Google TTS ile seslendiriyor,
      ffmpeg ile parçaları tek dosyada birleştiriyor
- [x] Google Cloud projesi (`usta-mugla`) üzerinde:
  - Text-to-Speech API etkinleştirildi
  - YouTube Data API v3 etkinleştirildi
  - `tts-bot` service account oluşturuldu, JSON key üretildi
- [x] `GOOGLE_CREDENTIALS_JSON` GitHub Secrets'a eklendi

## Sırada Ne Var

- [ ] Google TTS ile gerçek bir seslendirme testi yapmak (uçtan uca)
- [ ] YouTube OAuth Client kurulumu (Consent Screen + Client ID)
- [ ] `image_fetch.py` — sahne notlarından görsel sorgusu çıkarıp
      Pexels + Wikimedia'dan görsel indiren modül
- [ ] `youtube_montaj.py` — ffmpeg ile Ken Burns efektli video montajı
- [ ] `youtube_upload.py` — otomatik YouTube yükleme
- [ ] `pipeline.py` — tüm adımları sırayla çalıştıran orkestratör
- [ ] `.github/workflows/daily.yml` — günlük cron ile otomatik tetikleme

---

## Kullanılan Teknolojiler

- **Python 3.10+**
- **Google Cloud Text-to-Speech** — seslendirme (tr-TR-Standard-A sesi)
- **Pexels API + Wikimedia Commons** — görsel kaynakları
- **ffmpeg** — video montaj ve ses birleştirme
- **YouTube Data API v3** — otomatik video yükleme
- **GitHub Actions** — tüm pipeline'ın çalıştığı bulut ortamı (cron ile
  günlük tetikleme)

## Gerekli Secrets (Settings → Secrets and variables → Actions)

| Secret adı                 | Açıklama                                      | Durum |
|-----------------------------|------------------------------------------------|-------|
| `GOOGLE_CREDENTIALS_JSON`   | TTS service account key (JSON içeriği)         | ✅ Eklendi |
| `YOUTUBE_CLIENT_SECRET`     | YouTube OAuth client secret (JSON içeriği)     | ⏳ Bekliyor |
| `PEXELS_API_KEY`            | Pexels görsel arama API anahtarı               | ⏳ Bekliyor |

---

## Video Formatı

- Çözünürlük: 1920x1080 (yatay, klasik YouTube videosu)
- FPS: 30
- Süre: video başına ortalama 5-6 dakika seslendirme (~570-680 kelime)
- Yayın sıklığı: günde 1 video, 30 gün boyunca

---

*Bu proje Claude (Anthropic) yardımıyla, telefon üzerinden GitHub web
arayüzü kullanılarak geliştirilmektedir — bilgisayar kullanılmamıştır.*

