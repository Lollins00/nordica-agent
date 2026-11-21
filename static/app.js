'use strict';
const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let j = null; try { j = await r.json(); } catch (e) { /* no body */ }
  if (r.status === 401 && path !== '/api/login') { showLogin('Sessione scaduta: accedi di nuovo'); throw new Error('Sessione scaduta'); }
  if (!r.ok) { const err = new Error((j && j.detail) || `Errore ${r.status}`); err.status = r.status; throw err; }
  return j;
}

const S = { users: [], user: null, view: 'chat', chat: {}, brief: {}, tab: 'candidate', actFilter: 'all', channel: null,
  status: null, now: null, slack: {} };
const ICON = { email: '✉️', slack: '#️⃣', task: '✅', doc: '📄', event: '📅' };
const TITLES = {
  chat: ['Chiedi al Brain', 'Risponde solo da fatti approvati, nei limiti dei tuoi permessi'],
  email: ['Email del mattino', 'Il brief generato dalle regole, scritto in forma di email'],
  memory: ['Memoria', 'I fatti estratti sono candidati: decidi tu cosa il Brain può usare'],
  actions: ['Azioni', 'Nulla accade finché una persona abilitata non approva'],
  calendar: ['Calendario', 'Eventi visibili al tuo ruolo'],
  inbox: ['Casella dell\'agente', 'Email esterne smistate per settore: chi scrive non riceve mai dati'],
  slack: ['Slack', 'Vedi solo i canali del tuo ruolo'],
};
const SUGGESTIONS = ['Qual è il margine su Acme?', 'Qual è la causa dell\'incidente del 12/11?', 'Quando è la consegna della Release 2?',
  'Qual è la penale per il ritardo?', 'Quanto costa la mensa aziendale?', 'Sposta la scadenza della consegna Release 2 al 30/11',
  'Scrivi una bozza di email ad Acme per la richiesta di demo', 'Fissa una call con Acme giovedì alle 15'];

// ------------------------------------------------------------------ helpers
const hhmm = iso => (iso || '').slice(11, 16);
const dmy = iso => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: 'numeric' }); };
const dayName = iso => new Date(iso).toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' });
const initials = n => n.split(/\s+/).map(x => x[0]).slice(0, 2).join('').toUpperCase();
const hue = n => { let h = 0; for (const c of n) h = (h * 31 + c.charCodeAt(0)) % 360; return `hsl(${h} 60% 48%)`; };
const avatar = (name, color) => `<div class="avatar" style="background:${color || hue(name)}">${esc(initials(name))}</div>`;
const brainAvatar = '<div class="avatar brain">🧠</div>';
const chip = (s, n) => `<button class="chip" data-src="${esc(s.id)}" title="${esc(s.title)}"><span>${ICON[s.type] || '📎'}</span>${n ? `<span class="n">${n}</span>` : ''}<span class="t">${esc(s.title)}</span></button>`;
const writerTag = w => w === 'gemini' ? '<span class="tag gemini">✨ scritto da Gemini</span>' : w === 'fallback' ? '<span class="tag">⚙️ fallback deterministico</span>' : '<span class="tag">📏 regole</span>';
const still = el => { el.style.animation = 'none'; $$('*', el).forEach(c => c.style.animation = 'none'); return el; };

function toast(text, kind = '') {
  const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = text;
  $('#toasts').append(t); setTimeout(() => { t.style.transition = '.4s'; t.style.opacity = 0; setTimeout(() => t.remove(), 400); }, 3200);
}

function fmtText(text, cites = []) {
  const idx = {}; cites.forEach((c, i) => idx[c.fact_id] = [i + 1, c.source.id]);
  return esc(text).replace(/\n/g, '<br>').replace(/\[(F-[A-Z0-9\-]+)\]/g, (m, id) =>
    idx[id] ? `<sup class="cite" data-src="${esc(idx[id][1])}" title="Apri la fonte">${idx[id][0]}</sup>` : '');
}

function debugHTML(d, writer) {
  if (!d) return '';
  return `<details class="debug"><summary>🔎 Dietro le quinte</summary><div class="debug-grid">
    <span>Ruolo</span><b>${esc(d.role || 'sconosciuto')}</b>
    <span>Fatti approvati in memoria</span><b>${d.approved_total}</b>
    <span>Visibili al tuo ruolo</span><b>${d.approved_visible}</b>
    <span>Esclusi dai permessi <i>(prima dell'LLM)</i></span><b>${d.excluded_by_permissions}${d.excluded_matching ? ` · ${d.excluded_matching} pertinenti alla domanda` : ''}</b>
    <span>Fatti usati</span><b>${d.matched.length ? esc(d.matched.join(', ')) : '—'}</b>
    <span>Testo scritto da</span><b>${esc(writer)}</b></div></details>`;
}

// ------------------------------------------------------------------ azioni (card)
const mailBanner = () => {
  const m = S.status && S.status.mail; if (!m || !m.configured) return '';
  return m.mode === 'redirect'
    ? `<div class="banner demo">📬 <b>Modalità demo</b>: le email approvate vengono <b>dirottate a ${esc(m.redirect_to || '—')}</b>, non ai destinatari reali.</div>`
    : '<div class="banner real">⚠️ <b>Invio reale attivo</b>: le email approvate partono davvero verso i destinatari.</div>';
};

