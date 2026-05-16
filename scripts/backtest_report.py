import pandas as pd


def profit_factor(pnl):
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = pnl[pnl < 0].sum()

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0

    return gross_profit / abs(gross_loss)


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


def max_drawdown(pnl):
    equity = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    peak = equity.cummax()
    drawdown = equity - peak
    return drawdown.min()


def summarize_group(df, group_col):
    rows = []

    for group_value, group in df.groupby(group_col):
        pnl = group["pnl_usdt"]
        wins = group[group["result"] == "WIN"]
        losses = group[group["result"] == "LOSS"]

        rows.append({
            group_col: group_value,
            "trades": len(group),
            "net_pnl_usdt": pnl.sum(),
            "win_rate": len(wins) / len(group) * 100,
            "profit_factor": profit_factor(pnl),
            "avg_net_pnl": pnl.mean(),
            "max_drawdown": max_drawdown(pnl),
            "max_consec_losses": max_consecutive_losses(group["result"]),
            "avg_win": wins["pnl_usdt"].mean() if not wins.empty else 0.0,
            "avg_loss": losses["pnl_usdt"].mean() if not losses.empty else 0.0,
        })

    return (
        pd.DataFrame(rows)
        .sort_values("net_pnl_usdt", ascending=False)
        .set_index(group_col)
    )


def print_backtest_report(
    all_trades,
    strategy_name,
    trade_size_usdt,
    source_timeframe=None,
    strategy_timeframe=None,
    initial_capital_usdt=None,
):
    if not all_trades:
        print("No trades found.")
        return

    df = pd.DataFrame(all_trades).copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df = df.sort_values("exit_time").reset_index(drop=True)

    pnl = df["pnl_usdt"]
    wins = df[df["result"] == "WIN"]
    losses = df[df["result"] == "LOSS"]
    total_trades = len(df)
    win_rate = len(wins) / total_trades * 100
    gross_total_pnl = (
        trade_size_usdt * (df["gross_pnl_percent"] / 100).sum()
        if "gross_pnl_percent" in df.columns
        else pnl.sum()
    )
    net_total_pnl = pnl.sum()
    fees_estimate = gross_total_pnl - net_total_pnl
    average_win = wins["pnl_usdt"].mean() if not wins.empty else 0.0
    average_loss = losses["pnl_usdt"].mean() if not losses.empty else 0.0
    reward_risk = abs(average_win / average_loss) if average_loss else float("inf")
    if reward_risk == 0:
        breakeven_win_rate = 100.0
    elif reward_risk == float("inf"):
        breakeven_win_rate = 0.0
    else:
        breakeven_win_rate = 1 / (1 + reward_risk) * 100

    print("\n===== BACKTEST SUMMARY =====")
    print(f"Strategy: {strategy_name}")

    if source_timeframe:
        print(f"Source timeframe: {source_timeframe}")

    if strategy_timeframe:
        print(f"Strategy timeframe: {strategy_timeframe}")

    print(f"Total trades: {total_trades}")
    print(f"Wins: {len(wins)}")
    print(f"Losses: {len(losses)}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Breakeven win rate: {breakeven_win_rate:.2f}%")
    print(f"Profit factor: {profit_factor(pnl):.2f}")
    print(f"Gross PnL: {gross_total_pnl:.4f} USDT")
    print(f"Fees estimate: {fees_estimate:.4f} USDT")
    print(f"Net PnL: {net_total_pnl:.4f} USDT")

    if initial_capital_usdt:
        print(f"Initial capital: {initial_capital_usdt:.2f} USDT")
        print(f"Return on capital: {net_total_pnl / initial_capital_usdt * 100:.2f}%")

    print(f"Average net PnL/trade: {pnl.mean():.4f} USDT")
    print(f"Average win: {average_win:.4f} USDT")
    print(f"Average loss: {average_loss:.4f} USDT")
    print(f"Largest win: {pnl.max():.4f} USDT")
    print(f"Largest loss: {pnl.min():.4f} USDT")
    print(f"Max drawdown: {max_drawdown(pnl):.4f} USDT")
    print(f"Max consecutive losses: {max_consecutive_losses(df['result'])}")

    if "side" in df.columns:
        print("\n===== BY SIDE =====")
        print(summarize_group(df, "side").to_string())

    if "regime" in df.columns:
        print("\n===== BY REGIME =====")
        print(summarize_group(df, "regime").to_string())

    print("\n===== BY SYMBOL =====")
    print(summarize_group(df, "symbol").to_string())

    if "exit_reason" in df.columns:
        print("\n===== EXIT REASONS =====")
        print(df["exit_reason"].value_counts())

    print("\n===== BY MONTH =====")
    monthly = (
        df.assign(month=df["exit_time"].dt.to_period("M").astype(str))
        .groupby("month")
        .agg(
            trades=("symbol", "count"),
            net_pnl_usdt=("pnl_usdt", "sum"),
            win_rate=("result", lambda x: (x == "WIN").mean() * 100),
        )
    )
    monthly["equity"] = monthly["net_pnl_usdt"].cumsum()
    print(monthly.to_string())
