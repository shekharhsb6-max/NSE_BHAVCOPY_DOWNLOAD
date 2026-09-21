"""
fetch_bhavcopy.py
=================

Downloads NSE's daily equity Bhavcopy WITH delivery data and writes it
into the RAW_DATA tab of Google Sheets.

The program is designed to run unattended through GitHub Actions.

It:
  - skips weekends cleanly
  - automatically falls back to the most recent earlier NSE copy when the requested date is unavailable
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
    If that date is unavailable, the program searches backward for the
    most recent available NSE bhavcopy-with-delivery.

MAX_LOOKBACK_DAYS
    Optional.
    Maximum number of calendar days to search backward. Default is 10.

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

DEFAULT_MAX_LOOKBACK_DAYS = 10


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
    Downloads the latest available NSE bhavcopy-with-delivery on or before
    ``trade_date``.

    If NSE has not published the file for the requested date (for example,
    because the run happened before publication, or because the date was a
    weekend/holiday), the function searches backward one calendar day at a
    time until it finds the most recent available copy.

    The actual date of the downloaded copy is stored in
    ``df.attrs["trade_date"]`` so the Google Sheet receives the correct
    trading date rather than the date on which the workflow happened to run.
    """

    from nselib import capital_market

    try:
        max_lookback = int(
            os.environ.get(
                "MAX_LOOKBACK_DAYS",
                DEFAULT_MAX_LOOKBACK_DAYS
            )
        )
    except ValueError:
        max_lookback = DEFAULT_MAX_LOOKBACK_DAYS

    if max_lookback < 0:
        max_lookback = DEFAULT_MAX_LOOKBACK_DAYS

    print(
        f"Searching for the latest available NSE bhavcopy-with-delivery "
        f"on or before {trade_date}."
    )

    for days_back in range(max_lookback + 1):

        candidate_date = trade_date - pd.Timedelta(days=days_back)
        candidate_date = candidate_date.date()

        # Do not waste an NSE request on weekends.
        if candidate_date.weekday() >= 5:
            print(
                f"Skipping {candidate_date}: weekend."
            )
            continue

        nse_date = candidate_date.strftime("%d-%m-%Y")

        print(
            f"Trying NSE bhavcopy-with-delivery for "
            f"{candidate_date} ..."
        )

        try:
            df = capital_market.bhav_copy_with_delivery(
                trade_date=nse_date
            )

        except FileNotFoundError:
            print(
                f"No NSE bhavcopy-with-delivery for {candidate_date}. "
                f"Trying the previous date."
            )
            continue

        except Exception as exc:
            raise RuntimeError(
                f"Failed while downloading NSE bhavcopy-with-delivery "
                f"for {candidate_date}: {exc}"
            ) from exc

        if df is None or df.empty:
            print(
                f"NSE returned no rows for {candidate_date}. "
                f"Trying the previous date."
            )
            continue

        print(
            f"SUCCESS: NSE bhavcopy-with-delivery found for "
            f"{candidate_date}."
        )
        print(
            f"NSE returned {len(df)} rows."
        )
        print("NSE columns received:")
        print(df.columns.tolist())

        # Preserve the actual source date for the downstream transformation.
        df.attrs["trade_date"] = candidate_date

        return df

    print(
        f"No NSE bhavcopy-with-delivery found from {trade_date} "
        f"back through {max_lookback} calendar days."
    )

    return None


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

    In ``overwrite`` mode the sheet is replaced as before.

    In ``append`` mode:
      - a new trade date is appended normally;
      - if the trade date already exists in RAW_DATA, the existing rows for
        that date are updated with the new 15-column data instead of creating
        duplicate rows.

    This is important when the script falls back to the last available NSE
    copy: an already-present historical date can be enriched with DELIVERY
    data without duplicating the entire bhavcopy.
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
    # APPEND / UPDATE
    # -----------------------------------------------------------------------

    first_row = ws.row_values(1)

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

    # Empty sheet
    if not first_row:
        ws.update(
            "A1:O1",
            [header],
            value_input_option="USER_ENTERED"
        )
        first_row = header
        print("Created RAW_DATA header with DELIVERY columns.")

    elif first_row[:13] == old_header:
        print(
            "Existing RAW_DATA uses the old 13-column header. "
            "Expanding it to 15 columns."
        )
        ws.update(
            "A1:O1",
            [header],
            value_input_option="USER_ENTERED"
        )
        first_row = header

    elif first_row != header:
        print("Warning: existing RAW_DATA header differs from the expected header.")
        print("Existing header:", first_row)
        print("Expected header:", header)
        raise RuntimeError(
            "RAW_DATA header does not match the expected structure. "
            "No rows were written."
        )

    # -----------------------------------------------------------------------
    # If this date already exists, update it instead of duplicating it.
    # -----------------------------------------------------------------------

    source_date = str(df["TRADE_DATE"].iloc[0])
    existing_values = ws.get_all_values()

    date_rows = []
    for row_number, row in enumerate(existing_values[1:], start=2):
        if row and str(row[0]).strip() == source_date:
            date_rows.append(row_number)

    if date_rows:
        print(
            f"Trade date {source_date} already exists in RAW_DATA "
            f"({len(date_rows)} rows). Updating existing date instead "
            f"of appending duplicates."
        )

        # Build a lookup of incoming rows by SYMBOL + SERIES.
        incoming = {}
        for row in rows:
            key = (str(row[1]).strip(), str(row[2]).strip())
            incoming[key] = row

        updated = 0
        for row_number in date_rows:
            existing_row = existing_values[row_number - 1]
            symbol = str(existing_row[1]).strip() if len(existing_row) > 1 else ""
            series = str(existing_row[2]).strip() if len(existing_row) > 2 else ""
            key = (symbol, series)

            new_row = incoming.get(key)
            if new_row is not None:
                ws.update(
                    f"A{row_number}:O{row_number}",
                    [new_row],
                    value_input_option="USER_ENTERED"
                )
                updated += 1

        # Append any symbols that were not already present for that date.
        existing_keys = set()
        for row_number in date_rows:
            existing_row = existing_values[row_number - 1]
            symbol = str(existing_row[1]).strip() if len(existing_row) > 1 else ""
            series = str(existing_row[2]).strip() if len(existing_row) > 2 else ""
            existing_keys.add((symbol, series))

        missing_rows = [
            row
            for key, row in incoming.items()
            if key not in existing_keys
        ]

        if missing_rows:
            ws.append_rows(
                missing_rows,
                value_input_option="USER_ENTERED"
            )

        print(
            f"Updated {updated} existing rows for {source_date}; "
            f"appended {len(missing_rows)} previously missing rows."
        )
        return

    # -----------------------------------------------------------------------
    # New trade date: append normally.
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

    # The downloaded copy may be older than the requested date when the
    # requested day's NSE file is not yet available.
    actual_trade_date = raw_df.attrs.get(
        "trade_date",
        trade_date
    )

    # ---------------------------------------------------------------
    # Clean
    # ---------------------------------------------------------------

    df = clean_bhavcopy(
        raw_df,
        actual_trade_date
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
    print(f"Requested date   : {trade_date}")
    print(f"Downloaded date  : {actual_trade_date}")
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
