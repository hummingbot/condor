"""Review a bundle before it runs (FEAT-144).

Four properties, in the order they matter:

* **A key does not leave the box it was typed on.** An export refuses on a
  value that can only be a secret, naming where it is; an ambiguous shape warns
  and travels, which is the verdict ``condor/runtime/secrets.py`` already drew
  with its reasons written down and is not re-drawn here.
* **A finding locates, it does not quote.** No field carries the matched value.
  The offsets slice the run out of the file's text, which this module asserts by
  doing the slicing itself.
* **The same review everywhere.** The preview and the apply carry the identical
  list, and the module imports nothing that would stop a hub from re-running it
  on a submission — asserted in a subprocess rather than assumed.
* **It refuses nothing on the way in.** A bundle full of findings still lands,
  still muted, because a review the operator cannot read is not a protection.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from condor.agents import bundle as ab
from condor.agents import bundle_review as br
from condor.layering import publishable_files
from condor.memory.mutes import load_mutes
from condor.memory.paths import agent_home
from condor.paths import stock_agents_root

# A real BIP-39 phrase, which is the only reason ``phrase_spans`` matches it:
# every word is in the vendored wordlist. Certain by the table in
# ``secrets.py``'s docstring — a 12-word run of wordlist words is nothing else.
PHRASE = "legal winner thank year wave sausage worth useful legal winner thank yellow"

# 64 bare hex characters: a MetaMask private-key export, and equally a Bitcoin
# txid or any sha256 digest. ``certain=False`` on purpose.
HEX64 = "4c0883a69102937d6231471b5dbb6204fe512961708279f1b7e0f4e6a6b01f4c"

AGENT_MD = (
    "---\n"
    "name: Reviewed\n"
    "description: a bundle under review\n"
    "agent_key: claude-code\n"
    "tools: []\n"
    "when_to_consult: when something must move\n"
    "server_required: true\n"
    "server_name: ''\n"
    "created_by: 0\n"
    "created_at: '2026-01-01T00:00:00+00:00'\n"
    "---\n"
    "\n"
    "Review me.\n"
)

SKILL_MD = (
    "---\nname: s\ndescription: d\nwhen_to_use: whenever\nsource: builtin\n"
    "---\n\n{body}\n"
)

LOOP_MD = (
    "---\n"
    "name: l\n"
    "description: a loop\n"
    "agent_key: null\n"
    "skills: []\n"
    "default_config:\n"
    "  connector_name: binance_perpetual\n"
    "  trading_pair: SOL-USDC\n"
    "{limits}"
    "default_trading_context: ''\n"
    "created_by: 0\n"
    "---\n"
    "\n"
    "Tick.\n"
)

WIDE_LIMITS = (
    "  risk_limits:\n"
    "    max_position_size_quote: 999999\n"
    "    max_open_executors: 50\n"
)

ROUTINE_PY = (
    "from pydantic import BaseModel\n\n\n"
    "class Config(BaseModel):\n    pass\n\n\n"
    "async def run(config, context):\n    return 'ok'\n"
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


@pytest.fixture
def reviewed() -> Path:
    """A clean library agent — one of everything, nothing worth a finding."""
    home = agent_home("reviewed")
    _write(home / "AGENT.md", AGENT_MD)
    _write(home / "skills" / "s" / "SKILL.md", SKILL_MD.format(body="Look around."))
    _write(home / "routines" / "r.py", ROUTINE_PY)
    _write(home / "loops" / "l" / "loop.md", LOOP_MD.format(limits=""))
    return home


def _bundle_of(files: dict[str, str]):
    """A bundle carrying exactly ``files``, digests included, written nowhere.

    Built by hand rather than through :func:`ab.build` wherever the test is
    about :func:`br.review` alone: the review takes files and the export path
    would only add a disk round trip between the input and the assertion.
    """
    return ab.Bundle(
        agent=ab.BundleAgent(slug="reviewed", name="Reviewed"),
        files=[
            ab.BundleFile(path=path, sha256=ab._digest(text), text=text)
            for path, text in files.items()
        ],
    )


def _of(findings, category: str, label: str | None = None) -> list[br.Finding]:
    return [
        f
        for f in findings
        if f.category == category and (label is None or f.label == label)
    ]


# ── Secrets: the one thing an export refuses on ──


def test_certain_secret_is_located_and_never_quoted():
    text = f"Use the wallet seeded with {PHRASE} for the grid.\n"
    findings = br.review(_bundle_of({"skills/s/SKILL.md": text}))

    secrets = _of(findings, br.SECRET)
    assert len(secrets) == 1
    found = secrets[0]
    assert (found.path, found.certain, found.label) == (
        "skills/s/SKILL.md",
        True,
        "mnemonic",
    )
    assert found.line == 1
    # The contract ``secrets.Finding`` sets: the caller slices the bytes.
    assert text[found.start : found.end] == PHRASE
    # And the finding itself carries none of them, in any field.
    assert not any(PHRASE in str(value) for value in found._asdict().values())
    for word in PHRASE.split():
        assert not any(word in str(value) for value in found._asdict().values())


def test_export_refuses_a_certain_secret_and_writes_nothing(reviewed):
    _write(reviewed / "skills" / "s" / "SKILL.md", SKILL_MD.format(body=PHRASE))

    with pytest.raises(ab.BundleError) as exc:
        ab.build("reviewed")

    message = str(exc.value)
    assert "skills/s/SKILL.md" in message
    assert "mnemonic" in message
    assert PHRASE not in message
    # A refused export leaves no trace: the bookkeeping stamp runs only after
    # every check, so ``AGENT.md`` is still the file the test wrote.
    assert (reviewed / "AGENT.md").read_text() == AGENT_MD


def test_export_carries_an_ambiguous_secret_and_only_warns(reviewed):
    _write(
        reviewed / "routines" / "r.py",
        ROUTINE_PY + f'\nTX = "{HEX64}"  # a hash, or a key — unknowable\n',
    )

    bundle = ab.build("reviewed")

    secrets = _of(br.review(bundle), br.SECRET)
    assert len(secrets) == 1
    assert secrets[0].certain is False
    assert secrets[0].label == "evm-hex64"
    assert HEX64 not in str(secrets[0]._asdict())


# ── Endpoints: a stranger's host, apart from this box ──


def test_a_public_host_and_a_loopback_host_are_labelled_apart():
    public = 'requests.post("https://evil.example/x")\n'
    local = 'requests.get("http://localhost:8000/accounts")\n'
    findings = br.review(
        _bundle_of({"routines/evil.py": public, "routines/local.py": local})
    )

    assert [(f.path, f.label) for f in _of(findings, br.ENDPOINT)] == [
        ("routines/evil.py", br.PUBLIC),
        ("routines/local.py", br.LOOPBACK),
    ]
    found = _of(findings, br.ENDPOINT, br.PUBLIC)[0]
    assert public[found.start : found.end] == "https://evil.example"


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("https://api.llama.fi/v2", br.PUBLIC),
        ("http://127.0.0.1:8000/x", br.LOOPBACK),
        ("http://localhost/x", br.LOOPBACK),
        ("http://10.4.0.9:1883", br.PRIVATE),
        ("http://192.168.1.4:8000", br.PRIVATE),
        ("http://172.20.0.3:5432", br.PRIVATE),
        ("http://host.docker.internal:8000", br.PRIVATE),
        ("mqtt://broker.local:1883", br.PRIVATE),
        # The token is in the query string, which is precisely why the finding
        # carries offsets and not the URL.
        ("https://mainnet.helius-rpc.com/?api-key=deadbeef", br.PUBLIC),
    ],
)
def test_host_classes(url: str, label: str):
    findings = br.review(_bundle_of({"routines/r.py": f'URL = "{url}"\n'}))
    assert [f.label for f in _of(findings, br.ENDPOINT)] == [label]


def test_a_url_with_a_port_is_one_endpoint_not_two():
    findings = br.review(
        _bundle_of({"routines/r.py": 'URL = "https://evil.example:8443/x"\n'})
    )
    assert len(_of(findings, br.ENDPOINT)) == 1


# ── Steered tools: context, with the line that gives it ──


def test_a_steered_tool_carries_its_line():
    text = (
        "# Liquidation guard\n"
        "\n"
        "When the band is breached, then call `create_grid_executor`.\n"
    )
    findings = br.review(_bundle_of({"skills/s/SKILL.md": text}))

    steered = _of(findings, br.TOOL_STEER)
    assert len(steered) == 1
    assert steered[0].label == "create_grid_executor"
    assert steered[0].line == 3
    # The surrounding sentence is the whole finding, so the surface has it.
    assert steered[0].context == (
        "When the band is breached, then call `create_grid_executor`."
    )
    assert text[steered[0].start : steered[0].end] == "create_grid_executor"


def test_a_steering_line_carrying_a_phrase_does_not_carry_it_into_the_finding():
    """``context`` is the one field with file text in it, so it is redacted."""
    text = f"Call `place_order` with the wallet from {PHRASE}.\n"
    steered = _of(br.review(_bundle_of({"skills/s/SKILL.md": text})), br.TOOL_STEER)

    assert len(steered) == 1
    assert PHRASE not in steered[0].context
    assert "[redacted: mnemonic]" in steered[0].context


def test_prose_naming_no_dangerous_tool_steers_nothing():
    text = "Read the book with `get_order_book` and report the spread.\n"
    assert _of(br.review(_bundle_of({"skills/s/SKILL.md": text})), br.TOOL_STEER) == []


# ── Code: the doors onto arbitrary strings ──


def test_exec_and_subprocess_are_both_reported():
    text = (
        "import subprocess\n"
        "\n"
        "async def run(config, context):\n"
        "    exec(config.payload)\n"
        '    subprocess.run(["sh", "-c", config.payload])\n'
    )
    found = _of(br.review(_bundle_of({"routines/r.py": text})), br.CODE_RUN)

    assert sorted({f.label for f in found}) == ["exec", "subprocess"]
    assert text[_of(found, br.CODE_RUN, "exec")[0].start :].startswith("exec(")


def test_a_routine_with_neither_reports_no_code_run():
    assert _of(br.review(_bundle_of({"routines/r.py": ROUTINE_PY})), br.CODE_RUN) == []


@pytest.mark.parametrize(
    ("line", "labels"),
    [
        ('open("/tmp/a", "w")\n', ["open-write"]),
        ("open(path, 'a')\n", ["open-write"]),
        ('open("/tmp/a", "r")\n', []),
        ('open("/tmp/a")\n', []),
        ('eval("1 + 1")\n', ["eval"]),
        ('__import__("os")\n', ["__import__"]),
        ('await client.run_code(action="run", code=c)\n', ["run_code"]),
    ],
)
def test_code_run_shapes(line: str, labels: list[str]):
    found = _of(br.review(_bundle_of({"routines/r.py": line})), br.CODE_RUN)
    assert [f.label for f in found] == labels


def test_code_run_is_not_reported_in_prose():
    """A ``.md`` naming ``subprocess`` is writing about it, not running it."""
    text = "Never use `subprocess` or `exec(` in a routine.\n"
    assert _of(br.review(_bundle_of({"skills/s/SKILL.md": text})), br.CODE_RUN) == []


# ── Risk limits: values, and no verdict ──


def test_risk_limits_are_reported_as_values():
    text = LOOP_MD.format(limits=WIDE_LIMITS)
    found = _of(br.review(_bundle_of({"loops/l/loop.md": text})), br.RISK_LIMITS)

    assert {f.label: f.context for f in found} == {
        "max_position_size_quote": "999999",
        "max_open_executors": "50",
    }
    # No verdict anywhere: nothing says wide, nothing says refused.
    assert all(f.certain is False for f in found)
    for f in found:
        assert text[f.start : f.end].startswith(f.label)
        assert text[f.start : f.end].endswith(f.context)
        assert text.splitlines()[f.line - 1].strip().startswith(f.label)


def test_a_loop_with_no_risk_limits_reports_none():
    text = LOOP_MD.format(limits="")
    assert _of(br.review(_bundle_of({"loops/l/loop.md": text})), br.RISK_LIMITS) == []


def test_risk_limits_outside_default_config_are_not_read():
    """Only the block that becomes the agent's brakes counts.

    ``load_full_config`` starts from the loop's ``default_config`` and nothing
    else in the file, so a ``risk_limits:`` written anywhere else is prose.
    """
    text = "---\nname: l\nrisk_limits:\n  max_open_executors: 50\n---\n\nTick.\n"
    assert _of(br.review(_bundle_of({"loops/l/loop.md": text})), br.RISK_LIMITS) == []


def test_risk_limits_nested_under_something_else_are_not_read():
    """Only ``default_config.risk_limits`` becomes the agent's brakes."""
    text = (
        "---\n"
        "name: l\n"
        "default_config:\n"
        "  notes:\n"
        "    risk_limits:\n"
        "      max_open_executors: 50\n"
        "---\n"
        "\nTick.\n"
    )
    assert _of(br.review(_bundle_of({"loops/l/loop.md": text})), br.RISK_LIMITS) == []


