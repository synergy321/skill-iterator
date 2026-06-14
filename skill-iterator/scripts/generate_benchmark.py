#!/usr/bin/env python3
"""Generate benchmark.json from iteration data for eval-viewer compatibility.

Reads aggregate.json + per-run grading files to build a benchmark
in v5-compatible format that the viewer can render.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from utils import load_json, load_json_strict_existing, load_eval_plan  # noqa: E402


def first_nonnull(*vals):
    """Return first non-None value. Used for field resolution across
    metrics.json + timing.json where either may carry the value (or a
    legitimate zero — hence not using `a or b` which would swallow 0)."""
    for v in vals:
        if v is not None:
            return v
    return None


# Human-readable descriptions for L1/L2 checks (old grading files lack 'description')
L1_L2_DESCRIPTIONS = {
    "L1-1": "SKILL.md exists and > 20 lines",
    "L1-2": "YAML frontmatter valid",
    "L1-3": "requirements.md exists",
    "L1-4": "eval-criteria.md exists",
    "L2-1": "YAML name is kebab-case",
    "L2-2": "3+ trigger phrases + pushy sentence",
    "L2-3": "6 required sections present",
    "L2-4": "Every Step has Input/Output",
    "L2-5": "Body < 500 lines",
    "L2-6": "Skill type marked",
    "L2-7": "Examples non-empty with real content",
    "L2-8": "Examples ≤ 30% of body",
    "L2-9": "Troubleshooting has Error/Cause/Solution",
}


def build_l3_description_map(plan: dict) -> dict[str, dict[str, str]]:
    """Build per-case L3 assertion ID → criteria map from eval plan."""
    result: dict[str, dict[str, str]] = {}
    for case in plan.get("cases", []):
        case_id = case["id"]
        result[case_id] = {}
        for a in case.get("l3_assertions", []):
            key = a.get("id") or a.get("name")
            if key:
                result[case_id][key] = a.get("criteria", "")
    return result


def collect_runs(iter_dir: Path, plan: dict) -> list[dict]:
    """Collect per-run data from results directory."""
    results_dir = iter_dir / "results"
    runs = []
    l3_desc_map = build_l3_description_map(plan)

    for case in plan.get("cases", []):
        case_id = case["id"]
        eval_id = case.get("eval_id", 0)
        num_runs = case["runs_per_configuration"]
        case_dir = results_dir / case_id

        if not case_dir.exists():
            continue

        # utils.load_eval_plan normalizes case.configurations so we read directly.
        configs = case["configurations"]
        run_dirs = []
        for config in configs:
            if num_runs > 1 and len(configs) > 1:
                for i in range(1, num_runs + 1):
                    rd = case_dir / config / f"run-{i}"
                    if rd.exists():
                        run_dirs.append((i, config, rd))
            elif num_runs > 1:
                for i in range(1, num_runs + 1):
                    rd = case_dir / f"run-{i}"
                    if rd.exists():
                        run_dirs.append((i, config, rd))
            elif len(configs) > 1:
                rd = case_dir / config
                if rd.exists():
                    run_dirs.append((1, config, rd))
            else:
                config_dir = case_dir / config
                if config_dir.exists() and config_dir.is_dir():
                    run_dirs.append((1, config, config_dir))
                else:
                    run_dirs.append((1, config, case_dir))

        case_l3_descs = l3_desc_map.get(case_id, {})

        for run_num, config, run_dir in run_dirs:
            # grading files: fail loud on corrupt-but-existing (P0 fix) so a bad
            # JSON can't silently become a 0 score and invert the headline.
            l1_l2 = load_json_strict_existing(run_dir / "grading-l1-l2.json")
            l3 = load_json_strict_existing(run_dir / "grading-l3.json")
            metrics = load_json(run_dir / "metrics.json")
            timing = load_json(run_dir / "timing.json")

            # Count pass/fail across all levels
            all_checks = l1_l2.get("l1", []) + l1_l2.get("l2", [])
            l3_assertions = l3.get("assertions", l3.get("expectations", []))

            passed = sum(1 for c in all_checks if c.get("passed")) + sum(1 for a in l3_assertions if a.get("passed"))
            total = len(all_checks) + len(l3_assertions)
            failed = total - passed

            # L3 scores
            l3_scores = [a.get("score", 0) for a in l3_assertions]
            mean_l3 = sum(l3_scores) / len(l3_scores) if l3_scores else 0

            # By level breakdown
            by_level = {}
            for level_name, items in [("1", l1_l2.get("l1", [])), ("2", l1_l2.get("l2", [])), ("3", l3_assertions)]:
                level_passed = sum(1 for c in items if c.get("passed"))
                level_total = len(items)
                by_level[level_name] = {
                    "passed": level_passed,
                    "total": level_total,
                    "pass_rate": round(level_passed / level_total, 2) if level_total else 0.0,
                }

            # Build expectations list with proper descriptions
            expectations = []
            for c in all_checks:
                cid = c.get("id", "")
                desc = c.get("description", "") or L1_L2_DESCRIPTIONS.get(cid, "")
                expectations.append({
                    "text": f"{cid}: {desc}",
                    "passed": c.get("passed", False),
                    "evidence": c.get("evidence", ""),
                })
            for a in l3_assertions:
                aid = a.get("id", "")
                desc = a.get("criteria", "") or case_l3_descs.get(aid, "")
                expectations.append({
                    "text": f"{aid}: {desc}",
                    "passed": a.get("passed", False),
                    "evidence": a.get("evidence", ""),
                    "score": a.get("score", 0),
                })

            # Timing, token, tool call fields span metrics.json (executor) +
            # timing.json (orchestrator-backfilled tokens). Read both.
            time_seconds = first_nonnull(
                metrics.get("time_seconds"),
                metrics.get("duration_seconds"),
                timing.get("total_duration_seconds"),
                timing.get("executor_duration_seconds"),
            )
            tokens = first_nonnull(
                metrics.get("tokens"),
                metrics.get("total_tokens"),
                timing.get("total_tokens"),
            )
            tool_calls = first_nonnull(
                metrics.get("total_tool_calls"),
                metrics.get("tool_calls"),
                timing.get("tool_uses"),
            )
            errors = metrics.get("errors_encountered")

            result_data = {
                "pass_rate": round(passed / total, 4) if total else 0.0,
                "passed": passed,
                "failed": failed,
                "total": total,
                "l3_mean_score": round(mean_l3, 2),
            }
            if time_seconds is not None:
                result_data["time_seconds"] = time_seconds
            if tokens is not None:
                result_data["tokens"] = tokens
            if tool_calls is not None:
                result_data["tool_calls"] = tool_calls
            if errors is not None:
                result_data["errors"] = errors

            runs.append({
                "eval_id": eval_id,
                "eval_name": case.get("name", case_id),
                "configuration": config,
                "run_number": run_num,
                "run_status": "completed",
                "result": result_data,
                "by_level": by_level,
                "expectations": expectations,
            })

    return runs


def compute_summary(runs: list[dict]) -> dict:
    """Compute run_summary statistics."""
    configs: dict[str, list[dict]] = {}
    for run in runs:
        config = run["configuration"]
        configs.setdefault(config, []).append(run)

    summary = {}
    for config, config_runs in configs.items():
        pass_rates = [r["result"]["pass_rate"] for r in config_runs]
        l3_scores = [r["result"]["l3_mean_score"] for r in config_runs]
        times = [r["result"]["time_seconds"] for r in config_runs if "time_seconds" in r["result"]]
        tokens = [r["result"]["tokens"] for r in config_runs if "tokens" in r["result"]]
        tool_calls_list = [r["result"]["tool_calls"] for r in config_runs if "tool_calls" in r["result"]]

        n = len(pass_rates)
        pr_mean = sum(pass_rates) / n if n else 0
        l3_mean = sum(l3_scores) / n if n else 0

        # stddev
        pr_var = sum((x - pr_mean) ** 2 for x in pass_rates) / (n - 1) if n > 1 else 0
        l3_var = sum((x - l3_mean) ** 2 for x in l3_scores) / (n - 1) if n > 1 else 0

        # By level aggregated
        level_agg: dict[str, list[float]] = {}
        for r in config_runs:
            for level, data in r.get("by_level", {}).items():
                level_agg.setdefault(level, []).append(data["pass_rate"])

        by_level_summary = {}
        for level, rates in level_agg.items():
            by_level_summary[level] = {
                "mean": round(sum(rates) / len(rates), 4),
                "count": len(rates),
            }

        config_summary = {
            "pass_rate": {
                "mean": round(pr_mean, 4),
                "stddev": round(pr_var ** 0.5, 4),
                "min": round(min(pass_rates), 4) if pass_rates else 0,
                "max": round(max(pass_rates), 4) if pass_rates else 0,
            },
            "l3_mean_score": {
                "mean": round(l3_mean, 2),
                "stddev": round(l3_var ** 0.5, 2),
                "min": round(min(l3_scores), 2) if l3_scores else 0,
                "max": round(max(l3_scores), 2) if l3_scores else 0,
            },
            "by_level": by_level_summary,
            "runs": n,
        }

        if times:
            t_mean = sum(times) / len(times)
            t_var = sum((x - t_mean) ** 2 for x in times) / (len(times) - 1) if len(times) > 1 else 0
            config_summary["time_seconds"] = {
                "mean": round(t_mean, 1),
                "stddev": round(t_var ** 0.5, 1),
                "min": round(min(times), 1),
                "max": round(max(times), 1),
            }

        if tokens:
            tk_mean = sum(tokens) / len(tokens)
            tk_var = sum((x - tk_mean) ** 2 for x in tokens) / (len(tokens) - 1) if len(tokens) > 1 else 0
            config_summary["tokens"] = {
                "mean": round(tk_mean),
                "stddev": round(tk_var ** 0.5),
                "min": min(tokens),
                "max": max(tokens),
            }

        if tool_calls_list:
            tc_mean = sum(tool_calls_list) / len(tool_calls_list)
            tc_var = sum((x - tc_mean) ** 2 for x in tool_calls_list) / (len(tool_calls_list) - 1) if len(tool_calls_list) > 1 else 0
            config_summary["tool_calls"] = {
                "mean": round(tc_mean, 1),
                "stddev": round(tc_var ** 0.5, 1),
                "min": min(tool_calls_list),
                "max": max(tool_calls_list),
            }

        summary[config] = config_summary

    return summary


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/generate_benchmark.py <iteration_dir> [--skill-name NAME]")
        sys.exit(1)

    iter_dir = Path(sys.argv[1]).resolve()

    # Parse --skill-name (default "Skill" — callers should pass real name via cmd_finalize)
    skill_name = "Skill"
    for i, arg in enumerate(sys.argv):
        if arg == "--skill-name" and i + 1 < len(sys.argv):
            skill_name = sys.argv[i + 1]
            break

    # load_eval_plan normalizes: eval_id injection, runs_per_configuration
    # canonicalization, default configurations/mode. Replaces all the inline
    # fallback logic that was previously here (Cut I / I+ / L superseded).
    plan = load_eval_plan(iter_dir)
    if not plan:
        print(f"Error: no eval plan found in {iter_dir}", file=sys.stderr)
        sys.exit(1)

    runs = collect_runs(iter_dir, plan)
    summary = compute_summary(runs)

    # Build overall summary: per-config means + delta (if both configs present).
    # WHY: previous "Overall" print hardcoded with_skill and labeled it "Overall",
    # which was wrong whenever without_skill also ran or wasn't run at all.
    overall: dict = {}
    for cfg, stats in summary.items():
        pr = stats.get("pass_rate", {}).get("mean")
        l3 = stats.get("l3_mean_score", {}).get("mean")
        overall[cfg] = {
            "pass_rate_mean": pr,
            "l3_mean_score": l3,
            "runs": stats.get("runs", 0),
        }
    if "with_skill" in overall and "without_skill" in overall:
        ws = overall["with_skill"]
        wos = overall["without_skill"]
        if ws["pass_rate_mean"] is not None and wos["pass_rate_mean"] is not None:
            overall["delta_pass_rate"] = round(ws["pass_rate_mean"] - wos["pass_rate_mean"], 2)
        if ws["l3_mean_score"] is not None and wos["l3_mean_score"] is not None:
            overall["delta_l3_mean_score"] = round(ws["l3_mean_score"] - wos["l3_mean_score"], 2)

    benchmark = {
        "metadata": {
            "skill_name": skill_name,
            "skill_path": "SKILL.md",
            "executor_model": "claude-opus-4-20250514",
            "analyzer_model": "claude-opus-4-20250514",
            "timestamp": datetime.now().isoformat(),
            "iteration": plan.get("iteration", 0),
            "evals_run": sorted(set(r["eval_id"] for r in runs)),
            "runs_per_configuration": max(c.get("runs_per_configuration", c.get("runs", 1)) for c in plan.get("cases", [])),
        },
        "runs": runs,
        "run_summary": summary,
        "overall": overall,
        "notes": [
            f"Iteration {plan.get('iteration', '?')} baseline benchmark",
            "Configuration 'with_skill' = SKILL.md guiding executor",
            "L3 scores use 1-5 rubric (iteration 3+)",
        ],
    }

    out_path = iter_dir / "benchmark.json"
    out_path.write_text(json.dumps(benchmark, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Benchmark written to {out_path}")
    print(f"  {len(runs)} runs across {len(set(r['eval_id'] for r in runs))} evals")
    for cfg, stats in overall.items():
        if not isinstance(stats, dict):
            continue
        pr = stats.get("pass_rate_mean")
        l3 = stats.get("l3_mean_score")
        print(f"  [{cfg}] pass_rate={pr if pr is not None else 'N/A'}  L3_mean={l3 if l3 is not None else 'N/A'}  runs={stats.get('runs', 0)}")
    if "delta_pass_rate" in overall:
        print(f"  delta (with - without):  pass_rate={overall['delta_pass_rate']:+}  L3_mean={overall.get('delta_l3_mean_score', 'N/A'):+}")


if __name__ == "__main__":
    main()
