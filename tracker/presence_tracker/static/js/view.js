// SVG map: projection, pan/zoom, and drawing of the plan, sensors, zones and live data.
// Everything is drawn in screen pixels; world coordinates are meters with y pointing up.
import { api, coverageNow, heldBadges, openingsNow, state, sensorColor, unsaved } from './store.js';
import { PERSON_COLORS, ZONE_KINDS, crossesWall, dist, esc, fmt, outcomeColor, personColor, rad, zoneCenter, zoneOutline } from './util.js';

// coverage: tiles that no sensor, one, two or more see (the core's count per tile); a room the sensors don't
// see enough of to be observed is drawn as blind
const COVERAGE_COLORS = ['rgba(214,69,69,0.59)', 'rgba(240,180,41,0.22)', 'rgba(31,157,85,0.18)'];

// s: an arrow of the destination map is as long as a walk at its speed goes in this time
const ARROW_S = 0.2;

// m: a trail breaks where the person was shown this much farther than the point before (or through a wall):
// a line there would draw a way nobody walked
const TRAIL_JUMP = 1.0;

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

    // what the model learned (the tab "Gelerntes"), the layer chosen there
    if (state.tab === 'learned' && state.learned?.sensors) out.push(this._learned(state.learned));

    // blind-spot map
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

    // walls as drawn (a door paints its opening over its wall below); room dividers dashed; the selected one
    // on top
    const firstWall = out.length;
    const walls = c.walls.filter(w => w.kind !== 'divider').map(w => w.points);
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

    // doors: the opening the model has in its wall (the core's: derived.plan.doors), with a thin leaf line at
    // each end; until an edit of the walls or doors is saved, each door where it is entered
    const openings = openingsNow();
    (c.doors || []).forEach((door, k) => {
      const selected = sel?.kind === 'door' && sel.id === door.id;
      const o = openings?.[k];
      const w = o && c.walls[o.wall]?.points;
      if (!w) {
        const [x, y] = this.P(door.x, door.y);
        out.push(openings
          ? `<circle cx="${x}" cy="${y}" r="6" fill="var(--bad)" data-kind="door" data-id="${esc(door.id)}" ${pe('door')}><title>Tür ohne Wand</title></circle>`
          : `<circle class="door-entered${selected ? ' selected' : ''}" cx="${x}" cy="${y}" r="${Math.max(4, door.width * this.s / 2)}" data-kind="door" data-id="${esc(door.id)}" ${pe('door')}/>`);
        return;
      }
      const [a, b] = w, len = dist(a, b);
      const at = t => this.P(a[0] + (b[0] - a[0]) * t / len, a[1] + (b[1] - a[1]) * t / len);
      const [x0, y0] = at(o.t0), [x1, y1] = at(o.t1);
      const nx = -(y1 - y0), ny = x1 - x0, nl = Math.hypot(nx, ny) || 1, k5 = 5 / nl;
      out.push(`<g class="door${selected ? ' selected' : ''}" pointer-events="none">
          <line class="opening" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/>
          <line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/>
          <line x1="${x0 - nx * k5}" y1="${y0 - ny * k5}" x2="${x0 + nx * k5}" y2="${y0 + ny * k5}"/>
          <line x1="${x1 - nx * k5}" y1="${y1 - ny * k5}" x2="${x1 + nx * k5}" y2="${y1 + ny * k5}"/>
        </g>`);
      if (state.selectable.includes('door')) {
        out.push(`<line class="wall-hit" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" data-kind="door" data-id="${esc(door.id)}"/>`);
      }
    });

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
    const showFov = editing || state.showRaw;
    // what each sees, per tile as the filter weighs it (the core's sight of the saved configuration, beneath
    // the walls); while an edit is not saved its field of view as entered (not while the sight loads: no flash)
    const world = showFov && coverageNow();
    if (world) out.splice(firstWall, 0, this._sight(world, sel?.kind === 'sensor' ? sel.id : null));
    for (const s of c.sensors) {
      if (!s.placed) continue;
      const color = sensorColor(s.id);
      const selected = sel?.kind === 'sensor' && sel.id === s.id;
      const [x, y] = this.P(s.x, s.y);
      const a0 = rad(s.heading - s.fov / 2), a1 = rad(s.heading + s.fov / 2);
      const r = s.range * this.s;
      if (showFov && !world && unsaved()) {
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
    // the tab "Gelerntes" shows what was learned, not the people now
    if (!live || !state.config || state.tab === 'learned') { this.gDyn.innerHTML = ''; return; }
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
    // the uncertainty: only the cloud (cut off at the outline of its room); a circle around the dot would
    // claim "maybe next door" through the wall
    out.push(this.clouds(live));
    // trails, broken where the shown position jumped (more than TRAIL_JUMP or through a wall outside the doors'
    // openings since the point before): a line there would draw a path nobody walked
    const now = live.t;
    const openings = openingsNow() || [];
    for (const tr of live.tracks || []) {
      if (tr.x == null) continue;
      const trail = this.trails.get(tr.id) || [];
      const last = trail[trail.length - 1];
      const at = [tr.x, tr.y];
      if (!last || last[2] < now - 0.05) {
        const breaks = !!last && (dist(last, at) > TRAIL_JUMP || crossesWall(c.walls, openings, last, at));
        trail.push([tr.x, tr.y, now, breaks]);
      }
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
  // 3 standard deviations with a Gaussian fall-off, cut at the outline of the room of their mean (the
  // core's); without, the tiles they may be in (squares cut by their room), by mass per m^2
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
    const rooms = new Set(clouds.flatMap(cl => cl.rooms || []).filter(Boolean));
    if (tiling && clouds.some(cl => cl.tiles.length)) for (const t of tiling.tiles) rooms.add(t[2]);
    if (rooms.size) {
      out.push(`<defs>${state.config.zones.filter(z => rooms.has(z.id)).map(z =>
        `<clipPath id="room-${esc(z.id)}"><polygon points="${this.pts(zoneOutline(z))}"/></clipPath>`).join('')}</defs>`);
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
            fill-opacity="${(0.06 + 0.45 * d / top).toFixed(2)}" clip-path="url(#room-${esc(tile[2])})"/>`);
        }
      }
      cl.gauss.forEach(([w, mx, my, sx, sy], k) => {
        const room = cl.rooms?.[k];
        const [x, y] = this.P(mx, my);
        out.push(`<ellipse cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" rx="${(3 * sx * this.s).toFixed(1)}" ry="${(3 * sy * this.s).toFixed(1)}"
          fill="url(#gauss-${PERSON_COLORS.indexOf(color)})" fill-opacity="${(0.15 + 0.55 * w).toFixed(2)}" ${room ? `clip-path="url(#room-${esc(room)})"` : ''}/>`);
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

// What the sensors see (the core's sight of a person per tile, of the world the model runs in): each tile's
// square, cut by its room, in the sensor's colour as strong as its sight; the selected sensor's stronger, the
// others faint
MapView.prototype._sight = function (cv, selected) {
  const out = [];
  const zones = new Map(state.config.zones.map(z => [z.id, z]));
  const rooms = new Set(cv.tiles.map(t => t[1]));
  out.push(`<defs>${[...rooms].filter(id => zones.has(id)).map(id =>
    `<clipPath id="sight-${esc(id)}"><polygon points="${this.pts(zoneOutline(zones.get(id)))}"/></clipPath>`).join('')}</defs>`);
  const size = (cv.size * this.s).toFixed(1);
  for (const [id, sight] of Object.entries(cv.sight || {})) {
    const s = state.config.sensors.find(x => x.id === id);
    if (!s?.placed) continue;
    const color = sensorColor(id), k = selected === null ? 0.12 : id === selected ? 0.35 : 0.05;
    cv.tiles.forEach(([[x0, y0], room], t) => {
      if (!(sight[t] > 0)) return;
      const [x, y] = this.P(x0, y0 + cv.size);
      out.push(`<rect class="sight" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${size}" height="${size}" fill="${color}"
        fill-opacity="${(k * sight[t]).toFixed(3)}" clip-path="url(#sight-${esc(room)})" pointer-events="none"/>`);
    });
  }
  return out.join('');
};

// What the model learned (the core's, api/learned), the layer chosen in the tab "Gelerntes": the ghost map of a sensor
// per tile (its square cut by its room) as red as its rate above the prior's (log scale up to the most frequent);
// the destination map per cell an arrow in its most frequent velocity, as strong as its walks, and if asked the cell
// in the colour of where its walks most often went next (the core's), as strong as their share; the LD2410C's measured view per room in the sensor's colour as strong as
// the share it gets. The LD2410C's background has no map: its bars are in the panel.
MapView.prototype._learned = function (L) {
  const out = [];
  const zones = new Map(state.config.zones.map(z => [z.id, z]));
  const clips = rooms => `<defs>${[...new Set(rooms)].filter(id => zones.has(id)).map(id =>
    `<clipPath id="lrn-${esc(id)}"><polygon points="${this.pts(zoneOutline(zones.get(id)))}"/></clipPath>`).join('')}</defs>`;
  const square = ([x0, y0], size, room, attrs) => {
    const [x, y] = this.P(x0, y0 + size);
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(size * this.s).toFixed(1)}" height="${(size * this.s).toFixed(1)}"
      ${attrs} clip-path="url(#lrn-${esc(room)})" pointer-events="none"/>`;
  };
  const s = L.sensors[state.learnedSensor];
  if (state.learnedLayer === 'ghosts' && s) {
    const g = s.ghosts;
    const top = Math.log(Math.max(2, ...g.rate.filter(v => v != null).map(v => v / g.prior)));
    out.push(clips(L.tiles.map(t => t[1])));
    L.tiles.forEach(([corner, room], t) => {
      const v = g.rate[t];
      if (v == null) return;
      const a = 0.04 + 0.6 * Math.min(1, Math.max(0, Math.log(v / g.prior)) / top);
      out.push(square(corner, L.size, room, `fill="#d64545" fill-opacity="${a.toFixed(3)}"`));
    });
  } else if (state.learnedLayer === 'destinations') {
    const d = L.destinations;
    const top = Math.max(1e-9, ...d.cells.map(c => c.walks));
    if (state.learnedColors) {
      out.push(clips(d.cells.map(c => c.room)));
      for (const c of d.cells) {
        if (c.likely == null) continue;
        out.push(square(c.square, d.cell, c.room, `fill="${outcomeColor(d.outcomes, c.likely)}" fill-opacity="${(0.15 + 0.6 * c.shares[c.likely]).toFixed(3)}"`));
      }
    }
    for (const c of d.cells) {
      const w = c.walks / top;
      if (w < 0.02) continue;
      const [vx, vy] = c.velocity.map(v => v * ARROW_S / 2);
      const [x0, y0] = this.P(c.at[0] - vx, c.at[1] - vy), [x1, y1] = this.P(c.at[0] + vx, c.at[1] + vy);
      const len = Math.hypot(x1 - x0, y1 - y0) || 1, ux = (x1 - x0) / len, uy = (y1 - y0) / len, k = Math.min(5, 0.4 * len);
      const head = sgn => `${(x1 - k * (ux - sgn * 0.5 * uy)).toFixed(1)},${(y1 - k * (uy + sgn * 0.5 * ux)).toFixed(1)}`;
      out.push(`<path d="M${x0.toFixed(1)},${y0.toFixed(1)}L${x1.toFixed(1)},${y1.toFixed(1)}M${head(1)}L${x1.toFixed(1)},${y1.toFixed(1)}L${head(-1)}"
        fill="none" stroke="var(--text)" stroke-opacity="${(0.15 + 0.85 * w).toFixed(3)}" stroke-width="1.4" stroke-linecap="round" pointer-events="none"/>`);
    }
  } else if (state.learnedLayer === 'ld_view' && s?.ld_view) {
    const color = sensorColor(state.learnedSensor);
    for (const [id, share] of Object.entries(s.ld_view)) {
      const z = zones.get(id);
      if (!z) continue;
      const [x, y] = this.P(...zoneCenter(z));
      out.push(`<polygon points="${this.pts(zoneOutline(z))}" fill="${color}" fill-opacity="${(0.05 + 0.5 * Math.min(1, share)).toFixed(3)}" pointer-events="none"/>
        <text class="zone-label" x="${x}" y="${y + 16}" text-anchor="middle" fill="${color}" pointer-events="none">${Math.round(share * 100)} %</text>`);
    }
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
