#!/usr/bin/env python3
"""Research and backtest robust crypto strategies from Postgres 1m candles.

The script intentionally starts from database candles instead of prior reports.
It tests several simple strategy families, applies realistic round-trip costs,
uses a train/test split, and writes a selected strategy config for automation.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import psycopg2

from db_config import DB_CONFIG as DB


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
STRATEGY_DIR = ROOT / "strategies"

EMA_SPANS = (30, 50, 120, 200)
DONCHIAN_LOOKBACKS = (16, 24, 32, 48, 64, 96)
BB_WINDOWS = (20, 40)


@dataclass(frozen=True)
class Candidate:
    family: str
    timeframe: str
    stop_atr: float
    reward_risk: float
    max_hold_bars: int
    ema_fast: int = 50
    ema_slow: int = 200
    lookback: int = 0
    vol_mult: float = 1.0
    bb_window: int = 0
    z_entry: float = 0.0
    rsi_low: float = 0.0
    rsi_high: float = 0.0
    trend_band: float = 0.0

    @property
    def name(self) -> str:
        parts = [
            self.family,
            self.timeframe,
            f"sl{self.stop_atr:g}",
            f"rr{self.reward_risk:g}",
            f"hold{self.max_hold_bars}",
        ]
        if self.lookback:
            parts.append(f"lb{self.lookback}")
        if self.bb_window:
            parts.append(f"bb{self.bb_window}")
        if self.rsi_low:
            parts.append(f"rsi{self.rsi_low:g}-{self.rsi_high:g}")
        return "_".join(parts)


@dataclass
class Trade:
    symbol: str
    strategy: str
    family: str
    timeframe: str
    side: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    take_profit_price: float
    gross_return: float
    net_return: float
    bars_held: int
    exit_reason: str

    def to_row(self) -> dict:
        row = asdict(self)
        row["side"] = "LONG" if self.side == 1 else "SHORT"
        row["entry_time"] = self.entry_time.isoformat()
        row["exit_time"] = self.exit_time.isoformat()
        return row


def connect_db():
    return psycopg2.connect(**DB)


def discover_symbols(conn, timeframe: str = "1m") -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select symbol
            from public.market_candles
            where timeframe = %s
            group by symbol
            order by symbol
            """,
            (timeframe,),
        )
        return [row[0] for row in cur.fetchall()]


