"""
PERMANENT ETF STRATEGY BACKTESTER

Uses the SAME ETF scanner strategy core and the SAME trading rules as live
trading. Manual GitHub Actions execution only; never part of the live chain.

Inputs:
    Google Sheets:
      - ETF_HISTORY
      - Category_Map
      - PORTFOLIO_CONFIG

Outputs:
      - BACKTEST_SUMMARY
      - BACKTEST_TRADES
      - BACKTEST_EQUITY_CURVE

Rules:
    - One ETF per category, highest 20D average turnover.
    - 20D average delivery >= configured minimum.
    - 252-session high from DAILY HIGH.
    - Greatest eligible correction is rank 1.
    - Buy at signal-day CLOSE.
    - Fixed allocation = configured % of ORIGINAL TOTAL CAPITAL.
    - One buy/average per day.
    - Existing position averages when down by configured % from weighted cost.
    - If rank-1 is already held and not averaging eligible, do nothing.
    - Max averaging buys configurable.
    - Sell entire position at configured profit target.
    - No same-day re-entry.
    - Whole units only.
    - No transaction costs/slippage unless later added to live rules.
"""

from __future__ import annotations

import json
import os
from typing import Dict

import gspread
import pandas as pd

from etf_scanner import (
    load_category_map,
    load_history,
    calculate_scanner,
)


CONFIG_SHEET = "PORTFOLIO_CONFIG"
SUMMARY_SHEET = "BACKTEST_SUMMARY"
TRADES_SHEET = "BACKTEST_TRADES"
EQUITY_SHEET = "BACKTEST_EQUITY_CURVE"

SUMMARY_HEADERS = [
    "METRIC", "VALUE"
]

TRADE_HEADERS = [
    "TRADE_DATE", "ACTION", "SYMBOL", "CATEGORY", "QUANTITY",
    "PRICE", "GROSS_VALUE", "AVG_COST_BEFORE", "AVG_COST_AFTER",
    "AVERAGING_BUYS", "REALIZED_PNL", "CASH_AFTER", "REASON", "SIGNAL_RANK"
]

EQUITY_HEADERS = [
    "TRADE_DATE", "CASH", "POSITIONS_VALUE", "PORTFOLIO_VALUE",
    "REALIZED_PNL", "UNREALIZED_PNL", "OPEN_POSITIONS"
]


def get_client():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")
    info = json.loads(raw)
    return gspread.service_account_from_dict(info)


def get_spreadsheet():
    sid = os.environ.get("GOOGLE_SPREADSHEET_ID", "").strip()
    if not sid:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID is missing.")
    return get_client().open_by_key(sid)


def read_config(sheet) -> Dict[str, str]:
    values = sheet.get_all_values()
    result = {}
    for row in values[1:]:
        if len(row) >= 2 and str(row[0]).strip():
            result[str(row[0]).strip().upper()] = row[1]
    return result


