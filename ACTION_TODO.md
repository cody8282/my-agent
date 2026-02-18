# ACTION TODO — IWA Action Type Gap Analysis

Gap analysis of supported IWA action types vs what our agent currently implements.
Based on review of `autoppia_iwa/src/execution/actions/actions.py` (25 action types total).

## Current Coverage (7 of 25)

| IWA Action Type | Our Agent Action | IWA Format |
|---|---|---|
| `ClickAction` | `click` | `{"type": "ClickAction", "selector": {...}}` |
| `TypeAction` | `fill` / `type` | `{"type": "TypeAction", "text": "...", "selector": {...}}` |
| `NavigateAction` | `navigate` / `go_back` / `go_forward` | `{"type": "NavigateAction", "url": "..."}` |
| `ScrollAction` | `scroll` | `{"type": "ScrollAction", "up": true}` |
| `HoverAction` | `hover` | `{"type": "HoverAction", "selector": {...}}` |
| `SendKeysIWAAction` | `keys` | `{"type": "SendKeysIWAAction", "keys": "Enter"}` |
| `SelectAction` | `select_option` | `{"type": "SelectAction", "value": "...", "selector": {...}}` |

---

## HIGH IMPACT — Must Add (4 actions)

### 1. DoubleClickAction

- **Why:** Text selection in editable fields, opening items in lists, expanding tree nodes. Currently impossible.
- **IWA Format:** `{"type": "DoubleClickAction", "selector": {...}, "x": null, "y": null}`
- **IWA Base Class:** `BaseClickAction` (same params as ClickAction)
- **Agent side:** Add `double_click` action type in `prompts.py`, `action_parser.py`, `main.py`
- **Effort:** Low

### 2. DragAndDropAction

- **Why:** Kanban boards, sortable lists, sliders, range pickers. Any task with drag UI scores 0 today.
- **IWA Format:** `{"type": "DragAndDropAction", "sourceSelector": "xpath_value", "targetSelector": "xpath_value"}`
- **IWA Implementation:** Uses Playwright's native `drag_and_drop()` with XPath selectors as strings (not Selector objects)
- **Agent side:** Add `drag` action type with `source_xpath` and `target_xpath` params
- **Effort:** Medium (LLM needs to identify source + target elements)

### 3. WaitAction

- **Why:** SPA transitions, AJAX loading, animations. Agent currently acts on potentially stale DOM. Reduces wasted steps and false "stuck" detections.
- **IWA Format:** `{"type": "WaitAction", "selector": {...}, "time_seconds": 2.0, "timeout_seconds": 5.0}`
- **IWA Behavior:** Waits for element to appear (if selector given) OR pauses for duration (if time_seconds given)
- **Agent side:** Add `wait` action type. LLM can choose to wait for a specific element or a fixed time.
- **Effort:** Low
- **Scoring impact:** Directly reduces `execution_time` by avoiding wasted retry steps on loading pages

### 4. SelectDropDownOptionAction

- **Why:** IWA's implementation has **multiple fallback strategies** for dropdowns — JS select, click-based selection, text matching with timeout. Much more robust than our current `SelectAction` which just sets `.value`.
- **IWA Format:** `{"type": "SelectDropDownOptionAction", "selector": {...}, "text": "Option Text", "timeout_ms": 1000}`
- **IWA Behavior:** Tries `select_option(label=text)`, falls back to clicking dropdown + clicking option
- **Agent side:** Can replace or supplement current `select_option`. Map to this IWA type instead of `SelectAction`.
- **Effort:** Low (just change the IWA type mapping in `main.py`)

---

## MEDIUM IMPACT — Should Add (2 actions)

### 5. SubmitAction

- **Why:** Direct form submission via Enter key on element. Cleaner than finding + clicking a submit button that might be hidden or have ambiguous xpath.
- **IWA Format:** `{"type": "SubmitAction", "selector": {...}}`
- **IWA Behavior:** Calls `element.press("Enter")` on the selected element
- **Agent side:** Add `submit` action type with `xpath` param
- **Effort:** Low

### 6. TripleClickAction

- **Why:** Select entire line/paragraph of text. Useful for replacing text in contenteditable divs or textareas where `fill` (clear + type) doesn't work properly.
- **IWA Format:** `{"type": "TripleClickAction", "selector": {...}}`
- **IWA Base Class:** `BaseClickAction`
- **Agent side:** Add `triple_click` action type
- **Effort:** Low

---

## LOW IMPACT / CONSIDER — Optional (2 actions)

### 7. HoldKeyAction

- **Why:** Shift+click for multi-select, hold Ctrl while clicking for toggle selection. Niche but could help with multi-select lists.
- **IWA Format:** `{"type": "HoldKeyAction", "key": "Shift", "duration_ms": null, "release": false}`
- **IWA Behavior:** Press key down, optionally hold for duration, optionally release
- **Note:** `SendKeysIWAAction` already handles key combos like `Control+A`. Only add if multi-select tasks are failing.

