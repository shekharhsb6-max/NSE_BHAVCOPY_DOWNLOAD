from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


# ============================================================================
# CONFIG
# ============================================================================

DEFAULT_SPREADSHEET_ID = (
    "1D3E5lyH2QUq55AzsJqSbJj2tNkmOQht_8xUmt0mdvbk"
)

DEFAULT_SHEET_NAME = "RAW_DATA"
DEFAULT_MAX_LOOKBACK_DAYS = 10
DEFAULT_BACKFILL_TRADING_DAYS = 252
DEFAULT_BACKFILL_MAX_CALENDAR_DAYS = 450
IST = ZoneInfo("Asia/Kolkata")

RAW_HEADERS = [
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


# ============================================================================
# DATE
# ============================================================================

def get_requested_date() -> date:
    """Use BHAVCOPY_DATE if supplied; otherwise today's date in IST."""

    value = os.environ.get("BHAVCOPY_DATE", "").strip()

    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()

    return datetime.now(IST).date()


# ============================================================================
# COLUMN MATCHING
# ============================================================================

def normalize_column(value) -> str:
    return "".join(
        ch for ch in str(value).strip().upper()
        if ch.isalnum()
    )


def find_column(
    df: pd.DataFrame,
    names: list[str],
) -> str | None:

    lookup = {
        normalize_column(col): col
        for col in df.columns
    }

    for name in names:
        key = normalize_column(name)

        if key in lookup:
            return lookup[key]

    return None


# ============================================================================
# NSE DOWNLOAD
# ============================================================================

def download_bhavcopy_for_date(trade_date: date) -> pd.DataFrame | None:
    """Download bhavcopy-with-delivery for exactly one NSE date."""
    from nselib import capital_market

    nse_date = trade_date.strftime("%d-%m-%Y")

    try:
        df = capital_market.bhav_copy_with_delivery(trade_date=nse_date)
    except FileNotFoundError:
        return None
    except Exception as exc:
        message = str(exc).lower()
        retryable = (
            "404", "not found", "file not found", "no data",
            "no bhav", "unable to download", "failed to download"
        )
        if any(term in message for term in retryable):
            return None
        raise RuntimeError(
            f"NSE download failed for {trade_date}: {exc}"
        ) from exc

    if df is None or df.empty:
        return None

    df.attrs["trade_date"] = trade_date
    return df


def download_latest_bhavcopy(
    requested_date: date,
) -> pd.DataFrame | None:

    from nselib import capital_market

    try:
        lookback = int(
            os.environ.get(
                "MAX_LOOKBACK_DAYS",
                str(DEFAULT_MAX_LOOKBACK_DAYS),
            )
        )
    except ValueError:
        lookback = DEFAULT_MAX_LOOKBACK_DAYS

    if lookback < 0:
        lookback = DEFAULT_MAX_LOOKBACK_DAYS

    print(
        f"Searching for the latest available NSE "
        f"bhavcopy-with-delivery on or before {requested_date}."
    )

    for days_back in range(lookback + 1):

        candidate = requested_date - timedelta(days=days_back)

        # Skip Saturday/Sunday.
        if candidate.weekday() >= 5:
            print(f"Skipping {candidate}: weekend.")
            continue

        nse_date = candidate.strftime("%d-%m-%Y")

        print(
            f"Trying NSE bhavcopy-with-delivery for "
            f"{candidate} ..."
        )

        try:
            df = capital_market.bhav_copy_with_delivery(
                trade_date=nse_date
            )

        except FileNotFoundError:
            print(
                f"No NSE bhavcopy-with-delivery for {candidate}. "
                f"Trying previous date."
            )
            continue

        except Exception as exc:

            message = str(exc).lower()

            retryable = (
                "404",
                "not found",
                "file not found",
                "no data",
                "no bhav",
                "unable to download",
                "failed to download",
            )

            if any(term in message for term in retryable):

                print(
                    f"No usable NSE copy for {candidate}: {exc}"
                )
                print("Trying previous date.")
                continue

            raise RuntimeError(
                f"NSE download failed for {candidate}: {exc}"
            ) from exc

        if df is None or df.empty:

            print(
                f"NSE returned no rows for {candidate}. "
                f"Trying previous date."
            )

            continue

        print(
            f"SUCCESS: NSE bhavcopy-with-delivery found for "
            f"{candidate}."
        )

        print(f"NSE returned {len(df)} rows.")

        print("NSE columns received:")
        print(df.columns.tolist())

        # Store actual source date without converting it to Timestamp.
        df.attrs["trade_date"] = candidate

        return df

    print(
        f"No NSE bhavcopy-with-delivery found in the previous "
        f"{lookback} calendar days."
    )

    return None


# ============================================================================
# TRANSFORM
# ============================================================================

def clean_bhavcopy(
    df: pd.DataFrame,
    trade_date: date,
) -> pd.DataFrame:

    # Current nselib names are included explicitly.

    symbol_col = find_column(
        df,
        ["SYMBOL"],
    )

    series_col = find_column(
        df,
        ["SERIES"],
    )

    open_col = find_column(
        df,
        ["OPEN_PRICE", "OPEN"],
    )

    high_col = find_column(
        df,
        ["HIGH_PRICE", "HIGH"],
    )

    low_col = find_column(
        df,
        ["LOW_PRICE", "LOW"],
    )

    close_col = find_column(
        df,
        ["CLOSE_PRICE", "CLOSE"],
    )

    last_col = find_column(
        df,
        ["LAST_PRICE", "LAST"],
    )

    prev_close_col = find_column(
        df,
        ["PREV_CLOSE", "PREVIOUS_CLOSE"],
    )

    traded_qty_col = find_column(
        df,
        [
            "TTL_TRD_QNTY",
            "TOTAL_TRADED_QTY",
            "TOTAL_TRADED_QUANTITY",
        ],
    )

    turnover_lacs_col = find_column(
        df,
        [
            "TURNOVER_LACS",
        ],
    )

    traded_value_col = find_column(
        df,
        [
            "TOTAL_TRADED_VALUE",
            "TOTTRDVAL",
            "TURNOVER",
        ],
    )

    trades_col = find_column(
        df,
        [
            "NO_OF_TRADES",
            "TOTAL_TRADES",
        ],
    )

    isin_col = find_column(
        df,
        [
            "ISIN",
        ],
    )

    delivery_qty_col = find_column(
        df,
        [
            "DELIV_QTY",
            "DELIVERABLE_QTY",
            "DELIVERY_QTY",
            "DELIVERABLE_QTY",
        ],
    )

    delivery_pct_col = find_column(
        df,
        [
            "DELIV_PER",
            "DELIVERY_PCT",
            "DELIVERY_PERCENT",
        ],
    )

    print("")
    print("Column mapping detected:")
    print(f"  SYMBOL             : {symbol_col}")
    print(f"  SERIES             : {series_col}")
    print(f"  OPEN               : {open_col}")
    print(f"  HIGH               : {high_col}")
    print(f"  LOW                : {low_col}")
    print(f"  CLOSE              : {close_col}")
    print(f"  LAST               : {last_col}")
    print(f"  PREV_CLOSE         : {prev_close_col}")
    print(f"  TOTAL_TRADED_QTY   : {traded_qty_col}")
    print(f"  TURNOVER_LACS      : {turnover_lacs_col}")
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
        "TOTAL_TRADES": trades_col,
        "DELIVERY_QTY": delivery_qty_col,
        "DELIVERY_PCT": delivery_pct_col,
    }

    missing = [
        key
        for key, value in required.items()
        if value is None
    ]

    if missing:

        raise RuntimeError(
            "NSE bhavcopy-with-delivery is missing required "
            f"column(s): {missing}. "
            f"Actual columns returned by nselib: "
            f"{df.columns.tolist()}"
        )

    out = pd.DataFrame()

    # ------------------------------------------------------------------------
    # A:M
    # ------------------------------------------------------------------------

    # Write TRADE_DATE explicitly as a plain Python string for every row.
    # This prevents pandas/numpy NaN or datetime objects from reaching
    # the Google Sheets JSON request.
    trade_date_text = str(trade_date)
    out["TRADE_DATE"] = [trade_date_text] * len(df)

    out["SYMBOL"] = df[symbol_col]
    out["SERIES"] = df[series_col]
    out["OPEN"] = df[open_col]
    out["HIGH"] = df[high_col]
    out["LOW"] = df[low_col]
    out["CLOSE"] = df[close_col]
    out["LAST"] = df[last_col]
    out["PREV_CLOSE"] = df[prev_close_col]
    out["TOTAL_TRADED_QTY"] = df[traded_qty_col]

    # NSE's TURNOVER_LACS is in lakh rupees.
    # Convert to actual rupees for the existing RAW_DATA structure.
    if turnover_lacs_col is not None:

        turnover_lacs = pd.to_numeric(
            df[turnover_lacs_col],
            errors="coerce",
        )

        out["TOTAL_TRADED_VALUE"] = turnover_lacs * 100000

        print(
            "TOTAL_TRADED_VALUE mapped from TURNOVER_LACS "
            "and converted from ₹ lakh to ₹."
        )

    elif traded_value_col is not None:

        out["TOTAL_TRADED_VALUE"] = df[traded_value_col]

    else:

        out["TOTAL_TRADED_VALUE"] = None

    out["TOTAL_TRADES"] = df[trades_col]

    # The current nselib delivery bhavcopy does not return ISIN.
    # Preserve the RAW_DATA column and leave it blank for this download.
    if isin_col is not None:
        out["ISIN"] = df[isin_col]
    else:
        out["ISIN"] = None
        print(
            "ISIN column not returned by nselib; "
            "ISIN will be blank for these downloaded rows."
        )

    # ------------------------------------------------------------------------
    # N:O
    # ------------------------------------------------------------------------

    out["DELIVERY_QTY"] = df[delivery_qty_col]
    out["DELIVERY_PCT"] = df[delivery_pct_col]

    # ------------------------------------------------------------------------
    # Numeric conversion
    # ------------------------------------------------------------------------

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

    out["SYMBOL"] = (
        out["SYMBOL"]
        .astype(str)
        .str.strip()
    )

    out["SERIES"] = (
        out["SERIES"]
        .astype(str)
        .str.strip()
    )

    # Remove unusable rows.
    out = out[
        out["SYMBOL"].ne("")
        & out["SYMBOL"].ne("nan")
    ]

    # Exact A:O order.
    out = out[RAW_HEADERS]

    # Convert every cell to a JSON-safe plain Python value.
    # Google Sheets rejects NaN / NaT because JSON does not allow them.
    def json_safe(value):
        if value is None:
            return None

        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass

        # Convert numpy scalar values to native Python values.
        if hasattr(value, "item"):
            try:
                return value.item()
            except (ValueError, TypeError):
                pass

        return value

    # pandas 2.x removed DataFrame.applymap(). Use DataFrame.map()
    # when available, with a fallback for older pandas versions.
    if hasattr(out, "map"):
        out = out.map(json_safe)
    else:
        out = out.apply(lambda column: column.map(json_safe))

    # Final safety check: TRADE_DATE must never be blank/NaN.
    out["TRADE_DATE"] = trade_date_text

    print(f"Cleaned {len(out)} rows.")

    print(
        "Delivery Qty available:",
        sum(
            value is not None
            for value in out["DELIVERY_QTY"]
        ),
        "rows",
    )

    print(
        "Delivery % available:",
        sum(
            value is not None
            for value in out["DELIVERY_PCT"]
        ),
        "rows",
    )

    return out


