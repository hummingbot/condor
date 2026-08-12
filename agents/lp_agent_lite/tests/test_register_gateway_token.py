import asyncio
import copy
import json
from types import SimpleNamespace

from agents.lp_agent_lite.routines import register_gateway_token as routine

MINT = "2" * 32


class Gateway:
    def __init__(self, rows=None, *, add_error=None, read_errors=None):
        self.rows = copy.deepcopy(rows or [])
        self.add_error = add_error
        self.read_errors = list(read_errors or [])
        self.reads = 0
        self.adds = []

    async def get_network_tokens(self, network):
        assert network == "solana-mainnet-beta"
        self.reads += 1
        if self.read_errors:
            error = self.read_errors.pop(0)
            if error is not None:
                raise error
        return {"tokens": copy.deepcopy(self.rows)}

    async def add_token(self, **kwargs):
        self.adds.append(copy.deepcopy(kwargs))
        if self.add_error == "after":
            self.rows.append(
                {
                    "address": kwargs["address"],
                    "symbol": kwargs["symbol"],
                    "decimals": kwargs["decimals"],
                }
            )
            raise TimeoutError("response lost")
        if self.add_error:
            raise self.add_error
        replacement = {
            "address": kwargs["address"],
            "symbol": kwargs["symbol"],
            "decimals": kwargs["decimals"],
        }
        matching = [
            index
            for index, row in enumerate(self.rows)
            if row.get("address") == kwargs["address"]
            or str(row.get("symbol", "")).casefold() == kwargs["symbol"].casefold()
        ]
        if matching:
            self.rows[matching[0]] = replacement
        else:
            self.rows.append(replacement)
        return {"ok": True}


def _config(**changes):
    values = {"mint": MINT, "symbol": "TOK", "decimals": 9}
    values.update(changes)
    return routine.Config(**values)


def _run(monkeypatch, gateway, config=None):
    async def get_client(_context):
        return SimpleNamespace(gateway=gateway)

    monkeypatch.setattr(routine, "_get_client", get_client)
    raw = asyncio.run(routine.run(config or _config(), None))
    assert len(raw) < 1_900
    result = json.loads(raw)
    assert result["report_id"] == "rpt001"
    assert result["report_error"] is None
    return result


def test_exact_existing_tuple_is_confirmed_without_mutation(monkeypatch):
    gateway = Gateway([{"address": MINT, "symbol": "TOK", "decimals": 9}])
    result = _run(monkeypatch, gateway)

    assert result["status"] == "confirmed"
    assert result["mutation"] is False
    assert result["present"] is True
    assert result["canonical_symbol"] == "TOK"
    assert result["symbol_match"] == "exact"
    assert result["registered_token"] == [MINT, "TOK", 9]
    assert gateway.reads == 1
    assert gateway.adds == []


def test_case_only_symbol_alias_uses_gateway_canonical_symbol(monkeypatch):
    gateway = Gateway([{"address": MINT, "symbol": "CBBTC", "decimals": 8}])
    result = _run(
        monkeypatch,
        gateway,
        _config(symbol="cbBTC", decimals=8),
    )

    assert result["status"] == "confirmed"
    assert result["mutation"] is False
    assert result["present"] is True
    assert result["canonical_symbol"] == "CBBTC"
    assert result["symbol_match"] == "case_alias"
    assert result["registered_token"] == [MINT, "CBBTC", 8]
    assert gateway.reads == 1
    assert gateway.adds == []


def test_metadata_or_symbol_collision_rejects_before_submit(monkeypatch):
    cases = [
        [{"address": MINT, "symbol": "OTHER", "decimals": 9}],
        [{"address": "3" * 32, "symbol": "TOK", "decimals": 9}],
        [{"address": MINT, "symbol": "TOK", "decimals": 6}],
    ]
    for rows in cases:
        gateway = Gateway(rows)
        result = _run(monkeypatch, gateway)
        assert result["status"] == "rejected_before_submit"
        assert result["mutation"] is False
        assert gateway.adds == []


def test_preview_is_default_and_never_adds(monkeypatch):
    gateway = Gateway()
    result = _run(monkeypatch, gateway)

    assert result["status"] == "preview"
    assert result["outcome"] == "rejected_before_submit"
    assert result["mutation"] is False
    assert gateway.adds == []


def test_live_absent_token_is_added_once_then_verified(monkeypatch):
    gateway = Gateway()
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "confirmed"
    assert result["mutation"] is True
    assert result["canonical_symbol"] == "TOK"
    assert result["symbol_match"] == "exact"
    assert gateway.reads == 1
    assert gateway.adds == [
        {
            "network_id": "solana-mainnet-beta",
            "address": MINT,
            "symbol": "TOK",
            "decimals": 9,
            "name": "TOK",
        }
    ]


def test_lost_add_response_is_reconciled_without_retry(monkeypatch):
    gateway = Gateway(add_error="after")
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "confirmed"
    assert result["mutation"] is True
    assert "warning" in result
    assert len(gateway.adds) == 1
    assert gateway.reads == 1


def test_add_error_with_absent_post_truth_is_uncertain(monkeypatch):
    gateway = Gateway(add_error=RuntimeError("rejected or response lost"))
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["present"] is False
    assert len(gateway.adds) == 1
    assert gateway.reads == 1


def test_post_add_read_failure_is_uncertain_and_never_retries(monkeypatch):
    gateway = Gateway(read_errors=[TimeoutError("registry unavailable")])
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["present"] is None
    assert len(gateway.adds) == 1
    assert gateway.reads == 1


def test_live_registration_has_no_preflight_registry_read(monkeypatch):
    gateway = Gateway(read_errors=[TimeoutError("secret=" + "x" * 500)])
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert len(gateway.adds) == 1
    assert gateway.reads == 1
    assert len(result["reason"]) <= 180


def test_duplicate_registry_identity_is_ambiguous(monkeypatch):
    row = {"address": MINT, "symbol": "TOK", "decimals": 9}
    gateway = Gateway([row, row])
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "ambiguous"
    assert result["mutation"] is True
    assert len(gateway.adds) == 1
    assert gateway.reads == 1


def test_live_existing_tuple_is_still_added_then_verified(monkeypatch):
    gateway = Gateway([{"address": MINT, "symbol": "TOK", "decimals": 9}])
    result = _run(monkeypatch, gateway, _config(preview=False))

    assert result["status"] == "confirmed"
    assert result["mutation"] is True
    assert result["registered_token"] == [MINT, "TOK", 9]
    assert len(gateway.adds) == 1
    assert gateway.reads == 1
