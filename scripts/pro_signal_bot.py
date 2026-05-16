#!/usr/bin/env python3
"""Generate current trading signals from the selected professional strategy.

This is an automation-safe layer: it can write BUY/SELL/HOLD signals to the DB,
but it does not place exchange orders. Add exchange keys and an execution adapter
only after paper trading and explicit risk approval.
"""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from pro_strategy_research import (
    ROOT,
    Candidate,
    build_signals,
    connect_db,
    discover_symbols,
    prepare_frame,
    resample_ohlcv,
)


DEFAULT_CONFIG = ROOT / "strategies" / "pro_best_strategy.json"


def timeframe_to_minutes(timeframe: str) -> int:
    normalized = timeframe.lower()
    if normalized.endswith("min"):
        return int(normalized[:-3])
    if normalized.endswith("m"):
        return int(normalized[:-1])
    if normalized.endswith("h"):
        return int(normalized[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def candidate_from_config(path: Path) -> tuple[Candidate, list[str]]:
    config = json.loads(path.read_text())
    candidate = Candidate(**config["candidate"])
    symbols = [symbol.upper() for symbol in config.get("symbols", [])]
    return candidate, symbols


def load_recent_1m(conn, symbol: str, limit: int) -> pd.DataFrame:
    query = """
        select *
        from (
            select
                open_time,
                open::double precision as open,
                high::double precision as high,
                low::double precision as low,
                close::double precision as close,
                volume::double precision as volume
            from public.market_candles
            where timeframe = %s and symbol = %s
            order by open_time desc
            limit %s
        ) recent
        order by open_time
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pandas only supports SQLAlchemy.*",
            category=UserWarning,
        )
        df = pd.read_sql_query(query, conn, params=("1m", symbol, limit), parse_dates=["open_time"])
    if df.empty:
        raise ValueError(f"No recent 1m candles found for {symbol}")
    return df.set_index("open_time").sort_index()


def required_lookback_minutes(candidate: Candidate) -> int:
    timeframe_minutes = timeframe_to_minutes(candidate.timeframe)
    warmup_bars = max(
        260,
        candidate.ema_slow + 60,
        candidate.lookback + 60,
        candidate.bb_window + 60,
    )
    return timeframe_minutes * warmup_bars


def latest_complete_bar_index(frame: pd.DataFrame, last_1m_open: pd.Timestamp, timeframe: str) -> int:
    timeframe_delta = pd.Timedelta(minutes=timeframe_to_minutes(timeframe))
    data_known_until = last_1m_open + pd.Timedelta(minutes=1)
    complete = frame.index + timeframe_delta <= data_known_until
    complete_positions = np.flatnonzero(np.asarray(complete))
    if len(complete_positions) == 0:
        raise ValueError("No complete signal bar available in the recent candle window")
    return int(complete_positions[-1])


def signal_strength(candidate: Candidate, frame: pd.DataFrame, idx: int, side: int) -> float:
    if side == 0:
        return 0.0

    row = frame.iloc[idx]
    close = float(row["close"])
    if candidate.family == "donchian_breakout" and candidate.lookback:
        if side == 1:
            ref = float(row[f"roll_high_{candidate.lookback}"])
            return max(0.0, ((close / ref) - 1) * 10_000) if ref > 0 else 0.0
        ref = float(row[f"roll_low_{candidate.lookback}"])
        return max(0.0, ((ref / close) - 1) * 10_000) if close > 0 else 0.0

    if candidate.family == "range_mean_reversion" and candidate.bb_window:
        z_value = row.get(f"z_{candidate.bb_window}", 0.0)
        return float(abs(z_value)) if np.isfinite(z_value) else 0.0

    rsi = float(row.get("rsi14", 50.0))
    threshold = candidate.rsi_low if side == 1 else candidate.rsi_high
    return float(abs(rsi - threshold))


def evaluate_symbol(conn, symbol: str, candidate: Candidate, lookback_minutes: int) -> dict:
    one_min = load_recent_1m(conn, symbol, lookback_minutes)
    frame = prepare_frame(resample_ohlcv(one_min, candidate.timeframe))
    signals = build_signals(frame, candidate)
    idx = latest_complete_bar_index(frame, one_min.index.max(), candidate.timeframe)
    row = frame.iloc[idx]
    side = int(signals[idx])
    action = "BUY" if side == 1 else "SELL" if side == -1 else "HOLD"

    close = float(row["close"])
    atr = float(row["atr14"]) if np.isfinite(row["atr14"]) else 0.0
    stop_distance = atr * candidate.stop_atr
    if side == 1:
        stop = close - stop_distance
        target = close + stop_distance * candidate.reward_risk
    elif side == -1:
        stop = close + stop_distance
        target = close - stop_distance * candidate.reward_risk
    else:
        stop = None
        target = None

    strength = signal_strength(candidate, frame, idx, side)
    reason = (
        f"{candidate.family} {candidate.timeframe}; "
        f"bar={frame.index[idx].isoformat()}; "
        f"atr={atr:.8g}; stop={stop:.8g} target={target:.8g}"
        if side
        else f"{candidate.family} {candidate.timeframe}; bar={frame.index[idx].isoformat()}; no entry"
    )

    return {
        "symbol": symbol,
        "timeframe": candidate.timeframe,
        "bar_time": frame.index[idx],
        "close": close,
        "rsi": float(row["rsi14"]) if np.isfinite(row["rsi14"]) else None,
        "ema50": float(row["ema50"]) if np.isfinite(row["ema50"]) else None,
        "ema200": float(row["ema200"]) if np.isfinite(row["ema200"]) else None,
        "signal": action,
        "score": strength,
        "stop": stop,
        "target": target,
        "reason": reason,
    }


def write_signal(conn, signal: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.strategy_signals
                (symbol, timeframe, close_price, rsi, ema50, ema200, signal, score, reason, created_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                signal["symbol"],
                signal["timeframe"],
                signal["close"],
                signal["rsi"],
                signal["ema50"],
                signal["ema200"],
                signal["signal"],
                signal["score"],
                signal["reason"],
            ),
        )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--symbols", default="", help="Comma-separated override. Default: config symbols.")
    parser.add_argument("--lookback-minutes", type=int, default=0)
    parser.add_argument("--write-db", action="store_true", help="Insert signals into public.strategy_signals.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a compact table.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidate, config_symbols = candidate_from_config(args.config)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    lookback_minutes = args.lookback_minutes or required_lookback_minutes(candidate)

    conn = connect_db()
    try:
        if not symbols:
            symbols = config_symbols or discover_symbols(conn)

        signals = [evaluate_symbol(conn, symbol, candidate, lookback_minutes) for symbol in symbols]
        if args.write_db:
            for signal in signals:
                write_signal(conn, signal)

        if args.json:
            safe = []
            for signal in signals:
                item = dict(signal)
                item["bar_time"] = item["bar_time"].isoformat()
                safe.append(item)
            print(json.dumps({"strategy": asdict(candidate), "signals": safe}, indent=2))
            return

        print(f"Strategy: {candidate.name}")
        print(f"{'SYMBOL':<10} {'SIGNAL':<5} {'CLOSE':>14} {'SCORE':>10} {'STOP':>14} {'TARGET':>14} BAR")
        for signal in signals:
            stop = "" if signal["stop"] is None else f"{signal['stop']:.8g}"
            target = "" if signal["target"] is None else f"{signal['target']:.8g}"
            print(
                f"{signal['symbol']:<10} {signal['signal']:<5} "
                f"{signal['close']:>14.8g} {signal['score']:>10.3f} "
                f"{stop:>14} {target:>14} {signal['bar_time']}"
            )
        if not args.write_db:
            print("Dry run only. Add --write-db to store these signals in public.strategy_signals.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
