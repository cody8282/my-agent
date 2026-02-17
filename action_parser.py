"""
Parse and validate LLM JSON output, resolving element IDs to actual selectors.

Handles:
- Extracting the action from the thinking+action JSON structure
- Normalizing action type aliases
- Resolving eid references (e1 → actual CSS selector / xpath)
- Validating required fields per action type
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from html_processor import InteractiveElement

logger = logging.getLogger(__name__)

# Normalize various action type names to canonical types
ACTION_TYPE_ALIASES = {
    "input": "fill",
    "enter_text": "fill",
    "enter": "fill",
    "write": "fill",
    "set_value": "fill",
    "type_text": "type",
    "append": "type",
    "go_to": "navigate",
    "goto": "navigate",
    "go": "navigate",
    "open": "navigate",
    "visit": "navigate",
    "press": "click",
    "tap": "click",
    "submit": "click",
    "choose": "select_option",
    "select": "select_option",
    "dropdown": "select_option",
    "scroll_down": "scroll",
    "scroll_up": "scroll",
    "mouse_over": "hover",
    "mouseover": "hover",
    "hover_over": "hover",
    "back": "go_back",
    "go_back": "go_back",
    "navigate_back": "go_back",
    "browser_back": "go_back",
    "forward": "go_forward",
    "go_forward": "go_forward",
    "press_key": "keys",
    "key": "keys",
    "keyboard": "keys",
    "press_enter": "keys",
    "send_keys": "keys",
    "none": "noop",
    "done": "noop",
    "complete": "noop",
    "wait": "noop",
    "no_op": "noop",
    "no_action": "noop",
}

# Required fields per action type
REQUIRED_FIELDS = {
    "click": ["xpath"],
    "fill": ["xpath", "text"],
    "type": ["xpath", "text"],
    "select_option": ["xpath", "text"],
    "navigate": ["url"],
    "scroll": [],
    "hover": ["xpath"],
    "keys": ["keys"],
    "go_back": [],
    "go_forward": [],
    "noop": [],
}


def extract_thinking(content: str) -> str:
    """Extract the 'thinking' field from the LLM's JSON response."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "thinking" in obj:
            return str(obj["thinking"])
    except json.JSONDecodeError:
        pass
    # Try outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict) and "thinking" in obj:
                return str(obj["thinking"])
        except json.JSONDecodeError:
            pass
    return ""


def parse_llm_response(content: str, elements: list[InteractiveElement]) -> Optional[dict]:
    """
    Parse the LLM's JSON response and extract a validated action.

    Args:
        content: Raw LLM output string
        elements: List of interactive elements for eid resolution

    Returns:
        Action dict ready for the evaluator, or None for noop/failure.
    """
    action = _extract_action(content)
    if action is None:
        return None

    # Normalize action type
    action_type = action.get("type", "").lower().strip()
    original_type = action_type
    action_type = ACTION_TYPE_ALIASES.get(action_type, action_type)
    action["type"] = action_type
    action["_original_type"] = original_type

    if action_type == "noop":
        return None

    if action_type not in REQUIRED_FIELDS:
        logger.warning(f"Unknown action type: {action_type}")
        return None

    # Auto-set keys for press_enter alias before validation
    if action_type == "keys" and original_type == "press_enter" and not action.get("keys"):
        action["keys"] = "Enter"

    # Resolve eid references in xpath/selector
    action = _resolve_eids(action, elements)

    # Ensure xpath field exists (prefer xpath over css_selector)
    if "xpath" not in action or not action["xpath"]:
        if "selector" in action:
            action["xpath"] = _css_to_xpath_approx(action["selector"])
        elif "css_selector" in action:
            action["xpath"] = _css_to_xpath_approx(action["css_selector"])
        elif "css" in action:
            action["xpath"] = _css_to_xpath_approx(action["css"])

    # Validate required fields
    required = REQUIRED_FIELDS.get(action_type, [])
    for field in required:
        if not action.get(field):
            logger.warning(f"Missing required field '{field}' for action type '{action_type}'")
            return None

    # Clean action to only include recognized fields
    clean = {"type": action_type}
    if "xpath" in action:
        clean["xpath"] = action["xpath"]
    if "text" in action:
        clean["text"] = str(action["text"])
    if "url" in action:
        clean["url"] = action["url"]
    if action_type == "scroll":
        clean["direction"] = action.get("direction", "down")
    if action_type == "keys":
        keys_val = action.get("keys", "") or action.get("key", "") or action.get("text", "")
        # Auto-set "Enter" for press_enter alias when no keys specified
        if not keys_val and action.get("_original_type") == "press_enter":
            keys_val = "Enter"
        clean["keys"] = str(keys_val)
    if action_type in ("go_back", "go_forward"):
        clean[action_type] = True

    return clean


