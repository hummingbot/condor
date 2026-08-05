"""Tests for custom OpenAI-compatible endpoints.

Agent keys look like "custom@<endpoint>:<model-id>", where the endpoint names
one of the user's saved records in condor/preferences.py. The unnamed
"custom:<model-id>" form predates named endpoints and is still accepted.
"""

import asyncio

import pytest
from aiohttp import web

from condor.acp.pydantic_ai_client import (
    PydanticAIClient,
    _infer_tool_filter_mode,
    is_pydantic_ai_model,
    model_prefix,
)
from condor.preferences import (
    build_custom_agent_key,
    find_custom_provider,
    get_custom_providers,
    parse_custom_agent_key,
    remove_custom_provider,
    resolve_custom_endpoint,
    sanitize_provider_name,
    save_custom_provider,
    set_active_agent_key,
    suggest_provider_name,
    unique_provider_name,
)
from handlers.agents.custom_models import (
    CustomProviderError,
    _extract_model_ids,
    fetch_models,
    find_by_token,
    format_model_label,
    model_token,
    normalize_base_url,
)


# -- agent_key routing --


def test_custom_is_pydantic_ai_model():
    assert is_pydantic_ai_model("custom:llama-3.3-70b")
    assert is_pydantic_ai_model("custom:model:with:colons")
    assert is_pydantic_ai_model("custom@venice:llama-3.3-70b")
    assert not is_pydantic_ai_model("claude-code")


def test_model_prefix_strips_endpoint_name():
    assert model_prefix("custom@venice:llama-3.3-70b") == "custom"
    assert model_prefix("custom:llama-3.3-70b") == "custom"
    assert model_prefix("openrouter:anthropic/claude-sonnet-4.5") == "openrouter"
    assert model_prefix("claude-code") == ""


def test_custom_tool_filter_follows_the_model_not_the_prefix():
    # "custom:" says nothing about capability — a 4B model behind a local vLLM
    # would choke on the full toolset, so the size heuristics still apply.
    assert _infer_tool_filter_mode("custom:qwen3-4b") == "essential"
    assert _infer_tool_filter_mode("custom@local:qwen3-14b") == "moderate"
    assert _infer_tool_filter_mode("custom@together:llama-3.3-70b") == "full"


# -- agent key composition --


def test_build_and_parse_custom_agent_key():
    key = build_custom_agent_key("Venice", "llama-3.3-70b")
    assert key == "custom@Venice:llama-3.3-70b"
    assert parse_custom_agent_key(key) == ("Venice", "llama-3.3-70b")


def test_parse_custom_agent_key_keeps_separators_in_model_id():
    # Model ids routinely contain slashes and colons; only the first colon
    # after the prefix is structural.
    key = build_custom_agent_key("together", "meta-llama/Llama-3.3-70B:free")
    assert parse_custom_agent_key(key) == (
        "together",
        "meta-llama/Llama-3.3-70B:free",
    )


def test_parse_custom_agent_key_legacy_and_foreign():
    assert parse_custom_agent_key("custom:llama-3.3-70b") == (None, "llama-3.3-70b")
    assert parse_custom_agent_key("openrouter:openai/gpt-4o") == (None, "")
    assert parse_custom_agent_key("claude-code") == (None, "")


def test_sanitize_provider_name_removes_key_separators():
    assert sanitize_provider_name("Venice AI") == "Venice-AI"
    assert sanitize_provider_name("a:b@c") == "a-b-c"
    assert sanitize_provider_name("!!!") == "custom"
    assert len(sanitize_provider_name("x" * 100)) == 32


def test_suggest_provider_name():
    assert suggest_provider_name("https://api.venice.ai/api/v1") == "Venice"
    assert suggest_provider_name("http://localhost:8000/v1") == "localhost"
    assert suggest_provider_name("http://192.168.1.5:8000/v1") == "192.168.1.5"


# -- endpoint storage --


