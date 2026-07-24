---
name: vectorbt-backtest
description: Run fast VectorBT research backtests on DyadTetrade local trusted/bt_ready A-share data with explicit timing, costs, benchmark, and configuration. Use for vectorized signal validation and parameter screening. Do not fetch external data, read secrets, use foreign-market defaults, or connect to XtTrader.
---

# VectorBT Backtest

1. Load `config/market_rules.a-share.json`; stop if required cost/rule values are unreviewed for the requested conclusion.
2. Read local DuckDB/Parquet only and record adjustment, dates, symbols, and data hash.
3. Build signals without forward fill across suspension and shift close-derived entries to the configured next event.
4. Apply configurable lot, T+1 approximation, limits, fees, minimum fee, taxes, slippage, and benchmark.
5. Print gross/net statistics and export engine-labelled results.
6. Run `quant-data-quality`, `a-share-backtest-realism`, and `backtest-validation-and-overfitting` gates.

Never compare directly with Backtrader unless assumptions are identical. Read [backtest-contract.md](references/backtest-contract.md) and [trigger-tests.md](references/trigger-tests.md).
