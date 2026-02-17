"""
Web agent logic — decides what browser action to take given a task and page snapshot.

Replace or extend this with your own agent strategy.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Maximum HTML characters to include in the LLM prompt to stay within context limits.
MAX_HTML_CHARS = 30_000

SYSTEM_PROMPT = """\
You are an autonomous web agent. You interact with web pages by outputting a single JSON action per step.

Available action types:
  {"type": "click", "xpath": "//button[@id='submit']"}
  {"type": "fill", "xpath": "//input[@name='email']", "text": "user@test.com"}
  {"type": "type", "xpath": "//input[@name='search']", "text": "laptop"}
  {"type": "select_option", "xpath": "//select[@name='qty']", "text": "2"}
  {"type": "navigate", "url": "https://example.com/products"}

Rules:
- Output ONLY a single JSON object, no markdown fences, no explanation.
- Use precise XPath selectors derived from the provided HTML.
- Use "fill" for input fields (clears existing text first), "type" to append.
- Use "navigate" only when you need to go to a completely different page.
- If the task appears complete or you cannot determine a useful action, output: {"type": "noop"}
"""


def _build_user_prompt(
    task: dict,
    snapshot_html: str,
    url: str,
    step_index: int,
    history: list[dict],
) -> str:
    instruction = task.get("instruction", "") or task.get("prompt", "") or task.get("objective", "")
    truncated_html = snapshot_html[:MAX_HTML_CHARS]

    history_summary = ""
    if history:
        lines = []
        for h in history:
            status = "OK" if h.get("exec_ok", True) else f"FAILED: {h.get('error', '?')}"
            lines.append(f"  step {h.get('step', '?')}: {h.get('action', '?')} -> {status}")
        history_summary = "Previous actions:\n" + "\n".join(lines) + "\n\n"

    return (
        f"Task: {instruction}\n\n"
        f"Current URL: {url}\n"
        f"Step: {step_index}\n\n"
        f"{history_summary}"
        f"Current page HTML:\n{truncated_html}\n\n"
        f"Decide the next action. Output a single JSON object."
    )


def _parse_action(content: str) -> Optional[dict]:
    """Try to extract a JSON action from LLM output, tolerating markdown fences."""
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "type" in obj:
            if obj["type"] == "noop":
                return None
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: find first {...} in the output
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict) and "type" in obj:
                if obj["type"] == "noop":
                    return None
                return obj
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse action from LLM output: %s", text[:200])
    return None


class WebAgent:
    """
    Simple LLM-based web agent.

    Sends the task instruction + page HTML to an OpenAI-compatible chat endpoint
    (via the sandbox gateway) and parses the response into a browser action.
    """

    def __init__(
        self,
        openai_base_url: str,
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
    ):
        self.openai_base_url = openai_base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def decide_action(
        self,
        *,
        task: dict,
        snapshot_html: str,
        url: str,
        step_index: int,
        history: list[dict],
    ) -> Optional[dict]:
        task_id = task.get("id", "")
        user_prompt = _build_user_prompt(task, snapshot_html, url, step_index, history)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.openai_base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    # REQUIRED: the gateway uses this header for cost tracking.
                    # Without it, the request is rejected with 400.
                    "iwa-task-id": task_id,
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                },
            )

        if resp.status_code != 200:
            logger.error("LLM request failed: status=%d body=%s", resp.status_code, resp.text[:300])
            return None

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_action(content)
