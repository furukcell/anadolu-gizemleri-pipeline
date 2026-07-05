# Anadolu Gizemleri — Otomatik YouTube Video Pipeline

Bu repo, **Anadolu Gizemleri** YouTube kanalı için 7-8 dakikalık belgesel
tarzı videoları tamamen otomatik üreten bir pipeline'dır. Sistem, hazır
senaryolardan başlayarak seslendirme, görsel/video toplama, video montajı
ve YouTube'a yükleme adımlarının tamamını insan müdahalesi olmadan yapar.

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
script_parse.py         → TR bölümünü çeker, sahne notu + anlatım
        │                  metnini ayırır, script_parsed.json üretir
        ▼
[SES: kullanıcı kaydı]  → Kullanıcı kendi sesiyle anlatımı okur, ham
        │                  kaydı yükler (voice_postprocess.py henüz
        │                  yazılmadı — ffmpeg zinciri test edildi ama
        │                  modül haline getirilmedi)
        │                  (alternatif: google_tts_generate.py ile
        │                  Google Cloud TTS de hazır durumda)
        ▼
image_fetch.py          → HİBRİT modda çalışır: sahne notundan sorgu
        (yazıldı ✅)       çıkarır, önce Wikimedia'da gerçek/tarihi foto
                           arar, bulamazsa Pexels'ten atmosferik video
                           klip, o da yoksa Pexels foto dener. Hiçbir
                           sahne medyasız kalmaz. images_manifest.json
                           üretir.
        ▼
youtube_montaj.py       → ffmpeg ile foto sahnelerini Ken Burns
        (yazıldı ✅)       efektiyle, video sahnelerini trim/loop ile
                           işler, gerçek ses süresine göre sahne
                           sürelerini yeniden ölçeklendirir, sesle
                           senkronlar, video_XX.mp4 üretir
        ▼
youtube_upload.py       → YouTube Data API v3 ile videoyu otomatik yükler
   (henüz yazılmadı)      (OAuth kurulumu devam ediyor)
        ▼
pipeline.py             → tüm adımları sırayla çalıştıran orkestratör
   (henüz yazılmadı)
        ▼
GitHub Actions workflow → kullanıcı ham ses kaydını yükleyince (veya
   (henüz kurulmadı)       cron ile) tetiklenir, o günün videosu işlenir
```

---

## Dosya Yapısı

```
anadolu-gizemleri-pipeline/
├── config.py                  # Tüm ayarların tek merkezi
├── script_parse.py            # MD dosyalarını okuyup ayrıştırır
├── google_tts_generate.py     # Seslendirme modülü (TTS alternatifi)
├── image_fetch.py             # Hibrit foto/video toplama modülü
├── youtube_montaj.py          # Ken Burns + video montaj modülü
├── requirements.txt           # Python bağımlılıkları
├── content/                   # 30 adet hazır senaryo (.md, TR+EN)
│   ├── 01_gobeklitepe_karahantepe_senaryo_tr_en_UTF8.md
│   ├── 02_catalhoyuk_senaryo_tr_en_UTF8.md
│   └── ... (30 dosya)
├── output/                    # Üretilen dosyalar (script/ses/medya/video)
│   └── video_NN/
│       ├── script_parsed.json
│       ├── voiceover.mp3
│       ├── images_manifest.json
│       ├── media/              # indirilen foto (.jpg) / video (.mp4)
│       └── video_NN.mp4
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
- [x] **Karar:** TTS yerine kullanıcının kendi sesi kullanılacak —
      ffmpeg ile "temiz stüdyo + tok + net" belgesel tonu için ses
      işleme zinciri test edildi ve onaylandı (highpass, noise reduction,
      compressor, hedefli EQ, loudnorm — echo/pitch değişikliği YOK)
- [x] `image_fetch.py` yazıldı — **hibrit mod**: Wikimedia'dan gerçek
      foto, bulunamazsa Pexels'ten atmosferik video klip, o da yoksa
      Pexels foto. Fallback zinciri sayesinde hiçbir sahne medyasız
      kalmıyor.
- [x] `PEXELS_API_KEY` alındı ve GitHub Secrets'a eklendi
- [x] `youtube_montaj.py` yazıldı — Ken Burns (foto) + trim/loop (video)
      sahnelerini gerçek ses süresine göre ölçekleyip birleştiriyor,
      voiceover + opsiyonel arka müzik mix ediliyor
- [x] YouTube Data API v3, Google Cloud projesinde etkinleştirildi

## Sırada Ne Var

- [ ] `voice_postprocess.py` — test edilen ffmpeg ses zincirini modül
      haline getirmek (kullanıcının ham kaydını otomatik işleyecek)
- [ ] YouTube OAuth kurulumu:
  - [ ] OAuth Consent Screen (External, `youtube.upload` scope, test user)
  - [ ] OAuth Client ID (Desktop app tipi)
  - [ ] Refresh token üretimi (bir kerelik yetkilendirme)
- [ ] `youtube_upload.py` — otomatik YouTube yükleme
- [ ] `pipeline.py` — tüm adımları sırayla çalıştıran orkestratör
- [ ] `.github/workflows/` — kullanıcı ses kaydı yükleyince (veya cron
      ile) otomatik tetikleme

---

## Kullanılan Teknolojiler

- **Python 3.10+**
- **Kullanıcının kendi sesi** (ffmpeg ile post-prodüksiyon) — birincil
  seslendirme yöntemi; **Google Cloud Text-to-Speech** (tr-TR-Wavenet-B)
  yedek/alternatif olarak hazır duruyor
- **Wikimedia Commons + Pexels (Foto + Video API)** — hibrit görsel/video
  kaynakları
- **ffmpeg** — ses işleme, Ken Burns efekti, video montaj, senkronizasyon
- **YouTube Data API v3** — otomatik video yükleme
- **GitHub Actions** — tüm pipeline'ın çalıştığı bulut ortamı

## Gerekli Secrets (Settings → Secrets and variables → Actions)

| Secret adı                 | Açıklama                                      | Durum |
|-----------------------------|------------------------------------------------|-------|
| `GOOGLE_CREDENTIALS_JSON`   | TTS service account key (JSON içeriği)         | ✅ Eklendi |
| `PEXELS_API_KEY`            | Pexels foto/video arama API anahtarı           | ✅ Eklendi |
| `YOUTUBE_CLIENT_SECRET`     | YouTube OAuth client secret (JSON içeriği)     | ⏳ Bekliyor |

---

## Video Formatı

- Çözünürlük: 1920x1080 (yatay, klasik YouTube videosu)
- FPS: 30
- Süre: video başına ortalama 5-6 dakika seslendirme (~570-680 kelime)
- Görsel/video karışımı: sahneye göre hibrit (gerçek tarihi foto +
  atmosferik video klip)
- Yayın sıklığı: günde 1 video, 30 gün boyunca

---

*Bu proje Claude (Anthropic) yardımıyla, telefon üzerinden GitHub web
arayüzü kullanılarak geliştirilmektedir — bilgisayar kullanılmamıştır.*
