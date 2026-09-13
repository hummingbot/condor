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


def test_arousal_widens_and_pauses():
    b = Baseline()
    _warm(b)
    wide = decode(Channels(0.0, 11.5, 0), b, S)
    assert wide.spread_mult > 1
    b2 = Baseline()
    _warm(b2)
    pause = decode(Channels(0.0, 100.0, 0), b2, S)
    assert pause.regime == "pause" and pause.spread_mult == S.spread_max


def test_baseline_window_and_roundtrip():
    b = Baseline()
    _warm(b, n=50)
    assert b.count == S.window
    again = Baseline.from_dict(b.to_dict())
    assert again.trend == b.trend and again.arousal == b.arousal


def test_posture_roundtrip():
    p = Posture("quiet", 0.8, -1.0, -0.2, -1.3, True, True)
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
    base = Posture("ranging", 1.0, 0.0, 0, 0, False, True)
    assert should_apply(None, base, None, 1000, h)[0]
    same = Posture("ranging", 1.05, 0.2, 0, 0, False, True)
    assert not should_apply(base, same, 0, 1000, h)[0]
    regime = Posture("volatile", 1.0, 0.0, 0, 1.2, False, True)
    assert should_apply(base, regime, 0, 1000, h)[0]
    assert not should_apply(base, regime, 900, 1000, h)[0]  # cooldown
    wider = Posture("ranging", 1.2, 0.0, 0, 0, False, True)
    assert should_apply(base, wider, 0, 1000, h)[0]
    lean = Posture("ranging", 1.0, 0.6, 0, 0, True, True)
    assert should_apply(base, lean, 0, 1000, h)[0]
