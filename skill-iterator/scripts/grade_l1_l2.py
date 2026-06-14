#!/usr/bin/env python3
"""Code-based grader for L1 and L2 assertions. Zero LLM — pure regex + structure checks."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Import shared functions from quick_validate (canonical copy in skill-creator/scripts/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skill-creator" / "scripts"))
from quick_validate import (
    extract_frontmatter,
    validate_frontmatter,
    extract_description_lines,
    TRIGGER_HINTS,
    PUSHY_MARKERS,
    STRICT_MARKERS,
    read_text,
)

PASS = True
FAIL = False


# ---------------------------------------------------------------------------
# L1: Binary — 能跑通吗？
# ---------------------------------------------------------------------------

def check_skill_exists(skill_dir: Path) -> tuple[bool, str]:
    """L1-1: SKILL.md exists and has > 20 lines."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"
    lines = read_text(path).splitlines()
    if len(lines) < 20:
        return FAIL, f"SKILL.md only has {len(lines)} lines (need > 20)"
    return PASS, f"SKILL.md exists with {len(lines)} lines"


def check_yaml_valid(skill_dir: Path) -> tuple[bool, str]:
    """L1-2: YAML frontmatter is valid."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"
    try:
        content = read_text(path)
        fm_text, _ = extract_frontmatter(content)
        frontmatter, _ = validate_frontmatter(fm_text)
        name = frontmatter.get("name", "")
        return PASS, f"YAML valid, name='{name}'"
    except (ValueError, RuntimeError) as e:
        return FAIL, f"YAML invalid: {e}"


def check_requirements_exists(skill_dir: Path) -> tuple[bool, str]:
    """L1-3: references/requirements.md exists."""
    path = skill_dir / "references" / "requirements.md"
    if not path.exists():
        return FAIL, "references/requirements.md not found"
    content = read_text(path)
    if len(content.strip()) < 50:
        return FAIL, f"requirements.md too short ({len(content)} chars)"
    return PASS, f"requirements.md exists ({len(content)} chars)"


def check_eval_criteria_exists(skill_dir: Path) -> tuple[bool, str]:
    """L1-4: evals/eval-criteria.md exists."""
    path = skill_dir / "evals" / "eval-criteria.md"
    if not path.exists():
        return FAIL, "evals/eval-criteria.md not found"
    content = read_text(path)
    if len(content.strip()) < 50:
        return FAIL, f"eval-criteria.md too short ({len(content)} chars)"
    return PASS, f"eval-criteria.md exists ({len(content)} chars)"


# ---------------------------------------------------------------------------
# L2: Correctness — 对不对？
# ---------------------------------------------------------------------------

def check_name_kebab(skill_dir: Path) -> tuple[bool, str]:
    """L2-1: YAML name is kebab-case."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"
    try:
        content = read_text(path)
        fm_text, _ = extract_frontmatter(content)
        frontmatter, _ = validate_frontmatter(fm_text)
        name = frontmatter.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return FAIL, "No name in frontmatter"
        name = name.strip()
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            return FAIL, f"Name '{name}' is not valid kebab-case"
        return PASS, f"Name '{name}' is valid kebab-case"
    except (ValueError, RuntimeError) as e:
        return FAIL, f"Cannot check name: {e}"


