import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, time
import pytz
from concurrent.futures import ThreadPoolExecutor

# ========== TELEGRAM ==========
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
MY_CHAT_ID = os.environ.get('MY_CHAT_ID')

# ========== STOCK LIST (Liquid stocks) ==========
STOCK_LIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "LT.NS", "AXISBANK.NS",
    "BAJFINANCE.NS", "HINDUNILVR.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS",
    "ADANIENT.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "WIPRO.NS", "HCLTECH.NS",
    "ONGC.NS", "POWERGRID.NS", "NTPC.NS", "M&M.NS", "TATAMOTORS.NS"
]

def send_telegram(message):
    if not TELEGRAM_TOKEN or not MY_CHAT_ID:
        print("Telegram credentials missing")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": MY_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        requests.post(url, data=payload, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram Error: {e}")
        return False

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def analyze_stock(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="5m")

        if df is None or len(df) < 10:
            return None

        df = df.dropna()
        latest = df.iloc[-1]
        day_open = df.iloc[0]['Open']
        current_price = latest['Close']
        volume = latest['Volume']
        avg_volume = df['Volume'].mean()

        # Price change from open
        change_pct = ((current_price - day_open) / day_open) * 100

        # Volume spike
        volume_spike = volume > (avg_volume * 1.8)

        # Simple RSI
        rsi = calculate_rsi(df['Close'])

        # Approximate VWAP
        df['typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['vp'] = df['typical'] * df['Volume']
        vwap = df['vp'].sum() / df['Volume'].sum()

        signal = None

        # Bullish conditions
        if (change_pct > 1.2 and 
            volume_spike and 
            current_price > vwap and 
            50 < rsi < 75):
            signal = "BULLISH"

        # Bearish conditions
        elif (change_pct < -1.2 and 
              volume_spike and 
              current_price < vwap and 
              25 < rsi < 50):
            signal = "BEARISH"

        if signal:
            return {
                "symbol": symbol.replace(".NS", ""),
                "price": round(current_price, 2),
                "change_pct": round(change_pct, 2),
                "rsi": round(rsi, 1),
                "signal": signal,
                "vwap": round(vwap, 2)
            }
        return None

    except Exception as e:
        print(f"Error on {symbol}: {e}")
        return None

def main():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    current_time = now.time()

    # Market hours check
    if not (time(9, 20) <= current_time <= time(15, 15)):
        print("Outside market hours. Exiting.")
        return

    print(f"⚡ Intraday Scanner started at {now.strftime('%H:%M:%S')}")

    alerts = []

    # Parallel fetching for better speed
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = executor.map(analyze_stock, STOCK_LIST)

    for result in results:
        if result:
            alerts.append(result)

    if alerts:
        # Sort by strongest move
        alerts = sorted(alerts, key=lambda x: abs(x['change_pct']), reverse=True)

        msg = f"⚡ *Intraday Speed Scanner*\n"
        msg += f"⏰ {now.strftime('%d %b %Y  %H:%M')}\n\n"

        for alert in alerts[:8]:  # Max 8 alerts
            emoji = "🟢" if alert['signal'] == "BULLISH" else "🔴"
            msg += (
                f"{emoji} *{alert['symbol']}* → {alert['signal']}\n"
                f"₹{alert['price']}  ({alert['change_pct']}%)\n"
                f"RSI: {alert['rsi']} | VWAP: ₹{alert['vwap']}\n\n"
            )

        send_telegram(msg)
        print(f"✅ Sent {len(alerts)} alerts to Telegram")
    else:
        print("No high-probability setups found right now.")

if __name__ == "__main__":
    main()