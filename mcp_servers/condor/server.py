"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Thin wrapper layer: tool registration + docstrings only.
All business logic lives in mcp_servers.condor.tools.*
"""

from collections.abc import Iterable
from typing import Any

from mcp.server.fastmcp import FastMCP

from condor.telemetry import taps as telemetry_taps
from mcp_servers._profiles import make_resolver
from mcp_servers._profiles import register_tools as _register_tools
from mcp_servers._profiles import resolve_profiles
from mcp_servers.condor.middleware import handle_errors
from mcp_servers.condor.profiles import PROFILE_TOOLS
from mcp_servers.condor.settings import DEFAULT_TOOL_PROFILE, settings
from mcp_servers.condor.tools import available_models as available_models_tool
from mcp_servers.condor.tools import code as code_tool
from mcp_servers.condor.tools import consult as consult_tool
from mcp_servers.condor.tools import delegate as delegate_tool
from mcp_servers.condor.tools import (
    memory,
    notification,
    routines,
    servers,
    skills,
    trading_agent,
)

# FEAT-047: the branch above the routines one in cost — a one-off computation is
# a snippet, not a file. Identical for every seat (chat, worker, agent), so it is
# defined once and spliced into all three routing texts.
_RUN_CODE_RULE = (
    "- FOR A ONE-OFF COMPUTATION OVER DATA — candles → returns → filter, a "
    "spread across venues, a quick aggregation — write it as Python and call "
    '`run_code(code="...")` instead of chaining raw tools and doing the '
    "arithmetic by hand. It runs in the bot with the same primitives a routine "
    "has (`context`, `client`, pandas, `ReportBuilder`, every `condor.*` "
    "module); `print()` is the output and an optional `result` variable is the "
    "return value. On failure it returns the traceback — fix the snippet and "
    "re-run. Keep snippets short and `await` inside loops. If you have run "
    "essentially the same snippet three times, or you want it scheduled or "
    "shared, promote it to a routine with "
    '`manage_routines(action="create_routine")`.\n'
)

_CHAT_ROUTINES_RULE = (
    "- ROUTINES ARE SPECIAL: any request to CREATE, EDIT, FIX, DEBUG, or "
    "design a routine MUST go to a background Condor worker — "
    '`delegate(action="start", agent="condor", task="build a routine that …")`. '
    "It returns a task_id immediately (you are NOT blocked), reads the "
    "`routine_cookbook` playbook, writes and TESTS the routine, and pings the "
    "user with the result. Tell the user it is running in the background. Do NOT "
    "write routine code yourself and do NOT hand-roll it with raw "
    "`manage_routines` create_routine/edit_routine. (RUNNING an existing routine "
    'is not authoring — for that just call `manage_routines(action="run", '
    'name="...")`.)\n'
)

# The worker IS Condor — same agent record, same tools — so the routing text is
# the coordinator's, with exactly one branch swapped: authoring is the reason it
# was started, so it does the work instead of handing it on (FEAT-032).
_WORKER_ROUTINES_RULE = (
    "- ROUTINE AUTHORING IS YOURS: creating, editing, fixing and debugging "
    "routines is the work you were started for — do NOT hand it to anyone else. "
    'Read `manage_skill(action="read", name="routine_cookbook")` FIRST (and the '
    "companion file for what the routine does), create it into the GLOBAL "
    'routine library with `manage_routines(action="create_routine", name="...", '
    'code="...")` — no `agent`, so the user and the chat can see it — then '
    'TEST it with `manage_routines(action="run", name="...")` and fix it until '
    "the output is clean BEFORE reporting. Reporting an untested routine is a "
    "failed delegation. (RUNNING an existing routine is not authoring — for that "
    'just call `manage_routines(action="run", name="...")`.)\n'
)


def _chat_base() -> str:
    """Routing rules for the Condor coordinator — the unbound chat assistant."""
    return _coordinator_base(_CHAT_ROUTINES_RULE)


def _worker_base() -> str:
    """Routing rules for a BACKGROUND Condor worker (``--delegate-worker``).

    The chat and the worker resolve the same agent record (FEAT-033), so this is
    ``_chat_base()`` with the routines branch swapped for "authoring is yours",
    plus the framing that makes an unattended session behave: it has no user to
    ask, and it must not fan out into more delegations. The no-delegation line is
    a courtesy — the actual stop is in ``tools/delegate.py``, because a prompt is
    not a guard.
    """
    return (
        "You are a BACKGROUND WORKER instance of Condor: a detached session that "
        "`delegate` started to carry ONE task to completion, unattended. There is "
        "no user in the loop to ask — make the reasonable call, do the work "
        "yourself, and report what you actually did and verified.\n"
        "You must NEVER start another delegation: "
        '`delegate(action="start", ...)` is refused for you in code. Finish the '
        "task in this session (polling with "
        '`delegate(action="get"/"list")` is fine).\n\n'
    ) + _coordinator_base(_WORKER_ROUTINES_RULE)


def _coordinator_base(routines_rule: str) -> str:
    """The coordinator routing text, parameterized on its ROUTINES branch."""
    return (
        "Condor exposes reusable **skills** (playbooks, some linked to a runnable "
        "routine) and consultable **domain agents** on top of these tools.\n\n"
        "ROUTING RULE — before handling a request with raw tools (including tools "
        "from other connected MCP servers such as mcp-hummingbot), apply this "
        "priority: (1) a matching SKILL, (2) a matching AGENT, (3) raw tools only "
        "if neither matches:\n"
        '- If a SKILL matches, call `manage_skill(action="read", name="<name>")` '
        'and follow its steps. When it links a routine (shown as "→ routine: X"), '
        'run that routine via `manage_routines(action="run", name="X", config={})` '
        "instead of reimplementing it by hand.\n"
        "- If a domain AGENT matches, delegate with "
        '`consult(agent="<slug>", task="...", context="...")` and summarize its answer. '
        "For a long, one-off task you want run in the background until done, use "
        '`delegate(action="start", agent="<slug>", task="...")` instead. It returns a '
        "task id immediately; say the task is running and END YOUR TURN. When it "
        "finishes, the result is pushed back into this conversation and to the user on "
        "its own -- do NOT poll the task you just started. Reach for "
        '`delegate(action="get"/"list")` only when the user asks about a task later.\n'
        f"{routines_rule}"
        f"{_RUN_CODE_RULE}"
        "- Only fall back to raw tools when nothing matches.\n"
        "Anti-pattern: answering a domain request (deploy/tune an executor, analyze "
        "logs, author a routine) with a chain of raw `mcp-hummingbot`/`manage_*` "
        "calls when a skill or agent covers it.\n"
        'Discover more anytime with `manage_skill(action="list")`.'
    )


def _agent_base(slug: str, name: str, worker: bool = False) -> str:
    """Routing rules for a subprocess launched AS an Agent (``--agent-slug``).

    Same three-tier priority, read from the specialist's seat: its skills are
    its own playbooks (plus the ones Condor publishes), routine authoring is its
    own work, and consulting a PEER is for work outside its domain.

    An agent may also start a fresh BACKGROUND session of *itself* (FEAT-041).
    That is not handing the work to someone else — the copy carries the same
    identity, playbooks and memory — so it is the one form of delegation that
    stays inside the agent's own domain, and the right move for a long
    mechanical job (a sweep, a wide scan) that would otherwise fill the
    interactive conversation.

    ``worker`` marks that background seat. It reads the same identity and the
    same playbooks; what changes is that it has no user to ask and must not
    spawn a copy of itself in turn. The no-recursion line is a courtesy — the
    actual stop is in ``tools/delegate.py``, because a prompt is not a guard.
    """
    from condor.agents.agent import identity_header

    # FEAT-031: authoring is the agent's own work now that `routine_cookbook` is
    # inherited from Condor's library — the knowledge that used to justify a
    # round-trip to a dedicated builder agent travels with the agent itself. The
    # chat reaches the same playbook the other way, through a background worker
    # (FEAT-032), so nothing routes to a builder any more.
    routines_rule = (
        "- ROUTINE AUTHORING IS YOURS: creating, editing, fixing and debugging "
        "your own routines is your work — do NOT hand it to another agent. Read "
        '`manage_skill(action="read", name="routine_cookbook")` FIRST (and the '
        "companion file for what the routine does), create it into your own dir "
        'with `manage_routines(action="create_routine", name="...", code="...")`, '
        'then TEST it with `manage_routines(action="run", name="...")` and fix '
        "until the output is clean before reporting.\n"
    )
    # FEAT-041: the same agent, in a second seat. Named explicitly with the
    # agent's own slug because it is NOT in the [PEER AGENTS] index — an agent is
    # not its own peer, and without this line the roster reads as "everyone but
    # you", which is exactly how it used to refuse.
    self_delegation_rule = (
        "- SPAWN A BACKGROUND COPY OF YOURSELF for long work that is YOURS: "
        f'`delegate(action="start", agent="{slug}", task="...")` starts a fresh '
        "session of you — same identity, same playbooks, same memory — that runs "
        "unattended until done and pings the user. Reach for it when the work is "
        "long or mechanical (a parameter sweep, a wide scan, a batch of runs) and "
        "would otherwise fill this conversation. This is NOT handing the work to "
        "another agent and it is NOT outside your domain: it is you, working in "
        'the background. Add `on_complete="resume"` to be handed the result in a '
        "new turn here — then end your turn and continue when it arrives.\n"
    )
    worker_framing = (
        f"You are a BACKGROUND WORKER instance of {name}: a detached session that "
        "`delegate` started to carry ONE task to completion, unattended. There is "
        "no user in the loop to ask — make the reasonable call, do the work "
        "yourself, and report what you actually did and verified.\n"
        "You must NEVER spawn another copy of yourself: "
        f'`delegate(action="start", agent="{slug}", ...)` is refused for you in '
        "code. Finish the task in this session (polling with "
        '`delegate(action="get"/"list")` is fine).\n\n'
    )
    return (
        f"{worker_framing if worker else ''}"
        f"{identity_header(slug, name)}\n\n"
        "ROUTING RULE — you are the specialist, so domain work is yours to do. "
        "Before reaching for raw tools (including tools from other connected MCP "
        "servers such as mcp-hummingbot):\n"
        "- Your own SKILLS below are playbooks YOU follow: read one with "
        '`manage_skill(action="read", name="<name>")` and follow its steps. When '
        'it links a routine (shown as "→ routine: X"), run that routine via '
        '`manage_routines(action="run", name="X", config={})` instead of '
        "reimplementing it by hand.\n"
        f"{routines_rule}"
        f"{_RUN_CODE_RULE}"
        f"{'' if worker else self_delegation_rule}"
        "- You MAY consult a PEER agent listed below for work outside your own "
        'domain (`consult(agent="<slug>", task="...", context="...")`, or '
        '`delegate(action="start", agent="<slug>", task="...")` for a long '
        "background task). Say plainly that you are handing it over rather than "
        "answering outside your competence.\n"
        "- Only fall back to raw tools when nothing matches.\n"
        'Discover your own playbooks anytime with `manage_skill(action="list")`.'
    )


def _build_instructions() -> str:
    """Server-level instructions surfaced to the MCP host on connect.

    An external MCP client (Claude Code, Cursor, …) only receives a flat list of
    tool names — it never sees Condor's skills/agents indexes, which are injected
    only into the in-bot `/agent` brain prompt. Without this, the host reaches for
    whatever obvious tool is in scope (e.g. a raw `manage_bots`) instead of the
    matching Condor playbook. We embed the live indexes here so any host can route
    a request to the right skill/agent. Built once at import; cheap and read-only.

    One rule governs all three sections: **this text describes the assistant this
    subprocess belongs to**. Under ACP v1 there is no system-prompt channel
    (`session/new` carries only cwd + mcpServers), so these instructions are the
    strongest frame Condor controls — which is why a subprocess launched with
    ``--agent-slug`` must read its own identity here and not the coordinator's
    (FEAT-025).

    The chat's own subprocess now carries ``--agent-slug condor`` (FEAT-033), so
    the branch reads ``specialist_slug``: keyed on the raw slug it would serve
    Condor the specialist framing — including ``identity_header``'s "You are NOT
    Condor", which would be false — and nothing would error, the answers would
    just quietly get worse.

    A third seat exists for the same record: ``--delegate-worker`` (FEAT-032)
    marks the detached Condor that ``delegate`` starts to author a routine. It
    reads ``_worker_base`` — the coordinator text with authoring made its own job
    and delegation closed off.

    A specialist has that background seat too (FEAT-041), since an agent can now
    start a delegation of *itself*. It reads its own ``_agent_base`` in both
    seats — ``worker=True`` swaps the "spawn a copy of yourself" invitation for
    the unattended framing — never ``_worker_base``, which is Condor's.
    """
    from mcp_servers.condor.settings import settings

    slug = settings.specialist_slug
    agent = None
    if slug:
        try:
            from condor.agents.agent import AgentStore

            agent = AgentStore().get(slug)
        except Exception:
            pass  # Unknown/unreadable slug degrades to the coordinator text.

    if agent:
        # A specialist reads its OWN framing in both seats — never Condor's
        # `_worker_base`, which would tell it it is Condor. The flag only picks
        # which half of `_agent_base` it gets (FEAT-041).
        base = _agent_base(slug, agent.name, worker=settings.delegate_worker)
    elif settings.delegate_worker:
        base = _worker_base()
    else:
        base = _chat_base()
    sections = [base]

    # The house rules, shared with every loop tick (FEAT-095). Directly under the
    # seat's framing and above the indexes, because they govern how the indexes
    # below are used. Built once at import like the rest of this text, so an edit
    # to core_rules.md reaches this surface on the next server start (ticks get it
    # immediately).
    try:
        from condor.agents.prompts import core_rules_section

        core_rules = core_rules_section(slug or None)
        if core_rules:
            sections.append(core_rules)
    except Exception:
        pass  # Advisory — never block server startup on the rulebook.

    try:
        from condor.memory import SkillStore

        # Scope to the launched assistant: an agent subprocess (--agent-slug) must
        # advertise ITS OWN skills here, not the chat condor's global library.
        skills_index = SkillStore(slug or None).list_index()
        if skills_index:
            sections.append(
                "[SKILLS — read the playbook before a matching flow]\n" + skills_index
            )
    except Exception:
        pass  # Advisory — never block server startup on index assembly.
    try:
        from condor.agents.agent import AgentStore
        from condor.memory.paths import CHAT_SLUG

        # Never a peer of itself (the bug this fixes), and never a peer of the
        # coordinator: Condor is in the registry now (FEAT-033), and a
        # specialist offered it could consult back into the chat.
        exclude = {CHAT_SLUG} | ({agent.slug} if agent else set())
        agents_index = AgentStore().list_index(exclude=exclude)
        if agents_index:
            header = (
                "[PEER AGENTS — consult for work outside your domain]"
                if agent
                else "[AGENTS — consult for domain work]"
            )
            sections.append(f"{header}\n{agents_index}")
    except Exception:
        pass

    return "\n\n".join(sections)


mcp = FastMCP("condor", instructions=_build_instructions())


@handle_errors("consult agent")
@telemetry_taps.tracked("consult")
async def consult(agent: str, task: str, context: str = "") -> dict:
    """Consult a specialized domain agent and get its answer.

    Use this to delegate domain work instead of doing it yourself: the agent runs
    with its own focused tools and domain memory, then returns an answer you can
    summarize for the user. Available agents are listed in your [AGENTS] section.
    The agent may execute actions (gated by the user's confirmation).

    Args:
        agent: Agent slug (e.g. "executor_manager").
        task: The question or task for the agent, in plain language.
        context: Optional extra context (relevant numbers, the user's intent).

    Returns:
        {"agent": "...", "answer": "..."} or {"error": "..."}.
    """
    return await consult_tool.consult(agent, task, context)


@handle_errors("delegate task")
@telemetry_taps.tracked("delegate")
async def delegate(
    action: str,
    agent: str = "",
    task: str = "",
    task_id: str = "",
    on_complete: str = "notify",
    timeout_sec: int = 0,
) -> dict:
    """Delegate a one-off task to a background agent instance.

    DELEGATE is the async, unattended sibling of CONSULT. Where ``consult`` blocks
    and returns an answer now (mutations human-gated), ``delegate`` hands a
    goal-oriented task to a DETACHED agent that works autonomously until done, then
    notifies the user with the result — while you stay free to do other things. Use
    it for "go build/scan/produce X and ping me when finished" (e.g. "create a
    routine that scans SOL pools"). The agent runs unrestricted with full
    auto-approve, so delegate only to trusted agents/tasks.

    The user tracks a delegation on whichever surface they are on — the
    /delegations command in Telegram, the Tasks list in the chat's context dock
    on the dashboard — and is pinged automatically when it finishes. Never invent
    a status command (there is no "/task", and /delegations exists only in
    Telegram); "start" returns a next_steps hint already worded for this session's
    surface, so relay that rather than guessing.

    Actions:
    - "start": Begin a delegation (requires agent, task). Returns immediately with
      {"task_id", "status": "running", "next_steps"} — does NOT wait for completion.
    - "list": List in-flight/finished delegations (task_id, agent, status).
    - "get": Get a delegation's status + result/error (requires task_id).
    - "stop": Cancel a running delegation (requires task_id).

    An agent may pass its OWN slug to start a background copy of itself (FEAT-041)
    — same identity, playbooks and memory, running unattended on a long job while
    the interactive session stays responsive. That copy cannot spawn a further copy
    of itself; the recursion stops at depth one.

    Args:
        action: start | list | get | stop.
        agent: Agent slug to delegate to (for start). Your own slug is allowed and
            means "a background session of me".
        task: The one-off task, in plain language (for start).
        task_id: Delegation id returned by start (for get/stop).
        on_complete: What this conversation gets when the task ends (for start).
            "notify" (default) pings the user with the result AND writes the
            outcome into THIS conversation — recorded in the transcript and shown
            in the chat as it lands, so an already-open session sees it without a
            reload. What it does not do is give you a turn: the result is there
            for the human to read, not handed back to you. So never tell the user
            the result will not reach the chat. "resume" additionally hands the
            result back to you in a new turn of THIS conversation, so use it when
            you intend to do something with the result yourself ("research X,
            then draft the summary"). With "resume" you must end your turn after
            starting the task: you will be woken with the answer.
        timeout_sec: Wall-clock budget for the whole background task, in seconds
            (for start). Omit it (0) for the default of 900s. Raise it when you
            KNOW the job is long — several routines to build, a research sweep,
            a multi-step backtest — because a task that outlives its budget is
            cut off mid-run and loses whatever it had not finished. The ceiling
            is 1800s: an agent session has its own ~31-minute hard stop, so
            asking for more only delays the same cut-off. For work bigger than
            that, split it across delegations.

    Returns:
        Action-specific result dict.
    """
    return await delegate_tool.delegate(
        action, agent, task, task_id, on_complete, timeout_sec
    )


@handle_errors("send notification")
@telemetry_taps.tracked("send_notification")
async def send_notification(
    text: str,
    parse_mode: str = "Markdown",
) -> dict:
    """Send a Telegram message to the user.

    Args:
        text: Message text to send.
        parse_mode: Telegram parse mode ("Markdown" or "HTML"). Default: "Markdown".

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    return await notification.send_notification(text, parse_mode)


@handle_errors("manage routines")
@telemetry_taps.tracked("manage_routines")
async def manage_routines(
    action: str,
    name: str | None = None,
    config: dict | None = None,
    agent: str | None = None,
    code: str | None = None,
    shared: bool | None = None,
) -> dict:
    """Manage and run Condor routines (auto-discoverable Python scripts).

    Actions -- Discovery & Execution:
    - "list": List all available routines with name, description, type, and scope
    - "describe": Show config schema for a routine (requires name)
    - "run": Execute a one-shot routine and WAIT for its result (requires name, optional config).
      Blocks for up to 120s, so use it only for routines that finish fast.
    - "run_async": Submit a one-shot routine and return its instance_id immediately,
      without waiting (requires name, optional config). Use this for slow work — a
      backtest, a wide scan. The finished run is handed back to you automatically as
      a new turn, so say it is running and END YOUR TURN. NEVER poll it in a loop.
    - "get_instance": Read a run back by id (requires name=instance_id). For a user
      asking about a run later — not for waiting on one you just started.
    - "start": Start a continuous routine as a background task (requires name, optional config)
    - "stop": Stop a running routine instance (requires name=instance_id)
    - "list_instances": List all running/scheduled routine instances

    Actions -- Agent-Local Routine CRUD (requires agent or CONDOR_AGENT_SLUG):
    - "create_routine": Create a new agent-local routine (requires name, code)
    - "read_routine": Read source code of a routine (requires name)
    - "edit_routine": Update an agent-local routine (requires name, code)
    - "delete_routine": Delete an agent-local routine (requires name)

    Agent-local routines live in agents/{slug}/routines/ and are visible only to
    that AGENT — they are shared across all of its strategies, there is no
    per-strategy routine library. They follow the same pattern as global
    routines: a Config(BaseModel) class and an async run(config, context) function.

    Beside those per-agent libraries there is ONE shared library
    (agents/_shared/routines) that every assistant also reads, mirroring
    manage_skill's `shared`. A routine there is listed with scope="shared" and
    runs under its bare name from any seat. Publication is the library it lives
    in, not a flag in the file: `shared=True` on "create_routine" writes there.
    Only Condor may publish — for an agent the flag is ignored and the routine
    lands in its own dir. Shared routines are read-only to agents: to specialize
    one, "create_routine" a local routine with the SAME name and it shadows the
    published one. Publish deliberately — a shared routine lands in every
    agent's context, and it must work with no chat (no chat_id).

    Args:
        action: The action to perform.
        name: Routine name (required for all except list/list_instances). For "stop"
            and "get_instance", pass the instance_id as name.
        config: Config overrides for run/start (optional, merged with defaults).
        agent: Slug of the agent whose routine library to target (agent-local
            CRUD, and "list"/"run"/"start" against another agent's routines).
            A run started with it is attributed to that agent. Omit to use
            the current assistant's own library.
        code: Python source code for create_routine / edit_routine.
        shared: Target the shared library every assistant reads (routine CRUD).
            Condor only, and only without `agent` — for an agent it is ignored
            and the write stays in its own dir.

    Returns:
        Action-specific result dict.
    """
    return await routines.manage_routines(action, name, config, agent, code, shared)


@handle_errors("run code")
@telemetry_taps.tracked("run_code")
async def run_code(
    code: str | None = None,
    action: str = "run",
    label: str = "",
    timeout: int | None = None,
    run_id: str | None = None,
    agent: str | None = None,
    limit: int = 20,
) -> dict:
    """Run a Python snippet inside Condor and get its output back.

    The scratchpad below a routine: for a ONE-OFF computation over data — fetch
    candles, build a DataFrame, compute returns, filter the series — write the
    Python instead of chaining raw tools and doing the arithmetic by hand. There
    is no file to author and no config schema to declare.

    The snippet is a plain script body running in the bot process with the same
    primitives a routine has:
    - top-level `await` works, with no wrapper function
    - `print(...)` is the output (use `print`, not `logging` — logging is not
      captured); an optional `result` variable is the return value
    - `context` is the routine context (active server, bot, chat), `client` is
      the Hummingbot API client for that server (None if none resolves)
    - every `condor.*` module is importable, including
      `from condor.reports import ReportBuilder` — a snippet that saves one
      produces a real dashboard report and its id comes back as `report_id`

    Don't guess an import. `from condor.primitives import catalog, describe,
    call_routine`: `catalog()` lists every primitive and routine this install
    has, `describe(ref)` gives one full signature, docstring or config schema,
    and `await call_routine(name, config)` runs an existing routine inline
    instead of rewriting it (`start_routine` backgrounds it).

    On failure you get `status="error"` and a traceback whose line numbers are
    YOUR snippet's: read it, fix the code, run it again. Keep snippets short and
    `await` inside loops — a snippet that never yields blocks the bot until the
    timeout cuts it.

    Run essentially the same snippet a third time, or want it scheduled, shared
    or visible to the user? Promote it to a routine with
    `manage_routines(action="create_routine")`. That is the durable artifact;
    this is the scratchpad.

    Actions:
    - "run" (default): execute `code` and wait for it (requires code)
    - "history": list recent runs, newest first, with label and status
    - "get": read one past run back in full — code, stdout, result, traceback
      (requires run_id)

    Args:
        action: run | history | get.
        code: The Python snippet to execute (for "run").
        label: Short purpose of the run ("returns of SOL 1h"), shown in history
            and used as the report source name.
        timeout: Seconds to allow the snippet (for "run"). Default 60, max 120.
        run_id: Id of a past run (for "get").
        agent: Whose runs to list (for "history"). Defaults to your own; pass
            "all" for every assistant's.
        limit: How many runs to list (for "history"). Default 20.

    Returns:
        For "run": {run_id, status: ok|error|timeout, stdout, result, error,
        traceback, report_id, duration_ms}. Long stdout/result are clipped with
        "truncated": true — read the whole run with action="get".
    """
    return await code_tool.run_code(code, action, label, timeout, run_id, agent, limit)


@handle_errors("manage servers")
@telemetry_taps.tracked("manage_servers")
async def manage_servers(
    action: str,
    name: str | None = None,
) -> dict:
    """Manage Hummingbot API servers — and answer where you are pointed, as whom.

    Actions:
    - "list": List all accessible servers with permissions and active status. Also
      returns the caller's context: active_server, user_role, is_admin, plus the
      active_agent_key (the LLM new agents inherit — never invent one) and the
      custom_llm_endpoints the user has saved.
    - "status": Check if a server is online (optional name, defaults to active server)

    Args:
        action: The action to perform (list, status)
        name: Server name (optional for status)

    Returns:
        Action-specific result dict.
    """
    return await servers.manage_servers(action, name)


@handle_errors("get available models")
@telemetry_taps.tracked("get_available_models")
async def get_available_models(
    openrouter_query: str = "", openrouter_limit: int = 20
) -> dict:
    """List every model/provider currently usable for a trading agent's ``agent_key``.

    Use this when helping the user pick a model for an agent they are building
    (agent_builder skill) — recommend from what is ACTUALLY configured, not a
    hardcoded default. Read-only; never returns key values.

    Args:
        openrouter_query: Optional substring to filter the OpenRouter catalog by
            slug or name (e.g. "claude", "deepseek", "free"). Empty = no filter.
        openrouter_limit: Max OpenRouter models to return (default 20, cheapest
            input price first). ``total_matching`` reports how many matched.

    Returns a dict with:
      - acp_clis: subscription/CLI bridges (claude-code, gemini, copilot, …),
        each with ``agent_key``, ``available`` — the CLI is installed and
        launchable — and ``logged_in``: true a credential for its interactive
        login (Claude/Google/GitHub/OpenAI) was found on this machine, false none
        was found where that CLI keeps one, null no marker exists to read
        (Copilot) or the bridge isn't installed. ``available`` alone does NOT
        mean signed in, and ``logged_in`` is a heuristic (a credential file can
        still hold an expired token), so treat a bridge with ``logged_in`` false
        or null as a candidate to CONFIRM with the user, and prefer a credential
        you can verify (a set ``cloud_keys`` provider, or a loaded local model)
        when recommending unprompted.
      - cloud_keys: {provider: bool} — whether OPENROUTER/OPENAI/ANTHROPIC/GROQ/
        GOOGLE keys are set in the environment.
      - custom_endpoints: the user's own saved OpenAI-compatible endpoints, each
        with ``name``, ``base_url``, ``reachable`` and the chat ``models`` it
        serves (``agent_key`` set to ``custom@<endpoint>:<model-id>``). The
        strongest signal here — the user added these deliberately and they are
        re-validated on every call — so prefer a reachable one when it fits.
      - local: {ollama, lmstudio} each with ``base_url``, ``reachable``, and the
        ``models`` currently loaded on that server (empty if not running).
      - openrouter: tool-capable catalog (``models`` with ``agent_key`` set to
        ``openrouter:<slug>``, plus in/out $/Mtok and context). The catalog is
        public so this is ALWAYS present — no key needed to recommend. Check
        ``key_present``: false means these are options that need OPENROUTER_API_KEY
        added (Settings) before they can run; recommend a runnable option first.

    Guidance for choosing: correctness-critical or high-capital agents → a strong
    model (a capable OpenRouter model when its key is set, or a subscription ACP
    bridge the user confirms is signed in); simple
    report/watch loops or privacy/offline needs → a loaded local model or a cheap
    OpenRouter one. Only pydantic-ai keys (openrouter:/ollama:/lmstudio:/openai:/
    groq:) enforce an agent's ``tools`` allowlist; ACP bridges run unrestricted.
    """
    return await available_models_tool.get_available_models(
        openrouter_query, openrouter_limit
    )


@handle_errors("manage agents")
@telemetry_taps.tracked("manage_agents")
async def manage_agents(
    action: str,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    server_required: bool | None = None,
    server_name: str | None = None,
) -> dict:
    """Create and edit agent identities (agents/{slug}/AGENT.md).

    An *agent* (e.g. "executor_manager", "brigado") is an identity — the brain,
    and the primary artifact. It is created FIRST; everything else hangs off its
    slug. From the moment it exists it can be consulted (`consult`), delegated to
    (`delegate`) and looped (`control_agent(action="start")`) — there is no
    capability flag and nothing to enable. See `manage_strategies` for the
    playbooks an agent owns, `control_agent` for its running instances.

    Actions:
    - "list": All agents with their when_to_consult hint, owned strategies,
      agent_key and tools. Use this to answer "what agents exist?" — strategies
      and running instances do NOT show agents that own no loop strategy.
    - "create": New agent (AGENT.md identity + brain). Requires name. Returns
      agent_slug — use it for routines and strategies.
    - "get": Full definition including the AGENT.md body (requires agent_slug).
    - "update": Change AGENT.md / metadata (requires agent_slug + fields to change).
    - "delete": Delete an agent (requires agent_slug; refuses if it still owns
      strategies).

    Args:
        action: One of list, create, get, update, delete.
        agent_slug: The agent to act on (get/update/delete).
        name: Agent name (create/update).
        description: Agent description (create/update).
        instructions: The AGENT.md body — identity + domain knowledge (create/update).
        agent_key: Default LLM. Examples: "claude-code", "gemini", "copilot",
            "ollama:llama3.1", "ollama:qwen3:32b", "groq:llama-3.3-70b-versatile".
            Any model can be consulted; a pydantic-ai key (e.g. "ollama:...")
            additionally enforces the tools allowlist on consult. Default "claude-code".
        tools: Tool-name allowlist for the agent. Empty/None = unrestricted.
        when_to_consult: One-line hint describing when to route work to this agent.
            Purely for routing — every agent is consultable with or without it; it
            falls back to the description.
        server_required: Whether the agent needs a Hummingbot server. Default True.
        server_name: Pin the agent to a specific hummingbot-api server. LEAVE EMPTY
            unless the user explicitly asks to pin this agent to one server — empty
            means follow the ambient chat server, which is what travels to other
            installs. When set, the agent's mcp-hummingbot subprocess and any
            strategy it deploys use THIS server regardless of the chat's active
            server, and on a machine without that server the agent is broken. Do
            not fill it in with whatever server the chat happens to be on.

    Returns:
        Action-specific result dict.
    """
    return trading_agent.manage_agents(
        action,
        agent_slug=agent_slug,
        name=name,
        description=description,
        instructions=instructions,
        agent_key=agent_key,
        tools=tools,
        when_to_consult=when_to_consult,
        server_required=server_required,
        server_name=server_name,
    )


@handle_errors("manage strategies")
@telemetry_taps.tracked("manage_strategies")
async def manage_strategies(
    action: str,
    strategy_id: str | None = None,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    skills: list[str] | None = None,
    config: dict | None = None,
) -> dict:
    """Create and edit strategies — the looping playbooks an agent owns.

    A *strategy* is a sub-resource of an agent: the instructions one loop follows,
    plus its default config. It is an optimization over the default playbook every
    agent already loops, so an agent needs no strategy to run. ``strategy_id`` is
    the opaque key returned by list/create, of the form "agent_slug.strategy_slug"
    — just pass it back. Create the owning agent first (`manage_agents`); start a
    strategy with `control_agent(action="start")`.

    Actions:
    - "list": All strategies across all agents.
    - "get": Full details including the instructions body (requires strategy_id).
    - "create": New strategy under an agent (requires agent_slug, name, instructions).
    - "update": Change an existing strategy (requires strategy_id + fields to change).
    - "delete": Delete a strategy (requires strategy_id).

    Args:
        action: One of list, get, create, update, delete.
        strategy_id: Strategy key "agent_slug.strategy_slug" (get/update/delete).
        agent_slug: The owning agent — required to create.
        name: Strategy name (create/update).
        description: Strategy description (create/update).
        instructions: The playbook text the loop follows (create/update).
        agent_key: Default LLM for this strategy's loop, overriding the owning
            agent's. Same form as manage_agents.agent_key.
        skills: Optional skill names to enable for the loop (create/update).
        config: Default config for the loop (create/update) — the same keys
            control_agent(action="start") accepts as overrides.

    Returns:
        Action-specific result dict.
    """
    return trading_agent.manage_strategies(
        action,
        strategy_id=strategy_id,
        agent_slug=agent_slug,
        name=name,
        description=description,
        instructions=instructions,
        agent_key=agent_key,
        skills=skills,
        config=config,
    )


@handle_errors("control agent")
@telemetry_taps.tracked("control_agent")
async def control_agent(
    action: str,
    agent_id: str | None = None,
    strategy_id: str | None = None,
    config: dict | None = None,
    key: str | None = None,
    value: Any = None,
    expires_in: int | None = None,
    clear: bool = False,
) -> dict:
    """Run and steer live agent instances (start / stop / pause / resume).

    An *instance* is one running session of an agent's loop, identified by
    ``agent_id``. Start one from a strategy — or from a bare agent slug, since
    every agent is loopable. See `manage_agents` for the identities and
    `manage_strategies` for the playbooks; read what a running instance did with
    `trading_agent_journal_read`.

    STOP vs SHUTDOWN: "stop" halts the loop and KEEPS its open positions.
    "shutdown" is the emergency exit — it winds down this session's positions and
    executors per the agent's shutdown.md policy (closes perp, keeps spot by
    default). Reach for "stop" unless the user wants to be out of the market.

    Actions:
    - "list": All running instances with status.
    - "start": Start a session (requires strategy_id, optional config overrides).
      strategy_id may be a BARE AGENT SLUG — the agent then loops its only
      strategy, or a default playbook created from its identity on first start.
    - "stop": Stop a running instance, keeping positions (requires agent_id).
    - "shutdown": Stop AND wind down positions/executors (requires agent_id).
    - "pause": Pause a running instance (requires agent_id).
    - "resume": Resume a paused instance (requires agent_id).
    - "get_state" / "set_state": Read/write the instance's own scratch state
      (requires agent_id; set_state also requires key). State is for cursors and
      counters a loop would otherwise re-derive every tick — anything worth
      *remembering* belongs in `manage_memory`. The namespace is derived from
      agent_id, so an instance only ever sees its own.

    Args:
        action: One of list, start, stop, shutdown, pause, resume, get_state, set_state.
        agent_id: The running instance (everything except list and start).
        strategy_id: Strategy key "agent_slug.strategy_slug", or a bare agent slug
            (start only).
        config: Overrides on the strategy's defaults, for "start" only: agent_key,
            model_base_url (for LM Studio/vLLM), execution_mode, frequency_sec,
            tick_timeout_sec (wall-clock budget for one tick's agent session;
            0 = runtime default of 600s), total_amount_quote, trading_context,
            risk_limits, server_name, max_ticks.
        key: State key. get_state reads one key, or the whole state if omitted;
            set_state requires it.
        value: JSON-serializable value to store (set_state).
        expires_in: TTL in seconds for the stored value (set_state); None = no expiry.
        clear: Delete ``key`` instead of writing it (set_state).

    Returns:
        Action-specific result dict.
    """
    return await trading_agent.control_agent(
        action,
        agent_id=agent_id,
        strategy_id=strategy_id,
        config=config,
        key=key,
        value=value,
        expires_in=expires_in,
        clear=clear,
    )


@handle_errors("manage memory")
@telemetry_taps.tracked("manage_memory")
async def manage_memory(
    action: str,
    name: str | None = None,
    content: str | None = None,
    description: str | None = None,
    type: str = "fact",
    query: str | None = None,
    max_entries: int = 30,
) -> dict:
    """Manage your persistent memory ABOUT THE USER (yours alone, across sessions).

    This is what YOU remember about the user: their preferences, stable facts,
    feedback they gave you, and reference pointers. It persists across sessions
    and is keyed by (assistant, user) — so each agent has its OWN store: what the
    chat writes is invisible to the trading agents and vice versa. Write what
    matters for your own work; do not assume another agent will see it. (Skills,
    by contrast, CAN be published across agents — see manage_skill's `shared`.)
    The index of your memories is auto-injected into your context as
    [USER MEMORY]; use "read" to pull the full body of a specific memory.

    WHEN TO WRITE:
    - Save something only when it is NEW and STABLE about the user — a standing
      preference ("always report in USD"), a fact ("default exchange is Binance"),
      a correction the user made, or a reference pointer. Do NOT save ephemeral
      conversation details. One memory = one fact. Keep `description` to one line.

    Actions:
    - "write": Create/overwrite a memory (requires name, content, description; optional type).
    - "read": Get the full body of a memory (requires name).
    - "search": Keyword search over your memories (requires query).
    - "list": Return the memory index (one line per memory).
    - "delete": Remove a memory (requires name).
    - "audit": Recent write/delete events (who changed what).

    Args:
        action: write | read | search | list | delete | audit
        name: Short kebab/snake name for the memory (e.g. "report-in-usd").
        content: The full fact/body (required for write).
        description: One-line summary shown in the index (required for write).
        type: preference | fact | feedback | reference (default "fact").
        query: Search string (for search).
        max_entries: Cap for search/audit results (default 30).

    Returns:
        Action-specific result dict.
    """
    return await memory.manage_memory(
        action, name, content, description, type, query, max_entries
    )


@handle_errors("manage skill")
@telemetry_taps.tracked("manage_skill")
async def manage_skill(
    action: str,
    name: str | None = None,
    description: str | None = None,
    when_to_use: str | None = None,
    body: str | None = None,
    references_routine: str | None = None,
    query: str | None = None,
    max_entries: int = 30,
    agent: str | None = None,
    file: str | None = None,
    content: str | None = None,
    shared: bool | None = None,
) -> dict:
    """Manage your SKILLS — playbooks (know-how) you can follow and refine.

    A skill is a markdown *playbook*: a reusable procedure with WHEN to apply it
    and the STEPS to take (e.g. "how to open a grid in a band-walk", "checklist
    before raising leverage"). Skills are GENERAL to the assistant — a shared
    library, the same for everyone using it — distinct from manage_memory, which
    is what YOU learn about a specific USER (per-user). The skills index is
    auto-injected as [SKILLS]; use "read" to pull a full playbook before following
    it, and "create"/"edit" to capture or improve a reusable procedure.

    A skill can REFERENCE a routine: "read" reports `routine_ok` — if false, the
    referenced routine no longer exists; do NOT invoke it. A playbook is advisory
    text; executing what it describes (a routine, an executor) still goes through
    the normal risk/confirmation controls. The skill is NOT a bypass.

    A skill can also BUNDLE companion files (e.g. config templates) beside its
    playbook. "read" lists them under `files`; pull one on demand with
    "read_file" (name + file). This is progressive disclosure — the index shows
    only the playbook, the companions stay out of context until you ask for one.
    Author or update a companion with "write_file" (name + file + content):
    prefer it over a raw filesystem write so the path resolves through the skill
    slug and stays inside the skill folder. ("write_file" only touches companion
    files — edit the playbook body itself with "edit".)

    Skills are scoped PER-AGENT (one library per agent, shared by everyone using
    it; there is no per-strategy library). A launched agent WRITES only to its
    own. From the chat, pass `agent="<slug>"` to author or inspect a specific
    agent's library — DO THIS whenever the playbook belongs to a domain agent
    rather than to the chat, otherwise the skill silently lands in Condor's own
    library and only the chat sees it. Without `agent`, the current assistant's
    library is used.

    Beside those per-agent libraries there is ONE shared library that every
    assistant also reads, so a playbook like `routine_cookbook` reaches everyone.
    Publication is the library a skill lives in, not a flag: `shared=True` MOVES
    it there (companion files included) and `shared=False` moves it back. Only
    Condor may publish — for an agent the flag is ignored. Shared skills are
    read-only to agents (read reports `inherited`): to specialize one, "create" a
    local skill with the SAME name and it shadows the published one. Publish
    deliberately — a shared skill lands in every agent's context.

    Actions:
    - "read": Get a full playbook + routine validation + companion `files` (requires name).
    - "read_file": Get the contents of one bundled companion file (requires name + file).
    - "write_file": Create/overwrite one bundled companion file (requires name + file + content).
    - "search": Keyword search over the skills (requires query).
    - "list": Return the skills index (one line per skill).
    - "create": Add/overwrite a skill (requires name, description, when_to_use, body).
    - "edit": Patch fields of a skill (requires name + any of description/when_to_use/body/references_routine).
    - "delete": Remove a skill (requires name).

    Args:
        action: read | read_file | write_file | search | list | create | edit | delete
        name: Short kebab/snake name (e.g. "grid-en-band-walk").
        description: One-line summary (create/edit).
        when_to_use: The trigger/condition for the playbook (create/edit).
        body: The steps / playbook text (create/edit).
        references_routine: Optional routine name to link; "" clears it (create/edit).
        query: Search string (for search).
        max_entries: Cap for search results (default 30).
        agent: Slug of the agent whose skill library to target (chat-side
            authoring). Omit to use the current assistant's own library.
        file: Bare name of a bundled companion file (for read_file/write_file).
        content: Full contents to write to the companion file (for write_file).
        shared: Publish this playbook to every assistant by moving it into the
            shared library (create/edit); False moves it back. Condor only —
            ignored for an agent. Omit to leave publication unchanged.

    Returns:
        Action-specific result dict.
    """
    return await skills.manage_skill(
        action,
        name=name,
        description=description,
        when_to_use=when_to_use,
        body=body,
        references_routine=references_routine,
        query=query,
        max_entries=max_entries,
        agent=agent,
        file=file,
        content=content,
        shared=shared,
    )


# ---------------------------------------------------------------------------
# Trading-agent journal tools — the canonical interface live tick prompts call
# directly (see condor/agents/prompts.py). Kept as dedicated top-level tools
# rather than actions of the agent-management tools so the ergonomic, oft-used
# write path is a single named tool.
# ---------------------------------------------------------------------------


@handle_errors("journal read")
@telemetry_taps.tracked("trading_agent_journal_read")
async def trading_agent_journal_read(
    agent_id: str,
    section: str = "recent",
    max_entries: int = 30,
) -> dict:
    """Read the trading agent's journal.

    Args:
        agent_id: The trading agent instance ID.
        section: What to read:
                 "recent" (last 10 decisions from run snapshots),
                 "learnings" (all learnings, max 20),
                 "summary" (current status one-liner),
                 "state" (alias for summary),
                 "full" (entire journal),
                 "runs" (list recent run snapshots),
                 "run:N" (read specific run snapshot, e.g. "run:3"),
                 "tracker" (full tracker markdown + summary dict: tick history,
                 executor ledger, snapshots).
        max_entries: Max entries for recent/runs (default 30).

    Returns:
        {"content": "<journal text>"}, {"runs": [...]} for runs listing, or
        {"tracker_md": ..., "summary": {...}} for section="tracker".
    """
    return trading_agent.journal_read(agent_id, section, max_entries)


@handle_errors("journal write")
@telemetry_taps.tracked("trading_agent_journal_write")
async def trading_agent_journal_write(
    agent_id: str,
    entry_type: str,
    text: str,
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
    category: str = "",
    section: str = "",
) -> dict:
    """Write to the trading agent's journal. Keep entries SHORT (one line).

    Args:
        agent_id: The trading agent instance ID.
        entry_type: "action", "learning", "state", or "canvas".
            - "action": What you did this tick (auto-trimmed to last 10).
            - "learning": A new insight. Duplicates are auto-filtered. Only write
              if this is genuinely new and not already in learnings (max 20).
            - "state": Overwrite the current state snapshot (e.g. price, position, grids).
            - "canvas": Revise ONE section of your session canvas — the running
              narrative shown to the user in the session report. Requires
              `section`. Only revise a section when it is now wrong; a quiet
              tick needs no canvas call.
        text: The entry content. Keep it to ONE short line (a canvas section may
            be a short paragraph, truncated past ~1200 chars).
        reasoning: One-sentence reasoning (for actions only).
        risk_note: Optional risk note (for actions only).
        tick: Current tick number (for actions and canvas revisions).
        category: Learning category: "market" (observations, patterns, volatility)
            or "execution" (errors, fills, timing). Only used when entry_type="learning".
            Defaults to "market".
        section: Which canvas section to replace — "thesis", "working",
            "changed", or "questions". Only used when entry_type="canvas".

    Returns:
        {"written": true} — or {"skipped": "..."} in dry-run / run-once mode,
        which keep no journal.
    """
    return trading_agent.journal_write(
        agent_id,
        entry_type,
        text,
        reasoning,
        risk_note,
        tick,
        category,
        section,
    )


# ── Tool profiles (FEAT-066) ─────────────────────────────────────────────────
#
# Tool allowlists are only enforced for pydantic-ai model keys; an ACP bridge
# (claude-code, gemini, copilot) runs unrestricted. For those seats the surface a
# session MOUNTS is the whole permission model, so which tools this process
# registers is a security boundary — hence explicit registration below instead of
# an ``@mcp.tool()`` decorator that fires for every seat at import.
#
# The rings themselves — which tool sits in which one, and why — moved to
# ``profiles.py`` as plain name strings (FEAT-091), because the web process has
# to read them to draw a switch per tool and cannot import *this* module to ask:
# importing it parses argv and builds the ``FastMCP`` singleton. Here the names
# are resolved back into functions, at import, which is what keeps the table and
# the functions provably in step.


#: The tool function a name in ``profiles.PROFILE_TOOLS`` refers to, looked up
#: in *this* module — or a loud failure at import (ARCH-289: the mechanics are
#: shared with the hummingbot server, the namespace is not).
_resolve = make_resolver(globals())

#: profile name → the tools it registers, resolved from ``profiles.PROFILE_TOOLS``
#: (which carries the prose on what each ring is for).
TOOL_PROFILES: dict[str, tuple] = resolve_profiles(globals(), PROFILE_TOOLS)


def register_tools(
    server: FastMCP,
    profile: str = DEFAULT_TOOL_PROFILE,
    muted: Iterable[str] = (),
) -> None:
    """Register this profile's tools on ``server``, minus the muted ones.

    This server's rings and its own default; the rules — unknown profile raises
    rather than widening to ``full``, ``muted`` only ever subtracts — live once,
    in ``mcp_servers/_profiles.py``.
    """
    _register_tools(server, TOOL_PROFILES, profile, muted)


# Registration happens at import: ``mcp`` is a module-level singleton and the
# profile and the mute list are resolved from argv at import (settings), so the
# server object is complete for anything that inspects it before startup.
register_tools(mcp, settings.tool_profile, settings.muted_tools)


if __name__ == "__main__":
    # This server runs in its own process, spawned by the agent: a different
    # interpreter, a different heap, and no job_queue. hosted=False makes emit()
    # append to `spool.<pid>.jsonl`, which the host process drains and deletes.
    # Still a no-op unless the install opted in.
    try:
        from condor import telemetry

        telemetry.init(hosted=False)
    except Exception:  # noqa: BLE001 - never block the server on telemetry
        pass

    mcp.run()
