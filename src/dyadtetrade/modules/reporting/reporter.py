from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from dyadtetrade.modules.backtest.result import BacktestResult
from dyadtetrade.modules.performance import PerformanceResult


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    json_path: Path
    equity_csv_path: Path
    orders_csv_path: Path
    trades_csv_path: Path
    positions_csv_path: Path
    fees_csv_path: Path
    html_path: Path
    pdf_path: Path
    svg_path: Path


def generate_backtest_report(
    result: BacktestResult,
    performance: PerformanceResult,
    output_directory: Path,
    *,
    market_bars: pd.DataFrame | None = None,
    primary_symbol: str | None = None,
) -> ReportArtifacts:
    output_directory.mkdir(parents=True, exist_ok=True)
    prefix = output_directory / result.run_id
    json_path = prefix.with_suffix(".json")
    equity_csv_path = output_directory / f"{result.run_id}_equity.csv"
    orders_csv_path = output_directory / f"{result.run_id}_orders.csv"
    trades_csv_path = output_directory / f"{result.run_id}_trades.csv"
    positions_csv_path = output_directory / f"{result.run_id}_positions.csv"
    fees_csv_path = output_directory / f"{result.run_id}_fees.csv"
    html_path = prefix.with_suffix(".html")
    pdf_path = prefix.with_suffix(".pdf")
    svg_path = prefix.with_suffix(".svg")

    payload = result.to_summary()
    payload["metrics"] = performance.metrics
    payload["fees"] = _fee_summary(result)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    performance.equity.to_csv(equity_csv_path, index=False)
    pd.DataFrame([_order_row(order) for order in result.orders]).to_csv(
        orders_csv_path, index=False
    )
    pd.DataFrame([asdict(trade) for trade in result.trades]).to_csv(trades_csv_path, index=False)
    pd.DataFrame(
        [
            {"symbol": symbol, "market_value": str(value)}
            for symbol, value in result.final_positions.items()
        ]
    ).to_csv(positions_csv_path, index=False)
    pd.DataFrame([_fee_summary(result)]).to_csv(fees_csv_path, index=False)
    svg_path.write_text(
        _equity_svg(performance.equity, market_bars, result.trades, primary_symbol),
        encoding="utf-8",
    )
    html_path.write_text(_html_report(result, performance, svg_path.name), encoding="utf-8")
    _pdf_report(
        result,
        performance,
        pdf_path,
        market_bars=market_bars,
        primary_symbol=primary_symbol,
    )
    return ReportArtifacts(
        json_path,
        equity_csv_path,
        orders_csv_path,
        trades_csv_path,
        positions_csv_path,
        fees_csv_path,
        html_path,
        pdf_path,
        svg_path,
    )


def _fee_summary(result: BacktestResult) -> dict[str, float]:
    return {
        "commission": sum(float(trade.commission) for trade in result.trades),
        "stamp_duty": sum(float(trade.tax) for trade in result.trades),
        "transfer_fee": sum(float(trade.transfer_fee) for trade in result.trades),
        "total": sum(float(trade.total_fees) for trade in result.trades),
    }


def _order_row(order: Any) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "client_order_id": order.request.client_order_id,
        "symbol": order.request.symbol,
        "direction": order.request.direction.value,
        "quantity": order.request.quantity,
        "filled_quantity": order.filled_quantity,
        "status": order.status.value,
        "average_fill_price": str(order.average_fill_price),
        "reject_reason": order.reject_reason,
    }


