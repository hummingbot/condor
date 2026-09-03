"""The scrubber (FEAT-054) — what leaves an install, and what must not.

Like ``test_telemetry.py``, the load-bearing assertions here are the negative
ones. Half of this file proves that known values are *absent* from the output;
the other half proves that a trading pair, a price, an order id and a bot name
are *present*, because a corpus that mangled them would be a corpus nobody can
read the agent's reasoning out of.

The install's own values are injected rather than discovered, so the suite
asserts against a stated table instead of against whatever ``config.yml`` the
developer happens to have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from condor.runtime.conversations import TurnEntry, _tool_input
from condor.sharing import scrub, wire

SECRET = "0123456789abcdef0123456789abcdef"

# What this pretend install knows about itself.
KNOWN = [
    ("prod-hb", "known_server"),
    ("http://10.4.2.9:8000", "known_url"),
    ("hbot-admin", "known_user"),
    ("sk-live-9f8e7d6c5b4a3210", "known_key"),
    ("987654321", "known_user"),
    ("/Users/alice", "known_path"),
]

# Real-shaped values. None of them is live: the Solana address is the SPL token
# program, the EVM one is the address from the go-ethereum docs, and the key
# prefixes are shape only.
SOL_ADDR = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
EVM_ADDR = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
TX_HASH = "0x" + "9f" * 32
BARE_HEX64 = TX_HASH[2:]
ANTHROPIC_KEY = "sk-ant-api03-QQzzAAbbCCdd1122334455"
SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"


def _scrub(text: str) -> tuple[str, dict[str, int]]:
    turns, counts = scrub.scrub(
        [TurnEntry(role="user", text=text)], secret=SECRET, known=KNOWN
    )
    return turns[0].text, counts


# ── Tier 2: shapes that cannot be anything else ──────────────────────────


@pytest.mark.parametrize(
    "raw, category",
    [
        (f"send it to {SOL_ADDR} now", "sol_addr"),
        (f"the contract is {EVM_ADDR}", "evm_addr"),
        (f"filled in tx {TX_HASH}", "hex64"),
        # The bare form was always replaced, but by the generic last net, so it
        # was counted as "secret". It is a hex64 and is now counted as one.
        (f"my key is {BARE_HEX64}", "hex64"),
        (f"export ANTHROPIC_API_KEY={ANTHROPIC_KEY}", "api_key"),
        (f"my phrase: {SEED}", "seed_phrase"),
        ("mail me at alice@example.org", "email"),
        ("GET https://api.example.com/v1/orders?api_key=abc123def", "url"),
        ("the box is at 203.0.113.44", "ip"),
        ("blob aGVsbG93b3JsZDEyMzQ1Njc4OTBhYmNkZWZnaGlq here", "secret"),
    ],
)
def test_a_structural_secret_is_replaced_and_counted(raw, category):
    out, counts = _scrub(raw)
    assert counts[category] == 1
    for value in (
        SOL_ADDR,
        EVM_ADDR,
        TX_HASH,
        BARE_HEX64,
        ANTHROPIC_KEY,
        "alice@example.org",
    ):
        if value in raw:
            assert value not in out
    assert scrub._TAGS[category] in out


def test_a_recovery_phrase_survives_repeated_words():
    """BIP-39's own test vector repeats three words.

    This is why membership is checked against the vendored wordlist rather than
    guessed at structurally: the obvious heuristic — twelve short lowercase
    words, none repeated — waves this exact string through.
    """
    out, counts = _scrub(f"write this down: {SEED}")
    assert counts["seed_phrase"] == 1
    assert "sausage" not in out


@pytest.mark.parametrize(
    "shape, raw",
    [
        ("single space", SEED),
        ("newlines", "\n".join(SEED.split())),
        ("commas", ", ".join(SEED.split())),
        ("double spaces", "  ".join(SEED.split())),
        (
            "numbered sheet",
            " ".join(f"{n}. {word}" for n, word in enumerate(SEED.split(), 1)),
        ),
        (
            "numbered lines",
            "\n".join(f"{n}) {word}" for n, word in enumerate(SEED.split(), 1)),
        ),
    ],
)
def test_a_recovery_phrase_is_caught_in_every_shape_it_is_pasted_in(shape, raw):
    """A phrase does not arrive single-spaced (SEC-230).

    Out of a wallet UI it is one word per line, out of a backup sheet it is
    numbered, out of a paste it carries whatever spacing it had. Every one of
    these reached the collector verbatim while the candidate regex asked for a
    single literal space, and reported "nothing was replaced" while doing it.
    """
    out, counts = _scrub(raw)
    assert counts["seed_phrase"] == 1, shape
    for word in SEED.split():
        assert word not in out, shape


@pytest.mark.parametrize(
    "case, raw",
    [
        ("title case", SEED.title()),
        ("shouted", SEED.upper()),
        ("phone autocapitalised", SEED[0].upper() + SEED[1:]),
        (
            "numbered sheet, capitalised",
            "\n".join(f"{n}. {w.capitalize()}" for n, w in enumerate(SEED.split(), 1)),
        ),
    ],
)
def test_a_recovery_phrase_is_caught_whatever_case_it_arrives_in(case, raw):
    """BIP-39 writes its wordlist in lowercase; a paste is under no such rule.

    The candidate regex asked for ``[a-z]``, so a phrase that had been through
    a phone keyboard's autocapitalisation — or any wallet that renders its
    words capitalised — was rejected on shape before the wordlist was ever
    consulted, and reached the collector verbatim. Flagged on PR #224.
    """
    out, counts = _scrub(raw)
    assert counts["seed_phrase"] == 1, case
    for word in raw.split():
        assert word not in out, case


def test_a_capitalised_sentence_is_not_a_phrase():
    """The negative half of the case fix: matching any case must not turn a
    Title Case sentence into a mnemonic."""
    prose = "The Quick Brown Foxes Jumped Over Several Lazy Sleeping Dogs Near Rivers"
    out, counts = _scrub(prose)
    assert counts.get("seed_phrase", 0) == 0
    assert out == prose


def test_an_ordinary_sentence_of_wordlist_words_is_left_alone():
    """The widened shape leans on membership harder, so the negative case is
    what proves it: twelve-plus words, several of them real wordlist entries,
    and no run of twelve reaches the floor."""
    prose = "i will always cover the bridge and act toward the market with useful ideas"
    out, counts = _scrub(prose)
    assert counts.get("seed_phrase", 0) == 0
    assert out == prose


def test_a_phrase_pasted_mid_sentence_loses_only_the_phrase():
    out, counts = _scrub(f"my phrase is {SEED} and it is not working")
    assert counts["seed_phrase"] == 1
    assert out.startswith("my phrase is ")
    assert out.endswith(" it is not working")
    assert "sausage" not in out


def test_the_vendored_wordlist_is_the_canonical_one():
    """2048 words, ``abandon`` to ``zoo``. A truncated file silently stops
    catching phrases, so its shape is asserted rather than assumed."""
    words = scrub.bip39_words()
    assert len(words) == 2048
    assert "abandon" in words and "zoo" in words
    assert all(3 <= len(w) <= 8 and w.isalpha() and w.islower() for w in words)


def test_a_url_keeps_its_scheme_and_host_but_loses_its_query():
    out, _ = _scrub("GET https://api.example.com/v1/orders?api_key=abc123def")
    assert out.startswith("GET https://api.example.com/")
    assert "abc123def" not in out


def test_a_documentation_url_survives_whole():
    """No query, no userinfo, no secret. A corpus that lost these lost part of
    what the agent was reasoning about."""
    out, counts = _scrub("see https://docs.hummingbot.org/v2/quickstart")
    assert out == "see https://docs.hummingbot.org/v2/quickstart"
    assert counts["url"] == 0


# ── Tier 1: the values the install already holds ─────────────────────────


def test_every_known_value_is_absent_from_the_output():
    """Acceptance criterion: no server name, URL, key, username, user id or
    home path this install holds reaches the payload."""
    raw = (
        "on prod-hb at http://10.4.2.9:8000 as hbot-admin with "
        "sk-live-9f8e7d6c5b4a3210 for 987654321, logs in /Users/alice/condor"
    )
    out, counts = _scrub(raw)
    for value, _ in KNOWN:
        assert value not in out, value
    assert counts["known_server"] == 1
    assert counts["known_key"] == 1
    assert counts["known_path"] == 1
    assert counts["known_user"] == 2  # the username and the user id


def test_a_known_value_beats_the_pattern_that_would_also_match_it():
    """Tier 1 runs first on purpose: an exact hit is better labelled than a
    guess, so the server's URL reads as ``URL_…`` from the known table rather
    than being taken apart by the IP pattern."""
    out, counts = _scrub("the api is at http://10.4.2.9:8000/status")
    assert counts["known_url"] == 1
    assert counts["ip"] == 0
    assert "10.4.2.9" not in out


# ── Negative controls: what must survive ─────────────────────────────────


@pytest.mark.parametrize(
    "survivor",
    [
        "SOL-USDC",  # a trading pair
        "142.35",  # a price
        "x-XEKWYICXf1a2b3c4d5e6f7a8b9c0d1e2f3",  # an exchange order id
        "hummingbot-v2-pmm-solusdc",  # a bot name
        "1250.00",  # a quantity
        "binance_perpetual",  # a connector
        f"order-{BARE_HEX64}-1",  # 64 hex, but inside an identifier
    ],
)
def test_a_thing_that_only_looks_like_a_secret_survives(survivor):
    out, _ = _scrub(f"placed {survivor} on the book")
    assert survivor in out


def test_a_plain_english_sentence_is_untouched():
    raw = "the agent should check the current price before placing another order"
    out, counts = _scrub(raw)
    assert out == raw
    assert sum(counts.values()) == 0


# ── Pseudonyms ───────────────────────────────────────────────────────────


def test_a_pseudonym_is_stable_across_turns_so_the_chat_still_reads():
    """The agent's reasoning about "that wallet" has to survive redaction."""
    turns, _ = scrub.scrub(
        [
            TurnEntry(role="user", text=f"check {SOL_ADDR}"),
            TurnEntry(role="assistant", text=f"{SOL_ADDR} holds 12 SOL"),
            TurnEntry(role="user", text=f"and now? {SOL_ADDR}"),
        ],
        secret=SECRET,
        known=KNOWN,
    )
    found = {re.search(r"SOL_ADDR_\w+", t.text).group(0) for t in turns}
    assert len(found) == 1


