//! Rooms from walls (MODEL.md 2): the plan is drawn as walls only, and the rooms are the closed areas
//! between them, found here whenever the plan is edited (the web UI sends the walls, the shell stores what
//! comes back).
//!
//! Every wall is one straight segment of a kind: a real wall blocks the radar's line of sight, a divider is a
//! room boundary without a wall (between a living and a dining area). For finding rooms both close an area,
//! and doors (on a wall) count as closed. Rooms are the faces of the planar graph of the walls. Each room
//! zone keeps an anchor point inside it; after an edit, the face that holds the anchor is the same room, so
//! names and Home Assistant entities survive moving walls.
//!
//! Numbers and their order follow the Python editor of 0.43.0 (floorplan.py; its results on every
//! configuration at hand were the reference). Its order of nodes, edges and faces came from Python's sets;
//! here it is the order the walls give, and a room whose area did not change keeps its stored corners.

use serde_json::{Map, Value, json};

use crate::geometry::{Point, distance_to_segment, in_polygon};
use crate::vmath::{py_dist, py_round, py_sum};

/// m: wall ends this close to each other or to a wall are joined
pub const SNAP: f64 = 0.03;
/// m²: smaller closed areas are slivers, not rooms
pub const MIN_ROOM_AREA: f64 = 0.3;
/// decimals of the corners and anchors (mm)
const DIGITS: usize = 3;

/// A plan that cannot be read: a wall without two points, a room without an id.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanError(pub String);

impl std::fmt::Display for PlanError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "the plan cannot be read: {}", self.0)
    }
}

impl std::error::Error for PlanError {}

/// The walls of the stored form ([{"points": [a, b, ...], "kind"}], every kind) as segments: their first
/// two points.
pub fn walls_of(walls: &Value) -> Result<Vec<[Point; 2]>, PlanError> {
    walls
        .as_array()
        .ok_or_else(|| PlanError("the walls are no list".into()))?
        .iter()
        .enumerate()
        .map(|(k, w)| {
            let pts = w.get("points").and_then(Value::as_array);
            match pts.map(|p| (p.first().and_then(point_of), p.get(1).and_then(point_of))) {
                Some((Some(a), Some(b))) => Ok([a, b]),
                _ => Err(PlanError(format!("wall {} has no two points", k + 1))),
            }
        })
        .collect()
}

fn cross(o: Point, a: Point, b: Point) -> f64 {
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
}

/// The proper crossing of two segments: t along p and the point, or None.
fn intersection(p1: Point, p2: Point, q1: Point, q2: Point) -> Option<(f64, Point)> {
    let d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0]);
    if d.abs() < 1e-12 {
        return None;
    }
    let t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d;
    let u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d;
    (0.0 < t && t < 1.0 && 0.0 < u && u < 1.0).then(|| (t, [p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])]))
}

/// The nodes of the graph: points closer than SNAP are one.
#[derive(Default)]
struct Nodes(Vec<Point>);

impl Nodes {
    fn of(&mut self, p: Point) -> usize {
        if let Some(k) = self.0.iter().position(|&q| py_dist(p, q) < SNAP) {
            return k;
        }
        self.0.push(p);
        self.0.len() - 1
    }
}

