import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
import requests
from datetime import datetime
from collections import deque

# --- CONFIGURATION ---
VOL_THRESHOLD = 30000
SHORT_WINDOW = 60
MEDIUM_WINDOW = 300
SHORT_PUMP_LIMIT = 1.0
MEDIUM_PUMP_LIMIT = 1.0
MAX_DISPLAY_ROWS = 100


class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.stats = {}
        self.oi_cache = {}
        self.lock = threading.Lock()
        self.last_heartbeat = 0
        self.total_pairs = 0

    def get_open_interest(self, symbol):
        """Fetches Open Interest from Binance REST API"""
        try:
            url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                return float(response.json()['openInterest'])
        except:
            pass
        return None

    def process_data(self, data):
        now = time.time()
        with self.lock:
            self.last_heartbeat = now
            self.total_pairs = len(data)
            for item in data:
                symbol = item['s']
                if not symbol.endswith('USDT'): continue

                price = float(item['c'])
                quote_vol = float(item['q'])

                if symbol not in self.history:
                    self.history[symbol] = deque(maxlen=305)

                self.history[symbol].append((now, price, quote_vol))
                self.check_pump_dump(symbol, now)

    def check_pump_dump(self, symbol, now):
        data = list(self.history[symbol])
        if len(data) < 10: return

        short_past = next((x for x in data if now - x[0] <= SHORT_WINDOW), data[0])
        current_price = data[-1][1]
        current_vol = data[-1][2]

        chg_1m = ((current_price - short_past[1]) / short_past[1]) * 100
        vol_1m = current_vol - short_past[2]

        res_type = None
        if vol_1m >= VOL_THRESHOLD:
            if chg_1m >= SHORT_PUMP_LIMIT:
                res_type = "PUMP"
            elif chg_1m <= -SHORT_PUMP_LIMIT:
                res_type = "DUMP"

        if res_type:
            current_oi = self.get_open_interest(symbol)
            last_oi = self.oi_cache.get(symbol, 0)

            oi_confirmed = False
            if current_oi and last_oi and current_oi > last_oi:
                oi_confirmed = True

            if current_oi: self.oi_cache[symbol] = current_oi
            self.add_signal(symbol, current_price, chg_1m, res_type, oi_confirmed)

    def add_signal(self, symbol, price, change, s_type, confirmed):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")

        for s in self.signals[:5]:
            if s['Symbol'] == sym_clean and s['Time'][:-1] == t_str[:-1]:
                return

        if sym_clean not in self.stats:
            self.stats[sym_clean] = {"PUMP": 0, "DUMP": 0}
        self.stats[sym_clean][s_type] += 1

        new_sig = {
            "Time": t_str,
            "Symbol": sym_clean,
            "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
            "Change": f"{change:+.2f}%",
            "P/D": s_type,
            "OI": "✅ Confirmed" if confirmed else "⚪ Neutral"
        }
        self.signals.insert(0, new_sig)
        if len(self.signals) > MAX_DISPLAY_ROWS: self.signals.pop()


@st.cache_resource
def get_radar_instance():
    return MarketRadar()


async def binance_worker(radar_obj):
    uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    radar_obj.process_data(data)
        except:
            await asyncio.sleep(5)

        # --- UI DESIGN ---


st.set_page_config(layout="wide", page_title="Binance P/D Pro")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .status-offline { color: #ff4b4b; font-weight: bold; border: 1px solid #ff4b4b; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9rem; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9rem; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 10px; margin-bottom: 10px; border-left: 5px solid #f1c40f; }
    .x-link { color: #ffffff; text-decoration: none; font-weight: bold; font-size: 0.95rem; display: flex; align-items: center; gap: 8px; }
    .x-link:hover { color: #1DA1F2; }
    .warning-box { color: #ffb703; font-size: 0.75rem; font-style: italic; margin-top: 5px; border-top: 1px solid #333; padding-top: 5px; }
    .oi-confirmed { color: #00ff88; font-weight: bold; font-size: 0.85rem; }
    .oi-neutral { color: #888; font-size: 0.85rem; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()

if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Header
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    st.title("🛡️ Binance Futures Radar")
    st.markdown(
        '<div class="warning-box">⚠️ Avoid high leverage trading. / Yüksek kaldıraçlı işlemlerden uzak durunuz.</div>',
        unsafe_allow_html=True)

with c2:
    is_alive = (time.time() - radar.last_heartbeat) < 10 if radar.last_heartbeat > 0 else False
    status_html = '<span class="status-live">● SYSTEM LIVE</span>' if is_alive else '<span class="status-offline">● SYSTEM OFFLINE</span>'
    st.markdown(f"<div style='margin-top:15px;'>{status_html}</div>", unsafe_allow_html=True)

    # X (Twitter) Account Link
    st.markdown(f"""
        <div style="margin-top:8px;">
            <a href="https://x.com/SinyalEngineer" class="x-link" target="_blank">
                <img src="https://upload.wikimedia.org/wikipedia/commons/5/57/X_logo_2023_original.svg" width="16" style="filter: invert(1);"> @SinyalEngineer
            </a>
        </div>
    """, unsafe_allow_html=True)

with c3:
    st.metric("Pairs Tracked", radar.total_pairs)

st.divider()

# Main Layout
placeholder = st.empty()

while True:
    with placeholder.container():
        col_side, col_main = st.columns([1, 4])

        with col_side:
            st.subheader("🔥 Top 5 Activity")
            with radar.lock:
                sorted_stats = sorted(radar.stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
                if not sorted_stats: st.write("Waiting for data...")
                for sym, counts in sorted_stats:
                    st.markdown(
                        f'<div class="stat-card"><div style="font-weight:bold; color:#f1c40f; font-size:18px;">{sym}</div><div style="display:flex; justify-content: space-between; margin-top:5px;"><span style="color:#00ff88;">PUMP: {counts["PUMP"]}</span><span style="color:#ff4b4b;">DUMP: {counts["DUMP"]}</span></div></div>',
                        unsafe_allow_html=True)

        with col_main:
            with radar.lock:
                if radar.signals:
                    df = pd.DataFrame(radar.signals)
                    html = "<table style='width:100%; border-collapse: collapse;'>"
                    html += "<tr style='color: #888; border-bottom: 2px solid #333; text-align: left;'><th>Time</th><th>Symbol</th><th>Price</th><th>1m Chg</th><th>Type</th><th>OI Confirmation</th></tr>"
                    for _, row in df.iterrows():
                        color = "#00ff88" if row['P/D'] == "PUMP" else "#ff4b4b"
                        oi_style = "oi-confirmed" if "Confirmed" in row['OI'] else "oi-neutral"
                        html += f"<tr style='border-bottom: 1px solid #222; height: 45px;'>"
                        html += f"<td style='color: #666;'>{row['Time']}</td>"
                        html += f"<td style='font-weight: bold; color: #f1c40f;'>{row['Symbol']}</td>"
                        html += f"<td>{row['Price']}</td>"
                        html += f"<td style='color: {color}; font-weight: bold;'>{row['Change']}</td>"
                        html += f"<td><span class='{'pump-label' if row['P/D'] == 'PUMP' else 'dump-label'}'>{row['P/D']}</span></td>"
                        html += f"<td><span class='{oi_style}'>{row['OI']}</span></td>"
                        html += "</tr>"
                    html += "</table>"
                    st.markdown(html, unsafe_allow_html=True)
                else:
                    st.info(f"Scanning market... (OI filtering enabled 🔍)")

    time.sleep(1)
