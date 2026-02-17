# TODO — Future Improvements

Techniques borrowed from Magnitude and Browser Use that haven't been implemented yet.

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

## 6. Better Visibility Detection (Low effort, Medium impact)

**Source:** Browser Use's buildDomTree.js

Current `_is_hidden()` only checks `display:none`, `visibility:hidden`, `type=hidden`, and `hidden` attr. Add:

**Where:** `html_processor.py` — update `_is_hidden()`

- `opacity: 0` / `opacity:0`
- `pointer-events: none` (on element AND parents)
- `aria-hidden="true"`
- `width: 0` / `height: 0` patterns
- Parent chain check for `display:none` (currently only checks element itself)

```python
def _is_hidden(el):
    # ... existing checks ...
    # Check opacity
    if "opacity:0" in style.replace(" ", ""):
        return True
    # Check aria-hidden
    if el.get("aria-hidden") == "true":
        return True
    # Check parent chain (up to 3 levels)
    for parent in list(el.parents)[:3]:
        if isinstance(parent, Tag):
            pstyle = (parent.get("style") or "").lower().replace(" ", "")
            if "display:none" in pstyle or "visibility:hidden" in pstyle:
                return True
    return False
```

## ~~7. Explicit Task Decomposition~~ (DONE)

Implemented in `action_parser.py` (`extract_plan`, `_parse_plan_from_text`), `agent.py` (`_task_plan`, `_plan_step`, `_build_plan_text`), and `prompts.py` (`plan_text` parameter).

## 8. Self-Verification / State Assertions (Medium effort, Medium impact)

**Source:** Magnitude's visual assertion

After each action, compare the new page state against expected outcomes:
- Filled a form and clicked submit but URL didn't change → form validation likely failed
- Navigated but page content is identical → navigation failed
- New alert/error elements appeared → read the error message

**Where:** `agent.py` — add `_verify_action_result()` method

```python
def _verify_action_result(self, prev_url, curr_url, prev_elements, curr_elements, last_action):
    """Generate verification notes about what happened after the action."""
    notes = []
    if last_action.get("type") == "click" and prev_url == curr_url:
        # Check if new error/alert elements appeared
        new_alerts = [e for e in curr_elements if "alert" in e.role or "error" in e.classes.lower()]
        if new_alerts:
            notes.append(f"Alert appeared: {new_alerts[0].text}")
    if last_action.get("type") == "navigate" and prev_url == curr_url:
        notes.append("Navigation did not change the URL — check if the URL was correct")
    return notes
```

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

---

## Implementation Priority

| # | Item | Effort | Impact | Dependencies |
|---|------|--------|--------|-------------|
| 5 | Element prioritization | Medium | Medium | rapidfuzz |
| 6 | Visibility detection | Low | Medium | None |
| 7 | ~~Task decomposition~~ | ~~Low~~ | ~~Medium~~ | ~~DONE~~ |
| 8 | Self-verification | Medium | Medium | DOM diff (done) |
| 9 | Action validation | Low | Low-Med | rapidfuzz |
| 10 | Fallback candidates | Low | Low | None |
