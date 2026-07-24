from __future__ import annotations

import importlib
import sys

FORBIDDEN = {"torch", "transformers", "catboost", "lightgbm", "xgboost"}
FORBIDDEN_PREFIXES = (
    "dyadresearch",
    "dyadtetrade.modules.backtest",
    "dyadtetrade.modules.research",
    "dyadtetrade.modules.optimization",
    "dyadtetrade.modules.portfolio",
    "dyadtetrade.api.routes.backtest",
    "dyadtetrade.api.routes.research",
    "dyadtetrade.api.routes.optimization",
    "dyadtetrade.api.routes.portfolio",
    "dyadtetrade.api.routes.market_data",
    "dyadtetrade.api.routes.strategy_files",
)
FORBIDDEN_ROUTE_PREFIXES = (
    "/api/v1/backtests",
    "/api/v1/research",
    "/api/v1/optimizations",
    "/api/v1/portfolios",
    "/api/v1/market/history",
    "/api/v1/strategy-files",
)


def main() -> int:
    importlib.import_module("dyadtetrade.trading.app")
    product_api = importlib.import_module("dyadtetrade.api.main")
    loaded = sorted(name for name in FORBIDDEN if name in sys.modules)
    if loaded:
        raise SystemExit(f"trading process imported forbidden research runtimes: {loaded}")
    loaded_internal = sorted(name for name in sys.modules if name.startswith(FORBIDDEN_PREFIXES))
    if loaded_internal:
        raise SystemExit(
            "default trading API imported forbidden research modules: " + ", ".join(loaded_internal)
        )
    routes = set(product_api.app.openapi()["paths"])
    forbidden_routes = sorted(path for path in routes if path.startswith(FORBIDDEN_ROUTE_PREFIXES))
    if forbidden_routes:
        raise SystemExit(
            "default trading API exposed forbidden research routes: " + ", ".join(forbidden_routes)
        )
    print("trading import and API boundary passed; research modules and routes are absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