# ============================================================================
# GOOGLE SHEETS CONNECTION
# ============================================================================

def get_worksheet(
    spreadsheet_id: str,
    sheet_name: str,
):
    import gspread
    from google.oauth2.service_account import Credentials

    credentials_json = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    if not credentials_json:

        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON GitHub secret "
            "is not available."
        )

    try:
        credentials_info = json.loads(credentials_json)

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON."
        ) from exc

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes,
    )

    gc = gspread.authorize(credentials)

    print(
        f"Opening spreadsheet: {spreadsheet_id}"
    )

    spreadsheet = gc.open_by_key(
        spreadsheet_id
    )

    print(
        f"Spreadsheet opened: {spreadsheet.title}"
    )

    try:

        worksheet = spreadsheet.worksheet(
            sheet_name
        )

    except gspread.WorksheetNotFound:

        print(
            f'Worksheet "{sheet_name}" not found. '
            f"Creating it."
        )

        worksheet = spreadsheet.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=20,
        )

    print(
        f"Worksheet opened: {worksheet.title}"
    )

    return spreadsheet, worksheet


# ============================================================================
# SHEET HEADER
# ============================================================================

def ensure_header(worksheet) -> None:

    current = worksheet.row_values(1)

    if not current:

        print(
            "RAW_DATA is empty. Creating A:O header."
        )

        worksheet.update(
            range_name="A1:O1",
            values=[RAW_HEADERS],
            value_input_option="USER_ENTERED",
        )

        return

    # Existing old A:M structure.
    old_header = RAW_HEADERS[:13]

    if current[:13] == old_header:

        print(
            "Existing RAW_DATA has A:M header. "
            "Extending it to A:O."
        )

        worksheet.update(
            range_name="A1:O1",
            values=[RAW_HEADERS],
            value_input_option="USER_ENTERED",
        )

        return

    # Already correct.
    if current[:15] == RAW_HEADERS:

        return

    print("Existing RAW_DATA header:")
    print(current)

    print("Expected RAW_DATA header:")
    print(RAW_HEADERS)

    raise RuntimeError(
        "RAW_DATA header does not match the expected structure."
    )


