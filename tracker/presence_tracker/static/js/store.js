// Application state, server API, auto-save and undo.
import { syncRooms } from './floorplan.js';
import { debounce, layersError, rebase, toast } from './util.js';

const listeners = new Set();

export const state = {
  config: null,         // tracker.json as edited
  sensorsSeen: [],
  replay: false,
  haAvailable: false,
  live: null,           // last live message
  tab: 'live',
  selection: null,      // {kind: 'zone'|'wall'|'sensor'|'layer', id}
  tool: null,           // active drawing tool name
  selectable: [],       // element kinds that can be selected in the current tab
  showCoverage: false,
  sensorMap: null,      // {sensor, layer: 'prior' | 'clutter'} shown on the map
  showRaw: loadPref('showRaw', false),  // the raw sensor data on the live map (remembered per browser)
  holds: {},            // zone id -> {approach, target}: badges held for HOLD_MS after they went off
  calibResult: null,
  limits: {},           // allowed values of the number fields the model reads (the core's, presence_core.config_limits)
  // what the model makes of the saved configuration (the core's, with every save): the walls as the radar sees
  // them, the sensors' sight areas, the plan, the thresholds; the coverage (api/coverage) only while it is
  // shown. derivedVersion: the edit (state.version) that was saved; the map shows them only for that one
  derived: null,
  coverage: null,
  version: 0,           // counts the edits
  derivedVersion: -1,
  coverageVersion: -1,
};

