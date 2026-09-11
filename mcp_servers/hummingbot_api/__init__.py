"""
Hummingbot MCP Server

A professional Model Context Protocol server for Hummingbot API integration.
Enables AI assistants to manage crypto trading across multiple exchanges.
"""

__version__ = "1.0.4"
__author__ = "Federico Cardoso"

__all__ = ["main"]


def __getattr__(name: str):
    """``main``, imported only when somebody actually asks for it.

    It used to be a plain ``from .server import main`` up here, which made
    importing *anything* in this package wake the server — and ``server.py``
    parses argv and builds a ``FastMCP`` singleton at import. ``profiles.py``
    (FEAT-091) exists so the web process can read the tool tables without any of
    that, and a package-level import of the server would have quietly undone it,
    since importing a submodule imports its package first.
    """
    if name == "main":
        from .server import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
