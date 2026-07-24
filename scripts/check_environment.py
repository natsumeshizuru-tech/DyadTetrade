from __future__ import annotations

import importlib
import json
import sys
import warnings
from importlib import metadata

DISTRIBUTIONS = [
    "numpy",
    "pandas",
    "scipy",
    "pyarrow",
    "duckdb",
    "polars",
    "numba",
    "joblib",
    "bottleneck",
    "numexpr",
    "statsmodels",
    "arch",
    "linearmodels",
    "alphalens-reloaded",
    "backtrader",
    "vectorbt",
    "quantstats",
    "empyrical-reloaded",
    "pyfolio-reloaded",
    "bt",
    "TA-Lib",
    "pandas-ta",
    "mplfinance",
    "scikit-learn",
    "xgboost",
    "lightgbm",
    "catboost",
    "shap",
    "imbalanced-learn",
    "torch",
    "pytorch-lightning",
    "transformers",
    "sentence-transformers",
    "optuna",
    "cvxpy",
    "nevergrad",
    "matplotlib",
    "plotly",
    "kaleido",
    "openpyxl",
    "xlsxwriter",
    "reportlab",
    "jinja2",
    "fastapi",
    "uvicorn",
    "httpx",
    "websockets",
    "python-multipart",
    "orjson",
    "aiofiles",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "asyncpg",
    "redis",
    "akshare",
    "tushare",
    "baostock",
    "efinance",
    "yfinance",
    "fredapi",
    "tenacity",
    "apscheduler",
    "structlog",
    "loguru",
    "rich",
    "tqdm",
    "watchdog",
    "psutil",
    "prometheus-client",
    "sentry-sdk",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "hypothesis",
    "freezegun",
    "ruff",
    "mypy",
    "pre-commit",
]

IMPORTS = [
    "numpy",
    "pandas",
    "scipy",
    "pyarrow",
    "duckdb",
    "polars",
    "numba",
    "joblib",
    "bottleneck",
    "numexpr",
    "statsmodels",
    "arch",
    "linearmodels",
    "alphalens",
    "backtrader",
    "vectorbt",
    "quantstats",
    "empyrical",
    "pyfolio",
    "bt",
    "talib",
    "pandas_ta",
    "mplfinance",
    "sklearn",
    "xgboost",
    "lightgbm",
    "catboost",
    "shap",
    "imblearn",
    "torch",
    "pytorch_lightning",
    "transformers",
    "sentence_transformers",
    "optuna",
    "cvxpy",
    "nevergrad",
    "matplotlib",
    "plotly",
    "kaleido",
    "openpyxl",
    "xlsxwriter",
    "reportlab",
    "jinja2",
    "fastapi",
    "uvicorn",
    "httpx",
    "websockets",
    "python_multipart",
    "orjson",
    "aiofiles",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "asyncpg",
    "redis",
    "akshare",
    "tushare",
    "baostock",
    "efinance",
    "yfinance",
    "fredapi",
    "tenacity",
    "apscheduler",
    "structlog",
    "loguru",
    "rich",
    "tqdm",
    "watchdog",
    "psutil",
    "prometheus_client",
    "sentry_sdk",
    "pytest",
    "pytest_asyncio",
    "pytest_cov",
    "hypothesis",
    "freezegun",
    "mypy",
    "pre_commit",
]


def main() -> int:
    warnings.filterwarnings("ignore")
    versions: dict[str, str] = {}
    missing_distributions: dict[str, str] = {}
    failed_imports: dict[str, str] = {}

    for distribution in DISTRIBUTIONS:
        try:
            versions[distribution] = metadata.version(distribution)
        except Exception as exc:  # pragma: no cover - environment diagnostic
            missing_distributions[distribution] = f"{type(exc).__name__}: {exc}"

    for module in IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - environment diagnostic
            failed_imports[module] = f"{type(exc).__name__}: {exc}"

    result = {
        "python": sys.version.split()[0],
        "installed": len(versions),
        "imports_ok": len(IMPORTS) - len(failed_imports),
        "missing_distributions": missing_distributions,
        "failed_imports": failed_imports,
        "versions": versions,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if missing_distributions or failed_imports else 0


if __name__ == "__main__":
    raise SystemExit(main())
