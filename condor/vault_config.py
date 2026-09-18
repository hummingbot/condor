"""The canonical encoding of a vault's private config, and its hash.

A vault's parameters are **private, not confidential**: the values live here,
readable only by their creator, and what goes on chain is `sha256` of their
canonical encoding. A crank that is handed a config hashes it and compares
before it starts, so nobody — Condor's own operators included — can run a vault
on parameters its creator did not sign.

That only works if *every* implementation produces the same bytes. There are
two: this one and `frontend/src/lib/vault/canonical.ts`, which the browser uses
to check what it is about to sign. They are tested against the same vector
(`tests/test_vault_config.py`, `canonical.test.ts`); if they drift, every vault
on chain becomes unverifiable and nobody finds out until a run refuses to start.

The rules, and why each is a rule rather than a preference:

* **JSON, UTF-8, no whitespace.** One serialization, no formatting to agree on.
* **Object keys sorted at every depth**, by code point. Python and JavaScript
  both preserve insertion order, so two clients that built the same config in a
  different order would otherwise hash differently.
* **Array order preserved.** An array is data; sorting it would change meaning.
* **Floats are refused.** `0.1` does not have one shortest representation
  across languages, and a config that hashes differently on two machines is
  worse than one that will not hash at all. Use a string or an integer.
* **`null` is refused inside the config.** "Absent" and "present but null" are
  the same intent and two different hashes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class NotCanonical(ValueError):
    """The config cannot be encoded reproducibly. The message says which key."""


def _check(value: Any, path: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, str):
        return
    if value is None:
        raise NotCanonical(
            f"{path or 'config'} is null; leave the key out instead — "
            "absent and null are the same intent and two different hashes"
        )
    if isinstance(value, float):
        raise NotCanonical(
            f"{path or 'config'} is a float ({value!r}); use a string or an integer — "
            "floats do not have one shortest form across languages"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NotCanonical(f"{path or 'config'} has a non-string key {key!r}")
            _check(item, f"{path}.{key}" if path else key)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    raise NotCanonical(
        f"{path or 'config'} is a {type(value).__name__}, which has no canonical form"
    )


def canonical_json(config: dict[str, Any]) -> bytes:
    """The exact bytes that get hashed."""
    if not isinstance(config, dict):
        raise NotCanonical(
            f"the config must be an object, not a {type(config).__name__}"
        )
    _check(config, "")
    return json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def config_hash(config: dict[str, Any]) -> str:
    """`sha256` of the canonical encoding, lowercase hex — what goes on chain."""
    return hashlib.sha256(canonical_json(config)).hexdigest()
