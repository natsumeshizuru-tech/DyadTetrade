---
name: quant-risk-performance-reporting
description: Produce reproducible quantitative risk and performance reports covering returns, volatility, Sharpe, Sortino, Calmar, drawdown, alpha/beta, information ratio, trade statistics, turnover, capacity, costs, concentration, and industry exposure. Use after a backtest or simulation, not to place trades or make unsupported recommendations.
---

# Quant Risk Performance Reporting

## Workflow

1. Validate return frequency, annualization, benchmark, risk-free convention, cash flows, costs, and date alignment.
2. Compute total/annual return, volatility, Sharpe, Sortino, Calmar, max drawdown, duration, recovery, alpha, beta, and information ratio.
3. Compute win rate, payoff ratio, profit factor, holding period, turnover, slippage, fees, and capacity.
4. Report position concentration, sector/industry exposure, tail loss, and rolling metrics.
5. Separate gross/net, in/out-of-sample, and Backtrader/VectorBT results.
6. Generate structured JSON plus optional HTML/PDF with parameters, data hash, warnings, and source tables.

## Output and validation

Pass only if metric definitions, periods, benchmark, costs, and missing-data treatment are explicit and results reproduce from source tables.

Read [report-contract.md](references/report-contract.md) and [trigger-tests.md](references/trigger-tests.md).