def num(config, key, default):
    try:
        return float(str(config.get(key, default)).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return float(default)


def whole_units(budget, price):
    if price is None or price <= 0:
        return 0
    return int(budget // price)


def latest_prices(history, date):
    d = history[history["TRADE_DATE"] == date]
    return {
        str(r["SYMBOL"]).upper(): float(r["CLOSE"])
        for _, r in d.iterrows()
        if pd.notna(r["CLOSE"]) and float(r["CLOSE"]) > 0
    }


def write_sheet(spreadsheet, name, rows, cols):
    try:
        ws = spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=name,
            rows=max(100, len(rows) + 5),
            cols=max(cols, 2),
        )
    if ws.row_count < len(rows):
        ws.add_rows(len(rows) - ws.row_count)
    if ws.col_count < cols:
        ws.add_cols(cols - ws.col_count)
    ws.clear()
    ws.update(
        range_name=f"A1:{chr(64 + cols)}{len(rows)}",
        values=rows,
        value_input_option="USER_ENTERED",
    )


def main():
    print("=" * 70)
    print("ETF STRATEGY BACKTESTER")
    print("=" * 70)

    spreadsheet = get_spreadsheet()
    config = read_config(spreadsheet.worksheet(CONFIG_SHEET))
    category_map = load_category_map(spreadsheet)
    history = load_history(spreadsheet, category_map)

    total_capital = num(config, "TOTAL_CAPITAL", 500000)
    buy_pct = num(config, "BUY_ALLOCATION_PCT", 5)
    averaging_drop = num(config, "AVERAGING_DROP_PCT", 3)
    target_profit = num(config, "TARGET_PROFIT_PCT", 6.38)
    max_averaging = int(num(config, "MAX_AVERAGING_BUYS", 5))

    start_date = pd.to_datetime(
        os.environ.get("BACKTEST_START_DATE", ""),
        errors="coerce",
    )
    end_date = pd.to_datetime(
        os.environ.get("BACKTEST_END_DATE", ""),
        errors="coerce",
    )

    all_dates = sorted(history["TRADE_DATE"].dropna().unique())
    if start_date is not pd.NaT:
        all_dates = [d for d in all_dates if d >= start_date]
    if end_date is not pd.NaT:
        all_dates = [d for d in all_dates if d <= end_date]

    # A 252-session scanner requires at least 252 sessions available
    # through each signal date. The scanner itself enforces this.
    signal_dates = []
    for d in all_dates:
        prior_sessions = history[history["TRADE_DATE"] <= d]["TRADE_DATE"].nunique()
        if prior_sessions >= 252:
            signal_dates.append(d)

    if not signal_dates:
        raise RuntimeError("No valid backtest dates have 252 sessions of history.")

    cash = total_capital
    positions = {}
    trades = []
    equity_curve = []
    realized_pnl = 0.0

    print(f"History: {history['TRADE_DATE'].min().date()} to {history['TRADE_DATE'].max().date()}")
    print(f"Available sessions: {history['TRADE_DATE'].nunique()}")
    print(f"Backtest signal sessions: {len(signal_dates)}")
    print(f"Initial capital: {total_capital:.2f}")
    print(f"Buy allocation: {buy_pct:.2f}% = {total_capital * buy_pct / 100:.2f}")

    for trade_date in signal_dates:
        prices = latest_prices(history, trade_date)

        # ------------------------------------------------------------
        # 1. Exit target first.
        # ------------------------------------------------------------
        sold_today = set()
        for symbol in list(positions.keys()):
            p = positions[symbol]
            price = prices.get(symbol)
            if price is None:
                continue

            target = p["avg_cost"] * (1 + target_profit / 100)
            if price >= target:
                gross = p["quantity"] * price
                pnl = gross - p["invested_value"]
                cash += gross
                realized_pnl += pnl

                trades.append([
                    trade_date.date().isoformat(), "SELL", symbol,
                    p["category"], p["quantity"], price, gross,
                    p["avg_cost"], 0, p["averaging_buys"], pnl,
                    cash, "TARGET_PROFIT", ""
                ])
                sold_today.add(symbol)
                del positions[symbol]

        # ------------------------------------------------------------
        # 2. Scanner signal for this historical date.
        # ------------------------------------------------------------
        result, eligible = calculate_scanner(history, as_of_date=trade_date)

        rank1 = None
        if not eligible.empty:
            rank1 = eligible.sort_values(
                ["CORRECTION_PCT", "20D_AVG_TURNOVER", "TOP_SYMBOL"],
                ascending=[False, False, True],
            ).iloc[0]

        buy_done = False
        purchase_budget = total_capital * buy_pct / 100.0

        # ------------------------------------------------------------
        # 3. Average the existing position with greatest qualifying fall.
        # ------------------------------------------------------------
        candidates = []
        for symbol, p in positions.items():
            price = prices.get(symbol)
            if price is None:
                continue
            if p["averaging_buys"] >= max_averaging:
                continue

            fall = (p["avg_cost"] - price) / p["avg_cost"] * 100
            if fall >= averaging_drop:
                candidates.append((fall, symbol, price))

        candidates.sort(reverse=True)

        if candidates:
            fall, symbol, price = candidates[0]
            budget = min(purchase_budget, cash)
            qty = whole_units(budget, price)

            if qty > 0:
                p = positions[symbol]
                gross = qty * price
                old_avg = p["avg_cost"]
                old_invested = p["invested_value"]
                old_qty = p["quantity"]

                p["quantity"] += qty
                p["invested_value"] += gross
                p["avg_cost"] = p["invested_value"] / p["quantity"]
                p["averaging_buys"] += 1
                cash -= gross

                trades.append([
                    trade_date.date().isoformat(), "AVERAGE", symbol,
                    p["category"], qty, price, gross, old_avg,
                    p["avg_cost"], p["averaging_buys"], 0, cash,
                    f"AVERAGING_TRIGGER_{fall:.2f}%", ""
                ])
                buy_done = True

        # ------------------------------------------------------------
        # 4. If no averaging, buy final rank 1.
        # ------------------------------------------------------------
        if not buy_done and rank1 is not None:
            symbol = str(rank1["TOP_SYMBOL"]).upper()

            if symbol not in sold_today and symbol not in positions:
                price = prices.get(symbol)
                if price is not None:
                    budget = min(purchase_budget, cash)
                    qty = whole_units(budget, price)

                    if qty > 0:
                        gross = qty * price
                        cash_before = cash
                        cash -= gross

                        positions[symbol] = {
                            "category": str(rank1["CATEGORY"]),
                            "quantity": qty,
                            "avg_cost": price,
                            "invested_value": gross,
                            "averaging_buys": 0,
                        }

                        trades.append([
                            trade_date.date().isoformat(), "BUY", symbol,
                            str(rank1["CATEGORY"]), qty, price, gross,
                            0, price, 0, 0, cash,
                            "FINAL_RANK_1", int(rank1["FINAL_RANK"])
                        ])
                        buy_done = True

        # ------------------------------------------------------------
        # 5. Mark portfolio at close.
        # ------------------------------------------------------------
        positions_value = 0.0
        unrealized = 0.0
        for symbol, p in positions.items():
            price = prices.get(symbol, p["avg_cost"])
            value = p["quantity"] * price
            positions_value += value
            unrealized += value - p["invested_value"]

        portfolio_value = cash + positions_value

        equity_curve.append([
            trade_date.date().isoformat(),
            cash,
            positions_value,
            portfolio_value,
            realized_pnl,
            unrealized,
            len(positions),
        ])

    final_value = equity_curve[-1][3]
    total_return = (final_value / total_capital - 1) * 100
    peak = total_capital
    max_drawdown = 0.0
    for row in equity_curve:
        value = float(row[3])
        peak = max(peak, value)
        dd = (value - peak) / peak * 100
        max_drawdown = min(max_drawdown, dd)

    buys = sum(1 for t in trades if t[1] in ("BUY", "AVERAGE"))
    sells = sum(1 for t in trades if t[1] == "SELL")

    summary = [
        SUMMARY_HEADERS,
        ["INITIAL_CAPITAL", total_capital],
        ["FINAL_PORTFOLIO_VALUE", final_value],
        ["TOTAL_RETURN_PCT", total_return],
        ["REALIZED_PNL", realized_pnl],
        ["MAX_DRAWDOWN_PCT", max_drawdown],
        ["BUY_OR_AVERAGE_COUNT", buys],
        ["SELL_COUNT", sells],
        ["OPEN_POSITIONS", len(positions)],
        ["HISTORY_START", history["TRADE_DATE"].min().date().isoformat()],
        ["HISTORY_END", history["TRADE_DATE"].max().date().isoformat()],
        ["SIGNAL_SESSIONS_TESTED", len(signal_dates)],
        ["LOOKBACK_REQUIRED", 252],
        ["STATUS", "COMPLETED"],
    ]

    write_sheet(spreadsheet, SUMMARY_SHEET, summary, 2)
    write_sheet(spreadsheet, TRADES_SHEET, [TRADE_HEADERS] + trades, len(TRADE_HEADERS))
    write_sheet(spreadsheet, EQUITY_SHEET, [EQUITY_HEADERS] + equity_curve, len(EQUITY_HEADERS))

    print("-" * 70)
    print("BACKTEST RESULT")
    print("-" * 70)
    print(f"Signal sessions tested : {len(signal_dates)}")
    print(f"Initial capital        : {total_capital:.2f}")
    print(f"Final portfolio value  : {final_value:.2f}")
    print(f"Total return           : {total_return:.4f}%")
    print(f"Realized P&L           : {realized_pnl:.2f}")
    print(f"Max drawdown           : {max_drawdown:.4f}%")
    print(f"BUY/AVERAGE count      : {buys}")
    print(f"SELL count             : {sells}")
    print(f"Open positions         : {len(positions)}")
    print("-" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
