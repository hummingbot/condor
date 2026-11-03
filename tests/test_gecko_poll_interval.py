"""A coarser pool chart must actually cost less GeckoTerminal budget.

Every open pool chart polls GeckoTerminal forever with ``use_cache=False`` — that
is the whole feed for a DEX pair, there is no WS behind it — and they all draw on
one shared per-minute budget (``_GECKO_RATE_LIMIT`` in ``condor/pool_data.py``).

The poll rate used to be ``max(min(interval_sec // 2, 60), 30)``, whose 60s
ceiling was already reached at 5m: a 15m chart and a 5m chart both spent one
request a minute, so picking the coarser interval to save budget saved nothing.
The rate scales with the candle now — roughly five refreshes per forming candle —
which is what makes 15m the cheap default the pool page picks.
"""

from condor.web.streams.candles import (
    _GECKO_POLL_MAX,
    _GECKO_POLL_MIN,
    _INTERVAL_SECONDS,
    _gecko_poll_interval,
)


def polls_per_hour(interval: str) -> float:
    return 3600 / _gecko_poll_interval(_INTERVAL_SECONDS[interval])


def test_coarser_interval_polls_strictly_less():
    """The reason to default a pool chart to 15m rather than 5m or 1m."""
    assert polls_per_hour("15m") < polls_per_hour("5m") < polls_per_hour("1m")


def test_default_pool_interval_is_a_third_of_the_old_cost():
    """15m, the DexPool default: 20 requests an hour, down from 60."""
    assert _gecko_poll_interval(_INTERVAL_SECONDS["15m"]) == 180
    assert polls_per_hour("15m") == 20


def test_fine_intervals_keep_a_live_feel():
    """A 1m chart still ticks twice a candle — the floor, not the scaled rate."""
    assert _gecko_poll_interval(_INTERVAL_SECONDS["1m"]) == _GECKO_POLL_MIN


def test_hourly_candles_do_not_freeze_the_chart():
    """A chart silent for minutes reads as broken, however long its candle is."""
    assert _gecko_poll_interval(_INTERVAL_SECONDS["1d"]) == _GECKO_POLL_MAX


def test_every_interval_stays_inside_the_bounds():
    for interval, seconds in _INTERVAL_SECONDS.items():
        poll = _gecko_poll_interval(seconds)
        assert _GECKO_POLL_MIN <= poll <= _GECKO_POLL_MAX, interval
