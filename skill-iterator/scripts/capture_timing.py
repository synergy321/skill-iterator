#!/usr/bin/env python3
"""Normalize task notifications into timing.json (and optional run_status.json)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_text(args: argparse.Namespace) -> str:
    if args.notification_file:
        return args.notification_file.read_text()
    if args.notification:
        return args.notification
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide --notification-file, --notification, or stdin input")


def deep_find(data: Any, key: str) -> Any | None:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = deep_find(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = deep_find(item, key)
            if found is not None:
                return found
    return None


def parse_json_like(text: str) -> dict[str, int | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"total_tokens": None, "duration_ms": None, "tool_uses": None}

    return {
        "total_tokens": to_int(
            deep_find(data, "total_tokens")
            or deep_find(data, "totalTokens")
            or deep_find(data, "subagent_tokens")
            or deep_find(data, "subagentTokens")
            or deep_find(data, "tokens")
        ),
        "duration_ms": to_int(
            deep_find(data, "duration_ms")
            or deep_find(data, "durationMs")
        ),
        "tool_uses": to_int(
            deep_find(data, "tool_uses")
            or deep_find(data, "toolUses")
        ),
    }


def parse_regex(text: str) -> dict[str, int | None]:
    def grab(pattern: str) -> int | None:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return to_int(match.group(1))

    return {
        "total_tokens": grab(r"(?:total_tokens|subagent_tokens)\s*[:=]\s*(\d+)"),
        "duration_ms": grab(r"duration_ms\s*[:=]\s*(\d+)"),
        "tool_uses": grab(r"tool_uses\s*[:=]\s*(\d+)"),
    }


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
          return int(digits)
    return None


def parse_notification(text: str) -> tuple[dict[str, Any], str | None]:
    parsed = parse_json_like(text)
    if parsed["total_tokens"] is None and parsed["duration_ms"] is None:
        parsed = parse_regex(text)

    total_tokens = parsed["total_tokens"] if parsed["total_tokens"] is not None else 0
    duration_ms = parsed["duration_ms"] if parsed["duration_ms"] is not None else 0
    tool_uses = parsed["tool_uses"] if parsed["tool_uses"] is not None else 0

    error = None
    if parsed["total_tokens"] is None or parsed["duration_ms"] is None:
        error = "Could not find total_tokens or duration_ms in notification"

    payload: dict[str, Any] = {
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "total_duration_seconds": round(duration_ms / 1000, 3),
        "tool_uses": tool_uses,
        "captured_at": utc_now(),
    }
    if error:
        payload["parse_error"] = error
        payload["raw_excerpt"] = text[:400]
    return payload, error


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture timing/token info from a task notification")
    parser.add_argument("--run-dir", type=Path, required=True, help="Target run directory")
    parser.add_argument("--notification-file", type=Path, help="Path to raw task notification text")
    parser.add_argument("--notification", help="Raw notification text")
    parser.add_argument("--source", default=None, help="Override timing source label")
    parser.add_argument("--status", choices=["completed", "timeout", "incomplete", "token_exhausted", "failed"])
    parser.add_argument("--reason", default="", help="Optional run status reason")
    parser.add_argument("--started-at", default=None, help="Optional ISO timestamp for run start")
    parser.add_argument("--ended-at", default=None, help="Optional ISO timestamp for run end")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    raw_text = load_text(args)

    timing_data, _ = parse_notification(raw_text)
    timing_data["source"] = args.source or (
        args.notification_file.name if args.notification_file else "stdin"
    )

    timing_path = run_dir / "timing.json"
    existing_timing = load_existing(timing_path)
    existing_timing.update(timing_data)
    write_json(timing_path, existing_timing)

    if args.status:
        status_path = run_dir / "run_status.json"
        existing_status = load_existing(status_path)
        existing_status.update(
            {
                "status": args.status,
                "reason": args.reason,
                "started_at": args.started_at or existing_status.get("started_at") or utc_now(),
                "ended_at": args.ended_at or utc_now(),
            }
        )
        write_json(status_path, existing_status)


if __name__ == "__main__":
    main()
