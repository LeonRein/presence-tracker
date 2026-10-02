// Map interaction: selecting/dragging, drawing walls and zones, placing sensors, aligning images.
import { edit, endMerge, emit, select, sensorById, setTool, state } from './store.js';
import { ZONE_KINDS, deg, dist, rad, toast, uid, zoneOutline } from './util.js';
import { bgToWorld, loadSize } from './view.js';

const SNAP_PX = 10;

function vertices(exclude = null) {
  const c = state.config;
  const out = [];
  c.walls.forEach((w, i) => w.forEach((p, j) => { if (!(exclude?.wall === i && exclude.index === j)) out.push(p); }));
  for (const z of c.zones) {
    if (z.shape === 'circle' || z.id === exclude?.zone) continue;
    out.push(...zoneOutline(z));
  }
  return out;
}

// Snap to existing corners; otherwise to horizontal/vertical from `from` (hold Shift to disable).
function snap(view, p, ev, from = null, exclude = null) {
  if (ev?.shiftKey) return p;
  let best = null, bestD = SNAP_PX / view.s;
  for (const v of vertices(exclude)) {
    const d = dist(v, p);
    if (d < bestD) { best = v; bestD = d; }
  }
  if (best) return [...best];
  if (from) {
    const a = deg(Math.atan2(p[1] - from[1], p[0] - from[0]));
    const r = dist(p, from);
    for (const target of [0, 90, 180, -90, -180]) {
      if (Math.abs(a - target) < 4) {
        return [from[0] + r * Math.cos(rad(target)), from[1] + r * Math.sin(rad(target))].map(v => +v.toFixed(3));
      }
    }
  }
  return p.map(v => +v.toFixed(3));
}

const handle = (view, p, role, cls = '') => {
  const [x, y] = view.P(...p);
  return `<circle class="handle ${cls}" cx="${x}" cy="${y}" r="${cls.includes('small') ? 4 : 6}" data-kind="handle" data-role="${role}"/>`;
};

// ----------------------------------------------------------------- select / drag

export class SelectTool {
  constructor(kinds) { this.kinds = kinds; this.drag = null; }

  overlay(view) {
    const sel = state.selection;
    if (!sel) return '';
    const c = state.config;
    const out = [];
    if (sel.kind === 'zone') {
      const z = c.zones.find(z => z.id === sel.id);
      if (!z) return '';
      if (z.shape === 'circle') out.push(handle(view, [z.center[0] + z.radius, z.center[1]], 'radius'));
      else if (z.shape === 'rect') zoneOutline(z).forEach((p, i) => out.push(handle(view, p, `corner-${i}`)));
      else {
        z.points.forEach((p, i) => out.push(handle(view, p, `vertex-${i}`)));
        z.points.forEach((p, i) => {
          const q = z.points[(i + 1) % z.points.length];
          out.push(handle(view, [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2], `mid-${i}`, 'small'));
        });
      }
    } else if (sel.kind === 'wall') {
      const w = c.walls[sel.id];
      if (!w) return '';
      w.forEach((p, i) => out.push(handle(view, p, `vertex-${i}`)));
      for (let i = 0; i + 1 < w.length; i++) {
        out.push(handle(view, [(w[i][0] + w[i + 1][0]) / 2, (w[i][1] + w[i + 1][1]) / 2], `mid-${i}`, 'small'));
      }
    } else if (sel.kind === 'sensor') {
      const s = sensorById(sel.id);
      if (!s?.placed) return '';
      const [x, y] = view.P(s.x, s.y);
      const hx = x + 46 * Math.cos(rad(s.heading)), hy = y - 46 * Math.sin(rad(s.heading));
      out.push(`<line x1="${x}" y1="${y}" x2="${hx}" y2="${hy}" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="3 3"/>`);
      out.push(`<circle class="handle rotate" cx="${hx}" cy="${hy}" r="7" data-kind="handle" data-role="rotate"/>`);
    }
    return out.join('');
  }

