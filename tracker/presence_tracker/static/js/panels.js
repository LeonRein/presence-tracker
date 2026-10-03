// Side panel per tab.
import { computeCoverage } from './coverage.js';
import { api, edit, emit, select, sensorById, sensorColor, setTool, state } from './store.js';
import { AlignTool, PlaceSensorTool, WallTool, ZoneTool, autoFitImage, deleteSelection } from './tools.js';
import { ZONE_KINDS, dist, esc, fmt, h, toast, uid, zoneOutline } from './util.js';
import { loadSize } from './view.js';

let skipRender = false;
let zoneKind = 'area';
let calibAnchor = null;

// edits from panel inputs: don't rebuild the panel under the user's cursor
function panelEdit(fn, opts) {
  skipRender = true;
  try { edit(fn, opts); } finally { skipRender = false; }
}

export function renderPanel(panel, view, what) {
  if (skipRender && what === 'config') return;
  const fn = { live: livePanel, plan: planPanel, sensors: sensorsPanel, zones: zonesPanel, calibration: calibrationPanel, settings: settingsPanel }[state.tab];
  panel.innerHTML = '';
  fn(panel, view);
  updateLive(panel);
}

// parts of the panel that change with every live message
export function updateLive(panel) {
  for (const el of panel.querySelectorAll('[data-live]')) {
    const fn = LIVE[el.dataset.live];
    if (fn) el.innerHTML = fn(el.dataset);
  }
}

