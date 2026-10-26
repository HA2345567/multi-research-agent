"""Cloud authentication and streaming checks without external API calls."""

import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Thread
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from api import research as cloud
from http.server import ThreadingHTTPServer


def successful_pipeline(topic, emit):
    emit({"type": "ready"})
    for stage in ("search", "reader", "writer", "critic"):
        emit({"type": "stage", "stage": stage})
    emit({"type": "result", "data": {"search_results": "https://example.org/source", "scraped_content": "Read source", "report": "Research report", "feedback": "Score: 8/10"}})
    return 0


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"RESEARCH_ACCESS_KEY": "private-access-code", "NVIDIA_API_KEY": "nvidia-test-secret", "TAVILY_API_KEY": "tavily-test-secret"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_health_does_not_expose_keys_and_requires_authentication(self):
        status = cloud.cloud_health()
        self.assertTrue(status["authentication_required"])
        self.assertEqual(status["mode"], "cloud")
        self.assertNotIn("nvidia-test-secret", json.dumps(status))
        self.assertFalse(cloud.cloud_health("private-access-code")["authentication_required"])
        self.assertFalse(cloud.authorized("wrong-key"))

    def test_unconfigured_access_code_never_grants_access(self):
        with patch.dict(os.environ, {"RESEARCH_ACCESS_KEY": ""}):
            self.assertFalse(cloud.authorized(""))
            self.assertIn("RESEARCH_ACCESS_KEY", cloud.cloud_health()["missing"])

    def test_stream_runs_all_stages_and_emits_complete_output(self):
        snapshots = []
        cloud.stream_run("A topic", lambda item: snapshots.append(json.loads(json.dumps(item))), runner=successful_pipeline)
        self.assertEqual(snapshots[0]["status"], "running")
        self.assertEqual(snapshots[-1]["status"], "complete")
        self.assertEqual(snapshots[-1]["score"], 8.0)
        self.assertIsNotNone(snapshots[-1]["finished_at"])
        self.assertEqual(len(snapshots[-1]["sources"]), 1)
        self.assertTrue(all(step["status"] == "complete" for step in snapshots[-1]["steps"].values()))

    def test_stream_failure_marks_stage_failed_and_redacts_secret(self):
        def fail(topic, emit):
            emit({"type": "stage", "stage": "reader"})
            emit({"type": "error", "message": "Failure nvidia-test-secret"})
            return 1
        snapshots = []
        cloud.stream_run("A topic", lambda item: snapshots.append(json.loads(json.dumps(item))), runner=fail)
        self.assertEqual(snapshots[-1]["status"], "failed")
        self.assertEqual(snapshots[-1]["steps"]["reader"]["status"], "failed")
        self.assertNotIn("nvidia-test-secret", snapshots[-1]["error"])

    def test_http_auth_and_stream_contract(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), cloud.handler)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/api/research"
        request = urllib.request.Request(url, data=b'{"topic":"Research topic"}', headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        request.add_header("X-Research-Key", "private-access-code")
        with patch.object(cloud, "run_pipeline", side_effect=successful_pipeline), urllib.request.urlopen(request) as response:
            self.assertIn("application/x-ndjson", response.headers["Content-Type"])
            snapshots = [json.loads(line) for line in response]
        self.assertEqual(snapshots[-1]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
