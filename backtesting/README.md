# Backtesting

Baseline indicator-only backtest — no LLM cost, fast validation of v0 playbook rules.

## Usage

```bash
cd backtesting
pip install -r requirements.txt
python runner.py --days 30
python runner.py --days 90 --sl-atr-mult 1.2 --rr 2.5
```

## Metrics reported

- Trades executed / Win rate / Profit factor
- Total P&L % / Sharpe ratio (annualised)
- Max drawdown %

## Walk-forward note

Default split: full period (no holdout yet). Always validate on out-of-sample data
before going live. Paper trading (Phase 6 of the plan) is the required gate.
