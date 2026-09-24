"""Simple 3-bucket portfolio engine.

Targets come from PORTFOLIO_CONFIG.
Actual Liquid and Conservative values come from BUCKET_VALUES.
Actual Equity value is calculated as Equity Cash + Open Positions.

BUCKET_VALUES is the simple user-editable source for non-equity buckets.
PORTFOLIO_STATE is output only.
"""

import json
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CONFIG_SHEET = "PORTFOLIO_CONFIG"
STATE_SHEET = "PORTFOLIO_STATE"
POSITIONS_SHEET = "POSITIONS"
CAPITAL_SHEET = "CAPITAL_MANAGEMENT"
BUCKET_VALUES_SHEET = "BUCKET_VALUES"

BUCKETS = ("LIQUID", "CONSERVATIVE", "EQUITY")


def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def get_google_client():
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()
    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID is missing.")
    if not credentials_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")

    credentials = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=SCOPES,
    )
    return gspread.authorize(credentials).open_by_key(spreadsheet_id)


def read_key_value_sheet(sheet):
    values = sheet.get_all_values()
    result = {}
    for row in values[1:]:
        if len(row) < 2:
            continue
        key = str(row[0]).strip()
        if key:
            result[key] = row[1]
    return result


def read_state(sheet):
    values = sheet.get_all_values()
    if len(values) < 2:
        return {}
    headers = [str(x).strip().upper() for x in values[0]]
    row = values[1]
    return {
        header: row[i] if i < len(row) else ""
        for i, header in enumerate(headers)
    }


def read_positions(sheet):
    values = sheet.get_all_values()
    if len(values) < 2:
        return []

    headers = [str(x).strip().upper() for x in values[0]]
    positions = []

    for row in values[1:]:
        if not any(str(x).strip() for x in row):
            continue
        positions.append({
            headers[i]: row[i] if i < len(row) else ""
            for i in range(len(headers))
        })
    return positions


def positions_value(positions):
    total = 0.0
    for p in positions:
        status = str(p.get("STATUS", "")).strip().upper()
        if status and status != "OPEN":
            continue

        qty = to_float(p.get("QUANTITY"))
        price = to_float(p.get("CURRENT_PRICE"))
        current = to_float(p.get("CURRENT_VALUE"))

        if current == 0 and qty > 0 and price > 0:
            current = qty * price

        total += current

    return total


def read_total_capital(sheet, fallback):
    values = sheet.get_all_values()
    if not values:
        return fallback

    headers = [str(x).strip().upper() for x in values[0]]
    required = {"ACTION", "BUCKET", "AMOUNT"}
    if not required.issubset(set(headers)):
        return fallback

    ai = headers.index("ACTION")
    bi = headers.index("BUCKET")
    mi = headers.index("AMOUNT")

    total = 0.0

    for row in values[1:]:
        action = str(row[ai] if ai < len(row) else "").strip().upper()
        bucket = str(row[bi] if bi < len(row) else "").strip().upper()
        amount = to_float(row[mi] if mi < len(row) else "")

        if bucket != "TOTAL":
            continue

        if action in {
            "INITIAL_CAPITAL",
            "ADD_CAPITAL",
            "DEPOSIT",
            "CAPITAL_ADDITION",
        }:
            total += abs(amount)
        elif action == "WITHDRAWAL":
            total -= abs(amount)

    return total if total > 0 else fallback


def ensure_bucket_values_sheet(spreadsheet, state, targets):
    try:
        sheet = spreadsheet.worksheet(BUCKET_VALUES_SHEET)
        return sheet
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(
            title=BUCKET_VALUES_SHEET,
            rows=10,
            cols=4,
        )

        liquid = to_float(state.get("LIQUID_VALUE"), targets["LIQUID"])
        conservative = to_float(
            state.get("CONSERVATIVE_VALUE"),
            targets["CONSERVATIVE"],
        )

        sheet.update(
            range_name="A1:D4",
            values=[
                ["BUCKET", "ACTUAL_VALUE", "MODE", "REMARKS"],
                ["LIQUID", liquid, "MANUAL", "Enter current actual value"],
                ["CONSERVATIVE", conservative, "MANUAL", "Enter current actual value"],
                ["EQUITY", "", "AUTO", "Calculated from equity cash + open positions"],
            ],
        )
        return sheet


def read_bucket_values(sheet, state, targets):
    values = sheet.get_all_values()
    actual = {}

    if len(values) >= 2:
        headers = [str(x).strip().upper() for x in values[0]]
        if "BUCKET" not in headers or "ACTUAL_VALUE" not in headers:
            raise RuntimeError(
                "BUCKET_VALUES must contain BUCKET and ACTUAL_VALUE columns."
            )

        bi = headers.index("BUCKET")
        vi = headers.index("ACTUAL_VALUE")

        for row in values[1:]:
            bucket = str(row[bi] if bi < len(row) else "").strip().upper()
            if bucket in BUCKETS:
                actual[bucket] = to_float(
                    row[vi] if vi < len(row) else "",
                    0.0,
                )

    if "LIQUID" not in actual:
        actual["LIQUID"] = to_float(
            state.get("LIQUID_VALUE"),
            targets["LIQUID"],
        )

    if "CONSERVATIVE" not in actual:
        actual["CONSERVATIVE"] = to_float(
            state.get("CONSERVATIVE_VALUE"),
            targets["CONSERVATIVE"],
        )

    return actual


