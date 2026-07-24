---
name: quant-data-quality
description: Validate quantitative datasets for calendar coverage, empty/duplicate/missing fields, OHLC anomalies, extremes, adjustment consistency, suspension, point-in-time universes, look-ahead, versioning, and lineage. Use for every data ingestion or migration. Do not use to generate signals or trades.
---

# Quant Data Quality

## Workflow

1. Record source, requested range, expected calendar, symbols, schema, adjustment, and version.
2. Check empty datasets, duplicates, required fields, types, ordering, OHLC relationships, nonnegative volume/amount, and extremes.
3. Reconcile expected trading days while distinguishing suspension, pre-listing, and post-delisting periods.
4. Check latest-day coverage, adjustment consistency, corporate actions, point-in-time universe membership, and feature availability timestamps.
5. Preserve raw data; quarantine errors; promote only validated rows.
6. Generate JSON and CSV reports and persist issue metadata and lineage.

## Output and validation

Every task must output counts, coverage, findings by severity, quarantined rows, report paths, source hash/version, and pass/fail. Missing report means failure.

Read [quality-contract.md](references/quality-contract.md) and [trigger-tests.md](references/trigger-tests.md).
