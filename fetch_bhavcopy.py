"""
fetch_bhavcopy.py
=================

Downloads NSE's daily equity Bhavcopy WITH delivery data and writes it
into the RAW_DATA tab of Google Sheets.

Designed for unattended GitHub Actions.

Features:
- Searches for the most recent available NSE bhavcopy-with-delivery on or
  before the requested date.
- Skips weekends while searching backward.
- Preserves RAW_DATA columns A:M.
- Adds N = DELIVERY_QTY and O = DELIVERY_PCT.
- Uses NSE delivery percentage when available; otherwise calculates it.
- Updates an already-present trade date instead of creating duplicate rows.
- Supports BHAVCOPY_DATE for testing/backfilling.
- Supports MAX_LOOKBACK_DAYS.

CHANGES IN THIS VERSION
------------------------
- Fixed: every `ws.update(...)` call now uses explicit keyword arguments
  (range_name=..., values=...) instead of positional arguments. gspread 6.0
  swapped the positional argument order of Worksheet.update() from
  (range_name, values) to (values, range_name). Positional calls written
  against the old order silently break (or raise a TypeError) once the
  environment installs gspread>=6. Keyword arguments work correctly on
  both the old and new gspread APIs.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DEFAULT_SPREADSHEET_ID = (
    "1D3E5lyH2QUq55AzsJqSbJj2tNkmOQht_8xUmt0mdvbk"
)

DEFAULT_SHEET_NAME = "RAW_DATA"
DEFAULT_MAX_LOOKBACK_DAYS = 10
IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# TARGET DATE
# ---------------------------------------------------------------------------

def target_date() -> date:
    """Return the requested date, or today's date in IST."""

    override = os.environ.get("BHAVCOPY_DATE", "").strip()

    if override:
        return datetime.strptime(override, "%Y-%m-%d").date()

    return datetime.now(IST).date()


# ---------------------------------------------------------------------------
# NSE BHAVCOPY WITH DELIVERY
# ---------------------------------------------------------------------------

def download_bhavcopy_with_delivery(
    requested_date: date,
) -> pd.DataFrame | None:
    """
    Download the most recent available NSE bhavcopy-with-delivery on or
    before requested_date.

    IMPORTANT:
    We use datetime.timedelta for date arithmetic. This avoids the
    date/Timestamp .date() error that occurred in the previous version.

    The actual downloaded date is stored in:
        df.attrs["trade_date"]
    """

    from nselib import capital_market

    try:
        max_lookback = int(
            os.environ.get(
                "MAX_LOOKBACK_DAYS",
                str(DEFAULT_MAX_LOOKBACK_DAYS),
            )
        )
    except ValueError:
        max_lookback = DEFAULT_MAX_LOOKBACK_DAYS

    if max_lookback < 0:
        max_lookback = DEFAULT_MAX_LOOKBACK_DAYS

    print(
        f"Searching for the latest available NSE bhavcopy-with-delivery "
        f"on or before {requested_date}."
    )

    for days_back in range(max_lookback + 1):

        # Pure datetime.date arithmetic -- no pandas Timestamp involved.
        candidate_date = requested_date - timedelta(days=days_back)

        # NSE does not publish normal CM bhavcopy on weekends.
        if candidate_date.weekday() >= 5:
            print(f"Skipping {candidate_date}: weekend.")
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
            # Some versions of nselib may raise a generic exception for a
            # missing NSE file. Treat common "not found/no data" messages
            # as a reason to continue searching backward.
            msg = str(exc).lower()

            not_available_terms = (
                "404",
                "not found",
                "file not found",
                "no data",
                "no bhav",
                "unable to download",
                "failed to download",
            )

            if any(term in msg for term in not_available_terms):
                print(
                    f"No usable NSE copy for {candidate_date}: {exc}"
                )
                print("Trying the previous date.")
                continue

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
        print(f"NSE returned {len(df)} rows.")
        print("NSE columns received:")
        print(df.columns.tolist())

        # Preserve the actual source date for the downstream transformation.
        df.attrs["trade_date"] = candidate_date

        return df

    print(
        f"No NSE bhavcopy-with-delivery found from {requested_date} "
        f"back through {max_lookback} calendar days."
    )

    return None


