import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
import requests
import numpy as np
from datetime import datetime
from collections import deque

# --- KONFİGÜRASYON ---
VOL_THRESHOLD = 30000  # 30k USDT Hacim Barajı
SHORT_WINDOW = 60      # 1 Dakika (60 saniye)
SHORT_PUMP_LIMIT = 1.0 # %1.0 Değişim
MAX_DISPLAY_ROWS = 100 # Tabloda görünecek max satır
RSI_PERIOD = 14        # RSI Periyodu

class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.stats = {} 
        self.oi_cache = {} 
        self.recent_liquidations = {} 
        self.lock = threading.Lock()
        self.last_heartbeat = 0  
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        # Cloud engellemesini aşmak için Header tanımlıyoruz
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def check_hourly_reset(self):
        """Saat başı istatistikleri sıfırlar"""
        current_hour = datetime.now().hour
        if current_hour != self.last_reset_hour:
            with self.lock:
                self.stats.clear()
                self.last_reset_hour = current_hour

    def calculate_rsi(self, symbol):
        """Binance REST API üzerinden mum verilerini çekip RSI hesaplar"""
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit=50"
            response = requests.get(url, headers=self.headers, timeout=5)
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            if not isinstance(data, list) or len(data) < RSI_PERIOD:
                return None
            
            closes = np.array([float(m[4]) for m in data])
            diff = np.diff(closes)
            gain = np.where(diff > 0, diff, 0)
            loss = np.where(diff < 0, -diff, 0)
            
            avg_gain = np.mean(gain[-(RSI_PERIOD+1):])
            avg_loss = np.mean(loss[-(RSI_PERIOD+1):])
            
            if avg_loss == 0: return 100
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return round(rsi, 1)
        except:
            return None

    def get_open_interest(self, symbol):
        """Binance REST API üzerinden Open Interest verisini çeker"""
        try:
            url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
            response = requests.get(url, headers=self.headers, timeout=5)
            if response.status_code == 200:
                return float(response.json()['openInterest'])
        except: pass
        return None

    def process_liquidation(self, data):
        """Likidasyon verilerini WebSocket'ten yakalar"""
        try:
            order = data['o']
            symbol = order['s']
            side = order['S'] # SELL = Short Liquidation, BUY = Long Liquidation
            self.recent_liquidations[symbol] = {"side": side, "time": time.time()}
        except: pass

    def process_ticker(self, data):
        """Fiyat ve Hacim verilerini WebSocket'ten işler"""
        now = time.time()
        with self.lock:
            self.check_hourly_reset()
            self.last_heartbeat = now  
            self.total_pairs = len(data)
            for item in data:
                symbol = item['s']
                if not symbol.endswith('USDT'): continue
                price, quote_vol = float(item['c']), float(item['q'])
                if symbol not in self.history: self.history[symbol] = deque(maxlen=305)
                self.history[symbol].append((now, price, quote_vol))
                self.check_pump_dump(symbol, now)

    def check_pump_dump(self, symbol, now):
        data = list(self.history[symbol])
        if len(data) < 10: return
        short_past = next((x for x in data if now - x[0] <= SHORT_WINDOW), data[0])
        current_price, current_vol = data[-1][1], data[-1][2]
        chg_1m = ((current_price - short_past[1]) / short_past[1]) * 100
        vol_1m = current_vol - short_past[2]

        res_type = None
        if vol_1m >= VOL_THRESHOLD:
            if chg_1m >= SHORT_PUMP_LIMIT: res_type = "PUMP"
            elif chg_1m <= -SHORT_PUMP_LIMIT: res_type = "DUMP"

        if res_type:
            # Sinyal anında RSI ve OI Kontrolü
            rsi = self.calculate_rsi(symbol)
            current_oi = self.get_open_interest(symbol)
            last_oi = self.oi_cache.get(symbol, 0)
            oi_conf = (current_oi > last_oi) if (current_oi and last_oi) else False
            if current_oi: self.oi_cache[symbol] = current_oi
            
            # Likidasyon (Squeeze) Kontrolü
            liqd = self.recent_liquidations.get(symbol)
            is_squeeze = False
            if liqd and (now - liqd['time'] < 15):
                if res_type == "PUMP" and liqd['side'] == "SELL": is_squeeze = True
                if res_type == "DUMP" and liqd['side'] == "BUY": is_squeeze = True

            self.add_signal(symbol, current_price, chg_1m, res_type, oi_conf, rsi, is_squeeze)

    def add_signal(self, symbol, price, change, s_type, confirmed, rsi, is_squeeze):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")
        # Tekrar eden sinyalleri engelle
        for s in self.signals[:5]:
            if s.get('Symbol') == sym_clean and s.get('Time', '')[:-1] == t_str[:-1]: return
        
        if sym_clean not in self.stats: self.stats[sym_clean] = {"PUMP": 0, "DUMP": 0}
        self.stats[sym_clean][s_type] += 1
        
        self.signals.insert(0, {
            "Time": t_str, "Symbol": sym_clean,
            "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
            "Change": f"{change:+.2f}%", "P/D": s_type,
            "OI": "✅ Confirmed" if confirmed else "⚪ Neutral",
            "RSI": str(rsi) if rsi is not None else "--",
            "Squeeze": "🔥 SQUEEZE" if is_squeeze else "Normal"
        })
        if len(self.signals) > MAX_DISPLAY_ROWS: self.signals.pop()