def test_save_find_and_remove_provider():
    user_data = {}
    saved = save_custom_provider(user_data, "Venice", "https://x.example/v1", "sk-1")
    assert saved == {
        "name": "Venice",
        "base_url": "https://x.example/v1",
        "api_key": "sk-1",
    }
    assert find_custom_provider(user_data, "Venice") == saved
    assert remove_custom_provider(user_data, "Venice") is True
    assert get_custom_providers(user_data) == []
    assert remove_custom_provider(user_data, "Venice") is False


def test_save_provider_upserts_by_name():
    user_data = {}
    save_custom_provider(user_data, "Venice", "https://a.example/v1", "sk-1")
    save_custom_provider(user_data, "Venice", "https://b.example/v1", "sk-2")
    providers = get_custom_providers(user_data)
    assert len(providers) == 1
    assert providers[0]["base_url"] == "https://b.example/v1"


def test_unique_provider_name_suffixes_collisions():
    user_data = {}
    save_custom_provider(user_data, "Venice", "https://a.example/v1")
    assert unique_provider_name(user_data, "Venice") == "Venice-2"
    assert unique_provider_name(user_data, "Other") == "Other"


def test_find_provider_without_name_resolves_only_when_unambiguous():
    user_data = {}
    save_custom_provider(user_data, "One", "https://a.example/v1")
    # A legacy "custom:<model>" key has no endpoint name to match on
    assert find_custom_provider(user_data, None)["name"] == "One"
    save_custom_provider(user_data, "Two", "https://b.example/v1")
    assert find_custom_provider(user_data, None) is None


def test_picker_sentinels_are_flagged_not_shape_inferred():
    # Clients must not infer "is a picker" from the key ending in ":" —
    # "ollama:"/"lmstudio:" also do and ARE startable ("that backend's default
    # model"). Inferring it silently hid them from the web model picker.
    from handlers.agents._shared import AGENT_OPTIONS, selectable_agent_options

    pickers = {k for k, v in AGENT_OPTIONS.items() if v.get("picker")}
    assert pickers == {"openrouter:", "custom:"}

    startable = selectable_agent_options()
    assert pickers.isdisjoint(startable)
    assert {"ollama:", "lmstudio:"} <= set(startable)


def test_resolve_custom_endpoint():
    # Every surface that builds a model needs this, not just chat sessions —
    # consult/delegate/engine previously had no way to reach a saved endpoint.
    user_data = {}
    save_custom_provider(user_data, "Venice", "https://x.example/v1", "sk-1")
    key = build_custom_agent_key("Venice", "claude-sonnet-4-6")
    assert resolve_custom_endpoint(key, user_data=user_data) == (
        "https://x.example/v1",
        "sk-1",
    )


def test_resolve_custom_endpoint_passes_through_other_keys():
    # Callers invoke it unconditionally, so non-custom keys must be inert
    assert resolve_custom_endpoint("claude-code", user_data={}) == (None, None)
    assert resolve_custom_endpoint("ollama:llama3.1", user_data={}) == (None, None)
    assert resolve_custom_endpoint("custom@Missing:m", user_data={}) == (None, None)


def test_resolve_custom_endpoint_env_fallback(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "sk-env")
    assert resolve_custom_endpoint("custom:m", user_data={}) == (
        "https://env.example/v1",
        "sk-env",
    )


def test_active_agent_key_round_trips_through_preferences():
    # The MCP subprocess can't read the PTB pickle, so the live chat selection
    # is mirrored here — this is what lets a new Agent inherit it.
    user_data = {}
    set_active_agent_key(user_data, "custom@Venice:claude-sonnet-4-6")
    agent = user_data["user_preferences"]["agent"]
    assert agent["active_agent_key"] == "custom@Venice:claude-sonnet-4-6"


def test_legacy_custom_llm_is_migrated():
    # The first iteration stored one endpoint in raw user_data, outside the
    # preference system. It should move across on first read, once.
    user_data = {"custom_llm": {"base_url": "https://api.venice.ai/api/v1", "api_key": "sk-old"}}
    providers = get_custom_providers(user_data)
    assert providers == [
        {"name": "Venice", "base_url": "https://api.venice.ai/api/v1", "api_key": "sk-old"}
    ]
    assert "custom_llm" not in user_data
    assert get_custom_providers(user_data) == providers


