"""Test dei 7 comandamenti. Eseguiti in modalità offline (fallback deterministico) per essere ripetibili."""
import pytest
from fastapi.testclient import TestClient

from app import llm, store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh():
    llm.set_offline(True)
    store.reset()
    yield
    store.reset()


def ask(user, q):
    r = client.post("/api/ask", json={"user": user, "question": q})
    assert r.status_code == 200, r.text
    return r.json()


# 1 ------------------------------------------------------------------ brief da regole
def test_1_brief_is_rule_based_and_covers_all_rule_types():
    b = client.get("/api/brief", params={"user": "sara"}).json()
    rules = {a["rule"] for a in b["alerts"]}
    assert {"deadline", "unanswered", "conflict"} <= rules
    assert b["writer"] == "regole"                      # senza Gemini il brief esiste comunque
    assert all(a["text"] for a in b["alerts"])


def test_1_brief_alerts_decided_by_rules_not_llm(monkeypatch):
    """Anche se l'LLM prova a inventare avvisi o numeri, gli avvisi restano quelli delle regole."""
    llm.set_offline(False)
    monkeypatch.setattr(llm, "api_key", lambda: "x")
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: {"intro": "Ciao 999", "items": {"inventato": "x", "conflict:acme.delivery_date": "Consegna il 31/12 (inventato)"}})
    b = client.get("/api/brief", params={"user": "marco"}).json()
    ids = {a["id"] for a in b["alerts"]}
    assert "inventato" not in ids
    conflict = next(a for a in b["alerts"] if a["id"] == "conflict:acme.delivery_date")
    assert "31" not in conflict["text"]                  # numero non presente nei dati: scartato


# 2 ------------------------------------------------------------------ memoria curata
def test_2_only_approved_facts_answer():
    assert ask("giulia", "Qual è la penale per il ritardo?")["kind"] == "unknown"     # candidato
    client.post("/api/facts/F-ECON-PENALTY/status", json={"user": "giulia", "status": "approved"})
    r = ask("giulia", "Qual è la penale per il ritardo?")
    assert r["kind"] == "answer" and "1,5%" in r["text"]
    client.post("/api/facts/F-ECON-PENALTY/status", json={"user": "giulia", "status": "rejected"})
    assert ask("giulia", "Qual è la penale per il ritardo?")["kind"] == "unknown"


def test_2_cannot_curate_facts_you_cannot_see():
    r = client.post("/api/facts/F-ECON-MARGIN/status", json={"user": "marco", "status": "rejected"})
    assert r.status_code == 404
    assert ask("giulia", "Qual è il margine su Acme?")["kind"] == "answer"


# 3 ------------------------------------------------------------------ permessi prima dell'LLM
def test_3_same_question_finance_answers_tech_does_not():
    f, t = ask("giulia", "Qual è il margine su Acme?"), ask("marco", "Qual è il margine su Acme?")
    assert f["kind"] == "answer" and "22%" in f["text"]
    assert t["kind"] == "unknown" and "22" not in t["text"]
    f2, t2 = ask("giulia", "Qual è la causa dell'incidente del 12/11?"), ask("marco", "Qual è la causa dell'incidente del 12/11?")
    assert f2["kind"] == "unknown" and t2["kind"] == "answer"


def test_3_llm_never_sees_forbidden_facts(monkeypatch):
    seen = []
    llm.set_offline(False)
    monkeypatch.setattr(llm, "api_key", lambda: "x")
    monkeypatch.setattr(llm, "generate_json", lambda prompt, system=None: seen.append(prompt) or None)
    ask("marco", "Qual è il margine su Acme? E il valore del contratto?")
    ask("marco", "Quando è la consegna della Release 2?")
    joined = " ".join(seen)
    assert "22%" not in joined and "140.400" not in joined and "180.000" not in joined


def test_3_unknown_sender_has_no_permissions_and_ceo_sees_all():
    from app import inbox
    m = inbox.ingest("Qual è il margine su Acme?", "Ciao, mi serve il margine.")
    assert m["outcome"]["type"] == "unknown_sender"
    assert ask("sara", "Qual è il margine su Acme?")["kind"] == "answer"
    assert ask("sara", "Qual è la causa dell'incidente del 12/11?")["kind"] == "answer"


def test_3_source_endpoint_hides_forbidden_sources():
    assert client.get("/api/source/D-02", params={"user": "marco"}).status_code == 404
    assert client.get("/api/source/D-02", params={"user": "giulia"}).status_code == 200
    assert client.get("/api/channels/finance", params={"user": "marco"}).status_code == 404


def test_3_brief_is_role_filtered():
    marco = client.get("/api/brief", params={"user": "marco"}).json()
    assert not any("pagamento" in a["title"].lower() for a in marco["alerts"])


# 4 ------------------------------------------------------------------ fonti citate
def test_4_every_answer_cites_sources_and_only_provided_ones():
    r = ask("giulia", "Qual è il margine su Acme?")
    assert r["citations"] and r["citations"][0]["source"]["id"] == "D-02"
    assert all(c["fact_id"] in r["debug"]["matched"] for c in r["citations"])


def test_4_llm_citations_not_provided_are_rejected(monkeypatch):
    llm.set_offline(False)
    monkeypatch.setattr(llm, "api_key", lambda: "x")
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: {"answer": "Il margine è 99% [F-INVENTATO]", "used": ["F-INVENTATO"]})
    r = ask("giulia", "Qual è il margine su Acme?")
    assert r["writer"] == "fallback" and "99" not in r["text"] and r["citations"][0]["fact_id"] == "F-ECON-MARGIN"


