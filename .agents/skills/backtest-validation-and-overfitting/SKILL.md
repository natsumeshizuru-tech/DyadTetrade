---
name: backtest-validation-and-overfitting
description: Validate quantitative backtests with time splits, walk-forward, purged folds, embargo, parameter stability, market regimes, cost sensitivity, Monte Carlo, Deflated Sharpe, and random baselines. Use to assess robustness and reproducibility. Do not optimize toward a passing result or approve irreproducible results.
---

# Backtest Validation and Overfitting

## Workflow

1. Freeze strategy, parameters searched, data version, costs, universe, and seed.
2. Keep a final untouched test period; use rolling/expanding walk-forward inside development data.
3. Purge overlapping labels and apply embargo when samples share future return horizons.
4. Evaluate parameter neighborhoods, regimes, boards, liquidity buckets, and cost shocks.
5. Compare with buy-and-hold, cash, shuffled/random signals, and white-noise baselines.
6. Run trade/bootstrap Monte Carlo and report Deflated Sharpe where selection count is known.
7. Re-run from a clean environment and compare hashes/results.

## Output and validation

Return split diagram, all trials, selected rule, untouched-test metrics, stability, costs, baselines, reproducibility evidence, and verdict. If reproduction differs materially, verdict is `FAIL_REPRODUCIBILITY`.

Read [validation-protocol.md](references/validation-protocol.md) and [trigger-tests.md](references/trigger-tests.md).
