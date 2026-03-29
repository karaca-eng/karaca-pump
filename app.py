import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
from datetime import datetime
from collections import deque


# --- KONFİGÜRASYON ---
VOL_THRESHOLD = 35000  # 50,000 USDT Hacim Barajı (1dk veya 5dk içinde)
SHORT_WINDOW = 60  # 1 Dakika
MEDIUM_WINDOW = 300  # 5 Dakika
SHORT_PUMP_LIMIT = 2.0  # %1.2
MEDIUM_PUMP_LIMIT = 2.0  # %3.0
MAX_DISPLAY_ROWS = 100


class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.lock = threading.Lock()
        self.last_heartbeat = 0  # Sistem aktiflik takibi için
        self.total_pairs = 0

    def process_data(self, data):
        now = time.time()
        with self.lock:
            self.last_heartbeat = now  # Veri geldiği sürece güncellenir
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
        medium_past = data[0]

        current_price = data[-1][1]
        current_vol = data[-1][2]

        chg_1m = ((current_price - short_past[1]) / short_past[1]) * 100
        vol_1m = current_vol - short_past[2]

        chg_5m = ((current_price - medium_past[1]) / medium_past[1]) * 100
        vol_5m = current_vol - medium_past[2]

        res = None
        if vol_1m >= VOL_THRESHOLD and chg_1m >= SHORT_PUMP_LIMIT:
            res = ("PUMP", chg_1m)
        elif vol_5m >= VOL_THRESHOLD and chg_5m >= MEDIUM_PUMP_LIMIT:
            res = ("PUMP", chg_5m)
        elif vol_1m >= VOL_THRESHOLD and chg_1m <= -SHORT_PUMP_LIMIT:
            res = ("DUMP", chg_1m)
        elif vol_5m >= VOL_THRESHOLD and chg_5m <= -MEDIUM_PUMP_LIMIT:
            res = ("DUMP", chg_5m)

        if res:
            self.add_signal(symbol, current_price, res[1], res[0])

    def add_signal(self, symbol, price, change, s_type):
        t_str = datetime.now().strftime("%H:%M:%S")
        # Aynı sinyalin tekrarlanmaması için kontrol
        for s in self.signals[:5]:
            if s['Symbol'] == symbol.replace("USDT", "") and s['Time'][:-1] == t_str[:-1]:
                return

        new_sig = {
            "Time": t_str,
            "Symbol": symbol.replace("USDT", ""),
            "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
            "Change": f"{change:+.2f}%",
            "P/D": s_type
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
            await asyncio.sleep(5)  # Bağlantı koparsa bekle ve tekrar dene


# --- UI TASARIMI ---
st.set_page_config(layout="wide", page_title="Binance P/D Pro")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; }
    .status-offline { color: #ff4b4b; font-weight: bold; border: 1px solid #ff4b4b; padding: 2px 10px; border-radius: 15px; }
    .pump-label { background-color: #00ff88; color: black; padding: 4px 10px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()

if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Üst Panel (Header)
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    st.title("🛡️ Binance Futures P/D Radar")
with c2:
    # Sistem durumu kontrolü
    is_alive = (time.time() - radar.last_heartbeat) < 10 if radar.last_heartbeat > 0 else False
    status_html = '<span class="status-live">● SYSTEM LIVE</span>' if is_alive else '<span class="status-offline">● SYSTEM OFFLINE</span>'
    st.markdown(f"<br>{status_html}", unsafe_allow_html=True)
with c3:
    st.metric("Pairs Tracked", radar.total_pairs)

placeholder = st.empty()

while True:
    with placeholder.container():
        with radar.lock:
            if radar.signals:
                df = pd.DataFrame(radar.signals)
                html = "<table style='width:100%; border-collapse: collapse;'>"
                html += "<tr style='color: #888; border-bottom: 2px solid #333; text-align: left;'><th>Time</th><th>Symbol</th><th>Price</th><th>1m/5m Change</th><th>Type</th></tr>"

                for _, row in df.iterrows():
                    pd_style = "pump-label" if row['P/D'] == "PUMP" else "dump-label"
                    color = "#00ff88" if row['P/D'] == "PUMP" else "#ff4b4b"

                    html += f"<tr style='border-bottom: 1px solid #222; height: 45px;'>"
                    html += f"<td style='color: #666;'>{row['Time']}</td>"
                    html += f"<td style='font-weight: bold; color: #f1c40f;'>{row['Symbol']}</td>"
                    html += f"<td>{row['Price']}</td>"
                    html += f"<td style='color: {color}; font-weight: bold;'>{row['Change']}</td>"
                    html += f"<td><span class='{pd_style}'>{row['P/D']}</span></td>"
                    html += "</tr>"
                html += "</table>"
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.info(f"Market taranıyor... (Kriter: >{VOL_THRESHOLD / 1000}k$ Hacim & >%{SHORT_PUMP_LIMIT} Değişim)")

    time.sleep(1)
