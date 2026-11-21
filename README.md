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

Extra: calendario locale con form "Nuovo evento"; **login separati** (giulia / marco / sara, password demo `nordica2026`;
il ruolo viene sempre dalla sessione sul server); **casella email dell'agente** solo in ingresso e sempre non fidata:
ogni email esterna viene classificata per settore (Finance/Tech/CEO) e smistata a chi di competenza, con una bozza di risposta
IN ATTESA e fatti candidati. Chi scrive non riceve mai dati né risposte e un'email non può innescare azioni.
Simulatore nella UI; lettura IMAP reale (sola lettura) se `IMAP_USER`/`IMAP_PASSWORD` sono nel `.env`. 
**Invio email dalla dashboard (SMTP, stessa password per app dell'IMAP).** Le bozze si possono modificare (destinatario,
oggetto, testo; l'originale dell'AI resta registrata) e restano IN ATTESA. Solo "Approva e invia" (con conferma esplicita)
spedisce, e solo chi ha il permesso. **Dirottamento attivo di default** (`MAIL_MODE=redirect`): ogni email va a
`MAIL_REDIRECT_TO` con il destinatario reale nell'oggetto e nel testo. L'invio reale ai destinatari richiede `MAIL_MODE=real`
nel `.env` (mai dalla UI) e rifiuta i domini finti (.example). Se l'invio fallisce l'azione resta in attesa; limite di 10 invii/ora.

**Comunicazione interna in tempo reale** (aggiornamento ogni 2,5 s): canali e messaggi diretti salvati sul server.
I messaggi diretti li vedono solo i due partecipanti (né la CEO né il Brain). Il Brain legge i canali, ne ricava fatti
candidati e, citato con `@brain`, risponde nel canale usando solo fatti che TUTTI i membri possono vedere.
**Richieste ferie**: vanno alla CEO, unica a decidere; il Brain segnala da solo, con le fonti, preavviso inferiore ai 15 giorni
della policy e scadenze di cui la persona è responsabile. Notifiche in tempo reale a chi è coinvolto.

## Copione demo (5-7 minuti)
Due finestre/profili del browser: a sinistra **giulia** o **marco**, a destra **sara**.
1. **Parla per primo** – accedi: il brief compare da solo (3 avvisi a Giulia, 3 a Marco, 5 a Sara: conflitto 30/11 vs 05/12, email Acme senza risposta, scadenze). Mostra anche «Email del mattino».
2. **Memoria curata** – giulia: «Qual è la penale per il ritardo?» → «Non lo so». In Memoria approva «penale 1,5%» e **rifiuta** «secondo Franco sarebbe il 5%». Richiedi → solo 1,5%, con fonte.
3. **Sa chi chiede** – «Qual è il margine su Acme?»: giulia → 22% con fonte; marco → «Non lo so». (E marco: «Qual è la causa dell'incidente del 12/11?» → risponde; giulia no.)
4. **Cita** – ogni risposta e ogni avviso ha le fonti cliccabili.
5. **Ammette di non sapere** – «Quanto costa la mensa aziendale?» → «Non lo so».
6. **Propone soltanto** – marco: «Sposta la scadenza della consegna Release 2 al 30/11» → IN ATTESA → approva (giulia non può). Oppure una bozza email: modifica, «Approva e invia» (dirottata al tuo indirizzo).
7. **Sta nella giornata del team** – chat, email del mattino, canali: in #general «@brain qual è il margine su Acme?» → «Non lo so» (nel canale c'è anche Tech).
8. **Bonus tempo reale** – marco chiede ferie 01/12–05/12 in Richieste → nella finestra di sara compare subito l'avviso del Brain (preavviso 8 giorni, T-101 durante le ferie) → sara approva → marco riceve la notifica, le ferie vanno in calendario.

Ripristina con «↺ Reset demo» prima di iniziare.

Le credenziali demo sono salvate hashate (PBKDF2); le sessioni sono in memoria (riavviando il server serve un nuovo login).
