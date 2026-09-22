import os
import json
import math
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


# ============================================================
# CONFIGURATION
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_ID = os.environ["GOOGLE_SPREADSHEET_ID"]

CONFIG_SHEET = "PORTFOLIO_CONFIG"
STATE_SHEET = "PORTFOLIO_STATE"
POSITIONS_SHEET = "POSITIONS"
LEDGER_SHEET = "TRADE_LEDGER"
SCANNER_SHEET = "ETF_SCANNER"
HISTORY_SHEET = "ETF_HISTORY"


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_client():
    credentials = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=SCOPES,
    )

    return gspread.authorize(credentials)


def get_sheet(client, name):
    return client.open_by_key(SPREADSHEET_ID).worksheet(name)


# ============================================================
# HELPERS
# ============================================================

def number(value, default=0.0):
    try:
        if value is None or value == "":
            return default

        return float(str(value).replace(",", "").replace("%", ""))

    except Exception:
        return default


def whole_units(cash, price):
    if price <= 0:
        return 0

    return math.floor(cash / price)


def today_string():
    return datetime.now().strftime("%Y-%m-%d")


# ============================================================
# CONFIG
# ============================================================

def read_config(sheet):
    values = sheet.get_all_values()

    config = {}

    for row in values[1:]:
        if len(row) >= 2 and row[0]:
            config[row[0].strip()] = number(row[1])

    return config


# ============================================================
# POSITIONS
# ============================================================

POSITION_HEADERS = [
    "SYMBOL",
    "CATEGORY",
    "QUANTITY",
    "AVG_COST",
    "INVESTED_VALUE",
    "CURRENT_PRICE",
    "CURRENT_VALUE",
    "UNREALIZED_PNL",
    "UNREALIZED_PNL_PCT",
    "AVERAGING_BUYS",
    "LAST_BUY_DATE",
    "TARGET_PRICE",
    "STATUS",
]


def read_positions(sheet):

    values = sheet.get_all_values()

    positions = []

    if len(values) <= 1:
        return positions

    for row in values[1:]:

        row = row + [""] * (len(POSITION_HEADERS) - len(row))

        symbol = row[0].strip()

        if not symbol:
            continue

        positions.append({
            "SYMBOL": symbol,
            "CATEGORY": row[1],
            "QUANTITY": int(number(row[2])),
            "AVG_COST": number(row[3]),
            "INVESTED_VALUE": number(row[4]),
            "CURRENT_PRICE": number(row[5]),
            "CURRENT_VALUE": number(row[6]),
            "UNREALIZED_PNL": number(row[7]),
            "UNREALIZED_PNL_PCT": number(row[8]),
            "AVERAGING_BUYS": int(number(row[9])),
            "LAST_BUY_DATE": row[10],
            "TARGET_PRICE": number(row[11]),
            "STATUS": row[12],
        })

    return positions


def write_positions(sheet, positions):

    rows = [POSITION_HEADERS]

    for p in positions:

        rows.append([
            p["SYMBOL"],
            p["CATEGORY"],
            p["QUANTITY"],
            p["AVG_COST"],
            p["INVESTED_VALUE"],
            p["CURRENT_PRICE"],
            p["CURRENT_VALUE"],
            p["UNREALIZED_PNL"],
            p["UNREALIZED_PNL_PCT"],
            p["AVERAGING_BUYS"],
            p["LAST_BUY_DATE"],
            p["TARGET_PRICE"],
            p["STATUS"],
        ])
    if DRY_RUN:
        print("DRY RUN: POSITIONS sheet will NOT be modified.")
        return
    sheet.clear()
    sheet.update(
        f"A1:M{len(rows)}",
        rows,
    )


# ============================================================
# ETF SCANNER
# ============================================================

def read_scanner(sheet):

    values = sheet.get_all_values()

    if len(values) <= 1:
        return []

    headers = values[0]

    rows = []

    for row in values[1:]:

        row = row + [""] * (len(headers) - len(row))

        record = dict(zip(headers, row))

        if record.get("TOP_SYMBOL"):
            rows.append(record)

    return rows


# ============================================================
# ETF HISTORY
# ============================================================

