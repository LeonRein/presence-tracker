// Application state, server API, auto-save and undo.
import { debounce, toast } from './util.js';

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
  showRaw: true,
  calibResult: null,
  limits: {},           // allowed values of the number fields, from the server (model.PARAM_LIMITS)
};

export function onChange(fn) { listeners.add(fn); }
export function emit(what = 'all') { for (const fn of listeners) fn(what); }

export async function api(path, options = {}) {
  const r = await fetch(path, {
    headers: options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  const ctype = r.headers.get('Content-Type') || '';
  const data = ctype.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw Object.assign(new Error(typeof data === 'string' ? data : (data.error || r.statusText)), { status: r.status });
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
  emit('config');
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
    try {
      const r = await api('api/config', { method: 'PUT', body: JSON.stringify(state.config) });
      mergeRooms(r.rooms);
      // the model starts over for most edits (walls, rooms, parameters): say so, it changes the live view
      toast([saveNote, 'Gespeichert', r.restarted ? 'Modell neu gestartet' : ''].filter(Boolean).join(' · '));
      saveNote = '';
    } catch (e) {
      // refused (a value out of its limits): back to what the server has, so that the next edit
      // doesn't send the refused value again
      toast('Nicht gespeichert: ' + e.message + ' Es gilt der zuletzt gespeicherte Stand.', 8000);
      if (e.status === 400) await loadConfig().catch(() => {});
    }
  });
  return saving;
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
  c.zones = [...merged, ...c.zones.filter(z => z.kind !== 'room')];
  if (JSON.stringify(merged.map(z => [z.id, z.points])) !== before) emit('rooms');
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
  fn(state.config);
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
  saveSoon();
  emit('config');
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
