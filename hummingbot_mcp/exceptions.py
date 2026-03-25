"""
Custom exceptions for the Hummingbot MCP Server
"""


class HummingbotMCPError(Exception):
    """Base exception for Hummingbot MCP server"""

    pass


class ToolError(HummingbotMCPError):
    """Exception raised when a tool execution fails"""

    pass


class MaxConnectionsAttemptError(HummingbotMCPError):
    """Exception raised when API connection fails"""

    pass


class ConfigurationError(HummingbotMCPError):
    """Exception raised when configuration is invalid"""

    pass
