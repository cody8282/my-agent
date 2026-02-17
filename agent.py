"""
SOTA Web Agent — Orchestrator module.

Coordinates HTML processing, task analysis, planning, LLM calls,
action parsing, and retry logic for each step of a web task.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx

from action_parser import parse_llm_response
from html_processor import InteractiveElement, elements_to_prompt, process_html
from planner import Planner
from prompts import SYSTEM_PROMPT, build_user_prompt, format_history
from task_analyzer import TaskAnalysis, analyze_task, analysis_to_prompt

logger = logging.getLogger(__name__)

# Model fallback chain: strongest → fallback → budget
MODEL_CHAIN = ["gpt-4.1", "gpt-4o", "gpt-4.1-mini"]

# LLM call timeout
LLM_TIMEOUT = 55.0

# Max retries per step (for transient LLM failures)
MAX_RETRIES_PER_STEP = 2


class WebAgent:
    """
    SOTA LLM-based web agent with HTML processing, task analysis,
    planning, and structured output.
    """

    def __init__(
        self,
        openai_base_url: str,
        model: str = "gpt-4.1",
    ):
        self.openai_base_url = openai_base_url.rstrip("/")
        self.model = model
        self.planner = Planner()
        self._task_analysis: Optional[TaskAnalysis] = None
        self._current_task_id: Optional[str] = None

    async def decide_action(
        self,
        *,
        task: dict,
        snapshot_html: str,
        url: str,
        step_index: int,
        history: list[dict],
    ) -> Optional[dict]:
        """
        Main entry point: given task + page state, return the next action.

        Returns an action dict or None for NOOP.
        """
        task_id = task.get("id", "")
        start = time.monotonic()

        # Reset planner on new task
        if task_id != self._current_task_id:
            self._current_task_id = task_id
            self.planner.reset()
            self._task_analysis = None

        # 1. Analyze task (once per task)
        if self._task_analysis is None:
            self._task_analysis = analyze_task(task)
            logger.info(
                f"Task analysis: type={self._task_analysis.task_type}, "
                f"hints={len(self._task_analysis.completion_hints)}, "
                f"url_targets={len(self._task_analysis.url_targets)}"
            )

        # 2. Process HTML → interactive elements + page summary
        elements, page_summary = process_html(snapshot_html)
        elements_text = elements_to_prompt(elements)

        logger.info(
            f"Step {step_index}: {len(elements)} elements, "
            f"page_summary={len(page_summary)} chars, url={url[:80]}"
        )

        # 3. Check early completion (URL-based)
        if self._check_early_completion(url, snapshot_html):
            logger.info(f"Early completion detected at step {step_index}")
            return None

        # 4. Update planner
        last_action = None
        if history:
            last_h = history[-1]
            last_action = {"type": last_h.get("action", "")}
            if last_h.get("text"):
                last_action["text"] = last_h["text"]

        plan_state = self.planner.update(last_action, history, url)
        planning_context = self.planner.get_context_for_prompt()

        # 5. Build prompt
        success_criteria = analysis_to_prompt(self._task_analysis)
        history_text = format_history(history)

        instruction = self._task_analysis.instruction
        user_prompt = build_user_prompt(
            instruction=instruction,
            current_url=url,
            step_index=step_index,
            history_text=history_text,
            success_criteria=success_criteria,
            elements_text=elements_text,
            page_summary=page_summary,
            planning_context=planning_context,
        )

        # 6. Call LLM with fallback chain
        action = await self._call_llm_with_fallback(
            task_id=task_id,
            user_prompt=user_prompt,
            elements=elements,
        )

        elapsed = time.monotonic() - start
        if action:
            logger.info(f"Step {step_index} decided: {action.get('type')} in {elapsed:.1f}s")
        else:
            logger.info(f"Step {step_index} decided: NOOP in {elapsed:.1f}s")

        return action

    def _check_early_completion(self, current_url: str, html: str) -> bool:
        """Check if task appears already complete based on test criteria."""
        if not self._task_analysis:
            return False

        analysis = self._task_analysis

        # Check URL targets
        if analysis.url_targets:
            url_matched = any(
                target in current_url or current_url.endswith(target)
                for target in analysis.url_targets
            )
            if not url_matched:
                return False

        # Check required text
        if analysis.required_text:
            html_lower = html.lower()
            text_matched = all(
                text.lower() in html_lower
                for text in analysis.required_text
            )
            if not text_matched:
                return False

        # Only return True if we had criteria and all matched
        has_criteria = bool(analysis.url_targets or analysis.required_text)
        return has_criteria

    async def _call_llm_with_fallback(
        self,
        task_id: str,
        user_prompt: str,
        elements: list[InteractiveElement],
    ) -> Optional[dict]:
        """Call LLM with model fallback chain and retry logic."""
        models = [self.model] + [m for m in MODEL_CHAIN if m != self.model]

        for model in models:
            for attempt in range(MAX_RETRIES_PER_STEP):
                try:
                    content = await self._call_llm(task_id, model, user_prompt)
                    if content:
                        action = parse_llm_response(content, elements)
                        return action
                except httpx.TimeoutException:
                    logger.warning(f"LLM timeout: model={model}, attempt={attempt + 1}")
                    continue
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    logger.warning(f"LLM HTTP error: model={model}, status={status}, attempt={attempt + 1}")
                    if status == 402:
                        # Cost limit reached, stop trying
                        logger.error("Cost limit reached!")
                        return None
                    if status in (400, 422):
                        # Model not supported, try next
                        break
                    continue
                except Exception as e:
                    logger.warning(f"LLM error: model={model}, attempt={attempt + 1}: {e}")
                    continue

        logger.error("All LLM calls failed")
        return None

    async def _call_llm(self, task_id: str, model: str, user_prompt: str) -> Optional[str]:
        """Make a single LLM API call."""
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{self.openai_base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "iwa-task-id": task_id,
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                },
            )

        if resp.status_code != 200:
            resp.raise_for_status()

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content if content else None
