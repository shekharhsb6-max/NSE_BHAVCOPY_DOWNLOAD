"""
fetch_bhavcopy.py
==================
Downloads NSE's daily equity Bhavcopy (the "UDiFF" full bhavcopy CSV,
NSE's current format since July 2024) and writes it into a tab of a
Google Sheet.

Designed to run unattended (e.g. via a GitHub Actions cron job):
  - Skips cleanly (exit code 0) on weekends and on days NSE hasn't
    published data for yet (holidays, or the file not being live yet) —
    it does NOT treat "no data today" as a failure.
  - Uses a warmed-up requests session with browser-like headers, because
    NSE blocks plain script requests without cookies from a prior visit
    to nseindia.com.
  - Writes to Google Sheets via a service account (no interactive OAuth
    needed — safe to run in CI).

ENVIRONMENT VARIABLES
  GOOGLE_SERVICE_ACCOUNT_JSON   Required. The full JSON key of a Google
                                 service account, as a string (see
                                 README.md for how to create one and
                                 share the sheet with it).
  SPREADSHEET_ID                 Optional. Defaults to the sheet linked
                                 below.
  SHEET_NAME                     Optional. Defaults to "RAW_DATA".
  BHAVCOPY_MODE                  Optional. "append" (default) adds each
                                 day's rows to the bottom of the sheet,
                                 building a running history. "overwrite"
                                 replaces the sheet's contents with just
                                 the latest day.
  BHAVCOPY_DATE                  Optional. Force a specific date
                                 (YYYY-MM-DD) instead of "today" — handy
                                 for backfilling or local testing.

Local run:
  pip install -r requirements.txt
  export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service_account.json)"
  python fetch_bhavcopy.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_SPREADSHEET_ID = "1D3E5lyH2QUq55AzsJqSbJj2tNkmOQht_8xUmt0mdvbk"
DEFAULT_SHEET_NAME = "RAW_DATA"
IST = ZoneInfo("Asia/Kolkata")

NSE_HOME_URL = "https://www.nseindia.com/"
# NSE's current ("UDiFF") full bhavcopy — one CSV covering all CM segments.
BHAVCOPY_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
}

# Columns worth keeping from the UDiFF file, and the header row we'll
# actually write to the sheet. Trimmed to what's useful for a watchlist/
# analysis sheet — add/remove names here if you want more or fewer columns.
COLUMN_MAP = {
    "TradDt": "TRADE_DATE",
    "TckrSymb": "SYMBOL",
    "SctySrs": "SERIES",
    "OpnPric": "OPEN",
    "HghPric": "HIGH",
    "LwPric": "LOW",
    "ClsPric": "CLOSE",
    "LastPric": "LAST",
    "PrvsClsgPric": "PREV_CLOSE",
    "TtlTradgVol": "TOTAL_TRADED_QTY",
    "TtlTrfVal": "TOTAL_TRADED_VALUE",
    "TtlNbOfTxsExctd": "TOTAL_TRADES",
    "ISIN": "ISIN",
}


# ---------------------------------------------------------------------------
# Date handling
# ---------------------------------------------------------------------------

def target_date() -> date:
    """The trading date to fetch: BHAVCOPY_DATE env var if set, else today
    (IST)."""
    override = os.environ.get("BHAVCOPY_DATE")
    if override:
        return datetime.strptime(override, "%Y-%m-%d").date()
    return datetime.now(IST).date()


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    """A session with cookies set from a prior visit to nseindia.com —
    required, or NSE returns 403 on the data endpoints."""
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    session.get(NSE_HOME_URL, timeout=15)  # sets cookies; response body unused
    return session


def download_bhavcopy(session: requests.Session, trade_date: date) -> pd.DataFrame | None:
    """Downloads and parses the bhavcopy zip for trade_date.
    Returns None (not an error) if NSE has no file for that date yet —
    that's the normal case for weekends/holidays."""
    url = BHAVCOPY_URL_TEMPLATE.format(yyyymmdd=trade_date.strftime("%Y%m%d"))
    resp = session.get(url, timeout=30)

    if resp.status_code == 404:
        print(f"No bhavcopy published for {trade_date} (404) — likely a "
              f"weekend/holiday, or today's file isn't live yet. Skipping.")
        return None
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"Zip for {trade_date} had no CSV inside: {zf.namelist()}")
        with zf.open(csv_names[0]) as f:
            df = pd.read_csv(f)

    return df


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def clean_bhavcopy(df: pd.DataFrame) -> pd.DataFrame:
    """Keeps only the columns we care about, renames them, and makes sure
    everything is a plain JSON-serialisable value (Sheets API chokes on
    numpy/pandas-native types)."""
    available = [c for c in COLUMN_MAP if c in df.columns]
    missing = [c for c in COLUMN_MAP if c not in df.columns]
    if missing:
        print(f"Note: columns not found in this file (skipped): {missing}")

    out = df[available].rename(columns=COLUMN_MAP)
    out = out.where(pd.notnull(out), None)  # NaN -> None -> blank cell
    return out


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def get_worksheet(spreadsheet_id: str, sheet_name: str):
    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. See README.md for how "
            "to create a service account key and pass it in (as a GitHub "
            "secret in CI, or an env var locally)."
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        print(f'"{sheet_name}" tab not found — creating it.')
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=max(20, 1))
    return ws


def write_to_sheet(ws, df: pd.DataFrame, mode: str) -> None:
    header = df.columns.tolist()
    rows = df.astype(object).values.tolist()

    if mode == "overwrite":
        ws.clear()
        ws.update([header] + rows, value_input_option="USER_ENTERED")
        print(f"Overwrote {ws.title} with {len(rows)} rows.")
        return

    # append mode: write the header only if the sheet is currently empty,
    # otherwise assume it's already there and just add rows underneath.
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(header, value_input_option="USER_ENTERED")
    elif first_row != header:
        print("Warning: existing header row doesn't match the columns "
              "this run produced — appending anyway, but double-check the "
              "sheet. (Existing: %s | This run: %s)" % (first_row, header))

    # append_rows batches in one API call rather than row-by-row.
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Appended {len(rows)} rows to {ws.title}.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    trade_date = target_date()

    if trade_date.weekday() >= 5:  # Saturday=5, Sunday=6
        print(f"{trade_date} is a weekend — NSE doesn't publish a bhavcopy. Skipping.")
        return 0

    spreadsheet_id = os.environ.get("SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID)
    sheet_name = os.environ.get("SHEET_NAME", DEFAULT_SHEET_NAME)
    mode = os.environ.get("BHAVCOPY_MODE", "append").lower()
    if mode not in ("append", "overwrite"):
        print(f'BHAVCOPY_MODE must be "append" or "overwrite", got "{mode}"', file=sys.stderr)
        return 1

    session = make_session()
    raw_df = download_bhavcopy(session, trade_date)
    if raw_df is None:
        return 0  # holiday/not-yet-published — not an error

    df = clean_bhavcopy(raw_df)
    if df.empty:
        print("Downloaded file parsed to zero usable rows — nothing to write.")
        return 0

    ws = get_worksheet(spreadsheet_id, sheet_name)
    write_to_sheet(ws, df, mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