/// The closed areas enclosed by the walls (all kinds), as counterclockwise polygons, corners in mm.
pub fn faces(walls: &[[Point; 2]]) -> Vec<Vec<Point>> {
    let segs: Vec<[Point; 2]> = walls.iter().copied().filter(|&[a, b]| py_dist(a, b) > 1e-6).collect();

    // every piece split where another one crosses it or ends on it (T-junctions): (t along it, point)
    let mut cuts: Vec<Vec<(f64, Point)>> = segs.iter().map(|&[a, b]| vec![(0.0, a), (1.0, b)]).collect();
    for (i, &[a, b]) in segs.iter().enumerate() {
        let length = py_dist(a, b);
        for (j, &[c, d]) in segs.iter().enumerate() {
            if i == j {
                continue;
            }
            if let Some(x) = intersection(a, b, c, d) {
                cuts[i].push(x);
            }
            for e in [c, d] {
                if distance_to_segment(e, a, b) < SNAP {
                    let t = ((e[0] - a[0]) * (b[0] - a[0]) + (e[1] - a[1]) * (b[1] - a[1])) / (length * length);
                    if SNAP / length < t && t < 1.0 - SNAP / length {
                        // the point on this wall, not the loose end next to it
                        cuts[i].push((t, [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]));
                    }
                }
            }
        }
    }

    // points on walls first, so that loose wall ends join them and not the other way round
    let mut nodes = Nodes::default();
    for pts in &cuts {
        for &(_, p) in &pts[2..] {
            nodes.of(p);
        }
    }
    let mut edges = std::collections::BTreeSet::new();
    for pts in &mut cuts {
        pts.sort_by(|x, y| {
            x.0.total_cmp(&y.0)
                .then(x.1[0].total_cmp(&y.1[0]))
                .then(x.1[1].total_cmp(&y.1[1]))
        });
        let ids: Vec<usize> = pts.iter().map(|&(_, p)| nodes.of(p)).collect();
        for w in ids.windows(2) {
            if w[0] != w[1] {
                edges.insert((w[0].min(w[1]), w[0].max(w[1])));
            }
        }
    }
    let nodes = nodes.0;

    let mut adj: Vec<Vec<usize>> = vec![Vec::new(); nodes.len()];
    for &(u, v) in &edges {
        adj[u].push(v);
        adj[v].push(u);
    }
    // free-standing wall ends enclose nothing
    let mut loose: Vec<usize> = (0..nodes.len()).filter(|&n| adj[n].len() == 1).collect();
    while let Some(n) = loose.pop() {
        for m in std::mem::take(&mut adj[n]) {
            adj[m].retain(|&x| x != n);
            if adj[m].len() == 1 {
                loose.push(m);
            }
        }
    }
    // around each node its neighbours by angle (ties by node)
    for (n, nb) in adj.iter_mut().enumerate() {
        let angle = |m: usize| (nodes[m][1] - nodes[n][1]).atan2(nodes[m][0] - nodes[n][0]);
        nb.sort_by(|&a, &b| angle(a).total_cmp(&angle(b)).then(a.cmp(&b)));
    }

    let mut seen = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for u in 0..nodes.len() {
        for &v in &adj[u] {
            if seen.contains(&(u, v)) {
                continue;
            }
            // walk with the face on the left: at each corner take the next edge clockwise
            let mut ring = Vec::new();
            let (mut a, mut b) = (u, v);
            while seen.insert((a, b)) {
                ring.push(nodes[a]);
                let nb = &adj[b];
                let k = nb.iter().position(|&x| x == a).unwrap_or(0);
                (a, b) = (b, nb[(k + nb.len() - 1) % nb.len()]);
            }
            if signed_area(&ring) > MIN_ROOM_AREA {
                out.push(
                    drop_collinear(&ring)
                        .into_iter()
                        .map(|[x, y]| [py_round(x, DIGITS), py_round(y, DIGITS)])
                        .collect(),
                );
            }
        }
    }
    out
}

fn signed_area(ring: &[Point]) -> f64 {
    let next = ring.iter().cycle().skip(1);
    py_sum(ring.iter().zip(next).map(|(&[x1, y1], &[x2, y2])| x1 * y2 - x2 * y1)) / 2.0
}

fn drop_collinear(ring: &[Point]) -> Vec<Point> {
    let n = ring.len();
    let out: Vec<Point> = (0..n)
        .filter(|&i| {
            let (o, a, b) = (ring[(i + n - 1) % n], ring[i], ring[(i + 1) % n]);
            cross(o, a, b).abs() > 1e-6 * (py_dist(o, a) * py_dist(a, b)).max(1e-9)
        })
        .map(|i| ring[i])
        .collect();
    if out.is_empty() { ring.to_vec() } else { out }
}

