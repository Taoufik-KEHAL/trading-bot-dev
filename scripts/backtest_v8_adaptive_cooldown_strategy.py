import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2

from backtest_report import print_backtest_report


from db_config import DB_CONFIG as DB

PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"
]

SOURCE_TIMEFRAME = "5m"
STRATEGY_TIMEFRAME = "15min"
TRADE_SIZE_USDT = 10
FEE_RATE = 0.001
STRATEGY_NAME = "V8_SHORT_EDGE_ADAPTIVE_COOLDOWN"

EMA_FAST = 21
EMA_MID = 50
EMA_TREND = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_PERIOD = 20

BREAKOUT_LOOKBACK = 20
RECENT_BREAKOUT_CANDLES = 12
SWING_LOOKBACK = 8
EMA_MID_SLOPE_LOOKBACK = 8
EMA_TREND_SLOPE_LOOKBACK = 16

MIN_ATR_PERCENT = 0.004
VOLUME_MULTIPLIER = 1.2
ATR_STOP_MULTIPLIER = 1.5
BREAKEVEN_TRIGGER_R = 1.0
TRAIL_TRIGGER_R = 2.0
ATR_TRAIL_MULTIPLIER = 1.2
HARD_TARGET_R = 3.0
MAX_HOLDING_CANDLES = 48
LOSS_COOLDOWN_AFTER_2 = 16
LOSS_COOLDOWN_AFTER_3 = 48
LOSS_COOLDOWN_AFTER_4 = 96
ENABLE_LONGS = False

LONG_RSI_RECOVERY = 50
LONG_RSI_MAX_ENTRY = 65
SHORT_RSI_RECOVERY = 50
SHORT_RSI_MIN_ENTRY = 35


def setup_matplotlib():
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def load_raw_market_data(cur, symbol):
    query = """
        SELECT open_time, open, high, low, close, volume
        FROM market_candles
        WHERE symbol = %s AND timeframe = %s
        ORDER BY open_time ASC
    """

    cur.execute(query, (symbol, SOURCE_TIMEFRAME))
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    df = pd.DataFrame(rows, columns=columns)

    if df.empty:
        return df

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"])
    return df


def resample_to_strategy_timeframe(df):
    return (
        df.set_index("open_time")
        .resample(STRATEGY_TIMEFRAME)
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        .dropna()
        .reset_index()
    )


def load_market_data(cur, symbol):
    df = load_raw_market_data(cur, symbol)

    if df.empty:
        return df

    df = resample_to_strategy_timeframe(df)
    df = compute_indicators(df)
    return df.dropna().reset_index(drop=True)


def compute_rsi(close):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def compute_atr(df):
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()


def compute_indicators(df):
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["rsi"] = compute_rsi(df["close"])
    df["atr"] = compute_atr(df)
    df["atr_percent"] = df["atr"] / df["close"]
    df["volume_sma"] = df["volume"].rolling(VOLUME_PERIOD).mean()
    df["ema_mid_slope"] = df["ema_mid"] - df["ema_mid"].shift(EMA_MID_SLOPE_LOOKBACK)
    df["ema_trend_slope"] = df["ema_trend"] - df["ema_trend"].shift(EMA_TREND_SLOPE_LOOKBACK)

    df["prior_high"] = df["high"].shift(1).rolling(BREAKOUT_LOOKBACK).max()
    df["prior_low"] = df["low"].shift(1).rolling(BREAKOUT_LOOKBACK).min()
    df["long_breakout"] = df["close"] > df["prior_high"]
    df["short_breakout"] = df["close"] < df["prior_low"]
    df["recent_long_breakout"] = (
        df["long_breakout"].rolling(RECENT_BREAKOUT_CANDLES).max().fillna(False).astype(bool)
    )
    df["recent_short_breakout"] = (
        df["short_breakout"].rolling(RECENT_BREAKOUT_CANDLES).max().fillna(False).astype(bool)
    )
    return df


def add_btc_regime(df, btc_df):
    regime = btc_df[[
        "open_time", "close", "ema_mid", "ema_trend", "ema_mid_slope", "ema_trend_slope"
    ]].rename(columns={
        "close": "btc_close",
        "ema_mid": "btc_ema_mid",
        "ema_trend": "btc_ema_trend",
        "ema_mid_slope": "btc_ema_mid_slope",
        "ema_trend_slope": "btc_ema_trend_slope",
    })

    df = df.merge(regime, on="open_time", how="left")
    df["btc_bullish"] = (
        (df["btc_close"] > df["btc_ema_trend"])
        & (df["btc_ema_mid"] > df["btc_ema_trend"])
        & (df["btc_ema_mid_slope"] > 0)
        & (df["btc_ema_trend_slope"] > 0)
    )
    df["btc_bearish"] = (
        (df["btc_close"] < df["btc_ema_trend"])
        & (df["btc_ema_mid"] < df["btc_ema_trend"])
        & (df["btc_ema_mid_slope"] < 0)
        & (df["btc_ema_trend_slope"] < 0)
    )
    return df.dropna().reset_index(drop=True)


