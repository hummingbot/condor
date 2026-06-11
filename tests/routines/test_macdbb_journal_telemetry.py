"""Tests for extended signals_1h journal telemetry parsing."""

from routines.macdbb_replay.journal import _parse_signals_1h


def test_parse_signals_1h_legacy_format():
    raw = (
        "BTC-USD:bb=50.00,macd=1.0,sig=0.5,hist=0.5,gap=1.0,hr=0.5,"
        "tr=bull,mom=inc,fL=0,fS=0,aL=0,aS=0,sL=1.0,sS=2.0"
    )
    signals = _parse_signals_1h(raw)
    sig = signals["BTC-USD"]
    assert sig.bb_mid is None
    assert sig.bullish_cross is None
    assert not sig.has_replay_bands()


def test_parse_signals_1h_extended_format():
    raw = (
        "LIT-USD:bb=40.80,macd=-0.0166,sig=-0.0197,hist=0.0032,gap=0.1596,hr=0.1899,"
        "tr=bear,mom=dec,fL=0,fS=0,aL=0,aS=0,sL=0.0000,sS=0.0000,"
        "mid=1.4500,up=1.5200,bX=0,sX=1,p=1.4911"
    )
    signals = _parse_signals_1h(raw)
    sig = signals["LIT-USD"]
    assert sig.bb_mid == 1.45
    assert sig.bb_upper == 1.52
    assert sig.bullish_cross is False
    assert sig.bearish_cross is True
    assert sig.price == 1.4911
    assert sig.has_replay_bands()