def read_latest_prices(sheet):

    values = sheet.get_all_values()

    if len(values) <= 1:
        return {}

    headers = values[0]

    latest = {}

    date_index = headers.index("TRADE_DATE")
    symbol_index = headers.index("SYMBOL")
    close_index = headers.index("CLOSE")

    for row in values[1:]:

        if len(row) <= close_index:
            continue

        symbol = row[symbol_index].strip()

        if not symbol:
            continue

        trade_date = row[date_index]
        close = number(row[close_index])

        if close <= 0:
            continue

        existing = latest.get(symbol)

        if existing is None or trade_date > existing["DATE"]:

            latest[symbol] = {
                "DATE": trade_date,
                "CLOSE": close,
            }

    return latest


# ============================================================
# TRADE LEDGER
# ============================================================

LEDGER_HEADERS = [
    "TRADE_DATE",
    "ACTION",
    "SYMBOL",
    "CATEGORY",
    "QUANTITY",
    "PRICE",
    "GROSS_VALUE",
    "AVG_COST_BEFORE",
    "AVG_COST_AFTER",
    "AVERAGING_BUYS",
    "REALIZED_PNL",
    "CASH_BEFORE",
    "CASH_AFTER",
    "REASON",
    "SIGNAL_RANK",
]


def append_trade(sheet, trade):

    row = [
        trade["TRADE_DATE"],
        trade["ACTION"],
        trade["SYMBOL"],
        trade["CATEGORY"],
        trade["QUANTITY"],
        trade["PRICE"],
        trade["GROSS_VALUE"],
        trade["AVG_COST_BEFORE"],
        trade["AVG_COST_AFTER"],
        trade["AVERAGING_BUYS"],
        trade["REALIZED_PNL"],
        trade["CASH_BEFORE"],
        trade["CASH_AFTER"],
        trade["REASON"],
        trade["SIGNAL_RANK"],
    ]
    if DRY_RUN:
        print(
            f"DRY RUN: {trade['ACTION']} "
            f"{trade['SYMBOL']} "
            f"Qty={trade['QUANTITY']} "
            f"Price={trade['PRICE']}"
        )
        return
    sheet.append_row(
        row,
        value_input_option="USER_ENTERED",
    )


# ============================================================
# INITIAL EQUITY STATE
# ============================================================

def initialize_state(config, state_sheet):

    total_capital = number(
        config.get("TOTAL_CAPITAL")
    )

    equity_pct = number(
        config.get("EQUITY_BUCKET_PCT")
    )

    if equity_pct == 0:
        equity_pct = number(
            config.get("EQUITY_PCT"),
            60,
        )

    equity_target = (
        total_capital * equity_pct / 100
    )

    # --------------------------------------------------------
    # Recover previously saved equity cash.
    # If no valid previous state exists, initialize the
    # equity bucket with its configured target.
    # --------------------------------------------------------

    previous_values = state_sheet.get_all_values()

    if len(previous_values) >= 2:

        headers = previous_values[0]
        values = previous_values[1]

        if "EQUITY_AVAILABLE" in headers:

            equity_index = headers.index(
                "EQUITY_AVAILABLE"
            )

            if len(values) > equity_index:

                previous_cash = number(
                    values[equity_index],
                    -1,
                )

                if previous_cash >= 0:

                    return {
                        "TOTAL_CAPITAL": total_capital,
                        "EQUITY_TARGET": equity_target,
                        "EQUITY_AVAILABLE": previous_cash,
                    }

    # --------------------------------------------------------
    # First run / no valid previous state.
    # --------------------------------------------------------

    return {
        "TOTAL_CAPITAL": total_capital,
        "EQUITY_TARGET": equity_target,
        "EQUITY_AVAILABLE": equity_target,
    }
# ============================================================
# MAIN TRADING ENGINE
# ============================================================

