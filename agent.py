"""
SOTA Web Agent — Orchestrator module.

Coordinates HTML processing, task analysis, planning, LLM calls,
action parsing, and retry logic for each step of a web task.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from action_parser import extract_plan, extract_thinking, parse_llm_response
from html_processor import InteractiveElement, compute_element_diff, elements_to_prompt, process_html
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
        self._prev_elements: list[InteractiveElement] = []
        self._reasoning_memory: list[str] = []
        self._task_plan: list[str] = []
        self._plan_step: int = 0

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

        # Reset state on new task
        if task_id != self._current_task_id:
            self._current_task_id = task_id
            self.planner.reset()
            self._task_analysis = None
            self._prev_elements = []
            self._reasoning_memory = []
            self._task_plan = []
            self._plan_step = 0

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

        # 2b. Compute DOM diff from previous step
        dom_diff = compute_element_diff(self._prev_elements, elements)
        self._prev_elements = elements

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

        self.planner.update(last_action, history, url)
        planning_context = self.planner.get_context_for_prompt()

        # 5. Build prompt
        success_criteria = analysis_to_prompt(self._task_analysis)
        history_text = format_history(history)

        instruction = self._task_analysis.instruction
        memory_text = self._build_memory_text()
        plan_text = self._build_plan_text(step_index)
        user_prompt = build_user_prompt(
            instruction=instruction,
            current_url=url,
            step_index=step_index,
            history_text=history_text,
            success_criteria=success_criteria,
            elements_text=elements_text,
            page_summary=page_summary,
            planning_context=planning_context,
            dom_diff=dom_diff,
            memory_text=memory_text,
            form_warnings=self._check_form_completeness(elements),
            plan_text=plan_text,
        )

        # 6. Call LLM with fallback chain
        action, thinking, raw_content = await self._call_llm_with_fallback(
            task_id=task_id,
            user_prompt=user_prompt,
            elements=elements,
        )

        # 7. Parse plan on step 0, advance plan on later steps
        if step_index == 0 and raw_content and not self._task_plan:
            plan_steps = extract_plan(raw_content)
            if plan_steps:
                self._task_plan = plan_steps
                logger.info(f"Task plan extracted: {len(plan_steps)} steps")
        elif self._task_plan and action:
            # Advance plan step when an action is taken
            if self._plan_step < len(self._task_plan):
                self._plan_step += 1

        # 8. Store reasoning for memory
        if thinking:
            self._store_reasoning(step_index, thinking, action)

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

        # Check URL targets using path-segment matching to avoid false positives
        # e.g. "/cart" should not match "/discart"
        if analysis.url_targets:
            from urllib.parse import urlparse
            parsed = urlparse(current_url)
            url_path = parsed.path.rstrip("/")
            url_matched = any(
                url_path == target.rstrip("/")
                or url_path.endswith("/" + target.lstrip("/").rstrip("/"))
                or target in current_url  # fallback for full URL targets
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

    def _build_plan_text(self, step_index: int) -> str:
        """Build the task plan section for the prompt.

        On step 0 (no plan yet), returns instructions to create a plan.
        On later steps with a plan, returns plan progress with checkmarks.
        """
        if not self._task_plan:
            if step_index == 0:
                return (
                    "## Task Planning\n"
                    "This is the first step. Before acting, create a step-by-step plan to complete the task.\n"
                    "Include a \"plan\" field in your JSON output as a list of short step descriptions.\n"
                    "Example: \"plan\": [\"Navigate to login page\", \"Fill email field\", \"Fill password field\", \"Click submit\"]\n"
                    "Then execute the first step of your plan in the \"action\" field."
                )
            return ""

        lines = ["## Task Plan"]
        for i, step in enumerate(self._task_plan):
            if i < self._plan_step:
                lines.append(f"  [x] {i + 1}. {step}")
            elif i == self._plan_step:
                lines.append(f"  --> {i + 1}. {step}  (CURRENT)")
            else:
                lines.append(f"  [ ] {i + 1}. {step}")

        if self._plan_step >= len(self._task_plan):
            lines.append("\nAll planned steps completed. Verify the task is done or add more steps if needed.")

        return "\n".join(lines)

    def _store_reasoning(self, step_index: int, thinking: str, action: Optional[dict]):
        """Store LLM reasoning for rolling memory."""
        action_desc = ""
        if action:
            action_desc = f" -> {action.get('type', '?')}"
            if action.get("text"):
                action_desc += f' "{action["text"][:25]}"'
        entry = f"Step {step_index}: {thinking[:150]}{action_desc}"
        self._reasoning_memory.append(entry)

    def _build_memory_text(self) -> str:
        """Build the Agent Memory section for the prompt."""
        if not self._reasoning_memory:
            return ""

        # Keep last 5 entries in full detail
        FULL_DETAIL_COUNT = 5
        # Compress older entries to one-liners
        MAX_COMPRESSED = 10

        lines = ["## Agent Memory"]

        if len(self._reasoning_memory) > FULL_DETAIL_COUNT:
            compressed = self._reasoning_memory[:-FULL_DETAIL_COUNT]
            # Only keep the most recent compressed entries
            compressed = compressed[-MAX_COMPRESSED:]
            lines.append("Previous steps (summary):")
            for entry in compressed:
                # Truncate to one line
                short = entry[:100]
                if len(entry) > 100:
                    short += "..."
                lines.append(f"  - {short}")

        recent = self._reasoning_memory[-FULL_DETAIL_COUNT:]
        if recent:
            lines.append("Recent reasoning:")
            for entry in recent:
                lines.append(f"  - {entry}")

        return "\n".join(lines)

    def _check_form_completeness(self, elements: list[InteractiveElement]) -> str:
        """Check for required form fields that are still empty.

        Only shows form status when the page has at least 2 form input elements,
        to avoid noise on non-form pages (e.g. search bars, login links).
        """
        form_inputs = [
            e for e in elements
            if not e.is_hidden
            and e.tag in ("input", "textarea", "select")
            and e.type not in ("hidden", "submit", "button", "file")
        ]

        # Skip form analysis if fewer than 2 visible form inputs
        if len(form_inputs) < 2:
            return ""

        empty_required: list[str] = []
        empty_optional: list[str] = []

        for e in form_inputs:
            label = e.placeholder or e.name or e.aria_label or e.id or e.type
            if not label:
                continue

            has_value = bool(e.value)
            if e.is_required and not has_value:
                empty_required.append(f"{label} [{e.eid}] [required]")
            elif not has_value and e.tag in ("input", "textarea") and e.type not in ("checkbox", "radio"):
                empty_optional.append(f"{label} [{e.eid}]")

        if not empty_required and not empty_optional:
            return ""

        lines = ["## Form Status"]
        if empty_required:
            lines.append("REQUIRED fields still empty (MUST fill before submitting):")
            for field in empty_required[:10]:
                lines.append(f"  * {field}")
        if empty_optional and len(empty_optional) <= 6:
            lines.append("Other empty fields:")
            for field in empty_optional[:6]:
                lines.append(f"  - {field}")
        return "\n".join(lines)

    async def _call_llm_with_fallback(
        self,
        task_id: str,
        user_prompt: str,
        elements: list[InteractiveElement],
    ) -> tuple[Optional[dict], str, str]:
        """Call LLM with model fallback chain and retry logic. Returns (action, thinking, raw_content)."""
        models = [self.model] + [m for m in MODEL_CHAIN if m != self.model]

        for model in models:
            for attempt in range(MAX_RETRIES_PER_STEP):
                try:
                    content = await self._call_llm(task_id, model, user_prompt)
                    if content:
                        thinking = extract_thinking(content)
                        action = parse_llm_response(content, elements)
                        return action, thinking, content
                except httpx.TimeoutException:
                    logger.warning(f"LLM timeout: model={model}, attempt={attempt + 1}")
                    continue
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    logger.warning(f"LLM HTTP error: model={model}, status={status}, attempt={attempt + 1}")
                    if status == 402:
                        logger.error("Cost limit reached!")
                        return None, "", ""
                    if status in (400, 422):
                        break
                    continue
                except Exception as e:
                    logger.warning(f"LLM error: model={model}, attempt={attempt + 1}: {e}")
                    continue

        logger.error("All LLM calls failed")
        return None, "", ""

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
