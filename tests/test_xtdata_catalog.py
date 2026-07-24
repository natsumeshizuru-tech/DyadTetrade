from __future__ import annotations

from typing import Any

from dyadtetrade.api.routes import xtdata
from dyadtetrade.data.xtdata_catalog import catalog_payload


def test_catalog_covers_official_download_families_and_level2_boundary() -> None:
    payload = catalog_payload(
        {
            "connected": True,
            "available_periods": ["1d", "1m", "announcement"],
            "sector_count": 12,
            "functions": {
                "download_history_data2": True,
                "download_financial_data2": True,
                "download_sector_data": True,
                "download_history_contracts": True,
                "download_holiday_data": True,
                "download_cb_data": True,
                "download_etf_info": True,
                "download_index_weight": True,
            },
        }
    )

    assert {item["value"] for item in payload["datasets"]} == {
        "history",
        "financial",
        "sector",
        "history_contracts",
        "holiday",
        "convertible_bond",
        "etf",
        "index_weight",
    }
    periods = {item["value"] for item in payload["periods"]}
    assert {"tick", "1d", "1y", "announcement", "historymaincontract"} <= periods
    assert payload["level2_history_downloadable"] is False


def test_cache_only_download_uses_whitelisted_gateway(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    class FakeGateway:
        def download_dataset(self, dataset: str, **parameters: Any) -> None:
            calls.append({"dataset": dataset, **parameters})

    monkeypatch.setattr(xtdata, "_gateway", lambda: FakeGateway())
    monkeypatch.setattr(xtdata, "_settings", lambda: xtdata.load_data_config())
    request = xtdata.XtDataDownloadRequest(
        dataset="holiday",
        symbols=[],
        import_to_shared_store=False,
    )

    result = xtdata._run_download(request)

    assert result["status"] == "completed"
    assert result["imported_to_shared_store"] is False
    assert calls[0]["dataset"] == "holiday"
