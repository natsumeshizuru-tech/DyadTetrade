from __future__ import annotations

from fastapi.testclient import TestClient

from dyadtetrade.api.main import app
from dyadtetrade.core.config import Settings
from tests.test_release_bundle_contract import make_bundle


def test_strategy_bundle_validation_writes_audit_receipt(tmp_path, monkeypatch) -> None:
    make_bundle(tmp_path)
    settings = Settings(
        strategy_bundle_inbox=tmp_path,
        max_order_notional=200000,
        max_total_position_ratio=1,
    )
    from dyadtetrade.api.routes import strategy_bundles

    monkeypatch.setattr(strategy_bundles, "get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/strategy-bundles/validate",
            json={"bundle_name": "bundle", "execution_target": "paper", "actor": "test"},
        )

    assert response.status_code == 200
    assert response.json()["strategy_id"] == "example_momentum"
    assert response.json()["execution_target"] == "paper"
    assert (tmp_path.parent / "receipts").is_dir()


def test_strategy_bundle_api_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    settings = Settings(strategy_bundle_inbox=tmp_path)
    from dyadtetrade.api.routes import strategy_bundles

    monkeypatch.setattr(strategy_bundles, "get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/strategy-bundles/validate",
            json={"bundle_name": "../escape", "execution_target": "paper", "actor": "test"},
        )

    assert response.status_code == 422


def test_strategy_bundle_import_registers_a_draft_after_validation(tmp_path, monkeypatch) -> None:
    make_bundle(tmp_path, approval="qmt_sim_approved")
    settings = Settings(
        strategy_bundle_inbox=tmp_path,
        max_order_notional=300000,
        max_daily_notional=300000,
        max_strategy_daily_notional=300000,
        max_total_position_ratio=1,
    )
    from dyadtetrade.api.routes import strategy_bundles

    monkeypatch.setattr(strategy_bundles, "get_settings", lambda: settings)

    async def create_draft(_session, **kwargs):
        assert kwargs["execution_mode"].value == "qmt_sim"
        assert kwargs["strategy_id"] == "example_momentum"
        assert kwargs["universe_release_id"] == "universe-today"
        return {
            "release_id": "strategy-release-from-bundle",
            "status": "draft",
            "strategy_id": kwargs["strategy_id"],
        }

    monkeypatch.setattr(
        strategy_bundles.StrategyExecutionReleaseService,
        "create",
        create_draft,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/strategy-bundles/import",
            json={
                "bundle_name": "bundle",
                "execution_target": "qmt_sim",
                "universe_release_id": "universe-today",
                "actor": "test",
            },
        )

    assert response.status_code == 200
    assert response.json()["execution_release"]["status"] == "draft"
    receipt = tmp_path.parent / "receipts" / (f"{response.json()['bundle_hash']}-qmt_sim.json")
    assert "strategy-release-from-bundle" in receipt.read_text(encoding="utf-8")