def test_two_installs_give_the_same_address_different_pseudonyms():
    """The salt is the install's own ``share_secret``, which never leaves it —
    so a corpus cannot be joined on "who else talked about this wallet"."""
    one, _ = scrub.scrub(
        [TurnEntry(role="user", text=SOL_ADDR)], secret="aaa", known=[]
    )
    two, _ = scrub.scrub(
        [TurnEntry(role="user", text=SOL_ADDR)], secret="bbb", known=[]
    )
    assert one[0].text != two[0].text


def test_counts_always_carry_every_category():
    """An all-zero share is the signal that a build's scrubber stopped matching.
    That only reads as a signal if the zeros are actually reported."""
    _, counts = _scrub("nothing to see here")
    assert set(counts) == set(scrub.CATEGORIES)
    assert set(counts.values()) == {0}


# ── Structures ───────────────────────────────────────────────────────────


def test_a_tool_call_payload_is_scrubbed_at_every_depth():
    turns, counts = scrub.scrub(
        [
            TurnEntry(
                role="assistant",
                text="checking",
                thought=f"the wallet {SOL_ADDR} again",
                tool_calls=[
                    {
                        "id": "1",
                        "title": "get_balance",
                        "input": {"wallet": SOL_ADDR, "password": "[redacted]"},
                        "output": {"rows": [{"addr": EVM_ADDR, "qty": 12.5}]},
                    }
                ],
            )
        ],
        secret=SECRET,
        known=KNOWN,
    )
    call = turns[0].tool_calls[0]
    assert SOL_ADDR not in str(call) and SOL_ADDR not in turns[0].thought
    assert EVM_ADDR not in str(call)
    assert call["input"]["password"] == "[redacted]"  # _redact's marker survives
    assert call["output"]["rows"][0]["qty"] == 12.5  # quantities are kept
    assert call["title"] == "get_balance"  # so is the trajectory
    assert counts["sol_addr"] and counts["evm_addr"]


