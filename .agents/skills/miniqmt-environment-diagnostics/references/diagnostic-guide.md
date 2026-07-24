# Diagnostic guide

| Layer | Safe check | Failure meaning |
|---|---|---|
| Python | import `xtquant`; record version/ABI | SDK path or binary mismatch |
| Client | MiniQMT process and login state | local service unavailable |
| Data | sector list, trading calendar | XtData service/session failure |
| Files | redacted `userdata_mini` existence/readability | wrong client data root or permissions |
| Session | unique session id and one active owner | collision or stale process |
| Trading | connect-state construction only | configuration incomplete; stop safely |

Do not probe accounts, assets, positions, orders, trades, or callbacks containing private account data. The project gateway serializes XtData because the vendor extension is not thread-safe.
