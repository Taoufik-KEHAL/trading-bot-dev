import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2

from backtest_report import max_consecutive_losses, max_drawdown, profit_factor


from db_config import DB_CONFIG as DB

PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]

STRATEGY_NAME = "V13_ADAPTIVE_MARKET_REFLECTION"
SOURCE_TIMEFRAME = "1m"
DEFAULT_STRATEGY_TIMEFRAME = "15min"
ENABLE_LONGS = False
ENABLE_SHORTS = True

INITIAL_CAPITAL_USDT = 100.0
MAX_NOTIONAL_USDT = 100.0
MIN_NOTIONAL_USDT = 10.0
RISK_PER_TRADE = 0.02
MAX_OPEN_POSITIONS = 1
FEE_RATE = 0.001
SLIPPAGE_RATE = 0.0002

EMA_FAST = 21
EMA_MID = 50
EMA_TREND = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_PERIOD = 20

BREAKOUT_LOOKBACK = 20
RECENT_BREAKOUT_CANDLES = 12
SWING_LOOKBACK = 10
EMA_MID_SLOPE_LOOKBACK = 8
EMA_TREND_SLOPE_LOOKBACK = 16
RETURN_LOOKBACK = 96
VOL_REGIME_WINDOW = 96 * 30
VOL_REGIME_MIN_PERIODS = 96 * 7

MIN_ATR_PERCENT = 0.0035
MAX_ATR_PERCENT = 0.035

BULLISH_CLOSE_COUNT = 7
BULLISH_TREND_COUNT = 6
BEARISH_CLOSE_COUNT = 6
BEARISH_TREND_COUNT = 5

LOSS_COOLDOWN_AFTER_2 = 16
LOSS_COOLDOWN_AFTER_3 = 48
LOSS_COOLDOWN_AFTER_4 = 96
GLOBAL_COOLDOWN_AFTER_3 = 24


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


