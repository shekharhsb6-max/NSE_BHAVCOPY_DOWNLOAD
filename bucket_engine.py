"""3-Bucket Portfolio Engine - first integration layer."""
import json, os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

SCOPES=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
CONFIG_SHEET="PORTFOLIO_CONFIG"; STATE_SHEET="PORTFOLIO_STATE"; POSITIONS_SHEET="POSITIONS"

def f(v,d=0.0):
    try: return float(str(v).replace(",","").replace("%","").strip()) if v not in (None,"") else d
    except: return d

def i(v,d=0):
    try: return int(float(v))
    except: return d

def client():
    sid=os.environ.get("GOOGLE_SPREADSHEET_ID","").strip(); raw=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON","").strip()
    if not sid: raise RuntimeError("GOOGLE_SPREADSHEET_ID is missing.")
    if not raw: raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing.")
    c=Credentials.from_service_account_info(json.loads(raw),scopes=SCOPES)
    return gspread.authorize(c).open_by_key(sid)

def kv(sheet):
    rows=sheet.get_all_values(); return {str(r[0]).strip():r[1] for r in rows[1:] if len(r)>=2 and str(r[0]).strip()}

def rows_as_dicts(sheet):
    rows=sheet.get_all_values()
    if len(rows)<2:return []
    h=[str(x).strip() for x in rows[0]]
    return [{h[n]:r[n] if n<len(r) else "" for n in range(len(h))} for r in rows[1:] if any(str(x).strip() for x in r)]

def existing_state(sheet):
    rows=sheet.get_all_values()
    if len(rows)<2:return {}
    h=[str(x).strip() for x in rows[0]]; r=rows[1]
    return {h[n]:r[n] if n<len(r) else "" for n in range(len(h))}

def positions_value(positions):
    total=0.0
    for p in positions:
        if str(p.get("STATUS","")).strip().upper() not in ("","OPEN"): continue
        v=f(p.get("CURRENT_VALUE"))
        if v==0: v=i(p.get("QUANTITY"))*f(p.get("CURRENT_PRICE"))
        total+=v
    return total

def calculate(config, state_sheet, positions):
    capital=f(config.get("TOTAL_CAPITAL"),500000)
    lp=f(config.get("LIQUID_BUCKET_PCT"),18); cp=f(config.get("CONSERVATIVE_BUCKET_PCT"),22); ep=f(config.get("EQUITY_BUCKET_PCT"),60)
    if abs(lp+cp+ep-100)>1e-6: raise ValueError(f"Bucket percentages must total 100%; current total={lp+cp+ep:.4f}%.")
    lt=capital*lp/100; ct=capital*cp/100; et=capital*ep/100
    old=existing_state(state_sheet)
    cash=f(old.get("EQUITY_AVAILABLE"))
    pv=positions_value(positions)
    if cash==0 and pv==0: cash=et
    lv=f(old.get("LIQUID_VALUE"),lt); cv=f(old.get("CONSERVATIVE_VALUE"),ct)
    ev=cash+pv
    return dict(AS_OF_DATE=datetime.now().strftime("%Y-%m-%d"),TOTAL_CAPITAL=capital,LIQUID_TARGET=lt,CONSERVATIVE_TARGET=ct,EQUITY_TARGET=et,LIQUID_VALUE=lv,CONSERVATIVE_VALUE=cv,EQUITY_AVAILABLE=cash,POSITIONS_VALUE=pv,EQUITY_VALUE=ev,TOTAL_PORTFOLIO_VALUE=lv+cv+ev,CASH_RESERVE=cash)

def main():
    dry=os.environ.get("DRY_RUN","true").lower()=="true"
    ss=client(); config=kv(ss.worksheet(CONFIG_SHEET)); state_sheet=ss.worksheet(STATE_SHEET); positions=rows_as_dicts(ss.worksheet(POSITIONS_SHEET))
    s=calculate(config,state_sheet,positions)
    print("======================================\n3-BUCKET ENGINE\n======================================")
    for k in ("TOTAL_CAPITAL","LIQUID_TARGET","LIQUID_VALUE","CONSERVATIVE_TARGET","CONSERVATIVE_VALUE","EQUITY_TARGET","EQUITY_AVAILABLE","POSITIONS_VALUE","EQUITY_VALUE","TOTAL_PORTFOLIO_VALUE"):
        print(f"{k}: {s[k]:.2f}")
    print("======================================")
    if dry:
        print("DRY RUN: PORTFOLIO_STATE will NOT be modified.")
    else:
        values=[["AS_OF_DATE","TOTAL_CAPITAL","LIQUID_TARGET","CONSERVATIVE_TARGET","EQUITY_TARGET","LIQUID_VALUE","CONSERVATIVE_VALUE","EQUITY_AVAILABLE","POSITIONS_VALUE","EQUITY_VALUE","TOTAL_PORTFOLIO_VALUE","CASH_RESERVE"], [s[k] for k in ["AS_OF_DATE","TOTAL_CAPITAL","LIQUID_TARGET","CONSERVATIVE_TARGET","EQUITY_TARGET","LIQUID_VALUE","CONSERVATIVE_VALUE","EQUITY_AVAILABLE","POSITIONS_VALUE","EQUITY_VALUE","TOTAL_PORTFOLIO_VALUE","CASH_RESERVE"]]]
        state_sheet.update(range_name="A1:L2",values=values); print("LIVE: PORTFOLIO_STATE updated successfully.")
    print("BUCKET ENGINE COMPLETED")

if __name__=="__main__": main()