def clear_previous_results(cur):
    cur.execute(
        "DELETE FROM backtest_trades WHERE strategy = %s",
        (STRATEGY_NAME,),
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


def is_long_entry(row, previous_row):
    trend = row["close"] > row["ema_trend"] and row["ema_fast"] > row["ema_mid"] > row["ema_trend"]
    slope = row["ema_mid_slope"] > 0 and row["ema_trend_slope"] > 0
    pullback_recovered = previous_row["close"] <= previous_row["ema_fast"] and row["close"] > row["ema_fast"]
    rsi_recovered = previous_row["rsi"] < LONG_RSI_RECOVERY <= row["rsi"] <= LONG_RSI_MAX_ENTRY
    volume = row["volume"] >= row["volume_sma"] * VOLUME_MULTIPLIER
    volatility = row["atr_percent"] >= MIN_ATR_PERCENT

    return (
        row["btc_bullish"]
        and row["recent_long_breakout"]
        and trend
        and slope
        and pullback_recovered
        and rsi_recovered
        and volume
        and volatility
        and row["close"] > row["open"]
    )


def is_short_entry(row, previous_row):
    trend = row["close"] < row["ema_trend"] and row["ema_fast"] < row["ema_mid"] < row["ema_trend"]
    slope = row["ema_mid_slope"] < 0 and row["ema_trend_slope"] < 0
    pullback_rejected = previous_row["close"] >= previous_row["ema_fast"] and row["close"] < row["ema_fast"]
    rsi_rejected = previous_row["rsi"] > SHORT_RSI_RECOVERY >= row["rsi"] >= SHORT_RSI_MIN_ENTRY
    volume = row["volume"] >= row["volume_sma"] * VOLUME_MULTIPLIER
    volatility = row["atr_percent"] >= MIN_ATR_PERCENT

    return (
        row["btc_bearish"]
        and row["recent_short_breakout"]
        and trend
        and slope
        and pullback_rejected
        and rsi_rejected
        and volume
        and volatility
        and row["close"] < row["open"]
    )


def build_position(symbol, df, index, row, side):
    if side == "LONG":
        swing_stop = df.loc[index - SWING_LOOKBACK:index, "low"].min()
        atr_stop = row["close"] - (row["atr"] * ATR_STOP_MULTIPLIER)
        stop_price = min(swing_stop, atr_stop)
        risk_per_unit = row["close"] - stop_price
        hard_target_price = row["close"] + (risk_per_unit * HARD_TARGET_R)
    else:
        swing_stop = df.loc[index - SWING_LOOKBACK:index, "high"].max()
        atr_stop = row["close"] + (row["atr"] * ATR_STOP_MULTIPLIER)
        stop_price = max(swing_stop, atr_stop)
        risk_per_unit = stop_price - row["close"]
        hard_target_price = row["close"] - (risk_per_unit * HARD_TARGET_R)

    if risk_per_unit <= 0:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry_index": index,
        "entry_time": row["open_time"],
        "entry_price": row["close"],
        "stop_price": stop_price,
        "initial_stop_price": stop_price,
        "risk_per_unit": risk_per_unit,
        "hard_target_price": hard_target_price,
        "breakeven_active": False,
        "trailing_active": False,
    }


def current_r_multiple(position, row):
    if position["side"] == "LONG":
        return (row["high"] - position["entry_price"]) / position["risk_per_unit"]

    return (position["entry_price"] - row["low"]) / position["risk_per_unit"]


def update_dynamic_stop(position, row):
    r_multiple = current_r_multiple(position, row)

    if r_multiple >= BREAKEVEN_TRIGGER_R:
        position["breakeven_active"] = True

    if r_multiple >= TRAIL_TRIGGER_R:
        position["trailing_active"] = True

    if position["side"] == "LONG":
        if position["breakeven_active"]:
            position["stop_price"] = max(position["stop_price"], position["entry_price"])
        if position["trailing_active"]:
            atr_trail = row["close"] - (row["atr"] * ATR_TRAIL_MULTIPLIER)
            ema_trail = row["ema_fast"]
            position["stop_price"] = max(position["stop_price"], atr_trail, ema_trail)
    else:
        if position["breakeven_active"]:
            position["stop_price"] = min(position["stop_price"], position["entry_price"])
        if position["trailing_active"]:
            atr_trail = row["close"] + (row["atr"] * ATR_TRAIL_MULTIPLIER)
            ema_trail = row["ema_fast"]
            position["stop_price"] = min(position["stop_price"], atr_trail, ema_trail)


