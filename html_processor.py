"""
DOM parsing and interactive element extraction.

Converts raw HTML into a compact representation of interactive elements
plus a readable page summary, optimized for LLM token budgets.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Tags that are always interactive
INTERACTIVE_TAGS = {"input", "button", "select", "textarea", "a"}

# Attributes that make any element interactive
INTERACTIVE_ATTRS = {"onclick", "onsubmit", "onchange", "ng-click", "v-on:click", "@click"}

# Roles that imply interactivity
INTERACTIVE_ROLES = {"button", "link", "tab", "menuitem", "checkbox", "radio", "switch", "combobox", "listbox", "option", "textbox"}

# Tags/attrs to skip entirely
SKIP_TAGS = {"script", "style", "noscript", "svg", "path", "meta", "link", "head"}

MAX_TEXT_LEN = 80
MAX_ELEMENTS = 150
MAX_CONTENT_CHARS = 12000

# Extraction modes: tag filters for adaptive DOM extraction
EXTRACTION_MODES: dict[str, Optional[set[str]]] = {
    "input_fields": {"input", "select", "textarea", "button"},
    "links_only": {"a", "button"},
    "all_fields": None,  # no filter, current behavior
}


@dataclass
class InteractiveElement:
    eid: str  # short id like "e1"
    tag: str
    type: str = ""
    name: str = ""
    id: str = ""
    classes: str = ""
    text: str = ""
    placeholder: str = ""
    value: str = ""
    href: str = ""
    aria_label: str = ""
    role: str = ""
    options: list[str] = field(default_factory=list)
    css_selector: str = ""
    xpath: str = ""
    is_hidden: bool = False
    is_required: bool = False
    in_modal: bool = False

    def to_compact(self) -> str:
        """Single-line compact representation for the LLM prompt."""
        parts = [f"[{self.eid}]", self.tag]
        if self.type:
            parts.append(f'type="{self.type}"')
        if self.name:
            parts.append(f'name="{self.name}"')
        if self.role:
            parts.append(f'role="{self.role}"')
        if self.placeholder:
            parts.append(f'placeholder="{self.placeholder}"')
        if self.value:
            parts.append(f'value="{_trunc(self.value, 40)}"')
        if self.href:
            parts.append(f'href="{_trunc(self.href, 60)}"')
        if self.aria_label:
            parts.append(f'aria="{_trunc(self.aria_label, 40)}"')
        if self.text:
            parts.append(f'text="{_trunc(self.text, 50)}"')
        if self.options:
            opts = ", ".join(self.options[:8])
            parts.append(f"options=[{opts}]")
        if self.is_required:
            parts.append("[required]")
        if self.is_hidden:
            parts.append("[hidden]")
        if self.in_modal:
            parts.append("[modal]")
        # Always show xpath so the LLM knows how to target this element
        parts.append(f'xpath="{self.xpath}"')
        return " ".join(parts)


def _trunc(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) > max_len:
        return s[:max_len - 1] + "…"
    return s


def _get_text(el: Tag) -> str:
    """Get direct text content, not from children."""
    text = el.get_text(separator=" ", strip=True)
    return _trunc(text, MAX_TEXT_LEN) if text else ""


def _is_hidden(el: Tag) -> bool:
    """Check if element is hidden via CSS, attributes, or parent chain."""
    # Check element itself
    if _has_hidden_style(el):
        return True
    if el.get("hidden") is not None:
        return True
    el_type = (el.get("type") or "").lower()
    if el_type == "hidden":
        return True
    if el.get("aria-hidden") == "true":
        return True
    # Check parent chain (up to 3 levels) for inherited hiding
    for parent in list(el.parents)[:3]:
        if isinstance(parent, Tag) and parent.name not in ("html", "body", "[document]"):
            if _has_hidden_style(parent):
                return True
    return False


def _has_hidden_style(el: Tag) -> bool:
    """Check inline style for hidden indicators."""
    style = (el.get("style") or "").lower().replace(" ", "")
    if "display:none" in style:
        return True
    if "visibility:hidden" in style:
        return True
    if "opacity:0" in style:
        return True
    if "pointer-events:none" in style:
        return True
    return False


def _build_css_selector(el: Tag) -> str:
    """Build a CSS selector, preferring stable attributes over volatile ones."""
    tag = el.name or ""
    # Prefer name (always stable)
    name = el.get("name", "")
    if name:
        return f'{tag}[name="{name}"]'
    # aria-label (stable)
    aria = el.get("aria-label", "")
    if aria:
        return f'{tag}[aria-label="{aria}"]'
    # id only if stable
    el_id = el.get("id", "")
    if el_id and _is_stable_id(el_id):
        return f"#{el_id}"
    # Fallback to tag + classes
    classes = el.get("class", [])
    if classes:
        cls_str = ".".join(c for c in classes if c)
        if cls_str:
            return f"{tag}.{cls_str}"
    # Unstable id as last resort before bare tag
    if el_id:
        return f"#{el_id}"
    return tag


_RE_STABLE_ID = re.compile(r"[a-zA-Z][a-zA-Z_-]{0,30}$")
_RE_VARIANT_SUFFIX = re.compile(r"[-_]\d+$")
_RE_HEX_LONG = re.compile(r"[0-9a-f]{8,}")


def _is_stable_id(el_id: str) -> bool:
    """Check if an HTML id looks stable (not a dynamic/seed-variant id).

    Dynamic IDs from the anti-scraping system often contain trailing digits,
    UUIDs, or variant suffixes like 'card-2', 'item-3-variant'.
    """
    # Short, simple IDs are usually stable (e.g. "submit", "email", "search")
    if _RE_STABLE_ID.match(el_id):
        return True
    # IDs ending in digits after a separator are likely dynamic variants
    if _RE_VARIANT_SUFFIX.search(el_id):
        return False
    # IDs with UUIDs or long hex strings are dynamic
    if _RE_HEX_LONG.search(el_id):
        return False
    return True


def _build_xpath(el: Tag) -> str:
    """Build a reasonable XPath for the element.

    Prefers stable selectors (@name, @aria-label) over potentially volatile
    ones (@id, @class) to handle seed-based dynamic attribute systems.
    """
    tag = el.name or "*"
    # Prefer @name first — always stable across seeds
    name = el.get("name", "")
    if name:
        return f'//{tag}[@name="{name}"]'
    # aria-label — stable, set by developers for accessibility
    aria = el.get("aria-label", "")
    if aria:
        return f'//{tag}[@aria-label="{aria}"]'
    # @id — only if it looks stable (not a dynamic variant)
    el_id = el.get("id", "")
    if el_id and _is_stable_id(el_id):
        return f'//*[@id="{el_id}"]'
    # Text-based (avoid quotes in text that would break XPath)
    text = el.get_text(strip=True)
    if text and len(text) < 50 and '"' not in text and "'" not in text:
        return f'//{tag}[contains(text(), "{text[:40]}")]'
    # Unstable @id as fallback (better than class/positional)
    if el_id:
        return f'//*[@id="{el_id}"]'
    # Class-based fallback
    classes = el.get("class", [])
    if classes:
        first_cls = classes[0]
        return f'//{tag}[contains(@class, "{first_cls}")]'
    # Positional fallback
    return f"//{tag}"


def _is_interactive(el: Tag) -> bool:
    """Check if an element is interactive."""
    if el.name in INTERACTIVE_TAGS:
        return True
    if any(el.get(attr) for attr in INTERACTIVE_ATTRS):
        return True
    role = (el.get("role") or "").lower()
    if role in INTERACTIVE_ROLES:
        return True
    # contenteditable
    if el.get("contenteditable") in ("true", ""):
        return True
    # tabindex
    tabindex = el.get("tabindex")
    if tabindex is not None and tabindex != "-1":
        return True
    return False


def _has_non_tag_interactivity(el: Tag) -> bool:
    """Check if element is interactive via role, attr, tabindex, or contenteditable (not just its tag)."""
    if any(el.get(attr) for attr in INTERACTIVE_ATTRS):
        return True
    role = (el.get("role") or "").lower()
    if role in INTERACTIVE_ROLES:
        return True
    if el.get("contenteditable") in ("true", ""):
        return True
    tabindex = el.get("tabindex")
    if tabindex is not None and tabindex != "-1":
        return True
    return False


def _is_inside_decoy(el: Tag) -> bool:
    """Check if element is a decoy or nested inside a decoy/wrapper element."""
    if el.get("data-decoy") is not None or el.get("data-dyn-wrap") is not None:
        return True
    for parent in list(el.parents)[:5]:
        if isinstance(parent, Tag) and parent.name not in ("html", "body", "[document]"):
            if parent.get("data-decoy") is not None:
                return True
    return False


def _find_open_modals(soup: BeautifulSoup) -> list[Tag]:
    """Find open modal/dialog containers in the DOM.

    Detects modals via targeted queries (avoids full DOM walk):
    - role="dialog" or role="alertdialog"
    - <dialog open> elements
    - aria-modal="true"
    - data-state="open" with a role (Radix UI portals)
    """
    modals: list[Tag] = []
    # role="dialog" or "alertdialog"
    for el in soup.find_all(attrs={"role": re.compile(r"^(dialog|alertdialog)$", re.I)}):
        modals.append(el)
    # <dialog open>
    for el in soup.find_all("dialog", attrs={"open": True}):
        if el not in modals:
            modals.append(el)
    # aria-modal="true"
    for el in soup.find_all(attrs={"aria-modal": "true"}):
        if el not in modals:
            modals.append(el)
    return modals


def _is_inside_modal(el: Tag, modal_set: set[int]) -> bool:
    """Check if element is a descendant of any open modal container.

    Uses id()-based set for O(1) membership checks.
    """
    if not modal_set:
        return False
    if id(el) in modal_set:
        return True
    for parent in el.parents:
        if id(parent) in modal_set:
            return True
    return False


def extract_elements(html: str, mode: str = "all_fields") -> list[InteractiveElement]:
    """Extract interactive elements from HTML, optionally filtered by mode."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    tag_filter = EXTRACTION_MODES.get(mode)
    modal_containers = _find_open_modals(soup)
    modal_id_set = {id(m) for m in modal_containers}

    elements: list[InteractiveElement] = []
    seen_selectors: set[str] = set()
    eid_counter = 0

    for el in soup.find_all(True):
        if el.name in SKIP_TAGS:
            continue
        # Skip anti-scraping decoy elements and their children
        if _is_inside_decoy(el):
            continue
        if not _is_interactive(el):
            continue

        # Mode filter: skip tags not in the allowed set, unless the element
        # is also interactive via role/attr/tabindex/contenteditable.
        if tag_filter and el.name not in tag_filter and not _has_non_tag_interactivity(el):
            continue

        if eid_counter >= MAX_ELEMENTS:
            break

        css_sel = _build_css_selector(el)
        # Build a dedup key that distinguishes elements sharing Tailwind classes.
        # Include href (for links) or name/text as differentiator.
        dedup_extra = el.get("href", "") or el.get("name", "") or el.get_text(strip=True)[:40]
        dedup_key = f"{css_sel}|{dedup_extra}"
        # Deduplicate (skip bare tag-only selectors from dedup)
        if dedup_key in seen_selectors and css_sel != el.name:
            continue
        seen_selectors.add(dedup_key)

        eid_counter += 1
        eid = f"e{eid_counter}"

        xpath = _build_xpath(el)

        # Extract select options
        options: list[str] = []
        if el.name == "select":
            for opt in el.find_all("option"):
                opt_text = opt.get_text(strip=True)
                opt_val = opt.get("value", "")
                if opt_text:
                    options.append(opt_text)
                elif opt_val:
                    options.append(opt_val)

        # Detect required attribute
        is_required = el.get("required") is not None or (el.get("aria-required") or "").lower() == "true"

        elem = InteractiveElement(
            eid=eid,
            tag=el.name or "",
            type=(el.get("type") or "").lower(),
            name=el.get("name") or "",
            id=el.get("id") or "",
            classes=" ".join(el.get("class", [])),
            text=_get_text(el),
            placeholder=el.get("placeholder") or "",
            value=el.get("value") or "",
            href=el.get("href") or "",
            aria_label=el.get("aria-label") or "",
            role=(el.get("role") or "").lower(),
            options=options,
            css_selector=css_sel,
            xpath=xpath,
            is_hidden=_is_hidden(el),
            is_required=is_required,
            in_modal=_is_inside_modal(el, modal_id_set),
        )
        elements.append(elem)

    # When a modal is open, sort modal elements to top so LLM sees them first
    if modal_containers:
        modal_elems = [e for e in elements if e.in_modal]
        non_modal_elems = [e for e in elements if not e.in_modal]
        elements = modal_elems + non_modal_elems
        # Re-assign eids so numbering matches display order
        for i, elem in enumerate(elements, 1):
            elem.eid = f"e{i}"

    return elements


