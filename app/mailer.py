"""Invio email dalla dashboard (SMTP). Si invia SOLO come effetto di un'azione approvata da una persona.

Modalità (variabile MAIL_MODE, default "redirect"):
  - redirect: OGNI email viene dirottata su MAIL_REDIRECT_TO, con il destinatario reale scritto nel testo e
              nell'oggetto. Nessun cliente reale (o finto) riceve nulla. È la modalità della demo.
  - real:     l'email va davvero al destinatario (si attiva solo modificando il .env, mai dalla UI).
Protezioni: indirizzi validati, niente a capo negli header (header injection), limite di invii all'ora,
errori espliciti (se l'invio fallisce l'azione resta IN ATTESA).
"""
import os
import re
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")
RESERVED = (".example", ".invalid", ".test", ".localhost")
MAX_PER_HOUR = 10
_SENT: list[float] = []


class MailError(Exception):
    pass


def _user() -> str | None:
    return os.getenv("SMTP_USER") or os.getenv("IMAP_USER")


def _password() -> str | None:
    return os.getenv("SMTP_PASSWORD") or os.getenv("IMAP_PASSWORD")


def configured() -> bool:
    return bool(_user() and _password())


def mode() -> str:
    return "real" if os.getenv("MAIL_MODE", "redirect").strip().lower() == "real" else "redirect"


def redirect_to() -> str | None:
    return (os.getenv("MAIL_REDIRECT_TO") or "").strip() or None


def status() -> dict:
    return {"configured": configured(), "mode": mode(), "redirect_to": redirect_to(), "from": _user()}


def valid_address(addr: str) -> bool:
    return bool(addr) and len(addr) <= 254 and bool(EMAIL_RE.match(addr))


def clean_header(value: str, name: str) -> str:
    value = (value or "").strip()
    if re.search(r"[\r\n\x00]", value):
        raise MailError(f"{name} non valido")
    return value


def _rate_limit() -> None:
    now = time.time()
    _SENT[:] = [t for t in _SENT if now - t < 3600]
    if len(_SENT) >= MAX_PER_HOUR:
        raise MailError(f"limite di {MAX_PER_HOUR} invii all'ora raggiunto")
    _SENT.append(now)


def _deliver(msg: EmailMessage) -> None:
    with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")),
                          context=ssl.create_default_context(), timeout=20) as s:
        s.login(_user(), _password())
        s.send_message(msg)


def send(to: str, subject: str, body: str) -> dict:
    """Invia (o dirotta) un'email. Solleva MailError se non parte: il chiamante non deve segnare l'azione come fatta."""
    if not configured():
        raise MailError("SMTP non configurato")
    to, subject = clean_header(to, "destinatario"), clean_header(subject, "oggetto")
    if not valid_address(to):
        raise MailError("destinatario non valido")
    if not subject or not (body or "").strip():
        raise MailError("oggetto o testo vuoti")
    m = mode()
    if m == "redirect":
        target = redirect_to()
        if not target or not valid_address(target):
            raise MailError("dirottamento attivo ma MAIL_REDIRECT_TO non è impostato: non invio nulla")
        rcpt = target
        subj = f"[DEMO → {to}] {subject}"
        text = (f"⚠️ Email di prova: in modalità demo è stata dirottata qui.\nDestinatario reale: {to}\n"
                f"{'-' * 60}\n\n{body}")
    else:
        if to.lower().endswith(RESERVED):
            raise MailError("indirizzo di esempio: in modalità reale non invio a domini riservati")
        rcpt, subj, text = to, subject, body
    msg = EmailMessage()
    msg["From"] = formataddr(("Nordica · Company Brain", _user()))
    msg["To"], msg["Subject"] = rcpt, subj
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    msg["X-Company-Brain"] = "outbound"
    msg.set_content(text)
    _rate_limit()
    try:
        _deliver(msg)
    except (smtplib.SMTPException, OSError) as e:
        _SENT.pop()
        raise MailError(f"invio non riuscito ({type(e).__name__})")
    return {"mode": m, "sent_to": rcpt, "intended_to": to, "at": datetime.now().isoformat(timespec="seconds")}