def close_position(position, row, exit_price, exit_reason):
    if position["side"] == "LONG":
        gross_pnl_percent = (exit_price - position["entry_price"]) / position["entry_price"]
    else:
        gross_pnl_percent = (position["entry_price"] - exit_price) / position["entry_price"]

    net_pnl_percent = gross_pnl_percent - (FEE_RATE * 2)

    return {
        "symbol": position["symbol"],
        "side": position["side"],
        "entry_time": position["entry_time"],
        "exit_time": row["open_time"],
        "entry_price": position["entry_price"],
        "exit_price": exit_price,
        "pnl_percent": net_pnl_percent * 100,
        "gross_pnl_percent": gross_pnl_percent * 100,
        "pnl_usdt": TRADE_SIZE_USDT * net_pnl_percent,
        "result": "WIN" if net_pnl_percent > 0 else "LOSS",
        "exit_reason": exit_reason,
        "stop_price": position["initial_stop_price"],
        "final_stop_price": position["stop_price"],
        "hard_target_price": position["hard_target_price"],
    }


def cooldown_for_losing_streak(losing_streak):
    if losing_streak >= 4:
        return LOSS_COOLDOWN_AFTER_4
    if losing_streak == 3:
        return LOSS_COOLDOWN_AFTER_3
    if losing_streak == 2:
        return LOSS_COOLDOWN_AFTER_2
    return 0


def backtest_pair(conn, cur, symbol, df, save_results=True):
    print(f"Backtesting {symbol}...")

    if df.empty:
        print(f"No data for {symbol}")
        return []

    trades = []
    position = None
    cooldown_until_index = -1
    losing_streak = 0
    minimum_index = max(SWING_LOOKBACK, EMA_TREND_SLOPE_LOOKBACK, BREAKOUT_LOOKBACK)

    for index in range(1, len(df)):
        row = df.iloc[index]
        previous_row = df.iloc[index - 1]

        if position is None:
            if index < minimum_index or index <= cooldown_until_index:
                continue

            if ENABLE_LONGS and is_long_entry(row, previous_row):
                position = build_position(symbol, df, index, row, "LONG")
            elif is_short_entry(row, previous_row):
                position = build_position(symbol, df, index, row, "SHORT")

            continue

        update_dynamic_stop(position, row)

        exit_price = None
        exit_reason = None
        holding_candles = index - position["entry_index"]

        if position["side"] == "LONG":
            if row["low"] <= position["stop_price"]:
                exit_price = position["stop_price"]
                exit_reason = "DYNAMIC_STOP" if position["trailing_active"] else "STOP_LOSS"
            elif row["high"] >= position["hard_target_price"]:
                exit_price = position["hard_target_price"]
                exit_reason = "HARD_TARGET"
            elif row["close"] < row["ema_mid"] and previous_row["close"] < previous_row["ema_mid"]:
                exit_price = row["close"]
                exit_reason = "TREND_FAIL"
        else:
            if row["high"] >= position["stop_price"]:
                exit_price = position["stop_price"]
                exit_reason = "DYNAMIC_STOP" if position["trailing_active"] else "STOP_LOSS"
            elif row["low"] <= position["hard_target_price"]:
                exit_price = position["hard_target_price"]
                exit_reason = "HARD_TARGET"

        if exit_price is None and holding_candles >= MAX_HOLDING_CANDLES:
            exit_price = row["close"]
            exit_reason = "TIME_EXIT"

        if exit_price is None:
            continue

        trade = close_position(position, row, exit_price, exit_reason)
        trades.append(trade)

        if trade["result"] == "LOSS":
            losing_streak += 1
            cooldown_until_index = index + cooldown_for_losing_streak(losing_streak)
        else:
            losing_streak = 0

        if save_results:
            save_trade(cur, trade)

        position = None

    if save_results:
        conn.commit()

    print(f"{symbol}: {len(trades)} trades")
    return trades


