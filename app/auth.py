"""Login separati: l'identità e il ruolo vengono SEMPRE dalla sessione sul server,
mai da ciò che dichiara il client. Sessioni in memoria (la demo si disconnette al riavvio)."""
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request

from . import data

COOKIE = "cb_session"
_SESSIONS: dict[str, str] = {}


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def verify(username: str, password: str) -> dict | None:
    u = next((x for x in data.users() if x["username"] == (username or "").strip().lower()), None)
    h = _hash(password or "", u["salt"] if u else "no-such-user")     # stesso costo anche se l'utente non esiste
    if u and hmac.compare_digest(h, u["password_hash"]):
        return u
    return None


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = user_id
    return token


def drop_session(token: str | None) -> None:
    _SESSIONS.pop(token or "", None)


def public(u: dict) -> dict:
    return {k: u[k] for k in ("id", "name", "first", "role", "title", "color", "email")}


def current_user(request: Request) -> dict:
    uid = _SESSIONS.get(request.cookies.get(COOKIE, ""))
    u = data.user_by_id(uid) if uid else None
    if not u:
        raise HTTPException(401, "non autenticato")
    return u