# ============================================================================
# GOOGLE SHEETS BATCH WRITE
# ============================================================================

def write_date_batch(
    spreadsheet,
    worksheet,
    df: pd.DataFrame,
) -> None:
    """
    Write one trade date as one complete batch.

    If the date exists:
        - delete those existing rows in one Sheets API batch request;
        - insert the fresh rows in their place.

    If the date does not exist:
        - append the fresh rows.

    This avoids thousands of individual worksheet.update() calls.
    """

    source_date = str(
        df["TRADE_DATE"].iloc[0]
    )

    print(
        f"Preparing Google Sheets batch write for "
        f"trade date {source_date}."
    )

    # ------------------------------------------------------------------------
    # Read current sheet once.
    # ------------------------------------------------------------------------

    values = worksheet.get_all_values()

    if not values:

        ensure_header(worksheet)

        values = worksheet.get_all_values()

    # ------------------------------------------------------------------------
    # Find rows for source date.
    # ------------------------------------------------------------------------

    matching_rows = []

    for row_number, row in enumerate(
        values[1:],
        start=2,
    ):

        if (
            row
            and len(row) > 0
            and str(row[0]).strip() == source_date
        ):

            matching_rows.append(row_number)

    # Convert the dataframe to ordinary Python lists and make absolutely
    # sure no NaN/NaT survives into the Google Sheets JSON payload.
    rows = []
    for row in df.astype(object).values.tolist():
        safe_row = []
        for value in row:
            if value is None:
                safe_row.append(None)
                continue

            try:
                missing = pd.isna(value)
            except (TypeError, ValueError):
                missing = False

            if isinstance(missing, bool) and missing:
                safe_row.append(None)
                continue

            if hasattr(value, "item"):
                try:
                    value = value.item()
                except (ValueError, TypeError):
                    pass

            safe_row.append(value)

        rows.append(safe_row)

    # Hard validation before making the API request.
    for row_index, row in enumerate(rows, start=1):
        for col_index, value in enumerate(row, start=1):
            # None is valid JSON and is intentionally used for blank
            # fields such as ISIN, which NSE does not return here.
            if value is None:
                continue

            try:
                missing = pd.isna(value)
            except (TypeError, ValueError):
                missing = False

            # pd.isna() can return an array-like value for some objects.
            # Only a scalar True means an unsafe missing value here.
            if isinstance(missing, bool) and missing:
                raise RuntimeError(
                    f"Unsafe NaN/NaT value remains at "
                    f"row {row_index}, column {col_index}."
                )

    print(
        f"Prepared {len(rows)} JSON-safe rows for Google Sheets."
    )

    # ------------------------------------------------------------------------
    # Case 1: date already exists.
    # ------------------------------------------------------------------------

    if matching_rows:

        first_row = min(matching_rows)

        print(
            f"Trade date {source_date} already has "
            f"{len(matching_rows)} rows."
        )

        print(
            f"Replacing those rows with {len(rows)} fresh rows."
        )

        # Google Sheets row indexes are zero-based in batch requests.
        start_index = first_row - 1
        end_index = start_index + len(matching_rows)

        sheet_id = worksheet.id

        # One batch request:
        # 1. delete existing date rows
        # 2. insert the new number of rows
        spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": start_index,
                                "endIndex": end_index,
                            }
                        }
                    },
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": start_index,
                                "endIndex": start_index + len(rows),
                            },
                            "inheritFromBefore": False,
                        }
                    },
                ]
            }
        )

        # One range write for the entire date.
        worksheet.update(
            range_name=f"A{first_row}:O{first_row + len(rows) - 1}",
            values=rows,
            value_input_option="USER_ENTERED",
        )

        print(
            f"Successfully replaced {source_date} "
            f"with {len(rows)} rows."
        )

        return

    # ------------------------------------------------------------------------
    # Case 2: new date.
    # ------------------------------------------------------------------------

    next_row = len(values) + 1

    print(
        f"Trade date {source_date} is new."
    )

    print(
        f"Appending {len(rows)} rows starting at row {next_row}."
    )

    worksheet.update(
        range_name=f"A{next_row}:O{next_row + len(rows) - 1}",
        values=rows,
        value_input_option="USER_ENTERED",
    )

    print(
        f"Successfully appended {len(rows)} rows "
        f"for {source_date}."
    )


