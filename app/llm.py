"""Client Gemini (REST) con fallback. Se manca chiave/rete/quota o la risposta non è valida torna None e i
chiamanti usano il fallback deterministico: la demo non si rompe mai.

Strategia pensata per la quota gratuita (15 richieste/minuto per modello, alcuni modelli 20/giorno):
  - un modello alla volta, si passa al successivo solo se quello fallisce (niente chiamate parallele: consumano quota);
  - un modello che risponde 429 (quota) va in pausa per il tempo indicato da Google; 503/timeout per pochi secondi;
  - cache delle richieste identiche (10 minuti): le prove della demo non consumano quota e le risposte restano stabili.
"""
import hashlib
import json
import os
import re
import time

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
CACHE_TTL = 600

_status = {"last_ok": None, "model": None, "last_error": None, "forced_offline": False, "down_until": 0.0}
_cooldown: dict[str, float] = {}           # modello -> fino a quando non va chiamato
_cache: dict[str, tuple[float, str]] = {}


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or None


def models() -> list[str]:
    # Ordine scelto con prove dal vivo: i "lite" rispondono veloci (15 richieste/minuto ciascuno);
    # 3-flash-preview ha solo 20 richieste/giorno; i flash pieni spesso danno 503. gemini-2.5-* non è più disponibile.
    first = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    out = []
    for m in (first, "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3-flash-preview"):
        if m not in out:
            out.append(m)
    return out


def status() -> dict:
    configured = bool(api_key())
    if _status["forced_offline"]:
        mode = "offline-forzato"
    elif not configured:
        mode = "non-configurato"
    elif _status["last_ok"] is False:
        mode = "fallback"
    elif _status["last_ok"] is True:
        mode = "gemini"
    else:
        mode = "pronto"
    now = time.time()
    return {"mode": mode, "configured": configured, "model": _status["model"] or models()[0],
            "last_error": _status["last_error"], "forced_offline": _status["forced_offline"],
            "paused": {m: int(t - now) for m, t in _cooldown.items() if t > now}}


def set_offline(flag: bool) -> None:
    _status["forced_offline"] = flag
    _status["down_until"] = 0.0


def _post(model: str, body: dict, timeout) -> requests.Response:
    return requests.post(BASE.format(model), headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"},
                         json=body, timeout=timeout)


def _retry_after(r: requests.Response) -> float:
    try:
        for d in r.json().get("error", {}).get("details", []):
            if d.get("retryDelay"):
                return float(str(d["retryDelay"]).rstrip("s"))
    except (ValueError, AttributeError):
        pass
    return 60.0


def _one(model: str, body: dict, timeout: float) -> tuple[str | None, str | None]:
    """Una chiamata. Torna (testo, None) oppure (None, tipo_errore). Mette in pausa il modello se serve."""
    now = time.time()
    try:
        r = _post(model, body, timeout=(4, timeout))
    except requests.ConnectionError as e:
        _status["last_error"] = f"rete non raggiungibile ({type(e).__name__})"
        return None, "net"
    except requests.Timeout:
        _status["last_error"] = f"timeout su {model}"
        _cooldown[model] = now + 30
        return None, "timeout"
    if r.status_code != 200:
        _status["last_error"] = f"{model}: HTTP {r.status_code}"
        if r.status_code == 429:                       # quota: rispetta il tempo indicato da Google
            _cooldown[model] = now + min(max(_retry_after(r), 10), 12 * 3600)
        elif r.status_code in (500, 502, 503, 504):
            _cooldown[model] = now + 20
        elif r.status_code in (403, 404):
            _cooldown[model] = now + 3600
        return None, "http"
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    except (KeyError, IndexError, ValueError):
        _status["last_error"] = f"{model}: risposta non valida"
        return None, "bad"
    if text:
        _status["model"] = model
    return (text or None), (None if text else "bad")


def generate(prompt: str, system: str | None = None, json_mode: bool = False, max_tokens: int = 2048) -> str | None:
    """Testo generato o None (offline, quota, errore, risposta vuota). Non solleva mai."""
    if _status["forced_offline"] or not api_key() or time.time() < _status["down_until"]:
        return None
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    deadline = time.time() + 15
    for model in models():
        if time.time() < _cooldown.get(model, 0):
            continue                                   # in pausa (quota o errore recente): prova il prossimo
        remaining = deadline - time.time()
        if remaining < 2:
            break
        text, err = _one(model, body, min(8, remaining))
        if text:
            _status.update(last_ok=True, last_error=None)
            _cache[key] = (time.time(), text)
            if len(_cache) > 500:
                _cache.pop(next(iter(_cache)))
            return text
        if err == "net":                               # niente rete: inutile provare altri modelli
            _status["down_until"] = time.time() + 20
            break
    _status["last_ok"] = False
    if not any(time.time() >= _cooldown.get(m, 0) for m in models()):
        _status["last_error"] = "quota Gemini esaurita su tutti i modelli: uso il fallback"
    return None


def generate_json(prompt: str, system: str | None = None) -> dict | list | None:
    text = generate(prompt, system, json_mode=True)
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
        return None
