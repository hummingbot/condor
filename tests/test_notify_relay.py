"""Tests for the generic notification relay (integrations/notify_relay)."""

import asyncio
import json
import sys

import pytest

import integrations.notify_relay.relay as relay


def test_render_substitutes_as_whole_argv_elements():
    entry = {"text": "tick 1; rm -rf / && echo pwned", "agent_id": "mm_1",
             "kind": "session", "ts": 123.0}
    argv = relay._render(["send", "--text", "{text}", "--who", "{agent_id}"], entry)
    # the dangerous text is ONE element — never split into shell tokens
    assert argv == ["send", "--text", "tick 1; rm -rf / && echo pwned",
                    "--who", "mm_1"]


def test_render_json_placeholder():
    entry = {"text": "hi", "agent_id": "a_1", "kind": "info"}
    argv = relay._render(["curl", "-d", "{json}"], entry)
    assert json.loads(argv[2])["agent_id"] == "a_1"


def _run_relay_briefly(tmp_path, feed, template):
    """Start the relay loop, feed entries after it records EOF, collect deliveries."""
    outbox = tmp_path / "notifications.jsonl"
    outbox.write_text(json.dumps({"text": "PRE-EXISTING", "agent_id": "old"}) + "\n")

    async def scenario():
        relay.POLL_S = 0.05
        task = asyncio.create_task(relay.run(template, outbox=outbox))
        await asyncio.sleep(0.15)  # let it record EOF
        with outbox.open("a") as f:
            for e in feed:
                f.write(json.dumps(e) + "\n")
        await asyncio.sleep(0.3)  # let poll fire
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    return outbox


def test_relay_tails_from_eof_and_delivers(tmp_path):
    sink = tmp_path / "out.txt"
    template = [sys.executable, "-c",
                f"import sys;open(r'{sink}','a').write(sys.argv[1]+'\\n')",
                "{text}"]
    _run_relay_briefly(
        tmp_path,
        [{"text": "tick 1 opened position", "agent_id": "mm_1", "kind": "session"},
         {"text": "delegation done", "agent_id": "mm-d1", "kind": "delegation"}],
        template,
    )
    delivered = sink.read_text().splitlines()
    # PRE-EXISTING (before EOF) is skipped; both new entries delivered in order
    assert delivered == ["tick 1 opened position", "delegation done"]


def test_relay_survives_failing_delivery(tmp_path):
    sink = tmp_path / "out.txt"
    # command exits 1 on the first entry (text contains FAIL), 0 otherwise
    template = [sys.executable, "-c",
                f"import sys;t=sys.argv[1];"
                f"open(r'{sink}','a').write(t+'\\n');"
                f"sys.exit(1 if 'FAIL' in t else 0)",
                "{text}"]
    _run_relay_briefly(
        tmp_path,
        [{"text": "FAIL this one", "agent_id": "a"},
         {"text": "but keep going", "agent_id": "b"}],
        template,
    )
    # both attempted despite the first failing — relay never dies on a bad send
    assert sink.read_text().splitlines() == ["FAIL this one", "but keep going"]


def test_load_template_validation(monkeypatch):
    monkeypatch.delenv("CONDOR_NOTIFY_CMD", raising=False)
    with pytest.raises(SystemExit):
        relay._load_template()
    monkeypatch.setenv("CONDOR_NOTIFY_CMD", "not json")
    with pytest.raises(SystemExit):
        relay._load_template()
    monkeypatch.setenv("CONDOR_NOTIFY_CMD", '"a string not a list"')
    with pytest.raises(SystemExit):
        relay._load_template()
    monkeypatch.setenv("CONDOR_NOTIFY_CMD", '["openclaw","message","--text","{text}"]')
    assert relay._load_template()[0] == "openclaw"
