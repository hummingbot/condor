"""condor/doctor.py's pure logic: the tally, and the small classifiers the
port/connectivity checks lean on. Rendering is colored only under a real tty
(``_USE_COLOR`` is resolved once at import time), so these assert on content
rather than escape codes -- pytest's captured stdout is never a tty.
"""

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
