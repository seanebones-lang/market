"""Command-line runner for synthetic G3 design assurance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from market.research import power as power_module
from market.research.power import (
    PowerScenario,
    PowerStudyConfig,
    canonical_json_bytes,
    load_fold_layouts,
    run_power_study,
    write_power_study,
)

EXPECTED_TOP_LEVEL_FIELDS = {"schema_version", "bars", "power_study", "scenarios"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_study_definition(
    path: str | Path,
) -> tuple[int, PowerStudyConfig, tuple[PowerScenario, ...]]:
    """Load a strict, JSON-serializable synthetic study definition."""

    payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    unknown = set(payload) - EXPECTED_TOP_LEVEL_FIELDS
    missing = EXPECTED_TOP_LEVEL_FIELDS - set(payload)
    if unknown or missing:
        raise ValueError(
            f"study definition fields mismatch; missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    if payload["schema_version"] != 1:
        raise ValueError("study definition schema_version must be 1")
    bars = payload["bars"]
    if isinstance(bars, bool) or not isinstance(bars, int) or bars < 2:
        raise ValueError("study definition bars must be an integer >= 2")
    power_study = payload["power_study"]
    scenarios = payload["scenarios"]
    if not isinstance(power_study, dict) or not isinstance(scenarios, list) or not scenarios:
        raise ValueError("power_study must be an object and scenarios a nonempty array")
    try:
        config = PowerStudyConfig(**power_study)
        parsed_scenarios = tuple(PowerScenario(**scenario) for scenario in scenarios)
    except TypeError as exc:
        raise ValueError(f"invalid study definition: {exc}") from exc
    return bars, config, parsed_scenarios


def build_power_artifact(
    *,
    split_plan_path: str | Path,
    study_definition_path: str | Path,
) -> dict[str, Any]:
    """Run the declared study and bind its result to both input files."""

    split_path = Path(split_plan_path)
    definition_path = Path(study_definition_path)
    bars, config, scenarios = load_study_definition(definition_path)
    layouts = load_fold_layouts(split_path)
    payload = run_power_study(
        scenarios=scenarios,
        layouts=layouts,
        bars=bars,
        config=config,
    )
    payload["inputs"] = {
        "split_plan_path": split_path.as_posix(),
        "split_plan_sha256": _sha256(split_path),
        "study_definition_path": definition_path.as_posix(),
        "study_definition_sha256": _sha256(definition_path),
        "power_module_path": "src/market/research/power.py",
        "power_module_sha256": _sha256(Path(str(power_module.__file__))),
        "power_cli_path": "src/market/research/power_cli.py",
        "power_cli_sha256": _sha256(Path(__file__)),
    }
    # Round-trip through the canonical serializer here so a nonfinite value fails before write.
    canonical_json_bytes(payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split-plan", required=True)
    parser.add_argument("--study-definition", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = build_power_artifact(
        split_plan_path=args.split_plan,
        study_definition_path=args.study_definition,
    )
    output = write_power_study(payload, args.output)
    print(f"wrote {output} scenarios={len(payload['scenarios'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the module CLI
    raise SystemExit(main())
