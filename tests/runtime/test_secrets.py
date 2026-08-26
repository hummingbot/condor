"""Key material in free text (FEAT-056) — what is eaten, and what is not.

The positive half of this file is easy and the negative half is the point. A
detector that redacts a transaction hash is worse than no detector: it breaks
"check this tx", which is what this bot is *for*, on every single use. So the
corpus below carries a real tx hash, a Solana signature, a wallet address, a
trading pair and an ordinary English sentence, and asserts they come back byte
for byte.

None of the values here is live. The Solana address is the SPL token program,
the EVM one is from the go-ethereum docs, and the phrase is BIP-39's own
published test vector.
"""

from __future__ import annotations

import pytest

from condor.runtime import secrets

# BIP-39's published test vector. It repeats three words on purpose — see
# ``bip39_words``'s docstring for why that detail decided the design.
SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"
SEED_24 = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon art"
)
KEYPAIR = "[" + ",".join(str((i * 7) % 256) for i in range(64)) + "]"
TX_HASH = "0x" + "9f" * 32
SOL_SIG = (
    "5wHu1qwD4kLwYqLNGjaKfHUDNCLLDFFPGz1cUKb1t8HBxXpJhVFq"
    "1PbwzTV1RxRuFuvLWqJwHtDsL1s9jUn9Xg1H"
)
SOL_ADDR = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
EVM_ADDR = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"


def kinds(text: str) -> list[str]:
    return [finding.kind for finding in secrets.scan(text)]


# ── What is certain enough to eat ────────────────────────────────────────


@pytest.mark.parametrize(
    "shape, raw",
    [
        ("single space", SEED),
        ("newlines", "\n".join(SEED.split())),
        ("commas", ", ".join(SEED.split())),
        (
            "numbered sheet",
            " ".join(f"{n}. {w}" for n, w in enumerate(SEED.split(), 1)),
        ),
        ("twenty-four words", SEED_24),
        ("title case", SEED.title()),
        ("shouted", SEED.upper()),
        ("phone autocapitalised", SEED[0].upper() + SEED[1:]),
    ],
)
def test_a_recovery_phrase_is_redacted_in_every_shape_it_is_pasted_in(shape, raw):
    clean, findings = secrets.redact(raw)
    assert [f.kind for f in findings] == [secrets.MNEMONIC], shape
    assert clean == "[redacted: mnemonic]", shape


def test_prose_in_title_case_is_not_a_phrase():
    """The case-insensitive candidate leans on membership harder, so the
    negative is what proves it: a capitalised sentence is still not twelve
    consecutive wordlist entries."""
    prose = "The Quick Brown Foxes Jumped Over Several Lazy Sleeping Dogs Near Rivers"
    clean, findings = secrets.redact(prose)
    assert findings == []
    assert clean == prose


def test_a_phrase_pasted_mid_sentence_loses_only_the_phrase():
    clean, findings = secrets.redact(f"my phrase is {SEED} and it will not import")
    assert len(findings) == 1
    assert clean == "my phrase is [redacted: mnemonic] and it will not import"


def test_a_numbered_phrase_takes_its_numbering_with_it():
    """A span that started at the first word would redact the phrase and leave
    a numbered list of nothing behind."""
    raw = "here: " + " ".join(f"{n}) {w}" for n, w in enumerate(SEED.split(), 1))
    clean, _ = secrets.redact(raw)
    assert clean == "here: [redacted: mnemonic]"


def test_a_solana_keypair_array_is_redacted():
    clean, findings = secrets.redact(f"here is my key {KEYPAIR} please import it")
    assert [f.kind for f in findings] == [secrets.SOLANA_KEYPAIR]
    assert clean == "here is my key [redacted: solana-keypair] please import it"


def test_an_array_of_something_else_is_not_a_keypair():
    """64 numbers is the shape; over 255 is not a byte, and 63 is not 64."""
    too_big = "[" + ",".join(["300"] * 64) + "]"
    too_short = "[" + ",".join(["1"] * 63) + "]"
    for raw in (too_big, too_short):
        assert kinds(raw) == []


