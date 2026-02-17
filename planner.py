"""
Multi-step planning, stuck detection, and recovery strategies.

Tracks the agent's progress across steps, detects when it's stuck
in loops, and suggests recovery actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# How many times the same (action_type, target) can repeat in recent window before considered stuck
STUCK_REPEAT_THRESHOLD = 3
# How many consecutive failures before triggering recovery
FAILURE_STREAK_THRESHOLD = 3
# Size of recent action window for stuck detection
RECENT_WINDOW = 10


@dataclass
class PlanState:
    """Tracks the agent's planning state across steps."""
    phase: str = "exploring"  # exploring, filling_form, submitting, navigating, verifying
    recent_action_keys: list[str] = field(default_factory=list)  # sliding window of action keys
    failure_streak: int = 0
    total_failures: int = 0
    last_url: str = ""
    url_visit_count: dict[str, int] = field(default_factory=dict)
    is_stuck: bool = False
    recovery_suggestion: str = ""
    step_count: int = 0


class Planner:
    """Manages multi-step planning and stuck detection."""

    def __init__(self):
        self.state = PlanState()

    def update(self, action: Optional[dict], history: list[dict], current_url: str) -> PlanState:
        """
        Update plan state based on latest action and history.

        Args:
            action: The action that was just decided (or None for NOOP)
            history: Full action history from evaluator
            current_url: Current page URL

        Returns:
            Updated PlanState with stuck detection and recovery suggestions.
        """
        self.state.step_count = len(history)

        # Track URL visits
        if current_url:
            url_key = current_url.split("?")[0]  # Ignore query params
            self.state.url_visit_count[url_key] = self.state.url_visit_count.get(url_key, 0) + 1
            self.state.last_url = current_url

        # Track action repetition in a recent sliding window
        if action:
            action_type = action.get("type", "")
            target = action.get("xpath", "") or action.get("selector", "") or action.get("css_selector", "") or action.get("url", "")
            action_key = f"{action_type}:{target[:60]}"
            self.state.recent_action_keys.append(action_key)
            # Keep only last RECENT_WINDOW actions
            if len(self.state.recent_action_keys) > RECENT_WINDOW:
                self.state.recent_action_keys = self.state.recent_action_keys[-RECENT_WINDOW:]

        # Check failure streak from history
        if history:
            last = history[-1]
            if not last.get("exec_ok", True):
                self.state.failure_streak += 1
                self.state.total_failures += 1
            else:
                self.state.failure_streak = 0

        # Update phase based on recent actions
        self._update_phase(history)

        # Detect stuck states
        self.state.is_stuck = False
        self.state.recovery_suggestion = ""
        self._detect_stuck()

        return self.state

    def _update_phase(self, history: list[dict]):
        """Infer current phase from action history."""
        if not history:
            self.state.phase = "exploring"
            return

        recent = history[-3:] if len(history) >= 3 else history
        recent_types = [h.get("action", "").lower() for h in recent]

        if any(t in ("fill", "type") for t in recent_types):
            self.state.phase = "filling_form"
        elif any(t == "click" for t in recent_types) and self.state.phase == "filling_form":
            self.state.phase = "submitting"
        elif any(t == "navigate" for t in recent_types):
            self.state.phase = "navigating"
        elif self.state.step_count > 1 and all(t == "NOOP" for t in recent_types):
            self.state.phase = "verifying"

    def _detect_stuck(self):
        """Detect if the agent is stuck and suggest recovery."""
        # Check action repetition in recent window
        recent_counts: dict[str, int] = {}
        for key in self.state.recent_action_keys:
            recent_counts[key] = recent_counts.get(key, 0) + 1

        for action_key, count in recent_counts.items():
            if count >= STUCK_REPEAT_THRESHOLD:
                self.state.is_stuck = True
                action_type, target = action_key.split(":", 1) if ":" in action_key else (action_key, "")
                self.state.recovery_suggestion = (
                    f"Action '{action_type}' on '{target[:40]}' repeated {count} times in last {RECENT_WINDOW} steps. "
                    "Try a different approach: use an alternative selector, scroll to find the element, "
                    "or navigate to a different page."
                )
                return

        # Check failure streak
        if self.state.failure_streak >= FAILURE_STREAK_THRESHOLD:
            self.state.is_stuck = True
            self.state.recovery_suggestion = (
                f"{self.state.failure_streak} consecutive action failures. "
                "Try: check for validation errors on the page, use a different selector, "
                "scroll down, or navigate back and try a different approach."
            )
            return

        # Check URL loop (visiting same page too many times)
        for url, count in self.state.url_visit_count.items():
            if count >= 5:
                self.state.is_stuck = True
                self.state.recovery_suggestion = (
                    f"Visited '{url[:50]}' {count} times. "
                    "Try navigating to a different page or taking a different action path."
                )
                return

    def get_context_for_prompt(self) -> str:
        """Generate planning context for the LLM prompt."""
        lines = []

        lines.append(f"Current phase: {self.state.phase}")
        lines.append(f"Steps taken: {self.state.step_count}/30")

        if self.state.total_failures > 0:
            lines.append(f"Total failures: {self.state.total_failures}")

        if self.state.is_stuck:
            lines.append(f"\n⚠ STUCK DETECTED: {self.state.recovery_suggestion}")
            lines.append("You MUST try a DIFFERENT approach than your previous actions.")

        return "\n".join(lines)

    def reset(self):
        """Reset planner state for a new task."""
        self.state = PlanState()
