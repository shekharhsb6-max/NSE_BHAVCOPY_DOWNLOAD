from __future__ import annotations

import json
import os
from datetime import datetime

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

DEFAULT_SPREADSHEET_ID = "1D3E5lyH2QUq55AzsJqSbJj2tNkmOQht_8xUmt0mdvbk"
SHEET_NAME = "ETF_HISTORY"
HEADERS = [
    "TRADE_DATE", "SYMBOL", "CATEGORY", "HIGH", "CLOSE",
    "TOTAL_TRADED_QTY", "TOTAL_TRADED_VALUE", "DELIVERY_QTY", "DELIVERY_PCT",
]


def connect():
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip() or DEFAULT_SPREADSHEET_ID
    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not credentials_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")
    info = json.loads(credentials_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(spreadsheet_id).worksheet(SHEET_NAME)


def main():
    ws = connect()
    values = ws.get_all_values()
    if not values or values[0][:9] != HEADERS:
        raise RuntimeError("ETF_HISTORY header is not the expected 9-column structure.")

    df = pd.DataFrame(values[1:], columns=HEADERS)
    if df.empty:
        print("ETF_HISTORY is empty.")
        return 0

    df["TRADE_DATE_DT"] = pd.to_datetime(df["TRADE_DATE"], errors="coerce")
    df["HIGH_NUM"] = pd.to_numeric(df["HIGH"], errors="coerce")
    df["CLOSE_NUM"] = pd.to_numeric(df["CLOSE"], errors="coerce")

    repaired = []

    # Work chronologically so a repaired row never becomes the reference for
    # a later anomaly. We use the last known good close for each ETF.
    df = df.sort_values(["SYMBOL", "TRADE_DATE_DT"]).reset_index()

    last_good_close = {}

    for i, row in df.iterrows():
        symbol = str(row["SYMBOL"]).strip().upper()
        close = row["CLOSE_NUM"]
        high = row["HIGH_NUM"]

        if pd.isna(close) or pd.isna(high) or close <= 0 or high <= 0:
            continue

        previous = last_good_close.get(symbol)
        repaired_here = False

        if previous and previous > 0:
            close_ratio = float(close) / previous
            high_ratio = float(high) / previous
            median_ratio = (close_ratio + high_ratio) / 2.0
            factor = round(median_ratio)

            # Conservative detection of the exact class of corruption we found:
            # both HIGH and CLOSE are scaled by the same large integer factor.
            if (
                5 <= factor <= 20
                and abs(median_ratio / factor - 1.0) <= 0.02
                and abs(close_ratio / high_ratio - 1.0) <= 0.02
            ):
                new_close = float(close) / factor
                new_high = float(high) / factor

                print(
                    f"REPAIR {symbol} {row['TRADE_DATE']}: "
                    f"HIGH {high:g}->{new_high:g}, "
                    f"CLOSE {close:g}->{new_close:g}, factor={factor}x"
                )

                df.at[i, "HIGH_NUM"] = new_high
                df.at[i, "CLOSE_NUM"] = new_close
                repaired.append((row["index"], symbol, row["TRADE_DATE"], factor))
                close = new_close
                repaired_here = True

        # Only a validated/repaired price becomes the next reference.
        if not repaired_here:
            last_good_close[symbol] = float(close)
        else:
            last_good_close[symbol] = float(close)

    if not repaired:
        print("No conservative scaled-price anomalies found.")
        return 0

    # Map changes back to the original sheet order.
    original = values[1:]
    changes = {}
    for original_index, symbol, trade_date, factor in repaired:
        changes[original_index] = True

    # Use the sorted dataframe's original row index to update only HIGH/CLOSE.
    updates = []
    for i, row in df.iterrows():
        original_index = int(row["index"])
        if original_index not in changes:
            continue
        sheet_row = original_index + 2
        updates.append({
            "range": f"D{sheet_row}:E{sheet_row}",
            "values": [[float(row["HIGH_NUM"]), float(row["CLOSE_NUM"])]],
        })

    # Batch update all repairs in one API call.
    ws.batch_update(updates, value_input_option="USER_ENTERED")

    print(f"Repaired {len(updates)} ETF_HISTORY row(s).")
    for original_index, symbol, trade_date, factor in repaired:
        print(f"  {trade_date} | {symbol} | {factor}x scale correction")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
