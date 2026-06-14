#!/usr/bin/env python3
"""Prepare iteration data for eval-viewer compatibility.

Bridges the Eric-Travis eval format to the v5 viewer format:
- eval-plan.json → eval_plan.json (with eval_id integers)
- grading-l1-l2.json + grading-l3.json → grading.json (viewer format)
- Skill files → outputs/ directory
- Creates run_status.json
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from utils import load_json, load_json_strict_existing, write_json, load_eval_plan  # noqa: E402


# Files the infrastructure writes for its own bookkeeping — never belong in
# outputs/ (viewer shows outputs/ as "what the subject-under-test produced").
# case_output mode uses this filter to pick up everything ELSE the executor
# wrote (DESIGN.md, blindtest-output.html, comparison-report.md, etc.).
RESERVED_RUN_FILES = {
    # grading (orchestrator / grader artifacts):
    "grading.json", "grading-l1-l2.json", "grading-l3.json", "grading-l3-raw.md",
    # executor bookkeeping:
    "metrics.json", "timing.json", "run_status.json", "eval_metadata.json",
    "transcript.md", "user_notes.md", "timing_start.txt",
}


def hide_root_grading(iter_dir: Path) -> None:
    """Rename root-level grading.json so viewer doesn't treat iteration dir as a run."""
    root_grading = iter_dir / "grading.json"
    if root_grading.exists():
        dest = iter_dir / "grading-overall.json"
        root_grading.rename(dest)
        print(f"  renamed grading.json → grading-overall.json (prevents viewer collision)")


def convert_eval_plan(iter_dir: Path) -> dict:
    """Load + normalize plan via utils.load_eval_plan (injects eval_id,
    runs_per_configuration, configurations, mode). Write eval_plan.json so
    downstream scripts can load the normalized copy directly."""
    plan = load_eval_plan(iter_dir)
    if not plan:
        print(f"Error: eval-plan.json not found in {iter_dir}", file=sys.stderr)
        sys.exit(1)

    # Ensure each case has a display `name` — viewer uses this directly.
    for i, case in enumerate(plan.get("cases", []), start=1):
        if "name" not in case:
            case["name"] = case.get("id", f"case-{i}")

    out_path = iter_dir / "eval_plan.json"
    write_json(out_path, plan)
    print(f"  wrote {out_path.name}")
    return plan


def merge_grading(run_dir: Path, mode: str = "skill_production") -> dict:
    """Combine grading-l1-l2.json + grading-l3.json into viewer-compatible grading.json.

    L1/L2 checks inspect the SKILL.md artifact itself (YAML frontmatter,
    required sections, etc.). They only apply when the subject-under-test is
    a produced SKILL.md — i.e. mode=skill_production. For case_output /
    interaction modes there is no SKILL.md to lint, so grading-l1-l2.json
    being absent is expected, not a warning.
    """
    if mode == "skill_production" and not (run_dir / "grading-l1-l2.json").exists():
        print(f"  WARNING: {run_dir.name}/grading-l1-l2.json missing — L1/L2 results will be empty")
    if not (run_dir / "grading-l3.json").exists():
        print(f"  WARNING: {run_dir.name}/grading-l3.json missing — L3 results will be empty")
    l1_l2 = load_json_strict_existing(run_dir / "grading-l1-l2.json")
    l3 = load_json_strict_existing(run_dir / "grading-l3.json")

    expectations = []

    for item in l1_l2.get("l1", []):
        expectations.append({
            "text": f"{item['id']}: {item.get('evidence', '')}",
            "passed": item.get("passed", False),
            "evidence": item.get("evidence", ""),
            "level": 1,
        })

    for item in l1_l2.get("l2", []):
        expectations.append({
            "text": f"{item['id']}: {item.get('evidence', '')}",
            "passed": item.get("passed", False),
            "evidence": item.get("evidence", ""),
            "level": 2,
        })

    for item in l3.get("assertions", l3.get("expectations", [])):
        expectations.append({
            "text": f"{item['id']}: {item.get('criteria', '')}",
            "passed": item.get("passed", False),
            "evidence": item.get("evidence", ""),
            "score": item.get("score", 0),
            "level": 3,
        })

    # Build summary
    by_level: dict[str, dict] = {}
    total_pass = 0
    total = len(expectations)

    for exp in expectations:
        level = str(exp["level"])
        if level not in by_level:
            by_level[level] = {"passed": 0, "total": 0, "pass_rate": 0.0}
        by_level[level]["total"] += 1
        if exp["passed"]:
            by_level[level]["passed"] += 1
            total_pass += 1

    for level_data in by_level.values():
        level_data["pass_rate"] = round(level_data["passed"] / level_data["total"], 2) if level_data["total"] else 0.0

    grading = {
        "expectations": expectations,
        "summary": {
            "passed": total_pass,
            "failed": total - total_pass,
            "total": total,
            "pass_rate": round(total_pass / total, 2) if total else 0.0,
            "by_level": by_level,
        },
    }

    write_json(run_dir / "grading.json", grading)
    return grading


