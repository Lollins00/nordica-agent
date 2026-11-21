"""Utility: date italiane, normalizzazione testo."""
import re
import unicodedata
from datetime import date, datetime, timedelta

MONTHS = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
          "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
WEEKDAYS = {"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4, "sabato": 5, "domenica": 6}
WEEKDAY_NAMES = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MONTH_NAMES = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
               "settembre", "ottobre", "novembre", "dicembre"]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    return strip_accents(s or "").lower()


def to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)).date()
    except ValueError:
        return None


def fmt_date(v) -> str:
    d = to_date(v)
    return d.strftime("%d/%m/%Y") if d else str(v)


def fmt_short(v) -> str:
    d = to_date(v)
    return d.strftime("%d/%m") if d else str(v)


def fmt_dt(v) -> str:
    try:
        return datetime.fromisoformat(str(v)).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(v)


def day_label(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "oggi"
    if delta == 1:
        return "domani"
    if delta == -1:
        return "ieri"
    if delta < 0:
        return f"{-delta} giorni fa"
    return f"tra {delta} giorni"


def parse_date(text: str, today: date) -> date | None:
    t = norm(text)
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b", t)
    if m:
        day, mon = int(m[1]), int(m[2])
        year = int(m[3]) if m[3] else today.year
        if year < 100:
            year += 2000
        try:
            d = date(year, mon, day)
            if not m[3] and d < today - timedelta(days=30):
                d = date(year + 1, mon, day)
            return d
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(20\d\d))?\b", t)
    if m:
        year = int(m[3]) if m[3] else today.year
        try:
            d = date(year, MONTHS[m[2]], int(m[1]))
            if not m[3] and d < today - timedelta(days=30):
                d = date(year + 1, MONTHS[m[2]], int(m[1]))
            return d
        except ValueError:
            pass
    if re.search(r"\bdopodomani\b", t):
        return today + timedelta(days=2)
    if re.search(r"\bdomani\b", t):
        return today + timedelta(days=1)
    if re.search(r"\boggi\b", t):
        return today
    for name, wd in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            delta = (wd - today.weekday()) % 7 or 7
            return today + timedelta(days=delta)
    return None


def parse_time(text: str) -> str | None:
    t = norm(text)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return f"{int(m[1]):02d}:{m[2]}"
    m = re.search(r"\b(?:alle|ore|h)\s*([01]?\d|2[0-3])(?:[.:]([0-5]\d))?\b", t)
    if m:
        return f"{int(m[1]):02d}:{m[2] or '00'}"
    if "pomeriggio" in t:
        return "15:00"
    if "mattina" in t:
        return "10:00"
    return None


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[str]:
    """Numeri presenti in un testo, normalizzati (per il controllo anti-invenzione)."""
    return {n.replace(".", "").replace(",", ".").rstrip(".") for n in _NUM.findall(text or "")}
