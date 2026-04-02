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
MIN_VOL_3M = 100000  # 3dk / 100k USDT
MIN_CHG_3M = 1.1  # 3dk / %1.1
CONFIRM_CHG_15M = 2.5  # 15dk Trend Onayı
FAST_STRIKE_CHG = 1.5  # 1dk içinde %1.5 olursa (Onay bekleme!)
TRI_WINDOW = 180
MAX_DISPLAY_ROWS = 100


class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.stats_hourly = {}
        self.stats_4h = {}
        self.lock = threading.RLock()
        self.last_heartbeat = 0
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        self.last_reset_4h_block = datetime.now().hour // 4
        self.headers = {'User-Agent': 'Mozilla/5.0'}

    def get_15m_price(self, symbol):
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=2"
            response = requests.get(url, headers=self.headers, timeout=2)
            if response.status_code == 200:
                return float(response.json()[0][1])  # Bir önceki mumun açılışı
        except:
            pass
        return None

    def check_resets(self):
        now = datetime.now()
        if now.hour != self.last_reset_hour:
            self.stats_hourly.clear()
            self.last_reset_hour = now.hour
        if (now.hour // 4) != self.last_reset_4h_block:
            self.stats_4h.clear()
            self.last_reset_4h_block = now.hour // 4

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
                    self.history[symbol] = deque(maxlen=400)
                self.history[symbol].append((now, price, quote_vol))
                self.check_logic(symbol, now)

    def check_logic(self, symbol, now):
        hist = list(self.history[symbol])
        if len(hist) < 10: return

        current = hist[-1]
        past_1m = next((x for x in hist if now - x[0] <= 60), hist[0])
        past_3m = next((x for x in hist if now - x[0] <= TRI_WINDOW), hist[0])

        c1 = ((current[1] - past_1m[1]) / past_1m[1]) * 100
        c3 = ((current[1] - past_3m[1]) / past_3m[1]) * 100
        vol_3m = current[2] - past_3m[2]
        vol_1m = current[2] - past_1m[2]

        # --- YENİ ÇİFT KATMANLI MANTIK ---

        # 1. KATMAN: FLASH ATTACK (Çok Hızlı Reaksiyon - 15dk beklemez!)
        # Eğer 1 dakika içinde %1.5 ve ciddi hacim varsa hemen uyar.
        if abs(c1) >= FAST_STRIKE_CHG and vol_1m >= 50000:
            res_type = "PUMP" if c1 > 0 else "DUMP"
            self.add_signal(symbol, current[1], c1, 0, vol_1m, res_type, "⚡ FLASH")
            return  # Çift sinyal olmasın diye çık

        # 2. KATMAN: CONFIRMED TREND (Emin Sinyal - 15dk Onayıyla)
        if vol_3m >= MIN_VOL_3M and abs(c3) >= MIN_CHG_3M:
            price_15m_ago = self.get_15m_price(symbol)
            if price_15m_ago:
                c15 = ((current[1] - price_15m_ago) / price_15m_ago) * 100
                is_consistent = (c3 > 0 and c15 > 0) or (c3 < 0 and c15 < 0)

                if is_consistent and abs(c15) >= CONFIRM_CHG_15M:
                    res_type = "PUMP" if c3 > 0 else "DUMP"
                    self.add_signal(symbol, current[1], c3, c15, vol_3m, res_type, "💎 CONFIRMED")

    def add_signal(self, symbol, price, chg_main, chg_ref, vol, s_type, mode):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")
        with self.lock:
            # Tekrarı engelle (Mod bazlı)
            for s in self.signals[:5]:
                if s.get('Symbol') == sym_clean and s.get('Time', '')[:-1] == t_str[:-1]: return

            if sym_clean not in self.stats_hourly: self.stats_hourly[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_hourly[sym_clean][s_type] += 1
            if sym_clean not in self.stats_4h: self.stats_4h[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_4h[sym_clean][s_type] += 1

            self.signals.insert(0, {
                "Time": t_str, "Symbol": sym_clean, "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
                "Chg": chg_main, "Ref": chg_ref, "Vol": vol, "P/D": s_type, "Mode": mode,
                "SnapP": self.stats_4h[sym_clean]["PUMP"], "SnapD": self.stats_4h[sym_clean]["DUMP"]
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
                    radar_obj.process_ticker(json.loads(await ws.recv()))
        except:
            await asyncio.sleep(5)


# --- UI ---
st.set_page_config(layout="wide", page_title="Speed & Conviction Radar")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 10px; margin-bottom: 10px; border-left: 5px solid #f1c40f; }
    table { width: 100%; border-collapse: collapse; }
    th, td { white-space: nowrap; padding: 12px 15px; text-align: left; border-bottom: 1px solid #222; }
    .sym-link { color: #f1c40f; text-decoration: none; font-weight: bold; font-size: 1.1rem; }
    .green-arrow { color: #00ff88; font-weight: bold; }
    .red-arrow { color: #ff4b4b; font-weight: bold; }
    /* FLASH Satırlar (Daha parlak ve yanıp sönen efektli) */
    .row-flash-pump { background-color: rgba(0, 255, 136, 0.25) !important; border-left: 5px solid #00ff88 !important; }
    .row-flash-dump { background-color: rgba(255, 75, 75, 0.25) !important; border-left: 5px solid #ff4b4b !important; }
    .row-conf-pump { background-color: rgba(0, 255, 136, 0.1) !important; }
    .row-conf-dump { background-color: rgba(255, 75, 75, 0.1) !important; }
    div[data-testid="stTextInput"] > div { min-height: 0px; padding: 0px; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()
if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Header
h1, h2, h3 = st.columns([2, 1, 1])
h1.title("🛡️ Speed & Conviction Radar")
h1.caption("⚡ Flash: Hızlı Reaksiyon | 💎 Confirmed: Trend Onayı")
status_html = '<span class="status-live">● SYSTEM LIVE</span>' if (
                                                                              time.time() - radar.last_heartbeat) < 15 else '<span class="status-offline">● OFFLINE</span>'
h2.markdown(f"<div style='margin-top:10px;'>{status_html}</div>", unsafe_allow_html=True)
h2.markdown(
    f'<a href="https://x.com/SinyalEngineer" target="_blank" style="color:white; text-decoration:none;">𝕏 @SinyalEngineer</a>',
    unsafe_allow_html=True)
h3.metric("Pairs Tracked", radar.total_pairs)

st.divider()

col_side, col_main = st.columns([1, 4])
with col_main:
    header_col, search_col = st.columns([3, 1])
    header_col.subheader("📡 Intelligence Stream")
    search_query = search_col.text_input("Filter", placeholder="🔍 Sym...", label_visibility="collapsed",
                                         key="gs").upper()

placeholder_side = col_side.empty()
placeholder_main = col_main.empty()

while True:
    with placeholder_side.container():
        st.subheader("🔥 Top 5 Activity")
        with radar.lock:
            h_stats = getattr(radar, 'stats_hourly', {})
            sorted_stats = sorted(h_stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
            for sym, counts in sorted_stats:
                tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                st.markdown(f'''<div class="stat-card"><a href="{tv_url}" target="_blank" class="sym-link">{sym}</a><br>
                <small><span class="green-arrow">↑ {counts["PUMP"]}</span> | <span class="red-arrow">↓ {counts["DUMP"]}</span></small></div>''',
                            unsafe_allow_html=True)

    with placeholder_main.container():
        with radar.lock:
            signals = list(getattr(radar, 'signals', []))
            display_data = [s for s in signals if search_query in s.get('Symbol', '')] if search_query else signals
            if display_data:
                html = "<table><tr><th>Time</th><th>Symbol (4H ↑/↓)</th><th>Price</th><th>Momentum</th><th>15m Ref</th><th>Vol</th><th>Status</th><th>Type</th></tr>"
                for row in display_data:
                    sym = row.get('Symbol');
                    p_count = row.get('SnapP');
                    d_count = row.get('SnapD')
                    tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                    chg = row.get('Chg');
                    ref = row.get('Ref');
                    vol = row.get('Vol');
                    p_type = row.get('P/D');
                    mode = row.get('Mode')

                    # Dinamik Satır Renkleri
                    row_class = ""
                    if "FLASH" in mode:
                        row_class = ' class="row-flash-pump"' if p_type == "PUMP" else ' class="row-flash-dump"'
                    else:
                        row_class = ' class="row-conf-pump"' if p_type == "PUMP" else ' class="row-conf-dump"'

                    html += f"<tr{row_class}><td>{row.get('Time')}</td>"
                    html += f"<td><a href='{tv_url}' target='_blank' class='sym-link'>{sym}</a> <small class='green-arrow'>↑{p_count}</small> <small class='red-arrow'>↓{d_count}</small></td>"
                    html += f"<td>{row.get('Price')}</td>"
                    html += f"<td style='font-weight:bold;'>{chg:+.2f}%</td>"
                    html += f"<td>{ref:+.2f}%</td>"
                    html += f"<td>{vol / 1000:.0f}k</td>"
                    html += f"<td><b style='color:#f1c40f;'>{mode}</b></td>"
                    html += f"<td><span class='{'pump-label' if p_type == 'PUMP' else 'dump-label'}'>{p_type}</span></td></tr>"
                st.markdown(html + "</table>", unsafe_allow_html=True)
            else:
                st.info("Hız ve Onay radarı aktif... 🔍")
    time.sleep(1)