def resample_to_strategy_timeframe(df, timeframe):
    return (
        df.set_index("open_time")
        .resample(timeframe)
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
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"] = df["close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["rsi"] = compute_rsi(df["close"])
    df["atr"] = compute_atr(df)
    df["atr_percent"] = df["atr"] / df["close"]
    df["volume_sma"] = df["volume"].rolling(VOLUME_PERIOD).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma"]
    df["ema_mid_slope"] = df["ema_mid"] - df["ema_mid"].shift(EMA_MID_SLOPE_LOOKBACK)
    df["ema_trend_slope"] = df["ema_trend"] - df["ema_trend"].shift(EMA_TREND_SLOPE_LOOKBACK)
    df["return_24h"] = df["close"].pct_change(RETURN_LOOKBACK)
    df["trend_distance"] = df["close"] / df["ema_trend"] - 1

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
    df["bar_index"] = range(len(df))
    return df.dropna().reset_index(drop=True)


def load_market_data(cur, symbol, timeframe):
    df = load_raw_market_data(cur, symbol)

    if df.empty:
        return df

    df = resample_to_strategy_timeframe(df, timeframe)
    return compute_indicators(df)


def load_all_market_data(cur, symbols, timeframe):
    data = {}

    for symbol in symbols:
        print(f"Loading {symbol} {SOURCE_TIMEFRAME} -> {timeframe}...", flush=True)
        data[symbol] = load_market_data(cur, symbol, timeframe)

    return data


def build_market_regime(market_data_by_symbol):
    rows = []

    for symbol, df in market_data_by_symbol.items():
        if df.empty:
            continue

        rows.append(
            df[[
                "open_time", "close", "ema_mid", "ema_trend", "ema_mid_slope",
                "ema_trend_slope", "atr_percent", "return_24h", "trend_distance",
            ]].assign(symbol=symbol)
        )

    market_df = pd.concat(rows, ignore_index=True)
    market_df["above_trend"] = market_df["close"] > market_df["ema_trend"]
    market_df["below_trend"] = market_df["close"] < market_df["ema_trend"]
    market_df["bullish_stack"] = market_df["ema_mid"] > market_df["ema_trend"]
    market_df["bearish_stack"] = market_df["ema_mid"] < market_df["ema_trend"]

    breadth = (
        market_df.groupby("open_time")
        .agg(
            bullish_close_count=("above_trend", "sum"),
            bearish_close_count=("below_trend", "sum"),
            bullish_trend_count=("bullish_stack", "sum"),
            bearish_trend_count=("bearish_stack", "sum"),
            avg_atr_percent=("atr_percent", "mean"),
            median_return_24h=("return_24h", "median"),
            avg_abs_trend_distance=("trend_distance", lambda x: x.abs().mean()),
        )
        .reset_index()
    )

    btc_regime = market_data_by_symbol["BTCUSDT"][[
        "open_time", "close", "ema_mid", "ema_trend", "ema_mid_slope",
        "ema_trend_slope", "return_24h",
    ]].rename(columns={
        "close": "btc_close",
        "ema_mid": "btc_ema_mid",
        "ema_trend": "btc_ema_trend",
        "ema_mid_slope": "btc_ema_mid_slope",
        "ema_trend_slope": "btc_ema_trend_slope",
        "return_24h": "btc_return_24h",
    })

    breadth = breadth.merge(btc_regime, on="open_time", how="inner")
    breadth["btc_bullish"] = (
        (breadth["btc_close"] > breadth["btc_ema_trend"])
        & (breadth["btc_ema_mid"] > breadth["btc_ema_trend"])
        & (breadth["btc_ema_mid_slope"] > 0)
    )
    breadth["btc_bearish"] = (
        (breadth["btc_close"] < breadth["btc_ema_trend"])
        & (breadth["btc_ema_mid"] < breadth["btc_ema_trend"])
        & (breadth["btc_ema_mid_slope"] < 0)
    )

    rolling_vol = breadth["avg_atr_percent"].rolling(
        VOL_REGIME_WINDOW,
        min_periods=VOL_REGIME_MIN_PERIODS,
    )
    breadth["low_vol_threshold"] = rolling_vol.quantile(0.25)
    breadth["high_vol_threshold"] = rolling_vol.quantile(0.75)
    breadth["low_volatility"] = breadth["avg_atr_percent"] <= breadth["low_vol_threshold"]
    breadth["high_volatility"] = breadth["avg_atr_percent"] >= breadth["high_vol_threshold"]

    bullish_market = (
        breadth["btc_bullish"]
        & (breadth["bullish_close_count"] >= BULLISH_CLOSE_COUNT)
        & (breadth["bullish_trend_count"] >= BULLISH_TREND_COUNT)
        & (breadth["median_return_24h"] > -0.01)
    )
    bearish_market = (
        breadth["btc_bearish"]
        & (breadth["bearish_close_count"] >= BEARISH_CLOSE_COUNT)
        & (breadth["bearish_trend_count"] >= BEARISH_TREND_COUNT)
        & (breadth["median_return_24h"] < 0.01)
    )

    breadth["regime"] = "CHOP"
    breadth.loc[breadth["low_volatility"], "regime"] = "LOW_VOL_CHOP"
    breadth.loc[breadth["high_volatility"] & ~bullish_market & ~bearish_market, "regime"] = "HIGH_VOL_CHOP"
    breadth.loc[bullish_market, "regime"] = "BULL_TREND"
    breadth.loc[bearish_market, "regime"] = "BEAR_TREND"
    breadth.loc[bearish_market & breadth["high_volatility"], "regime"] = "HIGH_VOL_BEAR"

    return breadth.dropna().reset_index(drop=True)


def add_market_context(df, market_regime):
    regime_cols = [
        "open_time", "regime", "bullish_close_count", "bearish_close_count",
        "bullish_trend_count", "bearish_trend_count", "avg_atr_percent",
        "median_return_24h", "btc_return_24h",
    ]
    df = df.merge(market_regime[regime_cols], on="open_time", how="inner")
    df["relative_strength_24h"] = df["return_24h"] - df["btc_return_24h"]
    df["bar_index"] = range(len(df))
    return df.dropna().reset_index(drop=True)


def timeframe_to_minutes(timeframe):
    return {
        "15min": 15,
        "30min": 30,
        "1h": 60,
    }[timeframe]


def symbol_reflection(market_data_by_symbol):
    rows = []

    for symbol, df in market_data_by_symbol.items():
        if df.empty:
            continue

        close_return = df["close"].iloc[-1] / df["close"].iloc[0] - 1
        close_equity = df["close"] / df["close"].iloc[0]
        close_drawdown = (close_equity / close_equity.cummax() - 1).min()
        rows.append({
            "symbol": symbol,
            "first_open": df["open_time"].min(),
            "last_open": df["open_time"].max(),
            "bars": len(df),
            "return_percent": close_return * 100,
            "avg_atr_percent": df["atr_percent"].mean() * 100,
            "max_close_drawdown_percent": close_drawdown * 100,
            "above_ema200_percent": (df["close"] > df["ema_trend"]).mean() * 100,
        })

    return pd.DataFrame(rows).sort_values("return_percent", ascending=False)


def market_monthly_reflection(market_regime):
    monthly = (
        market_regime.assign(month=market_regime["open_time"].dt.to_period("M").astype(str))
        .groupby("month")
        .agg(
            bars=("open_time", "count"),
            btc_return_24h_median=("btc_return_24h", "median"),
            median_market_return_24h=("median_return_24h", "median"),
            avg_atr_percent=("avg_atr_percent", "mean"),
            bullish_breadth=("bullish_close_count", "mean"),
            bearish_breadth=("bearish_close_count", "mean"),
        )
    )
    regime_share = (
        market_regime.assign(month=market_regime["open_time"].dt.to_period("M").astype(str))
        .pivot_table(index="month", columns="regime", values="open_time", aggfunc="count", fill_value=0)
    )
    regime_share = regime_share.div(regime_share.sum(axis=1), axis=0) * 100
    regime_share.columns = [f"{col.lower()}_pct" for col in regime_share.columns]
    return monthly.join(regime_share).reset_index()


def regime_params(regime, side):
    if side == "LONG":
        return {
            "atr_stop_multiplier": 1.6,
            "hard_target_r": 2.4,
            "breakeven_trigger_r": 0.9,
            "trail_trigger_r": 1.5,
            "atr_trail_multiplier": 1.2,
            "max_holding_candles": 64,
        }

    return {
        "atr_stop_multiplier": 1.5,
        "hard_target_r": 3.0,
        "breakeven_trigger_r": 1.0,
        "trail_trigger_r": 2.0,
        "atr_trail_multiplier": 1.2,
        "max_holding_candles": 48,
    }


def is_long_candidate(row, previous_row):
    if not ENABLE_LONGS:
        return False

    if row["regime"] != "BULL_TREND":
        return False

    trend = (
        row["close"] > row["ema_trend"]
        and row["ema_fast"] > row["ema_mid"] > row["ema_trend"]
        and row["ema_mid_slope"] > 0
    )
    momentum_breakout = row["long_breakout"] and 54 <= row["rsi"] <= 76 and row["volume_ratio"] >= 1.10
    pullback_recovery = (
        row["recent_long_breakout"]
        and previous_row["close"] <= previous_row["ema_fast"]
        and row["close"] > row["ema_fast"]
        and previous_row["rsi"] < row["rsi"]
        and row["rsi"] >= 50
        and row["volume_ratio"] >= 0.95
    )

    return (
        trend
        and (momentum_breakout or pullback_recovery)
        and row["relative_strength_24h"] >= -0.01
        and MIN_ATR_PERCENT <= row["atr_percent"] <= MAX_ATR_PERCENT
        and row["close"] > row["open"]
    )


def is_short_candidate(row, previous_row):
    if not ENABLE_SHORTS:
        return False

    if row["regime"] not in ("BEAR_TREND", "HIGH_VOL_BEAR"):
        return False

    trend = (
        row["close"] < row["ema_trend"]
        and row["ema_fast"] < row["ema_mid"] < row["ema_trend"]
        and row["ema_mid_slope"] < 0
        and row["ema_trend_slope"] < 0
    )
    pullback_rejection = (
        row["recent_short_breakout"]
        and previous_row["close"] >= previous_row["ema_fast"]
        and row["close"] < row["ema_fast"]
        and previous_row["rsi"] > 50
        and 35 <= row["rsi"] <= 50
        and row["volume_ratio"] >= 1.15
    )

    return (
        trend
        and pullback_rejection
        and row["relative_strength_24h"] <= 0.01
        and 0.004 <= row["atr_percent"] <= MAX_ATR_PERCENT
        and row["close"] < row["open"]
    )


def signal_score(row, side):
    trend_score = abs(row["trend_distance"]) * 100
    volume_score = min(row["volume_ratio"], 3.0)
    breadth_score = (
        row["bullish_close_count"] / len(PAIRS)
        if side == "LONG"
        else row["bearish_close_count"] / len(PAIRS)
    )
    relative_strength_score = row["relative_strength_24h"] * 100

    if side == "SHORT":
        relative_strength_score *= -1

    regime_bonus = 0.75 if row["regime"] == "HIGH_VOL_BEAR" else 0.0
    return trend_score + volume_score + breadth_score + relative_strength_score + regime_bonus


def build_position(symbol, df, row, side, equity):
    index = int(row["bar_index"])
    params = regime_params(row["regime"], side)
    swing_start = max(0, index - SWING_LOOKBACK)
    swing_df = df.iloc[swing_start:index + 1]

    if side == "LONG":
        swing_stop = swing_df["low"].min()
        atr_stop = row["close"] - (row["atr"] * params["atr_stop_multiplier"])
        stop_price = min(swing_stop, atr_stop)
        risk_per_unit = row["close"] - stop_price
        hard_target_price = row["close"] + (risk_per_unit * params["hard_target_r"])
    else:
        swing_stop = swing_df["high"].max()
        atr_stop = row["close"] + (row["atr"] * params["atr_stop_multiplier"])
        stop_price = max(swing_stop, atr_stop)
        risk_per_unit = stop_price - row["close"]
        hard_target_price = row["close"] - (risk_per_unit * params["hard_target_r"])

    if risk_per_unit <= 0:
        return None

    risk_amount = equity * RISK_PER_TRADE
    risk_notional = risk_amount / (risk_per_unit / row["close"])
    notional_usdt = min(MAX_NOTIONAL_USDT, equity, risk_notional)

    if notional_usdt < MIN_NOTIONAL_USDT:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "regime": row["regime"],
        "entry_index": index,
        "entry_time": row["open_time"],
        "entry_price": row["close"],
        "stop_price": stop_price,
        "initial_stop_price": stop_price,
        "risk_per_unit": risk_per_unit,
        "hard_target_price": hard_target_price,
        "breakeven_active": False,
        "trailing_active": False,
        "notional_usdt": notional_usdt,
        **params,
    }