@st.cache_resource
def get_radar_instance(): return MarketRadar()

async def binance_worker(radar_obj):
    ticker_uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
    liq_uri = "wss://fstream.binance.com/ws/!forceOrder@arr"
    async def handle_tickers():
        async with websockets.connect(ticker_uri) as ws:
            while True:
                data = json.loads(await ws.recv())
                radar_obj.process_ticker(data)
    async def handle_liquidations():
        async with websockets.connect(liq_uri) as ws:
            while True:
                data = json.loads(await ws.recv())
                radar_obj.process_liquidation(data)
    await asyncio.gather(handle_tickers(), handle_liquidations())

# --- UI TASARIMI ---
st.set_page_config(layout="wide", page_title="SinyalEngineer Radar")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 10px; margin-bottom: 10px; border-left: 5px solid #f1c40f; }
    .warning-box { color: #ffb703; font-size: 0.75rem; font-style: italic; border-top: 1px solid #333; padding-top: 5px; }
    .squeeze-text { color: #ff4b4b; font-weight: bold; animation: blinker 1s linear infinite; }
    @keyframes blinker { 50% { opacity: 0; } }
    
    /* Tek Satır Garantisi ve Link Stili */
    table { width: 100%; border-collapse: collapse; table-layout: auto; }
    th, td { white-space: nowrap; padding: 8px 12px; text-align: left; border-bottom: 1px solid #222; }
    .sym-link { color: #f1c40f; text-decoration: none; font-weight: bold; }
    .sym-link:hover { text-decoration: underline; color: #ffffff; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()
if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Üst Panel (Header)
h1, h2, h3 = st.columns([2, 1, 1])
with h1:
    st.title("🛡️ Binance Futures Radar")
    st.markdown('<div class="warning-box">⚠️ Avoid high leverage trading. / Yüksek kaldıraçlı işlemlerden uzak durunuz.</div>', unsafe_allow_html=True)

with h2:
    status_html = '<span class="status-live">● SYSTEM LIVE</span>' if (time.time() - radar.last_heartbeat) < 10 else '<span class="status-offline">● OFFLINE</span>'
    st.markdown(f"<div style='margin-top:10px;'>{status_html}</div>", unsafe_allow_html=True)
    st.markdown(f'<div style="margin-top:5px;"><a href="https://x.com/SinyalEngineer" target="_blank" style="color:white; text-decoration:none; font-weight:bold;">𝕏 @SinyalEngineer</a><br><small style="color:#888;">Follow for more / Takip et</small></div>', unsafe_allow_html=True)

with h3:
    st.metric("Pairs Tracked", radar.total_pairs)

st.divider()

# Ana Ekran Düzeni
col_side, col_main = st.columns([1, 4])

with col_main:
    # Arama kutusu tablo başlığıyla aynı hizada
    c_title, c_search = st.columns([3, 1])
    c_title.subheader("📡 Live Signals (Click symbol for chart)")
    search_query = c_search.text_input("Filter", placeholder="Sym...", label_visibility="collapsed", key="global_search").upper()

placeholder_side = col_side.empty()
placeholder_main = col_main.empty()

while True:
    # Sol Panel: Top 5 Activity
    with placeholder_side.container():
        st.subheader("🔥 Top 5 Activity")
        with radar.lock:
            sorted_stats = sorted(radar.stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
            if not sorted_stats: st.write("Waiting for data...")
            for sym, counts in sorted_stats:
                tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                st.markdown(f'''<div class="stat-card">
                    <a href="{tv_url}" target="_blank" class="sym-link">{sym}</a><br>
                    <small><span style="color:#00ff88;">↑ {counts["PUMP"]}</span> | <span style="color:#ff4b4b;">↓ {counts["DUMP"]}</span></small>
                </div>''', unsafe_allow_html=True)

    # Sağ Panel: Canlı Sinyal Tablosu
    with placeholder_main.container():
        with radar.lock:
            # Arama filtresi uygulaması
            display_data = [s for s in radar.signals if search_query in s.get('Symbol', '')] if search_query else radar.signals
            
            if display_data:
                html = "<table>"
                html += "<tr style='color:#888; border-bottom:2px solid #333;'><th>Time</th><th>Symbol (↑/↓)</th><th>Price</th><th>1m Chg</th><th>Type</th><th>RSI(1m)</th><th>Status</th><th>OI</th></tr>"
                for row in display_data:
                    sym = row.get('Symbol', 'UNK')
                    counts = radar.stats.get(sym, {"PUMP": 0, "DUMP": 0})
                    
                    rsi_str = row.get('RSI', '--')
                    try:
                        rsi_val = float(rsi_str)
                        rsi_color = "#ff4b4b" if rsi_val > 70 else "#00ff88" if rsi_val < 30 else "white"
                    except:
                        rsi_color = "white"
                    
                    sq_status = row.get('Squeeze', 'Normal')
                    sq_html = f"<span class='squeeze-text'>{sq_status}</span>" if "SQUEEZE" in sq_status else "Normal"
                    
                    tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                    p_type = row.get('P/D', 'NONE')
                    p_color = '#00ff88' if 'PUMP' in p_type else '#ff4b4b'
                    
                    html += f"<tr>"
                    html += f"<td>{row.get('Time', '--')}</td>"
                    html += f"<td><a href='{tv_url}' target='_blank' class='sym-link'>{sym}</a> <small style='color:#00ff88;'>↑{counts['PUMP']}</small> <small style='color:#ff4b4b;'>↓{counts['DUMP']}</small></td>"
                    html += f"<td>{row.get('Price', '0')}</td>"
                    html += f"<td style='color:{p_color}; font-weight:bold;'>{row.get('Change', '0%')}</td>"
                    html += f"<td><span class='{'pump-label' if p_type=='PUMP' else 'dump-label'}'>{p_type}</span></td>"
                    html += f"<td style='color:{rsi_color}; font-weight:bold;'>{rsi_str}</td>"
                    html += f"<td>{sq_html}</td>"
                    html += f"<td>{row.get('OI', '--')}</td></tr>"
                st.markdown(html + "</table>", unsafe_allow_html=True)
            else:
                st.info("Scanning for High Conviction Signals... 🔍")

    time.sleep(1)
