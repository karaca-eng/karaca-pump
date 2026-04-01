import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
from datetime import datetime
from collections import deque

# --- CONFIGURATION (3DK / 60K AYARI) ---
VOL_THRESHOLD_3M = 60000  # 3 Dakikada toplam 60k USDT Hacim
PUMP_LIMIT_3M = 1.0       # 3 Dakikada min %1.0 Değişim
TRI_WINDOW = 180          # 3 Dakika (Saniye cinsinden)

SHORT_WINDOW = 60         # 1m (Tablo için)
MID_WINDOW = 300          # 5m (Tablo için)
LONG_WINDOW = 900         # 15m (Tablo için)
MAX_DISPLAY_ROWS = 100 

class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.stats_hourly = {} 
        self.stats_daily = {}  
        self.lock = threading.RLock() 
        self.last_heartbeat = 0  
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        self.last_reset_day = datetime.now().day

    def check_resets(self):
        now = datetime.now()
        if now.hour != self.last_reset_hour:
            self.stats_hourly.clear()
            self.last_reset_hour = now.hour
        if now.day != self.last_reset_day:
            self.stats_daily.clear()
            self.last_reset_day = now.day

    def process_ticker(self, data):
        now = time.time()
        with self.lock:
            self.check_resets()
            self.last_heartbeat = now  
            self.total_pairs = len(data)
            for item in data:
                symbol = item['s']
                if not symbol.endswith('USDT'): continue
                price, quote_vol = float(item['c']), float(item['q'])
                if symbol not in self.history: 
                    self.history[symbol] = deque(maxlen=1200) 
                self.history[symbol].append((now, price, quote_vol))
                self.check_logic(symbol, now)

    def check_logic(self, symbol, now):
        hist = list(self.history[symbol])
        if len(hist) < 10: return
        current = hist[-1]
        data_age = now - hist[0][0] 

        # 3 Dakikalık Tetikleyici Verileri
        past_3m = next((x for x in hist if now - x[0] <= TRI_WINDOW), hist[0])
        chg_3m = ((current[1] - past_3m[1]) / past_3m[1]) * 100
        vol_3m = current[2] - past_3m[2]

        # Tablo için Diğer Veriler
        past_1m = next((x for x in hist if now - x[0] <= SHORT_WINDOW), hist[0])
        past_5m = next((x for x in hist if now - x[0] <= MID_WINDOW), hist[0])
        
        chg_1m = ((current[1] - past_1m[1]) / past_1m[1]) * 100
        chg_5m = ((current[1] - past_5m[1]) / past_5m[1]) * 100 if data_age >= MID_WINDOW else 0.0
        chg_15m = ((current[1] - hist[0][1]) / hist[0][1]) * 100 if data_age >= LONG_WINDOW else 0.0

        res_type = None
        # TETİKLEME: 3 Dakikalık Hacim ve Fiyat Hareketine Göre
        if vol_3m >= VOL_THRESHOLD_3M:
            if chg_3m >= PUMP_LIMIT_3M: res_type = "PUMP"
            elif chg_3m <= -PUMP_LIMIT_3M: res_type = "DUMP"

        if res_type:
            self.add_signal(symbol, current[1], chg_1m, chg_5m, chg_15m, res_type)

    def add_signal(self, symbol, price, c1, c5, c15, s_type):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")
        with self.lock:
            # Aynı dakika içinde aynı yöne tekrar basma
            for s in self.signals[:5]:
                if s.get('Symbol') == sym_clean and s.get('Time', '')[:-1] == t_str[:-1] and s.get('P/D') == s_type: return
            
            if sym_clean not in self.stats_hourly: self.stats_hourly[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_hourly[sym_clean][s_type] += 1
            if sym_clean not in self.stats_daily: self.stats_daily[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_daily[sym_clean][s_type] += 1
            
            self.signals.insert(0, {
                "Time": t_str, "Symbol": sym_clean, "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
                "C1": c1, "C5": c5, "C15": c15, "P/D": s_type,
                "SnapP": self.stats_daily[sym_clean]["PUMP"], "SnapD": self.stats_daily[sym_clean]["DUMP"]
            })
            if len(self.signals) > MAX_DISPLAY_ROWS: self.signals.pop()

@st.cache_resource
def get_radar_instance(): return MarketRadar()

async def binance_worker(radar_obj):
    uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                while True:
                    msg = await ws.recv()
                    radar_obj.process_ticker(json.loads(msg))
        except: await asyncio.sleep(5)

# --- UI ---
st.set_page_config(layout="wide", page_title="SinyalEngineer Radar")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 10px; margin-bottom: 10px; border-left: 5px solid #f1c40f; }
    table { width: 100%; border-collapse: collapse; }
    th, td { white-space: nowrap; padding: 10px 15px; text-align: left; border-bottom: 1px solid #222; }
    .sym-link { color: #f1c40f; text-decoration: none; font-weight: bold; }
    .green-arrow { color: #00ff88; font-weight: bold; }
    .red-arrow { color: #ff4b4b; font-weight: bold; }
    div[data-testid="stTextInput"] > div { min-height: 0px; padding: 0px; }
    div[data-testid="stTextInput"] input { padding: 5px 10px; font-size: 0.85rem; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()
if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Header
h1, h2, h3 = st.columns([2, 1, 1])
h1.title("🛡️ Binance Price Radar")
is_alive = (time.time() - radar.last_heartbeat) < 15 if radar.last_heartbeat > 0 else False
status_html = '<span class="status-live">● SYSTEM LIVE</span>' if is_alive else '<span class="status-offline">● OFFLINE</span>'
h2.markdown(f"<div style='margin-top:10px;'>{status_html}</div>", unsafe_allow_html=True)
h2.markdown(f'<a href="https://x.com/SinyalEngineer" target="_blank" style="color:white; text-decoration:none; font-weight:bold;">𝕏 @SinyalEngineer</a>', unsafe_allow_html=True)
h3.metric("Pairs Tracked", radar.total_pairs)

st.divider()

# Layout
col_side, col_main = st.columns([1, 4])

with col_main:
    header_col, search_col = st.columns([3, 1])
    header_col.subheader("📡 Live Signals (3m/60k Trigger)")
    search_query = search_col.text_input("Filter", placeholder="🔍 Sym...", label_visibility="collapsed").upper()

placeholder_side = col_side.empty()
placeholder_main = col_main.empty()

def get_color(val):
    if val > 0.1: return "#00ff88"
    if val < -0.1: return "#ff4b4b"
    return "white"

while True:
    with placeholder_side.container():
        st.subheader("🔥 Top 5 Activity")
        with radar.lock:
            h_stats = dict(radar.stats_hourly)
            sorted_stats = sorted(h_stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
            if not sorted_stats: st.write("Scanning...")
            for sym, counts in sorted_stats:
                tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                st.markdown(f'''<div class="stat-card">
                    <a href="{tv_url}" target="_blank" class="sym-link">{sym}</a><br>
                    <small><span class="green-arrow">↑ {counts["PUMP"]}</span> | <span class="red-arrow">↓ {counts["DUMP"]}</span></small>
                </div>''', unsafe_allow_html=True)

    with placeholder_main.container():
        with radar.lock:
            signals_copy = list(radar.signals)
            display_data = [s for s in signals_copy if search_query in s.get('Symbol', '')] if search_query else signals_copy
            if display_data:
                html = "<table><tr><th>Time</th><th>Symbol (Daily ↑/↓)</th><th>Price</th><th>1m</th><th>5m</th><th>15m</th><th>Type</th></tr>"
                for row in display_data:
                    sym = row.get('Symbol', 'UNK'); p_count = row.get('SnapP', 0); d_count = row.get('SnapD', 0)
                    tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                    c1 = row.get('C1', 0); c5 = row.get('C5', 0); c15 = row.get('C15', 0)
                    html += f"<tr><td>{row.get('Time')}</td>"
                    html += f"<td><a href='{tv_url}' target='_blank' class='sym-link'>{sym}</a> <small class='green-arrow'>↑{p_count}</small> <small class='red-arrow'>↓{d_count}</small></td>"
                    html += f"<td>{row.get('Price')}</td>"
                    html += f"<td style='color:{get_color(c1)}; font-weight:bold;'>{c1:+.2f}%</td>"
                    html += f"<td style='color:{get_color(c5)}; font-weight:bold;'>{c5:+.2f}%</td>"
                    html += f"<td style='color:{get_color(c15)}; font-weight:bold;'>{c15:+.2f}%</td>"
                    html += f"<td><span class='{'pump-label' if row.get('P/D')=='PUMP' else 'dump-label'}'>{row.get('P/D')}</span></td></tr>"
                st.markdown(html + "</table>", unsafe_allow_html=True)
            else: st.info("Scanning Market (3m filter active)...")
    time.sleep(1)
