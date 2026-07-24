from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import FactorDefinitionRecord, FactorRunRecord
from dyadtetrade.modules.research.factors import FactorAnalysisResult, analyze_factor
from dyadtetrade.modules.research.registry import BUILTIN_FACTOR_REGISTRY, FactorRegistry


class FactorRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_name: str
    definition_version: str | None = None
    factor_columns: list[str] = Field(min_length=1, max_length=20)
    input_fields: list[str] = Field(min_length=1, max_length=100)
    return_column: str = "forward_return"
    groups: int = Field(default=5, ge=2, le=20)
    max_decay: int = Field(default=5, ge=1, le=20)
    stock_pool: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    data_version: str
    code_version: str
    visible_time_field: str = "visible_at"


@dataclass(frozen=True, slots=True)
class FactorRunOutput:
    record: FactorRunRecord
    result: dict[str, Any]
    created: bool


class FactorResearchService:
    def __init__(
        self,
        *,
        registry: FactorRegistry = BUILTIN_FACTOR_REGISTRY,
        output_root: Path = Path("reports/factors"),
    ) -> None:
        self.registry = registry
        self.output_root = output_root

    async def sync_definitions(self, session: AsyncSession) -> list[FactorDefinitionRecord]:
        rows: list[FactorDefinitionRecord] = []
        for definition in self.registry.list():
            row = await session.scalar(
                select(FactorDefinitionRecord).where(
                    FactorDefinitionRecord.name == definition.name,
                    FactorDefinitionRecord.version == definition.version,
                )
            )
            if row is None:
                row = FactorDefinitionRecord(
                    name=definition.name,
                    version=definition.version,
                    description=definition.description,
                    input_fields_json=json.dumps(definition.input_fields),
                    parameters_schema_json=json.dumps(definition.parameters_schema),
                    code_version=definition.code_version,
                    enabled=definition.enabled,
                )
                session.add(row)
            else:
                row.description = definition.description
                row.input_fields_json = json.dumps(definition.input_fields)
                row.parameters_schema_json = json.dumps(definition.parameters_schema)
                row.code_version = definition.code_version
                row.enabled = definition.enabled
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
        return rows

    async def create_and_run(
        self,
        session: AsyncSession,
        config: FactorRunConfig,
        frame: pd.DataFrame,
        *,
        idempotency_key: str | None = None,
    ) -> FactorRunOutput:
        definitions = await self.sync_definitions(session)
        definition = self.registry.get(config.definition_name, config.definition_version)
        definition_row = next(
            row
            for row in definitions
            if row.name == definition.name and row.version == definition.version
        )
        missing_fields = sorted(set(definition.input_fields).difference(config.input_fields))
        if missing_fields:
            raise ValueError(f"factor run input_fields omit definition fields: {missing_fields}")
        missing_data_fields = sorted(set(config.input_fields).difference(frame.columns))
        if missing_data_fields:
            raise ValueError(
                f"factor run data missing declared input fields: {missing_data_fields}"
            )
        key = idempotency_key or self._idempotency_key(config, frame)
        existing = await session.scalar(
            select(FactorRunRecord).where(FactorRunRecord.idempotency_key == key)
        )
        if existing is not None and existing.status == "succeeded" and existing.result_path:
            return FactorRunOutput(existing, self._read_result(Path(existing.result_path)), False)
        if existing is None:
            existing = FactorRunRecord(
                idempotency_key=key,
                definition_id=definition_row.id,
                name=definition.name,
                status="pending",
                progress=0.0,
                config_json=config.model_dump_json(),
                input_fields_json=json.dumps(config.input_fields),
                stock_pool=config.stock_pool,
                parameters_json=json.dumps(config.parameters, ensure_ascii=False),
                data_version=config.data_version,
                code_version=config.code_version,
                visible_time_field=config.visible_time_field,
            )
            session.add(existing)
            await session.commit()
            await session.refresh(existing)
        existing.status = "running"
        existing.progress = 10.0
        existing.error_message = None
        await session.commit()
        try:
            analysis = analyze_factor(
                frame,
                factor_columns=config.factor_columns,
                return_column=config.return_column,
                visible_at_column=config.visible_time_field,
                groups=config.groups,
                max_decay=config.max_decay,
            )
            payload = self._result_payload(analysis, config)
            output_directory = self.output_root / f"run_{existing.id}"
            output_directory.mkdir(parents=True, exist_ok=True)
            result_path = output_directory / "result.json"
            result_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            analysis.daily_ic.to_csv(output_directory / "daily_ic.csv", index=False)
            analysis.group_returns.to_csv(output_directory / "group_returns.csv", index=False)
            analysis.turnover.to_csv(output_directory / "turnover.csv", index=False)
            existing.result_path = str(result_path)
            existing.status = "succeeded"
            existing.progress = 100.0
            existing.calculated_at = datetime.now()
            existing.finished_at = datetime.now()
            await session.commit()
            return FactorRunOutput(existing, payload, True)
        except Exception as exc:
            await session.rollback()
            failed = await session.get(FactorRunRecord, existing.id)
            assert failed is not None
            failed.status = "failed"
            failed.error_message = f"{type(exc).__name__}: {exc}"
            failed.finished_at = datetime.now()
            await session.commit()
            raise

    @staticmethod
    def _idempotency_key(config: FactorRunConfig, frame: pd.DataFrame) -> str:
        hashed = np.asarray(pd.util.hash_pandas_object(frame, index=True), dtype=np.uint64)
        frame_hash = hashlib.sha256(hashed.tobytes()).hexdigest()
        payload = json.dumps(
            {"config": json.loads(config.model_dump_json()), "frame_hash": frame_hash},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        serializable = frame.copy()
        serializable.columns = [
            "_".join(str(item) for item in column if str(item))
            if isinstance(column, tuple)
            else str(column)
            for column in serializable.columns
        ]
        raw: object = json.loads(serializable.to_json(orient="records", date_format="iso"))
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise ValueError("factor result frame did not serialize to records")
        return [dict(item) for item in raw]

    def _result_payload(
        self, result: FactorAnalysisResult, config: FactorRunConfig
    ) -> dict[str, Any]:
        return {
            "metadata": json.loads(config.model_dump_json()),
            "summary": result.summary,
            "daily_ic": self._frame_records(result.daily_ic),
            "group_returns": self._frame_records(result.group_returns),
            "long_short": self._frame_records(result.long_short),
            "decay": self._frame_records(result.decay),
            "turnover": self._frame_records(result.turnover),
            "correlation": result.correlation.to_dict(),
            "exposures": self._frame_records(result.exposures.reset_index()),
        }

    @staticmethod
    def _read_result(path: Path) -> dict[str, Any]:
        return dict(json.loads(path.read_text(encoding="utf-8")))
