"""Stato mutabile della demo (fatti approvati, azioni, eventi, inbox) su state/state.json."""
import json
import threading
from .config import STATE_DIR

_lock = threading.RLock()
_state: dict | None = None
_FILE = STATE_DIR / "state.json"


def _default() -> dict:
    return {
        "facts": {},            # fact_id -> {status, decided_by, decided_at}
        "extra_facts": [],      # fatti estratti da fonti nuove (email in ingresso)
        "extra_sources": [],    # fonti create a runtime (email in ingresso)
        "actions": [],
        "task_overrides": {},   # task_id -> nuova scadenza ISO
        "events": [],           # eventi aggiunti a runtime
        "inbox": [],            # email ricevute dall'agente
        "imap_seen": [],
        "counters": {},
        "brief_cache": {},
    }


def get() -> dict:
    global _state
    with _lock:
        if _state is None:
            STATE_DIR.mkdir(exist_ok=True)
            if _FILE.exists():
                try:
                    _state = {**_default(), **json.loads(_FILE.read_text(encoding="utf-8"))}
                except (json.JSONDecodeError, OSError):
                    _state = _default()
            else:
                _state = _default()
        return _state


def save() -> None:
    with _lock:
        STATE_DIR.mkdir(exist_ok=True)
        s = {k: v for k, v in get().items() if k != "brief_cache"}
        _FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def reset() -> None:
    global _state
    with _lock:
        seen = list(get()["imap_seen"])   # le email già lette non si rileggono dopo un reset
        counters = dict(get()["counters"])  # gli ID non ripartono da 1: una pagina rimasta aperta non può colpire un'azione diversa
        _state = _default()
        _state["imap_seen"] = seen
        _state["counters"] = counters
        save()


def next_id(prefix: str) -> str:
    with _lock:
        c = get()["counters"]
        c[prefix] = c.get(prefix, 0) + 1
        return f"{prefix}-{c[prefix]:03d}"
