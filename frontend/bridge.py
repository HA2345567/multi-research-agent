"""Frontend-only HTTP bridge to the project's unchanged Python pipeline.

Uses the standard library plus python-dotenv, already required by the project.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
from threading import Lock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import load_dotenv

FRONTEND = Path(__file__).resolve().parent
ROOT = FRONTEND.parent
DATABASE = FRONTEND / ".data" / "research.sqlite3"
STAGES = ("search", "reader", "writer", "critic")
load_dotenv(ROOT / ".env")
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-bridge")
run_lock = Lock()
active_process = None


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=15)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            yield db
    finally:
        db.close()


def save_run(run):
    with connection() as db:
        db.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?)", (run["id"], run["created_at"], json.dumps(run)))


def read_run(run_id):
    with connection() as db:
        row = db.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_runs():
    with connection() as db:
        rows = db.execute("SELECT payload FROM runs ORDER BY created_at DESC LIMIT 100").fetchall()
    return [json.loads(row[0]) for row in rows]


def model_name():
    try:
        tree = ast.parse((ROOT / "agents.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ChatOpenAI":
                for keyword in node.keywords:
                    if keyword.arg == "model" and isinstance(keyword.value, ast.Constant):
                        return str(keyword.value.value)
    except (OSError, SyntaxError):
        pass
    return "Configured in agents.py"


def health():
    missing = [name for name in ("NVIDIA_API_KEY", "TAVILY_API_KEY") if not os.getenv(name)]
    return {"status": "ready" if not missing else "configuration_required", "missing": missing, "model": model_name()}


def redact(message):
    for name in ("NVIDIA_API_KEY", "TAVILY_API_KEY"):
        secret = os.getenv(name)
        if secret:
            message = message.replace(secret, "[redacted]")
    if "ResourceExhausted" in message or "Service Unavailable" in message:
        return "NVIDIA is temporarily at capacity. Please wait a moment and retry this research."
    return message[:1200]


def update_outputs(run, outputs):
    for key in ("search_results", "scraped_content", "report", "feedback"):
        if key in outputs:
            run[key] = outputs[key] if isinstance(outputs[key], str) else str(outputs[key])
    text = run.get("search_results", "") + "\n" + run.get("report", "")
    titles = {url.rstrip(".,;:!?)]}*`"): title for title, url in re.findall(r"\[([^\]]+)\]\((https?://[^\s]+)\)", text)}
    sources = []
    seen = set()
    for match in re.finditer(r"https?://[^\s<>\"']+", text):
        url = match.group().rstrip(".,;:!?)]}*`")
        try:
            hostname = urlsplit(url).hostname
        except ValueError:
            continue
        if not hostname or url in seen:
            continue
        seen.add(url)
        sources.append({"url": url, "title": titles.get(url, hostname.removeprefix("www.")), "snippet": ""})
    run["sources"] = sources
    run["word_count"] = len(run.get("report", "").split())
    score = re.search(r"Score\s*:\s*(\d+(?:\.\d+)?)\s*/\s*10", run.get("feedback", "").replace("**", ""), re.IGNORECASE)
    run["score"] = float(score.group(1)) if score and 0 <= float(score.group(1)) <= 10 else None


def process_event(run, event):
    if event["type"] == "ready":
        run["worker_started_at"] = now()
    elif event["type"] == "stage" and event.get("stage") in STAGES:
        stage = event["stage"]
        for prior in STAGES[:STAGES.index(stage)]:
            run["steps"][prior].update(status="complete", finished_at=run["steps"][prior].get("finished_at", now()))
        run["steps"][stage].update(status="running", started_at=now())
    elif event["type"] in ("output", "result"):
        update_outputs(run, event["data"])
        if event["type"] == "result":
            run["status"] = "complete"
            for step in run["steps"].values():
                step.update(status="complete", finished_at=step.get("finished_at", now()))
    elif event["type"] == "error":
        raise RuntimeError(event["message"])


def spawn_worker(topic, errors):
    # The bridge reads its own stdin to receive shutdown messages. Inheriting
    # that pipe can block Windows Python's stdio initialization before main.
    return subprocess.Popen([sys.executable, "-B", str(FRONTEND / "bridge_worker.py"), topic], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})


def execute_run(run_id):
    global active_process
    run = read_run(run_id)
    run["status"] = "running"
    save_run(run)
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            process = spawn_worker(run["topic"], errors)
            active_process = process
            try:
                for line in process.stdout:
                    process_event(run, json.loads(line))
                    save_run(run)
                code = process.wait()
                if code != 0 or run["status"] != "complete":
                    errors.seek(0)
                    raise RuntimeError(errors.read()[-1200:] or "The pipeline stopped before returning a result. Please retry.")
            finally:
                if process.poll() is None:
                    process.terminate()
                process.stdout.close()
                process.wait()
    except Exception as error:
        run.update(status="failed", error=redact(str(error)))
        for step in run["steps"].values():
            if step["status"] == "running":
                step.update(status="failed", finished_at=now())
    finally:
        active_process = None
        run["finished_at"] = now()
        save_run(run)


def create_run(topic):
    with run_lock:
        if any(run["status"] in ("queued", "running") for run in list_runs()):
            return 409, {"detail": "Research is already running. Open it from your history."}
        run = {"id": uuid4().hex, "topic": topic, "status": "queued", "created_at": now(), "finished_at": None, "sources": [], "pages_read": [], "report": "", "feedback": "", "score": None, "word_count": 0, "error": None, "steps": {stage: {"status": "waiting"} for stage in STAGES}}
        save_run(run)
        executor.submit(execute_run, run["id"])
        return 202, run


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self.respond(200, health())
        elif path == "/api/research":
            self.respond(200, [{key: run.get(key) for key in ("id", "topic", "status", "created_at", "finished_at", "score", "word_count")} for run in list_runs()])
        elif path.startswith("/api/research/"):
            run = read_run(path.removeprefix("/api/research/"))
            self.respond(200 if run else 404, run or {"detail": "Research run not found"})
        else:
            self.respond(404, {"detail": "Endpoint not found"})

    def do_POST(self):
        if urlsplit(self.path).path != "/api/research":
            self.respond(404, {"detail": "Endpoint not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError()
            body = json.loads(self.rfile.read(size))
            topic = body.get("topic", "").strip()
            if not 3 <= len(topic) <= 1000:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            self.respond(422, {"detail": "Enter a research topic between 3 and 1,000 characters."})
            return
        missing = health()["missing"]
        if missing:
            self.respond(503, {"detail": "Add these keys to the project .env: " + ", ".join(missing)})
            return
        self.respond(*create_run(topic))


def recover_runs():
    for run in list_runs():
        if run["status"] in ("queued", "running"):
            run.update(status="failed", error="The connection bridge restarted during this run. Please retry.", finished_at=now())
            for step in run["steps"].values():
                if step["status"] == "running":
                    step.update(status="failed", finished_at=now())
            save_run(run)


def serve():
    recover_runs()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)

    def stop_from_parent():
        for line in sys.stdin:
            if line.strip() == "stop":
                break
        if active_process and active_process.poll() is None:
            active_process.terminate()
        server.shutdown()

    if not sys.stdin.isatty():
        Thread(target=stop_from_parent, daemon=True).start()
    def interrupt(*args):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupt)
    print("Frontend connection bridge ready at http://127.0.0.1:8000", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if active_process and active_process.poll() is None:
            active_process.terminate()
        server.server_close()
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    serve()
