"""ST-08: an index that cannot be read is never treated as empty by a write.

Three stores do read-modify-write on one whole file — the reports index, the
routine hooks and the run-history index — and none could tell "missing" from
"failed to read". After one failed read the next write persisted its own entry
alone: every other report became an orphan file, every other owner's hooks were
erased, and run-history entries kept "for ever" were forgotten.
"""

import asyncio
import json

import pytest

import condor.reports as reports
import condor.routine_hooks as hooks
import condor.run_history_store as rhs
from condor import fsutil, paths
from condor.reports import rendering, store
from condor.run_history_store import RunHistoryEntry, RunHistoryStore

# ── fsutil.read_json ──


def test_read_json_defaults_only_for_a_missing_file(tmp_path):
    assert fsutil.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}

    good = tmp_path / "good.json"
    good.write_text('{"a": "é"}', encoding="utf-8")
    assert fsutil.read_json(good, None) == {"a": "é"}


def test_read_json_raises_for_garbage_and_for_a_failed_read(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        fsutil.read_json(bad, [])

    empty = tmp_path / "empty.json"
    empty.write_text("")
    with pytest.raises(ValueError):
        fsutil.read_json(empty, [])

    # A directory where the file should be: the read fails, it is not "missing".
    (tmp_path / "dir.json").mkdir()
    with pytest.raises(OSError):
        fsutil.read_json(tmp_path / "dir.json", [])


# ── Reports ──


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(rendering, "plotly_bundle", lambda: "window.Plotly={};")
    return tmp_path


def _save(title: str) -> str:
    builder = reports.ReportBuilder(title).source("routine", "probe")
    builder.markdown("body")
    return asyncio.run(builder.save())


def test_a_corrupt_report_index_is_kept_aside_and_rebuilt_on_save(reports_dir):
    first, second = _save("First <report>"), _save("Second")
    index_file = store._index_file()
    index_file.write_text('[{"id": "trunca', encoding="utf-8")

    third = _save("Third")

    ids = {entry["id"] for entry in store._read_index()}
    assert ids == {first, second, third}
    assert len(list(index_file.parent.glob(index_file.name + ".corrupt-*"))) == 1
    recovered = reports.get_report(first)
    assert recovered["title"] == "First <report>"
    assert recovered["recovered"] is True
    # The owner lived in the lost index: recovered reports are admin-only.
    assert recovered["user_id"] is None
    assert reports.get_report_raw_html(first) is not None


def test_a_rebuilt_report_index_survives_a_failed_save(reports_dir):
    """The rebuilt index is on disk before the save that triggered it can fail.

    Moving the corrupt index aside and keeping the rebuild in memory left the
    index missing; a save failing right after (disk full) meant the next one
    read "missing" as ``[]`` and listed only itself.
    """
    first, second = _save("First"), _save("Second")
    index_file = store._index_file()
    index_file.write_text('[{"id": "trunca', encoding="utf-8")

    def _disk_full(path, content):
        raise OSError(28, "No space left on device")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store, "_write_report_html", _disk_full)
        with pytest.raises(OSError):
            _save("Doomed")

    assert {entry["id"] for entry in store._read_index()} == {first, second}
    aside = list(index_file.parent.glob(index_file.name + ".corrupt-*"))
    assert len(aside) == 1
    assert aside[0].read_text(encoding="utf-8") == '[{"id": "trunca'

    third = _save("Third")
    assert {entry["id"] for entry in store._read_index()} == {first, second, third}


def test_a_failed_rebuild_write_leaves_the_corrupt_index_to_retry(reports_dir):
    first = _save("First")
    index_file = store._index_file()
    index_file.write_text("{garbage", encoding="utf-8")

    def _disk_full(entries):
        raise OSError(28, "No space left on device")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store, "_write_index", _disk_full)
        with pytest.raises(OSError):
            _save("Doomed")

    # Still corrupt, never missing: the next save recovers again.
    assert index_file.read_text(encoding="utf-8") == "{garbage"
    second = _save("Second")
    assert {entry["id"] for entry in store._read_index()} == {first, second}


