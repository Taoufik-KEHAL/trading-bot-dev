import requests
import psycopg2
from datetime import datetime, timezone, timedelta
import time

PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"
]

INTERVAL = "5m"   # better for first backtest than 1m
LIMIT = 1000

from db_config import DB_CONFIG as DB

start = datetime.now(timezone.utc) - timedelta(days=365)
end = datetime.now(timezone.utc)

def ms(dt):
    return int(dt.timestamp() * 1000)

conn = psycopg2.connect(**DB)
cur = conn.cursor()

for symbol in PAIRS:
    print(f"Downloading {symbol}...")

    current = start

    while current < end:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": LIMIT,
            "startTime": ms(current),
        }

        data = requests.get(url, params=params, timeout=20).json()

        if not data:
            break

        for c in data:
            cur.execute("""
                INSERT INTO market_candles
                (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
            """, (
                symbol,
                INTERVAL,
                datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
                c[1],
                c[2],
                c[3],
                c[4],
                c[5],
                datetime.fromtimestamp(c[6] / 1000, tz=timezone.utc),
            ))

        conn.commit()

        last_open_time = data[-1][0]
        current = datetime.fromtimestamp(last_open_time / 1000, tz=timezone.utc) + timedelta(minutes=5)

        print(symbol, current)

        time.sleep(0.2)

cur.close()
conn.close()

print("Done.")
