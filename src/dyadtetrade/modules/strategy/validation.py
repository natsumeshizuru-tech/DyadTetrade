from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import signal
import time
from pathlib import Path
from types import ModuleType
from typing import Any, TypedDict

import pandas as pd

from dyadtetrade.db.duckdb_store import query_bars
from dyadtetrade.modules.backtest.engine import BacktestEngine
from dyadtetrade.modules.backtest.schemas import BacktestConfig
from dyadtetrade.modules.strategy.base import Strategy
from dyadtetrade.modules.strategy.registry import strategy_registry

FORBIDDEN_IMPORT_ROOTS = {
    "xtquant",
    "sqlite3",
    "sqlalchemy",
    "duckdb",
    "subprocess",
    "socket",
}


class ValidationResult(TypedDict):
    valid: bool
    errors: list[str]


def validate_strategy_source(source: str, filename: str = "<strategy>") -> ValidationResult:
    try:
        tree = ast.parse(source, filename=filename)
        compile(tree, filename, "exec")
    except SyntaxError as exc:
        return {"valid": False, "errors": [f"SyntaxError line {exc.lineno}: {exc.msg}"]}
    errors: list[str] = []
    has_strategy = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                errors.append(f"forbidden import: {node.module}")
        elif isinstance(node, ast.ClassDef):
            has_strategy = has_strategy or any(
                (isinstance(base, ast.Name) and base.id == "Strategy")
                or (isinstance(base, ast.Attribute) and base.attr == "Strategy")
                for base in node.bases
            )
    if not has_strategy:
        errors.append("strategy file must define a class inheriting Strategy")
    return {"valid": not errors, "errors": errors}


def load_strategy_module(path: Path) -> tuple[ModuleType, type[Strategy]]:
    result = validate_strategy_source(path.read_text(encoding="utf-8"), str(path))
    if not result["valid"]:
        raise ValueError("; ".join(str(item) for item in result["errors"]))
    spec = importlib.util.spec_from_file_location(f"user_strategy_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load strategy: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidates = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and value is not Strategy
        and issubclass(value, Strategy)
        and value.__module__ == module.__name__
    ]
    if len(candidates) != 1:
        raise ValueError("strategy file must define exactly one local Strategy subclass")
    return module, candidates[0]


def smoke_backtest(path: Path, symbol: str, parameters: dict[str, Any]) -> dict[str, object]:
    _, strategy_class = load_strategy_module(path)
    strategy_registry.register(strategy_class)
    records = query_bars(
        symbols=[symbol],
        start_date="19000101",
        end_date="29991231",
        layer="bt_ready",
        limit=80,
    )
    if len(records) < 3:
        raise ValueError(f"insufficient bt_ready sample for {symbol}")
    frame = pd.DataFrame(records).sort_values("datetime").tail(60)
    config = BacktestConfig(
        strategy_name=strategy_class.name,
        strategy_parameters=parameters,
        symbols=[symbol],
        benchmark_symbol=None,
    )
    result = BacktestEngine().run(config, frame)
    return {
        "passed": True,
        "strategy_name": strategy_class.name,
        "strategy_version": strategy_class.version,
        "bars": len(frame),
        "orders": len(result.orders),
        "trades": len(result.trades),
        "final_assets": float(result.daily_equity[-1].total_assets),
    }


def heartbeat(path: Path, heartbeat_path: Path, log_path: Path | None = None) -> None:
    _, strategy_class = load_strategy_module(path)
    strategy_class("isolated-validation-runtime", {})
    running = True

    def stop_handler(_: int, __: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.time()} strategy={strategy_class.name} status=started\n")
    try:
        while running:
            heartbeat_path.write_text(str(time.time()), encoding="utf-8")
            time.sleep(1)
    finally:
        if log_path is not None:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.time()} strategy={strategy_class.name} status=stopped\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--heartbeat", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--symbol", default="600519.SH")
    parser.add_argument("--parameters", default="{}")
    args = parser.parse_args()
    if args.heartbeat is not None:
        heartbeat(args.file, args.heartbeat, args.log)
        return
    if args.smoke:
        print(json.dumps(smoke_backtest(args.file, args.symbol, json.loads(args.parameters))))
        return
    print(json.dumps(validate_strategy_source(args.file.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
