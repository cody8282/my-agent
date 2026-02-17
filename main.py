"""
Autoppia Subnet 36 — SOTA Miner Agent

FastAPI entrypoint for the sandboxed agent container.
The validator runs: uvicorn main:app --host 0.0.0.0 --port ${SANDBOX_AGENT_PORT}

Required endpoints:
  GET  /health  — return 200 when ready
  POST /act     — receive task + browser snapshot, return action(s)
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request

from agent import WebAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="SOTA Miner Web Agent")

# Environment variables injected by the validator's SandboxManager
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://sandbox-gateway:9000/openai/v1")
AGENT_UID = os.getenv("SANDBOX_AGENT_UID", "0")

# Use the strongest available model — cost doesn't affect eval score
MODEL = os.getenv("AGENT_MODEL", "gpt-4.1")

agent = WebAgent(openai_base_url=OPENAI_BASE_URL, model=MODEL)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/act")
async def act(request: Request):
    """
    Receive a task + browser snapshot, return the next action.

    Request body:
    {
        "task": {"id": "...", "instruction": "...", "url": "...", "tests": [...], ...},
        "snapshot_html": "<html>...</html>",
        "url": "https://current-page-url",
        "step_index": 0,
        "history": [{"step": 0, "action": "click", "exec_ok": true, ...}]
    }

    Returns a JSON list of action dicts. The validator executes only the first one.
    Return [] for NOOP.
    """
    body = await request.json()

    task = body.get("task", {})
    snapshot_html = body.get("snapshot_html", "")
    url = body.get("url", "")
    step_index = body.get("step_index", 0)
    history = body.get("history", [])

    try:
        action = await agent.decide_action(
            task=task,
            snapshot_html=snapshot_html,
            url=url,
            step_index=step_index,
            history=history,
        )
    except Exception:
        logger.exception("Agent decision failed at step %d", step_index)
        return []

    if action:
        return [action]
    return []
