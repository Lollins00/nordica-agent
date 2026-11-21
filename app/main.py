"""Company Brain – API FastAPI. Autenticazione solo dimostrativa: l'utente è scelto dal selettore della UI."""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, brief, data, events, inbox, llm, memory, qa, store
from .config import NOW, STATIC_DIR
from .permissions import acl_label, can_see

app = FastAPI(title="Company Brain · Nordica")


@app.on_event("startup")
def _startup():
    inbox.start_background_poll()


def U(uid: str) -> dict:
    u = data.user_by_id(uid)
    if not u:
        raise HTTPException(404, "utente sconosciuto")
    return u


# ------------------------------------------------------------- generale
@app.get("/api/users")
def api_users():
    return data.users()


@app.get("/api/status")
def api_status(user: str):
    u = U(user)
    return {"llm": llm.status(), "imap": inbox.imap_status(), "now": NOW.isoformat(timespec="minutes"),
            "badges": {"candidates": len(memory.facts_for(u["role"], "candidate")),
                       "pending": sum(1 for a in actions.visible_actions(u["role"]) if a["status"] == "pending" and u["role"] in a["approvers"])}}


class OfflineBody(BaseModel):
    offline: bool


@app.post("/api/llm/offline")
def api_offline(b: OfflineBody):
    llm.set_offline(b.offline)
    store.get()["brief_cache"].clear()
    return llm.status()


@app.post("/api/reset")
def api_reset():
    store.reset()
    return {"ok": True}


# ---------------------------------------------------------------- brief
@app.get("/api/brief")
def api_brief(user: str):
    return brief.build_brief(U(user))


@app.post("/api/brief/refresh")
def api_brief_refresh(user: str):
    store.get()["brief_cache"].clear()
    return brief.build_brief(U(user))


# ----------------------------------------------------------------- chat
class AskBody(BaseModel):
    user: str
    question: str


@app.post("/api/ask")
def api_ask(b: AskBody):
    u = U(b.user)
    q = b.question.strip()
    if not q:
        raise HTTPException(400, "domanda vuota")
    proposal = actions.propose_from_text(u["role"], u["name"], q, via="chat")
    if proposal:
        return {**proposal, "citations": [], "writer": "regole"}
    return qa.answer(u["role"], q)


# --------------------------------------------------------------- memoria
@app.get("/api/facts")
def api_facts(user: str):
    u = U(user)
    return memory.facts_for(u["role"])


class StatusBody(BaseModel):
    user: str
    status: str


@app.post("/api/facts/{fid}/status")
def api_fact_status(fid: str, b: StatusBody):
    u = U(b.user)
    try:
        return memory.set_status(fid, b.status, u["role"], u["name"])
    except LookupError:
        raise HTTPException(404, "fatto non trovato")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- azioni
@app.get("/api/actions")
def api_actions(user: str):
    u = U(user)
    return [{**a, "can_decide": u["role"] in a["approvers"]} for a in actions.visible_actions(u["role"])]


class DecideBody(BaseModel):
    user: str
    approve: bool


@app.post("/api/actions/{aid}/decide")
def api_decide(aid: str, b: DecideBody):
    u = U(b.user)
    try:
        return actions.decide(aid, u["role"], u["name"], b.approve)
    except LookupError:
        raise HTTPException(404, "azione non trovata")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


class ProposeBody(BaseModel):
    user: str
    type: str
    args: dict


@app.post("/api/actions/propose")
def api_propose(b: ProposeBody):
    u = U(b.user)
    try:
        if b.type == "change_deadline":
            return actions.propose_deadline_change(u["role"], u["name"], b.args["task_id"], b.args["new_due"], via="brief")
        if b.type == "reply_email":
            return actions.propose_reply_to_email(u["role"], u["name"], b.args["email_id"], via="brief")
    except LookupError:
        raise HTTPException(404, "elemento non trovato")
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, "tipo di azione non supportato")


# ----------------------------------------------------------- Slack mock
@app.get("/api/channels")
def api_channels(user: str):
    u = U(user)
    return [c for c in data.channels() if can_see(u["role"], c["acl"])]


@app.get("/api/channels/{cid}")
def api_channel(cid: str, user: str):
    u = U(user)
    ch = next((c for c in data.channels() if c["id"] == cid and can_see(u["role"], c["acl"])), None)
    if not ch:
        raise HTTPException(404, "canale non trovato")
    msgs = [s for s in data.all_sources().values() if s["type"] == "slack" and s["meta"]["channel"] == cid]
    return {"channel": ch, "messages": sorted([{"id": m["id"], "author": m["meta"]["author"], "ts": m["date"], "text": m["text"]} for m in msgs], key=lambda m: m["ts"])}


@app.get("/api/source/{sid}")
def api_source(sid: str, user: str):
    u = U(user)
    s = data.all_sources().get(sid)
    if not s or not can_see(u["role"], s["acl"]):
        raise HTTPException(404, "fonte non trovata")      # identico se non esiste o non è visibile
    return {**data.source_view(s), "visibility": acl_label(s["acl"])}


# ------------------------------------------------------------ calendario
@app.get("/api/calendar")
def api_calendar(user: str):
    return events.visible_events(U(user)["role"])


class EventBody(BaseModel):
    user: str
    title: str
    start: str
    end: str | None = None
    private: bool = False


@app.post("/api/calendar")
def api_add_event(b: EventBody):
    u = U(b.user)
    try:
        return events.add_event(u["role"], u["name"], b.title, b.start, b.end, b.private)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------- inbox dell'agente
@app.get("/api/inbox")
def api_inbox(user: str):
    return inbox.visible_messages(U(user))


class InboundBody(BaseModel):
    subject: str
    body: str


@app.post("/api/inbox/simulate")
def api_simulate(b: InboundBody):
    return inbox.ingest(b.subject, b.body, via="simulatore")


@app.post("/api/inbox/poll")
def api_poll():
    if not inbox.imap_configured():
        raise HTTPException(409, "IMAP non configurato (IMAP_USER / IMAP_PASSWORD nel .env)")
    try:
        return {"processed": inbox.imap_poll_once()}
    except Exception as e:
        raise HTTPException(502, f"IMAP non raggiungibile: {type(e).__name__}")


# ----------------------------------------------------------------- statico
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
