'use strict';
const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let j = null; try { j = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((j && j.detail) || `Errore ${r.status}`);
  return j;
}

const S = { users: [], user: null, view: 'chat', chat: {}, brief: {}, tab: 'candidate', actFilter: 'all', channel: null,
  status: null, now: null, slack: {} };
const uq = () => 'user=' + encodeURIComponent(S.user.id);
const ICON = { email: '✉️', slack: '#️⃣', task: '✅', doc: '📄', event: '📅' };
const TITLES = {
  chat: ['Chiedi al Brain', 'Risponde solo da fatti approvati, nei limiti dei tuoi permessi'],
  email: ['Email del mattino', 'Il brief generato dalle regole, scritto in forma di email'],
  memory: ['Memoria', 'I fatti estratti sono candidati: decidi tu cosa il Brain può usare'],
  actions: ['Azioni', 'Nulla accade finché una persona abilitata non approva'],
  calendar: ['Calendario', 'Eventi visibili al tuo ruolo'],
  inbox: ['Casella dell\'agente', 'Email in ingresso (sola lettura): il ruolo si dichiara nell\'oggetto, es. [Giulia]'],
  slack: ['Slack', 'Mock dei canali: vedi solo quelli del tuo ruolo'],
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
function actionCard(a) {
  const can = a.approvers && a.approvers.includes(S.user.role);
  const st = { pending: 'IN ATTESA', approved: 'APPROVATA', rejected: 'RIFIUTATA' }[a.status];
  const typeIco = { change_deadline: '🗓️', draft_email: '✉️', create_event: '📅' }[a.type] || '⚡';
  return `<div class="card action-card" data-aid="${esc(a.id)}">
    <div class="row"><b>${typeIco} ${esc(a.title)}</b><span class="spacer"></span><span class="status ${a.status}">${st}</span></div>
    <dl class="kv">${(a.details || []).map(d => `<dt>${esc(d.label)}</dt><dd>${esc(d.value)}</dd>`).join('')}</dl>
    <div class="meta">Proposta da ${esc(a.proposed_by)} · via ${esc(a.via)} · ${esc(a.id)}${a.decided_by ? ` · decisa da ${esc(a.decided_by)}` : ''}</div>
    ${a.status === 'pending'
      ? `<div class="row" style="margin-top:10px">
          <button class="btn ok" data-decide="1" ${can ? '' : 'disabled'}>✓ Approva</button>
          <button class="btn no" data-decide="0" ${can ? '' : 'disabled'}>✕ Rifiuta</button>
          ${can ? '' : `<span class="meta">Può decidere solo: ${a.approvers.join(', ')}</span>`}</div>`
      : `<div class="meta" style="margin-top:8px">→ ${esc(a.result || '')}</div>`}
  </div>`;
}

document.addEventListener('click', async e => {
  const src = e.target.closest('[data-src]');
  if (src) return openSource(src.dataset.src);
  const dec = e.target.closest('[data-decide]');
  if (dec) {
    const card = dec.closest('.action-card'); const id = card.dataset.aid;
    try {
      const a = await api(`/api/actions/${id}/decide`, { method: 'POST', body: { user: S.user.id, approve: dec.dataset.decide === '1' } });
      $$(`.action-card[data-aid="${id}"]`).forEach(c => { c.outerHTML = actionCard(a); });
      toast(a.status === 'approved' ? '✓ Approvata: ' + a.result : 'Rifiutata: nessuna modifica', a.status === 'approved' ? 'ok' : '');
      S.brief = {}; refreshStatus(); if (S.view === 'actions') renderActions();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  const act = e.target.closest('[data-aact]');
  if (act) {
    const al = (S.brief[S.user.id]?.alerts || []).find(a => a.id === act.dataset.aact);
    if (!al || !al.action) return;
    act.disabled = true; act.textContent = 'Preparo…';
    try {
      let a;
      if (al.action.type === 'reply_email') a = await api('/api/actions/propose', { method: 'POST', body: { user: S.user.id, type: 'reply_email', args: al.action.args } });
      else a = await api('/api/actions/propose', { method: 'POST', body: { user: S.user.id, type: al.action.type, args: al.action.args } });
      addAgent({ text: 'Ho preparato questa proposta dal brief. Resta IN ATTESA finché una persona abilitata non la approva.', action: a });
      act.textContent = '✓ Proposta creata'; toast('Proposta creata, in attesa di approvazione'); refreshStatus();
    } catch (err) { act.disabled = false; act.textContent = al.action.label; toast(err.message, 'bad'); }
  }
});

async function openSource(id) {
  try {
    const s = await api(`/api/source/${encodeURIComponent(id)}?${uq()}`);
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
function renderUsers() {
  $('#user-switch').innerHTML = S.users.map(u => `<button class="user-btn ${u.id === S.user.id ? 'active' : ''}" data-uid="${u.id}">
    <div class="avatar" style="background:${u.color}">${esc(initials(u.name))}</div><div><b>${esc(u.name)}</b><small>${esc(u.title)}</small></div></button>`).join('');
  $$('.user-btn').forEach(b => b.onclick = () => switchUser(b.dataset.uid));
}

async function switchUser(id) {
  if (S.user && S.user.id === id) return;
  S.user = S.users.find(u => u.id === id); renderUsers();
  toast(`Ora sei ${S.user.name} (${S.user.title})`);
  await loadChannels(); await refreshStatus();
  if (S.view === 'slack' && !S.channelsList.find(c => c.id === S.channel)) S.view = 'chat';
  navigate(S.view, S.channel, true);
}

async function loadChannels() {
  S.channelsList = await api(`/api/channels?${uq()}`);
  $('#channel-list').innerHTML = S.channelsList.map(c => `<a class="nav-item" data-view="slack" data-ch="${c.id}"><span class="ico">#</span>${esc(c.name)}${c.private ? '<span class="lock" style="margin-left:auto">🔒</span>' : ''}</a>`).join('');
  $$('#channel-list .nav-item').forEach(n => n.onclick = () => navigate('slack', n.dataset.ch));
}

async function refreshStatus() {
  try {
    const s = await api(`/api/status?${uq()}`); S.status = s;
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
        <h4><span class="sev ${a.severity}">${{ high: 'urgente', med: 'attenzione', low: 'in vista' }[a.severity]}</span>${esc(a.title)}<span class="rule">${{ deadline: '⏰ scadenza', unanswered: '📭 senza risposta', conflict: '⚠️ conflitto', absence: '🏖️ assenza' }[a.rule]}</span></h4>
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
    const [b] = await Promise.all([api(`/api/brief?${uq()}`), sleep(700)]);
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
    const [r] = await Promise.all([api('/api/ask', { method: 'POST', body: { user: S.user.id, question: q } }), sleep(500)]);
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
  try { if (!b || force === 'refresh') { b = await api(force === 'refresh' ? `/api/brief/refresh?${uq()}` : `/api/brief?${uq()}`, force === 'refresh' ? { method: 'POST' } : {}); S.brief[S.user.id] = b; } } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
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
  try { facts = await api(`/api/facts?${uq()}`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
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
      await api(`/api/facts/${card.dataset.fid}/status`, { method: 'POST', body: { user: S.user.id, status: b.dataset.fs } });
      card.classList.add('leave'); toast({ approved: '✓ Fatto approvato: ora il Brain può usarlo', rejected: 'Fatto rifiutato', candidate: 'Riportato tra i candidati' }[b.dataset.fs], b.dataset.fs === 'approved' ? 'ok' : '');
      S.brief = {}; await sleep(380); renderMemory(); refreshStatus();
    } catch (err) { toast(err.message, 'bad'); }
  });
}

// ------------------------------------------------------------------ azioni
async function renderActions() {
  const box = $('#actions-body'); let list;
  try { list = await api(`/api/actions?${uq()}`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const shown = S.actFilter === 'pending' ? list.filter(a => a.status === 'pending') : list;
  box.innerHTML = `<div class="tabs"><button class="tab ${S.actFilter === 'all' ? 'active' : ''}" data-f="all">Tutte (${list.length})</button>
    <button class="tab ${S.actFilter === 'pending' ? 'active' : ''}" data-f="pending">In attesa (${list.filter(a => a.status === 'pending').length})</button></div>
    ${shown.map(actionCard).join('') || '<div class="empty">Nessuna azione. Prova: «Sposta la scadenza della consegna Release 2 al 30/11» nella chat.</div>'}`;
  $$('.tab', box).forEach(t => t.onclick = () => { S.actFilter = t.dataset.f; renderActions(); });
}

// ------------------------------------------------------------------ calendario
async function renderCalendar() {
  const box = $('#calendar-body'); let evs;
  try { evs = await api(`/api/calendar?${uq()}`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
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
      await api('/api/calendar', { method: 'POST', body: { user: S.user.id, title: f.get('title'), start: `${f.get('date')}T${f.get('start')}`, end: `${f.get('date')}T${f.get('end')}`, private: !!f.get('private') } });
      toast('📅 Evento aggiunto', 'ok'); S.brief = {}; renderCalendar();
    } catch (err) { toast(err.message, 'bad'); }
  };
}

// ------------------------------------------------------------------ casella agente
const SAMPLES = [
  ['❓ Domanda (Giulia)', '[Giulia] Margine Acme', 'Qual è il margine su Acme?'],
  ['❓ Stessa domanda (Marco)', '[Marco] Margine Acme', 'Qual è il margine su Acme?'],
  ['⚡ Azione (Marco)', '[Marco] Scadenza consegna', 'Per favore sposta la scadenza della consegna Release 2 al 30/11'],
  ['📝 Informazione (Sara)', '[Sara] Aggiornamento budget', 'Il budget marketing del primo trimestre è di 12.000 € approvato oggi.'],
  ['🚫 Senza tag', 'Domanda urgente', 'Qual è il margine su Acme?'],
];
async function renderInbox() {
  const box = $('#inbox-body'); let msgs;
  try { msgs = await api(`/api/inbox?${uq()}`); } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const imap = S.status ? S.status.imap : { configured: false };
  box.innerHTML = `<div class="card"><b>📥 Simulatore casella</b>
    <p class="meta">La casella reale è solo in ingresso: ${imap.configured ? `collegata a <b>${esc(imap.user)}</b> (sola lettura, controllo ogni 30s)` : 'non configurata (aggiungi <code>IMAP_USER</code> e <code>IMAP_PASSWORD</code> al .env). Il simulatore inietta le stesse email.'}.
    Il ruolo si dichiara con il tag nell'oggetto; senza tag il mittente non ha alcun permesso.</p>
    <div class="row" style="margin-bottom:10px">${SAMPLES.map((s, i) => `<button class="sug" type="button" data-s="${i}">${esc(s[0])}</button>`).join('')}</div>
    <form id="in-form"><div class="form-grid"><div class="field" style="grid-column:1/-1"><label>Oggetto</label><input name="subject" required placeholder="[Giulia] Domanda sul margine"></div></div>
    <div class="field" style="margin-top:8px"><label>Testo</label><textarea name="body" required></textarea></div>
    <div class="row" style="margin-top:10px"><button class="btn" type="submit">Ricevi email</button>
    <button class="btn sec" type="button" id="poll" ${imap.configured ? '' : 'disabled'}>Controlla casella ora</button></div></form></div>
    <h2 class="sec">Email ricevute ${S.user.role === 'ceo' ? '(tutte)' : '(le tue)'}</h2>
    ${msgs.map(m => `<div class="card"><div class="row"><b>✉️ ${esc(m.subject)}</b><span class="spacer"></span><span class="tag">${esc(m.via)}</span></div>
      <div class="meta">da ${esc(m.sender_name)} · ${esc(dmy(m.received_at))} ${esc(hhmm(m.received_at))}</div>
      <div class="src-text">${esc(m.body)}</div>${outcomeHTML(m.outcome)}</div>`).join('') || '<div class="empty">Nessuna email ricevuta. Usa un esempio qui sopra.</div>'}`;
  $$('[data-s]', box).forEach(b => b.onclick = () => { const s = SAMPLES[b.dataset.s]; $('#in-form [name=subject]').value = s[1]; $('#in-form [name=body]').value = s[2]; });
  $('#in-form').onsubmit = async ev => {
    ev.preventDefault(); const f = new FormData(ev.target);
    const btn = $('button[type=submit]', ev.target); btn.disabled = true; btn.textContent = 'Elaboro…';
    try { await api('/api/inbox/simulate', { method: 'POST', body: { subject: f.get('subject'), body: f.get('body') } }); toast('Email elaborata dall\'agente', 'ok'); }
    catch (err) { toast(err.message, 'bad'); }
    S.brief = {}; renderInbox(); refreshStatus();
  };
  $('#poll').onclick = async () => { try { const r = await api('/api/inbox/poll', { method: 'POST' }); toast(`${r.processed} nuove email`); renderInbox(); refreshStatus(); } catch (err) { toast(err.message, 'bad'); } };
}
function outcomeHTML(o) {
  if (!o) return '';
  const label = { answer: '💬 Risposta (resta nella UI)', unknown: '🤷 Non lo so', action: '⚡ Azione in attesa', clarify: '❔ Serve un chiarimento', learned: '🧩 Nuova conoscenza (candidati)', unknown_sender: '🚫 Mittente sconosciuto' }[o.type];
  const cites = (o.citations || []);
  return `<div class="bubble agent" style="margin-top:10px"><b>${label}</b><div style="margin-top:6px">${fmtText(o.text, cites)}</div>
    ${cites.length ? `<div class="chips">${cites.map((c, i) => chip(c.source, i + 1)).join('')}</div>` : ''}
    ${o.type === 'action' ? '<button class="btn sm sec" onclick="navigate(\'actions\')">Vai alle azioni →</button>' : ''}
    ${o.type === 'learned' ? '<button class="btn sm sec" onclick="navigate(\'memory\')">Vai alla memoria →</button>' : ''}</div>`;
}

// ------------------------------------------------------------------ mock Slack
async function renderSlack() {
  const log = $('#slack-log'); let r;
  try { r = await api(`/api/channels/${encodeURIComponent(S.channel)}?${uq()}`); } catch (err) { log.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  const extra = (S.slack[S.user.id + ':' + S.channel] ||= []);
  const msg = m => `<div class="msg">${avatar(m.author)}<div class="body"><div class="who-line">${esc(m.author)}<span class="time">${esc(dmy(m.ts))} ${esc(hhmm(m.ts))}</span></div><div>${esc(m.text)}</div></div></div>`;
  log.innerHTML = `<div class="hello"><b># ${esc(r.channel.name)}</b> ${r.channel.private ? '🔒 privato' : ''}<div class="meta">${esc(r.channel.topic)} — mock di Slack: i canali privati compaiono solo ai ruoli autorizzati. Scrivi <code>@brain</code> + domanda per interpellare il Brain.</div></div>
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
      const r = await api('/api/ask', { method: 'POST', body: { user: S.user.id, question: q } });
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

(async function init() {
  $('#theme-btn').textContent = document.documentElement.dataset.theme === 'dark' ? '☀️' : '🌙';
  S.users = await api('/api/users'); S.user = S.users[0]; renderUsers();
  await loadChannels(); await refreshStatus(); navigate('chat');
  setInterval(refreshStatus, 20000);
})();