function actionCard(a) {
  (S.act ||= {})[a.id] = a;
  const can = a.approvers && a.approvers.includes(S.user.role);
  const st = { pending: 'IN ATTESA', approved: 'APPROVATA', rejected: 'RIFIUTATA' }[a.status];
  const typeIco = { change_deadline: '🗓️', draft_email: '✉️', create_event: '📅' }[a.type] || '⚡';
  const isMail = a.type === 'draft_email';
  return `<div class="card action-card" data-aid="${esc(a.id)}">
    <div class="row"><b>${typeIco} ${esc(a.title)}</b><span class="spacer"></span><span class="status ${a.status}">${st}</span></div>
    <dl class="kv">${(a.details || []).map(d => `<dt>${esc(d.label)}</dt><dd>${esc(d.value)}</dd>`).join('')}</dl>
    <div class="meta">Proposta da ${esc(a.proposed_by)} · via ${esc(a.via)} · ${esc(a.id)}${a.edited_by ? ` · ✏️ modificata da ${esc(a.edited_by)}` : ''}${a.decided_by ? ` · decisa da ${esc(a.decided_by)}` : ''}</div>
    ${a.status === 'pending'
      ? `<div class="row" style="margin-top:10px">
          <button class="btn ok" data-decide="1" ${can ? '' : 'disabled'}>${isMail ? '✓ Approva e invia' : '✓ Approva'}</button>
          ${isMail && can ? '<button class="btn sec" data-edit="1">✏️ Modifica</button>' : ''}
          <button class="btn no" data-decide="0" ${can ? '' : 'disabled'}>✕ Rifiuta</button>
          ${can ? '' : `<span class="meta">Può decidere solo: ${a.approvers.join(', ')}</span>`}</div>`
      : `<div class="meta" style="margin-top:8px">→ ${esc(a.result || '')}</div>`}
  </div>`;
}

const STALE = 'Questa azione non esiste più (demo ripristinata o server riavviato). Ricarica la pagina.';
function markStale(id) {
  $$(`.action-card[data-aid="${id}"]`).forEach(c => { c.outerHTML = `<div class="card stale"><b>⚠️ Azione non più disponibile</b><div class="meta">${esc(STALE)}</div></div>`; });
  toast(STALE, 'bad'); refreshStatus();
}

const replaceCards = a => $$(`.action-card[data-aid="${a.id}"]`).forEach(c => { c.outerHTML = actionCard(a); });

async function decideAction(id, approve, btn) {
  if (btn) btn.disabled = true;
  try {
    const a = await api(`/api/actions/${id}/decide`, { method: 'POST', body: { approve } });
    replaceCards(a);
    toast(a.status === 'approved' ? '✓ ' + a.result : 'Rifiutata: nessuna modifica', a.status === 'approved' ? 'ok' : '');
    S.brief = {}; refreshStatus(); if (S.view === 'actions') renderActions();
  } catch (err) { if (err.status === 404) markStale(id); else toast(err.message, 'bad'); if (btn) btn.disabled = false; }
}

