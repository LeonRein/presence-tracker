// Rooms from walls: the editor draws the plan as walls only, and the rooms are the closed areas between
// them, found here whenever the walls change (store.edit); the model gets the finished rooms when the
// configuration is saved.
//
// Every wall is one straight segment of a kind: a real wall blocks the radar's line of sight, a divider is a
// room boundary without a wall (between a living and a dining area). For finding rooms both close an area,
// and doors (on a wall) count as closed. Rooms are the faces of the planar graph of the walls. Each room
// zone keeps an anchor point inside it; after an edit, the face that holds the anchor is the same room, so
// names and Home Assistant entities survive moving walls.
//
// Numbers follow the Python editor of 0.43.0 (floorplan.py; its results on every configuration at hand are
// the reference). Its order of nodes, edges and faces came from Python's sets; here it is the order the walls
// give, and a room whose area did not change keeps its stored corners.
import { distToSegment, pointInPolygon, uid } from './util.js';

export const SNAP = 0.03;          // m: wall ends this close to each other or to a wall are joined
export const MIN_ROOM_AREA = 0.3;  // m²: smaller closed areas are slivers, not rooms

// Python's round(x, 3): the decimal nearest to the exact binary value, exact ties to even (toFixed takes the
// larger one)
export function round3(x) {
  if (!Number.isFinite(x)) return x;
  const tie = /^(\d+)\.(\d{3})50*$/.exec(Math.abs(x).toFixed(20));
  if (tie) {
    let n = BigInt(tie[1] + tie[2]);
    if (n % 2n) n += 1n;
    const s = n.toString().padStart(4, '0');
    return +`${x < 0 ? '-' : ''}${s.slice(0, -3)}.${s.slice(-3)}`;
  }
  return +x.toFixed(3);
}

// Python's sum of floats (Neumaier's compensation, as CPython 3.12+)
function pySum(values) {
  let s = 0, c = 0;
  for (const x of values) {
    const t = s + x;
    c += Math.abs(s) >= Math.abs(x) ? (s - t) + x : (x - t) + s;
    s = t;
  }
  return c !== 0 && Number.isFinite(c) ? s + c : s;
}

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

// the proper crossing of two segments: [t along p, the point], or null
function intersection(p1, p2, q1, q2) {
  const d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0]);
  if (Math.abs(d) < 1e-12) return null;
  const t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d;
  const u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d;
  return t > 0 && t < 1 && u > 0 && u < 1 ? [t, [p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])]] : null;
}

function signedArea(ring) {
  return pySum(ring.map(([x1, y1], i) => { const [x2, y2] = ring[(i + 1) % ring.length]; return x1 * y2 - x2 * y1; })) / 2;
}

function dropCollinear(ring) {
  const n = ring.length;
  const out = ring.filter((a, i) => {
    const o = ring[(i + n - 1) % n], b = ring[(i + 1) % n];
    return Math.abs(cross(o, a, b)) > 1e-6 * Math.max(dist(o, a) * dist(a, b), 1e-9);
  });
  return out.length ? out : ring;
}

// The closed areas enclosed by the walls (all kinds; [{points: [a, b, ...]}], their first two points), as
// counterclockwise polygons, corners in mm.
export function faces(walls) {
  const segs = walls.map(w => [w.points[0], w.points[1]]).filter(([a, b]) => dist(a, b) > 1e-6);

  // every piece split where another one crosses it or ends on it (T-junctions): [t along it, point]
  const cuts = segs.map(([a, b]) => [[0, a], [1, b]]);
  segs.forEach(([a, b], i) => {
    const length = dist(a, b);
    segs.forEach(([c, d], j) => {
      if (i === j) return;
      const x = intersection(a, b, c, d);
      if (x) cuts[i].push(x);
      for (const e of [c, d]) {
        if (distToSegment(e, a, b) < SNAP) {
          const t = ((e[0] - a[0]) * (b[0] - a[0]) + (e[1] - a[1]) * (b[1] - a[1])) / (length * length);
          // the point on this wall, not the loose end next to it
          if (SNAP / length < t && t < 1 - SNAP / length) cuts[i].push([t, [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]]);
        }
      }
    });
  });

  // the nodes: points closer than SNAP are one; points on walls first, so that loose wall ends join them
  // and not the other way round
  const nodes = [];
  const nodeOf = p => {
    const k = nodes.findIndex(q => dist(p, q) < SNAP);
    if (k >= 0) return k;
    nodes.push(p);
    return nodes.length - 1;
  };
  for (const pts of cuts) for (const [, p] of pts.slice(2)) nodeOf(p);
  const edgeKeys = new Set();
  const edges = [];
  for (const pts of cuts) {
    pts.sort((x, y) => x[0] - y[0] || x[1][0] - y[1][0] || x[1][1] - y[1][1]);
    const ids = pts.map(([, p]) => nodeOf(p));
    for (let k = 0; k + 1 < ids.length; k++) {
      const [u, v] = [Math.min(ids[k], ids[k + 1]), Math.max(ids[k], ids[k + 1])];
      if (u !== v && !edgeKeys.has(`${u},${v}`)) { edgeKeys.add(`${u},${v}`); edges.push([u, v]); }
    }
  }
  edges.sort((x, y) => x[0] - y[0] || x[1] - y[1]);

  const adj = nodes.map(() => []);
  for (const [u, v] of edges) { adj[u].push(v); adj[v].push(u); }
  // free-standing wall ends enclose nothing
  const loose = adj.map((nb, n) => (nb.length === 1 ? n : -1)).filter(n => n >= 0);
  while (loose.length) {
    const n = loose.pop();
    for (const m of adj[n]) {
      adj[m] = adj[m].filter(x => x !== n);
      if (adj[m].length === 1) loose.push(m);
    }
    adj[n] = [];
  }
  // around each node its neighbours by angle (ties by node)
  adj.forEach((nb, n) => {
    const angle = m => Math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0]);
    nb.sort((a, b) => angle(a) - angle(b) || a - b);
  });

  const seen = new Set();
  const out = [];
  for (let u = 0; u < nodes.length; u++) {
    for (const v of adj[u]) {
      if (seen.has(`${u},${v}`)) continue;
      // walk with the face on the left: at each corner take the next edge clockwise
      const ring = [];
      let [a, b] = [u, v];
      while (!seen.has(`${a},${b}`)) {
        seen.add(`${a},${b}`);
        ring.push(nodes[a]);
        const nb = adj[b];
        const k = Math.max(nb.indexOf(a), 0);
        [a, b] = [b, nb[(k + nb.length - 1) % nb.length]];
      }
      if (signedArea(ring) > MIN_ROOM_AREA) out.push(dropCollinear(ring).map(([x, y]) => [round3(x), round3(y)]));
    }
  }
  return out;
}

