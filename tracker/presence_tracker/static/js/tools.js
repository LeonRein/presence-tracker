// Map interaction: selecting/dragging, drawing walls and zones, placing sensors, aligning images.
import { edit, endMerge, emit, select, sensorById, setTool, state } from './store.js';
import { ZONE_KINDS, deg, dist, distToSegment, doorPlacement, rad, toast, uid, wallPieces, zoneOutline } from './util.js';
import { bgToWorld, loadSize } from './view.js';

const SNAP_PX = 10;
const LINK_EPS = 0.002; // m, wall ends closer than this are joined and move together

// exclude: {zone: id} and/or {pairs: Set of "wall:index"} for the points being moved
function vertices(exclude = null) {
  const c = state.config;
  const out = [];
  c.walls.forEach((w, i) => w.points.forEach((p, j) => {
    if (!exclude?.pairs?.has(`${i}:${j}`)) out.push(p);
  }));
  for (const z of c.zones) {
    if (z.shape === 'circle' || z.id === exclude?.zone) continue;
    out.push(...zoneOutline(z));
  }
  return out;
}

function segments(exclude = null) {
  const c = state.config;
  const out = [];
  c.walls.forEach((w, i) => {
    for (let j = 0; j + 1 < w.points.length; j++) {
      if (exclude?.pairs?.has(`${i}:${j}`) || exclude?.pairs?.has(`${i}:${j + 1}`)) continue;
      out.push([w.points[j], w.points[j + 1]]);
    }
  });
  return out;
}

const round3 = p => p.map(v => +v.toFixed(3));

function projectOnLine(p, a, dir) {
  const t = (p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1];
  return [a[0] + t * dir[0], a[1] + t * dir[1]];
}

function intersectLines(a, da, b, db) {
  const den = da[0] * db[1] - da[1] * db[0];
  if (Math.abs(den) < 1e-9) return null;
  const t = ((b[0] - a[0]) * db[1] - (b[1] - a[1]) * db[0]) / den;
  return [a[0] + t * da[0], a[1] + t * da[1]];
}

// Snap a point while drawing or dragging (hold Shift to disable). Priority:
//   1. existing corners
//   2. a 45° direction from a neighbor point where it meets a wall (lines end exactly on walls)
//   3. 45° directions from the neighbor points (both neighbors: their crossing, i.e. exact corners)
//   4. a point on an existing wall
// Sets snap.guides for the overlay.
export const snapGuides = [];
function snap(view, p, ev, anchors = [], exclude = null) {
  snapGuides.length = 0;
  if (ev?.shiftKey) return round3(p);
  const tol = SNAP_PX / view.s;
  let best = null, bestD = tol;
  for (const v of vertices(exclude)) {
    const d = dist(v, p);
    if (d < bestD) { best = v; bestD = d; }
  }
  if (best) { snapGuides.push({ type: 'point', p: best }); return [...best]; }

  const rays = [];
  for (const a of anchors.filter(Boolean)) {
    if (dist(a, p) < 1e-6) continue;
    const ang = Math.round(Math.atan2(p[1] - a[1], p[0] - a[0]) / (Math.PI / 4)) * (Math.PI / 4);
    const dir = [Math.cos(ang), Math.sin(ang)];
    const q = projectOnLine(p, a, dir);
    if (dist(q, p) < tol) rays.push({ a, dir, q, d: dist(q, p) });
  }
  // a ray that hits a wall near the pointer: take the hit point
  let hit = null;
  for (const r of rays) {
    for (const [a, b] of segments(exclude)) {
      const len = dist(a, b);
      if (len < 1e-6) continue;
      const sd = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
      const x = intersectLines(r.a, r.dir, a, sd);
      if (!x) continue;
      const t = (x[0] - a[0]) * sd[0] + (x[1] - a[1]) * sd[1];
      const d = dist(x, p);
      if (t >= -1e-6 && t <= len + 1e-6 && d < 1.5 * tol && (!hit || d < hit.d)) hit = { x, d, r };
    }
  }
  if (hit) {
    snapGuides.push({ type: 'ray', a: hit.r.a, b: hit.x }, { type: 'point', p: hit.x });
    return round3(hit.x);
  }
  if (rays.length >= 2) {
    const x = intersectLines(rays[0].a, rays[0].dir, rays[1].a, rays[1].dir);
    if (x && dist(x, p) < 1.5 * tol) {
      snapGuides.push({ type: 'ray', a: rays[0].a, b: x }, { type: 'ray', a: rays[1].a, b: x });
      return round3(x);
    }
  }
  if (rays.length) {
    const r = rays.sort((u, v) => u.d - v.d)[0];
    snapGuides.push({ type: 'ray', a: r.a, b: r.q });
    return round3(r.q);
  }
  for (const [a, b] of segments(exclude)) {
    const len = dist(a, b);
    if (len < 1e-6) continue;
    const dir = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    const t = (p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1];
    if (t < 0 || t > len) continue;
    const q = [a[0] + t * dir[0], a[1] + t * dir[1]];
    if (dist(q, p) < tol) { snapGuides.push({ type: 'point', p: q }); return round3(q); }
  }
  return round3(p);
}

