import { refreshCoverage, renderPanel, seenTotal, updateLive } from './panels.js';
import { TAB_NAMES, emit, loadConfig, onChange, redo, redoState, select, setTool, state, undo, undoState, updateHolds, heldBadges } from './store.js';
import { AlignTool, DoorTool, PlaceSensorTool, SelectTool, WallTool, ZoneTool } from './tools.js';
import { fmt, toast } from './util.js';
import { MapView } from './view.js';

// what can be selected and edited in which tab; everything else is display only
const SELECTABLE = {
  live: [],
  plan: ['wall', 'door', 'room', 'layer'],
  sensors: ['sensor'],
  zones: ['zone', 'room'],
  calibration: [],
  settings: [],
};

const view = new MapView(document.getElementById('map'));
const panel = document.getElementById('panel');
const hint = document.getElementById('hint');

function makeController() {
  const t = state.tool;
  let ctl;
  if (t === 'wall' || t === 'divider') ctl = new WallTool(t);
  else if (t === 'door') ctl = new DoorTool();
  else if (t?.name === 'zone') ctl = new ZoneTool(t.shape, t.kind);
  else if (t?.name === 'place') ctl = new PlaceSensorTool(t.id);
  else if (t?.name === 'align') ctl = new AlignTool(t.layer);
  else ctl = new SelectTool(SELECTABLE[state.tab]);
  state.selectable = SELECTABLE[state.tab];
  ctl.view = view;
  view.controller = ctl;
  view.svg.classList.toggle('tool-draw', !!t);
  updateHint();
}

function updateHint() {
  const text = view.controller?.hint?.();
  hint.hidden = !text;
  hint.textContent = text || '';
}

// undo / redo as buttons on the map (also for touch); they act only on the edits of this tab
const undoBtn = document.getElementById('undo');
const redoBtn = document.getElementById('redo');
undoBtn.onclick = () => undo();
redoBtn.onclick = () => redo();
function updateUndo() {
  const editing = state.tab !== 'live';
  undoBtn.hidden = redoBtn.hidden = !editing;
  for (const [btn, st, verb] of [[undoBtn, undoState(), 'Rückgängig'], [redoBtn, redoState(), 'Wiederholen']]) {
    btn.disabled = !st.can;
    btn.title = st.can ? `${verb} (${verb === 'Rückgängig' ? 'Strg+Z' : 'Strg+Y'})`
      : st.other ? `${verb}: Die letzte Änderung war im Tab „${TAB_NAMES[st.other]}“` : `${verb}: nichts da`;
  }
}

onChange(what => {
  updateUndo();
  if (what === 'tool' || what === 'tab') makeController();
  if (what === 'selection') view.renderOverlay();
  if ((what === 'config' || what === 'rooms') && state.showCoverage) refreshCoverage(view);
  else view.render();
  updateHint();
  // new room outlines from the server: don't rebuild the panel under the user's typing
  if (what === 'rooms' && panel.contains(document.activeElement) && document.activeElement.matches('input, select')) return;
  renderPanel(panel, view, what);
});

// tabs: each has its address (#live, #plan, ...), so the browser's back button and links work
function showTab(tab) {
  if (!SELECTABLE[tab] || tab === state.tab) return;
  state.tab = tab;
  for (const x of document.querySelectorAll('#tabs button')) x.classList.toggle('active', x.dataset.tab === tab);
  state.tool = null;
  if (state.selection && !SELECTABLE[state.tab].includes(state.selection.kind)) state.selection = null;
  if (state.tab !== 'sensors') { state.showCoverage = false; state.sensorMap = null; }
  emit('tab');
  panel.scrollTop = 0;  // each tab starts at its top (its tools, the totals)
}
for (const b of document.querySelectorAll('#tabs button')) {
  b.onclick = () => { if (location.hash !== '#' + b.dataset.tab) location.hash = b.dataset.tab; else showTab(b.dataset.tab); };
}
window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));

