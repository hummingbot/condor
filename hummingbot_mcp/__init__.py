"""
Hummingbot MCP Server

A professional Model Context Protocol server for Hummingbot API integration.
Enables AI assistants to manage crypto trading across multiple exchanges.
"""

__version__ = "1.0.4"
__author__ = "Federico Cardoso"

from .server import main

__all__ = ["main"]
