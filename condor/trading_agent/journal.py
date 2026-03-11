"""JournalManager -- compact persistent memory for trading agents.

Each agent instance gets its own ``journal.md`` under
``data/trading_agents/{agent_id}/``.  The journal is designed to be
small and useful -- a working memory the agent reads every tick.

Structure:
- **Learnings**: Deduplicated insights (max ~20). Agent's long-term memory.
- **State**: Current situation snapshot, overwritten each tick.
- **Recent Actions**: Rolling window of last N ticks (auto-trimmed).

The journal should stay under ~4KB to fit comfortably in a prompt.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).parent.parent.parent / "data" / "trading_agents"

MAX_LEARNINGS = 20
MAX_RECENT_ACTIONS = 10

JOURNAL_TEMPLATE = """\
# Journal - {agent_id}

## Learnings

## State
No ticks yet.

## Recent Actions
"""


class JournalManager:
    """Read/write a compact markdown journal for one agent instance."""

    def __init__(self, agent_id: str, strategy_name: str = "", strategy_description: str = ""):
        self.agent_id = agent_id
        self._dir = _DATA_ROOT / agent_id
        self._path = self._dir / "journal.md"
        self._dir.mkdir(parents=True, exist_ok=True)

        if not self._path.exists():
            self._path.write_text(JOURNAL_TEMPLATE.format(agent_id=agent_id))

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_full(self) -> str:
        """Return the entire journal contents."""
        if not self._path.exists():
            return ""
        return self._path.read_text()

    def read_recent(self, max_entries: int = MAX_RECENT_ACTIONS) -> str:
        """Return the recent actions section (already trimmed)."""
        content = self._get_section("Recent Actions")
        if not content:
            # Backwards compat: old journals used "Actions Log"
            content = self._get_section("Actions Log")
        return content

    def read_learnings(self) -> str:
        """Return the learnings section."""
        return self._get_section("Learnings")

    def read_state(self) -> str:
        """Return the current state snapshot."""
        return self._get_section("State")

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def write_state(self, state_text: str) -> None:
        """Overwrite the State section with a fresh snapshot."""
        self._replace_section("State", state_text.strip())

    def append_action(
        self,
        tick: int,
        action: str,
        reasoning: str,
        risk_note: str = "",
    ) -> None:
        """Append an action to Recent Actions, trimming old ones."""
        now = datetime.now(timezone.utc).strftime("%H:%M")
        parts = [f"- **#{tick}** ({now}) {action}"]
        if reasoning:
            parts[0] += f" — {reasoning}"
        if risk_note:
            parts[0] += f" [{risk_note}]"
        entry = parts[0]

        section = self._get_section("Recent Actions")
        lines = [l for l in section.splitlines() if l.strip()]
        lines.append(entry)

        # Keep only the last N entries
        if len(lines) > MAX_RECENT_ACTIONS:
            lines = lines[-MAX_RECENT_ACTIONS:]

        self._replace_section("Recent Actions", "\n".join(lines))

    def append_learning(self, text_content: str) -> None:
        """Add a learning, deduplicating against existing ones.

        Skips if a substantially similar learning already exists.
        Trims oldest if over MAX_LEARNINGS.
        """
        section = self._get_section("Learnings")
        existing_lines = [l for l in section.splitlines() if l.startswith("- ")]

        # Deduplicate: check if the core idea already exists
        normalized_new = _normalize(text_content)
        for line in existing_lines:
            # Strip the "- " and any timestamp prefix like "[HH:MM]"
            existing_text = re.sub(r"^- (\[\d{2}:\d{2}\] )?", "", line)
            if _normalize(existing_text) == normalized_new:
                return  # Already have this learning
            # Fuzzy: if >50% word overlap, skip
            if _word_overlap(normalized_new, _normalize(existing_text)) > 0.5:
                return

        now = datetime.now(timezone.utc).strftime("%H:%M")
        existing_lines.append(f"- [{now}] {text_content}")

        # Trim oldest if too many
        if len(existing_lines) > MAX_LEARNINGS:
            existing_lines = existing_lines[-MAX_LEARNINGS:]

        self._replace_section("Learnings", "\n".join(existing_lines))

    def append_error(self, error: str) -> None:
        """Append an error as an action entry."""
        now = datetime.now(timezone.utc).strftime("%H:%M")
        section = self._get_section("Recent Actions")
        lines = [l for l in section.splitlines() if l.strip()]
        lines.append(f"- **error** ({now}) {error}")
        if len(lines) > MAX_RECENT_ACTIONS:
            lines = lines[-MAX_RECENT_ACTIONS:]
        self._replace_section("Recent Actions", "\n".join(lines))

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def _get_section(self, name: str) -> str:
        """Extract content between ## {name} and the next ## header."""
        text = self.read_full()
        pattern = rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)"
        m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        return m.group(1).strip() if m else ""

    def _replace_section(self, name: str, content: str) -> None:
        """Replace the content of a section, preserving other sections."""
        text = self.read_full()
        pattern = rf"(^## {re.escape(name)}\n).*?(?=^## |\Z)"
        replacement = rf"\g<1>{content}\n\n"
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
        if count == 0:
            # Section doesn't exist, append it
            new_text = text.rstrip() + f"\n\n## {name}\n{content}\n"
        self._path.write_text(new_text)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def entry_count(self) -> int:
        """Count recent action entries."""
        section = self._get_section("Recent Actions")
        return len([l for l in section.splitlines() if l.startswith("- ")])

    def get_data_dir(self) -> Path:
        """Return the agent's data directory."""
        return self._dir

    def size_bytes(self) -> int:
        """Current file size."""
        return self._path.stat().st_size if self._path.exists() else 0


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _word_overlap(a: str, b: str) -> float:
    """Fraction of words in common between two strings."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))
