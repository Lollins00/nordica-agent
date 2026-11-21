"""Casella email dell'agente: SOLO IN INGRESSO. Nulla viene inviato: risposte e avvisi restano nella UI.

Il ruolo del mittente si dichiara con un tag nell'oggetto: [Giulia] [Marco] [Sara].
Senza tag il mittente è "sconosciuto": nessun permesso, quindi nessun dato (deny by default).
Cosa fa l'agente con una mail:
  - azione richiesta (bozza, cambio scadenza, evento) -> azione IN ATTESA
  - domanda                                           -> risposta con fonti (con i permessi del mittente)
  - altro                                             -> nuova fonte + fatti CANDIDATI da approvare
"""
import email
import email.message
import email.utils
import imaplib
import os
import re
import threading
import time
from email.header import decode_header, make_header

from . import actions, data, memory, qa, store
from .config import NOW
from .util import norm

TAG = re.compile(r"\[(giulia|marco|sara)\]", re.I)
QUESTION = re.compile(r"^(qual\w*|quand\w*|chi|cosa|come|quant\w*|dove|perch\w*|c'e|ce|esiste)\b")


def sender_from_subject(subject: str) -> dict | None:
    m = TAG.search(subject or "")
    return data.user_by_id(m.group(1).lower()) if m else None


def _clean_subject(subject: str) -> str:
    return TAG.sub("", subject or "").strip(" :-")


def ingest(subject: str, body: str, via: str = "simulatore", from_addr: str = "") -> dict:
    st = store.get()
    user = sender_from_subject(subject)
    clean = _clean_subject(subject)
    msg = {"id": store.next_id("M"), "subject": subject, "clean_subject": clean, "body": body, "from": from_addr,
           "via": via, "received_at": NOW.isoformat(timespec="minutes"),
           "sender": user["id"] if user else None, "sender_name": user["name"] if user else "Mittente sconosciuto",
           "sender_role": user["role"] if user else None}
    text = f"{clean}. {body}".strip()
    role, who = (user["role"], user["name"]) if user else (None, "sconosciuto")
    if not user:
        msg["outcome"] = {"type": "unknown_sender",
                          "text": "Non riconosco il mittente (manca il tag [Giulia]/[Marco]/[Sara] nell'oggetto): "
                                  "senza identità non ho permessi, quindi non posso rispondere né agire."}
    else:
        proposal = actions.propose_from_text(role, who, text, via="email")
        if proposal:
            msg["outcome"] = {"type": "action" if proposal["kind"] == "action" else "clarify", "text": proposal["text"],
                              "action_id": proposal.get("action", {}).get("id")}
        elif "?" in text or QUESTION.match(norm(clean)) or QUESTION.match(norm(body.strip())):
            r = qa.answer(role, text)
            msg["outcome"] = {"type": "answer" if r["kind"] == "answer" else "unknown", "text": r["text"],
                              "citations": r["citations"], "writer": r["writer"], "debug": r["debug"]}
        else:
            src = {"id": store.next_id("IN"), "type": "email", "title": f"Email in ingresso · {clean or 'senza oggetto'}",
                   "date": NOW.isoformat(timespec="minutes"), "acl": [role], "text": body,
                   "meta": {"from": who, "via": via}, "claims": []}
            st["extra_sources"].append(src)
            facts, engine = memory.extract_candidates(src)
            msg["outcome"] = {"type": "learned", "engine": engine, "fact_ids": [f["id"] for f in facts],
                              "text": (f"Ho acquisito l'email come nuova fonte (visibile solo a {user['first']} e CEO) ed estratto "
                                       f"{len(facts)} fatti CANDIDATI: non li userò per rispondere finché non li approvi nella Memoria.")}
    st["inbox"].insert(0, msg)
    st["brief_cache"].clear()
    store.save()
    return msg


def visible_messages(user: dict) -> list[dict]:
    msgs = store.get()["inbox"]
    if user["role"] == "ceo":
        return msgs
    return [m for m in msgs if m["sender"] == user["id"]]


# ---------------------------------------------------------------- IMAP
def imap_configured() -> bool:
    return bool(os.getenv("IMAP_USER") and os.getenv("IMAP_PASSWORD"))


def imap_status() -> dict:
    return {"configured": imap_configured(), "user": os.getenv("IMAP_USER") if imap_configured() else None}


def _body_of(m: email.message.Message) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = m.get_payload(decode=True)
    return payload.decode(m.get_content_charset() or "utf-8", "replace") if payload else ""


def imap_poll_once(limit: int = 15) -> int:
    """Legge (SOLA LETTURA) le ultime email e le processa se nuove. Torna quante ne ha processate."""
    if not imap_configured():
        return 0
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    n = 0
    conn = imaplib.IMAP4_SSL(host, timeout=15)
    try:
        conn.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
        conn.select("INBOX", readonly=True)
        typ, ids = conn.search(None, "ALL")
        seen = store.get()["imap_seen"]
        for num in ids[0].split()[-limit:]:
            typ, msgdata = conn.fetch(num, "(BODY.PEEK[])")
            raw = msgdata[0][1]
            m = email.message_from_bytes(raw)
            mid = m.get("Message-ID") or num.decode()
            if mid in seen:
                continue
            seen.append(mid)
            subject = str(make_header(decode_header(m.get("Subject", ""))))
            ingest(subject, _body_of(m).strip(), via="IMAP", from_addr=email.utils.parseaddr(m.get("From", ""))[1])
            n += 1
        store.save()
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return n


def start_background_poll(interval: int = 30) -> None:
    if not imap_configured():
        return

    def loop():
        while True:
            try:
                imap_poll_once()
            except Exception as e:  # la demo non deve cadere se la casella non risponde
                print("IMAP:", type(e).__name__)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
