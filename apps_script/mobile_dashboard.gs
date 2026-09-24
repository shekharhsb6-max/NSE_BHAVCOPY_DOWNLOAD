/**
 * AI WEALTH NAVIGATOR PRO — Google Sheets Dashboard V1
 *
 * Sheets-native dashboard. No HTML interface required.
 *
 * Run setupDashboard() once.
 *
 * IMPORTANT:
 * This V1 is a dashboard/control layer.
 * BUY / SKIP / HOLD decisions are written to SIGNAL_DECISIONS only.
 * It does NOT place broker orders and does NOT alter POSITIONS or TRADE_LEDGER.
 */

const AWN = {
  DASHBOARD: 'DASHBOARD',
  CONFIG: 'PORTFOLIO_CONFIG',
  STATE: 'PORTFOLIO_STATE',
  POSITIONS: 'POSITIONS',
  TRADES: 'TRADE_LEDGER',
  SCANNER: 'ETF_SCANNER',
  DECISIONS: 'SIGNAL_DECISIONS',
  CAPITAL: 'CAPITAL_MANAGEMENT'
};

const C = {
  navy: '#123B6D', blue: '#2196F3', green: '#18A558',
  yellow: '#FFC107', lightBlue: '#EAF4FF', lightGreen: '#E8F7EF',
  grey: '#F3F5F7', red: '#D64545', white: '#FFFFFF',
  dark: '#102A43', border: '#D9E2EC'
};

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('AI Wealth Navigator')
    .addItem('Open / Refresh Dashboard', 'setupDashboard')
    .addItem('Record Signal Decision', 'recordSelectedSignal')
    .addSeparator()
    .addItem('Open Configuration', 'openConfiguration')
    .addItem('Open Capital Management', 'openCapitalManagement')
    .addToUi();
}

function setupDashboard() {
  const ss = SpreadsheetApp.getActive();
  ensureSheet_(ss, AWN.DASHBOARD);
  ensureSheet_(ss, AWN.DECISIONS);
  setupDecisionSheet_(ss.getSheetByName(AWN.DECISIONS));
  refreshDashboard();
  ss.setActiveSheet(ss.getSheetByName(AWN.DASHBOARD));
}

function refreshDashboard() {
  const ss = SpreadsheetApp.getActive();
  const sh = ss.getSheetByName(AWN.DASHBOARD) || ss.insertSheet(AWN.DASHBOARD);
  const state = readKeyValueRow_(ss.getSheetByName(AWN.STATE));
  const config = readConfig_(ss.getSheetByName(AWN.CONFIG));
  const positions = readTable_(ss.getSheetByName(AWN.POSITIONS));
  const trades = readTable_(ss.getSheetByName(AWN.TRADES));
  const scanner = readTable_(ss.getSheetByName(AWN.SCANNER));

  sh.clear();
  sh.clearFormats();
  sh.setHiddenGridlines(true);

  for (let i = 1; i <= 12; i++) sh.setColumnWidth(i, 105);
  sh.setColumnWidth(1, 125);
  sh.setColumnWidth(7, 125);

  sh.getRange('A1:L1').merge().setValue('📈  AI WEALTH NAVIGATOR PRO')
    .setBackground(C.navy).setFontColor(C.white).setFontSize(21)
    .setFontWeight('bold').setHorizontalAlignment('center').setVerticalAlignment('middle');
  sh.setRowHeight(1, 42);

  sh.getRange('A2:L2').merge()
    .setValue('Disciplined Investing  •  Systematic Trading  •  A Brighter Tomorrow')
    .setBackground(C.navy).setFontColor(C.white).setFontSize(10)
    .setHorizontalAlignment('center');

  card_(sh, 'A4:D7', 'TOTAL PORTFOLIO VALUE', money_(state.TOTAL_PORTFOLIO_VALUE), C.lightGreen);
  card_(sh, 'E4:H7', "TODAY'S P&L", money_(todayPnl_(positions)), C.lightBlue);
  card_(sh, 'I4:L7', 'SYSTEM STATUS', systemStatus_(ss), C.grey);

  section_(sh, 'A9:L9', '🎯  PORTFOLIO BUCKETS — TARGET ALLOCATION');
  bucketCard_(sh, 'A10:D14', '💧 LIQUID', config.LIQUID_BUCKET_PCT, state.LIQUID_TARGET, state.LIQUID_VALUE, C.blue);
  bucketCard_(sh, 'E10:H14', '🛡 CONSERVATIVE', config.CONSERVATIVE_BUCKET_PCT, state.CONSERVATIVE_TARGET, state.CONSERVATIVE_VALUE, C.yellow);
  bucketCard_(sh, 'I10:L14', '📊 EQUITY', config.EQUITY_BUCKET_PCT, state.EQUITY_TARGET, state.EQUITY_VALUE, C.green);

  section_(sh, 'A16:F16', '📊  EQUITY BUCKET DETAILS');
  sh.getRange('A17:B21').setValues([
    ['Total Equity Value', money_(state.EQUITY_VALUE)],
    ['Positions Value', money_(state.POSITIONS_VALUE)],
    ['Available Equity Cash', money_(state.EQUITY_AVAILABLE)],
    ['Invested', pct_(safeNum_(state.POSITIONS_VALUE) / Math.max(safeNum_(state.EQUITY_VALUE),1) * 100)],
    ['Cash', pct_(safeNum_(state.EQUITY_AVAILABLE) / Math.max(safeNum_(state.EQUITY_VALUE),1) * 100)]
  ]);
  sh.getRange('A17:A21').setFontWeight('bold');
  border_(sh.getRange('A17:B21'));

  section_(sh, 'G16:L16', "🎯  TODAY'S TRADING SIGNAL");
  renderSignal_(sh, getFinalSignal_(scanner));

  section_(sh, 'A23:L23', '📈  CURRENT HOLDINGS — EQUITY BUCKET');
  renderPositions_(sh, positions);

  section_(sh, 'A35:L35', '🧾  RECENT TRADES');
  renderTrades_(sh, trades);

  section_(sh, 'A45:F45', '🔎  TOP CATEGORY OPPORTUNITIES');
  renderOpportunities_(sh, scanner);

  section_(sh, 'G45:L45', '⚙️  DASHBOARD CONTROLS');
  renderControls_(sh);

  sh.getRange('A57:L58').merge()
    .setValue('💡  Discipline   |   Automate   |   Grow   |   Stay Secure   |   Enjoy Life')
    .setBackground('#FFF2C6').setFontColor(C.dark).setFontSize(12)
    .setFontWeight('bold').setHorizontalAlignment('center').setVerticalAlignment('middle');

  sh.setFrozenRows(2);
}

