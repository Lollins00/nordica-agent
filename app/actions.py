"""Azioni (bozza email, cambio scadenza, nuovo evento). Nascono SEMPRE 'pending' e non hanno alcun
effetto finché una persona abilitata non approva (comandamento 6)."""
import re

from . import data, events, llm, mailer, memory, qa, store
from .config import now, today
from .permissions import can_see
from .util import fmt_date, fmt_dt, fmt_short, norm, numbers_in, parse_date, parse_time

CONTACTS = {
    "acme": ("Luca Ferri", "luca.ferri@acmelogistica.example"),
    "luca": ("Luca Ferri", "luca.ferri@acmelogistica.example"),
    "cloudnord": ("CloudNord Fatturazione", "fatture@cloudnord.example"),
}


def _acl(role: str, approvers: list[str]) -> list[str]:
    return sorted(set(approvers) | {role, "ceo"})


def _approvers(kind: str, role: str) -> list[str]:
    return {"change_deadline": ["tech", "ceo"], "draft_email": [role, "ceo"], "create_event": [role, "ceo"]}[kind]


def _create(kind: str, role: str, who: str, title: str, summary: str, details: list, payload: dict, via: str) -> dict:
    st = store.get()
    for a in st["actions"]:                      # niente doppioni: stessa azione ancora in attesa
        if a["status"] == "pending" and a["type"] == kind and a["payload"] == payload:
            return a
    approvers = _approvers(kind, role)
    a = {"id": store.next_id("A"), "type": kind, "status": "pending", "title": title, "summary": summary,
         "details": details, "payload": payload, "proposed_by": who, "proposed_role": role, "via": via,
         "approvers": approvers, "acl": _acl(role, approvers), "created_at": now().isoformat(timespec="minutes"),
         "decided_by": None, "decided_at": None, "result": None}
    st["actions"].insert(0, a)
    store.save()
    return a


# --------------------------------------------------------------- builders
def propose_deadline_change(role: str, who: str, task_id: str, new_due: str, via: str = "chat") -> dict:
    t = data.task_by_id(task_id)
    if not t or not can_see(role, t["acl"]):
        raise LookupError("task non trovato")
    if t["due"] == new_due:
        raise ValueError("la scadenza è già questa")
    return _create("change_deadline", role, who, f"Cambio scadenza: {t['title']}",
                   f"Spostare la scadenza di {t['id']} da {fmt_date(t['due'])} a {fmt_date(new_due)}.",
                   [{"label": "Task", "value": f"{t['id']} · {t['title']}"},
                    {"label": "Scadenza attuale", "value": fmt_date(t["due"])},
                    {"label": "Nuova scadenza", "value": fmt_date(new_due)},
                    {"label": "Responsabile", "value": t["owner_name"]}],
                   {"task_id": task_id, "old_due": t["due"], "new_due": new_due}, via)


def propose_event(role: str, who: str, title: str, start: str, end: str | None, via: str = "chat") -> dict:
    return _create("create_event", role, who, f"Nuovo evento: {title}", f"Aggiungere «{title}» al calendario il {fmt_dt(start)}.",
                   [{"label": "Titolo", "value": title}, {"label": "Quando", "value": fmt_dt(start)}],
                   {"title": title, "start": start, "end": end}, via)


def _based_on(sources: list[dict]) -> str:
    return ", ".join(s["title"] for s in sources) if sources else "nessun fatto verificato: risposta interlocutoria"


def propose_draft(role: str, who: str, to_name: str, to_addr: str, subject: str, body: str, via: str = "chat",
                  reply_to: str | None = None, writer: str = "fallback", sources: list[dict] | None = None) -> dict:
    sources = sources or []
    a = _create("draft_email", role, who, f"Bozza email a {to_name}", f"Oggetto: {subject}",
                [{"label": "A", "value": f"{to_name} <{to_addr}>"}, {"label": "Oggetto", "value": subject},
                 {"label": "Testo", "value": body}, {"label": "Basata su", "value": _based_on(sources)},
                 {"label": "Scritta da", "value": writer}],
                {"to": to_addr, "to_name": to_name, "subject": subject, "body": body, "reply_to": reply_to}, via)
    a["sources"] = sources                       # controllo 4: anche la bozza dice da dove viene
    store.save()
    return a


