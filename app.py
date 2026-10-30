import os
import datetime
import sqlite3
import requests
import pandas as pd
import pytz
from flask import Flask, render_template_string, jsonify, send_file, Response
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
IST = pytz.timezone('Asia/Kolkata')
DB_NAME = "option_chain_recorder.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
        """)
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
        cursor.execute("""
            INSERT INTO records (
                timestamp, symbol, expiry, spot_price, pcr, max_pain, support, resistance, atm_strike,
                ce_oi_atm_minus_100, ce_oi_atm_minus_50, ce_oi_atm, ce_oi_atm_plus_50, ce_oi_atm_plus_100,
                pe_oi_atm_minus_100, pe_oi_atm_minus_50, pe_oi_atm, pe_oi_atm_plus_50, pe_oi_atm_plus_100,
                total_ce_chg_oi, total_pe_chg_oi, net_oi_diff, signal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
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

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="mr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>NIFTY Live OI Terminal</title>
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#020617">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .custom-scroll::-webkit-scrollbar { width: 4px; height: 4px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-2 md:p-4 flex flex-col font-sans select-none">
    <header class="flex flex-wrap items-center justify-between pb-3 border-b border-slate-800 gap-2">
        <div class="flex items-center gap-2">
            <span class="w-3 h-3 rounded-full bg-emerald-500 animate-pulse"></span>
            <div>
                <h1 class="text-sm md:text-base font-black tracking-wide text-sky-400">NIFTY 1-MIN OI RECORDER</h1>
                <p class="text-[10px] text-slate-400">Render 24/7 Cloud Background Active | 2:00 AM Reset</p>
            </div>
        </div>
        <div class="flex items-center gap-2">
            <select id="expirySelect" class="bg-slate-900 border border-slate-700 text-xs px-2 py-1.5 rounded text-sky-300 font-bold focus:outline-none"></select>
            <button onclick="triggerNow()" class="bg-slate-800 border border-slate-700 text-xs px-2.5 py-1.5 rounded font-bold hover:bg-slate-700">🔄 Fetch</button>
            <a href="/api/export" class="bg-emerald-600 text-xs font-bold px-3 py-1.5 rounded text-white shadow hover:bg-emerald-500">📥 Excel</a>
        </div>
    </header>

    <section class="grid grid-cols-2 md:grid-cols-5 gap-2 my-3 text-center">
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-lg shadow">
            <div class="text-[10px] text-slate-400 font-bold uppercase">Spot Price</div>
            <div id="cardSpot" class="text-xl md:text-2xl font-black text-amber-400 mt-0.5">--</div>
            <div id="cardAtm" class="text-[10px] text-slate-500 font-mono">ATM: --</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-lg shadow">
            <div class="text-[10px] text-slate-400 font-bold uppercase">PCR</div>
            <div id="cardPcr" class="text-xl md:text-2xl font-black text-emerald-400 mt-0.5">--</div>
            <div id="pcrBias" class="text-[10px] text-slate-500 font-semibold">--</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-lg shadow">
            <div class="text-[10px] text-slate-400 font-bold uppercase">Max Pain</div>
            <div id="cardMaxPain" class="text-xl md:text-2xl font-black text-purple-400 mt-0.5">--</div>
            <div class="text-[10px] text-slate-500 font-mono">Expiry Pivot</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-lg shadow">
            <div class="text-[10px] text-slate-400 font-bold uppercase">Support</div>
            <div id="cardSupport" class="text-xl md:text-2xl font-black text-emerald-500 mt-0.5">--</div>
            <div class="text-[10px] text-slate-500 font-mono">Highest PE OI</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-lg shadow col-span-2 md:col-span-1">
            <div class="text-[10px] text-slate-400 font-bold uppercase">Resistance</div>
            <div id="cardResistance" class="text-xl md:text-2xl font-black text-rose-500 mt-0.5">--</div>
            <div class="text-[10px] text-slate-500 font-mono">Highest CE OI</div>
        </div>
    </section>

    <main class="flex-1 overflow-hidden bg-slate-900 border border-slate-800 rounded-lg flex flex-col shadow-2xl">
        <div class="px-3 py-2 bg-slate-900/90 border-b border-slate-800 flex justify-between items-center text-xs">
            <span class="font-bold text-slate-200">1-Min Stream (Newest Data Top)</span>
            <span class="text-slate-400 font-mono text-[11px]" id="lastSync">Sync: --</span>
        </div>
        <div class="overflow-x-auto overflow-y-auto flex-1 custom-scroll">
            <table class="w-full text-center border-collapse text-[11px] md:text-xs whitespace-nowrap">
                <thead class="sticky top-0 bg-slate-950/95 backdrop-blur text-slate-400 border-b border-slate-800">
                    <tr>
                        <th rowspan="2" class="py-2 px-2.5 border-r border-slate-800">Time</th>
                        <th rowspan="2" class="py-2 px-2.5 border-r border-slate-800">Spot</th>
                        <th colspan="5" class="py-1 px-1 border-r border-slate-800 bg-rose-950/20 text-rose-400 font-bold">CE Change in OI</th>
                        <th colspan="5" class="py-1 px-1 border-r border-slate-800 bg-emerald-950/20 text-emerald-400 font-bold">PE Change in OI</th>
                        <th rowspan="2" class="py-2 px-2 border-r border-slate-800 font-bold">Total CE Δ</th>
                        <th rowspan="2" class="py-2 px-2 border-r border-slate-800 font-bold">Total PE Δ</th>
                        <th rowspan="2" class="py-2 px-2.5 border-r border-slate-800 text-sky-400 font-black">Net (PE-CE)</th>
                        <th rowspan="2" class="py-2 px-3 font-black">Signal</th>
                    </tr>
                    <tr class="text-[10px]">
                        <th class="py-1 px-1.5 bg-rose-950/40 text-rose-300">-100</th>
                        <th class="py-1 px-1.5 bg-rose-950/40 text-rose-300">-50</th>
                        <th class="py-1 px-1.5 bg-rose-900/50 text-rose-200 font-bold">ATM</th>
                        <th class="py-1 px-1.5 bg-rose-950/40 text-rose-300">+50</th>
                        <th class="py-1 px-1.5 border-r border-slate-800 bg-rose-950/40 text-rose-300">+100</th>

                        <th class="py-1 px-1.5 bg-emerald-950/40 text-emerald-300">-100</th>
                        <th class="py-1 px-1.5 bg-emerald-950/40 text-emerald-300">-50</th>
                        <th class="py-1 px-1.5 bg-emerald-900/50 text-emerald-200 font-bold">ATM</th>
                        <th class="py-1 px-1.5 bg-emerald-950/40 text-emerald-300">+50</th>
                        <th class="py-1 px-1.5 border-r border-slate-800 bg-emerald-950/40 text-emerald-300">+100</th>
                    </tr>
                </thead>
                <tbody id="tableBody" class="divide-y divide-slate-800/60 font-mono"></tbody>
            </table>
        </div>
    </main>

    <script>
        function fmt(n) {
            if (n === null || n === undefined) return "-";
            return Number(n).toLocaleString('en-IN');
        }

        async function loadMeta() {
            try {
                const res = await fetch('/api/meta');
                const data = await res.json();
                const sel = document.getElementById('expirySelect');
                sel.innerHTML = '';
                data.expiries.forEach(exp => {
                    const opt = document.createElement('option');
                    opt.value = exp;
                    opt.innerText = exp;
                    sel.appendChild(opt);
                });
            } catch(e) {}
        }

        async function loadRecords() {
            try {
                const res = await fetch('/api/records');
                const rows = await res.json();
                if (!rows || rows.length === 0) return;

                const latest = rows[0];
                document.getElementById('cardSpot').innerText = latest.spot_price.toFixed(2);
                document.getElementById('cardAtm').innerText = 'ATM: ' + latest.atm_strike;
                document.getElementById('cardPcr').innerText = latest.pcr.toFixed(2);
                document.getElementById('pcrBias').innerText = latest.pcr >= 1.15 ? 'Bullish' : (latest.pcr <= 0.85 ? 'Bearish' : 'Neutral');
                document.getElementById('cardMaxPain').innerText = latest.max_pain;
                document.getElementById('cardSupport').innerText = latest.support;
                document.getElementById('cardResistance').innerText = latest.resistance;
                document.getElementById('lastSync').innerText = 'Sync: ' + latest.timestamp;

                const tbody = document.getElementById('tableBody');
                tbody.innerHTML = '';

                rows.forEach(r => {
                    const tr = document.createElement('tr');
                    tr.className = "hover:bg-slate-800/40 transition";

                    let sigClass = "text-slate-600";
                    if (r.signal.includes("BUY")) sigClass = "bg-emerald-900/50 text-emerald-300 border border-emerald-500 px-1.5 py-0.5 rounded font-black";
                    if (r.signal.includes("SELL")) sigClass = "bg-rose-900/50 text-rose-300 border border-rose-500 px-1.5 py-0.5 rounded font-black";

                    const netClass = r.net_oi_diff >= 0 ? "text-emerald-400" : "text-rose-400";

                    tr.innerHTML = `
                        <td class="py-1.5 px-2 text-slate-300 font-semibold border-r border-slate-800">${r.timestamp}</td>
                        <td class="py-1.5 px-2 text-amber-300 font-bold border-r border-slate-800">${r.spot_price.toFixed(2)}</td>
                        <td class="py-1.5 px-1 text-rose-400/80">${fmt(r.ce_oi_atm_minus_100)}</td>
                        <td class="py-1.5 px-1 text-rose-400/80">${fmt(r.ce_oi_atm_minus_50)}</td>
                        <td class="py-1.5 px-1 text-rose-200 font-bold bg-rose-950/30">${fmt(r.ce_oi_atm)}</td>
                        <td class="py-1.5 px-1 text-rose-400/80">${fmt(r.ce_oi_atm_plus_50)}</td>
                        <td class="py-1.5 px-1 text-rose-400/80 border-r border-slate-800">${fmt(r.ce_oi_atm_plus_100)}</td>
                        <td class="py-1.5 px-1 text-emerald-400/80">${fmt(r.pe_oi_atm_minus_100)}</td>
                        <td class="py-1.5 px-1 text-emerald-400/80">${fmt(r.pe_oi_atm_minus_50)}</td>
                        <td class="py-1.5 px-1 text-emerald-200 font-bold bg-emerald-950/30">${fmt(r.pe_oi_atm)}</td>
                        <td class="py-1.5 px-1 text-emerald-400/80">${fmt(r.pe_oi_atm_plus_50)}</td>
                        <td class="py-1.5 px-1 text-emerald-400/80 border-r border-slate-800">${fmt(r.pe_oi_atm_plus_100)}</td>
                        <td class="py-1.5 px-2 font-semibold border-r border-slate-800 text-rose-300">${fmt(r.total_ce_chg_oi)}</td>
                        <td class="py-1.5 px-2 font-semibold border-r border-slate-800 text-emerald-300">${fmt(r.total_pe_chg_oi)}</td>
                        <td class="py-1.5 px-2 font-black border-r border-slate-800 ${netClass}">${fmt(r.net_oi_diff)}</td>
                        <td class="py-1.5 px-2 text-center">${r.signal ? `<span class="${sigClass}">${r.signal}</span>` : `<span class="text-slate-600">-</span>`}</td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch(e) {}
        }

        async function triggerNow() {
            await fetch('/api/trigger');
            loadRecords();
        }

        loadMeta();
        loadRecords();
        setInterval(loadRecords, 10000);
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/manifest.json")
def manifest():
    manifest_data = """{
        "name": "Nifty OI Terminal",
        "short_name": "OI Recorder",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#020617",
        "theme_color": "#020617",
        "icons": [{
            "src": "https://cdn-icons-png.flaticon.com/512/2920/2920349.png",
            "sizes": "512x512",
            "type": "image/png"
        }]
    }"""
    return Response(manifest_data, mimetype='application/json')

@app.route("/api/meta")
def get_meta():
    raw = fetch_nse_chain("NIFTY")
    if raw and "records" in raw:
        return jsonify({
            "expiries": raw["records"].get("expiryDates", []),
            "spot": raw["records"].get("underlyingValue", 0.0)
        })
    return jsonify({"expiries": [], "spot": 0.0})

@app.route("/api/records")
def get_records():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM records ORDER BY id DESC")
        rows = [dict(r) for r in cursor.fetchall()]
    return jsonify(rows)

@app.route("/api/trigger")
def trigger_now():
    analyze_and_record("NIFTY")
    return jsonify({"status": "success"})

@app.route("/api/export")
def export_excel():
    with sqlite3.connect(DB_NAME) as conn:
        df = pd.read_sql_query("SELECT * FROM records ORDER BY id DESC", conn)
    excel_path = "Option_Chain_Log.xlsx"
    df.to_excel(excel_path, index=False)
    return send_file(excel_path, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
