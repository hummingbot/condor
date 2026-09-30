import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Exercise shipped agent source without writing into the operator's install."""
    from condor import paths

    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "runtime"))
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setenv(paths.AGENTS_ROOT_ENV, str(tmp_path / "agents"))
    monkeypatch.setenv(
        paths.STOCK_AGENTS_ROOT_ENV,
        str(Path(__file__).resolve().parents[2]),
    )
    monkeypatch.setenv(paths.REPORTS_DIR_ENV, str(tmp_path / "reports"))