def check_trigger_phrases(skill_dir: Path) -> tuple[bool, str]:
    """L2-2: Description has 3+ trigger phrases + pushy sentence."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"
    try:
        content = read_text(path)
        fm_text, _ = extract_frontmatter(content)
        description_lines = extract_description_lines(fm_text)
        description_text = "\n".join(description_lines)

        trigger_count = sum(1 for line in description_lines if line.strip().startswith("- "))
        has_pushy = "即使" in description_text and any(m in description_text for m in PUSHY_MARKERS)
        has_strict = any(m in description_text for m in STRICT_MARKERS)
        has_boundary = has_pushy or has_strict

        issues = []
        if trigger_count < 3:
            issues.append(f"only {trigger_count} trigger phrases (need 3+)")
        if not has_boundary:
            issues.append("missing trigger boundary declaration (pushy '即使...也应该触发' OR strict '不要用于：...')")

        if issues:
            return FAIL, "; ".join(issues)
        boundary_kind = "pushy" if has_pushy else "strict"
        return PASS, f"{trigger_count} trigger phrases + {boundary_kind} boundary present"
    except (ValueError, RuntimeError) as e:
        return FAIL, f"Cannot check triggers: {e}"


def check_sections(skill_dir: Path) -> tuple[bool, str]:
    """L2-3: 6 required sections present."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"

    content = read_text(path)

    # Check for required sections (allow common variants)
    checks = {
        "YAML frontmatter": content.startswith("---"),
        "一句话说明": bool(re.search(r"^#\s+.+", content, re.MULTILINE)),
        "执行逻辑": bool(re.search(r"##\s*(执行|流程|Workflow|Step)", content, re.IGNORECASE)),
        "Examples": bool(re.search(r"##\s*Examples?", content, re.IGNORECASE)),
        "Troubleshooting": bool(re.search(r"##\s*Troubleshoot", content, re.IGNORECASE)),
        "完成标准": "完成标准" in content,
    }

    missing = [name for name, found in checks.items() if not found]
    if missing:
        return FAIL, f"Missing sections: {', '.join(missing)}"
    return PASS, f"All 6 sections present: {', '.join(checks.keys())}"


def check_input_output(skill_dir: Path) -> tuple[bool, str]:
    """L2-4: Every Step has Input and Output."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"

    content = read_text(path)

    # Find all Step blocks
    step_pattern = re.compile(r"###\s*Step\s*\d+", re.IGNORECASE)
    steps = step_pattern.findall(content)

    if not steps:
        return FAIL, "No ### Step N blocks found"

    # Split content by steps and check each
    step_splits = step_pattern.split(content)[1:]  # skip content before first step
    missing_io = []

    for i, block in enumerate(step_splits, 1):
        has_input = bool(re.search(r"Input\s*[:：]", block))
        has_output = bool(re.search(r"Output\s*[:：]", block))
        if not has_input or not has_output:
            parts = []
            if not has_input:
                parts.append("Input")
            if not has_output:
                parts.append("Output")
            missing_io.append(f"Step {i} missing {'+'.join(parts)}")

    if missing_io:
        return FAIL, "; ".join(missing_io)
    return PASS, f"All {len(steps)} steps have Input and Output"


def check_body_length(skill_dir: Path) -> tuple[bool, str]:
    """L2-5: Body < 500 lines."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"

    content = read_text(path)
    try:
        _, body = extract_frontmatter(content)
        body_lines = body.rstrip("\n").splitlines() if body.strip() else []
        if len(body_lines) >= 500:
            return FAIL, f"Body has {len(body_lines)} lines (must be < 500)"
        return PASS, f"Body has {len(body_lines)} lines (< 500)"
    except ValueError as e:
        return FAIL, f"Cannot parse: {e}"


def check_skill_type_marked(skill_dir: Path) -> tuple[bool, str]:
    """L2-6: Skill type (Problem First / Tools First) is marked in requirements.md."""
    req_path = skill_dir / "references" / "requirements.md"
    if not req_path.exists():
        return FAIL, "requirements.md not found"

    content = read_text(req_path)
    has_type = bool(re.search(r"(Problem First|Tools First)", content))
    if not has_type:
        return FAIL, "No 'Problem First' or 'Tools First' found in requirements.md"

    # Determine which type
    is_problem = "Problem First" in content
    type_str = "Problem First" if is_problem else "Tools First"
    return PASS, f"Skill type marked as '{type_str}'"


