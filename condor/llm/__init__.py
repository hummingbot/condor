"""Platform-neutral LLM/model-catalog foundations (ARCH-190).

Everything here answers "which models can Condor run, and are they ready?" for
every surface — the Telegram bot, the web dashboard, the setup wizard and the
MCP tools. Nothing in this package may import from ``handlers/`` or
``condor.web``: the layering is handlers -> condor, never the reverse.

Modules:
- :mod:`condor.llm.options` — the agent/model catalog (``AGENT_OPTIONS``) and
  the resolved default (``DEFAULT_AGENT``).
- :mod:`condor.llm.readiness` — "can this machine actually run that model?".
- :mod:`condor.llm.openrouter_models` — the OpenRouter catalog fetch.
- :mod:`condor.llm.custom_models` — OpenAI-compatible custom endpoints.
"""