def main():

    client = get_client()

    config_sheet = get_sheet(client, CONFIG_SHEET)
    state_sheet = get_sheet(client, STATE_SHEET)
    positions_sheet = get_sheet(client, POSITIONS_SHEET)
    ledger_sheet = get_sheet(client, LEDGER_SHEET)
    scanner_sheet = get_sheet(client, SCANNER_SHEET)
    history_sheet = get_sheet(client, HISTORY_SHEET)

    config = read_config(config_sheet)

    positions = read_positions(
        positions_sheet
    )

    scanner = read_scanner(
        scanner_sheet
    )

    latest_prices = read_latest_prices(
        history_sheet
    )

    state = initialize_state(
    config,
    state_sheet,
)

    cash = state["EQUITY_AVAILABLE"]

    trade_date = max(
        row["TRADE_DATE"]
        for row in scanner
        if row.get("TRADE_DATE")
    )

    # --------------------------------------------------------
    # UPDATE CURRENT PRICES
    # --------------------------------------------------------

    for position in positions:

        symbol = position["SYMBOL"]

        if symbol in latest_prices:

            price = latest_prices[symbol]["CLOSE"]

            position["CURRENT_PRICE"] = price

            position["CURRENT_VALUE"] = (
                position["QUANTITY"] * price
            )

            position["UNREALIZED_PNL"] = (
                position["CURRENT_VALUE"]
                - position["INVESTED_VALUE"]
            )

            if position["INVESTED_VALUE"] > 0:

                position["UNREALIZED_PNL_PCT"] = (
                    position["UNREALIZED_PNL"]
                    / position["INVESTED_VALUE"]
                    * 100
                )

            position["TARGET_PRICE"] = (
                position["AVG_COST"]
                * (
                    1
                    + number(
                        config.get(
                            "TARGET_PROFIT_PCT",
                            6.38,
                        )
                    )
                    / 100
                )
            )

    # --------------------------------------------------------
    # 1. EXIT TARGET-PROFIT POSITIONS
    # --------------------------------------------------------

    sold_symbols = set()

    target_profit = number(
        config.get(
            "TARGET_PROFIT_PCT",
            6.38,
        )
    )

    remaining_positions = []

    for position in positions:

        price = position["CURRENT_PRICE"]

        target_price = (
            position["AVG_COST"]
            * (1 + target_profit / 100)
        )

        if (
            position["QUANTITY"] > 0
            and price >= target_price
        ):

            quantity = position["QUANTITY"]

            gross_value = quantity * price

            cash_before = cash

            cash += gross_value

            append_trade(
                ledger_sheet,
                {
                    "TRADE_DATE": trade_date,
                    "ACTION": "SELL",
                    "SYMBOL": position["SYMBOL"],
                    "CATEGORY": position["CATEGORY"],
                    "QUANTITY": quantity,
                    "PRICE": price,
                    "GROSS_VALUE": gross_value,
                    "AVG_COST_BEFORE": position["AVG_COST"],
                    "AVG_COST_AFTER": 0,
                    "AVERAGING_BUYS": position["AVERAGING_BUYS"],
                    "REALIZED_PNL": (
                        gross_value
                        - position["INVESTED_VALUE"]
                    ),
                    "CASH_BEFORE": cash_before,
                    "CASH_AFTER": cash,
                    "REASON": "TARGET_PROFIT",
                    "SIGNAL_RANK": "",
                },
            )

            sold_symbols.add(
                position["SYMBOL"]
            )

        else:

            remaining_positions.append(
                position
            )

    positions = remaining_positions

    # --------------------------------------------------------
    # 2. FIND AVERAGING CANDIDATE
    # --------------------------------------------------------

    averaging_trigger = number(
        config.get(
            "AVERAGING_DROP_PCT",
            3,
        )
    )

    max_averaging = int(
        number(
            config.get(
                "MAX_AVERAGING_BUYS",
                5,
            )
        )
    )

    averaging_candidates = []

    for position in positions:

        if (
            position["SYMBOL"] in sold_symbols
            or position["QUANTITY"] <= 0
            or position["AVG_COST"] <= 0
            or position["AVERAGING_BUYS"] >= max_averaging
        ):
            continue

        price = position["CURRENT_PRICE"]

        fall_pct = (
            (position["AVG_COST"] - price)
            / position["AVG_COST"]
            * 100
        )

        if fall_pct >= averaging_trigger:

            averaging_candidates.append(
                (
                    fall_pct,
                    position,
                )
            )

    # Largest qualifying fall gets priority.
    averaging_candidates.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    # --------------------------------------------------------
    # 3. MAX ONE BUY/AVERAGE PER DAY
    # --------------------------------------------------------

    buy_done = False

    allocation = number(
        config.get(
            "BUY_ALLOCATION_PCT",
            5,
        )
    )

    total_capital = number(
        config.get(
            "TOTAL_CAPITAL",
            500000,
        )
    )

    purchase_budget = (
        total_capital
        * allocation
        / 100
    )

    # --------------------------------------------------------
    # 4. AVERAGE FIRST
    # --------------------------------------------------------

    if averaging_candidates:

        fall_pct, position = averaging_candidates[0]

        if cash >= 1:

            price = position["CURRENT_PRICE"]

            budget = min(
                purchase_budget,
                cash,
            )

            quantity = whole_units(
                budget,
                price,
            )

            if quantity > 0:

                gross_value = quantity * price

                cash_before = cash

                old_quantity = position["QUANTITY"]

                old_avg = position["AVG_COST"]

                old_invested = (
                    old_quantity
                    * old_avg
                )

                new_quantity = (
                    old_quantity
                    + quantity
                )

                new_invested = (
                    old_invested
                    + gross_value
                )

                new_avg = (
                    new_invested
                    / new_quantity
                )

                position["QUANTITY"] = (
                    new_quantity
                )

                position["AVG_COST"] = (
                    new_avg
                )

                position["INVESTED_VALUE"] = (
                    new_invested
                )

                position["AVERAGING_BUYS"] += 1

                position["LAST_BUY_DATE"] = (
                    trade_date
                )

                position["TARGET_PRICE"] = (
                    new_avg
                    * (1 + target_profit / 100)
                )

                cash -= gross_value

                append_trade(
                    ledger_sheet,
                    {
                        "TRADE_DATE": trade_date,
                        "ACTION": "AVERAGE",
                        "SYMBOL": position["SYMBOL"],
                        "CATEGORY": position["CATEGORY"],
                        "QUANTITY": quantity,
                        "PRICE": price,
                        "GROSS_VALUE": gross_value,
                        "AVG_COST_BEFORE": old_avg,
                        "AVG_COST_AFTER": new_avg,
                        "AVERAGING_BUYS": position["AVERAGING_BUYS"],
                        "REALIZED_PNL": 0,
                        "CASH_BEFORE": cash_before,
                        "CASH_AFTER": cash,
                        "REASON": f"AVERAGING_TRIGGER_{fall_pct:.2f}%",
                        "SIGNAL_RANK": "",
                    },
                )

                buy_done = True

    # --------------------------------------------------------
    # 5. IF NO AVERAGING — BUY FINAL RANK 1
    # --------------------------------------------------------

    if not buy_done:

        eligible_candidates = []

        for candidate in scanner:

            if (
                str(
                    candidate.get("ELIGIBLE", "")
                ).upper()
                != "YES"
            ):
                continue

            rank = number(
                candidate.get(
                    "FINAL_RANK"
                ),
                999999,
            )

            if rank == 1:

                eligible_candidates.append(
                    candidate
                )

        if eligible_candidates:

            candidate = eligible_candidates[0]

            symbol = candidate["TOP_SYMBOL"]

            # No same-day re-entry after a sale.
            if symbol in sold_symbols:

                print(
                    f"Skipping {symbol}: "
                    "same-day re-entry prohibited."
                )

            elif symbol in latest_prices:

                price = latest_prices[
                    symbol
                ]["CLOSE"]

                budget = min(
                    purchase_budget,
                    cash,
                )

                quantity = whole_units(
                    budget,
                    price,
                )

                if quantity > 0:

                    gross_value = (
                        quantity * price
                    )

                    cash_before = cash

                    # Existing position should normally
                    # have been handled by averaging.
                    existing = None

                    for p in positions:

                        if p["SYMBOL"] == symbol:
                            existing = p
                            break

                    if existing is not None:

                        old_quantity = (
                            existing["QUANTITY"]
                        )

                        old_avg = (
                            existing["AVG_COST"]
                        )

                        old_invested = (
                            old_quantity
                            * old_avg
                        )

                        new_quantity = (
                            old_quantity
                            + quantity
                        )

                        new_invested = (
                            old_invested
                            + gross_value
                        )

                        new_avg = (
                            new_invested
                            / new_quantity
                        )

                        existing["QUANTITY"] = (
                            new_quantity
                        )

                        existing["AVG_COST"] = (
                            new_avg
                        )

                        existing["INVESTED_VALUE"] = (
                            new_invested
                        )

                        existing["AVERAGING_BUYS"] += 1

                        existing["LAST_BUY_DATE"] = (
                            trade_date
                        )

                        existing["TARGET_PRICE"] = (
                            new_avg
                            * (1 + target_profit / 100)
                        )

                        action = "AVERAGE"

                        avg_before = old_avg
                        avg_after = new_avg
                        averaging_count = (
                            existing["AVERAGING_BUYS"]
                        )

                    else:

                        new_position = {
                            "SYMBOL": symbol,
                            "CATEGORY": candidate.get(
                                "CATEGORY",
                                "",
                            ),
                            "QUANTITY": quantity,
                            "AVG_COST": price,
                            "INVESTED_VALUE": gross_value,
                            "CURRENT_PRICE": price,
                            "CURRENT_VALUE": gross_value,
                            "UNREALIZED_PNL": 0,
                            "UNREALIZED_PNL_PCT": 0,
                            "AVERAGING_BUYS": 0,
                            "LAST_BUY_DATE": trade_date,
                            "TARGET_PRICE": (
                                price
                                * (1 + target_profit / 100)
                            ),
                            "STATUS": "OPEN",
                        }

                        positions.append(
                            new_position
                        )

                        action = "BUY"

                        avg_before = 0
                        avg_after = price
                        averaging_count = 0

                    cash -= gross_value

                    append_trade(
                        ledger_sheet,
                        {
                            "TRADE_DATE": trade_date,
                            "ACTION": action,
                            "SYMBOL": symbol,
                            "CATEGORY": candidate.get(
                                "CATEGORY",
                                "",
                            ),
                            "QUANTITY": quantity,
                            "PRICE": price,
                            "GROSS_VALUE": gross_value,
                            "AVG_COST_BEFORE": avg_before,
                            "AVG_COST_AFTER": avg_after,
                            "AVERAGING_BUYS": averaging_count,
                            "REALIZED_PNL": 0,
                            "CASH_BEFORE": cash_before,
                            "CASH_AFTER": cash,
                            "REASON": "FINAL_RANK_1",
                            "SIGNAL_RANK": candidate.get(
                                "FINAL_RANK",
                                1,
                            ),
                        },
                    )

    # --------------------------------------------------------
    # 6. RECALCULATE POSITIONS
    # --------------------------------------------------------

    positions_value = 0

    for position in positions:

        symbol = position["SYMBOL"]

        if symbol in latest_prices:

            price = latest_prices[
                symbol
            ]["CLOSE"]

            position["CURRENT_PRICE"] = price

            position["CURRENT_VALUE"] = (
                position["QUANTITY"]
                * price
            )

            position["UNREALIZED_PNL"] = (
                position["CURRENT_VALUE"]
                - position["INVESTED_VALUE"]
            )

            if position["INVESTED_VALUE"] > 0:

                position["UNREALIZED_PNL_PCT"] = (
                    position["UNREALIZED_PNL"]
                    / position["INVESTED_VALUE"]
                    * 100
                )

            position["TARGET_PRICE"] = (
                position["AVG_COST"]
                * (1 + target_profit / 100)
            )

        positions_value += (
            position["CURRENT_VALUE"]
        )

        position["STATUS"] = "OPEN"

    # --------------------------------------------------------
    # 7. WRITE POSITIONS
    # --------------------------------------------------------

    write_positions(
        positions_sheet,
        positions,
    )

    # --------------------------------------------------------
    # 8. WRITE PORTFOLIO STATE
    # --------------------------------------------------------

    total_capital = number(
        config.get(
            "TOTAL_CAPITAL",
            500000,
        )
    )

    liquid_target = (
        total_capital
        * number(
            config.get(
                "LIQUID_BUCKET_PCT",
                18,
            )
        )
        / 100
    )

    conservative_target = (
        total_capital
        * number(
            config.get(
                "CONSERVATIVE_BUCKET_PCT",
                22,
            )
        )
        / 100
    )

    equity_target = (
        total_capital
        * number(
            config.get(
                "EQUITY_BUCKET_PCT",
                60,
            )
        )
        / 100
    )

    equity_value = (
        cash
        + positions_value
    )

    state_rows = [
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
            trade_date,
            total_capital,
            liquid_target,
            conservative_target,
            equity_target,
            liquid_target,
            conservative_target,
            equity_value,
            cash,
            positions_value,
            liquid_target
            + conservative_target
            + equity_value,
            cash,
        ],
    ]

    if DRY_RUN:
        print("DRY RUN: PORTFOLIO_STATE sheet will NOT be modified.")
    else:
        state_sheet.clear()

        state_sheet.update(
            "A1:L2",
            state_rows,
        )

    print("======================================")
    print("TRADING ENGINE COMPLETED")
    print("======================================")
    print(f"Trade date: {trade_date}")
    print(f"Equity target: {equity_target:.2f}")
    print(f"Equity cash: {cash:.2f}")
    print(f"Positions value: {positions_value:.2f}")
    print(f"Equity value: {equity_value:.2f}")
    print("======================================")


if __name__ == "__main__":
    main()
