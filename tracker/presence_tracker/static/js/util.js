// Small helpers shared by the UI modules.

// one colour per person: their dot, cloud and trail
export const PERSON_COLORS = ['#2f6fde', '#d9822b', '#2e9d5a', '#a855c7', '#c2413b', '#0f8f9c'];
export function personColor(id) { return PERSON_COLORS[((id - 1) % PERSON_COLORS.length + PERSON_COLORS.length) % PERSON_COLORS.length]; }

// color: fills and strokes; text: labels on the map (darker in light mode, for 4.5:1 contrast)
// where walks went next on the destination map (the core's outcomes: observed rooms, regions, "outside"), a colour each
const OUTCOME_COLORS = ['#2f6fde', '#d9822b', '#2e9d5a', '#a855c7', '#c2413b', '#0f8f9c', '#b8961c', '#d6589c', '#5d8a2e', '#8a5a3c'];
export function outcomeColor(outcomes, k) {
  if (outcomes[k] === 'outside') return '#3a3f47';
  return OUTCOME_COLORS[k % OUTCOME_COLORS.length];
}

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

// the header's "online/in use Sensoren": every sensor in use (the core's plan of the saved configuration:
// derived.plan.in_use) counts, also one the live message does not have (it has only those heard from since
// the model started: two boards offline showed as 5/5); online: with recent frames
export function sensorsOnline(derived, live) {
  const used = derived?.plan?.in_use || [];
  return { online: used.filter(id => live?.sensors?.[id]?.online).length, total: used.length };
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

// The words of the numbers of the configuration the model reads (the fields of the core's limits,
// config_limits): the settings, the sensor's and the door's fields, the problems the core finds (problemText)
export const LABELS = {
  params: {
    target_height: 'Höhe des Oberkörpers',
    range_sigma_base: 'Messfehler in Blickrichtung, Grundwert',
    range_sigma_slope: 'Messfehler in Blickrichtung, Anstieg',
    lateral_sigma_base: 'Messfehler seitlich, Grundwert',
    lateral_sigma_slope: 'Messfehler seitlich, Anstieg',
    wall_margin: 'Toleranz an Wänden',
    dwell_median: 'Typischer Aufenthalt',
    dwell_spread: 'Streuung des Aufenthalts',
    ld2410_hold: 'Haltezeit LD2410C (Anzeige)',
    light_cost: 'Kosten: Licht ohne Person',
    seen_hold: 'Haltezeit eigener LD2450',
    lead_time: 'Vorausschau „wird betreten“',
    approach_cost: 'Kosten: Einschalten auf Verdacht',
    target_threshold: 'Schwelle „Ziel“',
  },
  sensor: { x: 'x', y: 'y', heading: 'Blickrichtung', height: 'Montagehöhe', fov: 'Öffnungswinkel', range: 'Reichweite', scale: 'Maßstab' },
  door: { width: 'Breite der Tür' },
};
LABELS.threshold = LABELS.params;

// A problem the core found with a configuration (the server's 400: part, name, field, reason, limits, value) in
// words. "part|reason|field", "part|reason", "|reason": the most special one there is; {where} the part with its
// name, {label} the field's words, {value} the value as a number is written here, {raw} as it is, {low}/{high}
// the limits
const SENTENCES = {
  '|empty': '{where}{label}: leer, eine Zahl fehlt.',
  '|not_a_number': '{where}{label}: keine Zahl ({value}).',
  '|out_of_range': '{where}{label} muss {low} und {high} sein, nicht {value}.',
  '|not_an_object': 'Konfiguration: „{field}“ ist kein Objekt.',
  '|not_a_list': 'Konfiguration: „{field}“ ist keine Liste.',
  '|unreadable': 'Konfiguration unvollständig oder falsch: {raw}',
  'config|not_an_object': 'Konfiguration ist kein Objekt.',
  'config|too_many_sensors': 'Höchstens {value} Sensoren.',
  'config|too_large': 'Der Grundriss ist zu groß (höchstens 100 × 100 m).',
  'params|not_an_object': 'Einstellungen sind kein Objekt.',
  'params|not_an_object|target_thresholds': 'Schwelle „Ziel“ je Raum ist kein Objekt.',
  'sensor|no_id': 'Sensor ohne ID.',
  'sensor|id_chars': '{where}Die ID darf kein /, + oder # enthalten (sie ist Teil des MQTT-Topics).',
  'sensor|id_twice': 'Sensor-ID {raw} doppelt.',
  'sensor|not_a_bool': '{where}{field} ist nicht ja/nein.',
  'wall|not_points': 'Wand {name}: Punkte sind keine endlichen Koordinaten.',
  'wall|not_a_segment': 'Wand {name}: braucht genau zwei verschiedene Punkte.',
  'wall|unknown_kind': 'Wand {name}: unbekannte Art {value}.',
  'door|no_position': 'Tür ohne endliche Position.',
  'door|not_on_wall': 'Eine Tür liegt auf keiner Wand (Türen sitzen auf Wänden, nicht auf Raumgrenzen).',
  'zone|not_an_object': 'Zone ist kein Objekt.',
  'zone|no_id': 'Zone ohne ID.',
  'zone|id_twice': 'Zonen-ID {raw} doppelt.',
  'zone|unknown_kind': 'Zone {name}: unbekannte Art {value}.',
  'zone|unknown_shape': 'Zone {name}: unbekannte Form {value}.',
  'zone|not_points': 'Zone {name}: Punkte sind keine endlichen Koordinaten.',
  'zone|rect_corners': 'Zone {name}: ein Rechteck braucht zwei Ecken.',
  'zone|polygon_corners': 'Zone {name}: eine Fläche braucht mindestens drei Eckpunkte.',
  'zone|no_centre': 'Zone {name}: ein Kreis braucht einen Mittelpunkt.',
  'zone|not_at_least_0': 'Zone {name}: Radius ist keine Zahl ≥ 0.',
};
const WHERE = { threshold: 'Raum {name}: ', sensor: 'Sensor {name}: ' };

// a number as the problems write it (6 places, decimal comma), anything else as JSON
const problemNumber = v => (typeof v === 'number' ? String(+v.toPrecision(6)).replace('.', ',') : JSON.stringify(v));

export function problemText(p) {
  const text = SENTENCES[`${p.part}|${p.reason}|${p.field}`] ?? SENTENCES[`${p.part}|${p.reason}`] ?? SENTENCES[`|${p.reason}`]
    ?? `Konfiguration: ${p.reason}`;
  const fill = t => t.replace(/\{(\w+)\}/g, (_, k) => words[k] ?? '');
  const words = { name: p.name ?? '', field: p.field ?? '', raw: String(p.value ?? ''), value: problemNumber(p.value),
    label: LABELS[p.part]?.[p.field] ?? p.field ?? '' };
  words.where = fill(WHERE[p.part] ?? '');
  if (p.reason === 'out_of_range' && p.limits) {
    const [lo, hi, loOpen, hiOpen] = p.limits;
    words.low = `${loOpen ? 'größer als' : 'mindestens'} ${problemNumber(lo)}`;
    words.high = `${hiOpen ? 'kleiner als' : 'höchstens'} ${problemNumber(hi)}`;
  }
  return fill(text);
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

// The server's errors ({error: key, detail}: app.py, calibration's no_walking from the core) in words; an
// unknown key as it is
const ERRORS = {
  model_down: 'Das Modell läuft gerade nicht: {detail}',
  model_refused: 'Das Modell läuft mit dieser Konfiguration nicht: {detail}. Es gilt die vorige.',
  not_json: 'Keine gültige Konfiguration (JSON).',
  not_saved: 'Nicht gespeichert: {detail}',
  in_use_not_saved: 'In Gebrauch, aber nicht gespeichert: {detail}',
  https_only: 'Nur https-URLs.',
  download: 'Bild nicht geladen: HTTP {detail}',
  image_type: 'Nur PNG, JPEG, WebP oder SVG.',
  report_kind: 'Art des Fehlers fehlt.',
  minutes: 'Die Minuten sind keine Zahl.',
  no_ha: 'Keine Verbindung zu Home Assistant konfiguriert.',
  ha_http: 'Home Assistant: HTTP {detail}',
  no_walking: 'Keine Messungen einer Person in Bewegung.',
};
export const errorText = d => (ERRORS[d.error] ?? String(d.error)).replace('{detail}', d.detail ?? '');

// The kinds of an error report (the server keeps the key: the reports' header and file name). New kinds only
// add keys: old reports keep theirs
export const REPORT_KINDS = {
  light_on: 'Licht fälschlich an (niemand da)',
  light_off: 'Licht fälschlich aus (jemand da)',
  light_late: 'Licht kam zu spät',
  lost: 'Person verloren',
  ghost: 'Geist (Person, wo niemand ist)',
  wrong_place: 'Person am falschen Ort',
  inaccurate: 'Ungenaues Tracking',
  latency: 'Hohe Latenz',
  other: 'Sonstiges',
};

// Why the calibration proposes nothing or less for a sensor (the core's reasons: kind and numbers) in words
const REASONS = {
  few: r => `Erst ${r.stretches} von ${r.min} Stunden mit Gehenden.`,
  worse: r => `Mit dem neuen Wert lägen mehr Punkte in Bewegung außerhalb der Sicht (${fmt(100 * r.after, 0)} % statt ${fmt(100 * r.before, 0)} %). Bleibt wie bisher.`,
  conflict: r => `Grundriss (${fmt(r.plan, 0)}°) und gemeinsame Messungen mit anderen Sensoren (${fmt(r.pairs, 0)}°) widersprechen sich. Stimmt die eingezeichnete Position des Sensors und der Wände um ihn?`,
  ambiguous: r => `Drehung mehrdeutig: ${fmt(r.alt, 0)}° passt fast so gut.`,
  heading_uncertain: r => `Drehung unsicher (± ${fmt(r.sd, 0)}°).`,
  no_ruler: () => 'Maßstab: zu wenig gemeinsame Messungen mit anderen Sensoren.',
  scale_uncertain: r => `Maßstab unsicher (± ${fmt(r.sd, 2)}).`,
  mirrored: r => `Gespiegelt passen die Messungen besser (log ${fmt(r.gain, 0)}).`,
};
export const reasonText = r => REASONS[r.kind]?.(r) ?? r.kind;

// What a save of another geometry (a sensor's place or view, the walls, doors, zones; the server's "discarded":
// the calibration's measurements) discarded, as a sentence; '' for nothing
export function discardedText(d) {
  if (!d) return '';
  const n = d.calibration.toLocaleString('de-DE');
  return `Sensorlage oder Grundriss geändert, verworfen: ${n} Kalibriermessungen aller Sensoren, alles Gelernte `
    + '(Geisterkarte, LD2410C-Hintergrund, Zielkarte).';
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

export function distToSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

// even-odd rule: the edges crossed by a ray from the point to the right
export function pointInPolygon([x, y], pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// ---- the editor's geometry of the map (the model reads the saved map: docs/rust-core.md 1)

// Straight pieces of the walls: [a, b, kind, wallIndex]
export function wallPieces(walls, kind = null) {
  const out = [];
  walls.forEach((w, wi) => {
    if (kind && w.kind !== kind) return;
    for (let i = 0; i + 1 < w.points.length; i++) out.push([w.points[i], w.points[i + 1], w.kind, wi]);
  });
  return out;
}

// The real wall a door sits on (the nearest; the editor keeps it on one): {a, b, dir, t, length, wi} (t: the
// door's centre in m from a), or null without real walls
export function doorWall(walls, door) {
  let best = null;
  for (const [a, b, , wi] of wallPieces(walls, 'wall')) {
    const length = dist(a, b);
    if (length < 1e-6) continue;
    const d = distToSegment([door.x, door.y], a, b);
    if (!best || d < best.d) best = { d, a, b, wi, length };
  }
  if (!best) return null;
  const { a, b, length } = best;
  const dir = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
  const t = (door.x - a[0]) * dir[0] + (door.y - a[1]) * dir[1];
  return { a, b, dir, t, length, wi: best.wi };
}

// A door at p lies on the segment a-b: within onWall m of it. The editor puts every door exactly on a real wall
// (mm) and keeps it there; the model reads the wall it lies on and refuses a door on none: onWall is the core's
// (derived.plan.door_on_wall, store.onWall)
export const onSegment = (p, a, b, onWall) => distToSegment(p, a, b) <= onWall;

// Where each door is along its wall, before walls move: [{id, wi, f}] (f: fraction of the way from the
// wall's first end)
export function doorsOnWalls(c, onWall) {
  const out = [];
  for (const door of c.doors || []) {
    const pl = doorWall(c.walls, door);
    if (pl && onSegment([door.x, door.y], pl.a, pl.b, onWall)) out.push({ id: door.id, wi: pl.wi, f: pl.t / pl.length });
  }
  return out;
}

// Whether the line a-b crosses a real wall outside the doors' openings (the core's, of the saved map:
// derived.plan.doors, per door its wall and where its opening begins and ends along it): a trail drawn there
// would draw a way through the wall (view.js)
export function crossesWall(walls, openings, a, b) {
  for (const [p, q, , wi] of wallPieces(walls, 'wall')) {
    const r = [b[0] - a[0], b[1] - a[1]], e = [q[0] - p[0], q[1] - p[1]];
    const den = r[0] * e[1] - r[1] * e[0];
    if (Math.abs(den) < 1e-12) continue;
    const w = [p[0] - a[0], p[1] - a[1]];
    const s = (w[0] * e[1] - w[1] * e[0]) / den, u = (w[0] * r[1] - w[1] * r[0]) / den;
    if (s < 0 || s > 1 || u < 0 || u > 1) continue;
    const along = u * Math.hypot(e[0], e[1]);
    if (!openings.some(o => o && o.wall === wi && along >= o.t0 && along <= o.t1)) return true;
  }
  return false;
}

// The doors back on their walls after these moved: at the same fraction of the way (doorsOnWalls)
export function followDoors(c, placed) {
  for (const { id, wi, f } of placed || []) {
    const door = c.doors.find(x => x.id === id);
    const w = c.walls[wi]?.points;
    if (!door || !w) continue;
    const [a, b] = w;
    [door.x, door.y] = [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])].map(v => +v.toFixed(3));
  }
}

// where a zone's name goes: a room's anchor (floorplan.js), else the middle of its bounds
export function zoneCenter(z) {
  if (z.kind === 'room' && z.anchor) return z.anchor;
  if (z.shape === 'circle') return z.center;
  const pts = zoneOutline(z);
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

// m², of the outline as drawn
export function zoneArea(z) {
  if (z.shape === 'circle') return Math.PI * z.radius * z.radius;
  const pts = zoneOutline(z);
  let a = 0;
  for (let i = 0; i < pts.length; i++) { const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length]; a += x1 * y2 - x2 * y1; }
  return Math.abs(a) / 2;
}

// The placement of a layer of the background image and its limits (the web UI's alone: the model never reads
// the image): [low, high, low excluded, high excluded] as numberError takes them. scale in m per pixel: an SVG
// drawn in metres is about 1.
export const LAYER_LIMITS = {
  x: [-1e4, 1e4, false, false],
  y: [-1e4, 1e4, false, false],
  scale: [0, 10, true, false],
  rotation: [-3600, 3600, false, false],
  opacity: [0, 1, false, false],
};
const LAYER_LABELS = { x: 'x des Bildes', y: 'y des Bildes', scale: 'Maßstab des Bildes', rotation: 'Drehung des Bildes', opacity: 'Deckkraft des Bildes' };

// What is wrong with the background image's layers ('' if nothing): the sentence for the user
export function layersError(background) {
  const layers = background?.layers ?? [];
  if (!Array.isArray(layers)) return 'Die Bildebenen sind keine Liste.';
  for (const l of layers) {
    if (l === null || typeof l !== 'object' || Array.isArray(l)) return 'Bildebene ist kein Objekt.';
    for (const [k, lim] of Object.entries(LAYER_LIMITS)) {
      if (!(k in l)) continue;
      const err = numberError(l[k], lim);
      if (err) return `${LAYER_LABELS[k]}: ${err}`;
    }
  }
  return '';
}

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
