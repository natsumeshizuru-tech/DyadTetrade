from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_CREATED = {
    "a-share-backtest-realism",
    "backtest-validation-and-overfitting",
    "backtrader-xtquant-integration",
    "factor-research-a-share",
    "live-trading-release-gate",
    "miniqmt-environment-diagnostics",
    "qmt-bar-signal-semantics",
    "qmt-order-execution-safety",
    "quant-data-quality",
    "quant-risk-performance-reporting",
    "vectorbt-backtest",
    "vectorbt-expert",
    "vectorbt-optimize",
    "vectorbt-setup",
    "xtdata-market-data",
    "xttrader-trading-gateway",
}


def _frontmatter(text: str) -> tuple[str | None, str | None]:
    match = re.match(r"^---\s*\n(.*?)\n---", text, flags=re.DOTALL)
    if not match:
        return None, None
    block = match.group(1)
    name = re.search(r"^name:\s*(.+)$", block, flags=re.MULTILINE)
    description = re.search(r"^description:\s*(.+)$", block, flags=re.MULTILINE)
    return (
        name.group(1).strip().strip("\"'") if name else None,
        description.group(1).strip().strip("\"'") if description else None,
    )


def _count_trigger_cases(path: Path) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    positive, _, negative = text.partition("Should not trigger:")
    return positive.count("- "), negative.count("- ")


def validate(project_root: Path, home_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    roots = [("project", project_root), ("home", home_root)]
    for scope, root in roots:
        if not root.exists():
            continue
        for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                errors.append(f"{scope}:{skill_dir.name}: missing SKILL.md")
                continue
            text = skill_file.read_text(encoding="utf-8")
            name, description = _frontmatter(text)
            if not name or not description:
                errors.append(f"{scope}:{skill_dir.name}: invalid frontmatter")
            if name != skill_dir.name:
                errors.append(f"{scope}:{skill_dir.name}: name mismatch ({name})")
            missing_references = []
            for value in sorted(set(re.findall(r"`((?:references|scripts|assets)/[^`]+)`", text))):
                if not (skill_dir / value).exists():
                    missing_references.append(value)
                    errors.append(f"{scope}:{skill_dir.name}: missing reference {value}")
            trigger_counts: tuple[int, int] | None = None
            if name in PROJECT_CREATED:
                trigger_file = skill_dir / "references" / "trigger-tests.md"
                if not trigger_file.exists():
                    errors.append(f"{scope}:{skill_dir.name}: missing trigger tests")
                else:
                    trigger_counts = _count_trigger_cases(trigger_file)
                    if trigger_counts[0] < 2 or trigger_counts[1] < 2:
                        errors.append(
                            f"{scope}:{skill_dir.name}: needs two positive and negative triggers"
                        )
            records.append(
                {
                    "scope": scope,
                    "name": name,
                    "description_present": bool(description),
                    "missing_references": missing_references,
                    "trigger_tests": trigger_counts,
                }
            )

    counts = Counter(record["name"] for record in records if record["name"])
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    errors.extend(f"duplicate skill name: {name}" for name in duplicates)
    return {
        "passed": not errors,
        "skill_count": len(records),
        "duplicates": duplicates,
        "errors": errors,
        "skills": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate project and user Codex skills")
    parser.add_argument("--project-root", type=Path, default=Path(".agents/skills"))
    parser.add_argument("--home-root", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--output", type=Path, default=Path("reports/skills/validation.json"))
    args = parser.parse_args()
    report = validate(args.project_root, args.home_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
