"""condor/doctor.py's pure logic: the tally, and the small classifiers the
port/connectivity checks lean on. Rendering is colored only under a real tty
(``_USE_COLOR`` is resolved once at import time), so these assert on content
rather than escape codes -- pytest's captured stdout is never a tty.
"""

import os

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


# ── Report layout: long details wrap instead of spilling past the frame ──────


def test_render_wraps_a_long_detail_under_the_detail_column():
    check = doctor.Check("Dashboard port", doctor.WARN, "word " * 40)
    lines = check.render(width=20, report_width=80).splitlines()

    assert len(lines) > 1
    assert all(len(line) <= 80 for line in lines)
    # Continuation lines start under the detail column, not under the badge.
    assert lines[1].startswith(" " * (doctor._GUTTER + 20))


def test_render_leaves_a_short_detail_on_one_line():
    check = doctor.Check("uv", doctor.OK, "uv 0.12.3")
    assert "\n" not in check.render(width=28, report_width=80)


def test_report_width_stays_at_the_wizard_width_for_short_details(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil,
        "get_terminal_size",
        lambda fallback=None: os.terminal_size((200, 50)),
    )
    sections = [("Dependencies", [doctor.Check("uv", doctor.OK, "uv 0.12.3")])]
    assert doctor._report_width(sections) == doctor._FRAME_WIDTH


def test_report_width_grows_for_a_long_detail_but_stays_under_the_cap(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil,
        "get_terminal_size",
        lambda fallback=None: os.terminal_size((500, 50)),
    )
    sections = [("Dependencies", [doctor.Check("uv", doctor.FAIL, "x" * 500)])]
    width = doctor._report_width(sections)
    assert doctor._FRAME_WIDTH < width <= doctor._MAX_WIDTH


def test_report_width_never_exceeds_the_terminal(monkeypatch):
    monkeypatch.setattr(
        doctor.shutil,
        "get_terminal_size",
        lambda fallback=None: os.terminal_size((60, 50)),
    )
    sections = [("Dependencies", [doctor.Check("uv", doctor.FAIL, "x" * 500)])]
    assert doctor._report_width(sections) <= 60


# ── Fresh checkout: one actionable failure, not four ─────────────────────────


def test_check_config_reports_a_single_setup_failure_when_env_is_missing(
    tmp_path, monkeypatch
):
    from condor.llm.readiness import READY, Readiness

    monkeypatch.setattr(doctor, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(doctor, "REPO_ROOT", tmp_path)
    _patch_readiness_probe(monkeypatch, Readiness(READY, "installed and logged in"))

    checks = doctor.check_config()
    failures = [c for c in checks if c.state == doctor.FAIL]

    assert len(failures) == 1
    assert failures[0].name == "Setup"
    assert "make install" in failures[0].detail
    assert not [c for c in checks if c.name in ("TELEGRAM_TOKEN", "ADMIN_USER_ID")]


# ── Placeholder API credentials ──────────────────────────────────────────────


def test_placeholder_credentials_are_flagged():
    data = {"servers": {"local": {"host": "localhost", "password": "admin"}}}
    checks = doctor._check_placeholder_credentials(data)
    assert len(checks) == 1
    assert checks[0].state == doctor.WARN
    assert "local" in checks[0].detail


def test_real_credentials_are_not_flagged():
    data = {"servers": {"local": {"host": "localhost", "password": "s3cr3t-real"}}}
    assert doctor._check_placeholder_credentials(data) == []


def test_placeholder_credentials_tolerates_a_malformed_config():
    assert doctor._check_placeholder_credentials(None) == []
    assert doctor._check_placeholder_credentials({"servers": "nope"}) == []


# ── Doctor never creates config.yml ─────────────────────────────────────────


def test_hummingbot_api_check_does_not_create_a_missing_config(tmp_path, monkeypatch):
    """Instantiating a ConfigManager writes config.yml as a side effect, which
    would both break doctor's read-only contract and make the "not found"
    check above unreachable on every later run."""
    monkeypatch.setattr(doctor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "ENV_PATH", tmp_path / ".env")

    checks = doctor.check_hummingbot_api()

    assert "make setup" in checks[0].detail
    assert not (tmp_path / "config.yml").exists()


# ── Local stack diagnosis ────────────────────────────────────────────────────


def test_local_stack_diagnosis_fails_when_docker_is_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: None)
    checks = doctor._local_stack_diagnosis()
    assert checks[0].state == doctor.FAIL
    assert "not installed" in checks[0].detail


def test_local_stack_diagnosis_fails_when_the_daemon_is_down(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: "/usr/bin/docker")

    class FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeResult())
    checks = doctor._local_stack_diagnosis()
    assert checks[0].state == doctor.FAIL
    assert "daemon is not responding" in checks[0].detail


def test_local_stack_diagnosis_fails_when_the_container_is_absent(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: "/usr/bin/docker")

    class FakeResult:
        returncode = 0
        stdout = "emqx\tUp 2 hours\npostgres\tUp 2 hours\n"

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeResult())
    checks = doctor._local_stack_diagnosis()
    assert checks[0].state == doctor.FAIL
    assert "make deploy" in checks[0].detail


def test_local_stack_diagnosis_is_ok_when_the_container_is_up(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: "/usr/bin/docker")

    class FakeResult:
        returncode = 0
        stdout = "hummingbot-api\tUp 2 hours (healthy)\n"

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeResult())
    checks = doctor._local_stack_diagnosis()
    assert checks[0].state == doctor.OK
    assert "Up 2 hours" in checks[0].detail


# ── Dashboard port names the process holding it ──────────────────────────────


def test_dashboard_port_names_the_holding_process(monkeypatch):
    monkeypatch.setattr(config_module, "USE_TAILSCALE", False)
    monkeypatch.setattr(doctor, "_listening_binds", lambda port: ["0.0.0.0:8088"])
    monkeypatch.setattr(doctor, "_listening_process", lambda port: "python3, pid 5011")

    checks = doctor.check_dashboard_port()
    assert checks[0].state == doctor.WARN
    assert "python3, pid 5011" in checks[0].detail


def test_listening_process_parses_the_ss_users_field(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd: "/usr/bin/ss")

    class FakeResult:
        returncode = 0
        stdout = (
            "0      4096   0.0.0.0:8088  0.0.0.0:*  "
            'users:(("python3",pid=5011,fd=8))\n'
        )

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeResult())
    assert doctor._listening_process(8088) == "python3, pid 5011"


def test_hummingbot_api_check_only_warns_on_a_never_configured_checkout(
    tmp_path, monkeypatch
):
    """The Setup check already reports this; a second failure would just
    double-count the one thing there is to do about it."""
    monkeypatch.setattr(doctor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "ENV_PATH", tmp_path / ".env")

    checks = doctor.check_hummingbot_api()
    assert checks[0].state == doctor.WARN


def test_hummingbot_api_check_fails_when_env_exists_but_config_does_not(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_USER_ID=1\n")
    monkeypatch.setattr(doctor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(doctor, "ENV_PATH", env_file)

    checks = doctor.check_hummingbot_api()
    assert checks[0].state == doctor.FAIL
