// SVG map: projection, pan/zoom, and drawing of the plan, sensors, zones and live data.
// Everything is drawn in screen pixels; world coordinates are meters with y pointing up.
import { api, coverageNow, derivedNow, heldBadges, state, sensorColor } from './store.js';
import { ON_WALL, PERSON_COLORS, ZONE_KINDS, distToSegment, doorWall, esc, fmt, personColor, rad, zoneCenter, zoneOutline } from './util.js';

// coverage: tiles that no sensor, one, two or more see (the core's count per tile); a room the sensors don't
// see enough of to be observed is drawn as blind
const COVERAGE_COLORS = ['rgba(214,69,69,0.59)', 'rgba(240,180,41,0.22)', 'rgba(31,157,85,0.18)'];

// rooms marked as entry (stairwell) look like entry zones
const zoneStyle = z => (z.kind === 'room' && z.entry ? ZONE_KINDS.entry : ZONE_KINDS[z.kind] || ZONE_KINDS.area);

const imageSizes = new Map(); // url -> {w, h}

export class MapView {
  constructor(svg) {
    this.svg = svg;
    this.cx = 1; this.cy = 3; this.s = 50; // px per meter
    this.controller = null;
    this.pointers = new Map();
    this.trails = new Map(); // track id -> [[x, y, t, breaks before]]
    // room names above the people, so a person doesn't hide the count of their room
    svg.innerHTML = '<g id="l-static"></g><g id="l-dyn"></g><g id="l-labels"></g><g id="l-overlay"></g><g id="l-scale" class="scale-bar"></g>';
    this.gStatic = svg.querySelector('#l-static');
    this.gDyn = svg.querySelector('#l-dyn');
    this.gLabels = svg.querySelector('#l-labels');
    this.gOverlay = svg.querySelector('#l-overlay');
    this.gScale = svg.querySelector('#l-scale');
    this._bind();
    new ResizeObserver(() => this.render()).observe(svg);
  }

  get W() { return this.svg.clientWidth; }
  get H() { return this.svg.clientHeight; }
  P(x, y) { return [(x - this.cx) * this.s + this.W / 2, this.H / 2 - (y - this.cy) * this.s]; }
  U(sx, sy) { return [(sx - this.W / 2) / this.s + this.cx, (this.H / 2 - sy) / this.s + this.cy]; }
  pts(list) { return list.map(([x, y]) => this.P(x, y).map(v => v.toFixed(1)).join(',')).join(' '); }

  eventWorld(ev) {
    const r = this.svg.getBoundingClientRect();
    return this.U(ev.clientX - r.left, ev.clientY - r.top);
  }

  fit() {
    const c = state.config;
    const xs = [], ys = [];
    const add = (x, y) => { xs.push(x); ys.push(y); };
    for (const w of c.walls) for (const [x, y] of w.points) add(x, y);
    for (const z of c.zones) for (const [x, y] of zoneOutline(z, 8)) add(x, y);
    for (const s of c.sensors) if (s.placed) add(s.x, s.y);
    if (xs.length < 2) {
      for (const l of c.background.layers) {
        const size = loadSize(l.url, () => this.fit());
        if (!l.visible || !size) continue;
        for (const [u, v] of [[0, 0], [size.w, size.h]]) add(...bgToWorld(l, u, v));
      }
    }
    if (xs.length < 2) { add(-5, -2); add(8, 10); }
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    this.cx = (x0 + x1) / 2; this.cy = (y0 + y1) / 2;
    this.s = Math.max(5, Math.min((this.W - 60) / Math.max(x1 - x0, 1), (this.H - 60) / Math.max(y1 - y0, 1)));
    this.render();
  }

  zoom(factor, sx = this.W / 2, sy = this.H / 2) {
    const [wx, wy] = this.U(sx, sy);
    this.s = Math.max(5, Math.min(2000, this.s * factor));
    const [nx, ny] = this.U(sx, sy);
    this.cx += wx - nx; this.cy += wy - ny;
    this.render();
  }

  // ------------------------------------------------------------- input

