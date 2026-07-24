from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select, update

from dyadtetrade.core.symbols import normalize_symbol
from dyadtetrade.db.duckdb_store import query_bars, refresh_market_views
from dyadtetrade.db.models import DownloadTask, DownloadTaskItem
from dyadtetrade.db.repositories import (
    persist_quality_findings,
    upsert_data_file,
)
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.market_data.importer import LegacyMarketDataImporter
from dyadtetrade.modules.market_data.tasks import DownloadTaskRunner, task_to_dict
from dyadtetrade.modules.universe.service import InstrumentCatalogService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="DyadTetrade 行情数据中心运维命令")
    commands = result.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync-catalog", help="同步股票、ETF、指数和板块")
    sync.add_argument("--skip-details", action="store_true")
    sync.add_argument("--refresh-sectors", action="store_true")

    create = commands.add_parser("create-task", help="创建幂等历史行情下载任务")
    create.add_argument("--symbols", required=True, help="逗号分隔代码或 @文本文件")
    create.add_argument("--start", required=True)
    create.add_argument("--end", default="")
    create.add_argument("--period", default="1d")
    create.add_argument("--adjust", default="front")
    create.add_argument("--retries", type=int, default=2)
    create.add_argument("--run", action="store_true")

    run = commands.add_parser("run-task", help="运行或恢复指定下载任务")
    run.add_argument("task_id", type=int)
    run.add_argument("--retry-failed", action="store_true")

    pending = commands.add_parser("run-pending", help="依次执行所有待恢复任务")
    pending.add_argument("--limit", type=int, default=20)

    imported = commands.add_parser("import", help="只读导入旧 CSV/Parquet 全市场数据")
    imported.add_argument("source", type=Path)
    imported.add_argument("--period", default="1d")
    imported.add_argument("--adjust", default="front")

    query = commands.add_parser("query", help="通过 DuckDB 查询 Parquet 行情")
    query.add_argument("--symbols", required=True)
    query.add_argument("--start", required=True)
    query.add_argument("--end", required=True)
    query.add_argument("--layer", choices=("trusted", "bt_ready"), default="trusted")
    query.add_argument("--limit", type=int, default=100)
    return result


def load_symbols(value: str) -> list[str]:
    if value.startswith("@"):
        values = Path(value[1:]).read_text(encoding="utf-8").splitlines()
    else:
        values = value.split(",")
    return sorted({normalize_symbol(item) for item in values if item.strip()})


async def main() -> None:
    args = parser().parse_args()
    await initialize_database()
    if args.command == "query":
        records = query_bars(
            symbols=load_symbols(args.symbols),
            start_date=args.start,
            end_date=args.end,
            layer=args.layer,
            limit=args.limit,
        )
        print(json.dumps(records, ensure_ascii=False, default=str, indent=2))
        return
    if args.command == "import":
        importer = LegacyMarketDataImporter()
        result = importer.import_path(args.source, period=args.period, adjust=args.adjust)
        async with SessionFactory() as session:
            for metadata in result.files:
                await upsert_data_file(session, metadata)
            await persist_quality_findings(
                session, task_id=None, period=args.period, findings=result.findings
            )
        refresh_market_views()
        print(
            json.dumps(
                {
                    "source_files": result.source_files,
                    "imported_symbols": result.imported_symbols,
                    "errors": result.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    gateway = XtQuantGateway()
    async with SessionFactory() as session:
        if args.command == "sync-catalog":
            result = await InstrumentCatalogService(gateway).sync(
                session,
                refresh_remote_sectors=args.refresh_sectors,
                include_details=not args.skip_details,
                include_all_sectors=True,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "create-task":
            runner = DownloadTaskRunner(session, gateway=gateway)
            task, created = await runner.create(
                symbols=load_symbols(args.symbols),
                start_date=args.start,
                end_date=args.end,
                period=args.period,
                adjust=args.adjust,
                max_retries=args.retries,
            )
            if args.run:
                task = await runner.run(task.id)
            payload = task_to_dict(task)
            payload["created"] = created
            print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
        elif args.command == "run-task":
            if args.retry_failed:
                await session.execute(
                    update(DownloadTaskItem)
                    .where(
                        DownloadTaskItem.task_id == args.task_id,
                        DownloadTaskItem.status == "failed",
                    )
                    .values(status="pending", attempts=0, error_message=None, finished_at=None)
                )
                await session.commit()
            task = await DownloadTaskRunner(session, gateway=gateway).run(args.task_id)
            print(json.dumps(task_to_dict(task), ensure_ascii=False, default=str, indent=2))
        elif args.command == "run-pending":
            tasks = (
                await session.scalars(
                    select(DownloadTask)
                    .where(DownloadTask.status.in_(("pending", "running", "partial")))
                    .order_by(DownloadTask.id)
                    .limit(args.limit)
                )
            ).all()
            for task in tasks:
                completed = await DownloadTaskRunner(session, gateway=gateway).run(task.id)
                print(json.dumps(task_to_dict(completed), ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
