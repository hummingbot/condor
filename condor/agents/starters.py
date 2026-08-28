"""What this user keeps asking this agent to do, ranked.

The openers on an empty chat used to be three strings compiled into the
frontend. This module is the other half: a small per-``(agent, user)`` file of
*learned* intents, so the next empty chat can offer the things this person
actually comes to this agent for.

Three decisions are worth stating here, because the rest of the file is
arithmetic.

**It lives beside the memories.** ``store_root(user_id, agent_slug)`` is where
the repo already decided per-user-per-agent data goes
(``condor/memory/paths.py``), and a learned opener is exactly that shape of
fact. The skill library next door is keyed by the *agent alone* and read by
every user of it, so a habit stored there would be one person's routine served
to everybody — which is why an opener is not a skill even though it looks like
one (FEAT-073 §Alternatives).

**The ranking is Python, not the model's recollection.** An intent carries a
decayed count: each time it comes up again its score is aged toward now and
then incremented by one. That is an exponential moving count — "most common
lately" without keeping a history to re-scan and without asking a model to
remember what it saw last month. An intent that stops recurring fades on its
own; :data:`HALFLIFE_DAYS` is how fast.

**Merging is by slug, and the slug is the label.** Fragmentation ("check my
portfolio" vs "how's my portfolio") is handled at the cheap end — the reflection
prompt shows the model the slugs it already knows and asks it to reuse one
verbatim when it fits. There is deliberately no fuzzy matching here: a
near-miss that silently merges two different intents is worse than a duplicate
row that decays out.

Pure filesystem + arithmetic. No LLM, no web deps, no MCP — the same rule
``condor/memory/store.py`` follows, so this is importable from the main process
and from a subprocess alike.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from condor.frontmatter import slugify
from condor.memory.paths import store_root
from condor.runtime.registry_file import read_status, write_status

log = logging.getLogger(__name__)

STARTERS_FILENAME = "starters.json"

# How many intents one (agent, user) pair keeps. The surface serves three; the
# rest are the tail that a revival can promote back up without a re-learn.
MAX_ENTRIES = 12

# How fast an unrepeated intent fades: after this many days its contribution to
# the score is halved. Thirty days is one trading month — long enough that a
# habit survives a holiday, short enough that last quarter's obsession stops
# being the first thing offered.
HALFLIFE_DAYS = 30.0

# The icons a chip may ask for. The mapping to an actual glyph lives in the
# frontend (`Starters.tsx`); what lives here is the *vocabulary*, because it is
# the reflection prompt that has to be told what it may choose from, and an
# unknown keyword has to degrade to "" on the way in rather than reach a
# renderer that has no glyph for it.
ICON_VOCABULARY = (
    "portfolio",
    "bot",
    "risk",
    "trade",
    "chart",
    "lp",
    "market",
    "report",
    "config",
    "search",
)

# Bounds on what the model may write into a chip. A label is also the message
# sent on click, so it is a sentence, not an essay.
LABEL_MAX_CHARS = 80
HINT_MAX_CHARS = 120


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StarterEntry(BaseModel):
    """One learned opener.

    ``label`` keeps its first form for the life of the row while ``hint`` /
    ``icon`` / ``skill`` take the latest wording. A chip whose title rewrote
    itself every week would be a different button each time the user looked for
    it; the second line is free to improve.

    ``count`` is for a human reading the file — it is the honest number of times
    this came up. ``score`` is the ranking key and is not that number: it is the
    same count aged toward now, so a stale row sorts below a live one.
    """

    model_config = ConfigDict(extra="ignore")

    slug: str = Field(description="slugify(label) — the merge key.")
    label: str = Field(description="The chip title, and the message sent on click.")
    hint: str = ""
    icon: str = Field(default="", description="One keyword from ICON_VOCABULARY.")
    skill: str = Field(default="", description="Optional playbook this maps to.")
    count: int = 0
    score: float = 0.0
    last_seen: datetime = Field(default_factory=_utcnow)

    @field_validator("last_seen")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        """A hand-edited file may carry a naive stamp; arithmetic needs an aware one."""
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clean(value, limit: int) -> str:
    """One line of model-supplied text, bounded."""
    return " ".join(str(value or "").split())[:limit]


def _icon(value) -> str:
    """A keyword this vocabulary knows, or nothing at all."""
    word = str(value or "").strip().lower()
    return word if word in ICON_VOCABULARY else ""


def _rank(entries: list[StarterEntry]) -> list[StarterEntry]:
    """Best first: score, then recency, then slug so the order is stable."""
    return sorted(entries, key=lambda e: (-e.score, -e.last_seen.timestamp(), e.slug))


def read(user_id: int, agent_slug: str | None = None) -> list[StarterEntry]:
    """Every learned opener for this pair, ranked best first.

    A missing, truncated or hand-mangled file reads as *no openers* rather than
    raising — the caller's fallback is the static list the client already ships,
    which is a fine answer to "we learned nothing". One unparseable row is
    dropped without taking the rest of the file with it, the same tolerance
    ``read_transcript`` shows a bad line.
    """
    data = read_status(store_root(user_id, agent_slug), STARTERS_FILENAME) or {}
    rows = data.get("entries")
    if not isinstance(rows, list):
        return []

    entries: list[StarterEntry] = []
    for row in rows:
        try:
            entries.append(StarterEntry(**row))
        except Exception:  # noqa: BLE001 - one bad row is not the file
            log.debug("Skipping unparseable starter row for user %s", user_id)
    return _rank(entries)


def top(
    user_id: int, agent_slug: str | None = None, limit: int = 3
) -> list[StarterEntry]:
    """The openers worth showing: the best ``limit`` of them."""
    return read(user_id, agent_slug)[:limit]


def merge(
    user_id: int,
    agent_slug: str | None,
    intents: list[dict],
    now: datetime | None = None,
) -> list[StarterEntry]:
    """Fold freshly observed intents into the file and return the new ranking.

    The whole ranking is the two lines in the loop below. An intent already on
    file has its score aged to ``now`` and then incremented; a new one starts at
    ``1.0``. Because the decay is applied at merge time rather than at read
    time, the stored score is always "the count as of ``last_seen``" — which is
    what makes a rival seen twice this week outrank one seen once two months ago
    without anybody re-walking a history.

    An intent without a usable label is dropped rather than stored under a
    placeholder slug: a chip nobody can read is worse than one chip fewer.
    """
    now = now or _utcnow()
    by_slug = {entry.slug: entry for entry in read(user_id, agent_slug)}

    for intent in intents or []:
        if not isinstance(intent, dict):
            continue
        label = _clean(intent.get("label"), LABEL_MAX_CHARS)
        slug = slugify(label, fallback="") if label else ""
        if not slug:
            continue

        hint = _clean(intent.get("hint"), HINT_MAX_CHARS)
        icon = _icon(intent.get("icon"))
        skill = slugify(_clean(intent.get("skill"), LABEL_MAX_CHARS), fallback="")

        entry = by_slug.get(slug)
        if entry is None:
            by_slug[slug] = StarterEntry(
                slug=slug,
                label=label,
                hint=hint,
                icon=icon,
                skill=skill,
                count=1,
                score=1.0,
                last_seen=now,
            )
            continue

        age_days = max(0.0, (now - entry.last_seen).total_seconds() / 86400.0)
        entry.score = entry.score * 0.5 ** (age_days / HALFLIFE_DAYS) + 1.0
        entry.count += 1
        entry.last_seen = now
        # Latest wording wins for everything but the title.
        entry.hint = hint or entry.hint
        entry.icon = icon or entry.icon
        entry.skill = skill or entry.skill

    ranked = _rank(list(by_slug.values()))[:MAX_ENTRIES]
    write_status(
        store_root(user_id, agent_slug),
        STARTERS_FILENAME,
        entries=[entry.model_dump(mode="json") for entry in ranked],
    )
    return ranked
