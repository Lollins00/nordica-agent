# Company Brain — Nordica Srl (demo hackathon)

Demo FastAPI + UI web in stile Slack (animazioni, dark mode, card). Scenario: Nordica Srl, software house di 30 persone, cliente chiave Acme Logistica. Utenti: Giulia (Finance), Marco (Tech Lead), Sara (CEO).
LLM: Gemini via `GEMINI_API_KEY` da `.env`, sempre con fallback deterministico se manca la rete. Dati finti in JSON.

## I 7 COMANDAMENTI (non negoziabili: ogni modifica deve rispettarli tutti)

1. **Parla per primo.** Brief del mattino/alert generato da REGOLE (scadenze, email senza risposta, conflitti tra fonti). Gemini serve SOLO a scrivere il testo, mai a decidere cosa segnalare.
2. **Memoria curata.** I fatti estratti sono "candidati" che si approvano o rifiutano dalla UI. Si risponde SOLO da fatti approvati.
3. **Permessi per ruolo, in codice, PRIMA dell'LLM.** Stessa domanda da Finance e da Tech: una risponde, l'altra no. L'LLM non vede mai dati che il ruolo non può leggere.
4. **Ogni risposta cita le fonti.** Gli ID citati devono essere un sottoinsieme di quelli forniti al modello, verificato in codice.
5. **Se manca il dato: "non lo so".** Gate deterministico in codice (soglia), senza chiamare l'LLM.
6. **Le azioni restano "in attesa" finché una persona approva** (bozza email, cambio scadenza, eventi proposti dall'agente). Nessun effetto prima dell'approvazione.
7. **L'interfaccia mostra chat, email del mattino e mock Slack.**

## Decisioni di perimetro

- La casella email dell'agente è in INGRESSO (IMAP, sola lettura). Si invia email SOLO dalla dashboard (SMTP) come effetto di una bozza approvata da una persona, con DIROTTAMENTO ATTIVO di default (MAIL_MODE=redirect → tutto a MAIL_REDIRECT_TO). Invio reale solo con MAIL_MODE=real nel .env. Risposte, brief e avvisi restano nella UI.
- Calendario: base = calendario locale JSON con form "Nuovo evento" nella UI (eventi inseriti da una persona = immediati; eventi proposti dall'agente = in attesa). Bonus solo se avanza tempo: lettura Google Calendar via URL iCal.
- Ingresso email e calendario sono extra: si costruiscono DOPO i 7 comandamenti. Nessun extra può violarli.
- Login separati (sessione server-side): il ruolo viene SEMPRE dalla sessione, mai dal client. Niente selettore utenti.
- La casella email è NON FIDATA: ogni email è smistata per settore (Finance/Tech/CEO) a chi di competenza con bozza in attesa e fatti candidati; il mittente non riceve mai dati e un'email non può innescare azioni. Nessun tag nell'oggetto. Il settore decide CHI vede la richiesta, mai cosa può sapere chi scrive (comandamento 3).
- Orologio demo fisso (lunedì 23/11/2026 ore 09:00 (settimana di dati 16–20/11)) per risultati ripetibili.
- Commit piccoli e frequenti. **Niente push**: lo fa l'utente. `.env` e `state/` mai in git.
- Tempi: demo costruita in meno di 1 ora, poi solo test e rifinitura.