function renderControls_(sh) {
  sh.getRange('G46:J49').setValues([
    ['REFRESH', false, 'CONFIG', false],
    ['CAPITAL', false, 'RECORD SIGNAL', false],
    ['BUY', false, 'SKIP', false],
    ['HOLD', false, '', '']
  ]);
  sh.getRange('H46:H49').insertCheckboxes();
  sh.getRange('J46:J48').insertCheckboxes();
  sh.getRange('G46:J49').setBorder(true,true,true,true,true,true,C.border,SpreadsheetApp.BorderStyle.SOLID);
  sh.getRange('G46:J49').setBackground(C.lightBlue);
  sh.getRange('G46:G49').setFontWeight('bold');
  sh.getRange('I46:I48').setFontWeight('bold');

  sh.getRange('G51:L54').merge()
    .setValue('BUY / SKIP / HOLD are decisions only. They are recorded in SIGNAL_DECISIONS. No broker order is placed by this dashboard.')
    .setWrap(true).setBackground(C.grey).setVerticalAlignment('middle');
}

function onEdit(e) {
  if (!e || !e.range || e.value !== 'TRUE') return;
  const sh = e.range.getSheet();
  if (sh.getName() !== AWN.DASHBOARD) return;

  const a1 = e.range.getA1Notation();
  e.range.setValue(false);

  if (a1 === 'H46') refreshDashboard();
  else if (a1 === 'J46') openConfiguration();
  else if (a1 === 'H47') openCapitalManagement();
  else if (a1 === 'J47') recordSelectedSignal();
  else if (a1 === 'H48' || a1 === 'H20') recordDecision_('BUY');
  else if (a1 === 'J48' || a1 === 'J20') recordDecision_('SKIP');
  else if (a1 === 'H49' || a1 === 'L20') recordDecision_('HOLD');
}

function recordSelectedSignal() {
  const ui = SpreadsheetApp.getUi();
  const result = ui.prompt('Signal decision', 'Enter BUY, SKIP or HOLD:', ui.ButtonSet.OK_CANCEL);
  if (result.getSelectedButton() !== ui.Button.OK) return;
  const d = result.getResponseText().trim().toUpperCase();
  if (['BUY','SKIP','HOLD'].indexOf(d) < 0) {
    ui.alert('Please enter BUY, SKIP or HOLD.');
    return;
  }
  recordDecision_(d);
}

function recordDecision_(decision) {
  const ss = SpreadsheetApp.getActive();
  const signal = getFinalSignal_(readTable_(ss.getSheetByName(AWN.SCANNER)));
  if (!signal.symbol) {
    SpreadsheetApp.getUi().alert('No active BUY_CANDIDATE signal is available.');
    return;
  }

  ss.getSheetByName(AWN.DECISIONS).appendRow([
    new Date(), signal.tradeDate, signal.symbol, signal.category,
    signal.close, signal.correction, signal.delivery, signal.turnover,
    'BUY_CANDIDATE', decision, 'Dashboard decision'
  ]);

  SpreadsheetApp.getUi().alert(
    decision + ' recorded for ' + signal.symbol +
    '. No POSITIONS or TRADE_LEDGER entry was created.'
  );
}

