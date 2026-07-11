"""Skill curation — the loop that promotes experience into skills.

Refactor-05 Phase 3. The doing-agent never holds the editing pen: this pass
runs *between* sessions (Condor's sleep-time compute), reads what the agent
captured (learnings + recent session journals), and applies **delta patches**
to the agent's LOCAL skills only, with provenance and a scoped git commit.

Mandates enforced by construction (not just prompt):
- edits go through ``manage_skill(action="patch")`` — the store rejects
  full-body rewrites from this flow's prompt, ambiguous matches, and any
  write to the shared tier;
- provenance (`condor-updated-by` / `condor-changelog`) stamps every patch;
- the pass ends with a git commit scoped to ``agents/{slug}/skills/**`` —
  audit, rollback, and poisoning purge in one mechanism;
- promotion to the shared or host-facing tier is only ever *proposed* in the
  completion notification; the copy happens in the chat with the user.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent

CURATION_TIMEOUT_S = 600

# Auto-trigger preconditions (session-end hook): enough new material to make
# an LLM pass worthwhile, and enough history for the ≥2-session evidence rule.
MIN_UNPROMOTED_LEARNINGS = 3
MIN_TICK_SESSIONS = 2

# How many recent tick sessions the curator gets digests of.
DIGEST_SESSIONS = 5


def count_unpromoted_learnings(agent_dir: Path) -> int:
    """Learning bullets still in the active category sections (not Promoted)."""
    from condor.agents.journal import LEARNING_CATEGORIES

    path = agent_dir / "learnings.md"
    if not path.exists():
        return 0
    text = path.read_text()
    count = 0
    for header in LEARNING_CATEGORIES.values():
        m = re.search(
            rf"^## {re.escape(header)}\n(.*?)(?=^## |\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if m:
            count += sum(1 for l in m.group(1).splitlines() if l.startswith("- "))
    return count


def should_curate(agent_dir: Path) -> bool:
    """Auto-trigger precondition: skip silently when a pass would find nothing."""
    from condor.agents.sessions_index import count_sessions

    if count_unpromoted_learnings(agent_dir) < MIN_UNPROMOTED_LEARNINGS:
        return False
    if count_sessions(agent_dir, kind="tick_loop") < MIN_TICK_SESSIONS:
        return False
    return True


def _sessions_digest(agent_dir: Path) -> str:
    """Compact Summary + Decisions digest of the last N tick sessions."""
    from condor.agents.sessions_index import list_sessions

    parts: list[str] = []
    for s in list_sessions(agent_dir, kind="tick_loop")[:DIGEST_SESSIONS]:
        journal = agent_dir / "sessions" / f"session_{s['number']}" / "journal.md"
        if not journal.exists():
            continue
        text = journal.read_text(errors="replace")
        block = [f"### session_{s['number']} (strategy: {s['strategy'] or '-'}, status: {s['status'] or '-'})"]
        for section in ("Summary", "Decisions"):
            m = re.search(
                rf"^## {section}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
            )
            if m and m.group(1).strip():
                block.append(f"{section}: {m.group(1).strip()[:600]}")
        parts.append("\n".join(block))
    return "\n\n".join(parts) or "(no tick sessions yet)"


def build_curation_prompt(agent, session_id: str, learnings: str, digest: str, skills_index: str) -> str:
    """The curator's marching orders — mandates first, inputs after."""
    return f"""You are {agent.name} ({agent.slug}) in a SKILL CURATION pass — no market
analysis, no trading, no data fetching. Your only job: decide whether the
experience below has earned a place in your skills, and apply it as minimal
delta edits.

CURATION MANDATES (violations will be rejected by the store):
1. Every edit is a PATCH: manage_skill(action="patch", name="<skill>",
   old_string="<exact existing text>", new_string="<replacement>",
   changelog="<one line: what + why>"). Read the skill first
   (manage_skill(action="read", ...)) and copy exact text — old_string must
   match exactly once. NEVER use action="edit" and never rewrite a whole body.
2. Only promote patterns evidenced in AT LEAST TWO sessions below (or one
   session + an existing learning saying the same thing). Single-episode
   observations stay in learnings — do nothing with them.
3. Before adding anything, check the target skill doesn't already say it
   (semantic duplicates included). Prefer refining an existing bullet over
   appending a new one.
4. Your LOCAL skills only. Skills marked shared are read-only for you — if
   an insight belongs there (it would help every agent), do NOT try to write
   it; put it in PROMOTION PROPOSALS instead.
5. You may create a NEW local skill (manage_skill action="create") only when
   a multi-session procedure fits no existing skill. Description must state
   what + when, single line.
6. For every learning you fold into a skill, mark it promoted:
   trading_agent_journal_write(agent_id="{session_id}",
   entry_type="promote_learning", text="<the learning text>").
7. It is a GOOD outcome to change nothing. If the evidence is thin, say so
   and stop.

END your reply with exactly two sections:
CHANGELOG:
- <skill>: <what changed> (or "- none")
PROMOTION PROPOSALS:
- <skill or insight that belongs in the shared tier, and why> (or "- none")

[YOUR SKILLS INDEX]
{skills_index or '(none)'}

[LEARNINGS — the active, unpromoted pool]
{learnings or '(none)'}

[RECENT TICK SESSIONS — digest]
{digest}
"""


