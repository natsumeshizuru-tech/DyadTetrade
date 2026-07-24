---
name: vectorbt-setup
description: Inspect and prepare DyadTetrade's existing isolated VectorBT research environment using project dependencies and local data only. Use when VectorBT imports, versions, or local data views need validation. Do not create a new environment, read .env, request API keys, install global software, or configure broker/crypto/foreign-market access.
---

# VectorBT Setup

1. Reuse `.venv`; never create another environment.
2. Check imports and versions for vectorbt, NumPy, Pandas, Numba, DuckDB, and Plotly.
3. Run `pip check` and a tiny in-memory portfolio smoke test.
4. Confirm `config/market_rules.a-share.json` exists and has no unreviewed cost value before cost-sensitive work.
5. Confirm local DuckDB views/Parquet are readable in read-only mode.
6. Report missing dependencies; install only into `.venv` after explicit user authorization.

Output versions, local-data status, profile status, test result, and safe remediation. Read [setup-contract.md](references/setup-contract.md) and [trigger-tests.md](references/trigger-tests.md).