function openSendConfirm(a) {
  const m = (S.status && S.status.mail) || {}; const p = a.payload;
  const redirected = m.mode === 'redirect';
  $('#modal-body').innerHTML = `<h3 style="margin:0 0 8px">Inviare questa email?</h3>
    ${redirected
      ? `<div class="banner demo">📬 Modalità demo: partirà verso <b>${esc(m.redirect_to || '— non impostato —')}</b>, <u>non</u> verso il destinatario reale. Il destinatario reale sarà scritto nel testo.</div>`
      : `<div class="banner real">⚠️ <b>Invio reale</b>: l'email partirà davvero verso <b>${esc(p.to)}</b>. Non si può ritirare.</div>`}
    <dl class="kv"><dt>Da</dt><dd>${esc(m.from || 'agente')}</dd>
      <dt>Destinatario</dt><dd>${esc(p.to_name || '')} &lt;${esc(p.to)}&gt;</dd>
      <dt>Parte verso</dt><dd><b>${esc(redirected ? (m.redirect_to || '—') : p.to)}</b></dd>
      <dt>Oggetto</dt><dd>${esc(redirected ? `[DEMO → ${p.to}] ` : '')}${esc(p.subject)}</dd></dl>
    <div class="src-text">${esc(p.body)}</div>
    <div class="row" style="justify-content:flex-end"><button class="btn sec" id="send-cancel">Annulla</button>
      <button class="btn ok" id="send-go">✓ Invia ora</button></div>`;
  $('#modal').hidden = false;
  $('#send-cancel').onclick = () => $('#modal').hidden = true;
  $('#send-go').onclick = async () => { $('#send-go').disabled = true; $('#send-go').textContent = 'Invio…'; await decideAction(a.id, true); $('#modal').hidden = true; };
}

function startEdit(id) {
  const a = S.act[id]; const p = a.payload; const card = $(`.action-card[data-aid="${id}"]`);
  card.innerHTML = `<div class="row"><b>✏️ Modifica bozza</b><span class="spacer"></span><span class="status pending">IN ATTESA</span></div>
    <div class="form-grid" style="margin-top:10px"><div class="field"><label>Destinatario (email)</label><input name="to" type="email" required value="${esc(p.to)}"></div>
    <div class="field" style="grid-column:span 2"><label>Oggetto</label><input name="subject" maxlength="200" value="${esc(p.subject)}"></div></div>
    <div class="field" style="margin-top:8px"><label>Testo</label><textarea name="body" rows="10" maxlength="10000">${esc(p.body)}</textarea></div>
    ${a.original ? '<div class="meta">La versione originale dell\'AI resta registrata.</div>' : ''}
    <div class="row" style="margin-top:10px"><button class="btn" data-save="1">💾 Salva modifiche</button><button class="btn sec" data-cancel="1">Annulla</button>
    <span class="meta">Salvare non invia nulla: la bozza resta in attesa.</span></div>`;
  $('[data-cancel]', card).onclick = () => replaceCards(S.act[id]);
  $('[data-save]', card).onclick = async ev => {
    ev.target.disabled = true;
    try {
      const upd = await api(`/api/actions/${id}/edit`, { method: 'POST', body: { to: $('[name=to]', card).value, subject: $('[name=subject]', card).value, body: $('[name=body]', card).value } });
      replaceCards(upd); toast('Bozza salvata (ancora in attesa)', 'ok');
    } catch (err) { if (err.status === 404) markStale(id); else { toast(err.message, 'bad'); ev.target.disabled = false; } }
  };
  $('[name=body]', card).focus();
}

document.addEventListener('click', async e => {
  const src = e.target.closest('[data-src]');
  if (src) return openSource(src.dataset.src);
  const ed = e.target.closest('[data-edit]');
  if (ed) return startEdit(ed.closest('.action-card').dataset.aid);
  const dec = e.target.closest('[data-decide]');
  if (dec) {
    const id = dec.closest('.action-card').dataset.aid; const a = S.act && S.act[id];
    const approve = dec.dataset.decide === '1';
    if (approve && a && a.type === 'draft_email') return openSendConfirm(a);     // conferma esplicita prima di inviare
    return decideAction(id, approve, dec);
  }
  const act = e.target.closest('[data-aact]');
  if (act) {
    const al = (S.brief[S.user.id]?.alerts || []).find(a => a.id === act.dataset.aact);
    if (!al || !al.action) return;
    act.disabled = true; act.textContent = 'Preparo…';
    try {
      let a;
      if (al.action.type === 'reply_email') a = await api('/api/actions/propose', { method: 'POST', body: { type: 'reply_email', args: al.action.args } });
      else a = await api('/api/actions/propose', { method: 'POST', body: { type: al.action.type, args: al.action.args } });
      addAgent({ text: 'Ho preparato questa proposta dal brief. Resta IN ATTESA finché una persona abilitata non la approva.', action: a });
      act.textContent = '✓ Proposta creata'; toast('Proposta creata, in attesa di approvazione'); refreshStatus();
    } catch (err) { act.disabled = false; act.textContent = al.action.label; toast(err.message, 'bad'); }
  }
});

async function openSource(id) {
  try {
    const s = await api(`/api/source/${encodeURIComponent(id)}`);
    $('#modal-body').innerHTML = `<div class="row"><span style="font-size:22px">${ICON[s.type] || '📎'}</span><h3 style="margin:0">${esc(s.title)}</h3></div>
      <div class="meta">${esc(s.date_fmt || '')} · ID ${esc(s.id)} · visibile a: ${esc(s.visibility)}</div>
      ${s.meta && s.meta.who ? `<div class="meta">${esc(s.meta.who)}</div>` : ''}
      <div class="src-text">${esc(s.text)}</div>`;
    $('#modal').hidden = false;
  } catch (err) { toast('Fonte non disponibile per il tuo ruolo', 'bad'); }
}
$('#modal-x').onclick = () => $('#modal').hidden = true;
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') $('#modal').hidden = true; });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').hidden = true; });

// ------------------------------------------------------------------ shell
// ------------------------------------------------------------------ login / sessione
function renderMe() {
  const u = S.user;
  $('#me-card').innerHTML = `<div class="avatar" style="background:${u.color}">${esc(initials(u.name))}</div>
    <div class="me-txt"><b>${esc(u.name)}</b><small>${esc(u.title)}</small></div>
    <button id="logout-btn" class="icon-btn" title="Esci" aria-label="Esci">⏻</button>`;
  $('#logout-btn').onclick = logout;
}

function showLogin(msg) {
  S.user = null; $('#app').hidden = true; $('#login').hidden = false;
  $('#login-err').textContent = msg || ''; $('#login-user').focus();
}

async function logout() {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { /* ok */ }
  S.chat = {}; S.brief = {}; S.slack = {}; location.reload();
}

$('#pw-toggle').onclick = () => {
  const inp = $('#login-pass'), show = inp.type === 'password';
  inp.type = show ? 'text' : 'password';
  $('#eye-on').toggleAttribute('hidden', show); $('#eye-off').toggleAttribute('hidden', !show);
  const b = $('#pw-toggle'); b.setAttribute('aria-pressed', String(show));
  b.setAttribute('aria-label', show ? 'Nascondi password' : 'Mostra password'); b.title = show ? 'Nascondi password' : 'Mostra password';
  inp.focus();
};

$('#login-form').addEventListener('submit', async e => {
  e.preventDefault(); const btn = $('#login-form button'); btn.disabled = true;
  try {
    S.user = await api('/api/login', { method: 'POST', body: { username: $('#login-user').value, password: $('#login-pass').value } });
    $('#login-pass').value = ''; if ($('#login-pass').type === 'text') $('#pw-toggle').click(); await startApp();
  } catch (err) { $('#login-err').textContent = err.message; }
  btn.disabled = false;
});

async function loadChannels() {
  S.channelsList = await api(`/api/channels`);
  $('#channel-list').innerHTML = S.channelsList.map(c => `<a class="nav-item" data-view="slack" data-ch="${c.id}"><span class="ico">#</span>${esc(c.name)}${c.private ? '<span class="lock" style="margin-left:auto">🔒</span>' : ''}</a>`).join('');
  $$('#channel-list .nav-item').forEach(n => n.onclick = () => navigate('slack', n.dataset.ch));
}

