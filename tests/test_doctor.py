"""condor/doctor.py's pure logic: the tally, and the small classifiers the
port/connectivity checks lean on. Rendering is colored only under a real tty
(``_USE_COLOR`` is resolved once at import time), so these assert on content
rather than escape codes -- pytest's captured stdout is never a tty.
"""

import utils.config as config_module
from condor import doctor


def test_tally_counts_each_state_and_exits_nonzero_only_on_a_failure():
    checks = [
        doctor.Check("a", doctor.OK, "fine"),
        doctor.Check("b", doctor.OK, "fine"),
        doctor.Check("c", doctor.WARN, "hmm"),
        doctor.Check("d", doctor.FAIL, "broken"),
    ]

    line, exit_code = doctor._tally(checks)

    assert exit_code == 1
    assert "2 passed" in line
    assert "1 warning(s)" in line
    assert "1 failed" in line


def test_tally_exits_zero_with_only_warnings():
    checks = [
        doctor.Check("a", doctor.OK, "fine"),
        doctor.Check("b", doctor.WARN, "hmm"),
    ]

    line, exit_code = doctor._tally(checks)

    assert exit_code == 0
    assert "1 passed" in line
    assert "1 warning(s)" in line
    assert "0 failed" in line


def test_tally_of_no_checks_is_all_zero_and_exits_zero():
    line, exit_code = doctor._tally([])
    assert exit_code == 0
    assert "0 passed" in line
    assert "0 warning(s)" in line
    assert "0 failed" in line


def test_check_render_includes_the_glyph_name_and_detail():
    check = doctor.Check("Dashboard port", doctor.WARN, "nothing listening on 8088")
    rendered = check.render(width=20)
    assert doctor._BADGES[doctor.WARN] in rendered
    assert "Dashboard port" in rendered
    assert "nothing listening on 8088" in rendered


def test_is_public_bind_flags_every_interface():
    assert doctor._is_public_bind("0.0.0.0:8088") is True
    assert doctor._is_public_bind("*:8088") is True
    assert doctor._is_public_bind("[::]:8088") is True


def test_is_public_bind_does_not_flag_loopback():
    assert doctor._is_public_bind("127.0.0.1:8088") is False


def test_connection_hint_recognizes_an_auth_failure():
    hint = doctor._connection_hint("localhost", Exception("401 Unauthorized"))
    assert "username/password" in hint


def test_connection_hint_points_at_docker_for_a_local_host():
    hint = doctor._connection_hint("localhost", Exception("Connection refused"))
    assert "docker compose ps" in hint


def test_connection_hint_is_generic_for_a_remote_host():
    hint = doctor._connection_hint("some-remote-host", Exception("Connection refused"))
    assert "docker compose ps" not in hint
    assert "firewall" in hint


def test_connection_hint_points_at_the_tailscale_check_when_enabled(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", True)
    hint = doctor._connection_hint("hummingbot-api", Exception("Connection refused"))
    assert "Tailscale check above" in hint


def test_connection_hint_auth_failure_still_wins_when_tailscale_is_enabled(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", True)
    hint = doctor._connection_hint("hummingbot-api", Exception("401 Unauthorized"))
    assert "username/password" in hint


# ── Dashboard port: nothing listening is normal, not a warning ──────────────


def test_dashboard_port_check_is_ok_not_warn_when_nothing_is_listening(monkeypatch):
    monkeypatch.setattr(doctor, "_listening_binds", lambda port: [])
    checks = doctor.check_dashboard_port()
    assert len(checks) == 1
    assert checks[0].state == doctor.OK
    assert "not running yet" in checks[0].detail


# ── Tailscale check ──────────────────────────────────────────────────────────


def test_tailscale_check_is_ok_when_not_enabled(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", False)
    checks = doctor.check_tailscale()
    assert len(checks) == 1
    assert checks[0].state == doctor.OK
    assert "not enabled" in checks[0].detail


def test_tailscale_check_fails_when_enabled_but_not_installed(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", True)
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: None)
    checks = doctor.check_tailscale()
    assert checks[0].state == doctor.FAIL
    assert "isn't installed" in checks[0].detail
    assert "tailscale.com/install.sh" in checks[0].detail


def test_tailscale_check_fails_when_installed_but_not_connected(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", True)
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: "/usr/bin/tailscale")

    class FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeResult())
    checks = doctor.check_tailscale()
    assert checks[0].state == doctor.FAIL
    assert "not connected" in checks[0].detail


def test_tailscale_check_is_ok_when_connected(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", True)
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: "/usr/bin/tailscale")

    class FakeResult:
        returncode = 0
        stdout = "condor.tailxxxx.ts.net  100.64.0.1   me@example.com  linux  -\n"

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeResult())
    checks = doctor.check_tailscale()
    assert checks[0].state == doctor.OK
    assert "condor.tailxxxx.ts.net" in checks[0].detail


# ── Config: Telegram is optional in local mode ──────────────────────────────


def _patch_readiness_probe(monkeypatch, state):
    import condor.llm.readiness as readiness_module

    async def fake_probe(base, env=None):
        return state

    monkeypatch.setattr(readiness_module, "probe", fake_probe)


def test_check_config_does_not_require_telegram_in_local_mode(tmp_path, monkeypatch):
    from condor.llm.readiness import READY, Readiness

    env_file = tmp_path / ".env"
    env_file.write_text("CONDOR_MODE=local\nADMIN_USER_ID=1\n")
    monkeypatch.setattr(doctor, "ENV_PATH", env_file)
    _patch_readiness_probe(monkeypatch, Readiness(READY, "installed and logged in"))

    checks = doctor.check_config()
    token_check = next(c for c in checks if c.name == "TELEGRAM_TOKEN")
    assert token_check.state == doctor.OK
    assert "local mode" in token_check.detail


def test_check_config_still_requires_telegram_outside_local_mode(tmp_path, monkeypatch):
    from condor.llm.readiness import READY, Readiness

    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_USER_ID=1\n")
    monkeypatch.setattr(doctor, "ENV_PATH", env_file)
    _patch_readiness_probe(monkeypatch, Readiness(READY, "installed and logged in"))

    checks = doctor.check_config()
    token_check = next(c for c in checks if c.name == "TELEGRAM_TOKEN")
    assert token_check.state == doctor.FAIL
    assert "missing" in token_check.detail