const LIVE = {
  zones() {
    const live = state.live;
    const zones = state.config.zones.filter(z => z.kind === 'room' || z.kind === 'area');
    if (!zones.length) return '<p class="note">Noch keine Räume oder Bereiche. Im Tab „Zonen“ einzeichnen.</p>';
    return zones.map(z => {
      const st = live?.zones?.[z.id];
      const color = ZONE_KINDS[z.kind].color;
      const badges = st ? [
        st.count ? `<span class="badge on">${st.count} ${st.count === 1 ? 'Person' : 'Personen'}</span>` : '<span class="badge">leer</span>',
        st.moving ? `<span class="badge ok">${st.moving} bewegt</span>` : '',
        st.still ? `<span class="badge">${st.still} ruhig</span>` : '',
        st.approaching ? `<span class="badge warn">gleich${st.eta != null ? ` (${st.eta.toFixed(1)} s)` : ''}</span>` : '',
      ].join('') : '';
      return `<div class="item"><span class="swatch" style="background:${color}"></span><span class="grow">${esc(z.name)}</span>${badges}</div>`;
    }).join('');
  },
  total() {
    const t = state.live?.zones?._total;
    if (!t) return '–';
    return `<b style="font-size:28px">${t.count}</b> <span class="note">${t.count === 1 ? 'Person' : 'Personen'} · ${t.moving} bewegt · ${t.still} ruhig</span>`;
  },
  tracks() {
    const tracks = state.live?.tracks || [];
    if (!tracks.length) return '<p class="note">Keine Spuren.</p>';
    return `<table class="data"><tr><th>#</th><th>Status</th><th>x</th><th>y</th><th>v</th><th>Gehen</th><th>Alter</th></tr>${tracks.map(t => `
      <tr><td>${t.id}</td><td>${t.status === 'tentative' ? 'neu?' : t.lost ? 'verdeckt' : 'aktiv'}</td>
      <td>${fmt(t.x)}</td><td>${fmt(t.y)}</td><td>${fmt(Math.hypot(t.vx, t.vy), 1)}</td>
      <td>${Math.round(t.walk * 100)} %</td><td>${fmt(t.age, 0)} s</td></tr>`).join('')}</table>`;
  },
  sensorHealth() {
    const live = state.live;
    const ids = [...new Set([...state.config.sensors.map(s => s.id), ...Object.keys(live?.sensors || {})])].sort();
    return ids.map(id => {
      const sv = live?.sensors?.[id];
      const s = sensorById(id);
      const online = sv?.online;
      return `<div class="item"><span class="swatch" style="background:${sensorColor(id)}"></span>
        <span class="grow">${esc(s?.name || id)}</span>
        ${s?.placed ? '' : '<span class="badge warn">nicht platziert</span>'}
        <span class="badge ${online ? 'ok' : 'bad'}">${online ? 'online' : 'offline'}</span>
        <span class="meta">${sv ? sv.detections.length : 0} Ziele${sv?.ld2410?.present ? ` · LD2410 ${fmt(sv.ld2410.distance, 1)} m` : ''}</span></div>`;
    }).join('') || '<p class="note">Noch keine Daten von Sensoren.</p>';
  },
  load() {
    const l = state.live?.load;
    return l ? `${l.fps} Frames/s · ${l.cpu} % CPU` : '';
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
      <dt>wird betreten</dt><dd>${st.approaching ? `ja, in ${fmt(st.eta, 1)} s` : 'nein'}</dd></dl>`;
  },
  calib() {
    const c = state.live?.calibration;
    if (!c) return '<p class="note">Keine Aufnahme aktiv.</p>';
    const frames = Object.entries(c.frames).map(([id, n]) => `<div class="item"><span class="swatch" style="background:${sensorColor(id)}"></span><span class="grow">${esc(sensorById(id)?.name || id)}</span><span class="meta">${n} Frames mit genau 1 Ziel</span></div>`).join('');
    const pairs = Object.entries(c.pairs).map(([k, n]) => {
      const [a, b] = k.split('|');
      const ok = n >= 150 ? 'ok' : n >= 30 ? 'warn' : 'bad';
      return `<div class="item"><span class="grow">${esc(sensorById(a)?.name || a)} ↔ ${esc(sensorById(b)?.name || b)}</span><span class="badge ${ok}">${n} Paare</span></div>`;
    }).join('');
    return `${frames}<h3>Gemeinsam gesehen</h3>${pairs || '<p class="note">Noch keine Überschneidung. Durch Bereiche gehen, die zwei Sensoren sehen.</p>'}`;
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
      <text x="${x + w / 2}" y="${hgt + 12}" font-size="9" text-anchor="middle" fill="var(--muted)">${(i * 0.75).toFixed(1)}</text>`;
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
    <h3>Räume und Bereiche</h3><div class="list" data-live="zones"></div>
    <h3>Sensoren</h3><div class="list" data-live="sensorHealth"></div>
    <h3>Spuren</h3><div data-live="tracks"></div>
    <h3>Anzeige</h3>
    <label class="check"><input type="checkbox" id="raw" ${state.showRaw ? 'checked' : ''}> Rohdaten der Sensoren zeigen</label>
    <div class="legend">
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--moving)"/></svg>bewegt</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--still)"/></svg>ruhig</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--lost)"/></svg>verdeckt</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="3" fill="var(--muted)"/></svg>Messpunkt</span>
      <span><svg width="16" height="12"><path d="M1 10 A 9 9 0 0 1 15 10" stroke="var(--muted)" stroke-dasharray="2 3" fill="none" stroke-width="2"/></svg>LD2410C-Abstand</span>
    </div>
    <div class="row" style="margin-top:12px"><button class="btn" id="reset">Spuren neu aufnehmen</button></div>
    <p class="note">Löscht alle Spuren. In den nächsten Sekunden dürfen Personen überall neu erkannt werden.</p>
    <p class="note" data-live="load"></p>
  </div>`));
  panel.querySelector('#raw').onchange = e => { state.showRaw = e.target.checked; view.render(); };
  panel.querySelector('#reset').onclick = async () => { await api('api/tracks/reset', { method: 'POST' }); toast('Spuren gelöscht'); };
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
  else detail.append(h('<p class="note">Wand, Tür oder Raum anklicken zum Bearbeiten. Rückgängig: Strg+Z.</p>'));

  const roomList = panel.querySelector('#rooms');
  for (const z of rooms) {
    const st = state.live?.zones?.[z.id];
    const item = h(`<div class="item ${sel?.kind === 'room' && sel.id === z.id ? 'selected' : ''}">
      <span class="swatch" style="background:${z.entry ? ZONE_KINDS.entry.color : ZONE_KINDS.room.color}"></span>
      <span class="grow">${esc(z.name)}</span>${z.entry ? '<span class="badge warn">Eingang</span>' : ''}
      <span class="meta">${zoneArea(z).toFixed(1)} m²</span>${st ? `<span class="badge ${st.count ? 'on' : ''}">${st.count}</span>` : ''}</div>`);
    item.onclick = () => select({ kind: 'room', id: z.id });
    roomList.append(item);
  }
  if (!rooms.length) roomList.append(h('<p class="note">Noch keine geschlossenen Räume.</p>'));

  const list = panel.querySelector('#layers');
  for (const l of c.background.layers) {
    const item = h(`<div class="item ${sel?.kind === 'layer' && sel.id === l.id ? 'selected' : ''}">
      <input type="checkbox" ${l.visible ? 'checked' : ''} title="anzeigen">
      <span class="grow">${esc(l.name)}</span><span class="meta">${Math.round((l.opacity ?? 0.6) * 100)} %</span></div>`);
    item.onclick = e => { if (e.target.tagName !== 'INPUT') select({ kind: 'layer', id: l.id }); };
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
    <p class="note">${dist(w.points[0], w.points[1]).toFixed(2)} m · Enden oder die ganze Wand ziehen (rastet in 45°-Schritten und an Wänden ein). Angeschlossene Wände gehen mit. Doppelklick auf die Wand teilt sie, auf ein Ende verbindet es mit der anschließenden Wand.</p>
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
  el.querySelector('#width').onchange = e => panelEdit(c => { c.doors.find(d => d.id === id).width = Math.max(0.3, +e.target.value); });
}

// name and entry flag of a room; its outline comes from the walls
function roomDetail(el, z) {
  const st = state.live?.zones?.[z.id];
  el.append(h(`<div class="card">
    <label class="field">Raum<input type="text" value="${esc(z.name)}" id="name"></label>
    <label class="check" style="margin-top:8px"><input type="checkbox" id="entry" ${z.entry ? 'checked' : ''}> Eingang (Treppenhaus, Haustür)</label>
    <p class="note">Hier dürfen Personen auftauchen und verschwinden. Fläche ${zoneArea(z).toFixed(2)} m²${st ? `, jetzt ${st.count} ${st.count === 1 ? 'Person' : 'Personen'}` : ''}.</p>
    <p class="note">Die Form folgt den Wänden. Zum Ändern im Tab Grundriss die Wände verschieben.</p>
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
    input.onchange = () => edit(c => {
      const layer = c.background.layers.find(x => x.id === id);
      const v = +input.value;
      layer[input.dataset.k] = input.dataset.k === 'scale' ? v / 1000 : v;
    });
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
    item.onclick = () => select({ kind: 'sensor', id });
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
    </div>
    <label class="check"><input type="checkbox" data-k="mirror" ${s.mirror ? 'checked' : ''}> x-Achse gespiegelt</label>
    <p class="note">Prüfen: Vor dem Sensor nach rechts gehen (vom Sensor aus gesehen). Der Punkt auf der Karte muss mitgehen, sonst Haken setzen. Die Kalibrierung erkennt das auch selbst.</p>
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
    input.onchange = () => panelEdit(c => {
      const t = c.sensors.find(x => x.id === s.id);
      const k = input.dataset.k;
      t[k] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? +input.value : input.value;
    });
  }
  el.querySelector('#replace').onclick = () => setTool({ name: 'place', id: s.id });
  el.querySelector('#unplace')?.addEventListener('click', deleteSelection);
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
    <p class="note">Räume entstehen aus den Wänden (Tab Grundriss). Hier kommen Bereiche, Eingänge und Störer dazu.</p>
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
      const st = state.live?.zones?.[z.id];
      const item = h(`<div class="item ${sel?.id === z.id ? 'selected' : ''}"><span class="swatch" style="background:${meta.color}"></span>
        <span class="grow">${esc(z.name)}</span>${st ? `<span class="badge ${st.count ? 'on' : ''}">${st.count}</span>` : ''}</div>`);
      item.onclick = () => select({ kind: z.kind === 'room' ? 'room' : 'zone', id: z.id });
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
      <label class="field">Fläche<input type="text" value="${area.toFixed(2)} m²" disabled></label>
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
  calibAnchor = placed.some(s => s.id === calibAnchor) ? calibAnchor : placed[0]?.id;
  const active = !!state.live?.calibration;
  const result = state.calibResult;
  panel.append(h(`<div>
    <h2>Kalibrierung</h2>
    <ol class="steps">
      <li>Sensoren grob auf der Karte platzieren und ausrichten.</li>
      <li>Den Sensor wählen, dessen Lage am sichersten stimmt (Anker). Er bleibt, wie er ist.</li>
      <li>Aufnahme starten. <b>Allein</b> 2–3 Minuten langsam durch alle Bereiche gehen, die zwei Sensoren gleichzeitig sehen. Kreuz und quer, auch nah an den Rändern.</li>
      <li>Berechnen, Ergebnis prüfen und übernehmen.</li>
    </ol>
    <label class="field">Anker<select id="anchor">${placed.map(s => `<option value="${esc(s.id)}" ${s.id === calibAnchor ? 'selected' : ''}>${esc(s.name || s.id)}</option>`).join('')}</select></label>
    <div class="row" style="margin-top:10px">
      <button class="btn ${active ? '' : 'primary'}" id="toggle" ${placed.length < 2 ? 'disabled' : ''}>${active ? 'Aufnahme stoppen' : 'Aufnahme starten'}</button>
      <button class="btn ${active ? 'primary' : ''}" id="solve" ${placed.length < 2 ? 'disabled' : ''}>Berechnen</button>
    </div>
    ${placed.length < 2 ? '<p class="note">Dafür müssen mindestens zwei Sensoren platziert sein.</p>' : ''}
    <div data-live="calib"></div>
    <div id="result"></div>
    <h3>Sichtprüfung</h3>
    <p class="note">Die Messpunkte aller Sensoren werden auf der Karte gezeigt. Sieht ein Bereich zwei Sensoren, müssen die Punkte einer Person übereinanderliegen und mitlaufen.</p>
  </div>`));
  panel.querySelector('#anchor').onchange = e => { calibAnchor = e.target.value; };
  panel.querySelector('#toggle').onclick = async () => {
    await api('api/calibration/' + (active ? 'stop' : 'start'), { method: 'POST' });
    if (!active) state.calibResult = null;
    toast(active ? 'Aufnahme gestoppt' : 'Aufnahme läuft. Jetzt allein durch die Überschneidungen gehen.');
    setTimeout(() => emit('tab'), 300);
  };
  panel.querySelector('#solve').onclick = async () => {
    try {
      state.calibResult = await api('api/calibration/solve', { method: 'POST', body: JSON.stringify({ anchor: calibAnchor }) });
      emit('tab');
    } catch (e) { toast(e.message, 5000); }
  };
  if (result) calibrationResult(panel.querySelector('#result'), result);
}

