"""Create and run a bounded all-market daily incremental task after the close."""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from dyadtetrade.core.config import ExecutionMode
from dyadtetrade.db.session import SessionFactory
from dyadtetrade.modules.execution.universe import (
    ExecutionUniverseFilters,
    ExecutionUniverseService,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--allow-before-close", action="store_true")
    parser.add_argument("--activate-qmt-sim-universe", action="store_true")
    args = parser.parse_args()
    now = datetime.now(CHINA_TZ)
    if not args.allow_before_close and (now.hour, now.minute) < (15, 10):
        raise RuntimeError("post-close update is blocked before 15:10 Asia/Shanghai")
    if not 3 <= args.lookback_days <= 60:
        raise ValueError("lookback-days must be between 3 and 60")

    database = Path("data/sqlite/dyadtetrade.db")
    with sqlite3.connect(database) as connection:
        symbols = [
            str(row[0])
            for row in connection.execute(
                "select symbol from instruments where is_active=1 "
                "and asset_type in ('stock','etf','index') order by symbol"
            )
        ]
    if not symbols:
        raise RuntimeError("active instrument catalog is empty")

    payload = {
        "symbols": symbols,
        "start_date": (now.date() - timedelta(days=args.lookback_days)).strftime("%Y%m%d"),
        "end_date": now.date().strftime("%Y%m%d"),
        "period": "1d",
        "adjust": "front",
        "max_retries": 2,
        "execute_now": False,
    }
    with httpx.Client(base_url=args.api, timeout=30, trust_env=False) as client:
        created = client.post("/market/history/tasks", json=payload)
        created.raise_for_status()
        task = created.json()
        task_id = int(task["id"])
        with httpx.Client(base_url=args.api, timeout=6 * 60 * 60, trust_env=False) as runner:
            completed = runner.post(f"/market/history/tasks/{task_id}/run")
            completed.raise_for_status()
            result = completed.json()
    print(
        {
            "task_id": task_id,
            "status": result["status"],
            "total": result["total_items"],
            "succeeded": result["succeeded_items"],
            "failed": result["failed_items"],
            "quality_report_json": result["quality_report_json"],
        }
    )
    if args.activate_qmt_sim_universe:
        snapshot = asyncio.run(_activate_qmt_sim_universe())
        print(
            {
                "execution_universe_release_id": snapshot["release_id"],
                "status": snapshot["status"],
                "symbol_count": snapshot["symbol_count"],
                "snapshot_hash": snapshot["snapshot_hash"],
            }
        )


async def _activate_qmt_sim_universe() -> dict[str, object]:
    async with SessionFactory() as session:
        snapshot = await ExecutionUniverseService.build(
            session,
            execution_mode=ExecutionMode.QMT_SIM,
            filters=ExecutionUniverseFilters(),
        )
        release_id = str(snapshot["release_id"])
        await ExecutionUniverseService.review(session, release_id, reviewer="postclose-automation")
        return await ExecutionUniverseService.activate(
            session,
            release_id,
            approver="postclose-automation",
            confirmation="I_CONFIRM_QMT_SIM_UNIVERSE",
        )


if __name__ == "__main__":
    main()