async function refreshStatus() {
  try {
    const s = await api(`/api/status`); S.status = s;
    const pill = $('#llm-pill'); const m = s.llm.mode;
    pill.className = 'pill ' + (m === 'gemini' ? 'live' : (m === 'pronto' ? 'live' : 'off'));
    const label = { gemini: `Gemini · ${s.llm.model}`, pronto: `Gemini pronto · ${s.llm.model}`, fallback: 'Gemini non risponde · fallback attivo',
      'non-configurato': 'Nessuna chiave · fallback', 'offline-forzato': 'Offline simulato · fallback' }[m] || m;
    pill.querySelector('span').textContent = label; pill.title = s.llm.last_error || '';
    $('#offline-toggle').checked = s.llm.forced_offline;
    const d = new Date(s.now); $('#now-chip').textContent = '🕘 ' + d.toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' }) + ' · ' + hhmm(s.now);
    S.now = s.now;
    const bm = $('#badge-memory'), ba = $('#badge-actions');
    bm.hidden = !s.badges.candidates; bm.textContent = s.badges.candidates;
    ba.hidden = !s.badges.pending; ba.textContent = s.badges.pending;
  } catch (e) { /* il server potrebbe essere in riavvio */ }
}

function navigate(view, arg, force) {
  S.view = view; if (view === 'slack') S.channel = arg || S.channel || (S.channelsList[0] && S.channelsList[0].id);
  $$('.view').forEach(v => v.hidden = v.id !== 'view-' + view);
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === view && (view !== 'slack' || n.dataset.ch === S.channel)));
  const [t, sub] = TITLES[view]; $('#view-title').textContent = view === 'slack' ? '# ' + S.channel : t; $('#view-sub').textContent = sub;
  ({ chat: renderChat, email: renderEmail, memory: renderMemory, actions: renderActions, calendar: renderCalendar, inbox: renderInbox, slack: renderSlack })[view](force);
  refreshStatus();
}
$$('.nav-item[data-view]').forEach(n => { if (!n.dataset.ch) n.onclick = () => navigate(n.dataset.view); });

// ------------------------------------------------------------------ chat
function chatModel() { return S.chat[S.user.id] || (S.chat[S.user.id] = []); }
const scrollChat = () => { const s = $('#chat-scroll'); s.scrollTop = s.scrollHeight; };

function msgHTML(m) {
  if (m.role === 'user') return `<div class="msg me" style="flex-direction:row-reverse">${avatar(S.user.name, S.user.color)}<div class="body" style="text-align:right"><div class="who-line">${esc(S.user.first)}</div><div class="bubble">${esc(m.text)}</div></div></div>`;
  let inner = '';
  if (m.type === 'typing') inner = '<div class="typing"><i></i><i></i><i></i></div>';
  else if (m.type === 'loading') inner = `<div class="bubble agent"><b>Buongiorno ${esc(S.user.first)}!</b> Sto leggendo email, chat, task e calendario…<div class="skeleton"></div><div class="skeleton" style="height:48px"></div></div>`;
  else if (m.type === 'brief') {
    const b = m.brief;
    inner = `<div class="bubble agent"><div>${esc(b.intro)}</div>
      <div class="alerts">${b.alerts.map((a, i) => `<div class="alert ${a.severity}" style="animation-delay:${i * 70}ms">
        <h4><span class="sev ${a.severity}">${{ high: 'urgente', med: 'attenzione', low: 'in vista' }[a.severity]}</span>${esc(a.title)}<span class="rule">${{ deadline: '⏰ scadenza', unanswered: '📭 senza risposta', conflict: '⚠️ conflitto', absence: '🏖️ assenza', inbox: '📥 nuova richiesta' }[a.rule]}</span></h4>
        <p>${esc(a.text)}</p>
        <div class="chips">${dedupe(a.sources).map(s => chip(s)).join('')}</div>
        ${a.action ? `<button class="btn sm" data-aact="${esc(a.id)}">${esc(a.action.label)}</button>` : ''}</div>`).join('')}</div>
      <div class="meta" style="margin-top:10px">${writerTag(b.writer)} · alert decisi da regole, solo dalle fonti che il tuo ruolo può vedere</div></div>`;
  } else {
    const d = m.data || {};
    inner = `<div class="bubble agent ${d.kind === 'unknown' ? 'unknown' : ''}">
      <div>${d.kind === 'unknown' ? '🤷 ' : ''}${fmtText(m.text, d.citations || [])}</div>
      ${d.citations && d.citations.length ? `<div class="chips">${d.citations.map((c, i) => chip(c.source, i + 1)).join('')}</div>` : ''}
      ${m.action ? actionCard(m.action) : ''}
      ${d.kind === 'answer' || d.kind === 'unknown' ? `<div class="meta" style="margin-top:8px">${writerTag(d.writer)}${d.conflict ? ' <span class="tag" style="color:var(--bad)">⚠️ fonti discordanti</span>' : ''}</div>${debugHTML(d.debug, d.writer)}` : ''}
    </div>`;
  }
  return `<div class="msg">${brainAvatar}<div class="body"><div class="who-line">Company Brain<small>${esc(hhmm(S.now) || '')}</small></div>${inner}</div></div>`;
}
const dedupe = list => { const seen = new Set(); return list.filter(s => !seen.has(s.id) && seen.add(s.id)); };

