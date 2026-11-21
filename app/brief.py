"""Brief del mattino. Le REGOLE decidono cosa segnalare (scadenze, email senza risposta, conflitti tra fonti,
assenze); Gemini serve SOLO a scrivere il testo (comandamento 1). Tutto filtrato per ruolo prima di scrivere."""
import hashlib
import json
from datetime import datetime, timedelta

from . import data, llm, store
from .config import NOW, TODAY
from .permissions import can_see
from .util import day_label, fmt_date, fmt_short, numbers_in, to_date

SEV_ORDER = {"high": 0, "med": 1, "low": 2}


def _fmt_value(key: str, v: str) -> str:
    t = data.keys().get(key, {}).get("type")
    if t == "date":
        return fmt_date(v)
    if t == "days":
        return f"{v} giorni"
    return str(v)


def _short(s: dict) -> str:
    if s["type"] == "doc":
        return s["title"].split(" (")[0]
    if s["type"] == "slack":
        return s["title"].split(" · ")[0]
    if s["type"] == "task":
        return "task " + s["id"]
    return s["title"].replace("Email · ", "email «") + "»"


# ------------------------------------------------------------------ regole
def rule_deadlines(role: str) -> list[dict]:
    out = []
    for t in data.tasks():
        if t["status"] == "done" or not can_see(role, t["acl"]):
            continue
        due = to_date(t["due"])
        days = (due - TODAY).days
        if days > 7:
            continue
        sev = "high" if days <= 2 else "med" if days <= 5 else "low"
        when = "è SCADUTO" if days < 0 else f"scade il {fmt_short(due)} ({day_label(due, TODAY)})"
        out.append({"id": f"deadline:{t['id']}", "rule": "deadline", "severity": sev,
                    "title": f"Scadenza vicina: {t['title']}",
                    "detail": f"«{t['title']}» {when}. Responsabile: {t['owner_name']}.",
                    "sources": [data.source_ref(data.all_sources()[t["id"]])], "action": None, "_sort": days})
    return out


def rule_unanswered(role: str) -> list[dict]:
    out = []
    mails = data.emails()
    for e in mails:
        if e["direction"] != "in" or not e.get("external") or e.get("needs_reply") is False or not can_see(role, e["acl"]):
            continue
        answered = any(o["thread"] == e["thread"] and o["direction"] == "out" and o["date"] > e["date"] for o in mails)
        age = NOW - datetime.fromisoformat(e["date"])
        if answered or age < timedelta(hours=24):
            continue
        days = age.days
        sev = "high" if days >= 3 else "med"
        out.append({"id": f"unanswered:{e['id']}", "rule": "unanswered", "severity": sev,
                    "title": f"Email senza risposta: {e['subject']}",
                    "detail": f"{e['from_name']} ha scritto {days} giorni fa («{e['subject']}») e non c'è ancora una risposta.",
                    "sources": [data.source_ref(data.all_sources()[e["id"]])],
                    "action": {"type": "reply_email", "label": "Prepara bozza di risposta", "args": {"email_id": e["id"]}},
                    "_sort": -days})
    return out


def rule_conflicts(role: str) -> list[dict]:
    out = []
    for key, meta in data.keys().items():
        claims = []
        for s in data.all_sources().values():
            if not can_see(role, s["acl"]):
                continue
            for c in s.get("claims", []):
                if c.get("key") == key and c.get("value") is not None:
                    claims.append((c["value"], s))
        values = {v for v, _ in claims}
        if len(values) < 2:
            continue
        groups = {}
        for v, s in claims:
            groups.setdefault(v, []).append(s)
        parts = [f"{_fmt_value(key, v)} ({', '.join(_short(x) for x in ss)})" for v, ss in sorted(groups.items())]
        srcs = [data.source_ref(s) for _, s in claims]
        action = None
        auth = [(v, s) for v, s in claims if s["meta"].get("authority")]
        if meta.get("task_id") and auth:
            t = data.task_by_id(meta["task_id"])
            if t and can_see(role, t["acl"]) and t["due"] != auth[0][0]:
                action = {"type": "change_deadline", "label": f"Proponi di allineare {t['id']} al contratto ({fmt_short(auth[0][0])})",
                          "args": {"task_id": t["id"], "new_due": auth[0][0]}}
        elif key == "acme.payment_terms":
            em = next((e for e in data.emails() if e["id"] == "E-03" and can_see(role, e["acl"])), None)
            if em:
                action = {"type": "reply_email", "label": "Prepara bozza di risposta ad Acme", "args": {"email_id": "E-03"}}
        out.append({"id": f"conflict:{key}", "rule": "conflict", "severity": "high",
                    "title": f"Conflitto tra fonti: {meta['label']}",
                    "detail": f"Le fonti non concordano su «{meta['label']}»: " + " vs ".join(parts) + ".",
                    "sources": srcs, "action": action, "_sort": -10})
    return out


