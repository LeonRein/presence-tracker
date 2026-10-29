// Map interaction: selecting/dragging, drawing walls and zones, placing sensors, aligning images.
// While the pointer is down only the user's raw input is drawn (the dragged element alone); on release the
// edit goes to the core (store.applyEdit: snapping, what moves along, rooms) and its answer is the edit.
import { applyEdit, derivedNow, edit, endMerge, emit, select, sensorById, setTool, snapPath, state } from './store.js';
import { ZONE_KINDS, deg, rad, toast, uid, zoneOutline } from './util.js';
import { loadSize } from './view.js';

const SNAP_PX = 10;  // the core snaps within this many pixels (in m: SNAP_PX / view.s)
const r3 = v => +v.toFixed(3);  // mm, as entered

// what the pointer is over, in m (the core's tolerance); doors are put on a wall within twice that
const tolerance = (view, k = 1) => k * SNAP_PX / view.s;

const handle = (view, p, role) => {
  const [x, y] = view.P(...p);
  return `<circle class="handle" cx="${x}" cy="${y}" r="6" data-kind="handle" data-role="${role}"/>`;
};

// grip in the middle of an edge plus an invisible wide line along it: both drag the edge
const edgeGrip = (view, a, b, role) => {
  const [x1, y1] = view.P(...a), [x2, y2] = view.P(...b);
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const ang = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="transparent" stroke-width="14" class="edge-hit" data-kind="handle" data-role="${role}"/>
    <rect class="handle edge" x="${mx - 9}" y="${my - 3}" width="18" height="6" rx="3" transform="rotate(${ang} ${mx} ${my})" data-kind="handle" data-role="${role}"/>`;
};

// the raw input while dragging: the dragged element alone, dashed
const draftLine = (view, pts, closed = false) =>
  `<${closed ? 'polygon' : 'polyline'} class="wall-draft" points="${view.pts(pts)}" fill="none"/>`;

const isZoneSel = sel => sel?.kind === 'zone' || sel?.kind === 'room';
// rooms derived from the walls have no geometry of their own
const isFixedRoom = sel => sel?.kind === 'room';

// ----------------------------------------------------------------- select / drag

export class SelectTool {
  constructor(kinds) { this.kinds = kinds; this.drag = null; }

  hint() {
    const sel = state.selection;
    if (isFixedRoom(sel)) return '';
    if (sel?.kind === 'door') return 'Tür entlang der Wand ziehen · an den Enden die Breite ändern';
    if (sel?.kind === 'wall') {
      return 'Enden oder die Wand ziehen · Doppelklick auf die Wand teilt sie, auf ein Ende verbindet es mit der anschließenden Wand · Shift: ohne Einrasten';
    }
    const z = isZoneSel(sel) && state.config.zones.find(z => z.id === sel.id);
    if (z?.shape === 'polygon') {
      return 'Punkte und Kanten ziehen · Doppelklick auf eine Kante fügt einen Punkt ein, auf einen Punkt löscht ihn · Shift: ohne Einrasten';
    }
    if (z) return 'Ecken und Seiten ziehen · nochmal anklicken und ziehen verschiebt die Zone · Shift: ohne Einrasten';
    return '';
  }

  overlay(view) {
    const sel = state.selection;
    if (!sel) return '';
    const c = state.config;
    const out = [];
    if (this.drag?.draft) return this.drag.draft(view);
    if (isZoneSel(sel) && !isFixedRoom(sel)) {
      const z = c.zones.find(z => z.id === sel.id);
      if (!z) return '';
      if (z.shape === 'circle') out.push(handle(view, [z.center[0] + z.radius, z.center[1]], 'radius'));
      else if (z.shape === 'rect') {
        const pts = zoneOutline(z);
        pts.forEach((p, i) => out.push(edgeGrip(view, p, pts[(i + 1) % 4], `side-${i}`)));
        pts.forEach((p, i) => out.push(handle(view, p, `corner-${i}`)));
      } else {
        z.points.forEach((p, i) => out.push(edgeGrip(view, p, z.points[(i + 1) % z.points.length], `edge-${i}`)));
        z.points.forEach((p, i) => out.push(handle(view, p, `vertex-${i}`)));
      }
    } else if (sel.kind === 'wall') {
      const w = c.walls[sel.id]?.points;
      if (!w) return '';
      out.push(edgeGrip(view, w[0], w[1], 'edge-0'));
      w.forEach((p, i) => out.push(handle(view, p, `vertex-${i}`)));
    } else if (sel.kind === 'door') {
      // its ends where the core put it on its wall
      const opening = derivedNow()?.doors[sel.id];
      if (opening) opening.forEach((p, i) => out.push(handle(view, p, `doorend-${i}`)));
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

  // points of the selected wall/polygon zone (live object inside the config)
  _points(c, sel) {
    if (sel.kind === 'wall') return c.walls[sel.id]?.points;
    return c.zones.find(z => z.id === sel.id)?.points;
  }

  down(world, hit) {
    if (hit?.dataset.kind === 'handle') {
      const sel = state.selection;
      const pts = this._points(state.config, sel);
      this.drag = { role: hit.dataset.role, start: world, at: world, merge: uid('drag'), orig: pts?.map(p => [...p]) };
      return true;
    }
    const kind = hit?.dataset.kind;
    if (kind && this.kinds.includes(kind)) {
      const id = kind === 'wall' ? +hit.dataset.id : hit.dataset.id;
      const already = state.selection?.kind === kind && state.selection.id === id;
      select({ kind, id });
      // drag the body only when it was already selected (avoids moving things by accident);
      // rooms made from walls only move with their walls
      const movable = !isFixedRoom({ kind, id });
      if (movable && (already || kind === 'sensor' || kind === 'door')) {
        this.drag = { role: 'body', kind, id, start: world, last: world, at: world, merge: uid('drag') };
      }
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
    d.at = world;
    d.moved = true;
    if (d.role === 'body') {
      const dx = world[0] - d.last[0], dy = world[1] - d.last[1];
      d.last = world;
      if (d.kind === 'door') {
        // the door follows the pointer; on release the core puts it on the wall
        d.draft = view => { const [x, y] = view.P(...world); return `<circle cx="${x}" cy="${y}" r="6" fill="none" stroke="var(--accent)" stroke-width="2"/>`; };
        this.view.renderOverlay();
        return;
      }
      // the element moves by the pointer's way (as entered); a sensor snaps on release
      edit(c => {
        if (d.kind === 'zone' || d.kind === 'room') {
          const z = c.zones.find(z => z.id === d.id);
          if (z.shape === 'circle') z.center = [z.center[0] + dx, z.center[1] + dy].map(r3);
          else z.points = z.points.map(([x, y]) => [r3(x + dx), r3(y + dy)]);
        } else if (d.kind === 'sensor') {
          const s = c.sensors.find(s => s.id === d.id);
          s.x = r3(world[0]); s.y = r3(world[1]);
        } else if (d.kind === 'wall') {
          c.walls[d.id].points = c.walls[d.id].points.map(([x, y]) => [r3(x + dx), r3(y + dy)]);
        } else if (d.kind === 'layer') {
          const l = c.background.layers.find(l => l.id === d.id);
          l.x = +(l.x + dx).toFixed(4); l.y = +(l.y + dy).toFixed(4);
        }
      }, { merge: d.merge });
      return;
    }
    if (d.role === 'rotate') {
      // the heading towards the pointer, in 5° steps (Shift: 0.1°)
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
        z.radius = r3(Math.max(0.1, Math.hypot(world[0] - z.center[0], world[1] - z.center[1])));
      }, { merge: d.merge });
      return;
    }
    // the raw input: the dragged point at the pointer, a dragged edge by the pointer's way
    const [role, idx] = d.role.split('-');
    const i = +idx;
    const dx = world[0] - d.start[0], dy = world[1] - d.start[1];
    const z = isZoneSel(sel) && state.config.zones.find(z => z.id === sel.id);
    if (role === 'doorend') {
      const opening = derivedNow()?.doors[sel.id];
      if (opening) d.draft = view => draftLine(view, [opening[1 - i], world]);
    } else if (role === 'corner' || role === 'side') {
      d.draft = view => {
        const [[x1, y1], [x2, y2]] = z.points;
        const p = [[x1, y1], [x2, y2]];
        if (role === 'corner') { p[i === 0 || i === 3 ? 0 : 1][0] = world[0]; p[i < 2 ? 0 : 1][1] = world[1]; }
        else if (i === 0) p[0][1] = world[1];
        else if (i === 1) p[1][0] = world[0];
        else if (i === 2) p[1][1] = world[1];
        else p[0][0] = world[0];
        return draftLine(view, zoneOutline({ shape: 'rect', points: p }), true);
      };
    } else if (d.orig) {
      const pts = d.orig.map(p => [...p]);
      if (role === 'vertex') pts[i] = world;
      else for (const k of [i, (i + 1) % pts.length]) pts[k] = [pts[k][0] + dx, pts[k][1] + dy];
      d.draft = view => draftLine(view, pts, sel.kind !== 'wall');
    }
    this.view.renderOverlay();
  }

  async up(world, ev) {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    const sel = state.selection;
    const view = this.view;
    const base = { tol: tolerance(view), free: !!ev?.shiftKey };
    const [role, idx] = d.role.split('-');
    const i = +idx;
    let op = null;
    if (d.moved) {
      if (d.role === 'body' && d.kind === 'door') op = { op: 'move_door', door: d.id, to: d.at, tol: tolerance(view, 2) };
      else if (d.role === 'body' && d.kind === 'sensor') op = { op: 'move_sensor', sensor: d.id, to: d.at, ...base };
      else if (role === 'doorend') op = { op: 'door_end', door: sel.id, end: i, to: d.at, free: base.free };
      else if (sel?.kind === 'wall' && role === 'vertex') op = { op: 'wall_end', wall: sel.id, end: i, to: d.at, ...base };
      else if (sel?.kind === 'wall' && role === 'edge') op = { op: 'wall_edge', wall: sel.id, from: d.start, to: d.at, ...base };
      else if (role === 'vertex') op = { op: 'zone_point', zone: sel.id, index: i, to: d.at, ...base };
      else if (role === 'edge') op = { op: 'zone_edge', zone: sel.id, edge: i, from: d.start, to: d.at, ...base };
      else if (role === 'corner' || role === 'side') op = { op: 'rect', zone: sel.id, role, index: i, to: d.at, ...base };
    }
    if (op) await applyEdit(op, { merge: d.merge });
    endMerge();
    view.renderOverlay();
  }

  dblclick(world, ev) {
    // the handles were redrawn between the two clicks, so ask what is under the pointer now
    const hit = document.elementFromPoint(ev.clientX, ev.clientY)?.closest?.('[data-kind="handle"]');
    const sel = state.selection;
    if (!hit || !sel || isFixedRoom(sel)) return;
    const [role, idx] = hit.dataset.role.split('-');
    const i = +idx;
    if (sel.kind === 'wall') {
      // on the wall splits it in two, on an end joins it with the one wall there
      if (role === 'edge') applyEdit({ op: 'split_wall', wall: sel.id, at: world }).then(r => r && toast('Wand geteilt'));
      else {
        applyEdit({ op: 'join_wall', wall: sel.id, end: i }).then(r => {
          if (!r) return;
          if (r.select) select(r.select);
          toast('Wände verbunden');
        });
      }
      return;
    }
    if (role === 'vertex') {
      // remove the vertex
      edit(c => {
        const pts = this._points(c, sel);
        if (pts && pts.length > 3) pts.splice(i, 1);
      });
    } else if (role === 'edge') {
      // insert a vertex on the edge where it was clicked
      applyEdit({ op: 'insert_point', zone: sel.id, edge: i, at: world });
    }
  }

  key(ev) {
    const sel = state.selection;
    if (!sel || !['Delete', 'Backspace'].includes(ev.key)) return false;
    // a key is easily hit by accident (Backspace): ask first
    const what = selectionText(sel);
    if (what && !isFixedRoom(sel) && !confirm(`${what} ${sel.kind === 'sensor' ? 'von der Karte nehmen' : 'löschen'}? Das Modell startet dabei neu.`)) return true;
    deleteSelection();
    return true;
  }
}

// "Wand", "Tür", "Zone „Sofa“" ... for questions and messages
export function selectionText(sel) {
  const c = state.config;
  if (!sel) return '';
  if (sel.kind === 'wall') return c.walls[sel.id]?.kind === 'divider' ? 'Diese Raumgrenze' : 'Diese Wand';
  if (sel.kind === 'door') return 'Diese Tür';
  if (sel.kind === 'zone') return `Die Zone „${c.zones.find(z => z.id === sel.id)?.name ?? ''}“`;
  if (sel.kind === 'layer') return `Das Bild „${c.background.layers.find(l => l.id === sel.id)?.name ?? ''}“`;
  if (sel.kind === 'sensor') return `Den Sensor „${sensorById(sel.id)?.name || sel.id}“`;
  return '';
}

export function deleteSelection() {
  const sel = state.selection;
  if (!sel) return;
  if (isFixedRoom(sel)) { toast('Räume entstehen aus den Wänden. Zum Entfernen die Wand löschen.'); return; }
  edit(c => {
    if (sel.kind === 'zone' || sel.kind === 'room') c.zones = c.zones.filter(z => z.id !== sel.id);
    else if (sel.kind === 'wall') c.walls.splice(sel.id, 1);
    else if (sel.kind === 'door') c.doors = c.doors.filter(d => d.id !== sel.id);
    else if (sel.kind === 'layer') c.background.layers = c.background.layers.filter(l => l.id !== sel.id);
    else if (sel.kind === 'sensor') {
      const s = c.sensors.find(s => s.id === sel.id);
      if (s) s.placed = false;
    }
  });
  select(null);
}

// ------------------------------------------------- drawing a line of points (walls, polygons)

// The clicks of a wall or polygon being drawn: kept raw, each click asks the core where the points snap
// to (snap_path) and draws them there; finishing sends the raw clicks with the edit.
class PathTool {
  constructor(closed) { this.closed = closed; this.points = []; this.snapped = []; this.cursor = null; this.free = false; }

  async _snap() {
    const points = this.points.slice();
    if (!points.length) { this.snapped = []; this.view.renderOverlay(); return; }
    const snapped = await snapPath(points, this.closed, tolerance(this.view), this.free);
    if (snapped && points.length === this.points.length) { this.snapped = snapped; this.view.renderOverlay(); }
  }

  down(world, hit, ev) {
    this.points.push(world);
    this.free = !!ev?.shiftKey;
    this._snap();
    this.view.renderOverlay();
    return true;
  }

  move(world) {
    this.cursor = world;
    this.view.renderOverlay();
  }

  dblclick() { this.finish(); }

  // the points as the core snapped them, the newest click raw until its answer is there
  _shown() {
    const pts = this.snapped.length ? [...this.snapped] : [];
    if (this.points.length > pts.length && this.points.length) pts.push(this.points[this.points.length - 1]);
    return pts;
  }

  key(ev) {
    if (ev.key === 'Enter') { this.finish(); return true; }
    if (ev.key === 'Escape') {
      if (this.points.length) { this.points = []; this.snapped = []; this.view.renderOverlay(); } else setTool(null);
      return true;
    }
    if (ev.key === 'Backspace' && this.points.length) { this.points.pop(); this._snap(); return true; }
    return false;
  }
}

// ------------------------------------------------------------------- walls

export class WallTool extends PathTool {
  constructor(kind = 'wall') { super(false); this.kind = kind; }
  hint() {
    const what = this.kind === 'divider' ? 'die Raumgrenze' : 'die Wand';
    return `Klicken setzt Eckpunkte · Doppelklick oder Enter beendet ${what} · Esc bricht ab · Shift: ohne Einrasten`;
  }

  async finish() {
    const points = this.points;
    this.points = []; this.snapped = [];
    // a double click is two clicks at the same place: the core drops the second
    if (points.length >= 2) await applyEdit({ op: 'add_walls', points, kind: this.kind, tol: tolerance(this.view), free: this.free }, { quiet: true });
    this.view.renderOverlay();
  }

  overlay(view) {
    const pts = this._shown();
    if (this.cursor && pts.length) pts.push(this.cursor);
    const out = [];
    if (pts.length > 1) out.push(`<polyline class="wall-draft${this.kind === 'divider' ? ' divider' : ''}" points="${view.pts(pts)}"/>`);
    if (this.cursor) {
      const [x, y] = view.P(...this.cursor);
      out.push(`<circle cx="${x}" cy="${y}" r="4" fill="var(--accent)"/>`);
    }
    return out.join('');
  }
}

// ------------------------------------------------------------------- zones

export class ZoneTool extends PathTool {
  constructor(shape, kind) { super(true); this.shape = shape; this.kind = kind; this.dragStart = null; }

  hint() {
    return {
      rect: 'Rechteck aufziehen · Esc bricht ab',
      circle: 'Vom Mittelpunkt aus aufziehen · Esc bricht ab',
      polygon: 'Klicken setzt Eckpunkte · Doppelklick oder Enter schließt die Fläche · Esc bricht ab',
    }[this.shape];
  }

  down(world, hit, ev) {
    if (this.shape === 'polygon') return super.down(world, hit, ev);
    this.dragStart = world;
    this.view.renderOverlay();
    return true;
  }

  up(world, ev) {
    if (!this.dragStart) return;
    const a = this.dragStart;
    this.dragStart = null;
    const geom = this.shape === 'rect' ? { points: [a, world] }
      : { center: a, radius: r3(Math.hypot(world[0] - a[0], world[1] - a[1])), points: [] };
    this.create(geom, ev);
    this.view.renderOverlay();
  }

  finish() {
    if (this.shape === 'polygon' && this.points.length) this.create({ points: this.points }, { shiftKey: this.free });
    this.points = []; this.snapped = [];
    this.view.renderOverlay();
  }

  async create(geom, ev) {
    const zone = { id: uid('z'), kind: this.kind, shape: this.shape, ...geom };
    const r = await applyEdit({ op: 'add_zone', zone, tol: tolerance(this.view), free: !!ev?.shiftKey }, { quiet: true });
    if (!r) return;
    setTool(null);
    if (r.select) select(r.select);
  }

  key(ev) {
    if (ev.key === 'Escape') { this.points = []; this.snapped = []; this.dragStart = null; setTool(null); return true; }
    return super.key(ev);
  }

  overlay(view) {
    const color = ZONE_KINDS[this.kind].color;
    const style = `fill="${color}" fill-opacity="0.15" stroke="${color}" stroke-width="2" stroke-dasharray="5 3"`;
    if (this.dragStart && this.cursor) {
      if (this.shape === 'rect') return `<polygon points="${view.pts(zoneOutline({ shape: 'rect', points: [this.dragStart, this.cursor] }))}" ${style}/>`;
      const [x, y] = view.P(...this.dragStart);
      const r = Math.hypot(this.cursor[0] - this.dragStart[0], this.cursor[1] - this.dragStart[1]);
      return `<circle cx="${x}" cy="${y}" r="${r * view.s}" ${style}/>`;
    }
    if (this.shape === 'polygon' && this.points.length) {
      const pts = this._shown();
      if (this.cursor) pts.push(this.cursor);
      return `<polygon points="${view.pts(pts)}" ${style}/>`;
    }
    return '';
  }
}

// ------------------------------------------------------------------- doors

export class DoorTool {
  hint() { return 'Auf eine Wand klicken, um dort eine Tür zu setzen · Esc bricht ab'; }
  move(world) {
    this.cursor = world;
    this.view.renderOverlay();
  }
  down(world) {
    const id = uid('d');
    applyEdit({ op: 'add_door', id, at: world, tol: tolerance(this.view, 2) }).then(r => {
      if (!r) return;
      setTool(null);
      select({ kind: 'door', id });
    });
    return true;
  }
  overlay(view) {
    if (!this.cursor) return '';
    const [x, y] = view.P(...this.cursor);
    return `<circle cx="${x}" cy="${y}" r="6" fill="none" stroke="var(--accent)" stroke-width="2"/>`;
  }
  key(ev) { if (ev.key === 'Escape') { setTool(null); return true; } return false; }
}

// ----------------------------------------------------------------- sensors

export class PlaceSensorTool {
  constructor(id) { this.id = id; }
  hint() { return 'Auf die Position des Sensors klicken. Danach die Blickrichtung am Griff drehen.'; }
  down(world, hit, ev) {
    const name = sensorById(this.id)?.name || this.id.replace(/^presence-/, '');
    applyEdit({ op: 'place_sensor', sensor: this.id, name, to: world, tol: tolerance(this.view), free: !!ev?.shiftKey }).then(r => {
      if (!r) return;
      setTool(null);
      select({ kind: 'sensor', id: this.id });
    });
    return true;
  }
  key(ev) { if (ev.key === 'Escape') { setTool(null); return true; } return false; }
}

// --------------------------------------------------- background 2-point alignment

export class AlignTool {
  // Click a point on the image, then where it belongs; twice. The core scales, rotates and moves the image.
  constructor(layerId) { this.layerId = layerId; this.clicks = []; }
  hint() {
    return ['1/4: Ersten Punkt auf dem Bild anklicken (z. B. eine Hausecke)',
      '2/4: Wohin gehört dieser Punkt? Auf der Karte anklicken',
      '3/4: Zweiten Punkt auf dem Bild anklicken, möglichst weit vom ersten entfernt',
      '4/4: Wohin gehört der zweite Punkt?'][this.clicks.length];
  }
  down(world, hit, ev) {
    this.clicks.push(world);
    if (this.clicks.length === 4) {
      applyEdit({ op: 'align_layer', layer: this.layerId, clicks: this.clicks, tol: tolerance(this.view), free: !!ev?.shiftKey }).then(r => {
        setTool(null);
        if (r) toast('Bild ausgerichtet');
      });
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
// OPEN (Leon): this reads the image's pixels and computes the placement here; see PLAN.md, Sitzung 13.
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