def test_4_llm_invented_numbers_are_rejected(monkeypatch):
    llm.set_offline(False)
    monkeypatch.setattr(llm, "api_key", lambda: "x")
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: {"answer": "Il margine è del 35% [F-ECON-MARGIN]", "used": ["F-ECON-MARGIN"]})
    r = ask("giulia", "Qual è il margine su Acme?")
    assert r["writer"] == "fallback" and "35" not in r["text"]


def test_4_conflicting_sources_are_reported():
    r = ask("sara", "Quando è la consegna della Release 2?")
    assert r["conflict"] and len(r["citations"]) >= 2


# 5 ------------------------------------------------------------------ non lo so
def test_5_unknown_when_no_data_and_llm_not_called(monkeypatch):
    llm.set_offline(False)
    monkeypatch.setattr(llm, "api_key", lambda: "x")
    calls = []
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: calls.append(1))
    for q in ["Quanto costa la mensa aziendale?", "Che tempo fa domani a Milano?", "Chi ha vinto il campionato?"]:
        r = ask("sara", q)
        assert r["kind"] == "unknown" and r["text"].startswith("Non lo so") and not r["citations"]
    assert calls == []


# 6 ------------------------------------------------------------------ azioni in attesa
def test_6_actions_pending_until_approved():
    r = ask("marco", "Sposta la scadenza della consegna Release 2 al 30/11")
    assert r["kind"] == "action" and r["action"]["status"] == "pending"
    tasks = {t["id"]: t for t in __import__("app.data", fromlist=["x"]).tasks()}
    assert tasks["T-101"]["due"] == "2026-12-05"                       # ancora invariata
    d = client.post(f"/api/actions/{r['action']['id']}/decide", json={"user": "marco", "approve": True}).json()
    assert d["status"] == "approved"
    tasks = {t["id"]: t for t in __import__("app.data", fromlist=["x"]).tasks()}
    assert tasks["T-101"]["due"] == "2026-11-30"


def test_6_reject_has_no_effect_and_role_gate():
    r = ask("giulia", "Sposta la scadenza della consegna Release 2 al 30/11")
    assert r["kind"] == "action"
    assert client.post(f"/api/actions/{r['action']['id']}/decide", json={"user": "giulia", "approve": True}).status_code == 403
    d = client.post(f"/api/actions/{r['action']['id']}/decide", json={"user": "sara", "approve": False}).json()
    assert d["status"] == "rejected"
    from app import data
    assert {t["id"]: t for t in data.tasks()}["T-101"]["due"] == "2026-12-05"


def test_6_draft_email_pending_and_no_internal_facts_leak():
    r = ask("giulia", "Scrivi una bozza di email ad Acme per rispondere sui termini di pagamento")
    assert r["kind"] == "action" and r["action"]["type"] == "draft_email" and r["action"]["status"] == "pending"
    body = r["action"]["payload"]["body"]
    assert "22%" not in body and "140.400" not in body        # mai fatti riservati in una mail a un esterno
    assert client.get("/api/actions", params={"user": "marco"}).json() == []   # Marco non vede la bozza di Finance


def test_6_event_from_chat_is_pending():
    r = ask("marco", "Fissa una call con Acme giovedì alle 15")
    assert r["kind"] == "action" and r["action"]["type"] == "create_event" and r["action"]["status"] == "pending"
    assert not any(e["title"].lower().startswith("call con acme") for e in client.get("/api/calendar", params={"user": "marco"}).json())


def test_6_brief_action_button_creates_pending_only():
    b = client.get("/api/brief", params={"user": "marco"}).json()
    act = next(a["action"] for a in b["alerts"] if a["action"] and a["action"]["type"] == "change_deadline")
    r = client.post("/api/actions/propose", json={"user": "marco", "type": act["type"], "args": act["args"]}).json()
    assert r["status"] == "pending"


# 7 ------------------------------------------------------------------ interfaccia
def test_7_ui_serves_chat_email_and_slack_views():
    html = client.get("/").text
    for marker in ('id="view-chat"', 'id="view-email"', 'id="view-slack"'):
        assert marker in html
    assert client.get("/api/channels", params={"user": "giulia"}).json()


# extra ---------------------------------------------------------- inbox email
def test_inbox_email_question_action_and_learning():
    from app import inbox
    q = inbox.ingest("[Giulia] Margine Acme", "Qual è il margine su Acme?")
    assert q["outcome"]["type"] == "answer" and q["outcome"]["citations"]
    q2 = inbox.ingest("[Marco] Margine Acme", "Qual è il margine su Acme?")
    assert q2["outcome"]["type"] == "unknown"
    a = inbox.ingest("[Marco] Spostiamo", "Per favore sposta la scadenza della consegna Release 2 al 30/11")
    assert a["outcome"]["type"] == "action"
    n = inbox.ingest("[Sara] Aggiornamento", "Il budget marketing del primo trimestre è di 12.000 € approvato oggi.")
    assert n["outcome"]["type"] == "learned" and n["outcome"]["fact_ids"]
    assert ask("sara", "Qual è il budget marketing?")["kind"] == "unknown"           # candidato, non approvato
    client.post(f"/api/facts/{n['outcome']['fact_ids'][0]}/status", json={"user": "sara", "status": "approved"})
    assert ask("sara", "Qual è il budget marketing?")["kind"] == "answer"


def test_calendar_add_event_from_ui_and_privacy():
    r = client.post("/api/calendar", json={"user": "giulia", "title": "Call banca", "start": "2026-11-26T10:00", "private": True})
    assert r.status_code == 200
    assert any(e["title"] == "Call banca" for e in client.get("/api/calendar", params={"user": "giulia"}).json())
    assert not any(e["title"] == "Call banca" for e in client.get("/api/calendar", params={"user": "marco"}).json())
