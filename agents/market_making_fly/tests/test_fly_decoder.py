"""Spike channels → posture: the regime table, warm-up, centring, hysteresis."""

import pytest
from flybrain.decoder import (
    NEUTRAL,
    Baseline,
    Channels,
    DecoderSettings,
    Hysteresis,
    Posture,
    classify,
    decode,
    should_apply,
)

S = DecoderSettings(window=20, warmup=5)


def _warm(baseline, trend=0.0, arousal=10.0, n=None, jitter=True):
    """Feed a calm history with a little spread so std is non-zero."""
    n = n or S.warmup + 5
    for i in range(n):
        t = trend + (0.1 if jitter and i % 2 else -0.1)
        a = arousal + (0.5 if jitter and i % 2 else -0.5)
        decode(Channels(t, a, 0), baseline, S)


@pytest.mark.parametrize(
    "trend_z,arousal_z,gate,expected",
    [
        (0, 3.0, True, "pause"),
        (5, 3.0, True, "pause"),  # pause beats trending
        (0, 1.5, False, "volatile"),
        (2, 1.5, True, "volatile"),  # volatile beats trending
        (2, 0, True, "trending_up"),
        (-2, 0, True, "trending_down"),
        (2, 0, False, "ranging"),  # no gate, no trend call
        (0, -1.5, False, "quiet"),
        (2, -1.5, True, "trending_up"),  # trending beats quiet
        (0.5, 0.5, True, "ranging"),
    ],
)
def test_regime_table(trend_z, arousal_z, gate, expected):
    assert classify(trend_z, arousal_z, gate, S) == expected


def test_warmup_emits_neutral():
    b = Baseline()
    for _ in range(S.warmup - 1):
        assert decode(Channels(50.0, 500.0, 5), b, S) == NEUTRAL
    assert decode(Channels(50.0, 500.0, 5), b, S).warm is True


def test_centring_removes_constant_bias():
    b = Baseline()
    _warm(b, trend=8.0)  # the circuit "always turns right"
    p = decode(Channels(8.0, 10.0, 3), b, S)
    assert p.regime == "ranging" and abs(p.trend_z) < 1 and p.shift_bps == 0


def test_raw_mode_keeps_bias():
    raw = DecoderSettings(window=20, warmup=5, center_bias=False)
    b = Baseline()
    for _ in range(10):
        decode(Channels(8.0, 10.0, 3), b, raw)
    p = decode(Channels(8.0, 10.0, 3), b, raw)
    assert p.trend_z == pytest.approx(4.0)  # 8 Hz / 2 Hz unit


def test_trend_up_leans_and_caps():
    b = Baseline()
    _warm(b)
    p = decode(Channels(50.0, 10.0, 2), b, S)
    assert p.regime == "trending_up" and p.shift_bps == S.max_shift_bps and p.gate


def test_no_gate_no_lean():
    b = Baseline()
    _warm(b)
    p = decode(Channels(50.0, 10.0, 0), b, S)
    assert p.shift_bps == 0 and p.regime == "ranging"


def test_arousal_tightens_sizes_up_and_pauses():
    """An aroused fly leans in: tighter quotes and more of the book. The sign
    is a choice, not a finding — arousal is a population rate against its own
    average and nothing ties it to volatility."""
    b = Baseline()
    _warm(b)
    hot = decode(Channels(0.0, 11.5, 0), b, S)
    assert hot.arousal_z > 1
    assert hot.spread_mult < 1 and hot.size_mult > 1
    b2 = Baseline()
    _warm(b2)
    calm = decode(Channels(0.0, 8.0, 0), b2, S)
    assert calm.arousal_z < 0
    assert calm.spread_mult > 1 and calm.size_mult < 1
    # the breaker still fires on the same channel, and both knobs stay clipped
    b3 = Baseline()
    _warm(b3)
    pause = decode(Channels(0.0, 100.0, 0), b3, S)
    assert pause.regime == "pause"
    assert pause.spread_mult == S.spread_min and pause.size_mult == S.size_max


