---
name: xtdata-market-data
description: Build or diagnose XtData read-only market-data workflows for history download, incremental cache updates, local reads, subscriptions, calendars, sectors, universes, adjustment semantics, and completeness checks. Use for MiniQMT market data tasks. Do not use for XtTrader, account data, orders, or trading-price decisions.
---

# XtData Market Data

## Safety boundary

- Treat XtData as read-only and serialize all calls through `XtQuantGateway`.
- Never use adjusted prices as executable order prices.
- Never add XtTrader or account access to a market-data task.

## Workflow

1. Define symbols, period, date range, adjustment type, fields, expected calendar, and data layer.
2. Normalize codes to `000001.SZ`, `600519.SH`, or `430047.BJ`.
3. Prefer incremental vendor-cache download, then local `get_market_data_ex` read.
4. Choose single-symbol subscriptions for narrow use; use whole-market push only when capacity and throttling are explicit.
5. Normalize time, OHLCV, amount, suspension, `pre_close`, adjustment mode, and lineage.
6. Run `quant-data-quality`; do not promote bad rows to trusted data.
7. Persist idempotently and report coverage, missing days, retries, and failures.

## Output and validation

Return source parameters, data contract, files/views written, coverage, and quality-report paths. Pass when repeated execution creates no duplicate key and the latest expected date is explained.

Read [xtdata-reference.md](references/xtdata-reference.md) and [trigger-tests.md](references/trigger-tests.md).
