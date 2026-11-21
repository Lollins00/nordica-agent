"""Prova DAL VIVO del percorso Gemini (nessuna risposta simulata). Eseguire con:  pytest -m live -q -s"""
import pytest

from app import actions, brief, data, llm, qa, store

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def live():
    if not llm.api_key():
        pytest.skip("GEMINI_API_KEY mancante")
    llm.set_offline(False)
    store.reset()
    yield
    store.reset()


def test_live_answer_is_written_by_gemini_and_cites_only_given_facts():
    r = qa.answer("finance", "Qual è il margine su Acme?")
    assert r["writer"] == "gemini", llm.status()["last_error"]
    assert "22" in r["text"] and r["citations"][0]["fact_id"] == "F-ECON-MARGIN"


def test_live_permissions_hold_with_real_model():
    r = qa.answer("tech", "Qual è il margine su Acme?")
    assert r["kind"] == "unknown" and "22" not in r["text"]


def test_live_brief_written_by_gemini_alerts_from_rules():
    b = brief.build_brief(data.users()[1])
    assert b["writer"] == "gemini", llm.status()["last_error"]
    assert {a["id"] for a in b["alerts"]} == {a["id"] for a in brief.compute_alerts("tech")}


def test_live_draft_has_no_confidential_numbers():
    p = actions.propose_from_text("finance", "Giulia", "Scrivi una bozza di email ad Acme per la richiesta di demo")
    body = p["action"]["payload"]["body"]
    assert p["action"]["status"] == "pending" and "22%" not in body and "140.400" not in body
