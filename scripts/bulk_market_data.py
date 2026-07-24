from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from dyadtetrade.core.config import get_settings
from dyadtetrade.db.duckdb_store import refresh_market_views
from dyadtetrade.db.models import Instrument
from dyadtetrade.db.repositories import upsert_data_file
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.modules.market_data.adjustment_validation import (
    compare_adjusted_data,
    write_adjustment_report,
)
from dyadtetrade.modules.market_data.bulk_plan import (
    create_download_batches,
    estimate_capacity,
    format_bytes,
    write_plan,
)
from dyadtetrade.modules.market_data.derivation import DerivedPeriodBuilder
from dyadtetrade.modules.market_data.tasks import DownloadTaskRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全市场多周期数据安全规划与派生工具")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="只生成容量和批次计划，不下载")
    plan.add_argument("--end", required=True)
    plan.add_argument("--batch-size", type=int, default=100)
    plan.add_argument("--include-tick", action="store_true")
    plan.add_argument("--output", type=Path, default=Path("reports/market_data/bulk_plan.json"))
    create = commands.add_parser("create-native-tasks", help="由通过容量门禁的计划创建任务，不执行")
    create.add_argument("plan", type=Path)
    derive = commands.add_parser("derive", help="从 trusted 1m/1d 幂等派生周期")
    derive.add_argument(
        "--period", choices=("5m", "15m", "30m", "1h", "1w", "1q", "1y"), required=True
    )
    derive.add_argument("--adjust", choices=("none", "front"), required=True)
    derive.add_argument("--symbols", type=Path)
    derive.add_argument("--state", type=Path, default=Path("reports/market_data/derive_state.json"))
    validate = commands.add_parser("validate-adjustments", help="复核 none/front 数据")
    validate.add_argument("--period", required=True)
    validate.add_argument("--symbols", type=Path)
    validate.add_argument(
        "--output", type=Path, default=Path("reports/market_data/adjustment_check.json")
    )
    return parser


async def active_stock_symbols(path: Path | None = None) -> list[str]:
    if path is not None:
        return sorted(
            {
                line.strip().upper()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        )
    async with SessionFactory() as session:
        return list(
            await session.scalars(
                select(Instrument.symbol)
                .where(Instrument.is_active.is_(True), Instrument.asset_type == "stock")
                .order_by(Instrument.symbol)
            )
        )


async def create_native_tasks(plan_path: Path) -> dict[str, Any]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if not bool(payload.get("executable")):
        reasons = payload.get("capacity", {}).get("reasons", [])
        raise RuntimeError(f"容量门禁拒绝创建任务：{'; '.join(reasons)}")
    created_ids: list[int] = []
    existing_ids: list[int] = []
    async with SessionFactory() as session:
        runner = DownloadTaskRunner(session)
        for batch in payload["batches"]:
            task, created = await runner.create(
                symbols=list(batch["symbols"]),
                start_date=str(batch["start_date"]),
                end_date=str(batch["end_date"]),
                period=str(batch["period"]),
                adjust=str(batch["adjust"]),
            )
            (created_ids if created else existing_ids).append(task.id)
    return {"created_task_ids": created_ids, "existing_task_ids": existing_ids, "executed": False}


async def derive_periods(args: argparse.Namespace) -> dict[str, Any]:
    symbols = await active_stock_symbols(args.symbols)
    state = _read_state(args.state)
    completed = set(state.get("completed", []))
    errors: dict[str, str] = dict(state.get("errors", {}))
    builder = DerivedPeriodBuilder()
    async with SessionFactory() as session:
        for symbol in symbols:
            key = f"{symbol}:{args.period}:{args.adjust}"
            if key in completed:
                continue
            try:
                result = builder.derive(
                    symbol=symbol, target_period=args.period, adjust=args.adjust
                )
                for metadata in result.files:
                    await upsert_data_file(session, metadata)
                completed.add(key)
                errors.pop(key, None)
            except Exception as exc:
                errors[key] = str(exc)[:1024]
            _write_state(args.state, completed, errors)
    refresh_market_views()
    return {"completed": len(completed), "failed": len(errors), "state": str(args.state.resolve())}


async def validate_adjustments(args: argparse.Namespace) -> dict[str, Any]:
    symbols = await active_stock_symbols(args.symbols)
    builder = DerivedPeriodBuilder()
    checks = []
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            raw = builder.store.read(symbol, args.period, "none", "trusted")
            adjusted = builder.store.read(symbol, args.period, "front", "trusted")
            checks.append(compare_adjusted_data(raw, adjusted, symbol=symbol, period=args.period))
        except Exception as exc:
            errors[symbol] = str(exc)[:1024]
    path = write_adjustment_report(checks, args.output)
    return {
        "checked": len(checks),
        "passed": sum(item.passed for item in checks),
        "failed": sum(not item.passed for item in checks),
        "unreadable": len(errors),
        "unreadable_symbols": errors,
        "report": str(path),
    }


def _read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_state(path: Path, completed: set[str], errors: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"completed": sorted(completed), "errors": errors},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


async def main() -> None:
    args = build_parser().parse_args()
    await initialize_database()
    if args.command == "plan":
        symbols = await active_stock_symbols()
        estimate = estimate_capacity(
            len(symbols), data_root=get_settings().data_root, include_tick=args.include_tick
        )
        batches = create_download_batches(
            symbols,
            end_date=args.end,
            batch_size=args.batch_size,
            include_tick=args.include_tick,
        )
        path = write_plan(args.output, batches=batches, estimate=estimate)
        print(
            json.dumps(
                {
                    "plan": str(path),
                    "symbols": len(symbols),
                    "batches": len(batches),
                    "required": format_bytes(estimate.required_bytes),
                    "free": format_bytes(estimate.free_bytes),
                    "executable": estimate.bounded,
                    "reasons": estimate.reasons,
                    "download_started": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "create-native-tasks":
        print(json.dumps(await create_native_tasks(args.plan), ensure_ascii=False, indent=2))
    elif args.command == "derive":
        print(json.dumps(await derive_periods(args), ensure_ascii=False, indent=2))
    elif args.command == "validate-adjustments":
        print(json.dumps(await validate_adjustments(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
