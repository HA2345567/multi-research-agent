"""Observe the existing pipeline's stdout without changing its implementation."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
STAGE_MESSAGES = {
    "step 1 - search agent is working ...": "search",
    "step 2 - Reader agent is scraping top resources ...": "reader",
    "step 3 - Writer is drafting the report ...": "writer",
    "step 4 - critic is reviewing the report ": "critic",
}
OUTPUT_MESSAGES = {
    "\n search result ": "search_results",
    "\nscraped content: \n": "scraped_content",
    "\n Final Report\n": "report",
    "\n critic report \n": "feedback",
}


class PipelineOutput(io.TextIOBase):
    def __init__(self, emit):
        self.emit = emit
        self.enabled = False
        self.pending_key = None

    def write(self, text):
        if not self.enabled:
            return len(text)
        if text in STAGE_MESSAGES:
            self.pending_key = None
            self.emit({"type": "stage", "stage": STAGE_MESSAGES[text]})
        elif text in OUTPUT_MESSAGES:
            self.pending_key = OUTPUT_MESSAGES[text]
        elif self.pending_key and text.strip():
            self.emit({"type": "output", "data": {self.pending_key: text}})
            self.pending_key = None
        return len(text)


def run(topic, emit):
    output = PipelineOutput(emit)
    try:
        # Imports and the original tools.py startup print stay inside the worker.
        # Nothing in the original modules is patched, rewritten, or substituted.
        with redirect_stdout(output):
            from pipeline import run_research_pipeline
            output.enabled = True
            result = run_research_pipeline(topic)
        emit({"type": "result", "data": result})
        return 0
    except Exception as error:
        emit({"type": "error", "message": str(error) or type(error).__name__})
        return 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    protocol = sys.stdout

    def emit(message):
        protocol.write(json.dumps(message, ensure_ascii=True) + "\n")
        protocol.flush()

    emit({"type": "ready"})
    raise SystemExit(run(sys.argv[1], emit))