def _nest(depth: int, leaf):
    """``leaf`` buried so that ``payload`` reaches it at ``depth``."""
    for level in reversed(range(depth)):
        leaf = {f"l{level}": leaf}
    return leaf


def test_the_depth_cap_scrubs_the_leaf_it_stops_at():
    """CORR-245: the cap used to ``return value`` — the raw object, unscrubbed —
    while every other decision in this module fails closed. The scrubber is the
    last gate on the sweep path, so the one fail-open branch was the one that
    could put a verbatim wallet on the wire."""
    scrubber = scrub.Scrubber(SECRET, KNOWN)
    out = scrubber.payload(_nest(scrub._MAX_DEPTH, EVM_ADDR))
    assert EVM_ADDR not in json.dumps(out)
    assert scrubber.counts["evm_addr"] == 1


@pytest.mark.parametrize("extra", [0, 1, 4])
def test_nothing_below_the_cap_survives_verbatim(extra):
    scrubber = scrub.Scrubber(SECRET, KNOWN)
    buried = {"evm": EVM_ADDR, "sol": SOL_ADDR, "hash": TX_HASH}
    out = json.dumps(scrubber.payload(_nest(scrub._MAX_DEPTH + extra, buried)))
    for value in buried.values():
        assert value not in out


def test_a_container_past_the_cap_is_elided_not_emitted():
    """A dict at the cap has more below it than a leaf scrub can reach, so it
    goes out as ``_redact``'s marker rather than whole."""
    scrubber = scrub.Scrubber(SECRET, KNOWN)
    out = scrubber.payload(_nest(scrub._MAX_DEPTH, {"addr": EVM_ADDR}))
    leaf = out
    for level in range(scrub._MAX_DEPTH):
        leaf = leaf[f"l{level}"]
    assert leaf == "…"


