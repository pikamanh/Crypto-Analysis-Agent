"""Google Drive upload via OAuth (refresh token) — used by the weekly backup.

Scope is drive.file: the app can only see/manage files it creates itself,
not the rest of the user's Drive. Refresh token is obtained once via
scripts/get_drive_refresh_token.py and never needs to be regenerated unless
revoked.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_DRIVE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_DRIVE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_DRIVE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def upload_file(local_path: Path, drive_filename: str) -> str:
    """Uploads a file and returns its Drive file id."""
    service = _get_service()
    metadata = {"name": drive_filename}
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if folder_id:
        metadata["parents"] = [folder_id]
    media = MediaFileUpload(str(local_path), mimetype="application/octet-stream", resumable=True)
    file = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return file["id"]


def get_file_size(file_id: str) -> int:
    service = _get_service()
    meta = service.files().get(fileId=file_id, fields="size").execute()
    return int(meta["size"])
