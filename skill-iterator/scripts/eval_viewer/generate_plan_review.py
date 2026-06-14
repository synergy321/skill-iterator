#!/usr/bin/env python3
"""Generate and serve a plan review page for eval_plan.json."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.utils import load_json


def collect_previous_timing(workspace: Path, current_iteration: int) -> dict:
    """Collect timing data from previous iteration for estimates."""
    if current_iteration <= 1:
        return {}

    prev_dir = workspace / f"iteration-{current_iteration - 1}"
    if not prev_dir.exists():
        return {}

    timing_by_eval: dict[str, list[dict]] = {}
    for eval_dir in sorted(prev_dir.iterdir()):
        if not eval_dir.is_dir() or not eval_dir.name.startswith("eval-"):
            continue
        eval_name = eval_dir.name
        timings: list[dict] = []
        for config_dir in sorted(eval_dir.iterdir()):
            if not config_dir.is_dir():
                continue
            for run_dir in sorted(config_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                timing_file = run_dir / "timing.json"
                if timing_file.exists():
                    data = load_json(timing_file)
                    if data:
                        data["config"] = config_dir.name
                        data["run"] = run_dir.name
                        timings.append(data)
        if timings:
            timing_by_eval[eval_name] = timings

    return timing_by_eval


def build_plan_data(iteration_dir: Path) -> dict:
    """Build the data object for the plan viewer."""
    plan = load_json(iteration_dir / "eval_plan.json")
    if not plan:
        return {"error": "eval_plan.json not found or empty"}

    iteration = plan.get("iteration", 1)
    workspace = iteration_dir.parent
    prev_timing = collect_previous_timing(workspace, iteration)

    # Attach timing estimates to each case
    cases = plan.get("cases", [])
    for case in cases:
        eval_id = case.get("eval_id")
        eval_key = f"eval-{eval_id}" if eval_id else ""
        if eval_key in prev_timing:
            timings = prev_timing[eval_key]
            tokens = [t.get("total_tokens", 0) for t in timings if t.get("total_tokens")]
            durations = [t.get("total_duration_seconds", 0) for t in timings if t.get("total_duration_seconds")]
            case["_prev_timing"] = {
                "avg_tokens": round(sum(tokens) / len(tokens)) if tokens else None,
                "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
                "run_count": len(timings),
            }

    return {
        "plan": plan,
        "iteration": iteration,
        "workspace": str(workspace),
        "has_previous_timing": bool(prev_timing),
    }


def generate_html(iteration_dir: Path) -> str:
    """Generate the complete plan review HTML."""
    template_path = Path(__file__).resolve().parents[2] / "assets" / "eval_viewer" / "plan_viewer.html"
    template = template_path.read_text(encoding="utf-8")

    data = build_plan_data(iteration_dir)
    data_json = json.dumps(data, ensure_ascii=False, indent=2)

    html = template.replace("__PLAN_DATA_PLACEHOLDER__", data_json)
    return html


class PlanReviewHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, iteration_dir: Path, **kwargs):
        self.iteration_dir = iteration_dir
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            html = generate_html(self.iteration_dir)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        elif self.path == "/plan.json":
            plan = load_json(self.iteration_dir / "eval_plan.json")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/save-plan":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                plan_data = json.loads(body)
                plan_path = self.iteration_dir / "eval_plan.json"
                plan_path.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "saved"}')
            except (json.JSONDecodeError, OSError) as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_error(404)


def serve(iteration_dir: Path, port: int = 8766):
    """Start the plan review server."""
    handler = partial(PlanReviewHandler, iteration_dir=iteration_dir)
    server = HTTPServer(("127.0.0.1", port), handler)

    def shutdown(sig, frame):
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    url = f"http://127.0.0.1:{port}"
    print(f"Plan review server running at {url}")
    webbrowser.open(url)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Generate plan review for eval_plan.json")
    parser.add_argument("iteration_dir", type=Path, help="Path to iteration-N directory")
    parser.add_argument("--static", type=str, default=None, help="Write static HTML to this path instead of serving")
    parser.add_argument("--port", type=int, default=8766, help="Port for HTTP server (default: 8766)")
    args = parser.parse_args()

    iteration_dir = args.iteration_dir.resolve()
    if not iteration_dir.exists():
        print(f"Error: {iteration_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.static:
        html = generate_html(iteration_dir)
        Path(args.static).write_text(html, encoding="utf-8")
        print(f"Static HTML written to {args.static}")
        try:
            subprocess.run(["open", args.static], check=False)
        except FileNotFoundError:
            pass
    else:
        serve(iteration_dir, args.port)


if __name__ == "__main__":
    main()
