from __future__ import annotations

import argparse
import asyncio

from dyadtetrade.core.config import ExecutionMode
from dyadtetrade.db.session import SessionFactory
from dyadtetrade.modules.execution.universe import ExecutionUniverseService


async def _find(mode: ExecutionMode) -> str:
    async with SessionFactory() as session:
        snapshot = await ExecutionUniverseService.latest_active(session, execution_mode=mode)
    if snapshot is None:
        raise RuntimeError(f"no active {mode.value} execution universe exists for today")
    return str(snapshot["release_id"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("qmt_sim", "qmt_live"), required=True)
    args = parser.parse_args()
    print(asyncio.run(_find(ExecutionMode(args.mode))))


if __name__ == "__main__":
    main()
