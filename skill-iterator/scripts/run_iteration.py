#!/usr/bin/env python3
"""End-to-end iteration orchestrator.

Handles the non-agent parts of an eval iteration:
- setup: create directory structure, validate plan
- grade: run L1/L2 grading (hardcoded checks for skill_production/interaction modes)
- finalize: aggregate, benchmark, prepare viewer, write static review.html
- view: launch interactive eval viewer on HTTP server

Agent spawning (executor, grader) is done by Claude Code, not this script.
Grader agents write grading-l3.json directly (see agents/grader.md).

Usage:
  python3 scripts/run_iteration.py setup    evals/workspace/iteration-N/
  python3 scripts/run_iteration.py grade    evals/workspace/iteration-N/
  python3 scripts/run_iteration.py finalize evals/workspace/iteration-N/ [--skill-name "..."]
  python3 scripts/run_iteration.py view     evals/workspace/iteration-N/ [--skill-name "..."]
  python3 scripts/run_iteration.py all      evals/workspace/iteration-N/ [--skill-name "..."]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))
from utils import load_json, write_json, load_eval_plan  # noqa: E402


# ---------------------------------------------------------------------------
# setup: create directory structure
# ---------------------------------------------------------------------------

def cmd_setup(iter_dir: Path) -> None:
    # load_eval_plan normalizes fields (eval_id / runs_per_configuration /
    # configurations / mode) so we can read them directly below.
    plan = load_eval_plan(iter_dir)
    if not plan:
        print(f"Error: no eval-plan.json / eval_plan.json found in {iter_dir}", file=sys.stderr)
        sys.exit(1)

    results_dir = iter_dir / "results"
    results_dir.mkdir(exist_ok=True)

    print(f"Setting up iteration {plan.get('iteration', '?')}...")

    for case in plan.get("cases", []):
        case_id = case["id"]
        runs = case["runs_per_configuration"]
        configs = case["configurations"]

        case_dir = results_dir / case_id
        case_dir.mkdir(exist_ok=True)

        if runs > 1:
            for config in configs:
                for run_idx in range(1, runs + 1):
                    if len(configs) > 1:
                        run_dir = case_dir / config / f"run-{run_idx}"
                    else:
                        run_dir = case_dir / f"run-{run_idx}"
                    run_dir.mkdir(parents=True, exist_ok=True)
                    print(f"  {run_dir.relative_to(iter_dir)}")
        else:
            if len(configs) > 1:
                for config in configs:
                    config_dir = case_dir / config
                    config_dir.mkdir(exist_ok=True)
                    print(f"  {config_dir.relative_to(iter_dir)}")
            else:
                print(f"  {case_dir.relative_to(iter_dir)}")

    # Print executor prompts for convenience
    print("\n--- Executor Prompts ---")
    for case in plan.get("cases", []):
        case_id = case["id"]
        configs = case["configurations"]
        runs = case["runs_per_configuration"]
        for config in configs:
            print(f"\n[{case_id} / {config} / {runs} runs]")
            print(f"  prompt: {case['prompt'][:80]}...")
            if runs > 1 and len(configs) > 1:
                print(f"  output_dir: results/{case_id}/{config}/run-N/")
            elif runs > 1:
                print(f"  output_dir: results/{case_id}/run-N/")
            else:
                out = f"results/{case_id}/{config}/" if len(configs) > 1 else f"results/{case_id}/"
                print(f"  output_dir: {out}")
            print(f"  configuration: {config}")

    total = sum(c["runs_per_configuration"] * len(c["configurations"]) for c in plan.get("cases", []))
    print(f"\nSetup complete. {total} total runs to execute.")


# ---------------------------------------------------------------------------
# grade: run L1/L2 grading
# ---------------------------------------------------------------------------

def cmd_grade(iter_dir: Path) -> None:
    """Run code-driven L1/L2 grading on each run.

    Mode dispatch:
    - "skill_production" → if target has evals/checks.sh, run it (target's OWN
      [code] L1/L2 checks); else grade a produced SKILL.md as a generic ARTIFACT
      (YAML, sections, compliance). The checks.sh path is the backlog#1 fix.
    - "interaction" → grade a reject/clarify response (text-level checks).
    - "case_output" → DO NOT code-grade. Per-case output quality (DESIGN.md /
      generated docs / etc.) requires either case-specific assertions defined in
      eval-plan.json plus a grader agent (L3), or case-bespoke check scripts.
      The skill_artifact grader can't grade DESIGN.md — that was the design-md
      iteration-1 blocker. Skipping here is intentional, not a bug.
    """
    plan = load_eval_plan(iter_dir)
    results_dir = iter_dir / "results"
    grade_script = SCRIPTS_DIR / "grade_l1_l2.py"

    print(f"Grading L1/L2 for iteration {plan.get('iteration', '?')}...")

    for case in plan.get("cases", []):
        case_id = case["id"]
        mode = case["mode"]
        runs = case["runs_per_configuration"]
        configs = case["configurations"]

        run_dirs = find_run_dirs(results_dir / case_id, runs, configs)

        for run_dir in run_dirs:
            if mode == "skill_production":
                # backlog#1 fix: prefer target's OWN [code] L1/L2 checks
                # (evals/checks.sh) over generic SKILL.md-template grading.
                # grade_l1_l2.py only checks generic template compliance (YAML,
                # sections, body length) — zero overlap with what a skill declares
                # in its eval-criteria.md (e.g. "script exits 64 on short phone").
                # workspace lives at <skill>/evals/workspace/iteration-N/, so
                # checks.sh = parents[1]/checks.sh, skill root = parents[2].
                # checks.sh prints grading-l1-l2.json shape to stdout, exits 0/1.
                checks_sh = iter_dir.parents[1] / "checks.sh"
                if checks_sh.exists():
                    result = subprocess.run(
                        ["bash", str(checks_sh), str(iter_dir.parents[2])],
                        capture_output=True, text=True
                    )
                else:
                    # Fallback: no checks.sh → grade a newly-produced SKILL.md
                    # subdir for generic artifact compliance (producer skills).
                    skill_dir = None
                    for child in run_dir.iterdir():
                        if child.is_dir() and (child / "SKILL.md").exists():
                            skill_dir = child
                            break
                    if not skill_dir:
                        print(f"  SKIP {run_dir.relative_to(iter_dir)} — no evals/checks.sh, no produced SKILL.md")
                        continue
                    result = subprocess.run(
                        [sys.executable, str(grade_script), str(skill_dir)],
                        capture_output=True, text=True
                    )
            elif mode == "case_output":
                # File-producing skill (HTML / DESIGN.md / generated docs / etc.).
                # P1 fix: if the target ships evals/case-checks.sh, run it against
                # THIS run_dir to grade its own [code] L1/L2 assertions
                # deterministically. case-checks.sh takes <run_dir> (not <skill_root>
                # like checks.sh) because what's graded is the per-run artifact, not
                # the skill files. [llm] assertions still go to the L3 grader.
                # No script → keep SKIP, but say WHY (objective checks fall to L3).
                case_checks = iter_dir.parents[1] / "case-checks.sh"
                if case_checks.exists():
                    result = subprocess.run(
                        ["bash", str(case_checks), str(run_dir)],
                        capture_output=True, text=True
                    )
                else:
                    print(f"  SKIP {run_dir.relative_to(iter_dir)} — case_output mode, no evals/case-checks.sh; [code] L1/L2 deferred to L3 grader")
                    continue
            else:
                # Interaction case. New schema: eval-plan.json case.interaction_type
                # explicitly declares "reject" | "clarify" | "query" (+ future:
                # ingest | lint). Cut H fix. If field absent, fall back to the
                # legacy l1_l2_grader substring inference (reject-vs-clarify only).
                case_type = case.get("interaction_type")
                if not case_type:
                    case_type = "reject" if "reject" in case.get("l1_l2_grader", "") else "clarify"
                response_file = run_dir / "response.md"
                if not response_file.exists():
                    print(f"  SKIP {run_dir.relative_to(iter_dir)} — no response.md")
                    continue

                result = subprocess.run(
                    [sys.executable, str(grade_script), "--interaction", case_type, str(response_file)],
                    capture_output=True, text=True
                )

            if result.returncode in (0, 1):  # 0=all pass, 1=some fail
                # Parse the JSON from stdout (before the summary line)
                try:
                    json_text = result.stdout.split("\n\n")[0]
                    grading = json.loads(json_text)
                    write_json(run_dir / "grading-l1-l2.json", grading)
                    summary = grading.get("summary", {})
                    status = "PASS" if summary.get("total_pass") == summary.get("total") else "FAIL"
                    print(f"  {status} {run_dir.relative_to(iter_dir)} — {summary.get('total_pass')}/{summary.get('total')}")
                except (json.JSONDecodeError, IndexError):
                    print(f"  ERROR {run_dir.relative_to(iter_dir)} — could not parse grading output")
            else:
                print(f"  ERROR {run_dir.relative_to(iter_dir)} — grade script failed: {result.stderr[:100]}")

    print("\nL1/L2 grading complete.")


def find_run_dirs(case_dir: Path, runs: int, configs: list[str]) -> list[Path]:
    """Find all run directories for a case, handling various layouts."""
    dirs = []
    if not case_dir.exists():
        return dirs

    if runs > 1 and len(configs) > 1:
        for config in configs:
            for i in range(1, runs + 1):
                d = case_dir / config / f"run-{i}"
                if d.exists():
                    dirs.append(d)
    elif runs > 1:
        for i in range(1, runs + 1):
            d = case_dir / f"run-{i}"
            if d.exists():
                dirs.append(d)
    elif len(configs) > 1:
        for config in configs:
            d = case_dir / config
            if d.exists():
                dirs.append(d)
    else:
        config = configs[0] if configs else "with_skill"
        config_dir = case_dir / config
        if config_dir.exists() and config_dir.is_dir():
            dirs.append(config_dir)
        else:
            dirs.append(case_dir)

    return dirs


# ---------------------------------------------------------------------------
# finalize: aggregate + benchmark + viewer
# ---------------------------------------------------------------------------

def _viewer_args(iter_dir: Path, skill_name: str) -> list[str]:
    args = [
        sys.executable, str(PROJECT_DIR / "scripts" / "eval_viewer" / "generate_review.py"),
        str(iter_dir),
        "--skill-name", skill_name,
    ]
    suggestions = iter_dir / "suggestions.json"
    if suggestions.exists():
        args.extend(["--suggestions", str(suggestions)])
    benchmark = iter_dir / "benchmark.json"
    if benchmark.exists():
        args.extend(["--benchmark", str(benchmark)])
    return args


def cmd_finalize(iter_dir: Path, skill_name: str) -> None:
    # WHY step 4 writes static HTML instead of serving:
    # generate_review.py without --static calls serve_forever() which blocks
    # the caller indefinitely. In auto mode / CI / unattended iteration,
    # that hangs the whole pipeline. Static output = deterministic exit.
    # Interactive review is available via `run_iteration.py view <iter_dir>`.
    print(f"Finalizing iteration {iter_dir.name}...")

    # 1. Aggregate
    print("\n[1/4] Aggregating results...")
    subprocess.run([sys.executable, str(SCRIPTS_DIR / "aggregate_results.py"), str(iter_dir)], check=True)

    # 2. Generate benchmark
    print("\n[2/4] Generating benchmark...")
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "generate_benchmark.py"), str(iter_dir), "--skill-name", skill_name],
        check=True,
    )

    # 3. Prepare viewer
    print("\n[3/4] Preparing viewer data...")
    subprocess.run([sys.executable, str(SCRIPTS_DIR / "prepare_viewer.py"), str(iter_dir)], check=True)

    # 4. Write static review HTML
    review_path = iter_dir / "review.html"
    print(f"\n[4/4] Writing review.html...")
    viewer_args = _viewer_args(iter_dir, skill_name) + ["--static", str(review_path)]
    subprocess.run(viewer_args, check=True)
    print(f"\n  Open: {review_path}")


def cmd_view(iter_dir: Path, skill_name: str) -> None:
    """Launch interactive eval viewer on a local HTTP server.

    Use this when you need the live viewer UI (feedback POST endpoint,
    auto-reload) rather than the static HTML finalize produces.
    """
    viewer_args = _viewer_args(iter_dir, skill_name)
    subprocess.run(viewer_args)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    iter_dir = Path(sys.argv[2]).resolve()

    if not iter_dir.exists():
        print(f"Error: {iter_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    skill_name = "Skill"  # generic default; pass --skill-name to set per-iter
    for i, arg in enumerate(sys.argv):
        if arg == "--skill-name" and i + 1 < len(sys.argv):
            skill_name = sys.argv[i + 1]

    if command == "setup":
        cmd_setup(iter_dir)
    elif command == "grade":
        cmd_grade(iter_dir)
    elif command == "finalize":
        cmd_finalize(iter_dir, skill_name)
    elif command == "view":
        cmd_view(iter_dir, skill_name)
    elif command == "all":
        cmd_setup(iter_dir)
        print("\n" + "=" * 60)
        print("Setup complete. Follow these steps:\n")
        print("1. Spawn executor agents for each run.")
        print("   After all executors finish:")
        print(f"     python3 scripts/run_iteration.py grade {iter_dir}")
        print("\n2. Spawn L3 grader agents (they write grading-l3.json directly).")
        print("\n3. Spawn suggest agent (agents/suggest.md): reads per-case")
        print("   grading-l1-l2.json + grading-l3.json, writes suggestions.json.")
        print("   After graders + suggest finish:")
        print(f"     python3 scripts/run_iteration.py finalize {iter_dir}")
        print("=" * 60)
    else:
        print(f"Unknown command: {command}")
        print("Commands: setup, grade, finalize, view, all")
        sys.exit(1)


if __name__ == "__main__":
    main()