# ---------------------------------------------------------------------------
# COLUMN HELPER
# ---------------------------------------------------------------------------

def _normalize_column_name(value) -> str:
    """
    Normalize a column name so that spaces, %, underscores, dots, etc.
    do not prevent matching.
    """
    return "".join(
        ch for ch in str(value).strip().upper()
        if ch.isalnum()
    )


def find_column(
    df: pd.DataFrame,
    possible_names: list[str],
) -> str | None:
    """
    Find a DataFrame column using robust normalized matching.
    """

    normalized = {
        _normalize_column_name(col): col
        for col in df.columns
    }

    for name in possible_names:
        key = _normalize_column_name(name)
        if key in normalized:
            return normalized[key]

    return None


# ---------------------------------------------------------------------------
# CLEAN / TRANSFORM
# ---------------------------------------------------------------------------

def clean_bhavcopy(
    df: pd.DataFrame,
    trade_date: date,
) -> pd.DataFrame:
    """
    Convert NSE's bhavcopy-with-delivery DataFrame into the exact
    RAW_DATA structure.

    A:M = existing fields
    N   = DELIVERY_QTY
    O   = DELIVERY_PCT
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
        ],
    )

    series_col = find_column(
        df,
        [
            "SERIES",
            "Series",
            "SctySrs",
        ],
    )

    open_col = find_column(
        df,
        [
            "OPEN",
            "Open",
            "Open Price",
            "OpnPric",
        ],
    )

    high_col = find_column(
        df,
        [
            "HIGH",
            "High",
            "High Price",
            "HghPric",
        ],
    )

    low_col = find_column(
        df,
        [
            "LOW",
            "Low",
            "Low Price",
            "LwPric",
        ],
    )

    close_col = find_column(
        df,
        [
            "CLOSE",
            "Close",
            "Close Price",
            "ClsPric",
        ],
    )

    last_col = find_column(
        df,
        [
            "LAST",
            "Last",
            "Last Price",
            "LastPric",
        ],
    )

    prev_close_col = find_column(
        df,
        [
            "PREVCLOSE",
            "PREV_CLOSE",
            "Prev Close",
            "Previous Close",
            "PrvsClsgPric",
        ],
    )

    # nselib bhav_copy_with_delivery() returns these columns in the
    # current format:
    #   TTL_TRD_QNTY   = total traded quantity
    #   TURNOVER_LACS  = turnover in lakh rupees
    #   NO_OF_TRADES   = number of trades
    #   DELIV_QTY      = actual deliverable quantity
    #   DELIV_PER      = delivery percentage
    #
    # Keep the older aliases too, so the script remains compatible if
    # nselib/NSE changes the spelling in a future release.

    traded_qty_col = find_column(
        df,
        [
            "TTL_TRD_QNTY",
            "TOTTRDQTY",
            "TotalTradedQuantity",
            "Total Traded Quantity",
            "Total Traded Qty",
            "TtlTradgVol",
        ],
    )

    traded_value_col = find_column(
        df,
        [
            "TURNOVER_LACS",
            "TOTTRDVAL",
            "TurnoverInRs",
            "Turnover",
            "Total Traded Value",
            "TtlTrfVal",
        ],
    )

    trades_col = find_column(
        df,
        [
            "NO_OF_TRADES",
            "TOTALTRADES",
            "No.ofTrades",
            "No. of Trades",
            "Number of Trades",
            "TtlNbOfTxsExctd",
        ],
    )

    # The nselib delivery output shown in the successful test does not
    # contain ISIN. ISIN is therefore optional. If it is absent, leave it
    # blank rather than failing the whole download.
    isin_col = find_column(
        df,
        [
            "ISIN",
        ],
    )

    # Actual NSE/nselib delivery quantity.
    delivery_qty_col = find_column(
        df,
        [
            "DELIV_QTY",
            "DeliverableQty",
            "Deliverable Qty",
            "Deliverable Quantity",
            "Deliverable Volume",
            "DELIVERABLE_QTY",
            "DELIVERY_QTY",
            "Delivery Qty",
            "DELIVERY QTY",
            "DlyQty",
            "Dly Qty",
        ],
    )

    # Actual NSE/nselib delivery percentage.
    delivery_pct_col = find_column(
        df,
        [
            "DELIV_PER",
            "% Dly Qt to Traded Qty",
            "%DlyQttoTradedQty",
            "Percent Dly Qt to Traded Qty",
            "Delivery Percentage",
            "DELIVERY_PCT",
            "DELIVERY %",
            "Delivery %",
            "DlyQtyPct",
        ],
    )

    print("")
    print("Column mapping detected:")
    print(f"  SYMBOL             : {symbol_col}")
    print(f"  SERIES             : {series_col}")
    print(f"  TOTAL_TRADED_QTY   : {traded_qty_col}")
    print(f"  TOTAL_TRADED_VALUE : {traded_value_col}")
    print(f"  TOTAL_TRADES       : {trades_col}")
    print(f"  ISIN               : {isin_col}")
    print(f"  DELIVERY_QTY       : {delivery_qty_col}")
    print(f"  DELIVERY_PCT       : {delivery_pct_col}")
    print("")

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
            f"column(s): {missing}. "
            f"Actual columns returned by nselib: {df.columns.tolist()}"
        )

    # -----------------------------------------------------------------------
    # Build output
    # -----------------------------------------------------------------------

    out = pd.DataFrame()

    # Existing RAW_DATA fields A:M
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

    # nselib's TURNOVER_LACS is expressed in lakh rupees.
    # RAW_DATA.TOTAL_TRADED_VALUE is kept in rupees, matching the existing
    # RAW_DATA convention. Therefore convert lakhs -> rupees when the
    # source column is TURNOVER_LACS.
    traded_value = pd.to_numeric(
        df[traded_value_col],
        errors="coerce",
    )

    if _normalize_column_name(traded_value_col) == "TURNOVERLACS":
        traded_value = traded_value * 100000

    out["TOTAL_TRADED_VALUE"] = traded_value
    out["TOTAL_TRADES"] = df[trades_col]

    if isin_col is not None:
        out["ISIN"] = df[isin_col]
    else:
        out["ISIN"] = ""

    # N = DELIVERY_QTY
    out["DELIVERY_QTY"] = df[delivery_qty_col]

    # O = DELIVERY_PCT
    # Prefer NSE-provided percentage when available.
    if delivery_pct_col is not None:
        out["DELIVERY_PCT"] = df[delivery_pct_col]
        print("Using NSE-provided Delivery %.")
    else:
        print(
            "NSE delivery percentage column not supplied. "
            "Calculating Delivery % from Delivery Qty / Traded Qty."
        )

        traded_qty = pd.to_numeric(
            out["TOTAL_TRADED_QTY"],
            errors="coerce",
        )

        delivery_qty = pd.to_numeric(
            out["DELIVERY_QTY"],
            errors="coerce",
        )

        out["DELIVERY_PCT"] = (
            delivery_qty
            .div(traded_qty.replace(0, pd.NA))
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
            errors="coerce",
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
    # Convert NaN to None for gspread
    # -----------------------------------------------------------------------

    out = out.where(pd.notnull(out), None)

    # -----------------------------------------------------------------------
    # Exact RAW_DATA column order
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

    print(f"Cleaned {len(out)} rows.")

    print(
        "Delivery Qty available:",
        out["DELIVERY_QTY"].notna().sum(),
        "rows",
    )

    print(
        "Delivery % available:",
        out["DELIVERY_PCT"].notna().sum(),
        "rows",
    )

    return out


# ---------------------------------------------------------------------------
# GOOGLE SHEETS
# ---------------------------------------------------------------------------

def get_worksheet(
    spreadsheet_id: str,
    sheet_name: str,
):
    """Connect to Google Sheets using the service account."""

    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not creds_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set."
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    creds_info = json.loads(creds_json)

    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=scopes,
    )

    # Diagnostic: print exactly what we're trying to open and as whom.
    # An APIError with an HTML "Sorry, unable to open the file" body when
    # opening the spreadsheet almost always means either (a) this service
    # account email has not been given Editor access to the sheet, or
    # (b) spreadsheet_id below is wrong/stale.
    print(
        f"Opening spreadsheet_id={spreadsheet_id!r} "
        f"as service account {creds_info.get('client_email')!r}"
    )

    gc = gspread.authorize(creds)

    try:
        sh = gc.open_by_key(spreadsheet_id)

    except gspread.exceptions.APIError as exc:
        raise RuntimeError(
            "Could not open the spreadsheet. This usually means the "
            f"service account {creds_info.get('client_email')!r} has not "
            f"been shared as an Editor on spreadsheet_id={spreadsheet_id!r}, "
            "or that ID is wrong/stale. Share the sheet with that exact "
            "email address (Editor access) and re-run, or fix "
            "SPREADSHEET_ID / DEFAULT_SPREADSHEET_ID. "
            f"Original error: {exc}"
        ) from exc

    try:
        ws = sh.worksheet(sheet_name)

    except gspread.WorksheetNotFound:
        print(
            f'"{sheet_name}" not found — creating it.'
        )

        ws = sh.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=20,
        )

    return ws


# ---------------------------------------------------------------------------
# GOOGLE SHEETS WRITE
# ---------------------------------------------------------------------------

def write_to_sheet(
    ws,
    df: pd.DataFrame,
    mode: str,
) -> None:
    """
    Write data to RAW_DATA.

    overwrite:
        Replace the entire sheet.

    append:
        If the downloaded trade date is new, append it.
        If that trade date already exists, update existing rows and append
        only symbols that were not already present for that date.

    This allows an older existing bhavcopy to be enriched with delivery data
    without creating duplicate rows.

    NOTE: every call to ws.update() below uses explicit keyword arguments
    (range_name=..., values=...). gspread 6.0 swapped the positional
    argument order of Worksheet.update() from (range_name, values) to
    (values, range_name). Positional calls written against the old order
    break once the environment has gspread>=6 installed. Keyword arguments
    are safe on both the pre-6.0 and 6.0+ APIs.
    """

    header = df.columns.tolist()
    rows = df.astype(object).values.tolist()

    # -----------------------------------------------------------------------
    # OVERWRITE
    # -----------------------------------------------------------------------

    if mode == "overwrite":

        ws.clear()

        ws.update(
            range_name="A1:O1",
            values=[header],
            value_input_option="USER_ENTERED",
        )

        if rows:
            ws.update(
                range_name=f"A2:O{len(rows) + 1}",
                values=rows,
                value_input_option="USER_ENTERED",
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
            range_name="A1:O1",
            values=[header],
            value_input_option="USER_ENTERED",
        )

        first_row = header

        print(
            "Created RAW_DATA header with DELIVERY columns."
        )

    # Existing old A:M header
    elif first_row[:13] == old_header:

        print(
            "Existing RAW_DATA uses the old 13-column header. "
            "Expanding it to 15 columns."
        )

        ws.update(
            range_name="A1:O1",
            values=[header],
            value_input_option="USER_ENTERED",
        )

        first_row = header

    # Existing new header
    elif first_row != header:

        print(
            "Warning: existing RAW_DATA header differs "
            "from the expected header."
        )
        print("Existing header:", first_row)
        print("Expected header:", header)

        raise RuntimeError(
            "RAW_DATA header does not match the expected structure. "
            "No rows were written."
        )

    # -----------------------------------------------------------------------
    # Identify source date
    # -----------------------------------------------------------------------

    source_date = str(df["TRADE_DATE"].iloc[0]).strip()

    existing_values = ws.get_all_values()

    date_rows = []

    for row_number, row in enumerate(
        existing_values[1:],
        start=2,
    ):
        if row and str(row[0]).strip() == source_date:
            date_rows.append(row_number)

    # -----------------------------------------------------------------------
    # Existing date -> update rather than duplicate
    # -----------------------------------------------------------------------

    if date_rows:

        print(
            f"Trade date {source_date} already exists in RAW_DATA "
            f"({len(date_rows)} rows). Updating existing date instead "
            f"of appending duplicates."
        )

        # Incoming lookup by SYMBOL + SERIES
        incoming = {}

        for row in rows:
            key = (
                str(row[1]).strip(),
                str(row[2]).strip(),
            )
            incoming[key] = row

        existing_keys = set()
        updated = 0

        for row_number in date_rows:

            existing_row = existing_values[row_number - 1]

            symbol = (
                str(existing_row[1]).strip()
                if len(existing_row) > 1
                else ""
            )

            series = (
                str(existing_row[2]).strip()
                if len(existing_row) > 2
                else ""
            )

            key = (symbol, series)
            existing_keys.add(key)

            new_row = incoming.get(key)

            if new_row is not None:

                ws.update(
                    range_name=f"A{row_number}:O{row_number}",
                    values=[new_row],
                    value_input_option="USER_ENTERED",
                )

                updated += 1

        # Append symbols present in incoming data but absent from the
        # existing date.
        missing_rows = [
            row
            for key, row in incoming.items()
            if key not in existing_keys
        ]

        if missing_rows:

            ws.append_rows(
                missing_rows,
                value_input_option="USER_ENTERED",
            )

        print(
            f"Updated {updated} existing rows for {source_date}; "
            f"appended {len(missing_rows)} previously missing rows."
        )

        return

    # -----------------------------------------------------------------------
    # New trade date -> append normally
    # -----------------------------------------------------------------------

    ws.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )

    print(
        f"Appended {len(rows)} rows to {ws.title}."
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> int:

    requested_date = target_date()

    print("=" * 60)
    print("NSE BHAVCOPY + DELIVERY")
    print("=" * 60)
    print(f"Requested date : {requested_date}")

    # -----------------------------------------------------------------------
    # Download latest available copy.
    #
    # IMPORTANT: We deliberately do NOT exit just because requested_date is
    # a weekend. The downloader itself skips weekends and searches backward.
    # -----------------------------------------------------------------------

    raw_df = download_bhavcopy_with_delivery(
        requested_date
    )

    if raw_df is None:

        print(
            "No NSE bhavcopy-with-delivery could be found "
            "within the configured lookback period."
        )

        return 0

    # The actual downloaded copy may be older than the requested date.
    actual_trade_date = raw_df.attrs.get(
        "trade_date",
        requested_date,
    )

    print(f"Downloaded date: {actual_trade_date}")

    # -----------------------------------------------------------------------
    # Clean
    # -----------------------------------------------------------------------

    df = clean_bhavcopy(
        raw_df,
        actual_trade_date,
    )

    if df.empty:

        print(
            "Downloaded data contained zero usable rows."
        )

        return 0

    # -----------------------------------------------------------------------
    # Configuration
    # -----------------------------------------------------------------------

    spreadsheet_id = os.environ.get(
        "SPREADSHEET_ID",
        DEFAULT_SPREADSHEET_ID,
    )

    sheet_name = os.environ.get(
        "SHEET_NAME",
        DEFAULT_SHEET_NAME,
    )

    mode = os.environ.get(
        "BHAVCOPY_MODE",
        "append",
    ).lower()

    if mode not in ("append", "overwrite"):

        print(
            'BHAVCOPY_MODE must be "append" or "overwrite".',
            file=sys.stderr,
        )

        return 1

    # -----------------------------------------------------------------------
    # Google Sheets
    # -----------------------------------------------------------------------

    ws = get_worksheet(
        spreadsheet_id,
        sheet_name,
    )

    write_to_sheet(
        ws,
        df,
        mode,
    )

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------

    print("")
    print("=" * 60)
    print("NSE BHAVCOPY + DELIVERY COMPLETED")
    print("=" * 60)
    print(f"Requested date   : {requested_date}")
    print(f"Downloaded date  : {actual_trade_date}")
    print(f"Rows written     : {len(df)}")
    print(
        "Delivery Qty     :",
        df["DELIVERY_QTY"].notna().sum(),
    )
    print(
        "Delivery %       :",
        df["DELIVERY_PCT"].notna().sum(),
    )
    print(f"Google Sheet     : {sheet_name}")
    print("=" * 60)

    return 0


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