def current_r_multiple(position, row):
    if position["side"] == "LONG":
        return (row["high"] - position["entry_price"]) / position["risk_per_unit"]

    return (position["entry_price"] - row["low"]) / position["risk_per_unit"]


def update_dynamic_stop(position, row):
    r_multiple = current_r_multiple(position, row)

    if r_multiple >= position["breakeven_trigger_r"]:
        position["breakeven_active"] = True

    if r_multiple >= position["trail_trigger_r"]:
        position["trailing_active"] = True

    if position["side"] == "LONG":
        if position["breakeven_active"]:
            position["stop_price"] = max(position["stop_price"], position["entry_price"])
        if position["trailing_active"]:
            atr_trail = row["close"] - (row["atr"] * position["atr_trail_multiplier"])
            position["stop_price"] = max(position["stop_price"], atr_trail, row["ema_fast"])
    else:
        if position["breakeven_active"]:
            position["stop_price"] = min(position["stop_price"], position["entry_price"])
        if position["trailing_active"]:
            atr_trail = row["close"] + (row["atr"] * position["atr_trail_multiplier"])
            position["stop_price"] = min(position["stop_price"], atr_trail, row["ema_fast"])


def close_position(position, row, exit_price, exit_reason, equity):
    if position["side"] == "LONG":
        gross_pnl_percent = (exit_price - position["entry_price"]) / position["entry_price"]
    else:
        gross_pnl_percent = (position["entry_price"] - exit_price) / position["entry_price"]

    net_pnl_percent = gross_pnl_percent - ((FEE_RATE + SLIPPAGE_RATE) * 2)
    pnl_usdt = position["notional_usdt"] * net_pnl_percent
    new_equity = equity + pnl_usdt

    return {
        "strategy": STRATEGY_NAME,
        "symbol": position["symbol"],
        "side": position["side"],
        "regime": position["regime"],
        "entry_time": position["entry_time"],
        "exit_time": row["open_time"],
        "entry_price": position["entry_price"],
        "exit_price": exit_price,
        "notional_usdt": position["notional_usdt"],
        "pnl_percent": net_pnl_percent * 100,
        "gross_pnl_percent": gross_pnl_percent * 100,
        "pnl_usdt": pnl_usdt,
        "result": "WIN" if net_pnl_percent > 0 else "LOSS",
        "exit_reason": exit_reason,
        "stop_price": position["initial_stop_price"],
        "final_stop_price": position["stop_price"],
        "hard_target_price": position["hard_target_price"],
        "equity_after": new_equity,
    }