# ----------------------------------------------------------- decisione
def visible_actions(role: str) -> list[dict]:
    return [a for a in store.get()["actions"] if can_see(role, a["acl"])]


def _execute(a: dict) -> str:
    p, st = a["payload"], store.get()
    if a["type"] == "change_deadline":
        st["task_overrides"][p["task_id"]] = p["new_due"]
        return f"Scadenza di {p['task_id']} aggiornata a {fmt_date(p['new_due'])}."
    if a["type"] == "create_event":
        events.add_event(a["proposed_role"], a["decided_by"], p["title"], p["start"], p.get("end"), via="azione approvata")
        return f"Evento «{p['title']}» aggiunto al calendario."
    if a["type"] == "draft_email":
        if not mailer.configured():
            st.setdefault("outbox", []).append({"to": p["to"], "subject": p["subject"], "body": p["body"], "at": now().isoformat(timespec="minutes")})
            return "Bozza approvata e registrata come inviata (simulazione: SMTP non configurato)."
        res = mailer.send(p["to"], p["subject"], p["body"])          # solleva MailError se non parte
        a["sent"] = res
        st.setdefault("outbox", []).append({"to": res["sent_to"], "intended_to": res["intended_to"], "subject": p["subject"],
                                            "body": p["body"], "at": res["at"], "mode": res["mode"]})
        hhmm = res["at"][11:16]
        if res["mode"] == "redirect":
            return f"Inviata alle {hhmm} in modalità demo: dirottata a {res['sent_to']} (destinatario reale: {res['intended_to']})."
        return f"Inviata a {res['intended_to']} alle {hhmm}."
    return "ok"


def decide(action_id: str, role: str, who: str, approve: bool) -> dict:
    st = store.get()
    a = next((x for x in st["actions"] if x["id"] == action_id and can_see(role, x["acl"])), None)
    if not a:
        raise LookupError("azione non trovata")
    if a["status"] != "pending":
        raise ValueError("azione già decisa")
    if role not in a["approvers"]:
        raise PermissionError("il tuo ruolo non può decidere questa azione")
    a["decided_by"], a["decided_at"] = who, now().isoformat(timespec="minutes")
    if approve:
        try:
            a["result"] = _execute(a)
        except mailer.MailError as e:
            a["decided_by"] = a["decided_at"] = None                 # l'azione resta IN ATTESA: nulla è partito
            raise RuntimeError(str(e))
        a["status"] = "approved"
    else:
        a["status"], a["result"] = "rejected", "Rifiutata: nessuna modifica effettuata."
    st["brief_cache"].clear()
    store.save()
    return a


def edit_draft(action_id: str, role: str, who: str, to: str, subject: str, body: str) -> dict:
    """Una persona modifica la bozza dell'AI. Resta IN ATTESA; si conserva l'originale per tracciabilità."""
    st = store.get()
    a = next((x for x in st["actions"] if x["id"] == action_id and can_see(role, x["acl"])), None)
    if not a:
        raise LookupError("azione non trovata")
    if a["type"] != "draft_email" or a["status"] != "pending":
        raise ValueError("si possono modificare solo bozze email ancora in attesa")
    if role not in a["approvers"]:
        raise PermissionError("il tuo ruolo non può modificare questa bozza")
    try:
        to, subject = mailer.clean_header(to, "destinatario"), mailer.clean_header(subject, "oggetto")
    except mailer.MailError as e:
        raise ValueError(str(e))
    body = (body or "").replace("\r\n", "\n").strip()
    if not mailer.valid_address(to):
        raise ValueError("indirizzo del destinatario non valido")
    if not subject or len(subject) > 200:
        raise ValueError("oggetto mancante o troppo lungo (max 200 caratteri)")
    if not body or len(body) > 10000:
        raise ValueError("testo mancante o troppo lungo (max 10000 caratteri)")
    p = a["payload"]
    if "original" not in a:
        a["original"] = {"to": p["to"], "subject": p["subject"], "body": p["body"],
                         "writer": next((d["value"] for d in a["details"] if d["label"] == "Scritta da"), "")}
    p.update(to=to, subject=subject, body=body, to_name=p.get("to_name") or to)
    a["edited_by"], a["edited_at"] = who, now().isoformat(timespec="minutes")
    a["summary"] = f"Oggetto: {subject}"
    a["details"] = [{"label": "A", "value": f"{p['to_name']} <{to}>"}, {"label": "Oggetto", "value": subject},
                    {"label": "Testo", "value": body}, {"label": "Basata su", "value": _based_on(a.get("sources", []))},
                    {"label": "Scritta da", "value": f"{a['original']['writer']} · modificata da {who}"}]
    store.save()
    return a


