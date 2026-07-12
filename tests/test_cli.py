"""Tests for the condor CLI (condor/cli.py): env writing, harness
detection, and selection parsing."""

import argparse
from pathlib import Path

import pytest

import condor.cli as cli


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("TELEGRAM_TOKEN=\nADMIN_USER_ID=\n")
    monkeypatch.setattr(cli, "ENV_FILE", env)
    monkeypatch.setattr(cli, "ENV_EXAMPLE", example)
    return env


def test_write_env_var_creates_from_example(env_file):
    cli._write_env_var("ADMIN_USER_ID", "42")
    content = env_file.read_text()
    assert "ADMIN_USER_ID=42" in content
    assert "TELEGRAM_TOKEN=" in content  # template lines preserved


def test_write_env_var_is_idempotent(env_file):
    cli._write_env_var("ADMIN_USER_ID", "42")
    cli._write_env_var("ADMIN_USER_ID", "99")
    content = env_file.read_text()
    assert "ADMIN_USER_ID=99" in content
    assert "ADMIN_USER_ID=42" not in content
    assert content.count("ADMIN_USER_ID=") == 1


def test_write_env_var_appends_new_key(env_file):
    cli._write_env_var("NEW_KEY", "value")
    assert "NEW_KEY=value" in env_file.read_text()
    assert cli._read_env_file()["NEW_KEY"] == "value"


def test_detect_harnesses_by_config_dir(tmp_path, monkeypatch):
    """Config dirs count as evidence even when the binary is not on PATH."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    (tmp_path / ".hermes").mkdir()
    detected = cli.detect_harnesses(home=tmp_path)
    assert detected == {"claude-code": False, "openclaw": False, "hermes": True}


def test_detect_harnesses_by_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: "/bin/x" if name == "claude" else None
    )
    detected = cli.detect_harnesses(home=tmp_path)
    assert detected["claude-code"] is True
    assert detected["openclaw"] is False


def _args(harness):
    return argparse.Namespace(harness=harness)


def test_select_harnesses_flag_parsing():
    assert cli._select_harnesses(_args("claude-code,hermes")) == [
        "claude-code",
        "hermes",
    ]


def test_select_harnesses_none_means_empty():
    assert cli._select_harnesses(_args("none")) == []


def test_select_harnesses_rejects_unknown():
    with pytest.raises(SystemExit):
        cli._select_harnesses(_args("cursor"))
