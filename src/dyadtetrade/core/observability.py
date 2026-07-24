from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "dyadtetrade_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
HTTP_LATENCY = Histogram(
    "dyadtetrade_http_request_duration_seconds", "HTTP request latency", ["method", "path"]
)
BACKTEST_TASKS = Gauge("dyadtetrade_backtest_tasks", "Backtest task count by state", ["status"])
MARKET_CONNECTION = Gauge(
    "dyadtetrade_miniqmt_connected", "Whether miniQMT market data is connected"
)
PAPER_RECONCILED = Gauge(
    "dyadtetrade_paper_reconciled", "Whether the paper account has a reconciliation timestamp"
)
REALTIME_WATCHLIST = Gauge(
    "dyadtetrade_realtime_watchlist_symbols", "Bounded real-time watchlist size"
)
REALTIME_QUOTE_AGE = Gauge(
    "dyadtetrade_realtime_quote_age_seconds", "Age of the newest real-time quote"
)
RUNNING_STRATEGIES = Gauge(
    "dyadtetrade_running_strategies", "Number of in-process running strategies"
)
QMT_ACCOUNT_CONNECTED = Gauge(
    "dyadtetrade_qmt_account_connected", "Whether the QMT account is connected and subscribed"
)
