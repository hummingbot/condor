"""The code-run store keeps every snippet whole, and bounded (FEAT-047).

Two properties matter here and nothing else does. A run must come back
*complete* — a traceback the model has to read to fix its snippet is useless
clipped — and the pile must stay bounded, because a snippet that printed a
credential is on disk until the cap evicts it.
"""

import json

import pytest

from condor.code_runs import CodeRun, CodeRunStore, new_run_id


@pytest.fixture
def store(tmp_path):
    return CodeRunStore(tmp_path / "code_runs")


def _run(
    store_id: str, *, agent: str = "condor", created: float = 1.0, **kw
) -> CodeRun:
    return CodeRun(id=store_id, created=created, agent=agent, **kw)


def test_round_trip_keeps_the_whole_record(store):
    run = _run(
        "code_1_aaaa",
        label="returns of SOL",
        server="prod",
        status="error",
        duration_ms=42,
        code="raise ValueError('boom')",
        stdout="x" * 9000,
        result="",
        error="ValueError: boom",
        traceback='File "<code_run>", line 1',
        report_id="rep_1",
        session_key="web:7:slot-1",
    )
    store.save(run)

    got = store.get("code_1_aaaa")
    assert got == run.to_dict()
    assert len(got["stdout"]) == 9000, "the store never clips — the caller does"


def test_get_is_none_for_an_unknown_run(store):
    assert store.get("code_nope") is None


def test_list_is_newest_first_and_index_only(store, tmp_path):
    for i in range(3):
        store.save(_run(f"code_{i}_a", created=float(i), label=f"run {i}", code="x=1"))

    listed = store.list()
    assert [e["id"] for e in listed] == ["code_2_a", "code_1_a", "code_0_a"]
    assert "code" not in listed[0], "history carries meta, not bodies"
    assert listed[0]["label"] == "run 2"


def test_list_filters_by_agent_and_honors_limit(store):
    store.save(_run("code_1_a", agent="condor"))
    store.save(_run("code_2_a", agent="quant"))
    store.save(_run("code_3_a", agent="quant"))

    assert [e["id"] for e in store.list(agent="quant")] == ["code_3_a", "code_2_a"]
    assert [e["id"] for e in store.list(agent="quant", limit=1)] == ["code_3_a"]
    assert len(store.list()) == 3


def test_cap_evicts_the_oldest_file_and_its_index_entry(store, tmp_path, monkeypatch):
    monkeypatch.setattr("condor.code_runs.MAX_RUNS", 3)
    for i in range(5):
        store.save(_run(f"code_{i}_a", created=float(i)))

    assert [e["id"] for e in store.list(limit=99)] == [
        "code_4_a",
        "code_3_a",
        "code_2_a",
    ]
    assert store.get("code_0_a") is None
    assert not (tmp_path / "code_runs" / "code_0_a.json").exists()
    assert (tmp_path / "code_runs" / "code_4_a.json").exists()


def test_index_survives_a_new_store_over_the_same_dir(tmp_path):
    first = CodeRunStore(tmp_path / "code_runs")
    first.save(_run("code_1_a", label="kept"))

    second = CodeRunStore(tmp_path / "code_runs")
    assert [e["label"] for e in second.list()] == ["kept"]
    assert second.get("code_1_a")["label"] == "kept"


def test_a_corrupt_index_starts_empty_instead_of_raising(tmp_path):
    path = tmp_path / "code_runs"
    path.mkdir()
    (path / "_index.json").write_text("{not json")

    assert CodeRunStore(path).list() == []


def test_a_run_id_can_never_escape_the_store_dir(store, tmp_path):
    """Ids arrive from a tool argument, so the shape check is load-bearing."""
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"token": "hunter2"}))

    assert store.get("../secret") is None
    assert store.get("/etc/passwd") is None
    assert store.get("") is None


def test_new_run_id_is_unique_and_well_formed():
    ids = {new_run_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("code_") and i.count("_") == 2 for i in ids)


def test_new_run_id_suffix_carries_at_least_64_bits():
    """The store is keyed by the id, so a same-millisecond collision overwrites."""
    for run_id in (new_run_id() for _ in range(10)):
        suffix = run_id.rsplit("_", 1)[1]
        assert len(suffix) >= 16, run_id
        assert int(suffix, 16) >= 0
