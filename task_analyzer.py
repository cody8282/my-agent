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

# Map task types to DOM extraction modes
_EXTRACTION_MODE_MAP = {
    "form_fill": "input_fields",
    "login": "input_fields",
    "navigation": "links_only",
}

# Exact event name → actionable LLM guidance
_EVENT_ACTION_MAP: dict[str, str] = {
    # Auth
    "LOGIN": "Log in with the provided credentials",
    "LOGIN_BOOK": "Log in with the provided credentials",
    "REGISTRATION": "Register a new account with the provided details",
    "REGISTRATION_BOOK": "Register a new account with the provided details",
    "LOGOUT": "Log out of the current session",
    "LOGOUT_BOOK": "Log out of the current session",
    # Email
    "SEND_EMAIL": "Compose and send an email",
    "REPLY_EMAIL": "Reply to the specified email",
    "FORWARD_EMAIL": "Forward the specified email",
    "VIEW_EMAIL": "Open and view the specified email",
    "STAR_AN_EMAIL": "Star/unstar the specified email",
    "DELETE_EMAIL": "Delete the specified email",
    "ARCHIVE_EMAIL": "Archive the specified email",
    "SEARCH_EMAIL": "Search for the specified email",
    "MARK_AS_SPAM": "Mark the specified email as spam",
    "MARK_AS_UNREAD": "Mark the specified email as unread",
    "MARK_EMAIL_AS_IMPORTANT": "Mark the specified email as important",
    "ADD_LABEL": "Add a label to the specified email",
    # Contact / form
    "CONTACT": "Fill out and submit the contact form",
    "CONTACT_FORM_SUBMIT": "Fill out and submit the contact form",
    "CONTACT_DOCTOR": "Contact the specified doctor",
    # Cart / purchase
    "ADD_TO_CART": "Add the specified item to the cart",
    "ADD_TO_CART_BOOK": "Add the specified book to the cart",
    "ADD_TO_CART_MENU_ITEM": "Add the specified menu item to the cart",
    "PLACE_ORDER": "Place an order",
    "ORDER_COMPLETED": "Complete the order",
    "PROCEED_TO_CHECKOUT": "Proceed to checkout",
    "CHECKOUT_STARTED": "Start the checkout process",
    "CONFIRM_AND_PAY": "Confirm and complete payment",
    "PURCHASE_BOOK": "Purchase the specified book",
    # Review
    "SUBMIT_REVIEW": "Write and submit a review",
    "REVIEW_SUBMITTED": "Write and submit a review",
    # Booking / reservation
    "BOOK_RESTAURANT": "Book a reservation at the specified restaurant",
    "RESERVE_HOTEL": "Reserve the specified hotel",
    "RESERVE_RIDE": "Reserve a ride",
    "RESERVATION_COMPLETE": "Complete the reservation",
    "CANCEL_RESERVATION": "Cancel the specified reservation",
    "APPOINTMENT_BOOKED_SUCCESSFULLY": "Book an appointment",
    # Calendar
    "NEW_CALENDAR_EVENT_ADDED": "Create a new calendar event",
    "ADD_EVENT": "Create a new event",
    # Social
    "LIKE_POST": "Like the specified post",
    "COMMENT_ON_POST": "Comment on the specified post",
    "POST_STATUS": "Post a status update",
    "CONNECT_WITH_USER": "Connect with the specified user",
    # Jobs
    "APPLY_FOR_JOB": "Apply for the specified job",
    "POST_A_JOB": "Post a new job listing",
}

# Prefix-based patterns for the ~285 event names we can't map individually.
# Checked in order; first match wins.
_EVENT_PREFIX_MAP: list[tuple[str, str]] = [
    ("VIEW_", "Navigate to and view the specified item"),
    ("SEARCH_", "Search for the specified item"),
    ("FILTER_", "Apply the specified filter"),
    ("SORT_", "Sort by the specified criteria"),
    ("ADD_TO_CART", "Add the specified item to the cart"),
    ("ADD_TO_", "Add the item to the specified list"),
    ("ADD_", "Add the specified item"),
    ("REMOVE_FROM_", "Remove from the specified list"),
    ("DELETE_", "Delete the specified item"),
    ("EDIT_", "Edit the specified item"),
    ("SHARE_", "Share the specified item"),
    ("SELECT_", "Select the specified option"),
    ("OPEN_", "Open the specified page or form"),
    ("CREATE_", "Create a new item"),
    ("CANCEL_", "Cancel the specified action"),
]

# Human-readable operator labels
_OPERATOR_LABELS: dict[str, str] = {
    "equals": "must be",
    "not_equals": "must NOT be",
    "contains": "must contain",
    "not_contains": "must NOT contain",
    "greater_than": "must be greater than",
    "less_than": "must be less than",
    "greater_equal": "must be at least",
    "less_equal": "must be at most",
    "in_list": "must be one of",
    "not_in_list": "must NOT be one of",
}