function openConfiguration() {
  const sh = SpreadsheetApp.getActive().getSheetByName(AWN.CONFIG);
  if (sh) SpreadsheetApp.getActive().setActiveSheet(sh);
}

function openCapitalManagement() {
  const sh = SpreadsheetApp.getActive().getSheetByName(AWN.CAPITAL);
  if (sh) SpreadsheetApp.getActive().setActiveSheet(sh);
}

function setupDecisionSheet_(sh) {
  const h = ['RECORDED_AT','SIGNAL_DATE','SYMBOL','CATEGORY','SIGNAL_PRICE',
    'CORRECTION_PCT','20D_DELIVERY_PCT','20D_AVG_TURNOVER',
    'SIGNAL','USER_DECISION','REMARKS'];
  sh.clear();
  sh.getRange(1,1,1,h.length).setValues([h])
    .setBackground(C.navy).setFontColor(C.white).setFontWeight('bold');
  sh.setFrozenRows(1);
  sh.autoResizeColumns(1,h.length);
}

function ensureSheet_(ss,name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function readConfig_(sh) {
  const o = {};
  if (!sh) return o;
  sh.getDataRange().getValues().slice(1).forEach(r => {
    if (r[0] !== '') o[String(r[0]).trim()] = r[1];
  });
  return o;
}

function readKeyValueRow_(sh) {
  const o = {};
  if (!sh || sh.getLastRow() < 2) return o;
  const h = sh.getRange(1,1,1,sh.getLastColumn()).getValues()[0];
  const r = sh.getRange(2,1,1,sh.getLastColumn()).getValues()[0];
  h.forEach((x,i) => o[String(x).trim()] = r[i]);
  return o;
}

function readTable_(sh) {
  if (!sh || sh.getLastRow() < 2) return [];
  const v = sh.getDataRange().getValues();
  const h = v[0].map(x => String(x).trim());
  return v.slice(1).filter(r => r.some(x => x !== '')).map(r => {
    const o = {}; h.forEach((x,i) => o[x] = r[i]); return o;
  });
}

function getFinalSignal_(rows) {
  const r = rows.find(x => String(x.FINAL_SIGNAL || '').toUpperCase() === 'BUY_CANDIDATE');
  if (!r) return {};
  return {
    tradeDate:r.TRADE_DATE, symbol:r.TOP_SYMBOL, category:r.CATEGORY,
    close:r.TODAY_CLOSE, correction:r.CORRECTION_PCT,
    delivery:r['20D_AVG_DELIVERY_PCT'], turnover:r['20D_AVG_TURNOVER']
  };
}

function renderSignal_(sh,s) {
  if (!s.symbol) {
    sh.getRange('G17:L22').merge().setValue('NO ACTIVE BUY SIGNAL')
      .setBackground(C.grey).setFontSize(15).setFontWeight('bold')
      .setHorizontalAlignment('center').setVerticalAlignment('middle');
    return;
  }

  sh.getRange('G17:I18').merge().setValue('BUY SIGNAL\n'+s.symbol)
    .setBackground(C.green).setFontColor(C.white).setFontSize(15)
    .setFontWeight('bold').setHorizontalAlignment('center').setVerticalAlignment('middle');
  sh.getRange('J17:L18').merge().setValue('Correction\n'+pct_(s.correction))
    .setBackground('#FDECEC').setFontSize(14).setFontWeight('bold')
    .setHorizontalAlignment('center').setVerticalAlignment('middle');

  sh.getRange('G19:L20').setValues([
    ['Close',money_(s.close),'20D Delivery',pct_(s.delivery),'20D Turnover',compactMoney_(s.turnover)],
    ['BUY',false,'SKIP',false,'HOLD',false]
  ]);
  sh.getRange('H20').insertCheckboxes();
  sh.getRange('J20').insertCheckboxes();
  sh.getRange('L20').insertCheckboxes();
  sh.getRange('G19:L20').setBorder(true,true,true,true,true,true,C.border,SpreadsheetApp.BorderStyle.SOLID);
  sh.getRange('G19:L19').setFontWeight('bold');
  sh.getRange('G21:L22').merge()
    .setValue('Tap BUY / SKIP / HOLD. Your decision is recorded separately from actual trades.')
    .setWrap(true).setFontStyle('italic').setFontSize(9);
}

function renderPositions_(sh,rows) {
  const h=['SYMBOL','QTY','AVG COST','LTP','VALUE','P&L','P&L %','CATEGORY'];
  const d=rows.filter(r=>String(r.STATUS||'OPEN').toUpperCase()==='OPEN').slice(0,8)
    .map(r=>[r.SYMBOL,r.QUANTITY,r.AVG_COST,r.CURRENT_PRICE,r.CURRENT_VALUE,r.UNREALIZED_PNL,r.UNREALIZED_PNL_PCT,r.CATEGORY]);
  sh.getRange('A24:H24').setValues([h]).setFontWeight('bold').setBackground(C.lightBlue);
  if(d.length) sh.getRange(25,1,d.length,8).setValues(d);
  border_(sh.getRange('A24:H32'));
  sh.getRange('C25:F32').setNumberFormat('₹#,##0.00');
  sh.getRange('G25:G32').setNumberFormat('0.00%');
}

function renderTrades_(sh,rows) {
  const h=['DATE','SYMBOL','ACTION','QTY','PRICE','VALUE','CATEGORY'];
  const d=rows.slice(-6).reverse().map(r=>[r.TRADE_DATE,r.SYMBOL,r.ACTION,r.QUANTITY,r.PRICE,r.GROSS_VALUE,r.CATEGORY]);
  sh.getRange('A36:G36').setValues([h]).setFontWeight('bold').setBackground(C.lightBlue);
  if(d.length) sh.getRange(37,1,d.length,7).setValues(d);
  border_(sh.getRange('A36:G42'));
  sh.getRange('E37:F42').setNumberFormat('₹#,##0.00');
}

function renderOpportunities_(sh,rows) {
  const h=['SYMBOL','CATEGORY','CORRECTION','DELIVERY','TURNOVER','SIGNAL'];
  const d=rows.slice(0,8).map(r=>[r.TOP_SYMBOL,r.CATEGORY,r.CORRECTION_PCT,r['20D_AVG_DELIVERY_PCT'],r['20D_AVG_TURNOVER'],r.FINAL_SIGNAL||'']);
  sh.getRange('A46:F46').setValues([h]).setFontWeight('bold').setBackground(C.lightBlue);
  if(d.length) sh.getRange(47,1,d.length,6).setValues(d);
  border_(sh.getRange('A46:F54'));
  sh.getRange('C47:E54').setNumberFormat('0.00');
}

function bucketCard_(sh,range,name,pct,target,current,bg) {
  const r=sh.getRange(range), row=r.getRow(), col=r.getColumn(), n=r.getNumColumns();
  r.setBackground(bg); border_(r);
  sh.getRange(row,col,2,n).merge().setValue(name+'\n'+pct_(pct))
    .setFontSize(14).setFontWeight('bold').setFontColor(C.white)
    .setHorizontalAlignment('center').setVerticalAlignment('middle');
  sh.getRange(row+2,col,2,n).merge().setValue('Target\n'+money_(target)+'\nCurrent '+money_(current))
    .setBackground(C.white).setFontWeight('bold')
    .setHorizontalAlignment('center').setVerticalAlignment('middle');
}

function card_(sh,range,title,value,bg) {
  sh.getRange(range).merge().setValue(title+'\n'+value)
    .setBackground(bg).setFontColor(C.dark).setFontSize(15).setFontWeight('bold')
    .setHorizontalAlignment('center').setVerticalAlignment('middle');
  border_(sh.getRange(range));
}

function section_(sh,range,title) {
  sh.getRange(range).merge().setValue(title).setBackground(C.lightBlue)
    .setFontColor(C.dark).setFontWeight('bold').setFontSize(12);
}

function border_(r) {
  r.setBorder(true,true,true,true,true,true,C.border,SpreadsheetApp.BorderStyle.SOLID);
}

function systemStatus_(ss) {
  const required=[AWN.STATE,AWN.SCANNER,AWN.POSITIONS,AWN.TRADES];
  return required.every(n=>!!ss.getSheetByName(n)) ? '✓ ALL SYSTEM SHEETS OK' : '⚠ CHECK SYSTEM SHEETS';
}

function todayPnl_(rows) {
  return rows.reduce((s,r)=>s+safeNum_(r.UNREALIZED_PNL),0);
}

function safeNum_(v) {
  if(typeof v==='number') return v;
  const n=parseFloat(String(v||'').replace(/[₹,% ,]/g,''));
  return isNaN(n)?0:n;
}

function money_(v) {
  return '₹ '+Math.round(safeNum_(v)).toLocaleString('en-IN');
}

function compactMoney_(v) {
  const n=safeNum_(v);
  if(n>=10000000) return '₹ '+(n/10000000).toFixed(2)+' Cr';
  if(n>=100000) return '₹ '+(n/100000).toFixed(2)+' L';
  return money_(n);
}

function pct_(v) {
  return safeNum_(v).toFixed(2)+'%';
}