// small per-browser preferences; storage may be missing or blocked (private mode)
export function loadPref(key, fallback) {
  try { const v = localStorage.getItem('presence.' + key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
}
export function savePref(key, value) {
  try { localStorage.setItem('presence.' + key, JSON.stringify(value)); } catch { /* not remembered */ }
}

// "wird betreten" and "Ziel" are often on for a single message (0.2 s): held for HOLD_MS so that they
// can be seen, with the values of their last moment on
export const HOLD_MS = 2000;
export function updateHolds(zones, now = performance.now()) {
  for (const [id, st] of Object.entries(zones || {})) {
    const hold = state.holds[id] ??= {};
    if (st.approaching) hold.approach = { until: now + HOLD_MS, p: st.p_enter, eta: st.eta, person: st.person };
    if (st.target) hold.target = { until: now + HOLD_MS, p: st.p_target, source: st.target_source, from: st.target_from };
  }
}
// {approach, target} of a zone, as now or held; null where off
export function heldBadges(id, now = performance.now()) {
  const hold = state.holds[id] || {};
  return {
    approach: hold.approach && hold.approach.until > now ? hold.approach : null,
    target: hold.target && hold.target.until > now ? hold.target : null,
  };
}

export function onChange(fn) { listeners.add(fn); }
export function emit(what = 'all') { for (const fn of listeners) fn(what); }

export async function api(path, options = {}) {
  const r = await fetch(path, {
    headers: options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  const ctype = r.headers.get('Content-Type') || '';
  const data = ctype.includes('json') ? await r.json() : await r.text();
  // silent: the server has nothing to say about it (an edit of the editor that changes nothing)
  if (!r.ok) {
    throw Object.assign(new Error(typeof data === 'string' ? data : (data.error || r.statusText)),
      { status: r.status, code: data?.code, silent: data?.error === null });
  }
  return data;
}

export async function loadConfig() {
  const data = await api('api/config');
  state.config = data.config;
  state.config.background ??= {};
  state.config.background.layers ??= [];
  state.sensorsSeen = data.sensors_seen;
  state.replay = data.replay;
  state.haAvailable = data.ha;
  state.limits = data.limits || {};
  state.version++;
  state.derived = data.derived;
  state.derivedVersion = state.version;
  emit('config');
  if (state.showCoverage) refreshCoverage();
}

// ---- the model of the saved configuration (the core's; it never sees a draft)

// derived of the configuration as it is now (saved), or null while an edit is not saved yet
export const derivedNow = () => (state.derivedVersion === state.version ? state.derived : null);
export const coverageNow = () => (state.coverageVersion === state.version ? state.coverage : null);

// the coverage of the saved configuration (the core's, of the world the model runs in)
export async function refreshCoverage() {
  const version = state.derivedVersion;
  try {
    const cov = await api('api/coverage');
    if (version !== state.derivedVersion) return;  // saved again meanwhile: its own request follows
    state.coverage = cov;
    state.coverageVersion = version;
    emit('derived');
  } catch (e) {
    toast('Abdeckung: ' + e.message, 5000);
  }
}

// ---- edits: every change goes through edit() for undo and auto-save

const undoStack = [];
const redoStack = [];
const saveSoon = debounce(save, 500);

let saving = Promise.resolve();
let saveNote = '';  // what the toast after the next save says first

async function save() {
  // one request at a time, so that an older answer never overwrites a newer state
  saving = saving.then(async () => {
    // the background image is the web UI's: its layers are checked here, never by the model
    const bad = layersError(state.config.background);
    if (bad) { toast(`Nicht gespeichert: ${bad}`, 8000); return; }
    const body = JSON.stringify(state.config);
    const version = state.version;
    try {
      const r = await api('api/config', { method: 'PUT', body });
      // the model of what was saved (null: the model's part did not change)
      if (r.derived) state.derived = r.derived;
      else if (state.coverageVersion === state.derivedVersion) state.coverageVersion = version;
      state.derivedVersion = version;
      emit('derived');
      if (r.derived && state.showCoverage) refreshCoverage();
      // the model starts over for most edits (walls, rooms, parameters): say so, it changes the live view
      toast([saveNote, 'Gespeichert', r.restarted ? 'Modell neu gestartet' : ''].filter(Boolean).join(' · '));
      saveNote = '';
    } catch (e) {
      // refused (a value out of its limits): back to what the server has, so that the next edit
      // doesn't send the refused value again; what was edited meanwhile is kept and saved
      toast('Nicht gespeichert: ' + e.message + ' Es gilt der zuletzt gespeicherte Stand.', 8000);
      if (e.status === 400) {
        if (JSON.stringify(state.config) === body) await loadConfig().catch(() => {});
        else await keepEditsOnRefusal(JSON.parse(body)).catch(() => {});
      }
    }
  });
  return saving;
}

// save now (not after the debounce) and wait for it, e.g. before reloading the page
export function saveNow() { return save(); }

async function keepEditsOnRefusal(sent) {
  const before = state.config;
  await loadConfig();  // what the server holds
  state.config = rebase(state.config, sent, before);
  state.config.background ??= {};
  state.config.background.layers ??= [];
  changed();
}

export const TAB_NAMES = { live: 'Live', plan: 'Grundriss', sensors: 'Sensoren', zones: 'Zonen', calibration: 'Kalibrierung', settings: 'Einstellungen' };

export function edit(fn, { merge = null } = {}) {
  const before = JSON.stringify(state.config);
  // consecutive edits with the same merge key (e.g. dragging) form one undo step; each step remembers
  // its tab: undo acts only there, never on something out of sight
  if (!(merge && undoStack.length && undoStack[undoStack.length - 1].merge === merge)) {
    undoStack.push({ snapshot: before, merge, tab: state.tab });
    if (undoStack.length > 100) undoStack.shift();
  }
  redoStack.length = 0;
  saveNote = '';
  const walls = JSON.stringify(state.config.walls);
  fn(state.config);
  // the rooms are the closed areas between the walls (the editor's: floorplan.js)
  if (JSON.stringify(state.config.walls) !== walls) state.config.zones = syncRooms(state.config.zones, state.config.walls);
  changed();
}

// the configuration changed: save it soon; what the model makes of it comes with the answer
function changed() {
  state.version++;
  saveSoon();
  emit('config');
}

export function endMerge() {
  if (undoStack.length) undoStack[undoStack.length - 1].merge = null;
}

// what undo / redo would do in the current tab: {can, other} (other: the tab of the next step elsewhere)
export function undoState(stack = undoStack) {
  const top = stack[stack.length - 1];
  return { can: !!top && top.tab === state.tab, other: top && top.tab !== state.tab ? top.tab : null };
}
export const redoState = () => undoState(redoStack);

function step(from, to, what) {
  const top = from[from.length - 1];
  if (!top) { toast(`Nichts ${what === 'undo' ? 'rückgängig zu machen' : 'zu wiederholen'}.`); return; }
  if (top.tab !== state.tab) {
    toast(`Die letzte Änderung war im Tab „${TAB_NAMES[top.tab] || top.tab}“. Dort ${what === 'undo' ? 'rückgängig machen' : 'wiederholen'}.`, 4000);
    return;
  }
  from.pop();
  to.push({ snapshot: JSON.stringify(state.config), merge: null, tab: top.tab });
  state.config = JSON.parse(top.snapshot);
  saveNote = what === 'undo' ? 'Rückgängig gemacht' : 'Wiederholt';
  changed();
}

export function undo() { step(undoStack, redoStack, 'undo'); }
export function redo() { step(redoStack, undoStack, 'redo'); }

export function select(sel) {
  state.selection = sel;
  emit('selection');
}

export function setTool(tool) {
  state.tool = tool;
  emit('tool');
}

export function sensorById(id) {
  return state.config.sensors.find(s => s.id === id);
}

export function sensorColor(id) {
  const ids = [...new Set([...state.config.sensors.map(s => s.id), ...state.sensorsSeen])].sort();
  const colors = ['#e8590c', '#0c8599', '#9c36b5', '#2b8a3e', '#c2255c', '#5c7cfa', '#a16207', '#0b7285'];
  return colors[Math.max(ids.indexOf(id), 0) % colors.length];
}
