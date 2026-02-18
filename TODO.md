# TODO — Future Improvements

Techniques borrowed from top web agents (Magnitude, Browser Use/Prune4Web, Agent-E, Agent-Q, WebVoyager) that haven't been implemented yet. All improvements work within the IWA sandbox's HTML-snapshot paradigm (no screenshots).

## 5. Task-Relevant Element Prioritization (Medium effort, Medium impact)

**Source:** Browser Use / Prune4Web

Score elements by relevance to the task instruction using keyword overlap. Use `rapidfuzz` (pre-installed) for fuzzy matching between element text/attributes and task keywords. Put high-relevance elements first in the prompt; drop lowest-scored if over token budget.

**Where:** `html_processor.py` — add `score_elements(elements, instruction)` function

```python
from rapidfuzz import fuzz

def score_elements(elements, instruction):
    """Score each element by relevance to the task instruction."""
    keywords = instruction.lower().split()
    for e in elements:
        searchable = f"{e.text} {e.placeholder} {e.name} {e.aria_label} {e.value}".lower()
        e._relevance = max(fuzz.partial_ratio(kw, searchable) for kw in keywords) if keywords else 50
    return sorted(elements, key=lambda e: e._relevance, reverse=True)
```

## ~~6. Better Visibility Detection~~ (DONE)

Updated `_is_hidden()` in `html_processor.py` to check `opacity:0`, `aria-hidden="true"`, zero width/height, and parent chain `display:none`/`visibility:hidden`.

## ~~7. Explicit Task Decomposition~~ (DONE)

Implemented in `action_parser.py` (`extract_plan`, `_parse_plan_from_text`), `agent.py` (`_task_plan`, `_plan_step`, `_build_plan_text`), and `prompts.py` (`plan_text` parameter).

## ~~8. Self-Verification / State Assertions~~ (DONE)

Implemented in `agent.py` (`_verify_action_result`). After each action, compares new page state against expected outcomes: detects failed navigation, form validation errors, new alert/error elements, and page content changes.

## 9. Action Validation Against Page Elements (Low effort, Low-Medium impact)

**Source:** Magnitude's affordance validation

After parsing the action, verify the xpath matches an element in the extracted elements list. Check action/element compatibility (don't `fill` a button, don't `click` a hidden element). If validation fails, pick closest valid element using `rapidfuzz`.

**Where:** `action_parser.py` — add `validate_action(action, elements)` function

```python
def validate_action(action, elements):
    """Validate that the action target exists and is compatible."""
    action_type = action.get("type")
    xpath = action.get("xpath", "")

    # Find matching element
    match = None
    for e in elements:
        if e.xpath == xpath or e.css_selector == xpath:
            match = e
            break

    if match:
        # Check compatibility
        if action_type == "fill" and match.tag in ("button", "a"):
            return False, f"Cannot fill a {match.tag} element"
        if action_type == "click" and match.is_hidden:
            return False, "Target element is hidden"

    return True, ""
```

## 10. Fallback Action Candidates (Low effort, Low impact)

**Source:** Magnitude's branching strategy

Ask the LLM to output a `confidence` score (0-100) and an optional `fallback_action`. If the primary action fails on the next step, use the fallback without another LLM call.

**Where:** `agent.py` + `action_parser.py`

```python
# Update prompt to request:
# "confidence": 85,
# "fallback_action": {"type": "click", "xpath": "//button[contains(@class, 'submit')]"}

# In agent.py, store fallback:
# self._pending_fallback = parsed_fallback
# On next step, if last action failed and fallback exists, return it immediately
```

## 11. Adaptive DOM Extraction Modes (Medium effort, High impact)

**Source:** Agent-E

Switch element extraction strategy by task type instead of always extracting everything. Reduces token usage and focuses LLM attention on relevant elements.

**Modes:**
- `input_fields` — for form-filling tasks, prioritize `<input>`, `<select>`, `<textarea>`, `<button type=submit>`
- `links_only` — for navigation tasks, prioritize `<a>`, `<nav>` elements, breadcrumbs
- `all_fields` — default/exploration mode, extract everything (current behavior)

**Where:** `html_processor.py` — add `extraction_mode` parameter to `extract_interactive_elements()`

```python
EXTRACTION_MODES = {
    "input_fields": {"input", "select", "textarea", "button"},
    "links_only": {"a", "nav", "button"},
    "all_fields": None,  # None = no filter
}

def extract_interactive_elements(html, mode="all_fields"):
    allowed_tags = EXTRACTION_MODES.get(mode)
    elements = _extract_all(html)
    if allowed_tags:
        elements = [e for e in elements if e.tag in allowed_tags]
    return elements
```

**Detection:** Classify task instruction with keyword heuristics (e.g., "fill", "enter", "type" → `input_fields`; "navigate", "go to", "find page" → `links_only`).

## 12. Multi-Turn LLM Context (Low effort, High impact)

**Source:** Agent-Q / general agent best practice

