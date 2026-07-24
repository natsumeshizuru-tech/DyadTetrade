from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from dyadtetrade.modules.realtime import XtDataSubscriptionService


async def main() -> int:
    service = XtDataSubscriptionService(max_queue=32)
    evidence: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "single_symbol": "600519.SH",
        "whole_markets": ["SH", "SZ"],
        "orders_sent": 0,
    }
    try:
        await service.subscribe_single(["600519.SH"], "tick")
        await service.subscribe_whole(["SH", "SZ"])
        for _ in range(20):
            await asyncio.sleep(0.5)
            if int(service.snapshot()["latest_symbol_count"]) > 100:
                break
        evidence["status"] = service.snapshot()
        evidence["sample"] = service.latest(["600519.SH"])
    finally:
        await service.stop()
        evidence["stopped"] = service.snapshot()
    output = (
        Path("reports/validation")
        / f"xtdata_subscription_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        json.dumps({"evidence": str(output), **evidence}, ensure_ascii=False, indent=2, default=str)
    )
    status = evidence.get("status")
    if not isinstance(status, dict) or status.get("latest_symbol_count", 0) == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