def get_page_summary(html: str) -> str:
    """Get a readable text summary of the page content."""
    try:
        from readability import Document
        doc = Document(html)
        title = doc.title() or ""
        summary_html = doc.summary()
    except Exception:
        title = ""
        summary_html = html

    try:
        from markdownify import markdownify
        text = markdownify(summary_html, strip=["img", "script", "style"])
    except Exception:
        soup = BeautifulSoup(summary_html, "lxml")
        text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    if title:
        text = f"Page Title: {title}\n\n{text}"

    return text[:MAX_CONTENT_CHARS]


def process_html(html: str, mode: str = "all_fields") -> tuple[list[InteractiveElement], str]:
    """
    Main entry point: extract interactive elements and page summary.
    Returns (elements, page_summary).
    """
    if not html or not html.strip():
        return [], ""

    elements = extract_elements(html, mode=mode)
    summary = get_page_summary(html)
    return elements, summary


def compute_element_diff(
    prev_elements: list[InteractiveElement],
    curr_elements: list[InteractiveElement],
) -> str:
    """
    Compute a human-readable diff between two element snapshots.
    Returns a compact string describing new, removed, and changed elements.
    """
    if not prev_elements:
        return ""

    # Build a stable identity key for diffing across steps.
    # Use css_selector + tag + differentiators to avoid collisions
    # when multiple elements share a bare tag selector.
    def _key(e: InteractiveElement) -> str:
        extra = e.name or e.id or e.placeholder or e.text[:20] if e.text else ""
        return f"{e.tag}|{e.css_selector}|{extra}"

    prev_map = {_key(e): e for e in prev_elements}
    curr_map = {_key(e): e for e in curr_elements}

    prev_keys = set(prev_map.keys())
    curr_keys = set(curr_map.keys())

    new_keys = curr_keys - prev_keys
    removed_keys = prev_keys - curr_keys
    common_keys = prev_keys & curr_keys

    lines: list[str] = []

    # New elements (limit to 10 most important)
    new_elements = [curr_map[k] for k in new_keys]
    if new_elements:
        for e in new_elements[:10]:
            desc = e.tag
            if e.text:
                desc += f' "{_trunc(e.text, 30)}"'
            elif e.placeholder:
                desc += f' placeholder="{e.placeholder}"'
            elif e.name:
                desc += f' name="{e.name}"'
            lines.append(f"  + NEW [{e.eid}] {desc}")
        if len(new_elements) > 10:
            lines.append(f"  + ... and {len(new_elements) - 10} more new elements")

    # Removed elements (limit to 5)
    removed_elements = [prev_map[k] for k in removed_keys]
    if removed_elements:
        for e in removed_elements[:5]:
            desc = e.tag
            if e.text:
                desc += f' "{_trunc(e.text, 30)}"'
            elif e.name:
                desc += f' name="{e.name}"'
            lines.append(f"  - REMOVED [{e.eid}] {desc}")
        if len(removed_elements) > 5:
            lines.append(f"  - ... and {len(removed_elements) - 5} more removed")

    # Changed elements (value changes — important for form state)
    for key in common_keys:
        prev_e = prev_map[key]
        curr_e = curr_map[key]
        changes = []
        if prev_e.value != curr_e.value:
            changes.append(f'value: "{_trunc(prev_e.value, 20)}" -> "{_trunc(curr_e.value, 20)}"')
        if prev_e.text != curr_e.text and prev_e.tag not in ("a",):
            changes.append(f'text: "{_trunc(prev_e.text, 20)}" -> "{_trunc(curr_e.text, 20)}"')
        if prev_e.is_hidden != curr_e.is_hidden:
            changes.append(f'visibility: {"hidden" if curr_e.is_hidden else "visible"}')
        if changes:
            desc = curr_e.tag
            if curr_e.name:
                desc += f' name="{curr_e.name}"'
            lines.append(f"  ~ CHANGED [{curr_e.eid}] {desc}: {'; '.join(changes)}")

    if not lines:
        return ""

    return "## Page Changes Since Last Step\n" + "\n".join(lines)


def elements_to_prompt(elements: list[InteractiveElement]) -> str:
    """Format elements as a compact list for the LLM prompt."""
    if not elements:
        return "No interactive elements found on the page."

    has_modal = any(e.in_modal for e in elements)
    visible = [e for e in elements if not e.is_hidden]
    hidden = [e for e in elements if e.is_hidden]

    lines: list[str] = []

    if has_modal:
        modal_visible = [e for e in visible if e.in_modal]
        page_visible = [e for e in visible if not e.in_modal]
        lines.append("** MODAL/DIALOG IS OPEN — focus on these elements first: **")
        lines.append(f"Modal elements ({len(modal_visible)}):")
        for e in modal_visible:
            lines.append(f"  {e.to_compact()}")
        if page_visible:
            lines.append(f"\nBackground page elements ({len(page_visible)}):")
            for e in page_visible:
                lines.append(f"  {e.to_compact()}")
    else:
        lines.append("Interactive elements:")
        for e in visible:
            lines.append(f"  {e.to_compact()}")

    if hidden:
        lines.append(f"\nHidden elements ({len(hidden)}):")
        for e in hidden:
            lines.append(f"  {e.to_compact()}")

    return "\n".join(lines)
