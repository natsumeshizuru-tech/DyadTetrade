# XtData reference

Core read-only operations:

| Need | Operation | Guardrail |
|---|---|---|
| History cache | `download_history_data` | retry per symbol; record progress |
| Local bars | `get_market_data_ex` | `fill_data=False`; validate fields |
| Snapshot | `get_full_tick` | cap symbol count and JSON-normalize |
| Calendar | calendar API, then trading-date/index fallback | store source and range |
| Sectors | sector list and members | filter A-share/ETF/index codes |
| Subscription | single/whole quote callback | deduplicate and unsubscribe |

Adjustment values include none/front/back and ratio variants. Keep raw and adjusted datasets distinguishable. A missing first-row `pre_close` is not a valid order-price substitute.
