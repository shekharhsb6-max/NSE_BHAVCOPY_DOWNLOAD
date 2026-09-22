import os
import json
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.environ["GOOGLE_SPREADSHEET_ID"]
SHEET_NAME = "ETF_HISTORY"

TARGET_DATE = "2026-02-18"
TARGET_SYMBOL = "BANKNIFTY1"

# Verified historical values for BANKNIFTY1 on 18-Feb-2026.
CORRECT_HIGH = 65.29
CORRECT_CLOSE = 63.72

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main():
    service_account_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

    credentials = Credentials.from_service_account_info(
        json.loads(service_account_json),
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(SHEET_NAME)

    # Read only the actual ETF_HISTORY data columns A:I.
    values = worksheet.get("A:I")

    if not values:
        raise RuntimeError("ETF_HISTORY is empty.")

    headers = [str(x).strip() for x in values[0]]

    required = [
        "TRADE_DATE",
        "SYMBOL",
        "CATEGORY",
        "HIGH",
        "CLOSE",
        "TOTAL_TRADED_QTY",
        "TOTAL_TRADED_VALUE",
        "DELIVERY_QTY",
        "DELIVERY_PCT",
    ]

    if headers[:9] != required:
        raise RuntimeError(
            "Unexpected ETF_HISTORY headers in A:I.\n"
            f"Found: {headers[:9]}\n"
            f"Expected: {required}"
        )

    target_row = None

    for sheet_row, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * (9 - len(row))
        trade_date = str(padded[0]).strip()
        symbol = str(padded[1]).strip()

        if trade_date == TARGET_DATE and symbol == TARGET_SYMBOL:
            target_row = sheet_row
            old_high = padded[3]
            old_close = padded[4]
            break

    if target_row is None:
        raise RuntimeError(
            f"Could not find {TARGET_SYMBOL} on {TARGET_DATE} in ETF_HISTORY."
        )

    print(f"Found {TARGET_SYMBOL} on {TARGET_DATE} at sheet row {target_row}.")
    print(f"Old HIGH  = {old_high}")
    print(f"Old CLOSE = {old_close}")
    print(f"New HIGH  = {CORRECT_HIGH}")
    print(f"New CLOSE = {CORRECT_CLOSE}")

    # Change ONLY HIGH (column D) and CLOSE (column E).
    worksheet.update_cell(target_row, 4, CORRECT_HIGH)
    worksheet.update_cell(target_row, 5, CORRECT_CLOSE)

    # Read back the row to verify the write.
    check = worksheet.get(f"A{target_row}:I{target_row}")[0]

    verified_high = float(check[3])
    verified_close = float(check[4])

    if abs(verified_high - CORRECT_HIGH) > 0.0001:
        raise RuntimeError(
            f"HIGH verification failed: got {verified_high}, "
            f"expected {CORRECT_HIGH}"
        )

    if abs(verified_close - CORRECT_CLOSE) > 0.0001:
        raise RuntimeError(
            f"CLOSE verification failed: got {verified_close}, "
            f"expected {CORRECT_CLOSE}"
        )

    print("SUCCESS: BANKNIFTY1 18-Feb-2026 price row repaired and verified.")
    print(f"Verified HIGH  = {verified_high}")
    print(f"Verified CLOSE = {verified_close}")


if __name__ == "__main__":
    main()
