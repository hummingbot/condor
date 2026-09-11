"""Two writers, one status file: the merge holds together (CORR-247).

``write_status`` reads the file, merges the caller's fields and renames a new
one into place. The rename is atomic, the read-modify-write around it was not,
and there are genuinely two writers in one process: the sharing verbs run off
the event loop (``asyncio.to_thread``) and stamp the share receipt onto a
conversation's ``meta.json`` while the loop keeps writing ``turn_count`` into
the same file. The interesting loss is a privacy one — ``share_delete_token``
is the only local copy of the capability that revokes a share, and
``share_excluded`` is the flag the sweep honours forever.

The two sharing tests force the exact interleaving rather than hoping for it:
the main thread is stalled inside ``write_status`` just after its read, and the
worker runs its whole merge in that window. Without the lock the main thread
then lands its pre-share snapshot and the share fields are gone; with it the
worker simply waits its turn.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import threading
import time

import pytest

from condor.runtime import conversations, registry_file
from condor.runtime.conversations import TurnEntry
from condor.sharing import share

USER_ID = 4242


# ── The general property ─────────────────────────────────────────────────


@pytest.fixture
def widened_write(monkeypatch):
    """Stretch the read→rename window so the race is reachable in a test.

    Production loses this race on a GIL release inside ``read_text`` or
    ``fsync``; half a millisecond of sleep makes the same window wide enough to
    hit reliably. It sits *inside* the lock, so it slows the guarded version
    down without changing what it guarantees.
    """
    real = registry_file.atomic_write_json

    def slow_write(path, data, **kwargs):
        time.sleep(0.0005)
        return real(path, data, **kwargs)

    monkeypatch.setattr(registry_file, "atomic_write_json", slow_write)


def test_disjoint_merges_from_two_threads_both_survive(tmp_path, widened_write):
    """Two threads, two field sets, one file: the result carries both."""
    for i in range(40):
        record = tmp_path / f"record-{i}"
        record.mkdir()

        threads = [
            threading.Thread(
                target=registry_file.write_status, args=(record,), kwargs=fields
            )
            for fields in ({"alpha": i}, {"beta": i})
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        status = registry_file.read_status(record)
        assert status is not None
        assert status["alpha"] == i, f"iteration {i} lost the first writer"
        assert status["beta"] == i, f"iteration {i} lost the second writer"


def test_write_status_stays_synchronous():
    """The lock is held across the read, the merge and the rename — so none of
    those may ever become an await. A lock held across one would block every
    other writer for the length of an I/O round trip."""
    assert not inspect.iscoroutinefunction(registry_file.write_status)
    tree = ast.parse(textwrap.dedent(inspect.getsource(registry_file.write_status)))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Await)]


# ── The sharing race the item is about ───────────────────────────────────


@pytest.fixture
def install(tmp_path, monkeypatch):
    """An isolated install: its own config.yml under a throwaway cwd."""
    import config_manager as cm_module
    from condor.sharing import consent

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(consent.ENV_VAR, raising=False)
    cm_module.ConfigManager.reset_instance()
    yield tmp_path
    cm_module.ConfigManager.reset_instance()


@pytest.fixture
def chat(install):
    """One user with one two-turn conversation."""
    from config_manager import get_config_manager

    get_config_manager()  # materialize config.yml in the tmp cwd
    meta = conversations.new_conversation(USER_ID, surface="web", agent_slug="condor")
    conversations.append_turn(
        USER_ID, meta.id, TurnEntry(role="user", text="check SOL-USDC at 142.35")
    )
    conversations.append_turn(
        USER_ID, meta.id, TurnEntry(role="assistant", text="the book is thin")
    )
    return meta


def _stall_the_loops_read(monkeypatch, read_done, worker_done, timeout=1.0):
    """Hold the main thread inside ``write_status``, right after its read.

    Only the merge read is hooked (``write_status`` resolves ``read_status``
    from this module), and only on the calling thread, so the worker's own
    reads run at full speed. With the fix the worker is blocked on the lock and
    never sets ``worker_done``, so this simply times out — correctness first,
    a second of test time second.
    """
    real_read = registry_file.read_status
    main = threading.current_thread()
    fired = threading.Event()

    def stalling_read(session_dir, filename=registry_file.STATUS_FILENAME):
        data = real_read(session_dir, filename)
        if (
            threading.current_thread() is main
            and filename == conversations.META_FILENAME
            and not fired.is_set()
        ):
            fired.set()
            read_done.set()
            worker_done.wait(timeout)
        return data

    monkeypatch.setattr(registry_file, "read_status", stalling_read)


def _race_against_a_turn(monkeypatch, conv_id, verb):
    """Run ``verb`` in a worker thread inside the loop's read→write window."""
    read_done, worker_done = threading.Event(), threading.Event()
    _stall_the_loops_read(monkeypatch, read_done, worker_done)

    def worker():
        try:
            read_done.wait(5)
            verb()
        finally:
            worker_done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    conversations.append_turn(USER_ID, conv_id, TurnEntry(role="user", text="and now?"))
    thread.join(10)
    assert not thread.is_alive()

    meta = conversations.get_conversation(USER_ID, conv_id)
    assert meta is not None
    return meta


def test_sharing_mid_answer_keeps_both_the_turn_and_the_delete_token(chat, monkeypatch):
    """A share submitted while the conversation is being answered leaves a meta
    with the new turn *and* the receipt. Losing the token would leave a
    transcript on the collector that nothing on this box can revoke."""
    meta = _race_against_a_turn(
        monkeypatch, chat.id, lambda: share.submit(USER_ID, chat.id)
    )

    assert meta.turn_count == 3
    assert meta.share_id
    assert meta.share_delete_token


def test_unsharing_mid_answer_keeps_the_exclusion_flag(chat, monkeypatch):
    """Same window, the revoking half. ``share_excluded`` is what stops the
    sweep re-uploading a conversation the user just took back (CORR-231)."""
    share.submit(USER_ID, chat.id)

    meta = _race_against_a_turn(
        monkeypatch, chat.id, lambda: share.unshare(USER_ID, chat.id)
    )

    assert meta.share_excluded is True
    assert meta.turn_count == 3
    assert not meta.share_id
