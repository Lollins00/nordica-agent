"""Memoria curata: i fatti estratti sono "candidati". Solo i fatti APPROVATI da una persona
vengono usati per rispondere (comandamento 2)."""
import re
from datetime import datetime

from . import data, llm, store
from .config import NOW
from .permissions import acl_label, can_see
from .util import norm

STATUSES = ("candidate", "approved", "rejected")


def _fact_from_claim(claim: dict, source: dict) -> dict:
    return {"id": claim["fact"], "statement": claim["text"], "key": claim.get("key"), "value": claim.get("value"),
            "topics": claim.get("topics", []), "source": data.source_ref(source), "acl": source["acl"],
            "init": claim.get("init", "candidate"), "origin": "estratto dalle fonti"}


def _static_facts() -> list[dict]:
    out = []
    for s in data.all_sources().values():
        if s["id"].startswith("IN-"):
            continue
        for c in s.get("claims", []):
            out.append(_fact_from_claim(c, s))
    return out


def all_facts() -> list[dict]:
    st = store.get()
    facts = _static_facts() + [dict(f) for f in st["extra_facts"]]
    for f in facts:
        dec = st["facts"].get(f["id"], {})
        f["status"] = dec.get("status", f.get("init", "candidate"))
        f["decided_by"] = dec.get("decided_by")
        f["decided_at"] = dec.get("decided_at")
        f["visibility"] = acl_label(f["acl"])
    return facts


def get_fact(fid: str) -> dict | None:
    return next((f for f in all_facts() if f["id"] == fid), None)


def facts_for(role: str, status: str | None = None) -> list[dict]:
    """Fatti visibili al ruolo (filtro permessi in CODICE), opzionalmente per stato."""
    return [f for f in all_facts() if can_see(role, f["acl"]) and (status is None or f["status"] == status)]


def set_status(fid: str, status: str, role: str, who: str) -> dict:
    if status not in STATUSES:
        raise ValueError("stato non valido")
    f = get_fact(fid)
    if not f or not can_see(role, f["acl"]):
        raise LookupError("fatto non trovato")      # stessa risposta se non esiste o non è visibile
    store.get()["facts"][fid] = {"status": status, "decided_by": who, "decided_at": NOW.isoformat(timespec="minutes")}
    store.get()["brief_cache"].clear()
    store.save()
    return get_fact(fid)


# --------------------------------------------------- estrazione da testo nuovo
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_SIGNAL = re.compile(r"\d|€|scad|entro|consegn|pagament|fattur|riunion|call|demo|budget|ferie", re.I)


def _heuristic(text: str) -> list[dict]:
    out = []
    for s in _SENT.split(text):
        s = s.strip()
        if len(s) > 25 and _SIGNAL.search(s) and not s.endswith("?"):
            words = [w for w in re.findall(r"[a-zàèéìòù]{4,}", norm(s))][:8]
            out.append({"text": s, "topics": words})
    return out[:5]


def extract_candidates(source: dict) -> tuple[list[dict], str]:
    """Estrae fatti candidati da una fonte nuova. Gemini se disponibile, altrimenti euristica. Torna (fatti, motore)."""
    prompt = ("Estrai dal testo i fatti verificabili (date, importi, decisioni, impegni). "
              "Rispondi in JSON: {\"facts\": [{\"text\": \"frase autosufficiente\", \"topics\": [\"parole chiave\"]}]}. "
              "Non inventare nulla che non sia nel testo. Massimo 5 fatti.\n\nTESTO:\n" + source["text"])
    res = llm.generate_json(prompt, "Sei un estrattore di fatti aziendali. Rispondi solo con JSON valido.")
    engine = "gemini"
    items = None
    if isinstance(res, dict) and isinstance(res.get("facts"), list):
        items = [{"text": str(i.get("text", "")).strip(), "topics": [str(t) for t in i.get("topics", [])][:8]}
                 for i in res["facts"] if isinstance(i, dict) and i.get("text")]
        # guardia anti-invenzione: ogni numero del fatto deve comparire nel testo originale
        src_nums = {n for n in re.findall(r"\d+", source["text"])}
        items = [i for i in items if set(re.findall(r"\d+", i["text"])) <= src_nums]
    if not items:
        items, engine = _heuristic(source["text"]), "regole"
    st = store.get()
    created = []
    for it in items[:5]:
        fid = store.next_id("F-IN")
        st["extra_facts"].append({"id": fid, "statement": it["text"], "key": None, "value": None,
                                  "topics": it["topics"], "source": data.source_ref(source), "acl": source["acl"],
                                  "init": "candidate", "origin": f"estratto da email in ingresso ({engine})"})
        created.append(fid)
    store.save()
    return [get_fact(i) for i in created], engine