def test_risk_limits_in_the_body_are_not_read():
    text = (
        LOOP_MD.format(limits="")
        + "\ndefault_config:\n  risk_limits:\n    max_open_executors: 50\n"
    )
    assert _of(br.review(_bundle_of({"loops/l/loop.md": text})), br.RISK_LIMITS) == []


# ── Ordering ──


def test_a_certain_secret_sorts_first_and_risk_limits_last():
    findings = br.review(
        _bundle_of(
            {
                "AGENT.md": AGENT_MD,
                "loops/l/loop.md": LOOP_MD.format(limits=WIDE_LIMITS),
                "routines/r.py": (
                    'requests.post("https://evil.example/x")\nexec(payload)\n'
                ),
                "skills/s/SKILL.md": SKILL_MD.format(
                    body=f"Call `place_order`. Seed: {PHRASE}"
                ),
            }
        )
    )

    categories = [f.category for f in findings]
    assert set(categories) == {
        br.SECRET,
        br.ENDPOINT,
        br.CODE_RUN,
        br.TOOL_STEER,
        br.RISK_LIMITS,
    }
    assert findings[0].category == br.SECRET and findings[0].certain is True
    assert categories[-1] == br.RISK_LIMITS
    # The whole rank, most-decisive first.
    assert [
        c for i, c in enumerate(categories) if i == 0 or c != categories[i - 1]
    ] == [
        br.SECRET,
        br.ENDPOINT,
        br.CODE_RUN,
        br.TOOL_STEER,
        br.RISK_LIMITS,
    ]


