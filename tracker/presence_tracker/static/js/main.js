import { refreshCoverage, renderPanel, updateLive } from './panels.js';
import { emit, loadConfig, onChange, redo, select, setTool, state, undo } from './store.js';
import { AlignTool, PlaceSensorTool, SelectTool, WallTool, ZoneTool } from './tools.js';
import { esc, toast } from './util.js';
import { MapView } from './view.js';

// what can be selected and edited in which tab; everything else is display only
const SELECTABLE = {
  live: [],
  plan: ['wall', 'layer'],
  sensors: ['sensor'],
  zones: ['zone'],
  calibration: [],
  settings: [],
};

const view = new MapView(document.getElementById('map'));
const panel = document.getElementById('panel');
const hint = document.getElementById('hint');

function makeController() {
  const t = state.tool;
  let ctl;
  if (t === 'wall') ctl = new WallTool();
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

onChange(what => {
  if (what === 'tool' || what === 'tab') makeController();
  if (what === 'selection') view.renderOverlay();
  if (what === 'config' && state.showCoverage) refreshCoverage(view);
  else view.render();
  updateHint();
  renderPanel(panel, view, what);
});

// tabs
for (const b of document.querySelectorAll('#tabs button')) {
  b.onclick = () => {
    state.tab = b.dataset.tab;
    for (const x of document.querySelectorAll('#tabs button')) x.classList.toggle('active', x === b);
    state.tool = null;
    if (state.selection && !SELECTABLE[state.tab].includes(state.selection.kind)) state.selection = null;
    if (state.tab !== 'sensors') state.showCoverage = false;
    history.replaceState(null, '', '#' + state.tab);
    emit('tab');
  };
}

document.getElementById('zoom-fit').onclick = () => view.fit();
document.getElementById('zoom-in').onclick = () => view.zoom(1.4);
document.getElementById('zoom-out').onclick = () => view.zoom(1 / 1.4);

window.addEventListener('keydown', ev => {
  if (ev.target.matches('input, select, textarea')) return;
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); return; }
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'y') { ev.preventDefault(); redo(); return; }
  if (view.controller?.key?.(ev)) { ev.preventDefault(); return; }
  if (ev.key === 'Escape') { if (state.tool) setTool(null); else select(null); }
});

// ------------------------------------------------------------ live data

let liveFrame = 0;
let zoneKey = '';
function connect() {
  const url = new URL('api/live', location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(url);
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.type !== 'live') return;
    const hadCalib = !!state.live?.calibration;
    state.live = msg;
    if (!liveFrame) liveFrame = requestAnimationFrame(() => {
      liveFrame = 0;
      view.renderLive();
      // zone fills and labels follow the occupancy
      const key = JSON.stringify(msg.zones);
      if (state.tab === 'live' && key !== zoneKey) { zoneKey = key; view.render(); }
      updateLive(panel);
      updateStatus();
      if (hadCalib !== !!msg.calibration && state.tab === 'calibration') renderPanel(panel, view, 'live');
    });
  };
  ws.onclose = () => {
    document.getElementById('status').innerHTML = '<span><span class="dot bad"></span>getrennt</span>';
    setTimeout(connect, 2000);
  };
}

function updateStatus() {
  const live = state.live;
  const sensors = Object.values(live.sensors || {});
  const online = sensors.filter(s => s.online).length;
  const total = live.zones?._total;
  document.getElementById('status').innerHTML = `
    <span><span class="dot ${online === sensors.length && online ? 'ok' : 'bad'}"></span>${online}/${sensors.length} Sensoren</span>
    <span>${total ? total.count : 0} Personen</span>
    ${state.replay ? '<span>Wiedergabe</span>' : ''}
    <span>${esc(live.load?.cpu ?? '–')} % CPU</span>`;
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
  if (SELECTABLE[tab]) document.querySelector(`#tabs button[data-tab="${tab}"]`)?.click();
  else emit('tab');
  makeController();
  // wait for layout before fitting
  requestAnimationFrame(() => view.fit());
  connect();
})();
