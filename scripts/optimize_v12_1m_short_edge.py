import argparse
import contextlib
import io
from itertools import product
from pathlib import Path

import pandas as pd
import psycopg2

import backtest_v12_100usdt_aggressive_short as strategy
from backtest_report import max_consecutive_losses, max_drawdown, profit_factor


TIMEFRAMES = ["15min", "30min", "1h"]

QUICK_GRID = {
    "short_volume_multiplier": [1.15, 1.25],
    "min_atr_percent": [0.004],
    "short_rsi_min_entry": [35],
    "short_rsi_recovery": [50],
    "trail_trigger_r": [2.0],
    "short_hard_target_r": [3.0, 3.5],
}

FULL_GRID = {
    "bearish_close_count": [5, 6, 7],
    "bearish_trend_count": [4, 5, 6],
    "short_volume_multiplier": [1.10, 1.15, 1.25, 1.35],
    "min_atr_percent": [0.0035, 0.004, 0.005, 0.006],
    "short_rsi_min_entry": [30, 35, 40],
    "short_rsi_recovery": [48, 50, 52],
    "trail_trigger_r": [1.5, 2.0, 2.5],
    "short_hard_target_r": [2.5, 3.0, 3.5, 4.0],
}


def iter_grid(grid):
    keys = list(grid.keys())

    for values in product(*(grid[key] for key in keys)):
        params = dict(zip(keys, values))
        params.setdefault("bearish_close_count", strategy.BEARISH_CLOSE_COUNT)
        params.setdefault("bearish_trend_count", strategy.BEARISH_TREND_COUNT)
        yield params


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
    strategy.SHORT_RSI_MIN_ENTRY = params["short_rsi_min_entry"]
    strategy.SHORT_RSI_RECOVERY = params["short_rsi_recovery"]
    strategy.TRAIL_TRIGGER_R = params["trail_trigger_r"]
    strategy.SHORT_HARD_TARGET_R = params["short_hard_target_r"]
    strategy.ENABLE_LONGS = False
    strategy.ENABLE_SHORTS = True


def run_candidate(cur, symbols, timeframe, params, market_data_cache):
    set_strategy_params(timeframe, params)

    if timeframe not in market_data_cache:
        market_data_cache[timeframe] = strategy.load_all_market_data(cur, strategy.PAIRS)

    market_data_by_symbol = market_data_cache[timeframe]
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
            "score": -999.0,
        }

    df = pd.DataFrame(all_trades).sort_values("exit_time").reset_index(drop=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    pnl = df["pnl_usdt"]
    net_pnl = pnl.sum()
    pf = profit_factor(pnl)
    drawdown = max_drawdown(pnl)
    positive_months_pct = monthly_consistency(df)
    max_losses = max_consecutive_losses(df["result"])
    win_rate = (df["result"] == "WIN").mean() * 100

    score = (
        net_pnl
        + min(pf, 5.0) * 2
        + positive_months_pct / 5
        + drawdown * 0.75
        - max_losses * 0.5
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


def parse_args():
    parser = argparse.ArgumentParser(description="Optimize V12 against the current 1m candle database.")
    parser.add_argument("--full", action="store_true", help="Run the larger grid.")
    parser.add_argument(
        "--timeframe",
        choices=TIMEFRAMES,
        action="append",
        help="Timeframe to test. Can be passed more than once. Defaults to 15min.",
    )
    parser.add_argument(
        "--symbol",
        choices=strategy.PAIRS,
        action="append",
        help="Optimize only selected symbol(s). Defaults to all pairs.",
    )
    parser.add_argument("--limit", type=int, help="Maximum candidates per timeframe.")
    parser.add_argument("--top", type=int, default=20, help="Rows to print.")
    parser.add_argument(
        "--output",
        default="reports/v12_1m_short_edge_optimization.csv",
        help="CSV path for optimization results.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    grid = FULL_GRID if args.full else QUICK_GRID
    candidates = list(iter_grid(grid))

    if args.limit:
        candidates = candidates[:args.limit]

    timeframes = args.timeframe or ["15min"]
    symbols = args.symbol or strategy.PAIRS
    total_runs = len(candidates) * len(timeframes)

    conn = psycopg2.connect(**strategy.DB)
    cur = conn.cursor()
    market_data_cache = {}
    results = []
    run_number = 0

    print(f"Optimizing {total_runs} V12 candidates across {len(symbols)} symbol(s)...", flush=True)

    for timeframe in timeframes:
        for params in candidates:
            run_number += 1
            result = run_candidate(cur, symbols, timeframe, params, market_data_cache)
            results.append(result)
            print(
                f"[{run_number}/{total_runs}] {timeframe} "
                f"net={result['net_pnl_usdt']:.2f} "
                f"pf={result['profit_factor']:.2f} "
                f"dd={result['max_drawdown']:.2f} "
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

    print("\n===== TOP V12 1M RESULTS =====")
    print(results_df.head(args.top).to_string(index=False))
    print(f"\nSaved all results to {output_path}")


if __name__ == "__main__":
    main()