def cooldown_for_losing_streak(losing_streak):
    if losing_streak >= 4:
        return LOSS_COOLDOWN_AFTER_4
    if losing_streak == 3:
        return LOSS_COOLDOWN_AFTER_3
    if losing_streak == 2:
        return LOSS_COOLDOWN_AFTER_2
    return 0


def maybe_exit_position(position, row, previous_row):
    update_dynamic_stop(position, row)
    holding_candles = int(row["bar_index"]) - position["entry_index"]

    if position["side"] == "LONG":
        if row["low"] <= position["stop_price"]:
            reason = "DYNAMIC_STOP" if position["trailing_active"] else "STOP_LOSS"
            return position["stop_price"], reason
        if row["high"] >= position["hard_target_price"]:
            return position["hard_target_price"], "HARD_TARGET"
        if row["regime"] != "BULL_TREND" and row["close"] < row["ema_fast"]:
            return row["close"], "REGIME_FAIL"
        if row["close"] < row["ema_mid"] and previous_row["close"] < previous_row["ema_mid"]:
            return row["close"], "TREND_FAIL"
    else:
        if row["high"] >= position["stop_price"]:
            reason = "DYNAMIC_STOP" if position["trailing_active"] else "STOP_LOSS"
            return position["stop_price"], reason
        if row["low"] <= position["hard_target_price"]:
            return position["hard_target_price"], "HARD_TARGET"

    if holding_candles >= position["max_holding_candles"]:
        return row["close"], "TIME_EXIT"

    return None, None


