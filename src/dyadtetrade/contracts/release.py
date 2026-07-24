from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

REQUIRED_BUNDLE_FILES = frozenset(
    {
        "manifest.json",
        "parameters.schema.json",
        "required_data.json",
        "factors.json",
        "risk_budget.json",
        "backtest_summary.json",
        "robustness_summary.json",
        "data_manifest.json",
        "limitations.md",
        "LICENSES.json",
        "checksums.sha256",
        "signature.json",
    }
)
OPTIONAL_BUNDLE_FILES = frozenset({"strategy.py"})
APPROVAL_ORDER = (
    "draft",
    "candidate",
    "reviewed",
    "paper_approved",
    "qmt_sim_approved",
    "live_candidate",
    "live_approved",
)
ExecutionTarget = Literal["paper", "qmt_sim", "qmt_live"]
SignatureVerifier = Callable[[Path, Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class BundleImportPolicy:
    allowed_strategy_ids: frozenset[str]
    runtime_api_version: str
    data_capabilities: frozenset[str]
    maximum_risk_budget: Mapping[str, float]
    signature_verifier: SignatureVerifier | None = None


@dataclass(frozen=True, slots=True)
class BundleValidation:
    bundle_path: Path
    bundle_hash: str
    strategy_id: str
    strategy_version: str
    approval_status: str
    execution_target: ExecutionTarget
    validated_at: str


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return payload


def _validate_file_set(bundle: Path) -> set[str]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("bundle must be a real directory")
    files: set[str] = set()
    for entry in bundle.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise ValueError(f"bundle contains a link or nested path: {entry.name}")
        files.add(entry.name)
    missing = REQUIRED_BUNDLE_FILES - files
    unexpected = files - REQUIRED_BUNDLE_FILES - OPTIONAL_BUNDLE_FILES
    if missing:
        raise ValueError(f"bundle is missing required files: {sorted(missing)}")
    if unexpected:
        raise ValueError(f"bundle contains unsupported files: {sorted(unexpected)}")
    return files


def _validate_checksums(bundle: Path, files: set[str]) -> str:
    lines = (bundle / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    expected: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2:
            raise ValueError("invalid checksums.sha256 line")
        digest, name = parts
        if Path(name).name != name or name == "checksums.sha256":
            raise ValueError("checksum entry must name a plain bundle file")
        if name in expected:
            raise ValueError(f"duplicate checksum entry: {name}")
        expected[name] = digest
    required_checksums = files - {"checksums.sha256"}
    if set(expected) != required_checksums:
        raise ValueError("checksum manifest does not exactly cover bundle files")
    for name, digest in expected.items():
        actual = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
        if not hmac.compare_digest(actual, digest):
            raise ValueError(f"checksum mismatch: {name}")
    return hashlib.sha256((bundle / "checksums.sha256").read_bytes()).hexdigest()


def _validate_manifest(bundle: Path) -> dict[str, Any]:
    schema = _load_json(_project_root() / "contracts" / "strategy-release-bundle.schema.json")
    Draft202012Validator.check_schema(schema)
    manifest = _load_json(bundle / "manifest.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
    if errors:
        message = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"invalid StrategyReleaseBundle manifest: {message}")
    return manifest


def _validate_artifact_consistency(bundle: Path, manifest: Mapping[str, Any]) -> None:
    parameters_schema = _load_json(bundle / "parameters.schema.json")
    Draft202012Validator.check_schema(parameters_schema)
    required_data = _load_json(bundle / "required_data.json")
    if required_data.get("capabilities") != manifest["required_data_capabilities"]:
        raise ValueError("required_data.json does not match the manifest")
    if _load_json(bundle / "risk_budget.json") != manifest["default_risk_budget"]:
        raise ValueError("risk_budget.json does not match the manifest")
    evidence = manifest["backtest_evidence"]
    summary = _load_json(bundle / "backtest_summary.json")
    for key in ("result_id", "result_hash", "data_fingerprint", "engine_policy", "start", "end"):
        if summary.get(key) != evidence[key]:
            raise ValueError(f"backtest_summary.json does not match the manifest: {key}")
    data_manifest = _load_json(bundle / "data_manifest.json")
    if data_manifest.get("data_fingerprint") != evidence["data_fingerprint"]:
        raise ValueError("data_manifest.json does not match the backtest data fingerprint")
    limitations = (bundle / "limitations.md").read_text(encoding="utf-8")
    if any(str(item) not in limitations for item in manifest["limitations"]):
        raise ValueError("limitations.md does not cover every manifest limitation")


def _validate_approval(status: str, target: ExecutionTarget) -> None:
    if status == "revoked" or status not in APPROVAL_ORDER:
        raise ValueError(f"bundle approval is not usable: {status}")
    minimum = {
        "paper": "paper_approved",
        "qmt_sim": "qmt_sim_approved",
        "qmt_live": "live_approved",
    }[target]
    if APPROVAL_ORDER.index(status) < APPROVAL_ORDER.index(minimum):
        raise ValueError(f"{target} requires {minimum} or stronger approval")
    if target == "qmt_live" and status != "live_approved":
        raise ValueError("qmt_live requires exact live_approved status")


def _validate_risk_budget(manifest: Mapping[str, Any], policy: BundleImportPolicy) -> None:
    actual = manifest["default_risk_budget"]
    if not isinstance(actual, dict):
        raise ValueError("default_risk_budget must be an object")
    for name, maximum in policy.maximum_risk_budget.items():
        value = actual.get(name)
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"risk budget field is missing or non-numeric: {name}")
        if float(value) > float(maximum):
            raise ValueError(f"risk budget exceeds import policy: {name}")


def validate_release_bundle(
    bundle_path: Path,
    policy: BundleImportPolicy,
    *,
    execution_target: ExecutionTarget = "paper",
) -> BundleValidation:
    """Fail closed before a research release can reach any execution runtime."""

    if bundle_path.is_symlink():
        raise ValueError("bundle path must not be a symlink")
    bundle = bundle_path.resolve(strict=True)
    files = _validate_file_set(bundle)
    bundle_hash = _validate_checksums(bundle, files)
    manifest = _validate_manifest(bundle)
    _validate_artifact_consistency(bundle, manifest)
    strategy_id = str(manifest["strategy_id"])
    if strategy_id not in policy.allowed_strategy_ids:
        raise ValueError(f"strategy is not allowlisted: {strategy_id}")
    if manifest["runtime_api_version"] != policy.runtime_api_version:
        raise ValueError("runtime API version is incompatible")
    required = set(manifest["required_data_capabilities"])
    missing = required - policy.data_capabilities
    if missing:
        raise ValueError(f"required data capabilities are unavailable: {sorted(missing)}")
    _validate_risk_budget(manifest, policy)
    approval_status = str(manifest["approval_status"])
    _validate_approval(approval_status, execution_target)
    if execution_target in {"qmt_sim", "qmt_live"} and "strategy.py" in files:
        signature = _load_json(bundle / "signature.json")
        if policy.signature_verifier is None or not policy.signature_verifier(bundle, signature):
            raise ValueError("executable strategy code requires a configured verified signature")
    return BundleValidation(
        bundle_path=bundle,
        bundle_hash=bundle_hash,
        strategy_id=strategy_id,
        strategy_version=str(manifest["strategy_version"]),
        approval_status=approval_status,
        execution_target=execution_target,
        validated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