function calibrationResult(el, r) {
  if (r.error) { el.innerHTML = `<p class="note" style="color:var(--bad)">${esc(r.error)}</p>`; return; }
  const rows = Object.entries(r.sensors).map(([id, s]) => {
    const cur = sensorById(id);
    const quality = s.rms < 0.15 && s.inliers / s.pairs > 0.7 ? 'ok' : s.rms < 0.25 ? 'warn' : 'bad';
    return `<tr><td><label class="check"><input type="checkbox" data-id="${esc(id)}" checked>${esc(cur?.name || id)}</label></td>
      <td>${fmt(s.shift * 100, 0)} cm</td><td>${s.turn > 0 ? '+' : ''}${fmt(s.turn, 1)}°</td>
      <td>${s.mirror !== cur?.mirror ? '<b>ändern</b>' : '–'}</td>
      <td><span class="badge ${quality}">${fmt(s.rms * 100, 0)} cm</span></td><td>${s.inliers}/${s.pairs}</td></tr>`;
  }).join('');
  el.append(h(`<div class="card" style="margin-top:12px">
    <b>Ergebnis</b> <span class="note">(Anker: ${esc(sensorById(r.anchor)?.name || r.anchor)})</span>
    <table class="data" style="margin-top:8px"><tr><th>Sensor</th><th>Versatz</th><th>Drehung</th><th>Spiegel</th><th>Fehler</th><th>Paare</th></tr>${rows || '<tr><td colspan="6">nichts berechnet</td></tr>'}</table>
    ${r.unsolved.length ? `<p class="note">Nicht lösbar (zu wenig gemeinsame Daten): ${r.unsolved.map(id => esc(sensorById(id)?.name || id)).join(', ')}</p>` : ''}
    <p class="note">Fehler: mittlere Abweichung zwischen den Sensoren nach der Korrektur. Unter 15 cm ist gut.</p>
    ${rows ? '<button class="btn primary" id="apply">Übernehmen</button>' : ''}
  </div>`));
  el.querySelector('#apply')?.addEventListener('click', () => {
    const ids = [...el.querySelectorAll('input[data-id]:checked')].map(i => i.dataset.id);
    edit(c => {
      for (const id of ids) {
        const s = c.sensors.find(s => s.id === id);
        Object.assign(s, { x: r.sensors[id].x, y: r.sensors[id].y, heading: r.sensors[id].heading, mirror: r.sensors[id].mirror });
      }
    });
    state.calibResult = null;
    toast(`${ids.length} Sensor(en) übernommen`);
    emit('tab');
  });
}

