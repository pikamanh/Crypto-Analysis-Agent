"""One-time script: run locally to authorize this app against your Google
Drive account and print a refresh token. Save that token as
GOOGLE_DRIVE_REFRESH_TOKEN (alongside the client id/secret) wherever the
backup job runs (GitHub Actions secrets, etc) — it never needs to run again
unless you revoke access.

Requires a Desktop-app OAuth Client ID/Secret from Google Cloud Console
(APIs & Services -> Credentials), passed via GOOGLE_DRIVE_CLIENT_ID /
GOOGLE_DRIVE_CLIENT_SECRET env vars (or .env).

Run: python scripts/get_drive_refresh_token.py
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main() -> None:
    client_config = {
        "installed": {
            "client_id": os.environ["GOOGLE_DRIVE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_DRIVE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    print("\nSave this as GOOGLE_DRIVE_REFRESH_TOKEN:\n")
    print(creds.refresh_token)


if __name__ == "__main__":
    main()