# ── The shipped library, as a standing regression guard ──


def _library_findings(home: Path) -> tuple[list[br.Finding], dict[str, str]]:
    files = {
        rel.as_posix(): (home / rel).read_text("utf-8")
        for rel in publishable_files(home)
        if (home / rel).suffix in (".md", ".py", ".yml", ".yaml", ".json", ".txt")
    }
    return br.review_files(files.items()), files


def _shipped_agents() -> list[Path]:
    root = Path(__file__).resolve().parent.parent / "agents"
    return [
        home
        for home in sorted(root.iterdir())
        if home.is_dir()
        and not home.name.startswith("_")
        and (home / "AGENT.md").is_file()
    ]


def test_the_shipped_library_carries_no_secret_at_all():
    """Not merely no *certain* one: the library holds no key-shaped value.

    Stronger than FEAT-144's criterion asked for, and it is true today, so it is
    the version worth pinning — an ambiguous finding appearing in a shipped agent
    is a thing to look at even when it turns out to be a transaction hash.
    """
    offenders = []
    for home in _shipped_agents():
        findings, _ = _library_findings(home)
        offenders += [
            (home.name, f.path, f.line, f.label) for f in _of(findings, br.SECRET)
        ]
    assert offenders == []


#: The public hosts the shipped library legitimately reaches, by agent.
#:
#: FEAT-144 asked for *zero* public endpoints here. That criterion was wrong
#: about the library rather than about the code: these agents exist to read
#: public market and protocol data, and ``api.llama.fi`` is named in
#: ``fundamental_analyst``'s own ``AGENT.md``. A pin is the guard the criterion
#: was reaching for and the only one that can be green — a *new* host appearing
#: in a shipped agent fails here, which is what a regression guard is for.
LIBRARY_PUBLIC_HOSTS = {
    "fundamental_analyst": {"api.llama.fi", "api.coingecko.com"},
    "market_making_expert": {"api.hyperliquid.xyz"},
}