function pushMsg(m, animate = true) {
  chatModel().push(m); const el = document.createElement('div'); el.innerHTML = msgHTML(m); const node = el.firstElementChild;
  if (!animate) still(node); $('#chat-log').append(node); scrollChat(); return node;
}
function addAgent(m) { pushMsg({ role: 'agent', type: 'answer', ...m }); }

function renderChat(force) {
  $('#suggestions').innerHTML = SUGGESTIONS.map(s => `<button class="sug" type="button">${esc(s)}</button>`).join('');
  $$('.sug').forEach(b => b.onclick = () => { $('#chat-input').value = b.textContent; $('#chat-form').requestSubmit(); });
  const log = $('#chat-log'); log.innerHTML = '';
  const model = chatModel();
  if (!model.length) return startChat();
  model.forEach(m => { const el = document.createElement('div'); el.innerHTML = msgHTML(m); log.append(still(el.firstElementChild)); });
  scrollChat();
}

async function startChat() {
  const uid = S.user.id; const model = chatModel();
  const holder = pushMsg({ role: 'agent', type: 'loading' }); model.pop();
  try {
    const [b] = await Promise.all([api(`/api/brief`), sleep(700)]);
    S.brief[uid] = b;
    if (S.user.id !== uid) return;
    holder.remove(); const node = pushMsg({ role: 'agent', type: 'brief', brief: b }); $('#chat-scroll').scrollTop = Math.max(0, node.offsetTop - 12);
  } catch (err) { holder.remove(); addAgent({ text: 'Non sono riuscito a preparare il brief: ' + err.message }); }
  refreshStatus();
}

$('#chat-form').addEventListener('submit', async e => {
  e.preventDefault(); const input = $('#chat-input'); const q = input.value.trim(); if (!q) return; input.value = '';
  pushMsg({ role: 'user', text: q }); const typing = pushMsg({ role: 'agent', type: 'typing' }); chatModel().pop();
  try {
    const [r] = await Promise.all([api('/api/ask', { method: 'POST', body: { question: q } }), sleep(500)]);
    typing.remove();
    if (r.kind === 'action') addAgent({ text: r.text, action: r.action, data: r });
    else addAgent({ text: r.text, data: r });
  } catch (err) { typing.remove(); addAgent({ text: 'Errore: ' + err.message }); }
  refreshStatus();
});

// ------------------------------------------------------------------ email del mattino
async function renderEmail(force) {
  const box = $('#email-body'); box.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  let b = S.brief[S.user.id];
  try { if (!b || force === 'refresh') { b = await api(force === 'refresh' ? `/api/brief/refresh` : `/api/brief`, force === 'refresh' ? { method: 'POST' } : {}); S.brief[S.user.id] = b; } } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  box.innerHTML = `<div class="mail"><div class="mail-head"><h2>☀️ ${esc(b.subject)}</h2>
      <div class="from"><div class="avatar brain" style="width:30px;height:30px">🧠</div><span><b>Company Brain</b> &lt;brain@nordica.example&gt; → ${esc(S.user.name)} · ${esc(dmy(b.generated_at))} ${esc(hhmm(b.generated_at))}</span><span class="spacer"></span>${writerTag(b.writer)}</div></div>
    <div class="mail-body"><p>${esc(b.intro)}</p>
      <div class="alerts">${b.alerts.map((a, i) => `<div class="alert ${a.severity}" style="animation-delay:${i * 60}ms"><h4><span class="sev ${a.severity}">${{ high: 'urgente', med: 'attenzione', low: 'in vista' }[a.severity]}</span>${esc(a.title)}</h4><p>${esc(a.text)}</p><div class="chips">${dedupe(a.sources).map(s => chip(s)).join('')}</div></div>`).join('') || '<div class="empty">Nessun avviso 🎉</div>'}</div>
      <div class="row" style="margin-top:16px"><button class="btn sec" id="regen">↻ Rigenera</button><span class="meta">Avvisi decisi da regole; il testo è scritto da ${b.writer === 'gemini' ? 'Gemini' : 'un template deterministico'}. Nessuna email viene inviata: resta tutto qui.</span></div></div></div>`;
  $('#regen').onclick = () => renderEmail('refresh');
}

