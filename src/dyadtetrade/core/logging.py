from __future__ import annotations

import json
import logging
import logging.handlers
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
SENSITIVE_PATTERN = re.compile(
    r"(?i)(account|token|secret|password|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)"
)


def redact_sensitive(value: str) -> str:
    return SENSITIVE_PATTERN.sub(r"\1\2***", value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
            "request_id": request_id_context.get(),
        }
        for field in ("task_id", "strategy_id", "order_id", "trace_id"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact_sensitive(str(value))
        if record.exc_info:
            payload["exception"] = redact_sensitive(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_path: Path = Path("logs/dyadtetrade.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if any(getattr(handler, "name", "") == "dyadtetrade-json" for handler in root.handlers):
        return
    formatter = JsonFormatter()
    console = logging.StreamHandler()
    console.name = "dyadtetrade-json"
    console.setFormatter(formatter)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.name = "dyadtetrade-json-file"
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)
