# Quality contract

Hard errors: empty result, missing key fields, invalid datetime, nonpositive tradable OHLC, impossible high/low, negative volume/amount, broken primary key, and future-dated features.

Warnings: calendar gaps explainable by suspension/listing, missing first-row pre-close, stale latest date, extreme returns requiring corporate-action review, and incomplete universe history. Reports must be deterministic and reference data-file checksum and pipeline version.
