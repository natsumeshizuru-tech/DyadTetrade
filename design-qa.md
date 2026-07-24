# DyadTetrade Trading Terminal Design QA

## Scope

- Reference: public TradingView chart workspace captured at 1280x720 and 390x844.
- Implementation: DyadTetrade trading terminal using the same viewport sizes.
- Branding boundary: DyadTetrade name and logo retained; TradingView trademarks, logos, illustrations, and proprietary assets were not copied.
- Icon replacement: Lucide React line icons are used as the closest maintainable open-source equivalent.

## Evidence

- `reports/design/tradingview-source-desktop.png`
- `reports/design/dyadtetrade-terminal-desktop.png`
- `reports/design/desktop-comparison.png`
- `reports/design/tradingview-source-mobile.png`
- `reports/design/dyadtetrade-terminal-mobile.png`
- `reports/design/mobile-comparison.png`
- `reports/design/tradingview-reference.png`
- `reports/design/dyadtetrade-market-workspace.png`
- `reports/design/tradingview-comparison.png`

## Unified market, strategy, research and system workspaces

- Passed: the four modules now reuse one terminal composition: compact top toolbar, left tool rail,
  central work canvas, right context panel and bottom task/status panel.
- Passed: the market workspace uses the backend candle-stitching API rather than concatenating history
  and realtime arrays in the browser. It shows the adjust mode, historical endpoint, realtime endpoint,
  gap state and backfill state alongside the chart.
- Passed: the strategy workspace keeps file selection, code editing, validation, smoke testing, publish,
  start/stop and heartbeat monitoring in one screen.
- Passed: research capabilities remain grouped under backtest, factor, optimization, portfolio and reports;
  inactive capabilities are not populated with fake data.
- Passed: the system workspace exposes backend, XtData, single Trading Engine, QMT subscription, market
  phase, calendar source, execution mode, Kill Switch, data freshness and download tasks without exposing
  account identifiers or local private paths.
- Passed: direct visual comparison at 800x470 preserves the reference hierarchy and density while removing
  drawing, social, news and discretionary-trading controls that DyadTetrade does not use.
- Intentional difference: DyadTetrade keeps only quant-operational controls and real MiniQMT/API state;
  TradingView trademarks, proprietary assets and consumer-market content are not copied.

## Visual review

### Desktop 1280x720

- Passed: fixed-viewport shell with no document overflow.
- Passed: compact top command bars, 52px drawing rail, central K-line/volume workspace, approximately 345px right sidebar, and collapsed bottom trade-information bar.
- Passed: white canvas, subtle grey grid/borders, compact typography, green/red market colors, blue active controls, dense watchlist rows, and right-aligned price scale.
- Intentional difference: the product-level five-entry DyadTetrade navigation remains above the chart toolbar because it is required for the existing trading, market, strategy, research, and system workspaces.
- Intentional difference: displayed symbols, prices, account state, and strategy state come from DyadTetrade and miniQMT rather than source-site demonstration data.

### Mobile 390x844

- Passed: right sidebar is removed, the top toolbar is reduced to the primary period controls, and the left drawing rail remains usable.
- Passed: viewport and document dimensions are both 390x844; no horizontal or vertical document overflow was observed in the default collapsed state.
- Passed: chart, price scale, volume, range controls, and bottom trade tabs stay visible without cropped primary actions.

## Functional review

- Passed: historical daily bars load from bt_ready data and the current simulated daily K-line is updated from miniQMT ticks.
- Passed: symbol search, period switching, watchlist selection, and the chart range/zoom surface render from live APIs.
- Passed: QMT mode, connection, subscription, market freshness, and risk state are visible once and update by polling.
- Passed: positions, orders, trades, delivery statements, and strategy runtime details are backed by real API responses.
- Passed: delivery statement date filtering and CSV export are available; full account identifiers are not exposed.
- Passed: strategy detail shows heartbeat, parameters, state, logs, associated orders/trades, and risk events; live-order capability remains false.
- Passed: bottom trading details expand on demand and collapse to restore the chart-first workspace.
- Passed: qmt_sim order controls are mode-gated; no order was submitted during design QA.
- Passed: browser verification found no new console errors after the ECharts ESM import correction.

## Automated verification

- Backend: 157 pytest tests passed; total coverage 80%.
- Static checks: Ruff, Ruff format, strict mypy, dependency boundary, Alembic upgrade/check, and pip check passed.
- Frontend: 4 Vitest tests, ESLint, TypeScript compilation, and Vite production build passed.
- Runtime: qmt_sim Trading Engine connected, subscribed, reconciled, and entered NORMAL; XtData returned
  6,972 sectors and pre-open checks passed without sending an order.
- Calendar: MiniQMT calendar persisted 4,011 SH, 4,011 SZ and 2,257 BJ dates; weekend rows were 0 and
  the system page reported `sqlite_xtquant` as the active calendar source.
- Safety: `ENABLE_LIVE_TRADING=false` remained enforced throughout validation.

final result: passed