def plot_trade(symbol, df, trade, trade_number, output_dir, candles_before, candles_after):
    entry_matches = df.index[df["open_time"] == trade["entry_time"]].tolist()
    exit_matches = df.index[df["open_time"] == trade["exit_time"]].tolist()

    if not entry_matches or not exit_matches:
        return None

    entry_idx = entry_matches[0]
    exit_idx = exit_matches[0]
    start_idx = max(0, entry_idx - candles_before)
    end_idx = min(len(df), exit_idx + candles_after + 1)
    trade_df = df.iloc[start_idx:end_idx].copy()

    plt = setup_matplotlib()
    symbol_dir = output_dir / "trades" / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)

    exit_color = "#2ca02c" if trade["result"] == "WIN" else "#d62728"
    entry_marker = "^" if trade["side"] == "LONG" else "v"
    exit_marker = "v" if trade["side"] == "LONG" else "^"

    fig, (price_ax, rsi_ax) = plt.subplots(
        2,
        1,
        figsize=(14, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    price_ax.plot(trade_df["open_time"], trade_df["close"], label="Close", color="#222222", linewidth=1)
    price_ax.plot(trade_df["open_time"], trade_df["ema_fast"], label=f"EMA{EMA_FAST}", color="#1f77b4", linewidth=1)
    price_ax.plot(trade_df["open_time"], trade_df["ema_mid"], label=f"EMA{EMA_MID}", color="#ff7f0e", linewidth=1)
    price_ax.plot(trade_df["open_time"], trade_df["ema_trend"], label=f"EMA{EMA_TREND}", color="#7f7f7f", linewidth=1)
    price_ax.axhline(trade["stop_price"], color="#d62728", linestyle="--", linewidth=1, label="Initial stop")
    price_ax.axhline(trade["hard_target_price"], color="#2ca02c", linestyle="--", linewidth=1, label="Hard target")
    price_ax.axhline(trade["final_stop_price"], color="#9467bd", linestyle=":", linewidth=1, label="Final stop")
    price_ax.axvspan(trade["entry_time"], trade["exit_time"], color=exit_color, alpha=0.08)
    price_ax.scatter(trade["entry_time"], trade["entry_price"], color="#2ca02c", marker=entry_marker, s=95, zorder=5, label=f"{trade['side']} entry")
    price_ax.scatter(trade["exit_time"], trade["exit_price"], color=exit_color, marker=exit_marker, s=95, zorder=5, label=trade["exit_reason"])

    rsi_ax.plot(trade_df["open_time"], trade_df["rsi"], label="RSI", color="#9467bd", linewidth=1)
    rsi_ax.axhline(50, color="#d62728", linestyle="--", linewidth=1, label="RSI 50")
    rsi_ax.set_ylim(0, 100)

    price_ax.set_title(
        f"{symbol} V8 {trade['side']} trade {trade_number:04d} - {trade['result']} "
        f"net {trade['pnl_percent']:.2f}% / gross {trade['gross_pnl_percent']:.2f}%"
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
    output_path = symbol_dir / f"trade_{trade_number:04d}_{entry_time}_{trade['side']}_{trade['result']}.png"
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def print_summary(all_trades):
    print_backtest_report(
        all_trades,
        STRATEGY_NAME,
        TRADE_SIZE_USDT,
        source_timeframe=SOURCE_TIMEFRAME,
        strategy_timeframe=STRATEGY_TIMEFRAME,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest V8 short-edge strategy with adaptive cooldown.")
    parser.add_argument(
        "--plot-trades",
        action="store_true",
        help="Create one zoomed chart for every completed trade.",
    )
    parser.add_argument(
        "--plot-symbol",
        choices=PAIRS,
        help="Only run and plot one symbol.",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Run the backtest and charts without writing to backtest_trades.",
    )
    parser.add_argument(
        "--chart-dir",
        default="charts_v8_adaptive_cooldown",
        help="Directory where chart PNG files are saved.",
    )
    parser.add_argument(
        "--trade-candles-before",
        type=int,
        default=48,
        help="Candles to show before each trade entry on per-trade charts.",
    )
    parser.add_argument(
        "--trade-candles-after",
        type=int,
        default=24,
        help="Candles to show after each trade exit on per-trade charts.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    should_save_results = not args.plot_only
    symbols = [args.plot_symbol] if args.plot_symbol else PAIRS

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

    btc_df = load_market_data(cur, "BTCUSDT")

    all_trades = []
    market_data_by_symbol = {}
    trades_by_symbol = {}

    for symbol in symbols:
        symbol_df = load_market_data(cur, symbol)
        symbol_df = add_btc_regime(symbol_df, btc_df)
        trades = backtest_pair(conn, cur, symbol, symbol_df, save_results=should_save_results)
        all_trades.extend(trades)
        trades_by_symbol[symbol] = trades
        market_data_by_symbol[symbol] = symbol_df

    print_summary(all_trades)

    if args.plot_trades:
        output_dir = Path(args.chart_dir)
        chart_count = 0

        for symbol, trades in trades_by_symbol.items():
            df = market_data_by_symbol[symbol]

            for trade_number, trade in enumerate(trades, start=1):
                output_path = plot_trade(
                    symbol,
                    df,
                    trade,
                    trade_number,
                    output_dir,
                    args.trade_candles_before,
                    args.trade_candles_after,
                )

                if output_path:
                    chart_count += 1

        print("\n===== TRADE CHARTS =====")
        print(f"Generated {chart_count} trade charts in {output_dir / 'trades'}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
