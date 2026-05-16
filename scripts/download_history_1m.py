import argparse
import os
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import requests


PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"
]

INTERVAL = "1m"
LIMIT = 1000
BINANCE_URL = "https://api.binance.com/api/v3/klines"

from db_config import DB_CONFIG as DB


def ms(dt):
    return int(dt.timestamp() * 1000)


def get_resume_start(cur, symbol, default_start):
    cur.execute(
        """
        SELECT MAX(open_time)
        FROM market_candles
        WHERE symbol = %s AND timeframe = %s
        """,
        (symbol, INTERVAL),
    )
    latest_open_time = cur.fetchone()[0]

    if latest_open_time is None:
        return default_start

    if latest_open_time.tzinfo is None:
        latest_open_time = latest_open_time.replace(tzinfo=timezone.utc)

    return latest_open_time + timedelta(minutes=1)


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


def save_candles(cur, symbol, candles):
    for candle in candles:
        cur.execute(
            """
            INSERT INTO market_candles
            (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
            """,
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
            ),
        )


def download_symbol(conn, cur, symbol, default_start, end, sleep_seconds):
    current = get_resume_start(cur, symbol, default_start)

    if current >= end:
        print(f"{symbol}: already up to date")
        return

    print(f"{symbol}: downloading from {current} to {end}", flush=True)

    while current < end:
        candles = fetch_klines(symbol, current)

        if not candles:
            print(f"{symbol}: no more data at {current}", flush=True)
            break

        save_candles(cur, symbol, candles)
        conn.commit()

        last_open_time = candles[-1][0]
        current = datetime.fromtimestamp(last_open_time / 1000, tz=timezone.utc) + timedelta(minutes=1)

        print(f"{symbol}: saved {len(candles)} candles, next {current}", flush=True)
        time.sleep(sleep_seconds)


def parse_args():
    parser = argparse.ArgumentParser(description="Download Binance 1m candles for the past year.")
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="How many days of 1m history to download.",
    )
    parser.add_argument(
        "--symbol",
        choices=PAIRS,
        help="Download one symbol only.",
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
    start = datetime.now(timezone.utc) - timedelta(days=args.days)
    end = datetime.now(timezone.utc)

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    try:
        for symbol in symbols:
            download_symbol(conn, cur, symbol, start, end, args.sleep)
    finally:
        cur.close()
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
