import os
import io
import json
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_SPREADSHEET_ID = "1D3E5lyH2QUq55AzsJqSbJj2tNkmOQht_8xUmt0mdvbk"
DEFAULT_SHEET_NAME = "RAW_DATA"

NSE_HOME = "https://www.nseindia.com/"
NSE_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# NSE SESSION
# ============================================================

def create_nse_session():

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_HOME,
        "Connection": "keep-alive",
    })

    # Warm up NSE session
    try:
        r = session.get(NSE_HOME, timeout=30)
        print(f"NSE home page status: {r.status_code}")
    except Exception as e:
        print(f"Warning: NSE home page request failed: {e}")

    return session


# ============================================================
# DOWNLOAD ONE BHAVCOPY
# ============================================================

def download_bhavcopy(session, date_obj):

    yyyymmdd = date_obj.strftime("%Y%m%d")

    url = NSE_BHAVCOPY_URL.format(
        yyyymmdd=yyyymmdd
    )

    print(f"Trying NSE bhavcopy: {date_obj.strftime('%Y-%m-%d')}")
    print(url)

    try:
        response = session.get(
            url,
            timeout=60
        )

        print(f"NSE response: HTTP {response.status_code}")

        if response.status_code == 404:
            print("Bhavcopy not available for this date.")
            return None

        if response.status_code != 200:
            print(
                f"NSE returned HTTP {response.status_code}"
            )
            return None

        if len(response.content) < 1000:
            print("Downloaded response is unexpectedly small.")
            return None

        print(
            f"Downloaded {len(response.content):,} bytes."
        )

        return response.content

    except requests.RequestException as e:

        print(
            f"Download error for "
            f"{date_obj.strftime('%Y-%m-%d')}: {e}"
        )

        return None


# ============================================================
# FIND LATEST AVAILABLE BHAVCOPY
# ============================================================

def find_latest_bhavcopy(session, start_date, max_days_back=15):

    current_date = start_date

    for i in range(max_days_back + 1):

        print(
            f"\nSearch {i + 1}/{max_days_back + 1}: "
            f"{current_date.strftime('%Y-%m-%d')}"
        )

        data = download_bhavcopy(
            session,
            current_date
        )

        if data is not None:

            print(
                "\nSUCCESS!"
            )

            print(
                "Latest available NSE bhavcopy: "
                f"{current_date.strftime('%Y-%m-%d')}"
            )

            return current_date, data

        current_date -= timedelta(days=1)

    raise RuntimeError(
        f"No NSE bhavcopy found in the last "
        f"{max_days_back + 1} calendar days."
    )


# ============================================================
# READ ZIP / CSV
# ============================================================

def read_bhavcopy(zip_bytes):

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:

        files = z.namelist()

        print("Files inside ZIP:")
        for f in files:
            print(f"  {f}")

        csv_files = [
            f for f in files
            if f.lower().endswith(".csv")
        ]

        if not csv_files:
            raise RuntimeError(
                "No CSV file found inside NSE ZIP."
            )

        csv_name = csv_files[0]

        print(
            f"Reading CSV: {csv_name}"
        )

        with z.open(csv_name) as f:
            df = pd.read_csv(f)

    print(
        f"Raw NSE rows: {len(df):,}"
    )

    print(
        "Raw NSE columns:"
    )

    print(
        list(df.columns)
    )

    return df


# ============================================================
# COLUMN FINDER
# ============================================================

def find_column(df, possible_names):

    for name in possible_names:

        if name in df.columns:
            return name

    return None


# ============================================================
# CLEAN NSE UDIF BHAVCOPY
# ============================================================

def clean_bhavcopy(df, bhavcopy_date):

    # --------------------------------------------------------
    # Locate NSE UDiFF columns
    # --------------------------------------------------------

    columns = {

        "TRADE_DATE": [
            "TradDt",
            "TradeDate",
            "TRADE_DATE"
        ],

        "SYMBOL": [
            "TckrSymb",
            "Symbol",
            "SYMBOL"
        ],

        "SERIES": [
            "SctySrs",
            "Series",
            "SERIES"
        ],

        "OPEN": [
            "OpnPric",
            "OPEN"
        ],

        "HIGH": [
            "HghPric",
            "HIGH"
        ],

        "LOW": [
            "LwPric",
            "LOW"
        ],

        "CLOSE": [
            "ClsPric",
            "CLOSE"
        ],

        "LAST": [
            "LastPric",
            "LAST"
        ],

        "PREV_CLOSE": [
            "PrvsClsgPric",
            "PREV_CLOSE"
        ],

        "TOTAL_TRADED_QTY": [
            "TtlTradgVol",
            "TOTAL_TRADED_QTY"
        ],

        "TOTAL_TRADED_VALUE": [
            "TtlTrfVal",
            "TOTAL_TRADED_VALUE"
        ],

        "TOTAL_TRADES": [
            "TtlNbOfTxsExctd",
            "TOTAL_TRADES"
        ],

        "ISIN": [
            "ISIN",
            "ISINCode"
        ],
    }

    result = pd.DataFrame()

    missing = []

    for output_name, possible_names in columns.items():

        source_column = find_column(
            df,
            possible_names
        )

        if source_column is None:

            missing.append(output_name)

        else:

            result[output_name] = df[source_column]

    # --------------------------------------------------------
    # If critical columns missing, stop clearly
    # --------------------------------------------------------

    if missing:

        raise RuntimeError(
            "Could not find required NSE columns: "
            + ", ".join(missing)
            + "\n\nAvailable columns are:\n"
            + ", ".join(map(str, df.columns))
        )

    # --------------------------------------------------------
    # Normalize date
    # --------------------------------------------------------

    result["TRADE_DATE"] = bhavcopy_date.strftime(
        "%Y-%m-%d"
    )

    # --------------------------------------------------------
    # Remove blank symbols
    # --------------------------------------------------------

    result["SYMBOL"] = (
        result["SYMBOL"]
        .astype(str)
        .str.strip()
    )

    result = result[
        result["SYMBOL"].notna()
        & (result["SYMBOL"] != "")
        & (result["SYMBOL"] != "nan")
    ]

    # --------------------------------------------------------
    # Convert numeric columns
    # --------------------------------------------------------

    numeric_columns = [
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
        "LAST",
        "PREV_CLOSE",
        "TOTAL_TRADED_QTY",
        "TOTAL_TRADED_VALUE",
        "TOTAL_TRADES",
    ]

    for col in numeric_columns:

        result[col] = pd.to_numeric(
            result[col],
            errors="coerce"
        )

    # --------------------------------------------------------
    # Replace NaN with blank
    # --------------------------------------------------------

    result = result.where(
        pd.notnull(result),
        ""
    )

    print(
        f"Cleaned usable rows: {len(result):,}"
    )

    return result


