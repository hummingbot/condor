"""Exception hierarchy for the Condor MCP server."""


class CondorMCPError(Exception):
    """Base exception for all Condor MCP errors."""


class ToolError(CondorMCPError):
    """Error within a tool's business logic."""


class APIError(CondorMCPError):
    """Error calling the main-process HTTP API."""
