"""A restarted session's book must count the bands it adopted (SEC-559).

A band is tagged with the session that *opened* it. When a session restarts, or
when a strategy is redeployed under a fresh session number, it adopts the bands
still running from its predecessor and they keep the predecessor's
``controller_id``. Every strictly session-scoped read then under-counts: the
session in the field ran three live bands and reported two, and ``RiskEngine``
sized its limits against that wrong book.

The fix has two halves, both pinned here:

* the book is *the strategy's* family, not the session's exact id — a controller
  tag sharing the ``{agent}.{strategy}`` prefix is ours
  (:func:`same_strategy_family`);
* the Executors ledger actually mirrors that book each tick
  (``_mirror_executor_ledger``/``_book_totals``). ``track_executor`` existed but
  had no production caller, so the section stayed empty however many bands ran.

An unreachable API must not empty the book: the ledger keeps its last known
state, which is the safe direction for a gate that reads it.
"""

from types import SimpleNamespace

import pytest

from condor.agents.engine import TickEngine
from condor.agents.journal import JournalManager
from condor.agents.ownership import same_strategy_family, strip_session_suffix
from condor.agents.performance import _extract_executors_list

AGENT_ID = "solana_dex_lp_expert.adaptive_band_roaming_49"
FAMILY = "solana_dex_lp_expert.adaptive_band_roaming"


# ---------------------------------------------------------------------------
# Family matching
# ---------------------------------------------------------------------------


def test_strip_session_suffix_drops_the_session_number():
    assert strip_session_suffix(AGENT_ID) == FAMILY
    assert strip_session_suffix(f"{FAMILY}_e3") == FAMILY


def test_same_strategy_family_covers_self_sibling_and_rejects_foreign():
    # This session itself.
    assert same_strategy_family(AGENT_ID, AGENT_ID)
    # A sibling session of the same strategy — the adopted band's tag.
    assert same_strategy_family(AGENT_ID, f"{FAMILY}_46")
    assert same_strategy_family(AGENT_ID, f"{FAMILY}_e3")
    # A different strategy of the same agent, a different agent, and nothing.
    assert not same_strategy_family(AGENT_ID, "solana_dex_lp_expert.other_strategy_49")
    assert not same_strategy_family(AGENT_ID, "someone_else.adaptive_band_roaming_49")
    assert not same_strategy_family(AGENT_ID, "")
    assert not same_strategy_family("", AGENT_ID)


def test_family_prefix_is_not_a_loose_startswith():
    # ``..._49`` must not be matched by ``..._4`` — the separator is required.
    assert same_strategy_family(f"{FAMILY}_4", f"{FAMILY}_49")


# ---------------------------------------------------------------------------
# Ledger mirroring
# ---------------------------------------------------------------------------


def _row(eid, amount=10.0, pnl=0.0, volume=0.0, pair="X-USDC", connector="meteora"):
    return {
        "id": eid,
        "type": "lp_executor",
        "connector": connector,
        "pair": pair,
        "side": "RANGE",
        "amount": amount,
        "pnl": pnl,
        "volume": volume,
    }


def _engine(journal):
    """A stand-in carrying only what the book methods read off ``self``.

    The real methods are bound so ``_sync_lp_book`` can call
    ``self._mirror_executor_ledger``/``self._fetch_family_lp_rows``.
    """
    engine = SimpleNamespace(journal=journal, _last_skill_data={}, agent_id=AGENT_ID)
    engine._mirror_executor_ledger = TickEngine._mirror_executor_ledger.__get__(
        engine, SimpleNamespace
    )
    engine._book_totals = TickEngine._book_totals.__get__(engine, SimpleNamespace)
    engine._sync_lp_book = TickEngine._sync_lp_book.__get__(engine, SimpleNamespace)
    engine._fetch_family_lp_rows = TickEngine._fetch_family_lp_rows.__get__(
        engine, SimpleNamespace
    )
    return engine


@pytest.fixture
def journal(tmp_path):
    return JournalManager(AGENT_ID, session_dir=tmp_path / "session")


def test_mirror_tracks_a_new_band_then_updates_it(journal):
    engine = _engine(journal)

    engine._mirror_executor_ledger([_row("band-1", amount=30.88, pnl=0.0, volume=0.0)])
    rows = journal.list_executors()
    assert [r["executor"] for r in rows] == ["band-1"]
    assert journal.get_open_executor_count() == 1
    # Exposure is the band's quote amount, carried through the ledger row.
    assert journal.get_total_exposure() == pytest.approx(30.88)

    # Second tick: same band, moved PnL/volume — updated, not tracked twice.
    engine._mirror_executor_ledger(
        [_row("band-1", amount=30.88, pnl=0.42, volume=147.0)]
    )
    rows = journal.list_executors()
    assert len(rows) == 1
    assert float(rows[0]["pnl"]) == pytest.approx(0.42)
    assert float(rows[0]["volume"]) == pytest.approx(147.0)


