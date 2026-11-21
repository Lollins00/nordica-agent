"""Client Gemini (REST) con fallback. Se manca chiave/rete/risposta valida torna None e i chiamanti
usano il fallback deterministico: la demo non si rompe mai."""
import json
import os
import re
import time

import requests
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

BASE = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

_status = {"last_ok": None, "model": None, "last_error": None, "forced_offline": False, "down_until": 0.0}


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or None


def models() -> list[str]:
    # Ordine scelto con prove dal vivo: i modelli "lite" e 3-flash-preview rispondono, i flash pieni
    # (3.8/3.7/3.6/3.5) davano 503 "high demand". gemini-2.5-* non è più disponibile ai nuovi utenti (404).
    first = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    out = []
    for m in (first, "gemini-3-flash-preview", "gemini-flash-lite-latest", "gemini-3.8-flash"):
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
    return {"mode": mode, "configured": configured, "model": _status["model"] or models()[0],
            "last_error": _status["last_error"], "forced_offline": _status["forced_offline"]}


def set_offline(flag: bool) -> None:
    _status["forced_offline"] = flag
    _status["down_until"] = 0.0


def _post(model: str, body: dict, timeout: float) -> requests.Response:
    return requests.post(BASE.format(model), headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"},
                         json=body, timeout=timeout)


def _one(model: str, body: dict, timeout: float) -> str | None:
    """Una chiamata a un modello. Torna il testo o None; registra l'ultimo errore. Non solleva."""
    try:
        r = _post(model, body, timeout=(4, timeout))
    except requests.ConnectionError as e:
        _status["last_error"] = f"rete non raggiungibile ({type(e).__name__})"
        return None
    except requests.Timeout:
        _status["last_error"] = f"timeout su {model}"
        return None
    if r.status_code != 200:
        _status["last_error"] = f"{model}: HTTP {r.status_code}"
        return None
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    except (KeyError, IndexError, ValueError):
        _status["last_error"] = f"{model}: risposta non valida"
        return None
    if text:
        _status["model"] = model
    return text or None


def generate(prompt: str, system: str | None = None, json_mode: bool = False, max_tokens: int = 2048) -> str | None:
    """Testo generato o None (offline, errore, risposta vuota). Non solleva mai.
    Chiama in parallelo (hedging) i primi modelli e prende la prima risposta valida: i modelli di Gemini
    vanno spesso in 503 'high demand' o rallentano, e così la demo non aspetta il più lento."""
    if _status["forced_offline"] or not api_key() or time.time() < _status["down_until"]:
        return None
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    ms = models()[:3]
    ex = ThreadPoolExecutor(len(ms))
    futs = [ex.submit(_one, m, body, 14) for m in ms]
    text = None
    try:
        for f in as_completed(futs, timeout=16):
            text = f.result()
            if text:
                break
    except FuturesTimeout:
        _status["last_error"] = "nessun modello ha risposto in tempo"
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if text:
        _status.update(last_ok=True, last_error=None)
        return text
    _status.update(last_ok=False, down_until=time.time() + 45)   # tutti falliti: fallback subito per 45s
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
