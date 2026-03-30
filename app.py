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
SHORT_PUMP_LIMIT = 1.0  
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
        self.last_reset_period = datetime.now().minute // 30
        self.last_reset_time_str = datetime.now().strftime("%H:%M")

    def check_periodic_reset(self):
        """30 dakikada bir (00 ve 30) istatistikleri sıfırlar"""
        now = datetime.now()
        current_period = now.minute // 30
        if current_period != self.last_reset_period:
            self.stats = {} 
            self.last_reset_period = current_period
            self.last_reset_time_str = now.strftime("%H:%M")

    def get_open_interest(self, symbol):
        """Binance REST API üzerinden OI çeker"""
        try:
            url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
            # Lock dışındayken çağrılmalı (Performans için)
            resp = requests.get(url, timeout=1.5)
            if resp.status_code == 200:
                return float(resp.json()['openInterest'])
        except: pass
        return None

    def process_data(self, data):
        now_ts = time.time()
        with self.lock:
            self.check_periodic_reset()
            self.last_heartbeat = now_ts  
            self.total_pairs = len(data)
            
            for item in data:
                symbol = item['s']
                if not symbol.endswith('USDT'): continue
                
                price, quote_vol = float(item['c']), float(item['q'])
                if symbol not in self.history: 
                    self.history[symbol] = deque(maxlen=305)
                
                self.history[symbol].append((now_ts, price, quote_vol))
                
                # Hareket analizi
                hist = list(self.history[symbol])
                if len(hist) < 10: continue
                
                short_past = next((x for x in hist if now_ts - x[0] <= SHORT_WINDOW), hist[0])
                chg_1m = ((price - short_past[1]) / short_past[1]) * 100
                vol_1m = quote_vol - short_past[2]

                res_type = None
                if vol_1m >= VOL_THRESHOLD:
                    if chg_1m >= SHORT_PUMP_LIMIT: res_type = "PUMP"
                    elif chg_1m <= -SHORT_PUMP_LIMIT: res_type = "DUMP"

                if res_type:
                    # Sinyali listeye ekle
                    self.add_signal_to_queue(symbol, price, chg_1m, res_type)

    def add_signal_to_queue(self, symbol, price, change, s_type):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")
        
        # Tekrar kontrolü
        for s in self.signals[:3]:
            if s['Symbol'] == sym_clean and s['Time'][:-2] == t_str[:-2]: return

        # Stats Güncelle
        if sym_clean not in self.stats: self.stats[sym_clean] = {"PUMP": 0, "DUMP": 0}
        self.stats[sym_clean][s_type] += 1
        
        # OI Onayı (Ayrı bir adımda eklenebilir ama burada basitleştiriyoruz)
        current_oi = self.get_open_interest(symbol)
        last_oi = self.oi_cache.get(symbol, 0)
        confirmed = (current_oi > last_oi) if (current_oi and last_oi) else False
        if current_oi: self.oi_cache[symbol] = current_oi

        self.signals.insert(0, {
            "Time": t_str, "Symbol": sym_clean,
            "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
            "Change": f"{change:+.2f}%", "P/D": s_type,
            "OI": "✅ Confirmed" if confirmed else "⚪ Neutral"
        })
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
                    radar_obj.process_data(json.loads(msg))
        except: await asyncio.sleep(5)

# --- UI ---
st.set_page_config(layout="wide", page_title="SinyalEngineer Radar")

st.markdown("""
    <style>
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 8px; margin-bottom: 8px; border-left: 4px solid #f1c40f; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()

if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Header
h1, h2, h3 = st.columns([2, 1, 1])
h1.title("🛡️ Binance Futures Radar")
h1.caption("⚠️ Avoid high leverage trading. / Yüksek kaldıraçlı işlemlerden uzak durunuz.")

# UI Loop
col_side, col_main = st.columns([1, 4])
placeholder_side = col_side.empty()
placeholder_main = col_main.empty()

while True:
    try:
        # Verileri kilit altında hızlıca KOPYALAYALIM (Hata almamak için kritik)
        with radar.lock:
            temp_stats = dict(radar.stats)
            temp_signals = list(radar.signals)
            temp_pairs = radar.total_pairs
            temp_heartbeat = radar.last_heartbeat
            temp_reset_time = radar.last_reset_time_str

        # Header Güncelleme (Canlılık durumu)
        is_alive = (time.time() - temp_heartbeat) < 10
        h2.markdown(f"<div><span class='status-live'>● {'SYSTEM LIVE' if is_alive else 'OFFLINE'}</span></div>", unsafe_allow_html=True)
        h2.markdown(f'<a href="https://x.com/SinyalEngineer" target="_blank" style="color:white;text-decoration:none;">𝕏 @SinyalEngineer</a>', unsafe_allow_html=True)
        h3.metric("Pairs Tracked", temp_pairs)

        # Sol Panel
        with placeholder_side.container():
            st.subheader("🔥 Top 5")
            min_to_reset = 30 - (datetime.now().minute % 30)
            st.caption(f"Reset: {temp_reset_time} | Next: {min_to_reset}m")
            
            sorted_items = sorted(temp_stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
            for sym, counts in sorted_items:
                st.markdown(f'''<div class="stat-card"><b>{sym}</b><br><small><span style="color:#00ff88;">P: {counts["PUMP"]}</span> | <span style="color:#ff4b4b;">D: {counts["DUMP"]}</span></small></div>''', unsafe_allow_html=True)

        # Sağ Panel
        with placeholder_main.container():
            st.subheader("📡 Live Signals")
            if temp_signals:
                df = pd.DataFrame(temp_signals)
                html = "<table style='width:100%; text-align: left; border-collapse: collapse;'>"
                html += "<tr style='color:#888; border-bottom: 2px solid #333;'><th>Time</th><th>Symbol</th><th>Price</th><th>1m Chg</th><th>Type</th><th>OI</th></tr>"
                for _, s in df.iterrows():
                    color = "#00ff88" if s['P/D'] == "PUMP" else "#ff4b4b"
                    oi_color = "#00ff88" if "Confirmed" in s['OI'] else "#888"
                    html += f"<tr style='border-bottom: 1px solid #222; height: 35px;'>"
                    html += f"<td>{s['Time']}</td><td style='color:#f1c40f;'>{s['Symbol']}</td>"
                    html += f"<td>{s['Price']}</td><td style='color:{color};'>{s['Change']}</td>"
                    html += f"<td><span class='{'pump-label' if s['P/D']=='PUMP' else 'dump-label'}'>{s['P/D']}</span></td>"
                    html += f"<td style='color:{oi_color};'>{s['OI']}</td></tr>"
                st.markdown(html + "</table>", unsafe_allow_html=True)
            else:
                st.info("Scanning for signals...")

    except Exception as e:
        # Herhangi bir hata anında uygulamanın çökmesini engelle, 1 saniye bekle ve devam et
        pass
    
    time.sleep(1)
