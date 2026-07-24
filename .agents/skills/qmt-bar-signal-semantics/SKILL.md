---
name: qmt-bar-signal-semantics
description: Review QMT strategy timing semantics around init, handlebar, ContextInfo, latest versus historical bars, tick driving, quickTrade, and next-bar execution. Use to prevent future leakage and reconcile backtest/live timing. Do not use for order execution or generic indicator research.
---

# QMT Bar Signal Semantics

## Workflow

1. Label every input timestamp, bar-close time, signal time, decision time, submission time, and assumed fill time.
2. Separate initialization from per-bar logic and distinguish historical replay from the current live bar.
3. Treat an unfinished latest bar as mutable; do not compute a confirmed close signal from it.
4. Explain tick-driven callback frequency and deduplicate repeated decisions for one bar.
5. Keep `quickTrade` disabled unless its exact timing has a documented test.
6. Execute close-derived signals no earlier than the next eligible event/bar under the selected model.
7. Add future-function tests by shifting data availability and checking invariant results.

## Output and validation

Return a timing table and evidence that no field becomes visible before its timestamp. Any ambiguous same-bar execution fails validation.

Read [timing-model.md](references/timing-model.md) and [trigger-tests.md](references/trigger-tests.md).
