"""ST-09: a pickle that will not load is kept aside before it can be overwritten.

When neither the main file nor its ``.bak`` loads, the bot starts empty — and
the next two flushes used to finish the job: the first rotated the unreadable
main file over ``.bak``, the second replaced that with the near-empty state.
The likeliest cause is not disk damage but a refactor that moved a pickled
class, in which case the bytes are perfectly recoverable — if a copy survives.
"""

from __future__ import annotations

import pickle
import sys
import types

from condor.persistence import SafePicklePersistence


def _pickle_with_a_class_that_no_longer_imports() -> bytes:
    module = types.ModuleType("ist_gone_module")

    class Draft:
        def __init__(self):
            self.pair = "SOL-USDC"

    Draft.__module__ = "ist_gone_module"
    Draft.__qualname__ = "Draft"
    module.Draft = Draft
    sys.modules["ist_gone_module"] = module
    try:
        return pickle.dumps(
            {
                "user_data": {42: {"routine_instances": {"abc": Draft()}}},
                "chat_data": {},
                "bot_data": {},
                "conversations": {},
                "callback_data": None,
            }
        )
    finally:
        del sys.modules["ist_gone_module"]


def _quarantined(directory):
    return sorted(p.name for p in directory.glob("*.unreadable-*"))


def test_an_unloadable_pickle_and_its_backup_survive_the_first_flushes(tmp_path):
    main = tmp_path / "condor_bot_data.pickle"
    bak = tmp_path / "condor_bot_data.pickle.bak"
    payload = _pickle_with_a_class_that_no_longer_imports()
    main.write_bytes(payload)
    bak.write_bytes(payload + b" ")  # distinguishable from the main file

    persistence = SafePicklePersistence(filepath=main)
    persistence._load_singlefile()
    assert persistence.user_data == {}

    # Two flushes: exactly what used to leave nothing recoverable.
    persistence._dump_singlefile()
    persistence._dump_singlefile()

    kept = _quarantined(tmp_path)
    assert len(kept) == 2
    by_source = {name.split(".unreadable-")[0]: name for name in kept}
    assert (tmp_path / by_source[main.name]).read_bytes() == payload
    assert (tmp_path / by_source[bak.name]).read_bytes() == payload + b" "
    # And the live files really were overwritten, which is why the copy matters.
    assert main.read_bytes() != payload
    assert bak.read_bytes() not in (payload, payload + b" ")


def test_a_main_file_with_no_backup_is_quarantined_too(tmp_path):
    main = tmp_path / "condor_bot_data.pickle"
    main.write_bytes(b"not a pickle at all")

    SafePicklePersistence(filepath=main)._load_singlefile()

    assert len(_quarantined(tmp_path)) == 1


def test_nothing_is_quarantined_on_a_first_boot_or_a_clean_load(tmp_path):
    main = tmp_path / "condor_bot_data.pickle"

    fresh = SafePicklePersistence(filepath=main)
    fresh._load_singlefile()
    fresh.user_data = {1: {"k": "v"}}
    fresh._dump_singlefile()

    again = SafePicklePersistence(filepath=main)
    again._load_singlefile()

    assert again.user_data == {1: {"k": "v"}}
    assert _quarantined(tmp_path) == []


def test_recovery_from_the_backup_does_not_quarantine(tmp_path):
    main = tmp_path / "condor_bot_data.pickle"
    good = SafePicklePersistence(filepath=main)
    good._load_singlefile()
    good.user_data = {1: {"k": "v"}}
    good._dump_singlefile()
    good._dump_singlefile()  # now a .bak exists
    main.write_bytes(b"torn write")

    recovered = SafePicklePersistence(filepath=main)
    recovered._load_singlefile()

    assert recovered.user_data == {1: {"k": "v"}}
    assert _quarantined(tmp_path) == []
