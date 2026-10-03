// Blind-spot map: which parts of the rooms no sensor can see (field of view, range, walls).
import { sensorSees, sightSegments, zoneContains, zoneOutline } from './util.js';

export function computeCoverage(config, cell = 0.1) {
  const rooms = config.zones.filter(z => z.kind === 'room');
  if (!rooms.length) return null;
  const sensors = config.sensors.filter(s => s.placed && s.enabled);
  const segs = sightSegments(config);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const z of rooms) for (const [x, y] of zoneOutline(z, 16)) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  const cols = Math.ceil((x1 - x0) / cell), rows = Math.ceil((y1 - y0) / cell);
  const canvas = document.createElement('canvas');
  canvas.width = cols; canvas.height = rows;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(cols, rows);
  const perRoom = new Map(rooms.map(z => [z.id, { name: z.name, cells: 0, blind: 0, single: 0 }]));
  // only sensors whose range box reaches the cell are tested
  const reach = sensors.map(s => [s, s.x - s.range, s.x + s.range, s.y - s.range, s.y + s.range]);
  for (let r = 0; r < rows; r++) {
    const y = y1 - (r + 0.5) * cell;
    for (let c = 0; c < cols; c++) {
      const x = x0 + (c + 0.5) * cell;
      const room = rooms.find(z => zoneContains(z, x, y));
      if (!room) continue;
      let n = 0;
      for (const [s, ax, bx, ay, by] of reach) {
        if (x < ax || x > bx || y < ay || y > by) continue;
        if (sensorSees(s, x, y, segs)) n++;
        if (n >= 2) break;
      }
      const st = perRoom.get(room.id);
      st.cells++;
      if (n === 0) st.blind++;
      else if (n === 1) st.single++;
      const i = 4 * (r * cols + c);
      const [cr, cg, cb, ca] = n === 0 ? [214, 69, 69, 150] : n === 1 ? [240, 180, 41, 55] : [31, 157, 85, 45];
      img.data[i] = cr; img.data[i + 1] = cg; img.data[i + 2] = cb; img.data[i + 3] = ca;
    }
  }
  ctx.putImageData(img, 0, 0);
  const area = cell * cell;
  return {
    url: canvas.toDataURL(), x0, y1, cell, cols, rows,
    rooms: [...perRoom.values()].map(s => ({
      name: s.name, area: s.cells * area, blind: s.blind * area, single: s.single * area,
    })),
  };
}
