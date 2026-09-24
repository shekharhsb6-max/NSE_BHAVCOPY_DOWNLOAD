"""3-BUCKET PORTFOLIO ENGINE — ACTUAL BUCKET VALUE TRACKING

SOURCE OF TRUTH
----------------
TARGET VALUES come from PORTFOLIO_CONFIG percentages.

ACTUAL VALUES come from:
1. Latest SET_VALUE row in CAPITAL_MANAGEMENT for LIQUID / CONSERVATIVE.
2. Latest SET_VALUE row in CAPITAL_MANAGEMENT for EQUITY.
3. If SET_VALUE rows do not yet exist, the current PORTFOLIO_STATE values are
   used as the migration baseline and LIVE mode writes those three baseline
   rows.

TRANSFER / WITHDRAWAL / DEPOSIT rows are retained as audit records. The
control center writes a SET_VALUE snapshot for affected buckets so repeated
Bucket Engine runs do not replay the same movement.

The engine does not place broker orders.
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
        item = {
            headers[i]: row[i] if i < len(row) else ""
            for i in range(len(headers))
        }
        positions.append(item)

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


def read_capital_ledger(sheet):
    values = sheet.get_all_values()
    if not values:
        raise RuntimeError("CAPITAL_MANAGEMENT is empty.")

    headers = [str(x).strip().upper() for x in values[0]]
    required = {"ACTION", "BUCKET", "AMOUNT"}
    missing = required - set(headers)
    if missing:
        raise RuntimeError(
            "CAPITAL_MANAGEMENT is missing required columns: "
            + ", ".join(sorted(missing))
        )

    ai = headers.index("ACTION")
    bi = headers.index("BUCKET")
    mi = headers.index("AMOUNT")

    rows = []
    total_capital = 0.0

    for row_number, row in enumerate(values[1:], start=2):
        if not any(str(x).strip() for x in row):
            continue

        action = str(row[ai] if ai < len(row) else "").strip().upper()
        bucket = str(row[bi] if bi < len(row) else "").strip().upper()
        amount = to_float(row[mi] if mi < len(row) else "")

        rows.append({
            "ROW": row_number,
            "ACTION": action,
            "BUCKET": bucket,
            "AMOUNT": amount,
        })

        if bucket == "TOTAL":
            if action in {
                "INITIAL_CAPITAL",
                "ADD_CAPITAL",
                "DEPOSIT",
                "CAPITAL_ADDITION",
            }:
                total_capital += abs(amount)
            elif action == "WITHDRAWAL":
                total_capital -= abs(amount)

    if total_capital < 0:
        raise RuntimeError(
            f"Calculated TOTAL_CAPITAL is negative: {total_capital:.2f}"
        )

    return rows, total_capital


def latest_bucket_values(rows):
    latest = {}
    latest_row = {}

    for item in rows:
        bucket = item["BUCKET"]
        if bucket not in BUCKETS:
            continue
        if item["ACTION"] in {"SET_VALUE", "BUCKET_VALUE"}:
            latest[bucket] = item["AMOUNT"]
            latest_row[bucket] = item["ROW"]

    return latest, latest_row


def append_baseline_rows(capital_sheet, current):
    today = datetime.now().strftime("%Y-%m-%d")
    rows = [
        [
            today,
            "SET_VALUE",
            bucket,
            current[bucket],
            current[bucket],
            "BUCKET_ENGINE_MIGRATION",
            "Opening actual bucket value",
        ]
        for bucket in BUCKETS
    ]
    capital_sheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )


def apply_bucket_movements(rows, values, latest_rows):
    """
    Apply movement rows that occurred after each bucket's latest SET_VALUE.
    This makes SET_VALUE a snapshot/checkpoint and prevents replay.
    """
    result = dict(values)

    for item in rows:
        action = item["ACTION"]
        bucket = item["BUCKET"]
        row_number = item["ROW"]
        amount = item["AMOUNT"]

        if action == "TRANSFER" and "->" in bucket:
            from_bucket, to_bucket = [
                x.strip().upper() for x in bucket.split("->", 1)
            ]
            if from_bucket not in BUCKETS or to_bucket not in BUCKETS:
                continue

            if row_number > latest_rows.get(from_bucket, 0):
                result[from_bucket] -= abs(amount)
            if row_number > latest_rows.get(to_bucket, 0):
                result[to_bucket] += abs(amount)

        elif action in {"WITHDRAWAL", "DEPOSIT", "ADD_CAPITAL"}:
            if bucket in BUCKETS and row_number > latest_rows.get(bucket, 0):
                if action == "WITHDRAWAL":
                    result[bucket] -= abs(amount)
                else:
                    result[bucket] += abs(amount)

    return result


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

    rows, total_capital = read_capital_ledger(capital_sheet)

    if total_capital <= 0:
        total_capital = to_float(
            config.get("TOTAL_CAPITAL"),
            500000,
        )

    liquid_pct = to_float(config.get("LIQUID_BUCKET_PCT"), 18)
    conservative_pct = to_float(
        config.get("CONSERVATIVE_BUCKET_PCT"), 22
    )
    equity_pct = to_float(config.get("EQUITY_BUCKET_PCT"), 60)

    pct_total = liquid_pct + conservative_pct + equity_pct
    if abs(pct_total - 100.0) > 0.0001:
        raise ValueError(
            f"Bucket percentages must total 100%. Current total={pct_total:.4f}%."
        )

    targets = {
        "LIQUID": total_capital * liquid_pct / 100.0,
        "CONSERVATIVE": total_capital * conservative_pct / 100.0,
        "EQUITY": total_capital * equity_pct / 100.0,
    }

    actuals, latest_rows = latest_bucket_values(rows)

    # One-time migration: establish actual bucket values from the current
    # PORTFOLIO_STATE. This does not change the current values.
    if not actuals:
        actuals = {
            "LIQUID": to_float(
                existing_state.get("LIQUID_VALUE"),
                targets["LIQUID"],
            ),
            "CONSERVATIVE": to_float(
                existing_state.get("CONSERVATIVE_VALUE"),
                targets["CONSERVATIVE"],
            ),
            "EQUITY": to_float(
                existing_state.get("EQUITY_VALUE"),
                to_float(
                    existing_state.get("EQUITY_AVAILABLE"),
                    targets["EQUITY"],
                ) + positions_value(positions),
            ),
        }

        if not dry_run:
            append_baseline_rows(capital_sheet, actuals)
            rows, _ = read_capital_ledger(capital_sheet)
            actuals, latest_rows = latest_bucket_values(rows)

    # If only some buckets have checkpoints, use their current state values
    # for the missing buckets.
    for bucket in BUCKETS:
        if bucket not in actuals:
            actuals[bucket] = to_float(
                existing_state.get(bucket + "_VALUE"),
                targets[bucket],
            )
            latest_rows[bucket] = 0

    actuals = apply_bucket_movements(rows, actuals, latest_rows)

    pos_value = positions_value(positions)

    # Equity cash is the available equity amount maintained by the Trading
    # Engine / Mobile Control Center. Equity bucket value is cash + positions.
    equity_cash = to_float(
        existing_state.get("EQUITY_AVAILABLE"),
        max(0.0, actuals["EQUITY"] - pos_value),
    )

    # If an equity SET_VALUE checkpoint exists, use it as the current bucket
    # value. Otherwise derive it from the current trading state.
    if "EQUITY" in latest_rows and latest_rows["EQUITY"] > 0:
        equity_value = actuals["EQUITY"]
        equity_cash = max(0.0, equity_value - pos_value)
    else:
        equity_value = equity_cash + pos_value
        actuals["EQUITY"] = equity_value

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
    print("3-BUCKET ENGINE — ACTUAL VALUES")
    print("======================================")
    print(f"TOTAL_CAPITAL: {total_capital:.2f}")
    for bucket in BUCKETS:
        print(f"{bucket}_TARGET: {targets[bucket]:.2f}")
        print(f"{bucket}_VALUE: {state[bucket + '_VALUE']:.2f}")
        print(
            f"{bucket}_DEVIATION: "
            f"{state[bucket + '_VALUE'] - targets[bucket]:.2f}"
        )
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
