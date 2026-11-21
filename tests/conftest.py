"""I test NON devono mai toccare lo stato reale della demo né inviare email vere."""
import tempfile
from pathlib import Path

import pytest

from app import mailer, store

SENT: list = []          # email "inviate" dai test (intercettate: nessun SMTP reale)


@pytest.fixture(autouse=True, scope="session")
def isolated_state_file():
    old = store._FILE
    store._FILE = Path(tempfile.mkdtemp()) / "state.json"
    store._state = None
    yield
    store._FILE, store._state = old, None


@pytest.fixture(autouse=True)
def fake_mail(monkeypatch):
    monkeypatch.setenv("IMAP_USER", "agent@test.invalid")
    monkeypatch.setenv("IMAP_PASSWORD", "x")
    monkeypatch.setenv("MAIL_REDIRECT_TO", "demo-owner@test.invalid")
    monkeypatch.delenv("MAIL_MODE", raising=False)
    monkeypatch.setenv("DEMO_NOW", "2026-11-21T10:00:00")   # sabato 21/11: test ripetibili in qualunque giorno
    SENT.clear()
    mailer.TEST_SENT = SENT
    mailer._SENT.clear()
    monkeypatch.setattr(mailer, "_deliver", lambda msg: SENT.append(msg))
    yield