  down(world, hit, ev) {
    const view = this.view;
    if (hit?.dataset.kind === 'handle') {
      this.drag = { role: hit.dataset.role, start: world, merge: uid('drag') };
      if (this.drag.role.startsWith('mid-')) {
        // insert a vertex and drag it
        const i = +this.drag.role.slice(4);
        const sel = state.selection;
        edit(c => {
          const pts = sel.kind === 'wall' ? c.walls[sel.id] : c.zones.find(z => z.id === sel.id).points;
          pts.splice(i + 1, 0, world.map(v => +v.toFixed(3)));
        }, { merge: this.drag.merge });
        this.drag.role = `vertex-${i + 1}`;
      }
      return true;
    }
    const kind = hit?.dataset.kind;
    if (kind && this.kinds.includes(kind)) {
      const id = kind === 'wall' ? +hit.dataset.id : hit.dataset.id;
      const already = state.selection?.kind === kind && state.selection.id === id;
      select({ kind, id });
      // drag the body only when it was already selected (avoids moving things by accident)
      if (already || kind === 'sensor') this.drag = { role: 'body', kind, id, start: world, last: world, merge: uid('drag') };
      return true;
    }
    return false;
  }

  click(world, hit) {
    // click on empty map: clear selection
    if (!hit) select(null);
  }

  move(world, ev) {
    if (!this.drag) return;
    const d = this.drag;
    const sel = state.selection;
    const view = this.view;
    if (d.role === 'body') {
      const dx = world[0] - d.last[0], dy = world[1] - d.last[1];
      d.last = world;
      edit(c => {
        if (d.kind === 'zone') {
          const z = c.zones.find(z => z.id === d.id);
          if (z.shape === 'circle') z.center = [z.center[0] + dx, z.center[1] + dy].map(v => +v.toFixed(3));
          else z.points = z.points.map(([x, y]) => [+(x + dx).toFixed(3), +(y + dy).toFixed(3)]);
        } else if (d.kind === 'sensor') {
          const s = c.sensors.find(s => s.id === d.id);
          const p = snap(view, world, ev);
          s.x = +p[0].toFixed(3); s.y = +p[1].toFixed(3);
        } else if (d.kind === 'wall') {
          c.walls[d.id] = c.walls[d.id].map(([x, y]) => [+(x + dx).toFixed(3), +(y + dy).toFixed(3)]);
        } else if (d.kind === 'layer') {
          const l = c.background.layers.find(l => l.id === d.id);
          l.x = +(l.x + dx).toFixed(4); l.y = +(l.y + dy).toFixed(4);
        }
      }, { merge: d.merge });
      return;
    }
    if (d.role === 'rotate') {
      edit(c => {
        const s = c.sensors.find(s => s.id === sel.id);
        let a = deg(Math.atan2(world[1] - s.y, world[0] - s.x));
        if (!ev.shiftKey) a = Math.round(a / 5) * 5;
        s.heading = +(((a % 360) + 360) % 360).toFixed(1);
      }, { merge: d.merge });
      return;
    }
    if (d.role === 'radius') {
      edit(c => {
        const z = c.zones.find(z => z.id === sel.id);
        z.radius = +Math.max(0.1, dist(world, z.center)).toFixed(3);
      }, { merge: d.merge });
      return;
    }
    const [role, idx] = d.role.split('-');
    const i = +idx;
    edit(c => {
      if (sel.kind === 'wall') {
        const w = c.walls[sel.id];
        const from = w[i - 1] || w[i + 1];
        w[i] = snap(view, world, ev, from, { wall: sel.id, index: i });
      } else {
        const z = c.zones.find(z => z.id === sel.id);
        if (role === 'corner') {
          // rect corners: 0 (x1,y1) 1 (x2,y1) 2 (x2,y2) 3 (x1,y2)
          const p = snap(view, world, ev, null, { zone: z.id });
          const [[x1, y1], [x2, y2]] = z.points;
          const xi = i === 0 || i === 3 ? 0 : 1, yi = i < 2 ? 0 : 1;
          const pts = [[x1, y1], [x2, y2]];
          pts[xi][0] = p[0]; pts[yi][1] = p[1];
          z.points = pts;
        } else {
          z.points[i] = snap(view, world, ev, null, { zone: z.id });
        }
      }
    }, { merge: d.merge });
  }