def setup_outputs(run_dir: Path, mode: str) -> None:
    """Create outputs/ directory with relevant files."""
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    if mode == "skill_production":
        # Find the skill subdirectory (e.g., daily-standup/)
        for child in run_dir.iterdir():
            if child.is_dir() and child.name != "outputs":
                skill_md = child / "SKILL.md"
                if skill_md.exists():
                    shutil.copy2(skill_md, outputs_dir / "SKILL.md")
                    break
        transcript = run_dir / "transcript.md"
        if transcript.exists():
            shutil.copy2(transcript, outputs_dir / "transcript.md")
    elif mode == "case_output":
        # Pick up any subject-under-test artifact the executor wrote at top level.
        # Reserved files (bookkeeping / grading) are filtered out.
        copy_exts = {".md", ".html", ".json", ".txt", ".csv", ".svg", ".png"}
        for f in run_dir.iterdir():
            if not f.is_file():
                continue
            if f.name in RESERVED_RUN_FILES:
                continue
            if f.suffix.lower() not in copy_exts:
                continue
            shutil.copy2(f, outputs_dir / f.name)
    else:
        # Interaction case: copy response.md
        response = run_dir / "response.md"
        if response.exists():
            shutil.copy2(response, outputs_dir / "response.md")
        transcript = run_dir / "transcript.md"
        if transcript.exists():
            shutil.copy2(transcript, outputs_dir / "transcript.md")


def setup_run_status(run_dir: Path) -> None:
    """Create run_status.json."""
    write_json(run_dir / "run_status.json", {"status": "completed"})


def write_eval_metadata(case_dir: Path, case: dict) -> None:
    """Write eval_metadata.json in case dir so viewer can find prompt, eval_id, name."""
    metadata = {
        "eval_id": case.get("eval_id"),
        "name": case.get("name", case.get("id", "")),
        "prompt": case.get("prompt", ""),
        "assertions": case.get("l3_assertions", []),
        "runs_per_configuration": case["runs_per_configuration"],
    }
    write_json(case_dir / "eval_metadata.json", metadata)


def process_case(results_dir: Path, case: dict) -> int:
    """Process a single eval case. Returns number of runs processed."""
    case_id = case["id"]
    case_dir = results_dir / case_id
    if not case_dir.exists():
        print(f"  skip {case_id} (not found)")
        return 0

    # utils.load_eval_plan normalizes these fields upstream so we can read directly.
    runs = case["runs_per_configuration"]
    configs = case["configurations"]
    mode = case["mode"]
    processed = 0

    # Write metadata at case level (viewer checks run_dir.parent for it)
    write_eval_metadata(case_dir, case)
    # Also write metadata into each config subdir so viewer can find it
    for config in configs:
        config_dir = case_dir / config
        if config_dir.exists() and config_dir.is_dir():
            write_eval_metadata(config_dir, case)

    # Build list of run dirs
    run_dirs = []
    if len(configs) > 1 and runs > 1:
        for config in configs:
            for run_idx in range(1, runs + 1):
                rd = case_dir / config / f"run-{run_idx}"
                if rd.exists():
                    run_dirs.append(rd)
    elif len(configs) > 1:
        for config in configs:
            rd = case_dir / config
            if rd.exists():
                run_dirs.append(rd)
    elif runs > 1:
        for run_idx in range(1, runs + 1):
            rd = case_dir / f"run-{run_idx}"
            if rd.exists():
                run_dirs.append(rd)
    else:
        # Single config, single run — check if executor wrote to config subdir
        config = configs[0] if configs else "with_skill"
        config_dir = case_dir / config
        if config_dir.exists() and config_dir.is_dir():
            run_dirs.append(config_dir)
        else:
            run_dirs.append(case_dir)

    for run_dir in run_dirs:
        merge_grading(run_dir, mode)
        setup_outputs(run_dir, mode)
        setup_run_status(run_dir)
        processed += 1

    print(f"  {case_id}: {processed} run(s)")
    return processed


