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
from typing import Optional

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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

agent = WebAgent(openai_base_url=OPENAI_BASE_URL, model=MODEL, api_key=OPENAI_API_KEY)


_last_seen_base_url: str = ""


def _fix_navigate_url(url: str) -> str:
    """Fix URLs where the LLM dropped the port number.

    The LLM sometimes outputs http://localhost/path instead of http://localhost:8000/path.
    We remember the base URL from the last request and fix it.
    """
    if not url or not _last_seen_base_url:
        return url
    from urllib.parse import urlparse

    parsed = urlparse(url)
    base_parsed = urlparse(_last_seen_base_url)
    # Only fix if same hostname and agent dropped the port
    if parsed.hostname == base_parsed.hostname and not parsed.port and base_parsed.port:
        return url.replace(f"{parsed.scheme}://{parsed.hostname}", f"{parsed.scheme}://{parsed.hostname}:{base_parsed.port}", 1)
    return url


def _to_iwa_action(action: dict) -> Optional[dict]:
    """Convert internal action format to IWA BaseAction format.

    Internal: {"type": "click", "xpath": "//...", "text": "..."}
    IWA:      {"type": "ClickAction", "selector": {"type": "xpathSelector", "value": "//..."}}
    """
    action_type = action.get("type", "")
    xpath = action.get("xpath", "")

    def _make_selector(xp: str) -> dict:
        return {"type": "xpathSelector", "value": xp}

    if action_type == "click":
        if xpath:
            return {"type": "ClickAction", "selector": _make_selector(xpath)}
        return None

    if action_type in ("fill", "type"):
        text = action.get("text", "")
        result = {"type": "TypeAction", "text": text}
        if xpath:
            result["selector"] = _make_selector(xpath)
        return result

    if action_type == "navigate":
        url = _fix_navigate_url(action.get("url", ""))
        return {"type": "NavigateAction", "url": url}

    if action_type == "go_back":
        return {"type": "NavigateAction", "go_back": True}

    if action_type == "go_forward":
        return {"type": "NavigateAction", "go_forward": True}

    if action_type == "scroll":
        direction = action.get("direction", "down")
        result: dict = {"type": "ScrollAction"}
        if direction == "up":
            result["up"] = True
        else:
            result["down"] = True
        return result

    if action_type == "hover":
        if xpath:
            return {"type": "HoverAction", "selector": _make_selector(xpath)}
        return None

    if action_type == "keys":
        keys = action.get("keys", "")
        return {"type": "SendKeysIWAAction", "keys": keys}

    if action_type == "select_option":
        text = action.get("text", "")
        result = {"type": "SelectAction", "value": text}
        if xpath:
            result["selector"] = _make_selector(xpath)
        return result

    logger.warning(f"Unknown action type for IWA conversion: {action_type}")
    return None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/act")
async def act(request: Request):
    """
    Receive a task + browser snapshot, return the next action.

    Supports two request formats:
    1. Validator format: {"task": {...}, "snapshot_html": "...", "url": "...", ...}
    2. Benchmark format: {"task_id": "...", "prompt": "...", "snapshot_html": "...", "url": "...", ...}

    Returns {"actions": [...]} with IWA-format action dicts.
    """
    body = await request.json()

    # Support both validator format (nested task) and benchmark format (flat fields)
    if "task" in body and isinstance(body["task"], dict):
        task = body["task"]
    else:
        task = {
            "id": body.get("task_id", ""),
            "prompt": body.get("prompt", ""),
            "instruction": body.get("prompt", ""),
            "url": body.get("url", ""),
            "web_project_id": body.get("web_project_id", ""),
        }

    snapshot_html = body.get("snapshot_html", "")
    url = body.get("url", "")
    step_index = body.get("step_index", 0)
    history = body.get("history", [])

    # Remember base URL for navigate URL fixing
    global _last_seen_base_url
    if url:
        _last_seen_base_url = url

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
        return {"actions": []}

    if action:
        iwa_action = _to_iwa_action(action)
        if iwa_action:
            return {"actions": [iwa_action]}
    return {"actions": []}