// --------------------------------------------------------------- settings

const PARAMS = [
  ['Messung', [
    ['target_height', 'Höhe des Oberkörpers', 'm', 'Für die Umrechnung des schrägen Abstands auf den Boden.', 0.05],
    ['sigma_range', 'Messrauschen Abstand', 'm', 'Größer = Filter glättet stärker, reagiert langsamer.', 0.01],
    ['sigma_angle', 'Messrauschen Winkel', '°', 'Seitliche Ungenauigkeit des LD2450.', 0.5],
    ['sigma_speed', 'Messrauschen Geschwindigkeit', 'm/s', '', 0.05],
  ]],
  ['Bewegung', [
    ['walk_accel', 'Beschleunigung beim Gehen', 'm/s²', 'Wie abrupt Richtung und Tempo wechseln dürfen.', 0.1],
    ['still_jitter', 'Unruhe beim Sitzen', 'm/√s', 'Wie weit eine ruhende Person „wandert“.', 0.01],
    ['walk_to_still', 'Wechsel gehen → ruhig', '1/s', '', 0.1],
    ['still_to_walk', 'Wechsel ruhig → gehen', '1/s', '', 0.1],
  ]],
  ['Neue Personen', [
    ['confirm_time_entry', 'Bestätigung im Eingang', 's', 'So lange muss eine neue Person in einer Eingangszone gesehen werden.', 0.1],
    ['confirm_time', 'Bestätigung sonst', 's', 'Mitten im Raum taucht niemand einfach auf: hier braucht es länger.', 0.1],
    ['confirm_ratio', 'Anteil Frames', '', 'Anteil der Frames, in denen die neue Person dabei sein muss.', 0.05],
    ['warmup', 'Anlaufzeit', 's', 'Nach dem Start dürfen Personen überall sofort erkannt werden.', 1],
  ]],
  ['Verdeckte Personen', [
    ['lost_after', 'Als verdeckt gelten nach', 's', '', 0.1],
    ['coast_time', 'Weiterlaufen ohne Messung', 's', 'So lange bewegt sich eine verdeckte Person in der vorhergesagten Richtung weiter.', 0.1],
    ['exit_timeout', 'Verlassen über Eingang nach', 's', 'Verdeckt in einer Eingangszone oder außerhalb aller Sensoren.', 0.5],
    ['absence_time', 'Ohne LD2410C-Bestätigung nach', 's', 'Verdeckte Person endet, wenn kein LD2410C sie so lange bestätigt.', 5],
    ['ld2410_hold', 'LD2410C-Haltezeit', 's', 'Lücken in der LD2410C-Präsenz bis zu dieser Länge werden überbrückt. Der Radar selbst hält nicht (Timeout 0).', 0.1],
    ['ld2410_min_presence', 'LD2410C-Mindestdauer', 's', 'Kürzere Präsenz ist ein Geist (Störung durch den LD2450, etwa 1 s).', 0.5],
    ['ld2410_support_window', 'LD2410C-Abstandsfenster', 'm', '', 0.05],
    ['max_lost_time', 'Höchstens verdeckt', 's', 'Wo kein LD2410C hinsieht.', 60],
  ]],
  ['Zuordnung', [
    ['gate', 'Zuordnungsschwelle (χ²)', '', 'Größer = Messungen werden großzügiger bestehenden Personen zugeordnet.', 0.5],
    ['max_gate_radius', 'Maximaler Zuordnungsradius', 'm', '', 0.05],
    ['split_radius', 'Doppelte Ziele zusammenfassen', 'm', 'Der LD2450 meldet eine Person manchmal als zwei Ziele.', 0.05],
    ['merge_distance', 'Spuren verschmelzen unter', 'm', '', 0.05],
    ['merge_time', 'Verschmelzen nach', 's', '', 0.1],
  ]],
  ['Ausgabe', [
    ['lead_time', 'Vorausschau „wird betreten“', 's', 'Wie früh eine Zone als „wird betreten“ gilt. Bei 1 m/s Gehtempo entspricht 1 s etwa 1 m.', 0.1],
    ['approach_min_speed', 'Mindesttempo dafür', 'm/s', '', 0.05],
    ['zone_hysteresis', 'Zonen-Hysterese', 'm', 'Verhindert Flattern an Zonengrenzen.', 0.01],
  ]],
];

function settingsPanel(panel) {
  const p = state.config.params;
  const root = h('<div><h2>Einstellungen</h2><p class="note">Änderungen wirken sofort. Die Standardwerte sind ein guter Anfang.</p></div>');
  for (const [group, items] of PARAMS) {
    root.append(h(`<h3>${group}</h3>`));
    for (const [key, label, unit, desc, step] of items) {
      const el = h(`<div class="param"><label class="field">${label}${unit ? ` (${unit})` : ''}
        <input type="number" step="${step}" value="${p[key]}"></label>${desc ? `<div class="desc">${desc}</div>` : ''}</div>`);
      el.querySelector('input').onchange = e => panelEdit(c => { c.params[key] = +e.target.value; });
      root.append(el);
    }
  }
  const reset = h('<button class="btn" style="margin-top:12px">Alle auf Standard</button>');
  reset.onclick = () => { if (confirm('Alle Einstellungen zurücksetzen?')) edit(c => { c.params = {}; }); setTimeout(() => location.reload(), 800); };
  root.append(reset);
  panel.append(root);
}