# ============================================================
# GOOGLE SHEETS AUTHENTICATION
# ============================================================

def connect_google_sheet():

    service_account_json = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    if not service_account_json:

        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON secret is missing."
        )

    try:

        service_account_info = json.loads(
            service_account_json
        )

    except json.JSONDecodeError as e:

        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON."
        ) from e

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=scopes
    )

    gc = gspread.authorize(
        credentials
    )

    spreadsheet_id = os.environ.get(
        "SPREADSHEET_ID"
    ) or DEFAULT_SPREADSHEET_ID

    sheet_name = os.environ.get(
        "SHEET_NAME"
    ) or DEFAULT_SHEET_NAME

    print(
        f"Opening spreadsheet: {spreadsheet_id}"
    )

    print(
        f"Opening worksheet: {sheet_name}"
    )

    spreadsheet = gc.open_by_key(
        spreadsheet_id
    )

    try:

        worksheet = spreadsheet.worksheet(
            sheet_name
        )

    except gspread.WorksheetNotFound:

        print(
            f"Worksheet {sheet_name} not found."
        )

        print(
            "Creating worksheet..."
        )

        worksheet = spreadsheet.add_worksheet(
            title=sheet_name,
            rows=10000,
            cols=20
        )

    return worksheet


# ============================================================
# WRITE DATA
# ============================================================

def write_to_sheet(ws, df):

    headers = list(df.columns)

    rows = df.astype(object).values.tolist()

    values = [
        headers
    ] + rows

    print(
        f"Writing {len(rows):,} rows "
        f"and {len(headers)} columns..."
    )

    ws.clear()

    ws.update(
        values,
        value_input_option="USER_ENTERED"
    )

    print(
        f"SUCCESS: Wrote {len(rows):,} rows "
        f"to {ws.title}."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("NSE BHAVCOPY → GOOGLE SHEETS")
    print("=" * 70)

    # --------------------------------------------------------
    # Determine starting date
    # --------------------------------------------------------

    override_date = os.environ.get(
        "BHAVCOPY_DATE",
        ""
    ).strip()

    if override_date:

        try:

            start_date = datetime.strptime(
                override_date,
                "%Y-%m-%d"
            ).date()

        except ValueError:

            raise RuntimeError(
                "BHAVCOPY_DATE must be YYYY-MM-DD."
            )

        print(
            f"Manual starting date: "
            f"{start_date}"
        )

    else:

        start_date = datetime.now(
            IST
        ).date()

        print(
            f"Automatic starting date: "
            f"{start_date}"
        )

    # --------------------------------------------------------
    # Create NSE session
    # --------------------------------------------------------

    session = create_nse_session()

    # --------------------------------------------------------
    # Find latest available trading-day bhavcopy
    # --------------------------------------------------------

    bhavcopy_date, zip_bytes = find_latest_bhavcopy(
        session,
        start_date,
        max_days_back=15
    )

    # --------------------------------------------------------
    # Read ZIP
    # --------------------------------------------------------

    raw_df = read_bhavcopy(
        zip_bytes
    )

    # --------------------------------------------------------
    # Clean data
    # --------------------------------------------------------

    clean_df = clean_bhavcopy(
        raw_df,
        bhavcopy_date
    )

    if clean_df.empty:

        raise RuntimeError(
            "Bhavcopy downloaded successfully "
            "but contains zero usable rows."
        )

    # --------------------------------------------------------
    # Connect Google Sheets
    # --------------------------------------------------------

    worksheet = connect_google_sheet()

    # --------------------------------------------------------
    # Write
    # --------------------------------------------------------

    write_to_sheet(
        worksheet,
        clean_df
    )

    # --------------------------------------------------------
    # Final confirmation
    # --------------------------------------------------------

    print("=" * 70)

    print(
        "COMPLETED SUCCESSFULLY"
    )

    print(
        f"Bhavcopy date: {bhavcopy_date}"
    )

    print(
        f"Rows written: {len(clean_df):,}"
    )

    print(
        f"Worksheet: {worksheet.title}"
    )

    print("=" * 70)

    return 0


if __name__ == "__main__":
    main()
