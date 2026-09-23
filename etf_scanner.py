"""
ETF SCANNER — V1
=================
Strategy core shared by future live scanner and backtester.

Input:
    Google Sheet:
      - ETF_HISTORY
      - Category_Map

Output:
    Google Sheet:
      - ETF_SCANNER

Strategy:
    1. Exclude Debt, G-Sec, Liquid, Overnight, Money Market.
    2. For each remaining category, select the ETF with the highest
       20-trading-session average turnover.
    3. Delivery is a mandatory eligibility filter. The initial configurable
       threshold is 40% 20D average delivery.
    4. For each category winner, calculate 252-session high using DAILY HIGH.
    5. Correction = (252W HIGH - TODAY CLOSE) / 252W HIGH * 100.
    6. Among eligible category winners, select the greatest correction.
       Turnover is the tie-breaker.
    7. Today's CLOSE is the signal/execution price.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

import gspread
import pandas as pd


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

ETF_HISTORY_SHEET = "ETF_HISTORY"
CATEGORY_MAP_SHEET = "Category_Map"
OUTPUT_SHEET = "ETF_SCANNER"

LOOKBACK_TURNOVER_DAYS = 20
LOOKBACK_HIGH_DAYS = 252

# Initial configurable threshold. It is deliberately not hard-coded into
# the strategy logic so it can later be changed from a CONFIG sheet.
MIN_20D_AVG_DELIVERY_PCT = 40.0

EXCLUDED_CATEGORIES = {
    "DEBT",
    "G-SEC",
    "GSEC",
    "LIQUID",
    "OVERNIGHT",
    "MONEY MARKET",
}

OUTPUT_HEADERS = [
    "TRADE_DATE",
    "CATEGORY",
    "TOP_SYMBOL",
    "20D_AVG_TURNOVER",
    "TODAY_TURNOVER",
    "TODAY_DELIVERY_PCT",
    "20D_AVG_DELIVERY_PCT",
    "20D_AVG_DELIVERY_VALUE",
    "252W_HIGH",
    "TODAY_CLOSE",
    "CORRECTION_PCT",
    "ELIGIBLE",
    "EXCLUSION_REASON",
    "FINAL_RANK",
    "FINAL_SIGNAL",
]


# ---------------------------------------------------------------------------
# GOOGLE SHEETS CONNECTION
# ---------------------------------------------------------------------------

def get_gspread_client():
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable is missing."
        )

    try:
        credentials_info = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON."
        ) from exc

    return gspread.service_account_from_dict(credentials_info)


def get_spreadsheet():
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise RuntimeError(
            "GOOGLE_SPREADSHEET_ID environment variable is missing."
        )

    client = get_gspread_client()
    return client.open_by_key(spreadsheet_id)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def normalize_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_category(value) -> str:
    return normalize_text(value).upper().replace("  ", " ")


def is_excluded_category(category: str) -> bool:
    return normalize_category(category) in EXCLUDED_CATEGORIES


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if pd.isna(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def load_sheet_dataframe(worksheet) -> pd.DataFrame:
    values = worksheet.get_all_values()

    if not values:
        return pd.DataFrame()

    headers = [normalize_text(x) for x in values[0]]
    rows = values[1:]

    # Keep only columns represented by headers.
    rows = [row[:len(headers)] for row in rows]

    return pd.DataFrame(rows, columns=headers)


# ---------------------------------------------------------------------------
# INPUT LOADERS
# ---------------------------------------------------------------------------

def load_category_map(spreadsheet) -> Dict[str, str]:
    worksheet = spreadsheet.worksheet(CATEGORY_MAP_SHEET)
    df = load_sheet_dataframe(worksheet)

    if df.empty:
        raise RuntimeError("Category_Map is empty.")

    symbol_col = next(
        (c for c in df.columns if normalize_text(c).upper() == "SYMBOL"),
        None,
    )
    category_col = next(
        (
            c for c in df.columns
            if normalize_text(c).upper() == "CATEGORY"
        ),
        None,
    )

    if not symbol_col or not category_col:
        raise RuntimeError(
            "Category_Map must contain SYMBOL and CATEGORY columns."
        )

    mapping = {}

    for _, row in df.iterrows():
        symbol = normalize_text(row[symbol_col]).upper()
        category = normalize_text(row[category_col])

        if symbol and category:
            mapping[symbol] = category

    if not mapping:
        raise RuntimeError("No usable symbol/category mappings found.")

    return mapping


def load_history(spreadsheet, category_map: Dict[str, str]) -> pd.DataFrame:
    worksheet = spreadsheet.worksheet(ETF_HISTORY_SHEET)
    df = load_sheet_dataframe(worksheet)

    required = {
        "TRADE_DATE",
        "SYMBOL",
        "CATEGORY",
        "HIGH",
        "CLOSE",
        "TOTAL_TRADED_VALUE",
        "DELIVERY_QTY",
        "DELIVERY_PCT",
    }

    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(
            "ETF_HISTORY is missing required columns: "
            + ", ".join(sorted(missing))
        )

    # Category is retained from ETF_HISTORY when present, but Category_Map
    # remains authoritative for category membership.
    df["SYMBOL"] = df["SYMBOL"].map(
        lambda x: normalize_text(x).upper()
    )
    df["CATEGORY"] = df["SYMBOL"].map(category_map)

    df = df[df["CATEGORY"].notna()].copy()

    df["TRADE_DATE"] = pd.to_datetime(
        df["TRADE_DATE"],
        errors="coerce",
    )

    for col in [
        "HIGH",
        "CLOSE",
        "TOTAL_TRADED_VALUE",
        "DELIVERY_QTY",
        "DELIVERY_PCT",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=[
            "TRADE_DATE",
            "SYMBOL",
            "CATEGORY",
            "HIGH",
            "CLOSE",
            "TOTAL_TRADED_VALUE",
        ]
    )

    # One row per symbol/date is required for rolling calculations.
    df = (
        df.sort_values(["SYMBOL", "TRADE_DATE"])
        .drop_duplicates(
            subset=["TRADE_DATE", "SYMBOL"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return df


# ---------------------------------------------------------------------------
# STRATEGY ENGINE
# ---------------------------------------------------------------------------

def calculate_scanner(history: pd.DataFrame, as_of_date=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if history.empty:
        raise RuntimeError("ETF_HISTORY contains no usable ETF records.")

    if as_of_date is None:
        latest_date = history["TRADE_DATE"].max()
    else:
        latest_date = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(latest_date):
            raise RuntimeError(f"Invalid as_of_date: {as_of_date}")
        latest_date = pd.Timestamp(latest_date).normalize()

    # Backtests must use only information available on or before the signal date.
    history = history[history["TRADE_DATE"] <= latest_date].copy()

    # The selected session is the signal date.
    signal_df = history[
        history["TRADE_DATE"] == latest_date
    ].copy()

    if signal_df.empty:
        raise RuntimeError("No ETF data exists for the latest trade date.")

    # Only non-excluded categories participate.
    signal_categories = [
        c for c in signal_df["CATEGORY"].dropna().unique()
        if not is_excluded_category(c)
    ]

    candidates = []

    for category in sorted(signal_categories, key=str.upper):
        category_today = signal_df[
            signal_df["CATEGORY"] == category
        ].copy()

        # Step 1: highest 20-day average turnover determines the category
        # winner. We do NOT select on correction first.
        category_symbols = category_today["SYMBOL"].tolist()

        category_history = history[
            history["SYMBOL"].isin(category_symbols)
        ].copy()

        turnover_roll = (
            category_history
            .sort_values(["SYMBOL", "TRADE_DATE"])
            .groupby("SYMBOL", group_keys=False)["TOTAL_TRADED_VALUE"]
            .rolling(LOOKBACK_TURNOVER_DAYS, min_periods=LOOKBACK_TURNOVER_DAYS)
            .mean()
            .reset_index(name="AVG_TURNOVER_20D")
        )

        delivery_roll = (
            category_history
            .sort_values(["SYMBOL", "TRADE_DATE"])
            .groupby("SYMBOL", group_keys=False)["DELIVERY_PCT"]
            .rolling(
                LOOKBACK_TURNOVER_DAYS,
                min_periods=LOOKBACK_TURNOVER_DAYS,
            )
            .mean()
            .reset_index(name="AVG_DELIVERY_20D")
        )

        delivery_value = category_history.copy()
        delivery_value["DELIVERY_VALUE"] = (
            delivery_value["TOTAL_TRADED_VALUE"]
            * delivery_value["DELIVERY_PCT"]
            / 100.0
        )

        delivery_value_roll = (
            delivery_value
            .sort_values(["SYMBOL", "TRADE_DATE"])
            .groupby("SYMBOL", group_keys=False)["DELIVERY_VALUE"]
            .rolling(
                LOOKBACK_TURNOVER_DAYS,
                min_periods=LOOKBACK_TURNOVER_DAYS,
            )
            .mean()
            .reset_index(name="AVG_DELIVERY_VALUE_20D")
        )

        # The groupby/rolling output retains symbol as the first index level
        # and original row index as the second. Merge by original row index.
        turnover_roll = turnover_roll.set_index("level_1")
        delivery_roll = delivery_roll.set_index("level_1")
        delivery_value_roll = delivery_value_roll.set_index("level_1")

        category_history = category_history.copy()
        category_history["AVG_TURNOVER_20D"] = turnover_roll[
            "AVG_TURNOVER_20D"
        ]
        category_history["AVG_DELIVERY_20D"] = delivery_roll[
            "AVG_DELIVERY_20D"
        ]
        category_history["AVG_DELIVERY_VALUE_20D"] = delivery_value_roll[
            "AVG_DELIVERY_VALUE_20D"
        ]

        latest_metrics = category_history[
            category_history["TRADE_DATE"] == latest_date
        ].copy()

        if latest_metrics.empty:
            continue

        # First ranking criterion: highest 20D average turnover.
        latest_metrics = latest_metrics.sort_values(
            ["AVG_TURNOVER_20D", "SYMBOL"],
            ascending=[False, True],
            na_position="last",
        )

        winner = latest_metrics.iloc[0]

        symbol = winner["SYMBOL"]

        # 252-session high based on DAILY HIGH, including the signal session.
        symbol_history = history[
            history["SYMBOL"] == symbol
        ].sort_values("TRADE_DATE")

        last_252 = symbol_history.tail(LOOKBACK_HIGH_DAYS)

        # ---------------------------------------------------------------
        # CORPORATE-ACTION ADJUSTMENT FOR 27-FEB-2026 10:1 SPLIT
        # ---------------------------------------------------------------
        # ETF_HISTORY remains raw exchange data.  For the 252-session
        # high/correction calculation only, pre-split HIGH values are
        # adjusted to the post-split unit basis.
        #
        # Affected Kotak ETFs: BANKNIFTY1, CONS, SILVER1, NV20, MIDCAP.
        # Split effective date: 27-Feb-2026.
        split_symbols = {
            "BANKNIFTY1",
            "CONS",
            "SILVER1",
            "NV20",
            "MIDCAP",
        }

        price_history_252 = last_252.copy()

        if symbol in split_symbols:
            split_date = pd.Timestamp("2026-02-27")
            pre_split_mask = (
                price_history_252["TRADE_DATE"] < split_date
            )

            price_history_252.loc[pre_split_mask, "HIGH"] = (
                price_history_252.loc[pre_split_mask, "HIGH"] / 10.0
            )

        high_252 = price_history_252["HIGH"].max()

        today_row = signal_df[
            signal_df["SYMBOL"] == symbol
        ].iloc[0]

        today_close = safe_float(today_row["CLOSE"])
        today_turnover = safe_float(today_row["TOTAL_TRADED_VALUE"])
        today_delivery = safe_float(today_row["DELIVERY_PCT"])
        avg_turnover = safe_float(winner["AVG_TURNOVER_20D"])
        avg_delivery = safe_float(winner["AVG_DELIVERY_20D"])
        avg_delivery_value = safe_float(
            winner["AVG_DELIVERY_VALUE_20D"]
        )

        correction = None
        if high_252 and today_close is not None:
            correction = (
                (high_252 - today_close) / high_252 * 100.0
            )

        reason = ""
        eligible = True

        if avg_turnover is None:
            eligible = False
            reason = "Insufficient 20D turnover history"
        elif avg_delivery is None:
            eligible = False
            reason = "Insufficient 20D delivery history"
        elif len(last_252) < LOOKBACK_HIGH_DAYS:
            eligible = False
            reason = "Insufficient 252-session history"
        elif avg_delivery < MIN_20D_AVG_DELIVERY_PCT:
            eligible = False
            reason = (
                f"20D average delivery {avg_delivery:.2f}% "
                f"< {MIN_20D_AVG_DELIVERY_PCT:.2f}%"
            )
        elif correction is None:
            eligible = False
            reason = "Unable to calculate correction"

        candidates.append(
            {
                "TRADE_DATE": latest_date.date().isoformat(),
                "CATEGORY": winner["CATEGORY"],
                "TOP_SYMBOL": symbol,
                "20D_AVG_TURNOVER": avg_turnover,
                "TODAY_TURNOVER": today_turnover,
                "TODAY_DELIVERY_PCT": today_delivery,
                "20D_AVG_DELIVERY_PCT": avg_delivery,
                "20D_AVG_DELIVERY_VALUE": avg_delivery_value,
                "252W_HIGH": high_252,
                "TODAY_CLOSE": today_close,
                "CORRECTION_PCT": correction,
                "ELIGIBLE": "YES" if eligible else "NO",
                "EXCLUSION_REASON": reason,
                "FINAL_RANK": None,
                "FINAL_SIGNAL": "",
            }
        )

    result = pd.DataFrame(candidates, columns=OUTPUT_HEADERS)

    if result.empty:
        raise RuntimeError(
            "No category winners could be calculated."
        )

    # Final selection is ONLY among eligible category winners.
    eligible = result[
        result["ELIGIBLE"] == "YES"
    ].copy()

    if not eligible.empty:
        eligible = eligible.sort_values(
            ["CORRECTION_PCT", "20D_AVG_TURNOVER", "TOP_SYMBOL"],
            ascending=[False, False, True],
        )

        for rank, idx in enumerate(eligible.index, start=1):
            result.loc[idx, "FINAL_RANK"] = rank

        # One final ETF is selected.
        selected_idx = eligible.index[0]
        result.loc[selected_idx, "FINAL_SIGNAL"] = "BUY_CANDIDATE"

    # Re-sort for readable output: category first, then final rank.
    result = result.sort_values(
        ["FINAL_RANK", "CATEGORY"],
        na_position="last",
    ).reset_index(drop=True)

    return result, eligible


# ---------------------------------------------------------------------------
# SHEETS OUTPUT
# ---------------------------------------------------------------------------

def write_output(spreadsheet, result: pd.DataFrame) -> None:
    try:
        worksheet = spreadsheet.worksheet(OUTPUT_SHEET)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=OUTPUT_SHEET,
            rows=max(100, len(result) + 10),
            cols=len(OUTPUT_HEADERS),
        )

    required_rows = len(result) + 1

    if worksheet.row_count < required_rows:
        worksheet.add_rows(required_rows - worksheet.row_count)

    if worksheet.col_count < len(OUTPUT_HEADERS):
        worksheet.add_cols(len(OUTPUT_HEADERS) - worksheet.col_count)

    worksheet.clear()

    output = [OUTPUT_HEADERS]

    for _, row in result.iterrows():
        values = []
        for header in OUTPUT_HEADERS:
            value = row.get(header)

            if pd.isna(value):
                value = ""

            if isinstance(value, (float,)):
                value = round(value, 6)

            values.append(value)

        output.append(values)

    worksheet.update(
        range_name=f"A1:O{len(output)}",
        values=output,
        value_input_option="USER_ENTERED",
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("ETF SCANNER V1")
    print("=" * 70)

    spreadsheet = get_spreadsheet()

    category_map = load_category_map(spreadsheet)
    print(f"Category_Map symbols: {len(category_map)}")

    history = load_history(spreadsheet, category_map)

    print(f"ETF_HISTORY rows used: {len(history)}")
    print(
        "ETF_HISTORY date range: "
        f"{history['TRADE_DATE'].min().date()} to "
        f"{history['TRADE_DATE'].max().date()}"
    )

    unique_dates = history["TRADE_DATE"].nunique()
    print(f"Unique trading sessions: {unique_dates}")

    if unique_dates < LOOKBACK_HIGH_DAYS:
        raise RuntimeError(
            f"At least {LOOKBACK_HIGH_DAYS} trading sessions are required; "
            f"only {unique_dates} are available."
        )

    result, eligible = calculate_scanner(history)

    write_output(spreadsheet, result)

    print("-" * 70)
    print("CATEGORY WINNERS")
    print("-" * 70)

    for _, row in result.iterrows():
        print(
            f"{row['CATEGORY']}: {row['TOP_SYMBOL']} | "
            f"20D turnover={row['20D_AVG_TURNOVER']} | "
            f"20D delivery={row['20D_AVG_DELIVERY_PCT']}% | "
            f"correction={row['CORRECTION_PCT']}% | "
            f"eligible={row['ELIGIBLE']}"
        )

    print("-" * 70)

    if eligible.empty:
        print("FINAL SIGNAL: NONE — no category winner passed the delivery/history filters.")
    else:
        selected = eligible.sort_values(
            ["CORRECTION_PCT", "20D_AVG_TURNOVER", "TOP_SYMBOL"],
            ascending=[False, False, True],
        ).iloc[0]

        print(
            "FINAL SIGNAL: BUY_CANDIDATE | "
            f"{selected['TOP_SYMBOL']} | "
            f"category={selected['CATEGORY']} | "
            f"correction={selected['CORRECTION_PCT']:.2f}% | "
            f"close={selected['TODAY_CLOSE']}"
        )

    print("=" * 70)
    print("ETF SCANNER COMPLETE")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