  _bind() {
    const svg = this.svg;
    svg.addEventListener('contextmenu', ev => ev.preventDefault());
    svg.addEventListener('wheel', ev => {
      ev.preventDefault();
      const r = svg.getBoundingClientRect();
      this.zoom(Math.exp(-ev.deltaY * (ev.deltaMode ? 0.05 : 0.0015)), ev.clientX - r.left, ev.clientY - r.top);
    }, { passive: false });
    svg.addEventListener('pointerdown', ev => {
      svg.focus();
      svg.setPointerCapture(ev.pointerId);
      this.pointers.set(ev.pointerId, [ev.clientX, ev.clientY]);
      if (this.pointers.size === 2) { this.pinch = this._pinchState(); this.pan = null; return; }
      const world = this.eventWorld(ev);
      this.downAt = [ev.clientX, ev.clientY];
      const hit = ev.target.closest?.('[data-kind]');
      const handled = ev.button === 0 && this.controller?.down?.(world, hit, ev);
      if (!handled) this.pan = { x: ev.clientX, y: ev.clientY, cx: this.cx, cy: this.cy, moved: false, hit };
    });
    svg.addEventListener('pointermove', ev => {
      const world = this.eventWorld(ev);
      document.getElementById('cursor').textContent = `x ${fmt(world[0])}  y ${fmt(world[1])} m`;
      if (this.pointers.has(ev.pointerId)) this.pointers.set(ev.pointerId, [ev.clientX, ev.clientY]);
      if (this.pinch && this.pointers.size === 2) {
        const p = this._pinchState();
        this.zoom(p.d / this.pinch.d, p.x, p.y);
        this.cx -= (p.x - this.pinch.x) / this.s; this.cy += (p.y - this.pinch.y) / this.s;
        this.pinch = p;
        return;
      }
      if (this.pan) {
        const dx = ev.clientX - this.pan.x, dy = ev.clientY - this.pan.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) { this.pan.moved = true; svg.classList.add('panning'); }
        this.cx = this.pan.cx - dx / this.s; this.cy = this.pan.cy + dy / this.s;
        this.render();
        return;
      }
      this.controller?.move?.(world, ev);
    });
    const up = ev => {
      this.pointers.delete(ev.pointerId);
      // own double-click detection on release: two clicks without movement in between. The native
      // dblclick is unreliable when the handles are redrawn between the clicks, touch screens
      // don't send it, and "click, then drag right away" must stay a drag.
      const still = this.downAt && Math.hypot(ev.clientX - this.downAt[0], ev.clientY - this.downAt[1]) < 5;
      const now = performance.now();
      const last = this.lastClick;
      const double = ev.button === 0 && still && last && now - last.t < 400
        && Math.hypot(ev.clientX - last.x, ev.clientY - last.y) < 8;
      this.lastClick = ev.button === 0 && still && !double ? { t: now, x: ev.clientX, y: ev.clientY } : null;
      if (this.pointers.size < 2) this.pinch = null;
      if (this.pan) {
        svg.classList.remove('panning');
        if (!this.pan.moved && ev.button === 0) this.controller?.click?.(this.eventWorld(ev), this.pan.hit, ev);
        this.pan = null;
        return;
      }
      this.controller?.up?.(this.eventWorld(ev), ev);
      if (double) this.controller?.dblclick?.(this.eventWorld(ev), ev);
    };
    svg.addEventListener('pointerup', up);
    svg.addEventListener('pointercancel', up);
    svg.addEventListener('dblclick', ev => ev.preventDefault());
  }

  _pinchState() {
    const [[x1, y1], [x2, y2]] = [...this.pointers.values()];
    const r = this.svg.getBoundingClientRect();
    return { d: Math.hypot(x2 - x1, y2 - y1) || 1, x: (x1 + x2) / 2 - r.left, y: (y1 + y2) / 2 - r.top };
  }

  // ------------------------------------------------------------ drawing

  render() {
    if (!state.config || !this.W) return;
    const c = state.config;
    const out = [];
    const editing = ['plan', 'sensors', 'zones', 'calibration'].includes(state.tab);
    const sel = state.selection;
    const pe = kind => (state.selectable.includes(kind) ? '' : 'pointer-events="none"');

    // background images
    for (const l of c.background.layers) {
      if (!l.visible) continue;
      const size = loadSize(l.url, () => this.render());
      if (!size) continue;
      const m = this._bgMatrix(l);
      out.push(`<image href="${esc(l.url)}" width="${size.w}" height="${size.h}" transform="matrix(${m})"
        opacity="${l.opacity ?? 0.6}" preserveAspectRatio="none" data-kind="layer" data-id="${esc(l.id)}"
        ${pe('layer')}/>`);
    }

    // blind-spot map
    // map of one sensor (detection probability from the geometry, or learned ghosts)
    if (state.sensorMap && this.sensorMapImage) {
      const sm = this.sensorMapImage;
      const [sx, sy] = this.P(sm.x0, sm.y1);
      const k = sm.cell * this.s;
      out.push(`<image href="${sm.url}" width="${sm.cols}" height="${sm.rows}" style="image-rendering:pixelated;pointer-events:none"
        transform="matrix(${k},0,0,${k},${sx},${sy})"/>`);
    }

    const coverage = state.showCoverage && coverageNow();
    if (coverage) out.push(this._coverage(coverage));

    // zones
    const zoneStates = state.live?.zones || {};
    for (const z of c.zones) {
      const kind = zoneStyle(z);
      const zs = zoneStates[z.id];
      const occupied = zs?.occupied && state.tab === 'live';
      const dataKind = z.kind === 'room' ? 'room' : 'zone';
      const selected = sel?.kind === dataKind && sel.id === z.id;
      const fillOpacity = occupied ? 0.28 : selected ? 0.16 : (z.kind === 'room' ? 0.05 : 0.12);
      // "wird betreten" pulses (held 2 s), only where nobody is yet
      const approaching = state.tab === 'live' && !zs?.occupied && heldBadges(z.id).approach;
      const cls = `zone${selected ? ' selected' : ''}${occupied ? ' occupied' : ''}${approaching ? ' approaching' : ''}`;
      const common = `class="${cls}" fill="${kind.color}" fill-opacity="${fillOpacity}" stroke="${kind.color}" data-kind="${dataKind}" data-id="${esc(z.id)}" ${pe(dataKind)}`;
      if (z.shape === 'circle') {
        const [x, y] = this.P(...z.center);
        out.push(`<circle cx="${x}" cy="${y}" r="${z.radius * this.s}" ${common}/>`);
      } else {
        out.push(`<polygon points="${this.pts(zoneOutline(z))}" ${common}/>`);
      }
    }

    // walls as the radar sees them, their door openings left out (the model's, of the saved configuration;
    // until an edit is saved the walls as drawn); room dividers dashed; the selected one on top
    const d = derivedNow();
    const walls = d ? d.walls : c.walls.filter(w => w.kind !== 'divider').map(w => w.points);
    const wallPath = walls.map(([a, b]) => `M${this.P(...a).join(',')}L${this.P(...b).join(',')}`).join('');
    out.push(`<path class="wall" d="${wallPath}" pointer-events="none"/>`);
    for (let i = 0; i < c.walls.length; i++) {
      const w = c.walls[i];
      const selected = sel?.kind === 'wall' && sel.id === i;
      const pts = this.pts(w.points);
      if (w.kind === 'divider' || selected) {
        out.push(`<polyline class="wall${w.kind === 'divider' ? ' divider' : ''}${selected ? ' selected' : ''}" points="${pts}" pointer-events="none"/>`);
      }
      // wider invisible line to grab it
      if (state.selectable.includes('wall')) out.push(`<polyline class="wall-hit" points="${pts}" data-kind="wall" data-id="${i}"/>`);
    }

    // doors: the door on its wall (the editor keeps it there) with a thin leaf line at each end
    for (const door of c.doors || []) {
      const pl = doorWall(c.walls, door);
      const selected = sel?.kind === 'door' && sel.id === door.id;
      if (!pl || distToSegment([door.x, door.y], pl.a, pl.b) >= ON_WALL) {
        const [x, y] = this.P(door.x, door.y);
        out.push(`<circle cx="${x}" cy="${y}" r="6" fill="var(--bad)" data-kind="door" data-id="${esc(door.id)}" ${pe('door')}><title>Tür ohne Wand</title></circle>`);
        continue;
      }
      const at = t => this.P(pl.a[0] + pl.dir[0] * t, pl.a[1] + pl.dir[1] * t);
      const [x0, y0] = at(pl.t - door.width / 2), [x1, y1] = at(pl.t + door.width / 2);
      const nx = -(y1 - y0), ny = x1 - x0, nl = Math.hypot(nx, ny) || 1, k = 5 / nl;
      out.push(`<g class="door${selected ? ' selected' : ''}" pointer-events="none">
          <line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/>
          <line x1="${x0 - nx * k}" y1="${y0 - ny * k}" x2="${x0 + nx * k}" y2="${y0 + ny * k}"/>
          <line x1="${x1 - nx * k}" y1="${y1 - ny * k}" x2="${x1 + nx * k}" y2="${y1 + ny * k}"/>
        </g>`);
      if (state.selectable.includes('door')) {
        out.push(`<line class="wall-hit" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" data-kind="door" data-id="${esc(door.id)}"/>`);
      }
    }

    // zone labels on top of walls. A room without a sensor that shares its group with others has no
    // count of its own: the model only knows "somewhere in the group"
    const grouped = new Set(Object.values(state.live?.regions || {}).filter(r => (r.rooms || []).length > 1).flatMap(r => r.rooms));
    const labels = [];
    for (const z of c.zones) {
      if (z.kind === 'room' || state.tab !== 'live' || this.s > 25) {
        const kind = zoneStyle(z);
        const zs = zoneStates[z.id];
        const [x, y] = this.P(...zoneCenter(z));
        const count = state.tab === 'live' && zs && !grouped.has(z.id) ? ` · ${zs.count}` : '';
        const occ = state.tab === 'live' && zs?.occupied;
        labels.push(`<text class="zone-label${occ ? ' occupied' : ''}" x="${x}" y="${y}" text-anchor="middle" fill="${kind.text}" pointer-events="none">${esc(z.name)}${count}</text>`);
      }
    }

    // sensors
    for (const s of c.sensors) {
      if (!s.placed) continue;
      const color = sensorColor(s.id);
      const selected = sel?.kind === 'sensor' && sel.id === s.id;
      const [x, y] = this.P(s.x, s.y);
      const a0 = rad(s.heading - s.fov / 2), a1 = rad(s.heading + s.fov / 2);
      const r = s.range * s.scale * this.s;
      const showFov = editing || state.showRaw;
      // what it sees, cut at the walls (the model's, of the saved configuration); until an edit is saved its
      // field of view as entered
      const sight = d?.sensors[s.id];
      if (showFov && sight) {
        out.push(`<polygon class="fov" points="${this.pts(sight)}" fill="${color}" fill-opacity="${selected ? 0.12 : 0.04}"
          stroke="${color}" stroke-opacity="${selected ? 0.9 : 0.35}" pointer-events="none"/>`);
      } else if (showFov) {
        const [x0, y0] = [x + r * Math.cos(a0), y - r * Math.sin(a0)];
        const [x1, y1] = [x + r * Math.cos(a1), y - r * Math.sin(a1)];
        out.push(`<path class="fov" d="M${x},${y} L${x0},${y0} A${r},${r} 0 0 0 ${x1},${y1} Z" fill="${color}"
          fill-opacity="${selected ? 0.12 : 0.04}" stroke="${color}" stroke-opacity="${selected ? 0.9 : 0.35}" pointer-events="none"/>`);
      }
      const hx = x + 14 * Math.cos(rad(s.heading)), hy = y - 14 * Math.sin(rad(s.heading));
      out.push(`<g data-kind="sensor" data-id="${esc(s.id)}" ${state.selectable.includes('sensor') ? 'style="cursor:pointer"' : 'pointer-events="none"'}>
        <circle cx="${x}" cy="${y}" r="${selected ? 9 : 7}" fill="${color}" stroke="var(--surface)" stroke-width="2"/>
        <line x1="${x}" y1="${y}" x2="${hx}" y2="${hy}" stroke="${color}" stroke-width="3" stroke-linecap="round"/>
        ${s.enabled ? '' : `<line x1="${x - 6}" y1="${y - 6}" x2="${x + 6}" y2="${y + 6}" stroke="var(--surface)" stroke-width="2"/>`}
      </g>
      <text class="sensor-label" x="${x + 12}" y="${y - 10}" fill="${color}" pointer-events="none">${esc(s.name || s.id)}</text>`);
    }

    this.gStatic.innerHTML = out.join('');
    this.gLabels.innerHTML = labels.join('');
    this.renderOverlay();
    this.renderLive();
    this._scaleBar();
  }

  _bgMatrix(l) {
    // image pixel (u, v; v down) -> world: (x, y) + Rot(r) * (u*k, -v*k)
    const k = l.scale, r = rad(l.rotation || 0), s = this.s;
    const [ex, ey] = this.P(l.x, l.y);
    return [s * k * Math.cos(r), -s * k * Math.sin(r), s * k * Math.sin(r), s * k * Math.cos(r), ex, ey].map(v => +v.toFixed(5)).join(',');
  }

  renderOverlay() {
    this.gOverlay.innerHTML = this.controller?.overlay?.(this) || '';
  }

  _scaleBar() {
    const target = 120 / this.s;
    const nice = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20].find(v => v >= target * 0.6) || 20;
    const len = nice * this.s;
    const y = this.H - 34;
    this.gScale.innerHTML = `<line x1="12" y1="${y}" x2="${12 + len}" y2="${y}"/>
      <line x1="12" y1="${y - 4}" x2="12" y2="${y + 4}"/><line x1="${12 + len}" y1="${y - 4}" x2="${12 + len}" y2="${y + 4}"/>
      <text x="${12 + len / 2}" y="${y - 6}" text-anchor="middle">${nice < 1 ? nice * 100 + ' cm' : nice + ' m'}</text>`;
  }

  // live layer: detections, LD2410C distance, tracks
  renderLive() {
    const live = state.live;
    if (!live || !state.config) { this.gDyn.innerHTML = ''; return; }
    const out = [];
    const c = state.config;
    const showRaw = state.showRaw || state.tab === 'calibration' || state.tab === 'sensors';
    if (showRaw) {
      for (const [sid, sv] of Object.entries(live.sensors || {})) {
        const s = c.sensors.find(x => x.id === sid);
        if (!s || !s.placed) continue;
        const color = sensorColor(sid);
        if (sv.ld2410?.present && sv.ld2410.distance > 0) {
          const r = sv.ld2410.distance * this.s;
          const [x, y] = this.P(s.x, s.y);
          const a0 = rad(s.heading - s.fov / 2), a1 = rad(s.heading + s.fov / 2);
          out.push(`<path d="M${x + r * Math.cos(a0)},${y - r * Math.sin(a0)} A${r},${r} 0 0 0 ${x + r * Math.cos(a1)},${y - r * Math.sin(a1)}"
            fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="3 5" stroke-opacity="0.7"/>`);
        }
        for (const d of sv.detections || []) {
          const [x, y] = this.P(d.x, d.y);
          out.push(d.hidden
            ? `<circle cx="${x}" cy="${y}" r="4" fill="none" stroke="var(--muted)" stroke-width="1.5"/>`
            : `<circle cx="${x}" cy="${y}" r="4" fill="${color}" fill-opacity="0.85"/>`);
        }
      }
    }
    // the uncertainty: only the cloud (cut off at the walls by the core); a circle around the dot would
    // claim "maybe next door" through the wall
    out.push(this.clouds(live));
    // trails, broken where the estimate jumped (the core's: more than 1 m or through a wall since the last
    // message): a line there would draw a path nobody walked
    const now = live.t;
    for (const tr of live.tracks || []) {
      if (tr.x == null) continue;
      const trail = this.trails.get(tr.id) || [];
      const last = trail[trail.length - 1];
      if (tr.jumped && last) last[4] = true;  // breaks after the last point kept
      if (!last || last[2] < now - 0.05) trail.push([tr.x, tr.y, now, !!last?.[4]]);
      while (trail.length && trail[0][2] < now - 6) trail.shift();
      this.trails.set(tr.id, trail);
    }
    const ids = new Set((live.tracks || []).map(t => t.id));
    for (const id of this.trails.keys()) if (!ids.has(id)) this.trails.delete(id);
    for (const [id, trail] of this.trails) {
      let piece = [];
      const flush = () => {
        if (piece.length > 1) out.push(`<polyline points="${this.pts(piece)}" fill="none" stroke="${personColor(id)}" stroke-opacity="0.45" stroke-width="2"/>`);
      };
      for (const p of trail) {
        if (p[3]) { flush(); piece = []; }
        piece.push(p);
      }
      flush();
    }
    // the dot in the person's colour: an arrow when walking, pale and dashed when not seen just now
    for (const tr of live.tracks || []) {
      if (tr.x == null) continue; // behind a door: shown with the room, not as a point
      const [x, y] = this.P(tr.x, tr.y);
      const moving = tr.moving;
      const color = personColor(tr.id);
      if (moving) {
        const [vx, vy] = this.P(tr.x + tr.vx, tr.y + tr.vy);
        out.push(`<line x1="${x}" y1="${y}" x2="${vx}" y2="${vy}" stroke="${color}" stroke-width="3" stroke-linecap="round" marker-end="url(#arrow-${(tr.id - 1) % PERSON_COLORS.length})"/>`);
      }
      out.push(`<circle cx="${x}" cy="${y}" r="10" fill="${color}" ${tr.lost ? 'fill-opacity="0.4" stroke-dasharray="3 2"' : ''} stroke="var(--surface)" stroke-width="2"/>
        <text class="track-label" x="${x}" y="${y + 4}" text-anchor="middle">${tr.id % 100}</text>`);
    }
    if ((live.tracks || []).length) {
      out.unshift(`<defs>${PERSON_COLORS.map((col, n) => `<marker id="arrow-${n}" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${col}"/></marker>`).join('')}</defs>`);
    }
    this.gDyn.innerHTML = out.join('');
  }

  // where each person may be (filter._cloud): with a track their Gaussians, drawn as ellipses out to
  // 3 standard deviations with a Gaussian fall-off, cut off at the walls (what is in sight from their
  // mean); without, the tiles they may be in (squares cut by their room), by mass per m^2
  clouds(live) {
    const out = [];
    const clouds = live.clouds || [];
    if (live.tiling !== this.tilingId && !this.tilingLoading && clouds.some(cl => cl.tiles.length)) {
      this.tilingLoading = true;
      api('api/tiles').then(r => { this.tilingId = r.id; this.tiling = r; })
        .finally(() => { this.tilingLoading = false; });
    }
    const tiling = live.tiling === this.tilingId ? this.tiling : null;
    if (clouds.some(cl => cl.gauss.length)) {
      // the fall-off of a Gaussian along its radius, 0 .. 3 standard deviations
      const stops = color => [0, 0.2, 0.4, 0.6, 0.8, 1].map(o =>
        `<stop offset="${o}" stop-color="${color}" stop-opacity="${Math.exp(-0.5 * (3 * o) ** 2).toFixed(3)}"/>`).join('');
      out.push(`<defs>${PERSON_COLORS.map((c, n) => `<radialGradient id="gauss-${n}">${stops(c)}</radialGradient>`).join('')}</defs>`);
    }
    if (tiling && clouds.some(cl => cl.tiles.length)) {
      const rooms = new Set(tiling.tiles.map(t => t[2]));
      out.push(`<defs>${state.config.zones.filter(z => rooms.has(z.id)).map(z =>
        `<clipPath id="room-${z.id}"><polygon points="${this.pts(zoneOutline(z))}"/></clipPath>`).join('')}</defs>`);
    }
    for (const cl of clouds) {
      const color = personColor(cl.id);
      if (tiling && cl.tiles.length) {
        // each tile a square, cut by its room; anywhere in it alike
        const top = Math.max(...cl.tiles.map(t => t[1]), 1e-9);
        const size = (tiling.size * this.s).toFixed(1);
        for (const [i, d] of cl.tiles) {
          const tile = tiling.tiles[i];
          if (!tile) continue;
          const [x, y] = this.P(tile[0], tile[1] + tiling.size);
          out.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${size}" height="${size}" fill="${color}"
            fill-opacity="${(0.06 + 0.45 * d / top).toFixed(2)}" clip-path="url(#room-${tile[2]})"/>`);
        }
      }
      cl.gauss.forEach(([w, mx, my, sx, sy], k) => {
        const poly = cl.clips?.[k];
        const clip = `clip-${cl.id}-${k}`;
        if (poly) out.push(`<clipPath id="${clip}"><polygon points="${this.pts(poly)}"/></clipPath>`);
        const [x, y] = this.P(mx, my);
        out.push(`<ellipse cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" rx="${(3 * sx * this.s).toFixed(1)}" ry="${(3 * sy * this.s).toFixed(1)}"
          fill="url(#gauss-${PERSON_COLORS.indexOf(color)})" fill-opacity="${(0.15 + 0.55 * w).toFixed(2)}" ${poly ? `clip-path="url(#${clip})"` : ''}/>`);
      });
    }
    return out.join('');
  }
}

// The coverage (the core's, of the world the model runs in: the saved configuration): per tile its square cut by its room,
// coloured by how many sensors see it; rooms of the home that are not observed as blind
MapView.prototype._coverage = function (cv) {
  const out = [];
  const zones = new Map(state.config.zones.map(z => [z.id, z]));
  const rooms = new Set(cv.tiles.map(t => t[1]));
  out.push(`<defs>${[...rooms].filter(id => zones.has(id)).map(id =>
    `<clipPath id="cov-${esc(id)}"><polygon points="${this.pts(zoneOutline(zones.get(id)))}"/></clipPath>`).join('')}</defs>`);
  const size = (cv.size * this.s).toFixed(1);
  for (const [[x0, y0], room, seen] of cv.tiles) {
    const [x, y] = this.P(x0, y0 + cv.size);
    out.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${size}" height="${size}" fill="${COVERAGE_COLORS[Math.min(seen, 2)]}"
      clip-path="url(#cov-${esc(room)})" pointer-events="none"/>`);
  }
  for (const r of cv.rooms) {
    const z = zones.get(r.id);
    if (!r.observed && z) out.push(`<polygon points="${this.pts(zoneOutline(z))}" fill="${COVERAGE_COLORS[0]}" pointer-events="none"/>`);
  }
  return out.join('');
};

export function bgToWorld(l, u, v) {
  const r = rad(l.rotation || 0), k = l.scale;
  return [l.x + k * (u * Math.cos(r) + v * Math.sin(r)), l.y + k * (u * Math.sin(r) - v * Math.cos(r))];
}

const pendingImages = new Map(); // url -> callbacks waiting for the size

export function loadSize(url, onload) {
  if (imageSizes.has(url)) return imageSizes.get(url);
  if (pendingImages.has(url)) {
    if (onload) pendingImages.get(url).add(onload);
    return null;
  }
  pendingImages.set(url, new Set(onload ? [onload] : []));
  const img = new Image();
  img.onload = () => {
    imageSizes.set(url, { w: img.naturalWidth, h: img.naturalHeight, img });
    const callbacks = pendingImages.get(url);
    pendingImages.delete(url);
    for (const fn of callbacks) fn();
  };
  img.src = url;
  return null;
}
