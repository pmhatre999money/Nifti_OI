const DB_KEY = "nifty_oi_records";
const LAST_CLEAR_KEY = "nifty_last_cleared_date";

function fmt(n) {
    if (n === null || n === undefined) return "-";
    return Number(n).toLocaleString('en-IN');
}

function checkMidnightReset() {
    const now = new Date();
    const todayStr = now.toDateString();
    const lastCleared = localStorage.getItem(LAST_CLEAR_KEY);

    if (now.getHours() >= 2 && lastCleared !== todayStr) {
        localStorage.setItem(DB_KEY, JSON.stringify([]));
        localStorage.setItem(LAST_CLEAR_KEY, todayStr);
        renderTable();
    }
}

async function fetchOptionChain() {
    const targetUrl = encodeURIComponent("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY");
    const gateway = `https://api.allorigins.win/raw?url=${targetUrl}`;

    try {
        const response = await fetch(gateway, { cache: "no-store" });
        if (response.ok) {
            return await response.json();
        }
    } catch (e) {
        console.warn("Gateway sync error:", e);
    }
    return null;
}

async function recordSnapshot(manual = false) {
    checkMidnightReset();

    const raw = await fetchOptionChain();
    if (!raw || !raw.records) return;

    const records = raw.records;
    const spot = Number(records.underlyingValue || 0);
    const expiries = records.expiryDates || [];
    if (!expiries.length) return;

    const sel = document.getElementById("expirySelect");
    if (sel.options.length === 0) {
        expiries.forEach(exp => {
            const opt = document.createElement("option");
            opt.value = exp;
            opt.innerText = exp;
            sel.appendChild(opt);
        });
    }

    const selectedExpiry = sel.value || expiries[0];
    const dataList = records.data.filter(item => item.expiryDate === selectedExpiry);

    const step = 50;
    const atm = Math.round(spot / step) * step;
    const targetStrikes = [atm - 100, atm - 50, atm, atm + 50, atm + 100];

    let totCeOi = 0, totPeOi = 0;
    let maxCeOi = 0, maxPeOi = 0;
    let resistance = 0, support = 0;
    const strikeMap = {};

    dataList.forEach(item => {
        const stk = item.strikePrice;
        const ce = item.CE || {};
        const pe = item.PE || {};

        const ceOi = ce.openInterest || 0;
        const peOi = pe.openInterest || 0;
        const ceChg = ce.changeinOpenInterest || 0;
        const peChg = pe.changeinOpenInterest || 0;

        totCeOi += ceOi;
        totPeOi += peOi;

        if (ceOi > maxCeOi) { maxCeOi = ceOi; resistance = stk; }
        if (peOi > maxPeOi) { maxPeOi = peOi; support = stk; }

        strikeMap[stk] = { ceChg, peChg };
    });

    let maxPain = atm;
    let minLoss = Infinity;
    Object.keys(strikeMap).forEach(s1 => {
        let totalLoss = 0;
        const s1Num = Number(s1);
        Object.keys(strikeMap).forEach(s2 => {
            const s2Num = Number(s2);
            const ceVal = Math.max(0, s1Num - s2Num) * strikeMap[s2].ceChg;
            const peVal = Math.max(0, s2Num - s1Num) * strikeMap[s2].peChg;
            totalLoss += (ceVal + peVal);
        });
        if (totalLoss < minLoss && totalLoss > 0) {
            minLoss = totalLoss;
            maxPain = s1Num;
        }
    });

    const pcr = totCeOi > 0 ? Number((totPeOi / totCeOi).toFixed(2)) : 1.0;

    const ceChgs = targetStrikes.map(s => strikeMap[s]?.ceChg || 0);
    const peChgs = targetStrikes.map(s => strikeMap[s]?.peChg || 0);

    const sumCe = ceChgs.reduce((a, b) => a + b, 0);
    const sumPe = peChgs.reduce((a, b) => a + b, 0);
    const netDiff = sumPe - sumCe;

    let signal = "";
    if (pcr >= 1.15 && netDiff > 250000) {
        signal = "STRONG BUY (BULLISH)";
    } else if (pcr <= 0.85 && netDiff < -250000) {
        signal = "STRONG SELL (BEARISH)";
    } else if (netDiff > 150000 && spot >= support) {
        signal = "BUY ON DIPS";
    } else if (netDiff < -150000 && spot <= resistance) {
        signal = "SELL ON RISE";
    }

    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0];

    const newRecord = {
        time: timeStr,
        spot: spot,
        atm: atm,
        pcr: pcr,
        maxPain: maxPain,
        support: support,
        resistance: resistance,
        ce_m100: ceChgs[0],
        ce_m50: ceChgs[1],
        ce_atm: ceChgs[2],
        ce_p50: ceChgs[3],
        ce_p100: ceChgs[4],
        pe_m100: peChgs[0],
        pe_m50: peChgs[1],
        pe_atm: peChgs[2],
        pe_p50: peChgs[3],
        pe_p100: peChgs[4],
        totCe: sumCe,
        totPe: sumPe,
        netDiff: netDiff,
        signal: signal
    };

    let stored = JSON.parse(localStorage.getItem(DB_KEY) || "[]");
    stored.unshift(newRecord);
    if (stored.length > 400) stored.pop();
    localStorage.setItem(DB_KEY, JSON.stringify(stored));

    renderTable();
}

