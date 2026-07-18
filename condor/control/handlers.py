"""Control-server method handlers.

Maps JSON-RPC method names to the transport-agnostic executor ops over the live,
in-process runtime singleton. This is the seam that will let the daemon expose
the runtime once the web REST app is retired.
"""

from __future__ import annotations

from condor.control.server import Handler


def build_executor_handlers() -> dict[str, Handler]:
    from condor.executors import ops
    from condor.executors.capabilities import get_capability_registry
    from condor.executors.service import get_executor_runtime

    def _rt():
        return get_executor_runtime()

    def _direct_register(
        token: str, _connection_id: str = "", session_capability: str = ""
    ):
        """Exchange the 0600 direct token for a condor-direct capability bound
        to THIS persistent connection (§6.2 bootstrap)."""
        cap = get_capability_registry().register_direct(
            token, _connection_id, session_capability=session_capability
        )
        return {"capability": cap.id}

    return {
        "ping": lambda: {"ok": True},
        "executor.create": lambda **kw: ops.create(_rt(), **kw),
        "executor.stop": lambda **kw: ops.stop(_rt(), **kw),
        "executor.get": lambda executor_id: ops.get(_rt(), executor_id),
        "executor.list": lambda **kw: ops.list_(_rt(), **kw),
        "executor.performance": lambda **kw: ops.performance(_rt(), **kw),
        "executor.snapshot": lambda **kw: _snapshot(**kw),
        # §6.2 stop hierarchy: executor.stop (one), stop_run (every executor
        # attributed to a run), stop_agent (every executor of the slug).
        "executor.stop_run": lambda agent_id, keep_position=True: {
            "stopped": _rt().stop_agent_executors(agent_id, keep_position=keep_position)
        },
        "executor.stop_agent": lambda agent_slug, keep_position=True: {
            "stopped": _rt().stop_slug_executors(
                agent_slug, keep_position=keep_position
            )
        },
        "direct.register": _direct_register,
    }


def _snapshot(agent_slug=None, agent_id=None, venue_id=None, account=None):
    from condor.executors.service import get_executor_runtime
    from condor.executors.snapshot import snapshot
    from condor.executors.wallets import account_store

    ref = account_store().resolve(venue_id, account) if venue_id else None
    return snapshot(
        get_executor_runtime(),
        account_ref=ref,
        agent_slug=agent_slug,
        agent_id=agent_id,
    )


def build_agent_handlers() -> dict[str, Handler]:
    """AgentService verbs over the socket — CRUD + lifecycle + consult — so
    the MCP subprocess runs entirely over the socket (no web app) and every
    surface goes through the ONE service (§5.2)."""
    from condor.agents.lifecycle import LifecycleError
    from condor.agents.service import AgentService

    svc = AgentService()

    async def _consult(agent, task, context=""):
        answer = await svc.consult(agent, task, context=context)
        return {"agent": agent, "answer": answer}

    async def _notify_emit(text, agent_id="", kind="agent", origin="mcp"):
        from condor.notifications import notify

        entry = await notify(text, agent_id=agent_id, kind=kind, origin=origin)
        return {"entry": entry}

    def _approval_list():
        from condor.agents.approvals import get_approval_manager

        return {"approvals": get_approval_manager().open_approvals()}

    def _approval_resolve(approval_id, decision, note="", channel="mcp"):
        from condor.agents.approvals import get_approval_manager

        try:
            return get_approval_manager().resolve(
                approval_id, decision, channel=channel, note=note
            )
        except ValueError as e:
            raise LifecycleError(422, str(e))

    def _run_emit(
        run_id, type, payload=None, tick=None, tool_call_id=None, executor_id=None
    ):
        # The MCP subprocess emits run events via this socket method — it
        # never writes run files itself (§7.1 single-writer rule).
        from condor.agents.runstore import UnknownRunError, get_run_store

        try:
            ev = get_run_store().emit(
                run_id,
                type,
                payload,
                tick=tick,
                tool_call_id=tool_call_id or None,
                executor_id=executor_id or None,
            )
        except UnknownRunError as e:
            raise LifecycleError(404, str(e))
        except ValueError as e:
            raise LifecycleError(422, str(e))
        return {"seq": ev["seq"]}

    return {
        # lifecycle
        "agent.list": lambda: {"agents": svc.list_runs(live_only=True)},
        # runs (RunStore history)
        "run.list": lambda slug=None, kind=None, limit=50: {
            "runs": svc.list_runs(slug, kind=kind, limit=limit)
        },
        "run.get": lambda run_id, include_events=False: svc.get_run(
            run_id, include_events=include_events
        ),
        "run.export": lambda run_id: {"markdown": svc.export_run(run_id)},
        "run.emit": _run_emit,
        # approvals (§4.2)
        "approval.list": _approval_list,
        "approval.resolve": _approval_resolve,
        # notifications (§4.1): the MCP subprocess enqueues through the one
        # serialized main-process outbox writer — never by writing the file.
        "notify.emit": _notify_emit,
        "agent.start": lambda **kw: svc.run(**kw),
        # experiment: one simulated tick in memory, returns {"report": md} —
        # nothing recorded, so it is a verb here, never a run.
        "agent.experiment": lambda **kw: svc.experiment(**kw),
        "agent.verb": lambda slug, verb, agent_id=None, close=False: svc.control(
            slug, verb, agent_id=agent_id, close=close
        ),
        # The brain's own "job finished" signal — ends its run after the
        # current tick (may only shorten a run, never extend one).
        "run.complete": lambda run_id, summary="": svc.complete_run(
            run_id, summary=summary
        ),
        "agent.consult": lambda **kw: _consult(**kw),
        # CRUD (the service owns guards — tombstone semantics, spec validation)
        "agent.get": lambda slug: {"agent": svc.agent_summary(svc.get(slug))},
        "agent.definitions": lambda: {
            "agents": [svc.agent_summary(a) for a in svc.list()]
        },
        "agent.create": lambda **kw: {"agent": svc.agent_summary(svc.create(**kw))},
        "agent.update": lambda slug, patch: {
            "agent": svc.agent_summary(svc.update(slug, patch))
        },
        "agent.delete": lambda slug, reason="": svc.delete(slug, reason=reason),
        "agent.directive": lambda **kw: svc.inject_directive(**kw),
    }


def build_all_handlers() -> dict[str, Handler]:
    """Every control method — executors + agent lifecycle/consult.
    Used by the web-app host (`condor serve`)."""
    return {**build_executor_handlers(), **build_agent_handlers()}