def write_state(sheet, state):
    rows = [
        [
            "AS_OF_DATE",
            "TOTAL_CAPITAL",
            "LIQUID_TARGET",
            "CONSERVATIVE_TARGET",
            "EQUITY_TARGET",
            "LIQUID_VALUE",
            "CONSERVATIVE_VALUE",
            "EQUITY_VALUE",
            "EQUITY_AVAILABLE",
            "POSITIONS_VALUE",
            "TOTAL_PORTFOLIO_VALUE",
            "CASH_RESERVE",
        ],
        [
            state["AS_OF_DATE"],
            state["TOTAL_CAPITAL"],
            state["LIQUID_TARGET"],
            state["CONSERVATIVE_TARGET"],
            state["EQUITY_TARGET"],
            state["LIQUID_VALUE"],
            state["CONSERVATIVE_VALUE"],
            state["EQUITY_VALUE"],
            state["EQUITY_AVAILABLE"],
            state["POSITIONS_VALUE"],
            state["TOTAL_PORTFOLIO_VALUE"],
            state["CASH_RESERVE"],
        ],
    ]
    sheet.update(range_name="A1:L2", values=rows)


def main():
    dry_run = os.environ.get("DRY_RUN", "true").strip().lower() == "true"

    spreadsheet = get_google_client()

    config_sheet = spreadsheet.worksheet(CONFIG_SHEET)
    state_sheet = spreadsheet.worksheet(STATE_SHEET)
    positions_sheet = spreadsheet.worksheet(POSITIONS_SHEET)
    capital_sheet = spreadsheet.worksheet(CAPITAL_SHEET)

    config = read_key_value_sheet(config_sheet)
    existing_state = read_state(state_sheet)
    positions = read_positions(positions_sheet)

    total_capital = read_total_capital(
        capital_sheet,
        to_float(config.get("TOTAL_CAPITAL"), 500000),
    )

    liquid_pct = to_float(config.get("LIQUID_BUCKET_PCT"), 18)
    conservative_pct = to_float(config.get("CONSERVATIVE_BUCKET_PCT"), 22)
    equity_pct = to_float(config.get("EQUITY_BUCKET_PCT"), 60)

    if abs(liquid_pct + conservative_pct + equity_pct - 100.0) > 0.0001:
        raise ValueError("Bucket percentages must total 100%.")

    targets = {
        "LIQUID": total_capital * liquid_pct / 100.0,
        "CONSERVATIVE": total_capital * conservative_pct / 100.0,
        "EQUITY": total_capital * equity_pct / 100.0,
    }

    bucket_sheet = ensure_bucket_values_sheet(
        spreadsheet,
        existing_state,
        targets,
    )

    actuals = read_bucket_values(
        bucket_sheet,
        existing_state,
        targets,
    )

    pos_value = positions_value(positions)

    # Equity is always derived from the trading state.
    equity_cash = to_float(
        existing_state.get("EQUITY_AVAILABLE"),
        targets["EQUITY"],
    )
    equity_value = equity_cash + pos_value

    state = {
        "AS_OF_DATE": datetime.now().strftime("%Y-%m-%d"),
        "TOTAL_CAPITAL": total_capital,
        "LIQUID_TARGET": targets["LIQUID"],
        "CONSERVATIVE_TARGET": targets["CONSERVATIVE"],
        "EQUITY_TARGET": targets["EQUITY"],
        "LIQUID_VALUE": actuals["LIQUID"],
        "CONSERVATIVE_VALUE": actuals["CONSERVATIVE"],
        "EQUITY_VALUE": equity_value,
        "EQUITY_AVAILABLE": equity_cash,
        "POSITIONS_VALUE": pos_value,
        "TOTAL_PORTFOLIO_VALUE": (
            actuals["LIQUID"]
            + actuals["CONSERVATIVE"]
            + equity_value
        ),
        "CASH_RESERVE": equity_cash,
    }

    print("======================================")
    print("3-BUCKET ENGINE — SIMPLE ACTUAL VALUES")
    print("======================================")
    print(f"TOTAL_CAPITAL: {total_capital:.2f}")

    for bucket in BUCKETS:
        value = state[bucket + "_VALUE"]
        target = targets[bucket]
        print(f"{bucket}_TARGET: {target:.2f}")
        print(f"{bucket}_VALUE: {value:.2f}")
        print(f"{bucket}_DEVIATION: {value - target:.2f}")

    print(f"POSITIONS_VALUE: {pos_value:.2f}")
    print(f"TOTAL_PORTFOLIO_VALUE: {state['TOTAL_PORTFOLIO_VALUE']:.2f}")
    print("======================================")

    if dry_run:
        print("DRY RUN: PORTFOLIO_STATE will NOT be modified.")
    else:
        write_state(state_sheet, state)
        print("LIVE: PORTFOLIO_STATE updated successfully.")

    print("BUCKET ENGINE COMPLETED")


if __name__ == "__main__":
    main()
