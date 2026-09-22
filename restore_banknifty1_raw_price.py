import os
import json
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.environ["GOOGLE_SPREADSHEET_ID"]
SHEET_NAME = "ETF_HISTORY"

TARGET_DATE = "2026-02-18"
TARGET_SYMBOL = "BANKNIFTY1"

# Restore the original raw NSE values.
ORIGINAL_HIGH = 652.87
ORIGINAL_CLOSE = 637.24

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main():
    credentials = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)
    worksheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    values = worksheet.get("A:I")

    if not values:
        raise RuntimeError("ETF_HISTORY is empty.")

    headers = [str(x).strip() for x in values[0]]

    expected = [
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

    if headers[:9] != expected:
        raise RuntimeError(
            f"Unexpected ETF_HISTORY headers A:I: {headers[:9]}"
        )

    target_row = None

    for sheet_row, row in enumerate(values[1:], start=2):
        row = list(row) + [""] * (9 - len(row))

        if (
            str(row[0]).strip() == TARGET_DATE
            and str(row[1]).strip() == TARGET_SYMBOL
        ):
            target_row = sheet_row

            print(f"Found target row: {sheet_row}")
            print(f"Current HIGH = {row[3]}")
            print(f"Current CLOSE = {row[4]}")
            break

    if target_row is None:
        raise RuntimeError(
            f"{TARGET_SYMBOL} on {TARGET_DATE} was not found."
        )

    # Restore ONLY HIGH and CLOSE.
    worksheet.update_cell(
        target_row,
        4,
        ORIGINAL_HIGH
    )

    worksheet.update_cell(
        target_row,
        5,
        ORIGINAL_CLOSE
    )

    # Verify the written values.
    check = worksheet.get(
        f"A{target_row}:I{target_row}"
    )[0]

    high = float(check[3])
    close = float(check[4])

    if (
        abs(high - ORIGINAL_HIGH) > 0.0001
        or abs(close - ORIGINAL_CLOSE) > 0.0001
    ):
        raise RuntimeError(
            f"Verification failed. HIGH={high}, CLOSE={close}"
        )

    print("SUCCESS: Raw BANKNIFTY1 data restored.")
    print(f"18-Feb-2026 HIGH  = {high}")
    print(f"18-Feb-2026 CLOSE = {close}")


if __name__ == "__main__":
    main()