def test_the_marker_names_the_kind():
    """Readable to the model on purpose: it can say something useful about a
    hole it can name, and is only confused by one it cannot."""
    clean, _ = secrets.redact(SEED)
    assert "mnemonic" in clean
    assert "sausage" not in clean


# ── What passes through, because it is more often something else ─────────


@pytest.mark.parametrize(
    "raw, kind",
    [
        (f"did {TX_HASH} land?", secrets.EVM_HEX64),
        (f"check {SOL_SIG} on solscan", secrets.SOLANA_B58),
    ],
)
def test_an_ambiguous_shape_is_reported_but_never_redacted(raw, kind):
    """This is the accepted trade-off, not an oversight: a 64-byte value is a
    transaction far more often than a key, and redacting it by default would
    break the most routine thing anyone asks this bot."""
    clean, findings = secrets.redact(raw)
    assert [f.kind for f in findings] == [kind]
    assert findings[0].certain is False
    assert clean == raw


# ── The false-positive corpus ────────────────────────────────────────────


@pytest.mark.parametrize(
    "label, raw",
    [
        ("wallet address", f"send it to {SOL_ADDR} now"),
        ("evm address", f"the contract is {EVM_ADDR}"),
        ("trading pair", "buy 10 SOL-USDC at market"),
        ("bot name", "restart hummingbot-pmm-solusdc-1 please"),
        (
            "english sentence",
            "i will always cover the bridge and act toward the market with useful ideas",
        ),
        ("a number", "pnl was 1234.56 today, up 4.2%"),
        ("empty", ""),
    ],
)
def test_ordinary_text_passes_through_byte_identical(label, raw):
    clean, findings = secrets.redact(raw)
    assert findings == [], label
    assert clean == raw, label


def test_a_44_char_address_never_reaches_the_base58_key_shape():
    """The 87-88 bound is exact for this reason: an address is 32-44."""
    assert kinds(SOL_ADDR) == []
    assert len(SOL_SIG) in (87, 88)


# ── A finding is offsets, never the value ────────────────────────────────


def test_a_finding_carries_offsets_and_not_the_secret():
    """It exists to be logged, counted and put in telemetry. A finding that
    carried the value would be the leak it is there to prevent."""
    raw = f"my phrase is {SEED}"
    (finding,) = secrets.scan(raw)
    assert SEED not in "".join(str(field) for field in finding)
    assert raw[finding.start : finding.end] == SEED


def test_findings_are_returned_in_order_with_the_ambiguous_ones():
    # Separated by pipes rather than prose: "and then legal winner …" opens
    # the phrase one word earlier, because ``then`` is itself a wordlist entry.
    raw = f"{TX_HASH} | {SEED} | {KEYPAIR}"
    findings = secrets.scan(raw)
    assert [f.kind for f in findings] == [
        secrets.EVM_HEX64,
        secrets.MNEMONIC,
        secrets.SOLANA_KEYPAIR,
    ]
    assert [f.start for f in findings] == sorted(f.start for f in findings)
    clean, _ = secrets.redact(raw)
    assert clean == f"{TX_HASH} | [redacted: mnemonic] | [redacted: solana-keypair]"


def test_counts_are_categories_only():
    assert secrets.counts(secrets.scan(f"{SEED} {TX_HASH} {TX_HASH}")) == {
        secrets.MNEMONIC: 1,
        secrets.EVM_HEX64: 2,
    }


def test_the_vendored_wordlist_is_the_canonical_one():
    """2048 words, ``abandon`` to ``zoo``. A truncated file silently stops
    catching phrases, so its shape is asserted rather than assumed."""
    words = secrets.bip39_words()
    assert len(words) == 2048
    assert "abandon" in words and "zoo" in words


def test_a_missing_wordlist_degrades_to_no_phrase_detection(monkeypatch):
    """It runs on the one funnel every turn crosses. A mangled install must not
    take the agent down with it."""
    monkeypatch.setattr(secrets, "_bip39", None)
    monkeypatch.setattr(secrets, "_BIP39_PATH", secrets._BIP39_PATH.parent / "nope.txt")
    try:
        assert secrets.scan(SEED) == []
    finally:
        secrets._bip39 = None