def test_the_shipped_librarys_public_hosts_are_the_pinned_ones():
    for home in _shipped_agents():
        findings, files = _library_findings(home)
        hosts = {
            br._host_of(files[f.path][f.start : f.end].split("//", 1)[-1])
            for f in _of(findings, br.ENDPOINT, br.PUBLIC)
        }
        assert hosts == LIBRARY_PUBLIC_HOSTS.get(home.name, set()), home.name


def test_a_stock_agent_still_exports(tmp_path):
    """The guard above is worth nothing if the library cannot leave at all."""
    home = stock_agents_root() / "shipped"
    _write(home / "AGENT.md", AGENT_MD)
    _write(home / "loops" / "l" / "loop.md", LOOP_MD.format(limits=WIDE_LIMITS))

    bundle = ab.build("shipped")

    assert [f.category for f in br.review(bundle)] == [br.RISK_LIMITS] * 2


# ── One review, three doors ──


def test_the_preview_and_the_apply_carry_the_same_findings_field_for_field(reviewed):
    _write(
        reviewed / "skills" / "s" / "SKILL.md",
        SKILL_MD.format(body="Then call `create_grid_executor` on the pair."),
    )
    _write(reviewed / "loops" / "l" / "loop.md", LOOP_MD.format(limits=WIDE_LIMITS))
    bundle = ab.build("reviewed")

    expected = [f._asdict() for f in br.review(bundle)]
    assert expected  # the fixture is not accidentally clean

    plan = ab.plan(bundle, as_slug="landed", user_id=7)
    assert [dict(row) for row in plan.summary()["findings"]] == expected

    result = ab.apply(plan, user_id=7)
    assert [dict(row) for row in result["findings"]] == expected


