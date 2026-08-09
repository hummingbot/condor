import asyncio
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agents.lp_agent_lite.routines import snapshot_lp_metrics as routine


def _position(index=1, **changes):
    values = {
        "executor_id": f"executor-{index}",
        "position_address": str(index + 1) * 32,
        "pool_address": "8" * 32,
        "position_mint": "9" * 32,
        "state": "active",
        "age_minutes": "12.5",
        "base_amount": "0.1",
        "quote_amount": "2.3",
        "fees_quote": "0.02",
        "pnl_quote": "0.04",
        "pnl_ratio": "0.004",
    }
    values.update(changes)
    return values


def _config(controller="lp_agent_lite.orca_7", tick=2, **changes):
    values = {
        "controller_id": controller,
        "tick": tick,
        "session_pnl_quote": "0.25",
        "quote_balance": "9.75",
        "sol_balance": "0.2",
        "positions": [_position()],
        "residuals": [
            {
                "mint": "7" * 32,
                "amount": "0.01",
                "value_quote": "0.002",
                "status": "clean",
            }
        ],
        "last": {
            "kind": "open",
            "identity": "executor-1",
            "status": "confirmed",
            "transaction": "tx-1",
        },
    }
    values.update(changes)
    return routine.Config(**values)


def _engine(tmp_path, *, mode="loop", tick_count=1, session_number=7):
    strategy_dir = tmp_path / "strategies" / "orca"
    session_dir = strategy_dir / "sessions" / f"session_{session_number}"
    if mode == "loop":
        session_dir.mkdir(parents=True)
        session_config = session_dir / "config.yml"
        session_config.write_text("execution_mode: loop\n")
        os.utime(session_config, (1_000, 1_000))
    return SimpleNamespace(
        config={"execution_mode": mode},
        strategy=SimpleNamespace(slug="orca", dir=strategy_dir),
        agent=SimpleNamespace(slug="lp_agent_lite"),
        agent_id=(
            f"lp_agent_lite.orca_{session_number}"
            if mode == "loop"
            else f"lp_agent_lite.orca_e{session_number}"
        ),
        status="running",
        journal=SimpleNamespace(tick_count=tick_count) if mode == "loop" else None,
        session_dir=session_dir if mode == "loop" else None,
        _last_tick_at=1_600.0,
    )


def _install(monkeypatch, engine):
    monkeypatch.setattr(
        routine,
        "_get_engine",
        lambda controller: engine if controller == engine.agent_id else None,
    )


def _run(config):
    raw = asyncio.run(routine.run(config, None))
    assert len(raw) < 1_900
    result = json.loads(raw)
    assert result["report_id"] == "rpt001"
    assert result["report_error"] is None
    return raw, result


def test_loop_snapshot_uses_current_session_clock_and_writes_same_json(
    monkeypatch, tmp_path
):
    engine = _engine(tmp_path)
    _install(monkeypatch, engine)
    raw, result = _run(_config())
    target = engine.session_dir / "metrics" / "tick_2.json"

    assert result["ts"] == "1970-01-01T00:26:40Z"
    assert result["session"]["start"] == "1970-01-01T00:16:40Z"
    assert Decimal(result["session"]["age_min"]) == Decimal("10")
    assert result["session"]["pnl_q"] == "0.25"
    assert result["wallet"] == {"quote": "9.75", "sol": "0.2"}
    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert result["artifact_write"] is True
    artifact = json.loads(target.read_text())
    assert artifact == {
        key: value
        for key, value in result.items()
        if key not in {"report_id", "report_error"}
    }


def test_same_tick_write_is_idempotent_and_conflict_preserves_original(
    monkeypatch, tmp_path
):
    engine = _engine(tmp_path)
    _install(monkeypatch, engine)
    raw, result = _run(_config())
    repeated_raw, repeated = _run(_config())
    target = engine.session_dir / "metrics" / "tick_2.json"

    assert repeated_raw == raw
    assert repeated == result

    _, conflict = _run(_config(quote_balance="8"))
    assert conflict["status"] == "unavailable"
    assert "different facts" in conflict["reason"]
    artifact = json.loads(target.read_text())
    assert artifact == {
        key: value
        for key, value in result.items()
        if key not in {"report_id", "report_error"}
    }


