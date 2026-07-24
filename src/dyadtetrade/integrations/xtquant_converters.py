from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from dyadtetrade.domain import (
    Account,
    Direction,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    Tick,
    Trade,
    TradingStatus,
)

XT_ORDER_STATUS: dict[int, OrderStatus] = {
    48: OrderStatus.SUBMITTING,
    49: OrderStatus.SUBMITTING,
    50: OrderStatus.SUBMITTED,
    51: OrderStatus.CANCELLING,
    52: OrderStatus.CANCELLING,
    53: OrderStatus.CANCELLED,
    54: OrderStatus.CANCELLED,
    55: OrderStatus.PARTIALLY_FILLED,
    56: OrderStatus.FILLED,
    57: OrderStatus.REJECTED,
    255: OrderStatus.UNKNOWN,
}

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def object_payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    names = (
        "account_id",
        "account_type",
        "cash",
        "frozen_cash",
        "current_balance",
        "fetch_balance",
        "market_value",
        "total_asset",
        "stock_code",
        "volume",
        "can_use_volume",
        "frozen_volume",
        "on_road_volume",
        "avg_price",
        "open_price",
        "order_id",
        "order_sysid",
        "order_type",
        "order_volume",
        "price",
        "strategy_name",
        "order_remark",
        "order_status",
        "order_time",
        "traded_volume",
        "traded_price",
        "traded_amount",
        "traded_id",
        "traded_time",
        "commission",
        "seq",
        "error_id",
        "cancel_result",
        "market",
        "error_msg",
        "status",
    )
    return {name: getattr(value, name) for name in names if hasattr(value, name)}


def decimal_field(
    payload: dict[str, Any],
    *names: str,
    default: str = "0",
    decimals: int = 8,
) -> Decimal:
    """Normalize XtQuant C-double tails at the adapter boundary."""

    quantum = Decimal(1).scaleb(-decimals)
    for name in names:
        value = payload.get(name)
        if value is not None:
            return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return Decimal(default).quantize(quantum)


def optional_decimal_field(
    payload: dict[str, Any],
    *names: str,
    decimals: int = 8,
) -> Decimal | None:
    """Return a normalized broker numeric fact without inventing a missing zero."""

    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return decimal_field(payload, name, decimals=decimals)
    return None


def xt_timestamp(value: Any) -> datetime:
    """Normalize XtData/XtTrader seconds, milliseconds and local time strings."""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("empty XtQuant timestamp")
        if not stripped.isdigit():
            for pattern in ("%Y%m%d %H:%M:%S", "%Y%m%d%H%M%S"):
                try:
                    local_time = datetime.strptime(stripped, pattern).replace(tzinfo=CHINA_TZ)
                    return local_time.astimezone(UTC)
                except ValueError:
                    continue
            parsed = datetime.fromisoformat(stripped)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=CHINA_TZ)
            return parsed.astimezone(UTC)
        value = stripped
    numeric = int(value)
    return datetime.fromtimestamp(numeric / 1000 if numeric > 10**12 else numeric, tz=UTC)


def tick_from_xtdata(
    symbol: str, payload: dict[str, Any], *, received_at: datetime | None = None
) -> Tick:
    timestamp_value = payload.get("timetag") or payload.get("time")
    if timestamp_value is not None:
        timestamp = xt_timestamp(timestamp_value)
    else:
        timestamp = received_at or datetime.now(UTC)
    status = (
        TradingStatus.SUSPENDED if int(payload.get("volume", 0)) == 0 else TradingStatus.TRADING
    )
    return Tick(
        symbol=symbol,
        timestamp=timestamp,
        last_price=decimal_field(payload, "lastPrice", "last_price"),
        volume=int(payload.get("volume", 0)),
        amount=decimal_field(payload, "amount", decimals=2),
        bid_price=tuple(
            Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            for value in payload.get("bidPrice", []) or []
        ),
        ask_price=tuple(
            Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            for value in payload.get("askPrice", []) or []
        ),
        bid_volume=tuple(int(value) for value in payload.get("bidVol", []) or []),
        ask_volume=tuple(int(value) for value in payload.get("askVol", []) or []),
        trading_status=status,
        source="xtdata",
    )