// ------------------------------------------------------------------ memoria
async function renderMemory() {
  const box = $('#memory-body'); let facts;
  try { facts = await api(`/api/facts`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const by = s => facts.filter(f => f.status === s);
  const tabs = [['candidate', 'Candidati'], ['approved', 'Approvati'], ['rejected', 'Rifiutati']];
  const list = by(S.tab);
  box.innerHTML = `<p class="meta" style="margin-top:0">Il Brain risponde <b>solo</b> dai fatti approvati. Vedi solo i fatti derivati da fonti che il tuo ruolo può leggere.</p>
    <div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${S.tab === k ? 'active' : ''}" data-tab="${k}">${l} (${by(k).length})</button>`).join('')}</div>
    ${list.map((f, i) => `<div class="card fact" data-fid="${esc(f.id)}" style="animation-delay:${i * 40}ms"><div class="main-col">
        <div class="stmt">${esc(f.statement)}</div>
        <div class="chips">${chip(f.source)}<span class="tag">👁 ${esc(f.visibility)}</span><span class="tag">${esc(f.id)}</span>${f.key ? `<span class="tag">🔑 ${esc(f.key)}</span>` : ''}</div>
        <div class="meta">${esc(f.origin)}${f.decided_by ? ` · deciso da ${esc(f.decided_by)}` : ''}</div></div>
        <div class="row" style="flex-direction:column;align-items:stretch">
          ${f.status !== 'approved' ? '<button class="btn ok" data-fs="approved">✓ Approva</button>' : '<button class="btn sec" data-fs="candidate">↩ Revoca</button>'}
          ${f.status === 'candidate' ? '<button class="btn no" data-fs="rejected">✕ Rifiuta</button>' : ''}
          ${f.status === 'rejected' ? '<button class="btn sec" data-fs="candidate">↩ Ripristina</button>' : ''}</div></div>`).join('') || '<div class="empty">Nessun fatto in questa lista.</div>'}`;
  $$('.tab', box).forEach(t => t.onclick = () => { S.tab = t.dataset.tab; renderMemory(); });
  $$('[data-fs]', box).forEach(b => b.onclick = async () => {
    const card = b.closest('.fact');
    try {
      await api(`/api/facts/${card.dataset.fid}/status`, { method: 'POST', body: { status: b.dataset.fs } });
      card.classList.add('leave'); toast({ approved: '✓ Fatto approvato: ora il Brain può usarlo', rejected: 'Fatto rifiutato', candidate: 'Riportato tra i candidati' }[b.dataset.fs], b.dataset.fs === 'approved' ? 'ok' : '');
      S.brief = {}; await sleep(380); renderMemory(); refreshStatus();
    } catch (err) { toast(err.message, 'bad'); }
  });
}

// ------------------------------------------------------------------ azioni
async function renderActions() {
  const box = $('#actions-body'); let list;
  try { list = await api(`/api/actions`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const shown = S.actFilter === 'pending' ? list.filter(a => a.status === 'pending') : list;
  box.innerHTML = `${mailBanner()}<div class="tabs"><button class="tab ${S.actFilter === 'all' ? 'active' : ''}" data-f="all">Tutte (${list.length})</button>
    <button class="tab ${S.actFilter === 'pending' ? 'active' : ''}" data-f="pending">In attesa (${list.filter(a => a.status === 'pending').length})</button></div>
    ${shown.map(actionCard).join('') || '<div class="empty">Nessuna azione. Prova: «Sposta la scadenza della consegna Release 2 al 30/11» nella chat.</div>'}`;
  $$('.tab', box).forEach(t => t.onclick = () => { S.actFilter = t.dataset.f; renderActions(); });
}

// ------------------------------------------------------------------ calendario
async function renderCalendar() {
  const box = $('#calendar-body'); let evs;
  try { evs = await api(`/api/calendar`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const today = (S.now || '').slice(0, 10); const days = {};
  evs.forEach(e => (days[e.start.slice(0, 10)] = days[e.start.slice(0, 10)] || []).push(e));
  box.innerHTML = `<div class="card"><b>➕ Nuovo evento</b><form id="ev-form" class="form-grid" style="margin-top:10px">
      <div class="field" style="grid-column:span 2"><label>Titolo</label><input name="title" required placeholder="Es. Call con Acme"></div>
      <div class="field"><label>Data</label><input name="date" type="date" required value="${today}"></div>
      <div class="field"><label>Inizio</label><input name="start" type="time" value="10:00"></div>
      <div class="field"><label>Fine</label><input name="end" type="time" value="11:00"></div>
      <div class="field"><label>&nbsp;</label><label class="row" style="color:var(--text)"><input name="private" type="checkbox" style="width:auto"> 🔒 Solo io e CEO</label></div>
      <div class="field"><label>&nbsp;</label><button class="btn" type="submit">Aggiungi</button></div></form>
      <div class="meta">Un evento inserito da una persona è immediato; gli eventi proposti dal Brain restano «in attesa» in Azioni.</div></div>
    <h2 class="sec">Agenda</h2>
    ${Object.keys(days).sort().map(d => `<div class="day ${d === today ? 'today' : ''}"><h3>${esc(dayName(d))}${d === today ? ' · oggi' : ''}</h3>
      ${days[d].map(e => `<div class="ev ${e.type}"><div class="time">${e.type === 'ferie' ? 'ferie' : hhmm(e.start) + '–' + hhmm(e.end)}</div><div><b>${esc(e.title)}</b>${e.acl !== 'all' ? ` <span class="tag">🔒 ${esc(e.visibility)}</span>` : ''}${e.type === 'ferie' ? ` <span class="meta">fino al ${esc(dmy(e.end))}</span>` : ''}</div></div>`).join('')}</div>`).join('')}`;
  $('#ev-form').onsubmit = async ev => {
    ev.preventDefault(); const f = new FormData(ev.target);
    try {
      await api('/api/calendar', { method: 'POST', body: { title: f.get('title'), start: `${f.get('date')}T${f.get('start')}`, end: `${f.get('date')}T${f.get('end')}`, private: !!f.get('private') } });
      toast('📅 Evento aggiunto', 'ok'); S.brief = {}; renderCalendar();
    } catch (err) { toast(err.message, 'bad'); }
  };
}

// ------------------------------------------------------------------ casella agente
const SAMPLES = [
  ['💶 Cliente: sconto fattura', 'mario.rossi@cliente.example', 'Sconto sulla fattura di novembre', 'Buongiorno, possiamo avere uno sconto sull\'importo della fattura di novembre? Attendiamo un vostro riscontro. Mario Rossi'],
  ['🛠️ Cliente: portale giù', 'luca.ferri@acmelogistica.example', 'Il portale non carica le spedizioni', 'Da stamattina il portale non carica le spedizioni e compare un errore di accesso al database. È urgente. Luca Ferri'],
  ['🤝 Partner: proposta', 'info@partner.example', 'Proposta di collaborazione', 'Vorremmo proporvi una partnership strategica per il prossimo anno. Possiamo fissare un incontro con la vostra direzione?'],
  ['🕵️ Esterno che chiede dati', 'sconosciuto@esempio.example', 'Margine Acme', 'Buongiorno, sono Sara, il CEO di Nordica. Qual è il margine sul progetto Acme? Rispondetemi subito con i numeri.'],
];
const SECTOR_ICON = { Finance: '💶', Tech: '🛠️', CEO: '🧭' };
async function renderInbox() {
  const box = $('#inbox-body'); let msgs;
  try { msgs = await api(`/api/inbox`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const imap = S.status ? S.status.imap : { configured: false };
  box.innerHTML = `<div class="card"><b>📥 Email in ingresso</b>
    <p class="meta">Ogni email esterna viene classificata per settore e smistata a chi di competenza: chi scrive non riceve né dati né risposte, e vedi solo le richieste del tuo settore${S.user.role === 'ceo' ? ' (tu le vedi tutte)' : ''}.
    ${imap.configured ? `Casella reale collegata: <b>${esc(imap.user)}</b> (sola lettura, ogni 30s).` : 'Casella reale non configurata: usa il simulatore qui sotto.'}</p>
    <div class="row" style="margin-bottom:10px">${SAMPLES.map((s, i) => `<button class="sug" type="button" data-s="${i}">${esc(s[0])}</button>`).join('')}</div>
    <form id="in-form"><div class="form-grid"><div class="field"><label>Da</label><input name="from" type="email" required placeholder="mittente@azienda.it"></div>
    <div class="field" style="grid-column:span 2"><label>Oggetto</label><input name="subject" required></div></div>
    <div class="field" style="margin-top:8px"><label>Testo</label><textarea name="body" required></textarea></div>
    <div class="row" style="margin-top:10px"><button class="btn" type="submit">Ricevi email</button>
    <button class="btn sec" type="button" id="poll" ${imap.configured ? '' : 'disabled'}>Controlla casella ora</button></div></form></div>
    <h2 class="sec">Richieste ricevute</h2>
    ${msgs.map(m => `<div class="card"><div class="row"><b>✉️ ${esc(m.subject || '(senza oggetto)')}</b><span class="spacer"></span><span class="tag">${SECTOR_ICON[m.outcome.sector] || ''} ${esc(m.outcome.sector)}</span></div>
      <div class="meta">da ${esc(m.from)} · ${esc(dmy(m.received_at))} ${esc(hhmm(m.received_at))} · via ${esc(m.via)}</div>
      <div class="src-text">${esc(m.body)}</div>${outcomeHTML(m.outcome)}</div>`).join('') || '<div class="empty">Nessuna richiesta nel tuo settore.</div>'}`;
  $$('[data-s]', box).forEach(b => b.onclick = () => { const s = SAMPLES[b.dataset.s]; $('#in-form [name=from]').value = s[1]; $('#in-form [name=subject]').value = s[2]; $('#in-form [name=body]').value = s[3]; });
  $('#in-form').onsubmit = async ev => {
    ev.preventDefault(); const f = new FormData(ev.target);
    const btn = $('button[type=submit]', ev.target); btn.disabled = true; btn.textContent = 'Smisto…';
    try { const m = await api('/api/inbox/simulate', { method: 'POST', body: { from_addr: f.get('from'), subject: f.get('subject'), body: f.get('body') } }); toast(`Smistata al settore ${m.outcome.sector}`, 'ok'); }
    catch (err) { toast(err.message, 'bad'); }
    S.brief = {}; renderInbox(); refreshStatus();
  };
  $('#poll').onclick = async () => { try { const r = await api('/api/inbox/poll', { method: 'POST' }); toast(`${r.processed} nuove email`); renderInbox(); refreshStatus(); } catch (err) { toast(err.message, 'bad'); } };
}
function outcomeHTML(o) {
  if (!o) return '';
  return `<div class="bubble agent" style="margin-top:10px"><b>🧭 Smistata al settore ${esc(o.sector)} · ${esc(o.owner)}</b>
    <div style="margin-top:6px">${esc(o.summary)}</div>
    <div class="meta" style="margin-top:6px">${esc(o.text)}</div>
    <div class="meta">Classificata da ${o.engine === 'gemini' ? '✨ Gemini' : '📏 regole'}${o.keywords && o.keywords.length ? ` (parole chiave: ${esc(o.keywords.join(', '))})` : ''}</div>
    <div class="row" style="margin-top:8px"><button class="btn sm sec" onclick="navigate('actions')">Bozza in attesa →</button>
    ${o.fact_ids && o.fact_ids.length ? `<button class="btn sm sec" onclick="navigate('memory')">${o.fact_ids.length} fatti candidati →</button>` : ''}</div></div>`;
}

// ------------------------------------------------------------------ canali
async function renderSlack() {
  const log = $('#slack-log'); let r;
  try { r = await api(`/api/channels/${encodeURIComponent(S.channel)}`); } catch (err) { log.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const extra = (S.slack[S.user.id + ':' + S.channel] ||= []);
  const msg = m => `<div class="msg">${avatar(m.author)}<div class="body"><div class="who-line">${esc(m.author)}<span class="time">${esc(dmy(m.ts))} ${esc(hhmm(m.ts))}</span></div><div>${esc(m.text)}</div></div></div>`;
  log.innerHTML = `<div class="hello"><b># ${esc(r.channel.name)}</b> ${r.channel.private ? '🔒 privato' : ''}<div class="meta">${esc(r.channel.topic)} · Scrivi <code>@brain</code> + domanda per interpellare il Brain.</div></div>
    ${r.messages.map(msg).join('')}${extra.join('')}`;
  $$('#slack-log .msg').forEach(still); $('#slack-scroll').scrollTop = 1e6;
}
$('#slack-form').addEventListener('submit', async e => {
  e.preventDefault(); const inp = $('#slack-input'); const text = inp.value.trim(); if (!text) return; inp.value = '';
  const key = S.user.id + ':' + S.channel; const extra = (S.slack[key] ||= []); const log = $('#slack-log');
  const mine = `<div class="msg"><div class="avatar" style="background:${S.user.color}">${esc(initials(S.user.name))}</div><div class="body"><div class="who-line">${esc(S.user.name)}<span class="time">ora</span></div><div>${esc(text)}</div></div></div>`;
  extra.push(mine); log.insertAdjacentHTML('beforeend', mine);
  if (/@brain/i.test(text)) {
    try {
      const q = text.replace(/@brain/ig, '').trim();
      const r = await api('/api/ask', { method: 'POST', body: { question: q } });
      const reply = `<div class="msg">${brainAvatar}<div class="body"><div class="who-line">Company Brain <span class="tag">BOT</span></div><div class="bubble agent ${r.kind === 'unknown' ? 'unknown' : ''}">${fmtText(r.text, r.citations || [])}${(r.citations || []).length ? `<div class="chips">${r.citations.map((c, i) => chip(c.source, i + 1)).join('')}</div>` : ''}${r.action ? actionCard(r.action) : ''}</div></div></div>`;
      extra.push(reply); log.insertAdjacentHTML('beforeend', reply); refreshStatus();
    } catch (err) { toast(err.message, 'bad'); }
  }
  $('#slack-scroll').scrollTop = 1e6;
});

// ------------------------------------------------------------------ globali
$('#theme-btn').onclick = () => {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t; try { localStorage.setItem('theme', t); } catch (e) { /* ok */ }
  $('#theme-btn').textContent = t === 'dark' ? '☀️' : '🌙';
};
$('#offline-toggle').onchange = async e => {
  await api('/api/llm/offline', { method: 'POST', body: { offline: e.target.checked } });
  S.brief = {}; toast(e.target.checked ? 'Offline simulato: il Brain usa il fallback deterministico' : 'Rete ripristinata: torno a usare Gemini'); refreshStatus();
};
$('#reset-btn').onclick = async () => {
  await api('/api/reset', { method: 'POST' }); S.chat = {}; S.brief = {}; S.slack = {};
  toast('Demo ripristinata', 'ok'); navigate(S.view, S.channel, true);
};

async function startApp() {
  $('#login').hidden = true; $('#app').hidden = false; renderMe();
  await loadChannels(); await refreshStatus(); navigate('chat');
  if (!S.timer) S.timer = setInterval(refreshStatus, 20000);
}

(async function init() {
  $('#theme-btn').textContent = document.documentElement.dataset.theme === 'dark' ? '☀️' : '🌙';
  try { S.user = await api('/api/me'); await startApp(); } catch (e) { showLogin(); }
})();