@pytest.mark.parametrize("mode", ["dry_run", "run_once"])
def test_experiment_modes_preview_without_clock_or_custom_state(
    monkeypatch, tmp_path, mode
):
    engine = _engine(tmp_path, mode=mode, session_number=3)
    _install(monkeypatch, engine)
    _, result = _run(_config(controller=engine.agent_id, tick=1))

    assert result["preview"] is True
    assert result["status"] == "preview"
    assert result["mutation"] is False
    assert result["artifact_write"] is False
    assert "start" not in result["session"]
    assert "age_min" not in result["session"]
    assert not (tmp_path / "strategies" / "orca" / "sessions").exists()


def test_foreign_or_malformed_session_path_fails_closed(monkeypatch, tmp_path):
    engine = _engine(tmp_path)
    foreign = tmp_path / "foreign" / "session_7"
    foreign.mkdir(parents=True)
    engine.session_dir = foreign
    _install(monkeypatch, engine)
    _, result = _run(_config())

    assert result["status"] == "unavailable"
    assert "unsafe" in result["reason"]
    assert not (foreign / "metrics").exists()


def test_stale_tick_and_wrong_mode_identity_write_nothing(monkeypatch, tmp_path):
    engine = _engine(tmp_path)
    _install(monkeypatch, engine)
    _, stale = _run(_config(tick=1))
    assert stale["status"] == "unavailable"

    engine.config["execution_mode"] = "run_once"
    _, wrong = _run(_config())
    assert wrong["status"] == "unavailable"
    assert not (engine.session_dir / "metrics").exists()


def test_untracked_direct_orca_position_has_exact_position_and_null_executor(
    monkeypatch, tmp_path
):
    engine = _engine(tmp_path)
    _install(monkeypatch, engine)
    untracked = _position(executor_id=None, state="untracked")
    _, result = _run(_config(positions=[untracked]))

    row = result["p"][0]
    assert row[0] is None
    assert row[1] == untracked["position_address"]
    assert row[4] == "untracked"


def test_tracked_state_without_executor_and_nonfinite_numbers_are_rejected():
    with pytest.raises(ValidationError):
        _config(positions=[_position(executor_id=None)])
    with pytest.raises(ValidationError):
        _config(session_pnl_quote="NaN")
    with pytest.raises(ValidationError):
        _config(exit_state="wind_down")


def test_large_current_tick_snapshot_uses_deterministic_bounded_prefix(
    monkeypatch, tmp_path
):
    engine = _engine(tmp_path)
    _install(monkeypatch, engine)
    positions = [
        _position(
            index,
            executor_id=f"executor-{index}-" + "x" * 90,
            position_address=str(index + 2) * 32,
        )
        for index in range(1, 11)
    ]
    residuals = [
        {
            "mint": chr(65 + index) * 32,
            "amount": "1",
            "value_quote": "1",
            "status": "cleanup",
        }
        for index in range(10)
    ]
    raw, result = _run(_config(positions=positions, residuals=residuals))

    assert len(raw) <= routine._TARGET_CHARS
    assert result["p_omit"] + len(result["p"]) == 10
    assert result["r_omit"] + len(result["r"]) == 10
    assert [row[1] for row in result["p"]] == sorted(
        row["position_address"] for row in positions
    )[: len(result["p"])]


def test_symlinked_metrics_directory_is_rejected(monkeypatch, tmp_path):
    engine = _engine(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (engine.session_dir / "metrics").symlink_to(outside, target_is_directory=True)
    _install(monkeypatch, engine)
    _, result = _run(_config())

    assert result["status"] == "unavailable"
    assert "unsafe" in result["reason"]
    assert list(outside.iterdir()) == []