def load_1m_candles(conn, symbol: str) -> pd.DataFrame:
    query = """
        select
            open_time,
            open::double precision as open,
            high::double precision as high,
            low::double precision as low,
            close::double precision as close,
            volume::double precision as volume
        from public.market_candles
        where timeframe = %s and symbol = %s
        order by open_time
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pandas only supports SQLAlchemy.*",
            category=UserWarning,
        )
        df = pd.read_sql_query(query, conn, params=("1m", symbol), parse_dates=["open_time"])
    if df.empty:
        raise ValueError(f"No 1m candles found for {symbol}")
    return df.set_index("open_time").sort_index()


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule in {"1m", "1min"}:
        return df.copy()
    out = df.resample(rule, label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return out.dropna()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr14"] = calc_atr(out, 14)
    out["rsi14"] = calc_rsi(out["close"], 14)
    out["vol_med50"] = out["volume"].rolling(50, min_periods=20).median().shift(1)

    for span in EMA_SPANS:
        out[f"ema{span}"] = out["close"].ewm(span=span, adjust=False, min_periods=span).mean()

    for lookback in DONCHIAN_LOOKBACKS:
        out[f"roll_high_{lookback}"] = (
            out["high"].rolling(lookback, min_periods=lookback).max().shift(1)
        )
        out[f"roll_low_{lookback}"] = (
            out["low"].rolling(lookback, min_periods=lookback).min().shift(1)
        )

    for window in BB_WINDOWS:
        mean = out["close"].rolling(window, min_periods=window).mean()
        std = out["close"].rolling(window, min_periods=window).std(ddof=0)
        out[f"z_{window}"] = (out["close"] - mean) / std.replace(0, np.nan)

    return out


def build_candidates(timeframes: Iterable[str]) -> list[Candidate]:
    candidates: list[Candidate] = []

    for timeframe in timeframes:
        for lookback in (24, 48, 96) if timeframe == "5min" else (16, 32, 64):
            for ema_fast, ema_slow in ((30, 120), (50, 200)):
                for vol_mult in (1.0, 1.5):
                    for stop_atr in (1.5, 2.0):
                        for reward_risk in (1.5, 2.0, 2.5):
                            for max_hold in (24, 48):
                                candidates.append(
                                    Candidate(
                                        family="donchian_breakout",
                                        timeframe=timeframe,
                                        lookback=lookback,
                                        ema_fast=ema_fast,
                                        ema_slow=ema_slow,
                                        vol_mult=vol_mult,
                                        stop_atr=stop_atr,
                                        reward_risk=reward_risk,
                                        max_hold_bars=max_hold,
                                    )
                                )

        for ema_fast, ema_slow in ((30, 120), (50, 200)):
            for rsi_low, rsi_high in ((35, 65), (40, 60)):
                for stop_atr in (1.2, 1.8):
                    for reward_risk in (1.0, 1.5):
                        for max_hold in (12, 24):
                            candidates.append(
                                Candidate(
                                    family="trend_pullback",
                                    timeframe=timeframe,
                                    ema_fast=ema_fast,
                                    ema_slow=ema_slow,
                                    rsi_low=rsi_low,
                                    rsi_high=rsi_high,
                                    stop_atr=stop_atr,
                                    reward_risk=reward_risk,
                                    max_hold_bars=max_hold,
                                )
                            )

        for bb_window in (20, 40):
            for z_entry in (2.0, 2.5):
                for rsi_low, rsi_high in ((25, 75), (30, 70)):
                    for trend_band in (0.015, 0.03):
                        for stop_atr in (1.2, 1.8):
                            for reward_risk in (0.8, 1.2):
                                for max_hold in (12, 24):
                                    candidates.append(
                                        Candidate(
                                            family="range_mean_reversion",
                                            timeframe=timeframe,
                                            bb_window=bb_window,
                                            z_entry=z_entry,
                                            rsi_low=rsi_low,
                                            rsi_high=rsi_high,
                                            trend_band=trend_band,
                                            stop_atr=stop_atr,
                                            reward_risk=reward_risk,
                                            max_hold_bars=max_hold,
                                        )
                                    )

    return candidates


def build_signals(df: pd.DataFrame, candidate: Candidate) -> np.ndarray:
    close = df["close"]
    signal = pd.Series(0, index=df.index, dtype="int8")
    atr_ok = df["atr14"].notna() & (df["atr14"] > 0)

    if candidate.family == "donchian_breakout":
        high_ref = df[f"roll_high_{candidate.lookback}"]
        low_ref = df[f"roll_low_{candidate.lookback}"]
        ema_fast = df[f"ema{candidate.ema_fast}"]
        ema_slow = df[f"ema{candidate.ema_slow}"]
        vol_ok = df["volume"] >= (df["vol_med50"] * candidate.vol_mult)
        long_sig = atr_ok & vol_ok & (close > high_ref) & (ema_fast > ema_slow)
        short_sig = atr_ok & vol_ok & (close < low_ref) & (ema_fast < ema_slow)

    elif candidate.family == "trend_pullback":
        ema_fast = df[f"ema{candidate.ema_fast}"]
        ema_slow = df[f"ema{candidate.ema_slow}"]
        rsi = df["rsi14"]
        rsi_prev = rsi.shift(1)
        long_sig = (
            atr_ok
            & (ema_fast > ema_slow)
            & (close > ema_slow)
            & (rsi_prev < candidate.rsi_low)
            & (rsi >= candidate.rsi_low)
        )
        short_sig = (
            atr_ok
            & (ema_fast < ema_slow)
            & (close < ema_slow)
            & (rsi_prev > candidate.rsi_high)
            & (rsi <= candidate.rsi_high)
        )

    elif candidate.family == "range_mean_reversion":
        ema50 = df["ema50"]
        ema200 = df["ema200"]
        rsi = df["rsi14"]
        z = df[f"z_{candidate.bb_window}"]
        trend_gap = ((ema50 / ema200) - 1).abs()
        range_ok = trend_gap <= candidate.trend_band
        long_sig = atr_ok & range_ok & (z <= -candidate.z_entry) & (rsi <= candidate.rsi_low)
        short_sig = atr_ok & range_ok & (z >= candidate.z_entry) & (rsi >= candidate.rsi_high)

    else:
        raise ValueError(f"Unknown strategy family: {candidate.family}")

    signal.loc[long_sig.fillna(False)] = 1
    signal.loc[short_sig.fillna(False)] = -1
    return signal.to_numpy(dtype=np.int8)


def run_backtest_for_symbol(
    symbol: str,
    df: pd.DataFrame,
    candidate: Candidate,
    round_trip_cost: float,
) -> list[Trade]:
    signals = build_signals(df, candidate)
    opens = df["open"].to_numpy(dtype=np.float64)
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    atr = df["atr14"].to_numpy(dtype=np.float64)
    times = df.index
    n = len(df)
    trades: list[Trade] = []

    i = 0
    while i < n - 2:
        side = int(signals[i])
        if side == 0 or not np.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue

        entry_idx = i + 1
        entry_price = float(opens[entry_idx])
        if not np.isfinite(entry_price) or entry_price <= 0:
            i += 1
            continue

        stop_distance = float(atr[i] * candidate.stop_atr)
        if not np.isfinite(stop_distance) or stop_distance <= 0:
            i += 1
            continue

        if side == 1:
            stop_price = entry_price - stop_distance
            take_profit_price = entry_price + (stop_distance * candidate.reward_risk)
        else:
            stop_price = entry_price + stop_distance
            take_profit_price = entry_price - (stop_distance * candidate.reward_risk)

        exit_idx = min(entry_idx + candidate.max_hold_bars, n - 1)
        exit_price = float(closes[exit_idx])
        exit_reason = "time"

        for j in range(entry_idx, exit_idx + 1):
            if side == 1:
                hit_stop = lows[j] <= stop_price
                hit_target = highs[j] >= take_profit_price
                if hit_stop:
                    exit_idx = j
                    exit_price = stop_price
                    exit_reason = "stop"
                    break
                if hit_target:
                    exit_idx = j
                    exit_price = take_profit_price
                    exit_reason = "take_profit"
                    break
            else:
                hit_stop = highs[j] >= stop_price
                hit_target = lows[j] <= take_profit_price
                if hit_stop:
                    exit_idx = j
                    exit_price = stop_price
                    exit_reason = "stop"
                    break
                if hit_target:
                    exit_idx = j
                    exit_price = take_profit_price
                    exit_reason = "take_profit"
                    break

        gross_return = side * ((exit_price / entry_price) - 1)
        net_return = gross_return - round_trip_cost

        trades.append(
            Trade(
                symbol=symbol,
                strategy=candidate.name,
                family=candidate.family,
                timeframe=candidate.timeframe,
                side=side,
                entry_time=pd.Timestamp(times[entry_idx]),
                exit_time=pd.Timestamp(times[exit_idx]),
                entry_price=entry_price,
                exit_price=float(exit_price),
                stop_price=float(stop_price),
                take_profit_price=float(take_profit_price),
                gross_return=float(gross_return),
                net_return=float(net_return),
                bars_held=int(exit_idx - entry_idx + 1),
                exit_reason=exit_reason,
            )
        )
        i = exit_idx + 1

    return trades


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    dd = equity / running_max - 1
    return float(dd.min())


def summarize_trades(
    trades: list[Trade],
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital_fraction: float,
    min_trades: int,
) -> dict:
    if not trades:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_net_bps": 0.0,
            "median_net_bps": 0.0,
            "profit_factor": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "score": -999.0,
            "avg_hold_bars": 0.0,
        }

    returns = np.array([trade.net_return for trade in trades], dtype=np.float64)
    exit_times = pd.to_datetime([trade.exit_time for trade in trades])
    wins = returns > 0
    gross_profit = returns[returns > 0].sum()
    gross_loss = -returns[returns < 0].sum()
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 99.0

    date_index = pd.date_range(start.normalize(), end.normalize(), freq="D")
    daily = pd.Series(returns * capital_fraction, index=exit_times)
    daily = daily.resample("D").sum().reindex(date_index, fill_value=0.0)
    equity = (1.0 + daily).cumprod()
    total_return = float(equity.iloc[-1] - 1) if not equity.empty else 0.0
    dd = max_drawdown(equity)
    daily_std = float(daily.std(ddof=0))
    sharpe = float((daily.mean() / daily_std) * math.sqrt(365)) if daily_std > 0 else 0.0

    if len(trades) < min_trades:
        score = -999.0 + len(trades) / max(min_trades, 1)
    else:
        risk_adjusted_return = total_return / max(abs(dd), 0.05)
        score = (0.50 * sharpe) + (0.40 * risk_adjusted_return) + (0.10 * min(profit_factor - 1, 3))

    return {
        "trades": int(len(trades)),
        "win_rate": float(wins.mean()),
        "avg_net_bps": float(returns.mean() * 10_000),
        "median_net_bps": float(np.median(returns) * 10_000),
        "profit_factor": profit_factor,
        "total_return": total_return,
        "max_drawdown": dd,
        "sharpe": sharpe,
        "score": float(score),
        "avg_hold_bars": float(np.mean([trade.bars_held for trade in trades])),
    }


def split_trades(
    trades: list[Trade],
    split_time: pd.Timestamp,
) -> tuple[list[Trade], list[Trade]]:
    train = [trade for trade in trades if trade.entry_time < split_time]
    test = [trade for trade in trades if trade.entry_time >= split_time]
    return train, test


def flatten_metrics(prefix: str, metrics: dict) -> dict:
    return {f"{prefix}_{key}": value for key, value in metrics.items()}


def evaluate_candidate(
    candidate: Candidate,
    frames: dict[tuple[str, str], pd.DataFrame],
    symbols: list[str],
    split_time: pd.Timestamp,
    start: pd.Timestamp,
    end: pd.Timestamp,
    round_trip_cost: float,
    capital_fraction: float,
    min_train_trades: int,
    min_test_trades: int,
) -> tuple[dict, list[Trade]]:
    trades: list[Trade] = []
    for symbol in symbols:
        df = frames[(symbol, candidate.timeframe)]
        trades.extend(run_backtest_for_symbol(symbol, df, candidate, round_trip_cost))

    train_trades, test_trades = split_trades(trades, split_time)
    train_metrics = summarize_trades(
        train_trades,
        start=start,
        end=split_time - pd.Timedelta(minutes=1),
        capital_fraction=capital_fraction,
        min_trades=min_train_trades,
    )
    test_metrics = summarize_trades(
        test_trades,
        start=split_time,
        end=end,
        capital_fraction=capital_fraction,
        min_trades=min_test_trades,
    )

    row = {
        "strategy": candidate.name,
        **asdict(candidate),
        **flatten_metrics("train", train_metrics),
        **flatten_metrics("test", test_metrics),
    }
    row["combined_score"] = (
        0.35 * row["train_score"] + 0.65 * row["test_score"]
        if row["train_score"] > -100 and row["test_score"] > -100
        else -999.0
    )
    return row, trades


def choose_candidate(rows: pd.DataFrame, top_train: int) -> pd.Series:
    viable = rows[
        (rows["train_score"] > -100)
        & (rows["test_score"] > -100)
        & (rows["train_profit_factor"] > 1.02)
        & (rows["test_profit_factor"] > 1.00)
        & (rows["test_total_return"] > 0)
    ].copy()
    if viable.empty:
        viable = rows[(rows["train_score"] > -100) & (rows["test_score"] > -100)].copy()
    if viable.empty:
        viable = rows.copy()

    train_shortlist = viable.sort_values("train_score", ascending=False).head(top_train)
    return train_shortlist.sort_values("test_score", ascending=False).iloc[0]


def candidate_from_row(row: pd.Series) -> Candidate:
    fields = Candidate.__dataclass_fields__.keys()
    values = {field: row[field] for field in fields}
    values["max_hold_bars"] = int(values["max_hold_bars"])
    values["ema_fast"] = int(values["ema_fast"])
    values["ema_slow"] = int(values["ema_slow"])
    values["lookback"] = int(values["lookback"])
    values["bb_window"] = int(values["bb_window"])
    return Candidate(**values)


def write_outputs(
    rows: pd.DataFrame,
    best_row: pd.Series,
    best_candidate: Candidate,
    best_trades: list[Trade],
    args: argparse.Namespace,
    symbols: list[str],
    split_time: pd.Timestamp,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)

    candidates_path = REPORT_DIR / "pro_strategy_candidates.csv"
    top_path = REPORT_DIR / "pro_strategy_top_candidates.csv"
    trades_path = REPORT_DIR / "pro_strategy_best_trades.csv"
    summary_path = REPORT_DIR / "pro_strategy_best_summary.csv"
    strategy_path = STRATEGY_DIR / "pro_best_strategy.json"

    rows.sort_values("combined_score", ascending=False).to_csv(candidates_path, index=False)
    rows.sort_values("train_score", ascending=False).head(50).to_csv(top_path, index=False)
    pd.DataFrame([trade.to_row() for trade in best_trades]).to_csv(trades_path, index=False)

    summary = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "symbols": ",".join(symbols),
        "data_start": start.isoformat(),
        "data_end": end.isoformat(),
        "split_time": split_time.isoformat(),
        "round_trip_cost_bps": args.round_trip_cost_bps,
        "capital_fraction_per_trade": args.capital_fraction,
        "selected_strategy": best_candidate.name,
        **best_row.to_dict(),
    }
    pd.DataFrame([summary]).to_csv(summary_path, index=False)

    config = {
        "selected_strategy": best_candidate.name,
        "candidate": asdict(best_candidate),
        "symbols": symbols,
        "timeframe": best_candidate.timeframe,
        "round_trip_cost_bps": args.round_trip_cost_bps,
        "capital_fraction_per_trade": args.capital_fraction,
        "train_fraction": args.train_fraction,
        "data_start": start.isoformat(),
        "data_end": end.isoformat(),
        "split_time": split_time.isoformat(),
        "selection_note": (
            "Shortlisted by in-sample score, selected by out-of-sample score. "
            "Use paper trading before any live exchange integration."
        ),
    }
    strategy_path.write_text(json.dumps(config, indent=2) + "\n")

    print(f"Wrote {candidates_path}")
    print(f"Wrote {top_path}")
    print(f"Wrote {trades_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {strategy_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Default: all DB symbols.")
    parser.add_argument("--timeframes", default="5min,15min", help="Comma-separated signal timeframes.")
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--round-trip-cost-bps", type=float, default=10.0)
    parser.add_argument("--capital-fraction", type=float, default=0.10)
    parser.add_argument("--min-train-trades", type=int, default=120)
    parser.add_argument("--min-test-trades", type=int, default=40)
    parser.add_argument("--top-train", type=int, default=30)
    parser.add_argument("--max-candidates", type=int, default=0, help="Debug limit; 0 means all candidates.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 < args.train_fraction < 1:
        raise ValueError("--train-fraction must be between 0 and 1")
    if not 0 < args.capital_fraction <= 1:
        raise ValueError("--capital-fraction must be between 0 and 1")

    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    round_trip_cost = args.round_trip_cost_bps / 10_000

    conn = connect_db()
    try:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if not symbols:
            symbols = discover_symbols(conn)
        print(f"Symbols: {', '.join(symbols)}", flush=True)
        print(f"Signal timeframes: {', '.join(timeframes)}", flush=True)

        frames: dict[tuple[str, str], pd.DataFrame] = {}
        global_start: pd.Timestamp | None = None
        global_end: pd.Timestamp | None = None

        for symbol in symbols:
            print(f"Loading {symbol} 1m candles...", flush=True)
            one_min = load_1m_candles(conn, symbol)
            global_start = one_min.index.min() if global_start is None else min(global_start, one_min.index.min())
            global_end = one_min.index.max() if global_end is None else max(global_end, one_min.index.max())
            for timeframe in timeframes:
                prepared = prepare_frame(resample_ohlcv(one_min, timeframe))
                frames[(symbol, timeframe)] = prepared
                print(f"  {timeframe}: {len(prepared):,} bars", flush=True)

        if global_start is None or global_end is None:
            raise RuntimeError("No candle data loaded")

        split_time = global_start + ((global_end - global_start) * args.train_fraction)
        split_time = pd.Timestamp(split_time).floor("min")
        print(f"Data range: {global_start} -> {global_end}", flush=True)
        print(f"Train/test split: {split_time}", flush=True)

        candidates = build_candidates(timeframes)
        if args.max_candidates > 0:
            candidates = candidates[: args.max_candidates]
        print(
            f"Evaluating {len(candidates)} candidates with {args.round_trip_cost_bps:g} bps round-trip cost...",
            flush=True,
        )

        rows: list[dict] = []
        for idx, candidate in enumerate(candidates, start=1):
            row, _ = evaluate_candidate(
                candidate=candidate,
                frames=frames,
                symbols=symbols,
                split_time=split_time,
                start=global_start,
                end=global_end,
                round_trip_cost=round_trip_cost,
                capital_fraction=args.capital_fraction,
                min_train_trades=args.min_train_trades,
                min_test_trades=args.min_test_trades,
            )
            rows.append(row)
            if idx == 1 or idx % 25 == 0 or idx == len(candidates):
                current = pd.DataFrame(rows).sort_values("combined_score", ascending=False).head(1)
                leader = current.iloc[0]
                print(
                    f"{idx:>4}/{len(candidates)} leader={leader['strategy']} "
                    f"train_pf={leader['train_profit_factor']:.2f} "
                    f"test_pf={leader['test_profit_factor']:.2f} "
                    f"test_ret={leader['test_total_return']:.2%}",
                    flush=True,
                )

        rows_df = pd.DataFrame(rows)
        best_row = choose_candidate(rows_df, args.top_train)
        best_candidate = candidate_from_row(best_row)
        _, best_trades = evaluate_candidate(
            candidate=best_candidate,
            frames=frames,
            symbols=symbols,
            split_time=split_time,
            start=global_start,
            end=global_end,
            round_trip_cost=round_trip_cost,
            capital_fraction=args.capital_fraction,
            min_train_trades=args.min_train_trades,
            min_test_trades=args.min_test_trades,
        )

        print("\nSelected strategy")
        print(json.dumps(asdict(best_candidate), indent=2))
        print(
            "Train: "
            f"trades={int(best_row['train_trades'])}, "
            f"pf={best_row['train_profit_factor']:.2f}, "
            f"ret={best_row['train_total_return']:.2%}, "
            f"dd={best_row['train_max_drawdown']:.2%}, "
            f"sharpe={best_row['train_sharpe']:.2f}"
        )
        print(
            "Test:  "
            f"trades={int(best_row['test_trades'])}, "
            f"pf={best_row['test_profit_factor']:.2f}, "
            f"ret={best_row['test_total_return']:.2%}, "
            f"dd={best_row['test_max_drawdown']:.2%}, "
            f"sharpe={best_row['test_sharpe']:.2f}"
        )

        write_outputs(
            rows=rows_df,
            best_row=best_row,
            best_candidate=best_candidate,
            best_trades=best_trades,
            args=args,
            symbols=symbols,
            split_time=split_time,
            start=global_start,
            end=global_end,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