def process_case_minimal(results_dir: Path, case: dict) -> int:
    """Minimal-mode: only set up outputs/ + run_status, skip grading merge.

    For when user has run a few cases by hand (no executor agent, no grader)
    and just wants to visually review the outputs in the viewer. design-md
    iteration-1 hit exactly this: case outputs existed in /tmp/, but no
    grading-l1-l2.json or grading-l3.json — full pipeline refused to run,
    no viewer either. Minimal mode unblocks that.
    """
    case_id = str(case.get("id", case.get("eval_id", "unknown")))
    case_dir = results_dir / case_id
    if not case_dir.exists():
        print(f"  SKIP {case_id}: directory not found")
        return 0

    processed = 0
    for run_dir in case_dir.rglob("*"):
        if not run_dir.is_dir():
            continue
        # Heuristic: a "run dir" is one that contains output files directly,
        # or has an outputs/ subdir already
        has_outputs = (run_dir / "outputs").exists() or any(
            f.suffix in {".md", ".html", ".txt", ".json", ".csv"} for f in run_dir.iterdir() if f.is_file()
        )
        if not has_outputs:
            continue

        # Determine mode based on parent dir name
        mode = "with_skill" if "with_skill" in str(run_dir) else (
            "without_skill" if "without_skill" in str(run_dir) else "with_skill"
        )
        try:
            setup_outputs(run_dir, mode)
            setup_run_status(run_dir)
            write_eval_metadata(run_dir.parent if run_dir.parent.name in {"with_skill", "without_skill"} else run_dir, case)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN {run_dir.relative_to(results_dir)}: {exc}")

    print(f"  {case_id}: {processed} run(s) (minimal mode — no grading merge)")
    return processed


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 scripts/prepare_viewer.py <iteration_dir>")
        print("  python3 scripts/prepare_viewer.py --minimal <iteration_dir>")
        print("")
        print("--minimal: skip grading merge (just stage outputs for viewer).")
        print("           Use when you ran cases by hand and only want visual review.")
        sys.exit(1)

    minimal = False
    args = sys.argv[1:]
    if args and args[0] == "--minimal":
        minimal = True
        args = args[1:]

    if not args:
        print("Error: iteration_dir required", file=sys.stderr)
        sys.exit(1)

    iter_dir = Path(args[0]).resolve()
    results_dir = iter_dir / "results"

    if minimal:
        print(f"Preparing viewer data for {iter_dir.name} (MINIMAL MODE — no grading merge)...")
        plan = convert_eval_plan(iter_dir)
        total_runs = 0
        for case in plan.get("cases", []):
            total_runs += process_case_minimal(results_dir, case)
        print(f"\nDone: {total_runs} runs staged for viewer (no grading data — visual review only).")
        return

    print(f"Preparing viewer data for {iter_dir.name}...")

    # Step 0: Hide root-level grading.json (blocks viewer recursion)
    hide_root_grading(iter_dir)

    # Step 1: Convert eval plan
    plan = convert_eval_plan(iter_dir)

    # Step 2: Process each case
    total_runs = 0
    for case in plan.get("cases", []):
        total_runs += process_case(results_dir, case)

    print(f"\nDone: {total_runs} runs prepared for viewer.")


if __name__ == "__main__":
    main()
