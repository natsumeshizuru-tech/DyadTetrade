from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from subprocess import Popen
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.core.config import Settings, get_settings
from dyadtetrade.db.models import StrategyFileRecord
from dyadtetrade.modules.strategy.validation import validate_strategy_source

SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
TEMPLATE = """from dyadtetrade.modules.strategy.base import Strategy, StrategyContext
from dyadtetrade.domain import Bar


class MyStrategy(Strategy):
    name = "my_strategy"
    version = "1.0.0"
    description = "User strategy"

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        if context.get_position(bar.symbol) is None:
            context.buy(bar.symbol, 100)
"""


class StrategyFileService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.user_strategies_path.resolve()
        self.backup_root = self.root / ".backups"
        self.published_root = self.root / ".published"
        self._processes: dict[str, Popen[bytes]] = {}

    def safe_path(self, name: str) -> Path:
        candidate = name[:-3] if name.endswith(".py") else name
        if not SAFE_NAME.fullmatch(candidate):
            raise ValueError("strategy name must contain only letters, digits and underscore")
        path = (self.root / f"{candidate}.py").resolve()
        if path.parent != self.root:
            raise ValueError("strategy path traversal is forbidden")
        return path

    async def list(self, session: AsyncSession) -> list[dict[str, object]]:
        records = (
            await session.scalars(select(StrategyFileRecord).order_by(StrategyFileRecord.name))
        ).all()
        return [self._view(item, include_code=False) for item in records]

    async def get(self, session: AsyncSession, name: str) -> dict[str, object]:
        record = await self._record(session, name)
        return self._view(record, include_code=True)

    async def create(
        self,
        session: AsyncSession,
        name: str,
        code: str | None = None,
        clone_from: str | None = None,
    ) -> dict[str, object]:
        path = self.safe_path(name)
        if path.exists() or await self._optional_record(session, name) is not None:
            raise FileExistsError(name)
        self.root.mkdir(parents=True, exist_ok=True)
        source = code or TEMPLATE.replace("my_strategy", path.stem)
        if clone_from:
            source = self.safe_path(clone_from).read_text(encoding="utf-8")
            source = source.replace(f'name = "{clone_from}"', f'name = "{path.stem}"')
        path.write_text(source, encoding="utf-8")
        digest = self._digest(source)
        record = StrategyFileRecord(
            name=path.stem,
            relative_path=path.name,
            draft_sha256=digest,
            validation_status="draft",
            smoke_status="pending",
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return self._view(record, include_code=True)

    async def update(self, session: AsyncSession, name: str, code: str) -> dict[str, object]:
        record = await self._record(session, name)
        path = self.safe_path(name)
        if path.exists():
            self.backup_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            shutil.copy2(path, self.backup_root / f"{path.stem}_{stamp}.py")
        path.write_text(code, encoding="utf-8")
        record.draft_sha256 = self._digest(code)
        record.validation_status = "draft"
        record.smoke_status = "pending"
        record.last_error = None
        await session.commit()
        return self._view(record, include_code=True)

    async def validate(self, session: AsyncSession, name: str) -> dict[str, object]:
        record = await self._record(session, name)
        path = self.safe_path(name)
        result = validate_strategy_source(path.read_text(encoding="utf-8"), str(path))
        record.validation_status = "valid" if result["valid"] else "invalid"
        record.last_error = None if result["valid"] else "; ".join(result["errors"])
        await session.commit()
        return dict(result)

    async def smoke(
        self,
        session: AsyncSession,
        name: str,
        *,
        symbol: str,
        parameters: dict[str, Any],
        timeout: float = 30,
    ) -> dict[str, object]:
        record = await self._record(session, name)
        if record.validation_status != "valid":
            raise PermissionError("strategy must pass validation before smoke backtest")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "dyadtetrade.modules.strategy.validation",
            "--file",
            str(self.safe_path(name)),
            "--smoke",
            "--symbol",
            symbol,
            "--parameters",
            json.dumps(parameters),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            record.smoke_status = "failed"
            record.last_error = "smoke backtest timeout"
            await session.commit()
            raise
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-1000:]
            record.smoke_status = "failed"
            record.last_error = message
            await session.commit()
            raise RuntimeError(message)
        result = json.loads(stdout.decode("utf-8"))
        record.smoke_status = "passed"
        record.last_error = None
        await session.commit()
        return dict(result)

    async def publish(self, session: AsyncSession, name: str) -> dict[str, object]:
        record = await self._record(session, name)
        source = self.safe_path(name).read_text(encoding="utf-8")
        digest = self._digest(source)
        if record.validation_status != "valid" or record.smoke_status != "passed":
            raise PermissionError("validation and smoke backtest must pass before publish")
        if digest != record.draft_sha256:
            raise PermissionError("draft changed after validation")
        version = (record.published_version or 0) + 1
        target = self.published_root / name / f"v{version}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        record.published_version = version
        record.published_sha256 = digest
        await session.commit()
        return self._view(record, include_code=False)

    async def start(self, session: AsyncSession, name: str) -> dict[str, object]:
        record = await self._record(session, name)
        if record.published_version is None or record.published_sha256 != record.draft_sha256:
            raise PermissionError("only the current published version can start")
        existing = self._processes.get(name)
        if existing is not None and existing.poll() is None:
            return self._view(record, include_code=False)
        heartbeat = Path("logs/strategies") / f"{name}.heartbeat"
        log_path = Path("logs/strategies") / f"{name}.log"
        published = self.published_root / name / f"v{record.published_version}.py"
        process = Popen(
            [
                sys.executable,
                "-m",
                "dyadtetrade.modules.strategy.validation",
                "--file",
                str(published),
                "--heartbeat",
                str(heartbeat),
                "--log",
                str(log_path),
            ],
            cwd=Path.cwd(),
        )
        self._processes[name] = process
        record.runtime_status = "running"
        record.heartbeat_at = datetime.now()
        record.last_error = None
        await session.commit()
        return self._view(record, include_code=False)

    async def stop(self, session: AsyncSession, name: str) -> dict[str, object]:
        record = await self._record(session, name)
        process = self._processes.pop(name, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except TimeoutError:
                process.kill()
        record.runtime_status = "stopped"
        await session.commit()
        return self._view(record, include_code=False)

    async def refresh_runtime(self, session: AsyncSession, name: str) -> dict[str, object]:
        record = await self._record(session, name)
        process = self._processes.get(name)
        heartbeat = Path("logs/strategies") / f"{name}.heartbeat"
        if process is not None and process.poll() is None and heartbeat.exists():
            record.runtime_status = "running"
            record.heartbeat_at = datetime.fromtimestamp(heartbeat.stat().st_mtime)
        elif record.runtime_status == "running":
            record.runtime_status = "failed"
            record.last_error = "strategy subprocess stopped"
        await session.commit()
        return self._view(record, include_code=False)

    async def _record(self, session: AsyncSession, name: str) -> StrategyFileRecord:
        record = await self._optional_record(session, name)
        if record is None:
            raise FileNotFoundError(name)
        return record

    async def _optional_record(self, session: AsyncSession, name: str) -> StrategyFileRecord | None:
        normalized = self.safe_path(name).stem
        return cast(
            StrategyFileRecord | None,
            await session.scalar(
                select(StrategyFileRecord).where(StrategyFileRecord.name == normalized)
            ),
        )

    def _view(self, record: StrategyFileRecord, *, include_code: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "name": record.name,
            "validation_status": record.validation_status,
            "smoke_status": record.smoke_status,
            "published_version": record.published_version,
            "runtime_status": record.runtime_status,
            "heartbeat_at": record.heartbeat_at.isoformat() if record.heartbeat_at else None,
            "last_error": record.last_error,
            "updated_at": record.updated_at.isoformat(),
        }
        if include_code:
            result["code"] = self.safe_path(record.name).read_text(encoding="utf-8")
        log_path = Path("logs/strategies") / f"{record.name}.log"
        result["logs"] = (
            log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
            if log_path.exists()
            else []
        )
        return result

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


strategy_file_service = StrategyFileService()
