// Application state, server API, saving and undo.
import { syncRooms } from './floorplan.js';
import { discardedText, errorText, layersError, problemText, rebase, toast } from './util.js';

const listeners = new Set();

export const state = {
  config: null,         // tracker.json as edited: a draft until it is saved (save), then the server's answer
  sensorsSeen: [],
  replay: false,
  haAvailable: false,
  live: null,           // last live message
  tab: 'live',
  selection: null,      // {kind: 'zone'|'wall'|'sensor'|'layer', id}
  tool: null,           // active drawing tool name
  selectable: [],       // element kinds that can be selected in the current tab
  showCoverage: false,
  // the tab "Gelerntes": what the model learned (api/learned, the core's; null: not fetched since the tab opened),
  // the layer shown (remembered per browser), the destination cells coloured by their likeliest outcome
  learned: null,
  learnedLayer: loadPref('learnedLayer', 'ghosts'),
  learnedColors: false,
  showRaw: loadPref('showRaw', false),  // the raw sensor data on the live map (remembered per browser)
  holds: {},            // zone id -> {approach, target}: badges held for HOLD_MS after they went off
  calibResult: null,
  limits: {},           // allowed values of the number fields the model reads (the core's, presence_core.config_limits)
  defaults: {},         // what a configuration may leave out (the core's, config_defaults): a new sensor's values
  // the model's quantities of the saved configuration (the core's, with every save): the plan, the thresholds,
  // the numbers the editor reads (door_on_wall, ld2410_gate); of the world the model runs in (api/coverage,
  // after every save that changed the model): per tile how many sensors see it and each sensor's sight.
  // derivedVersion: the edit (state.version) that was saved; the map shows them only for that one.
  // derivedMap: the walls and doors they are of (the doors' openings hold while these are the same)
  derived: null,
  derivedMap: null,
  coverage: null,
  version: 0,           // counts the edits
  savedVersion: 0,      // the edit that is saved (state.config as the server has it)
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
  // silent: the server has nothing to say about it (an edit of the editor that changes nothing); error: the server's
  // key, problem: the core's problem with a configuration, both worded here
  if (!r.ok) {
    const text = typeof data === 'string' ? data : (data.error ? errorText(data) : (data.problem && problemText(data.problem)) || r.statusText);
    throw Object.assign(new Error(text),
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
  state.defaults = data.defaults || {};
  state.version++;
  state.savedVersion = state.version;
  state.derived = data.derived;
  state.derivedVersion = state.version;
  state.derivedMap = mapOf(data.config);
  emit('config');
  refreshCoverage();
}

// m: a door this close to a wall is on it (the core's plan::DOOR_ON_WALL; util.onSegment)
export const onWall = () => state.derived.plan.door_on_wall;

const mapOf = c => JSON.stringify([c.walls, c.doors]);

// per door (in the configuration's order) its opening in its wall as the model has it (the core's:
// derived.plan.doors, {wall, t0, t1}), or null while an edit of the walls or doors is not saved yet
export const openingsNow = () => (state.derived && state.derivedMap === mapOf(state.config) ? state.derived.plan.doors : null);

// ---- the model of the saved configuration (the core's; it never sees a draft)

// the coverage and sight of the configuration as it is now (saved), or null while an edit is not saved yet
export const coverageNow = () => (state.coverageVersion === state.version ? state.coverage : null);

// the coverage and the sensors' sight per tile of the saved configuration (the core's, of the world the model
// runs in)
export async function refreshCoverage() {
  const version = state.derivedVersion;
  try {
    const cov = await api('api/coverage');
    if (version !== state.derivedVersion) return;  // saved again meanwhile: its own request follows
    state.coverage = cov;
    state.coverageVersion = version;
    emit('derived');
  } catch (e) {
    // the map then shows the fields of view as entered; only the coverage asked for says why
    if (state.showCoverage) toast('Abdeckung: ' + e.message, 5000);
  }
}

// ---- edits: every change goes through edit() for undo; the edits are a draft until they are saved

const undoStack = [];
const redoStack = [];

let saving = Promise.resolve();
let saveNote = '';  // what the toast after the next save says first

// edits not saved yet
export const unsaved = () => state.version !== state.savedVersion;

// Save the draft: the server stores it in the core's form (checked, what it leaves out filled in) and answers
// with exactly that form, which the editor goes on with: one version of the configuration. Refused (a value
// the model can't run with): the draft stays, with the reason.
async function save() {
  // one request at a time, so that an older answer never overwrites a newer state
  saving = saving.then(async () => {
    if (!unsaved()) return;
    // the background image is the web UI's: its layers are checked here, never by the model
    const bad = layersError(state.config.background);
    if (bad) { toast(`Nicht gespeichert: ${bad}`, 8000); return; }
    const body = JSON.stringify(state.config);
    const version = state.version;
    try {
      const r = await api('api/config', { method: 'PUT', body });
      // the saved form; what was edited while the request was under way stays on top of it
      const now = state.config;
      state.config = JSON.stringify(now) === body ? r.config : rebase(r.config, JSON.parse(body), now);
      state.config.background ??= {};
      state.config.background.layers ??= [];
      if (state.version === version) state.savedVersion = version;
      else state.version++;
      // the model of what was saved (null: the model's part did not change)
      if (r.derived) state.derived = r.derived;
      else if (state.coverageVersion === state.derivedVersion) state.coverageVersion = version;
      state.derivedVersion = version;
      state.derivedMap = mapOf(r.config);
      emit('config');
      if (r.derived) refreshCoverage();
      // the model starts over for most edits (walls, rooms, parameters): say so, it changes the live view; and
      // what another geometry discarded (the server's: the calibration tab shows 0 now)
      const gone = discardedText(r.discarded);
      if (r.discarded) state.calibResult = null;  // computed from the measurements discarded
      toast([saveNote, 'Gespeichert', r.restarted ? 'Modell neu gestartet' : '', gone].filter(Boolean).join(' · '),
        gone ? 10000 : 2500);
      saveNote = '';
    } catch (e) {
      toast('Nicht gespeichert: ' + e.message + ' Der Entwurf bleibt.', 8000);
    }
  });
  return saving;
}

// save the draft and wait for it (the "Speichern" button, Ctrl+S, before reloading the page)
export function saveNow() { return save(); }

export const TAB_NAMES = { live: 'Live', plan: 'Grundriss', sensors: 'Sensoren', zones: 'Zonen', calibration: 'Kalibrierung', learned: 'Gelerntes', settings: 'Einstellungen' };

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

// the draft changed; what the model makes of it comes with the answer to its saving
function changed() {
  state.version++;
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

// the sensor chosen in the tab "Gelerntes" (on the map or in its list) if it has something learned, else null: all alike
export const learnedSelected = () => (state.selection?.kind === 'sensor' && state.learned?.sensors?.[state.selection.id] ? state.selection.id : null);

export function sensorById(id) {
  return state.config.sensors.find(s => s.id === id);
}

export function sensorColor(id) {
  const ids = [...new Set([...state.config.sensors.map(s => s.id), ...state.sensorsSeen])].sort();
  const colors = ['#e8590c', '#0c8599', '#9c36b5', '#2b8a3e', '#c2255c', '#5c7cfa', '#a16207', '#0b7285'];
  return colors[Math.max(ids.indexOf(id), 0) % colors.length];
}
