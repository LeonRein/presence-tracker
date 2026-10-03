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
  sensorMap: null,      // {sensor, layer: 'prior' | 'learned' | 'clutter'} shown on the map
  showRaw: true,
  calibResult: null,
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
  if (!r.ok) throw new Error(typeof data === 'string' ? data : (data.error || r.statusText));
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
  emit('config');
}

// ---- edits: every change goes through edit() for undo and auto-save

const undoStack = [];
const redoStack = [];
const saveSoon = debounce(save, 500);

let saving = Promise.resolve();

async function save() {
  // one request at a time, so that an older answer never overwrites a newer state
  saving = saving.then(async () => {
    try {
      const r = await api('api/config', { method: 'PUT', body: JSON.stringify(state.config) });
      mergeRooms(r.rooms);
    } catch (e) {
      toast('Speichern fehlgeschlagen: ' + e.message, 5000);
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

export function edit(fn, { merge = null } = {}) {
  const before = JSON.stringify(state.config);
  // consecutive edits with the same merge key (e.g. dragging) form one undo step
  if (!(merge && undoStack.length && undoStack[undoStack.length - 1].merge === merge)) {
    undoStack.push({ snapshot: before, merge });
    if (undoStack.length > 100) undoStack.shift();
  }
  redoStack.length = 0;
  fn(state.config);
  saveSoon();
  emit('config');
}

export function endMerge() {
  if (undoStack.length) undoStack[undoStack.length - 1].merge = null;
}

export function undo() {
  const step = undoStack.pop();
  if (!step) return;
  redoStack.push(JSON.stringify(state.config));
  state.config = JSON.parse(step.snapshot);
  saveSoon();
  emit('config');
}

export function redo() {
  const snap = redoStack.pop();
  if (!snap) return;
  undoStack.push({ snapshot: JSON.stringify(state.config), merge: null });
  state.config = JSON.parse(snap);
  saveSoon();
  emit('config');
}

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