function guidesOverlay(view) {
  return snapGuides.map(g => {
    if (g.type === 'point') {
      const [x, y] = view.P(...g.p);
      return `<circle cx="${x}" cy="${y}" r="7" fill="none" stroke="var(--ok)" stroke-width="2"/>`;
    }
    const [x1, y1] = view.P(...g.a), [x2, y2] = view.P(...g.b);
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--ok)" stroke-width="1" stroke-dasharray="4 4"/>`;
  }).join('');
}

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

// polyline/polygon helpers: neighbors of vertex i
function neighbors(pts, i, closed) {
  const n = pts.length;
  const prev = i > 0 ? pts[i - 1] : closed ? pts[n - 1] : null;
  const next = i < n - 1 ? pts[i + 1] : closed ? pts[0] : null;
  return [prev, next];
}

// all wall vertices at the same place as `p` (joined wall ends)
function linkedWallVertices(c, p, skipWall = -1) {
  const out = [];
  c.walls.forEach((w, i) => w.points.forEach((q, j) => {
    if (i !== skipWall && dist(p, q) < LINK_EPS) out.push([i, j]);
  }));
  return out;
}

const isZoneSel = sel => sel?.kind === 'zone' || sel?.kind === 'room';
// rooms derived from the walls have no geometry of their own
const isFixedRoom = sel => sel?.kind === 'room';

// closest real wall (no divider) within `tol`: {a, dir, len, t, d}
function nearestWall(c, p, tol) {
  let best = null;
  for (const [a, b] of wallPieces(c.walls, 'wall')) {
    const len = dist(a, b);
    if (len < 1e-6) continue;
    const dir = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    const t = Math.max(0, Math.min(len, (p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1]));
    const d = dist(p, [a[0] + t * dir[0], a[1] + t * dir[1]]);
    if (d < tol && (!best || d < best.d)) best = { a, dir, len, t, d };
  }
  return best;
}

const along = (w, t) => round3([w.a[0] + w.dir[0] * t, w.a[1] + w.dir[1] * t]);

// door center at t on wall w, kept so that the whole door stays on the wall
const clampDoor = (w, t, width) => Math.max(Math.min(width / 2, w.len / 2), Math.min(t, w.len - Math.min(width / 2, w.len / 2)));