def _equity_svg(
    equity: pd.DataFrame,
    market_bars: pd.DataFrame | None,
    trades: list[Any],
    primary_symbol: str | None,
) -> str:
    values = equity["net_value"].astype(float).tolist()
    drawdown = equity["drawdown"].astype(float).tolist()
    dates = [pd.Timestamp(value).normalize() for value in pd.to_datetime(equity["date"])]
    width, height, padding = 960, 620, 50
    if not values:
        values = [1.0]
        drawdown = [0.0]
        dates = [pd.Timestamp("1970-01-01")]

    aligned_bars = _align_market_bars(market_bars, dates, primary_symbol)

    def points(series: list[float], top: int, chart_height: int) -> str:
        low, high = min(series), max(series)
        span = high - low or 1.0
        return " ".join(
            f"{padding + index * (width - 2 * padding) / max(len(series) - 1, 1):.1f},"
            f"{top + chart_height - (value - low) / span * chart_height:.1f}"
            for index, value in enumerate(series)
        )

    price_low = min((bar[2] for bar in aligned_bars.values()), default=0.0)
    price_high = max((bar[1] for bar in aligned_bars.values()), default=1.0)
    price_span = price_high - price_low or 1.0

    def x_position(index: int) -> float:
        return padding + index * (width - 2 * padding) / max(len(dates) - 1, 1)

    candles: list[str] = []
    for index, day in enumerate(dates):
        bar = aligned_bars.get(day)
        if bar is None:
            continue
        open_price, high, low, close = bar
        x = x_position(index)
        high_y = 55 + (price_high - high) / price_span * 185
        low_y = 55 + (price_high - low) / price_span * 185
        open_y = 55 + (price_high - open_price) / price_span * 185
        close_y = 55 + (price_high - close) / price_span * 185
        color = "#fb7185" if close >= open_price else "#35d0ba"
        candles.append(
            f'<line x1="{x:.1f}" y1="{high_y:.1f}" x2="{x:.1f}" y2="{low_y:.1f}" '
            f'stroke="{color}"/><rect x="{x - 2.5:.1f}" y="{min(open_y, close_y):.1f}" '
            f'width="5" height="{max(abs(open_y - close_y), 1):.1f}" fill="{color}"/>'
        )

    trade_markers: list[str] = []
    date_positions = {day: index for index, day in enumerate(dates)}
    for trade in trades:
        if primary_symbol is not None and trade.symbol != primary_symbol:
            continue
        day = pd.Timestamp(trade.timestamp).normalize()
        trade_index = date_positions.get(day)
        if trade_index is None:
            continue
        y = 55 + (price_high - float(trade.price)) / price_span * 185
        color = "#facc15" if str(trade.direction.value) == "buy" else "#c084fc"
        trade_markers.append(
            f'<circle class="trade-marker" cx="{x_position(trade_index):.1f}" cy="{y:.1f}" '
            f'r="5" fill="{color}" stroke="#08111f"/>'
        )

    benchmark_line = ""
    if "benchmark" in equity:
        benchmark = equity["benchmark"].astype(float)
        if benchmark.notna().any():
            normalized = (benchmark / benchmark.dropna().iloc[0]).ffill().fillna(1.0).tolist()
            benchmark_line = (
                f'<polyline points="{points(normalized, 305, 130)}" fill="none" '
                'stroke="#60a5fa" stroke-width="1.5"/>'
            )
    first_date = pd.Timestamp(dates[0]).strftime("%Y-%m-%d")
    last_date = pd.Timestamp(dates[-1]).strftime("%Y-%m-%d")
    return f"""<svg xmlns="http://www.w3.org/2000/svg"
 width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#08111f"/>
<text x="50" y="28" fill="#dbeafe" font-family="sans-serif">K-line / Trades</text>
<line x1="50" y1="250" x2="910" y2="250" stroke="#24364d"/>
{"".join(candles)}{"".join(trade_markers)}
<text x="50" y="288" fill="#dbeafe" font-family="sans-serif">Net value / Benchmark</text>
<polyline points="{points(values, 305, 130)}" fill="none" stroke="#35d0ba" stroke-width="2"/>
{benchmark_line}
<text x="50" y="472" fill="#dbeafe" font-family="sans-serif">Drawdown</text>
<polyline points="{points(drawdown, 490, 80)}" fill="none" stroke="#fb7185" stroke-width="2"/>
<text x="50" y="600" fill="#72849d" font-family="sans-serif">{first_date}</text>
<text x="840" y="600" fill="#72849d" font-family="sans-serif">{last_date}</text>
</svg>"""


