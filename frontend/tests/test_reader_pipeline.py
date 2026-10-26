import ast
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace as Message
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from langchain.tools import tool
import requests

HERE = Path(__file__).resolve().parents[2]
URL = "https://docs.python.org/3/library/asyncio.html"
tree = ast.parse((HERE / "tools.py").read_text(encoding="utf-8"))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "scrape_url")
namespace = {"tool": tool, "requests": requests, "BeautifulSoup": BeautifulSoup}
exec(compile(ast.Module(body=[function], type_ignores=[]), "candidate_scraper", "exec"), namespace)
scraper = namespace["scrape_url"]


class ReaderFixTests(unittest.TestCase):
    def components(self, reader_content="Actual content from a source page.", call_tool=True, source_prefix=""):
        search, reader, writer, critic = (MagicMock() for _ in range(4))
        search.invoke.return_value = {"messages": [Message(name="web_search", content=source_prefix + f"Title: Source\nURL: {URL}\nSnippet: Async I/O"), Message(content="A summary without source links.")]}
        messages = [Message(tool_calls=[{"id": "read-1", "name": "scrape_url", "args": {"url": URL}}]), Message(name="scrape_url", tool_call_id="read-1", content=reader_content)] if call_tool else []
        reader.invoke.return_value = {"messages": messages + [Message(content="Reader summary")]}
        writer.invoke.return_value = "Written report"
        critic.invoke.return_value = "Score: 8/10"
        module = Message(build_search_agent=lambda: search, build_reader_agent=lambda: reader, writer_chain=writer, critic_chain=critic)
        return module, search, reader, writer

    def execute(self, components):
        spec = importlib.util.spec_from_file_location("candidate_pipeline", HERE / "pipeline.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"agents": components}), redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
            return module.run_research_pipeline("Python async I/O")

    def test_preserves_tool_urls_when_summary_drops_them(self):
        components, _, reader, _ = self.components()
        result = self.execute(components)
        self.assertIn(URL, reader.invoke.call_args.args[0]["messages"][0][1])
        self.assertIn(URL, result["search_results"])
        self.assertIn(URL, result["scraped_content"])
        self.assertIn("Actual content", result["scraped_content"])

    def test_does_not_truncate_a_late_source_url(self):
        components, _, reader, _ = self.components(source_prefix="Search detail. " * 100)
        self.execute(components)
        self.assertIn(URL, reader.invoke.call_args.args[0]["messages"][0][1])

    def test_preserves_parentheses_in_source_urls(self):
        components, search, reader, _ = self.components()
        url = "https://en.wikipedia.org/wiki/Agent_(computing)"
        search.invoke.return_value["messages"][0].content = f"Title: Agent\nURL: {url}\nSnippet: Computing agents"
        self.execute(components)
        source_list = reader.invoke.call_args.args[0]["messages"][0][1].split("Search Results:")[0]
        self.assertIn(url, source_list)

    def test_does_not_write_after_failed_scrapes(self):
        components, _, _, writer = self.components(reader_content="could not scrape the URL: 404 Not Found")
        with self.assertRaisesRegex(RuntimeError, "did not successfully scrape"):
            self.execute(components)
        writer.invoke.assert_not_called()

    def test_requires_actual_reader_tool_output(self):
        components, _, _, writer = self.components(call_tool=False)
        with self.assertRaisesRegex(RuntimeError, "did not successfully scrape"):
            self.execute(components)
        writer.invoke.assert_not_called()

    def test_rejects_search_without_tool_sources(self):
        components, search, reader, _ = self.components()
        search.invoke.return_value = {"messages": [Message(content=f"A made-up reference {URL}")]}
        with self.assertRaisesRegex(RuntimeError, "did not return usable source URLs"):
            self.execute(components)
        reader.invoke.assert_not_called()

    def test_http_errors_are_not_research_content(self):
        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        response.content = b"<html><body>404 error page</body></html>"
        with patch.object(requests, "get", return_value=response):
            result = scraper.invoke("https://example.org/missing")
        self.assertTrue(result.startswith("could not scrape"))

    def test_extracts_main_content_with_correct_unicode(self):
        response = MagicMock()
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.content = '<html><head><meta charset="utf-8"></head><body><aside>Sidebar</aside><main>asyncio — café</main></body></html>'.encode("utf-8")
        with patch.object(requests, "get", return_value=response):
            result = scraper.invoke(URL)
        self.assertEqual(result, "asyncio — café")

    def test_binary_documents_are_not_returned_as_page_text(self):
        response = MagicMock()
        response.headers = {"Content-Type": "application/pdf"}
        response.content = b"%PDF-1.7 binary content"
        with patch.object(requests, "get", return_value=response):
            result = scraper.invoke("https://example.org/paper.pdf")
        self.assertTrue(result.startswith("could not scrape"))
        self.assertIn("Unsupported page type", result)

    def test_prefers_main_content_role_over_navigation(self):
        response = MagicMock()
        response.headers = {"Content-Type": "text/html"}
        response.content = b'<html><body><div>Navigation links</div><div role="main">Actual documentation content.</div></body></html>'
        with patch.object(requests, "get", return_value=response):
            result = scraper.invoke(URL)
        self.assertEqual(result, "Actual documentation content.")

    def test_empty_pages_are_reported_as_failed_reads(self):
        response = MagicMock()
        response.headers = {"Content-Type": "text/html"}
        response.content = b"<html><body><main></main></body></html>"
        with patch.object(requests, "get", return_value=response):
            result = scraper.invoke(URL)
        self.assertTrue(result.startswith("could not scrape"))
        self.assertIn("no readable text", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
