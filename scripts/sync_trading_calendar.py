"""Synchronize the MiniQMT A-share trading calendar into SQLite."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from dyadtetrade.db.repositories import upsert_trading_days
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.integrations.xtquant import XtQuantGateway


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同步沪深京交易日历并清理错误周末记录")
    parser.add_argument("--start", default="20100101")
    parser.add_argument(
        "--end",
        default=(date.today() + timedelta(days=31)).strftime("%Y%m%d"),
    )
    return parser


async def sync(start_date: str, end_date: str) -> dict[str, object]:
    await initialize_database()
    gateway = XtQuantGateway()
    result: dict[str, object] = {}
    async with SessionFactory() as session:
        for market in ("SH", "SZ", "BJ"):
            days = await asyncio.to_thread(
                gateway.trading_days,
                start_date,
                end_date,
                market,
            )
            await upsert_trading_days(
                session,
                market,
                days,
                source="xtquant",
                start_date=start_date,
                end_date=end_date,
            )
            result[market] = {
                "count": len(days),
                "first": days[0] if days else None,
                "last": days[-1] if days else None,
            }
    return result


async def main() -> None:
    args = build_parser().parse_args()
    print(await sync(args.start, args.end))


if __name__ == "__main__":
    asyncio.run(main())
