import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
from datetime import datetime
from collections import deque

# --- CONFIGURATION (BALİNA VE ZAMAN AYARLARI) ---
WHALE_VOL_3M = 100000  # Balina: 3dk / 100k USDT
WHALE_CHG_3M = 2.0  # Balina: 3dk / %2.0
NORMAL_LIMIT_3M = 1.1  # Normal Sinyal: %1.1
TRI_WINDOW = 180  # 3 Dakika (Tetikleyici)

SHORT_WINDOW = 60  # 1m
MID_WINDOW = 300  # 5m
LONG_WINDOW = 900  # 15m
MAX_DISPLAY_ROWS = 100


class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.stats_hourly = {}
        self.stats_4h = {}  # 4 Saatte bir sıfırlanacak sayaç
        self.lock = threading.RLock()
        self.last_heartbeat = 0
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        self.last_reset_4h_block = datetime.now().hour // 4  # 0,1,2,3,4,5 nolu bloklar

    def check_resets(self):
        now = datetime.now()
        current_hour = now.hour
        current_4h_block = current_hour // 4

        # Saatlik Reset (Top 5 için)
        if current_hour != self.last_reset_hour:
            self.stats_hourly.clear()
            self.last_reset_hour = current_hour

        # 4 Saatlik Reset (Tablodaki sayaçlar için)
        if current_4h_block != self.last_reset_4h_block:
            self.stats_4h.clear()
            self.last_reset_4h_block = current_4h_block

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

                # 15dk verisi için kapasite 3000'e çıkarıldı (Garanti çözüm)
                if symbol not in self.history:
                    self.history[symbol] = deque(maxlen=3000)

                self.history[symbol].append((now, price, quote_vol))
                self.check_logic(symbol, now)

    def check_logic(self, symbol, now):
        hist = list(self.history[symbol])
        if len(hist) < 20: return  # Minimum veri birikmeli

        current = hist[-1]
        data_age = now - hist[0][0]

        # 3 Dakikalık Analiz
        past_3m_point = next((x for x in hist if now - x[0] <= TRI_WINDOW), hist[0])
        chg_3m = ((current[1] - past_3m_point[1]) / past_3m_point[1]) * 100
        vol_3m = current[2] - past_3m_point[2]

        # 1m, 5m, 15m Hesaplamaları (Daha dayanıklı hale getirildi)
        past_1m = next((x for x in hist if now - x[0] <= SHORT_WINDOW), hist[0])
        c1 = ((current[1] - past_1m[1]) / past_1m[1]) * 100

        # 5m Kontrolü
        c5 = 0.0
        if data_age >= MID_WINDOW - 10:  # 10sn tolerans
            past_5m = next((x for x in hist if now - x[0] <= MID_WINDOW), hist[0])
            c5 = ((current[1] - past_5m[1]) / past_5m[1]) * 100

        # 15m Kontrolü
        c15 = 0.0
        if data_age >= LONG_WINDOW - 10:
            past_15m = next((x for x in hist if now - x[0] <= LONG_WINDOW), hist[0])
            c15 = ((current[1] - past_15m[1]) / past_15m[1]) * 100

        res_type = None
        is_whale = False

        if vol_3m >= WHALE_VOL_3M and abs(chg_3m) >= WHALE_CHG_3M:
            res_type = "PUMP" if chg_3m > 0 else "DUMP"
            is_whale = True
        elif vol_3m >= 30000 and abs(chg_3m) >= NORMAL_LIMIT_3M:
            res_type = "PUMP" if chg_3m > 0 else "DUMP"
            is_whale = False

        if res_type:
            self.add_signal(symbol, current[1], c1, chg_3m, c5, c15, res_type, is_whale)

    def add_signal(self, symbol, price, c1, c3, c5, c15, s_type, is_whale):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")
        with self.lock:
            for s in self.signals[:5]:
                if s.get('Symbol') == sym_clean and s.get('Time', '')[:-1] == t_str[:-1] and s.get(
                    'P/D') == s_type: return

            # Sayaçlar
            if sym_clean not in self.stats_hourly: self.stats_hourly[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_hourly[sym_clean][s_type] += 1
            if sym_clean not in self.stats_4h: self.stats_4h[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_4h[sym_clean][s_type] += 1

            self.signals.insert(0, {
                "Time": t_str, "Symbol": sym_clean, "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
                "C1": c1, "C3": c3, "C5": c5, "C15": c15, "P/D": s_type,
                "SnapP": self.stats_4h[sym_clean]["PUMP"],
                "SnapD": self.stats_4h[sym_clean]["DUMP"],
                "Whale": is_whale
            })
            if len(self.signals) > MAX_DISPLAY_ROWS: self.signals.pop()


@st.cache_resource
def get_radar_instance(): return MarketRadar()


async def binance_worker(radar_obj):
    uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                while True:
                    msg = await ws.recv()
                    radar_obj.process_ticker(json.loads(msg))
        except:
            await asyncio.sleep(5)


# --- UI ---
st.set_page_config(layout="wide", page_title="SinyalEngineer Whale Radar")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .status-offline { color: #ff4b4b; font-weight: bold; border: 1px solid #ff4b4b; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 10px; margin-bottom: 10px; border-left: 5px solid #f1c40f; }
    table { width: 100%; border-collapse: collapse; }
    th, td { white-space: nowrap; padding: 10px 15px; text-align: left; border-bottom: 1px solid #222; }
    .sym-link { color: #f1c40f; text-decoration: none; font-weight: bold; }
    .green-arrow { color: #00ff88; font-weight: bold; }
    .red-arrow { color: #ff4b4b; font-weight: bold; }
    .whale-pump-row { background-color: rgba(0, 255, 136, 0.15) !important; border-left: 5px solid #00ff88 !important; }
    .whale-dump-row { background-color: rgba(255, 75, 75, 0.15) !important; border-left: 5px solid #ff4b4b !important; }
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
h1.title("🛡️ Whale Decision Radar")
is_alive = (time.time() - radar.last_heartbeat) < 15 if radar.last_heartbeat > 0 else False
status_html = '<span class="status-live">● SYSTEM LIVE</span>' if is_alive else '<span class="status-offline">● OFFLINE</span>'
h2.markdown(f"<div style='margin-top:10px;'>{status_html}</div>", unsafe_allow_html=True)
h2.markdown(
    f'<a href="https://x.com/SinyalEngineer" target="_blank" style="color:white; text-decoration:none; font-weight:bold;">𝕏 @SinyalEngineer</a>',
    unsafe_allow_html=True)
h3.metric("Pairs Tracked", radar.total_pairs)

st.divider()

col_side, col_main = st.columns([1, 4])
with col_main:
    header_col, search_col = st.columns([3, 1])
    header_col.subheader("📡 Live Signals (4H Counters Active)")
    search_query = search_col.text_input("Filter", placeholder="🔍 Sym...", label_visibility="collapsed",
                                         key="gs").upper()

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
            h_stats = getattr(radar, 'stats_hourly', {})
            sorted_stats = sorted(h_stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
            if not sorted_stats: st.write("Scanning...")
            for sym, counts in sorted_stats:
                tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                st.markdown(f'''<div class="stat-card"><a href="{tv_url}" target="_blank" class="sym-link">{sym}</a><br>
                <small><span class="green-arrow">↑ {counts["PUMP"]}</span> | <span class="red-arrow">↓ {counts["DUMP"]}</span></small></div>''',
                            unsafe_allow_html=True)

    with placeholder_main.container():
        with radar.lock:
            signals_copy = list(getattr(radar, 'signals', []))
            display_data = [s for s in signals_copy if
                            search_query in s.get('Symbol', '')] if search_query else signals_copy
            if display_data:
                html = "<table><tr><th>Time</th><th>Symbol (4H ↑/↓)</th><th>Price</th><th>1m</th><th>3m(T)</th><th>5m</th><th>15m</th><th>Type</th></tr>"
                for row in display_data:
                    sym = row.get('Symbol', 'UNK');
                    p_count = row.get('SnapP', 0);
                    d_count = row.get('SnapD', 0)
                    tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                    c1, c3, c5, c15 = row.get('C1', 0), row.get('C3', 0), row.get('C5', 0), row.get('C15', 0);
                    p_type = row.get('P/D')

                    row_class = ""
                    whale_icon = ""
                    if row.get('Whale'):
                        whale_icon = " 🐳"
                        row_class = ' class="whale-pump-row"' if p_type == "PUMP" else ' class="whale-dump-row"'

                    html += f"<tr{row_class}><td>{row.get('Time')}</td>"
                    html += f"<td><a href='{tv_url}' target='_blank' class='sym-link'>{sym}{whale_icon}</a> <small class='green-arrow'>↑{p_count}</small> <small class='red-arrow'>↓{d_count}</small></td>"
                    html += f"<td>{row.get('Price')}</td>"
                    html += f"<td style='color:{get_color(c1)};'>{c1:+.2f}%</td>"
                    html += f"<td style='color:{get_color(c3)}; font-weight:bold;'>{c3:+.2f}%</td>"
                    html += f"<td style='color:{get_color(c5)};'>{c5:+.2f}%</td>"
                    html += f"<td style='color:{get_color(c15)};'>{c15:+.2f}%</td>"
                    html += f"<td><span class='{'pump-label' if p_type == 'PUMP' else 'dump-label'}'>{p_type}</span></td></tr>"
                st.markdown(html + "</table>", unsafe_allow_html=True)
            else:
                st.info("Waiting for signals (15m data warming up)...")
    time.sleep(1)
