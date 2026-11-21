"""Calendario locale (base) con form 'Nuovo evento' dalla UI."""
from datetime import datetime

from . import data, store
from .permissions import acl_label, can_see
from .util import to_date


def visible_events(role: str) -> list[dict]:
    evs = [e for e in data.all_events() if can_see(role, e["acl"])]
    for e in evs:
        e["visibility"] = acl_label(e["acl"])
    return sorted(evs, key=lambda e: e["start"])


def add_event(role: str, who: str, title: str, start: str, end: str | None = None, private: bool = False,
              via: str = "ui") -> dict:
    title = title.strip()
    if not title:
        raise ValueError("titolo mancante")
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end) if end else s.replace(hour=min(s.hour + 1, 23))
    except ValueError:
        raise ValueError("data/ora non valida")
    ev = {"id": store.next_id("EV"), "title": title, "start": s.isoformat(timespec="minutes"),
          "end": e.isoformat(timespec="minutes"), "owner": role, "acl": [role] if private else "all",
          "type": "meeting", "created_by": who, "via": via}
    store.get()["events"].append(ev)
    store.get()["brief_cache"].clear()
    store.save()
    return ev


def day_of(ev: dict):
    return to_date(ev["start"])
