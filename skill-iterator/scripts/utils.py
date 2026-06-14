from __future__ import annotations

"""Shared utilities for skill-iterator scripts.

All plan loading + JSON IO goes through here. Keep this the single source of
truth so schema changes touch one place, not N. See references/schemas.md for
the fields this module normalizes.
"""

import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Text / JSON IO — consolidated from 5 duplicated implementations
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    """Load JSON; tolerant of control chars + invalid backslash escapes sometimes
    emitted by LLM-produced JSON. Returns {} if file missing or unparseable
    after recovery attempts."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    # Strip common control chars (e.g. \x08 backspace from LLM output)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Heuristic recovery: strip invalid backslash escapes (e.g. \. \s)
        text = re.sub(r"\\([^\"\\/bfnrtu])", r"\1", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}


def load_json_strict_existing(path: Path) -> dict:
    """Like load_json, but if the file EXISTS yet still cannot be parsed (corrupt
    JSON — e.g. an agent-written grading file with unescaped quotes), raise loudly
    instead of silently returning {}. A missing file still returns {}.

    WHY: a corrupt grading-l3.json silently becoming {} (= 0 score, 0 assertions)
    inverted the benchmark headline once — with_skill showed L3=0.0 when the grader
    had actually scored 4.67, making the skill look worse than baseline with no
    error surfaced. Fail loud so a corrupt result file can never masquerade as a
    real "0" result.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        recovered = re.sub(r"\\([^\"\\/bfnrtu])", r"\1", text)
        try:
            return json.loads(recovered)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{path} exists but is not valid JSON ({e}). An agent-written "
                f"result file is corrupt — refusing to treat it as an empty/zero "
                f"result (that would silently invert the benchmark). Re-run the "
                f"grader for this run or fix the JSON, then re-run finalize."
            ) from e


def write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# SKILL.md parsing
# ---------------------------------------------------------------------------

def parse_skill_md(skill_path: Path) -> tuple[str, str, str]:
    """Parse a SKILL.md file, returning (name, description, full_content)."""
    content = read_text(skill_path / "SKILL.md")
    lines = content.split("\n")

    if lines[0].strip() != "---":
        raise ValueError("SKILL.md missing frontmatter (no opening ---)")

    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        raise ValueError("SKILL.md missing frontmatter (no closing ---)")

    name = ""
    description = ""
    frontmatter_lines = lines[1:end_idx]
    i = 0
    while i < len(frontmatter_lines):
        line = frontmatter_lines[i]
        if line.startswith("name:"):
            name = line[len("name:"):].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            value = line[len("description:"):].strip()
            # Handle YAML multiline indicators (>, |, >-, |-)
            if value in (">", "|", ">-", "|-"):
                continuation_lines: list[str] = []
                i += 1
                while i < len(frontmatter_lines) and (frontmatter_lines[i].startswith("  ") or frontmatter_lines[i].startswith("\t")):
                    continuation_lines.append(frontmatter_lines[i].strip())
                    i += 1
                description = " ".join(continuation_lines)
                continue
            else:
                description = value.strip('"').strip("'")
        i += 1

    return name, description, content


# ---------------------------------------------------------------------------
# Eval plan loading — SINGLE SOURCE OF TRUTH for schema + defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIGURATIONS = ["with_skill", "without_skill"]
DEFAULT_MODE = "skill_production"


def load_eval_plan(workspace: Path) -> dict:
    """Load + normalize eval-plan.json.

    Prefer eval_plan.json (viewer-generated, often has eval_id) over source
    eval-plan.json. Normalize each case so every downstream script reads the
    same shape:

    - case.eval_id: 1-based, injected if missing or 0
    - case.runs_per_configuration: canonicalized from legacy `runs` field
    - case.configurations: defaults to ["with_skill", "without_skill"]
    - case.mode: defaults to "skill_production"
    - case.interaction_type: unchanged — cmd_grade handles legacy inference

    WHY: pre-v0.6 every script re-implemented these defaults inline and drifted
    (Cut L was `runs`/`runs_per_configuration` mismatch across 7 scripts; Cut I+
    was eval_id absent in generate_benchmark). Single-source here so one fix
    propagates everywhere.
    """
    plan = load_json(workspace / "eval_plan.json")
    if not plan:
        plan = load_json(workspace / "eval-plan.json")
    if not plan:
        return plan
    return normalize_eval_plan(plan)


def normalize_eval_plan(plan: dict) -> dict:
    """Apply canonical defaults to each case in-place. Idempotent — safe to call
    on already-normalized plans."""
    for i, case in enumerate(plan.get("cases", []), start=1):
        if not isinstance(case, dict):
            continue
        # eval_id — 1-based, injected if missing or 0
        if not case.get("eval_id"):
            case["eval_id"] = i
        # id — the dir-name key cmd_setup / find_run_dirs read. The validator
        # only requires eval_id, so a validator-shaped plan can lack id and crash
        # setup with KeyError: 'id'. Mirror it here (prefer existing id) so the
        # two schemas can't disagree. P3 fix.
        if not case.get("id"):
            case["id"] = str(case["eval_id"])
        # runs_per_configuration — canonical; fall back to legacy `runs`
        if "runs_per_configuration" not in case:
            legacy = case.get("runs")
            case["runs_per_configuration"] = legacy if isinstance(legacy, int) else 1
        # configurations — default to dual-run A/B baseline pattern
        if not case.get("configurations"):
            case["configurations"] = list(DEFAULT_CONFIGURATIONS)
        # mode — default to skill_production (pre-v0.6 implicit default)
        if not case.get("mode"):
            case["mode"] = DEFAULT_MODE
    return plan


# ---------------------------------------------------------------------------
# Eval ID helpers
# ---------------------------------------------------------------------------

def guess_eval_id_from_name(name: str) -> int | None:
    match = re.search(r"eval-(\d+)", name)
    if not match:
        return None
    return int(match.group(1))


def build_eval_case_lookup(eval_plan: dict) -> dict[int, dict]:
    lookup: dict[int, dict] = {}
    for case in eval_plan.get("cases", []):
        if not isinstance(case, dict):
            continue
        eval_id = case.get("eval_id")
        if isinstance(eval_id, int):
            lookup[eval_id] = case
    return lookup


def resolve_eval_case(case_lookup: dict[int, dict], eval_dir_name: str = "", eval_id: int | None = None) -> dict | None:
    if isinstance(eval_id, int) and eval_id in case_lookup:
        return case_lookup[eval_id]
    guessed = guess_eval_id_from_name(eval_dir_name)
    if guessed is not None:
        return case_lookup.get(guessed)
    return None
