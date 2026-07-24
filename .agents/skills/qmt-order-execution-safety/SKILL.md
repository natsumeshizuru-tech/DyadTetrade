---
name: qmt-order-execution-safety
description: Specify and review QMT execution guardrails for trigger prices, pacing, reference quotes, price bands, quantities, cancellations, validity, retry ceilings, suspension, limits, rejects, duplicates, and loss caps. Use for simulation design and safety review. Do not execute or authorize real orders.
---

# QMT Order Execution Safety

## Workflow

1. Require total quantity, total notional, per-order quantity, per-symbol exposure, daily loss, and retry ceilings.
2. Define trigger source, quote freshness, price reference, permitted deviation, price-limit behavior, and invalid-price handling.
3. Define minimum order/cancel interval, maximum attempts, validity window, and treatment of unfilled orders.
4. Reject suspension, limit-lock, stale quote, lot-size violation, insufficient capacity, duplicate idempotency key, or unknown prior state.
5. Simulate partial fills, rejects, disconnects, late callbacks, and restart recovery.
6. Produce rules and tests only; never call a broker or vendor method.

## Output and validation

Return a machine-readable guardrail table, state transitions, failure cases, and unresolved limits. Any missing hard limit is a failed review.

Read [execution-checklist.md](references/execution-checklist.md) and [trigger-tests.md](references/trigger-tests.md).
