from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from dyadtetrade.domain import Account, Bar, Direction, Order, OrderRequest, RiskResult
from dyadtetrade.domain.orders import FINAL_ORDER_STATUSES


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_order_value: Decimal = Decimal("100000")
    max_order_quantity: int = 100000
    max_symbol_weight: Decimal = Decimal("0.20")
    max_industry_weight: Decimal = Decimal("0.40")
    max_total_position_weight: Decimal = Decimal("0.95")
    max_daily_turnover: Decimal = Decimal("500000")
    max_daily_orders: int = 200
    max_price_deviation: Decimal = Decimal("0.02")
    max_quote_age: timedelta = timedelta(seconds=10)
    max_consecutive_rejections: int = 5
    max_consecutive_cancels: int = 20
    max_strategy_heartbeat_age: timedelta = timedelta(seconds=30)
    max_order_response_age: timedelta = timedelta(minutes=2)
    max_asset_change: Decimal = Decimal("0.10")


@dataclass(slots=True)
class TradingSwitches:
    global_stop: bool = False
    cancel_only: bool = False
    no_new_positions: bool = False
    stopped_accounts: set[str] | None = None
    stopped_strategies: set[str] | None = None

    def __post_init__(self) -> None:
        self.stopped_accounts = self.stopped_accounts or set()
        self.stopped_strategies = self.stopped_strategies or set()


