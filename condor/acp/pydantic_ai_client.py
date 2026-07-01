"""Pydantic AI agent client -- uses pydantic-ai with MCP tool servers.

Drop-in alternative to ACPClient for open-source / local models.
Supports any model backend that pydantic-ai supports: ollama, openai-compatible
(LM Studio), groq, anthropic, etc.

Yields the same ACPEvent types so TickEngine can consume it identically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .client import (
    ACPEvent,
    Heartbeat,
    PermissionCallback,
    PromptDone,
    TextChunk,
    ToolCallEvent,
    ToolCallUpdate,
)

log = logging.getLogger(__name__)


def _infer_tool_filter_mode(model_name: str) -> str:
    """Automatically detect the best tool filter mode based on model name.

    Analyzes model size and family to determine capability:
    - Small models (≤8B): essential (minimal tools)
    - Medium models (9B-32B): moderate (common operations)
    - Large models (>32B) or cloud APIs: full (all tools)

    Args:
        model_name: Model identifier like "ollama:llama3.1:8b" or "lmstudio:qwen-14b"

    Returns:
        "essential", "moderate", or "full"
    """
    import re

    model_lower = model_name.lower()

    # Cloud providers always get full access (they're powerful enough)
    if any(
        provider in model_lower
        for provider in ["openai:", "anthropic:", "groq:", "google:", "openrouter:"]
    ):
        log.info("Auto-detected cloud provider → tool_filter_mode=full")
        return "full"

    # Extract parameter count (e.g., "7b", "14b", "72b", "32b")
    # Matches patterns like: 7b, 8b, 14b, 32b, 72b, 1.5b, 2.7b, etc.
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*[bB](?![a-z])", model_lower)

    if size_match:
        size = float(size_match.group(1))

        if size <= 8.0:
            mode = "essential"
            log.info(f"Auto-detected {size}B model → tool_filter_mode=essential")
        elif size <= 32.0:
            mode = "moderate"
            log.info(f"Auto-detected {size}B model → tool_filter_mode=moderate")
        else:
            mode = "full"
            log.info(f"Auto-detected {size}B model → tool_filter_mode=full")

        return mode

    # Model name-based heuristics (if no size found)
    # Small models
    if any(name in model_lower for name in ["gemma", "phi", "tiny", "mini", "small"]):
        log.info(f"Auto-detected small model family → tool_filter_mode=essential")
        return "essential"

    # Large models
    if any(name in model_lower for name in ["deepseek", "mixtral", "command-r", "gpt"]):
        log.info(f"Auto-detected large model family → tool_filter_mode=full")
        return "full"

    # Default to moderate for unknown models
    log.info(f"Unknown model size, defaulting → tool_filter_mode=moderate")
    return "moderate"


# Model prefix → pydantic-ai model string mapping
# Users set agent_key like "ollama:llama3.1:70b" or "openai:gpt-4o"
# which maps directly to pydantic-ai model identifiers.
PYDANTIC_AI_PREFIXES = frozenset(
    {"ollama", "openai", "groq", "anthropic", "google", "lmstudio", "openrouter"}
)

# Default base URLs for local model providers and OpenRouter
DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


# Global semaphores keyed by base URL so all clients pointing at the same
# inference server (e.g. LM Studio) share one slot, regardless of which
# session (user chat, trading tick, etc.) holds it.
_SERVER_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def _get_server_semaphore(base_url: str) -> asyncio.Semaphore:
    if base_url not in _SERVER_SEMAPHORES:
        _SERVER_SEMAPHORES[base_url] = asyncio.Semaphore(1)
    return _SERVER_SEMAPHORES[base_url]


def is_pydantic_ai_model(agent_key: str) -> bool:
    """Check if an agent_key should use the PydanticAI client."""
    prefix = agent_key.split(":", 1)[0] if ":" in agent_key else ""
    return prefix in PYDANTIC_AI_PREFIXES


def resolve_base_url(model_name: str, base_url: str | None = None) -> str | None:
    """Return the OpenAI-compatible base URL a model would use.

    Returns ``base_url`` when given, else the provider default (ollama/lmstudio/
    openrouter). ``None`` for cloud providers pydantic-ai resolves natively
    (anthropic, groq, default openai/google).
    """
    if base_url:
        return base_url
    prefix = model_name.split(":", 1)[0]
    return DEFAULT_BASE_URLS.get(prefix)


async def healthcheck_local_backend(
    model_name: str, base_url: str | None = None
) -> str | None:
    """Preflight a LOCAL OpenAI-compatible backend before a run.

    For ollama / lmstudio (or openai:* with a custom base_url) this verifies the
    inference server is reachable and, when a model id is given, that the model is
    actually loaded. Returns ``None`` when healthy or when ``model_name`` is not a
    local backend (cloud providers are left to fail with their own formatted error);
    otherwise a short, human-readable reason string.
    """
    import httpx

    prefix, _, model_id = model_name.partition(":")
    is_local = prefix in ("ollama", "lmstudio") or (prefix == "openai" and base_url)
    if not is_local:
        return None

    url = resolve_base_url(model_name, base_url)
    if not url:
        return None

    models_url = f"{url.rstrip('/')}/models"
    try:
        timeout = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(models_url)
    except Exception as e:
        return (
            f"the model backend at {url} is unreachable ({type(e).__name__}) — "
            f"is the {prefix} server running?"
        )

    if resp.status_code != 200:
        return f"the model backend at {url} returned HTTP {resp.status_code}."

    if model_id:
        try:
            ids = {
                m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict)
            }
        except Exception:
            ids = set()
        ids = {i for i in ids if isinstance(i, str) and i}
        # Ollama reports ids like "qwen3:32b"; match exact or tag-prefix.
        loaded = any(i == model_id or i.startswith(f"{model_id}:") for i in ids)
        if ids and not loaded:
            available = ", ".join(sorted(ids)) or "(none)"
            return (
                f"model '{model_id}' is not loaded on the {prefix} backend at {url}. "
                f"Available: {available}."
            )

    return None


_NULL_SAFE_MODEL_CLS: Any = None


def _make_openai_compat_model(model_id: str, provider: Any) -> Any:
    """Build an OpenAIModel that never sends an assistant ``content: null``.

    Ollama's OpenAI-compatible ``/v1/chat/completions`` endpoint rejects any
    message whose ``content`` is null with ``invalid message content type:
    <nil>``. pydantic-ai serializes an assistant turn that is *only* tool calls
    (no accompanying text) with ``content=None`` — which happens routinely the
    moment a model decides to call a tool without narrating first. Coerce those
    nulls to an empty string so strict local backends (Ollama, some LM Studio /
    vLLM builds) accept the follow-up request inside a multi-step tool run.

    The subclass is defined lazily and cached so the heavy pydantic-ai import
    only happens when a model is actually built.
    """
    global _NULL_SAFE_MODEL_CLS
    if _NULL_SAFE_MODEL_CLS is None:
        from pydantic_ai.models.openai import OpenAIModel

        class _NullContentSafeOpenAIModel(OpenAIModel):
            async def _map_messages(self, *args: Any, **kwargs: Any) -> Any:
                mapped = await super()._map_messages(*args, **kwargs)
                for msg in mapped:
                    if (
                        isinstance(msg, dict)
                        and msg.get("role") == "assistant"
                        and msg.get("content") is None
                    ):
                        msg["content"] = ""
                return mapped

        _NULL_SAFE_MODEL_CLS = _NullContentSafeOpenAIModel

    return _NULL_SAFE_MODEL_CLS(model_id, provider=provider)


def _tool_args_to_dict(args: Any) -> dict | None:
    """Normalise a pydantic-ai tool-call `args` value to a dict.

    OpenAI-compatible providers (LM Studio, Ollama, OpenRouter) deliver tool-call
    arguments as a JSON string rather than a dict, so a bare isinstance(dict)
    check would drop them. Parse the string form too.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class PydanticAIClient:
    """Manages a pydantic-ai agent with MCP tool servers.

    Mirrors ACPClient's interface: start() → prompt_stream() → stop().
    MCP servers are launched as stdio subprocesses, same as ACP does,
    but tools are consumed via pydantic-ai's MCPServerStdio integration.

    Model resolution:
      - "ollama:llama3.1"  → uses ollama provider (localhost:11434)
      - "openai:gpt-4o"    → uses OpenAI API
      - "openai:my-model"  → with base_url, uses any OpenAI-compatible API (LM Studio, vLLM, etc.)
      - "groq:llama-3.3-70b-versatile" → uses Groq cloud
      - "anthropic:claude-sonnet-4-6" → uses Anthropic API
      - "openrouter:anthropic/claude-sonnet-4-5" → uses OpenRouter (requires OPENROUTER_API_KEY)
    """

    def __init__(
        self,
        model: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_callback: PermissionCallback | None = None,
        extra_env: dict[str, str] | None = None,
        base_url: str | None = None,
        tool_filter_mode: (
            str | None
        ) = None,  # "essential", "moderate", "full", or None for auto-detect
        allowed_tools: (
            list[str] | None
        ) = None,  # restrict the agent to these tool names
    ):
        self.model_name = model
        self.mcp_server_configs = mcp_servers or []
        self.permission_callback = permission_callback
        self.extra_env = extra_env
        self.base_url = base_url
        # When set, the agent only sees tools whose name is in this allowlist
        # (used by domain-expert consults to scope an agent to one domain).
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        # Auto-detect filter mode based on model if not explicitly set
        self.tool_filter_mode = tool_filter_mode or _infer_tool_filter_mode(model)
        self._mcp_servers: list[Any] = []
        self._agent: Any = None
        # Resolved in start() to the global semaphore for this server's base URL,
        # so all sessions sharing the same local inference server (e.g. LM Studio)
        # are serialized. Stays None for natively-resolved cloud providers
        # (anthropic/groq/default openai/google), which handle concurrency fine.
        self._request_semaphore: asyncio.Semaphore | None = None
        # Background task that owns the MCP server cancel scopes.
        # anyio requires cancel scopes to be entered/exited in the same task,
        # so we can't close them from an arbitrary caller task.
        self._mcp_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._startup_error: BaseException | None = None
        # Accumulated turn history — grows with each prompt_stream() call so
        # the model sees prior turns. A fresh client is created per session/tick,
        # so history is reset by recreating the client rather than in-place.
        self._message_history: list = []

    def _build_model(self) -> Any:
        """Build the pydantic-ai model object with sensible defaults.

        All local providers (ollama, lmstudio) are routed through OpenAI-compatible
        endpoints so we control the base_url explicitly. This avoids requiring
        environment variables like OLLAMA_BASE_URL.

        Resolution:
          - ollama:model     → OpenAI-compat at localhost:11434/v1 (or custom base_url)
          - lmstudio:model   → OpenAI-compat at localhost:1234/v1 (or custom base_url)
          - openrouter:model → OpenAI-compat at https://openrouter.ai/api/v1,
                               requires OPENROUTER_API_KEY; model id must be
                               explicit (e.g. "openrouter:anthropic/claude-sonnet-4-5").
          - openai:model     → OpenAI API (or custom base_url for vLLM, etc.)
          - groq/anthropic   → standard pydantic-ai resolution
        """
        import httpx
        from openai import AsyncOpenAI
        from pydantic_ai.providers.openai import OpenAIProvider

        prefix, _, model_id = self.model_name.partition(":")
        base_url = self.base_url

        # The OpenAI SDK applies its own default timeout (connect=5s) that takes
        # precedence over any httpx.AsyncClient timeout. Set it on AsyncOpenAI
        # directly so it actually applies. connect=30s handles a busy LM Studio
        # connection pool; the read timeout covers slow local model generation —
        # including a cold first request where the model is still loading into
        # memory. Default 600s; override via LOCAL_MODEL_READ_TIMEOUT.
        _read_timeout = float(os.environ.get("LOCAL_MODEL_READ_TIMEOUT", "600"))
        _local_timeout = httpx.Timeout(
            connect=30.0, read=_read_timeout, write=30.0, pool=30.0
        )

        # OpenRouter: OpenAI-compatible cloud gateway, requires API key.
        # Handled before the generic DEFAULT_BASE_URLS branch because that branch
        # uses api_key="not-needed", which OpenRouter rejects.
        if prefix == "openrouter":
            if not model_id:
                raise RuntimeError(
                    "OpenRouter requires an explicit model id, e.g. "
                    "'openrouter:openai/gpt-4o' or 'openrouter:anthropic/claude-sonnet-4-5'."
                )
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "OPENROUTER_API_KEY is not set. Add it to your .env to use openrouter:* models."
                )
            openai_client = AsyncOpenAI(
                base_url=base_url or DEFAULT_BASE_URLS["openrouter"],
                api_key=api_key,
                timeout=_local_timeout,
            )
            return _make_openai_compat_model(
                model_id, OpenAIProvider(openai_client=openai_client)
            )

        # Local providers: always use OpenAI-compatible endpoint with default URL
        if prefix in DEFAULT_BASE_URLS:
            base_url = base_url or DEFAULT_BASE_URLS[prefix]
            if not model_id:
                model_id = self._resolve_default_local_model(
                    prefix=prefix, base_url=base_url
                )
            openai_client = AsyncOpenAI(
                base_url=base_url,
                api_key="not-needed",
                timeout=_local_timeout,
            )
            return _make_openai_compat_model(
                model_id, OpenAIProvider(openai_client=openai_client)
            )

        # OpenAI with custom base_url (vLLM, TGI, etc.)
        if prefix == "openai" and base_url:
            openai_client = AsyncOpenAI(
                base_url=base_url,
                api_key="not-needed",
                timeout=_local_timeout,
            )
            return _make_openai_compat_model(
                model_id, OpenAIProvider(openai_client=openai_client)
            )

        # Standard pydantic-ai resolution (openai, groq, anthropic, google)
        from pydantic_ai.models import infer_model

        return infer_model(self.model_name)

    def _resolve_default_local_model(self, prefix: str, base_url: str) -> str:
        """Resolve a usable default model for local providers.

        For ollama/lmstudio with model strings like "ollama:" (no explicit model),
        prefer an env override and then probe known model-list endpoints.
        """
        env_override = os.environ.get("CONDOR_DEFAULT_LOCAL_MODEL") or os.environ.get(
            "OLLAMA_MODEL"
        )
        if env_override:
            return env_override

        model_id = self._fetch_openai_compatible_model(base_url)
        if model_id:
            return model_id

        if prefix == "ollama":
            model_id = self._fetch_ollama_native_model(base_url)
            if model_id:
                return model_id

        raise RuntimeError(
            f"No local model found for '{prefix}'. "
            f"Use an explicit key like '{prefix}:<model-name>' (e.g. ollama:llama3.1) "
            "or set CONDOR_DEFAULT_LOCAL_MODEL."
        )

    def _fetch_openai_compatible_model(self, base_url: str) -> str | None:
        """Try GET {base_url}/models and return the first model id."""
        url = f"{base_url.rstrip('/')}/models"
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=2) as resp:
                if resp.status != 200:
                    return None
                import json

                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                model_id = first.get("id")
                if isinstance(model_id, str) and model_id.strip():
                    return model_id.strip()
        return None

    def _fetch_ollama_native_model(self, base_url: str) -> str | None:
        """Try GET /api/tags from the Ollama host and return first model name."""
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        native_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
        try:
            req = Request(native_url, method="GET")
            with urlopen(req, timeout=2) as resp:
                if resp.status != 200:
                    return None
                import json

                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        models = payload.get("models")
        if isinstance(models, list) and models:
            first = models[0]
            if isinstance(first, dict):
                name = first.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        return None

    async def start(self) -> None:
        """Initialize MCP servers and create the pydantic-ai agent."""
        from pydantic_ai import Agent
        from pydantic_ai.mcp import MCPServerStdio

        toolsets = []
        for srv_config in self.mcp_server_configs:
            command = srv_config["command"]
            args = srv_config.get("args", [])

            env = dict(self.extra_env or {})
            for env_entry in srv_config.get("env", []):
                if isinstance(env_entry, dict):
                    env[env_entry["name"]] = env_entry["value"]

            mcp_server = MCPServerStdio(
                command,
                args=args,
                env=env if env else None,
                timeout=30,
            )

            toolsets.append(mcp_server)
            self._mcp_servers.append(mcp_server)

        model = self._build_model()
        prepare = self._prepare_tools if self.allowed_tools else None
        self._agent = Agent(model, toolsets=toolsets, prepare_tools=prepare)

        # Resolve the global semaphore for this server's base URL so all client
        # instances targeting the same local inference server share one request
        # slot. Cloud providers pydantic-ai resolves natively (anthropic, groq,
        # default openai/google) have no base URL here; they handle concurrency
        # fine, so we leave the semaphore None and skip serialization for them.
        resolved_base_url = resolve_base_url(self.model_name, self.base_url)
        self._request_semaphore = (
            _get_server_semaphore(resolved_base_url)
            if resolved_base_url is not None
            else None
        )

        # Spin up a dedicated background task to own the MCP server cancel scopes.
        # anyio cancel scopes must be entered and exited in the same asyncio task;
        # using a shared AsyncExitStack across tasks causes RuntimeError on teardown.
        self._ready_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._startup_error = None
        self._mcp_task = asyncio.create_task(self._run_mcp_lifecycle())

        await self._ready_event.wait()
        if self._startup_error is not None:
            self._mcp_task = None
            self._mcp_servers.clear()
            self._agent = None
            raise self._startup_error

        log.info(
            "PydanticAI client ready: model=%s, mcp_servers=%d",
            self.model_name,
            len(self._mcp_servers),
        )

    async def _prepare_tools(self, ctx: Any, tool_defs: list) -> list:
        """Filter tools to ``self.allowed_tools`` before each run.

        pydantic-ai calls this with the full ``list[ToolDefinition]`` discovered
        from all MCP servers; we keep only those whose name is allowlisted. Tool
        names may be namespaced by the MCP layer (e.g. ``mcp__condor__manage_skill``),
        so we match on either the full name or its last ``__``-delimited segment.
        """
        allowed = self.allowed_tools or set()

        def _ok(name: str) -> bool:
            return name in allowed or name.rsplit("__", 1)[-1] in allowed

        kept = [td for td in tool_defs if _ok(td.name)]
        dropped = len(tool_defs) - len(kept)
        if dropped:
            log.debug(
                "Tool allowlist: kept %d/%d tools (%s)",
                len(kept),
                len(tool_defs),
                ", ".join(sorted(allowed)),
            )
        return kept

    async def _run_mcp_lifecycle(self) -> None:
        """Background task that holds the MCP server context open."""
        try:
            async with self._agent.run_mcp_servers():
                self._ready_event.set()
                await self._shutdown_event.wait()
        except BaseException as exc:
            if not self._ready_event.is_set():
                self._startup_error = exc
                self._ready_event.set()

    async def stop(self) -> None:
        """Signal the MCP lifecycle task to shut down and wait for it."""
        if self._mcp_task is not None:
            self._shutdown_event.set()
            try:
                await asyncio.wait_for(self._mcp_task, timeout=10)
            except Exception:
                log.exception("Error stopping MCP server task")
                self._mcp_task.cancel()
            self._mcp_task = None
        self._mcp_servers.clear()
        self._agent = None

    @property
    def alive(self) -> bool:
        return self._agent is not None

    @contextlib.asynccontextmanager
    async def _release_request_slot(self) -> AsyncIterator[None]:
        """Temporarily release the per-server request slot for a blocking wait.

        The per-server semaphore exists only to serialize concurrent HTTP
        requests to single-threaded local backends (LM Studio/Ollama). It must
        NOT stay held while we block on a human-in-the-loop confirmation: the
        semaphore is GLOBAL and keyed by base URL, so one user sitting on a
        permission dialog would otherwise stall every other session/tick that
        targets the same backend for the whole confirmation timeout.

        Releases the slot on entry and re-acquires it before returning, so model
        HTTP work stays serialized. No-op for cloud providers, whose semaphore is
        None (PERF-038).
        """
        sem = self._request_semaphore
        if sem is None:
            yield
            return
        sem.release()
        try:
            yield
        finally:
            await sem.acquire()

    async def prompt(self, text: str) -> str:
        """One-shot prompt: send text, return response."""
        chunks: list[str] = []
        async for event in self.prompt_stream(text):
            if isinstance(event, TextChunk):
                chunks.append(event.text)
        return "".join(chunks)

    async def prompt_stream(self, text: str) -> AsyncIterator[ACPEvent]:
        """Send a prompt and yield ACPEvents as they arrive.

        Uses pydantic-ai's streaming run with MCP tools.
        Tool calls go through the permission callback for risk checking.
        MCP servers are already running (started in start()), so we
        call iter() directly without run_mcp_servers().
        """
        assert self._agent is not None, "Client not started"

        # Serialize requests: local inference servers (LM Studio, Ollama) process
        # one request at a time. Without this, concurrent ticks race to connect
        # and the losing ticks ConnectTimeout against a busy server. Cloud
        # providers leave the semaphore None (see start()) so concurrent prompts
        # run in parallel; nullcontext() makes the guard a no-op for them.
        async with self._request_semaphore or contextlib.nullcontext():
            start_time = time.monotonic()

            try:
                from pydantic_ai.agent import CallToolsNode, ModelRequestNode
                from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
                from pydantic_graph import End

                async with self._agent.iter(
                    text, message_history=self._message_history
                ) as run:
                    async for node in run:
                        if isinstance(node, End):
                            # Final result -- extract text from the result
                            if hasattr(node, "data") and node.data:
                                result_data = node.data
                                if hasattr(result_data, "data"):
                                    yield TextChunk(text=str(result_data.data))
                            break

                        if isinstance(node, ModelRequestNode):
                            elapsed = time.monotonic() - start_time
                            yield Heartbeat(elapsed_seconds=elapsed)
                            # Extract tool return results from request parts
                            if hasattr(node, "request") and node.request:
                                for part in node.request.parts:
                                    if isinstance(part, ToolReturnPart):
                                        content = part.content
                                        output_str = (
                                            content
                                            if isinstance(content, str)
                                            else str(content)
                                        )
                                        yield ToolCallUpdate(
                                            tool_call_id=part.tool_call_id or "",
                                            status="completed",
                                            output=output_str,
                                        )

                        elif isinstance(node, CallToolsNode):
                            # Emit text and tool-call events from model response
                            for part in node.model_response.parts:
                                if isinstance(part, TextPart) and part.content:
                                    yield TextChunk(text=part.content)

                                elif isinstance(part, ToolCallPart):
                                    tool_id = part.tool_call_id or uuid.uuid4().hex[:12]
                                    tool_name = part.tool_name

                                    # Risk check via permission callback
                                    if self.permission_callback:
                                        tool_call_info = {
                                            "tool": tool_name,
                                            "title": tool_name,
                                            "input": _tool_args_to_dict(part.args)
                                            or {},
                                        }
                                        options = [
                                            {"optionId": "allow", "kind": "allow_once"},
                                            {"optionId": "deny", "kind": "deny"},
                                        ]
                                        # Don't hold the per-server slot while a
                                        # human decides — release it for the wait
                                        # so other sessions/ticks on this backend
                                        # aren't blocked (PERF-029).
                                        async with self._release_request_slot():
                                            result = await self.permission_callback(
                                                tool_call_info, options
                                            )
                                        outcome = result.get("outcome", {})
                                        if (
                                            isinstance(outcome, dict)
                                            and outcome.get("outcome") == "cancelled"
                                        ):
                                            yield ToolCallEvent(
                                                tool_call_id=tool_id,
                                                title=tool_name,
                                                status="blocked",
                                                kind="mcp",
                                                input=_tool_args_to_dict(part.args),
                                            )
                                            continue

                                    yield ToolCallEvent(
                                        tool_call_id=tool_id,
                                        title=tool_name,
                                        status="in_progress",
                                        kind="mcp",
                                        input=_tool_args_to_dict(part.args),
                                    )

                                    yield ToolCallUpdate(
                                        tool_call_id=tool_id,
                                        status="completed",
                                    )

                    # Accumulate messages so the next prompt_stream() call sees
                    # this turn's context via message_history.
                    if run.result is not None:
                        self._message_history.extend(run.result.new_messages())

                yield PromptDone(stop_reason="end_turn")

            except asyncio.TimeoutError:
                yield PromptDone(stop_reason="timeout")
            except Exception as e:
                log.exception("PydanticAI prompt error: %s", e)
                yield TextChunk(text=self._format_error(e))
                yield PromptDone(stop_reason="error")

    def _format_error(self, e: Exception) -> str:
        """Translate provider HTTP errors into actionable user-facing text.

        Falls back to the raw exception string for anything we don't recognize.
        """
        try:
            from pydantic_ai.exceptions import ModelHTTPError
        except ImportError:
            return f"(error: {e})"

        if not isinstance(e, ModelHTTPError):
            return f"(error: {e})"

        is_openrouter = self.model_name.startswith("openrouter:")
        status = getattr(e, "status_code", None)

        if is_openrouter and status == 402:
            return (
                "OpenRouter rejected the request: insufficient credits.\n\n"
                "Either top up at https://openrouter.ai/settings/credits, or "
                "switch to a free model with /agent → Change LLM → OpenRouter "
                "→ Enter model manually → openrouter/free."
            )
        if is_openrouter and status == 401:
            return (
                "OpenRouter rejected the API key (401). Check OPENROUTER_API_KEY "
                "in your .env and confirm the key is on the account that holds your credits."
            )
        if is_openrouter and status == 429:
            return (
                "OpenRouter rate-limited the request (429). Free models share a "
                "tighter quota — wait a moment and retry, or switch to a paid model."
            )
        if is_openrouter and status and 500 <= status < 600:
            return (
                f"OpenRouter upstream error ({status}). The selected provider may "
                "be down — try again, or switch models with /agent → Change LLM."
            )
        return f"(error: {e})"
