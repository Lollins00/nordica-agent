"""Livello dati: carica i JSON finti e li normalizza in "fonti" con ACL e "claim" strutturati."""
import json
from functools import lru_cache

from . import store
from .config import DATA_DIR
from .util import fmt_date, fmt_dt


def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache
def users() -> list[dict]:
    return _load("users.json")


def user_by_id(uid: str) -> dict | None:
    return next((u for u in users() if u["id"] == uid), None)


@lru_cache
def keys() -> dict:
    return _load("keys.json")


@lru_cache
def _emails() -> list[dict]:
    return _load("emails.json")


@lru_cache
def _chats() -> dict:
    return _load("chats.json")


@lru_cache
def _tasks() -> list[dict]:
    return _load("tasks.json")


@lru_cache
def _docs() -> list[dict]:
    return _load("docs.json")


@lru_cache
def _calendar() -> list[dict]:
    return _load("calendar.json")


def channels() -> list[dict]:
    return _chats()["channels"]


# ----------------------------------------------------------------- task
def tasks() -> list[dict]:
    """Task con le scadenze modificate da azioni approvate."""
    ov = store.get()["task_overrides"]
    out = []
    for t in _tasks():
        t = dict(t)
        if t["id"] in ov:
            t["due"] = ov[t["id"]]
        out.append(t)
    return out


def task_by_id(tid: str) -> dict | None:
    return next((t for t in tasks() if t["id"] == tid), None)


# ------------------------------------------------------------- fonti
def _ref(s: dict) -> dict:
    return {"id": s["id"], "type": s["type"], "title": s["title"], "date": s.get("date")}


def _subst(claim: dict, due: str | None) -> dict:
    c = dict(claim)
    if c.get("value") == "@due":
        c["value"] = due
    if due and "{due}" in c.get("text", ""):
        c["text"] = c["text"].replace("{due}", fmt_date(due))
    return c


def all_sources() -> dict[str, dict]:
    """Tutte le fonti (id -> fonte normalizzata). Ogni fonte ha acl e claims."""
    src: dict[str, dict] = {}
    for e in _emails():
        who = e["from_name"] if e["direction"] == "in" else f"{e['from_name']} → {', '.join(e['to'])}"
        src[e["id"]] = {"id": e["id"], "type": "email", "title": f"Email · {e['subject']}", "date": e["date"],
                        "acl": e["acl"], "text": e["body"], "meta": {"from": e["from_name"], "who": who,
                        "thread": e["thread"], "direction": e["direction"], "subject": e["subject"]},
                        "claims": e.get("claims", [])}
    ch = {c["id"]: c for c in channels()}
    for m in _chats()["messages"]:
        c = ch[m["channel"]]
        src[m["id"]] = {"id": m["id"], "type": "slack", "title": f"#{c['name']} · {m['author']}", "date": m["ts"],
                        "acl": c["acl"], "text": m["text"], "meta": {"channel": c["id"], "author": m["author"]},
                        "claims": m.get("claims", [])}
    for t in tasks():
        claims = [_subst(c, t["due"]) for c in t.get("claims", [])]
        text = f"{t['title']}. Scadenza {fmt_date(t['due'])}, stato: {t['status']}, assegnato a {t['owner_name']}."
        src[t["id"]] = {"id": t["id"], "type": "task", "title": f"Task {t['id']} · {t['title']}", "date": t["due"],
                        "acl": t["acl"], "text": text, "meta": {"owner": t["owner"], "status": t["status"]},
                        "claims": claims}
    for d in _docs():
        text = "\n".join(f"{s['heading']}: {s['text']}" for s in d["sections"])
        src[d["id"]] = {"id": d["id"], "type": "doc", "title": d["title"], "date": d["date"], "acl": d["acl"],
                        "text": text, "meta": {"kind": d["kind"], "authority": d.get("authority", False)},
                        "claims": d.get("claims", [])}
    for s in store.get()["extra_sources"]:
        src[s["id"]] = s
    return src


def source_ref(s: dict) -> dict:
    return _ref(s)


def source_view(s: dict) -> dict:
    return {**_ref(s), "text": s["text"], "acl": s["acl"], "meta": s.get("meta", {}),
            "date_fmt": fmt_dt(s["date"]) if "T" in str(s.get("date")) else fmt_date(s.get("date"))}


# ---------------------------------------------------------- calendario
def base_events() -> list[dict]:
    return [dict(e) for e in _calendar()]


def all_events() -> list[dict]:
    return base_events() + [dict(e) for e in store.get()["events"]]


def emails() -> list[dict]:
    return _emails()