def test_baseline_window_and_roundtrip():
    b = Baseline()
    _warm(b, n=50)
    assert b.count == S.window
    again = Baseline.from_dict(b.to_dict())
    assert again.trend == b.trend and again.arousal == b.arousal


def test_posture_roundtrip():
    p = Posture("quiet", 0.8, 1.0, 1.0, -1.0, -0.2, -1.3, 0.0, True, True)
    assert Posture.from_dict(p.to_dict()) == p


def test_channels_validation():
    with pytest.raises(ValueError):
        Channels(float("nan"), 1.0, 0)
    with pytest.raises(ValueError):
        Channels(0.0, -1.0, 0)


def test_settings_validation():
    with pytest.raises(ValueError):
        DecoderSettings(z_regime=3.0, z_pause=2.5)
    with pytest.raises(ValueError):
        DecoderSettings(warmup=0)


def test_hysteresis():
    h = Hysteresis(min_apply_interval_sec=300)
    base = Posture("ranging", 1.0, 1.0, 1.0, 0.0, 0, 0, 0.0, False, True)
    assert should_apply(None, base, None, 1000, h)[0]
    same = Posture("ranging", 1.05, 1.0, 1.0, 0.2, 0, 0, 0.0, False, True)
    assert not should_apply(base, same, 0, 1000, h)[0]
    regime = Posture("volatile", 1.0, 1.0, 1.0, 0.0, 0, 1.2, 0.0, False, True)
    assert should_apply(base, regime, 0, 1000, h)[0]
    assert not should_apply(base, regime, 900, 1000, h)[0]  # cooldown
    wider = Posture("ranging", 1.2, 1.0, 1.0, 0.0, 0, 0, 0.0, False, True)
    assert should_apply(base, wider, 0, 1000, h)[0]
    lean = Posture("ranging", 1.0, 1.0, 1.0, 0.6, 0, 0, 0.0, True, True)
    assert should_apply(base, lean, 0, 1000, h)[0]


def test_valence_moves_the_size_the_fly_commits():
    """MBON07 minus MBON11 is what the KC→MBON memory rule writes to, so it is
    the only path a P&L pulse has to a decision. Without it the dopamine loop
    moved thousands of synapses and changed nothing the fly did."""
    b = Baseline()
    for _ in range(S.warmup):
        b.push(Channels(0.0, 8.0, 0, 0.0, 400), S.window)
    good = decode(Channels(0.0, 8.0, 0, 6.0, 400), b, S)
    assert good.valence_z > 1 and good.size_mult > 1
    b2 = Baseline()
    for _ in range(S.warmup):
        b2.push(Channels(0.0, 8.0, 0, 0.0, 400), S.window)
    bad = decode(Channels(0.0, 8.0, 0, -6.0, 400), b2, S)
    assert bad.valence_z < -1 and bad.size_mult < 1
    # and it moves size only — the spread is the arousal channel's
    assert good.spread_mult == pytest.approx(bad.spread_mult)


def test_a_scene_that_never_reached_the_mushroom_body_is_not_acted_on():
    """Kenyon drive is the confidence test: with no sparse code of the chart,
    every other channel is reading the network's own noise."""
    b = Baseline()
    for _ in range(S.warmup):
        b.push(Channels(0.0, 8.0, 0, 0.0, 400), S.window)
    seen = decode(Channels(0.0, 8.0, 1, 0.0, 400), b, S)
    assert seen.confident

    dark = Baseline()
    for _ in range(S.warmup):
        dark.push(Channels(0.0, 8.0, 0, 0.0, 400), S.window)
    blind = decode(Channels(0.0, 8.0, 1, 0.0, 0), dark, S)
    assert not blind.confident
    ok, why = should_apply(seen, blind, None, 0.0, Hysteresis())
    assert ok is False and "kenyon" in why.lower()
