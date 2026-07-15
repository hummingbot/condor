"""Control-server method handlers.

Maps JSON-RPC method names to the transport-agnostic executor ops over the live,
in-process runtime singleton. This is the seam that will let the daemon expose
the runtime once the web REST app is retired.
"""

from __future__ import annotations

from condor.control.server import Handler


def build_executor_handlers() -> dict[str, Handler]:
    from condor.executors import ops
    from condor.executors.service import get_executor_runtime

    def _rt():
        return get_executor_runtime()

    return {
        "ping": lambda: {"ok": True},
        "executor.create": lambda **kw: ops.create(_rt(), **kw),
        "executor.stop": lambda **kw: ops.stop(_rt(), **kw),
        "executor.get": lambda executor_id: ops.get(_rt(), executor_id),
        "executor.list": lambda **kw: ops.list_(_rt(), **kw),
        "executor.performance": lambda **kw: ops.performance(_rt(), **kw),
    }


def build_agent_handlers() -> dict[str, Handler]:
    """Agent-session lifecycle + consult + delegate — the rest of what the MCP
    needs so it runs entirely over the socket (no web app)."""
    from condor.agents import delegate as dg
    from condor.agents import lifecycle
    from condor.agents.consult import run_consult
    from condor.agents.lifecycle import LifecycleError

    async def _consult(agent, task, context="", chat_id=0, user_id=0, server_name=None):
        answer = await run_consult(
            slug=agent, user_id=user_id, chat_id=chat_id,
            server_name=server_name, task=task, context=context,
        )
        return {"agent": agent, "answer": answer}

    async def _delegate_start(agent, task, chat_id=0, user_id=0, server_name=None,
                              risk_limits=None, timeout_s=None):
        dt = await dg.start_delegation(
            agent_slug=agent, user_id=user_id, chat_id=chat_id,
            server_name=server_name, task=task, timeout_s=timeout_s,
            risk_limits=risk_limits,
        )
        return {"task_id": dt.task_id, "status": dt.status}

    def _delegate_get(task_id):
        dt = dg.get_delegation(task_id)
        if dt is not None:
            return dt.to_dict()
        # After a restart the live registry is empty; the flat transcript file
        # still resolves so a task_id never goes dark.
        import re

        from condor.agents.agent import AgentStore
        from condor.agents.journal import find_delegation_file

        m = re.match(r"^(?P<slug>.+)-d(?P<num>\d+)$", task_id)
        if m:
            agent = AgentStore().get(m.group("slug"))
            if agent is not None:
                path = find_delegation_file(agent.agent_dir, int(m.group("num")))
                if path:
                    return {"task_id": task_id, "transcript": path.read_text()}
        raise LifecycleError(404, f"Delegation '{task_id}' not found")

    async def _delegate_stop(task_id):
        if dg.get_delegation(task_id) is None:
            raise LifecycleError(404, f"Delegation '{task_id}' not found")
        return {"stopped": await dg.stop_delegation(task_id)}

    return {
        "agent.list": lambda: {"agents": lifecycle.list_instances()},
        "agent.start": lambda **kw: lifecycle.start_session(**kw),
        "agent.verb": lambda **kw: lifecycle.apply_verb(**kw),
        "agent.consult": lambda **kw: _consult(**kw),
        "delegate.start": lambda **kw: _delegate_start(**kw),
        "delegate.list": lambda: {"delegations": [d.to_dict() for d in dg.get_all_delegations().values()]},
        "delegate.get": lambda task_id: _delegate_get(task_id),
        "delegate.stop": lambda task_id: _delegate_stop(task_id),
    }


def build_all_handlers() -> dict[str, Handler]:
    """Every control method — executors + agent lifecycle/consult/delegate.
    Used by both the web-app host and the standalone `condor.daemon`."""
    return {**build_executor_handlers(), **build_agent_handlers()}
