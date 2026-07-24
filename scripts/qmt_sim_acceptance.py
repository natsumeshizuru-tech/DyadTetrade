from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

ACTIVE = {"CREATED", "SUBMITTING", "SUBMITTED", "PARTIALLY_FILLED", "CANCELLING"}


def request(client: httpx.Client, method: str, path: str, **kwargs: object) -> dict[str, object]:
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected response from {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Actual QMT simulation one-lot acceptance")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-text", default="")
    parser.add_argument("--symbol", default="510300.SH")
    parser.add_argument("--price", type=Decimal)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    base = "http://127.0.0.1:8000/api/v1"
    with httpx.Client(base_url=base, timeout=10) as client:
        ready = request(client, "GET", "/ready")
        status = request(client, "GET", "/execution/status")
        evidence: dict[str, object] = {
            "observed_at": datetime.now(UTC).isoformat(),
            "execution_mode": status.get("execution_mode"),
            "engine_online": status.get("engine_online"),
            "connected": status.get("connected"),
            "subscribed": status.get("subscribed"),
            "account_healthy": status.get("account_healthy"),
            "risk_state": status.get("risk_state"),
            "live_trading_enabled": ready.get("live_trading_enabled"),
            "order_sent": False,
        }
        if not args.execute:
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            return 0
        if args.confirm_text != "I_CONFIRM_QMT_SIM_ONE_LOT":
            raise SystemExit("--confirm-text I_CONFIRM_QMT_SIM_ONE_LOT is required")
        if (
            status.get("execution_mode") != "qmt_sim"
            or ready.get("live_trading_enabled") is not False
        ):
            raise SystemExit("qmt_sim with live trading disabled is required")
        if not all(
            status.get(key) is True
            for key in ("engine_online", "connected", "subscribed", "account_healthy")
        ):
            raise SystemExit("QMT simulation gateway is not fully ready")
        risk = status.get("risk_state")
        if not isinstance(risk, dict) or risk.get("state") != "NORMAL":
            raise SystemExit("risk state must be NORMAL")
        if args.price is None or args.price <= 0:
            raise SystemExit("explicit positive --price is required")
        client_order_id = f"accept-{uuid4().hex[:24]}"
        payload = {
            "symbol": args.symbol.upper(),
            "direction": "buy",
            "quantity": 100,
            "limit_price": str(args.price),
            "strategy_id": "qmt-sim-acceptance",
            "client_order_id": client_order_id,
            "idempotency_key": client_order_id,
            "confirm_simulated_order": True,
        }
        submission = request(client, "POST", "/execution/orders", json=payload)
        evidence.update(
            {"order_sent": True, "client_order_id": client_order_id, "submission": submission}
        )
        deadline = time.monotonic() + args.timeout
        observed: dict[str, object] | None = None
        while time.monotonic() < deadline:
            time.sleep(0.5)
            snapshot = request(client, "GET", "/execution/status")
            orders = snapshot.get("orders", [])
            if isinstance(orders, list):
                observed = next(
                    (
                        item
                        for item in orders
                        if isinstance(item, dict) and item.get("client_order_id") == client_order_id
                    ),
                    None,
                )
            if observed is not None and observed.get("status") not in {"SUBMITTING", "CREATED"}:
                break
        evidence["callback_order"] = observed
        if observed is None:
            raise RuntimeError("no QMT order callback was observed")
        order_id = observed.get("qmt_order_id") or observed.get("order_id")
        if observed.get("status") in ACTIVE and order_id is not None and str(order_id).isdigit():
            evidence["cancel_response"] = request(
                client,
                "POST",
                f"/execution/orders/{int(str(order_id))}/cancel",
                params={"confirm_simulated_order": True},
            )
            time.sleep(1)
        evidence["reconciliation"] = request(client, "POST", "/execution/reconcile")

    output = (
        Path("reports/validation")
        / f"qmt_sim_acceptance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        json.dumps({"evidence": str(output), **evidence}, ensure_ascii=False, indent=2, default=str)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
