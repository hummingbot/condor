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
from condor.fetchers.executors import EXECUTORS_PAGE_SIZE

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


def test_family_rejects_a_strategy_slug_that_extends_the_prefix():
    """``adaptive_band_roaming`` must not absorb ``..._roaming_aggressive``.

    Slugs contain underscores, so a prefix test admitted a *different* strategy
    whose slug starts with ours. That band then entered this strategy's book,
    inflating its exposure and — since an open ledger entry settles the stop gate
    — letting this strategy stop another strategy's band.
    """
    assert not same_strategy_family(f"{FAMILY}_49", f"{FAMILY}_aggressive_3")
    assert not same_strategy_family(f"{FAMILY}_49", f"{FAMILY}_aggressive")


def test_strip_session_suffix_leaves_an_unsuffixed_id_whole():
    # No session token to strip: the id must not be cut at an inner underscore.
    assert strip_session_suffix(FAMILY) == FAMILY
    assert strip_session_suffix("") == ""


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


def test_mirror_reopens_a_band_that_reappears(journal):
    """A band dropped from one short page must come back when seen again.

    A row left ``status=closed`` was excluded from the open count and the
    exposure the gate reads, permanently, even once the band was live again.
    """
    engine = _engine(journal)
    engine._mirror_executor_ledger([_row("band-1"), _row("band-2")])
    engine._mirror_executor_ledger([_row("band-1")])  # band-2 read as closed
    assert journal.get_open_executor_count() == 1

    # The API recovers and band-2 is live again: it must count as open.
    engine._mirror_executor_ledger([_row("band-1"), _row("band-2", amount=31.41)])
    rows = {r["executor"]: r for r in journal.list_executors()}
    assert rows["band-2"]["status"] == "open"
    assert "stopped" not in rows["band-2"]
    assert journal.get_open_executor_count() == 2


def test_mirror_is_a_noop_without_a_journal():
    engine = _engine(None)
    # Must not raise: experiments run with no journal.
    engine._mirror_executor_ledger([_row("band-1")])


# ---------------------------------------------------------------------------
# Book totals
# ---------------------------------------------------------------------------


def test_book_totals_counts_adopted_bands_the_provider_cannot_see(journal):
    engine = _engine(journal)
    engine._last_skill_data = {"executors": [], "total_exposure": 0.0}
    journal.track_executor("band-1", "lp_executor", {"total_amount_quote": 30.88})
    journal.track_executor("band-2", "lp_executor", {"total_amount_quote": 31.41})
    assert engine._book_totals() == (2, pytest.approx(30.88 + 31.41))


def test_book_totals_keeps_bot_exposure_alongside_ledger_bands(journal):
    """Bot positions and a session's own LP band both count. Neither replaces
    the other — dropping the bots' exposure let the gate approve past its
    limits."""
    engine = _engine(journal)
    engine._last_skill_data = {"executors": [{"id": "bot-1"}], "total_exposure": 12.5}
    journal.track_executor("band-1", "lp_executor", {"total_amount_quote": 30.88})
    assert engine._book_totals() == (2, pytest.approx(12.5 + 30.88))


def test_book_totals_does_not_double_count_a_band_the_provider_lists(journal):
    engine = _engine(journal)
    engine._last_skill_data = {
        "executors": [{"id": "band-1"}],
        "total_exposure": 30.88,
    }
    journal.track_executor("band-1", "lp_executor", {"total_amount_quote": 30.88})
    assert engine._book_totals() == (1, pytest.approx(30.88))


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


class _PagedExecutors:
    """Serves ``pages`` in order, one per call, cursor until the last page."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    async def search_executors(self, limit, cursor=None, **kwargs):
        page = self._pages[self.calls] if self.calls < len(self._pages) else []
        self.calls += 1
        envelope = {"executors": page}
        if self.calls < len(self._pages):
            envelope["next_cursor"] = f"c{self.calls}"
        return envelope


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
async def test_fetch_walks_every_page_not_just_the_first():
    """Another strategy's bands may fill the first page; ours are on the next.

    Reading only the first page leaves our live bands out of the book, and the
    mirror then marks them closed — a short book at the gate.
    """
    filler = [
        {"id": f"f{i}", "controller_id": "other.strategy_1", "type": "lp_executor"}
        for i in range(EXECUTORS_PAGE_SIZE)
    ]
    ours = {"id": "mine", "controller_id": AGENT_ID, "type": "lp_executor"}

    client = _FakeClient()
    client.executors = _PagedExecutors([filler, [ours]])
    rows = await TickEngine._fetch_family_lp_rows(
        SimpleNamespace(agent_id=AGENT_ID), client
    )
    assert [r["id"] for r in rows] == ["mine"]
    assert client.executors.calls == 2


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
