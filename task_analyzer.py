"""
Task test analysis — extract success criteria from task tests to guide the agent.

Parses the task's `tests` array to identify what the evaluator will check,
then translates these into human-readable sub-goals for the LLM prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TaskAnalysis:
    """Structured analysis of what the task requires."""
    task_type: str = "general"  # form_fill, navigation, search, cart, login, multi_step
    url_targets: list[str] = field(default_factory=list)  # URLs we need to reach
    required_text: list[str] = field(default_factory=list)  # Text that must appear
    required_elements: list[str] = field(default_factory=list)  # Elements that must exist
    completion_hints: list[str] = field(default_factory=list)  # Human-readable goals
    instruction: str = ""


def _extract_from_test(test: Any) -> dict[str, list[str]]:
    """Extract criteria from a single test object (dict or object)."""
    result: dict[str, list[str]] = {
        "url_targets": [],
        "required_text": [],
        "required_elements": [],
        "hints": [],
    }

    # Handle both dict and object access
    def _get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    test_type = _get(test, "type", "") or _get(test, "test_type", "") or ""
    test_type = str(test_type).lower()

    # URL matching tests
    if "url" in test_type:
        url = _get(test, "url", "") or _get(test, "expected_url", "") or _get(test, "value", "")
        if url:
            result["url_targets"].append(str(url))
            result["hints"].append(f"Navigate to URL matching: {url}")

    # Text content tests
    if "text" in test_type or "content" in test_type:
        text = _get(test, "text", "") or _get(test, "expected_text", "") or _get(test, "value", "")
        if text:
            result["required_text"].append(str(text))
            result["hints"].append(f"Page should contain text: {text}")

    # Element existence tests
    if "element" in test_type or "selector" in test_type:
        selector = _get(test, "selector", "") or _get(test, "css_selector", "") or _get(test, "xpath", "") or _get(test, "value", "")
        if selector:
            result["required_elements"].append(str(selector))
            result["hints"].append(f"Element should exist: {selector}")

    # Generic value/condition tests
    if not any(result.values()):
        # Try to extract anything useful
        for key in ["description", "name", "condition", "check"]:
            val = _get(test, key, "")
            if val:
                result["hints"].append(f"Test condition: {val}")
                break

        # Check for nested fields
        config = _get(test, "config", {}) or _get(test, "params", {}) or {}
        if isinstance(config, dict):
            for k, v in config.items():
                if "url" in k.lower() and v:
                    result["url_targets"].append(str(v))
                    result["hints"].append(f"Target URL: {v}")
                elif "text" in k.lower() and v:
                    result["required_text"].append(str(v))
                    result["hints"].append(f"Expected text: {v}")
                elif "selector" in k.lower() and v:
                    result["required_elements"].append(str(v))

    return result


def _infer_task_type(instruction: str) -> str:
    """Infer the task type from the instruction text."""
    instruction_lower = instruction.lower()

    patterns = {
        "login": r"\b(log\s*in|sign\s*in|authenticate)\b",
        "form_fill": r"\b(fill|enter|type|input|form|register|sign\s*up|create\s*account)\b",
        "search": r"\b(search|find|look\s*for|query)\b",
        "cart": r"\b(cart|add\s*to\s*cart|basket|buy|purchase|checkout|order)\b",
        "navigation": r"\b(navigate|go\s*to|visit|open|click\s*on|select)\b",
    }

    for task_type, pattern in patterns.items():
        if re.search(pattern, instruction_lower):
            return task_type

    return "multi_step"


def analyze_task(task: dict[str, Any]) -> TaskAnalysis:
    """
    Analyze a task to extract success criteria and infer type.

    Args:
        task: Task dict with 'instruction'/'prompt', 'tests', 'url' etc.

    Returns:
        TaskAnalysis with extracted criteria and hints.
    """
    instruction = task.get("instruction", "") or task.get("prompt", "") or task.get("objective", "")
    tests = task.get("tests", []) or []
    task_url = task.get("url", "")

    analysis = TaskAnalysis(instruction=instruction)
    analysis.task_type = _infer_task_type(instruction)

    for test in tests:
        try:
            extracted = _extract_from_test(test)
            analysis.url_targets.extend(extracted["url_targets"])
            analysis.required_text.extend(extracted["required_text"])
            analysis.required_elements.extend(extracted["required_elements"])
            analysis.completion_hints.extend(extracted["hints"])
        except Exception as e:
            logger.debug(f"Failed to extract from test: {e}")
            continue

    # Add instruction-derived hints
    if not analysis.completion_hints:
        analysis.completion_hints.append(f"Complete the task: {instruction}")

    # Deduplicate
    analysis.url_targets = list(dict.fromkeys(analysis.url_targets))
    analysis.required_text = list(dict.fromkeys(analysis.required_text))
    analysis.required_elements = list(dict.fromkeys(analysis.required_elements))
    analysis.completion_hints = list(dict.fromkeys(analysis.completion_hints))

    return analysis


def analysis_to_prompt(analysis: TaskAnalysis) -> str:
    """Format task analysis as a prompt section for the LLM."""
    lines = ["## Success Criteria"]

    if analysis.completion_hints:
        for hint in analysis.completion_hints:
            lines.append(f"- {hint}")

    if analysis.url_targets:
        lines.append("\nTarget URLs:")
        for url in analysis.url_targets:
            lines.append(f"  - {url}")

    if analysis.required_text:
        lines.append("\nRequired text on page:")
        for text in analysis.required_text:
            lines.append(f'  - "{text}"')

    if analysis.required_elements:
        lines.append("\nRequired elements:")
        for elem in analysis.required_elements:
            lines.append(f"  - {elem}")

    lines.append(f"\nInferred task type: {analysis.task_type}")

    return "\n".join(lines)
