import argparse
import os

import psycopg2


from db_config import DB_CONFIG as DB


def parse_args():
    parser = argparse.ArgumentParser(description="Delete all rows from market_candles.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation flag.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.yes:
        print("This deletes ALL rows from market_candles.")
        print("Run again with --yes to confirm:")
        print("python scripts/delete_market_candles.py --yes")
        return

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    cur.execute("DELETE FROM market_candles")
    deleted_rows = cur.rowcount
    conn.commit()

    cur.close()
    conn.close()

    print(f"Deleted {deleted_rows} rows from market_candles.")


if __name__ == "__main__":
    main()