def _git_commit_skills(agent_slug: str, session_id: str, changelog: str) -> str:
    """Commit skill changes scoped to this agent's skills dir. Returns summary."""
    rel = f"agents/{agent_slug}/skills"
    try:
        status = subprocess.run(
            ["git", "-C", str(_ROOT), "status", "--porcelain", rel],
            capture_output=True, text=True, timeout=30,
        )
        if not status.stdout.strip():
            return "no skill changes"
        subprocess.run(
            ["git", "-C", str(_ROOT), "add", rel],
            check=True, capture_output=True, timeout=30,
        )
        msg = f"skills({agent_slug}): curation pass {session_id}\n\n{changelog}".strip()
        subprocess.run(
            ["git", "-C", str(_ROOT), "commit", "-m", msg, "--", rel],
            check=True, capture_output=True, timeout=30,
        )
        sha = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return f"committed {sha}"
    except Exception as e:
        log.exception("curation git commit failed for %s", agent_slug)
        return f"GIT COMMIT FAILED: {e}"


def _extract_section(text: str, header: str) -> str:
    m = re.search(rf"^{header}:\s*\n(.*?)(?=^[A-Z ]+:\s*$|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


async def start_skill_curation(
    *,
    agent_slug: str,
    user_id: int,
    chat_id: int,
    trigger: str,
    bot=None,
) -> str:
    """Allocate the curation session and spawn the pass. Returns the session id."""
    from condor.agents.agent import AgentStore
    from condor.agents.journal import allocate_session_dir, write_session_meta

    agent = AgentStore().get(agent_slug)
    if agent is None:
        raise ValueError(f"No agent named '{agent_slug}' exists.")

    num, session_dir = allocate_session_dir(agent.agent_dir)
    session_id = f"{agent_slug}_{num}"
    write_session_meta(
        session_dir,
        {
            "kind": "curation",
            "status": "running",
            "task": f"skill curation ({trigger})",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    asyncio.create_task(
        _run_curation(agent, session_id, session_dir, user_id, chat_id, trigger, bot)
    )
    return session_id


async def _run_curation(agent, session_id, session_dir, user_id, chat_id, trigger, bot):
    from condor.agents.journal import finalize_session_meta, render_transcript
    from condor.agents.policies import AUTO
    from condor.agents.run import run_agent
    from condor.memory import SkillStore

    status, error, answer = "done", "", ""
    commit_note = "no skill changes"
    try:
        agent_dir = agent.agent_dir
        learnings_path = agent_dir / "learnings.md"
        learnings = learnings_path.read_text() if learnings_path.exists() else ""
        prompt = build_curation_prompt(
            agent,
            session_id,
            learnings,
            _sessions_digest(agent_dir),
            SkillStore(agent.slug).list_index(),
        )

        # Curation touches only manage_skill / journal tools; the SkillStore
        # tier guard is the enforcement, so AUTO is safe here.
        result = await run_agent(
            agent,
            prompt,
            permission_policy=AUTO,
            user_id=user_id,
            chat_id=chat_id,
            server_name=None,
            timeout_s=CURATION_TIMEOUT_S,
            fallback_on_unhealthy=True,
        )
        answer = result.fallback_note + (result.text or "")
        if result.timed_out:
            status, error = "error", result.error

        (session_dir / "transcript.md").write_text(
            f"# Curation {session_id} ({trigger})\n\n"
            f"## Session\n\n{render_transcript(result.events) or '(no events)'}\n\n"
            f"## Answer\n\n{answer or '(none)'}\n"
        )
        commit_note = _git_commit_skills(agent.slug, session_id, _extract_section(answer, "CHANGELOG"))
    except Exception as e:  # noqa: BLE001 — surface as session error
        status, error = "error", str(e)
        log.exception("Skill curation %s failed", session_id)
    finally:
        try:
            finalize_session_meta(
                session_dir, status, **({"error": error} if error else {})
            )
        except Exception:
            log.exception("Failed to finalize curation meta %s", session_id)

    # Notify: changelog + promotion proposals (the human gate's doorbell).
    try:
        changelog = _extract_section(answer, "CHANGELOG") or "- none"
        proposals = _extract_section(answer, "PROMOTION PROPOSALS") or "- none"
        text = (
            f"🧠 Skill curation {session_id} {status} ({trigger}; {commit_note})\n\n"
            f"Changes:\n{changelog}\n\nPromotion proposals (need your OK):\n{proposals}"
        )
        if error:
            text = f"🧠 Skill curation {session_id} failed: {error}"
        await _notify(chat_id, text, bot)
    except Exception:
        log.exception("Failed to notify curation %s", session_id)


async def _notify(chat_id: int, text: str, bot=None) -> None:
    if not chat_id:
        return
    target = bot
    if target is None:
        try:
            from condor.routine_store import get_routine_store

            target = get_routine_store().get_bot()
        except Exception:
            target = None
    if target is None:
        from condor.routine_store import _HttpBot

        target = _HttpBot()
    await target.send_message(chat_id=chat_id, text=text)