  up() {
    if (this.drag) endMerge();
    this.drag = null;
  }

  dblclick(world, ev) {
    // remove a vertex of the selected polygon/wall with a double click on its handle
    const hit = ev.target.closest?.('[data-kind="handle"]');
    const sel = state.selection;
    if (!hit || !sel || !hit.dataset.role.startsWith('vertex-')) return;
    const i = +hit.dataset.role.slice(7);
    edit(c => {
      const pts = sel.kind === 'wall' ? c.walls[sel.id] : c.zones.find(z => z.id === sel.id)?.points;
      if (pts && pts.length > (sel.kind === 'wall' ? 2 : 3)) pts.splice(i, 1);
    });
  }

  key(ev) {
    const sel = state.selection;
    if (!sel || !['Delete', 'Backspace'].includes(ev.key)) return false;
    deleteSelection();
    return true;
  }
}

export function deleteSelection() {
  const sel = state.selection;
  if (!sel) return;
  edit(c => {
    if (sel.kind === 'zone') c.zones = c.zones.filter(z => z.id !== sel.id);
    else if (sel.kind === 'wall') c.walls.splice(sel.id, 1);
    else if (sel.kind === 'layer') c.background.layers = c.background.layers.filter(l => l.id !== sel.id);
    else if (sel.kind === 'sensor') {
      const s = c.sensors.find(s => s.id === sel.id);
      if (s) s.placed = false;
    }
  });
  select(null);
}

// ------------------------------------------------------------------- walls

export class WallTool {
  constructor() { this.points = []; this.cursor = null; }
  hint() { return 'Klicken setzt Eckpunkte · Doppelklick oder Enter beendet die Wand · Esc bricht ab · Shift: ohne Einrasten'; }

  down(world, hit, ev) {
    const p = snap(this.view, world, ev, this.points[this.points.length - 1]);
    const last = this.points[this.points.length - 1];
    if (!last || dist(last, p) > 0.02) this.points.push(p);
    this.view.renderOverlay();
    return true;
  }

  move(world, ev) {
    this.cursor = snap(this.view, world, ev, this.points[this.points.length - 1]);
    this.view.renderOverlay();
  }

  dblclick() { this.finish(); }

  finish() {
    if (this.points.length >= 2) {
      const pts = this.points;
      edit(c => c.walls.push(pts));
    }
    this.points = [];
    this.view.renderOverlay();
  }

  key(ev) {
    if (ev.key === 'Enter') { this.finish(); return true; }
    if (ev.key === 'Escape') {
      if (this.points.length) { this.points = []; this.view.renderOverlay(); } else setTool(null);
      return true;
    }
    if (ev.key === 'Backspace' && this.points.length) { this.points.pop(); this.view.renderOverlay(); return true; }
    return false;
  }

  overlay(view) {
    const pts = this.cursor ? [...this.points, this.cursor] : this.points;
    const out = [];
    if (pts.length > 1) out.push(`<polyline class="wall-draft" points="${view.pts(pts)}"/>`);
    if (this.cursor) {
      const [x, y] = view.P(...this.cursor);
      out.push(`<circle cx="${x}" cy="${y}" r="4" fill="var(--accent)"/>`);
      const last = this.points[this.points.length - 1];
      if (last) out.push(`<text x="${x + 10}" y="${y - 10}" class="zone-label" fill="var(--accent)">${dist(last, this.cursor).toFixed(2)} m</text>`);
    }
    return out.join('');
  }
}

// ------------------------------------------------------------------- zones

export class ZoneTool {
  constructor(shape, kind) { this.shape = shape; this.kind = kind; this.points = []; this.cursor = null; this.dragStart = null; }

  hint() {
    return {
      rect: 'Rechteck aufziehen · Esc bricht ab',
      circle: 'Vom Mittelpunkt aus aufziehen · Esc bricht ab',
      polygon: 'Klicken setzt Eckpunkte · Doppelklick oder Enter schließt die Fläche · Esc bricht ab',
    }[this.shape];
  }