def _extract_action(content: str) -> Optional[dict]:
    """Extract the action dict from LLM output."""
    text = content.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Try parsing as JSON first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            # If it has an "action" key, extract it
            if "action" in obj and isinstance(obj["action"], dict):
                return obj["action"]
            # If it has "type" directly, it's the action itself
            if "type" in obj:
                return obj
    except json.JSONDecodeError:
        pass

    # Try to find JSON in the text
    # Look for {"action": ...} or {"type": ...} patterns
    for pattern in [r'\{[^{}]*"action"\s*:\s*\{[^{}]*\}[^{}]*\}', r'\{[^{}]*"type"\s*:[^{}]*\}']:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict):
                    if "action" in obj and isinstance(obj["action"], dict):
                        return obj["action"]
                    if "type" in obj:
                        return obj
            except json.JSONDecodeError:
                continue

    # More aggressive: find outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                if "action" in obj and isinstance(obj["action"], dict):
                    return obj["action"]
                if "type" in obj:
                    return obj
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not extract action from LLM output: {text[:200]}")
    return None


def _resolve_eids(action: dict, elements: list[InteractiveElement]) -> dict:
    """Resolve eid references (e.g., 'e1', '#e1') to actual xpaths."""
    eid_map = {e.eid: e for e in elements}

    for field in ["xpath", "selector", "css_selector", "target", "element"]:
        val = action.get(field, "")
        if not val:
            continue

        val = str(val).strip()

        # Check if value is an eid reference
        eid_match = re.match(r"^#?e(\d+)$", val, re.IGNORECASE)
        if eid_match:
            eid = f"e{eid_match.group(1)}"
            if eid in eid_map:
                elem = eid_map[eid]
                action["xpath"] = elem.xpath
                action["_resolved_from"] = eid
                break

    # Also check if eid appears in xpath string like "//e1" or "[e1]"
    xpath = action.get("xpath", "")
    if xpath:
        eid_in_xpath = re.search(r"\be(\d+)\b", xpath)
        if eid_in_xpath and not xpath.startswith("//"):
            eid = f"e{eid_in_xpath.group(1)}"
            if eid in eid_map:
                action["xpath"] = eid_map[eid].xpath

    return action


def _css_to_xpath_approx(css: str) -> str:
    """Approximate conversion of simple CSS selectors to XPath."""
    css = css.strip()
    if not css:
        return ""

    # Already an xpath
    if css.startswith("//") or css.startswith("/"):
        return css

    # ID selector: #foo
    if css.startswith("#"):
        return f'//*[@id="{css[1:]}"]'

    # Attribute selector: tag[attr="val"]
    attr_match = re.match(r'^(\w+)?\[(\w[\w-]*)="([^"]+)"\]$', css)
    if attr_match:
        tag = attr_match.group(1) or "*"
        attr = attr_match.group(2)
        val = attr_match.group(3)
        return f'//{tag}[@{attr}="{val}"]'

    # Class selector: tag.class
    class_match = re.match(r'^(\w+)\.(.+)$', css)
    if class_match:
        tag = class_match.group(1)
        cls = class_match.group(2).replace(".", " ")
        return f'//{tag}[contains(@class, "{cls}")]'

    # Simple tag
    if re.match(r'^\w+$', css):
        return f"//{css}"

    return css
