"""SEC-627: a routine ``Config`` default must not carry a credential.

A routine's definition is install-public (SEC-617): ``GET /routines`` serves the
``fields`` schema to every approved user, ``GET /routines/{name}/source`` serves
the file itself, and the Telegram editor pre-fills the defaults. So a key typed
as a ``Field(default=...)`` is published the moment the file lands.

The guard sits at **discovery**, in ``RoutineInfo``, because every one of those
surfaces is downstream of a ``RoutineInfo`` existing — including the source
route, which resolves through ``_discover_all`` before it reads the file and is
the reason redacting the value in the response would not have been enough.

These tests pin three things: what the detector calls a secret (and, just as
importantly, what it does not), that discovery drops an offending routine
instead of publishing it, and that the routines this repo actually ships are
clean — enumerated from the files, not from discovery, so the corpus check
cannot pass merely because the guard hid an offender.
"""

import importlib.util

import pytest
from pydantic import BaseModel, Field

import routines.base
from condor.routine_store import routine_source_roots
from routines.base import (
    RoutineInfo,
    SecretDefaultError,
    discover_routines_from_path,
    load_error,
    secret_default_reasons,
)


def _config(**fields) -> type[BaseModel]:
    """Build a routine ``Config`` class from ``name -> FieldInfo`` pairs."""
    return type(
        "Config",
        (BaseModel,),
        {
            "__doc__": "A routine",
            "__annotations__": {name: type(f.default) for name, f in fields.items()},
            **fields,
        },
    )


# Credential-shaped fixtures are joined at runtime so no literal in this file
# matches a secret scanner's pattern; the joined values are unchanged.
def _k(*parts: str) -> str:
    return "".join(parts)


# ── What counts as a secret ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field_name,default",
    [
        ("api_key", _k("sk-", "proj-abcdefghijklmnopqrstuvwxyz0123456789")),
        ("anthropic_key", _k("sk-", "ant-api03-abcdefghijklmnopqrstuvwxyz01")),
        ("stripe", _k("sk_", "live_abcdefghijklmnopqrstuvwx")),
        ("gh", _k("ghp", "_abcdefghijklmnopqrstuvwxyz0123456789")),
        ("slack", _k("xoxb", "-123456789012-abcdefghijkl")),
        ("aws", _k("AKIA", "1234567890ABCDEF")),
        ("google", _k("AIza", "SyA1234567890abcdefghijklmnopqrstuvw")),
        ("session", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abcdefg"),
        ("pem", _k("-----BEGIN RSA ", "PRIVATE KEY-----\nMIIEpAIB\n")),
        ("rpc_url", "https://mainnet.helius-rpc.com/?api-key=3f8c1a2b9d4e4f00bc11"),
        ("db_url", "postgres://condor:s3cr3tpassword@db.internal:5432/condor"),
    ],
)
def test_credential_shaped_value_is_refused(field_name, default):
    """A literal of a known credential shape is caught whatever it is called."""
    reasons = secret_default_reasons(_config(**{field_name: Field(default=default)}))
    assert reasons, f"{field_name}={default!r} slipped through"
    assert field_name in reasons[0]
    assert default not in reasons[0], "the reason must never echo the value"


@pytest.mark.parametrize(
    "field_name",
    ["password", "api_secret", "wallet_private_key", "mnemonic", "auth_token"],
)
def test_credential_named_field_with_a_live_default_is_refused(field_name):
    """A field that names a credential may not carry a real-looking default."""
    config = _config(**{field_name: Field(default="Tr0ub4dor&3xkcd")})
    assert secret_default_reasons(config)


def test_nested_default_is_walked():
    """``default={"api_key": …}`` hides nothing: containers are walked."""
    config = _config(
        creds=Field(default={"api_key": _k("sk-", "abcdefghijklmnopqrstuvwxyz0123")})
    )
    reasons = secret_default_reasons(config)
    assert reasons and "creds.api_key" in reasons[0]


def test_description_and_docstring_are_scanned():
    """A key pasted into prose is published exactly like one in a default."""
    described = _config(
        note=Field(
            default="ok",
            description="call it with " + _k("sk-", "proj-abcdefghijklmnop12"),
        )
    )
    assert secret_default_reasons(described)

    documented = _config(pair=Field(default="SOL-USDC"))
    documented.__doc__ = "Uses " + _k("AKIA", "1234567890ABCDEF") + " for the feed"
    assert secret_default_reasons(documented)


@pytest.mark.parametrize(
    "field_name,default",
    [
        # The bread and butter of a routine config, none of it a credential.
        ("trading_pair", "SOL-USDC"),
        ("connector_name", "binance_perpetual"),
        # ``token`` is a coin in this codebase, never a bearer token.
        ("token", "USDC"),
        # Empty and placeholder credentials are how a real Config declares one.
        ("api_key", ""),
        ("password", "changeme"),
        ("api_secret", "<your-secret-here>"),
        ("private_key", "${WALLET_PRIVATE_KEY}"),
        ("aws_example", "AKIAIOSFODNN7EXAMPLE"),
        # A keyless public endpoint is not a credential.
        ("rpc_url", "https://api.mainnet-beta.solana.com"),
        ("secret_len", 32),
        ("password_required", False),
    ],
)
def test_ordinary_defaults_are_left_alone(field_name, default):
    """False positives cost a working routine, so precision is pinned too."""
    assert secret_default_reasons(_config(**{field_name: Field(default=default)})) == []


