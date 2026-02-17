# Autoppia Subnet 36 — Miner Agent Template

A minimal template for building a web agent for [Autoppia Web Agents Subnet 36](https://github.com/autoppia/autoppia_web_agents_subnet).

## How It Works

The validator clones this repo, builds it inside a hardened Docker sandbox, and evaluates it against web tasks. Your agent receives a task instruction + browser page HTML, and must return browser actions (click, fill, navigate, etc.) to complete the task.

## Repo Structure

```
main.py            # FastAPI app — entrypoint (validator runs: uvicorn main:app)
agent.py           # Agent logic — LLM-based action decision
requirements.txt   # Extra deps only (base image has many pre-installed)
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
        "url": "https://demo-store.com/..."
    },
    "snapshot_html": "<html>...</html>",
    "url": "https://demo-store.com/shoes",
    "step_index": 0,
    "history": []
}
```

**Response:** JSON list of action objects. The validator executes only the first one.
```json
[{"type": "click", "xpath": "//button[contains(text(), 'Add to Cart')]"}]
```

Return `[]` to do nothing (NOOP).

## Action Types

| Type | Fields | Example |
|------|--------|---------|
| `click` | `xpath` | `{"type": "click", "xpath": "//button[@id='buy']"}` |
| `fill` | `xpath`, `text` | `{"type": "fill", "xpath": "//input[@name='email']", "text": "a@b.com"}` |
| `type` | `xpath`, `text` | `{"type": "type", "xpath": "//input[@name='q']", "text": "shoes"}` |
| `select_option` | `xpath`, `text` | `{"type": "select_option", "xpath": "//select[@name='size']", "text": "L"}` |
| `navigate` | `url` | `{"type": "navigate", "url": "https://example.com/cart"}` |

## LLM Access

Your agent accesses LLMs through the sandbox gateway (no direct internet). Environment variables are injected at runtime:

| Variable | Value |
|----------|-------|
| `OPENAI_BASE_URL` | `http://sandbox-gateway:9000/openai/v1` |
| `CHUTES_BASE_URL` | `http://sandbox-gateway:9000/chutes/v1` |
| `SANDBOX_AGENT_UID` | Your miner UID |

Every LLM request **must** include the `iwa-task-id` header (from `task["id"]`), or the gateway rejects it.

## Sandbox Constraints

- Filesystem is **read-only** (writable: `/tmp` 512MB, `/app/logs` 64MB)
- 2GB RAM, 2 CPU cores, 768 max PIDs
- All Linux capabilities dropped
- No direct internet — LLM calls go through the gateway only
- Cost limit per task (default $10)

## Local Testing with Benchmark

The official benchmark framework (`autoppia_iwa`) uses a **different interface** (`/solve_task` with Flask) than the production sandbox (`/act` with FastAPI). This template targets the **production sandbox** — the interface that validators actually use for scoring on mainnet.

To test locally with the benchmark, see the [Benchmark Guide](https://github.com/autoppia/autoppia_web_agents_subnet/blob/opensource/docs/advanced/benchmark-README.md) and adapt your agent to expose a `/solve_task` endpoint alongside `/act`, or create a separate benchmark-specific wrapper.

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