def run_portfolio_backtest(market_data_by_symbol, market_regime, timeframe):
    contextual_data = {
        symbol: add_market_context(df, market_regime)
        for symbol, df in market_data_by_symbol.items()
        if not df.empty
    }
    indexed_data = {
        symbol: df.set_index("open_time", drop=False)
        for symbol, df in contextual_data.items()
    }

    trades = []
    open_positions = []
    equity = INITIAL_CAPITAL_USDT
    symbol_losing_streak = {symbol: 0 for symbol in contextual_data}
    symbol_cooldown_until = {symbol: -1 for symbol in contextual_data}
    global_losing_streak = 0
    global_cooldown_until_time = None
    strategy_minutes = timeframe_to_minutes(timeframe)

    for current_time in market_regime["open_time"]:
        still_open = []

        for position in open_positions:
            symbol = position["symbol"]
            symbol_df = indexed_data[symbol]

            if current_time not in symbol_df.index:
                still_open.append(position)
                continue

            row = symbol_df.loc[current_time]
            index = int(row["bar_index"])

            if index <= 0:
                still_open.append(position)
                continue

            previous_row = contextual_data[symbol].iloc[index - 1]
            exit_price, exit_reason = maybe_exit_position(position, row, previous_row)

            if exit_price is None:
                still_open.append(position)
                continue

            trade = close_position(position, row, exit_price, exit_reason, equity)
            trades.append(trade)
            equity = trade["equity_after"]

            if trade["result"] == "LOSS":
                symbol_losing_streak[symbol] += 1
                cooldown = cooldown_for_losing_streak(symbol_losing_streak[symbol])
                symbol_cooldown_until[symbol] = index + cooldown
                global_losing_streak += 1
                if global_losing_streak >= 3:
                    global_cooldown_until_time = current_time + pd.Timedelta(
                        minutes=strategy_minutes * GLOBAL_COOLDOWN_AFTER_3,
                    )
            else:
                symbol_losing_streak[symbol] = 0
                global_losing_streak = 0

        open_positions = still_open

        if len(open_positions) >= MAX_OPEN_POSITIONS:
            continue

        if global_cooldown_until_time is not None and current_time <= global_cooldown_until_time:
            continue

        if equity < MIN_NOTIONAL_USDT:
            break

        candidates = []

        for symbol, symbol_df in indexed_data.items():
            if current_time not in symbol_df.index:
                continue

            if any(position["symbol"] == symbol for position in open_positions):
                continue

            row = symbol_df.loc[current_time]
            index = int(row["bar_index"])

            if index <= max(SWING_LOOKBACK, BREAKOUT_LOOKBACK):
                continue

            if index <= symbol_cooldown_until[symbol]:
                continue

            previous_row = contextual_data[symbol].iloc[index - 1]

            if is_long_candidate(row, previous_row):
                candidates.append((signal_score(row, "LONG"), symbol, row, "LONG"))
            elif is_short_candidate(row, previous_row):
                candidates.append((signal_score(row, "SHORT"), symbol, row, "SHORT"))

        candidates.sort(key=lambda item: item[0], reverse=True)

        for _, symbol, row, side in candidates:
            position = build_position(symbol, contextual_data[symbol], row, side, equity)

            if position is None:
                continue

            open_positions.append(position)
            break

    return trades, contextual_data


