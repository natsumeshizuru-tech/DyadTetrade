from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal

from dyadtetrade.core.config import ExecutionMode, get_settings
from dyadtetrade.domain import Direction, OrderRequest, OrderType, PriceType
from dyadtetrade.integrations.qmt_sim_gateway import QmtSimTradingGateway
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.integrations.xtquant_converters import tick_from_xtdata


def masked(value: str) -> str:
    return f"***{value[-4:]}" if value else "<missing>"


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe MiniQMT simulation-account smoke test")
    parser.add_argument("--confirm-sim-order", action="store_true")
    parser.add_argument("--confirm-text", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--price", type=Decimal)
    args = parser.parse_args()
    settings = get_settings()
    settings.validate_execution_safety()
    if settings.execution_mode is not ExecutionMode.QMT_SIM:
        raise SystemExit("execution_mode must be qmt_sim")
    gateway = QmtSimTradingGateway.create(settings)
    try:
        gateway.start()
        result: dict[str, object] = {
            "mode": settings.execution_mode.value,
            "account": masked(settings.qmt_account_id),
            "status": gateway.status(),
            "reconciliation": gateway.reconcile(),
            "order_sent": False,
            "live_trading": settings.enable_live_trading,
        }
        if args.confirm_sim_order:
            if args.confirm_text != "I_CONFIRM_QMT_SIM":
                raise SystemExit("explicit --confirm-text I_CONFIRM_QMT_SIM is required")
            symbol = args.symbol.upper()
            if symbol not in settings.allowed_symbols:
                raise SystemExit("symbol is not in DYADTETRADE_ALLOWED_SYMBOLS")
            if args.price is None or args.price <= 0:
                raise SystemExit("a positive --price is required")
            raw = XtQuantGateway().full_tick([symbol]).get(symbol)
            if raw is None:
                raise SystemExit("real-time quote unavailable")
            gateway.update_market(tick_from_xtdata(symbol, raw, received_at=datetime.now(UTC)))
            request = OrderRequest(
                symbol=symbol,
                direction=Direction.BUY,
                quantity=100,
                order_type=OrderType.LIMIT,
                price_type=PriceType.LIMIT,
                limit_price=args.price,
                strategy_id="preopen-smoke",
                account_id=settings.qmt_account_id,
                client_order_id=f"preopen-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                idempotency_key=f"preopen-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
                created_at=datetime.now(UTC),
            )
            submission = gateway.submit_async(request)
            result["order_sent"] = True
            result["qmt_seq"] = submission.mapping.qmt_seq
            result["order_status"] = submission.order.status.value
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        gateway.stop()


if __name__ == "__main__":
    main()
