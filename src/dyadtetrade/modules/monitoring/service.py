from __future__ import annotations

from collections import Counter
from decimal import Decimal


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def account_monitor_snapshot(execution: dict[str, object]) -> dict[str, object]:
    assets = execution.get("assets")
    asset_values = assets if isinstance(assets, dict) else {}
    raw_positions = execution.get("positions")
    raw_orders = execution.get("orders")
    raw_trades = execution.get("trades")
    positions = (
        [dict(item) for item in raw_positions if isinstance(item, dict)]
        if isinstance(raw_positions, list)
        else []
    )
    orders = (
        [dict(item) for item in raw_orders if isinstance(item, dict)]
        if isinstance(raw_orders, list)
        else []
    )
    trades = (
        [dict(item) for item in raw_trades if isinstance(item, dict)]
        if isinstance(raw_trades, list)
        else []
    )

    total_assets = _decimal(asset_values.get("total_assets", 0))
    available_cash = _decimal(asset_values.get("available_cash", 0))
    market_value = sum((_decimal(item.get("market_value", 0)) for item in positions), Decimal(0))
    cost_value = sum(
        (
            _decimal(item.get("average_cost", 0)) * int(item.get("quantity", 0) or 0)
            for item in positions
        ),
        Decimal(0),
    )
    position_rows: list[dict[str, object]] = []
    for item in positions:
        value = _decimal(item.get("market_value", 0))
        cost = _decimal(item.get("average_cost", 0)) * int(item.get("quantity", 0) or 0)
        position_rows.append(
            {
                **item,
                "cost_value": str(cost),
                "unrealized_pnl": str(value - cost),
                "weight": float(value / total_assets) if total_assets > 0 else 0.0,
            }
        )
    position_rows.sort(key=lambda item: _decimal(item["weight"]), reverse=True)
    statuses = Counter(str(item.get("status", "UNKNOWN")) for item in orders)
    active_statuses = {"SUBMITTING", "SUBMITTED", "PARTIALLY_FILLED", "CANCELLING"}
    return {
        "connection": {
            "execution_mode": execution.get("execution_mode"),
            "connected": execution.get("connected", False),
            "subscribed": execution.get("subscribed", False),
            "account_healthy": execution.get("account_healthy", False),
            "account": execution.get("account"),
            "last_heartbeat": execution.get("last_heartbeat"),
            "last_reconciled_at": execution.get("last_reconciled_at"),
            "last_error": execution.get("last_error"),
        },
        "capital": {
            **asset_values,
            "market_value": str(market_value),
            "cost_value": str(cost_value),
            "unrealized_pnl": str(market_value - cost_value),
            "cash_ratio": float(available_cash / total_assets) if total_assets > 0 else 0.0,
            "gross_exposure": float(market_value / total_assets) if total_assets > 0 else 0.0,
            "largest_position_weight": (
                float(_decimal(position_rows[0]["weight"])) if position_rows else 0.0
            ),
        },
        "positions": position_rows,
        "orders": orders,
        "trades": trades,
        "order_statistics": {
            "total": len(orders),
            "active": sum(str(item.get("status")) in active_statuses for item in orders),
            "by_status": dict(statuses),
        },
        "trade_statistics": {
            "total": len(trades),
            "notional": str(
                sum(
                    (
                        _decimal(item.get("price", 0)) * int(item.get("quantity", 0) or 0)
                        for item in trades
                    ),
                    Decimal(0),
                )
            ),
        },
        "risk_switches": {
            "kill_switch": execution.get("kill_switch", False),
            "cancel_only": execution.get("cancel_only", False),
            "no_new_positions": execution.get("no_new_positions", False),
        },
    }
