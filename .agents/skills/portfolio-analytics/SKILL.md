---
name: portfolio-analytics
description: Analyze local A-share or traditional-security backtest and paper-trading returns, equity curves, drawdowns, exposure, turnover, and benchmark-relative performance. Trigger for portfolio metrics or strategy comparisons. Do not trigger for broker account queries, live positions, order placement, crypto analytics, or single-company fundamental research.
---

# Portfolio Analytics for DyadTetrade

## Safety Boundary

- Accept local backtest or paper-trading results only.
- Never request broker credentials, query a live account, or send orders.
- Keep Backtrader and VectorBT engine labels separate in every comparison.
- Use a configurable A-share trading calendar and benchmark; do not assume 365-day markets.

## Workflow

1. Validate timestamps, currency, valuation frequency, cash flows, and missing observations.
2. Distinguish prices, equity values, simple returns, and log returns.
3. Calculate total and annualized return, volatility, Sharpe, Sortino, Calmar, maximum drawdown, and drawdown duration.
4. Add Alpha, Beta, information ratio, turnover, transaction cost, concentration, and industry exposure when inputs exist.
5. Align the benchmark and risk-free convention explicitly.
6. Reconcile summary metrics against daily return and transaction ledgers.

## Output

Return a metric table, data period, benchmark, annualization convention, cost assumptions, engine, caveats, and reproducible input path. Use `references/a-share-portfolio-metrics.md` for definitions.