### 8. RightClickAction

- **Why:** Context menus. Rare but some web apps use them.
- **IWA Format:** `{"type": "RightClickAction", "selector": {...}}`
- **Note:** Very unlikely to be needed in IWA benchmark tasks. Add only if specific tasks require it.

---

## SKIP — Not Worth Adding

| Action | Why Skip |
|---|---|
| `MiddleClickAction` | Opens new tabs — agent receives single HTML snapshot per step, can't manage tabs |
| `LeftClickDragAction` | Low-level manual drag (mousedown/move/up) — `DragAndDropAction` covers it |
| `MouseDownAction` | Low-level primitive — covered by higher-level actions |
| `MouseUpAction` | Low-level primitive — covered by higher-level actions |
| `MouseMoveAction` | Low-level primitive — covered by higher-level actions |
| `GetDropDownOptionsAction` | Agent already extracts options from HTML via `extract_elements()` |
| `ScreenshotAction` | Agent has no vision capability — screenshot is useless |
| `AssertAction` | Testing/debugging utility, not an agent action |
| `UndefinedAction` | No-op — already covered by `noop` |
| `IdleAction` | No-op — already covered by `noop` |

---

## Implementation Plan

### Phase 1: Quick Wins (Low effort, High/Medium impact)

Files to modify: `prompts.py`, `action_parser.py`, `main.py`

1. **SelectDropDownOptionAction** — Just change IWA type mapping in `main.py` from `SelectAction` to `SelectDropDownOptionAction`. Zero risk.
2. **WaitAction** — Add `wait` to action space. Add IWA conversion. Update system prompt.
3. **DoubleClickAction** — Add `double_click` to action space. Same pattern as `click` in all files.
4. **SubmitAction** — Add `submit` to action space. Trivial.

### Phase 2: Medium Effort

5. **DragAndDropAction** — Requires LLM to identify source + target elements. Need prompt guidance for drag scenarios. Need to extract source/target xpaths.
6. **TripleClickAction** — Add `triple_click`. Low code effort but need prompt guidance on when to use it vs `fill`.

### Changes Per File

**`prompts.py`** — Add new action types to the system prompt action list with descriptions and examples.

**`action_parser.py`** — Add new action types to:
- `ACTION_TYPE_ALIASES` (e.g., `"dblclick"` -> `"double_click"`)
- `REQUIRED_FIELDS` (e.g., `"double_click": ["xpath"]`)
- `_resolve_eids()` if needed

**`main.py`** (`_to_iwa_action()`) — Add IWA format conversions:
- `double_click` -> `DoubleClickAction`
- `drag` -> `DragAndDropAction`
- `wait` -> `WaitAction`
- `submit` -> `SubmitAction`
- `triple_click` -> `TripleClickAction`
- `select_option` -> `SelectDropDownOptionAction` (upgrade existing)

**`agent.py`** — No changes needed (action types are transparent to the orchestrator).

---

## Scoring Context

Validator reward formula: `reward = eval_score - TIME_WEIGHT * time_penalty - COST_WEIGHT * cost_penalty`

- **WaitAction**: Fewer wasted steps = less time + less LLM cost
- **DragAndDropAction**: Unlocks entire task categories that currently score 0.0
- **SelectDropDownOptionAction**: Fixes flaky dropdown selection (common failure mode)
- **DoubleClickAction**: Fixes text selection tasks that currently fail
- **SubmitAction**: More reliable form submission = higher eval_score

## IWA Action Class Hierarchy (Reference)

```
BaseAction (Pydantic BaseModel)
 +-- BaseActionWithSelector (requires selector)
 |    +-- SelectAction
 |    +-- SubmitAction
 |    +-- HoverAction
 |    +-- BaseClickAction (selector optional, supports x/y coords)
 |    |    +-- ClickAction
 |    |    +-- DoubleClickAction      <-- ADD
 |    |    +-- RightClickAction
 |    |    +-- MiddleClickAction
 |    |    +-- TripleClickAction      <-- ADD
 |    |    +-- MouseDownAction
 |    |    +-- MouseUpAction
 |    |    +-- MouseMoveAction
 |    +-- GetDropDownOptionsAction
 |    +-- SelectDropDownOptionAction  <-- ADD (upgrade)
 +-- NavigateAction
 +-- TypeAction
 +-- WaitAction                       <-- ADD
 +-- ScrollAction
 +-- AssertAction
 +-- DragAndDropAction                <-- ADD
 +-- LeftClickDragAction
 +-- ScreenshotAction
 +-- SendKeysIWAAction
 +-- HoldKeyAction
 +-- UndefinedAction
 +-- IdleAction
```

## IWA Selector Format (Reference)

```json
{
  "type": "xpathSelector",
  "value": "//input[@name='email']"
}
```

Three selector types: `ATTRIBUTE_VALUE_SELECTOR`, `TAG_CONTAINS_SELECTOR`, `XPATH_SELECTOR`
Our agent uses `xpathSelector` exclusively.
