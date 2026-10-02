// SVG map: projection, pan/zoom, and drawing of the plan, sensors, zones and live data.
// Everything is drawn in screen pixels; world coordinates are meters with y pointing up.
import { state, sensorColor } from './store.js';
import { ZONE_KINDS, esc, rad, zoneCenter, zoneOutline } from './util.js';

const imageSizes = new Map(); // url -> {w, h}

export class MapView {
  constructor(svg) {
    this.svg = svg;
    this.cx = 1; this.cy = 3; this.s = 50; // px per meter
    this.controller = null;
    this.pointers = new Map();
    this.trails = new Map(); // track id -> [[x, y, t]]
    this.coverage = null;
    svg.innerHTML = '<g id="l-static"></g><g id="l-dyn"></g><g id="l-overlay"></g><g id="l-scale" class="scale-bar"></g>';
    this.gStatic = svg.querySelector('#l-static');
    this.gDyn = svg.querySelector('#l-dyn');
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
    for (const w of c.walls) for (const [x, y] of w) add(x, y);
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
      const hit = ev.target.closest?.('[data-kind]');
      const handled = ev.button === 0 && this.controller?.down?.(world, hit, ev);
      if (!handled) this.pan = { x: ev.clientX, y: ev.clientY, cx: this.cx, cy: this.cy, moved: false, hit };
    });
    svg.addEventListener('pointermove', ev => {
      const world = this.eventWorld(ev);
      document.getElementById('cursor').textContent = `x ${world[0].toFixed(2)}  y ${world[1].toFixed(2)} m`;
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
      if (this.pointers.size < 2) this.pinch = null;
      if (this.pan) {
        svg.classList.remove('panning');
        if (!this.pan.moved && ev.button === 0) this.controller?.click?.(this.eventWorld(ev), this.pan.hit, ev);
        this.pan = null;
        return;
      }
      this.controller?.up?.(this.eventWorld(ev), ev);
    };
    svg.addEventListener('pointerup', up);
    svg.addEventListener('pointercancel', up);
    svg.addEventListener('dblclick', ev => this.controller?.dblclick?.(this.eventWorld(ev), ev));
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

    // background images
    for (const l of c.background.layers) {
      if (!l.visible) continue;
      const size = loadSize(l.url, () => this.render());
      if (!size) continue;
      const m = this._bgMatrix(l);
      out.push(`<image href="${esc(l.url)}" width="${size.w}" height="${size.h}" transform="matrix(${m})"
        opacity="${l.opacity ?? 0.6}" preserveAspectRatio="none" data-kind="layer" data-id="${esc(l.id)}"
        style="${state.tab === 'plan' ? '' : 'pointer-events:none'}"/>`);
    }

    // blind-spot map
    if (state.showCoverage && this.coverage) {
      const cv = this.coverage;
      const [sx, sy] = this.P(cv.x0, cv.y1);
      const k = cv.cell * this.s;
      out.push(`<image href="${cv.url}" width="${cv.cols}" height="${cv.rows}" style="image-rendering:pixelated;pointer-events:none"
        transform="matrix(${k},0,0,${k},${sx},${sy})"/>`);
    }

    // zones
    const zoneStates = state.live?.zones || {};
    for (const z of c.zones) {
      const kind = ZONE_KINDS[z.kind] || ZONE_KINDS.area;
      const zs = zoneStates[z.id];
      const occupied = zs?.occupied && state.tab === 'live';
      const selected = sel?.kind === 'zone' && sel.id === z.id;
      const fillOpacity = occupied ? 0.28 : (z.kind === 'room' ? 0.05 : 0.12);
      const cls = `zone${selected ? ' selected' : ''}${zs?.approaching && state.tab === 'live' ? ' approaching' : ''}`;
      const common = `class="${cls}" fill="${kind.color}" fill-opacity="${fillOpacity}" stroke="${kind.color}" data-kind="zone" data-id="${esc(z.id)}"`;
      if (z.shape === 'circle') {
        const [x, y] = this.P(...z.center);
        out.push(`<circle cx="${x}" cy="${y}" r="${z.radius * this.s}" ${common}/>`);
      } else {
        out.push(`<polygon points="${this.pts(zoneOutline(z))}" ${common}/>`);
      }
    }

    // walls
    for (let i = 0; i < c.walls.length; i++) {
      const selected = sel?.kind === 'wall' && sel.id === i;
      out.push(`<polyline class="wall${selected ? ' selected' : ''}" points="${this.pts(c.walls[i])}" data-kind="wall" data-id="${i}"/>`);
    }

    // zone labels on top of walls
    for (const z of c.zones) {
      if (z.kind === 'room' || state.tab !== 'live' || this.s > 25) {
        const kind = ZONE_KINDS[z.kind] || ZONE_KINDS.area;
        const zs = zoneStates[z.id];
        const [x, y] = this.P(...zoneCenter(z));
        const count = state.tab === 'live' && zs ? ` · ${zs.count}` : '';
        out.push(`<text class="zone-label" x="${x}" y="${y}" text-anchor="middle" fill="${kind.color}" pointer-events="none">${esc(z.name)}${count}</text>`);
      }
    }

    // sensors
    for (const s of c.sensors) {
      if (!s.placed) continue;
      const color = sensorColor(s.id);
      const selected = sel?.kind === 'sensor' && sel.id === s.id;
      const [x, y] = this.P(s.x, s.y);
      const a0 = rad(s.heading - s.fov / 2), a1 = rad(s.heading + s.fov / 2);
      const r = s.range * this.s;
      const showFov = editing || state.showRaw;
      if (showFov) {
        const [x0, y0] = [x + r * Math.cos(a0), y - r * Math.sin(a0)];
        const [x1, y1] = [x + r * Math.cos(a1), y - r * Math.sin(a1)];
        out.push(`<path class="fov" d="M${x},${y} L${x0},${y0} A${r},${r} 0 0 0 ${x1},${y1} Z" fill="${color}"
          fill-opacity="${selected ? 0.12 : 0.04}" stroke="${color}" stroke-opacity="${selected ? 0.9 : 0.35}" pointer-events="none"/>`);
      }
      const hx = x + 14 * Math.cos(rad(s.heading)), hy = y - 14 * Math.sin(rad(s.heading));
      out.push(`<g data-kind="sensor" data-id="${esc(s.id)}" style="cursor:pointer">
        <circle cx="${x}" cy="${y}" r="${selected ? 9 : 7}" fill="${color}" stroke="var(--surface)" stroke-width="2"/>
        <line x1="${x}" y1="${y}" x2="${hx}" y2="${hy}" stroke="${color}" stroke-width="3" stroke-linecap="round"/>
        ${s.enabled ? '' : `<line x1="${x - 6}" y1="${y - 6}" x2="${x + 6}" y2="${y + 6}" stroke="var(--surface)" stroke-width="2"/>`}
      </g>
      <text class="sensor-label" x="${x + 12}" y="${y - 10}" fill="${color}" pointer-events="none">${esc(s.name || s.id)}</text>`);
    }

    this.gStatic.innerHTML = out.join('');
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
          out.push(d.ignored
            ? `<path d="M${x - 4},${y - 4}L${x + 4},${y + 4}M${x - 4},${y + 4}L${x + 4},${y - 4}" stroke="${color}" stroke-width="2"/>`
            : `<circle cx="${x}" cy="${y}" r="4" fill="${color}" fill-opacity="0.85"/>`);
        }
      }
    }
    // trails
    const now = live.t;
    for (const tr of live.tracks || []) {
      if (tr.status !== 'confirmed') continue;
      const trail = this.trails.get(tr.id) || [];
      if (!trail.length || trail[trail.length - 1][2] < now - 0.05) trail.push([tr.x, tr.y, now]);
      while (trail.length && trail[0][2] < now - 6) trail.shift();
      this.trails.set(tr.id, trail);
    }
    const ids = new Set((live.tracks || []).map(t => t.id));
    for (const id of this.trails.keys()) if (!ids.has(id)) this.trails.delete(id);
    for (const trail of this.trails.values()) {
      if (trail.length > 1) out.push(`<polyline points="${this.pts(trail)}" fill="none" stroke="var(--moving)" stroke-opacity="0.35" stroke-width="2"/>`);
    }
    for (const tr of live.tracks || []) {
      const [x, y] = this.P(tr.x, tr.y);
      if (tr.status !== 'confirmed') {
        out.push(`<circle cx="${x}" cy="${y}" r="8" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="2 3"/>`);
        continue;
      }
      const moving = !tr.lost && tr.walk > 0.5 && Math.hypot(tr.vx, tr.vy) > 0.15;
      const color = tr.lost ? 'var(--lost)' : moving ? 'var(--moving)' : 'var(--still)';
      const sr = Math.max(tr.sigma * 2 * this.s, 14);
      out.push(`<circle cx="${x}" cy="${y}" r="${sr}" fill="${color}" fill-opacity="0.1" stroke="${color}" stroke-opacity="0.4"/>`);
      if (moving) {
        const [vx, vy] = this.P(tr.x + tr.vx, tr.y + tr.vy);
        out.push(`<line x1="${x}" y1="${y}" x2="${vx}" y2="${vy}" stroke="${color}" stroke-width="2.5" stroke-linecap="round"/>`);
      }
      out.push(`<circle cx="${x}" cy="${y}" r="10" fill="${color}" ${tr.lost ? 'fill-opacity="0.55"' : ''} stroke="var(--surface)" stroke-width="2"/>
        <text class="track-label" x="${x}" y="${y + 4}" text-anchor="middle">${tr.id % 100}</text>`);
    }
    this.gDyn.innerHTML = out.join('');
  }
}

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
