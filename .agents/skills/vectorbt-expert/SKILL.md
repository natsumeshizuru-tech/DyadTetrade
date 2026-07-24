---
name: vectorbt-expert
description: Design and review VectorBT research architecture, array alignment, broadcasting, portfolio semantics, performance, and parity checks for DyadTetrade. Use for advanced vectorized research questions. Do not replace Backtrader as the event-driven engine, mix engine metrics, use foreign defaults, or access live trading.
---

# VectorBT Expert

## Engine boundary

Use VectorBT for fast vectorized research and parameter surfaces. Use Backtrader for event-driven order/fill behavior. Label every result with its engine and never merge metrics across unequal assumptions.

## Workflow

1. Review index alignment, NaN handling, broadcasting shape, signal timing, grouping, cash sharing, and memory use.
2. Load only local data and the A-share profile.
3. Make fee, calendar, benchmark, limits, lot size, T+1 approximation, slippage, and data source configurable.
4. Add small deterministic parity cases before scaling.
5. Escalate finalists to Backtrader with matched assumptions.

Read [engine-boundary.md](references/engine-boundary.md) and [trigger-tests.md](references/trigger-tests.md).