// the other ends of the walls joined at a wall end
function linkedFarEnds(c, links) {
  return links.map(([wi, wj]) => c.walls[wi].points[1 - wj]);
}

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
      const door = (c.doors || []).find(d => d.id === sel.id);
      const pl = door && doorPlacement(c.walls, door);
      if (pl) {
        for (const [sign, role] of [[-1, 'doorend-0'], [1, 'doorend-1']]) {
          const t = pl.t + sign * door.width / 2;
          out.push(handle(view, [pl.a[0] + pl.dir[0] * t, pl.a[1] + pl.dir[1] * t], role));
        }
      }
    } else if (sel.kind === 'sensor') {
      const s = sensorById(sel.id);
      if (!s?.placed) return '';
      const [x, y] = view.P(s.x, s.y);
      const hx = x + 46 * Math.cos(rad(s.heading)), hy = y - 46 * Math.sin(rad(s.heading));
      out.push(`<line x1="${x}" y1="${y}" x2="${hx}" y2="${hy}" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="3 3"/>`);
      out.push(`<circle class="handle rotate" cx="${hx}" cy="${hy}" r="7" data-kind="handle" data-role="rotate"/>`);
    }
    if (this.drag) out.push(guidesOverlay(view));
    return out.join('');
  }

  // points of the selected wall/polygon zone (live object inside the config)
  _points(c, sel) {
    if (sel.kind === 'wall') return c.walls[sel.id]?.points;
    return c.zones.find(z => z.id === sel.id)?.points;
  }

  down(world, hit, ev) {
    if (hit?.dataset.kind === 'handle') {
      const sel = state.selection;
      const d = { role: hit.dataset.role, start: world, merge: uid('drag') };
      const c = state.config;
      if (sel.kind === 'wall' || isZoneSel(sel)) {
        const pts = this._points(c, sel);
        d.orig = pts ? pts.map(p => [...p]) : null;
        const [role, idx] = d.role.split('-');
        // joined wall ends move along
        if (sel.kind === 'wall' && (role === 'vertex' || role === 'edge')) {
          const idxs = role === 'vertex' ? [+idx] : [+idx, +idx + 1];
          d.links = idxs.map(k => {
            const linked = linkedWallVertices(c, pts[k], sel.id);
            return { k, linked, far: linkedFarEnds(c, linked) };
          });
          d.pairs = new Set(idxs.map(k => `${sel.id}:${k}`));
          for (const l of d.links) for (const [wi, wj] of l.linked) d.pairs.add(`${wi}:${wj}`);
          // walls ending on this one (T-junctions) and doors on it move with it
          const [a, b] = pts;
          d.attached = [];
          c.walls.forEach((w, wi) => w.points.forEach((q, wj) => {
            if (wi === sel.id || d.pairs.has(`${wi}:${wj}`)) return;
            const t = ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / (dist(a, b) ** 2);
            if (t > 0 && t < 1 && distToSegment(q, a, b) < 0.03) {  // as SNAP in floorplan.py
              d.attached.push({ wi, wj, far: w.points[1 - wj] });
              d.pairs.add(`${wi}:${wj}`);
            }
          }));
          d.doors = (c.doors || []).filter(door => distToSegment([door.x, door.y], a, b) < 0.01).map(door => ({
            id: door.id, t: dist([door.x, door.y], a) / (dist(a, b) || 1),
          }));
        }
      }
      this.drag = d;
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
        this.drag = { role: 'body', kind, id, start: world, last: world, merge: uid('drag') };
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
    const view = this.view;
    if (d.role === 'body') {
      const dx = world[0] - d.last[0], dy = world[1] - d.last[1];
      d.last = world;
      edit(c => {
        if (d.kind === 'zone' || d.kind === 'room') {
          const z = c.zones.find(z => z.id === d.id);
          if (z.shape === 'circle') z.center = [z.center[0] + dx, z.center[1] + dy].map(v => +v.toFixed(3));
          else z.points = z.points.map(([x, y]) => [+(x + dx).toFixed(3), +(y + dy).toFixed(3)]);
        } else if (d.kind === 'sensor') {
          const s = c.sensors.find(s => s.id === d.id);
          const p = snap(view, world, ev);
          s.x = +p[0].toFixed(3); s.y = +p[1].toFixed(3);
        } else if (d.kind === 'wall') {
          c.walls[d.id].points = c.walls[d.id].points.map(([x, y]) => [+(x + dx).toFixed(3), +(y + dy).toFixed(3)]);
        } else if (d.kind === 'door') {
          // a door slides along its wall (or jumps to another wall it is dragged onto), always
          // completely on it
          const door = c.doors.find(x => x.id === d.id);
          let w = nearestWall(c, world, 2 * SNAP_PX / view.s);
          if (!w) {
            // pointer away from all walls: follow it along the door's own wall
            const pl = doorPlacement(c.walls, door);
            if (pl) {
              const t = (world[0] - pl.a[0]) * pl.dir[0] + (world[1] - pl.a[1]) * pl.dir[1];
              w = { a: pl.a, dir: pl.dir, len: pl.length, t: Math.max(0, Math.min(pl.length, t)) };
            }
          }
          if (w) {
            const t = clampDoor(w, w.t, door.width);
            [door.x, door.y] = along(w, t);
            door.width = Math.min(door.width, +w.len.toFixed(3));
          }
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
    if (d.role.startsWith('doorend')) {
      edit(c => {
        const door = c.doors.find(x => x.id === sel.id);
        // placement at drag start: the door must not hop to another wall while its width changes
        d.pl ??= doorPlacement(c.walls, door);
        const pl = d.pl;
        if (!pl) return;
        // the dragged end moves along the wall (not past its ends), the other end stays
        d.fixed ??= pl.t + (d.role === 'doorend-0' ? 1 : -1) * door.width / 2;
        const fixed = d.fixed;
        let t = Math.max(0, Math.min(pl.length, (world[0] - pl.a[0]) * pl.dir[0] + (world[1] - pl.a[1]) * pl.dir[1]));
        if (!ev.shiftKey) t = fixed + Math.round((t - fixed) * 100) / 100;
        if (Math.abs(t - fixed) < 0.3) t = fixed + Math.sign(t - fixed || -1) * 0.3;
        const width = Math.abs(t - fixed);
        const center = (t + fixed) / 2;
        door.width = +width.toFixed(3);
        [door.x, door.y] = round3([pl.a[0] + pl.dir[0] * center, pl.a[1] + pl.dir[1] * center]);
      }, { merge: d.merge });
      view.renderOverlay();
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
      if (role === 'corner' || role === 'side') {
        this._moveRect(c.zones.find(z => z.id === sel.id), role, i, world, ev);
        return;
      }
      const pts = this._points(c, sel);
      let moved;
      if (sel.kind === 'wall') {
        const exclude = { pairs: d.pairs };
        if (role === 'vertex') {
          // 45° from the other end and from the far ends of the walls joined here
          const anchors = [d.orig[1 - i], ...d.links[0].far];
          moved = { [i]: snap(view, world, ev, anchors, exclude) };
        } else {
          // ends slide along the wall joined there, if exactly one is
          const prev = d.links[0].far.length === 1 ? d.links[0].far[0] : null;
          const next = d.links[1].far.length === 1 ? d.links[1].far[0] : null;
          const line = [prev, d.orig[0], d.orig[1], next].filter(Boolean);
          const k = prev ? 1 : 0;
          const m = this._moveEdge(line, k, false, world, d.start, ev, exclude);
          moved = { 0: m[k], 1: m[k + 1] };
        }
      } else {
        const exclude = { zone: sel.id };
        moved = role === 'vertex'
          ? { [i]: snap(view, world, ev, neighbors(d.orig, i, true), exclude) }
          : this._moveEdge(d.orig, i, true, world, d.start, ev, exclude);
      }
      for (const [k, p] of Object.entries(moved)) {
        pts[k] = p;
        for (const link of d.links || []) {
          if (link.k === +k) for (const [wi, wj] of link.linked) c.walls[wi].points[wj] = [...p];
        }
      }
      if (sel.kind === 'wall') {
        const [a, b] = pts;
        const dir = [b[0] - a[0], b[1] - a[1]];
        // T-junction walls keep their direction and end on the moved wall again
        for (const { wi, wj, far } of d.attached || []) {
          const own = c.walls[wi].points[wj];
          const x = intersectLines(far, [own[0] - far[0], own[1] - far[1]], a, dir);
          if (x) c.walls[wi].points[wj] = round3(x);
        }
        for (const { id, t } of d.doors || []) {
          const door = c.doors.find(x => x.id === id);
          if (door) [door.x, door.y] = round3([a[0] + dir[0] * t, a[1] + dir[1] * t]);
        }
      }
    }, { merge: d.merge });
    view.renderOverlay();
  }

  // Shift edge i (vertices i, i+1) parallel to itself; its ends slide along the neighboring edges,
  // so all angles stay as they are.
  _moveEdge(orig, i, closed, world, start, ev, exclude) {
    const n = orig.length;
    const j = (i + 1) % n;
    const a = orig[i], b = orig[j];
    const len = dist(a, b) || 1;
    const dir = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    const nrm = [-dir[1], dir[0]];
    let off = (world[0] - start[0]) * nrm[0] + (world[1] - start[1]) * nrm[1];
    snapGuides.length = 0;
    if (!ev.shiftKey) {
      // let the edge line pass exactly through a nearby corner
      const tol = SNAP_PX / this.view.s;
      let best = null;
      for (const v of vertices(exclude)) {
        const ov = (v[0] - a[0]) * nrm[0] + (v[1] - a[1]) * nrm[1];
        if (Math.abs(ov - off) < tol && (!best || Math.abs(ov - off) < Math.abs(best.ov - off))) best = { ov, v };
      }
      if (best) { off = best.ov; snapGuides.push({ type: 'point', p: best.v }); }
      else off = Math.round(off * 1000) / 1000;
    }
    const a2 = [a[0] + off * nrm[0], a[1] + off * nrm[1]];
    const slide = (k, other) => {
      // vertex k of the edge slides along the neighbor edge (k -> other), if there is one
      const p = orig[k];
      const moved = [p[0] + off * nrm[0], p[1] + off * nrm[1]];
      if (!other) return moved;
      const nd = [other[0] - p[0], other[1] - p[1]];
      const x = intersectLines(a2, dir, p, nd);
      return x && dist(x, moved) < 10 * Math.max(Math.abs(off), 0.05) ? x : moved;
    };
    const prev = closed ? orig[(i - 1 + n) % n] : i > 0 ? orig[i - 1] : null;
    const next = closed ? orig[(j + 1) % n] : j + 1 < n ? orig[j + 1] : null;
    return { [i]: round3(slide(i, prev)), [j]: round3(slide(j, next)) };
  }

  _moveRect(z, role, i, world, ev) {
    // corners: 0 (x1,y1) 1 (x2,y1) 2 (x2,y2) 3 (x1,y2); sides: 0 y1, 1 x2, 2 y2, 3 x1
    const p = snap(this.view, world, ev, [], { zone: z.id });
    const [[x1, y1], [x2, y2]] = z.points;
    const pts = [[x1, y1], [x2, y2]];
    if (role === 'corner') {
      pts[i === 0 || i === 3 ? 0 : 1][0] = p[0];
      pts[i < 2 ? 0 : 1][1] = p[1];
    } else if (i === 0) pts[0][1] = p[1];
    else if (i === 1) pts[1][0] = p[0];
    else if (i === 2) pts[1][1] = p[1];
    else pts[0][0] = p[0];
    z.points = pts;
  }

  up() {
    if (this.drag) endMerge();
    this.drag = null;
    snapGuides.length = 0;
    this.view.renderOverlay();
  }

  dblclick(world, ev) {
    // the handles were redrawn between the two clicks, so ask what is under the pointer now
    const hit = document.elementFromPoint(ev.clientX, ev.clientY)?.closest?.('[data-kind="handle"]');
    const sel = state.selection;
    if (!hit || !sel || isFixedRoom(sel)) return;
    const [role, idx] = hit.dataset.role.split('-');
    const i = +idx;
    if (sel.kind === 'wall') {
      this._splitOrJoin(role, i, world);
      return;
    }
    if (role === 'vertex') {
      // remove the vertex
      edit(c => {
        const pts = this._points(c, sel);
        if (pts && pts.length > (sel.kind === 'wall' ? 2 : 3)) pts.splice(i, 1);
      });
    } else if (role === 'edge') {
      // insert a vertex on the edge where it was clicked
      edit(c => {
        const pts = this._points(c, sel);
        const a = pts[i], b = pts[(i + 1) % pts.length];
        const len = dist(a, b) || 1;
        const q = projectOnLine(world, a, [(b[0] - a[0]) / len, (b[1] - a[1]) / len]);
        pts.splice(i + 1, 0, round3(q));
      });
    }
  }

  // double click on a wall: on the wall splits it in two, on an end joins it with the one wall there
  _splitOrJoin(role, i, world) {
    const sel = state.selection;
    const c = state.config;
    const w = c.walls[sel.id];
    if (role === 'edge') {
      const [a, b] = w.points;
      const len = dist(a, b) || 1;
      const q = round3(projectOnLine(world, a, [(b[0] - a[0]) / len, (b[1] - a[1]) / len]));
      if (dist(q, a) < 0.05 || dist(q, b) < 0.05) return;
      edit(c => {
        c.walls[sel.id].points = [a, q];
        c.walls.push({ points: [q, b], kind: w.kind });
      });
      toast('Wand geteilt');
      return;
    }
    const linked = linkedWallVertices(c, w.points[i], sel.id);
    if (linked.length !== 1 || c.walls[linked[0][0]].kind !== w.kind) {
      toast(linked.length ? 'Hier treffen mehrere Wände zusammen.' : 'An diesem Ende schließt keine Wand an.');
      return;
    }
    const [wi, wj] = linked[0];
    const keep = w.points[1 - i], far = c.walls[wi].points[1 - wj];
    edit(c => {
      c.walls[sel.id].points = [keep, far];
      c.walls.splice(wi, 1);
    });
    select({ kind: 'wall', id: wi < sel.id ? sel.id - 1 : sel.id });
    toast('Wände verbunden');
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

// ------------------------------------------------------------------- walls

export class WallTool {
  constructor(kind = 'wall') { this.kind = kind; this.points = []; this.cursor = null; }
  hint() {
    const what = this.kind === 'divider' ? 'die Raumgrenze' : 'die Wand';
    return `Klicken setzt Eckpunkte · Doppelklick oder Enter beendet ${what} · Esc bricht ab · Shift: ohne Einrasten`;
  }

  down(world, hit, ev) {
    const p = snap(this.view, world, ev, [this.points[this.points.length - 1]]);
    const last = this.points[this.points.length - 1];
    if (!last || dist(last, p) > 0.02) this.points.push(p);
    this.view.renderOverlay();
    return true;
  }

  move(world, ev) {
    this.cursor = snap(this.view, world, ev, [this.points[this.points.length - 1]]);
    this.view.renderOverlay();
  }

  dblclick() { this.finish(); }

  finish() {
    if (this.points.length >= 2) {
      // one wall per straight segment; clicks along a straight line don't split it
      const pts = [this.points[0]];
      for (let k = 1; k < this.points.length; k++) {
        const o = pts[pts.length - 1], a = this.points[k], b = this.points[k + 1];
        const straight = b && Math.abs((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])) / (dist(o, b) || 1) < 0.005
          && dist(o, a) + dist(a, b) < dist(o, b) + 0.005;
        if (!straight) pts.push(a);
      }
      const kind = this.kind;
      edit(c => { for (let k = 0; k + 1 < pts.length; k++) c.walls.push({ points: [pts[k], pts[k + 1]], kind }); });
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
    if (pts.length > 1) out.push(`<polyline class="wall-draft${this.kind === 'divider' ? ' divider' : ''}" points="${view.pts(pts)}"/>`);
    if (this.cursor) {
      const [x, y] = view.P(...this.cursor);
      out.push(`<circle cx="${x}" cy="${y}" r="4" fill="var(--accent)"/>`);
      const last = this.points[this.points.length - 1];
      if (last) out.push(`<text x="${x + 10}" y="${y - 10}" class="zone-label" fill="var(--accent)">${dist(last, this.cursor).toFixed(2)} m</text>`);
    }
    out.push(guidesOverlay(view));
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

  _anchors() {
    // polygon: 45° to the previous point and, to close it square, to the first point
    const n = this.points.length;
    return n ? [this.points[n - 1], n >= 2 ? this.points[0] : null] : [];
  }

  down(world, hit, ev) {
    const p = snap(this.view, world, ev, this._anchors());
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
    this.cursor = this.shape === 'circle' ? world : snap(this.view, world, ev, this._anchors());
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
      return `<polygon points="${view.pts(pts)}" ${style}/>` + guidesOverlay(view);
    }
    return guidesOverlay(view);
  }
}

// ------------------------------------------------------------------- doors

export class DoorTool {
  hint() { return 'Auf eine Wand klicken, um dort eine Tür zu setzen · Esc bricht ab'; }
  move(world) {
    const w = nearestWall(state.config, world, 2 * SNAP_PX / this.view.s);
    this.cursor = w && along(w, w.t);
    this.view.renderOverlay();
  }
  down(world) {
    const w = nearestWall(state.config, world, 2 * SNAP_PX / this.view.s);
    if (!w) { toast('Türen sitzen auf einer Wand (keine Raumgrenze).'); return true; }
    const width = Math.min(0.9, +w.len.toFixed(3));
    const [x, y] = along(w, clampDoor(w, w.t, width));
    const door = { id: uid('d'), x, y, width };
    edit(c => { c.doors ??= []; c.doors.push(door); });
    setTool(null);
    select({ kind: 'door', id: door.id });
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
