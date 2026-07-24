"""Run a reproducible small-cap plus momentum research/report workflow.

This is a daily-bar proxy for the intraday ``tick_momentum_small_cap`` paper
strategy.  It intentionally never submits live orders.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dyadtetrade.db.session import SessionFactory
from dyadtetrade.modules.backtest.repository import result_detail
from dyadtetrade.modules.backtest.schemas import BacktestConfig
from dyadtetrade.modules.backtest.service import BacktestService
from dyadtetrade.modules.strategy import Strategy, StrategyContext, strategy_registry


class DailySmallCapMomentumParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str]
    float_shares: dict[str, float]
    lookback: int = Field(default=20, ge=2)
    small_cap_n: int = Field(default=20, ge=2)
    top_n: int = Field(default=5, ge=1)
    rebalance_every: int = Field(default=5, ge=1)
    max_total_weight: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    max_single_weight: Decimal = Field(default=Decimal("0.05"), gt=0, le=1)
    minimum_momentum: float = 0.0

    @model_validator(mode="after")
    def validate_universe(self) -> DailySmallCapMomentumParameters:
        unique = set(self.symbols)
        if not unique or self.top_n > self.small_cap_n or self.small_cap_n > len(unique):
            raise ValueError("require top_n <= small_cap_n <= unique symbol count")
        if unique - self.float_shares.keys():
            raise ValueError("float_shares are required for every symbol")
        return self


class DailySmallCapMomentumProxy(Strategy):
    name = "daily_small_cap_momentum_proxy"
    version = "1.0.0"
    description = "Daily-bar research proxy for tick momentum plus small-cap paper strategy."
    parameters_model = DailySmallCapMomentumParameters

    def on_bar(self, context: StrategyContext, bar: Any) -> None:
        parameters = DailySmallCapMomentumParameters.model_validate(self.parameters)
        date_key = context.now.date().isoformat()
        if self.state.get("last_date") == date_key:
            return
        self.state["last_date"] = date_key
        dates_seen = int(self.state.get("dates_seen", 0)) + 1
        self.state["dates_seen"] = dates_seen
        if dates_seen % parameters.rebalance_every:
            return
        history = context.history(parameters.symbols, parameters.lookback, ["symbol", "close"])
        if history.empty or "symbol" not in history:
            return
        momentum: dict[str, float] = {}
        market_caps: dict[str, float] = {}
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_index()
            if len(ordered) < parameters.lookback:
                continue
            first = float(ordered["close"].iloc[0])
            latest = float(ordered["close"].iloc[-1])
            if first <= 0 or latest <= 0:
                continue
            normalized = str(symbol)
            momentum[normalized] = latest / first - 1
            market_caps[normalized] = parameters.float_shares[normalized] * latest
        small_cap = sorted(market_caps, key=market_caps.__getitem__)[: parameters.small_cap_n]
        selected = [
            symbol
            for symbol in sorted(small_cap, key=momentum.__getitem__, reverse=True)
            if momentum[symbol] >= parameters.minimum_momentum
        ][: parameters.top_n]
        target = (
            min(parameters.max_total_weight / len(selected), parameters.max_single_weight)
            if selected
            else Decimal("0")
        )
        for symbol in parameters.symbols:
            context.order_target_percent(symbol, target if symbol in selected else Decimal("0"))


def _latest_live_configuration(database: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "select strategy_instance_id,parameters_json,state_json,last_heartbeat "
            "from strategy_runs where strategy_name='tick_momentum_small_cap' "
            "order by id desc limit 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("no persisted tick_momentum_small_cap paper run found")
        parameters = json.loads(str(row["parameters_json"]))
        state = json.loads(str(row["state_json"]))
        state["strategy_instance_id"] = row["strategy_instance_id"]
        state["last_heartbeat"] = row["last_heartbeat"]
        return parameters, state


def _instrument_names(database: Path, symbols: list[str]) -> dict[str, str]:
    placeholders = ",".join("?" for _ in symbols)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"select symbol,name from instruments where symbol in ({placeholders})", symbols
        ).fetchall()
    return {str(symbol): str(name or "") for symbol, name in rows}


def _paper_activity(database: Path, strategy_id: str, trade_date: date) -> dict[str, Any]:
    day = trade_date.isoformat()
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        trades = [
            dict(row)
            for row in connection.execute(
                "select t.symbol,i.name,t.direction,count(*) fills,sum(t.quantity) quantity,"
                "round(sum(t.quantity*t.price)/sum(t.quantity),4) vwap,"
                "round(sum(t.quantity*t.price),2) notional,round(sum(t.fees),2) fees,"
                "min(t.timestamp) first_fill_utc,max(t.timestamp) last_fill_utc "
                "from trades t left join instruments i on i.symbol=t.symbol "
                "where t.strategy_id=? and date(t.timestamp)=? "
                "group by t.symbol,i.name,t.direction order by t.symbol",
                (strategy_id, day),
            )
        ]
        rejections: Counter[str] = Counter()
        for (payload,) in connection.execute(
            "select payload_json from orders where strategy_id=? and status='RISK_REJECTED' "
            "and date(created_at)=?",
            (strategy_id, day),
        ):
            try:
                reason = json.loads(payload or "{}").get("reject_reason", "UNKNOWN")
            except json.JSONDecodeError:
                reason = "INVALID_PAYLOAD"
            rejections[str(reason)] += 1
        filled_orders = connection.execute(
            "select count(*) from orders where strategy_id=? and status='FILLED' "
            "and date(created_at)=?",
            (strategy_id, day),
        ).fetchone()[0]
    return {
        "trades": trades,
        "filled_orders": int(filled_orders),
        "distinct_bought_symbols": len(
            {item["symbol"] for item in trades if item["direction"] == "buy"}
        ),
        "buy_fills": sum(int(item["fills"]) for item in trades if item["direction"] == "buy"),
        "buy_notional": sum(
            float(item["notional"]) for item in trades if item["direction"] == "buy"
        ),
        "fees": sum(float(item["fees"]) for item in trades),
        "rejections": dict(rejections.most_common()),
    }


def _quality_summary(frame: pd.DataFrame, symbols: list[str], end_date: date) -> dict[str, Any]:
    required = {"datetime", "symbol", "open", "high", "low", "close", "volume"}
    timestamps = pd.to_datetime(frame["datetime"])
    candidates = frame.loc[frame["symbol"].isin(symbols)].copy()
    candidates["datetime"] = pd.to_datetime(candidates["datetime"])
    latest_by_symbol = candidates.groupby("symbol")["datetime"].max()
    invalid_ohlc = candidates.loc[
        (candidates[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (candidates["high"] < candidates[["open", "close", "low"]].max(axis=1))
        | (candidates["low"] > candidates[["open", "close", "high"]].min(axis=1))
        | (candidates["volume"] < 0)
    ]
    issues: list[dict[str, Any]] = []
    if invalid_ohlc.shape[0]:
        issues.append(
            {"severity": "error", "code": "invalid_ohlcv", "count": int(invalid_ohlc.shape[0])}
        )
    stale = [symbol for symbol in symbols if latest_by_symbol.get(symbol) != pd.Timestamp(end_date)]
    if stale:
        issues.append(
            {
                "severity": "warning",
                "code": "stale_latest_date",
                "count": len(stale),
                "symbols": stale,
            }
        )
    return {
        "source": "DyadTetrade bt_ready/bars_1d front-adjusted Parquet via DuckDB",
        "requested_start": timestamps.min().date().isoformat(),
        "requested_end": end_date.isoformat(),
        "symbols_requested": len(symbols),
        "symbols_present": int(candidates["symbol"].nunique()),
        "rows": int(len(candidates)),
        "duplicate_keys": int(candidates.duplicated(["datetime", "symbol"]).sum()),
        "missing_required_fields": sorted(required - set(candidates.columns)),
        "null_required_values": int(candidates[list(required)].isna().sum().sum()),
        "latest_date_covered_symbols": len(symbols) - len(stale),
        "invalid_ohlcv_rows": int(len(invalid_ohlc)),
        "issues": issues,
        "pass": not any(item["severity"] == "error" for item in issues),
    }


def _latest_signal(
    frame: pd.DataFrame, parameters: dict[str, Any], symbols: list[str]
) -> list[dict[str, Any]]:
    lookback = int(parameters.get("lookback", 20))
    scores: list[dict[str, Any]] = []
    for symbol, group in frame.loc[frame["symbol"].isin(symbols)].groupby("symbol"):
        ordered = group.sort_values("datetime").tail(lookback)
        if len(ordered) < lookback:
            continue
        first = float(ordered["close"].iloc[0])
        latest = float(ordered["close"].iloc[-1])
        if first <= 0 or latest <= 0:
            continue
        scores.append(
            {
                "symbol": str(symbol),
                "close": latest,
                "float_market_cap": float(parameters["float_shares"][str(symbol)]) * latest,
                "momentum": latest / first - 1,
            }
        )
    small_cap = sorted(scores, key=lambda item: item["float_market_cap"])[
        : int(parameters.get("small_cap_n", 20))
    ]
    return [
        item
        for item in sorted(small_cap, key=lambda item: item["momentum"], reverse=True)
        if item["momentum"] >= float(parameters.get("minimum_momentum", 0.0))
    ][: int(parameters.get("top_n", 5))]


async def _run_scenario(
    service: BacktestService,
    symbols: list[str],
    parameters: dict[str, Any],
    start_date: date,
    end_date: date,
    name: str,
    costs: dict[str, Decimal],
) -> dict[str, Any]:
    config = BacktestConfig(
        strategy_name=DailySmallCapMomentumProxy.name,
        strategy_parameters=parameters,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        initial_cash=Decimal("1000000"),
        commission_rate=costs["commission_rate"],
        minimum_commission=costs["minimum_commission"],
        stamp_duty_rate=costs["stamp_duty_rate"],
        transfer_fee_rate=costs["transfer_fee_rate"],
        slippage_bps=costs["slippage_bps"],
        max_volume_participation=Decimal("0.10"),
        allow_partial_fills=True,
        enforce_t1=True,
        conservative_limit_board=True,
        execution_delay_bars=1,
        random_seed=20260713,
        benchmark_symbol="000300.SH",
        data_version="bt_ready-v1/current-small-cap-universe-proxy/report-marker-v2",
    )
    async with SessionFactory() as session:
        task, created = await service.create_task(session, config)
        if task.status != "succeeded":
            task = await service.run_task(session, task.id)
        assert task.result_id is not None
        detail = await result_detail(session, task.result_id)
    assert detail is not None
    return {
        "scenario": name,
        "task_id": task.id,
        "result_id": task.result_id,
        "created": created,
        "metrics": detail["metrics"],
        "reports": detail["reports"],
    }


async def run(output_root: Path, database: Path, lookback_days: int) -> dict[str, Any]:
    live_parameters, live_state = _latest_live_configuration(database)
    raw_symbols = [str(item) for item in live_parameters["symbols"]]
    names = _instrument_names(database, raw_symbols)
    excluded = [
        symbol
        for symbol in raw_symbols
        if "退" in names.get(symbol, "") or "ST" in names.get(symbol, "").upper()
    ]
    symbols = [symbol for symbol in raw_symbols if symbol not in excluded]
    if len(symbols) < 20:
        raise RuntimeError("too few tradable candidates after removing ST/delisting names")
    parameters = {
        "symbols": symbols,
        "float_shares": {symbol: live_parameters["float_shares"][symbol] for symbol in symbols},
        "lookback": 20,
        "small_cap_n": 20,
        "top_n": 5,
        "rebalance_every": 5,
        "max_total_weight": "0.25",
        "max_single_weight": "0.05",
        "minimum_momentum": 0.0,
    }
    strategy_registry.register(DailySmallCapMomentumProxy)
    service = BacktestService()
    probe = BacktestConfig(
        strategy_name=DailySmallCapMomentumProxy.name,
        strategy_parameters=parameters,
        symbols=symbols,
        start_date=date(1900, 1, 1),
        end_date=date(2999, 12, 31),
        benchmark_symbol="000300.SH",
    )
    frame = service._load_frame(probe)
    end_date = pd.to_datetime(frame.loc[frame["symbol"].isin(symbols), "datetime"]).max().date()
    start_date = end_date - timedelta(days=lookback_days)
    frame = frame.loc[pd.to_datetime(frame["datetime"]).dt.date.between(start_date, end_date)]
    quality = _quality_summary(frame, symbols, end_date)
    data_hash = hashlib.sha256(
        frame[["datetime", "symbol", "open", "high", "low", "close", "volume"]]
        .sort_values(["datetime", "symbol"])
        .to_csv(index=False)
        .encode()
    ).hexdigest()
    cost_scenarios = {
        "zero_cost": {
            "commission_rate": Decimal("0"),
            "minimum_commission": Decimal("0"),
            "stamp_duty_rate": Decimal("0"),
            "transfer_fee_rate": Decimal("0"),
            "slippage_bps": Decimal("0"),
        },
        "base_cost": {
            "commission_rate": Decimal("0.0003"),
            "minimum_commission": Decimal("5"),
            "stamp_duty_rate": Decimal("0.0005"),
            "transfer_fee_rate": Decimal("0"),
            "slippage_bps": Decimal("5"),
        },
        "stressed_cost": {
            "commission_rate": Decimal("0.0006"),
            "minimum_commission": Decimal("5"),
            "stamp_duty_rate": Decimal("0.001"),
            "transfer_fee_rate": Decimal("0"),
            "slippage_bps": Decimal("15"),
        },
    }
    scenarios = [
        await _run_scenario(service, symbols, parameters, start_date, end_date, name, costs)
        for name, costs in cost_scenarios.items()
    ]
    paper = _paper_activity(database, str(live_state["strategy_instance_id"]), end_date)
    latest_signal = _latest_signal(frame, parameters, symbols)
    for item in latest_signal:
        item["name"] = names.get(item["symbol"], "")
    for item in paper["trades"]:
        for key in ("first_fill_utc", "last_fill_utc"):
            item[key.replace("_utc", "_cst")] = (
                pd.Timestamp(item[key], tz="UTC").tz_convert("Asia/Shanghai").isoformat()
            )
    payload = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "workflow": "DyadTetrade small-cap + momentum quick research workflow",
        "mode": "research_and_paper_audit_only",
        "backtest_proxy": {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "universe": {
                "raw_candidates": len(raw_symbols),
                "used": len(symbols),
                "excluded": [
                    {"symbol": symbol, "name": names.get(symbol, "")} for symbol in excluded
                ],
            },
            "parameters": parameters,
            "data_hash": data_hash,
            "quality": quality,
            "scenarios": scenarios,
            "latest_close_signal": latest_signal,
        },
        "paper_today": paper,
        "latest_intraday_target": {
            "selected": live_state.get("selected", []),
            "last_rebalance_at": live_state.get("last_rebalance_at"),
            "tick_count": live_state.get("tick_count"),
            "last_heartbeat": live_state.get("last_heartbeat"),
        },
        "validation": {
            "status": "EXPLORATORY_ONLY",
            "warnings": [
                "日线收盘代理并非原始的盘中 Tick 策略。",
                "股票池和流通股本使用当前快照而非历史时点快照，仍有幸存者和前视偏差。",
                "当前候选池过滤未排除名称含退的标的，本次代理回测中才做修正。",
                "收盘信号按下一根日线执行，并启用 T+1。",
                "费率仅为有日期的模型假设，未经经纪商或法律口径核验。",
                "本次简要流程未做 Walk-forward 和最终未触碰测试集。",
            ],
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    prefix = output_root / f"small_cap_momentum_workflow_{end_date:%Y%m%d}"
    json_path = prefix.with_suffix(".json")
    csv_path = output_root / f"small_cap_momentum_paper_buys_{end_date:%Y%m%d}.csv"
    quality_json = output_root / f"small_cap_momentum_quality_{end_date:%Y%m%d}.json"
    quality_csv = output_root / f"small_cap_momentum_quality_{end_date:%Y%m%d}.csv"
    markdown_path = prefix.with_suffix(".md")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    quality_json.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(paper["trades"][0]) if paper["trades"] else ["symbol"]
        )
        writer.writeheader()
        writer.writerows(paper["trades"])
    quality_rows = quality["issues"] or [
        {"severity": "info", "code": "quality_checks_passed", "count": 0}
    ]
    with quality_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = sorted(set().union(*(row.keys() for row in quality_rows)))
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(quality_rows)
    base = next(item for item in scenarios if item["scenario"] == "base_cost")
    lines = [
        f"# 小市值 + 动量策略简要回测与 {end_date.isoformat()} Paper 运行报告",
        "",
        "## 执行摘要",
        "",
        (
            f"- 今日共模拟买入 {paper['distinct_bought_symbols']} 只、"
            f"{paper['buy_fills']} 笔成交，名义金额 {paper['buy_notional']:,.2f} 元；"
            "均为 Paper 模拟成交。"
        ),
        (
            f"- 日线代理回测区间 {start_date.isoformat()} 至 {end_date.isoformat()}；"
            f"基准成本口径总收益 {base['metrics']['total_return']:.2%}，"
            f"年化 {base['metrics']['annual_return']:.2%}，"
            f"最大回撤 {base['metrics']['max_drawdown']:.2%}，"
            f"Sharpe {base['metrics']['sharpe']:.2f}。"
        ),
        (
            f"- 候选池 {len(raw_symbols)} 只中剔除 {len(excluded)} 只名称含 "
            "ST/退的标的；当前实现未完整排除退市整理标的。"
        ),
        (
            "- 结论等级：仅探索。日线代理、当前时点股票池和当前流通股本带来"
            "显著时点偏差，不能视为原 Tick 策略的正式历史验证。"
        ),
        "",
        "## 今日 Paper 模拟买入",
        "",
        "| 代码 | 名称 | 成交笔数 | 数量 | 均价 | 名义金额 | 费用 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in paper["trades"]:
        if item["direction"] == "buy":
            lines.append(
                f"| {item['symbol']} | {item['name']} | {item['fills']} | "
                f"{item['quantity']} | {item['vwap']:.4f} | "
                f"{item['notional']:,.2f} | {item['fees']:,.2f} |"
            )
    lines += [
        "",
        "## 成本敏感性",
        "",
        "| 场景 | 总收益 | 年化收益 | 最大回撤 | Sharpe | 换手 | 成交数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in scenarios:
        metrics = item["metrics"]
        lines.append(
            f"| {item['scenario']} | {metrics['total_return']:.2%} | "
            f"{metrics['annual_return']:.2%} | {metrics['max_drawdown']:.2%} | "
            f"{metrics['sharpe']:.2f} | {metrics['turnover']:.2f} | "
            f"{metrics['trade_count']:.0f} |"
        )
    lines += [
        "",
        "## 最新日线收盘信号（下一可交易日才可执行）",
        "",
        "| 代码 | 名称 | 20日动量 | 流通市值代理 |",
        "|---|---|---:|---:|",
    ]
    for item in latest_signal:
        lines.append(
            f"| {item['symbol']} | {item['name']} | {item['momentum']:.2%} | "
            f"{item['float_market_cap']:,.0f} |"
        )
    if not latest_signal:
        lines.append("| - | 无满足非负 20 日动量的标的 | - | - |")
    lines += [
        "",
        "## 建议下一步",
        "",
        "1. 先修复候选池：排除名称含退、非正常上市状态和缺乏可交易报价的标的。",
        "2. 降低换手：增加最小排名变化或持有缓冲区，再复跑三档成本敏感性。",
        "3. 若要验证原策略，需保存并回放历史 Tick 与时点流通股本，不能用日线代理替代。",
        "",
        "## 仍需回答的问题",
        "",
        "- 当前经纪商的真实佣金、最低佣金和滑点口径是多少？",
        "- 是否有足够长的历史 Tick 快照，支持盘中信号和成交时点的无前视回放？",
    ]
    lines += ["", "## 关键风险与限制", ""] + [
        f"- {warning}" for warning in payload["validation"]["warnings"]
    ]
    lines += [
        "",
        "## 工件",
        "",
        f"- 主结果 JSON：`{json_path}`",
        f"- 今日模拟买入 CSV：`{csv_path}`",
        f"- 数据质量 JSON/CSV：`{quality_json}` / `{quality_csv}`",
        f"- DyadTetrade 基准成本 HTML：`{base['reports']['html']}`",
        f"- DyadTetrade 基准成本 PDF：`{base['reports']['pdf']}`",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload["workflow_artifacts"] = {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "paper_buys_csv": str(csv_path),
        "quality_json": str(quality_json),
        "quality_csv": str(quality_csv),
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/sqlite/dyadtetrade.db"))
    parser.add_argument("--output-root", type=Path, default=Path("reports/workflows"))
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()
    if not 120 <= args.lookback_days <= 3650:
        raise ValueError("lookback-days must be between 120 and 3650")
    result = asyncio.run(run(args.output_root, args.database, args.lookback_days))
    print(
        json.dumps(
            {
                "status": "succeeded",
                "validation": result["validation"]["status"],
                "artifacts": result["workflow_artifacts"],
                "base_backtest": next(
                    item
                    for item in result["backtest_proxy"]["scenarios"]
                    if item["scenario"] == "base_cost"
                ),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