  down(world, hit, ev) {
    const p = snap(this.view, world, ev, this.points[this.points.length - 1]);
    if (this.shape === 'polygon') {
      const last = this.points[this.points.length - 1];
      if (!last || dist(last, p) > 0.02) this.points.push(p);
    } else {
      this.dragStart = p;
    }
    this.view.renderOverlay();
    return true;
  }

  move(world, ev) {
    this.cursor = this.shape === 'circle' ? world : snap(this.view, world, ev, this.points[this.points.length - 1]);
    this.view.renderOverlay();
  }

  up(world, ev) {
    if (!this.dragStart) return;
    const a = this.dragStart, b = this.shape === 'circle' ? world : snap(this.view, world, ev);
    this.dragStart = null;
    if (this.shape === 'rect' && Math.abs(a[0] - b[0]) > 0.1 && Math.abs(a[1] - b[1]) > 0.1) {
      this.create({ shape: 'rect', points: [a, b] });
    } else if (this.shape === 'circle' && dist(a, b) > 0.1) {
      this.create({ shape: 'circle', points: [], center: a, radius: +dist(a, b).toFixed(3) });
    }
    this.view.renderOverlay();
  }

  dblclick() { this.finish(); }

  finish() {
    if (this.shape === 'polygon' && this.points.length >= 3) this.create({ shape: 'polygon', points: this.points });
    this.points = [];
    this.view.renderOverlay();
  }

  create(geom) {
    const kind = this.kind;
    const count = state.config.zones.filter(z => z.kind === kind).length + 1;
    const zone = { id: uid('z'), name: `${ZONE_KINDS[kind].label} ${count}`, kind, ...geom };
    edit(c => c.zones.push(zone));
    setTool(null);
    select({ kind: 'zone', id: zone.id });
  }

  key(ev) {
    if (ev.key === 'Enter') { this.finish(); return true; }
    if (ev.key === 'Escape') { this.points = []; this.dragStart = null; setTool(null); return true; }
    return false;
  }

  overlay(view) {
    const color = ZONE_KINDS[this.kind].color;
    const style = `fill="${color}" fill-opacity="0.15" stroke="${color}" stroke-width="2" stroke-dasharray="5 3"`;
    if (this.dragStart && this.cursor) {
      if (this.shape === 'rect') {
        const z = { shape: 'rect', points: [this.dragStart, this.cursor] };
        return `<polygon points="${view.pts(zoneOutline(z))}" ${style}/>`;
      }
      const [x, y] = view.P(...this.dragStart);
      return `<circle cx="${x}" cy="${y}" r="${dist(this.dragStart, this.cursor) * view.s}" ${style}/>`;
    }
    if (this.shape === 'polygon' && this.points.length) {
      const pts = this.cursor ? [...this.points, this.cursor] : this.points;
      return `<polygon points="${view.pts(pts)}" ${style}/>`;
    }
    return '';
  }
}

// ----------------------------------------------------------------- sensors

export class PlaceSensorTool {
  constructor(id) { this.id = id; }
  hint() { return 'Auf die Position des Sensors klicken. Danach die Blickrichtung am Griff drehen.'; }
  down(world, hit, ev) {
    const p = snap(this.view, world, ev);
    edit(c => {
      let s = c.sensors.find(s => s.id === this.id);
      if (!s) {
        s = { id: this.id, name: this.id.replace(/^presence-/, ''), x: 0, y: 0, heading: 90, height: 1.5, mirror: false,
              fov: 120, range: 6, enabled: true, placed: false };
        c.sensors.push(s);
      }
      s.x = +p[0].toFixed(3); s.y = +p[1].toFixed(3); s.placed = true;
    });
    setTool(null);
    select({ kind: 'sensor', id: this.id });
    return true;
  }
  key(ev) { if (ev.key === 'Escape') { setTool(null); return true; } return false; }
}

// --------------------------------------------------- background 2-point alignment

