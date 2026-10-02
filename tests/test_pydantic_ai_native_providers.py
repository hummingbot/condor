"""Every natively resolved provider prefix still builds a model (FEAT-130).

pydantic-ai 2.x narrowed the meta package's default extras: a prefix Condor
offers but whose SDK is not installed fails only when a user picks it, as an
``ImportError`` deep inside ``start()``. Building the model needs no network —
just the provider's package and a key in the environment.
"""

import asyncio

import pytest

from condor.acp.pydantic_ai_client import PydanticAIClient

NATIVE = [
    ("openai:gpt-4o", "OPENAI_API_KEY"),
    ("groq:llama-3.3-70b-versatile", "GROQ_API_KEY"),
    ("anthropic:claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
    ("google:gemini-2.5-flash", "GOOGLE_API_KEY"),
]


@pytest.mark.parametrize("agent_key,env_var", NATIVE)
def test_a_native_provider_key_builds_a_model(agent_key, env_var, monkeypatch):
    monkeypatch.setenv(env_var, "test-key")

    model = asyncio.run(PydanticAIClient(agent_key)._build_model())

    assert model.model_name == agent_key.partition(":")[2]


def test_an_openai_compatible_backend_builds_the_null_safe_chat_model():
    from pydantic_ai.models.openai import OpenAIChatModel

    model = asyncio.run(PydanticAIClient("ollama:llama3.1")._build_model())

    assert isinstance(model, OpenAIChatModel)
    assert type(model).__name__ == "_NullContentSafeOpenAIChatModel"
