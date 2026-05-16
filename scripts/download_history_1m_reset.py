import argparse
import os
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values
import requests


PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"
]

INTERVAL = "1m"
LIMIT = 1000
BINANCE_URL = "https://api.binance.com/api/v3/klines"
ONE_MINUTE_INDEX = "idx_market_candles_1m_symbol_open_time"

from db_config import DB_CONFIG as DB


def floor_to_minute(dt):
    return dt.replace(second=0, microsecond=0)


def ms(dt):
    return int(dt.timestamp() * 1000)


def prepare_table(cur, symbols):
    print("Preparing market_candles for a clean 1m reload...", flush=True)

    cur.execute("DROP INDEX IF EXISTS idx_market_candles_symbol_time")
    cur.execute(f"DROP INDEX IF EXISTS {ONE_MINUTE_INDEX}")

    if symbols == PAIRS:
        cur.execute("DELETE FROM market_candles WHERE timeframe = %s", (INTERVAL,))
        print("Deleted existing 1m candles for all pairs.", flush=True)
    else:
        cur.execute(
            "DELETE FROM market_candles WHERE timeframe = %s AND symbol = ANY(%s)",
            (INTERVAL, symbols),
        )
        print(f"Deleted existing 1m candles for: {', '.join(symbols)}", flush=True)


def create_one_minute_index(cur):
    print("Creating 1m index...", flush=True)
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS {ONE_MINUTE_INDEX}
        ON market_candles (symbol, open_time)
        WHERE timeframe = '1m'
    """)


def fetch_klines(symbol, start_time):
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": LIMIT,
        "startTime": ms(start_time),
    }

    response = requests.get(BINANCE_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict):
        raise RuntimeError(f"Binance error for {symbol}: {data}")

    return data


def candle_rows(symbol, candles):
    return [
        (
            symbol,
            INTERVAL,
            datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc),
            candle[1],
            candle[2],
            candle[3],
            candle[4],
            candle[5],
            datetime.fromtimestamp(candle[6] / 1000, tz=timezone.utc),
        )
        for candle in candles
    ]


def save_candles(cur, symbol, candles):
    execute_values(
        cur,
        """
        INSERT INTO market_candles
        (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
        VALUES %s
        ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
        """,
        candle_rows(symbol, candles),
        page_size=1000,
    )


def download_symbol(conn, cur, symbol, start, end, sleep_seconds):
    current = start
    total_saved = 0

    print(f"{symbol}: downloading from {current} to {end}", flush=True)

    while current < end:
        candles = fetch_klines(symbol, current)

        if not candles:
            print(f"{symbol}: no more data at {current}", flush=True)
            break

        save_candles(cur, symbol, candles)
        conn.commit()

        total_saved += len(candles)
        last_open_time = candles[-1][0]
        current = datetime.fromtimestamp(last_open_time / 1000, tz=timezone.utc) + timedelta(minutes=1)

        print(f"{symbol}: saved batch {len(candles)}, total {total_saved}, next {current}", flush=True)
        time.sleep(sleep_seconds)

    print(f"{symbol}: done, saved about {total_saved} candles", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Delete existing 1m candles, redownload them, then create a 1m index."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="How many days of 1m history to download.",
    )
    parser.add_argument(
        "--symbol",
        choices=PAIRS,
        help="Reload one symbol only. Without this, reloads all pairs.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Seconds to sleep between Binance requests.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    symbols = [args.symbol] if args.symbol else PAIRS
    start = floor_to_minute(datetime.now(timezone.utc) - timedelta(days=args.days))
    end = floor_to_minute(datetime.now(timezone.utc))

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    try:
        prepare_table(cur, symbols)
        conn.commit()

        for symbol in symbols:
            download_symbol(conn, cur, symbol, start, end, args.sleep)

        create_one_minute_index(cur)
        conn.commit()
    finally:
        cur.close()
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
