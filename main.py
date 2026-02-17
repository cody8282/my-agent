"""
Autoppia Subnet 36 — Miner Agent Template

This is the entrypoint for the sandboxed agent container.
The validator runs: uvicorn main:app --host 0.0.0.0 --port ${SANDBOX_AGENT_PORT}

Required endpoints:
  GET  /health  — return 200 when ready (polled for ~20s after container start)
  POST /act     — receive task + browser snapshot, return action(s)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request

from agent import WebAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Miner Web Agent")

# The sandbox gateway proxies LLM calls and tracks cost per task.
# These env vars are injected by the validator's SandboxManager.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://sandbox-gateway:9000/openai/v1")
CHUTES_BASE_URL = os.getenv("CHUTES_BASE_URL", "http://sandbox-gateway:9000/chutes/v1")
AGENT_UID = os.getenv("SANDBOX_AGENT_UID", "0")

agent = WebAgent(openai_base_url=OPENAI_BASE_URL)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/act")
async def act(request: Request):
    """
    Receive a task + browser snapshot from the validator's evaluator.

    Request body:
    {
        "task": {
            "id": "task_abc123",
            "instruction": "Add the red shoes to your cart",
            "url": "https://demo-store.com/...",
            ...
        },
        "snapshot_html": "<html>...</html>",   # sanitized current page DOM
        "url": "https://demo-store.com/shoes", # current browser URL
        "step_index": 0,                       # 0-based step counter
        "history": [                           # previous steps (empty on first call)
            {
                "step": 0,
                "action": "click",
                "candidate_id": null,
                "text": null,
                "exec_ok": true,
                "error": null
            }
        ]
    }

    Must return a JSON list of action dicts. The validator executes only the first one.
    Return [] to do nothing (NOOP).
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
        logger.exception("Agent decision failed")
        return []

    if action:
        return [action]
    return []
