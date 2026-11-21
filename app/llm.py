"""Client Gemini (REST) con fallback. Se manca chiave/rete/risposta valida torna None e i chiamanti
usano il fallback deterministico: la demo non si rompe mai."""
import json
import os
import re
import time

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

_status = {"last_ok": None, "model": None, "last_error": None, "forced_offline": False, "down_until": 0.0}


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or None


def models() -> list[str]:
    first = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    out = []
    for m in (first, "gemini-3.5-flash", "gemini-flash-latest"):
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


def generate(prompt: str, system: str | None = None, json_mode: bool = False, max_tokens: int = 2048) -> str | None:
    """Testo generato o None (offline, errore, risposta vuota). Non solleva mai."""
    if _status["forced_offline"] or not api_key() or time.time() < _status["down_until"]:
        return None
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    deadline = time.time() + 22
    for i, model in enumerate(models()[:3]):
        remaining = deadline - time.time()
        if remaining < 3:
            break
        try:
            r = _post(model, body, timeout=min(12 if i == 0 else 8, remaining))
        except requests.ConnectionError as e:   # niente rete: inutile riprovare, apro il circuito per 60s
            _status.update(last_ok=False, last_error=f"rete non raggiungibile ({type(e).__name__})",
                           down_until=time.time() + 60)
            return None
        except requests.Timeout:
            _status.update(last_ok=False, last_error=f"timeout su {model}")
            continue
        if r.status_code != 200:
            _status.update(last_ok=False, last_error=f"{model}: HTTP {r.status_code}")
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        except (KeyError, IndexError, ValueError):
            _status.update(last_ok=False, last_error=f"{model}: risposta non valida")
            continue
        if text:
            _status.update(last_ok=True, model=model, last_error=None)
            return text
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
