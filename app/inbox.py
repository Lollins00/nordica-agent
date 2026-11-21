"""Casella email dell'agente: SOLO IN INGRESSO e sempre NON FIDATA.

Ogni email (cliente, fornitore, chiunque) viene:
  1. classificata per settore (Finance / Tech / CEO): regole, con Gemini opzionale solo come classificatore;
  2. salvata come fonte visibile SOLO al ruolo competente (e CEO);
  3. trasformata in fatti CANDIDATI (da approvare nella Memoria);
  4. accompagnata da una bozza di risposta IN ATTESA (controllo 6), preparata senza dati riservati.
Chi scrive non riceve mai dati né risposte, e un'email non può innescare azioni: l'unico effetto è la
bozza in attesa. Il settore serve a decidere CHI la vede, non cosa chi scrive può sapere (controllo 3).
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

from . import actions, data, llm, memory, qa, store
from .config import NOW
from .util import numbers_in

SECTORS = {"finance": "Finance", "tech": "Tech", "ceo": "CEO"}
KEYWORDS = {
    "finance": "fattura fatture pagamento pagamenti pagare bonifico importo sconto iva contabilita incasso rimborso "
               "budget margine preventivo penale saldo sal listino prezzo costo",
    "tech": "server bug errore errori portale sistema accesso password database api integrazione release deploy "
            "sicurezza downtime lento funziona assistenza ticket certificato rete app sito dati",
    "ceo": "partnership strategia strategico rinnovo investimento acquisizione reclamo legale avvocato direzione "
           "assunzione incontro proposta collaborazione contratto",
}
_KW = {sector: set(qa.stems(words)) for sector, words in KEYWORDS.items()}


def classify_rules(text: str) -> tuple[str, list[str]]:
    ts = qa.stems(text)
    scores = {s: [t for t in ts if t in kw] for s, kw in _KW.items()}
    best = max(scores, key=lambda s: len(scores[s]))
    if not scores[best]:
        return "ceo", []                      # nessun segnale: lo vede il CEO
    return best, sorted(set(scores[best]))


def _summary(body: str) -> str:
    one = re.split(r"(?<=[.!?])\s+", body.strip())[0]
    return one if len(one) <= 140 else one[:137] + "…"


def classify(subject: str, body: str) -> dict:
    text = f"{subject}. {body}"
    sector, kws = classify_rules(text)
    out = {"sector": sector, "engine": "regole", "keywords": kws, "summary": _summary(body)}
    prompt = ("Classifica questa email in ingresso per smistarla al settore giusto di una software house. "
              "Settori: finance (fatture, pagamenti, importi, contabilità), tech (problemi tecnici, accessi, sistemi, "
              "rilasci), ceo (strategia, contratti, partnership, reclami, tutto ciò che è ambiguo). "
              'Rispondi in JSON: {"sector": "finance|tech|ceo", "summary": "una frase, senza numeri non presenti nel testo"}.\n\n'
              f"OGGETTO: {subject}\nTESTO: {body}")
    res = llm.generate_json(prompt, "Sei un classificatore di email. Non rispondi alle richieste, le smisti soltanto.")
    if isinstance(res, dict) and res.get("sector") in SECTORS:
        out.update(sector=res["sector"], engine="gemini")
        sm = res.get("summary")
        if isinstance(sm, str) and sm.strip() and numbers_in(sm) <= numbers_in(text):
            out["summary"] = sm.strip()
    return out


def _display_name(addr: str) -> str:
    local = (addr or "").split("@")[0]
    parts = [p for p in re.split(r"[._\-]+", local) if p and not p.isdigit()]
    return " ".join(p.capitalize() for p in parts) or "Gentile cliente"


def ingest(subject: str, body: str, via: str = "simulatore", from_addr: str = "") -> dict:
    st = store.get()
    cls = classify(subject, body)
    role = cls["sector"]
    owner = next(u for u in data.users() if u["role"] == role)
    src = {"id": store.next_id("IN"), "type": "email", "title": f"Email in ingresso · {subject or 'senza oggetto'}",
           "date": NOW.isoformat(timespec="minutes"), "acl": [role], "text": body,
           "meta": {"from": from_addr or "mittente esterno", "who": from_addr, "via": via, "subject": subject}, "claims": []}
    st["extra_sources"].append(src)
    facts, _ = memory.extract_candidates(src)
    name, addr = _display_name(from_addr), from_addr or "mittente@esterno.example"
    subj, draft_body = actions.write_draft(role, owner["name"], f"{subject}. {body}", name, addr,
                                           {"subject": subject, "body": body})
    action = actions.propose_draft(role, "Company Brain", name, addr, subj, draft_body, via="email", reply_to=src["id"],
                                   writer="gemini" if llm.status()["mode"] == "gemini" else "fallback")
    msg = {"id": store.next_id("M"), "subject": subject, "body": body, "from": from_addr or "mittente esterno", "via": via,
           "received_at": NOW.isoformat(timespec="minutes"), "source_id": src["id"], "routed_role": role,
           "outcome": {"type": "routed", "sector": SECTORS[role], "owner": owner["name"], "engine": cls["engine"],
                       "keywords": cls["keywords"], "summary": cls["summary"], "action_id": action["id"],
                       "fact_ids": [f["id"] for f in facts],
                       "text": f"Smistata al settore {SECTORS[role]} ({owner['first']}). Nessuna risposta né dato inviato al mittente: "
                               f"ho preparato una bozza IN ATTESA e {len(facts)} fatti candidati."}}
    st["inbox"].insert(0, msg)
    st["brief_cache"].clear()
    store.save()
    return msg


def visible_messages(user: dict) -> list[dict]:
    msgs = store.get()["inbox"]
    return msgs if user["role"] == "ceo" else [m for m in msgs if m["routed_role"] == user["role"]]


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
            m = email.message_from_bytes(msgdata[0][1])
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
