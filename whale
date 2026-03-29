import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
from datetime import datetime
from collections import deque

# --- 1. CONFIGURATION ---
MIN_IMPACT = 0.20
MIN_VOL_LIMIT = 25000
LOOKBACK_WINDOW = 3.0
STREAK_WINDOW = 10
MAX_ROWS = 50


class AlphaStore:
    def __init__(self):
        self.events = []
        self.market_buffer = {}
        self.btc_buffer = deque(maxlen=60)
        self.lock = threading.Lock()
        self.last_sync = 0
        self.is_connected = False


@st.cache_resource
def get_store(): return AlphaStore()


store = get_store()


# --- 2. ANALYTICS ENGINE ---
def process_event(symbol, vol, impact, type_label, now):
    if vol < MIN_VOL_LIMIT or abs(impact) < MIN_IMPACT:
        return

    raw_side = "BUY" if impact > 0 else "SELL"
    display_side = "💹 BUY" if impact > 0 else "📉 SELL"

    with store.lock:
        found = False
        for ev in store.events:
            if ev['Symbol'] == symbol.upper() and ev['_raw_side'] == raw_side:
                if (now - ev['_ts_last'] < STREAK_WINDOW):
                    ev['Vol_USDT'] = vol
                    ev['Impact%'] = impact
                    ev['Hits'] += 1
                    ev['Time'] = datetime.now().strftime("%H:%M:%S")
                    ev['_ts_last'] = now
                    found = True
                    break

        if not found:
            new_ev = {
                "Time": datetime.now().strftime("%H:%M:%S"),
                "Symbol": symbol.upper(),
                "Hits": 1,
                "Side": display_side,
                "_raw_side": raw_side,  # Mantık için arka planda tutuyoruz
                "Impact%": impact,
                "Vol_USDT": vol,
                "Type": type_label,
                "_ts_start": now,
                "_ts_last": now
            }
            store.events.insert(0, new_ev)
            if len(store.events) > MAX_ROWS: store.events.pop()


def check_signals(symbol, now):
    with store.lock:
        if symbol not in store.market_buffer: return
        buffer = store.market_buffer[symbol]
        while buffer and (now - buffer[0]['t'] > LOOKBACK_WINDOW):
            buffer.popleft()
        if len(buffer) < 2: return

        start_p = buffer[0]['p']
        end_p = buffer[-1]['p']
        impact = ((end_p - start_p) / start_p) * 100
        total_vol = sum(x['v'] for x in buffer)
    process_event(symbol, total_vol, impact, "🚀 SWEEP", now)


# --- 3. BINANCE ENGINE ---
async def binance_engine():
    symbols = ["espusdt", "banusdt", "iousdt", "bluaiusdt", "alchusdt", "stousdt",
               "treeusdt", "beatusdt", "btcusdt", "ethusdt", "solusdt", "nightusdt",
               "adausdt", "dogeusdt", "atusdt", "fetusdt", "injusdt", "musdt",
               "flowusdt", "partiusdt", "tradoorusdt", "avaxusdt"]

    streams = "/".join([f"{s.lower()}@aggTrade" for s in symbols])
    uri = f"wss://fstream.binance.com/stream?streams={streams}/!forceOrder@arr"

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=15) as ws:
                store.is_connected = True
                while True:
                    msg = await ws.recv()
                    packet = json.loads(msg)
                    if "data" not in packet: continue
                    data = packet['data']
                    stream = packet.get('stream', '')
                    now = time.time()
                    store.last_sync = now

                    if "@aggTrade" in stream:
                        sym = data['s'].replace("USDT", "")
                        p, v = float(data['p']), float(data['p']) * float(data['q'])
                        if data['s'] == "BTCUSDT": store.btc_buffer.append(p)
                        with store.lock:
                            if sym not in store.market_buffer: store.market_buffer[sym] = deque()
                            store.market_buffer[sym].append({'p': p, 'v': v, 't': now})
                        check_signals(sym, now)

                    elif "!forceOrder" in stream:
                        order_list = data if isinstance(data, list) else [data]
                        for item in order_list:
                            if 'o' in item:
                                o = item['o']
                                l_vol = float(o['q']) * float(o['p'])
                                l_imp = 0.25 if o['S'] == "BUY" else -0.25
                                process_event(o['s'].replace("USDT", ""), l_vol, l_imp, "💀 LIQ", now)
        except:
            store.is_connected = False
            await asyncio.sleep(5)


# --- 4. UI ---
st.set_page_config(layout="wide", page_title="PRO IMPACT RADAR")

if "init" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_engine()), daemon=True).start()
    st.session_state.init = True

st.title("👁️ PRO IMPACT RADAR")

c1, c2, c3, c4 = st.columns(4)
status = "🟢 LIVE" if (time.time() - store.last_sync < 10 and store.is_connected) else "🔴 RECONNECTING"
with c1: st.metric("SYSTEM", status)
with c2: st.metric("VOL LIMIT", f"${MIN_VOL_LIMIT:,}")
with c3: st.metric("IMPACT LIMIT", f"%{MIN_IMPACT}")
with c4:
    btc_move = 0
    if len(store.btc_buffer) >= 2:
        btc_move = ((store.btc_buffer[-1] - store.btc_buffer[0]) / store.btc_buffer[0]) * 100
    st.metric("BTC 1m", f"{btc_move:+.3f}%")

with store.lock:
    if store.events:
        df = pd.DataFrame(store.events)


        # RENKLENDİRME FONKSİYONU (KeyError düzeltildi)
        def row_styler(row):
            # Artık '_raw_side' yerine görünen 'Side' kolonuna bakıyoruz
            is_buy = "BUY" in str(row['Side'])

            if is_buy:
                bg_color = '#002611'  # Koyu Yeşil
                text_color = '#00ff88'  # Parlak Yeşil
            else:
                bg_color = '#330006'  # Koyu Kırmızı
                text_color = '#ff4d4d'  # Parlak Kırmızı

            weight = 'bold' if abs(row['Impact%']) >= 0.50 else 'normal'
            return [f'background-color: {bg_color}; color: {text_color}; font-weight: {weight}'] * len(row)


        # Sadece bu kolonları göstereceğiz
        display_cols = ['Time', 'Symbol', 'Side', 'Hits', 'Impact%', 'Vol_USDT', 'Type']

        st.dataframe(
            df[display_cols].style.apply(row_styler, axis=1).format({
                "Vol_USDT": "${:,.0f}",
                "Impact%": "{:+.3f}%"
            }),
            width=2500, height=800, hide_index=True
        )
    else:
        st.info(f"Filtreler Aktif: >%{MIN_IMPACT} ve >${MIN_VOL_LIMIT:,} bekleniyor...")

time.sleep(1)
st.rerun()
