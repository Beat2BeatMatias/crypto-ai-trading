"""Barrido de geometría (sl_atr_mult × rr) sobre histórico real o sintético.

Usage:
    python sweep.py --timeframe 1h --days 365
    python sweep.py --timeframe 15m --days 120
"""
from __future__ import annotations
import argparse
import pandas as pd
from runner import add_indicators, fetch_history, run_baseline

def run_sweep(
    df: pd.DataFrame,
    *,
    sl_grid: list[float],
    rr_grid: list[float],
    fee: float = 0.001,
) -> list[dict]:
    """Corre run_baseline para cada (sl, rr) y devuelve filas ordenadas por P&L desc."""
    rows: list[dict] = []
    for sl in sl_grid:
        for rr in rr_grid:
            res = run_baseline(df, sl_atr_mult=sl, rr=rr, fee=fee)
            breakeven_wr = 1 / (1 + rr) * 100
            rows.append({
                "sl_atr_mult": sl,
                "rr": rr,
                "n_trades": res.n_trades,
                "win_rate": round(res.win_rate, 2),
                "breakeven_wr": round(breakeven_wr, 1),
                "total_pnl_pct": round(res.total_pnl_pct, 2),
                "sharpe": round(res.sharpe, 2),
                "max_dd_pct": round(res.max_drawdown_pct, 2),
                "profit_factor": round(res.profit_factor, 2),
            })
    rows.sort(key=lambda r: r["total_pnl_pct"], reverse=True)
    return rows

def main() -> None:
    parser = argparse.ArgumentParser(description="Geometry sweep backtest")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--fee", type=float, default=0.001,
                        help="Fee por lado (0.001 = 0.1% taker LIVE). Round-trip = 2×fee.")
    args = parser.parse_args()

    print(f"Fetching {args.symbol} {args.timeframe} {args.days}d...")
    df = add_indicators(fetch_history(args.symbol, args.timeframe, args.days))

    sl_grid = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]
    rr_grid = [1.5, 2.0, 2.5, 3.0]
    rows = run_sweep(df, sl_grid=sl_grid, rr_grid=rr_grid, fee=args.fee)

    print(f"\n{'='*82}")
    print(f"  Geometry sweep — {args.symbol} {args.timeframe} {args.days}d — fee/lado {args.fee*100:.2f}%")
    print(f"{'='*82}")
    hdr = f"  {'SL×ATR':>7} {'R:R':>5} {'N':>5} {'WR%':>7} {'BE%':>6} {'P&L%':>9} {'Sharpe':>7} {'DD%':>8} {'PF':>6}"
    print(hdr)
    print("  " + "-" * 78)
    for r in rows:
        edge = "✅" if (r["win_rate"] > r["breakeven_wr"] and r["profit_factor"] > 1.2) else "  "
        print(f"  {r['sl_atr_mult']:>7} {r['rr']:>5} {r['n_trades']:>5} "
              f"{r['win_rate']:>7} {r['breakeven_wr']:>6} {r['total_pnl_pct']:>9} "
              f"{r['sharpe']:>7} {r['max_dd_pct']:>8} {r['profit_factor']:>6} {edge}")
    print(f"\n  Mejor combinación: SL={rows[0]['sl_atr_mult']}×ATR R:R={rows[0]['rr']} "
          f"→ P&L {rows[0]['total_pnl_pct']}% PF {rows[0]['profit_factor']}")
    print("  (Anotá la mejor combinación con ✅ y PF>1.3 para aplicar en DB.)")

if __name__ == "__main__":
    main()
