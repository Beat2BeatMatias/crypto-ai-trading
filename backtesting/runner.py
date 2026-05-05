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


@dataclass
class BacktestResult:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    total_pnl_pct: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: float = 0.0
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
    close = df["close"]
    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["rsi"] = 100 - (100 / (1 + rs))
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    # EMAs
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()
    # ATR
    prev_close = close.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(com=13, min_periods=14).mean()
    df["volume_avg"] = df["volume"].rolling(20).mean()
    # MACD crossover (histogram changes from negative to positive)
    df["macd_cross_up"] = (df["macd_hist"] > 0) & (df["macd_hist"].shift(1) <= 0)
    # MACD momentum building: histogram positive and growing
    df["macd_building"] = (df["macd_hist"] > 0) & (df["macd_hist"] > df["macd_hist"].shift(1))
    # EMA200 slope: positive means long-term trend is up (20-candle lookback)
    df["ema200_slope_pct"] = (df["ema200"] - df["ema200"].shift(20)) / df["ema200"].shift(20) * 100
    return df


def signal_buy(row: pd.Series) -> bool:
    """Relaxed trend-following entry: 3 of 4 soft confluences + EMA hard filter.

    Hard filters (mandatory):
      - EMA20 > EMA50 > EMA200: confirmed uptrend
      - Price between EMA50 and EMA20*1.03: pullback zone (not chasing, not crashed)

    Soft confluences (need >= 3 of 4):
      1. RSI 38-68: broad momentum range, excludes extremes
      2. MACD histogram > 0: bullish momentum present
      3. Bullish candle (close > open)
      4. Volume >= 1.1x average
    """
    if any(pd.isna([row["ema20"], row["ema50"], row["ema200"], row["rsi"],
                    row["macd_hist"], row["volume_avg"], row["open"],
                    row["ema200_slope_pct"]])):
        return False
    # Hard filter 1: confirmed uptrend (EMAs stacked)
    if not (row["ema20"] > row["ema50"] > row["ema200"]):
        return False
    # Hard filter 2: EMA200 slope positive (long-term trend still rising, not topping)
    if row["ema200_slope_pct"] <= 0:
        return False
    # Hard filter 3: price in pullback zone (between EMA50 and EMA20*1.03)
    if row["close"] > row["ema20"] * 1.03:
        return False
    if row["close"] < row["ema50"]:
        return False
    # Soft confluences
    confluences = 0
    if 38 <= row["rsi"] <= 68:
        confluences += 1
    if row["macd_hist"] > 0:
        confluences += 1
    if row["close"] > row["open"]:
        confluences += 1
    if row["volume"] >= 1.1 * row["volume_avg"]:
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
    gross_profit = float(pnl[pnl > 0].sum()) if wins > 0 else 0.0
    gross_loss = float(pnl[pnl <= 0].abs().sum()) if losses > 0 else 1e-9
    profit_factor = gross_profit / gross_loss

    return BacktestResult(
        n_trades=len(trades), n_wins=wins, n_losses=losses,
        win_rate=wins / len(trades) * 100,
        total_pnl_pct=float(cum.iloc[-1] - 1) * 100,
        sharpe=sharpe, max_drawdown_pct=max_dd,
        profit_factor=profit_factor, trades=trades,
    )


def diagnose(df: pd.DataFrame) -> None:
    df = df.dropna()
    total = len(df)
    ema_full = (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema200"])
    slope_ok = ema_full & (df["ema200_slope_pct"] > 0)
    zone = slope_ok & (df["close"] > df["ema50"]) & (df["close"] <= df["ema20"] * 1.03)
    # Soft confluences >= 3 of 4
    c1 = (df["rsi"] >= 38) & (df["rsi"] <= 68)
    c2 = df["macd_hist"] > 0
    c3 = df["close"] > df["open"]
    c4 = df["volume"] >= 1.1 * df["volume_avg"]
    conf_count = c1.astype(int) + c2.astype(int) + c3.astype(int) + c4.astype(int)
    signals = zone & (conf_count >= 3)
    print(f"\n  Diagnóstico de filtros (velas):")
    print(f"    Total velas:              {total}")
    print(f"    EMA stacked (uptrend):    {ema_full.sum()} ({ema_full.mean()*100:.0f}%)")
    print(f"    + EMA200 slope > 0:       {slope_ok.sum()} ({slope_ok.mean()*100:.0f}%)")
    print(f"    + Precio en zona EMA:     {zone.sum()} ({zone.mean()*100:.0f}%)")
    print(f"    + 3/4 confluencias:       {signals.sum()} ({signals.mean()*100:.1f}%)")
    print(f"      (RSI 38-68: {(zone & c1).sum()}, MACD>0: {(zone & c2).sum()}, "
          f"Vela alcista: {(zone & c3).sum()}, Vol 1.1x: {(zone & c4).sum()})")
    df2 = df.copy()
    df2["signal"] = signals
    df2["year_month"] = df2.index.to_period("M")
    monthly = df2.groupby("year_month")["signal"].sum()
    print(f"\n  Señales por mes:")
    for period, count in monthly.items():
        if count > 0:
            print(f"    {period}: {int(count)} señales")


def main() -> None:
    parser = argparse.ArgumentParser(description="Indicator-only baseline backtest")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--sl-atr-mult", type=float, default=1.0)
    parser.add_argument("--rr", type=float, default=2.5)
    parser.add_argument("--diagnose", action="store_true", help="Show filter breakdown")
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
    print(f"  Profit factor: {res.profit_factor:.2f}")
    print(f"{'='*50}")

    # Gate dinámico según R:R — breakeven WR = 1/(1+rr), target = breakeven + 8%
    wr_min = round(1 / (1 + args.rr) * 100 + 8, 1)
    wr_ok = res.win_rate > wr_min
    sharpe_ok = res.sharpe > 0.5
    dd_ok = abs(res.max_drawdown_pct) < 15
    pf_ok = res.profit_factor > 1.2
    ok = wr_ok and sharpe_ok and dd_ok and pf_ok
    print(f"\n  Gate (R:R={args.rr}): WR>{wr_min}%({'✅' if wr_ok else '❌'}) "
          f"Sharpe>0.5({'✅' if sharpe_ok else '❌'}) "
          f"DD<15%({'✅' if dd_ok else '❌'}) PF>1.2({'✅' if pf_ok else '❌'})")
    print(f"  Resultado: {'✅ PASS' if ok else '❌ FAIL'}")
    print(f"  (Breakeven matemático con R:R {args.rr}: {1/(1+args.rr)*100:.1f}% WR)")

    if args.diagnose:
        diagnose(df)


if __name__ == "__main__":
    main()
