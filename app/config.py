"""Configurazione: carica .env (anche se salvato in UTF-16 da PowerShell) e orologio demo fisso."""
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_DIR = Path(os.getenv("STATE_DIR") or ROOT / "state")
STATIC_DIR = ROOT / "static"


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in _decode(path.read_bytes()).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

# Orologio: ora REALE del computer. Per rendere la demo ripetibile in un altro giorno si può fissarla
# con DEMO_NOW nel .env (es. DEMO_NOW=2026-11-21T09:00:00). È una funzione: non resta ferma all'avvio.
def now() -> datetime:
    v = os.getenv("DEMO_NOW", "").strip()
    return datetime.fromisoformat(v) if v else datetime.now().replace(microsecond=0)


def today():
    return now().date()
