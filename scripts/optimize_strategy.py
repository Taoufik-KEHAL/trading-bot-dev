import argparse
import contextlib
import io
from itertools import product
from pathlib import Path

import pandas as pd
import psycopg2

import backtest_v11_multi_regime_short_only as strategy


TIMEFRAMES = ["15min", "30min", "1h"]

QUICK_GRID = {
    "bearish_close_count": [6],
    "bearish_trend_count": [5],
    "short_volume_multiplier": [1.10, 1.15],
    "min_atr_percent": [0.004],
    "trail_trigger_r": [2.0],
    "short_hard_target_r": [3.0],
}

FULL_GRID = {
    "bearish_close_count": [5, 6, 7],
    "bearish_trend_count": [4, 5, 6],
    "short_volume_multiplier": [1.05, 1.10, 1.15, 1.25],
    "min_atr_percent": [0.003, 0.004, 0.005],
    "trail_trigger_r": [1.5, 2.0, 2.5],
    "short_hard_target_r": [2.5, 3.0, 3.5],
}


def profit_factor(pnl):
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = pnl[pnl < 0].sum()

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0

    return gross_profit / abs(gross_loss)


def max_drawdown(pnl):
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    peak = equity.cummax()
    return (equity - peak).min()


def max_consecutive_losses(results):
    max_losses = 0
    current_losses = 0

    for result in results:
        if result == "LOSS":
            current_losses += 1
            max_losses = max(max_losses, current_losses)
        else:
            current_losses = 0

    return max_losses


def monthly_consistency(df):
    if df.empty:
        return 0.0

    monthly = df.assign(month=df["exit_time"].dt.to_period("M")).groupby("month")["pnl_usdt"].sum()
    return (monthly > 0).mean() * 100


def set_strategy_params(timeframe, params):
    strategy.STRATEGY_TIMEFRAME = timeframe
    strategy.BEARISH_CLOSE_COUNT = params["bearish_close_count"]
    strategy.BEARISH_TREND_COUNT = params["bearish_trend_count"]
    strategy.SHORT_VOLUME_MULTIPLIER = params["short_volume_multiplier"]
    strategy.MIN_ATR_PERCENT = params["min_atr_percent"]
    strategy.TRAIL_TRIGGER_R = params["trail_trigger_r"]
    strategy.SHORT_HARD_TARGET_R = params["short_hard_target_r"]
    strategy.ENABLE_LONGS = False
    strategy.ENABLE_SHORTS = True


def get_market_data(cur, symbols, timeframe, cache):
    if timeframe not in cache:
        strategy.STRATEGY_TIMEFRAME = timeframe
        cache[timeframe] = strategy.load_all_market_data(cur, symbols)

    return cache[timeframe]


def run_candidate(cur, symbols, timeframe, params, market_data_cache):
    set_strategy_params(timeframe, params)

    market_data_by_symbol = get_market_data(cur, strategy.PAIRS, timeframe, market_data_cache)
    market_regime = strategy.build_market_regime(market_data_by_symbol)

    all_trades = []

    for symbol in symbols:
        symbol_df = strategy.add_market_regime(market_data_by_symbol[symbol], market_regime)

        with contextlib.redirect_stdout(io.StringIO()):
            trades = strategy.backtest_pair(None, cur, symbol, symbol_df, save_results=False)

        all_trades.extend(trades)

    if not all_trades:
        return {
            **params,
            "timeframe": timeframe,
            "trades": 0,
            "net_pnl_usdt": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "max_consec_losses": 0,
            "positive_months_pct": 0.0,
            "avg_net_pnl": 0.0,
            "score": -999.0,
        }

    df = pd.DataFrame(all_trades).sort_values("exit_time").reset_index(drop=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    pnl = df["pnl_usdt"]
    pf = profit_factor(pnl)
    net_pnl = pnl.sum()
    drawdown = max_drawdown(pnl)
    positive_months_pct = monthly_consistency(df)
    win_rate = (df["result"] == "WIN").mean() * 100
    max_losses = max_consecutive_losses(df["result"])

    score = (
        net_pnl
        + min(pf, 5.0)
        + (positive_months_pct / 25)
        + drawdown
        - (max_losses * 0.05)
    )

    return {
        **params,
        "timeframe": timeframe,
        "trades": len(df),
        "net_pnl_usdt": net_pnl,
        "profit_factor": pf,
        "win_rate": win_rate,
        "max_drawdown": drawdown,
        "max_consec_losses": max_losses,
        "positive_months_pct": positive_months_pct,
        "avg_net_pnl": pnl.mean(),
        "score": score,
    }


def iter_grid(grid):
    keys = list(grid.keys())

    for values in product(*(grid[key] for key in keys)):
        yield dict(zip(keys, values))


def parse_args():
    parser = argparse.ArgumentParser(description="Optimize V11-style short-only market-regime parameters.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run the larger parameter grid.",
    )
    parser.add_argument(
        "--timeframe",
        choices=TIMEFRAMES,
        action="append",
        help="Timeframe to test. Can be passed more than once. Defaults to all.",
    )
    parser.add_argument(
        "--plot-symbol",
        choices=strategy.PAIRS,
        help="Optimize only one symbol for faster experiments.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of ranked rows to print.",
    )
    parser.add_argument(
        "--output",
        default="reports/strategy_optimization.csv",
        help="CSV path for all optimization results.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of parameter combinations per timeframe.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    grid = FULL_GRID if args.full else QUICK_GRID
    timeframes = args.timeframe or TIMEFRAMES
    symbols = [args.plot_symbol] if args.plot_symbol else strategy.PAIRS
    candidates = list(iter_grid(grid))

    if args.limit:
        candidates = candidates[:args.limit]

    total_runs = len(candidates) * len(timeframes)

    conn = psycopg2.connect(**strategy.DB)
    cur = conn.cursor()
    market_data_cache = {}
    results = []
    run_number = 0

    print(f"Optimizing {total_runs} candidates across {len(symbols)} symbol(s)...", flush=True)

    for timeframe in timeframes:
        for params in candidates:
            run_number += 1
            result = run_candidate(cur, symbols, timeframe, params, market_data_cache)
            results.append(result)

            print(
                f"[{run_number}/{total_runs}] {timeframe} "
                f"net={result['net_pnl_usdt']:.4f} "
                f"pf={result['profit_factor']:.2f} "
                f"dd={result['max_drawdown']:.4f} "
                f"score={result['score']:.2f}",
                flush=True,
            )

    cur.close()
    conn.close()

    results_df = pd.DataFrame(results).sort_values(
        ["score", "profit_factor", "net_pnl_usdt"],
        ascending=False,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results_df.to_csv(output_path, index=False)

    print("\n===== TOP RESULTS =====")
    print(results_df.head(args.top).to_string(index=False))
    print(f"\nSaved all results to {output_path}")


if __name__ == "__main__":
    main()
