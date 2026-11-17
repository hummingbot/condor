"""Exception hierarchy for the Condor MCP server."""


class CondorMCPError(Exception):
    """Base exception for all Condor MCP errors."""


class ToolError(CondorMCPError):
    """Error within a tool's business logic."""


class APIError(CondorMCPError):
    """Error calling the main-process HTTP API."""


# The three ways a crossing fails need three different next moves (MC-11), so
# they are three types. All are ``APIError``: a caller that only wants "it did
# not work" keeps catching the base.


class APIUnreachable(APIError):
    """Nothing accepted the connection — the request never ran."""


class APITimeout(APIError):
    """The main process was reached and did not answer in time.

    The request may well still be executing: a client that stops waiting does
    not cancel the route handler.
    """


class APIHTTPError(APIError):
    """The main process answered with an error status."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"API error ({status}): {detail}")
        self.status = status
        self.detail = detail
