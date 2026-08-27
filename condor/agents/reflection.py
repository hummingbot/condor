"""Reading a finished conversation back, once, to learn from it (FEAT-061).

Three things come out of a chat that has gone quiet: **facts worth remembering
about this user**, which go to the agent's per-user memory like any other;
**what the user actually came for**, which goes to
:mod:`condor.agents.starters` and becomes the openers on their next empty chat
with that agent; and, occasionally, **a procedure the conversation worked out**,
which goes to :mod:`condor.memory.proposals` as an *offer* — a playbook a human
accepts before it ever reaches a prompt (FEAT-062).

The shape of this module is borrowed on purpose.

**A conversation never formally ends**, so ``condor/sharing/sweep.py`` had to
invent a notion of "finished" and settled on "nothing has happened for
:data:`IDLE_S`", with a job-queue tick, a per-tick budget, oldest-first pooling
across users and a blanket "one bad conversation is not the tick". That is
exactly this problem, so this is exactly that answer rather than a second
notion of finished. What is *not* borrowed is everything the sharing sweep
needed because its payload leaves the machine: there is no consent gate here,
no rate limit and no forward-only rule, because nothing here goes anywhere.
``CONDOR_REFLECTION=off`` is the operator's switch, and it is read in
:func:`eligible` so the sweep goes quiet rather than half-running.

**The pass is tool-less.** ``build_llm_client(..., mcp_servers=None)`` — no MCP
subprocess, no permission callback, no toolset. That is load-bearing twice
over: its whole input is a transcript already on disk, so tools would buy
nothing; and it makes "memories are written, playbooks are only proposed" a
structural property of the run rather than an instruction a model has to be
trusted to follow.

**A conversation is attempted exactly once.** Every failure path — no agent, no
model, an empty transcript, an answer that will not parse, an LLM that raises —
still stamps ``reflected_at``. Retrying an unparseable answer forever on a
15-minute tick is how a background pass turns into a token leak.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from condor.frontmatter import parse_frontmatter
from condor.memory.paths import CHAT_SLUG
from condor.runtime import conversations
from condor.runtime.conversations import ConversationMeta

log = logging.getLogger(__name__)

# The operator's switch. Anything but ``off`` leaves the pass running.
ENV_VAR = "CONDOR_REFLECTION"
OFF = "off"

# How long a conversation must have been untouched to count as finished. The
# sharing sweep's number, for the sharing sweep's reason: ``updated_at`` is
# stamped on every merge, so silence is the only available ending.
IDLE_S = 30 * 60

# How often the job runs, and how many reflections one run may spend. Unlike
# sharing there is no external allowance to divide up — the budget exists only
# so a backlog drains steadily instead of firing a hundred model runs at once
# the first time an install turns this on.
SWEEP_INTERVAL_S = 15 * 60
PER_TICK = 3

# A conversation with one turn in it is a greeting, not a task.
MIN_TURNS = 2

# What one conversation may teach, enforced here rather than asked for in the
# prompt: a model that returns twenty does not get to write twenty.
MAX_INTENTS = 2
MAX_MEMORIES = 2

# Memories written by this pass are stamped so they are distinguishable in the
# audit log from the ones the user or the chat wrote.
SOURCE = "reflection"

REFLECTION_JOB = "agent_reflection"

REFLECT_FILENAME = "reflect.md"


def enabled() -> bool:
    """False only when an operator has explicitly turned the pass off."""
    return (os.environ.get(ENV_VAR) or "").strip().lower() != OFF


# ── The prompt ───────────────────────────────────────────────────────────


def load_policy(agent_slug: str | None = None) -> str:
    """The reflection prompt for this agent: its own, else the shipped default.

    The agent → ``_defaults`` half of :func:`condor.agents.shutdown.load_shutdown_policy`,
    and resolved the same way for the same reason: an agent that wants to learn
    something else from its chats says so by dropping a ``reflect.md`` in its own
    directory, with no code change and no registry to update. Frontmatter is
    parsed off and discarded — this file is a body, but an override is free to
    carry metadata without it leaking into the prompt.
    """
    from condor.memory.paths import assistant_home

    home = assistant_home(agent_slug)
    for path in (home / REFLECT_FILENAME, home.parent / "_defaults" / REFLECT_FILENAME):
        try:
            if not path.is_file():
                continue
            _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            body = body.strip()
            if body:
                return body
        except Exception:  # noqa: BLE001 - an unreadable policy is not a crash
            log.warning("Could not read %s", path, exc_info=True)
    return ""


def build_prompt(
    policy: str,
    transcript: str,
    memory_index: str,
    known: list[str],
    skill_index: str = "",
) -> str:
    """Policy, then everything the model needs and nothing it does not.

    The known slugs are the whole of the de-duplication strategy: showing the
    model what it has already named and asking it to reuse one verbatim is
    cheaper and truer than fuzzy-matching labels in Python, where a near-miss
    would silently fuse two different intents (FEAT-061 §1).

    The skill index is here for the same reason at the other end: the bar for
    proposing a playbook (FEAT-062) is "no existing skill covers it", which is
    only a checkable bar if the model is shown the library. It is the same
    index line the agent itself is handed at the top of every turn, so what the
    pass reasons against is what the agent would actually read.
    """
    from condor.agents import starters

    parts = [policy] if policy else []
    parts.append(f"## The conversation\n\n{transcript}")
    if memory_index:
        parts.append(f"## What you already remember about this user\n\n{memory_index}")
    if skill_index:
        parts.append(
            "## Playbooks this assistant already has\n\n"
            + skill_index
            + "\n\nPropose a playbook only if none of these covers it."
        )
    if known:
        parts.append(
            "## Intent slugs you have already named for this user\n\n"
            + "\n".join(f"- {slug}" for slug in known)
            + "\n\nReuse one verbatim as the `label`'s slug when it fits."
        )
    parts.append(
        "## Icon vocabulary\n\n" + ", ".join(starters.ICON_VOCABULARY) + ', or "".'
    )
    return "\n\n".join(parts)


# ── The answer ───────────────────────────────────────────────────────────

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _first_object(text: str) -> str:
    """The first brace-balanced ``{...}`` in ``text``, or ``""``.

    Braces inside strings are skipped, so a label containing one does not end
    the object early. Cheap, and it only has to survive a model that wrapped its
    JSON in an apology.
    """
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def parse_answer(text: str) -> dict | None:
    """The model's JSON, however it wrapped it. ``None`` when there is none.

    A fenced block first, because that is what the prompt asks for and because
    a fence is unambiguous; then the first balanced object anywhere, because a
    model that prefixed "Sure, here you go:" has still answered. Anything else
    is a failure, and a failure is final for this conversation — see the module
    docstring.
    """
    if not text:
        return None
    fenced = _FENCE.search(text)
    for candidate in (fenced.group(1) if fenced else None, _first_object(text)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _rows(data: dict, key: str, limit: int) -> list[dict]:
    """``limit`` well-formed rows from one list in the answer."""
    raw = data.get(key)
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)][:limit]


# ── The pass ─────────────────────────────────────────────────────────────


def _file_proposal(data: dict, meta: ConversationMeta) -> bool:
    """File the answer's playbook, if it offered one. True when one was filed.

    The model **cannot** write a skill — the run is tool-less, so there is no
    ``manage_skill`` for it to call — and this is the whole of what it can do
    instead: hand the runtime four fields, which land in ``proposals/`` for a
    human to accept or discard (FEAT-062). Guarded like the memories beside it:
    a missing, mistyped or half-filled proposal is dropped, never fatal to the
    intents and memories in the same answer.
    """
    from condor.memory import proposals

    row = data.get("skill_proposal")
    if not isinstance(row, dict):
        return False
    try:
        filed = proposals.put(
            meta.agent_slug or None,
            name=str(row.get("name") or ""),
            description=str(row.get("description") or ""),
            when_to_use=str(row.get("when_to_use") or ""),
            body=str(row.get("body") or ""),
            conversation_id=meta.id,
        )
        return bool(filed.get("saved"))
    except Exception:  # noqa: BLE001 - a bad proposal is not the pass
        log.warning("Could not file a proposed playbook", exc_info=True)
        return False


def _mark(user_id: int, conv_id: str, ok: bool) -> None:
    """Stamp the attempt. Written as ISO, like every other meta datetime."""
    conversations.update_meta(
        user_id,
        conv_id,
        reflected_at=datetime.now(timezone.utc).isoformat(),
        reflected_ok=bool(ok),
    )


async def reflect(user_id: int, meta: ConversationMeta) -> bool:
    """Read one finished conversation back and keep what it taught. Never raises.

    Returns True when something was actually learned. Either way the
    conversation is stamped on the way out, from a single ``finally``, so no
    early return can accidentally leave one eligible forever.
    """
    from condor.agents import starters
    from condor.agents.agent import AgentStore
    from condor.memory import MemoryStore, SkillStore

    slug = meta.agent_slug or CHAT_SLUG
    learned = False
    client = None
    try:
        agent = AgentStore().get(slug)
        if agent is None:
            log.debug("No agent %s to reflect conversation %s with", slug, meta.id)
            return False

        model_key = meta.agent_key or agent.agent_key
        if not model_key:
            log.debug(
                "No model for agent %s; conversation %s unreflected", slug, meta.id
            )
            return False

        transcript = conversations.replay_context(user_id, meta.id)
        if not transcript.strip():
            return False

        store = MemoryStore(user_id, meta.agent_slug or None)
        prompt = build_prompt(
            load_policy(meta.agent_slug or None),
            transcript,
            store.list_index(),
            [entry.slug for entry in starters.read(user_id, meta.agent_slug or None)],
            SkillStore(meta.agent_slug or None).list_index(),
        )

        from condor.runtime.llm_client import build_llm_client

        # Tool-less on purpose: no MCP servers, no permission callback, no
        # allowlist to enforce. See the module docstring.
        client = build_llm_client(model_key, mcp_servers=None, user_id=user_id)
        await client.start()
        answer = await client.prompt(prompt)

        data = parse_answer(answer or "")
        if data is None:
            log.info("Reflection on %s returned nothing parseable", meta.id)
            return False

        for row in _rows(data, "memories", MAX_MEMORIES):
            try:
                # The store refuses by *returning* an error, the house idiom, so
                # a half-filled row costs nothing and does not count as learning.
                saved = store.write(
                    name=str(row.get("name") or ""),
                    content=str(row.get("content") or ""),
                    description=str(row.get("description") or ""),
                    type=str(row.get("type") or "fact"),
                    source=SOURCE,
                )
                learned = learned or bool(saved.get("saved"))
            except Exception:  # noqa: BLE001 - one bad memory is not the pass
                log.warning("Could not write a reflected memory", exc_info=True)

        intents = _rows(data, "intents", MAX_INTENTS)
        if intents:
            starters.merge(user_id, meta.agent_slug or None, intents)
            learned = True

        learned = _file_proposal(data, meta) or learned
        return learned
    except Exception:  # noqa: BLE001 - a failed reflection is not a crash
        log.warning("Reflection failed for conversation %s", meta.id, exc_info=True)
        return False
    finally:
        if client is not None:
            try:
                await client.stop()
            except Exception:  # noqa: BLE001
                log.debug("Could not stop the reflection client", exc_info=True)
        _mark(user_id, meta.id, learned)


# ── The sweep ────────────────────────────────────────────────────────────


def _users() -> list[int]:
    """Every user with a conversation store on this install.

    Sharing reads its list from the consent record; there is no consent record
    here, so the list is the filesystem. A directory whose name is not an id is
    skipped rather than guessed at — the runtime root is not only conversations.
    """
    from condor import paths

    found: list[int] = []
    root: Path = paths.users_root()
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not (child / paths.CONVERSATIONS_DIRNAME).is_dir():
            continue
        try:
            found.append(int(child.name))
        except ValueError:
            log.debug("Skipping non-numeric user directory %s", child.name)
    return found


def eligible(user_id: int, now: float | None = None) -> list[ConversationMeta]:
    """This user's conversations the pass may read right now, oldest first.

    Pure but for reading ``meta.json``: no model, no writes. Four rules, each
    able to refuse a conversation on its own and each therefore testable on its
    own against a conversation that fails only that one:

    **Off.** ``CONDOR_REFLECTION=off`` is checked here rather than in
    :func:`sweep`, so a disabled install produces no candidates at all instead
    of building a candidate list it then declines to use.

    **Idle.** Nothing has happened for :data:`IDLE_S`. The only available notion
    of "finished" (see the module docstring), and it will sometimes take a chat
    the user was about to continue — harmless, because the intent merges into
    the same row the next time it comes up.

    **Grown.** At least :data:`MIN_TURNS` turns. One turn is a greeting; there
    is no task in it to name.

    **Unread.** ``reflected_at`` unset. This is what makes a conversation cost
    one model run for its whole life.
    """
    if not enabled():
        return []

    now = time.time() if now is None else now
    ready: list[ConversationMeta] = []
    for meta in conversations.list_conversations(user_id, limit=0):
        if meta.reflected_at is not None:
            continue
        if meta.turn_count < MIN_TURNS:
            continue
        if now - meta.updated_at.timestamp() <= IDLE_S:
            continue
        ready.append(meta)

    ready.sort(key=lambda m: m.updated_at)
    return ready


def _candidates(now: float) -> list[tuple[int, ConversationMeta]]:
    """Everything ready across every user, oldest waiting first.

    Pooled and sorted together rather than swept user by user, exactly as
    ``condor.sharing.sweep._candidates`` does, so one user with a large backlog
    drains at the same rate as everyone else instead of owning every tick.
    """
    found: list[tuple[int, ConversationMeta]] = []
    for user_id in _users():
        try:
            found.extend((user_id, meta) for meta in eligible(user_id, now))
        except Exception:  # noqa: BLE001 - one unreadable store is not the tick
            log.debug("Could not list conversations for %s", user_id, exc_info=True)
    found.sort(key=lambda pair: pair[1].updated_at)
    return found


async def sweep(now: float | None = None) -> int:
    """Reflect what is finished. Never raises; returns how many ran.

    Runs on the job queue, so a failure here must not be able to take the bot
    down or stall the jobs behind it. The listing is a ``meta.json`` per
    conversation per user, which is blocking work on a loop uvicorn shares, so
    it goes to a thread; the reflections themselves are already async.
    """
    if not enabled():
        return 0

    now = time.time() if now is None else now
    candidates = await asyncio.to_thread(_candidates, now)
    if not candidates:
        return 0

    ran = 0
    for user_id, meta in candidates[:PER_TICK]:
        try:
            await reflect(user_id, meta)
            ran += 1
        except Exception:  # noqa: BLE001 - one bad conversation is not the tick
            log.warning("Could not reflect conversation %s", meta.id, exc_info=True)

    if len(candidates) > PER_TICK:
        log.info(
            "Reflection ran on %d of %d finished conversations; the rest wait "
            "for the next tick",
            ran,
            len(candidates),
        )
    return ran


async def _reflection_job(context) -> None:  # pragma: no cover - PTB plumbing
    try:
        await sweep()
    except Exception:  # noqa: BLE001 - a sweep must never take the bot down
        log.debug("Reflection sweep job failed", exc_info=True)


def register_jobs(application) -> None:
    """Register the pass beside the sharing sweep, the house pattern.

    Registered unconditionally and free on an install with nothing to read: the
    first thing :func:`sweep` does is walk the stores, and a conversation still
    being used, already reflected or a single turn long produces no candidate.

    ``first`` is a long way out for the same reason it is there: a conversation
    has to be idle for :data:`IDLE_S` to qualify at all, so a sweep at boot can
    find nothing a sweep a few minutes later will miss — and the first minutes
    after a restart are when the rest of the runtime is reconciling.
    """
    try:
        queue = getattr(application, "job_queue", None)
        if queue is None:
            return
        for job in queue.get_jobs_by_name(REFLECTION_JOB):
            job.schedule_removal()
        queue.run_repeating(
            _reflection_job, interval=SWEEP_INTERVAL_S, first=300, name=REFLECTION_JOB
        )
    except Exception:  # noqa: BLE001
        log.debug("Could not register the reflection sweep job", exc_info=True)
