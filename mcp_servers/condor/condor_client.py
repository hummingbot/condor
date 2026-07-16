"""Client for calling the persistent Condor main process (unix control socket)."""


from mcp_servers.condor.exceptions import APIError


async def call_control(
    method: str, params: dict | None = None, timeout: float = 60
) -> dict | list:
    """Call the persistent Condor process over its unix control socket.

    Raises APIError (mapping ControlError) so callers keep one error type.
    """
    from condor.control.client import ControlError
    from condor.control.client import call_control as _call

    try:
        return await _call(method, params, timeout=timeout)
    except ControlError as e:
        raise APIError(f"control error ({e.status}): {e.message}")


