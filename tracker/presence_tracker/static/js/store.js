// Application state, server API, auto-save and undo.
import { debounce, rebase, toast } from './util.js';

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
  limits: {},           // allowed values of the number fields, from the server (the core's, presence_core.config_limits)
  // what the core derives from the configuration (POST api/preview): walls with door openings, the doors'
  // openings, lengths, the zones' areas and labels, the sensors' sight areas, the plan, the thresholds;
  // version: the edit it belongs to (state.version); coverage only while it is shown
  derived: null,
  coverage: null,
  version: 0,           // counts the edits: derived.version < version means the drawing shows raw input
  derivedVersion: -1,
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
  if (state.showCoverage) refreshDerived();
}

// ---- the core's view of the configuration being edited (POST api/preview: saves nothing)

// derived of the configuration as it is now, or null while the drawing shows raw input
export const derivedNow = () => (state.derivedVersion === state.version ? state.derived : null);

let previewing = 0;
async function preview(body) {
  return api('api/preview', { method: 'POST', body: JSON.stringify({ config: state.config, coverage: state.showCoverage, ...body }) });
}

// after an edit (debounced): what the core derives from it; dropped if another edit came meanwhile
const refreshSoon = debounce(() => refreshDerived(), 120);
export async function refreshDerived() {
  const version = state.version;
  const ticket = ++previewing;
  try {
    const r = await preview({});
    if (ticket !== previewing || version !== state.version) return;
    state.derived = r.derived;
    state.derivedVersion = version;
    state.coverage = r.coverage;
    emit('derived');
  } catch (e) {
    if (ticket === previewing) toast('Entwurf: ' + e.message, 5000);
  }
}

// An operation of the editor (snapping, what moves along, rooms: the core's, presence_core.edit) applied to
// the configuration as it is now; its result becomes an edit (one undo step, or part of the drag's with
// merge). tol: the snapping tolerance in m (the view's 10 px). Returns the answer ({points, select}) or null.
export async function applyEdit(op, { merge = null, quiet = false } = {}) {
  const version = state.version;
  const ticket = ++previewing;
  let r;
  try {
    r = await preview({ edit: op });
  } catch (e) {
    // a shape too small to be meant, a split at a wall's end: nothing to say
    if (!quiet && !e.silent) toast(e.message, 4000);
    return null;
  }
  if (version !== state.version) { toast('Nicht übernommen: inzwischen geändert. Bitte noch einmal.'); return null; }
  edit(c => { for (const k of Object.keys(r.config)) c[k] = r.config[k]; }, { merge, fresh: true });
  if (ticket === previewing) {
    state.derived = r.derived;
    state.derivedVersion = state.version;
    state.coverage = r.coverage;
    emit('derived');
  }
  return r;
}

// Where the clicks of a wall or polygon being drawn snap to (the core's snap_path): the points, or null.
export async function snapPath(points, closed, tol, free) {
  try {
    const r = await api('api/preview', { method: 'POST', body: JSON.stringify({ config: state.config, edit: { op: 'snap_path', points, closed, tol, free } }) });
    return r.points;
  } catch (e) {
    if (!e.silent) toast(e.message, 4000);
    return null;
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
    const body = JSON.stringify(state.config);
    try {
      const r = await api('api/config', { method: 'PUT', body });
      mergeRooms(r.rooms);
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

// The server derives the rooms from the walls. Take over their outlines; names and the entry
// flag edited meanwhile stay as they are here.
function mergeRooms(rooms) {
  const c = state.config;
  const local = new Map(c.zones.filter(z => z.kind === 'room').map(z => [z.id, z]));
  const merged = rooms.map(r => {
    const l = local.get(r.id);
    return l ? { ...r, name: l.name, entry: !!l.entry } : r;
  });
  const before = JSON.stringify(c.zones.filter(z => z.kind === 'room').map(z => [z.id, z.points]));
  const ids = JSON.stringify(c.zones.filter(z => z.kind === 'room').map(z => z.id));
  c.zones = [...merged, ...c.zones.filter(z => z.kind !== 'room')];
  if (JSON.stringify(merged.map(z => [z.id, z.points])) !== before) emit('rooms');
  // new rooms got their ids on saving: the labels follow
  if (JSON.stringify(merged.map(z => z.id)) !== ids) { state.version++; refreshSoon(); }
}

export const TAB_NAMES = { live: 'Live', plan: 'Grundriss', sensors: 'Sensoren', zones: 'Zonen', calibration: 'Kalibrierung', settings: 'Einstellungen' };

export function edit(fn, { merge = null, fresh = false } = {}) {
  const before = JSON.stringify(state.config);
  // consecutive edits with the same merge key (e.g. dragging) form one undo step; each step remembers
  // its tab: undo acts only there, never on something out of sight
  if (!(merge && undoStack.length && undoStack[undoStack.length - 1].merge === merge)) {
    undoStack.push({ snapshot: before, merge, tab: state.tab });
    if (undoStack.length > 100) undoStack.shift();
  }
  redoStack.length = 0;
  saveNote = '';
  fn(state.config);
  changed(fresh);
}

// the configuration changed: save it soon; what the core derives follows (fresh: it came with the change)
function changed(fresh = false) {
  state.version++;
  saveSoon();
  emit('config');
  if (!fresh) refreshSoon();
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
