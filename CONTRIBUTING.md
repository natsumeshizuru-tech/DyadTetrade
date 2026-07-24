# Contributing

Use Python 3.12, install `.[trading,dev]`, and run Ruff, strict mypy, pytest, frontend tests, lint, and build before proposing a change. Changes to OMS, risk, reconciliation, or broker gateways need fail-closed tests.

Live execution must remain disabled by default. Never commit account IDs, order or trade records, QMT caches, vendor binaries, credentials, private databases, logs, or absolute local paths. Public tests must use Paper, mocks, or synthetic market events.
