// Small helpers shared by the UI modules.

export const SENSOR_COLORS = ['#e8590c', '#0c8599', '#9c36b5', '#2b8a3e', '#c2255c', '#5c7cfa', '#a16207', '#0b7285'];
export const ZONE_KINDS = {
  room: { label: 'Raum', color: '#2f6fde' },  // from the walls, not drawn
  area: { label: 'Bereich', color: '#1f9d55', note: 'Wird an Home Assistant gemeldet (z. B. Sofa, Esstisch)' },
  entry: { label: 'Eingang', color: '#d48806', note: 'Hier dürfen Personen auftauchen und verschwinden (Treppe, Haustür, Balkon)' },
  ignore: { label: 'Störer', color: '#d64545', note: 'Hier entstehen keine neuen Personen (Ventilator, Vorhang, Pflanze)' },
};

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
  return Number.isFinite(v) ? v.toFixed(digits) : '–';
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
// LD2450 x to the right of the viewing direction (or to the left when mirrored).
export function sensorToLocal(s, x, y) {
  const c = Math.cos(rad(s.heading)), sn = Math.sin(rad(s.heading));
  const dx = x - s.x, dy = y - s.y;
  const gy = dx * c + dy * sn, gx = dx * sn - dy * c;
  return [s.mirror ? -gx : gx, gy];
}

export function sensorSees(s, x, y, segs) {
  const [lx, ly] = sensorToLocal(s, x, y);
  const r = Math.hypot(lx, ly);
  if (ly <= 0 || r > s.range || Math.abs(deg(Math.atan2(lx, ly))) > s.fov / 2) return false;
  const c = Math.cos(rad(s.heading)), sn = Math.sin(rad(s.heading));
  const from = [s.x + 0.05 * c, s.y + 0.05 * sn];
  for (const [a, b] of segs) if (segmentsCross(from, [x, y], a, b)) return false;
  return true;
}
