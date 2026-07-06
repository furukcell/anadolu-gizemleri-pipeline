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
content/raw_audio/NN.mp3  (kullanıcı ses kaydı yükler)
        │
        ▼  [push tetikler]
.github/workflows/generate_and_upload.yml
        (yazıldı ✅)  → GitHub Actions'ı otomatik başlatır, gün
                         numarasını değişen dosyadan tespit eder
        ▼
pipeline.py             → tüm adımları sırayla çalıştıran orkestratör
        (yazıldı ✅)
        │
        ├─ script_parse.py         → TR bölümünü çeker, sahne notu +
        │    (yazıldı ✅)             anlatım metnini ayırır, script_parsed.json üretir
        │
        ├─ voice_postprocess.py    → Kullanıcının kendi ham ses kaydını
        │    (yazıldı ✅)             ffmpeg ile temiz + tok + net belgesel
        │                             tonuna çevirir, voiceover.mp3 üretir
        │                             (alternatif: google_tts_generate.py ile
        │                             Google Cloud TTS de hazır durumda)
        │
        ├─ image_fetch.py          → HİBRİT modda çalışır: sahne notundan
        │    (yazıldı ✅)             sorgu çıkarır, önce Wikimedia'da gerçek/
        │                             tarihi foto arar, bulamazsa Pexels'ten
        │                             atmosferik video klip, o da yoksa Pexels
        │                             foto dener. images_manifest.json üretir.
        │
        ├─ youtube_montaj.py       → ffmpeg ile foto sahnelerini Ken Burns
        │    (yazıldı ✅)             efektiyle, video sahnelerini trim/loop
        │                             ile işler, gerçek ses süresine göre
        │                             ölçekler, video_XX.mp4 üretir
        │
        └─ youtube_upload.py       → YouTube Data API v3 ile videoyu otomatik
             (yazıldı ✅)             yükler (OAuth refresh token ile),
                                       uploaded.json'a kaydeder
```

---

## Dosya Yapısı

```
anadolu-gizemleri-pipeline/
├── config.py                  # Tüm ayarların tek merkezi
├── pipeline.py                 # Tüm adımları sırayla çalıştıran orkestratör
├── script_parse.py            # MD dosyalarını okuyup ayrıştırır
├── voice_postprocess.py       # Kullanıcı ses kaydını işleyen modül
├── google_tts_generate.py     # Seslendirme modülü (TTS alternatifi)
├── image_fetch.py             # Hibrit foto/video toplama modülü
├── youtube_montaj.py          # Ken Burns + video montaj modülü
├── youtube_upload.py          # YouTube'a otomatik yükleme modülü
├── requirements.txt           # Python bağımlılıkları
├── .github/workflows/
│   └── generate_and_upload.yml  # Otomatik/manuel tetiklenen pipeline workflow'u
├── content/                   # 30 adet hazır senaryo (.md, TR+EN)
│   ├── 01_gobeklitepe_karahantepe_senaryo_tr_en_UTF8.md
│   ├── 02_catalhoyuk_senaryo_tr_en_UTF8.md
│   ├── ... (30 dosya)
│   └── raw_audio/              # Kullanıcının yüklediği ham ses kayıtları (NN.mp3)
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
- [x] `google_tts_generate.py` yazıldı — TTS alternatifi olarak hazır
      duruyor
- [x] Google Cloud projesi (`usta-mugla`) üzerinde:
  - Text-to-Speech API etkinleştirildi
  - YouTube Data API v3 etkinleştirildi
  - `tts-bot` service account oluşturuldu, JSON key üretildi
- [x] **Karar:** TTS yerine kullanıcının kendi sesi kullanılacak
- [x] `voice_postprocess.py` yazıldı ve test edildi — ffmpeg ile "temiz
      stüdyo + tok + net" belgesel tonu (highpass, noise reduction,
      compressor, hedefli EQ, loudnorm — echo/pitch değişikliği YOK)