def test_the_wire_model_mirrors_the_review_finding_field_for_field():
    assert set(ab.BundleFinding.model_fields) == set(br.Finding._fields)


def test_an_import_with_findings_still_lands_muted(reviewed):
    _write(
        reviewed / "routines" / "r.py",
        ROUTINE_PY + f'\nTX = "{HEX64}"\nexec(payload)\n',
    )
    _write(
        reviewed / "skills" / "s" / "SKILL.md",
        SKILL_MD.format(body="Call `place_order` now."),
    )
    bundle = ab.build("reviewed")

    plan = ab.plan(bundle, as_slug="landed", user_id=7)
    assert plan.findings, "the fixture is meant to produce findings"
    assert plan.refusals == []
    assert plan.ok

    result = ab.apply(plan, user_id=7)

    assert result["imported"] is True
    assert "to look at" in result["next_step"]
    assert (agent_home("landed") / "routines" / "r.py").is_file()
    mutes = load_mutes("landed")
    assert "r" in mutes.get("routines", [])
    assert "s" in mutes.get("skills", [])


def test_a_clean_bundle_has_no_findings_and_says_nothing_extra(reviewed):
    plan = ab.plan(ab.build("reviewed"), as_slug="landed", user_id=7)
    assert plan.findings == []
    assert "to look at" not in ab.apply(plan, user_id=7)["next_step"]


# ── The HTTP and MCP doors ──


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=7, username="u", first_name="U", role="user"
    )
    return TestClient(app)


def test_the_export_endpoint_counts_the_findings_in_a_header(reviewed):
    _write(reviewed / "loops" / "l" / "loop.md", LOOP_MD.format(limits=WIDE_LIMITS))

    res = _client().get("/agents/reviewed/bundle")

    assert res.status_code == 200
    assert json.loads(res.headers["x-bundle-findings"]) == {br.RISK_LIMITS: 2}


def test_the_export_endpoint_refuses_a_certain_secret(reviewed):
    _write(reviewed / "skills" / "s" / "SKILL.md", SKILL_MD.format(body=PHRASE))

    res = _client().get("/agents/reviewed/bundle")

    assert res.status_code == 400
    assert "skills/s/SKILL.md" in res.json()["detail"]
    assert PHRASE not in res.text


def test_the_preview_endpoint_carries_the_findings(reviewed):
    _write(
        reviewed / "skills" / "s" / "SKILL.md",
        SKILL_MD.format(body="Then call `create_grid_executor`."),
    )
    client = _client()
    text = client.get("/agents/reviewed/bundle").text

    preview = client.post(
        "/agents/import/preview",
        files={"file": ("x.condor-agent.json", text, "application/json")},
        data={"as_slug": "landed"},
    )

    assert preview.status_code == 200
    findings = preview.json()["findings"]
    assert [f["category"] for f in findings] == [br.TOOL_STEER]
    assert findings[0]["label"] == "create_grid_executor"
    assert findings[0]["path"] == "skills/s/SKILL.md"
    assert "create_grid_executor" in findings[0]["context"]


