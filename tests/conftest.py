"""I test NON devono mai toccare lo stato reale della demo (state/state.json)."""
import tempfile
from pathlib import Path

import pytest

from app import store


@pytest.fixture(autouse=True, scope="session")
def isolated_state_file():
    old = store._FILE
    store._FILE = Path(tempfile.mkdtemp()) / "state.json"
    store._state = None
    yield
    store._FILE, store._state = old, None