# ============================================================================
# HISTORICAL BACKFILL
# ============================================================================

def run_historical_backfill(
    requested_date: date,
    spreadsheet_id: str,
    sheet_name: str,
) -> int:
    """Load historical NSE sessions into RAW_DATA without clearing history."""
    try:
        sessions_needed = int(os.environ.get(
            "BACKFILL_TRADING_DAYS",
            str(DEFAULT_BACKFILL_TRADING_DAYS)
        ))
    except ValueError:
        sessions_needed = DEFAULT_BACKFILL_TRADING_DAYS

    try:
        calendar_limit = int(os.environ.get(
            "BACKFILL_MAX_CALENDAR_DAYS",
            str(DEFAULT_BACKFILL_MAX_CALENDAR_DAYS)
        ))
    except ValueError:
        calendar_limit = DEFAULT_BACKFILL_MAX_CALENDAR_DAYS

    sessions_needed = max(1, sessions_needed)
    calendar_limit = max(sessions_needed, calendar_limit)

    print("=" * 70)
    print("NSE HISTORICAL BACKFILL")
    print("=" * 70)
    print(f"End date requested : {requested_date}")
    print(f"Sessions requested : {sessions_needed}")
    print(f"Calendar-day limit : {calendar_limit}")

    spreadsheet, worksheet = get_worksheet(spreadsheet_id, sheet_name)
    ensure_header(worksheet)

    loaded = 0
    checked = 0
    candidate = requested_date

    while loaded < sessions_needed and checked <= calendar_limit:
        if candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
            checked += 1
            continue

        print(f"Trying {candidate} ...")
        raw_df = download_bhavcopy_for_date(candidate)

        if raw_df is None:
            print(f"No usable bhavcopy for {candidate}.")
            candidate -= timedelta(days=1)
            checked += 1
            continue

        actual_trade_date = raw_df.attrs.get("trade_date", candidate)
        df = clean_bhavcopy(raw_df, actual_trade_date)

        if not df.empty:
            # write_date_batch() safely replaces an existing date or appends
            # a new date. Therefore rerunning the backfill is safe.
            write_date_batch(spreadsheet, worksheet, df)
            loaded += 1
            print(f"Loaded {actual_trade_date}: {len(df)} rows.")

        candidate -= timedelta(days=1)
        checked += 1

    print("=" * 70)
    print(f"BACKFILL COMPLETE: {loaded} trading sessions loaded.")
    print("=" * 70)

    if loaded < sessions_needed:
        raise RuntimeError(
            f"Only {loaded} sessions were loaded; "
            f"{sessions_needed} requested. Increase "
            "BACKFILL_MAX_CALENDAR_DAYS if required."
        )

    return loaded


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:

    requested_date = get_requested_date()

    mode = os.environ.get("MODE", "DAILY").strip().upper()

    spreadsheet_id = (
        os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()
        or DEFAULT_SPREADSHEET_ID
    )
        sheet_name = (
        os.environ.get("SHEET_NAME", "").strip()
        or DEFAULT_SHEET_NAME
    )

    if mode == "BACKFILL":
        run_historical_backfill(
            requested_date=requested_date,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
        )
        return 0

    if mode != "DAILY":
        raise RuntimeError("MODE must be DAILY or BACKFILL.")
    print("=" * 70)
    print("NSE BHAVCOPY + DELIVERY")
    print("=" * 70)

    print(
        f"Requested date : {requested_date}"
    )

    # ------------------------------------------------------------------------
    # Download latest available NSE copy.
    # ------------------------------------------------------------------------

    raw_df = download_latest_bhavcopy(
        requested_date
    )

    if raw_df is None:

        print(
            "No available NSE bhavcopy-with-delivery "
            "was found within the lookback period."
        )

        return 0

    actual_trade_date = raw_df.attrs.get(
        "trade_date",
        requested_date,
    )

    print(
        f"Downloaded date: {actual_trade_date}"
    )

    # ------------------------------------------------------------------------
    # Transform.
    # ------------------------------------------------------------------------

    df = clean_bhavcopy(
        raw_df,
        actual_trade_date,
    )

    if df.empty:

        print(
            "The downloaded NSE file contained no usable rows."
        )

        return 0

    # ------------------------------------------------------------------------
    # Google Sheets configuration.
    # ------------------------------------------------------------------------

    # Spreadsheet ID and worksheet name were resolved at the start of main().

    # ------------------------------------------------------------------------
    # Connect.
    # ------------------------------------------------------------------------

    spreadsheet, worksheet = get_worksheet(
        spreadsheet_id,
        sheet_name,
    )

    # ------------------------------------------------------------------------
    # Ensure A:O header.
    # ------------------------------------------------------------------------

    ensure_header(worksheet)

    # ------------------------------------------------------------------------
    # Batch write.
    # ------------------------------------------------------------------------

    write_date_batch(
        spreadsheet,
        worksheet,
        df,
    )

    # ------------------------------------------------------------------------
    # Final summary.
    # ------------------------------------------------------------------------

    print("")
    print("=" * 70)
    print("COMPLETED SUCCESSFULLY")
    print("=" * 70)

    print(
        f"Requested date  : {requested_date}"
    )

    print(
        f"Downloaded date : {actual_trade_date}"
    )

    print(
        f"Rows processed  : {len(df)}"
    )

    print(
        "Delivery Qty    :",
        df["DELIVERY_QTY"].notna().sum(),
    )

    print(
        "Delivery %      :",
        df["DELIVERY_PCT"].notna().sum(),
    )

    print(
        f"Sheet           : {sheet_name}"
    )

    print("=" * 70)

    return 0


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as exc:

        print("")
        print("=" * 70)
        print("FETCH SCRIPT FAILED")
        print("=" * 70)
        print(f"{type(exc).__name__}: {exc}")
        print("=" * 70)

        raise