def test_the_mcp_export_reports_the_findings(reviewed):
    from mcp_servers.condor.tools.trading_agent import _export_agent

    _write(reviewed / "loops" / "l" / "loop.md", LOOP_MD.format(limits=WIDE_LIMITS))

    result = _export_agent("reviewed")

    assert result["exported"] is True
    assert [f["label"] for f in result["findings"]] == [
        "max_position_size_quote",
        "max_open_executors",
    ]


def test_the_mcp_export_refuses_a_certain_secret(reviewed):
    from mcp_servers.condor.tools.trading_agent import _export_agent

    _write(reviewed / "skills" / "s" / "SKILL.md", SKILL_MD.format(body=PHRASE))

    result = _export_agent("reviewed")

    assert "findings" not in result
    assert "skills/s/SKILL.md" in result["error"]
    assert PHRASE not in result["error"]


# ── Portability: the hub must be able to run this ──
#
# FEAT-144 asked for a subprocess that imports the module and then finds no
# ``condor.agents.engine`` in ``sys.modules``, and could not write it: both
# package ``__init__`` files re-exported their submodules eagerly, so importing
# any submodule ran the tick engine, the ACP client and ``pydantic``. ARCH-744
# made both lazy, so the criterion is now reachable and is asserted below.
#
# Two tests, in two directions. The first reads this module's imports out of its
# source: path-independent, and the thing that would regress if somebody reached
# for a convenience import here. The second runs the module in a subprocess and
# looks at what actually got loaded, which is the only place a package-level
# regression one level up can show — and it reviews a file while it is there,
# because a module that imports clean and cannot review is no use to a hub.

_ALLOWED_IMPORTS = {"condor.runtime.secrets", "condor.runtime.danger"}


def test_the_review_imports_only_the_stdlib_and_two_pure_modules():
    """Read off the source, so it holds wherever the file sits."""
    import ast

    source = Path(br.__file__).read_text("utf-8")
    edges: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            edges |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            edges.add(node.module)

    foreign = {
        name
        for name in edges
        if name.split(".")[0] not in sys.stdlib_module_names
        and name not in _ALLOWED_IMPORTS
    }
    assert foreign == set(), f"{br.__name__} grew an import edge: {sorted(foreign)}"


#: Prefixes no process that only wanted the review has any business loading.
#: ``fastapi``/``telegram``/``condor.web`` are the hub's own line — it vendors a
#: reviewer, not an app. ``condor.agents.engine``, ``condor.acp`` and
#: ``pydantic`` are what the eager re-exports used to drag in (ARCH-744): the
#: whole agent framework, reached by a module whose own edges are ``re``,
#: ``collections.abc``, ``typing`` and two pure siblings.
_FORBIDDEN = (
    "fastapi",
    "telegram",
    "condor.web",
    "condor.agents.engine",
    "condor.acp",
    "pydantic",
)

_PURITY_PROBE = textwrap.dedent("""
    import json, sys
    import condor.agents.bundle_review as br
    forbidden = sorted(
        name
        for name in sys.modules
        if name in FORBIDDEN or name.startswith(tuple(p + "." for p in FORBIDDEN))
    )
    # Not a smoke test of the import alone: a module that imports clean and
    # cannot review is no use to a hub either.
    assert br.review_files([("skills/s/SKILL.md", "call place_order\\n")])
    print(json.dumps(forbidden))
    """)


def _probe(source: str) -> list[str]:
    """Run ``source`` in a bare subprocess and return the JSON list it prints."""
    repo = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [sys.executable, "-c", f"FORBIDDEN = {_FORBIDDEN!r}\n{source}"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_review_runs_without_the_web_app_or_the_bot():
    assert _probe(_PURITY_PROBE) == []


#: Sibling promise: ``danger.py`` and ``secrets.py`` say "stdlib-only on
#: purpose" too, and the review's own purity rests on theirs. Importing either
#: may touch its package but nothing else under ``condor``.
_SIBLING_PROBE = textwrap.dedent("""
    import json, sys
    import importlib
    importlib.import_module(MODULE)
    allowed = {"condor", "condor.runtime", MODULE}
    print(json.dumps(sorted(
        name for name in sys.modules
        if name.split(".")[0] == "condor" and name not in allowed
    )))
    """)


@pytest.mark.parametrize("module", ["condor.runtime.secrets", "condor.runtime.danger"])
def test_the_two_pure_siblings_pull_in_nothing_else(module):
    assert _probe(f"MODULE = {module!r}\n{_SIBLING_PROBE}") == []
