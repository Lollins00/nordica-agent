"""Comunicazione interna: canali, messaggi diretti, richieste (ferie) e notifiche in tempo reale.

Regole di visibilità (tutte applicate in codice):
  - canale: lo vede chi ha accesso al canale (stesse ACL dei dati);
  - messaggio diretto: SOLO i due partecipanti. Né la CEO né il Brain lo leggono;
  - richiesta ferie: chi l'ha fatta e la CEO, che è l'unica a poterla approvare;
  - notifica: solo le persone a cui è indirizzata.
Il Brain "guarda" i CANALI (non i messaggi diretti): dai messaggi utili estrae fatti candidati, e se citato
con @brain risponde nel canale usando solo fatti che TUTTI i membri del canale possono vedere.
Sulle richieste ferie segnala da solo i problemi (preavviso, scadenze di cui la persona è responsabile),
ma non decide: decide la CEO.
"""
import threading
from datetime import date, datetime, timedelta

from . import data, memory, qa, store
from .config import TODAY
from .permissions import ROLES, can_see
from .util import fmt_date, fmt_short, to_date

POLICY_NOTICE_DAYS = 15
WATCH_ASYNC = True            # i test lo mettono a False per rendere l'estrazione deterministica


def stamp() -> str:
    """Data della demo + ora reale: i messaggi live restano coerenti con il «lunedì 23/11» della demo."""
    return f"{TODAY.isoformat()}T{datetime.now().strftime('%H:%M:%S')}"


def bump() -> int:
    st = store.get()
    st["seq"] = st.get("seq", 0) + 1
    return st["seq"]


def notify(user_ids, text: str, kind: str, view: str | None = None, link: str | None = None) -> None:
    st = store.get()
    st["notifications"].append({"id": store.next_id("N"), "seq": bump(), "to": sorted(set(user_ids)), "text": text,
                                "kind": kind, "view": view, "link": link, "at": stamp()})
    del st["notifications"][:-300]


def users_with_role(*roles: str) -> list[str]:
    return [u["id"] for u in data.users() if u["role"] in roles]


# ------------------------------------------------------------------ conversazioni
def dm_id(a: str, b: str) -> str:
    return "dm:" + ":".join(sorted([a, b]))


def audience_roles(channel: dict) -> tuple[str, ...]:
    acl = channel["acl"]
    return ROLES if acl == "all" else tuple(sorted(set(acl) | {"ceo"}))


def _conv(user: dict, cid: str) -> dict | None:
    if cid.startswith("ch:"):
        ch = next((c for c in data.channels() if c["id"] == cid[3:]), None)
        return {"kind": "channel", "channel": ch} if ch and can_see(user["role"], ch["acl"]) else None
    if cid.startswith("dm:"):
        parts = cid.split(":")[1:]
        if len(parts) == 2 and parts[0] != parts[1] and user["id"] in parts and all(data.user_by_id(p) for p in parts) \
                and cid == dm_id(*parts):
            return {"kind": "dm", "participants": parts}
    return None


def conversations(user: dict) -> list[dict]:
    out = [{"id": f"ch:{c['id']}", "kind": "channel", "name": c["name"], "topic": c["topic"], "private": bool(c.get("private"))}
           for c in data.channels() if can_see(user["role"], c["acl"])]
    for u in data.users():
        if u["id"] != user["id"]:
            out.append({"id": dm_id(user["id"], u["id"]), "kind": "dm", "name": u["name"], "with": u["id"],
                        "title": u["title"], "color": u["color"]})
    return out


def _seed(channel_id: str) -> list[dict]:
    return [{"id": m["id"], "conv": f"ch:{channel_id}", "author_id": m.get("author_id"), "author": m["author"],
             "ts": m["ts"], "text": m["text"], "seq": 0} for m in data._chats()["messages"] if m["channel"] == channel_id]


def messages(user: dict, cid: str) -> list[dict] | None:
    conv = _conv(user, cid)
    if not conv:
        return None
    base = _seed(conv["channel"]["id"]) if conv["kind"] == "channel" else []
    live = [m for m in store.get()["messages"] if m["conv"] == cid]
    return sorted(base + live, key=lambda m: (m["ts"], m["seq"]))