def summarize_trades(trades):
    if not trades:
        return pd.DataFrame([{
            "strategy": STRATEGY_NAME,
            "trades": 0,
            "net_pnl_usdt": 0.0,
            "return_on_capital_percent": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usdt": 0.0,
            "max_consecutive_losses": 0,
            "final_equity": INITIAL_CAPITAL_USDT,
        }])

    df = pd.DataFrame(trades).sort_values("exit_time").reset_index(drop=True)
    pnl = df["pnl_usdt"]

    return pd.DataFrame([{
        "strategy": STRATEGY_NAME,
        "trades": len(df),
        "wins": int((df["result"] == "WIN").sum()),
        "losses": int((df["result"] == "LOSS").sum()),
        "net_pnl_usdt": pnl.sum(),
        "return_on_capital_percent": pnl.sum() / INITIAL_CAPITAL_USDT * 100,
        "win_rate": (df["result"] == "WIN").mean() * 100,
        "profit_factor": profit_factor(pnl),
        "avg_net_pnl": pnl.mean(),
        "largest_win": pnl.max(),
        "largest_loss": pnl.min(),
        "max_drawdown_usdt": max_drawdown(pnl),
        "max_consecutive_losses": max_consecutive_losses(df["result"]),
        "final_equity": INITIAL_CAPITAL_USDT + pnl.sum(),
    }])


def summarize_group(df, group_col):
    if df.empty or group_col not in df.columns:
        return pd.DataFrame()

    rows = []

    for group_value, group in df.groupby(group_col):
        pnl = group["pnl_usdt"]
        rows.append({
            group_col: group_value,
            "trades": len(group),
            "net_pnl_usdt": pnl.sum(),
            "win_rate": (group["result"] == "WIN").mean() * 100,
            "profit_factor": profit_factor(pnl),
            "avg_net_pnl": pnl.mean(),
        })

    return pd.DataFrame(rows).sort_values("net_pnl_usdt", ascending=False)


def print_historical_reflection(symbol_df, market_regime):
    regime_share = market_regime["regime"].value_counts(normalize=True).mul(100).round(2)
    best = symbol_df.iloc[0]
    worst = symbol_df.iloc[-1]
    first_open = symbol_df["first_open"].min()
    last_open = symbol_df["last_open"].max()

    print("\n===== HISTORICAL MARKET REFLECTION =====")
    print(f"Dataset: {first_open} to {last_open} on {len(symbol_df)} symbols from {SOURCE_TIMEFRAME} candles.")
    print(f"Best drift: {best['symbol']} {best['return_percent']:.2f}%")
    print(f"Worst drift: {worst['symbol']} {worst['return_percent']:.2f}%")
    print(f"Average ATR: {symbol_df['avg_atr_percent'].mean():.2f}% per strategy candle")
    print("\nRegime share:")
    print(regime_share.to_string())
    print("\nReflection: this market should be traded as regimes, not as one permanent bias.")
    print("Longs need broad bullish breadth; shorts need synchronized BTC and alt weakness; chop is a capital-preservation zone.")


