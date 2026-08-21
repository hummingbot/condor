from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


@pytest.fixture
def agent_root() -> Path:
    return Path(__file__).resolve().parents[1]
