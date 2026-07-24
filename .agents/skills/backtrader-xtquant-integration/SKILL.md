---
name: backtrader-xtquant-integration
description: Integrate trusted XtData bars with Backtrader data feeds and simulation brokers, including time zones, calendars, suspension, limits, missing data, corporate actions, and order/fill mapping. Use for research or paper backtests. Do not connect Backtrader directly to XtTrader or submit live orders.
---

# Backtrader XtQuant Integration

## Architecture

Keep three modes separate: research reads `bt_ready` Parquet; simulation uses a deterministic broker; future live mode emits intents through Risk/OMS/Gateway only. Backtrader never owns XtTrader.

## Workflow

1. Read trusted/bt_ready data with explicit adjustment and lineage.
2. Map datetime, OHLCV, amount, suspension, limits, and corporate-action flags to a custom feed.
3. Use Asia/Shanghai trading dates; do not forward-fill suspended bars as tradable.
4. Configure an A-share simulation broker through `a-share-backtest-realism`.
5. Map simulated order statuses and fills into project domain objects.
6. Test missing dates, suspension, limit lock, T+1, lot size, fees, and next-bar timing.
7. Compare against a buy-and-hold baseline and produce reproducible parameters.

## Output and validation

Return feed schema, broker assumptions, run mode, tests, and metrics. Fail if code imports XtTrader or uses same-bar close information for an earlier fill.

Read [integration-contract.md](references/integration-contract.md) and [trigger-tests.md](references/trigger-tests.md).