// A point well inside the polygon: of its centroid and a 15 x 15 grid over its bounds, the one in it
// farthest from the edges (mm). Where a room's name goes.
export function interiorPoint(poly) {
  if (!poly.length) return [0, 0];
  const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const candidates = [[pySum(xs) / poly.length, pySum(ys) / poly.length]];
  for (let i = 1; i < 16; i++) for (let j = 1; j < 16; j++) candidates.push([x0 + (x1 - x0) * i / 16, y0 + (y1 - y0) * j / 16]);
  let best = null;
  for (const p of candidates) {
    if (!pointInPolygon(p, poly)) continue;
    const d = Math.min(...poly.map((a, k) => distToSegment(p, a, poly[(k + 1) % poly.length])));
    if (!best || d > best.d) best = { p, d };
  }
  return best ? best.p.map(round3) : poly[0];
}

// the same ring of corners, perhaps starting at another one
function sameRing(a, b) {
  if (!Array.isArray(a) || a.length !== b.length) return false;
  const eq = (p, q) => Array.isArray(p) && p[0] === q[0] && p[1] === q[1];
  return a.some((_, s) => b.every((q, i) => eq(a[(s + i) % a.length], q)));
}

// Python's truth of a JSON value
const truthy = v => !(v == null || v === false || v === 0 || v === '' || (Array.isArray(v) && !v.length)
  || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length));

// The zones with the room zones for the walls, the other zones unchanged after them. A room keeps its id,
// name and entry flag when its anchor lies in one of the new areas (the first room whose anchor is in it),
// and its corners as stored if the area is the same; the rooms keep their order, new ones come last with the
// name "Raum n" (the first n not taken) and a new id (newId).
export function syncRooms(zones, walls, newId = () => uid('r')) {
  const polys = faces(walls);
  const rooms = zones.filter(z => z.kind === 'room');
  const others = zones.filter(z => z.kind !== 'room');
  const matched = polys.map(() => null);
  for (const r of rooms) {
    // a room without an anchor (none, or empty) is gone
    const a = r.anchor;
    if (!truthy(a) || !Array.isArray(a) || a.length < 2) continue;
    const k = polys.findIndex((poly, i) => !matched[i] && pointInPolygon(a, poly));
    if (k >= 0) matched[k] = r;
  }
  const names = rooms.map(r => (typeof r.name === 'string' ? r.name : ''));
  const result = polys.map((poly, k) => {
    const old = matched[k];
    let id, name, entry, points;
    if (old) {
      ({ id } = old);
      name = typeof old.name === 'string' ? old.name : '';
      entry = truthy(old.entry);
      points = sameRing(old.points, poly) ? old.points : poly;
    } else {
      let n = 1;
      while (names.includes(`Raum ${n}`)) n++;
      name = `Raum ${n}`;
      names.push(name);
      id = newId();
      entry = false;
      points = poly;
    }
    // the previous order of the rooms, new ones at the end
    let position = rooms.length;
    if (old) for (let i = rooms.length - 1; i >= 0; i--) if (rooms[i].id === id) { position = i; break; }
    return { position, zone: { id, name, kind: 'room', shape: 'polygon', points, anchor: interiorPoint(points), entry } };
  });
  result.sort((x, y) => x.position - y.position);
  return [...result.map(r => r.zone), ...others];
}
