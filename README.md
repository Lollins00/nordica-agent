# Company Brain · Nordica Srl (demo hackathon)

FastAPI + UI web in stile Slack (dark mode, card, animazioni). Dati finti in `data/*.json`, orologio demo fisso
(lunedì 23/11/2026 09:00). LLM: Gemini via `GEMINI_API_KEY` (`.env`) con fallback deterministico.

## Avvio
```
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
poi http://localhost:8000 · test dei 7 controlli: `.venv\Scripts\python.exe -m pytest tests -q`

## I 7 controlli (dove sono nel codice)
| # | Controllo | Codice | Test |
|---|---|---|---|
| 1 | Brief da regole, Gemini solo scrive | `app/brief.py` | `test_1_*` |
| 2 | Memoria curata (candidati → approvati) | `app/memory.py`, vista Memoria | `test_2_*` |
| 3 | Permessi per ruolo prima dell'LLM | `app/permissions.py`, `qa.retrieve` | `test_3_*` |
| 4 | Fonti citate e verificate | `app/qa.py` (`_llm_answer`) | `test_4_*` |
| 5 | "Non lo so" deterministico | `app/qa.py` (soglia) | `test_5_*` |
| 6 | Azioni in attesa finché si approva | `app/actions.py`, vista Azioni | `test_6_*` |
| 7 | UI: chat, email del mattino, mock Slack | `static/` | `test_7_*` |

Extra: calendario locale con form "Nuovo evento"; casella email dell'agente **solo in ingresso**
(simulatore nella UI, IMAP in sola lettura se `IMAP_USER`/`IMAP_PASSWORD` sono nel `.env`).
Nessuna email viene mai inviata. Il ruolo del mittente si dichiara nell'oggetto: `[Giulia]`, `[Marco]`, `[Sara]`.

## Copione demo (5 minuti)
1. **Giulia**: il Brain parla per primo (brief a regole: conflitto 30/11 vs 5/12, email Acme senza risposta, ferie di Marco).
2. Giulia: «Qual è il margine su Acme?» → 22% con fonte. Passa a **Marco**, stessa domanda → «Non lo so». (Dietro le quinte: fatti esclusi dai permessi.)
3. Marco: «Qual è la causa dell'incidente del 12/11?» → risponde; Giulia no.
4. «Qual è la penale per il ritardo?» → «Non lo so» (è solo candidato) → Memoria → Approva → richiedi di nuovo.
5. «Quanto costa la mensa aziendale?» → «Non lo so».
6. «Quando è la consegna della Release 2?» → fonti discordanti, entrambe citate.
7. «Sposta la scadenza della consegna Release 2 al 30/11» → azione IN ATTESA → solo Tech/CEO possono approvare.
8. «Scrivi una bozza di email ad Acme per la richiesta di demo» → bozza in attesa (mai fatti riservati in una mail a un esterno).
9. Casella agente: stessa domanda con `[Giulia]` e `[Marco]`, e senza tag (nessun permesso).
10. Interruttore «Simula offline»: tutto continua a funzionare con il fallback.

Autenticazione solo dimostrativa (selettore utente nella UI).