def test_scrub_reaches_everything_redact_left_on_disk():
    """The two caps are one apart because ``turn`` enters at the *call* and
    reaches ``input`` a level later. A value ``_redact`` kept has to be a value
    the scrubber still walks, or the deepest surviving argument goes out raw."""
    raw = _nest(5, EVM_ADDR)
    disk = _tool_input(raw)
    assert EVM_ADDR in json.dumps(disk), "the fixture must survive redaction"

    turns, counts = scrub.scrub(
        [TurnEntry(role="assistant", tool_calls=[{"id": "1", "input": disk}])],
        secret=SECRET,
        known=KNOWN,
    )
    assert EVM_ADDR not in json.dumps(turns[0].tool_calls)
    assert counts["evm_addr"] == 1


def test_quantities_are_deliberately_kept():
    """Called out in the consent copy rather than hidden: a corpus without the
    numbers cannot tell whether the agent computed the right answer."""
    raw = "balance 12,450.31 USDC, pnl -3.2%, filled 0.25 at 142.35"
    out, _ = _scrub(raw)
    assert out == raw


# ── Coverage over the model, not over three names ────────────────────────


def test_every_turn_field_is_classified():
    """The guard: a field the scrubber cannot bucket breaks the build here.

    ``wire.envelope`` posts ``model_dump()`` of the whole entry, so the fields
    the scrubber does not cover are the fields that ship raw. The scrubber
    derives its coverage from ``TurnEntry`` instead of restating it, and this is
    what makes that derivation honest: add a field of a shape ``classify`` does
    not know — a nested model, a ``datetime`` — and this fails now, rather than
    the share leaking later.

    The fix when it fails is to teach :func:`condor.sharing.scrub.classify`
    about the type, not to delete the field from the assertion.
    """
    unclassified = [
        name
        for name, bucket in scrub.TURN_FIELDS.items()
        if bucket not in scrub.BUCKETS
    ]
    assert unclassified == []
    assert set(scrub.TURN_FIELDS) == set(TurnEntry.model_fields)


def test_the_current_fields_land_in_the_bucket_they_should():
    """Stated once, so a change of type is a change of test."""
    assert scrub.TURN_FIELDS["text"] == scrub.TEXT
    assert scrub.TURN_FIELDS["thought"] == scrub.TEXT
    assert scrub.TURN_FIELDS["stop_reason"] == scrub.TEXT
    assert scrub.TURN_FIELDS["tool_calls"] == scrub.PAYLOAD
    assert scrub.TURN_FIELDS["attachments"] == scrub.PAYLOAD
    assert scrub.TURN_FIELDS["ts"] == scrub.SCALAR


def test_a_secret_in_any_other_string_field_does_not_reach_the_wire():
    """The failure the enumeration used to allow, asserted end to end.

    ``stop_reason`` stands in for the ``error_text`` or ``system_note`` somebody
    adds next: it is a plain string field the scrubber never named, and before
    coverage was derived from the model it went out verbatim. The assertion is
    over ``wire.envelope`` rather than over the scrubbed turn, because the
    envelope is what actually leaves.
    """
    turns, counts = scrub.scrub(
        [
            TurnEntry(
                role="assistant",
                text="done",
                stop_reason=f"error: sk-live-9f8e7d6c5b4a3210 rejected by {EVM_ADDR}",
                agent_key="claude-opus-4",
            )
        ],
        secret=SECRET,
        known=KNOWN,
    )
    body = wire.envelope(
        share_install_id="i",
        share_id="s",
        delete_token="t",
        revision=1,
        turns=turns,
        counts=counts,
        truncated=False,
    )
    payload = json.dumps(body["turns"])
    assert "sk-live-9f8e7d6c5b4a3210" not in payload
    assert EVM_ADDR not in payload
    assert counts["known_key"] == 1 and counts["evm_addr"] == 1
    # The field is scrubbed, not blanked: the reason a stream ended is still
    # readable, which is the whole point of keeping it.
    assert body["turns"][0]["stop_reason"].startswith("error: API_KEY_")
    assert body["turns"][0]["agent_key"] == "claude-opus-4"