class RiskEngine:
    def __init__(
        self,
        limits: RiskLimits | None = None,
        switches: TradingSwitches | None = None,
        industries: dict[str, str] | None = None,
    ) -> None:
        self.limits = limits or RiskLimits()
        self.switches = switches or TradingSwitches()
        self.industries = industries or {}
        self.daily_order_count = 0
        self.daily_turnover = Decimal("0")
        self.consecutive_rejections = 0
        self.consecutive_cancels = 0
        self._idempotency_keys: set[str] = set()
        self.events: list[RiskResult] = []

    def check_order(
        self,
        request: OrderRequest,
        *,
        account: Account,
        market: Bar | None,
        now: datetime,
    ) -> RiskResult:
        rejected = self._pretrade_rejection(request, account=account, market=market, now=now)
        if rejected is not None:
            self.consecutive_rejections += 1
            if self.consecutive_rejections >= self.limits.max_consecutive_rejections:
                self.switches.no_new_positions = True
            result = RiskResult(False, rejected[0], rejected[1], now)
            self.events.append(result)
            return result
        self._idempotency_keys.add(request.idempotency_key)
        self.daily_order_count += 1
        self.consecutive_rejections = 0
        assert market is not None
        self.daily_turnover += market.close * request.quantity
        return RiskResult(True, "OK", "accepted", now)

    def monitor_intraday(
        self,
        *,
        now: datetime,
        account: Account,
        last_market_at: datetime | None,
        gateway_connected: bool,
        strategy_heartbeats: dict[str, datetime],
        active_orders: list[Order],
        previous_total_assets: Decimal | None = None,
    ) -> list[RiskResult]:
        findings: list[RiskResult] = []
        if last_market_at is None or now - last_market_at > self.limits.max_quote_age:
            self.switches.no_new_positions = True
            findings.append(
                RiskResult(False, "MARKET_DATA_INTERRUPTED", "market heartbeat is stale", now)
            )
        if not gateway_connected:
            self.switches.cancel_only = True
            findings.append(
                RiskResult(False, "GATEWAY_DISCONNECTED", "trading gateway is disconnected", now)
            )
        for strategy_id, heartbeat in strategy_heartbeats.items():
            if now - heartbeat > self.limits.max_strategy_heartbeat_age:
                assert self.switches.stopped_strategies is not None
                self.switches.stopped_strategies.add(strategy_id)
                findings.append(
                    RiskResult(
                        False,
                        "STRATEGY_HEARTBEAT_LOST",
                        "strategy heartbeat is stale",
                        now,
                        {"strategy_id": strategy_id},
                    )
                )
        for order in active_orders:
            if (
                order.status not in FINAL_ORDER_STATUSES
                and order.submitted_at is not None
                and now - order.submitted_at > self.limits.max_order_response_age
            ):
                findings.append(
                    RiskResult(
                        False,
                        "ORDER_RESPONSE_TIMEOUT",
                        "order has no terminal response within the configured age",
                        now,
                        {"order_id": order.order_id},
                    )
                )
        if previous_total_assets and previous_total_assets > 0:
            change = abs(account.total_assets / previous_total_assets - Decimal("1"))
            if change > self.limits.max_asset_change:
                self.switches.global_stop = True
                findings.append(
                    RiskResult(
                        False,
                        "ASSET_CHANGE_ANOMALY",
                        "account assets changed beyond the configured threshold",
                        now,
                        {"change": str(change)},
                    )
                )
        self.events.extend(findings)
        return findings

    def record_cancel(self) -> None:
        self.consecutive_cancels += 1
        if self.consecutive_cancels >= self.limits.max_consecutive_cancels:
            self.switches.no_new_positions = True

    def reset_day(self) -> None:
        self.daily_order_count = 0
        self.daily_turnover = Decimal("0")
        self.consecutive_rejections = 0
        self.consecutive_cancels = 0
        self._idempotency_keys.clear()

    def _pretrade_rejection(
        self,
        request: OrderRequest,
        *,
        account: Account,
        market: Bar | None,
        now: datetime,
    ) -> tuple[str, str] | None:
        switches = self.switches
        if switches.global_stop or switches.cancel_only:
            return "TRADING_STOPPED", "global stop or cancel-only mode is active"
        if request.account_id in (switches.stopped_accounts or set()):
            return "ACCOUNT_STOPPED", "account stop is active"
        if request.strategy_id in (switches.stopped_strategies or set()):
            return "STRATEGY_STOPPED", "strategy stop is active"
        if market is None:
            return "NO_MARKET", "market snapshot is unavailable"
        if now - market.timestamp > self.limits.max_quote_age:
            return "STALE_MARKET", "market snapshot is stale"
        if market.suspended:
            return "SUSPENDED", "instrument is suspended"
        if (
            request.direction is Direction.BUY
            and market.limit_up is not None
            and market.close >= market.limit_up
        ):
            return "LIMIT_UP", "buying at the upper price limit is blocked conservatively"
        if (
            request.direction is Direction.SELL
            and market.limit_down is not None
            and market.close <= market.limit_down
        ):
            return "LIMIT_DOWN", "selling at the lower price limit is blocked conservatively"
        if request.idempotency_key in self._idempotency_keys:
            return "DUPLICATE_ORDER", "idempotency key has already been accepted"
        if request.quantity > self.limits.max_order_quantity:
            return "MAX_QUANTITY", "order quantity exceeds risk limit"
        order_value = market.close * request.quantity
        if order_value > self.limits.max_order_value:
            return "MAX_ORDER_VALUE", "order value exceeds risk limit"
        if self.daily_order_count >= self.limits.max_daily_orders:
            return "MAX_DAILY_ORDERS", "daily order count limit reached"
        if self.daily_turnover + order_value > self.limits.max_daily_turnover:
            return "MAX_DAILY_TURNOVER", "daily turnover limit reached"
        if request.direction is Direction.BUY and switches.no_new_positions:
            return "NO_NEW_POSITIONS", "new positions are disabled"
        if request.direction is Direction.BUY and account.total_assets > 0:
            current_position = account.positions.get(request.symbol)
            current_symbol_value = (
                current_position.market_value if current_position else Decimal("0")
            )
            projected_symbol_weight = (current_symbol_value + order_value) / account.total_assets
            if projected_symbol_weight > self.limits.max_symbol_weight:
                return "MAX_SYMBOL_WEIGHT", "projected symbol weight exceeds risk limit"
            projected_total_weight = (account.positions_value + order_value) / account.total_assets
            if projected_total_weight > self.limits.max_total_position_weight:
                return "MAX_TOTAL_POSITION", "projected total position exceeds risk limit"
            industry = self.industries.get(request.symbol)
            if industry:
                industry_value = sum(
                    position.market_value
                    for symbol, position in account.positions.items()
                    if self.industries.get(symbol) == industry
                )
                if (
                    industry_value + order_value
                ) / account.total_assets > self.limits.max_industry_weight:
                    return "MAX_INDUSTRY_WEIGHT", "projected industry weight exceeds risk limit"
        if request.direction is Direction.BUY and account.available_cash < order_value:
            return "INSUFFICIENT_CASH", "available cash is insufficient"
        if request.direction is Direction.SELL:
            position = account.positions.get(request.symbol)
            if position is None or position.available_quantity < request.quantity:
                return "INSUFFICIENT_POSITION", "sellable position is insufficient"
        if request.limit_price is not None:
            deviation = abs(request.limit_price / market.close - Decimal("1"))
            if deviation > self.limits.max_price_deviation:
                return "PRICE_DEVIATION", "limit price deviates too far from market"
        return None
