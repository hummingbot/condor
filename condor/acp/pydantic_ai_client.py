"""Pydantic AI agent client -- uses pydantic-ai with MCP tool servers.

Drop-in alternative to ACPClient for open-source / local models.
Supports any model backend that pydantic-ai supports: ollama, openai-compatible
(LM Studio), groq, anthropic, etc.

Yields the same ACPEvent types so TickEngine can consume it identically.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import AsyncExitStack
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
    if any(provider in model_lower for provider in ["openai:", "anthropic:", "groq:", "google:"]):
        log.info("Auto-detected cloud provider → tool_filter_mode=full")
        return "full"

    # Extract parameter count (e.g., "7b", "14b", "72b", "32b")
    # Matches patterns like: 7b, 8b, 14b, 32b, 72b, 1.5b, 2.7b, etc.
    size_match = re.search(r'(\d+(?:\.\d+)?)\s*[bB](?![a-z])', model_lower)

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
PYDANTIC_AI_PREFIXES = frozenset({"ollama", "openai", "groq", "anthropic", "google", "lmstudio"})

# Default base URLs for local model providers
DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}


def is_pydantic_ai_model(agent_key: str) -> bool:
    """Check if an agent_key should use the PydanticAI client."""
    prefix = agent_key.split(":", 1)[0] if ":" in agent_key else ""
    return prefix in PYDANTIC_AI_PREFIXES


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
    """

    def __init__(
        self,
        model: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        permission_callback: PermissionCallback | None = None,
        extra_env: dict[str, str] | None = None,
        base_url: str | None = None,
        tool_filter_mode: str | None = None,  # "essential", "moderate", "full", or None for auto-detect
    ):
        self.model_name = model
        self.mcp_server_configs = mcp_servers or []
        self.permission_callback = permission_callback
        self.extra_env = extra_env
        self.base_url = base_url
        # Auto-detect filter mode based on model if not explicitly set
        self.tool_filter_mode = tool_filter_mode or _infer_tool_filter_mode(model)
        self._mcp_servers: list[Any] = []
        self._exit_stack: AsyncExitStack | None = None
        self._agent: Any = None

    def _build_model(self) -> Any:
        """Build the pydantic-ai model object with sensible defaults.

        All local providers (ollama, lmstudio) are routed through OpenAI-compatible
        endpoints so we control the base_url explicitly. This avoids requiring
        environment variables like OLLAMA_BASE_URL.

        Resolution:
          - ollama:model    → OpenAI-compat at localhost:11434/v1 (or custom base_url)
          - lmstudio:model  → OpenAI-compat at localhost:1234/v1 (or custom base_url)
          - openai:model    → OpenAI API (or custom base_url for vLLM, etc.)
          - groq/anthropic  → standard pydantic-ai resolution
        """
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider

        prefix, _, model_id = self.model_name.partition(":")
        base_url = self.base_url

        # Local providers: always use OpenAI-compatible endpoint with default URL
        if prefix in DEFAULT_BASE_URLS:
            base_url = base_url or DEFAULT_BASE_URLS[prefix]
            if not model_id:
                model_id = self._resolve_default_local_model(prefix=prefix, base_url=base_url)
            provider = OpenAIProvider(base_url=base_url, api_key="not-needed")
            return OpenAIModel(model_id, provider=provider)

        # OpenAI with custom base_url (vLLM, TGI, etc.)
        if prefix == "openai" and base_url:
            provider = OpenAIProvider(base_url=base_url, api_key="not-needed")
            return OpenAIModel(model_id, provider=provider)

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

        self._exit_stack = AsyncExitStack()

        # Build MCP server instances from configs
        # Each config has: name, command, args, env
        # Don't use deferred loading - it adds unnecessary complexity:
        # - Cloud models can handle all tools easily
        # - Local models struggle with the search_tools workflow
        # Just show all tools upfront for best results
        toolsets = []
        for srv_config in self.mcp_server_configs:
            command = srv_config["command"]
            args = srv_config.get("args", [])

            # Build env dict: merge extra_env + per-server env vars
            env = dict(self.extra_env or {})
            for env_entry in srv_config.get("env", []):
                if isinstance(env_entry, dict):
                    env[env_entry["name"]] = env_entry["value"]

            mcp_server = MCPServerStdio(
                command,
                args=args,
                env=env if env else None,
            )

            # Add MCP server directly (no deferred loading)
            toolsets.append(mcp_server)
            self._mcp_servers.append(mcp_server)

        model = self._build_model()

        self._agent = Agent(
            model,
            toolsets=toolsets,
        )

        log.info(
            "PydanticAI client ready: model=%s, mcp_servers=%d",
            self.model_name,
            len(self._mcp_servers),
        )

    async def stop(self) -> None:
        """Clean up MCP server connections."""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._mcp_servers.clear()
        self._agent = None

    @property
    def alive(self) -> bool:
        return self._agent is not None

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
        """
        assert self._agent is not None, "Client not started"

        start_time = time.monotonic()

        try:
            from pydantic_ai.agent import CallToolsNode, ModelRequestNode
            from pydantic_ai.messages import TextPart, ToolCallPart
            from pydantic_graph import End

            async with self._agent.run_mcp_servers():
                async with self._agent.iter(text) as run:
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
                                            "input": part.args if isinstance(part.args, dict) else {},
                                        }
                                        options = [
                                            {"optionId": "allow", "kind": "allow_once"},
                                            {"optionId": "deny", "kind": "deny"},
                                        ]
                                        result = await self.permission_callback(
                                            tool_call_info, options
                                        )
                                        outcome = result.get("outcome", {})
                                        if isinstance(outcome, dict) and outcome.get("outcome") == "cancelled":
                                            yield ToolCallEvent(
                                                tool_call_id=tool_id,
                                                title=tool_name,
                                                status="blocked",
                                                kind="mcp",
                                                input=part.args if isinstance(part.args, dict) else None,
                                            )
                                            continue

                                    yield ToolCallEvent(
                                        tool_call_id=tool_id,
                                        title=tool_name,
                                        status="in_progress",
                                        kind="mcp",
                                        input=part.args if isinstance(part.args, dict) else None,
                                    )

                                    yield ToolCallUpdate(
                                        tool_call_id=tool_id,
                                        status="completed",
                                    )

            yield PromptDone(stop_reason="end_turn")

        except asyncio.TimeoutError:
            yield PromptDone(stop_reason="timeout")
        except Exception as e:
            log.exception("PydanticAI prompt error: %s", e)
            yield TextChunk(text=f"(error: {e})")
            yield PromptDone(stop_reason="error")
