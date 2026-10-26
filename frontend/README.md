# Research Studio frontend

React and Vite frontend for the multi-agent research project. UI and local connection files live in this folder. The bridge calls the project's Python pipeline directly; reader validation and page extraction are owned by `pipeline.py` and `tools.py`.

## Start with Bun

Use the project's existing Python `.venv` with its existing dependencies and API keys. From this `frontend` folder:

```powershell
bun install
bun run dev
```

Open http://127.0.0.1:5173. The command starts Vite on port 5173 and the frontend's local connection bridge on port 8000. Keep the command running while using the app. Press Ctrl+C to stop both.

## How it connects

Browsers cannot directly import or execute Python. `bridge.py` exposes local endpoints for the frontend and starts `bridge_worker.py` for each research run. The worker imports and calls the existing `run_research_pipeline(topic)` function. It observes the pipeline's existing console stage messages and returned state; it does not patch or replace project functions or change any provider, model, prompt, tool, or retry settings.

Your original code reads `NVIDIA_API_KEY` and `TAVILY_API_KEY` from the root `.env` file. Those keys stay in Python. The frontend displays the model configured in the original `agents.py`.

Reports, critic feedback, and source URLs come from the pipeline's output. Stage status comes from the pipeline's existing progress messages. Source URLs are extracted from returned research text; the original pipeline does not expose an exact count of scraped pages, so new runs show “Source analysis ready” when the reader finishes.

History is saved inside `frontend/.data/research.sqlite3`. Runs continue if the browser closes. One run executes at a time. Provider errors appear in the UI, with a retry button. A bridge restart marks unfinished runs as interrupted.

## Build and checks

```powershell
bun run build
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The automated connection checks use temporary storage and simulated pipeline output, without calling external providers or modifying project files.

For separate processes, run `..\.venv\Scripts\python.exe -B bridge.py` in one terminal and `bun run dev:ui` in another. `bun run preview` serves the built frontend and proxies `/api` to a running local bridge.
