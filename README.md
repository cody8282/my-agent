# Autoppia Subnet 36 — SOTA Miner Web Agent

A high-performance web agent for [Autoppia Web Agents Subnet 36](https://github.com/autoppia/autoppia_web_agents_subnet). Built to maximize eval score on the Infinite Web Arena (IWA) benchmark.

## Architecture

The agent uses a modular pipeline that processes each step:

```
/act request
  │
  ├── task_analyzer.py    Parse task tests → extract success criteria & sub-goals
  ├── html_processor.py   Raw HTML → compact interactive elements + page summary
  ├── planner.py          Track phase, detect stuck loops, suggest recovery
  ├── prompts.py          Assemble system + user prompt with all context
  │
  ├── agent.py            Orchestrate pipeline, call LLM with fallback chain
  ├── action_parser.py    Parse JSON response, resolve element IDs, validate
  │
  └── main.py             FastAPI entrypoint (/health, /act)
```

### Key Design Decisions

- **Model: `gpt-4.1`** — Cost/time don't affect score (`COST_WEIGHT=0.0`, `TIME_WEIGHT=0.0`). Budget math: 30 steps x ~15K tokens = ~$1.60, well under the $10 limit. Fallback chain: `gpt-4.1` → `gpt-4o` → `gpt-4.1-mini`.

- **HTML processing** — Extracts interactive elements (inputs, buttons, links, selects) with short IDs (`e1`, `e2`, ...) plus CSS selectors and XPaths. Saves ~80% tokens vs raw HTML. Page content summarized via readability + markdownify.

- **Task test analysis** — Parses the task's `tests` array to extract URL targets, required text, required elements. Presents success criteria to the LLM *before* page content to prime its reasoning.

- **Structured output** — `{"thinking": "...", "action": {...}}` gives chain-of-thought reasoning inside the gateway's forced `json_object` response format.

- **Stuck detection** — Catches repeated actions (same target 3+ times) and failure streaks (3+ consecutive). Forces the LLM to try a different approach.

- **Early completion** — Before calling the LLM, checks if URL/text criteria from task tests already pass. Returns NOOP to avoid breaking a passing state.

## Repo Structure

```
main.py              FastAPI entrypoint (validator runs: uvicorn main:app)
agent.py             Orchestrator — coordinates all modules per step
html_processor.py    DOM → interactive elements + page summary
task_analyzer.py     Extract success criteria from task tests
planner.py           Multi-step planning, stuck detection, recovery
prompts.py           System and user prompt templates
action_parser.py     Parse/validate LLM JSON, resolve element IDs
requirements.txt     Extra deps (base image has many pre-installed)
```

## Required Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Return 200 when ready. Validator polls this for ~20s after start. |
| `POST /act` | Receive task + page snapshot, return list of action(s). |

## POST /act Contract

**Request body:**
```json
{
    "task": {
        "id": "task_abc123",
        "instruction": "Add the red shoes to your cart",
        "url": "https://demo-store.com/...",
        "tests": [...]
    },
    "snapshot_html": "<html>...</html>",
    "url": "https://demo-store.com/shoes",
    "step_index": 0,
    "history": [
        {"step": 0, "action": "click", "text": null, "exec_ok": true, "error": null}
    ]
}
```

**Response:** JSON list of action objects. The validator executes only the first one.
```json
[{"type": "click", "xpath": "//button[contains(text(), 'Add to Cart')]"}]
```

Return `[]` for NOOP (do nothing).

## Action Types

| Type | Fields | Description |
|------|--------|-------------|
| `click` | `xpath` | Click an element |
| `fill` | `xpath`, `text` | Clear input and type text |
| `type` | `xpath`, `text` | Append text (no clear) |
| `select_option` | `xpath`, `text` | Select dropdown option by visible text |
| `navigate` | `url` | Go to a URL |
| `scroll` | `direction` | Scroll page (`"up"` or `"down"`) |

## LLM Access

The agent accesses LLMs through the sandbox gateway (no direct internet). Environment variables injected at runtime:

| Variable | Value |
|----------|-------|
| `OPENAI_BASE_URL` | `http://sandbox-gateway:9000/openai/v1` |
| `CHUTES_BASE_URL` | `http://sandbox-gateway:9000/chutes/v1` |
| `SANDBOX_AGENT_UID` | Your miner UID |

Every LLM request **must** include the `iwa-task-id` header (from `task["id"]`), or the gateway rejects it with 400.

The gateway automatically forces `response_format=json_object` on chat completions, so the LLM always returns valid JSON.

## Sandbox Constraints

- Filesystem is **read-only** (writable: `/tmp` 512MB, `/app/logs` 64MB)
- 2GB RAM, 2 CPU cores, 768 max PIDs
- All Linux capabilities dropped
- No direct internet — LLM calls go through the gateway only
- Cost limit per task (default $10)
- Max 30 steps per task evaluation

## Pre-installed Packages

The sandbox base image includes: `fastapi`, `uvicorn`, `httpx`, `pydantic`, `openai`, `beautifulsoup4`, `lxml`, `tenacity`, `requests`, `aiohttp`, `rapidfuzz`, `pillow`, `rich`, `orjson`, `jsonschema`, `markdownify`, `readability-lxml`, `tldextract`.

Only add packages NOT in this list to `requirements.txt`.

## Local Testing

```bash
# Create venv and install deps
python3 -m venv .venv
.venv/bin/pip install beautifulsoup4 lxml markdownify readability-lxml httpx fastapi uvicorn

# Verify imports
.venv/bin/python3 -c "from main import app; print('OK')"

# Run locally (won't have LLM access without the gateway)
.venv/bin/uvicorn main:app --port 8000

# Test health endpoint
curl http://localhost:8000/health
```

## Miner Setup

1. Push this repo to GitHub
2. Configure your miner `.env`:
   ```
   AGENT_NAME="My Web Agent"
   GITHUB_URL="https://github.com/youruser/your-agent/tree/main"
   AGENT_IMAGE="https://example.com/logo.png"
   ```
3. Run the miner neuron:
   ```bash
   python neurons/miner.py --netuid 36 --subtensor.network finney \
     --wallet.name <coldkey> --wallet.hotkey <hotkey> --axon.port 8091
   ```

The miner neuron handles the Bittensor handshake and points the validator to your `GITHUB_URL`. The validator clones, builds, and evaluates your agent automatically.
