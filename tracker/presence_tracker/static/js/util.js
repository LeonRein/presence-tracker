// Small helpers shared by the UI modules.

export const SENSOR_COLORS = ['#e8590c', '#0c8599', '#9c36b5', '#2b8a3e', '#c2255c', '#5c7cfa', '#a16207', '#0b7285'];
export const ZONE_KINDS = {
  room: { label: 'Raum', color: '#2f6fde' },  // from the walls, not drawn
  area: { label: 'Bereich', color: '#1f9d55', note: 'Wird an Home Assistant gemeldet (z. B. Sofa, Esstisch)' },
  entry: { label: 'Eingang', color: '#d48806', note: 'Hier kommen Personen von außen herein oder verlassen das Haus (z. B. die Haustür in einem Raum mit Sensor)' },
};

// the rooms the sensors see: every room of the home (not an entry room: the stairwell is outside)
// that is not in a group without a sensor (state.live.regions, derived from the plan on the server)
export function roomsWithSensor(config, live) {
  const unseen = new Set(Object.values(live?.regions || {}).flatMap(r => r.rooms || []));
  return config.zones.filter(z => z.kind === 'room' && !z.entry && !unseen.has(z.id));
}

// the group without a sensor a room belongs to, or null
export function regionOfRoom(live, id) {
  return Object.values(live?.regions || {}).find(r => (r.rooms || []).includes(id)) || null;
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

export function uid(prefix = 'z') {
  return prefix + Math.random().toString(36).slice(2, 8);
}

export function fmt(v, digits = 2) {
  return Number.isFinite(v) ? v.toFixed(digits).replace('.', ',') : '–';
}

// A number from an input field against its limits [low, high, low excluded, high excluded] (from the
// server, model.PARAM_LIMITS): the error text, or '' if it is fine. Empty is an error: "" became 0
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
export const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export function pointInPolygon(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function segmentsCross(p1, p2, q1, q2) {
  const o = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  return o(q1, q2, p1) * o(q1, q2, p2) < 0 && o(p1, p2, q1) * o(p1, p2, q2) < 0;
}

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

export function zoneContains(z, x, y) {
  if (z.shape === 'circle') return Math.hypot(x - z.center[0], y - z.center[1]) <= z.radius;
  if (z.shape === 'rect') {
    const [[x1, y1], [x2, y2]] = z.points;
    return x >= Math.min(x1, x2) && x <= Math.max(x1, x2) && y >= Math.min(y1, y2) && y <= Math.max(y1, y2);
  }
  return pointInPolygon(x, y, z.points);
}

export function zoneCenter(z) {
  if (z.shape === 'circle') return z.center;
  const pts = zoneOutline(z);
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

// Straight pieces of the walls: [a, b, kind, wallIndex]
export function wallPieces(walls, kind = null) {
  const out = [];
  walls.forEach((w, wi) => {
    if (kind && w.kind !== kind) return;
    for (let i = 0; i + 1 < w.points.length; i++) out.push([w.points[i], w.points[i + 1], w.kind, wi]);
  });
  return out;
}

export function distToSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

export const DOOR_REACH = 0.25; // m, as in floorplan.py

// The wall piece a door sits on: {a, b, dir, t, length} (t = distance of the door center from a)
export function doorPlacement(walls, door) {
  let best = null;
  for (const [a, b] of wallPieces(walls, 'wall')) {
    const d = distToSegment([door.x, door.y], a, b);
    if (d < DOOR_REACH && (!best || d < best.d)) best = { d, a, b };
  }
  if (!best) return null;
  const { a, b } = best;
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const dir = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
  const t = (door.x - a[0]) * dir[0] + (door.y - a[1]) * dir[1];
  return { a, b, dir, t, length };
}

// Segments that block the radar: real walls (no room dividers) without their door openings
export function sightSegments(config) {
  const cuts = new Map();
  for (const door of config.doors || []) {
    const pl = doorPlacement(config.walls, door);
    if (!pl) continue;
    const key = pl.a.join() + '|' + pl.b.join();
    if (!cuts.has(key)) cuts.set(key, []);
    cuts.get(key).push([Math.max(pl.t - door.width / 2, 0), Math.min(pl.t + door.width / 2, pl.length)]);
  }
  const out = [];
  for (const [a, b] of wallPieces(config.walls, 'wall')) {
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const u = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
    const at = t => [a[0] + u[0] * t, a[1] + u[1] * t];
    let start = 0;
    for (const [t0, t1] of (cuts.get(a.join() + '|' + b.join()) || []).sort((x, y) => x[0] - y[0])) {
      if (t0 > start) out.push([at(start), at(t0)]);
      start = Math.max(start, t1);
    }
    if (start < length) out.push([at(start), b]);
  }
  return out;
}

// Sensor geometry, same conventions as model.py: heading = viewing direction (degrees, CCW from +x),
// LD2450 x to the right of the viewing direction (or to the left when mirrored), as the sensor
// measures it (scale taken out).
export function sensorToLocal(s, x, y) {
  const c = Math.cos(rad(s.heading)), sn = Math.sin(rad(s.heading));
  const dx = (x - s.x) / s.scale, dy = (y - s.y) / s.scale;
  const gy = dx * c + dy * sn, gx = dx * sn - dy * c;
  return [s.mirror ? -gx : gx, gy];
}

export function sensorSees(s, x, y, segs) {
  const [lx, ly] = sensorToLocal(s, x, y);
  const r = Math.hypot(lx, ly);
  if (ly <= 0 || r > s.range || Math.abs(deg(Math.atan2(lx, ly))) > s.fov / 2) return false;
  const c = Math.cos(rad(s.heading)), sn = Math.sin(rad(s.heading));
  // 20 cm in front of the sensor, like SensorConfig.sight_origin: it hangs on a wall
  const from = [s.x + 0.2 * c, s.y + 0.2 * sn];
  for (const [a, b] of segs) if (segmentsCross(from, [x, y], a, b)) return false;
  return true;
}

// What is in sight from o within radius R: the polygon of the nearest wall hit per direction (rays at
// n even angles and just beside every wall end near o). For clipping a Gaussian at the walls.
export function visibilityPolygon(o, segs, R, n = 72) {
  const [ox, oy] = o;
  const TAU = 2 * Math.PI;
  const near = segs.filter(([a, b]) => {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((ox - a[0]) * dx + (oy - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(a[0] + t * dx - ox, a[1] + t * dy - oy) < R;
  });
  const angles = [];
  for (let k = 0; k < n; k++) angles.push(TAU * k / n);
  for (const [a, b] of near) {
    for (const p of [a, b]) {
      const t = Math.atan2(p[1] - oy, p[0] - ox);
      for (const e of [-1e-4, 0, 1e-4]) angles.push(((t + e) % TAU + TAU) % TAU);
    }
  }
  angles.sort((x, y) => x - y);
  return angles.map(t => {
    const dx = Math.cos(t), dy = Math.sin(t);
    let best = R;
    for (const [a, b] of near) {
      const ex = b[0] - a[0], ey = b[1] - a[1];
      const den = dx * ey - dy * ex;
      if (Math.abs(den) < 1e-12) continue;
      const wx = a[0] - ox, wy = a[1] - oy;
      const hit = (wx * ey - wy * ex) / den;  // along the ray
      const u = (wx * dy - wy * dx) / den;    // along the wall
      if (hit > 0 && u >= 0 && u <= 1 && hit < best) best = hit;
    }
    return [ox + dx * best, oy + dy * best];
  });
}