# -- URL normalization --


def test_normalize_base_url_variants():
    assert (
        normalize_base_url("https://api.venice.ai/api/v1")
        == "https://api.venice.ai/api/v1"
    )
    assert normalize_base_url("api.venice.ai/api/v1/") == "https://api.venice.ai/api/v1"
    assert normalize_base_url(" https://x.example/v1/chat/completions ") == "https://x.example/v1"
    assert normalize_base_url("http://localhost:8000/v1/models") == "http://localhost:8000/v1"


def test_normalize_base_url_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_base_url("")
    with pytest.raises(ValueError):
        normalize_base_url("ftp://foo.example")
    with pytest.raises(ValueError):
        normalize_base_url("https://")


def test_normalize_base_url_blocks_metadata_endpoints():
    # Cloud instance metadata hands out credentials to any HTTP GET
    with pytest.raises(ValueError, match="link-local"):
        normalize_base_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="not an allowed endpoint host"):
        normalize_base_url("http://metadata.google.internal/v1")


def test_normalize_base_url_allows_local_servers_by_default():
    # Local model servers are the most common use of this feature
    assert normalize_base_url("http://localhost:8000/v1") == "http://localhost:8000/v1"
    assert normalize_base_url("http://192.168.1.5:1234/v1") == "http://192.168.1.5:1234/v1"


