---
name: cointegration-analysis
description: Test cointegration and mean-reversion hypotheses for A-share, ETF, index, futures, or other traditional-security pairs using local point-in-time data. Trigger for Engle-Granger, Johansen, spread stationarity, hedge ratios, or pairs-research validation. Do not trigger for crypto/DEX pairs, live execution, order placement, or ordinary correlation-only questions.
---

# Cointegration Analysis for Traditional Securities

## Safety Boundary

- Use local, point-in-time, survivorship-aware data.
- Never call an execution venue or produce an executable order.
- Treat statistical evidence as a research hypothesis, not a trading instruction.
- Include realistic two-leg costs, borrow/short constraints, price limits, and suspension risk where applicable.

## Workflow

1. Define the economic relationship and point-in-time universe before testing.
2. Align unadjusted/adjusted conventions, timestamps, currency, and missing sessions.
3. Test integration order; do not apply cointegration tests blindly to stationary levels.
4. Estimate both Engle-Granger directions or a justified Johansen system.
5. Test residual stationarity, half-life, structural breaks, and rolling stability.
6. Split chronologically and freeze the hedge-ratio estimation inside each training window.
7. Evaluate a cost-aware, next-bar simulation with a no-trade random or shuffled baseline.

## Output

Return pair rationale, sample definition, test statistics and critical values, hedge-ratio convention, stability diagnostics, out-of-sample result, costs, and rejection criteria. Read `references/traditional-pairs-guide.md` before using the audited demo scripts.
