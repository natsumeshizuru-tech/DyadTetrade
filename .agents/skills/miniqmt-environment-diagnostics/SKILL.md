---
name: miniqmt-environment-diagnostics
description: Diagnose local MiniQMT and XtQuant availability, Python compatibility, userdata_mini selection, permissions, ports, sessions, and read-only market/trader connectivity. Use for connection, import, startup, path, or session-conflict failures. Do not use to submit, cancel, simulate, or recommend orders.
---

# MiniQMT Environment Diagnostics

## Safety boundary

- Keep `DYADTETRADE_ENABLE_LIVE_TRADING=false`.
- Never request or print an account ID, password, token, private MiniQMT path, or `.env` value.
- Use import checks, process checks, sector/calendar reads, and connection status only. Never call an order API.

## Inputs

Collect the symptom, Python version, XtQuant import result, whether MiniQMT is started and logged in, expected mode, and a redacted path shape. Accept no credentials.

## Workflow

1. Confirm Windows, Python 3.12, architecture, virtual environment, and `xtquant.__file__` without exposing private paths in the report.
2. Confirm MiniQMT is running, logged in, and using the intended minimal/normal mode.
3. Validate that a `userdata_mini` directory exists and is readable; report only basename and existence.
4. Run read-only `XtQuantGateway.status()`, sector-list, and short calendar checks.
5. Diagnose permission, client-version, port, duplicate session, stale process, and Python ABI conflicts in that order.
6. If trading connectivity is specifically requested, validate construction and connection state only; do not subscribe an account or place/cancel an order.
7. Return a redacted checklist, evidence, likely cause, and safe next action.

## Output and validation

Output `Environment`, `Read-only probes`, `Findings`, `Remediation`, and `Not executed`. Pass only when import and requested read-only probes succeed. Missing trading configuration must fail closed.

Read [diagnostic-guide.md](references/diagnostic-guide.md) for checks and [trigger-tests.md](references/trigger-tests.md) for routing tests.
