"""Domande e risposte.

Pipeline (l'ordine è il punto):
  1. solo fatti APPROVATI                         (comandamento 2)
  2. filtro permessi per ruolo, IN CODICE         (comandamento 3) – l'LLM non vede mai il resto
  3. retrieval + soglia: se niente supera la soglia -> "Non lo so" senza chiamare l'LLM  (comandamento 5)
  4. risposta (Gemini, vincolata ai soli fatti passati) o fallback deterministico
  5. citazioni verificate in codice: solo ID passati al modello                         (comandamento 4)
"""
import re

from . import llm, memory
from .permissions import can_see
from .util import norm, numbers_in

STOP = set("""il lo la i gli le un uno una di a da in con su per tra fra e ed o ma che chi cui non si
del dello della dei degli delle al allo alla ai agli alle dal dallo dalla dai dagli dalle nel nello nella nei negli
nelle sul sullo sulla sui sugli sulle quale quali quanto quanta quanti quante come dove quando perche cosa cose
sono era siamo essere stato stata ho hai ha abbiamo hanno mi ti ci vi mio tuo suo nostro loro questo questa questi
queste quello quella fare fatto puoi puo dimmi dammi sai vorrei voglio dire sapere esiste c'e ce piu molto anche
gia ancora oggi ora poi se ne lo qual dell dall nell sull quell all coll vorrebbe devo deve puoi dimmi mostra mostrami elenca vedere sapere dovrei""".split())

LOW = {"acme", "nordica", "logistica", "progetto", "azienda", "team", "release", "tracker"}

SYN_GROUPS = [
    ("consegna", {"consegna", "consegnare", "consegnata", "rilascio", "rilasciare", "deliver", "delivery", "chiusura", "chiudere", "chiudiamo"}),
    ("margine", {"margine", "margini", "marginalita", "guadagno", "guadagni", "profitto", "profitti", "utile", "ricavo"}),
    ("pagamento", {"pagamento", "pagamenti", "pagare", "pagato", "incasso", "incassi", "incassato", "incassare", "paga"}),
    ("incidente", {"incidente", "guasto", "outage", "downtime", "blocco", "crash", "successo", "accaduto", "durato", "durata"}),
    ("budget", {"budget", "stanziato", "stanziamento", "stanziare", "fondi"}),
    ("ferie", {"ferie", "vacanza", "vacanze", "assenza", "assente"}),
    ("penale", {"penale", "penali", "sanzione", "sanzioni", "mora"}),
    ("valore", {"valore", "vale", "prezzo", "importo", "costa", "costo", "costi", "quanto"}),
    ("sla", {"sla", "assistenza", "supporto"}),
]
_CANON = {w: c for c, ws in SYN_GROUPS for w in ws}
_CANON.pop("quanto", None)  # "quanto" da solo è una stopword


