"""Client Gemini: failover tra modelli, pausa su quota (429), cache. Nessuna chiamata reale."""
import pytest

from app import llm


class R:
    def __init__(self, code, text="ok", retry=None):
        self.status_code = code
        self._j = ({"candidates": [{"content": {"parts": [{"text": text}]}}]} if code == 200 else
                   {"error": {"details": [{"@type": "RetryInfo", "retryDelay": retry}] if retry else []}})

    def json(self):
        return self._j


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(llm, "api_key", lambda: "x")
    llm.set_offline(False)
    llm._cooldown.clear(); llm._cache.clear()
    yield
    llm._cooldown.clear(); llm._cache.clear()


def test_quota_on_first_model_fails_over_and_pauses_it(monkeypatch):
    calls = []
    def post(model, body, timeout):
        calls.append(model)
        return R(429, retry="52s") if model == llm.models()[0] else R(200, "ciao")
    monkeypatch.setattr(llm, "_post", post)
    assert llm.generate("a") == "ciao"
    assert calls == llm.models()[:2]                        # uno alla volta, non in parallelo
    assert llm._cooldown[llm.models()[0]] > __import__("time").time() + 40
    calls.clear()
    assert llm.generate("b") == "ciao" and calls == [llm.models()[1]]     # il modello in pausa viene saltato


def test_identical_requests_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "_post", lambda m, b, timeout: calls.append(m) or R(200, "x"))
    assert llm.generate("stessa domanda") == "x" and llm.generate("stessa domanda") == "x"
    assert len(calls) == 1


def test_all_models_on_quota_returns_none_fast(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda m, b, timeout: R(429, retry="60s"))
    assert llm.generate("q") is None
    assert "quota" in llm.status()["last_error"]
    monkeypatch.setattr(llm, "_post", lambda *a, **k: pytest.fail("non deve chiamare modelli in pausa"))
    assert llm.generate("altra") is None
