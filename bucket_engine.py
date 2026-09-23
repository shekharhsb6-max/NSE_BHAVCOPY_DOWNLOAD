"""
3-BUCKET PORTFOLIO ENGINE — CAPITAL MANAGEMENT INTEGRATED

Reads:
    PORTFOLIO_CONFIG
    PORTFOLIO_STATE
    POSITIONS
    CAPITAL_MANAGEMENT

The CAPITAL_MANAGEMENT ledger is now the source for TOTAL_CAPITAL.

Expected CAPITAL_MANAGEMENT columns:
    DATE
    ACTION
    BUCKET
    AMOUNT
    BALANCE_AFTER
    REFERENCE
    REMARKS

For total-capital movements, use BUCKET = TOTAL:
    INITIAL_CAPITAL
    ADD_CAPITAL
    WITHDRAWAL

AMOUNT convention:
    additions are positive
    withdrawals are negative

BALANCE_AFTER is retained for user visibility/audit but is not used
as the source of truth; the engine calculates total capital from the ledger.

This version:
- Calculates 18% Liquid / 22% Conservative / 60% Equity.
- Reads existing equity cash and open positions.
- Does NOT move money between buckets.
- Does NOT place broker orders.
- Does NOT modify CAPITAL_MANAGEMENT.
- Updates PORTFOLIO_STATE only when DRY_RUN=false.
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


def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def to_int(value, default=0):
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def get_google_client():
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()
    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID is missing.")

    if not credentials_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")

    credentials_info = json.loads(credentials_json)
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
    )

    return gspread.authorize(credentials).open_by_key(spreadsheet_id)


def read_key_value_sheet(sheet):
    values = sheet.get_all_values()

    if not values:
        return {}

    result = {}
    for row in values[1:]:
        if len(row) < 2:
            continue

        key = str(row[0]).strip()
        value = row[1]

        if key:
            result[key] = value

    return result


def read_positions(sheet):
    values = sheet.get_all_values()

    if len(values) < 2:
        return []

    headers = [str(x).strip() for x in values[0]]
    positions = []

    for row in values[1:]:
        if not any(str(x).strip() for x in row):
            continue

        item = {}
        for i, header in enumerate(headers):
            item[header] = row[i] if i < len(row) else ""

        positions.append(item)

    return positions


def read_capital_ledger(sheet):
    values = sheet.get_all_values()

    if len(values) < 2:
        raise RuntimeError(
            "CAPITAL_MANAGEMENT has no transaction rows."
        )

    headers = [str(x).strip().upper() for x in values[0]]

    required = {"ACTION", "BUCKET", "AMOUNT"}
    missing = required - set(headers)

    if missing:
        raise RuntimeError(
            "CAPITAL_MANAGEMENT is missing required columns: "
            + ", ".join(sorted(missing))
        )

    action_index = headers.index("ACTION")
    bucket_index = headers.index("BUCKET")
    amount_index = headers.index("AMOUNT")

    total_capital = 0.0
    transaction_count = 0

    for row in values[1:]:
        if not any(str(x).strip() for x in row):
            continue

        action = (
            str(row[action_index]).strip().upper()
            if action_index < len(row)
            else ""
        )
        bucket = (
            str(row[bucket_index]).strip().upper()
            if bucket_index < len(row)
            else ""
        )
        amount = (
            to_float(row[amount_index])
            if amount_index < len(row)
            else 0.0
        )

        if bucket != "TOTAL":
            continue

        if action == "WITHDRAWAL":
            total_capital -= abs(amount)
        elif action in {
            "INITIAL_CAPITAL",
            "ADD_CAPITAL",
            "DEPOSIT",
            "CAPITAL_ADDITION",
        }:
            total_capital += abs(amount)
        else:
            # Unknown TOTAL actions are ignored rather than silently
            # changing capital.
            continue

        transaction_count += 1

    if transaction_count == 0:
        raise RuntimeError(
            "No recognised TOTAL capital transactions found in "
            "CAPITAL_MANAGEMENT."
        )

    if total_capital < 0:
        raise RuntimeError(
            f"Calculated total capital is negative: {total_capital:.2f}"
        )

    return total_capital, transaction_count


def calculate_equity_positions_value(positions):
    total = 0.0

    for position in positions:
        status = str(position.get("STATUS", "")).strip().upper()

        if status and status != "OPEN":
            continue

        current_value = to_float(position.get("CURRENT_VALUE"))

        if current_value == 0:
            quantity = to_int(position.get("QUANTITY"))
            current_price = to_float(position.get("CURRENT_PRICE"))
            current_value = quantity * current_price

        total += current_value

    return total


def read_existing_state(state_sheet):
    values = state_sheet.get_all_values()

    if len(values) < 2:
        return {}

    headers = [str(x).strip().upper() for x in values[0]]
    row = values[1]

    result = {}
    for i, header in enumerate(headers):
        result[header] = row[i] if i < len(row) else ""

    return result


def calculate_bucket_state(
    config,
    positions,
    state_sheet,
    total_capital,
    capital_transactions,
):
    liquid_pct = to_float(config.get("LIQUID_BUCKET_PCT"), 18)
    conservative_pct = to_float(
        config.get("CONSERVATIVE_BUCKET_PCT"),
        22,
    )
    equity_pct = to_float(config.get("EQUITY_BUCKET_PCT"), 60)

    pct_total = liquid_pct + conservative_pct + equity_pct

    if abs(pct_total - 100.0) > 0.0001:
        raise ValueError(
            f"Bucket percentages must total 100%. "
            f"Current total={pct_total:.4f}%."
        )

    liquid_target = total_capital * liquid_pct / 100.0
    conservative_target = total_capital * conservative_pct / 100.0
    equity_target = total_capital * equity_pct / 100.0

    positions_value = calculate_equity_positions_value(positions)

    existing_state = read_existing_state(state_sheet)

    # Existing equity cash remains authoritative until the capital allocation
    # and transfer/rebalancing layer is implemented.
    equity_cash = to_float(
        existing_state.get("EQUITY_AVAILABLE"),
        0.0,
    )

    if (
        equity_cash == 0
        and positions_value == 0
        and not existing_state
    ):
        equity_cash = equity_target

    equity_value = equity_cash + positions_value

    liquid_value = to_float(
        existing_state.get("LIQUID_VALUE"),
        liquid_target,
    )

    conservative_value = to_float(
        existing_state.get("CONSERVATIVE_VALUE"),
        conservative_target,
    )

    total_portfolio_value = (
        liquid_value
        + conservative_value
        + equity_value
    )

    return {
        "AS_OF_DATE": datetime.now().strftime("%Y-%m-%d"),
        "TOTAL_CAPITAL": total_capital,
        "LIQUID_TARGET": liquid_target,
        "CONSERVATIVE_TARGET": conservative_target,
        "EQUITY_TARGET": equity_target,
        "LIQUID_VALUE": liquid_value,
        "CONSERVATIVE_VALUE": conservative_value,
        "EQUITY_AVAILABLE": equity_cash,
        "POSITIONS_VALUE": positions_value,
        "EQUITY_VALUE": equity_value,
        "TOTAL_PORTFOLIO_VALUE": total_portfolio_value,
        "CASH_RESERVE": equity_cash,
        "LIQUID_DEVIATION": liquid_value - liquid_target,
        "CONSERVATIVE_DEVIATION": (
            conservative_value - conservative_target
        ),
        "EQUITY_DEVIATION": equity_value - equity_target,
        "CAPITAL_TRANSACTIONS": capital_transactions,
    }


def print_state(state):
    print("======================================")
    print("3-BUCKET ENGINE")
    print("======================================")
    print(f"TOTAL_CAPITAL: {state['TOTAL_CAPITAL']:.2f}")
    print(f"LIQUID_TARGET: {state['LIQUID_TARGET']:.2f}")
    print(f"LIQUID_VALUE: {state['LIQUID_VALUE']:.2f}")
    print(
        f"LIQUID_DEVIATION: "
        f"{state['LIQUID_DEVIATION']:.2f}"
    )
    print(
        f"CONSERVATIVE_TARGET: "
        f"{state['CONSERVATIVE_TARGET']:.2f}"
    )
    print(
        f"CONSERVATIVE_VALUE: "
        f"{state['CONSERVATIVE_VALUE']:.2f}"
    )
    print(
        f"CONSERVATIVE_DEVIATION: "
        f"{state['CONSERVATIVE_DEVIATION']:.2f}"
    )
    print(f"EQUITY_TARGET: {state['EQUITY_TARGET']:.2f}")
    print(
        f"EQUITY_AVAILABLE: "
        f"{state['EQUITY_AVAILABLE']:.2f}"
    )
    print(
        f"POSITIONS_VALUE: "
        f"{state['POSITIONS_VALUE']:.2f}"
    )
    print(f"EQUITY_VALUE: {state['EQUITY_VALUE']:.2f}")
    print(
        f"EQUITY_DEVIATION: "
        f"{state['EQUITY_DEVIATION']:.2f}"
    )
    print(
        f"TOTAL_PORTFOLIO_VALUE: "
        f"{state['TOTAL_PORTFOLIO_VALUE']:.2f}"
    )
    print("======================================")


def update_state_sheet(sheet, state):
    rows = [
        [
            "AS_OF_DATE",
            "TOTAL_CAPITAL",
            "LIQUID_TARGET",
            "CONSERVATIVE_TARGET",
            "EQUITY_TARGET",
            "LIQUID_VALUE",
            "CONSERVATIVE_VALUE",
            "EQUITY_AVAILABLE",
            "POSITIONS_VALUE",
            "EQUITY_VALUE",
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
            state["EQUITY_AVAILABLE"],
            state["POSITIONS_VALUE"],
            state["EQUITY_VALUE"],
            state["TOTAL_PORTFOLIO_VALUE"],
            state["CASH_RESERVE"],
        ],
    ]

    sheet.update(
        range_name="A1:L2",
        values=rows,
    )


def main():
    dry_run = (
        os.environ.get("DRY_RUN", "true").strip().lower()
        == "true"
    )

    spreadsheet = get_google_client()

    config_sheet = spreadsheet.worksheet(CONFIG_SHEET)
    state_sheet = spreadsheet.worksheet(STATE_SHEET)
    positions_sheet = spreadsheet.worksheet(POSITIONS_SHEET)
    capital_sheet = spreadsheet.worksheet(CAPITAL_SHEET)

    config = read_key_value_sheet(config_sheet)
    positions = read_positions(positions_sheet)

    total_capital, capital_transactions = read_capital_ledger(
        capital_sheet
    )

    state = calculate_bucket_state(
        config=config,
        positions=positions,
        state_sheet=state_sheet,
        total_capital=total_capital,
        capital_transactions=capital_transactions,
    )

    print_state(state)

    print(
        f"CAPITAL_TRANSACTIONS: "
        f"{capital_transactions}"
    )

    if dry_run:
        print("")
        print(
            "DRY RUN: PORTFOLIO_STATE "
            "will NOT be modified."
        )
    else:
        update_state_sheet(state_sheet, state)
        print("")
        print(
            "LIVE: PORTFOLIO_STATE "
            "updated successfully."
        )

    print("")
    print("BUCKET ENGINE COMPLETED")


if __name__ == "__main__":
    main()
