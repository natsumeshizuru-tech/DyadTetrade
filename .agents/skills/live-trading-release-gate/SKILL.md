---
name: live-trading-release-gate
description: Audit readiness for future live trading across dry-run, paper, staged rollout, dual confirmation, allowlists, limits, circuit breakers, kill switch, heartbeat, recovery, reconciliation, alerts, and audit logs. Trigger only for explicit release-readiness reviews. Never enable live trading, accept credentials, connect an account, or send/cancel orders.
---

# Live Trading Release Gate

## Default verdict

Return `BLOCKED` unless every mandatory control has current evidence. This Skill is an auditor, not an enabler.

## Gates

1. Configuration: live false by default, two independent confirmations, account allowlist, redacted secrets, environment separation.
2. Validation: deterministic backtest, paper trading, fault injection, small staged plan, rollback owner.
3. Limits: per-order, per-symbol, gross/net, daily loss, drawdown, retries, rate, and stale-quote limits.
4. Controls: pre-trade risk, circuit breaker, kill switch, heartbeat, disconnect handling, restart recovery, idempotency.
5. Operations: reconciliation, alerts, immutable audit log, clock sync, runbook, incident drill, human owner.
6. Evidence: dated test results, approvals, configuration hash, and unresolved issues.

## Output and validation

Return gate-by-gate `PASS/FAIL/MISSING`, evidence, owner, expiry, blockers, and final verdict. Never change configuration or perform a connection test with a real account.

Read [release-checklist.md](references/release-checklist.md) and [trigger-tests.md](references/trigger-tests.md).