def stems(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[a-z0-9]+", norm(text)):
        if tok in STOP or (len(tok) < 3 and not tok.isdigit()):
            continue
        tok = _CANON.get(tok, tok)
        out.append(tok if tok.isdigit() else tok[:5])
    return out


_LOW_STEMS = {w[:5] for w in LOW}


def score(question_stems: set[str], fact: dict) -> float:
    """Punteggio lessicale. Le parole generiche (Acme, progetto...) e i numeri pesano poco e da soli non bastano;
    inoltre almeno il 60% dei termini 'forti' della domanda deve comparire nel fatto, altrimenti è un'altra domanda
    (es. «budget marketing» non deve pescare il budget assunzioni)."""
    fstems = set(stems(fact["statement"])) | set(stems(" ".join(fact["topics"])))
    hit = question_stems & fstems
    # un numero identificativo nella domanda (es. «fattura 4521») deve comparire nel fatto: altrimenti è un'altra cosa
    q_ids = {q for q in question_stems if q.isdigit() and len(q) >= 3 and not re.fullmatch(r"(19|20)\d\d", q)}
    if q_ids and not q_ids <= fstems:
        return 0.0
    q_strong = {q for q in question_stems if not q.isdigit() and q not in _LOW_STEMS}
    h_strong = hit & q_strong
    if not h_strong or len(h_strong) / len(q_strong) < 0.6:
        return 0.0
    return sum(0.5 if (h.isdigit() or h in _LOW_STEMS) else 1.0 for h in hit)


THRESHOLD = 1.0
UNKNOWN_TEXT = "Non lo so: tra i fatti approvati che posso consultare non c'è nulla che risponda a questa domanda."


def retrieve(role: str, question: str, k: int = 5) -> tuple[list[dict], dict]:
    """Ritorna (fatti pertinenti visibili, debug). I fatti non visibili vengono esclusi PRIMA del ranking."""
    qs = set(stems(question))
    approved = memory.facts_for(role, "approved") if role else []
    all_approved = [f for f in memory.all_facts() if f["status"] == "approved"]
    hidden = [f for f in all_approved if not can_see(role, f["acl"])]
    hidden_match = sum(1 for f in hidden if score(qs, f) >= THRESHOLD)   # solo un conteggio, mai il contenuto
    ranked = sorted(((score(qs, f), f) for f in approved), key=lambda x: -x[0])
    hits = [f for s, f in ranked if s >= THRESHOLD][:k]
    debug = {"role": role, "approved_total": len(all_approved), "approved_visible": len(approved),
             "excluded_by_permissions": len(hidden), "excluded_matching": hidden_match,
             "matched": [f["id"] for f in hits], "threshold": THRESHOLD}
    return hits, debug


def _conflicts(facts: list[dict]) -> list[tuple[dict, dict]]:
    out = []
    for i, a in enumerate(facts):
        for b in facts[i + 1:]:
            if a.get("key") and a["key"] == b.get("key") and a.get("value") != b.get("value"):
                out.append((a, b))
    return out


def _cite(f: dict) -> dict:
    return {"fact_id": f["id"], "statement": f["statement"], "source": f["source"]}


def _fallback_answer(facts: list[dict]) -> str:
    conflicts = _conflicts(facts)
    lines = []
    if conflicts:
        a, b = conflicts[0]
        lines.append("Attenzione: le fonti sono discordanti.")
        lines.append(f"• {a['statement']} [{a['id']}]")
        lines.append(f"• {b['statement']} [{b['id']}]")
        used = {a["id"], b["id"]}
        for f in facts:
            if f["id"] not in used:
                lines.append(f"• {f['statement']} [{f['id']}]")
    elif len(facts) == 1:
        lines.append(f"{facts[0]['statement']} [{facts[0]['id']}]")
    else:
        for f in facts:
            lines.append(f"• {f['statement']} [{f['id']}]")
    return "\n".join(lines)


def _llm_answer(question: str, facts: list[dict]) -> tuple[str | None, list[str]]:
    listing = "\n".join(f"[{f['id']}] {f['statement']} (fonte: {f['source']['title']})" for f in facts)
    conflict_note = "Se due fatti sono in contrasto, dillo esplicitamente e riporta entrambi." if _conflicts(facts) else ""
    system = ("Sei Company Brain, l'assistente interno di Nordica Srl. Rispondi in italiano, breve e preciso. "
              "Usa ESCLUSIVAMENTE i fatti elencati: non aggiungere date, numeri o informazioni che non vi compaiono. "
              "Dopo ogni affermazione cita il fatto con il suo ID tra parentesi quadre, es. [F-XYZ]. " + conflict_note)
    prompt = f"FATTI APPROVATI DISPONIBILI:\n{listing}\n\nDOMANDA: {question}\n\nRispondi in JSON: {{\"answer\": \"testo con citazioni [ID]\", \"used\": [\"ID\", ...]}}"
    res = llm.generate_json(prompt, system)
    if not isinstance(res, dict) or not isinstance(res.get("answer"), str):
        return None, []
    answer, used = res["answer"].strip(), res.get("used") or []
    allowed = {f["id"] for f in facts}
    cited = set(re.findall(r"\[(F-[A-Z0-9\-]+)\]", answer))
    # Verifiche in codice: citazioni solo tra i fatti forniti, almeno una, nessun numero inventato
    if not cited or not cited <= allowed or not set(map(str, used)) <= allowed:
        return None, []
    allowed_nums = numbers_in(" ".join(f["statement"] for f in facts) + " " + question)
    if not numbers_in(answer) - {i for c in cited for i in numbers_in(c)} <= allowed_nums:
        return None, []
    return answer, sorted(cited)


def answer(role: str | None, question: str) -> dict:
    hits, debug = retrieve(role, question)
    if not hits:
        return {"kind": "unknown", "text": UNKNOWN_TEXT, "citations": [], "writer": "regole", "debug": debug}
    text, used = _llm_answer(question, hits)
    writer = "gemini"
    if text is None:
        text, used, writer = _fallback_answer(hits), [f["id"] for f in hits], "fallback"
    by_id = {f["id"]: f for f in hits}
    return {"kind": "answer", "text": text, "citations": [_cite(by_id[i]) for i in used if i in by_id],
            "writer": writer, "conflict": bool(_conflicts(hits)), "debug": debug}