def test_normalize_base_url_strict_mode_blocks_private(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BLOCK_PRIVATE_URLS", "true")
    with pytest.raises(ValueError, match="private address"):
        normalize_base_url("http://192.168.1.5:1234/v1")
    with pytest.raises(ValueError, match="private address"):
        normalize_base_url("http://127.0.0.1:8000/v1")


# -- /models payload parsing --


def test_extract_model_ids():
    payload = {
        "data": [
            {"id": "zeta"},
            {"id": "Alpha"},
            {"id": "zeta"},  # dupe
            {"id": ""},  # empty
            {"no_id": 1},
            "junk",
        ]
    }
    assert _extract_model_ids(payload) == ["Alpha", "zeta"]


def test_extract_model_ids_bad_shapes():
    assert _extract_model_ids(None) == []
    assert _extract_model_ids({"models": [{"id": "x"}]}) == []
    assert _extract_model_ids({"data": "nope"}) == []


def test_extract_model_ids_drops_non_chat_modalities():
    # Providers serve embeddings/image/audio models from the same /models list;
    # picking one succeeds here and then fails opaquely at prompt time.
    payload = {
        "data": [
            {"id": "llama-3.3-70b", "type": "text"},
            {"id": "flux-dev", "type": "image"},
            {"id": "tts-kokoro", "type": "tts"},
            {"id": "bge-m3", "type": "embedding"},
        ]
    }
    assert _extract_model_ids(payload) == ["llama-3.3-70b"]
    assert len(_extract_model_ids(payload, chat_only=False)) == 4


def test_extract_model_ids_falls_back_to_name_heuristics():
    # Stock OpenAI reports object="model" for everything, so the id is all
    # there is to go on.
    payload = {
        "data": [
            {"id": "gpt-4o", "object": "model"},
            {"id": "text-embedding-3-large", "object": "model"},
            {"id": "whisper-1", "object": "model"},
            {"id": "dall-e-3", "object": "model"},
        ]
    }
    assert _extract_model_ids(payload) == ["gpt-4o"]


# -- picker helpers --


def test_model_token_is_stable_and_resolvable():
    models = ["meta-llama/Llama-3.3-70B", "qwen3-4b"]
    for model in models:
        assert find_by_token(models, model_token(model)) == model
    # callback_data is capped at 64 bytes; "agent:cu_pick:" + token must fit
    assert len(f"agent:cu_pick:{model_token(models[0])}") < 64
    assert find_by_token(models, "deadbeef") is None


def test_format_model_label_truncates_through_the_middle():
    long_id = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"
    label = format_model_label(long_id)
    assert len(label) == 38
    # The tail is what distinguishes variants, so it must survive
    assert label.startswith("meta-llama/")
    assert label.endswith("Free")
    assert format_model_label("qwen3-4b") == "qwen3-4b"


# -- model building --


def test_build_model_requires_base_url(monkeypatch):
    monkeypatch.delenv("CUSTOM_LLM_BASE_URL", raising=False)
    client = PydanticAIClient(model="custom:foo")
    with pytest.raises(RuntimeError, match="base URL"):
        client._build_model()


def test_build_model_requires_model_id():
    client = PydanticAIClient(model="custom:", base_url="https://api.example.com/v1")
    with pytest.raises(RuntimeError, match="model id"):
        client._build_model()


def test_build_model_uses_base_url_and_key():
    client = PydanticAIClient(
        model="custom:llama-3.3-70b",
        base_url="https://api.venice.ai/api/v1",
        api_key="sk-test",
    )
    model = client._build_model()
    assert model.model_name == "llama-3.3-70b"
    assert str(model.client.base_url).rstrip("/") == "https://api.venice.ai/api/v1"
    assert model.client.api_key == "sk-test"


def test_build_model_strips_endpoint_name_from_model_id():
    client = PydanticAIClient(
        model="custom@venice:meta-llama/Llama-3.3-70B",
        base_url="https://api.venice.ai/api/v1",
        api_key="sk-test",
    )
    model = client._build_model()
    assert model.model_name == "meta-llama/Llama-3.3-70B"


def test_build_model_env_fallbacks(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "sk-env")
    client = PydanticAIClient(model="custom:m")
    model = client._build_model()
    assert str(model.client.base_url).rstrip("/") == "https://env.example/v1"
    assert model.client.api_key == "sk-env"


# -- fetch_models against a real (local) OpenAI-compatible server --
# The suite has no pytest-asyncio, so async flows run through asyncio.run().


async def _with_mock_provider(scenario):
    """Run `scenario(base_url)` against a local /v1/models server with Bearer auth."""

    async def models_handler(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer sk-good":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"data": [{"id": "model-b"}, {"id": "model-a"}]})

    app = web.Application()
    app.router.add_get("/v1/models", models_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        await scenario(f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()


def test_fetch_models_success():
    async def scenario(base):
        resolved, models = await fetch_models(f"{base}/v1", "sk-good")
        assert resolved == f"{base}/v1"
        assert models == ["model-a", "model-b"]

    asyncio.run(_with_mock_provider(scenario))


def test_fetch_models_resolves_v1_suffix():
    # User pasted the host root — fetch_models should fall back to /v1
    async def scenario(base):
        resolved, models = await fetch_models(base, "sk-good")
        assert resolved == f"{base}/v1"
        assert models == ["model-a", "model-b"]

    asyncio.run(_with_mock_provider(scenario))


def test_fetch_models_bad_key():
    async def scenario(base):
        with pytest.raises(CustomProviderError, match="rejected the API key"):
            await fetch_models(f"{base}/v1", "sk-wrong")

    asyncio.run(_with_mock_provider(scenario))


def test_fetch_models_unreachable():
    async def scenario(_base):
        with pytest.raises(CustomProviderError, match="could not reach"):
            await fetch_models("http://127.0.0.1:9/v1", "sk-any")

    asyncio.run(_with_mock_provider(scenario))


def test_fetch_models_reports_when_only_non_chat_models_exist():
    async def models_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {"data": [{"id": "bge-m3", "type": "embedding"}, {"id": "flux", "type": "image"}]}
        )

    async def scenario():
        app = web.Application()
        app.router.add_get("/v1/models", models_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with pytest.raises(CustomProviderError, match="none of them are chat"):
                await fetch_models(f"http://127.0.0.1:{port}/v1")
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
