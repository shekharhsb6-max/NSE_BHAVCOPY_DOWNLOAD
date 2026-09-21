"""
fetch_bhavcopy.py
=================

Downloads NSE's daily equity Bhavcopy WITH delivery data and writes it
into the RAW_DATA tab of Google Sheets.

The program is designed to run unattended through GitHub Actions.

It:
  - skips weekends cleanly
  - skips NSE holidays / dates for which NSE has not published data
  - downloads NSE bhavcopy including DeliverableQty
  - calculates Delivery % from DeliverableQty / Total Traded Qty
  - preserves the existing RAW_DATA columns A:M
  - adds:
        N = DELIVERY_QTY
        O = DELIVERY_PCT
  - writes to Google Sheets using a service account
  - supports append or overwrite mode
  - supports BHAVCOPY_DATE for testing/backfilling

ENVIRONMENT VARIABLES
---------------------

GOOGLE_SERVICE_ACCOUNT_JSON
    Required.
    Full JSON service-account key as a string.

SPREADSHEET_ID
    Optional.
    Defaults to the existing spreadsheet.

SHEET_NAME
    Optional.
    Defaults to RAW_DATA.

BHAVCOPY_MODE
    Optional.
    "append" (default) or "overwrite".

BHAVCOPY_DATE
    Optional.
    YYYY-MM-DD.
    If omitted, today's date in IST is used.

Example:
    BHAVCOPY_DATE=2026-09-18
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DEFAULT_SPREADSHEET_ID = (
    "1D3E5lyH2QUq55AzsJqSbJj2tNkmOQht_8xUmt0mdvbk"
)

DEFAULT_SHEET_NAME = "RAW_DATA"

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# TARGET DATE
# ---------------------------------------------------------------------------

def target_date() -> date:
    """
    Returns the trading date to fetch.

    BHAVCOPY_DATE can be used for testing/backfilling:
        YYYY-MM-DD
    """

    override = os.environ.get("BHAVCOPY_DATE")

    if override:
        return datetime.strptime(
            override,
            "%Y-%m-%d"
        ).date()

    return datetime.now(IST).date()


# ---------------------------------------------------------------------------
# NSE BHAVCOPY WITH DELIVERY
# ---------------------------------------------------------------------------

def download_bhavcopy_with_delivery(
    trade_date: date
) -> pd.DataFrame | None:
    """
    Downloads NSE's daily bhavcopy including delivery quantity.

    Uses nselib's current bhav_copy_with_delivery() function.

    Returns:
        DataFrame
        None if NSE has no data for the requested date.
    """

    from nselib import capital_market

    nse_date = trade_date.strftime("%d-%m-%Y")

    print(
        f"Downloading NSE bhavcopy with delivery for "
        f"{trade_date} ..."
    )

    try:

        df = capital_market.bhav_copy_with_delivery(
            trade_date=nse_date
        )

    except FileNotFoundError:

        print(
            f"No NSE bhavcopy-with-delivery available for "
            f"{trade_date}. "
            f"Likely weekend, holiday, or file not yet published."
        )

        return None

    except Exception as exc:

        raise RuntimeError(
            f"Failed to download NSE bhavcopy-with-delivery "
            f"for {trade_date}: {exc}"
        ) from exc

    if df is None or df.empty:

        print(
            f"NSE returned no rows for {trade_date}."
        )

        return None

    print(
        f"NSE returned {len(df)} rows."
    )

    print(
        "NSE columns received:"
    )

    print(
        df.columns.tolist()
    )

    return df


# ---------------------------------------------------------------------------
# COLUMN HELPER
# ---------------------------------------------------------------------------

def find_column(
    df: pd.DataFrame,
    possible_names: list[str]
) -> str | None:
    """
    Finds a DataFrame column using case-insensitive matching.
    """

    normalized = {
        str(col).strip().upper(): col
        for col in df.columns
    }

    for name in possible_names:

        key = str(name).strip().upper()

        if key in normalized:
            return normalized[key]

    return None


# ---------------------------------------------------------------------------
# CLEAN / TRANSFORM
# ---------------------------------------------------------------------------

def clean_bhavcopy(
    df: pd.DataFrame,
    trade_date: date
) -> pd.DataFrame:
    """
    Converts NSE's bhavcopy-with-delivery DataFrame into the exact
    RAW_DATA structure used by the existing scanner.

    Existing columns A:M are preserved.

    New columns:
        N = DELIVERY_QTY
        O = DELIVERY_PCT
    """

    # -----------------------------------------------------------------------
    # Find NSE columns
    # -----------------------------------------------------------------------

    symbol_col = find_column(
        df,
        [
            "SYMBOL",
            "Symbol",
            "TckrSymb",
        ]
    )

    series_col = find_column(
        df,
        [
            "SERIES",
            "Series",
            "SctySrs",
        ]
    )

    open_col = find_column(
        df,
        [
            "OPEN",
            "Open",
            "Open Price",
            "OpnPric",
        ]
    )

    high_col = find_column(
        df,
        [
            "HIGH",
            "High",
            "High Price",
            "HghPric",
        ]
    )

    low_col = find_column(
        df,
        [
            "LOW",
            "Low",
            "Low Price",
            "LwPric",
        ]
    )

    close_col = find_column(
        df,
        [
            "CLOSE",
            "Close",
            "Close Price",
            "ClsPric",
        ]
    )

    last_col = find_column(
        df,
        [
            "LAST",
            "Last",
            "Last Price",
            "LastPric",
        ]
    )

    prev_close_col = find_column(
        df,
        [
            "PREVCLOSE",
            "PREV_CLOSE",
            "Prev Close",
            "PrvsClsgPric",
        ]
    )

    traded_qty_col = find_column(
        df,
        [
            "TOTTRDQTY",
            "TotalTradedQuantity",
            "Total Traded Quantity",
            "TtlTradgVol",
        ]
    )

    traded_value_col = find_column(
        df,
        [
            "TOTTRDVAL",
            "TurnoverInRs",
            "Turnover",
            "TtlTrfVal",
        ]
    )

    trades_col = find_column(
        df,
        [
            "TOTALTRADES",
            "No.ofTrades",
            "No. of Trades",
            "TtlNbOfTxsExctd",
        ]
    )

    isin_col = find_column(
        df,
        [
            "ISIN",
        ]
    )

    delivery_qty_col = find_column(
        df,
        [
            "DeliverableQty",
            "DELIVERABLE_QTY",
            "DELIVERY_QTY",
            "Delivery Qty",
            "DELIVERY QTY",
        ]
    )

    delivery_pct_col = find_column(
        df,
        [
            "% Dly Qt to Traded Qty",
            "%DlyQttoTradedQty",
            "DELIVERY_PCT",
            "DELIVERY %",
            "Delivery %",
        ]
    )

    # -----------------------------------------------------------------------
    # Check essential fields
    # -----------------------------------------------------------------------

    required = {
        "SYMBOL": symbol_col,
        "SERIES": series_col,
        "OPEN": open_col,
        "HIGH": high_col,
        "LOW": low_col,
        "CLOSE": close_col,
        "LAST": last_col,
        "PREV_CLOSE": prev_close_col,
        "TOTAL_TRADED_QTY": traded_qty_col,
        "TOTAL_TRADED_VALUE": traded_value_col,
        "TOTAL_TRADES": trades_col,
        "ISIN": isin_col,
        "DELIVERY_QTY": delivery_qty_col,
    }

    missing = [
        name
        for name, column in required.items()
        if column is None
    ]

    if missing:

        raise RuntimeError(
            "NSE bhavcopy-with-delivery is missing required "
            f"column(s): {missing}"
        )

    # -----------------------------------------------------------------------
    # Build output
    # -----------------------------------------------------------------------

    out = pd.DataFrame()

    # Existing RAW_DATA fields

    out["TRADE_DATE"] = trade_date.isoformat()

    out["SYMBOL"] = df[symbol_col]

    out["SERIES"] = df[series_col]

    out["OPEN"] = df[open_col]

    out["HIGH"] = df[high_col]

    out["LOW"] = df[low_col]

    out["CLOSE"] = df[close_col]

    out["LAST"] = df[last_col]

    out["PREV_CLOSE"] = df[prev_close_col]

    out["TOTAL_TRADED_QTY"] = df[traded_qty_col]

    out["TOTAL_TRADED_VALUE"] = df[traded_value_col]

    out["TOTAL_TRADES"] = df[trades_col]

    out["ISIN"] = df[isin_col]

    # -----------------------------------------------------------------------
    # NEW: DELIVERY QTY
    # -----------------------------------------------------------------------

    out["DELIVERY_QTY"] = df[delivery_qty_col]

    # -----------------------------------------------------------------------
    # NEW: DELIVERY %
    #
    # Prefer NSE-provided percentage if available.
    # Otherwise calculate:
    #
    # Delivery % = Delivery Qty / Total Traded Qty × 100
    # -----------------------------------------------------------------------

    if delivery_pct_col is not None:

        out["DELIVERY_PCT"] = df[delivery_pct_col]

    else:

        traded_qty = pd.to_numeric(
            out["TOTAL_TRADED_QTY"],
            errors="coerce"
        )

        delivery_qty = pd.to_numeric(
            out["DELIVERY_QTY"],
            errors="coerce"
        )

        out["DELIVERY_PCT"] = (
            delivery_qty
            .div(traded_qty)
            .mul(100)
        )

    # -----------------------------------------------------------------------
    # Clean numeric columns
    # -----------------------------------------------------------------------

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
        "DELIVERY_QTY",
        "DELIVERY_PCT",
    ]

    for column in numeric_columns:

        out[column] = pd.to_numeric(
            out[column],
            errors="coerce"
        )

    # -----------------------------------------------------------------------
    # Remove rows without a symbol
    # -----------------------------------------------------------------------

    out["SYMBOL"] = (
        out["SYMBOL"]
        .astype(str)
        .str.strip()
    )

    out = out[
        out["SYMBOL"].ne("")
        & out["SYMBOL"].ne("nan")
    ]

    # -----------------------------------------------------------------------
    # Convert NaN to None
    # -----------------------------------------------------------------------

    out = out.where(
        pd.notnull(out),
        None
    )

    # -----------------------------------------------------------------------
    # Ensure exact column order
    # -----------------------------------------------------------------------

    columns = [
        "TRADE_DATE",
        "SYMBOL",
        "SERIES",
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
        "LAST",
        "PREV_CLOSE",
        "TOTAL_TRADED_QTY",
        "TOTAL_TRADED_VALUE",
        "TOTAL_TRADES",
        "ISIN",
        "DELIVERY_QTY",
        "DELIVERY_PCT",
    ]

    out = out[columns]

    print(
        f"Cleaned {len(out)} rows."
    )

    print(
        "Delivery Qty available:",
        out["DELIVERY_QTY"].notna().sum(),
        "rows"
    )

    print(
        "Delivery % available:",
        out["DELIVERY_PCT"].notna().sum(),
        "rows"
    )

    return out


# ---------------------------------------------------------------------------
# GOOGLE SHEETS
# ---------------------------------------------------------------------------

def get_worksheet(
    spreadsheet_id: str,
    sheet_name: str
):
    """
    Connects to Google Sheets using the service account.
    """

    import gspread

    from google.oauth2.service_account import Credentials

    creds_json = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    if not creds_json:

        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set."
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=scopes
    )

    gc = gspread.authorize(creds)

    sh = gc.open_by_key(
        spreadsheet_id
    )

    try:

        ws = sh.worksheet(
            sheet_name
        )

    except gspread.WorksheetNotFound:

        print(
            f'"{sheet_name}" not found — creating it.'
        )

        ws = sh.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=20
        )

    return ws


# ---------------------------------------------------------------------------
# GOOGLE SHEETS WRITE
# ---------------------------------------------------------------------------

def write_to_sheet(
    ws,
    df: pd.DataFrame,
    mode: str
) -> None:
    """
    Writes data to Google Sheets.

    Special handling:
    If RAW_DATA already has the old 13-column header,
    the header is automatically expanded to 15 columns.

    Existing historical rows remain intact.
    Their DELIVERY_QTY and DELIVERY_PCT cells will simply be blank.
    """

    header = df.columns.tolist()

    rows = df.astype(object).values.tolist()

    # -----------------------------------------------------------------------
    # OVERWRITE
    # -----------------------------------------------------------------------

    if mode == "overwrite":

        ws.clear()

        ws.update(
            [header] + rows,
            value_input_option="USER_ENTERED"
        )

        print(
            f"Overwrote {ws.title} with {len(rows)} rows."
        )

        return

    # -----------------------------------------------------------------------
    # APPEND
    # -----------------------------------------------------------------------

    first_row = ws.row_values(1)

    # Empty sheet
    if not first_row:

        ws.update(
            "A1:O1",
            [header],
            value_input_option="USER_ENTERED"
        )

        print(
            "Created RAW_DATA header with DELIVERY columns."
        )

    else:

        # Existing old 13-column header
        old_header = [
            "TRADE_DATE",
            "SYMBOL",
            "SERIES",
            "OPEN",
            "HIGH",
            "LOW",
            "CLOSE",
            "LAST",
            "PREV_CLOSE",
            "TOTAL_TRADED_QTY",
            "TOTAL_TRADED_VALUE",
            "TOTAL_TRADES",
            "ISIN",
        ]

        if first_row[:13] == old_header:

            print(
                "Existing RAW_DATA uses the old 13-column "
                "header. Expanding it to 15 columns."
            )

            ws.update(
                "A1:O1",
                [header],
                value_input_option="USER_ENTERED"
            )

        elif first_row != header:

            print(
                "Warning: existing RAW_DATA header differs "
                "from the expected header."
            )

            print(
                "Existing header:",
                first_row
            )

            print(
                "Expected header:",
                header
            )

            raise RuntimeError(
                "RAW_DATA header does not match the expected "
                "structure. No rows were appended."
            )

    # -----------------------------------------------------------------------
    # APPEND DATA
    # -----------------------------------------------------------------------

    ws.append_rows(
        rows,
        value_input_option="USER_ENTERED"
    )

    print(
        f"Appended {len(rows)} rows to {ws.title}."
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> int:

    trade_date = target_date()

    # ---------------------------------------------------------------
    # Weekend
    # ---------------------------------------------------------------

    if trade_date.weekday() >= 5:

        print(
            f"{trade_date} is a weekend — "
            "NSE does not publish a bhavcopy. Skipping."
        )

        return 0

    # ---------------------------------------------------------------
    # Configuration
    # ---------------------------------------------------------------

    spreadsheet_id = os.environ.get(
        "SPREADSHEET_ID",
        DEFAULT_SPREADSHEET_ID
    )

    sheet_name = os.environ.get(
        "SHEET_NAME",
        DEFAULT_SHEET_NAME
    )

    mode = os.environ.get(
        "BHAVCOPY_MODE",
        "append"
    ).lower()

    if mode not in (
        "append",
        "overwrite"
    ):

        print(
            'BHAVCOPY_MODE must be "append" or "overwrite".',
            file=sys.stderr
        )

        return 1

    # ---------------------------------------------------------------
    # Download
    # ---------------------------------------------------------------

    raw_df = download_bhavcopy_with_delivery(
        trade_date
    )

    if raw_df is None:

        return 0

    # ---------------------------------------------------------------
    # Clean
    # ---------------------------------------------------------------

    df = clean_bhavcopy(
        raw_df,
        trade_date
    )

    if df.empty:

        print(
            "Downloaded data contained zero usable rows."
        )

        return 0

    # ---------------------------------------------------------------
    # Google Sheets
    # ---------------------------------------------------------------

    ws = get_worksheet(
        spreadsheet_id,
        sheet_name
    )

    write_to_sheet(
        ws,
        df,
        mode
    )

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------

    print("")
    print("=" * 60)
    print("NSE BHAVCOPY + DELIVERY COMPLETED")
    print("=" * 60)
    print(f"Trade date       : {trade_date}")
    print(f"Rows written     : {len(df)}")
    print(
        "Delivery Qty     :",
        df["DELIVERY_QTY"].notna().sum()
    )
    print(
        "Delivery %       :",
        df["DELIVERY_PCT"].notna().sum()
    )
    print(f"Google Sheet     : {sheet_name}")
    print("=" * 60)

    return 0


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    sys.exit(
        main()
    )