- [x] `image_fetch.py` yazıldı — **hibrit mod**: Wikimedia'dan gerçek
      foto, bulunamazsa Pexels'ten atmosferik video klip, o da yoksa
      Pexels foto. Fallback zinciri sayesinde hiçbir sahne medyasız
      kalmıyor.
- [x] `youtube_montaj.py` yazıldı — Ken Burns (foto) + trim/loop (video)
      sahnelerini gerçek ses süresine göre ölçekleyip birleştiriyor,
      voiceover + opsiyonel arka müzik mix ediliyor
- [x] YouTube OAuth kurulumu tamamlandı:
  - OAuth Consent Screen (External, `youtube.upload` scope, test user:
    `destek.fkdigital@gmail.com`)
  - Web application tipi OAuth Client oluşturuldu
  - OAuth Playground üzerinden bir kerelik yetkilendirme yapıldı,
    kalıcı **refresh token** üretildi
- [x] `youtube_upload.py` yazıldı — resumable upload, geçici hata
      durumunda otomatik tekrar deneme, `uploaded.json` ile tekrar
      yükleme koruması
- [x] `pipeline.py` yazıldı — tüm adımları (ses kaynağı otomatik seçimi
      dahil) sırayla çalıştırıyor, `--no-upload` ile güvenli test modu var
- [x] `.github/workflows/generate_and_upload.yml` yazıldı — `raw_audio/*.mp3`
      push edilince otomatik, ya da Actions sekmesinden manuel tetiklenebilir;
      `uploaded.json`'ı otomatik commit edip repoya geri yazıyor
- [x] `PEXELS_API_KEY`, `GOOGLE_CREDENTIALS_JSON`, `YOUTUBE_CLIENT_SECRET`
      (client_id + client_secret + refresh_token içeren JSON) GitHub
      Secrets'a eklendi
- [x] Güvenlik önlemi: `config.YOUTUBE_PRIVACY_STATUS = "private"` yapıldı
      — ilk testler bitmeden gerçek yayına düşmesin

## Sırada Ne Var

- [ ] **İlk uçtan uca test:** `content/raw_audio/01.mp3` yüklenip pipeline'ın
      tamamının doğru çalıştığının teyit edilmesi (private videoyla)
- [ ] Test başarılı olursa `config.YOUTUBE_PRIVACY_STATUS` değerinin
      `"public"` yapılması
- [ ] Kalan 29 gün için ses kayıtlarının sırayla yüklenmesi

---

## Kullanılan Teknolojiler

- **Python 3.10+**
- **Kullanıcının kendi sesi** (ffmpeg ile post-prodüksiyon) — birincil
  seslendirme yöntemi; **Google Cloud Text-to-Speech** (tr-TR-Wavenet-B)
  yedek/alternatif olarak hazır duruyor
- **Wikimedia Commons + Pexels (Foto + Video API)** — hibrit görsel/video
  kaynakları
- **ffmpeg** — ses işleme, Ken Burns efekti, video montaj, senkronizasyon
- **YouTube Data API v3** — OAuth refresh token ile otomatik video yükleme
- **GitHub Actions** — tüm pipeline'ın çalıştığı bulut ortamı

## Gerekli Secrets (Settings → Secrets and variables → Actions)

| Secret adı                 | Açıklama                                              | Durum |
|-----------------------------|--------------------------------------------------------|-------|
| `GOOGLE_CREDENTIALS_JSON`   | TTS service account key (JSON içeriği)                 | ✅ Eklendi |
| `PEXELS_API_KEY`            | Pexels foto/video arama API anahtarı                   | ✅ Eklendi |
| `YOUTUBE_CLIENT_SECRET`     | client_id + client_secret + refresh_token (JSON)       | ✅ Eklendi |

## Workflow Nasıl Tetiklenir

**Otomatik:** `content/raw_audio/` klasörüne `NN.mp3` formatında bir dosya
yükleyip commit edince (örn. `03.mp3`), workflow otomatik başlar ve o günün
videosunu üretip yükler.

**Manuel:** GitHub → **Actions** sekmesi → "Anadolu Gizemleri - Video Uret
ve Yukle" → **Run workflow** → gün numarasını yaz → çalıştır.

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