def check_examples_nonempty(skill_dir: Path) -> tuple[bool, str]:
    """L2-7: Examples section exists and has real content (> 5 lines, no placeholders)."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"

    content = read_text(path)
    match = re.search(r"##\s*Examples?\s*\n([\s\S]*?)(?=\n##\s|\Z)", content, re.IGNORECASE)
    if not match:
        return FAIL, "No ## Examples section found"

    examples_text = match.group(1).strip()
    lines = [l for l in examples_text.splitlines() if l.strip()]

    if len(lines) < 3:
        return FAIL, f"Examples section only has {len(lines)} non-empty lines (need >= 3)"

    placeholder_patterns = [r"\[placeholder\]", r"\[TODO\]", r"\[TBD\]", r"\.\.\."]
    placeholder_count = sum(1 for p in placeholder_patterns if re.search(p, examples_text, re.IGNORECASE))
    if placeholder_count > 0:
        return FAIL, f"Examples contain {placeholder_count} placeholder pattern(s)"

    return PASS, f"Examples section has {len(lines)} lines of real content"


def check_examples_ratio(skill_dir: Path) -> tuple[bool, str]:
    """L2-8: Examples section is ≤ 30% of SKILL.md body."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"

    content = read_text(path)
    try:
        _, body = extract_frontmatter(content)
    except ValueError:
        return FAIL, "Cannot parse frontmatter"

    body_lines = body.strip().splitlines()
    if not body_lines:
        return FAIL, "Empty body"

    match = re.search(r"##\s*Examples?\s*\n([\s\S]*?)(?=\n##\s|\Z)", body, re.IGNORECASE)
    if not match:
        return PASS, "No Examples section (0%)"

    examples_lines = match.group(0).strip().splitlines()
    ratio = len(examples_lines) / len(body_lines)

    if ratio > 0.30:
        return FAIL, f"Examples = {len(examples_lines)}/{len(body_lines)} lines ({ratio:.0%}, exceeds 30%)"
    return PASS, f"Examples = {len(examples_lines)}/{len(body_lines)} lines ({ratio:.0%}, within 30%)"


def check_troubleshooting_structure(skill_dir: Path) -> tuple[bool, str]:
    """L2-9: Troubleshooting has Error/Cause/Solution structure."""
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return FAIL, "SKILL.md not found"

    content = read_text(path)
    match = re.search(r"##\s*Troubleshoot\w*\s*\n([\s\S]*?)(?=\n##\s|\Z)", content, re.IGNORECASE)
    if not match:
        return FAIL, "No Troubleshooting section"

    ts_text = match.group(1)
    has_error = bool(re.search(r"Error\s*[:：]", ts_text, re.IGNORECASE))
    has_cause = bool(re.search(r"Cause\s*[:：]", ts_text, re.IGNORECASE))
    has_solution = bool(re.search(r"Solution\s*[:：]", ts_text, re.IGNORECASE))

    present = []
    missing = []
    for name, found in [("Error", has_error), ("Cause", has_cause), ("Solution", has_solution)]:
        (present if found else missing).append(name)

    if missing:
        return FAIL, f"Troubleshooting missing: {', '.join(missing)} (has: {', '.join(present)})"
    return PASS, f"Troubleshooting has Error/Cause/Solution structure"


# ---------------------------------------------------------------------------
# Interaction mode — for Case 4 (reject) and Case 5 (clarify)
# ---------------------------------------------------------------------------

def check_no_skill_output(response_text: str) -> tuple[bool, str]:
    """L1 (interaction): No SKILL.md YAML frontmatter produced."""
    if re.search(r"---\s*\nname:", response_text):
        return FAIL, "Found YAML frontmatter (skill was produced when it shouldn't be)"
    return PASS, "No YAML frontmatter in response"


def check_asks_question(response_text: str) -> tuple[bool, str]:
    """L1 (interaction): Response contains a question mark."""
    if "?" in response_text or "\uff1f" in response_text:
        count = response_text.count("?") + response_text.count("\uff1f")
        return PASS, f"Response contains {count} question mark(s)"
    return FAIL, "No question marks found in response"