def _align_market_bars(
    market_bars: pd.DataFrame | None,
    dates: list[pd.Timestamp],
    primary_symbol: str | None = None,
) -> dict[pd.Timestamp, tuple[float, float, float, float]]:
    if market_bars is None or market_bars.empty:
        return {}
    frame = market_bars.copy()
    if "symbol" in frame and frame["symbol"].nunique() > 1:
        available = sorted(frame["symbol"].astype(str).unique())
        selected_symbol = primary_symbol if primary_symbol in available else available[0]
        frame = frame.loc[frame["symbol"] == selected_symbol]
    time_column = "datetime" if "datetime" in frame else "date"
    required = {time_column, "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        return {}
    frame[time_column] = pd.to_datetime(frame[time_column]).dt.normalize()
    frame = frame.drop_duplicates(time_column, keep="last").set_index(time_column)
    wanted = set(dates)
    rows: dict[pd.Timestamp, tuple[float, float, float, float]] = {}
    for day, row in frame.iterrows():
        timestamp = pd.Timestamp(str(day)).normalize()
        if timestamp not in wanted:
            continue
        rows[timestamp] = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )
    return rows


def _html_report(result: BacktestResult, performance: PerformanceResult, svg_name: str) -> str:
    metric_rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in performance.metrics.items()
    )
    order_rows = "".join(
        f"<tr><td>{html.escape(order.order_id)}</td><td>{html.escape(order.request.symbol)}</td>"
        f"<td>{order.request.direction.value}</td><td>{order.request.quantity}</td>"
        f"<td>{order.filled_quantity}</td><td>{order.status.value}</td></tr>"
        for order in result.orders
    )
    trade_rows = "".join(
        f"<tr><td>{html.escape(trade.trade_id)}</td><td>{html.escape(trade.symbol)}</td>"
        f"<td>{trade.direction.value}</td><td>{trade.quantity}</td><td>{trade.price}</td>"
        f"<td>{trade.total_fees}</td></tr>"
        for trade in result.trades
    )
    position_rows = "".join(
        f"<tr><td>{html.escape(symbol)}</td><td>{value}</td></tr>"
        for symbol, value in result.final_positions.items()
    )
    fees = _fee_summary(result)
    config_json = html.escape(json.dumps(result.config_snapshot, ensure_ascii=False, indent=2))
    fee_text = (
        f"总费用 {fees['total']:.4f}，佣金 {fees['commission']:.4f}，"
        f"印花税 {fees['stamp_duty']:.4f}，过户费 {fees['transfer_fee']:.4f}"
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>DyadTetrade 回测报告 {html.escape(result.run_id)}</title>
<style>
body{{font:14px sans-serif;max-width:1100px;margin:auto;padding:24px;color:#172033}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}
img{{width:100%}}
</style></head>
<body><h1>DyadTetrade 回测报告</h1><p>Run ID: {html.escape(result.run_id)}</p>
<p>数据指纹: {html.escape(result.data_fingerprint)}</p>
<img src="{html.escape(svg_name)}" alt="净值和回撤">
<h2>绩效指标</h2><table>{metric_rows}</table>
<h2>参数与数据版本</h2><pre>{config_json}</pre>
<h2>订单</h2><table><tr><th>ID</th><th>标的</th><th>方向</th><th>数量</th><th>成交</th><th>状态</th></tr>{order_rows}</table>
<h2>成交与费用</h2><p>{fee_text}</p>
<table><tr><th>ID</th><th>标的</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th></tr>{trade_rows}</table>
<h2>期末持仓</h2><table><tr><th>标的</th><th>市值</th></tr>{position_rows}</table>
<h2>审计摘要</h2>
<p>订单 {len(result.orders)}，成交 {len(result.trades)}；
策略版本 {html.escape(result.strategy_version)}。</p>
</body></html>"""


def _pdf_report(
    result: BacktestResult,
    performance: PerformanceResult,
    output_path: Path,
    *,
    market_bars: pd.DataFrame | None,
    primary_symbol: str | None,
) -> None:
    canvas = Canvas(str(output_path), pagesize=A4)
    width, height = A4
    canvas.setTitle(f"DyadTetrade Backtest {result.run_id}")
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(40, height - 50, "DyadTetrade Backtest Report")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(40, height - 70, f"Run ID: {result.run_id}")
    canvas.drawString(40, height - 85, f"Data fingerprint: {result.data_fingerprint}")
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(40, height - 115, "Aligned trading-day overview")
    chart_left, chart_right = 40.0, float(width - 40)
    dates = [
        pd.Timestamp(value).normalize() for value in pd.to_datetime(performance.equity["date"])
    ]
    bars = _align_market_bars(market_bars, dates, primary_symbol)
    if bars:
        low = min(item[2] for item in bars.values())
        high = max(item[1] for item in bars.values())
        span = high - low or 1.0
        for index, day in enumerate(dates):
            bar = bars.get(day)
            if bar is None:
                continue
            x = chart_left + index * (chart_right - chart_left) / max(len(dates) - 1, 1)
            open_price, bar_high, bar_low, close = bar

            def price_y(value: float) -> float:
                return float(height - 265 + (value - low) / span * 120)

            if close < open_price:
                canvas.setStrokeColorRGB(0.2, 0.7, 0.6)
            else:
                canvas.setStrokeColorRGB(0.9, 0.35, 0.4)
            canvas.line(x, price_y(bar_low), x, price_y(bar_high))
            canvas.line(x - 1.5, price_y(open_price), x, price_y(open_price))
            canvas.line(x, price_y(close), x + 1.5, price_y(close))
        date_positions = {day: index for index, day in enumerate(dates)}
        for trade in result.trades:
            if primary_symbol is not None and trade.symbol != primary_symbol:
                continue
            trade_index = date_positions.get(pd.Timestamp(trade.timestamp).normalize())
            if trade_index is None:
                continue
            x = chart_left + trade_index * (chart_right - chart_left) / max(len(dates) - 1, 1)
            canvas.setFillColorRGB(0.95, 0.75, 0.1)
            canvas.circle(x, price_y(float(trade.price)), 2.5, fill=1, stroke=0)
    values = performance.equity["net_value"].astype(float).tolist()
    benchmark_values: list[float] = []
    if "benchmark" in performance.equity:
        benchmark = performance.equity["benchmark"].astype(float)
        if benchmark.notna().any():
            benchmark_values = (benchmark / benchmark.dropna().iloc[0]).ffill().fillna(1.0).tolist()
    comparable_values = values + benchmark_values
    value_low, value_high = min(comparable_values), max(comparable_values)
    value_span = value_high - value_low or 1.0
    canvas.setStrokeColorRGB(0.1, 0.65, 0.55)
    for index in range(1, len(values)):
        x1 = chart_left + (index - 1) * (chart_right - chart_left) / max(len(values) - 1, 1)
        x2 = chart_left + index * (chart_right - chart_left) / max(len(values) - 1, 1)
        y1 = height - 455 + (values[index - 1] - value_low) / value_span * 90
        y2 = height - 455 + (values[index] - value_low) / value_span * 90
        canvas.line(x1, y1, x2, y2)
    if benchmark_values:
        canvas.setStrokeColorRGB(0.25, 0.5, 0.9)
        for index in range(1, len(benchmark_values)):
            x1 = chart_left + (index - 1) * (chart_right - chart_left) / max(
                len(benchmark_values) - 1, 1
            )
            x2 = chart_left + index * (chart_right - chart_left) / max(len(benchmark_values) - 1, 1)
            y1 = height - 455 + (benchmark_values[index - 1] - value_low) / value_span * 90
            y2 = height - 455 + (benchmark_values[index] - value_low) / value_span * 90
            canvas.line(x1, y1, x2, y2)
    drawdown = performance.equity["drawdown"].astype(float).tolist()
    drawdown_low = min(drawdown) if drawdown else -1.0
    drawdown_span = abs(drawdown_low) or 1.0
    canvas.setStrokeColorRGB(0.9, 0.3, 0.4)
    for index in range(1, len(drawdown)):
        x1 = chart_left + (index - 1) * (chart_right - chart_left) / max(len(drawdown) - 1, 1)
        x2 = chart_left + index * (chart_right - chart_left) / max(len(drawdown) - 1, 1)
        y1 = height - 535 + (drawdown[index - 1] - drawdown_low) / drawdown_span * 45
        y2 = height - 535 + (drawdown[index] - drawdown_low) / drawdown_span * 45
        canvas.line(x1, y1, x2, y2)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(chart_left, height - 285, "K-line / trades")
    canvas.drawString(chart_left, height - 475, "Net value / benchmark / drawdown")
    canvas.showPage()
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(40, height - 50, "Metrics and audit summary")
    canvas.setFont("Helvetica", 9)
    y = height - 75
    for key, value in performance.metrics.items():
        canvas.drawString(40, y, f"{key}: {value}")
        y -= 13
        if y < 50:
            canvas.showPage()
            canvas.setFont("Helvetica", 9)
            y = height - 50
    canvas.save()
