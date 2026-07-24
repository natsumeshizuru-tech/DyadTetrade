---
name: xttrader-trading-gateway
description: Design or review an XtTrader gateway lifecycle, session ownership, callbacks, account subscription boundary, order-state mapping, idempotency, queries, and cancellation semantics. Use for gateway code or simulated integration tests only. Do not trigger on market-data, research, or requests to place real orders; default to disconnected mock mode.
---

# XtTrader Trading Gateway

## Non-negotiable safety

- Keep live trading disabled and use mock/paper adapters by default.
- Never accept credentials or a real account in examples, tests, fixtures, or logs.
- Never call a submit/cancel method during diagnostics or automated tests.
- Require `live-trading-release-gate` before any future live connection work.

## Workflow

1. Separate domain requests/events from vendor objects.
2. Give one process exclusive ownership of one unique session.
3. Model lifecycle: created, connecting, connected, subscribed, degraded, stopped.
4. Map account/status/order/trade/position/asset callbacks into redacted domain events.
5. Enforce a client-order idempotency key and explicit order-state transitions.
6. Test synchronous/asynchronous request construction against a fake adapter; assert zero vendor submit/cancel calls.
7. Design reconnect, callback ordering, query reconciliation, and audit logging before live support.

## Output and validation

Return interfaces, state diagrams, callback mapping, failure handling, and mock-test evidence. If any live gate is missing, output `BLOCKED_FOR_LIVE`.

Read [gateway-contract.md](references/gateway-contract.md) and [trigger-tests.md](references/trigger-tests.md).
