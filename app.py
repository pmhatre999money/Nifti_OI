import os
import datetime
import sqlite3
import requests
import pandas as pd
import pytz
from flask import Flask, render_template_string, jsonify, send_file
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
IST = pytz.timezone('Asia/Kolkata')
DB_NAME = "option_chain_recorder.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                expiry TEXT,
                spot_price REAL,
                pcr REAL,
                max_pain REAL,
                support REAL,
                resistance REAL,
                atm_strike REAL,
                ce_oi_atm_minus_100 REAL,
                ce_oi_atm_minus_50 REAL,
                ce_oi_atm REAL,
                ce_oi_atm_plus_50 REAL,
                ce_oi_atm_plus_100 REAL,
                pe_oi_atm_minus_100 REAL,
                pe_oi_atm_minus_50 REAL,
                pe_oi_atm REAL,
                pe_oi_atm_plus_50 REAL,
                pe_oi_atm_plus_100 REAL,
                total_ce_chg_oi REAL,
                total_pe_chg_oi REAL,
                net_oi_diff REAL,
                signal TEXT
            )
        ''')
        conn.commit()

init_db()

def fetch_nse_chain(symbol="NIFTY"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=6)
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        res = session.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"NSE Error: {e}")
    return None

def analyze_and_record(symbol="NIFTY", selected_expiry=None):
    raw_data = fetch_nse_chain(symbol)
    if not raw_data or "records" not in raw_data:
        return

    records = raw_data["records"]
    spot_price = float(records.get("underlyingValue", 0.0))
    expiry_dates = records.get("expiryDates", [])
    if not expiry_dates:
        return

    expiry = selected_expiry if (selected_expiry and selected_expiry in expiry_dates) else expiry_dates[0]
    data_list = [item for item in records.get("data", []) if item.get("expiryDate") == expiry]

    step = 50
    atm_strike = round(spot_price / step) * step
    target_strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]

    tot_pe_oi, tot_ce_oi = 0, 0
    max_ce_oi, max_pe_oi = 0, 0
    resistance, support = 0, 0
    loss_map = {}
    strike_data_map = {}

    for item in data_list:
        stk = item.get("strikePrice")
        ce = item.get("CE", {})
        pe = item.get("PE", {})

        ce_oi = ce.get("openInterest", 0)
        pe_oi = pe.get("openInterest", 0)
        ce_chg = ce.get("changeinOpenInterest", 0)
        pe_chg = pe.get("changeinOpenInterest", 0)

        tot_ce_oi += ce_oi
        tot_pe_oi += pe_oi

        if ce_oi > max_ce_oi:
            max_ce_oi = ce_oi
            resistance = stk
        if pe_oi > max_pe_oi:
            max_pe_oi = pe_oi
            support = stk

        strike_data_map[stk] = {"ce_chg": ce_chg, "pe_chg": pe_chg}

    for s1 in strike_data_map.keys():
        total_loss = 0
        for s2 in strike_data_map.keys():
            ce_val = max(0, s1 - s2) * strike_data_map[s2]["ce_chg"]
            pe_val = max(0, s2 - s1) * strike_data_map[s2]["pe_chg"]
            total_loss += (ce_val + pe_val)
        loss_map[s1] = total_loss

    max_pain = min(loss_map, key=loss_map.get) if loss_map else atm_strike
    pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi > 0 else 1.0

    ce_chgs = [strike_data_map.get(s, {}).get("ce_chg", 0) for s in target_strikes]
    pe_chgs = [strike_data_map.get(s, {}).get("pe_chg", 0) for s in target_strikes]

    sum_ce_5 = sum(ce_chgs)
    sum_pe_5 = sum(pe_chgs)
    net_diff = sum_pe_5 - sum_ce_5

    signal = ""
    if pcr >= 1.15 and net_diff > 250000:
        signal = "STRONG BUY (BULLISH)"
    elif pcr <= 0.85 and net_diff < -250000:
        signal = "STRONG SELL (BEARISH)"
    elif net_diff > 150000 and spot_price >= support:
        signal = "BUY ON DIPS"
    elif net_diff < -150000 and spot_price <= resistance:
        signal = "SELL ON RISE"
    else:
        signal = ""

    now_str = datetime.datetime.now(IST).strftime("%H:%M:%S")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO records (
                timestamp, symbol, expiry, spot_price, pcr, max_pain, support, resistance, atm_strike,
                ce_oi_atm_minus_100, ce_oi_atm_minus_50, ce_oi_atm, ce_oi_atm_plus_50, ce_oi_atm_plus_100,
                pe_oi_atm_minus_100, pe_oi_atm_minus_50, pe_oi_atm, pe_oi_atm_plus_50, pe_oi_atm_plus_100,
                total_ce_chg_oi, total_pe_chg_oi, net_oi_diff, signal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            now_str, symbol, expiry, spot_price, pcr, max_pain, support, resistance, atm_strike,
            ce_chgs[0], ce_chgs[1], ce_chgs[2], ce_chgs[3], ce_chgs[4],
            pe_chgs[0], pe_chgs[1], pe_chgs[2], pe_chgs[3], pe_chgs[4],
            sum_ce_5, sum_pe_5, net_diff, signal
        ))
        conn.commit()

def scheduled_job():
    now = datetime.datetime.now(IST)
    if now.weekday() < 5:
        m_start = now.replace(hour=9, minute=15, second=0)
        m_end = now.replace(hour=15, minute=30, second=0)
        if m_start <= now <= m_end:
            analyze_and_record("NIFTY")

def clear_db_job():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM records")
        conn.commit()

scheduler = BackgroundScheduler(timezone=IST)
scheduler.add_job(scheduled_job, 'interval', minutes=1)
scheduler.add_job(clear_db_job, 'cron', hour=2, minute=0)
scheduler.start()

@app.route("/")
def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.route("/app.js")
def serve_js():
    with open("app.js", "r", encoding="utf-8") as f:
        return f.read(), 200, {'Content-Type': 'application/javascript'}

@app.route("/manifest.json")
def serve_manifest():
    with open("manifest.json", "r", encoding="utf-8") as f:
        return f.read(), 200, {'Content-Type': 'application/json'}

@app.route("/api/records")
def get_records():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM records ORDER BY id DESC")
        rows = [dict(r) for r in cursor.fetchall()]
    return jsonify(rows)

@app.route("/api/export")
def export_excel():
    with sqlite3.connect(DB_NAME) as conn:
        df = pd.read_sql_query("SELECT * FROM records ORDER BY id DESC", conn)
    excel_path = "Option_Chain_Log.xlsx"
    df.to_excel(excel_path, index=False)
    return send_file(excel_path, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
