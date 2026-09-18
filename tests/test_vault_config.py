"""The canonical config encoding, pinned.

This hash is a commitment a vault carries on chain for its whole life. If this
file's expected value ever has to change, every vault already deployed becomes
unverifiable — so it changes only alongside a migration, never to make a new
test pass.
"""

import pytest

from condor.vault_config import NotCanonical, canonical_json, config_hash

# The vector both implementations are held to. Deliberately awkward: nested
# objects, keys out of order, an array whose order matters, a large integer, a
# unicode string, and an empty object.
VECTOR = {
    "pair": "SOL-USDC",
    "bands": [{"width_bps": 50, "side": "both"}, {"width_bps": 120, "side": "buy"}],
    "amount_lamports": 12_500_000_000,
    "label": "café ☕",
    "advanced": {"z": 1, "a": {"nested": True}, "m": "x"},
    "empty": {},
}
VECTOR_BYTES = (
    '{"advanced":{"a":{"nested":true},"m":"x","z":1},'
    '"amount_lamports":12500000000,'
    '"bands":[{"side":"both","width_bps":50},{"side":"buy","width_bps":120}],'
    '"empty":{},'
    '"label":"café ☕",'
    '"pair":"SOL-USDC"}'
).encode("utf-8")
VECTOR_HASH = "0d391584d44d093fb6cd08844b4551b08e3faa2e5ab1c231735b69ae7cae2ad7"


def test_the_encoding_is_byte_exact():
    assert canonical_json(VECTOR) == VECTOR_BYTES


def test_the_hash_is_the_one_the_browser_computes():
    """The same vector and the same expected digest as `canonical.test.ts`.

    Changing this number means every vault already on chain can no longer
    verify its own config. It moves only with a migration.
    """
    assert config_hash(VECTOR) == VECTOR_HASH


def test_key_order_in_the_input_does_not_change_the_hash():
    reversed_config = {k: VECTOR[k] for k in reversed(list(VECTOR))}
    assert config_hash(reversed_config) == config_hash(VECTOR)


def test_array_order_does_change_the_hash():
    """An array is data, not a set: reordering it is a different config."""
    swapped = dict(VECTOR, bands=list(reversed(VECTOR["bands"])))
    assert config_hash(swapped) != config_hash(VECTOR)


def test_floats_are_refused_rather_than_rounded():
    with pytest.raises(NotCanonical) as exc:
        canonical_json({"spread": 0.1})
    assert "spread" in str(exc.value)


def test_null_is_refused_because_absent_means_the_same_thing():
    with pytest.raises(NotCanonical) as exc:
        canonical_json({"outer": {"inner": None}})
    assert "outer.inner" in str(exc.value)


def test_a_non_object_config_is_refused():
    with pytest.raises(NotCanonical):
        canonical_json([1, 2, 3])  # type: ignore[arg-type]
