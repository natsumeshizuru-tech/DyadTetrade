from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dyadtetrade.contracts import BundleImportPolicy, validate_release_bundle


def manifest(approval: str = "paper_approved") -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "strategy_id": "example_momentum",
        "strategy_version": "0.1.0",
        "source_commit": "a" * 40,
        "source_hash": "1" * 64,
        "created_at": "2026-07-19T22:00:00+08:00",
        "research_engine_version": "0.1.0",
        "runtime_api_version": "1.0",
        "strategy_mode": "declarative",
        "parameters": {"lookback": 20},
        "required_data_capabilities": ["bars.1d.none"],
        "allowed_markets": ["SH", "SZ"],
        "allowed_periods": ["1d"],
        "universe_policy": {"point_in_time": True},
        "default_risk_budget": {
            "max_position_weight": 0.2,
            "max_gross_exposure": 1.0,
            "max_daily_turnover": 1.0,
            "max_order_notional": 200000.0,
        },
        "backtest_evidence": {
            "result_id": "example-result",
            "result_hash": "2" * 64,
            "data_fingerprint": "3" * 64,
            "engine_policy": "strict_research",
            "start": "2024-01-01",
            "end": "2026-07-17",
        },
        "limitations": ["Synthetic example only."],
        "approval_status": approval,
    }


def make_bundle(tmp_path: Path, *, approval: str = "paper_approved", code: bool = False) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    files: dict[str, object] = {
        "manifest.json": manifest(approval),
        "parameters.schema.json": {"type": "object"},
        "required_data.json": {"capabilities": ["bars.1d.none"]},
        "factors.json": [],
        "risk_budget.json": {
            "max_position_weight": 0.2,
            "max_gross_exposure": 1.0,
            "max_daily_turnover": 1.0,
            "max_order_notional": 200000.0,
        },
        "backtest_summary.json": {
            "result_id": "example-result",
            "result_hash": "2" * 64,
            "data_fingerprint": "3" * 64,
            "engine_policy": "strict_research",
            "start": "2024-01-01",
            "end": "2026-07-17",
        },
        "robustness_summary.json": {"status": "not_run"},
        "data_manifest.json": {"classification": "SYNTHETIC", "data_fingerprint": "3" * 64},
        "limitations.md": "# Limitations\n\nSynthetic example only.\n",
        "LICENSES.json": {"project": "Apache-2.0"},
        "signature.json": {"status": "unsigned", "algorithm": "none"},
    }
    if code:
        files["strategy.py"] = "VALUE = 1\n"
    checksums: list[str] = []
    for name, value in files.items():
        if isinstance(value, str):
            payload = value.encode()
        else:
            payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
        (bundle / name).write_bytes(payload)
        checksums.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
    (bundle / "checksums.sha256").write_text("\n".join(sorted(checksums)) + "\n", encoding="utf-8")
    return bundle


def policy() -> BundleImportPolicy:
    return BundleImportPolicy(
        allowed_strategy_ids=frozenset({"example_momentum"}),
        runtime_api_version="1.0",
        data_capabilities=frozenset({"bars.1d.none"}),
        maximum_risk_budget={
            "max_position_weight": 0.2,
            "max_gross_exposure": 1.0,
            "max_daily_turnover": 1.0,
            "max_order_notional": 200000.0,
        },
    )


def test_paper_bundle_validates_all_gates(tmp_path: Path) -> None:
    result = validate_release_bundle(make_bundle(tmp_path), policy())
    assert result.strategy_id == "example_momentum"
    assert result.execution_target == "paper"


def test_checksum_tamper_fails_closed(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "factors.json").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_release_bundle(bundle, policy())


def test_qmt_sim_rejects_unsigned_executable_code(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, approval="qmt_sim_approved", code=True)
    with pytest.raises(ValueError, match="verified signature"):
        validate_release_bundle(bundle, policy(), execution_target="qmt_sim")


def test_risk_budget_cannot_exceed_runtime_policy(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    restrictive = BundleImportPolicy(
        allowed_strategy_ids=frozenset({"example_momentum"}),
        runtime_api_version="1.0",
        data_capabilities=frozenset({"bars.1d.none"}),
        maximum_risk_budget={"max_position_weight": 0.1},
    )
    with pytest.raises(ValueError, match="risk budget exceeds"):
        validate_release_bundle(bundle, restrictive)


def test_import_rejects_internally_inconsistent_sidecars(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    payload = json.loads((bundle / "required_data.json").read_text(encoding="utf-8"))
    payload["capabilities"] = ["bars.1m.none"]
    content = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    (bundle / "required_data.json").write_bytes(content)
    checksums = (bundle / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    checksums = [
        f"{hashlib.sha256(content).hexdigest()}  required_data.json"
        if line.endswith("  required_data.json")
        else line
        for line in checksums
    ]
    (bundle / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="required_data.json"):
        validate_release_bundle(bundle, policy())
