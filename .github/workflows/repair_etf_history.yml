name: Repair ETF History Prices

on:
  workflow_dispatch:

jobs:
  repair:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Repair ETF_HISTORY price anomalies
        run: python repair_etf_history_prices.py
        env:
          GOOGLE_SPREADSHEET_ID: ${{ secrets.GOOGLE_SPREADSHEET_ID }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