# ----------------------------------------------------- scrittura bozze
def _reply_candidate(role: str, to_addr: str, text: str) -> dict | None:
    ts = set(qa.stems(text))
    cands = [e for e in data.emails() if e["direction"] == "in" and e["from"] == to_addr and can_see(role, e["acl"])]
    if not cands:
        return None
    best = max(cands, key=lambda e: (len(ts & set(qa.stems(e["subject"] + " " + e["body"]))), e["date"]))
    return best


def write_draft(role: str, who: str, request: str, to_name: str, to_addr: str, reply: dict | None) -> tuple[str, str, list[dict]]:
    """Testo della bozza + fonti usate. Per i destinatari esterni si usano SOLO fatti con visibilità 'tutti'."""
    hits, _ = qa.retrieve(role, request)
    ctx = [f for f in hits if f["acl"] == "all"]
    srcs = data.all_sources()
    sources = ([data.source_ref(srcs[reply["id"]])] if reply and reply.get("id") in srcs else []) + \
              [f["source"] for f in ctx if f["source"]["id"] != (reply or {}).get("id")]
    first = to_name.split()[0]
    facts_txt = " ".join(f["statement"] for f in ctx)
    subject = reply["subject"] if reply and reply["subject"].lower().startswith("re:") else (
        f"Re: {reply['subject']}" if reply else "Aggiornamento da Nordica")
    prompt = (f"Scrivi una breve email professionale in italiano a {to_name} (cliente esterno) a nome di {who}, Nordica Srl.\n"
              f"Richiesta interna: {request}\n"
              + (f"Email a cui rispondere: «{reply['body']}»\n" if reply else "")
              + (f"Fatti utilizzabili (non aggiungerne altri, nessun numero inventato): {facts_txt}\n" if ctx else
                 "Non hai fatti verificati: non impegnarti su date o numeri, di' solo che stai verificando e risponderai a breve.\n")
              + "Rispondi in JSON: {\"body\": \"testo completo con saluto e firma\"}")
    res = llm.generate_json(prompt, "Scrivi bozze email prudenti. Non inventare impegni, date o importi.")
    if isinstance(res, dict) and isinstance(res.get("body"), str) and res["body"].strip():
        allowed = numbers_in(facts_txt + " " + request + " " + (reply["body"] if reply else ""))
        if numbers_in(res["body"]) <= allowed:
            return subject, res["body"].strip() + "\n", sources
    body = f"Gentile {first},\n\n"
    about = f" («{reply['subject']}»)" if reply else ""
    body += f"grazie per il messaggio{about}. "
    body += (f"Le confermo quanto segue: {facts_txt}\n\n" if ctx else "Stiamo verificando e le daremo un riscontro puntuale a breve.\n\n")
    body += f"Cordiali saluti,\n{who}\nNordica Srl\n"
    return subject, body, sources


def propose_reply_to_email(role: str, who: str, email_id: str, via: str = "brief") -> dict:
    e = next((x for x in data.emails() if x["id"] == email_id), None)
    if not e or not can_see(role, e["acl"]):
        raise LookupError("email non trovata")
    name = e["from_name"].split(" (")[0]
    subject, body, sources = write_draft(role, who, f"Rispondi a: {e['subject']}", name, e["from"], e)
    return propose_draft(role, who, name, e["from"], subject, body, via, reply_to=email_id,
                         writer="gemini" if llm.status()["mode"] == "gemini" else "fallback", sources=sources)


