import os
import pickle
import pandas as pd
import logging

import gspread
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

class GoogleSheets:
    def __init__(self, sheet_id: str = None, credentials_path: str = None):
        self.sheet_id = sheet_id or "1wTTuxXt8n9q7C4NDXqQpI3wpKu1_5bGVmP9Xz0XGSyU"
        self.scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        self.credentials_path = credentials_path or "credentials.json"
        self.creds = None

    def _login(self):
        flow = InstalledAppFlow.from_client_secrets_file(
            self.credentials_path,
            self.scopes
        )
        self.creds = flow.run_local_server(port=0)

        with open("token.pickle", "wb") as token:
            pickle.dump(self.creds, token)

    def _authorize(self):
        if os.path.exists("token.pickle"):
            with open("token.pickle", "rb") as token:
                self.creds = pickle.load(token)

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                self._login()

        gc = gspread.authorize(self.creds)

        return gc

    def get_coin_id(self) -> pd.DataFrame:
        gc = self._authorize()
        spreadsheet = gc.open_by_key(self.sheet_id)

        sheets = spreadsheet.worksheets()
        for sheet in sheets:
            if "coin" in sheet.title.lower():
                df = pd.DataFrame(sheet.get_all_records(head=2))
                df.rename(columns={
                    "Coin Id (API id)": "ID"
                }, inplace=True)

                logger.info("Completed get ID Token.")
                return df

if __name__ == "__main__":
    gg = GoogleSheets()
    df = gg.get_coin_id()