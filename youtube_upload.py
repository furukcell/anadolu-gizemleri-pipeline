"""
youtube_upload.py
------------------
youtube_montaj.py'nin urettigi video_NN.mp4 dosyasini YouTube Data API v3
ile otomatik olarak kanala yukler.

Kimlik dogrulama, GitHub Secrets'taki YOUTUBE_CLIENT_SECRET degiskeninden
okunur. Bu deger, su formatta bir JSON STRING olmalidir:

  {
    "client_id": "....apps.googleusercontent.com",
    "client_secret": "GOCSPX-....",
    "refresh_token": "1//...."
  }

Bu JSON, bir defaya mahsus OAuth Playground uzerinden uretildi (refresh
token suresiz gecerlidir, her calistirmada yeni access token bu refresh
token'dan otomatik uretilir - kullanicinin tekrar giris yapmasina gerek
yoktur).

Baslik/aciklama/etiketler config.py'daki sablonlardan doldurulur.
Basariyla yuklenen videolar state/uploaded.json'a kaydedilir, boylece
ayni gun/video iki kez yuklenmez.

Kullanim:
  python youtube_upload.py <gun_numarasi>
"""

import json
import os
import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

import config

TOKEN_URI = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]

# Google'in resumable upload'da onerdigi parca boyutu (8MB) - kucuk
# parcalar halinde yuklenir, GitHub Actions'in sinirli bant genisligine
# ve olasi kesintilere karsi daha dayanikli olur.
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024

MAX_RETRIES = 5
RETRY_SLEEP_SECONDS = 10


# =========================================================
# KIMLIK DOGRULAMA
# =========================================================
def load_credentials() -> Credentials:
    """
    YOUTUBE_CLIENT_SECRET ortam degiskenini okur, JSON olarak parse eder
    ve google-auth Credentials nesnesi olusturur. Credentials nesnesi
    kullanildiginda gerekirse access token'i otomatik yeniler (refresh_token
    sayesinde), yani her calistirmada manuel yenileme gerekmez.
    """
    raw = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not raw:
        raise EnvironmentError(
            "YOUTUBE_CLIENT_SECRET ortam degiskeni bulunamadi. "
            "GitHub Secrets'ta tanimli oldugundan ve workflow dosyasinda "
            "env olarak gecirildiginden emin ol."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            "YOUTUBE_CLIENT_SECRET gecerli bir JSON degil. "
            "Beklenen format: {'client_id':..., 'client_secret':..., 'refresh_token':...}"
        ) from e

    required_keys = ("client_id", "client_secret", "refresh_token")
    missing = [k for k in required_keys if not data.get(k)]
    if missing:
        raise ValueError(f"YOUTUBE_CLIENT_SECRET icinde eksik alan(lar): {missing}")

    creds = Credentials(
        token=None,
        refresh_token=data["refresh_token"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        token_uri=TOKEN_URI,
        scopes=YOUTUBE_UPLOAD_SCOPE,
    )

    # Ilk access token'i hemen uret (video yuklemeden once gecerli olsun)
    creds.refresh(Request())
    return creds


# =========================================================
# METADATA HAZIRLAMA
# =========================================================
def build_video_metadata(day: int, title: str) -> dict:
    """config.py'daki sablonlari kullanarak YouTube video metadata'sini hazirlar."""
    full_title = config.YOUTUBE_TITLE_TEMPLATE.format(title=title, day=day)
    full_description = config.YOUTUBE_DESCRIPTION_TEMPLATE.format(title=title, day=day)

    # YouTube baslik siniri 100 karakter - asarsa kirp
    if len(full_title) > 100:
        full_title = full_title[:97] + "..."

    return {
        "snippet": {
            "title": full_title,
            "description": full_description,
            "tags": config.YOUTUBE_TAGS,
            "categoryId": config.YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.YOUTUBE_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }


# =========================================================
# UPLOADED LOG (tekrar yuklemeyi onlemek icin)
# =========================================================
def load_uploaded_log() -> dict:
    if config.UPLOADED_LOG.exists():
        return json.loads(config.UPLOADED_LOG.read_text(encoding="utf-8"))
    return {}


def save_uploaded_log(log: dict):
    config.UPLOADED_LOG.write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def already_uploaded(day: int) -> bool:
    log = load_uploaded_log()
    return str(day) in log


def mark_as_uploaded(day: int, video_id: str, title: str):
    log = load_uploaded_log()
    log[str(day)] = {
        "video_id": video_id,
        "title": title,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_uploaded_log(log)


# =========================================================
# YUKLEME (resumable, retry destekli)
# =========================================================
def upload_video(day: int) -> str:
    if already_uploaded(day):
        log = load_uploaded_log()
        existing = log[str(day)]
        print(f"[youtube_upload] Gun {day} zaten yuklenmis -> {existing['youtube_url']}")
        return existing["video_id"]

    video_dir = config.OUTPUT_DIR / f"video_{day:02d}"
    video_path = video_dir / f"video_{day:02d}.mp4"
    parsed_path = video_dir / "script_parsed.json"

    if not video_path.exists():
        raise FileNotFoundError(f"Video dosyasi bulunamadi: {video_path}")
    if not parsed_path.exists():
        raise FileNotFoundError(f"script_parsed.json bulunamadi: {parsed_path}")

    title = json.loads(parsed_path.read_text(encoding="utf-8"))["title"]
    metadata = build_video_metadata(day, title)

    print(f"[youtube_upload] Gun {day} icin yukleme basliyor: '{metadata['snippet']['title']}'")

    creds = load_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=UPLOAD_CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=metadata,
        media_body=media,
    )

    response = None
    retry_count = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"[youtube_upload] Gun {day}: %{progress} yuklendi")
        except HttpError as e:
            # 500/502/503/504 gibi gecici sunucu hatalarinda tekrar dene
            if e.resp.status in (500, 502, 503, 504) and retry_count < MAX_RETRIES:
                retry_count += 1
                print(f"[youtube_upload] Gecici hata (HTTP {e.resp.status}), "
                      f"{RETRY_SLEEP_SECONDS}s sonra tekrar denenecek "
                      f"({retry_count}/{MAX_RETRIES})")
                time.sleep(RETRY_SLEEP_SECONDS)
                continue
            raise RuntimeError(f"YouTube yuklemesi basarisiz oldu: {e}") from e

    video_id = response["id"]
    mark_as_uploaded(day, video_id, title)

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"[youtube_upload] Gun {day} basariyla yuklendi -> {youtube_url}")
    return video_id


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        gun = int(sys.argv[1])
        upload_video(gun)
    else:
        print("Kullanim: python youtube_upload.py <gun_numarasi>")
        print("Ornek: python youtube_upload.py 1")
      
