import os

import psycopg2


from db_config import DB_CONFIG as DB


def main():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    cur.execute("""
        DROP INDEX IF EXISTS idx_market_candles_symbol_time
    """)
    conn.commit()

    cur.close()
    conn.close()

    print("Dropped obsolete unique index idx_market_candles_symbol_time.")
    print("market_candles can now store multiple timeframes for the same symbol/open_time.")


if __name__ == "__main__":
    main()
