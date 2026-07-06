"""
youtube_upload.py
------------------
youtube_montaj.py'nin urettigi video_NN.mp4 dosyasini YouTube Data API v3 ile yukler.

FORCE_REUPLOAD=true verilirse state/uploaded.json icinde gun kayitli olsa bile
yeni video yeniden yuklenir. Bu, ayni gunun kurgu kalitesini test ederken lazim.
"""

import json
import os
import time

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

import config

TOKEN_URI = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
MAX_RETRIES = 5
RETRY_SLEEP_SECONDS = 10


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def load_credentials() -> Credentials:
    raw = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not raw:
        raise EnvironmentError(
            "YOUTUBE_CLIENT_SECRET ortam degiskeni bulunamadi. "
            "GitHub Secrets'ta tanimli oldugundan emin ol."
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
    creds.refresh(Request())
    return creds


def build_video_metadata(day: int, title: str) -> dict:
    full_title = config.YOUTUBE_TITLE_TEMPLATE.format(title=title, day=day)
    full_description = config.YOUTUBE_DESCRIPTION_TEMPLATE.format(title=title, day=day)
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


def load_uploaded_log() -> dict:
    if config.UPLOADED_LOG.exists():
        return json.loads(config.UPLOADED_LOG.read_text(encoding="utf-8"))
    return {}


def save_uploaded_log(log: dict):
    config.UPLOADED_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def already_uploaded(day: int) -> bool:
    return str(day) in load_uploaded_log()


def mark_as_uploaded(day: int, video_id: str, title: str):
    log = load_uploaded_log()
    log[str(day)] = {
        "video_id": video_id,
        "title": title,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_uploaded_log(log)


def upload_video(day: int) -> str:
    force_reupload = _truthy_env("FORCE_REUPLOAD")

    if already_uploaded(day) and not force_reupload:
        log = load_uploaded_log()
        existing = log[str(day)]
        print(f"[youtube_upload] Gun {day} zaten yuklenmis -> {existing['youtube_url']}")
        print("[youtube_upload] Yeniden yuklemek icin workflow'da force_upload=true sec.")
        return existing["video_id"]

    if already_uploaded(day) and force_reupload:
        print(f"[youtube_upload] Gun {day} daha once yuklenmis ama FORCE_REUPLOAD=true, yeni video yuklenecek.")

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
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=UPLOAD_CHUNK_SIZE, resumable=True)

    request = youtube.videos().insert(part="snippet,status", body=metadata, media_body=media)

    response = None
    retry_count = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"[youtube_upload] Gun {day}: %{int(status.progress() * 100)} yuklendi")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retry_count < MAX_RETRIES:
                retry_count += 1
                print(f"[youtube_upload] Gecici hata (HTTP {e.resp.status}), {RETRY_SLEEP_SECONDS}s sonra tekrar denenecek ({retry_count}/{MAX_RETRIES})")
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
        upload_video(int(sys.argv[1]))
    else:
        print("Kullanim: python youtube_upload.py <gun_numarasi>")
        print("Ornek: python youtube_upload.py 1")
