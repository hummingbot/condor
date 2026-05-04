"""Utilities for building and validating Hummingbot API server URLs."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse


class ServerUrlError(ValueError):
    """Raised when server host/port/protocol configuration is invalid."""


def _normalize_protocol(protocol: str | None) -> str:
    value = (protocol or "auto").strip().lower()
    if value not in {"auto", "http", "https"}:
        raise ServerUrlError(
            f"Invalid protocol '{protocol}'. Expected one of: auto, http, https."
        )
    return value


def _normalize_host_for_url(host: str) -> str:
    # Bracket IPv6 literals so URL parsing and formatting remain valid.
    if ":" in host and not host.startswith("[") and host.count(":") > 1:
        return f"[{host}]"
    return host


def _format_url(protocol: str, host: str, port: int) -> str:
    host = _normalize_host_for_url(host)
    if (protocol == "https" and port == 443) or (protocol == "http" and port == 80):
        return f"{protocol}://{host}"
    return f"{protocol}://{host}:{port}"


def build_server_url(host: str, port: int, protocol: str | None = "auto") -> str:
    """Build a validated API URL from host/port/protocol settings.

    Rules:
    - If host includes scheme, keep that scheme (unless explicit protocol conflicts).
    - If host includes a port and it conflicts with `port`, raise ServerUrlError.
    - If scheme is omitted and protocol is "auto", infer https for 443 else http.
    - Default ports are omitted from final URL (http:80, https:443).
    """

    if host is None:
        raise ServerUrlError("Server host is required.")
    host = str(host).strip().rstrip("/")
    if not host:
        raise ServerUrlError("Server host cannot be empty.")

    try:
        port = int(port)
    except Exception as exc:
        raise ServerUrlError(f"Invalid port '{port}'.") from exc
    if port <= 0 or port > 65535:
        raise ServerUrlError(f"Invalid port '{port}'. Must be between 1 and 65535.")

    protocol = _normalize_protocol(protocol)

    if host.startswith(("http://", "https://")):
        parsed = urlparse(host)
        parsed_scheme = (parsed.scheme or "").lower()
        parsed_host = parsed.hostname
        parsed_port = parsed.port

        if parsed_scheme not in {"http", "https"}:
            raise ServerUrlError(
                f"Unsupported URL scheme '{parsed_scheme}' in host '{host}'."
            )
        if not parsed_host:
            raise ServerUrlError(f"Invalid host URL '{host}'.")

        if protocol != "auto" and protocol != parsed_scheme:
            raise ServerUrlError(
                f"Protocol conflict: host uses '{parsed_scheme}' but protocol is '{protocol}'."
            )
        if parsed_port is not None and parsed_port != port:
            raise ServerUrlError(
                f"Port conflict: host URL includes port {parsed_port} but port is set to {port}."
            )

        final_port = parsed_port if parsed_port is not None else port
        return _format_url(parsed_scheme, parsed_host, final_port)

    if "://" in host:
        raise ServerUrlError(
            f"Invalid host '{host}'. Include a full URL (http/https) or plain hostname."
        )

    resolved_protocol = protocol if protocol != "auto" else ("https" if port == 443 else "http")
    return _format_url(resolved_protocol, host, port)


def build_server_url_from_config(server: Mapping[str, Any]) -> str:
    """Build URL from a server config mapping."""
    host = server.get("host", "localhost")
    port = server.get("port", 8000)
    protocol = server.get("protocol", "auto")
    return build_server_url(host=host, port=port, protocol=protocol)
