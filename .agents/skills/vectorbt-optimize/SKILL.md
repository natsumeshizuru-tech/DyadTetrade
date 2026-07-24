---
name: vectorbt-optimize
description: Perform bounded VectorBT parameter scans on local A-share data with declared search spaces, train-only selection, cost sensitivity, stability surfaces, and complete trial reporting. Use for fast research screening. Do not optimize on final test data, hide failed trials, fetch external data, or produce live orders.
---

# VectorBT Optimize

1. Declare parameter grid, objective, constraints, seed, compute budget, and untouched test before running.
2. Use local data and the dated A-share profile only.
3. Select on training/walk-forward segments; never on final test.
4. Retain every trial, error, trade count, turnover, drawdown, gross/net metric, and runtime.
5. Plot parameter stability, not only the maximum cell.
6. Stress costs and validate the selected neighborhood with `backtest-validation-and-overfitting`.

Output full trial data, selection rule, stability, final untouched result, and limitations. Read [optimization-contract.md](references/optimization-contract.md) and [trigger-tests.md](references/trigger-tests.md).