def test_a_report_index_that_cannot_be_read_blocks_the_write(reports_dir):
    first = _save("First")
    before = store._index_file().read_text(encoding="utf-8")
    html_before = sorted(p.name for p in reports_dir.glob("*.html"))

    def _emfile(path, default=None):
        raise OSError(24, "Too many open files")

    # Scoped: ``monkeypatch.undo()`` would also revert the reports dir.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store, "read_json", _emfile)

        with pytest.raises(OSError):
            _save("Second")
        with pytest.raises(OSError):
            asyncio.run(reports.delete_report(first))
        # A reader degrades instead of raising.
        assert reports.list_reports() == ([], 0)

    assert store._index_file().read_text(encoding="utf-8") == before
    # Nothing was written that the index does not list.
    assert sorted(p.name for p in reports_dir.glob("*.html")) == html_before


# ── Routine hooks ──


@pytest.fixture
def hooks_file(monkeypatch):
    import config_manager

    class FakeConfigManager:
        def is_admin(self, user_id):
            return False

    monkeypatch.setattr(config_manager, "get_config_manager", FakeConfigManager)
    path = paths.routine_hooks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _hook(chat_id: int) -> dict:
    return {"telegram": {"enabled": True, "chat_ids": [str(chat_id)]}}


def test_garbage_in_the_hooks_file_is_not_overwritten_by_a_save(hooks_file):
    hooks.save_hooks("probe", _hook(111), 111)
    hooks_file.write_text('{"probe": {"111": ', encoding="utf-8")

    with pytest.raises(ValueError):
        hooks.save_hooks("probe", _hook(222), 222)

    assert hooks_file.read_text(encoding="utf-8") == '{"probe": {"111": '
    # Reading still degrades to "no hooks" rather than raising.
    assert hooks.load_hooks("probe", 111) is None


def test_a_missing_hooks_file_is_still_just_empty(hooks_file):
    assert not hooks_file.exists()
    hooks.save_hooks("probe", _hook(111), 111)
    assert set(json.loads(hooks_file.read_text())["probe"]) == {"111"}


# ── Run history ──


@pytest.fixture(autouse=True)
def _no_background_pruning(monkeypatch):
    monkeypatch.setattr(RunHistoryStore, "_maybe_prune", lambda self: None)


def _entry(bot: str) -> RunHistoryEntry:
    return RunHistoryEntry(
        server="s",
        bot_name=bot,
        deployed_at="2026-01-01T00:00:00+00:00",
        stopped_at="2026-01-02T00:00:00+00:00",
    )


def test_a_corrupt_run_history_index_is_kept_aside_before_the_next_put(tmp_path):
    RunHistoryStore(tmp_path).put("old", _entry("old"), {"c": []})
    index = tmp_path / "_index.json"
    index.write_text('{"entries": {"old": ', encoding="utf-8")

    fresh = RunHistoryStore(tmp_path)
    fresh.put("new", _entry("new"), {"c": []})

    aside = list(tmp_path.glob("_index.json.corrupt-*"))
    assert len(aside) == 1
    assert aside[0].read_text(encoding="utf-8") == '{"entries": {"old": '
    assert set(json.loads(index.read_text())["entries"]) == {"new"}


def test_a_run_history_index_that_failed_to_read_is_merged_not_replaced(
    tmp_path, monkeypatch
):
    RunHistoryStore(tmp_path).put("old", _entry("old"), {"c": []})
    index = tmp_path / "_index.json"
    before = index.read_text(encoding="utf-8")
    real_read = rhs.read_json

    def _emfile(path, default=None):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(rhs, "read_json", _emfile)
    blind = RunHistoryStore(tmp_path)
    assert blind.get_entry("old") is None

    # Still unreadable: the put refuses rather than persisting "new" alone.
    with pytest.raises(OSError):
        blind.put("new", _entry("new"), {"c": []})
    assert index.read_text(encoding="utf-8") == before

    # Readable again: the write goes through with both entries.
    monkeypatch.setattr(rhs, "read_json", real_read)
    blind.put("new", _entry("new"), {"c": []})
    assert set(json.loads(index.read_text())["entries"]) == {"old", "new"}
    assert blind.get_entry("old") is not None
