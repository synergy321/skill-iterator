#!/usr/bin/env python3
"""Generate and serve a review page for eval results."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import time
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path.home() / ".claude" / "skills" / "skill-creator" / "scripts"))

from quick_validate import validate_feedback_data
from scripts.utils import build_eval_case_lookup, guess_eval_id_from_name, load_eval_plan, resolve_eval_case


METADATA_FILES = {"transcript.md", "user_notes.md", "metrics.json", "task_notification.txt"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".yaml", ".yml", ".xml", ".html", ".css", ".sh", ".rb", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".sql", ".r", ".toml",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MIME_OVERRIDES = {
    ".svg": "image/svg+xml",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def get_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def normalize_review(review: dict) -> dict:
    if "skill_feedback" in review or "eval_feedback" in review:
        return {
            "skill_feedback": review.get("skill_feedback", ""),
            "eval_feedback": review.get("eval_feedback", ""),
        }
    return {
        "skill_feedback": review.get("feedback", ""),
        "eval_feedback": "",
    }


def load_feedback_map(feedback_path: Path) -> dict[str, dict]:
    data = load_json(feedback_path)
    feedback_map: dict[str, dict] = {}
    for review in data.get("reviews", []):
        run_id = review.get("run_id")
        if not run_id:
            continue
        normalized = normalize_review(review)
        feedback_map[run_id] = {
            "skill_feedback": normalized["skill_feedback"],
            "eval_feedback": normalized["eval_feedback"],
        }
    return feedback_map


def find_runs(workspace: Path) -> list[dict]:
    runs: list[dict] = []
    plan_cases = build_eval_case_lookup(load_eval_plan(workspace))
    _find_runs_recursive(workspace, workspace, runs, plan_cases)
    runs.sort(key=lambda r: (r.get("eval_id") if r.get("eval_id") is not None else float("inf"), r["id"]))
    return runs


def _find_runs_recursive(root: Path, current: Path, runs: list[dict], plan_cases: dict[int, dict]) -> None:
    if not current.is_dir():
        return

    if (current / "outputs").is_dir() or (current / "run_status.json").exists() or (current / "grading.json").exists():
        run = build_run(root, current, plan_cases)
        if run:
            runs.append(run)
        return

    skip = {"node_modules", ".git", "__pycache__", "skill", "inputs"}
    for child in sorted(current.iterdir()):
        if child.is_dir() and child.name not in skip:
            _find_runs_recursive(root, child, runs, plan_cases)


def find_eval_dir(root: Path, run_dir: Path) -> Path:
    current = run_dir
    while current != root and current != current.parent:
        if guess_eval_id_from_name(current.name) is not None:
            return current
        current = current.parent
    if guess_eval_id_from_name(root.name) is not None:
        return root
    return run_dir.parent


def build_run(root: Path, run_dir: Path, plan_cases: dict[int, dict]) -> dict | None:
    prompt = ""
    eval_id = None
    eval_name = ""
    expected_output = ""
    files: list[str] = []
    planned_assertions: list[dict] = []
    runs_per_configuration = 0
    eval_dir = find_eval_dir(root, run_dir)

    for candidate in [run_dir / "eval_metadata.json", run_dir.parent / "eval_metadata.json", eval_dir / "eval_metadata.json"]:
        if candidate.exists():
            metadata = load_json(candidate)
            prompt = metadata.get("prompt", prompt)
            eval_id = metadata.get("eval_id", eval_id)
            eval_name = metadata.get("eval_name") or metadata.get("name") or eval_name
            expected_output = metadata.get("expected_output", expected_output)
            files = metadata.get("files", files)
            planned_assertions = metadata.get("assertions", planned_assertions)
            runs_per_configuration = metadata.get("runs_per_configuration", runs_per_configuration)

    case = resolve_eval_case(plan_cases, eval_dir_name=eval_dir.name, eval_id=eval_id)
    if case:
        eval_id = case.get("eval_id", eval_id)
        prompt = case.get("prompt") or prompt
        eval_name = case.get("name") or eval_name
        expected_output = case.get("expected_output") or expected_output
        files = case.get("files", files)
        planned_assertions = case.get("assertions", planned_assertions)
        runs_per_configuration = case.get("runs_per_configuration", runs_per_configuration)
    elif not isinstance(eval_id, int):
        eval_id = guess_eval_id_from_name(eval_dir.name)

    if not prompt:
        for candidate in [run_dir / "transcript.md", run_dir / "outputs" / "transcript.md"]:
            if candidate.exists():
                try:
                    text = candidate.read_text()
                except OSError:
                    continue
                match = re.search(r"## (?:Eval )?Prompt\n([\s\S]*?)(?=\n##|$)", text)
                if match:
                    prompt = match.group(1).strip()
                    break

    if not prompt:
        prompt = "(No prompt found)"
    if not eval_name:
        eval_name = f"Eval {eval_id}" if eval_id is not None else run_dir.name

    run_id = str(run_dir.relative_to(root)).replace("/", "-").replace("\\", "-")

    outputs_dir = run_dir / "outputs"
    output_files: list[dict] = []
    if outputs_dir.is_dir():
        for file in sorted(outputs_dir.iterdir()):
            if file.is_file() and file.name not in METADATA_FILES:
                output_files.append(embed_file(file))

    grading = load_json(run_dir / "grading.json") or load_json(run_dir.parent / "grading.json")
    run_status = load_json(run_dir / "run_status.json") or load_json(run_dir.parent / "run_status.json")

    return {
        "id": run_id,
        "prompt": prompt,
        "eval_id": eval_id,
        "eval_name": eval_name,
        "expected_output": expected_output,
        "files": files if isinstance(files, list) else [],
        "planned_assertions": planned_assertions if isinstance(planned_assertions, list) else [],
        "runs_per_configuration": runs_per_configuration if isinstance(runs_per_configuration, int) else 0,
        "outputs": output_files,
        "grading": grading or None,
        "run_status": run_status or None,
    }


def embed_file(path: Path) -> dict:
    ext = path.suffix.lower()
    mime = get_mime_type(path)

    if ext in TEXT_EXTENSIONS:
        try:
            content = path.read_text(errors="replace")
        except OSError:
            content = "(Error reading file)"
        return {"name": path.name, "type": "text", "content": content}
    if ext in IMAGE_EXTENSIONS:
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "image", "mime": mime, "data_uri": f"data:{mime};base64,{b64}"}
    if ext == ".pdf":
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "pdf", "data_uri": f"data:{mime};base64,{b64}"}
    if ext == ".xlsx":
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "xlsx", "data_b64": b64}

    try:
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
    except OSError:
        return {"name": path.name, "type": "error", "content": "(Error reading file)"}
    return {"name": path.name, "type": "binary", "mime": mime, "data_uri": f"data:{mime};base64,{b64}"}


def load_previous_iteration(workspace: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    feedback_map = load_feedback_map(workspace / "feedback.json")
    prev_runs = find_runs(workspace)
    for run in prev_runs:
        result[run["id"]] = {
            "feedback": feedback_map.get(run["id"], {"skill_feedback": "", "eval_feedback": ""}),
            "outputs": run.get("outputs", []),
        }
    for run_id, feedback in feedback_map.items():
        result.setdefault(run_id, {"feedback": feedback, "outputs": []})
    return result


def parse_iteration_number(workspace: Path) -> int | None:
    match = re.search(r"iteration-(\d+)", workspace.name)
    if match:
        return int(match.group(1))
    return None


def generate_html(
    runs: list[dict],
    skill_name: str,
    iteration: int | None,
    previous: dict[str, dict] | None = None,
    benchmark: dict | None = None,
    suggestions: dict | None = None,
) -> str:
    template_path = Path(__file__).resolve().parents[2] / "assets" / "eval_viewer" / "viewer.html"
    template = template_path.read_text()

    previous_feedback: dict[str, dict] = {}
    previous_outputs: dict[str, list[dict]] = {}
    if previous:
        for run_id, data in previous.items():
            if data.get("feedback"):
                previous_feedback[run_id] = data["feedback"]
            if data.get("outputs"):
                previous_outputs[run_id] = data["outputs"]

    embedded = {
        "skill_name": skill_name,
        "iteration": iteration,
        "runs": runs,
        "previous_feedback": previous_feedback,
        "previous_outputs": previous_outputs,
    }
    if benchmark:
        embedded["benchmark"] = benchmark
    if suggestions:
        embedded["suggestions"] = suggestions

    data_json = json.dumps(embedded).replace("</", "<\\/")
    return template.replace("/*__EMBEDDED_DATA__*/", f"const EMBEDDED_DATA = {data_json};")


def _kill_port(port: int) -> None:
    try:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5)
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    os.kill(int(pid_str.strip()), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
        if result.stdout.strip():
            time.sleep(0.5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


class ReviewHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        workspace: Path,
        skill_name: str,
        feedback_path: Path,
        previous: dict[str, dict],
        benchmark_path: Path | None,
        suggestions_path: Path | None,
        iteration: int | None,
        *args,
        **kwargs,
    ):
        self.workspace = workspace
        self.skill_name = skill_name
        self.feedback_path = feedback_path
        self.previous = previous
        self.benchmark_path = benchmark_path
        self.suggestions_path = suggestions_path
        self.iteration = iteration
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            runs = find_runs(self.workspace)
            benchmark = load_json(self.benchmark_path) if self.benchmark_path else None
            suggestions = load_json(self.suggestions_path) if self.suggestions_path else None
            html = generate_html(runs, self.skill_name, self.iteration, self.previous, benchmark, suggestions)
            content = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if self.path == "/api/feedback":
            data = b"{}"
            if self.feedback_path.exists():
                data = self.feedback_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/api/feedback":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or "reviews" not in data:
                raise ValueError("Expected JSON object with 'reviews' key")
            validate_feedback_data(data)
            self.feedback_path.write_text(json.dumps(data, indent=2) + "\n")
            resp = b'{"ok":true}'
            self.send_response(200)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            resp = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and serve eval review")
    parser.add_argument("workspace", type=Path, help="Path to workspace directory")
    parser.add_argument("--port", "-p", type=int, default=3117, help="Server port (default: 3117)")
    parser.add_argument("--skill-name", "-n", type=str, default=None, help="Skill name for header")
    parser.add_argument("--previous-workspace", type=Path, default=None, help="Path to previous iteration workspace")
    parser.add_argument("--benchmark", type=Path, default=None, help="Path to benchmark.json")
    parser.add_argument("--suggestions", type=Path, default=None, help="Path to suggestions.json")
    parser.add_argument("--static", "-s", type=Path, default=None, help="Write standalone HTML to this path")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"Error: {workspace} is not a directory", file=sys.stderr)
        sys.exit(1)

    runs = find_runs(workspace)
    if not runs:
        print(f"No runs found in {workspace}", file=sys.stderr)
        sys.exit(1)

    skill_name = args.skill_name or workspace.name.replace("-workspace", "")
    feedback_path = workspace / "feedback.json"
    iteration = parse_iteration_number(workspace)

    previous: dict[str, dict] = {}
    if args.previous_workspace:
        previous = load_previous_iteration(args.previous_workspace.resolve())

    benchmark_path = args.benchmark.resolve() if args.benchmark else None
    if benchmark_path is None:
        default_benchmark = workspace / "benchmark.json"
        benchmark_path = default_benchmark if default_benchmark.exists() else None

    suggestions_path = args.suggestions.resolve() if args.suggestions else None
    if suggestions_path is None:
        default_suggestions = workspace / "suggestions.json"
        suggestions_path = default_suggestions if default_suggestions.exists() else None

    benchmark = load_json(benchmark_path) if benchmark_path else None
    suggestions = load_json(suggestions_path) if suggestions_path else None

    if args.static:
        html = generate_html(runs, skill_name, iteration, previous, benchmark, suggestions)
        args.static.parent.mkdir(parents=True, exist_ok=True)
        args.static.write_text(html)
        print(f"\n  Static viewer written to: {args.static}\n")
        sys.exit(0)

    port = args.port
    _kill_port(port)
    handler = partial(
        ReviewHandler,
        workspace,
        skill_name,
        feedback_path,
        previous,
        benchmark_path,
        suggestions_path,
        iteration,
    )
    try:
        server = HTTPServer(("127.0.0.1", port), handler)
    except OSError:
        server = HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]

    url = f"http://localhost:{port}"
    print(f"\n  Eval Viewer")
    print(f"  ─────────────────────────────────")
    print(f"  URL:         {url}")
    print(f"  Workspace:   {workspace}")
    print(f"  Feedback:    {feedback_path}")
    if previous:
        print(f"  Previous:    {args.previous_workspace} ({len(previous)} runs)")
    if benchmark_path:
        print(f"  Benchmark:   {benchmark_path}")
    if suggestions_path:
        print(f"  Suggestions: {suggestions_path}")
    print(f"\n  Press Ctrl+C to stop.\n")

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