function renderTable() {
    const stored = JSON.parse(localStorage.getItem(DB_KEY) || "[]");
    const tbody = document.getElementById("tableBody");
    tbody.innerHTML = "";

    if (!stored.length) return;

    const latest = stored[0];
    document.getElementById("cardSpot").innerText = latest.spot.toFixed(2);
    document.getElementById("cardAtm").innerText = "ATM: " + latest.atm;
    document.getElementById("cardPcr").innerText = latest.pcr.toFixed(2);
    document.getElementById("pcrBias").innerText = latest.pcr >= 1.15 ? "Bullish" : (latest.pcr <= 0.85 ? "Bearish" : "Neutral");
    document.getElementById("cardMaxPain").innerText = latest.maxPain;
    document.getElementById("cardSupport").innerText = latest.support;
    document.getElementById("cardResistance").innerText = latest.resistance;
    document.getElementById("lastSync").innerText = "Sync: " + latest.time;

    stored.forEach(r => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-slate-800/40 transition";

        let sigBadge = `<span class="text-slate-600">-</span>`;
        if (r.signal.includes("BUY")) {
            sigBadge = `<span class="bg-emerald-900/50 text-emerald-300 border border-emerald-500 px-1.5 py-0.5 rounded font-black">${r.signal}</span>`;
        } else if (r.signal.includes("SELL")) {
            sigBadge = `<span class="bg-rose-900/50 text-rose-300 border border-rose-500 px-1.5 py-0.5 rounded font-black">${r.signal}</span>`;
        }

        const netClass = r.netDiff >= 0 ? "text-emerald-400" : "text-rose-400";

        tr.innerHTML = `
            <td class="py-1.5 px-2 text-slate-300 font-semibold border-r border-slate-800">${r.time}</td>
            <td class="py-1.5 px-2 text-amber-300 font-bold border-r border-slate-800">${r.spot.toFixed(2)}</td>
            <td class="py-1.5 px-1 text-rose-400/80">${fmt(r.ce_m100)}</td>
            <td class="py-1.5 px-1 text-rose-400/80">${fmt(r.ce_m50)}</td>
            <td class="py-1.5 px-1 text-rose-200 font-bold bg-rose-950/30">${fmt(r.ce_atm)}</td>
            <td class="py-1.5 px-1 text-rose-400/80">${fmt(r.ce_p50)}</td>
            <td class="py-1.5 px-1 text-rose-400/80 border-r border-slate-800">${fmt(r.ce_p100)}</td>

            <td class="py-1.5 px-1 text-emerald-400/80">${fmt(r.pe_m100)}</td>
            <td class="py-1.5 px-1 text-emerald-400/80">${fmt(r.pe_m50)}</td>
            <td class="py-1.5 px-1 text-emerald-200 font-bold bg-emerald-950/30">${fmt(r.pe_atm)}</td>
            <td class="py-1.5 px-1 text-emerald-400/80">${fmt(r.pe_p50)}</td>
            <td class="py-1.5 px-1 text-emerald-400/80 border-r border-slate-800">${fmt(r.pe_p100)}</td>

            <td class="py-1.5 px-2 font-semibold border-r border-slate-800 text-rose-300">${fmt(r.totCe)}</td>
            <td class="py-1.5 px-2 font-semibold border-r border-slate-800 text-emerald-300">${fmt(r.totPe)}</td>
            <td class="py-1.5 px-2 font-black border-r border-slate-800 ${netClass}">${fmt(r.netDiff)}</td>
            <td class="py-1.5 px-2 text-center">${sigBadge}</td>
        `;
        tbody.appendChild(tr);
    });
}

function exportToExcel() {
    const stored = JSON.parse(localStorage.getItem(DB_KEY) || "[]");
    if (!stored.length) {
        alert("डाउनलोड करण्यासाठी डेटा नाही.");
        return;
    }
    const ws = XLSX.utils.json_to_sheet(stored);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "NIFTY_OI_1Min");
    XLSX.writeFile(wb, `NIFTY_OI_Log_${new Date().toISOString().slice(0, 10)}.xlsx`);
}

function clearHistoryManual() {
    if (confirm("रेकॉर्ड केलेला सर्व डेटा साफ करायचा आहे का?")) {
        localStorage.setItem(DB_KEY, JSON.stringify([]));
        renderTable();
    }
}

document.addEventListener("DOMContentLoaded", () => {
    renderTable();
    recordSnapshot();
    setInterval(recordSnapshot, 60000);
});
