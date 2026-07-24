---
name: backtrader
description: Build or review offline, event-driven Backtrader research for A shares and traditional securities using local trusted data. Trigger for Cerebro, Strategy, DataFeed, analyzers, order simulation, or Backtrader debugging. Do not trigger for live XtTrader orders, broker account access, VectorBT parameter scans, or fundamental stock research.
---

# Backtrader for DyadTetrade

Use Backtrader as the primary event-driven research engine. Keep research, paper trading, and live gateways separate.

## Safety Boundary

- Read local `trusted` or `bt_ready` data only unless the user explicitly requests a safe data download.
- Never import or call XtTrader from a Backtrader strategy or smoke test.
- Require `ENABLE_LIVE_TRADING=false` for examples and verification.
- Model A-share execution with the `a-share-backtest-realism` skill.
- Do not use cheat-on-open/close unless the user is explicitly testing that semantic and the report labels it.

## Workflow

1. Confirm symbol universe, date range, adjustment convention, signal time, and execution time.
2. Validate OHLCV, duplicate dates, suspensions, missing sessions, and point-in-time universe membership.
3. Build a `PandasData` or custom feed with an Asia/Shanghai trading-day index.
4. Keep indicators in `__init__`, decisions in `next`, and order-state handling in `notify_order`.
5. Configure cash, commission, minimum commission, taxes, lot size, slippage, T+1, price limits, and suspension behavior explicitly.
6. Add analyzers for returns, drawdown, trades, turnover, and risk metrics.
7. Run deterministic tests and compare at least one hand-calculated trade path.

## Output

Return the data contract, strategy assumptions, execution model, parameters, reproducible command, metrics, and limitations. Never describe an offline fill as an executable live order.

Read `references/a-share-backtrader-guide.md` for the project contract. The audited local scripts may be used with `--demo` or local CSV input only.