def test_required_and_none_defaults_are_not_flagged():
    """A required field has no default to publish, and ``None`` is not a value."""

    class Config(BaseModel):
        """A routine"""

        api_key: str
        password: str | None = None

    assert secret_default_reasons(Config) == []


# ── The guard, at the point the routine becomes visible ──────────────────────


def test_routine_info_refuses_to_exist():
    """The refusal is in the constructor, so no surface can ever hold one."""

    class Config(BaseModel):
        """A routine"""

        api_key: str = Field(default=_k("sk-", "proj-abcdefghijklmnopqrstuvwxyz01"))

    with pytest.raises(SecretDefaultError) as exc:
        RoutineInfo(name="leaky", config_class=Config, run_fn=lambda c, ctx: None)
    assert "api_key" in str(exc.value)
    assert "sk-proj" not in str(exc.value)


CLEAN = '''
from pydantic import BaseModel, Field

class Config(BaseModel):
    """A clean routine"""
    trading_pair: str = Field(default="SOL-USDC")

async def run(config, context):
    return "ok"
'''

LEAKY = '''
from pydantic import BaseModel, Field

class Config(BaseModel):
    """A leaky routine"""
    api_key: str = Field(default="__FAKE_KEY__")

async def run(config, context):
    return "ok"
'''.replace("__FAKE_KEY__", _k("sk-", "proj-abcdefghijklmnopqrstuvwxyz01"))


def test_discovery_drops_the_leaky_routine_and_records_why(tmp_path, monkeypatch):
    """Not published anywhere: it never enters the catalog discovery returns."""
    monkeypatch.setattr(routines.base, "_path_caches", {})
    monkeypatch.setattr(routines.base, "_load_errors", {})
    (tmp_path / "clean.py").write_text(CLEAN)
    (tmp_path / "leaky.py").write_text(LEAKY)

    found = discover_routines_from_path(tmp_path)

    assert set(found) == {"clean"}
    reason = load_error(tmp_path / "leaky.py")
    assert reason and "api_key" in reason
    assert load_error(tmp_path / "clean.py") is None


def test_the_source_route_cannot_reach_an_undiscovered_routine(tmp_path, monkeypatch):
    """The one path a response-side redaction would have missed.

    ``GET /routines/{name}/source`` reads the file off the ``RoutineInfo`` that
    ``_discover_all`` returned, so a routine the guard refused is a 404 there —
    which is why the guard belongs at discovery and not in ``get_fields``.
    """
    monkeypatch.setattr(routines.base, "_path_caches", {})
    (tmp_path / "leaky.py").write_text(LEAKY)

    assert "leaky" not in discover_routines_from_path(tmp_path)


def test_create_routine_tells_the_author_why(tmp_path, monkeypatch):
    """The write door repeats the reason instead of blaming a syntax error."""
    import mcp_servers.condor.tools.routines as tools

    monkeypatch.setattr(routines.base, "_path_caches", {})
    monkeypatch.setattr(routines.base, "_load_errors", {})
    monkeypatch.setattr(tools, "_get_agent_routines_dir", lambda *a, **k: tmp_path)

    result = tools.create_routine("leaky", LEAKY, target=None)

    assert "api_key" in result["error"]
    assert "syntax" not in result["error"]
    assert not (tmp_path / "leaky.py").exists(), "the file must not be left behind"


# ── The routines this install actually ships ─────────────────────────────────


def _shipped_configs():
    """Every routine ``Config`` on disk, enumerated from the files.

    Deliberately not from discovery: the guard *removes* an offender from the
    catalog, so a corpus check that iterated discovered routines would pass by
    construction. This imports each file the way discovery does and asks the
    detector directly.

    Covers every root ``RoutineStore._discover_all`` reads — the general
    library, both layers of the shared one, and both layers of every agent's,
    including the unreviewed ``.condor/agents/*/routines`` overrides.
    """
    for root in routine_source_roots():
        if not root.exists():
            continue
        for path in sorted(root.glob("*.py")):
            if path.stem.startswith("_") or path.stem == "base":
                continue
            spec = importlib.util.spec_from_file_location(
                f"sec627_{root.parent.name}_{path.stem}", path
            )
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                # Unimportable is a different failure, and one discovery
                # already refuses to publish; it hides no default from a reader.
                continue
            config = getattr(module, "Config", None)
            if config is not None and isinstance(config, type):
                yield path, config


def test_no_shipped_routine_publishes_a_credential():
    """Prevention, not remediation — this is the check that keeps it that way."""
    scanned = list(_shipped_configs())
    assert len(scanned) > 10, "corpus scan found almost nothing — roots wrong?"
    offenders = {
        str(path): reasons
        for path, config in scanned
        if (reasons := secret_default_reasons(config))
    }
    assert offenders == {}
