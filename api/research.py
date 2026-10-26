"""Vercel streaming adapter for the existing research pipeline."""

import hmac
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import sys
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from frontend.bridge import health, now, process_event, redact
from frontend.bridge_worker import run as run_pipeline

run_lock = Lock()


def authorized(provided):
    expected = os.getenv("RESEARCH_ACCESS_KEY", "")
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


def cloud_health(provided=""):
    result = health()
    result.update(mode="cloud", authentication_required=not authorized(provided))
    if not os.getenv("RESEARCH_ACCESS_KEY"):
        result["status"] = "configuration_required"
        result["missing"].append("RESEARCH_ACCESS_KEY")
    return result


def initial_run(topic):
    return {"id": uuid4().hex, "topic": topic, "status": "running", "created_at": now(), "finished_at": None, "sources": [], "pages_read": [], "report": "", "feedback": "", "score": None, "word_count": 0, "error": None, "steps": {stage: {"status": "waiting"} for stage in ("search", "reader", "writer", "critic")}}


def stream_run(topic, send, runner=None):
    runner = runner or run_pipeline
    research = initial_run(topic)
    send(research)

    def consume(event):
        if event["type"] == "error":
            research.update(status="failed", error=redact(event["message"]))
        else:
            process_event(research, event)
        send(research)

    try:
        runner(topic, consume)
        if research["status"] not in ("complete", "failed"):
            research.update(status="failed", error="The pipeline stopped before returning a result. Please retry.")
    except (BrokenPipeError, ConnectionResetError):
        raise
    except Exception as error:
        research.update(status="failed", error=redact(str(error)))
    finally:
        if research["status"] == "failed":
            for step in research["steps"].values():
                if step["status"] == "running":
                    step.update(status="failed", finished_at=now())
        research["finished_at"] = now()
    send(research)


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def json_response(self, status, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        if "health" in query or urlsplit(self.path).path.endswith("/health"):
            self.json_response(200, cloud_health(self.headers.get("X-Research-Key", "")))
        else:
            self.json_response(200, [])

    def do_POST(self):
        if not authorized(self.headers.get("X-Research-Key", "")):
            self.json_response(401, {"detail": "Enter your deployment access code to start research."})
            return
        config = cloud_health(self.headers.get("X-Research-Key", ""))
        if config["missing"]:
            self.json_response(503, {"detail": "Configure these Vercel environment variables: " + ", ".join(config["missing"])})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError()
            topic = json.loads(self.rfile.read(length)).get("topic", "").strip()
            if not 3 <= len(topic) <= 1000:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            self.json_response(422, {"detail": "Enter a topic between 3 and 1,000 characters."})
            return
        if not run_lock.acquire(blocking=False):
            self.json_response(409, {"detail": "Research is already running in this instance. Please try again shortly."})
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, no-transform")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

            def send(research):
                self.wfile.write((json.dumps(research, ensure_ascii=True) + "\n").encode("utf-8"))
                self.wfile.flush()

            stream_run(topic, send)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            run_lock.release()
