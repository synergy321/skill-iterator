#!/usr/bin/env python3
"""Validate skill folders and iteration workspace artifacts for skill-creator-v5.

NOTE: This file exists in both skill-creator/scripts/ and skill-iterator/scripts/.
Both copies MUST stay identical. If you change one, change the other.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - handled at runtime
    yaml = None


ALLOWED_PROPERTIES = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
TRIGGER_HINTS = ("也应该触发", "也应该使用", "也应该进入")
# Trigger boundary markers — description must declare a boundary, but the
# direction (pushy vs strict) is the skill author's choice.
# pushy = "even if X, trigger anyway"; strict = "do NOT trigger when X".
PUSHY_MARKERS = TRIGGER_HINTS + ("whenever the user", "Use this skill when", "use this skill when", "use this skill whenever")
STRICT_MARKERS = ("不要用于", "不应触发", "不应使用", "不要使用", "Do NOT use", "do not use for", "Don't use for", "DO NOT USE", "仅当", "Only use when", "only use for")
BOUNDARY_MARKERS = PUSHY_MARKERS + STRICT_MARKERS


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def extract_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---"):
        raise ValueError("No YAML frontmatter found")
    match = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
    if not match:
        raise ValueError("Invalid frontmatter format")
    return match.group(1), content[match.end():]


def extract_description_lines(frontmatter_text: str) -> list[str]:
    lines = frontmatter_text.splitlines()
    description_lines: list[str] = []
    in_description = False
    indent = None

    for raw_line in lines:
        if not in_description:
            if raw_line.startswith("description:"):
                value = raw_line[len("description:"):].strip()
                if value in {"|", ">", "|-", ">-"}:
                    in_description = True
                    indent = None
                elif value:
                    description_lines.append(value)
                    break
            continue

        if not raw_line.strip():
            description_lines.append("")
            continue

        current_indent = len(raw_line) - len(raw_line.lstrip(" \t"))
        if indent is None:
            indent = current_indent
        if current_indent < indent:
            break
        description_lines.append(raw_line[indent:])

    return description_lines


def validate_frontmatter(frontmatter_text: str) -> tuple[dict[str, Any], list[str]]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to run quick_validate.py. Install it before validating skills.")

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in frontmatter: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise ValueError("Frontmatter must be a YAML dictionary")

    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        allowed = ", ".join(sorted(ALLOWED_PROPERTIES))
        unexpected = ", ".join(sorted(unexpected_keys))
        raise ValueError(f"Unexpected key(s) in SKILL.md frontmatter: {unexpected}. Allowed properties are: {allowed}")

    description_lines = extract_description_lines(frontmatter_text)
    return frontmatter, description_lines


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    try:
        content = read_text(skill_md)
        frontmatter_text, body = extract_frontmatter(content)
        frontmatter, description_lines = validate_frontmatter(frontmatter_text)

        name = frontmatter.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing 'name' in frontmatter")
        name = name.strip()
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError(f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)")
        if name.startswith("-") or name.endswith("-") or "--" in name:
            raise ValueError(f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens")
        if len(name) > 64:
            raise ValueError(f"Name is too long ({len(name)} characters). Maximum is 64 characters.")

        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Missing 'description' in frontmatter")
        description = description.strip()
        if "<" in description or ">" in description:
            raise ValueError("Description cannot contain angle brackets (< or >)")
        if len(description) > 1024:
            raise ValueError(f"Description is too long ({len(description)} characters). Maximum is 1024 characters.")

        trigger_count = sum(1 for line in description_lines if line.strip().startswith("- "))
        if trigger_count < 3:
            raise ValueError("Description must include at least 3 trigger phrases")
        has_pushy = "即使" in description and any(m in description for m in PUSHY_MARKERS)
        has_strict = any(m in description for m in STRICT_MARKERS)
        if not (has_pushy or has_strict):
            raise ValueError(
                "Description must declare a trigger boundary. "
                "Acceptable forms: pushy ('即使...也应该触发') OR "
                "strict ('不要用于：...' / 'Do NOT use for ...')"
            )

        compatibility = frontmatter.get("compatibility")
        if compatibility is not None:
            if not isinstance(compatibility, str):
                raise ValueError(f"Compatibility must be a string, got {type(compatibility).__name__}")
            if len(compatibility) > 500:
                raise ValueError(f"Compatibility is too long ({len(compatibility)} characters). Maximum is 500 characters.")

        body_lines = body.rstrip("\n").splitlines() if body.strip() else []
        if len(body_lines) >= 500:
            raise ValueError(f"SKILL.md body must stay under 500 lines (current: {len(body_lines)})")
        if "完成标准" not in body:
            raise ValueError("SKILL.md body must contain a '完成标准' section")
        # I/O chain validation — every ### Step must have Input: and Output:
        # Lookahead: terminate at next "### Step N" or end-of-file. Old pattern
        # `[^#]*` truncated at any `#`, breaking Step bodies that embed
        # markdown samples (e.g. `## Example`). Discovered via 2026-05-01 dogfood.
        step_pattern = re.compile(r'### Step \d+.*?(?=\n### Step \d+|\Z)', re.DOTALL)
        steps_in_body = step_pattern.findall(body)
        for i, step_text in enumerate(steps_in_body, start=1):
            if 'Input:' not in step_text:
                raise ValueError(f"SKILL.md Step {i} missing 'Input:' line")
            if 'Output:' not in step_text:
                raise ValueError(f"SKILL.md Step {i} missing 'Output:' line")
        # steps.md validation — if exists, check I/O + WHY there too
        steps_md = skill_dir / "references" / "steps.md"
        if steps_md.exists():
            steps_content = read_text(steps_md)
            steps_in_file = step_pattern.findall(steps_content)
            for i, step_text in enumerate(steps_in_file, start=1):
                if 'Input:' not in step_text:
                    raise ValueError(f"steps.md Step {i} missing 'Input:' line")
                if 'Output:' not in step_text:
                    raise ValueError(f"steps.md Step {i} missing 'Output:' line")
                if 'WHY' not in step_text:
                    raise ValueError(f"steps.md Step {i} missing WHY — every Step must explain why it exists")
            # Error handling warning — Steps with branching should have error paths
            for i, step_text in enumerate(steps_in_file, start=1):
                has_branching = 'IF ' in step_text or 'ELSE' in step_text
                has_error_handling = '错误处理' in step_text or re.search(r'IF.*失败|IF.*超时|IF.*报错', step_text)
                if has_branching and not has_error_handling:
                    print(f"  ⚠ steps.md Step {i}: has branching but no error handling section")
        # ≥3 Steps in SKILL.md body → must be extracted to steps.md
        if len(steps_in_body) >= 3 and not steps_md.exists():
            raise ValueError(
                f"SKILL.md has {len(steps_in_body)} Steps — skills with ≥3 Steps "
                "must extract them to references/steps.md"
            )
        evals_dir = skill_dir / "evals"
        if evals_dir.is_dir():
            eval_criteria = evals_dir / "eval-criteria.md"
            if not eval_criteria.exists():
                raise ValueError("evals/eval-criteria.md not found — evals/ directory exists but eval-criteria.md is missing")
        if "[TODO:" in content or "TODO:" in content:
            raise ValueError("SKILL.md still contains TODO placeholders")
        # Q3 Review gate — requirements.md 存在时检查 Q3 Review 是否已填
        requirements_md = skill_dir / "references" / "requirements.md"
        if requirements_md.exists():
            req_content = read_text(requirements_md)
            if "### Q3 Review" not in req_content:
                raise ValueError(
                    "requirements.md missing '### Q3 Review' section — "
                    "must be completed before proceeding to Q4"
                )
            for field in ("Q1 对照结果", "Q2 对照结果", "是否已回填"):
                if field not in req_content:
                    raise ValueError(
                        f"requirements.md Q3 Review missing '{field}'"
                    )

        # === skill-creator (Eric Travis) 架构合规 5 条规则 ===

        # Rule 4: top-level 目录必须归 6 类（铁律，无白名单）
        ALLOWED_TOP_DIRS = {"references", "scripts", "agents", "assets", "evals"}
        top_dirs = [p.name for p in skill_dir.iterdir() if p.is_dir() and not p.name.startswith('.')]
        unauthorized_dirs = sorted(d for d in top_dirs if d not in ALLOWED_TOP_DIRS)
        if unauthorized_dirs:
            raise ValueError(
                f"Unauthorized top-level director(ies): {unauthorized_dirs}. "
                f"Must be in {sorted(ALLOWED_TOP_DIRS)}. No whitelist allowed."
            )

        # Rule 5: SKILL.md 引用文件必须存在
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
        for label, url in link_pattern.findall(content):
            if url.startswith(('http://', 'https://', 'mailto:', '#')):
                continue
            path_part = url.split('#')[0]
            if not path_part:
                continue
            target = skill_dir / path_part
            if not target.exists():
                raise ValueError(
                    f"Broken link in SKILL.md: [{label}]({url}) → {target} does not exist"
                )

        # Rule 2: SKILL.md 不含 python3/bash scripts 命令（Troubleshooting block 内除外）
        ts_match = re.search(r'^##\s+(Troubleshooting|故障排查|疑难解答)\b', body, re.MULTILINE)
        if ts_match:
            ts_start = ts_match.start()
            next_section = re.search(r'^##\s+', body[ts_start + 1:], re.MULTILINE)
            ts_end = ts_start + 1 + next_section.start() if next_section else len(body)
        else:
            ts_start = -1
            ts_end = -1
        exec_pattern = re.compile(r'\b(python3?\s+\S+\.py\b|bash\s+\S+\.sh\b|bash\s+scripts/\S+)')
        for m in exec_pattern.finditer(body):
            pos = m.start()
            in_troubleshooting = ts_match is not None and ts_start <= pos < ts_end
            if not in_troubleshooting:
                # show context: line containing the match
                line_start = body.rfind('\n', 0, pos) + 1
                line_end = body.find('\n', pos)
                line = body[line_start:line_end if line_end != -1 else len(body)].strip()
                raise ValueError(
                    f"SKILL.md must not contain execution commands outside Troubleshooting. "
                    f"Found '{m.group(0)}' in line: {line!r}. "
                    f"Move execution details to references/steps.md."
                )

        # Rule 1: 文件结构图 vs references/scripts/agents/assets 实际文件一致
        fsm_pattern = re.compile(r'```\n(.*?)\n```', re.DOTALL)
        code_blocks = fsm_pattern.findall(body)
        file_struct_block = None
        for block in code_blocks:
            first_line = block.split('\n', 1)[0]
            if (skill_dir.name + '/' in first_line) or '├──' in block or '└──' in block:
                file_struct_block = block
                break
        if file_struct_block:
            key_dirs = ['references', 'scripts', 'agents', 'assets']
            actual_key_files: set[str] = set()
            ignore_dir_parts = {'__pycache__', '.git', '.DS_Store', 'node_modules'}
            for kd in key_dirs:
                kp = skill_dir / kd
                if kp.is_dir():
                    for f in kp.rglob('*'):
                        if not f.is_file():
                            continue
                        rel_parts = f.relative_to(skill_dir).parts
                        if any(part.startswith('.') or part in ignore_dir_parts for part in rel_parts):
                            continue
                        actual_key_files.add(f.name)
            ignore_files = {'__init__.py', '.DS_Store'}
            missing_in_doc = sorted(n for n in actual_key_files if n not in ignore_files and not n.endswith('.pyc') and n not in file_struct_block)
            if missing_in_doc:
                raise ValueError(
                    f"File structure diagram out of date — these files exist on disk but are not mentioned in SKILL.md: {missing_in_doc}"
                )

        # Rule 3: SKILL.md ↔ steps.md 产物名一致（按 base stem 归组比对）
        if steps_md.exists():
            artifact_pattern = re.compile(r'\b([a-z][\w-]*?)\.(json|md|txt)\b')

            def _base_stem(stem: str) -> str:
                return re.sub(r'-(raw|tmp|temp|backup|bak|old|new)$', '', stem)

            skill_names = artifact_pattern.findall(body)
            steps_text_for_check = read_text(steps_md)
            steps_names = artifact_pattern.findall(steps_text_for_check)

            skill_groups: dict[str, set[str]] = {}
            for stem, ext in skill_names:
                skill_groups.setdefault(_base_stem(stem), set()).add(f"{stem}.{ext}")
            steps_groups: dict[str, set[str]] = {}
            for stem, ext in steps_names:
                steps_groups.setdefault(_base_stem(stem), set()).add(f"{stem}.{ext}")

            for base in sorted(set(skill_groups) & set(steps_groups)):
                skill_forms = skill_groups[base]
                steps_forms = steps_groups[base]
                if skill_forms != steps_forms:
                    raise ValueError(
                        f"Cross-doc inconsistency for base name '{base}': "
                        f"SKILL.md mentions {sorted(skill_forms)}, "
                        f"steps.md mentions {sorted(steps_forms)}"
                    )

        return True, "Skill is valid!"
    except (OSError, ValueError, RuntimeError) as exc:
        return False, str(exc)


def validate_eval_plan_data(data: dict[str, Any]) -> None:
    required = {"iteration", "status", "generated_from", "cases"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"eval_plan.json missing required field(s): {', '.join(sorted(missing))}")
    if not isinstance(data["iteration"], int) or data["iteration"] < 1:
        raise ValueError("eval_plan.json 'iteration' must be a positive integer")
    if data["status"] not in {"draft", "approved"}:
        raise ValueError("eval_plan.json 'status' must be 'draft' or 'approved'")
    if not isinstance(data["generated_from"], dict):
        raise ValueError("eval_plan.json 'generated_from' must be an object")
    if not isinstance(data["cases"], list) or not data["cases"]:
        raise ValueError("eval_plan.json must contain at least one case")

    seen_ids: set[int] = set()
    for index, case in enumerate(data["cases"], start=1):
        if not isinstance(case, dict):
            raise ValueError(f"eval_plan.json case #{index} must be an object")
        case_required = {"eval_id", "name", "prompt", "expected_output", "assertions", "files", "runs_per_configuration"}
        case_missing = case_required - case.keys()
        if case_missing:
            raise ValueError(f"eval_plan.json case #{index} missing field(s): {', '.join(sorted(case_missing))}")
        eval_id = case["eval_id"]
        if not isinstance(eval_id, int) or eval_id < 1:
            raise ValueError(f"eval_plan.json case #{index} has invalid eval_id")
        if eval_id in seen_ids:
            raise ValueError(f"eval_plan.json has duplicate eval_id: {eval_id}")
        seen_ids.add(eval_id)
        if not isinstance(case["name"], str) or not case["name"].strip():
            raise ValueError(f"eval_plan.json case #{index} must have a non-empty name")
        if not isinstance(case["prompt"], str) or not case["prompt"].strip():
            raise ValueError(f"eval_plan.json case #{index} must have a non-empty prompt")
        if not isinstance(case["expected_output"], str) or not case["expected_output"].strip():
            raise ValueError(f"eval_plan.json case #{index} must have a non-empty expected_output")
        if not isinstance(case["assertions"], list) or not case["assertions"]:
            raise ValueError(f"eval_plan.json case #{index} must include at least one assertion")
        for assertion in case["assertions"]:
            if not isinstance(assertion, dict):
                raise ValueError(f"eval_plan.json case #{index} contains a non-object assertion")
            if not {"name", "grader", "criteria"} <= assertion.keys():
                raise ValueError(f"eval_plan.json case #{index} has an assertion missing name/grader/criteria")
            if assertion["grader"] not in {"code", "llm"}:
                raise ValueError(f"eval_plan.json case #{index} has invalid grader '{assertion['grader']}'")
        if not isinstance(case["files"], list) or any(not isinstance(item, str) for item in case["files"]):
            raise ValueError(f"eval_plan.json case #{index} files must be a string array")
        runs = case["runs_per_configuration"]
        if not isinstance(runs, int) or runs < 1:
            raise ValueError(f"eval_plan.json case #{index} must have runs_per_configuration >= 1")


def validate_feedback_data(data: dict[str, Any]) -> None:
    if data.get("status") not in {"in_progress", "complete"}:
        raise ValueError("feedback.json 'status' must be 'in_progress' or 'complete'")
    reviews = data.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("feedback.json 'reviews' must be an array")
    for index, review in enumerate(reviews, start=1):
        if not isinstance(review, dict):
            raise ValueError(f"feedback.json review #{index} must be an object")
        run_id = review.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(f"feedback.json review #{index} must include run_id")
        skill_feedback = review.get("skill_feedback", review.get("feedback", ""))
        eval_feedback = review.get("eval_feedback", "")
        if not isinstance(skill_feedback, str) or not isinstance(eval_feedback, str):
            raise ValueError(f"feedback.json review #{index} feedback fields must be strings")


def validate_suggestions_data(data: dict[str, Any]) -> None:
    required = {"summary", "priority_order", "skill_suggestions", "eval_suggestions", "trigger_suggestions", "do_not_change"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"suggestions.json missing required field(s): {', '.join(sorted(missing))}")
    if not isinstance(data["summary"], str):
        raise ValueError("suggestions.json 'summary' must be a string")
    for key in ("priority_order", "do_not_change"):
        if not isinstance(data[key], list) or any(not isinstance(item, str) for item in data[key]):
            raise ValueError(f"suggestions.json '{key}' must be a string array")
    for key in ("skill_suggestions", "eval_suggestions", "trigger_suggestions"):
        items = data[key]
        if not isinstance(items, list):
            raise ValueError(f"suggestions.json '{key}' must be an array")
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"suggestions.json '{key}' item #{index} must be an object")
            if "id" not in item or "action" not in item:
                raise ValueError(f"suggestions.json '{key}' item #{index} must include id and action")


def validate_workspace(workspace_path: str | Path) -> tuple[bool, str]:
    workspace = Path(workspace_path)
    if workspace.is_file():
        files = [workspace]
    else:
        files = [
            workspace / "eval_plan.json",
            workspace / "feedback.json",
            workspace / "suggestions.json",
        ]

    validated: list[str] = []
    try:
        for file_path in files:
            if not file_path.exists():
                continue
            data = load_json(file_path)
            if file_path.name == "eval_plan.json":
                validate_eval_plan_data(data)
            elif file_path.name == "feedback.json":
                validate_feedback_data(data)
            elif file_path.name == "suggestions.json":
                validate_suggestions_data(data)
            else:
                continue
            validated.append(file_path.name)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, str(exc)

    if not validated:
        return False, "No recognizable workspace artifact found (expected eval_plan.json, feedback.json, or suggestions.json)"
    return True, f"Workspace artifacts are valid: {', '.join(validated)}"


def validate_path(target: str | Path) -> tuple[bool, str]:
    path = Path(target)
    if path.is_dir() and (path / "SKILL.md").exists():
        return validate_skill(path)
    return validate_workspace(path)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 quick_validate.py <skill_directory_or_workspace>")
        sys.exit(1)

    valid, message = validate_path(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