/// A point well inside the polygon: of its centroid and a 15 x 15 grid over its bounds, the one in it
/// farthest from the edges (mm).
pub fn interior_point(poly: &[Point]) -> Point {
    let Some(&first) = poly.first() else {
        return [0.0, 0.0];
    };
    let (xs, ys): (Vec<f64>, Vec<f64>) = poly.iter().map(|&[x, y]| (x, y)).unzip();
    let lo = |v: &[f64]| v.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = |v: &[f64]| v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let (x0, x1, y0, y1) = (lo(&xs), hi(&xs), lo(&ys), hi(&ys));
    let n = poly.len() as f64;
    let mut candidates = vec![[py_sum(xs.iter().copied()) / n, py_sum(ys.iter().copied()) / n]];
    for i in 1..16 {
        for j in 1..16 {
            candidates.push([
                x0 + (x1 - x0) * f64::from(i) / 16.0,
                y0 + (y1 - y0) * f64::from(j) / 16.0,
            ]);
        }
    }
    let edges = poly.iter().zip(poly.iter().cycle().skip(1));
    let mut best: Option<(Point, f64)> = None;
    for p in candidates {
        if !in_polygon(p, poly) {
            continue;
        }
        let d = edges
            .clone()
            .map(|(&a, &b)| distance_to_segment(p, a, b))
            .fold(f64::INFINITY, f64::min);
        if best.is_none_or(|(_, b)| d > b) {
            best = Some((p, d));
        }
    }
    best.map_or(first, |([x, y], _)| [py_round(x, DIGITS), py_round(y, DIGITS)])
}

fn point_of(p: &Value) -> Option<Point> {
    let p = p.as_array()?;
    Some([p.first()?.as_f64()?, p.get(1)?.as_f64()?])
}

fn points_of(v: Option<&Value>) -> Option<Vec<Point>> {
    v?.as_array()?.iter().map(point_of).collect()
}

/// Python's truth of a JSON value.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// The same ring of corners, perhaps starting at another one.
fn same_ring(a: &[Point], b: &[Point]) -> bool {
    a.len() == b.len() && (0..a.len()).any(|s| (0..a.len()).all(|i| a[(s + i) % a.len()] == b[i]))
}

fn is_room(z: &Value) -> bool {
    z.get("kind").and_then(Value::as_str) == Some("room")
}

