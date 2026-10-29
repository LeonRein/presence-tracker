// Small helpers shared by the UI modules.

// one colour per person: their dot, cloud and trail
export const PERSON_COLORS = ['#2f6fde', '#d9822b', '#2e9d5a', '#a855c7', '#c2413b', '#0f8f9c'];
export function personColor(id) { return PERSON_COLORS[((id - 1) % PERSON_COLORS.length + PERSON_COLORS.length) % PERSON_COLORS.length]; }

// color: fills and strokes; text: labels on the map (darker in light mode, for 4.5:1 contrast)
export const ZONE_KINDS = {
  room: { label: 'Raum', color: '#2f6fde', text: 'var(--accent-text)' },  // from the walls, not drawn
  area: { label: 'Bereich', color: '#1f9d55', text: 'var(--ok-text)', note: 'Wird an Home Assistant gemeldet (z. B. Sofa, Esstisch)' },
  entry: { label: 'Eingang', color: '#d48806', text: 'var(--warn-text)', note: 'Hier kommen Personen von außen herein oder verlassen das Haus (z. B. die Haustür in einem Raum mit Sensor)' },
};

// the rooms the sensors see (the core's plan of the configuration: derived.plan.observed), in the order of
// the configuration
export function roomsWithSensor(config, derived) {
  const seen = new Set(derived?.plan?.observed || []);
  return config.zones.filter(z => seen.has(z.id));
}

// the header's "online/in use Sensoren": every sensor in use (enabled and placed) counts, also one the
// live message does not have (it has only those heard from since the model started: two boards
// offline showed as 5/5); online: with recent frames
export function sensorsOnline(config, live) {
  const used = (config?.sensors || []).filter(s => s.enabled && s.placed);
  return { online: used.filter(s => live?.sensors?.[s.id]?.online).length, total: used.length };
}

// the group without a sensor a room belongs to (the core's plan: derived.plan.regions), or null
export function regionOfRoom(derived, id) {
  return Object.values(derived?.plan?.regions || {}).find(r => r.rooms.includes(id)) || null;
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

export function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// An edit the server refused: `base` is what it holds, `sent` what it refused, `now` the editor's state
// with what was edited while the request was under way. What changed from sent to now goes onto base;
// the rest (the refused change) is base's. Lists of objects with ids are matched by id; other lists
// whose lengths differ are taken from now.
export function rebase(base, sent, now) {
  if (JSON.stringify(now) === JSON.stringify(sent)) return base;
  const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  if (Array.isArray(sent) && Array.isArray(now)) {
    const b = Array.isArray(base) ? base : [];
    if ([...sent, ...now, ...b].every(v => isObj(v) && v.id != null)) {
      const byId = list => new Map(list.map(v => [v.id, v]));
      const bi = byId(b), si = byId(sent);
      return now.map(v => (si.has(v.id) ? rebase(bi.get(v.id), si.get(v.id), v) : v)).filter(v => v !== undefined);
    }
    if (b.length === sent.length && sent.length === now.length) return now.map((v, i) => rebase(b[i], sent[i], v));
    return now;
  }
  if (isObj(sent) && isObj(now)) {
    const b = isObj(base) ? base : {};
    const out = {};
    for (const k of new Set([...Object.keys(b), ...Object.keys(now)])) {
      if (!(k in now)) {
        if (!(k in sent)) out[k] = b[k];  // only on the server; else deleted meanwhile
        continue;
      }
      const v = k in sent ? rebase(b[k], sent[k], now[k]) : now[k];
      if (v !== undefined) out[k] = v;
    }
    return out;
  }
  return now;
}

export function uid(prefix = 'z') {
  return prefix + Math.random().toString(36).slice(2, 8);
}

export function fmt(v, digits = 2) {
  return Number.isFinite(v) ? v.toFixed(digits).replace('.', ',') : '–';
}

// A number from an input field against its limits [low, high, low excluded, high excluded] (from the
// server: the core's, presence_core.config_limits): the error text, or '' if it is fine. Empty is an error: "" became 0
// before, and light_cost 0 switched every light on.
export function numberError(raw, lim) {
  const text = String(raw ?? '').trim();
  if (text === '') return 'Leer: bitte eine Zahl eingeben.';
  const v = Number(text);
  if (!Number.isFinite(v)) return 'Keine Zahl.';
  if (!lim) return '';
  const [lo, hi, loOpen, hiOpen] = lim;
  const n = x => String(x).replace('.', ',');
  if (v < lo || v > hi || (loOpen && v === lo) || (hiOpen && v === hi)) {
    return `Erlaubt: ${loOpen ? 'größer als' : 'mindestens'} ${n(lo)} und ${hiOpen ? 'kleiner als' : 'höchstens'} ${n(hi)}.`;
  }
  return '';
}

// Binds a number field: a valid value goes to apply(value); an empty or invalid one keeps the previous
// value, marks the field and says why. allowEmpty: empty is valid and gives apply(null).
export function bindNumber(input, lim, apply, { allowEmpty = false } = {}) {
  let prev = input.value;
  if (lim) {
    const [lo, hi, loOpen, hiOpen] = lim;
    if (!loOpen) input.min = lo;
    if (!hiOpen) input.max = hi;
  }
  const label = input.closest('label');
  const showError = text => {
    let el = label?.querySelector('.field-error');
    if (text && !el && label) { el = document.createElement('span'); el.className = 'field-error'; el.setAttribute('role', 'alert'); label.append(el); }
    if (el) { if (text) el.textContent = text; else el.remove(); }
    input.classList.toggle('invalid', !!text);
    input.toggleAttribute('aria-invalid', !!text);
  };
  input.onchange = () => {
    const raw = input.value.trim();
    if (raw === '' && allowEmpty) { prev = ''; showError(''); apply(null); return; }
    const err = numberError(raw, lim);
    if (err) {
      input.value = prev;
      showError(`${err} Der alte Wert bleibt.`);
      return;
    }
    showError('');
    prev = input.value;
    apply(Number(raw));
  };
}

export function toast(text, ms = 2500) {
  const el = document.getElementById('toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, ms);
}

export const rad = d => d * Math.PI / 180;
export const deg = r => r * 180 / Math.PI;

// The outline of a zone as stored, for drawing: a rectangle's four corners, a circle as a polygon
export function zoneOutline(z, segments = 48) {
  if (z.shape === 'circle') {
    const [cx, cy] = z.center;
    return Array.from({ length: segments }, (_, i) => {
      const a = 2 * Math.PI * i / segments;
      return [cx + z.radius * Math.cos(a), cy + z.radius * Math.sin(a)];
    });
  }
  if (z.shape === 'rect') {
    const [[x1, y1], [x2, y2]] = z.points;
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]];
  }
  return z.points;
}