document.getElementById('zoom-fit').onclick = () => view.fit();
document.getElementById('zoom-in').onclick = () => view.zoom(1.4);
document.getElementById('zoom-out').onclick = () => view.zoom(1 / 1.4);

window.addEventListener('keydown', ev => {
  if (ev.target.matches?.('input, select, textarea')) return;
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); return; }
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'y') { ev.preventDefault(); redo(); return; }
  if (view.controller?.key?.(ev)) { ev.preventDefault(); return; }
  if (ev.key === 'Escape') { if (state.tool) setTool(null); else select(null); }
});

// ------------------------------------------------------------ live data

let liveFrame = 0;
let lastMessage = 0;  // performance.now() of the last live message
let connected = false;
let zoneKey = '';
let panelAt = 0;  // the panel's live lists: at most twice a second (the map follows every message)
function connect() {
  const url = new URL('api/live', location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(url);
  ws.onopen = () => { connected = true; };
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.type !== 'live') return;
    lastMessage = performance.now();
    const hadCalib = !!state.live?.calibration;
    state.live = msg;
    updateHolds(msg.zones);
    if (!liveFrame) liveFrame = requestAnimationFrame(() => {
      liveFrame = 0;
      view.renderLive();
      // zone fills and labels follow the occupancy and the held "wird betreten"
      const key = JSON.stringify(msg.zones) + Object.keys(state.live.zones || {}).map(id => !!heldBadges(id).approach).join();
      if (state.tab === 'live' && key !== zoneKey) { zoneKey = key; view.render(); }
      const now = performance.now();
      if (now - panelAt > 450) { panelAt = now; updateLive(panel); }
      updateStatus();
      if (hadCalib !== !!msg.calibration && state.tab === 'calibration') renderPanel(panel, view, 'live');
    });
  };
  ws.onclose = () => {
    connected = false;
    document.getElementById('status').innerHTML = '<span><span class="dot bad"></span>getrennt</span>';
    checkStale();
    setTimeout(connect, 2000);
  };
}

// Without fresh data the map and the lists show an old state as if it were now: then they are greyed
// and a bar says since when (also on the phone, where the header has little room)
const staleBar = document.getElementById('stale');
function checkStale() {
  const age = (performance.now() - lastMessage) / 1000;
  const stale = !!state.live && (!connected || age > 3);
  document.body.classList.toggle('stale', stale);
  staleBar.hidden = !stale;
  if (stale) staleBar.textContent = `${connected ? 'Keine neuen Daten' : 'Verbindung getrennt, verbinde neu …'} · Stand von vor ${age < 90 ? `${Math.round(age)} s` : `${Math.round(age / 60)} min`}`;
}
setInterval(checkStale, 1000);

function updateStatus() {
  const live = state.live;
  const sensors = Object.values(live.sensors || {});
  const online = sensors.filter(s => s.online).length;
  const total = seenTotal();
  const cpu = live.load?.cpu;
  document.getElementById('status').innerHTML = `
    <span><span class="dot ${online === sensors.length && online ? 'ok' : 'bad'}"></span>${online}/${sensors.length} Sensoren</span>
    <span title="in den Räumen mit Sensor">${total ? total.count : 0} ${total?.count === 1 ? 'Person' : 'Personen'}</span>
    ${state.replay ? '<span class="st-extra">Wiedergabe</span>' : ''}
    <span class="st-extra" title="Rechenzeit des Modells auf dem Server, in % eines Prozessorkerns (über 100 %: mehr als ein Kern)">Modell ${Number.isFinite(cpu) ? fmt(cpu, 1) : '–'}\u00a0% CPU</span>`;
}

// ------------------------------------------------------------------ start

(async () => {
  try {
    await loadConfig();
  } catch (e) {
    toast('Konfiguration nicht geladen: ' + e.message, 8000);
    return;
  }
  const tab = location.hash.slice(1);
  if (SELECTABLE[tab] && tab !== state.tab) showTab(tab);
  else emit('tab');
  makeController();
  // wait for layout before fitting
  requestAnimationFrame(() => view.fit());
  connect();
})();