export class AlignTool {
  // Click a point on the image, then where it belongs; twice. Scales, rotates and moves the image.
  constructor(layerId) { this.layerId = layerId; this.clicks = []; }
  hint() {
    return ['1/4: Ersten Punkt auf dem Bild anklicken (z. B. eine Hausecke)',
      '2/4: Wohin gehört dieser Punkt? Auf der Karte anklicken',
      '3/4: Zweiten Punkt auf dem Bild anklicken, möglichst weit vom ersten entfernt',
      '4/4: Wohin gehört der zweite Punkt?'][this.clicks.length];
  }
  down(world, hit, ev) {
    this.clicks.push(snap(this.view, world, ev));
    if (this.clicks.length === 4) {
      const [a, A, b, B] = this.clicks;
      edit(c => {
        const l = c.background.layers.find(l => l.id === this.layerId);
        // image pixel coordinates of a and b under the current placement
        const toPx = p => {
          const r = rad(l.rotation || 0), k = l.scale, dx = p[0] - l.x, dy = p[1] - l.y;
          return [(dx * Math.cos(r) + dy * Math.sin(r)) / k, (dx * Math.sin(r) - dy * Math.cos(r)) / k];
        };
        const pa = toPx(a), pb = toPx(b);
        const k = dist(A, B) / dist(pa, pb);
        // world angle of the image vector (u, -v) must match the world vector A->B
        const r = Math.atan2(B[1] - A[1], B[0] - A[0]) - Math.atan2(-(pb[1] - pa[1]), pb[0] - pa[0]);
        l.scale = +k.toPrecision(6);
        l.rotation = +deg(r).toFixed(3);
        const [wx, wy] = bgToWorld({ x: 0, y: 0, scale: l.scale, rotation: l.rotation }, pa[0], pa[1]);
        l.x = +(A[0] - wx).toFixed(4); l.y = +(A[1] - wy).toFixed(4);
      });
      setTool(null);
      toast('Bild ausgerichtet');
      return true;
    }
    emit('tool');
    return true;
  }
  overlay(view) {
    return this.clicks.map((p, i) => {
      const [x, y] = view.P(...p);
      return `<circle cx="${x}" cy="${y}" r="5" fill="${i % 2 ? 'var(--ok)' : 'var(--accent)'}" stroke="var(--surface)" stroke-width="2"/>`;
    }).join('');
  }
  key(ev) { if (ev.key === 'Escape') { setTool(null); return true; } return false; }
}

// Fit a vacuum map image to its room rectangles: the image's non-empty pixels span the rooms.
export function autoFitImage(layer, rooms) {
  const size = loadSize(layer.url);
  if (!size?.img) return false;
  const canvas = document.createElement('canvas');
  canvas.width = size.w; canvas.height = size.h;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(size.img, 0, 0);
  const data = ctx.getImageData(0, 0, size.w, size.h).data;
  const bg = data.slice(0, 4);
  let u0 = Infinity, v0 = Infinity, u1 = -1, v1 = -1;
  for (let v = 0; v < size.h; v++) {
    for (let u = 0; u < size.w; u++) {
      const i = 4 * (v * size.w + u);
      const a = data[i + 3];
      const diff = Math.abs(data[i] - bg[0]) + Math.abs(data[i + 1] - bg[1]) + Math.abs(data[i + 2] - bg[2]) + Math.abs(a - bg[3]);
      if (a > 20 && diff > 40) { u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
    }
  }
  if (u1 < 0) return false;
  const X0 = Math.min(...rooms.map(r => r.x0)), X1 = Math.max(...rooms.map(r => r.x1));
  const Y0 = Math.min(...rooms.map(r => r.y0)), Y1 = Math.max(...rooms.map(r => r.y1));
  const k = ((X1 - X0) / (u1 - u0) + (Y1 - Y0) / (v1 - v0)) / 2;
  layer.scale = +k.toPrecision(6);
  layer.rotation = 0;
  layer.x = +(X0 - k * u0).toFixed(4);
  layer.y = +(Y1 + k * v0).toFixed(4);
  return true;
}