def order_from_xttrader(value: object) -> Order:
    payload = object_payload(value)
    raw_order_id = str(payload.get("order_id") or "").strip()
    order_sysid = str(payload.get("order_sysid") or "").strip()
    qmt_order_id = int(raw_order_id) if raw_order_id.isdigit() and int(raw_order_id) > 0 else None
    broker_order_id = str(qmt_order_id) if qmt_order_id is not None else order_sysid
    if not broker_order_id:
        raise ValueError("XtTrader order is missing broker order identity")
    direction = Direction.BUY if int(payload.get("order_type", 23)) == 23 else Direction.SELL
    request = OrderRequest(
        symbol=str(payload["stock_code"]),
        direction=direction,
        quantity=int(payload["order_volume"]),
        limit_price=decimal_field(payload, "price") or None,
        strategy_id=str(payload.get("strategy_name", "")),
        account_id="masked",
        client_order_id=str(payload.get("order_remark") or broker_order_id),
        idempotency_key=str(payload.get("order_remark") or broker_order_id),
    )
    status = XT_ORDER_STATUS.get(int(payload.get("order_status", -1)), OrderStatus.UNKNOWN)
    order = Order(
        order_id=broker_order_id,
        request=request,
        status=status,
        filled_quantity=int(payload.get("traded_volume", 0)),
        average_fill_price=decimal_field(payload, "traded_price"),
        broker_order_id=broker_order_id,
        qmt_order_id=qmt_order_id,
        order_sysid=order_sysid or None,
    )
    order_time = payload.get("order_time")
    if order_time not in (None, "", 0, "0"):
        order.submitted_at = xt_timestamp(order_time)
        order.updated_at = order.submitted_at
    return order


def trade_from_xttrader(
    value: object,
    *,
    source: str,
    execution_mode: str,
    account_id: str,
    is_simulated: bool | None = None,
) -> Trade:
    payload = object_payload(value)
    direction = Direction.BUY if int(payload.get("order_type", 23)) == 23 else Direction.SELL
    broker_trade_id = str(payload["traded_id"])
    raw_order_id = str(payload.get("order_id") or "").strip()
    order_sysid = str(payload.get("order_sysid") or "").strip()
    qmt_order_id = int(raw_order_id) if raw_order_id.isdigit() and int(raw_order_id) > 0 else None
    broker_order_id = str(qmt_order_id) if qmt_order_id is not None else order_sysid
    if not broker_order_id:
        raise ValueError("XtTrader trade is missing broker order identity")
    reported_at = xt_timestamp(payload.get("traded_time", 0))
    broker_reported_fee = optional_decimal_field(payload, "commission")
    if broker_reported_fee is not None and broker_reported_fee < 0:
        raise ValueError("XtTrader trade commission must be non-negative")
    return Trade(
        trade_id=broker_trade_id,
        order_id=broker_order_id,
        symbol=str(payload["stock_code"]),
        direction=direction,
        quantity=int(payload["traded_volume"]),
        price=decimal_field(payload, "traded_price"),
        timestamp=reported_at,
        broker_reported_fee=broker_reported_fee,
        fee_source=("xttrade.commission" if broker_reported_fee is not None else "unavailable"),
        fee_breakdown_complete=False,
        execution_mode=execution_mode,
        account_id=account_id,
        strategy_id=str(payload.get("strategy_name", "")),
        source=source,
        is_simulated=execution_mode != "qmt_live" if is_simulated is None else is_simulated,
        broker_order_id=broker_order_id,
        qmt_order_id=qmt_order_id,
        order_sysid=order_sysid or None,
        broker_trade_id=broker_trade_id,
        broker_reported_at=reported_at,
    )


def position_from_xttrader(value: object) -> Position:
    payload = object_payload(value)
    quantity = int(payload.get("volume", 0))
    available_quantity = int(payload.get("can_use_volume", 0))
    market_value = decimal_field(payload, "market_value", decimals=2)
    average_cost = decimal_field(payload, "avg_price", "open_price")
    frozen_quantity = int(payload.get("frozen_volume", max(quantity - available_quantity, 0)))
    return Position(
        symbol=str(payload["stock_code"]),
        quantity=quantity,
        available_quantity=available_quantity,
        frozen_quantity=frozen_quantity,
        average_cost=average_cost,
        last_price=(market_value / max(quantity, 1)).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        ),
    )


def account_from_xttrader(value: object) -> Account:
    payload = object_payload(value)
    # XtAsset.cash is the broker-reported available amount.  Do not subtract
    # frozen_cash again: doing so double-counts the broker's frozen amount.
    available_cash = decimal_field(payload, "cash", decimals=2)
    frozen = decimal_field(payload, "frozen_cash", decimals=2)
    return Account(
        account_id="masked",
        cash=available_cash + frozen,
        available_cash=available_cash,
        frozen_cash=frozen,
        reported_positions_value=optional_decimal_field(payload, "market_value", decimals=2),
        reported_total_assets=optional_decimal_field(payload, "total_asset", decimals=2),
    )
