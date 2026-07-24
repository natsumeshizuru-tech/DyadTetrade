from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dyadtetrade.api.legacy_app import app
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.modules.research import (
    FactorDefinition,
    FactorRegistry,
    FactorResearchService,
    FactorRunConfig,
    analyze_factor,
    fill_missing_cross_section,
)


def factor_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = date(2026, 1, 5)
    for day in range(5):
        trade_date = start + timedelta(days=day)
        for index, symbol in enumerate(("A", "B", "C", "D")):
            rows.append(
                {
                    "date": trade_date,
                    "visible_at": trade_date,
                    "symbol": symbol,
                    "close": 10 + index + day,
                    "momentum": index * (1 if day % 2 == 0 else -1) + day / 10,
                    "forward_return": (index - 1.5) / 100 + day / 1000,
                }
            )
    return pd.DataFrame(rows)


def test_factor_preprocessing_turnover_and_future_visibility() -> None:
    values = pd.Series([1.0, float("nan"), 3.0, float("nan")])
    groups = pd.Series(["x", "x", "y", "y"])
    filled = fill_missing_cross_section(values, groups=groups)
    assert filled.tolist() == [1.0, 1.0, 3.0, 3.0]

    result = analyze_factor(factor_frame(), factor_columns=["momentum"])
    assert len(result.turnover) == 4
    assert result.turnover["turnover"].dropna().between(0, 1).all()

    invalid = factor_frame()
    invalid.loc[0, "visible_at"] = date(2026, 2, 1)
    with pytest.raises(ValueError, match="not visible"):
        analyze_factor(invalid, factor_columns=["momentum"])


def test_factor_registry_rejects_conflicting_definition() -> None:
    registry = FactorRegistry()
    definition = FactorDefinition(
        name="test_factor",
        version="1.0.0",
        description="test",
        input_fields=("close",),
        code_version="test-v1",
    )
    registry.register(definition)
    registry.register(definition)
    with pytest.raises(ValueError, match="different metadata"):
        registry.register(definition.model_copy(update={"description": "changed"}))


@pytest.mark.asyncio
async def test_factor_run_is_persisted_and_idempotent(tmp_path: Path) -> None:
    await initialize_database()
    service = FactorResearchService(output_root=tmp_path)
    config = FactorRunConfig(
        definition_name="momentum_5d",
        factor_columns=["momentum"],
        input_fields=["close"],
        stock_pool="test-four-symbols",
        parameters={"lookback": 5},
        data_version="unit-data-v1",
        code_version="unit-code-v1",
    )
    key = f"factor-test-{uuid4().hex}"
    async with SessionFactory() as session:
        first = await service.create_and_run(session, config, factor_frame(), idempotency_key=key)
        second = await service.create_and_run(session, config, factor_frame(), idempotency_key=key)
    assert first.created is True
    assert second.created is False
    assert first.record.id == second.record.id
    assert first.record.status == "succeeded"
    assert first.record.stock_pool == "test-four-symbols"
    assert first.record.data_version == "unit-data-v1"
    assert first.record.code_version == "unit-code-v1"
    assert Path(first.record.result_path or "").exists()
    assert first.result["metadata"]["visible_time_field"] == "visible_at"
    assert first.result["turnover"]


def test_factor_definition_and_run_api() -> None:
    records = factor_frame()
    records[["date", "visible_at"]] = records[["date", "visible_at"]].astype(str)
    payload = {
        "records": records.astype(object).where(pd.notna(records), None).to_dict(orient="records"),
        "factor_columns": ["momentum"],
        "definition_name": "momentum_5d",
        "input_fields": ["close"],
        "stock_pool": "api-test-pool",
        "parameters": {"lookback": 5},
        "data_version": "api-data-v1",
        "code_version": "api-code-v1",
        "idempotency_key": uuid4().hex,
    }
    with TestClient(app) as client:
        definitions = client.get("/api/v1/research/factors/definitions")
        assert definitions.status_code == 200
        assert any(item["name"] == "momentum_5d" for item in definitions.json())
        response = client.post("/api/v1/research/factors/runs", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["run"]["status"] == "succeeded"
        assert body["result"]["turnover"]
        run_id = body["run"]["id"]
        detail = client.get(f"/api/v1/research/factors/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["run"]["data_version"] == "api-data-v1"
