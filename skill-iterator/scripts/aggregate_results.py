#!/usr/bin/env python3
"""Aggregate pass@3 results: read multiple run gradings, compute mean/stddev for L3 scores."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from utils import load_json as load_json_safe, load_json_strict_existing, load_eval_plan  # noqa: E402


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


# Kept for legacy stub in case any code path below still references the inline
# helper's sentinel return shape; utils.load_json matches exactly.
def _load_json_safe_legacy_stub(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  WARNING: Invalid JSON in {path}: {e}", file=sys.stderr)
            return {}


def aggregate_case(case_dir: Path, case_id: str, runs: int, configurations: list[str] | None = None) -> dict:
    """Aggregate multiple runs for a single case, handling configurations."""
    if configurations is None:
        configurations = ["with_skill"]

    result = {
        "case_id": case_id,
        "runs_expected": runs * len(configurations),
        "runs_found": 0,
        "l1_l2": [],
        "l3_scores": {},
        "l3_pass_rates": {},
        "by_configuration": {},
    }

    for config in configurations:
        config_result = {"l3_scores": {}, "l3_pass_rates": {}}

        for run_idx in range(1, runs + 1):
            if len(configurations) > 1 and runs > 1:
                run_dir = case_dir / config / f"run-{run_idx}"
            elif len(configurations) > 1:
                run_dir = case_dir / config
            elif runs > 1:
                run_dir = case_dir / f"run-{run_idx}"
            else:
                config_dir = case_dir / config
                if config_dir.exists() and config_dir.is_dir():
                    run_dir = config_dir
                else:
                    run_dir = case_dir

            if not run_dir.exists():
                continue

            result["runs_found"] += 1

            # Read L1/L2 grading
            l1_l2_file = run_dir / "grading-l1-l2.json"
            if l1_l2_file.exists():
                data = load_json_strict_existing(l1_l2_file)
                summary = data.get("summary", {})
                result["l1_l2"].append({
                    "run": run_idx,
                    "configuration": config,
                    "l1_pass": summary.get("l1_pass", 0),
                    "l1_total": summary.get("l1_total", 0),
                    "l2_pass": summary.get("l2_pass", 0),
                    "l2_total": summary.get("l2_total", 0),
                })

            # Read L3 grading (with scores)
            l3_file = run_dir / "grading-l3.json"
            if l3_file.exists():
                l3_data = load_json_strict_existing(l3_file)
                assertions = l3_data.get("assertions", l3_data.get("expectations", []))
                for assertion in assertions:
                    aid = assertion.get("id", "unknown")
                    score = assertion.get("score", 0)
                    passed = assertion.get("passed", False)

                    for target in [result["l3_scores"], config_result["l3_scores"]]:
                        if aid not in target:
                            target[aid] = []
                        target[aid].append(score)

                    for target in [result["l3_pass_rates"], config_result["l3_pass_rates"]]:
                        if aid not in target:
                            target[aid] = []
                        target[aid].append(1 if passed else 0)

        # Summarize per-config
        config_summary = {}
        for aid, scores in config_result["l3_scores"].items():
            config_summary[aid] = {
                "mean_score": round(mean(scores), 2),
                "scores": scores,
            }
        result["by_configuration"][config] = config_summary

    # Compute aggregates.
    # stability = within-config run-to-run variance, NOT cross-config delta.
    # WHY: with runs_per_configuration=1 and 2 configs, cross-config diff
    # would mark every real A/B signal as "unstable" (false positive).
    # config_delta is the legitimate cross-config comparison, kept separate.
    result["l3_summary"] = {}
    for aid, scores in result["l3_scores"].items():
        per_config_scores = {
            cfg: cfg_summary.get(aid, {}).get("scores", [])
            for cfg, cfg_summary in result["by_configuration"].items()
        }

        within_stddevs = [
            stddev(cfg_scores)
            for cfg_scores in per_config_scores.values()
            if len(cfg_scores) >= 2
        ]
        max_within_stddev = max(within_stddevs) if within_stddevs else 0.0

        config_means = {
            cfg: round(mean(cfg_scores), 2)
            for cfg, cfg_scores in per_config_scores.items()
            if cfg_scores
        }
        delta = None
        if "with_skill" in config_means and "without_skill" in config_means:
            delta = round(config_means["with_skill"] - config_means["without_skill"], 2)

        result["l3_summary"][aid] = {
            "mean_score": round(mean(scores), 2),
            "pass_rate": round(mean(result["l3_pass_rates"][aid]), 2),
            "scores": scores,
            "by_config_mean": config_means,
            "config_delta": delta,
            "within_config_stddev": round(max_within_stddev, 2),
            "stable": max_within_stddev < 0.5,
        }

    return result


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 aggregate_results.py <iteration_dir>")
        print("  e.g.: python3 aggregate_results.py evals/workspace/iteration-3/")
        sys.exit(1)

    iter_dir = Path(sys.argv[1])
    plan = load_eval_plan(iter_dir)
    if not plan:
        print(f"eval-plan.json not found in {iter_dir}")
        sys.exit(1)
    results_dir = iter_dir / "results"

    all_results = []
    for case in plan.get("cases", []):
        case_id = case["id"]
        runs = case["runs_per_configuration"]
        configs = case["configurations"]
        case_dir = results_dir / case_id
        if case_dir.exists():
            result = aggregate_case(case_dir, case_id, runs, configs)
            all_results.append(result)
        else:
            all_results.append({"case_id": case_id, "runs_found": 0, "error": "directory not found"})

    # Overall summary
    all_scores = []
    unstable = []
    for r in all_results:
        for aid, summary in r.get("l3_summary", {}).items():
            all_scores.append(summary["mean_score"])
            if not summary["stable"]:
                unstable.append(f"{r['case_id']}/{aid}")

    output = {
        "iteration": plan.get("iteration"),
        "cases": all_results,
        "overall": {
            "mean_l3_score": round(mean(all_scores), 2) if all_scores else None,
            "unstable_assertions": unstable,
            "total_cases": len(all_results),
            "cases_with_data": sum(1 for r in all_results if r.get("runs_found", 0) > 0),
        },
    }

    output_path = iter_dir / "aggregate.json"
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