def check_provides_alternative(response_text: str) -> tuple[bool, str]:
    """L2 (interaction): Response contains shell command alternatives."""
    shell_patterns = [r"\bmv\b", r"\brename\b", r"\btr\b", r"\bfind\b.*-exec", r"\bfor\b.*\bin\b", r"\bbash\b", r"\bsh\b"]
    found = [p for p in shell_patterns if re.search(p, response_text, re.IGNORECASE)]
    if found:
        return PASS, f"Shell alternatives found: {', '.join(found)}"
    return FAIL, "No shell command alternatives found (mv/rename/tr/find/bash)"


def check_has_citations(response_text: str) -> tuple[bool, str]:
    """L1 (interaction/query): Response cites sources via [[wikilink]] / Source: / [n] / See:."""
    patterns = [
        (r"\[\[[^\]]+\]\]", "wikilink"),
        (r"(?:Source|Ref|See|Cite)s?\s*:\s*\S", "explicit prefix"),
        (r"(?<!\S)\[\d+\](?!\s*\()", "footnote [n]"),
    ]
    hits = [(label, len(re.findall(p, response_text))) for p, label in patterns]
    hits = [(label, n) for label, n in hits if n > 0]
    if hits:
        return PASS, "Citations: " + ", ".join(f"{n} {label}" for label, n in hits)
    return FAIL, "No citations found (wikilink / Source: / [n] / See:)"


def check_nontrivial_length(response_text: str) -> tuple[bool, str]:
    """L1 (interaction/query): Response is substantive (> 200 chars), not bailed-out."""
    length = len(response_text.strip())
    if length > 200:
        return PASS, f"Response is {length} chars (> 200 substantive threshold)"
    return FAIL, f"Response only {length} chars (bailed out or trivial)"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

L1_CHECKS = [
    ("L1-1", "SKILL.md exists and > 20 lines", check_skill_exists),
    ("L1-2", "YAML frontmatter valid", check_yaml_valid),
    ("L1-3", "requirements.md exists", check_requirements_exists),
    ("L1-4", "eval-criteria.md exists", check_eval_criteria_exists),
]

L2_CHECKS = [
    ("L2-1", "YAML name is kebab-case", check_name_kebab),
    ("L2-2", "3+ trigger phrases + pushy sentence", check_trigger_phrases),
    ("L2-3", "6 sections present", check_sections),
    ("L2-4", "Every Step has Input/Output", check_input_output),
    ("L2-5", "Body < 500 lines", check_body_length),
    ("L2-6", "Skill type marked", check_skill_type_marked),
    ("L2-7", "Examples non-empty with real content", check_examples_nonempty),
    ("L2-8", "Examples ≤ 30% of body", check_examples_ratio),
    ("L2-9", "Troubleshooting has Error/Cause/Solution", check_troubleshooting_structure),
]


def grade_skill_artifact(skill_dir: Path) -> dict:
    """Grade a produced skill DIRECTORY for artifact-level compliance.

    This checks whether SKILL.md is well-formed (YAML, sections, trigger phrases,
    body length, etc.) — NOT whether per-case outputs from running the skill are
    good. Per-case output grading is the grader agent's job (L3) plus per-case
    assertions defined in eval-plan.json (graded separately).

    The distinction matters: a skill can be artifact-compliant (passes everything
    here) but produce useless outputs, or vice-versa. Conflating them was a real
    blocker — design-md iteration-1 hit this and had to bypass the whole
    grading pipeline. Don't put case-output checks in here.
    """
    results = {"mode": "skill_artifact", "skill_dir": str(skill_dir), "l1": [], "l2": []}

    for check_id, desc, fn in L1_CHECKS:
        passed, evidence = fn(skill_dir)
        results["l1"].append({"id": check_id, "description": desc, "passed": passed, "evidence": evidence})

    for check_id, desc, fn in L2_CHECKS:
        passed, evidence = fn(skill_dir)
        results["l2"].append({"id": check_id, "description": desc, "passed": passed, "evidence": evidence})

    l1_pass = sum(1 for r in results["l1"] if r["passed"])
    l2_pass = sum(1 for r in results["l2"] if r["passed"])
    results["summary"] = {
        "l1_pass": l1_pass, "l1_total": len(results["l1"]),
        "l2_pass": l2_pass, "l2_total": len(results["l2"]),
        "total_pass": l1_pass + l2_pass, "total": len(results["l1"]) + len(results["l2"]),
    }
    return results