def print_strategy_report(trades):
    summary = summarize_trades(trades)
    print("\n===== V13 STRATEGY SUMMARY =====")
    print(summary.to_string(index=False))

    if not trades:
        return

    df = pd.DataFrame(trades).sort_values("exit_time").reset_index(drop=True)

    print("\n===== BY SIDE =====")
    print(summarize_group(df, "side").to_string(index=False))
    print("\n===== BY REGIME =====")
    print(summarize_group(df, "regime").to_string(index=False))
    print("\n===== BY SYMBOL =====")
    print(summarize_group(df, "symbol").to_string(index=False))
    print("\n===== EXIT REASONS =====")
    print(df["exit_reason"].value_counts().to_string())
    print("\n===== BY MONTH =====")
    monthly = (
        df.assign(month=pd.to_datetime(df["exit_time"]).dt.to_period("M").astype(str))
        .groupby("month")
        .agg(
            trades=("symbol", "count"),
            net_pnl_usdt=("pnl_usdt", "sum"),
            win_rate=("result", lambda x: (x == "WIN").mean() * 100),
        )
    )
    monthly["equity"] = INITIAL_CAPITAL_USDT + monthly["net_pnl_usdt"].cumsum()
    print(monthly.to_string())


def save_reports(output_dir, symbol_df, monthly_market_df, market_regime, trades):
    output_dir.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame(trades)

    symbol_df.to_csv(output_dir / "v13_market_reflection_by_symbol.csv", index=False)
    monthly_market_df.to_csv(output_dir / "v13_market_reflection_monthly.csv", index=False)
    market_regime.to_csv(output_dir / "v13_market_regime_timeline.csv", index=False)
    summarize_trades(trades).to_csv(output_dir / "v13_adaptive_strategy_summary.csv", index=False)

    if not trades_df.empty:
        trades_df.sort_values("exit_time").to_csv(output_dir / "v13_adaptive_strategy_trades.csv", index=False)
        summarize_group(trades_df, "symbol").to_csv(output_dir / "v13_adaptive_strategy_by_symbol.csv", index=False)
        summarize_group(trades_df, "regime").to_csv(output_dir / "v13_adaptive_strategy_by_regime.csv", index=False)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Reflect on the one-year crypto market history and backtest an adaptive V13 portfolio strategy.",
    )
    parser.add_argument(
        "--timeframe",
        choices=["15min", "30min", "1h"],
        default=DEFAULT_STRATEGY_TIMEFRAME,
        help="Strategy candle size resampled from 1m data.",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where CSV reports are saved.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        conn = psycopg2.connect(**DB)
    except psycopg2.OperationalError as exc:
        print("Could not connect to PostgreSQL.")
        print(f"Host: {DB['host']}  Port: {DB['port']}  Database: {DB['dbname']}  User: {DB['user']}")
        error_message = str(exc).strip() or repr(exc)
        print(f"Error: {error_message}")
        return

    cur = conn.cursor()
    market_data_by_symbol = load_all_market_data(cur, PAIRS, args.timeframe)
    cur.close()
    conn.close()

    market_regime = build_market_regime(market_data_by_symbol)
    symbol_df = symbol_reflection(market_data_by_symbol)
    monthly_market_df = market_monthly_reflection(market_regime)
    print_historical_reflection(symbol_df, market_regime)

    trades, _ = run_portfolio_backtest(market_data_by_symbol, market_regime, args.timeframe)
    print_strategy_report(trades)
    save_reports(Path(args.output_dir), symbol_df, monthly_market_df, market_regime, trades)
    print(f"\nSaved V13 reports to {Path(args.output_dir)}")


if __name__ == "__main__":
    main()
