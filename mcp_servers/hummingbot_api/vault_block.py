"""The vault block: what a Condor Vault run tells its tools, and nothing more.

A leaf module on purpose, like ``profiles.py``: strings and one validator, no
argv, no settings, no imports from either side. The spawner
(``condor.runtime.toolsets``) validates a run config's ``vault`` block with it
before putting the block on ``--vault-json``, and ``settings.py`` validates
what arrives off argv with the same function — one definition of the shape, so
the two ends cannot disagree about what a vault block is.

Every key is public: the vault's slug, its Token-2022 mint, its DBC pool, the
run id the bridge assigned, the Swig funds-owner address the sweeps sign from,
and the runner's chosen buyback share. ``session_dir`` is the one local key —
where this run keeps its sweep ledger — and is optional because an experiment
keeps no session directory. Nothing confidential belongs here, because argv is
readable by every local user.
"""

from __future__ import annotations

import json
from typing import Any

VAULT_REQUIRED_KEYS = ("slug", "mint", "pool", "run_id", "swig_wallet", "buyback_bps")
VAULT_OPTIONAL_KEYS = ("session_dir",)
VAULT_KEYS = VAULT_REQUIRED_KEYS + VAULT_OPTIONAL_KEYS


def validate_vault_block(block: Any) -> dict[str, Any]:
    """Return ``block`` if it is a well-formed vault block, else raise ValueError.

    Strict on purpose: ``sweep_fees`` signs from the address in here, so a block
    missing a key, carrying one the shape does not define, or holding a
    malformed ``buyback_bps`` is refused where it is built, not discovered
    mid-sweep. ``buyback_bps`` is an integer in ``[0, 10000]``; ``0`` is legal
    (the runner chose no buyback) and ``sweep_fees`` refuses on it by itself.
    """
    if not isinstance(block, dict):
        raise ValueError("the vault block must be a mapping")
    missing = [k for k in VAULT_REQUIRED_KEYS if k not in block]
    if missing:
        raise ValueError(f"the vault block is missing {', '.join(missing)}")
    unknown = sorted(set(block) - set(VAULT_KEYS))
    if unknown:
        raise ValueError(
            "the vault block carries keys the shape does not define: "
            + ", ".join(unknown)
        )
    for key in ("slug", "mint", "pool", "run_id", "swig_wallet"):
        if not isinstance(block[key], str) or not block[key].strip():
            raise ValueError(f"the vault block's {key} must be a non-empty string")
    bps = block["buyback_bps"]
    if isinstance(bps, bool) or not isinstance(bps, int) or not 0 <= bps <= 10_000:
        raise ValueError(
            f"the vault block's buyback_bps must be an integer in [0, 10000], got {bps!r}"
        )
    if "session_dir" in block and (
        not isinstance(block["session_dir"], str) or not block["session_dir"]
    ):
        raise ValueError("the vault block's session_dir must be a non-empty string")
    return block


def encode_vault_block(block: dict[str, Any]) -> str:
    """The block as the one JSON string ``--vault-json`` carries."""
    return json.dumps(
        validate_vault_block(block), sort_keys=True, separators=(",", ":")
    )


def decode_vault_block(raw: str) -> dict[str, Any]:
    """The block back off argv, validated the same way."""
    try:
        block = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--vault-json is not valid JSON: {exc}") from exc
    return validate_vault_block(block)