/// The room zones for the walls, the other zones unchanged after them. A room keeps its id, name and
/// entry flag when its anchor lies in one of the new areas (the first room whose anchor is in it), and
/// its corners as stored if the area is the same; the rooms keep their order, new ones come last with
/// the name "Raum n" (the first n not taken) and the id null (the shell gives one: the core draws no
/// random numbers).
pub fn sync_rooms(zones: &[Value], walls: &[[Point; 2]]) -> Result<Vec<Value>, PlanError> {
    let polys = faces(walls);
    let (rooms, others): (Vec<&Value>, Vec<&Value>) = zones.iter().partition(|z| is_room(z));
    let mut matched: Vec<Option<&Value>> = vec![None; polys.len()];
    for &r in &rooms {
        // a room without an anchor (none, or empty) is gone
        let Some(a) = r.get("anchor").filter(|a| truthy(Some(a))).and_then(point_of) else {
            continue;
        };
        if let Some(k) = (0..polys.len()).find(|&k| matched[k].is_none() && in_polygon(a, &polys[k])) {
            matched[k] = Some(r);
        }
    }
    let text = |z: &Value, key: &str| z.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
    let mut names: Vec<String> = rooms.iter().map(|r| text(r, "name")).collect();
    let mut result: Vec<(usize, Map<String, Value>)> = Vec::new();
    for (poly, old) in polys.iter().zip(&matched) {
        let (id, name, entry, points) = match old {
            Some(r) => {
                let id = r.get("id").filter(|id| !id.is_null()).cloned();
                let id = id.ok_or_else(|| PlanError(format!("a room without an id: {r}")))?;
                let stored = points_of(r.get("points")).filter(|p| same_ring(p, poly));
                let points = stored.map_or_else(|| json!(poly), |_| r["points"].clone());
                (id, text(r, "name"), truthy(r.get("entry")), points)
            }
            None => {
                let name = (1..)
                    .map(|n| format!("Raum {n}"))
                    .find(|n| !names.contains(n))
                    .unwrap_or_default();
                names.push(name.clone());
                (Value::Null, name, false, json!(poly))
            }
        };
        let corners = points_of(Some(&points)).unwrap_or_default();
        // the previous order of the rooms, new ones at the end
        let position = rooms
            .iter()
            .rposition(|r| !id.is_null() && r.get("id") == Some(&id))
            .unwrap_or(rooms.len());
        let mut z = Map::new();
        z.insert("id".into(), id);
        z.insert("name".into(), name.into());
        z.insert("kind".into(), "room".into());
        z.insert("shape".into(), "polygon".into());
        z.insert("points".into(), points);
        z.insert("anchor".into(), json!(interior_point(&corners)));
        z.insert("entry".into(), entry.into());
        result.push((position, z));
    }
    result.sort_by_key(|&(position, _)| position);
    Ok(result
        .into_iter()
        .map(|(_, z)| Value::Object(z))
        .chain(others.into_iter().cloned())
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    const OUTER: [[Point; 2]; 4] = [
        [[0.0, 0.0], [6.0, 0.0]],
        [[6.0, 0.0], [6.0, 4.0]],
        [[6.0, 4.0], [0.0, 4.0]],
        [[0.0, 4.0], [0.0, 0.0]],
    ];

    fn area(poly: &[Point]) -> f64 {
        (signed_area(poly) * 1000.0).round() / 1000.0
    }

    fn areas(walls: &[[Point; 2]]) -> Vec<f64> {
        let mut a: Vec<f64> = faces(walls).iter().map(|p| area(p)).collect();
        a.sort_by(f64::total_cmp);
        a
    }

    #[test]
    fn walls_make_rooms() {
        let with = |extra: &[[Point; 2]]| [&OUTER[..], extra].concat();
        assert_eq!(areas(&with(&[[[3.0, 0.0], [3.0, 4.0]]])), [12.0, 12.0]);
        // a gap merges them, a wall just short of another still closes, a stub changes nothing
        assert_eq!(
            areas(&with(&[[[3.0, 0.0], [3.0, 1.5]], [[3.0, 2.5], [3.0, 4.0]]])),
            [24.0]
        );
        assert_eq!(areas(&with(&[[[3.0, 0.02], [3.0, 3.98]]])).len(), 2);
        assert_eq!(areas(&with(&[[[1.0, 1.0], [2.0, 1.0]]])), [24.0]);
        // crossing walls make four rooms, a diagonal two
        assert_eq!(
            areas(&with(&[[[3.0, 0.0], [3.0, 4.0]], [[0.0, 2.0], [6.0, 2.0]]])),
            [6.0; 4]
        );
        assert_eq!(areas(&with(&[[[2.0, 0.0], [4.0, 4.0]]])), [12.0, 12.0]);
        let anchor = interior_point(&faces(&OUTER)[0]);
        assert!(in_polygon(anchor, &faces(&OUTER)[0]) && (anchor[1] - 2.0).abs() < 0.3);
    }

    #[test]
    fn rooms_keep_their_names_ids_and_corners() {
        let walls = [&OUTER[..], &[[[3.0, 0.0], [3.0, 4.0]]]].concat();
        let mut zones = sync_rooms(&[], &walls).expect("rooms");
        assert!(zones.iter().all(|z| z["id"].is_null()));
        assert_eq!(zones[0]["name"], "Raum 1");
        for (k, z) in zones.iter_mut().enumerate() {
            z["id"] = json!(format!("r{k}"));
        }
        let area = json!({"id": "sofa", "kind": "area", "shape": "rect", "points": [[0.5, 0.5], [1, 1]]});
        zones.push(area.clone());
        // the same walls: the same zones (a stored room keeps its corners where it starts its ring)
        let ring = zones[0]["points"].as_array().expect("corners").clone();
        zones[0]["points"] = json!([&ring[1..], &ring[..1]].concat());
        assert_eq!(sync_rooms(&zones, &walls).expect("rooms"), zones);
        // a wall moved: the rooms keep their ids and names, the area stays last
        let moved = [&OUTER[..], &[[[3.6, 0.0], [3.6, 4.0]]]].concat();
        let after = sync_rooms(&zones, &moved).expect("rooms");
        assert_eq!(
            after.iter().map(|z| z["id"].clone()).collect::<Vec<_>>(),
            ["r0", "r1", "sofa"]
        );
        assert_eq!(after[2], area);
        // the wall removed: one room keeps its name; put back, a new room "Raum 2" next to it
        let one = sync_rooms(&after, &OUTER).expect("a room");
        assert_eq!(one.len(), 2);
        let again = sync_rooms(&one, &walls).expect("rooms");
        assert!(again.iter().any(|z| z["name"] == "Raum 2" && z["id"].is_null()));
    }
}
