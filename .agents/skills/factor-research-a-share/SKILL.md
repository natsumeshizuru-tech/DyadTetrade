---
name: factor-research-a-share
description: Research A-share cross-sectional factors with point-in-time definitions, winsorization, standardization, industry/size neutralization, IC, RankIC, quantiles, turnover, costs, Fama-MacBeth, Newey-West, multiple testing, out-of-sample validation, crowding, and decay. Use for factor studies, not single-stock advice or order generation.
---

# Factor Research A Share

## Workflow

1. Write a timestamped factor definition, economic hypothesis, universe, lag, frequency, and exclusions before computing results.
2. Use point-in-time fundamentals and constituents; lag publication data and prevent revised values from leaking backward.
3. Winsorize, standardize, and neutralize by industry/size with train-only parameters.
4. Report IC/RankIC series, quantile monotonicity, long-short returns, turnover, costs, coverage, and capacity.
5. Use Fama-MacBeth/Newey-West where appropriate and correct for multiple testing.
6. Validate out of sample across periods, boards, liquidity regimes, and cost scenarios.
7. Report all planned parameter variants, not only the best.

## Output and validation

Return definition, lineage, preprocessing, statistics, robustness, costs, rejected variants, and reproducibility seed/version. A factor with no out-of-sample evidence cannot pass.

Read [factor-protocol.md](references/factor-protocol.md) and [trigger-tests.md](references/trigger-tests.md).
