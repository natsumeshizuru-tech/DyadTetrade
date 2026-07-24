from __future__ import annotations

import argparse
import asyncio
import json

from dyadtetrade.core.config import ExecutionMode
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.modules.execution.universe import (
    ExecutionUniverseFilters,
    ExecutionUniverseService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and optionally activate a reviewed daily execution universe."
    )
    parser.add_argument("--mode", choices=("qmt_sim", "qmt_live"), default="qmt_sim")
    parser.add_argument("--minimum-listed-days", type=int, default=20)
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--include-delisting", action="store_true")
    parser.add_argument("--allow-missing-daily-data", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--actor", default="local-operator")
    parser.add_argument("--confirmation", default="")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.activate and not args.review:
        raise ValueError("--activate requires --review in the same invocation")
    mode = ExecutionMode(args.mode)
    if mode is ExecutionMode.QMT_LIVE and args.activate:
        raise PermissionError(
            "This helper refuses to activate qmt_live; use the reviewed API and manual daily gate."
        )
    await initialize_database()
    async with SessionFactory() as session:
        result = await ExecutionUniverseService.build(
            session,
            execution_mode=mode,
            filters=ExecutionUniverseFilters(
                exclude_st=not args.include_st,
                exclude_delisting=not args.include_delisting,
                minimum_listed_days=args.minimum_listed_days,
                require_daily_data=not args.allow_missing_daily_data,
            ),
        )
        release_id = str(result["release_id"])
        if args.review:
            result = await ExecutionUniverseService.review(session, release_id, reviewer=args.actor)
        if args.activate:
            result = await ExecutionUniverseService.activate(
                session,
                release_id,
                approver=args.actor,
                confirmation=args.confirmation,
            )
        return dict(result)


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
