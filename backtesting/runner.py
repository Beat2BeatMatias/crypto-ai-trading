"""Indicator-only backtest baseline — no LLM cost.

Applies the v0 playbook rules deterministically against historical OHLCV data
to establish a performance baseline before paying for LLM calls.

Usage:
    python runner.py --days 30
    python runner.py --days 90 --sl-atr-mult 1.2 --rr 2.5
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import ccxt
import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass
class BacktestResult:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    total_pnl_pct: float
    sharpe: float
    max_drawdown_pct: float
    trades: list[dict] = field(default_factory=list)


def fetch_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Fetch historical OHLCV from Binance (no auth required for public data)."""
    ex = ccxt.binance()
    since = ex.parse8601(
        (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    )
    all_rows: list[list] = []
    while True:
        rows = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not rows:
            break
        all_rows.extend(rows)
        since = rows[-1][0] + 1
        if len(rows) < 1000:
            break
    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd.iloc[:, 0]
        df["macd_signal"] = macd.iloc[:, 1]
        df["macd_hist"] = macd.iloc[:, 2]
    df["ema20"] = ta.ema(df["close"], length=20)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["volume_avg"] = df["volume"].rolling(20).mean()
    return df


def signal_buy(row: pd.Series) -> bool:
    """Returns True if >= 3 confluences from the v0 playbook are present."""
    if any(pd.isna([row["ema20"], row["ema50"], row["ema200"], row["rsi"]])):
        return False
    confluences = 0
    # Confluence 1: bullish EMA alignment
    if row["ema20"] > row["ema50"] > row["ema200"]:
        confluences += 1
    # Confluence 2: RSI oversold rebound
    if row["rsi"] < 35:
        confluences += 1
    # Confluence 3: MACD bullish cross
    if "macd_hist" in row.index and not pd.isna(row["macd_hist"]) and row["macd_hist"] > 0:
        confluences += 1
    # Confluence 4: volume confirmation
    if not pd.isna(row["volume_avg"]) and row["volume"] > 1.3 * row["volume_avg"]:
        confluences += 1
    return confluences >= 3


def run_baseline(
    df: pd.DataFrame, *, sl_atr_mult: float = 1.0, rr: float = 2.0, fee: float = 0.001,
) -> BacktestResult:
    df = df.dropna()
    in_pos = False
    entry = sl = tp = 0.0
    entry_ts: pd.Timestamp | None = None
    trades: list[dict] = []

    for ts, row in df.iterrows():
        if not in_pos:
            if signal_buy(row) and not pd.isna(row["atr"]):
                entry = float(row["close"])
                sl = entry - sl_atr_mult * float(row["atr"])
                tp = entry + rr * sl_atr_mult * float(row["atr"])
                in_pos = True
                entry_ts = ts
        else:
            high = float(row["high"])
            low = float(row["low"])
            exit_price = exit_reason = None
            if low <= sl:
                exit_price, exit_reason = sl, "stop_loss"
            elif high >= tp:
                exit_price, exit_reason = tp, "take_profit"
            if exit_price is not None:
                pnl_pct = (exit_price - entry) / entry - 2 * fee
                trades.append({
                    "entry_ts": entry_ts, "exit_ts": ts,
                    "entry": entry, "exit": exit_price,
                    "pnl_pct": pnl_pct * 100, "reason": exit_reason,
                })
                in_pos = False

    if not trades:
        return BacktestResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0)

    pnl = pd.Series([t["pnl_pct"] / 100 for t in trades])
    wins = int((pnl > 0).sum())
    losses = int((pnl <= 0).sum())
    cum = (1 + pnl).cumprod()
    max_dd = float((cum / cum.cummax() - 1).min() * 100)
    sharpe = float(pnl.mean() / (pnl.std() + 1e-9) * np.sqrt(252))

    return BacktestResult(
        n_trades=len(trades), n_wins=wins, n_losses=losses,
        win_rate=wins / len(trades) * 100,
        total_pnl_pct=float(cum.iloc[-1] - 1) * 100,
        sharpe=sharpe, max_drawdown_pct=max_dd, trades=trades,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Indicator-only baseline backtest")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--sl-atr-mult", type=float, default=1.0)
    parser.add_argument("--rr", type=float, default=2.0)
    args = parser.parse_args()

    print(f"Fetching {args.symbol} {args.timeframe} {args.days}d from Binance...")
    df = fetch_history(args.symbol, args.timeframe, args.days)
    df = add_indicators(df)

    res = run_baseline(df, sl_atr_mult=args.sl_atr_mult, rr=args.rr)

    print(f"\n{'='*50}")
    print(f"  Backtest: {args.symbol} {args.timeframe} {args.days}d")
    print(f"  SL={args.sl_atr_mult}×ATR | R:R={args.rr}:1")
    print(f"{'='*50}")
    print(f"  Trades:        {res.n_trades}")
    print(f"  Wins:          {res.n_wins}  Losses: {res.n_losses}")
    print(f"  Win rate:      {res.win_rate:.2f}%")
    print(f"  Total P&L:     {res.total_pnl_pct:+.2f}%")
    print(f"  Sharpe (ann):  {res.sharpe:.2f}")
    print(f"  Max drawdown:  {res.max_drawdown_pct:.2f}%")
    print(f"{'='*50}")

    # Gate check against spec §11
    ok = res.win_rate > 52 and res.sharpe > 1.0 and abs(res.max_drawdown_pct) < 5
    print(f"\n  Gate (WR>52%, Sharpe>1.0, DD<5%): {'✅ PASS' if ok else '❌ FAIL'}")


if __name__ == "__main__":
    main()