def test_a_scalar_field_is_left_exactly_as_it_was():
    """``ts`` is a float and stays one — a scrubbed timestamp is a broken
    transcript, and no free text can hide in a number anyway."""
    entry = TurnEntry(role="user", text="hi", ts=1755000000.5)
    turns, _ = scrub.scrub([entry], secret=SECRET, known=KNOWN)
    assert turns[0].ts == 1755000000.5


def test_an_unclassifiable_field_never_travels(monkeypatch):
    """Fail closed, the way ``ATTRIBUTABLE_SURFACES`` refuses a surface it does
    not recognise: a shape this module cannot walk is dropped to its default
    rather than posted on the hope that it holds nothing."""
    monkeypatch.setitem(scrub.TURN_FIELDS, "kind", scrub.UNCLASSIFIED)
    turns, _ = scrub.scrub(
        [TurnEntry(role="system", kind="switch", text="hi")], secret=SECRET, known=KNOWN
    )
    assert turns[0].kind == ""
    assert turns[0].text == "hi"


def test_classify_reads_the_annotation_not_the_name():
    assert scrub.classify(str) == scrub.TEXT
    assert scrub.classify(str | None) == scrub.TEXT
    assert scrub.classify(list[dict]) == scrub.PAYLOAD
    assert scrub.classify(dict[str, object]) == scrub.PAYLOAD
    assert scrub.classify(float) == scrub.SCALAR
    assert scrub.classify(bool) == scrub.SCALAR
    assert scrub.classify(TurnEntry) == scrub.UNCLASSIFIED


# ── The separation rule ──────────────────────────────────────────────────


def test_sharing_never_imports_the_telemetry_taxonomy():
    """The load-bearing constraint of the whole feature, asserted the way the
    collector asserts its vendored taxonomy is the only definition there.

    ``condor/telemetry/`` is where "free text cannot escape" is enforced by
    construction — an allowlist, a 64-character cap, a character class. If the
    conversation path ever imported that taxonomy, the next person to widen a
    property "just like the transcript one" would turn it from a fence into a
    suggestion, and nobody would notice.

    Asserted over the import graph rather than over the file text, so a
    docstring may go on *explaining* the rule without appearing to break it.
    ``condor.telemetry.context`` is not in scope: the deployment block is not
    the taxonomy, and the envelope legitimately carries it.
    """
    import ast

    package = Path(scrub.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [
                    f"{node.module}.{alias.name}" for alias in node.names
                ]
            else:
                continue
            if any(name.startswith("condor.telemetry.schema") for name in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_a_shared_attachment_is_an_inert_reference():
    """What a share carries when the turn had a picture on it (FEAT-098).

    The reference and nothing else: no bytes, no filename, and no path a corpus
    could resolve — the file lives under the *sharer's* conversation directory,
    behind a bearer token, and the id names nothing outside it. That outcome is
    the design's, not a special case: ``attachments`` classifies as ``PAYLOAD``,
    so the walk covers it the day it exists.
    """
    turns, _ = scrub.scrub(
        [
            TurnEntry(
                role="user",
                text="what is wrong here?",
                attachments=[{"id": "9f8e7d.png", "mime": "image/png", "bytes": 20481}],
            )
        ],
        secret=SECRET,
        known=KNOWN,
    )
    (attachment,) = turns[0].attachments
    assert attachment == {"id": "9f8e7d.png", "mime": "image/png", "bytes": 20481}

    payload = json.dumps([turn.model_dump(mode="json") for turn in turns])
    assert "/conversations/" not in payload, "no path the corpus could dial"
    assert "users/" not in payload
    assert ".condor" not in payload
    # Nothing that could be a filename the user did not consider giving.
    assert "Screenshot" not in payload
