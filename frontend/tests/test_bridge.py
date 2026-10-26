"""Check the frontend bridge against the original pipeline without provider calls."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

FRONTEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND))
import bridge
import bridge_worker


def original_pipeline(reader_fails=False):
    search, reader, writer, critic = (MagicMock() for _ in range(4))
    search.invoke.return_value = {"messages": [SimpleNamespace(name="web_search", content="Title: Evidence\nURL: https://example.org/research\nSnippet: Research source"), SimpleNamespace(content="[Evidence](https://example.org/research) supports this topic.")]}
    reader.invoke.return_value = {"messages": [SimpleNamespace(tool_calls=[{"id": "read-1", "name": "scrape_url", "args": {"url": "https://example.org/research"}}]), SimpleNamespace(name="scrape_url", tool_call_id="read-1", content="Detailed source content."), SimpleNamespace(content="Detailed source content.")]}
    if reader_fails:
        reader.invoke.side_effect = RuntimeError("Provider failed")
    writer.invoke.return_value = "# Report\nFinding from [Evidence](https://example.org/research)."
    critic.invoke.return_value = "**Score:** 8.5/10\nStrengths: well sourced."
    agents = SimpleNamespace(build_search_agent=lambda: search, build_reader_agent=lambda: reader, writer_chain=writer, critic_chain=critic)
    spec = importlib.util.spec_from_file_location("pipeline", FRONTEND.parent / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"agents": agents}):
        spec.loader.exec_module(module)
    return module


class WorkerCompatibilityTests(unittest.TestCase):
    def test_real_subprocess_can_start_with_an_independent_input_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / 'bridge_worker.py'
            worker.write_text('import json, sys\nprint(json.dumps({"ready": True, "input_is_empty": sys.stdin.read() == ""}), flush=True)\n', encoding='utf-8')
            with patch.object(bridge, 'FRONTEND', Path(directory)), tempfile.TemporaryFile(mode='w+', encoding='utf-8') as errors:
                process = bridge.spawn_worker('Startup check', errors)
                try:
                    output, _ = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 0)
                    self.assertEqual(json.loads(output), {"ready": True, "input_is_empty": True})
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_original_pipeline_emits_all_stages_and_returns_its_state(self):
        events = []
        with patch.dict(sys.modules, {"pipeline": original_pipeline()}):
            code = bridge_worker.run("Research workflows", events.append)
        self.assertEqual(code, 0)
        self.assertEqual([item["stage"] for item in events if item["type"] == "stage"], list(bridge.STAGES))
        outputs = {key: value for item in events if item["type"] == "output" for key, value in item["data"].items()}
        result = events[-1]
        self.assertEqual(result["type"], "result")
        self.assertEqual(outputs, result["data"])
        self.assertEqual(set(result["data"]), {"search_results", "scraped_content", "report", "feedback"})

    def test_original_errors_are_reported_without_fabricated_results(self):
        events = []
        with patch.dict(sys.modules, {"pipeline": original_pipeline(reader_fails=True)}):
            code = bridge_worker.run("Research workflows", events.append)
        self.assertEqual(code, 1)
        self.assertEqual(events[-1], {"type": "error", "message": "Provider failed"})
        self.assertFalse(any(item["type"] == "result" for item in events))
        self.assertTrue(any("search_results" in item.get("data", {}) for item in events))


class BridgeHttpTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for change in (
            patch.object(bridge, "DATABASE", Path(directory.name) / "history.sqlite3"),
            patch.object(bridge, "executor", MagicMock()),
            patch.dict(os.environ, {"NVIDIA_API_KEY": "test-secret-nvidia", "TAVILY_API_KEY": "test-secret-tavily"}),
        ):
            change.start()
            self.addCleanup(change.stop)
        self.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, body=None):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_create_progress_persistence_and_reopen(self):
        status, run = self.request("/api/research", {"topic": "  Research workflow  "})
        self.assertEqual(status, 202)
        self.assertEqual(run["topic"], "Research workflow")
        self.assertEqual(self.request("/api/research", {"topic": "Another research run"})[0], 409)
        events = []
        with patch.dict(sys.modules, {"pipeline": original_pipeline()}):
            bridge_worker.run(run["topic"], events.append)
        for event in events:
            bridge.process_event(run, event)
            bridge.save_run(run)
        status, saved = self.request(f"/api/research/{run['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["score"], 8.5)
        self.assertEqual(len(saved["sources"]), 1)
        self.assertEqual(saved["sources"][0]["title"], "Evidence")
        self.assertTrue(all(step["status"] == "complete" for step in saved["steps"].values()))
        self.assertEqual(self.request("/api/research")[1][0]["id"], run["id"])

    def test_configuration_validation_and_secret_redaction(self):
        status, config = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("test-secret-nvidia", json.dumps(config))
        self.assertEqual(self.request("/api/research", {"topic": "  "})[0], 422)
        self.assertEqual(self.request("/api/research", {"topic": 42})[0], 422)
        self.assertEqual(self.request("/api/research/missing")[0], 404)
        with patch.dict(os.environ, {"NVIDIA_API_KEY": ""}):
            self.assertEqual(self.request("/api/research", {"topic": "Valid topic"})[0], 503)
        self.assertEqual(bridge.redact("Failure test-secret-nvidia"), "Failure [redacted]")

    def test_restart_recovers_unfinished_runs(self):
        _, run = self.request("/api/research", {"topic": "Interrupted research"})
        run["status"] = "running"
        run["steps"]["reader"]["status"] = "running"
        bridge.save_run(run)
        bridge.recover_runs()
        saved = bridge.read_run(run["id"])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["steps"]["reader"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
