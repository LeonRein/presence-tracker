// Side panel per tab.
import { computeCoverage } from './coverage.js';
import { api, edit, emit, heldBadges, savePref, saveNow, select, sensorById, sensorColor, setTool, state } from './store.js';
import { AlignTool, PlaceSensorTool, WallTool, ZoneTool, autoFitImage, deleteSelection } from './tools.js';
import { ZONE_KINDS, bindNumber, dist, esc, fmt, h, personColor, regionOfRoom, roomsWithSensor, toast, uid, zoneOutline } from './util.js';
import { loadSize } from './view.js';

let skipRender = false;

// a list row that selects something: also by keyboard (Tab, Enter or Space); the map needs a pointer
function clickable(item, fn) {
  item.tabIndex = 0;
  item.setAttribute('role', 'button');
  item.onclick = fn;
  item.onkeydown = e => {
    if (e.target !== item || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    fn(e);
    // the panel is rebuilt: keep the keyboard on the row just chosen
    setTimeout(() => document.querySelector('#panel .item.selected')?.focus(), 0);
  };
}
let zoneKind = 'area';

// edits from panel inputs: don't rebuild the panel under the user's cursor
function panelEdit(fn, opts) {
  skipRender = true;
  try { edit(fn, opts); } finally { skipRender = false; }
}

export function renderPanel(panel, view, what) {
  mapView = view;
  if (skipRender && what === 'config') return;
  const fn = { live: livePanel, plan: planPanel, sensors: sensorsPanel, zones: zonesPanel, calibration: calibrationPanel, settings: settingsPanel }[state.tab];
  panel.innerHTML = '';
  fn(panel, view);
  updateLive(panel);
}

// parts of the panel that change with the live messages. Only what changed is replaced: a list
// gives its rows with a key ([key, html]) and only changed rows are rebuilt, so tooltips and the
// rows' places hold while the rest updates
export function updateLive(panel) {
  for (const el of panel.querySelectorAll('[data-live]')) {
    const fn = LIVE[el.dataset.live];
    if (!fn) continue;
    const out = fn(el.dataset);
    if (Array.isArray(out)) patchRows(el, out);
    else if (el._html !== out) { el.innerHTML = out; el._html = out; }
  }
}

function patchRows(el, rows) {
  if (el._html !== undefined) { el.innerHTML = ''; el._html = undefined; }  // was a note, now rows
  const old = new Map([...el.children].map(c => [c.dataset.key, c]));
  let prev = null;
  for (const [key, html] of rows) {
    let node = old.get(key);
    old.delete(key);
    if (!node || node._html !== html) {
      const fresh = h(html);
      fresh.dataset.key = key;
      fresh._html = html;
      if (node) node.replaceWith(fresh);
      node = fresh;
    }
    const want = prev ? prev.nextElementSibling : el.firstElementChild;
    if (node !== want) el.insertBefore(node, want);
    prev = node;
  }
  for (const n of old.values()) n.remove();
}

const pct = p => `${Math.round(p * 100)}\u00a0%`;
// P(somebody there) above which a room is occupied (light on): light_cost / (light_cost + 1)
function occupiedAbove() {
  const k = state.config.params?.light_cost ?? 2;
  return k / (k + 1);
}
const people = n => `${n}\u00a0${n === 1 ? 'Person' : 'Personen'}`;
// what decided "besetzt" (the attribute quelle in Home Assistant): [badge, explanation]
const SOURCE = {
  filter: ['Filter', 'Besetzt, weil die Wahrscheinlichkeit des Filters \u00fcber der Schwelle liegt.'],
  ld2450: ['LD2450', 'Besetzt, weil der LD2450 dieses Raums gerade jemanden im Raum misst (Haltezeit in den Einstellungen). Der Filter allein sagt \u201eleer\u201c.'],
  beide: ['beide', 'Besetzt nach dem Filter und nach dem LD2450 dieses Raums.'],
};

// where a person of the model is: the room they are drawn in, or the most probable places
function whereText(t) {
  const regions = state.live?.regions || {};
  const names = Object.fromEntries(state.config.zones.map(z => [z.id, z.name]));
  const name = k => k === 'observed' ? (t.room ? names[t.room] : 'in Räumen mit Sensor')
    : k === 'outside' ? 'außer Haus' : (regions[k]?.rooms || []).map(id => names[id] || id).join(', ') || k;
  return Object.entries(t.places || {}).sort((a, b) => b[1] - a[1]).slice(0, 2)
    .map(([k, v]) => `${esc(name(k))} ${pct(v)}`).join(' · ');
}

// people in the rooms the sensors see (the house total counts guesses about rooms without a sensor)
export function seenTotal() {
  const live = state.live;
  if (!live?.zones || !state.config) return null;
  const t = { count: 0, moving: 0, still: 0 };
  for (const z of roomsWithSensor(state.config, live)) {
    const st = live.zones[z.id];
    if (st) { t.count += st.count; t.moving += st.moving; t.still += st.still; }
  }
  return t;
}

const LIVE = {
  // the rooms and areas the sensors see; the rest are regions without a sensor (derived from the
  // plan on the server: rooms no sensor covers, grouped by the doors between them)
  zones() {
    const live = state.live;
    const zones = [...roomsWithSensor(state.config, live), ...state.config.zones.filter(z => z.kind === 'area')];
    if (!zones.length) return '<p class="note">Noch keine Räume oder Bereiche mit Sensor. Im Tab „Zonen“ einzeichnen, Sensoren platzieren.</p>';
    // occupied first (what the lights follow), else in the order of the plan
    const rows = zones.map((z, i) => ({ z, i, st: live?.zones?.[z.id] }))
      .sort((a, b) => (b.st?.occupied ? 1 : 0) - (a.st?.occupied ? 1 : 0) || a.i - b.i);
    const c = occupiedAbove();
    return rows.map(({ z, st }) => {
      const color = ZONE_KINDS[z.kind].color;
      const occ = !!st?.occupied;
      // "wird betreten" / "Ziel" (held 2 s) matter for a dark room only: where somebody already is,
      // the light is on anyway
      const held = occ ? {} : heldBadges(z.id);
      const extra = st ? [
        st.count ? `<span>${people(st.count)}${st.moving ? ` · ${st.moving} bewegt` : ''}${st.still ? ` · ${st.still} ruhig` : ''}</span>` : '',
        held.approach ? `<span class="badge warn" title="Jemand Gehendes kommt gleich herein: ${pct(held.approach.p ?? 0)} (Vorausschau in den Einstellungen)">wird betreten${held.approach.eta > 0 ? ` · in ${fmt(held.approach.eta, 1)}\u00a0s` : ''}</span>` : '',
        held.target ? `<span class="badge warn" title="Jemand Gehendes geht als Nächstes hierher. Entschieden von ${held.target.source === 'karte' ? 'der gelernten Karte, wohin Gänge von dort gingen' : 'der Bewegung (wie „wird betreten“)'}">Ziel ${pct(held.target.p ?? 0)} · ${held.target.source === 'karte' ? 'Karte' : 'Bewegung'}</span>` : '',
      ].filter(Boolean).join('') : '';
      return [z.id, `<div class="item room-row${occ ? ' occupied' : ''}"><span class="swatch" style="background:${color}"></span>
        <span class="grow">${esc(z.name)}</span>
        ${st ? `<span class="badge ${occ ? 'on' : ''}">${occ ? 'besetzt' : 'leer'}</span>` : ''}
        ${occ && SOURCE[st.source] ? `<span class="badge" title="${SOURCE[st.source][1]}">${SOURCE[st.source][0]}</span>` : ''}
        ${st?.probability != null ? `<span class="badge num" title="Wahrscheinlichkeit, dass jemand im Raum ist; besetzt ab ${pct(c)}">${pct(st.probability)}</span>` : ''}
        ${extra ? `<div class="meta sub">${extra}</div>` : ''}</div>`];
    });
  },
  total() {
    const t = seenTotal();
    if (!t) return '–';
    const live = state.live;
    const occupied = state.config.zones.filter(z => (z.kind === 'room' && !z.entry || z.kind === 'area') && live?.zones?.[z.id]?.occupied);
    return `<div class="occupied-line">${occupied.length ? `Besetzt: <b>${occupied.map(z => esc(z.name)).join(', ')}</b>` : 'Kein Raum besetzt'}</div>
      <div><b style="font-size:22px">${t.count}</b> <span class="note">${t.count === 1 ? 'Person' : 'Personen'} in den Räumen mit Sensor · ${t.moving} bewegt · ${t.still} ruhig</span></div>`;
  },
  people() {
    const tracks = state.live?.tracks || [];
    if (!tracks.length) return '<p class="note">Keine Personen im Modell.</p>';
    return tracks.map(t => {
      const moving = t.x != null && !t.lost && t.walk > 0.5 && Math.hypot(t.vx, t.vy) > 0.15;
      const state_ = t.x == null ? '' : t.lost ? '<span class="badge">nicht gesehen</span>' : moving ? '<span class="badge ok">bewegt</span>' : '<span class="badge">ruhig</span>';
      return [String(t.id), `<div class="item" style="flex-wrap:wrap"><span class="swatch dot" style="background:${personColor(t.id)}"></span><span class="grow">Person ${t.id}</span>${state_}
        <div class="meta sub">${whereText(t)}</div></div>`];
    });
  },
  sensorHealth() {
    const live = state.live;
    const ids = [...new Set([...state.config.sensors.map(s => s.id), ...Object.keys(live?.sensors || {})])].sort();
    if (!ids.length) return '<p class="note">Noch keine Daten von Sensoren.</p>';
    // fixed columns: name | status, the measurements below; nothing jumps when a number changes
    return ids.map(id => {
      const sv = live?.sensors?.[id];
      const s = sensorById(id);
      const online = sv?.online;
      const n = sv ? sv.detections.length : 0;
      return [id, `<div class="item sensor-row"><span class="swatch" style="background:${sensorColor(id)}"></span>
        <span class="grow">${esc(s?.name || id)}</span>
        <span>${s?.placed ? '' : '<span class="badge warn">nicht platziert</span> '}<span class="badge ${online ? 'ok' : 'bad'}">${online ? 'online' : 'offline'}</span></span>
        <span class="meta sub">${n} ${n === 1 ? 'Ziel' : 'Ziele'} · LD2410C ${sv?.ld2410?.present ? `${fmt(sv.ld2410.distance, 1)}\u00a0m` : 'nichts'}</span></div>`];
    });
  },
  unobserved() {
    const regions = Object.values(state.live?.regions || {});
    if (!regions.length) return '<p class="note">Alle Räume haben einen Sensor.</p>';
    const fmtS = s => s >= 5400 ? `${Math.round(s / 3600)} h` : s >= 90 ? `${Math.round(s / 60)} min` : `${s} s`;
    const names = Object.fromEntries(state.config.zones.map(z => [z.id, z.name]));
    return regions.map(r => [r.name, `<div class="item" style="flex-wrap:wrap"><span class="grow" style="white-space:normal">${(r.rooms || []).map(id => esc(names[id] || id)).join(', ') || esc(r.name)}</span>
      ${r.open ? '<span class="badge" title="Von hier aus kann man das Haus verlassen (Tür nach draußen, Eingang)">Ausgang</span>' : ''}
      ${r.probabilities.length ? r.probabilities.map(p => `<span class="badge ${p >= 0.5 ? 'on' : ''}" title="Wahrscheinlichkeit, dass diese Person hier ist">${pct(p)}</span>`).join('') : '<span class="badge">leer</span>'}
      <div class="meta sub">${(r.rooms || []).length > 1 ? 'Ohne Sensor über Türen verbunden, deshalb zusammen. ' : ''}Aufenthalt typisch ${fmtS(r.dwell.median)}, 90\u00a0% unter ${fmtS(r.dwell.p90)} (Annahme)</div></div>`]);
  },
  sensorNow(ds) {
    const sv = state.live?.sensors?.[ds.id];
    if (!sv) return '<p class="note">Keine Daten.</p>';
    const rows = sv.detections.map((d, i) => `<tr><td>${i + 1}</td><td>${fmt(d.lx)}</td><td>${fmt(d.ly)}</td><td>${fmt(d.speed, 2)}</td></tr>`).join('');
    return `<table class="data"><tr><th>Ziel</th><th>x (m)</th><th>y (m)</th><th>v (m/s)</th></tr>${rows || '<tr><td colspan="4">kein Ziel</td></tr>'}</table>
      <p class="note">LD2410C: ${sv.ld2410.present ? `Präsenz in ${fmt(sv.ld2410.distance, 2)} m` : 'keine Präsenz'}</p>
      ${gateBars(sv.ld2410)}`;
  },
  zoneNow(ds) {
    const st = state.live?.zones?.[ds.id];
    if (!st) return '';
    return `<dl class="kv"><dt>Personen</dt><dd>${st.count}</dd><dt>bewegt / ruhig</dt><dd>${st.moving} / ${st.still}</dd>
      <dt>wird betreten</dt><dd>${st.approaching ? 'ja' : 'nein'}${st.p_enter != null ? `, ${Math.round(st.p_enter * 100)} %` : ''}${st.eta != null ? ` · in ${fmt(st.eta, 1)} s, ${fmt(st.distance, 1)} m${st.person != null ? ` (Person ${st.person})` : ''}` : ''}</dd>
      <dt>Ziel</dt><dd>${st.target ? 'ja' : 'nein'}${st.p_target != null ? `, ${Math.round(st.p_target * 100)} %` : ''}${st.target_from ? ` · aus ${esc(roomName(st.target_from))}${st.target_distance != null ? `, ${fmt(st.target_distance, 1)} m zur Tür` : ''}${st.target_person != null ? ` (Person ${st.target_person})` : ''}` : ''}${st.target_source ? ` · ${st.target_source === 'karte' ? 'Karte' : 'Bewegung'} (Karte ${Math.round((st.target_weight ?? 0) * 100)} %, ${st.target_walks ?? 0} Gänge)` : ''}</dd></dl>`;
  },
  calib() {
    const c = state.live?.calibration;
    if (!c) return '';
    const since = c.since ? new Date(c.since * 1000).toLocaleString('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '–';
    // per sensor: its walking points (as the result counts them) and its hours with walking data
    // towards the min_hours a proposal needs
    const need = c.min_hours || 3;
    const ids = Object.keys(c.points || c.frames);
    const frames = ids.map(id => {
      const hrs = c.hours?.[id] ?? 0;
      const n = c.points?.[id] ?? c.frames[id];
      return `<div class="item calib-progress"><span class="swatch" style="background:${sensorColor(id)}"></span><span class="grow">${esc(sensorById(id)?.name || id)}</span>
        <span class="meta">${n} Punkte</span>
        <span class="badge ${hrs >= need ? 'ok' : ''}" title="Stunden mit Gehenden; ein Vorschlag braucht ${need}">${Math.min(hrs, need)}/${need}\u00a0h</span>
        <progress max="${need}" value="${Math.min(hrs, need)}" aria-label="Stunden mit Gehenden"></progress></div>`;
    }).join('');
    const ready = ids.filter(id => (c.hours?.[id] ?? 0) >= need).length;
    return `<h3>Gesammelt seit ${since}</h3>
      <p class="note">Punkte in Bewegung: höchstens einer je Sekunde und Spur, wie die Berechnung sie zählt. ${ready} von ${ids.length} Sensoren haben Gehende aus ${need} verschiedenen Stunden${ready < ids.length ? '; für die übrigen schlägt die Berechnung noch nichts vor' : ''}.</p>${frames}`;
  },
  calibPairs() {
    const c = state.live?.calibration;
    if (!c) return '';
    const pairs = Object.entries(c.pairs).map(([k, n]) => {
      const [a, b] = k.split('|');
      const ok = n >= 60 ? 'ok' : n >= 15 ? 'warn' : '';
      return `<div class="item"><span class="grow">${esc(sensorById(a)?.name || a)} ↔ ${esc(sensorById(b)?.name || b)}</span><span class="badge ${ok}">${n}\u00a0s</span></div>`;
    }).join('');
    return pairs || '<p class="note">Noch nichts. Auch Sensoren, die sich nur in einer Tür überschneiden, werden über den Grundriss und die Übergänge kalibriert.</p>';
  },
};

// LD2410C energy per 0.75 m gate: bewegt (move) and ruhig (still), 0-100
function gateBars(ld) {
  if (!ld?.still_gates) return '<p class="note">Keine Energiewerte pro Entfernungsstufe (Firmware mit Engineering Mode nötig).</p>';
  const w = 26, hgt = 60, gap = 4;
  const bars = ld.still_gates.map((sv, i) => {
    const mv = ld.move_gates?.[i] ?? 0;
    const x = i * (w + gap);
    return `<rect x="${x}" y="${hgt - mv * hgt / 100}" width="${w / 2 - 1}" height="${mv * hgt / 100}" fill="var(--moving)"/>
      <rect x="${x + w / 2}" y="${hgt - sv * hgt / 100}" width="${w / 2 - 1}" height="${sv * hgt / 100}" fill="var(--still)"/>
      <text x="${x + w / 2}" y="${hgt + 12}" font-size="9" text-anchor="middle" fill="var(--muted)">${fmt(i * 0.75, 2)}</text>`;
  }).join('');
  return `<h3>LD2410C-Energie pro Stufe</h3>
    <svg viewBox="0 -2 ${9 * (w + gap)} ${hgt + 16}" width="100%" style="max-width:300px">${bars}</svg>
    <div class="legend"><span><span class="swatch" style="background:var(--moving)"></span>bewegt</span>
    <span><span class="swatch" style="background:var(--still)"></span>ruhig</span><span>Abstand in m</span></div>`;
}

// ------------------------------------------------------------------- live

function livePanel(panel, view) {
  panel.append(h(`<div>
    <div class="card" data-live="total"></div>
    <details class="card" id="report">
      <summary><b>Fehler melden</b></summary>
      <div class="row" style="margin-top:8px">
        <label class="field grow">Raum<select id="rep-room"></select></label>
        <label class="field grow">Was war falsch?<select id="rep-kind"></select></label>
      </div>
      <div class="row">
        <label class="field">Vor wie vielen Minuten?<input type="number" id="rep-ago" min="0" max="15" step="1" value="0" style="width:6em"></label>
        <label class="field grow" style="flex-basis:100%">Was war los? (optional)<input id="rep-text" placeholder="z. B. saß auf dem Sofa, wurde nach 1 min verloren"></label>
      </div>
      <div class="row"><button class="btn primary" id="rep-send">Melden</button></div>
      <p class="note">Speichert die Sensordaten der letzten 15 Minuten mit der Konfiguration, zum genauen Nachspielen. Die Meldungen sind die Wahrheitsdaten für die Bewertung.</p>
      <div class="list" id="rep-list"></div>
    </details>
    <h3>Räume mit Sensor</h3>
    <p class="note">% = Wahrscheinlichkeit, dass jemand im Raum ist. Besetzt (Licht an) ab ${pct(occupiedAbove())}, aus den Kosten in den Einstellungen, oder solange der LD2450 des Raums dort jemanden misst (Filter / LD2450 / beide: was entschied).</p>
    <div class="list" data-live="zones"></div>
    <h3>Räume ohne Sensor</h3><div class="list" data-live="unobserved"></div>
    <p class="note">Aus dem Grundriss: Räume, die kein Sensor überwiegend sieht, über Türen zu Gruppen verbunden. Wer hineingeht, ist dort. Die Wahrscheinlichkeit, dass jemand noch drin ist, sinkt mit der Zeit, je nachdem, wie lange Besuche dort üblicherweise dauern. % = Wahrscheinlichkeit, dass diese Person dort ist, je Person.</p>
    <h3>Sensoren</h3><div class="list" data-live="sensorHealth"></div>
    <h3>Personen im Modell</h3><div class="list" data-live="people"></div>
    <p class="note">Je Person die zwei wahrscheinlichsten Orte; % = Wahrscheinlichkeit, dass diese Person dort ist (nicht dieselbe Zahl wie beim Raum). Auf der Karte in ihrer Farbe: der Punkt und die Wolke, wo sie sein kann.</p>
    <h3>Anzeige</h3>
    <div class="legend">
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="${personColor(1)}"/></svg>Person (eine Farbe je Person)</span>
      <span><svg width="18" height="12"><line x1="2" y1="6" x2="14" y2="6" stroke="var(--muted)" stroke-width="2.5"/><path d="M12 2 L17 6 L12 10 z" fill="var(--muted)"/></svg>geht</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--muted)" fill-opacity="0.4" stroke="var(--muted)" stroke-dasharray="2 1.5"/></svg>gerade nicht gesehen</span>
    </div>
    <label class="check"><input type="checkbox" id="raw" ${state.showRaw ? 'checked' : ''}> Rohdaten der Sensoren zeigen (Sichtfelder, Messpunkte)</label>
    <div class="legend" id="raw-legend" ${state.showRaw ? '' : 'hidden'}>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="3" fill="var(--muted)"/></svg>Messpunkt</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="3.5" fill="none" stroke="var(--muted)" stroke-width="1.5"/></svg>verworfen (hinter einer Wand oder eingefroren)</span>
      <span><svg width="16" height="12"><path d="M1 10 A 9 9 0 0 1 15 10" stroke="var(--muted)" stroke-dasharray="2 3" fill="none" stroke-width="2"/></svg>LD2410C-Abstand</span>
    </div>
    <div class="row" style="margin-top:12px"><button class="btn" id="reset">Neu beginnen</button></div>
    <p class="note">Das Modell vergisst, wo wer ist: Jede Person kann wieder überall sein, auch außer Haus. Die nächsten Messungen entscheiden neu.</p>
  </div>`));
  panel.querySelector('#raw').onchange = e => {
    state.showRaw = e.target.checked;
    savePref('showRaw', state.showRaw);
    panel.querySelector('#raw-legend').hidden = !state.showRaw;
    view.render();
  };
  panel.querySelector('#reset').onclick = async () => {
    if (!confirm('Neu beginnen? Das Modell vergisst, wo wer ist. Bis die Sensoren wieder jemanden sehen, können Räume als leer gelten und Lichter ausgehen.')) return;
    await api('api/tracks/reset', { method: 'POST' });
    toast('Neu begonnen');
  };
  reportForm(panel.querySelector('#report'));
}

// error reports: what looked wrong, saved with the last minutes of sensor data (app.h_report)
async function reportForm(el) {
  const rooms = state.config.zones.filter(z => z.kind === 'room');
  // no room preselected: the first of the list was taken as the room of the error unnoticed
  el.querySelector('#rep-room').innerHTML = '<option value="?" selected disabled>Bitte wählen</option>'
    + rooms.map(z => `<option value="${esc(z.id)}">${esc(z.name)}</option>`).join('')
    + '<option value="">ganzes Haus</option>';
  const list = el.querySelector('#rep-list');
  const load = async () => {
    const data = await api('api/reports');
    const kinds = el.querySelector('#rep-kind');
    if (!kinds.options.length) kinds.innerHTML = Object.entries(data.kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    const names = Object.fromEntries(rooms.map(z => [z.id, z.name]));
    list.innerHTML = '';
    for (const r of data.reports.slice(0, 20)) {
      const when = new Date(r.t_event * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      list.append(h(`<div class="item"><span class="grow">${when} · ${esc(names[r.room] || 'Haus')} · ${esc(r.kind_text)}${r.text ? ` · <i>${esc(r.text)}</i>` : ''}</span>
        <a class="meta" href="api/reports/${encodeURIComponent(r.name)}" download>${r.size < 1e6 ? `${Math.max(1, Math.round(r.size / 1e3))} kB` : `${fmt(r.size / 1e6, 1)} MB`}</a></div>`));
    }
    if (!data.reports.length) list.append(h('<p class="note">Noch keine Meldungen.</p>'));
  };
  el.querySelector('#rep-send').onclick = async () => {
    const roomSel = el.querySelector('#rep-room');
    if (roomSel.value === '?') { toast('Bitte den Raum wählen (oder „ganzes Haus“).'); roomSel.focus(); return; }
    try {
      const r = await api('api/reports', { method: 'POST', body: JSON.stringify({
        room: el.querySelector('#rep-room').value, kind: el.querySelector('#rep-kind').value,
        minutes_ago: Number(el.querySelector('#rep-ago').value) || 0, text: el.querySelector('#rep-text').value }) });
      toast(`Gemeldet (${r.messages} Nachrichten gespeichert)`);
      el.querySelector('#rep-text').value = '';
      await load();
    } catch (err) { toast(err.message, 5000); }
  };
  try { await load(); } catch (err) { list.append(h(`<p class="note">${esc(err.message)}</p>`)); }
}

// ------------------------------------------------------------------- plan

function planPanel(panel, view) {
  const c = state.config;
  const sel = state.selection;
  const tool = state.tool;
  const toolBtn = (name, label) => `<button class="${tool === name ? 'active' : ''}" data-tool="${name}">${label}</button>`;
  const rooms = c.zones.filter(z => z.kind === 'room');
  panel.append(h(`<div>
    <h2>Grundriss</h2>
    <p class="note">Wände zeichnen, Türen auf die Wände setzen. Räume sind die geschlossenen Flächen dazwischen: Eine Tür trennt zwei Räume, ein Durchgang ohne Tür bleibt einfach eine Lücke in der Wand und verbindet sie zu einem Raum. Eine Raumgrenze teilt einen Raum ohne Wand (z. B. Wohn- und Essbereich).</p>
    <div class="seg" id="tools">${toolBtn('wall', 'Wand')}${toolBtn('divider', 'Raumgrenze')}${toolBtn('door', 'Tür')}</div>
    <div id="detail" style="margin-top:12px"></div>
    <h3>Räume (${rooms.length})</h3><div class="list" id="rooms"></div>
    <h3>Hintergrund</h3>
    <div class="list" id="layers"></div>
    <div class="row" style="margin-top:8px">
      <label class="btn">Bild hochladen<input type="file" accept="image/*" id="upload" hidden></label>
      ${state.haAvailable ? '<button class="btn" id="vacuum">Saugroboter-Karte</button>' : ''}
    </div>
    <div id="layer-edit"></div>
  </div>`));
  for (const b of panel.querySelectorAll('#tools button')) b.onclick = () => setTool(tool === b.dataset.tool ? null : b.dataset.tool);

  const detail = panel.querySelector('#detail');
  if (sel?.kind === 'wall' && c.walls[sel.id]) wallDetail(detail, sel.id);
  else if (sel?.kind === 'door') doorDetail(detail, sel.id);
  else if (sel?.kind === 'room') { const z = c.zones.find(z => z.id === sel.id); if (z) roomDetail(detail, z); }
  else detail.append(h('<p class="note">Wand, Tür oder Raum anklicken zum Bearbeiten. Jede Änderung wird sofort gespeichert; Rückgängig und Wiederholen mit ↶ ↷ unten rechts auf der Karte (oder Strg+Z / Strg+Y).</p>'));

  const roomList = panel.querySelector('#rooms');
  for (const z of rooms) {
    const item = h(`<div class="item ${sel?.kind === 'room' && sel.id === z.id ? 'selected' : ''}">
      <span class="swatch" style="background:${z.entry ? ZONE_KINDS.entry.color : ZONE_KINDS.room.color}"></span>
      <span class="grow">${esc(z.name)}</span>${z.entry ? '<span class="badge warn" title="Öffentlicher Raum vor der Wohnungstür (Treppenhaus): außer Haus">außer Haus</span>' : ''}
      ${state.live && !z.entry ? (regionOfRoom(state.live, z.id) ? '<span class="badge">ohne Sensor</span>' : '<span class="badge ok">Sensor</span>') : ''}
      <span class="meta">${fmt(zoneArea(z), 1)} m²</span></div>`);
    clickable(item, () => select({ kind: 'room', id: z.id }));
    roomList.append(item);
  }
  if (!rooms.length) roomList.append(h('<p class="note">Noch keine geschlossenen Räume.</p>'));

  const list = panel.querySelector('#layers');
  for (const l of c.background.layers) {
    const item = h(`<div class="item ${sel?.kind === 'layer' && sel.id === l.id ? 'selected' : ''}">
      <input type="checkbox" ${l.visible ? 'checked' : ''} title="anzeigen">
      <span class="grow">${esc(l.name)}</span><span class="meta">${Math.round((l.opacity ?? 0.6) * 100)} %</span></div>`);
    clickable(item, e => { if (e.target.tagName !== 'INPUT') select({ kind: 'layer', id: l.id }); });
    item.querySelector('input').onchange = e => edit(c => { c.background.layers.find(x => x.id === l.id).visible = e.target.checked; });
    list.append(item);
  }
  if (!c.background.layers.length) list.append(h('<p class="note">Kein Hintergrundbild.</p>'));

  panel.querySelector('#upload').onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await api('api/background', { method: 'POST', body: form });
      const layer = { id: uid('l'), name: file.name, url: r.url, x: view.cx - 5, y: view.cy + 4, scale: 0.01, rotation: 0, opacity: 0.6, visible: true };
      edit(c => c.background.layers.push(layer));
      select({ kind: 'layer', id: layer.id });
      toast('Bild geladen. Mit der 2-Punkt-Ausrichtung einpassen.');
    } catch (err) { toast(err.message, 5000); }
  };
  panel.querySelector('#vacuum')?.addEventListener('click', () => importVacuum(view));

  if (sel?.kind === 'layer') layerEditor(panel.querySelector('#layer-edit'), view, sel.id);
}

function wallDetail(el, i) {
  const w = state.config.walls[i];
  el.append(h(`<div class="card">
    <div class="row"><b style="flex:1">${w.kind === 'divider' ? 'Raumgrenze' : 'Wand'}</b><button class="btn danger" id="del">Löschen</button></div>
    <div class="seg" id="kind"><button data-kind="wall" class="${w.kind === 'wall' ? 'active' : ''}">Wand</button><button data-kind="divider" class="${w.kind === 'divider' ? 'active' : ''}">Raumgrenze</button></div>
    <p class="note">${fmt(dist(w.points[0], w.points[1]))} m · Enden oder die ganze Wand ziehen (rastet in 45°-Schritten und an Wänden ein). Angeschlossene Wände gehen mit. Doppelklick auf die Wand teilt sie, auf ein Ende verbindet es mit der anschließenden Wand.</p>
  </div>`));
  el.querySelector('#del').onclick = deleteSelection;
  for (const b of el.querySelectorAll('#kind button')) b.onclick = () => edit(c => { c.walls[i].kind = b.dataset.kind; });
}

function doorDetail(el, id) {
  const door = (state.config.doors || []).find(d => d.id === id);
  if (!door) return;
  el.append(h(`<div class="card">
    <div class="row"><b style="flex:1">Tür</b><button class="btn danger" id="del">Löschen</button></div>
    <label class="field">Breite (m)<input type="number" step="0.01" min="0.3" value="${door.width}" id="width"></label>
    <p class="note">Entlang der Wand ziehen verschiebt die Tür, die Griffe an den Enden ändern die Breite. Für die Räume ist die Tür zu, für Radar und Personen offen.</p>
  </div>`));
  el.querySelector('#del').onclick = deleteSelection;
  bindNumber(el.querySelector('#width'), [0.3, 20, false, false], v => panelEdit(c => { c.doors.find(d => d.id === id).width = v; }));
}

// name and entry flag of a room; its outline comes from the walls
function roomDetail(el, z) {
  const region = regionOfRoom(state.live, z.id);
  const names = Object.fromEntries(state.config.zones.map(x => [x.id, x.name]));
  const others = (region?.rooms || []).filter(id => id !== z.id).map(id => esc(names[id] || id));
  const seen = !state.live ? '' : !region ? 'Die Sensoren sehen diesen Raum: Personen darin werden gezählt.'
    : `Kein Sensor sieht diesen Raum überwiegend.${others.length ? ` Über Türen mit ${others.join(', ')} verbunden: Das Modell weiß nur, dass jemand in dieser Gruppe ist, nicht in welchem Raum.` : ''}${region.open ? ' Von hier kann man das Haus verlassen.' : ' Wer herauskommt, muss vorher hineingegangen sein.'}`;
  const outside = 'Außer Haus: Wer hier ist, hat die Wohnung verlassen. Keine Personenzahl, nichts an Home Assistant; die Tür hierher ist die Wohnungstür.';
  el.append(h(`<div class="card">
    <label class="field">Raum<input type="text" value="${esc(z.name)}" id="name"></label>
    <label class="check" style="margin-top:8px"><input type="checkbox" id="entry" ${z.entry ? 'checked' : ''}> Außer Haus: öffentlicher Raum vor der Wohnungstür (Treppenhaus). Gehört nicht zur Wohnung; wer hineingeht, hat sie verlassen</label>
    <p class="note">Fläche ${fmt(zoneArea(z))} m². ${z.entry ? outside : seen}</p>
    <p class="note">Die Form folgt den Wänden: zum Ändern die Wände verschieben${state.tab === 'plan' ? '' : ' (Tab Grundriss)'}.</p>
  </div>`));
  el.querySelector('#name').onchange = e => panelEdit(c => { c.zones.find(x => x.id === z.id).name = e.target.value; });
  el.querySelector('#entry').onchange = e => edit(c => { c.zones.find(x => x.id === z.id).entry = e.target.checked; });
}

function zoneArea(z) {
  const pts = zoneOutline(z, 64);
  let a = 0;
  for (let i = 0; i < pts.length; i++) { const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length]; a += x1 * y2 - x2 * y1; }
  return Math.abs(a) / 2;
}

function layerEditor(el, view, id) {
  const l = state.config.background.layers.find(l => l.id === id);
  if (!l) return;
  el.append(h(`<div class="card" style="margin-top:12px">
    <div class="row"><b class="grow" style="flex:1">${esc(l.name)}</b><button class="btn danger" id="del">Entfernen</button></div>
    <label class="field">Deckkraft<input type="range" min="0.05" max="1" step="0.05" value="${l.opacity ?? 0.6}" id="opacity"></label>
    <div class="grid2">
      <label class="field">x links oben (m)<input type="number" step="0.01" value="${l.x}" data-k="x"></label>
      <label class="field">y links oben (m)<input type="number" step="0.01" value="${l.y}" data-k="y"></label>
      <label class="field">Maßstab (mm/Pixel)<input type="number" step="0.01" value="${+(l.scale * 1000).toFixed(4)}" data-k="scale"></label>
      <label class="field">Drehung (°)<input type="number" step="0.1" value="${l.rotation || 0}" data-k="rotation"></label>
    </div>
    <div class="row">
      <button class="btn" id="align">2-Punkt-Ausrichtung</button>
      ${l.rooms ? '<button class="btn" id="fit">An Räume einpassen</button>' : ''}
    </div>
    <p class="note">Ausgewähltes Bild lässt sich mit der Maus verschieben.</p>
  </div>`));
  el.querySelector('#del').onclick = deleteSelection;
  el.querySelector('#opacity').oninput = e => panelEdit(c => { c.background.layers.find(x => x.id === id).opacity = +e.target.value; }, { merge: 'opacity' + id });
  for (const input of el.querySelectorAll('[data-k]')) {
    const k = input.dataset.k;
    const lim = state.limits.layer?.[k];
    // the scale is entered in mm per pixel
    bindNumber(input, k === 'scale' && lim ? [lim[0] * 1000, lim[1] * 1000, lim[2], lim[3]] : lim, v => edit(c => {
      c.background.layers.find(x => x.id === id)[k] = k === 'scale' ? v / 1000 : v;
    }));
  }
  el.querySelector('#align').onclick = () => setTool({ name: 'align', layer: id });
  el.querySelector('#fit')?.addEventListener('click', () => {
    edit(c => { autoFitImage(c.background.layers.find(x => x.id === id), l.rooms); });
  });
}

// Exact placement from the vacuum's calibration points: robot (0,0), (1000,0), (0,1000) mm -> map pixels
function fitFromCalibration(layer, cal) {
  if (!Array.isArray(cal) || cal.length < 2) return false;
  const o = cal.find(c => c.vacuum.x === 0 && c.vacuum.y === 0)?.map;
  const ax = cal.find(c => c.vacuum.x === 1000 && c.vacuum.y === 0)?.map;
  if (!o || !ax) return false;
  const du = ax.x - o.x, dv = ax.y - o.y;
  const k = 1 / Math.hypot(du, dv);
  const r = Math.atan2(dv, du);  // pixel direction of the robot's +x
  // world of pixel (0,0): (x, y) = -Rot(r) * (o.x k, -o.y k)
  const a = o.x * k, b = -o.y * k;
  layer.scale = +k.toPrecision(6);
  layer.rotation = +(r * 180 / Math.PI).toFixed(3);
  layer.x = +(-(a * Math.cos(r) - b * Math.sin(r))).toFixed(4);
  layer.y = +(-(a * Math.sin(r) + b * Math.cos(r))).toFixed(4);
  return true;
}

async function importVacuum(view) {
  let cams;
  try { cams = await api('api/ha/cameras'); } catch (e) { toast(e.message, 5000); return; }
  if (!cams.length) { toast('Keine Saugroboter-Karte mit Räumen gefunden.'); return; }
  const cam = cams.length === 1 ? cams[0] : cams.find(c => c.entity_id === prompt('Kamera-Entität:\n' + cams.map(c => c.entity_id).join('\n'), cams[0].entity_id));
  if (!cam) return;
  let map;
  try { map = await api('api/ha/map?entity=' + encodeURIComponent(cam.entity_id)); } catch (e) { toast(e.message, 5000); return; }
  const layer = { id: uid('l'), name: cam.name, url: map.image + '?t=' + Date.now(), x: 0, y: 0, scale: 0.0125, rotation: 0,
    opacity: 0.7, visible: true, rooms: map.rooms };
  loadSize(layer.url, () => {
    edit(c => {
      if (!fitFromCalibration(layer, map.calibration_points)) autoFitImage(layer, map.rooms);
      c.background.layers.push(layer);
    });
    select({ kind: 'layer', id: layer.id });
    view.fit();
    toast('Karte geladen und eingepasst.');
  });
}

// ---------------------------------------------------------------- sensors

function sensorsPanel(panel, view) {
  const c = state.config;
  const sel = state.selection?.kind === 'sensor' ? state.selection.id : null;
  const ids = [...new Set([...c.sensors.map(s => s.id), ...state.sensorsSeen])].sort();
  panel.append(h(`<div>
    <h2>Sensoren</h2>
    <p class="note">Sensoren melden sich per MQTT selbst an. Nicht platzierte Sensoren auf die Karte setzen und die Blickrichtung am Griff drehen. Die genaue Lage ermittelt danach die Kalibrierung.</p>
    <div class="list" id="list"></div>
    <div id="detail"></div>
    <h3>Abdeckung</h3>
    <label class="check"><input type="checkbox" id="cov" ${state.showCoverage ? 'checked' : ''}> Tote Winkel zeigen</label>
    <div class="legend"><span><span class="swatch" style="background:#d64545"></span>kein Sensor</span><span><span class="swatch" style="background:#f0b429"></span>ein Sensor</span><span><span class="swatch" style="background:#1f9d55"></span>zwei oder mehr</span></div>
    <div id="cov-stats"></div>
  </div>`));
  const list = panel.querySelector('#list');
  for (const id of ids) {
    const s = c.sensors.find(s => s.id === id);
    const online = state.live?.sensors?.[id]?.online;
    const item = h(`<div class="item ${sel === id ? 'selected' : ''}">
      <span class="swatch" style="background:${sensorColor(id)}"></span>
      <span class="grow">${esc(s?.name || id)}</span>
      ${s?.placed ? '' : '<button class="btn" data-place>Platzieren</button>'}
      <span class="badge ${online ? 'ok' : 'bad'}">${online ? 'online' : 'offline'}</span></div>`);
    clickable(item, () => select({ kind: 'sensor', id }));
    item.querySelector('[data-place]')?.addEventListener('click', e => { e.stopPropagation(); setTool({ name: 'place', id }); });
    list.append(item);
  }
  if (!ids.length) list.append(h('<p class="note">Noch kein Sensor hat Daten geschickt.</p>'));

  const s = sel && sensorById(sel);
  if (s) sensorDetail(panel.querySelector('#detail'), s);

  panel.querySelector('#cov').onchange = e => { state.showCoverage = e.target.checked; refreshCoverage(view); renderCoverageStats(panel); };
  renderCoverageStats(panel);
}

function sensorDetail(el, s) {
  el.append(h(`<div class="card" style="margin-top:12px">
    <div class="grid2">
      <label class="field" style="grid-column: span 2">Name<input type="text" value="${esc(s.name)}" data-k="name"></label>
      <label class="field">x (m)<input type="number" step="0.01" value="${s.x}" data-k="x"></label>
      <label class="field">y (m)<input type="number" step="0.01" value="${s.y}" data-k="y"></label>
      <label class="field">Blickrichtung (°)<input type="number" step="1" value="${s.heading}" data-k="heading"></label>
      <label class="field">Montagehöhe (m)<input type="number" step="0.05" value="${s.height}" data-k="height"></label>
      <label class="field">Öffnungswinkel (°)<input type="number" step="5" value="${s.fov}" data-k="fov"></label>
      <label class="field">Reichweite (m)<input type="number" step="0.5" value="${s.range}" data-k="range"></label>
      <label class="field">Maßstab<input type="number" step="0.01" value="${s.scale}" data-k="scale"></label>
    </div>
    <p class="note">Maßstab: wahre Entfernung geteilt durch die gemessene. 1,04 = der Sensor misst 4 % zu kurz. Stellt die Kalibrierung ein.</p>
    <label class="check"><input type="checkbox" data-k="mirror" ${s.mirror ? 'checked' : ''}> x-Achse gespiegelt</label>
    <p class="note">Prüfen: Vor dem Sensor nach rechts gehen (vom Sensor aus gesehen). Der Punkt auf der Karte muss mitgehen, sonst Haken setzen. Die Kalibrierung erkennt das auch selbst.</p>
    <h3>Was der Sensor sieht</h3>
    <p class="note">Nur zum Anschauen: blendet eine Karte über den Grundriss ein. Das ändert nichts am Tracking.</p>
    <label class="field">Karte einblenden<select id="smap">${[['', 'keine'], ['prior', 'Erkennung: aus der Geometrie'],
      ['ghosts', 'Geister: im Betrieb gelernt']].map(([k, l]) =>
      `<option value="${k}" ${(state.sensorMap?.sensor === s.id ? state.sensorMap.layer : '') === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    <p id="smap-info" class="note"></p>
    <label class="check"><input type="checkbox" data-k="enabled" ${s.enabled ? 'checked' : ''}> Für die Verfolgung verwenden</label>
    <h3>Jetzt gemessen (Sensorkoordinaten)</h3>
    <div data-live="sensorNow" data-id="${esc(s.id)}"></div>
    <div class="row" style="margin-top:8px">
      <button class="btn" id="replace">Neu platzieren</button>
      ${s.placed ? '<button class="btn danger" id="unplace">Von Karte nehmen</button>' : ''}
    </div>
    <p class="note">ID: ${esc(s.id)}</p>
  </div>`));
  for (const input of el.querySelectorAll('[data-k]')) {
    const k = input.dataset.k;
    const set = v => panelEdit(c => { c.sensors.find(x => x.id === s.id)[k] = v; });
    if (input.type === 'number') bindNumber(input, state.limits.sensor?.[k], set);
    else input.onchange = () => set(input.type === 'checkbox' ? input.checked : input.value);
  }
  el.querySelector('#replace').onclick = () => setTool({ name: 'place', id: s.id });
  el.querySelector('#smap').onchange = e => {
    state.sensorMap = e.target.value ? { sensor: s.id, layer: e.target.value } : null;
    showSensorMap(el.querySelector('#smap-info'), s.id);
  };
  showSensorMap(el.querySelector('#smap-info'), s.id);
  el.querySelector('#unplace')?.addEventListener('click', () => {
    if (confirm(`„${s.name || s.id}“ von der Karte nehmen? Er zählt dann nicht mehr für die Verfolgung, das Modell startet neu.`)) deleteSelection();
  });
}

let mapView = null;

// Heatmap of one sensor's model: what it sees (from the geometry) or where it starts ghost tracks (learned)
async function showSensorMap(info, sensorId) {
  const view = mapView;
  const sel = state.sensorMap?.sensor === sensorId ? state.sensorMap : null;
  if (!sel) { view.sensorMapImage = null; view.render(); info.innerHTML = ''; return; }
  let data;
  try { data = await api('api/sensormodel?sensor=' + encodeURIComponent(sensorId)); } catch (e) { info.textContent = e.message; return; }
  const m = data.maps;
  if (!m || !m.cols) { info.textContent = 'Dafür braucht es Räume.'; return; }
  const grid = m[sel.layer];
  const canvas = document.createElement('canvas');
  canvas.width = m.cols; canvas.height = m.rows;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(m.cols, m.rows);
  if (!grid) { info.textContent = 'Keine Daten.'; return; }
  const maxGhosts = Math.max(1e-6, ...grid.flat().filter(v => v != null));
  for (let c = 0; c < m.cols; c++) {
    for (let r = 0; r < m.rows; r++) {
      const v = grid[c][r];
      if (v == null || (sel.layer !== 'ghosts' && v <= 0.01)) continue;
      const i = 4 * ((m.rows - 1 - r) * m.cols + c);
      if (sel.layer === 'ghosts') {
        img.data.set([214, 69, 69, Math.round(40 + 200 * Math.min(v / maxGhosts, 1))], i);
      } else {
        // low = red, high = green
        img.data.set([Math.round(220 * (1 - v)), Math.round(170 * v + 40), 70, 150], i);
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  view.sensorMapImage = { url: canvas.toDataURL(), x0: m.x0, y1: m.y0 + m.rows * m.cell, cell: m.cell, cols: m.cols, rows: m.rows };
  view.render();
  info.innerHTML = {
    prior: 'Aus der Geometrie: 0 hinter Wänden, fällt zum Rand des Sichtfelds und zur Reichweite hin ab. Rot = selten, grün = fast immer erkannt.',
    ghosts: `Wo der Sensor Spuren beginnt, die zu keiner Person gehören (Reflexionen), je röter, desto öfter; höchstens ${fmt(maxGhosts, 2)} je m² und Stunde. Gelernt über ${fmt(m.watched_h, 1)} h, beginnt neu, wenn ein Sensor versetzt wird.`,
  }[sel.layer];
}

export function refreshCoverage(view) {
  view.coverage = state.showCoverage ? computeCoverage(state.config) : null;
  view.render();
}

function renderCoverageStats(panel) {
  const el = panel.querySelector('#cov-stats');
  if (!el) return;
  const cov = state.showCoverage && computeCoverage(state.config);
  if (!state.showCoverage) { el.innerHTML = ''; return; }
  if (!cov) { el.innerHTML = '<p class="note">Dafür braucht es Zonen vom Typ „Raum“.</p>'; return; }
  el.innerHTML = `<table class="data"><tr><th>Raum</th><th>Fläche</th><th>tot</th><th>nur 1 Sensor</th></tr>${cov.rooms.map(r => `
    <tr><td>${esc(r.name)}</td><td>${fmt(r.area, 1)} m²</td>
    <td style="color:${r.blind > 0.05 ? 'var(--bad)' : 'inherit'}">${fmt(r.blind, 2)} m² (${fmt(100 * r.blind / Math.max(r.area, 1e-6), 0)} %)</td>
    <td>${fmt(100 * r.single / Math.max(r.area, 1e-6), 0)} %</td></tr>`).join('')}</table>
    <p class="note">Wände blockieren die Sicht. Möbel kennt die Karte nicht.</p>`;
}

// ------------------------------------------------------------------ zones

// zone kinds that are drawn by hand (rooms come from the walls)
const drawableKinds = () => Object.entries(ZONE_KINDS).filter(([k]) => k !== 'room');

function zonesPanel(panel, view) {
  const c = state.config;
  const selKind = state.selection?.kind;
  const sel = selKind === 'zone' || selKind === 'room' ? c.zones.find(z => z.id === state.selection.id) : null;
  const tool = state.tool?.name === 'zone' ? state.tool : null;
  if (!drawableKinds().some(([k]) => k === zoneKind)) zoneKind = 'area';
  panel.append(h(`<div>
    <h2>Zonen</h2>
    <p class="note">Räume entstehen aus den Wänden (Tab Grundriss). Hier kommen Bereiche und Eingänge dazu.</p>
    <div class="seg" id="kinds" style="margin-bottom:6px">${drawableKinds().map(([k, v]) => `<button data-kind="${k}" class="${zoneKind === k ? 'active' : ''}">${v.label}</button>`).join('')}</div>
    <p class="note">${ZONE_KINDS[zoneKind].note}</p>
    <div class="row">
      ${['rect', 'circle', 'polygon'].map(s => `<button class="btn ${tool?.shape === s ? 'active' : ''}" data-shape="${s}">${{ rect: 'Rechteck', circle: 'Kreis', polygon: 'Polygon' }[s]}</button>`).join('')}
    </div>
    <div id="detail"></div>
    <div id="groups"></div>
  </div>`));
  for (const b of panel.querySelectorAll('#kinds button')) b.onclick = () => { zoneKind = b.dataset.kind; emit('tool'); };
  for (const b of panel.querySelectorAll('[data-shape]')) {
    b.onclick = () => setTool(tool?.shape === b.dataset.shape ? null : { name: 'zone', shape: b.dataset.shape, kind: zoneKind });
  }
  const groups = panel.querySelector('#groups');
  for (const [kind, meta] of Object.entries(ZONE_KINDS)) {
    const zones = c.zones.filter(z => z.kind === kind);
    if (!zones.length) continue;
    groups.append(h(`<h3>${meta.label}</h3>`));
    const list = h('<div class="list"></div>');
    for (const z of zones) {
      const item = h(`<div class="item ${sel?.id === z.id ? 'selected' : ''}"><span class="swatch" style="background:${meta.color}"></span>
        <span class="grow">${esc(z.name)}</span></div>`);
      clickable(item, () => select({ kind: z.kind === 'room' ? 'room' : 'zone', id: z.id }));
      list.append(item);
    }
    groups.append(list);
  }
  if (sel?.kind === 'room') roomDetail(panel.querySelector('#detail'), sel);
  else if (sel) zoneDetail(panel.querySelector('#detail'), sel);
}

function zoneDetail(el, z) {
  const area = (() => {
    const pts = zoneOutline(z, 64);
    let a = 0;
    for (let i = 0; i < pts.length; i++) { const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length]; a += x1 * y2 - x2 * y1; }
    return Math.abs(a) / 2;
  })();
  el.append(h(`<div class="card">
    <label class="field">Name<input type="text" value="${esc(z.name)}" id="name"></label>
    <div class="grid2" style="margin-top:8px">
      <label class="field">Typ<select id="kind">${drawableKinds().map(([k, v]) => `<option value="${k}" ${z.kind === k ? 'selected' : ''}>${v.label}</option>`).join('')}</select></label>
      <label class="field">Fläche<input type="text" value="${fmt(area)} m²" disabled></label>
    </div>
    ${z.kind === 'room' || z.kind === 'area' ? `<div data-live="zoneNow" data-id="${esc(z.id)}"></div>` : ''}
    <p class="note">Ecken und Seiten ziehen, die Winkel bleiben dabei erhalten. Nochmal anklicken und ziehen verschiebt die ganze Zone. Bei Polygonen: Doppelklick auf eine Kante fügt einen Punkt ein, auf einen Punkt löscht ihn.</p>
    <button class="btn danger" id="del">Zone löschen</button>
  </div>`));
  el.querySelector('#name').onchange = e => panelEdit(c => { c.zones.find(x => x.id === z.id).name = e.target.value; });
  el.querySelector('#kind').onchange = e => edit(c => { c.zones.find(x => x.id === z.id).kind = e.target.value; });
  el.querySelector('#del').onclick = deleteSelection;
}

// ------------------------------------------------------------ calibration

function calibrationPanel(panel, view) {
  const c = state.config;
  const placed = c.sensors.filter(s => s.placed && s.enabled);
  const result = state.calibResult;
  panel.append(h(`<div>
    <h2>Kalibrierung</h2>
    <ol class="steps">
      <li>Sensoren genau an ihrer Position einzeichnen, mit Montagehöhe und der x-Richtung, wie sie eingebaut sind. Die Blickrichtung muss nur grob stimmen.</li>
      <li>Die App sammelt laufend, wo Gehende gemessen werden (die letzten 24 Stunden). Ein Tag normales Leben reicht meist. Vorgeschlagen wird für einen Sensor erst etwas, wenn er in mindestens 3 verschiedenen Stunden Gehende gemessen hat: Ein einzelner Gang zeigt nicht, ob die Lage auch im Alltag passt. Ein Gang allein kreuz und quer durch die Räume und Türen ergänzt den Alltag um Stellen, an die man sonst selten kommt.</li>
      <li>Berechnen, Ergebnis prüfen und übernehmen.</li>
    </ol>
    <p class="note">Die eingezeichneten Positionen und die x-Richtung bleiben. Blickrichtung und Maßstab (wie viel zu kurz oder zu lang ein Sensor misst) folgen für alle Sensoren gemeinsam aus drei Dingen: Was zwei Sensoren gleichzeitig sehen, muss übereinanderliegen. Wer aus dem Blickfeld eines Sensors in das eines anderen geht, geht dazwischen weiter. Und wer geht, ist in einem Raum und geht durch Türen, nicht durch Wände. Übernommen wird nur, was die Messungen sicher bestimmen.</p>
    <div class="row" style="margin-top:10px">
      <button class="btn primary" id="solve" ${placed.length < 1 ? 'disabled' : ''}>Berechnen</button>
      <button class="btn" id="reset">Neu sammeln</button>
    </div>
    <label class="check"><input type="checkbox" id="mirror" ${state.calibMirror ? 'checked' : ''}> Auch die x-Richtung prüfen (nur nötig, wenn unklar ist, wie ein Sensor eingebaut ist)</label>
    <div id="result"></div>
    <p class="note"><i>Neu sammeln</i> nach dem Drehen oder Versetzen eines Sensors: seine alten Messungen passen dann nicht mehr.</p>
    <div data-live="calib"></div>
    <details class="pairs"><summary>Gleichzeitig gesehen: Sekunden, in denen zwei Sensoren je genau einen Gehenden sahen</summary><div class="list" data-live="calibPairs"></div></details>
    <h3>Sichtprüfung</h3>
    <p class="note">Die Messpunkte aller Sensoren werden auf der Karte gezeigt. Wo zwei Sensoren denselben Bereich sehen, müssen die Punkte einer Person übereinanderliegen und mitlaufen; Wege führen durch Türen, nicht durch Wände.</p>
  </div>`));
  panel.querySelector('#mirror').onchange = e => { state.calibMirror = e.target.checked; };
  panel.querySelector('#reset').onclick = async () => {
    const cal = state.live?.calibration;
    const hours = cal?.since && state.live?.t ? Math.max(0, (state.live.t - cal.since) / 3600) : null;
    if (!confirm(`Neu sammeln? Das verwirft alle gesammelten Messungen in Bewegung${hours != null ? ` (${fmt(hours, 1)} h)` : ''}. `
      + 'Für einen Vorschlag braucht es danach wieder Gehende aus mindestens 3 verschiedenen Stunden. Nur nach dem Drehen oder Versetzen eines Sensors nötig.')) return;
    await api('api/calibration/reset', { method: 'POST' });
    state.calibResult = null;
    toast('Gesammelte Messungen verworfen. Die App sammelt neu.');
    setTimeout(() => emit('tab'), 300);
  };
  panel.querySelector('#solve').onclick = async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = 'Rechnet …';
    try {
      state.calibResult = await api('api/calibration/solve', { method: 'POST', body: JSON.stringify({ mirror: !!state.calibMirror }) });
      state.calibScroll = true;
      emit('tab');
    } catch (err) {
      toast(err.message, 5000);
      btn.disabled = false;
      btn.textContent = 'Berechnen';
    }
  };
  if (result) {
    const el = panel.querySelector('#result');
    calibrationResult(el, result);
    // the result is what was asked for: in view, right under the button
    if (state.calibScroll) { state.calibScroll = false; requestAnimationFrame(() => el.scrollIntoView({ block: 'start', behavior: 'smooth' })); }
  }
}

function calibrationResult(el, r) {
  if (r.error) { el.innerHTML = `<p class="note" style="color:var(--bad)">${esc(r.error)}</p>`; return; }
  const label = { ok: 'gut', warn: 'teilweise', bad: 'bleibt' };
  const cls = { ok: 'ok', warn: 'warn', bad: '' };  // "bleibt" is mostly "not enough data yet", no error
  const entries = Object.entries(r.sensors);
  const need = r.min_hours || 3;
  const name = id => esc(sensorById(id)?.name || id);
  // what holds for several sensors, said once
  const few = entries.filter(([, s]) => s.few);
  const common = [
    few.length ? `<p class="note"><b>Noch zu wenig Daten:</b> ${few.map(([id, s]) => `${name(id)} (${s.stretches}/${need}\u00a0h)`).join(', ')}. Ein Vorschlag braucht Gehende aus mindestens ${need} verschiedenen Stunden: Ein einzelner Gang zeigt nicht, ob die Lage auch im Alltag passt. Später erneut berechnen, die App sammelt weiter.</p>` : '',
    entries.some(([, s]) => s.walk_more) ? '<p class="note">Wo etwas unsicher bleibt, hilft es, mehr kreuz und quer durch den Raum des Sensors und durch die Türen zu den Nachbarräumen zu gehen.</p>' : '',
  ].join('');
  const pm = (v, sd, d) => `${fmt(v, d)} <span class="meta">± ${fmt(sd, d)}</span>`;
  // one block per sensor (the side panel is too narrow for a table with the explanations)
  const rows = entries.map(([id, s]) => {
    const cur = sensorById(id);
    const turn = s.apply.heading ? `${fmt(cur?.heading ?? 0, 1)}° → ${fmt(s.heading, 1)}° <span class="meta">(${s.turn > 0 ? '+' : ''}${pm(s.turn, s.heading_sd, 1)}°)</span>` : 'bleibt';
    const scale = s.apply.scale ? `${fmt(cur?.scale ?? 1, 3)} → ${pm(s.scale, s.scale_sd, 3)}` : 'bleibt';
    const out = s.outside.map(v => Math.round(100 * v));
    return `<div class="calib-sensor">
      <div class="row"><b class="grow">${esc(cur?.name || id)}</b><span class="badge ${cls[s.quality]}">${label[s.quality]}</span></div>
      <dl class="kv"><dt>Drehung</dt><dd>${turn}</dd><dt>Maßstab</dt><dd>${scale}</dd>
        ${s.mirror !== cur?.mirror ? '<dt>x-Richtung</dt><dd><b>ändern</b></dd>' : ''}</dl>
      <p class="note">${s.points} Punkte in Bewegung aus ${s.stretches}/${need}\u00a0h, ${s.pairs}\u00a0s gleichzeitig mit anderen Sensoren gesehen (${s.agree} passend), ${s.handovers} Übergänge. Außerhalb der Sicht: ${out[0]}\u00a0% → ${out[1]}\u00a0%.${s.reason && !s.few ? ' ' + esc(s.reason) : ''}</p>
    </div>`;
  }).join('');
  const usable = entries.filter(([, s]) => s.apply.heading || s.apply.scale);
  el.append(h(`<div class="card" style="margin-top:12px">
    <b>Ergebnis</b>
    ${common}
    <div style="margin-top:8px">${rows || '<p class="note">Nichts berechnet.</p>'}</div>
    ${entries.length && r.inside != null ? `<p class="note">${Math.round(100 * r.inside)} % der Punkte in Bewegung liegen da, wo ihr Sensor hinsieht.</p>` : ''}
    ${r.unsolved.length ? `<p class="note">Bleiben, wie sie sind: ${r.unsolved.map(name).join(', ')}. Keine Messungen in Bewegung.</p>` : ''}
    ${usable.length ? `<button class="btn primary" id="apply">Übernehmen</button>` : '<p class="note">Nichts sicher bestimmt, nichts zu übernehmen.</p>'}
  </div>`));
  el.querySelector('#apply')?.addEventListener('click', () => {
    edit(c => {
      for (const [id, s] of usable) {
        const sc = c.sensors.find(x => x.id === id);
        if (s.apply.heading) sc.heading = s.heading;
        if (s.apply.scale) sc.scale = s.scale;
        if (s.mirror !== sc.mirror && s.apply.heading) sc.mirror = s.mirror;
      }
    });
    state.calibResult = null;
    toast(`${usable.length} Sensoren übernommen`);
    emit('tab');
  });
}

// --------------------------------------------------------------- settings

const PARAMS = [
  ['Messung', [
    ['target_height', 'Höhe des Oberkörpers', 'm', 'Für die Umrechnung des schrägen Abstands auf den Boden.', 0.05],
    ['range_sigma_base', 'Messfehler in Blickrichtung, Grundwert', 'm', 'Fehler = Grundwert + Anstieg × Abstand. Größer = einzelne Messungen zählen weniger.', 0.01],
    ['range_sigma_slope', 'Messfehler in Blickrichtung, Anstieg', 'm/m', '', 0.005],
    ['lateral_sigma_base', 'Messfehler seitlich, Grundwert', 'm', 'Seitlich wächst der Fehler schneller mit dem Abstand (Winkelfehler).', 0.01],
    ['lateral_sigma_slope', 'Messfehler seitlich, Anstieg', 'm/m', '', 0.005],
    ['wall_margin', 'Toleranz an Wänden', 'm', 'Messpunkte weiter hinter einer Wand oder außerhalb aller Räume sind Reflexionen und werden verworfen.', 0.05],
  ]],
  ['Räume ohne Sensor', [
    ['dwell_median', 'Typischer Aufenthalt', 's', 'Annahme für Räume ohne Sensor. Das Treppenhaus zählt nicht dazu: Es liegt außer Haus.', 10],
    ['dwell_spread', 'Streuung des Aufenthalts', '', 'Streuung von ln(Dauer): breit, damit lange Aufenthalte möglich bleiben.', 0.1],
  ]],
  ['LD2410C', [
    ['ld2410_hold', 'Haltezeit (Anzeige)', 's', 'Lücken in der LD2410C-Präsenz bis zu dieser Länge werden in der Anzeige überbrückt.', 0.1],
  ]],
  ['Ausgabe', [
    ['light_cost', 'Kosten: Licht ohne Person', '×', 'Eine Sekunde Licht ohne Person ist so schlimm wie so viele Sekunden Dunkel mit Person. Ein Raum gilt als besetzt, wenn die Wahrscheinlichkeit über Kosten / (Kosten + 1) liegt.', 0.5],
    ['seen_hold', 'Haltezeit eigener LD2450', 's', 'Ein Raum gilt auch als besetzt, solange der LD2450 dieses Raums in den letzten so vielen Sekunden ein gemessenes Ziel im Raum hatte (nicht gehalten, nicht hinter einer Wand), auch wenn der Filter „leer“ sagt. Nur der Sensor des Raums zählt: Andere messen Personen an Raumgrenzen im Nachbarraum. 0 = nur der Filter. Gemessen mit 10 s: dunkel mit Person 25,6 → 0,6 min, Licht im leeren Raum 4,3 → 5,7 min.', 1],
    ['lead_time', 'Vorausschau „wird betreten“', 's', 'So weit rechnet das Modell jeden Gehenden mit seinem eigenen Bewegungsmodell voraus (Wände halten auf, Türen nicht). Weil es Richtung und Tempo mit der Zeit vergisst, kommt das Signal später als diese Zeit vor dem Eintritt: gemessen mit 2 s: bei 60 % der Eintritte mindestens 1 s (1 m) vorher.', 0.1],
    ['approach_cost', 'Kosten: Einschalten auf Verdacht', '×', 'Ein Einschalten auf Verdacht, nach dem niemand hereinkommt, ist so schlimm wie so viele Eintritte in einen dunklen Raum. „Wird betreten“ gilt, wenn die Wahrscheinlichkeit über Kosten / (Kosten + 1) liegt.', 0.01],
    ['target_threshold', 'Schwelle „Ziel“', '', '„Ziel“ ist an, wenn die Wahrscheinlichkeit, dass ein Gehender als Nächstes in den Raum geht, mindestens so hoch ist. Sie entsteht aus der Bewegung (wie „wird betreten“) und der gelernten Karte, wohin die Gänge von dort bisher gingen; wo die Karte noch nichts weiß, ist „Ziel“ genau „wird betreten“. Schwelle = K_Fehl / (K_Fehl + K_spät): 0,8 heißt, ein vergebliches Licht ist so schlimm wie 4 späte. Je Raum unten anders einstellbar.', 0.05],
  ]],
];

// what describes the home and the output; the rest are the model's measured numbers
const HOME_GROUPS = ['Ausgabe'];

// the threshold that follows from a cost, shown next to it
const DERIVED = {
  light_cost: k => `Damit: besetzt ab ${Math.round(100 * k / (k + 1))}\u00a0%.`,
  approach_cost: k => `Damit: „wird betreten“ ab ${Math.round(100 * k / (k + 1))}\u00a0%.`,
};

function roomName(id) {
  return state.config.zones.find(z => z.id === id)?.name || id;
}

// the threshold of "Ziel" per room: empty = the default above
function targetRooms(p) {
  const el = h(`<div><h3>Schwelle „Ziel“ je Raum</h3><p class="note">Leer: die Schwelle oben. Gemessen: 0,8 halbiert die vergeblichen Lichter gegenüber „wird betreten“, kommt aber seltener früh; 0,5 kommt öfter früh und ist trotzdem seltener vergeblich.</p></div>`);
  for (const z of roomsWithSensor(state.config, state.live)) {
    const v = (p.target_thresholds || {})[z.id];
    const row = h(`<div class="param"><label class="field">${esc(z.name)}
      <input type="number" step="0.05" min="0" max="1" placeholder="${p.target_threshold}" value="${v ?? ''}"></label></div>`);
    // empty: the default above
    bindNumber(row.querySelector('input'), state.limits.params?.target_threshold, v => panelEdit(c => {
      const t = { ...(c.params.target_thresholds || {}) };
      if (v == null) delete t[z.id]; else t[z.id] = v;
      c.params.target_thresholds = t;
    }), { allowEmpty: true });
    el.append(row);
  }
  return el;
}

function settingsPanel(panel) {
  const p = state.config.params;
  const root = h('<div><h2>Einstellungen</h2><p class="note">Änderungen wirken sofort. Dabei startet das Modell neu (außer bei den Schwellen „Ziel“): Es behält, was es über die Personen weiß, wenn der Grundriss gleich bleibt.</p></div>');
  const experts = h(`<details style="margin-top:16px"><summary><b>Experten: Werte des Modells</b></summary>
    <p class="note">Gemessen oder aus Messungen abgeleitet. Wer sie ändert, ändert das Modell; die Auswertung mit
    bekannter Wahrheit gilt dann nicht mehr. Nur zum Ausprobieren.</p></details>`);
  for (const [group, items] of PARAMS) {
    const target = HOME_GROUPS.includes(group) ? root : experts;
    target.append(h(`<h3>${group}</h3>`));
    for (const [key, label, unit, desc, step] of items) {
      const el = h(`<div class="param"><label class="field">${label}${unit ? ` (${unit})` : ''}
        <input type="number" step="${step}" value="${p[key]}"></label>${desc ? `<div class="desc">${desc}</div>` : ''}
        ${DERIVED[key] ? `<div class="desc derived">${DERIVED[key](p[key])}</div>` : ''}</div>`);
      bindNumber(el.querySelector('input'), state.limits.params?.[key], v => {
        panelEdit(c => { c.params[key] = v; });
        if (DERIVED[key]) el.querySelector('.derived').textContent = DERIVED[key](v);
      });
      target.append(el);
    }
  }
  root.append(targetRooms(p));
  root.append(experts);
  const reset = h('<button class="btn" style="margin-top:12px">Alle auf Standard</button>');
  reset.onclick = async () => {
    if (!confirm('Alle Einstellungen zurücksetzen?')) return;
    edit(c => { c.params = {}; });
    await saveNow();  // the reload shows what the server took over (a fixed delay could cut the request off)
    location.reload();
  };
  root.append(reset);
  panel.append(root);
}
