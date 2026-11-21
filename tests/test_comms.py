"""Comunicazione interna: canali, messaggi diretti, richieste ferie, notifiche in tempo reale."""
import pytest
from fastapi.testclient import TestClient

from app import comms, llm, store
from app.main import app

_clients: dict[str, TestClient] = {}


def as_(name: str) -> TestClient:
    if name not in _clients:
        c = TestClient(app)
        assert c.post("/api/login", json={"username": name, "password": "nordica2026"}).status_code == 200
        _clients[name] = c
    return _clients[name]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    llm.set_offline(True)
    monkeypatch.setattr(comms, "WATCH_ASYNC", False)
    store.reset()
    yield
    store.reset()


def post(user, conv, text):
    r = as_(user).post(f"/api/conversations/{conv}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def cursor(user):
    return as_(user).get("/api/updates").json()["seq"]


def updates(user, since):
    return as_(user).get("/api/updates", params={"since": since}).json()


DM = "dm:giulia:marco"


# ------------------------------------------------------------------ messaggi diretti
def test_dm_visible_only_to_the_two_participants_not_even_ceo():
    s0 = {u: cursor(u) for u in ("giulia", "marco", "sara")}
    post("marco", DM, "Giulia, ti giro una cosa privata sul budget")
    assert [m["text"] for m in as_("giulia").get(f"/api/conversations/{DM}/messages").json()] == ["Giulia, ti giro una cosa privata sul budget"]
    assert as_("sara").get(f"/api/conversations/{DM}/messages").status_code == 404          # la CEO non legge i privati
    assert as_("sara").post(f"/api/conversations/{DM}/messages", json={"text": "x"}).status_code == 404
    assert DM not in {c["id"] for c in as_("sara").get("/api/conversations").json()}
    assert updates("sara", s0["sara"])["messages"] == [] and updates("sara", s0["sara"])["notifications"] == []
    up = updates("giulia", s0["giulia"])
    assert len(up["messages"]) == 1 and up["notifications"][0]["kind"] == "dm"            # Giulia la riceve in tempo reale


def test_dm_ids_cannot_be_forged():
    for bad in ("dm:marco:sara", "dm:marco:giulia", "dm:giulia:giulia", "dm:giulia:nessuno", "ch:direzione", "ch:tech"):
        assert as_("giulia").get(f"/api/conversations/{bad}/messages").status_code == 404, bad


def test_brain_does_not_read_dms():
    post("marco", DM, "La consegna slitta al 12/12, il budget è di 45.000 €")
    from app import memory
    assert not any("45.000" in f["statement"] for f in memory.all_facts())


# ------------------------------------------------------------------ canali
def test_private_channel_messages_reach_only_members():
    s = cursor("marco")
    post("giulia", "ch:finance", "Promemoria interno finance")
    assert updates("marco", s)["messages"] == []
    assert as_("marco").get("/api/conversations/ch:finance/messages").status_code == 404
    assert any(m["text"] == "Promemoria interno finance" for m in as_("sara").get("/api/conversations/ch:finance/messages").json())


def test_brain_in_channel_answers_only_with_what_everyone_there_can_see():
    out = post("giulia", "ch:general", "@brain qual è il margine su Acme?")
    assert out[1]["author_id"] == "brain" and out[1]["kind"] == "unknown" and "22" not in out[1]["text"]   # in #general c'è anche Tech
    out = post("giulia", "ch:finance", "@brain qual è il margine su Acme?")
    assert out[1]["kind"] == "answer" and "22%" in out[1]["text"] and out[1]["citations"]
    out = post("marco", "ch:general", "@brain quando è la consegna della Release 2?")
    assert out[1]["kind"] == "answer" and out[1]["citations"]                                  # fatti visibili a tutti


def test_brain_watches_channels_and_creates_candidates_with_channel_acl():
    post("marco", "ch:tech", "Il nuovo server di staging sarà pronto il 27/11, costo 1.200 € al mese.")
    from app import memory
    new = [f for f in memory.all_facts() if f["source"]["id"].startswith("S-MSG")]
    assert new and all(f["status"] == "candidate" and f["acl"] == ["tech"] for f in new)
    assert not any(f["id"] == new[0]["id"] for f in as_("giulia").get("/api/facts").json())   # Finance non lo vede
    assert any(f["id"] == new[0]["id"] for f in as_("marco").get("/api/facts").json())


# ------------------------------------------------------------------ richieste ferie
def _ferie(start="2026-12-01", end="2026-12-05", note="Ponte dell'Immacolata"):
    r = as_("marco").post("/api/requests", json={"start": start, "end": end, "note": note})
    assert r.status_code == 200, r.text
    return r.json()


def test_request_flags_policy_and_deadlines_by_itself():
    r = _ferie()
    texts = " ".join(c["text"] for c in r["checks"])
    assert r["status"] == "pending"
    assert "Preavviso di 10 giorni" in texts and "15" in texts                       # policy ferie
    assert "T-101" in texts                                                           # consegna di cui Marco è responsabile
    assert all(c["sources"] for c in r["checks"] if c["severity"] == "high")          # cita le fonti


def test_request_visibility_and_only_ceo_decides():
    s_sara, s_giulia = cursor("sara"), cursor("giulia")
    r = _ferie()
    assert as_("giulia").get("/api/requests").json() == []                            # Giulia non vede le ferie di Marco
    assert as_("marco").post(f"/api/requests/{r['id']}/decide", json={"approve": True}).status_code == 403
    assert as_("giulia").post(f"/api/requests/{r['id']}/decide", json={"approve": True}).status_code == 404
    assert updates("giulia", s_giulia)["notifications"] == []
    note = updates("sara", s_sara)["notifications"]
    assert note and "Marco chiede ferie" in note[0]["text"] and "problemi" in note[0]["text"]   # avviso proattivo alla CEO
    brief = as_("sara").get("/api/brief").json()
    assert any(a["rule"] == "request" for a in brief["alerts"])                        # e nel suo brief


def test_request_approved_goes_to_calendar_and_requester_is_notified():
    s_marco = cursor("marco")
    r = _ferie()
    d = as_("sara").post(f"/api/requests/{r['id']}/decide", json={"approve": True}).json()
    assert d["status"] == "approved" and d["decided_by"] == "Sara Conti"
    assert any(e["title"] == "Ferie Marco" for e in as_("giulia").get("/api/calendar").json())
    assert "approvato" in updates("marco", s_marco)["notifications"][-1]["text"]
    assert any(a["rule"] == "absence" for a in as_("sara").get("/api/brief").json()["alerts"])   # T-101 scade durante le ferie
    assert as_("sara").post(f"/api/requests/{r['id']}/decide", json={"approve": False}).status_code == 409


def test_request_rejected_changes_nothing():
    r = _ferie()
    as_("sara").post(f"/api/requests/{r['id']}/decide", json={"approve": False})
    assert not any(e["title"] == "Ferie Marco" for e in as_("sara").get("/api/calendar").json())


def test_request_validation():
    for s, e in (("2026-12-05", "2026-12-01"), ("2026-11-01", "2026-11-03"), ("2026-12-01", "2027-02-01"), ("domani", "x")):
        assert as_("marco").post("/api/requests", json={"start": s, "end": e}).status_code == 400, (s, e)


def test_request_without_issues():
    r = _ferie("2027-01-11", "2027-01-15")
    assert [c["severity"] for c in r["checks"]] == ["ok"]


# ------------------------------------------------------------------ tempo reale
def test_updates_cursor_and_inbound_email_notifies_routed_role_only():
    s = {u: cursor(u) for u in ("giulia", "marco", "sara")}
    as_("sara").post("/api/inbox/simulate", json={"from_addr": "a@cliente.it", "subject": "Portale giù", "body": "Il portale non funziona, errore sul database."})
    assert any(n["kind"] == "inbox" for n in updates("marco", s["marco"])["notifications"])
    assert any(n["kind"] == "inbox" for n in updates("sara", s["sara"])["notifications"])
    assert not updates("giulia", s["giulia"])["notifications"]
    first = as_("marco").get("/api/updates").json()
    assert first["notifications"] == [] and first["messages"] == []                    # primo aggancio: solo il cursore