@dataclass
class TaskAnalysis:
    """Structured analysis of what the task requires."""
    task_type: str = "general"  # form_fill, navigation, search, cart, login, multi_step
    url_targets: list[str] = field(default_factory=list)  # URLs we need to reach
    required_text: list[str] = field(default_factory=list)  # Text that must appear
    required_elements: list[str] = field(default_factory=list)  # Elements that must exist
    completion_hints: list[str] = field(default_factory=list)  # Human-readable goals
    action_hints: list[str] = field(default_factory=list)  # "Compose and send an email"
    field_hints: list[str] = field(default_factory=list)  # "'subject' must contain: ..."
    instruction: str = ""
    extraction_mode: str = "all_fields"  # input_fields, links_only, all_fields


def _extract_from_test(test: Any) -> dict[str, list[str]]:
    """Extract criteria from a single test object (dict or object)."""
    result: dict[str, list[str]] = {
        "url_targets": [],
        "required_text": [],
        "required_elements": [],
        "hints": [],
        "action_hints": [],   # "Action required: ..." lines
        "field_hints": [],    # "Required field values:" + indented lines
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

    # CheckEventTest — extract event criteria as actionable guidance
    if "event" in test_type or "checkevent" in test_type:
        event_name = _get(test, "event_name", "")
        event_criteria = _get(test, "event_criteria", {}) or {}
        if event_name:
            action_desc = _EVENT_ACTION_MAP.get(event_name)
            if not action_desc:
                # Try prefix-based matching
                for prefix, desc in _EVENT_PREFIX_MAP:
                    if event_name.startswith(prefix):
                        action_desc = desc
                        break
            if action_desc:
                result["action_hints"].append(action_desc)
            else:
                # Final fallback: humanize the event name
                readable = event_name.replace("_", " ").title()
                result["action_hints"].append(readable)
        if isinstance(event_criteria, dict) and event_criteria:
            for field_name, condition in event_criteria.items():
                readable_field = field_name.replace("_", " ")
                if isinstance(condition, dict):
                    op = condition.get("operator", "equals")
                    val = condition.get("value", "")
                    op_label = _OPERATOR_LABELS.get(op, op)
                    if isinstance(val, list):
                        val_str = ", ".join(str(v) for v in val)
                        result["field_hints"].append(f"'{readable_field}' {op_label}: [{val_str}]")
                    else:
                        result["field_hints"].append(f"'{readable_field}' {op_label}: \"{val}\"")
                else:
                    result["field_hints"].append(f"'{readable_field}' must be: \"{condition}\"")

    # Generic value/condition tests
    if not any(result.values()):
        # Try to extract anything useful
        desc = _get(test, "description", "")
        if desc:
            result["hints"].append(f"Test: {desc}")
        for key in ["name", "condition", "check"]:
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
    analysis.extraction_mode = _EXTRACTION_MODE_MAP.get(analysis.task_type, "all_fields")

    for test in tests:
        try:
            extracted = _extract_from_test(test)
            analysis.url_targets.extend(extracted["url_targets"])
            analysis.required_text.extend(extracted["required_text"])
            analysis.required_elements.extend(extracted["required_elements"])
            analysis.completion_hints.extend(extracted["hints"])
            analysis.action_hints.extend(extracted["action_hints"])
            analysis.field_hints.extend(extracted["field_hints"])
        except Exception as e:
            logger.debug(f"Failed to extract from test: {e}")
            continue

    # Add instruction-derived hints
    if not analysis.completion_hints and not analysis.action_hints:
        analysis.completion_hints.append(f"Complete the task: {instruction}")

    # Deduplicate
    analysis.url_targets = list(dict.fromkeys(analysis.url_targets))
    analysis.required_text = list(dict.fromkeys(analysis.required_text))
    analysis.required_elements = list(dict.fromkeys(analysis.required_elements))
    analysis.completion_hints = list(dict.fromkeys(analysis.completion_hints))
    analysis.action_hints = list(dict.fromkeys(analysis.action_hints))
    analysis.field_hints = list(dict.fromkeys(analysis.field_hints))

    return analysis


def analysis_to_prompt(analysis: TaskAnalysis) -> str:
    """Format task analysis as a prompt section for the LLM."""
    lines = ["## Success Criteria"]

    # Action hints first (most important — tells LLM what to DO)
    for hint in analysis.action_hints:
        lines.append(f"- Action required: {hint}")

    # Field requirements grouped together
    if analysis.field_hints:
        lines.append("- Required field values:")
        for hint in analysis.field_hints:
            lines.append(f"  - {hint}")

    # General completion hints
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
