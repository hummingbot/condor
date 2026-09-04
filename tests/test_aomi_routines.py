"""The shared ``aomi_*`` routines and the one client seam they hang off.

Every routine gets its ``PipelineClient`` from ``condor.aomi_client.
get_pipeline_client`` and nothing else, so one monkeypatch stands in for Aomi
here. The fake mirrors the client's surface exactly — async iterators for the
listings, awaitables for the reads — because a routine that did ``await
client.list_apps()`` would pass a looser fake and fail against the real thing.

The routines are loaded the way production loads them, from their file in
``agents/_shared/routines`` (see ``tests/conftest.py``), at import time so the
suite's isolating fixture has not yet moved the agents root.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import condor.aomi_client as aomi_client
from routines.base import RoutineResult, discover_routines_from_path
from tests.conftest import load_shared_routine

catalog = load_shared_routine("aomi_catalog")
read = load_shared_routine("aomi_read")
skill = load_shared_routine("aomi_skill")

SHARED_ROOT = Path(__file__).resolve().parent.parent / "agents" / "_shared" / "routines"


@dataclass
class _Entry:
    name: str
    kind: str = "operation"
    href: str = ""


@dataclass
class _Descriptor:
    """The attributes of ``aomi.pipeline.models.OperationDescriptor`` the routine reads."""

    name: str
    description: str | None = ""
    input_schema: dict = field(default_factory=dict)

    @property
    def required_args(self) -> list[str]:
        return list(self.input_schema.get("required") or [])

    @property
    def properties(self) -> dict:
        return self.input_schema.get("properties") or {}


class FakePipeline:
    """Enough of ``PipelineClient`` for the three routines, with a call log."""

    def __init__(
        self,
        apps=None,
        operations=None,
        descriptors=None,
        skills=None,
        skill_text="",
        read_result=None,
        fail_list=None,
        fail_describe=(),
        fail_read=None,
        fail_skill=None,
    ):
        self.apps = apps or {}
        self.operations = operations or {}
        self.descriptors = descriptors or {}
        self.skills = skills or []
        self.skill_text = skill_text
        self.read_result = read_result if read_result is not None else {"ok": True}
        self.fail_list = fail_list
        self.fail_describe = set(fail_describe)
        self.fail_read = fail_read
        self.fail_skill = fail_skill
        self.calls: list[tuple] = []
        self.closed = False

    async def list_apps(self):
        self.calls.append(("list_apps",))
        if self.fail_list:
            raise self.fail_list
        for name in self.apps:
            yield _Entry(name, kind="app")

    async def list_operations(self, app):
        self.calls.append(("list_operations", app))
        if self.fail_list:
            raise self.fail_list
        for name in self.apps.get(app, []):
            yield _Entry(name)

    async def describe_operation(self, app, op):
        self.calls.append(("describe_operation", app, op))
        if (app, op) in self.fail_describe:
            raise RuntimeError(f"descriptor {op} exploded")
        return self.descriptors[(app, op)]

    async def list_skills(self):
        self.calls.append(("list_skills",))
        if self.fail_skill:
            raise self.fail_skill
        for name in self.skills:
            yield _Entry(name, kind="skill")

    async def skill_markdown(self, name):
        self.calls.append(("skill_markdown", name))
        if self.fail_skill:
            raise self.fail_skill
        return self.skill_text

    async def read(self, chain, name, arguments=None):
        self.calls.append(("read", chain, name, dict(arguments or {})))
        if self.fail_read:
            raise self.fail_read
        return self.read_result

    async def close(self):
        self.closed = True


def _use(monkeypatch, pipeline) -> FakePipeline:
    """Hand ``pipeline`` to every routine through the one seam."""
    monkeypatch.setattr(aomi_client, "get_pipeline_client", lambda: pipeline)
    # The routines import the factory lazily inside ``run``; a module-level copy
    # would be the bug this guards against, so patch it too if one ever appears.
    for module in (catalog, read, skill):
        monkeypatch.setattr(
            module, "get_pipeline_client", lambda: pipeline, raising=False
        )
    return pipeline


def _catalog_pipeline() -> FakePipeline:
    return FakePipeline(
        apps={"uniswap": ["swap", "quote"], "aave": ["supply"]},
        descriptors={
            ("uniswap", "swap"): _Descriptor(
                "swap",
                "Swap tokens on Uniswap.\nSecond line is not shown.",
                {
                    "required": ["token_in", "amount"],
                    "properties": {"token_in": {}, "amount": {}, "slippage": {}},
                },
            ),
            ("uniswap", "quote"): _Descriptor("quote", "Quote a swap."),
            ("aave", "supply"): _Descriptor(
                "supply", "Supply to Aave.", {"required": ["asset"]}
            ),
        },
    )


# ── Config contracts ─────────────────────────────────────────────────────────


def test_config_defaults_and_descriptions():
    assert catalog.Config().model_dump() == {
        "app": "",
        "describe": True,
        "max_operations": 40,
    }
    assert read.Config().model_dump() == {
        "op": "context",
        "chain_id": 8453,
        "address": "",
        "args_json": "{}",
    }
    assert skill.Config().model_dump() == {"skill": ""}
    for module in (catalog, read, skill):
        first = module.Config.__doc__.strip().split("\n")[0]
        assert first and "Aomi" in first
        assert module.CATEGORY == "DeFi"


# ── aomi_catalog ─────────────────────────────────────────────────────────────


def test_catalog_lists_apps_operations_and_stars_required_args(monkeypatch):
    pipeline = _use(monkeypatch, _catalog_pipeline())

    result = asyncio.run(catalog.run(catalog.Config(), context=None))

    assert isinstance(result, RoutineResult)
    text = result.text
    assert text.startswith("# Aomi Pipeline catalog")
    assert "## uniswap (2 operations)" in text
    assert "## aave (1 operations)" in text
    assert (
        "- `swap` — Swap tokens on Uniswap. (args: token_in*, amount*, slippage)"
        in text
    )
    assert "Second line" not in text
    assert "- `supply` — Supply to Aave. (args: asset*)" in text
    assert "aomi_read" in text and "onchain_executor" in text
    assert result.table_columns == ["app", "operation", "args"]
    assert {
        "app": "uniswap",
        "operation": "swap",
        "args": "token_in*, amount*, slippage",
    } in (result.table_data)
    assert len(result.table_data) == 3
    assert pipeline.closed


def test_catalog_scopes_to_one_app_and_can_skip_descriptors(monkeypatch):
    pipeline = _use(monkeypatch, _catalog_pipeline())

    result = asyncio.run(
        catalog.run(catalog.Config(app="aave", describe=False), context=None)
    )

    assert "## aave (1 operations)" in result.text
    assert "uniswap" not in result.text
    assert ("list_apps",) not in pipeline.calls
    assert not any(c[0] == "describe_operation" for c in pipeline.calls)
    assert result.table_data == [{"app": "aave", "operation": "supply", "args": ""}]


def test_catalog_tolerates_one_failing_descriptor(monkeypatch):
    pipeline = _catalog_pipeline()
    pipeline.fail_describe = {("uniswap", "quote")}
    _use(monkeypatch, pipeline)

    result = asyncio.run(catalog.run(catalog.Config(), context=None))

    assert isinstance(result, RoutineResult)
    assert "- `quote` — (describe failed: descriptor quote exploded)" in result.text
    assert "- `swap` — Swap tokens on Uniswap." in result.text
    assert len(result.table_data) == 3


def test_catalog_caps_operations_per_app(monkeypatch):
    _use(monkeypatch, _catalog_pipeline())

    result = asyncio.run(
        catalog.run(catalog.Config(max_operations=1, describe=False), context=None)
    )

    assert "- … 1 more not shown" in result.text
    assert len(result.table_data) == 2


def test_catalog_listing_failure_is_one_line_not_a_raise(monkeypatch):
    pipeline = _use(monkeypatch, FakePipeline(fail_list=RuntimeError("401 nope")))

    result = asyncio.run(catalog.run(catalog.Config(), context=None))

    assert result == "Aomi catalog failed: 401 nope"
    assert pipeline.closed


# ── aomi_read ────────────────────────────────────────────────────────────────


def test_read_builds_args_and_renders_json(monkeypatch):
    pipeline = _use(
        monkeypatch, FakePipeline(read_result={"balance_native": "1.5", "nonce": 7})
    )

    result = asyncio.run(
        read.run(
            read.Config(
                op="account", chain_id=1, address="0xabc", args_json='{"x": 1}'
            ),
            context=None,
        )
    )

    assert pipeline.calls == [
        ("read", "evm", "account", {"chain_id": 1, "address": "0xabc", "x": 1})
    ]
    assert result.text.startswith("# Aomi evm/account")
    assert "- address: 0xabc" in result.text
    assert '"nonce": 7' in result.text
    assert "```json" in result.text
    assert pipeline.closed


def test_read_context_sends_no_arguments_and_says_so(monkeypatch):
    pipeline = _use(monkeypatch, FakePipeline(read_result={"block": 1}))

    result = asyncio.run(read.run(read.Config(), context=None))

    # The context tool has no inputs: it reports the wallet's active chain.
    assert pipeline.calls == [("read", "evm", "context", {})]
    assert (
        "supported_chains" in result.text and "chain_id is not an input" in result.text
    )


def test_read_token_holdings_maps_address_to_holder(monkeypatch):
    pipeline = _use(monkeypatch, FakePipeline())

    asyncio.run(
        read.run(
            read.Config(
                op="token-holdings",
                address="0xholder",
                args_json='{"token_address": "0xtoken"}',
            ),
            context=None,
        )
    )

    assert pipeline.calls[0][3] == {
        "chain_id": 8453,
        "holder_address": "0xholder",
        "token_address": "0xtoken",
    }


def test_read_token_holdings_needs_a_token_address(monkeypatch):
    pipeline = _use(monkeypatch, FakePipeline())

    result = asyncio.run(read.run(read.Config(op="token-holdings"), context=None))

    assert result.startswith("Invalid config:") and "token_address" in result
    assert pipeline.calls == []


@pytest.mark.parametrize(
    "config",
    [
        {"op": "balance"},
        {"args_json": "{not json"},
        {"args_json": "[1, 2]"},
    ],
)
def test_read_rejects_a_bad_op_or_bad_json_before_calling(monkeypatch, config):
    pipeline = _use(monkeypatch, FakePipeline())

    result = asyncio.run(read.run(read.Config(**config), context=None))

    assert isinstance(result, str) and result.startswith("Invalid config:")
    assert pipeline.calls == []


def test_read_truncates_a_huge_result(monkeypatch):
    _use(monkeypatch, FakePipeline(read_result={"blob": "x" * 20_000}))

    result = asyncio.run(read.run(read.Config(), context=None))

    assert "(truncated)" in result.text
    assert len(result.text) < 7_000


def test_read_failure_is_one_line(monkeypatch):
    pipeline = _use(monkeypatch, FakePipeline(fail_read=RuntimeError("rpc down")))

    result = asyncio.run(read.run(read.Config(), context=None))

    assert result == "Aomi read failed: rpc down"
    assert pipeline.closed


# ── aomi_skill ───────────────────────────────────────────────────────────────


def test_skill_returns_the_playbook_text(monkeypatch):
    pipeline = _use(monkeypatch, FakePipeline(skill_text="# Lending\n\nDo this."))

    result = asyncio.run(skill.run(skill.Config(skill="lending"), context=None))

    assert result.text == "# Lending\n\nDo this."
    assert pipeline.calls == [("skill_markdown", "lending")]
    assert pipeline.closed


def test_skill_blank_lists_the_skills(monkeypatch):
    _use(monkeypatch, FakePipeline(skills=["lending", "swaps"]))

    result = asyncio.run(skill.run(skill.Config(), context=None))

    assert "# Aomi skills (2)" in result.text
    assert "- `lending`" in result.text and "- `swaps`" in result.text


def test_skill_failure_is_one_line(monkeypatch):
    _use(monkeypatch, FakePipeline(fail_skill=RuntimeError("404 no such skill")))

    result = asyncio.run(skill.run(skill.Config(skill="nope"), context=None))

    assert result == "Aomi skill failed: 404 no such skill"


# ── The seam itself ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", [catalog, read, skill])
def test_missing_token_renders_the_help_text(monkeypatch, module):
    """The real factory with no ``AOMI_TOKEN``: every routine says how to set one."""
    import utils.config  # noqa: F401  (load_dotenv() runs once; delenv must come after)

    monkeypatch.delenv("AOMI_TOKEN", raising=False)

    result = asyncio.run(module.run(module.Config(), context=None))

    assert result == aomi_client.MISSING_TOKEN_MESSAGE
    assert "AOMI_TOKEN" in result and "AOMI_URL" in result
    assert "python -m aomi.auth mint" in result


def test_factory_reads_the_environment_at_call_time(monkeypatch):
    """Settings are read per call, not at import: a token added after startup counts."""
    import utils.config  # noqa: F401

    class RecordingClient:
        instances: list = []

        def __init__(self, base_url, token_provider):
            self.base_url = base_url
            self.token_provider = token_provider
            RecordingClient.instances.append(self)

    pipeline_mod = types.ModuleType("aomi.pipeline")
    pipeline_mod.PipelineClient = RecordingClient
    aomi_mod = types.ModuleType("aomi")
    aomi_mod.pipeline = pipeline_mod
    monkeypatch.setitem(sys.modules, "aomi", aomi_mod)
    monkeypatch.setitem(sys.modules, "aomi.pipeline", pipeline_mod)

    monkeypatch.delenv("AOMI_TOKEN", raising=False)
    monkeypatch.delenv("AOMI_URL", raising=False)
    assert aomi_client.get_pipeline_client() is None
    assert aomi_client.aomi_configured() is False
    assert aomi_client.aomi_settings() == (aomi_client.DEFAULT_AOMI_URL, "")

    monkeypatch.setenv("AOMI_TOKEN", "tok-1")
    first = aomi_client.get_pipeline_client()
    assert isinstance(first, RecordingClient)
    assert (first.base_url, first.token_provider) == (
        aomi_client.DEFAULT_AOMI_URL,
        "tok-1",
    )
    assert aomi_client.aomi_configured() is True

    monkeypatch.setenv("AOMI_URL", "https://chat.aomi.dev/")
    monkeypatch.setenv("AOMI_TOKEN", "  tok-2  ")
    second = aomi_client.get_pipeline_client()
    assert (second.base_url, second.token_provider) == (
        "https://chat.aomi.dev/",
        "tok-2",
    )


def test_factory_returns_none_when_aomi_is_not_installed(monkeypatch):
    import utils.config  # noqa: F401

    monkeypatch.setenv("AOMI_TOKEN", "tok")
    monkeypatch.setitem(sys.modules, "aomi", None)
    monkeypatch.setitem(sys.modules, "aomi.pipeline", None)

    assert aomi_client.get_pipeline_client() is None


def test_default_url_is_the_clients_staging_origin():
    models = pytest.importorskip("aomi.pipeline")
    assert aomi_client.DEFAULT_AOMI_URL == models.PipelineClient.STAGING


def test_catalog_reads_the_real_descriptor_shape(monkeypatch):
    """Pin the wire shape: the routine's duck-typing must fit the real dataclass."""
    models = pytest.importorskip("aomi.pipeline.models")
    pipeline = _catalog_pipeline()
    pipeline.apps = {"uniswap": ["swap"]}
    pipeline.descriptors = {
        ("uniswap", "swap"): models.OperationDescriptor.from_json(
            {
                "name": "swap",
                "description": "Swap tokens.",
                "inputSchema": {
                    "required": ["amount"],
                    "properties": {"amount": {}, "slippage": {}},
                },
            }
        )
    }
    _use(monkeypatch, pipeline)

    result = asyncio.run(catalog.run(catalog.Config(), context=None))

    assert "- `swap` — Swap tokens. (args: amount*, slippage)" in result.text


# ── Discoverability ──────────────────────────────────────────────────────────


def test_routines_are_discoverable_from_the_shared_root():
    found = discover_routines_from_path(SHARED_ROOT, agent_slug=None, force_reload=True)

    for name in ("aomi_catalog", "aomi_read", "aomi_skill"):
        assert name in found, name
        assert found[name].category == "DeFi"
        assert found[name].source == "global"
        assert "Aomi" in found[name].description


def test_every_agent_inherits_the_aomi_routines(monkeypatch):
    from condor import paths
    from routines.base import assistant_routines

    monkeypatch.delenv(paths.AGENTS_ROOT_ENV, raising=False)

    available = assistant_routines("some-agent", force_reload=True)

    assert {"aomi_catalog", "aomi_read", "aomi_skill"} <= set(available)
    assert all(available[n].category == "DeFi" for n in ("aomi_catalog", "aomi_read"))


def test_config_json_is_the_shape_the_dashboard_renders():
    """Every field is a plain scalar, so the routine form needs no custom widget."""
    for module in (catalog, read, skill):
        for name, info in module.Config.model_fields.items():
            assert info.annotation in (str, bool, int), (module.__name__, name)
            assert info.description, (module.__name__, name)
    json.dumps(read.Config().model_dump())
