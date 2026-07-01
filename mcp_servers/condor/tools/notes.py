"""DEPRECATED key-value notes — alias over the user memory store.

Historically notes were a per-chat key-value JSON blob. They are now a thin,
deprecated alias over ``manage_memory`` (keyed by user, with frontmatter and
auditing). Kept for one release so anything still calling it keeps working;
new code should use ``manage_memory`` directly.
"""

from mcp_servers.condor.tools import memory


async def manage_notes(
    action: str, key: str | None = None, value: str | None = None
) -> dict:
    if action == "list":
        return await memory.manage_memory(action="list")

    elif action == "get":
        if not key:
            return {"error": "key is required"}
        res = await memory.manage_memory(action="read", name=key)
        if "error" in res:
            return {"error": f"Note '{key}' not found"}
        return {"key": key, "value": res["content"]}

    elif action == "set":
        if not key or value is None:
            return {"error": "key and value are required"}
        res = await memory.manage_memory(
            action="write",
            name=key,
            content=str(value),
            description=key,
            type="reference",
        )
        if "error" in res:
            return res
        return {"saved": True, "key": key, "value": str(value)}

    elif action == "delete":
        if not key:
            return {"error": "key is required"}
        res = await memory.manage_memory(action="delete", name=key)
        if "error" in res:
            return {"error": f"Note '{key}' not found"}
        return {"deleted": True, "key": key}

    return {"error": f"Unknown action: {action}"}
