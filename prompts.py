"""
All prompt templates for the SOTA web agent.

System prompt defines the agent's capabilities and output format.
User prompt combines task analysis, page state, and planning context.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are an expert autonomous web agent. You interact with web pages by outputting structured JSON actions to complete tasks.

## Output Format
You MUST output a JSON object with exactly this structure:
{
  "thinking": "Your step-by-step reasoning about what to do next",
  "action": {
    "type": "<action_type>",
    ...action-specific fields...
  }
}

## Available Actions

1. **click** — Click an element
   {"type": "click", "xpath": "//button[@id='submit']"}

2. **fill** — Clear an input field and type new text
   {"type": "fill", "xpath": "//input[@name='email']", "text": "user@test.com"}

3. **type** — Append text to a field (does NOT clear first)
   {"type": "type", "xpath": "//input[@name='search']", "text": "laptop"}

4. **select_option** — Select a dropdown option by visible text
   {"type": "select_option", "xpath": "//select[@name='qty']", "text": "2"}

5. **navigate** — Go to a specific URL
   {"type": "navigate", "url": "https://example.com/products"}

6. **scroll** — Scroll the page (use when elements might be below viewport)
   {"type": "scroll", "direction": "down"}

7. **noop** — Do nothing (task appears complete)
   {"type": "noop"}

## Element References
Interactive elements are listed with short IDs like [e1], [e2], etc.
When using an element, use its xpath from the element list.
You can also construct your own xpath if the element you need isn't listed.

## Rules
- Output ONLY valid JSON. No markdown fences, no extra text.
- Use "fill" for inputs (clears existing text first). Use "type" to append.
- Use "navigate" only when you need to go to a completely different URL.
- Always use the most specific xpath available (prefer @id, @name, @aria-label).
- If a form has validation errors visible, fix them before resubmitting.
- If the task goal is already achieved on the current page, output {"type": "noop"} in the action.
- Think step by step in the "thinking" field before deciding your action.
- When filling forms, fill ALL required fields before clicking submit.
"""


def build_user_prompt(
    *,
    instruction: str,
    current_url: str,
    step_index: int,
    history_text: str,
    success_criteria: str,
    elements_text: str,
    page_summary: str,
    planning_context: str,
) -> str:
    """Build the user prompt combining all context."""
    sections = []

    # Task instruction first (most important)
    sections.append(f"## Task\n{instruction}")

    # Success criteria (prime the model with goals)
    if success_criteria:
        sections.append(success_criteria)

    # Planning context (phase, stuck detection)
    if planning_context:
        sections.append(f"## Agent State\n{planning_context}")

    # Current state
    sections.append(f"## Current URL\n{current_url}")
    sections.append(f"## Step\n{step_index} of 30")

    # History
    if history_text:
        sections.append(f"## Action History\n{history_text}")

    # Page content (interactive elements first, then summary)
    sections.append(f"## Page Elements\n{elements_text}")

    if page_summary:
        sections.append(f"## Page Content Summary\n{page_summary}")

    # Final instruction
    sections.append("## Your Turn\nAnalyze the page and decide the next action. Output a JSON object with 'thinking' and 'action' fields.")

    return "\n\n".join(sections)


def format_history(history: list[dict]) -> str:
    """Format action history for the prompt."""
    if not history:
        return ""

    lines = []
    for h in history:
        step = h.get("step", "?")
        action = h.get("action", "?")
        text = h.get("text", "")
        ok = h.get("exec_ok", True)
        error = h.get("error", "")

        status = "✓" if ok else f"✗ ({error})" if error else "✗"
        text_part = f' "{text[:30]}"' if text else ""
        lines.append(f"  Step {step}: {action}{text_part} → {status}")

    return "\n".join(lines)
