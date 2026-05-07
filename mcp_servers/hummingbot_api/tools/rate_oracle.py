"""
Rate oracle management operations.

The Hummingbot API client does not currently expose rate-oracle helpers, so this
module uses the client's authenticated HTTP session for the documented endpoints.
"""
import inspect
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import ManageRateOracleRequest


_HTTP_RESOURCE_CANDIDATES = (
    "market_data",
    "executors",
    "portfolio",
    "accounts",
    "controllers",
)


def _get_http_transport(client: Any) -> tuple[Any, str]:
    """Return an authenticated session and base URL from any client resource."""
    base_url = getattr(client, "base_url", None)
    for session_attr in ("session", "_session"):
        session = getattr(client, session_attr, None)
        if session is not None and base_url:
            return session, str(base_url).rstrip("/")

    for resource_name in _HTTP_RESOURCE_CANDIDATES:
        try:
            resource = getattr(client, resource_name, None)
        except RuntimeError:
            continue
        session = getattr(resource, "session", None)
        base_url = getattr(resource, "base_url", None)
        if session is not None and base_url:
            return session, str(base_url).rstrip("/")

    raise ToolError("Could not find an authenticated HTTP session on the Hummingbot API client")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _extract_error_message(response: Any) -> str:
    try:
        error_detail = await _maybe_await(response.json())
    except Exception:
        text = await _maybe_await(response.text()) if hasattr(response, "text") else ""
        return text or str(getattr(response, "reason", "unknown error"))

    if isinstance(error_detail, dict):
        for key in ("detail", "message", "error"):
            if key in error_detail:
                return str(error_detail[key])
    if isinstance(error_detail, list) and error_detail:
        return "; ".join(str(item) for item in error_detail)
    return str(error_detail)


async def _response_json(response: Any) -> Any:
    status = getattr(response, "status", None)
    ok = getattr(response, "ok", None)
    if ok is False or (isinstance(status, int) and status >= 400):
        error_message = await _extract_error_message(response)
        status_text = f"HTTP {status}" if status is not None else "HTTP error"
        raise ToolError(f"{status_text}: {error_message}")

    response.raise_for_status()
    return await _maybe_await(response.json())


async def _request_json(client: Any, method: str, path: str, **kwargs: Any) -> Any:
    session, base_url = _get_http_transport(client)
    request = getattr(session, method)
    request_result = request(f"{base_url}{path}", **kwargs)

    if hasattr(request_result, "__aenter__"):
        async with request_result as response:
            return await _response_json(response)

    response = await request_result
    try:
        return await _response_json(response)
    finally:
        release = getattr(response, "release", None)
        if callable(release):
            release()


def _ensure_dict(payload: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ToolError(f"Unexpected {endpoint} response: {payload}")
    return payload


def _extract_sources(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [str(source) for source in payload]

    if isinstance(payload, dict):
        for key in ("sources", "available_sources", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [str(source) for source in value]

    raise ToolError(f"Unexpected rate oracle sources response: {payload}")


def _source_aliases(source: str) -> set[str]:
    normalized = source.lower().replace(" ", "_").replace("-", "_")
    return {normalized, normalized.replace("_", "")}


def _source_from_config(config: dict[str, Any]) -> str:
    rate_oracle_source = config.get("rate_oracle_source")
    if isinstance(rate_oracle_source, dict):
        return str(rate_oracle_source.get("name", "unknown"))
    if rate_oracle_source is not None:
        return str(rate_oracle_source)
    return "unknown"


def _format_sources(sources: list[str]) -> str:
    if not sources:
        return "Available Rate Oracle Sources:\nNo sources returned by the API."

    return "Available Rate Oracle Sources:\n" + "\n".join(f"- {source}" for source in sources)


def _format_config(config: dict[str, Any]) -> str:
    source = _source_from_config(config)
    global_token_data = config.get("global_token")
    global_token = global_token_data if isinstance(global_token_data, dict) else {}
    token_name = global_token.get("global_token_name", "unknown")
    token_symbol = global_token.get("global_token_symbol", "unknown")
    available_sources = config.get("available_sources")

    output = (
        "Rate Oracle Configuration:\n"
        f"  Source: {source}\n"
        f"  Global Token: {token_name} ({token_symbol})"
    )

    if isinstance(available_sources, list) and available_sources:
        output += "\n\nAvailable Sources:\n"
        output += "\n".join(f"- {source}" for source in available_sources)

    return output


def _format_update_result(result: dict[str, Any]) -> str:
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    message = str(result.get("message", "Rate oracle configuration updated."))

    status_line = "Rate oracle configuration updated successfully."
    if config:
        return f"{status_line}\n\n{_format_config(config)}\n\nAPI Message: {message}"

    return f"{status_line}\n\nAPI Message: {message}"


async def _list_sources(client: Any) -> list[str]:
    payload = await _request_json(client, "get", "/rate-oracle/sources")
    return _extract_sources(payload)


async def _update_config(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    result = await _request_json(client, "put", "/rate-oracle/config", json=payload)
    result = _ensure_dict(result, "/rate-oracle/config")
    if result.get("success") is False:
        raise ToolError(str(result.get("message", "Rate oracle configuration update failed")))
    return result


async def manage_rate_oracle(client: Any, request: ManageRateOracleRequest) -> dict[str, Any]:
    """Manage rate oracle sources and configuration."""
    if request.operation == "list_sources":
        sources = await _list_sources(client)
        return {
            "operation": request.operation,
            "sources": sources,
            "formatted_output": _format_sources(sources),
        }

    if request.operation == "get_config":
        config = _ensure_dict(
            await _request_json(client, "get", "/rate-oracle/config"),
            "/rate-oracle/config",
        )
        return {
            "operation": request.operation,
            "config": config,
            "formatted_output": _format_config(config),
        }

    if request.operation == "set_source":
        if request.source is None:
            raise ToolError("source is required for operation='set_source'")

        sources = await _list_sources(client)
        sources_by_normalized_name = {}
        for source in sources:
            for alias in _source_aliases(source):
                sources_by_normalized_name[alias] = source

        canonical_source = sources_by_normalized_name.get(request.source)
        if canonical_source is None:
            canonical_source = sources_by_normalized_name.get(request.source.replace("_", ""))
        if canonical_source is None:
            raise ToolError(
                f"Invalid rate oracle source '{request.source}'. "
                f"Available sources: {', '.join(sources) if sources else 'none'}"
            )

        result = await _update_config(
            client,
            {"rate_oracle_source": {"name": canonical_source}},
        )

        return {
            "operation": request.operation,
            "source": canonical_source,
            "result": result,
            "formatted_output": _format_update_result(result),
        }

    if request.operation == "set_global_token":
        if request.global_token_name is None:
            raise ToolError("global_token_name is required for operation='set_global_token'")

        global_token = {"global_token_name": request.global_token_name}
        if request.global_token_symbol is not None:
            global_token["global_token_symbol"] = request.global_token_symbol

        result = await _update_config(client, {"global_token": global_token})
        return {
            "operation": request.operation,
            "global_token": global_token,
            "result": result,
            "formatted_output": _format_update_result(result),
        }

    raise ToolError(f"Unknown rate oracle operation: {request.operation}")