Currently each step sends a single system+user message pair. The LLM has no memory of its own previous reasoning. Send the last 2-3 assistant+user turns as conversation history so the LLM sees its own prior actions and reasoning, reducing contradictory or repeated actions.

**Where:** `agent.py` — modify `_call_llm()` to maintain a rolling message window

```python
# Maintain self._message_history = [] (list of {"role": ..., "content": ...})
# Each step: append user message, call LLM, append assistant response
# Keep only last N turns (e.g., 3) to stay within token budget
# Pass full history to LLM instead of single message
```

## 13. Dynamic Token Budgeting (Medium effort, Medium impact)

**Source:** Agent-E / WebVoyager

Scale `MAX_ELEMENTS` and `MAX_CONTENT_CHARS` based on page complexity instead of using fixed limits (currently 150/12000). Simple pages with few elements should expand the page summary; complex pages with many elements should compress the summary and prioritize top elements.

**Where:** `html_processor.py` + `agent.py`

```python
def compute_budget(total_elements, total_chars):
    """Dynamically allocate token budget between summary and elements."""
    if total_elements < 30:
        # Simple page — give more room to page summary
        return {"max_elements": total_elements, "max_summary_chars": 16000}
    elif total_elements > 200:
        # Complex page — compress everything
        return {"max_elements": 80, "max_summary_chars": 6000}
    else:
        # Default
        return {"max_elements": 150, "max_summary_chars": 12000}
```

## 14. XPath Repair with Fuzzy Matching (Low effort, Medium impact)

**Source:** Magnitude / SeeAct

When the parsed action xpath doesn't match any extracted element, use `rapidfuzz` to find the closest match by element text/name/aria-label. Prevents wasted steps from LLM xpath hallucination (a common failure mode).

**Where:** `action_parser.py` — add `repair_xpath(action, elements)` function

```python
from rapidfuzz import fuzz, process

def repair_xpath(action, elements):
    """If xpath doesn't match any element, find closest by text similarity."""
    xpath = action.get("xpath", "")
    if any(e.xpath == xpath for e in elements):
        return action  # exact match, no repair needed

    # Extract target hint from xpath (e.g., text content, @name value)
    target_text = _extract_text_from_xpath(xpath)
    if not target_text:
        return action

    # Fuzzy match against element descriptions
    candidates = {i: f"{e.text} {e.name} {e.aria_label}" for i, e in enumerate(elements)}
    best_idx, score, _ = process.extractOne(target_text, candidates, scorer=fuzz.partial_ratio)
    if score > 70:
        action["xpath"] = elements[best_idx].xpath
    return action
```

## 15. Smart Scroll Strategy (Medium effort, Medium impact)

**Source:** WebVoyager

Current scrolling is blind — the agent doesn't track what it has already seen. Track which elements have appeared in previous snapshots. When the agent is stuck, suggest scrolling to unexplored page regions. Detect infinite-scroll pages vs. fixed-length pages.

**Where:** `agent.py` — add `_scroll_tracker` state

```python
# Track seen element xpaths across steps
self._seen_elements = set()

def _suggest_scroll(self, current_elements):
    new_elements = [e for e in current_elements if e.xpath not in self._seen_elements]
    self._seen_elements.update(e.xpath for e in current_elements)

    if len(new_elements) == 0 and self._last_action == "scroll_down":
        return "bottom_reached"  # stop scrolling
    return None
```

## 16. Structured Output / JSON Mode (Low effort, Medium impact)

**Source:** General best practice

Use OpenAI `response_format: { "type": "json_object" }` to guarantee valid JSON output from the LLM, eliminating JSON parse failures. Currently relies on regex extraction from free-text responses, which occasionally fails.

**Where:** `agent.py` — modify `_call_llm()` API call

```python
response = client.chat.completions.create(
    model=self.model,
    messages=messages,
    response_format={"type": "json_object"},
    # ...
)
```

**Note:** Requires updating the system prompt to explicitly request JSON output format and testing with the specific model endpoint being used (not all OpenAI-compatible APIs support this).

---

## Implementation Priority

| # | Item | Effort | Impact | Status |
|---|------|--------|--------|--------|
| 6 | ~~Visibility detection~~ | ~~Low~~ | ~~Medium~~ | **DONE** |
| 7 | ~~Task decomposition~~ | ~~Low~~ | ~~Medium~~ | **DONE** |
| 8 | ~~Self-verification~~ | ~~Medium~~ | ~~Medium~~ | **DONE** |
| **12** | **Multi-turn LLM context** | **Low** | **High** | Next — easy win |
| **11** | **Adaptive DOM extraction** | **Medium** | **High** | High priority |
| **16** | **Structured JSON output** | **Low** | **Medium** | Easy win |
| **14** | **XPath repair** | **Low** | **Medium** | Easy win |
| 5 | Element prioritization | Medium | Medium | |
| **13** | **Dynamic token budgeting** | **Medium** | **Medium** | |
| 9 | Action validation | Low | Low-Med | |
| **15** | **Smart scroll strategy** | **Medium** | **Medium** | |
| 10 | Fallback candidates | Low | Low | |