def post(user: dict, cid: str, text: str) -> list[dict]:
    conv = _conv(user, cid)
    if not conv:
        raise LookupError("conversazione non trovata")
    text = (text or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("messaggio vuoto o troppo lungo (max 2000 caratteri)")
    st = store.get()
    msg = {"id": store.next_id("MSG"), "conv": cid, "author_id": user["id"], "author": user["name"], "ts": stamp(),
           "text": text, "seq": bump()}
    st["messages"].append(msg)
    out = [msg]
    if conv["kind"] == "dm":
        other = next(p for p in conv["participants"] if p != user["id"])
        notify([other], f"💬 {user['first']}: {text[:90]}", "dm", view="conv", link=cid)
    else:
        ch = conv["channel"]
        if "@brain" in text.lower():
            q = text.replace("@brain", "").replace("@Brain", "").strip(" ,:")
            r = qa.answer(user["role"], q, audience=audience_roles(ch))
            reply = {"id": store.next_id("MSG"), "conv": cid, "author_id": "brain", "author": "Company Brain", "ts": stamp(),
                     "text": r["text"], "kind": r["kind"], "citations": r["citations"], "writer": r["writer"], "seq": bump()}
            st["messages"].append(reply)
            out.append(reply)
        elif memory._SIGNAL.search(text):
            if WATCH_ASYNC:
                threading.Thread(target=_watch, args=(msg, ch), daemon=True).start()
            else:
                _watch(msg, ch)
    store.save()
    return out


def _watch(msg: dict, ch: dict) -> None:
    """Il Brain legge il messaggio del canale e ne ricava fatti CANDIDATI (da approvare), con le ACL del canale."""
    src = {"id": f"S-{msg['id']}", "type": "slack", "title": f"#{ch['name']} · {msg['author']}", "date": msg["ts"],
           "acl": ch["acl"], "text": msg["text"], "meta": {"channel": ch["id"], "author": msg["author"], "live": True}, "claims": []}
    store.get()["extra_sources"].append(src)
    memory.extract_candidates(src, origin=f"estratto dal canale #{ch['name']}")
    bump()
    store.save()


# ---------------------------------------------------------------------- richieste
def _parse(d: str) -> date:
    v = to_date(d)
    if not v:
        raise ValueError("data non valida")
    return v


def create_request(user: dict, start: str, end: str, note: str = "") -> dict:
    s, e = _parse(start), _parse(end)
    if e < s:
        raise ValueError("la data di fine è precedente a quella di inizio")
    if s < TODAY:
        raise ValueError("non si possono chiedere ferie nel passato")
    if (e - s).days > 30:
        raise ValueError("periodo troppo lungo (max 30 giorni)")
    st = store.get()
    req = {"id": store.next_id("R"), "type": "ferie", "user_id": user["id"], "user_name": user["name"], "first": user["first"],
           "role": user["role"], "start": s.isoformat(), "end": e.isoformat(), "note": (note or "").strip()[:500],
           "status": "pending", "created_at": stamp(), "decided_by": None, "decided_at": None, "seq": bump()}
    st["requests"].insert(0, req)
    view = request_view(req)
    issues = [c for c in view["checks"] if c["severity"] == "high"]
    head = f"🏖️ {user['first']} chiede ferie dal {fmt_short(s)} al {fmt_short(e)}"
    notify(users_with_role("ceo"), head + (f": ⚠️ {len(issues)} problemi rilevati dal Brain" if issues else ": nessun conflitto rilevato"),
           "request", view="requests")
    st["brief_cache"].clear()
    store.save()
    return view


def checks(req: dict) -> list[dict]:
    """Controlli DETERMINISTICI del Brain su una richiesta ferie (sempre ricalcolati: seguono i dati attuali)."""
    s, e = to_date(req["start"]), to_date(req["end"])
    out = []
    srcs = data.all_sources()
    notice = (s - TODAY).days
    if notice < POLICY_NOTICE_DAYS:
        out.append({"severity": "high", "text": f"Preavviso di {notice} giorni: la policy chiede almeno {POLICY_NOTICE_DAYS} giorni.",
                    "sources": [data.source_ref(srcs["D-04"])] if "D-04" in srcs else []})
    for t in data.tasks():
        due = to_date(t["due"])
        if t["owner"] != req["user_id"] or t["status"] == "done" or not (s - timedelta(days=2) <= due <= e):
            continue
        when = "durante il" if s <= due <= e else "a ridosso del"
        out.append({"severity": "high", "text": f"«{t['title']}» ({t['id']}) scade il {fmt_short(due)}, {when} periodo richiesto: "
                                                f"{req['first']} ne è responsabile.",
                    "sources": [data.source_ref(srcs[t["id"]])]})
    if not out:
        out.append({"severity": "ok", "text": "Nessun conflitto con la policy o con scadenze di cui è responsabile.", "sources": []})
    return out


def request_view(req: dict) -> dict:
    return {**req, "checks": checks(req), "period": f"{fmt_date(req['start'])} – {fmt_date(req['end'])}"}


def visible_requests(user: dict) -> list[dict]:
    reqs = store.get()["requests"]
    mine = reqs if user["role"] == "ceo" else [r for r in reqs if r["user_id"] == user["id"]]
    return [request_view(r) for r in mine]


def decide_request(user: dict, rid: str, approve: bool) -> dict:
    st = store.get()
    req = next((r for r in st["requests"] if r["id"] == rid), None)
    if not req or (user["role"] != "ceo" and req["user_id"] != user["id"]):
        raise LookupError("richiesta non trovata")
    if user["role"] != "ceo":
        raise PermissionError("solo la CEO può approvare le ferie")
    if req["status"] != "pending":
        raise ValueError("richiesta già decisa")
    req.update(status="approved" if approve else "rejected", decided_by=user["name"], decided_at=stamp(), seq=bump())
    if approve:
        st["events"].append({"id": store.next_id("EV"), "title": f"Ferie {req['first']}", "start": f"{req['start']}T00:00",
                             "end": f"{req['end']}T23:59", "owner": req["user_id"], "acl": "all", "type": "ferie",
                             "person": req["user_id"], "person_name": req["first"], "created_by": user["name"], "via": req["id"]})
    esito = "✅ approvato" if approve else "❌ rifiutato"
    notify([req["user_id"]], f"🏖️ {user['first']} ha {esito} le tue ferie dal {fmt_short(req['start'])} al {fmt_short(req['end'])}",
           "request", view="requests")
    st["brief_cache"].clear()
    store.save()
    return request_view(req)


# ------------------------------------------------------------------ tempo reale
def updates(user: dict, since: int | None) -> dict:
    st = store.get()
    seq = st.get("seq", 0)
    if since is None or since > seq:            # primo aggancio (o stato ripristinato): solo il cursore
        return {"seq": seq, "notifications": [], "messages": [], "requests_changed": False}
    notes = [n for n in st["notifications"] if n["seq"] > since and user["id"] in n["to"]]
    msgs = [m for m in st["messages"] if m["seq"] > since and _conv(user, m["conv"])]
    reqs = any(r["seq"] > since for r in st["requests"] if user["role"] == "ceo" or r["user_id"] == user["id"])
    return {"seq": seq, "notifications": notes, "messages": msgs, "requests_changed": reqs}
