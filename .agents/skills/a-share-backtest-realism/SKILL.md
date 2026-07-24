---
name: a-share-backtest-realism
description: Apply China A-share backtest realism for T+1, board-specific lots and limits, suspension, fees, slippage, corporate actions, delisting, survivorship, and signal/fill separation. Use when building or reviewing A-share backtests. Do not use as current legal/fee advice or for live order execution.
---

# A-Share Backtest Realism

## Workflow

1. Load a dated, configurable market profile; verify current exchange and broker rules before changing defaults.
2. Enforce settlement/availability, board and instrument type, lot rules, price limits, suspension, and valid trading dates.
3. Model commission, minimum commission, stamp duty, transfer fee, spread, slippage, and impact separately.
4. Use point-in-time universes and constituents; retain delisted securities and corporate actions.
5. Separate signal timestamp from order eligibility and fill timestamp.
6. Run zero-cost, base-cost, and stressed-cost scenarios plus data-availability checks.

## Output and validation

Return a dated assumptions table, exceptions, sensitivity results, and evidence against look-ahead/survivorship bias. Unknown rule values must be configurable and marked unverified.

Read [market-rules.md](references/market-rules.md) and [trigger-tests.md](references/trigger-tests.md).