# -------------------------------------------------- da linguaggio naturale
def detect_intent(text: str) -> str | None:
    t = norm(text)
    if re.search(r"\b(mail|email|bozza)\b", t) and re.search(r"\b(scriv\w*|prepar\w*|redig\w*|bozza|rispond\w*|invia\w*|mand\w*)\b", t):
        return "draft_email"
    if re.search(r"\b(fiss\w*|cre\w*|aggiung\w*|organizz\w*|programm\w*|prenot\w*|metti)\b", t) and \
            re.search(r"\b(riunion\w*|call|evento|meeting|demo|incontro|appuntament\w*)\b", t):
        return "create_event"
    if re.search(r"\b(spost\w*|posticip\w*|anticip\w*|cambi\w*|modific\w*|aggiorn\w*)\b", t) and \
            re.search(r"\b(scadenz\w*|consegn\w*|deadline|data)\b", t):
        return "change_deadline"
    return None


def propose_from_text(role: str, who: str, text: str, via: str = "chat") -> dict | None:
    intent = detect_intent(text)
    if not intent:
        return None
    t = norm(text)
    if intent == "change_deadline":
        new = parse_date(text, today())
        ts = set(qa.stems(text))
        cands = [x for x in data.tasks() if can_see(role, x["acl"]) and x["status"] != "done"]
        def sc(x):
            s = len(ts & set(qa.stems(x["title"]))) + (5 if x["id"].lower() in t else 0)
            return s
        best = max(cands, key=sc, default=None)
        if not best or sc(best) == 0:
            return {"kind": "clarify", "text": "Quale task vuoi spostare? Indicamelo (es. «T-101» o il nome) e la nuova data."}
        if not new:
            return {"kind": "clarify", "text": f"Per quando vuoi spostare «{best['title']}»? Indicami la nuova data (es. 30/11)."}
        try:
            a = propose_deadline_change(role, who, best["id"], new.isoformat(), via)
        except ValueError as e:
            return {"kind": "clarify", "text": str(e).capitalize() + "."}
        return {"kind": "action", "action": a, "text": "Ho preparato la proposta di cambio scadenza. Resta IN ATTESA finché una persona abilitata non la approva."}
    if intent == "create_event":
        d = parse_date(text, today())
        if not d:
            return {"kind": "clarify", "text": "Per quando fisso l'evento? Indicami giorno e, se vuoi, l'ora."}
        hhmm = parse_time(text) or "10:00"
        m = re.search(r"(riunione|call|evento|meeting|demo|incontro|appuntamento)(.*)", text, re.I)
        title = (m[0] if m else "Evento")
        title = re.split(r"\b(?:il|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica|domani|dopodomani|alle|ore|\d)", title, 1, re.I)[0].strip(" ,.") or "Evento"
        title = title[0].upper() + title[1:]
        a = propose_event(role, who, title, f"{d.isoformat()}T{hhmm}", None, via)
        return {"kind": "action", "action": a, "text": "Ho preparato l'evento. Resta IN ATTESA finché una persona non lo approva."}
    # draft_email
    key = next((k for k in CONTACTS if k in t), None)
    if not key:
        return {"kind": "clarify", "text": "A chi devo scrivere? Indicami il destinatario (es. «Acme» o «CloudNord»)."}
    to_name, to_addr = CONTACTS[key]
    reply = _reply_candidate(role, to_addr, text)
    subject, body, sources = write_draft(role, who, text, to_name, to_addr, reply)
    a = propose_draft(role, who, to_name, to_addr, subject, body, via, reply_to=reply["id"] if reply else None, sources=sources,
                      writer="gemini" if llm.status()["mode"] == "gemini" else "fallback")
    return {"kind": "action", "action": a, "text": "Ho scritto una bozza. Resta IN ATTESA: nulla viene inviato finché una persona non approva."}