def rule_absences(role: str) -> list[dict]:
    out = []
    for ev in data.all_events():
        if ev.get("type") != "ferie" or not can_see(role, ev["acl"]):
            continue
        s, e = to_date(ev["start"]), to_date(ev["end"])
        for t in data.tasks():
            if t["status"] == "done" or t["owner"] != ev.get("person") or not can_see(role, t["acl"]):
                continue
            due = to_date(t["due"])
            if s <= due <= e:
                out.append({"id": f"absence:{t['id']}:{ev['id']}", "rule": "absence", "severity": "high",
                            "title": f"Responsabile assente alla scadenza: {t['title']}",
                            "detail": f"«{t['title']}» scade il {fmt_short(due)} ma {ev['person_name']} è in ferie dal {fmt_short(s)} al {fmt_short(e)}.",
                            "sources": [data.source_ref(data.all_sources()[t["id"]]), {"id": ev["id"], "type": "event", "title": ev["title"], "date": ev["start"]}],
                            "action": None, "_sort": -5})
    return out


def rule_inbox(role: str) -> list[dict]:
    """Email esterne smistate a questo settore con bozza ancora in attesa."""
    out = []
    st = store.get()
    for m in st["inbox"]:
        if role != "ceo" and m["routed_role"] != role:
            continue
        o = m["outcome"]
        act = next((a for a in st["actions"] if a["id"] == o.get("action_id")), None)
        if act and act["status"] != "pending":
            continue
        src = data.all_sources().get(m["source_id"])
        out.append({"id": f"inbox:{m['id']}", "rule": "inbox", "severity": "med",
                    "title": f"Nuova richiesta in ingresso: {m['subject'] or 'senza oggetto'}",
                    "detail": f"{m['from']} ha scritto «{m['subject']}»: {o['summary']} Smistata al settore {o['sector']}; "
                              f"la bozza di risposta è in attesa tra le Azioni.",
                    "sources": [data.source_ref(src)] if src else [], "action": None, "_sort": -3})
    return out


def compute_alerts(role: str) -> list[dict]:
    alerts = rule_conflicts(role) + rule_absences(role) + rule_inbox(role) + rule_unanswered(role) + rule_deadlines(role)
    alerts.sort(key=lambda a: (SEV_ORDER[a["severity"]], a["_sort"]))
    for a in alerts:
        a.pop("_sort", None)
    return alerts


# ------------------------------------------------------------- scrittura
def _fallback_intro(first: str, alerts: list[dict]) -> str:
    n, hi = len(alerts), sum(1 for a in alerts if a["severity"] == "high")
    if not n:
        return f"Buongiorno {first}! Oggi non vedo nulla di urgente: scadenze, email e fonti sono in ordine."
    return f"Buongiorno {first}! Ho controllato scadenze, email e fonti per te: {n} punti da guardare, {hi} urgenti."


def build_brief(user: dict) -> dict:
    role = user["role"]
    alerts = compute_alerts(role)
    first = user["first"]
    key = f"{user['id']}:{hashlib.md5(json.dumps(alerts, sort_keys=True, default=str).encode()).hexdigest()}"
    cache = store.get()["brief_cache"]
    if key in cache:
        return cache[key]
    intro, writer = _fallback_intro(first, alerts), "regole"
    texts = {a["id"]: a["detail"] for a in alerts}
    if alerts:
        brief_in = [{"id": a["id"], "severity": a["severity"], "titolo": a["title"], "dettaglio": a["detail"]} for a in alerts]
        prompt = ("Scrivi il brief del mattino per " + first + " partendo da questi avvisi GIÀ DECISI da regole. "
                  "Non aggiungere, togliere o cambiare date, numeri, nomi o fatti. Tono diretto e cordiale, italiano. "
                  "Per ogni avviso max 2 frasi. Rispondi in JSON: {\"intro\": \"saluto di 1-2 frasi\", \"items\": {\"<id>\": \"testo\"}}.\n\n"
                  + json.dumps(brief_in, ensure_ascii=False))
        res = llm.generate_json(prompt, "Sei Company Brain di Nordica Srl. Scrivi solo il testo, non decidi cosa segnalare.")
        if isinstance(res, dict) and isinstance(res.get("items"), dict):
            ok = 0
            for a in alerts:
                t = res["items"].get(a["id"])
                if isinstance(t, str) and t.strip() and numbers_in(t) <= numbers_in(a["detail"] + a["title"]):
                    texts[a["id"]], ok = t.strip(), ok + 1
            if ok:
                writer = "gemini"
                i = res.get("intro")
                if isinstance(i, str) and i.strip() and numbers_in(i) <= {str(len(alerts))}:
                    intro = i.strip()
    out = {"user": user["id"], "role": role, "first": first, "intro": intro, "writer": writer,
           "generated_at": NOW.isoformat(timespec="minutes"),
           "subject": f"Buongiorno {first}: {len(alerts)} cose da sapere oggi" if alerts else f"Buongiorno {first}: tutto in ordine",
           "alerts": [{**a, "text": texts[a["id"]]} for a in alerts]}
    if writer == "gemini":
        cache[key] = out
    return out