def test_mirror_keeps_adopted_siblings_in_one_book(journal):
    """The whole point: three bands, two of them a predecessor's, count as three."""
    engine = _engine(journal)
    engine._mirror_executor_ledger(
        [
            _row("mine", amount=0.27),
            _row("adopted-a", amount=30.88),
            _row("adopted-b", amount=31.41),
        ]
    )
    assert journal.get_open_executor_count() == 3
    assert journal.get_total_exposure() == pytest.approx(0.27 + 30.88 + 31.41)


def test_mirror_marks_a_vanished_band_stopped(journal):
    engine = _engine(journal)
    engine._mirror_executor_ledger([_row("band-1"), _row("band-2")])
    assert journal.get_open_executor_count() == 2

    # band-2 is gone from the live book: closed, not silently left open.
    engine._mirror_executor_ledger([_row("band-1")])
    statuses = {r["executor"]: r["status"] for r in journal.list_executors()}
    assert statuses["band-1"] == "open"
    assert statuses["band-2"] == "closed"
    assert journal.get_open_executor_count() == 1


def test_mirror_is_a_noop_without_a_journal():
    engine = _engine(None)
    # Must not raise: experiments run with no journal.
    engine._mirror_executor_ledger([_row("band-1")])


# ---------------------------------------------------------------------------
# Book totals
# ---------------------------------------------------------------------------


def test_book_totals_prefers_the_ledger(journal):
    engine = _engine(journal)
    engine._last_skill_data = {"executors": [], "total_exposure": 0.0}
    journal.track_executor("band-1", "lp_executor", {"total_amount_quote": 30.88})
    journal.track_executor("band-2", "lp_executor", {"total_amount_quote": 31.41})
    assert engine._book_totals() == (2, pytest.approx(30.88 + 31.41))


def test_book_totals_falls_back_to_the_provider_without_a_journal():
    engine = _engine(None)
    engine._last_skill_data = {"executors": [{"id": "bot-1"}], "total_exposure": 12.5}
    assert engine._book_totals() == (1, 12.5)


def test_book_totals_falls_back_when_the_ledger_is_empty(journal):
    """A controller-mode session's positions come from its bots, not the ledger."""
    engine = _engine(journal)
    engine._last_skill_data = {"executors": [{"id": "bot-1"}], "total_exposure": 12.5}
    assert engine._book_totals() == (1, 12.5)


# ---------------------------------------------------------------------------
# Fetch + sync
# ---------------------------------------------------------------------------


class _FakeExecutors:
    def __init__(self, result=None, raises=False):
        self._result = result
        self._raises = raises

    async def search_executors(self, **kwargs):
        if self._raises:
            raise RuntimeError("api down")
        return self._result


class _FakeClient:
    def __init__(self, result=None, raises=False):
        self.executors = _FakeExecutors(result, raises)


@pytest.mark.asyncio
async def test_fetch_family_rows_keeps_only_this_strategys_bands():
    engine = SimpleNamespace(agent_id=AGENT_ID)
    ours = {"id": "mine", "controller_id": AGENT_ID, "type": "lp_executor"}
    adopted = {"id": "adopted", "controller_id": f"{FAMILY}_46", "type": "lp_executor"}
    foreign = {
        "id": "foreign",
        "controller_id": "someone_else.other_strategy_1",
        "type": "lp_executor",
    }
    untagged = {"id": "untagged", "type": "lp_executor"}
    client = _FakeClient(result={"executors": [ours, adopted, foreign, untagged]})

    rows = await TickEngine._fetch_family_lp_rows(engine, client)
    assert [r["id"] for r in rows] == ["mine", "adopted"]


@pytest.mark.asyncio
async def test_fetch_returns_none_when_the_api_fails():
    engine = SimpleNamespace(agent_id=AGENT_ID)
    assert (
        await TickEngine._fetch_family_lp_rows(engine, _FakeClient(raises=True)) is None
    )


@pytest.mark.asyncio
async def test_sync_mirrors_into_the_ledger(journal):
    engine = _engine(journal)

    async def _rows(client):
        return [_row("band-1", amount=30.88), _row("band-2", amount=31.41)]

    engine._fetch_family_lp_rows = _rows
    rows = await TickEngine._sync_lp_book(engine, object())
    assert len(rows) == 2
    assert journal.get_open_executor_count() == 2


@pytest.mark.asyncio
async def test_sync_leaves_the_ledger_intact_when_the_api_fails(journal):
    """An unreachable API must not read as an empty book — the gate fails closed."""
    engine = _engine(journal)
    journal.track_executor("band-1", "lp_executor", {"total_amount_quote": 30.88})

    async def _none(client):
        return None

    engine._fetch_family_lp_rows = _none
    assert await TickEngine._sync_lp_book(engine, object()) is None
    assert journal.get_open_executor_count() == 1


@pytest.mark.asyncio
async def test_sync_is_a_noop_without_a_journal():
    engine = _engine(None)
    assert await TickEngine._sync_lp_book(engine, object()) is None


def test_extract_executors_list_reads_the_known_envelopes():
    assert _extract_executors_list([{"id": "a"}]) == [{"id": "a"}]
    assert _extract_executors_list({"executors": [{"id": "a"}]}) == [{"id": "a"}]
    assert _extract_executors_list({"data": [{"id": "a"}]}) == [{"id": "a"}]
    assert _extract_executors_list(None) == []
