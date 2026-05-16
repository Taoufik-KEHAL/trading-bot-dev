import argparse
import os
from pathlib import Path

import psycopg2
import pandas as pd

from backtest_report import print_backtest_report

from db_config import DB_CONFIG as DB

PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"
]

TIMEFRAME = "5m"
TRADE_SIZE_USDT = 10
TAKE_PROFIT = 0.02
STOP_LOSS = 0.01
STRATEGY_VERSION = "V1"
STRATEGY_NAME = f"{STRATEGY_VERSION}_EMA50_EMA200_RSI35_TP2_SL1"


def setup_matplotlib():
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def load_market_data(cur, symbol):
    query = """
        SELECT open_time, open, high, low, close, volume
        FROM market_candles
        WHERE symbol = %s AND timeframe = %s
        ORDER BY open_time ASC
    """

    cur.execute(query, (symbol, TIMEFRAME))
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    df = pd.DataFrame(rows, columns=columns)

    if df.empty:
        return df

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df = compute_indicators(df)
    return df.dropna().reset_index(drop=True)


def compute_indicators(df):
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


def clear_previous_results(cur):
    cur.execute(
        "DELETE FROM backtest_trades WHERE strategy = %s",
        (STRATEGY_NAME,)
    )


def save_trade(cur, trade):
    cur.execute("""
        INSERT INTO backtest_trades
        (symbol, entry_time, exit_time, entry_price, exit_price,
         pnl_percent, pnl_usdt, result, strategy)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        trade["symbol"],
        trade["entry_time"],
        trade["exit_time"],
        trade["entry_price"],
        trade["exit_price"],
        trade["pnl_percent"],
        trade["pnl_usdt"],
        trade["result"],
        STRATEGY_NAME,
    ))


def backtest_pair(conn, cur, symbol, save_results=True):
    print(f"Backtesting {symbol}...")

    df = load_market_data(cur, symbol)

    if df.empty:
        print(f"No data for {symbol}")
        return []

    trades = []
    position = None

    for _, row in df.iterrows():
        price = row["close"]

        if position is None:
            buy_signal = (
                row["close"] > row["ema50"]
                and row["close"] > row["ema200"]
                and row["rsi"] < 35
            )

            if buy_signal:
                position = {
                    "symbol": symbol,
                    "entry_time": row["open_time"],
                    "entry_price": price,
                    "take_profit_price": price * (1 + TAKE_PROFIT),
                    "stop_loss_price": price * (1 - STOP_LOSS),
                }

        else:
            exit_price = None
            result = None

            if row["low"] <= position["stop_loss_price"]:
                exit_price = position["stop_loss_price"]
                result = "LOSS"

            elif row["high"] >= position["take_profit_price"]:
                exit_price = position["take_profit_price"]
                result = "WIN"

            if exit_price is not None:
                pnl_percent = (exit_price - position["entry_price"]) / position["entry_price"]
                pnl_usdt = TRADE_SIZE_USDT * pnl_percent

                trade = {
                    "symbol": symbol,
                    "entry_time": position["entry_time"],
                    "exit_time": row["open_time"],
                    "entry_price": position["entry_price"],
                    "exit_price": exit_price,
                    "pnl_percent": pnl_percent * 100,
                    "pnl_usdt": pnl_usdt,
                    "result": result,
                }

                trades.append(trade)

                if save_results:
                    save_trade(cur, trade)

                position = None

    if save_results:
        conn.commit()

    print(f"{symbol}: {len(trades)} trades")
    return trades


def plot_strategy(symbol, df, trades, output_dir, max_candles):
    if df.empty:
        return None

    plt = setup_matplotlib()

    output_dir.mkdir(parents=True, exist_ok=True)

    plot_df = df.tail(max_candles).copy()
    start_time = plot_df["open_time"].min()
    visible_trades = [
        trade for trade in trades
        if trade["entry_time"] >= start_time or trade["exit_time"] >= start_time
    ]

    fig, (price_ax, rsi_ax) = plt.subplots(
        2,
        1,
        figsize=(16, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    price_ax.plot(plot_df["open_time"], plot_df["close"], label="Close", color="#222222", linewidth=1)
    price_ax.plot(plot_df["open_time"], plot_df["ema50"], label="EMA50", color="#1f77b4", linewidth=1)
    price_ax.plot(plot_df["open_time"], plot_df["ema200"], label="EMA200", color="#ff7f0e", linewidth=1)

    for trade in visible_trades:
        entry_color = "#2ca02c"
        exit_color = "#2ca02c" if trade["result"] == "WIN" else "#d62728"

        price_ax.scatter(
            trade["entry_time"],
            trade["entry_price"],
            color=entry_color,
            marker="^",
            s=70,
            zorder=5,
            label="Entry" if "Entry" not in price_ax.get_legend_handles_labels()[1] else None,
        )
        price_ax.scatter(
            trade["exit_time"],
            trade["exit_price"],
            color=exit_color,
            marker="v",
            s=70,
            zorder=5,
            label="Exit" if "Exit" not in price_ax.get_legend_handles_labels()[1] else None,
        )
        price_ax.plot(
            [trade["entry_time"], trade["exit_time"]],
            [trade["entry_price"], trade["exit_price"]],
            color=exit_color,
            linewidth=1,
            alpha=0.7,
        )

    rsi_ax.plot(plot_df["open_time"], plot_df["rsi"], label="RSI", color="#9467bd", linewidth=1)
    rsi_ax.axhline(35, color="#d62728", linestyle="--", linewidth=1, label="Buy threshold RSI 35")
    rsi_ax.axhline(70, color="#888888", linestyle=":", linewidth=1)
    rsi_ax.set_ylim(0, 100)

    price_ax.set_title(f"{symbol} {TIMEFRAME} - {STRATEGY_NAME}")
    price_ax.set_ylabel("Price")
    rsi_ax.set_ylabel("RSI")
    rsi_ax.set_xlabel("Time")

    price_ax.grid(True, alpha=0.25)
    rsi_ax.grid(True, alpha=0.25)
    price_ax.legend(loc="upper left")
    rsi_ax.legend(loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()

    output_path = output_dir / f"{symbol}_{TIMEFRAME}_{STRATEGY_NAME}.png"
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def find_trade_window(df, trade, candles_before, candles_after):
    entry_matches = df.index[df["open_time"] == trade["entry_time"]].tolist()
    exit_matches = df.index[df["open_time"] == trade["exit_time"]].tolist()

    if not entry_matches or not exit_matches:
        return pd.DataFrame()

    entry_idx = entry_matches[0]
    exit_idx = exit_matches[0]
    start_idx = max(0, min(entry_idx, exit_idx) - candles_before)
    end_idx = min(len(df), max(entry_idx, exit_idx) + candles_after + 1)

    return df.iloc[start_idx:end_idx].copy()


def plot_trade(symbol, df, trade, trade_number, output_dir, candles_before, candles_after):
    trade_df = find_trade_window(df, trade, candles_before, candles_after)

    if trade_df.empty:
        return None

    plt = setup_matplotlib()

    symbol_dir = output_dir / "trades" / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)

    exit_color = "#2ca02c" if trade["result"] == "WIN" else "#d62728"
    take_profit_price = trade["entry_price"] * (1 + TAKE_PROFIT)
    stop_loss_price = trade["entry_price"] * (1 - STOP_LOSS)

    fig, (price_ax, rsi_ax) = plt.subplots(
        2,
        1,
        figsize=(14, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    price_ax.plot(trade_df["open_time"], trade_df["close"], label="Close", color="#222222", linewidth=1)
    price_ax.plot(trade_df["open_time"], trade_df["ema50"], label="EMA50", color="#1f77b4", linewidth=1)
    price_ax.plot(trade_df["open_time"], trade_df["ema200"], label="EMA200", color="#ff7f0e", linewidth=1)

    price_ax.axhline(take_profit_price, color="#2ca02c", linestyle="--", linewidth=1, label="Take profit +2%")
    price_ax.axhline(stop_loss_price, color="#d62728", linestyle="--", linewidth=1, label="Stop loss -1%")
    price_ax.axvspan(trade["entry_time"], trade["exit_time"], color=exit_color, alpha=0.08)

    price_ax.scatter(
        trade["entry_time"],
        trade["entry_price"],
        color="#2ca02c",
        marker="^",
        s=95,
        zorder=5,
        label="Entry",
    )
    price_ax.scatter(
        trade["exit_time"],
        trade["exit_price"],
        color=exit_color,
        marker="v",
        s=95,
        zorder=5,
        label=f"Exit {trade['result']}",
    )
    price_ax.plot(
        [trade["entry_time"], trade["exit_time"]],
        [trade["entry_price"], trade["exit_price"]],
        color=exit_color,
        linewidth=1.5,
        alpha=0.8,
    )

    rsi_ax.plot(trade_df["open_time"], trade_df["rsi"], label="RSI", color="#9467bd", linewidth=1)
    rsi_ax.axhline(35, color="#d62728", linestyle="--", linewidth=1, label="Buy threshold RSI 35")
    rsi_ax.axhline(70, color="#888888", linestyle=":", linewidth=1)
    rsi_ax.set_ylim(0, 100)

    price_ax.set_title(
        f"{symbol} trade {trade_number:04d} - {trade['result']} "
        f"{trade['pnl_percent']:.2f}% / {trade['pnl_usdt']:.4f} USDT"
    )
    price_ax.set_ylabel("Price")
    rsi_ax.set_ylabel("RSI")
    rsi_ax.set_xlabel("Time")

    price_ax.grid(True, alpha=0.25)
    rsi_ax.grid(True, alpha=0.25)
    price_ax.legend(loc="upper left")
    rsi_ax.legend(loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()

    entry_time = trade["entry_time"].strftime("%Y%m%d_%H%M%S")
    output_path = symbol_dir / f"trade_{trade_number:04d}_{entry_time}_{trade['result']}.png"
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def plot_all_trades(trades_by_symbol, market_data_by_symbol, output_dir, candles_before, candles_after):
    output_paths = []

    for symbol, trades in trades_by_symbol.items():
        df = market_data_by_symbol.get(symbol)

        if df is None or df.empty:
            continue

        for trade_number, trade in enumerate(trades, start=1):
            output_path = plot_trade(
                symbol,
                df,
                trade,
                trade_number,
                output_dir,
                candles_before,
                candles_after,
            )

            if output_path:
                output_paths.append(output_path)

    return output_paths


def print_summary(all_trades):
    print_backtest_report(
        all_trades,
        STRATEGY_NAME,
        TRADE_SIZE_USDT,
        strategy_timeframe=TIMEFRAME,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest EMA/RSI strategy and optionally create charts.")
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Create strategy charts after the backtest.",
    )
    parser.add_argument(
        "--plot-trades",
        action="store_true",
        help="Create one zoomed chart for every completed trade.",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Create charts without deleting or inserting backtest results.",
    )
    parser.add_argument(
        "--plot-symbol",
        choices=PAIRS,
        help="Create a chart for one symbol only. Use with --plot.",
    )
    parser.add_argument(
        "--chart-dir",
        default="charts",
        help="Directory where chart PNG files are saved.",
    )
    parser.add_argument(
        "--max-candles",
        type=int,
        default=1500,
        help="How many recent candles to show in each chart.",
    )
    parser.add_argument(
        "--trade-candles-before",
        type=int,
        default=80,
        help="Candles to show before each trade entry on per-trade charts.",
    )
    parser.add_argument(
        "--trade-candles-after",
        type=int,
        default=40,
        help="Candles to show after each trade exit on per-trade charts.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    should_save_results = not args.plot_only
    should_plot = args.plot or (args.plot_only and not args.plot_trades)
    should_plot_trades = args.plot_trades

    try:
        conn = psycopg2.connect(**DB)
    except psycopg2.OperationalError as exc:
        print("Could not connect to PostgreSQL.")
        print(f"Host: {DB['host']}  Port: {DB['port']}  Database: {DB['dbname']}  User: {DB['user']}")
        error_message = str(exc).strip() or repr(exc)
        print(f"Error: {error_message}")
        return

    cur = conn.cursor()

    if should_save_results:
        clear_previous_results(cur)
        conn.commit()

    all_trades = []

    for symbol in PAIRS:
        trades = backtest_pair(conn, cur, symbol, save_results=should_save_results)
        all_trades.extend(trades)

    print_summary(all_trades)

    if should_plot or should_plot_trades:
        output_dir = Path(args.chart_dir)
        symbols_to_plot = [args.plot_symbol] if args.plot_symbol else PAIRS
        trades_by_symbol = {}
        market_data_by_symbol = {}

        for trade in all_trades:
            trades_by_symbol.setdefault(trade["symbol"], []).append(trade)

        for symbol in symbols_to_plot:
            market_data_by_symbol[symbol] = load_market_data(cur, symbol)

    if should_plot:
        print("\n===== CHARTS =====")
        for symbol in symbols_to_plot:
            output_path = plot_strategy(
                symbol,
                market_data_by_symbol[symbol],
                trades_by_symbol.get(symbol, []),
                output_dir,
                args.max_candles,
            )
            if output_path:
                print(f"{symbol}: {output_path}")
            else:
                print(f"{symbol}: no data to plot")

    if should_plot_trades:
        selected_trades = {
            symbol: trades_by_symbol.get(symbol, [])
            for symbol in symbols_to_plot
        }

        trade_chart_paths = plot_all_trades(
            selected_trades,
            market_data_by_symbol,
            output_dir,
            args.trade_candles_before,
            args.trade_candles_after,
        )

        print("\n===== TRADE CHARTS =====")
        print(f"Generated {len(trade_chart_paths)} trade charts in {output_dir / 'trades'}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