def grade_interaction(response_text: str, case_type: str) -> dict:
    """Grade an interaction response (reject/clarify). Returns structured results."""
    results = {"mode": "interaction", "case_type": case_type, "l1": [], "l2": []}

    if case_type == "reject":
        passed, evidence = check_no_skill_output(response_text)
        results["l1"].append({"id": "L1-1", "description": "No skill output", "passed": passed, "evidence": evidence})
        passed, evidence = check_provides_alternative(response_text)
        results["l2"].append({"id": "L2-1", "description": "Provides shell alternative", "passed": passed, "evidence": evidence})

    elif case_type == "clarify":
        passed, evidence = check_asks_question(response_text)
        results["l1"].append({"id": "L1-1", "description": "Asks question", "passed": passed, "evidence": evidence})
        passed, evidence = check_no_skill_output(response_text)
        results["l1"].append({"id": "L1-2", "description": "No premature output", "passed": passed, "evidence": evidence})

    elif case_type == "query":
        # Query-type interaction (search → read → cite-backed answer).
        # Minimal L1 checks: did the response cite sources at all, and is it
        # substantive (not a "nothing found" bail). Stricter quality signals
        # (right source, no hallucination) are L3 grader territory.
        passed, evidence = check_has_citations(response_text)
        results["l1"].append({"id": "L1-1", "description": "Cites sources", "passed": passed, "evidence": evidence})
        passed, evidence = check_nontrivial_length(response_text)
        results["l1"].append({"id": "L1-2", "description": "Non-trivial response", "passed": passed, "evidence": evidence})

    l1_pass = sum(1 for r in results["l1"] if r["passed"])
    l2_pass = sum(1 for r in results["l2"] if r["passed"])
    results["summary"] = {
        "l1_pass": l1_pass, "l1_total": len(results["l1"]),
        "l2_pass": l2_pass, "l2_total": len(results["l2"]),
        "total_pass": l1_pass + l2_pass, "total": len(results["l1"]) + len(results["l2"]),
    }
    return results


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 grade_l1_l2.py <skill_directory>              # grade a produced skill")
        print("  python3 grade_l1_l2.py --interaction reject <file>    # grade reject response")
        print("  python3 grade_l1_l2.py --interaction clarify <file>   # grade clarify response")
        print("  python3 grade_l1_l2.py --interaction query <file>     # grade query/cite-backed response")
        sys.exit(1)

    if sys.argv[1] == "--interaction":
        if len(sys.argv) < 4:
            print("Need: --interaction <reject|clarify> <response_file>")
            sys.exit(1)
        case_type = sys.argv[2]
        response_text = Path(sys.argv[3]).read_text(encoding="utf-8")
        results = grade_interaction(response_text, case_type)
    else:
        skill_dir = Path(sys.argv[1])
        results = grade_skill_artifact(skill_dir)

    print(json.dumps(results, indent=2, ensure_ascii=False))
    total = results["summary"]
    passed = total["total_pass"]
    total_count = total["total"]
    status = "PASS" if passed == total_count else "FAIL"
    print(f"\n{status}: {passed}/{total_count} assertions passed")
    sys.exit(0 if passed == total_count else 1)


if __name__ == "__main__":
    main()
