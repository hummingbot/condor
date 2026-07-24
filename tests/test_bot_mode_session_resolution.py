"""Bot-mode session→bot_name resolution (`_session_bot_base`).

The per-session PnL distribution and the operator's live-executor view share this
one helper, so its fallback rules must hold for both: a non-empty per-session
``bot_name`` wins (runtime-named bots), but an empty/absent one falls back to the
strategy default — early sessions predating the config key saved ``bot_name: ''``
and must still map to the shared bot they operated.
"""

from pathlib import Path

import yaml

from condor.web.routes.agents import _session_bot_base


def _write_session(strategy_dir: Path, num: int, cfg: dict) -> None:
    sd = strategy_dir / "sessions" / f"session_{num}"
    sd.mkdir(parents=True)
    (sd / "config.yml").write_text(yaml.safe_dump(cfg))


def test_per_session_bot_name_wins(tmp_path):
    # A session that recorded its runtime-derived deploy name takes precedence.
    _write_session(tmp_path, 1, {"bot_name": "dn-CL-BRENTOIL-mm-20260724-182221"})
    assert (
        _session_bot_base(tmp_path, {"bot_name": "dn-mm"}, 1)
        == "dn-CL-BRENTOIL-mm-20260724-182221"
    )


def test_empty_session_bot_name_falls_back_to_default(tmp_path):
    # Early session saved bot_name as '' — must still resolve to the shared bot.
    _write_session(tmp_path, 2, {"bot_name": ""})
    assert _session_bot_base(tmp_path, {"bot_name": "dn-mm"}, 2) == "dn-mm"


def test_missing_session_bot_name_falls_back_to_default(tmp_path):
    # Session config predates the key entirely.
    _write_session(tmp_path, 3, {"some_other": 1})
    assert _session_bot_base(tmp_path, {"bot_name": "dn-mm"}, 3) == "dn-mm"


def test_no_session_dir_uses_default(tmp_path):
    # A session_num with no on-disk config still maps to the strategy default.
    assert _session_bot_base(tmp_path, {"bot_name": "dn-mm"}, 99) == "dn-mm"


def test_direct_executor_strategy_resolves_empty(tmp_path):
    # No default and no per-session bot_name → direct-executor agent, nothing to
    # attribute; distribution and operator merge both correctly skip it.
    _write_session(tmp_path, 1, {"bot_name": ""})
    assert _session_bot_base(tmp_path, {}, 1) == ""
    assert _session_bot_base(tmp_path, None, 99) == ""
