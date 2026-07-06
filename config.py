"""
config.py
---------
Anadolu Gizemleri video pipeline'inin tek merkezi ayar dosyasi.
Diger tum moduller ayarlari buradan import eder. Hicbir modulde
hardcode deger olmamali - degistirmek istedigin her sey burada.
"""

import os
from pathlib import Path

# =========================================================
# KLASOR YAPISI
# =========================================================
BASE_DIR = Path(__file__).resolve().parent

CONTENT_DIR = BASE_DIR / "content"        # 30 adet .md senaryo dosyasi burada duracak
OUTPUT_DIR = BASE_DIR / "output"          # uretilen video_XX klasorleri burada olacak
STATE_DIR = BASE_DIR / "state"            # uploaded.json, ilerleme takibi
ASSETS_DIR = BASE_DIR / "assets"          # muzik, intro/outro gibi sabit dosyalar (opsiyonel)

for _d in (CONTENT_DIR, OUTPUT_DIR, STATE_DIR, ASSETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

UPLOADED_LOG = STATE_DIR / "uploaded.json"

# =========================================================
# DIL / ICERIK AYARLARI
# =========================================================
# Sadece Turkce kanal calisiyoruz. .md dosyalarinda hem TR hem EN var,
# script_parse.py sadece "# TURKCE SENARYO" ile "# ENGLISH SCRIPT"
# basliklari arasindaki kismi cekecek.
LANGUAGE = "tr"
TR_SECTION_MARKER = "# TÜRKÇE SENARYO"
EN_SECTION_MARKER = "# ENGLISH SCRIPT"

# =========================================================
# SESLENDIRME (Google Cloud Text-to-Speech)
# =========================================================
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS", "google_credentials.json"
)
GOOGLE_TTS_LANGUAGE_CODE = "tr-TR"
# WaveNet: Standard'dan cok daha dogal/sicak, VE ucretsiz kotaya sahip
# (Google Cloud TTS'te WaveNet sesleri ayda 1 milyon karaktere kadar ucretsiz).
# 30 videoluk serimiz toplam ~110.000 karakter - kotanin cok altinda, ucret yok.
# tr-TR-Wavenet-B: erkek, derin ton - belgesel/gizem anlatimina uygun
GOOGLE_TTS_VOICE_NAME = "tr-TR-Wavenet-B"
GOOGLE_TTS_SPEAKING_RATE = 0.97   # belgesel/gizem tonu icin hafif yavas
GOOGLE_TTS_PITCH = -1.5           # hafif kalin/ciddi ton

# =========================================================
# GORSELLER
# =========================================================
# "mixed" -> once Wikimedia Commons'ta gercek yer gorseli ara, bulamazsa Pexels'e dus.
# "pexels_only" / "wikimedia_only" test amacli kullanilabilir.
IMAGE_PROVIDER = "mixed"
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

VISUAL_MODE = "photo"   # 7-8 dk'lik belgeselde agirlikli statik foto + Ken Burns efekti
KEN_BURNS_ENABLED = True
KEN_BURNS_ZOOM_RATIO = 1.15   # sahne suresince %15 zoom

# =========================================================
# VIDEO / MONTAJ AYARLARI
# =========================================================
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
VIDEO_FORMAT = "landscape"   # yatay, klasik YouTube videosu

# Sahne basina hedeflenen sure araligi (saniye) - cok kisa/uzun sahneleri dengelemek icin
MIN_SCENE_SECONDS = 4.0
MAX_SCENE_SECONDS = 14.0

BACKGROUND_MUSIC_PATH = ASSETS_DIR / "background_music.mp3"  # opsiyonel, yoksa sessiz gecer
BACKGROUND_MUSIC_VOLUME = 0.12  # anlatimin uzerine hafif altmuzik

# =========================================================
# YOUTUBE UPLOAD
# =========================================================
YOUTUBE_CATEGORY_ID = "27"   # Education (Truva, Hattusas vb. icin uygun)
YOUTUBE_PRIVACY_STATUS = "private"   # "private" / "unlisted" ile test edebilirsin
YOUTUBE_CLIENT_SECRET_FILE = os.environ.get(
    "YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json"
)
YOUTUBE_TOKEN_FILE = STATE_DIR / "youtube_token.json"

# Baslik/aciklama sablonlari - {title} ve {day} otomatik doldurulur
YOUTUBE_TITLE_TEMPLATE = "{title} | Anadolu Gizemleri #{day}"
YOUTUBE_DESCRIPTION_TEMPLATE = (
    "Anadolu'nun en gizemli ve tarihi yerlerinden biri: {title}\n\n"
    "Bu seri, Anadolu topraklarindaki arkeolojik ve tarihi gizemleri "
    "belgesel formatinda anlatiyor.\n\n"
    "#anadolugizemleri #tarih #arkeoloji"
)
YOUTUBE_TAGS = ["anadolu", "gizem", "tarih", "arkeoloji", "belgesel", "türkiye"]

# =========================================================
# ZAMANLAMA (GitHub Actions cron ile eslesecek)
# =========================================================
# batch_run.py / pipeline.py bu numaradan devam eder.
# "gun" = content klasorundeki dosya numarasi (01, 02, ... 30)
DAILY_UPLOAD_COUNT = 1   # her calistirmada kac video islensin/yuklensin

# =========================================================
# GENEL
# =========================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # gorsel sorgu iyilestirme icin opsiyonel kullanilabilir
