# Dependency licenses

The public package deliberately declares only its trading runtime and development dependencies. Historical research extras were removed from the public package boundary and retained only in a private migration requirements file.

| Dependency group | License decision |
| --- | --- |
| Pydantic, SQLAlchemy, aiosqlite, jsonschema, PyYAML | Permissive OSI-approved licenses reported by package metadata |
| FastAPI, Uvicorn, HTTPX, Alembic, Prometheus client | Permissive OSI-approved licenses reported by package metadata |
| pandas and transitive NumPy | BSD or BSD-compatible licenses reported by package metadata |
| pytest, Ruff, mypy and test tooling | Permissive licenses; Hypothesis is MPL-2.0 library use |

`vectorbt` 1.0.0 is not a public dependency: its current Apache-2.0-with-Commons-Clause terms restrict selling and are outside this repository's public dependency policy. Backtrader and other legacy research packages are likewise not required by the public trading runtime.
