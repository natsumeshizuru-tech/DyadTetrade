# A-share Backtrader Guide

## Feed contract

Required columns are `datetime`, `open`, `high`, `low`, `close`, and `volume`. Preserve `amount`, `suspended`, `pre_close`, `adjust_factor`, `period`, and `adjust` alongside the feed for validation and reporting.

## Execution contract

- Signals formed with bar-close information execute no earlier than the next eligible bar.
- Reject fills during suspension or when the intended side is blocked by a price limit.
- Apply board-specific lot and trading rules from versioned configuration.
- Corporate-action-adjusted prices are for research continuity, not live limit prices or order prices.
- Report commission, minimum commission, taxes, transfer fees, slippage, and impact separately.

## Verification

Check feed length, first/last timestamp, order lifecycle, final cash/value, trade count, turnover, drawdown, and deterministic rerun equality. Compare a small sample with a hand calculation before accepting the engine result.
