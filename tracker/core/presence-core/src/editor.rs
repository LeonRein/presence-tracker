//! The geometry of the floor-plan editor (web UI): snapping, drawing walls and zones, dragging wall ends,
//! walls, doors, zone corners and sensors, splitting and joining walls, aligning background images. The UI
//! only takes the mouse: on release it sends the configuration and an operation, and stores the configuration
//! that comes back.
//!
//! The configuration is its stored form as JSON; every field an operation does not touch stays as it is. An
//! operation is `{"op": name, ...arguments, "tol": m, "free": bool}`: `tol` the snapping tolerance in metres
//! (the UI's 10 px), `free` no snapping (Shift held: only rounded to mm). A drag is computed from the
//! configuration at the press and the pointer where it was let go.
//!
//! Numbers and rules are those of the JavaScript editor of 0.46.0 (tools.js, util.js), without the snapping
//! guides it drew.

use std::collections::HashSet;
use std::f64::consts::{FRAC_PI_4, PI};

use serde_json::{Map, Value, json};

use crate::floorplan::SNAP;
use crate::geometry::{Point, distance_to_segment};
use crate::vmath::{py_dist, py_round};

/// m: wall ends this close are joined and move together
const LINK_EPS: f64 = 0.002;
/// m: a door this close to a dragged wall moves with it
const ON_WALL: f64 = 0.01;
/// m: a door belongs to the nearest real wall within this (floorplan.py)
const DOOR_REACH: f64 = 0.25;
/// m: the narrowest door the ends can be dragged to
const MIN_DOOR: f64 = 0.3;
/// m: a new door
const DOOR_WIDTH: f64 = 0.9;
/// m: a click this close to the previous point of a path is dropped
const DROP: f64 = 0.02;
/// m: a wall is not split this close to an end
const SPLIT_MARGIN: f64 = 0.05;
/// m: clicks this close to a straight line make one wall
const STRAIGHT: f64 = 0.005;
/// m: the smallest side of a new rectangle and radius of a new circle
const MIN_SIZE: f64 = 0.1;

/// What an operation gives: the new configuration, the snapped points of `snap_path`, and what the UI
/// selects afterwards (`{"kind", "id"}`; None: the selection stays).
#[derive(Debug, Clone, PartialEq)]
pub struct Applied {
    pub config: Value,
    pub points: Option<Vec<Point>>,
    pub select: Option<Value>,
}

/// Why an operation changes nothing; `code` is what the UI tells apart.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EditError {
    /// a wall index, zone, door, sensor or layer the configuration does not have
    NotFound(String),
    /// no real wall in reach for a door
    NoWall,
    /// a split this close to an end of the wall
    TooClose,
    /// no wall joined at the end to join
    NoneJoined,
    /// several walls, or one of another kind, joined at the end to join
    ManyJoined,
    /// a wall or zone that would be too small
    TooSmall,
    /// no two usable calibration points of a vacuum map
    NoCalibration,
    /// an unknown operation, a missing or ill-typed argument, an unreadable configuration
    BadOp(String),
}

impl EditError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::NotFound(_) => "not_found",
            Self::NoWall => "no_wall",
            Self::TooClose => "too_close",
            Self::NoneJoined => "none_joined",
            Self::ManyJoined => "many_joined",
            Self::TooSmall => "too_small",
            Self::NoCalibration => "no_calibration",
            Self::BadOp(_) => "bad_op",
        }
    }
}

impl std::fmt::Display for EditError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotFound(what) => write!(f, "{what} not found"),
            Self::NoWall => write!(f, "doors sit on a wall (not on a room boundary), and none is in reach"),
            Self::TooClose => write!(f, "too close to an end of the wall to split it"),
            Self::NoneJoined => write!(f, "no wall is joined at this end"),
            Self::ManyJoined => write!(f, "several walls meet here"),
            Self::TooSmall => write!(f, "too small"),
            Self::NoCalibration => write!(f, "the map has no usable calibration points"),
            Self::BadOp(why) => write!(f, "bad operation: {why}"),
        }
    }
}

impl std::error::Error for EditError {}

fn bad(why: impl Into<String>) -> EditError {
    EditError::BadOp(why.into())
}

// ------------------------------------------------------------------------------------------ small math

/// JavaScript's `+v.toFixed(3)` (up to exact binary ties, which go to even here), -0 as 0.
fn round3([x, y]: Point) -> Point {
    [py_round(x, 3) + 0.0, py_round(y, 3) + 0.0]
}

/// JavaScript's Math.round: the nearest integer, halves up (also below 0).
fn js_round(x: f64) -> f64 {
    let f = x.floor();
    if x - f >= 0.5 { f + 1.0 } else { f }
}

/// JavaScript's `+k.toPrecision(6)`.
fn precision6(k: f64) -> f64 {
    format!("{k:.5e}").parse().unwrap_or(k)
}

/// JavaScript's `x || 1` of a length: 0 (or NaN) as 1.
fn nonzero(x: f64) -> f64 {
    if x == 0.0 || x.is_nan() { 1.0 } else { x }
}

fn unit(a: Point, b: Point) -> Point {
    let len = nonzero(py_dist(a, b));
    [(b[0] - a[0]) / len, (b[1] - a[1]) / len]
}

/// p projected onto the line through a with the unit direction dir.
fn project_on_line(p: Point, a: Point, dir: Point) -> Point {
    let t = (p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1];
    [a[0] + t * dir[0], a[1] + t * dir[1]]
}

/// The crossing of the lines a + s·da and b + t·db, None if they are parallel.
fn intersect_lines(a: Point, da: Point, b: Point, db: Point) -> Option<Point> {
    let den = da[0] * db[1] - da[1] * db[0];
    if den.abs() < 1e-9 {
        return None;
    }
    let t = ((b[0] - a[0]) * db[1] - (b[1] - a[1]) * db[0]) / den;
    Some([a[0] + t * da[0], a[1] + t * da[1]])
}

fn point_of(v: &Value) -> Option<Point> {
    match v.as_array()?.as_slice() {
        [x, y] => Some([x.as_f64()?, y.as_f64()?]).filter(|p| p.iter().all(|c| c.is_finite())),
        _ => None,
    }
}

fn points_of(v: &Value) -> Option<Vec<Point>> {
    v.as_array()?.iter().map(point_of).collect()
}

fn pv([x, y]: Point) -> Value {
    json!([x, y])
}

// ---------------------------------------------------------------------------------------- the plan read

/// A wall of the stored form: one segment.
struct Wall {
    ends: [Point; 2],
    /// "wall" (blocks the radar, doors sit on it) or "divider"; "wall" if not given (config/check.rs)
    kind: String,
}

impl Wall {
    fn real(&self) -> bool {
        self.kind == "wall"
    }
}

struct Zone {
    id: Value,
    shape: String,
    /// rect: two corners, polygon: the corners, circle: none
    points: Vec<Point>,
}

impl Zone {
    /// The corners a point snaps to (zoneOutline without circles).
    fn outline(&self) -> Vec<Point> {
        match (self.shape.as_str(), self.points.as_slice()) {
            ("circle", _) => Vec::new(),
            ("rect", [[x1, y1], [x2, y2], ..]) => vec![[*x1, *y1], [*x2, *y1], [*x2, *y2], [*x1, *y2]],
            _ => self.points.clone(),
        }
    }
}

struct Door {
    id: Value,
    at: Point,
    width: Option<f64>,
}

/// The geometry of a configuration, read once per operation; the indices are those of its lists.
struct Plan {
    walls: Vec<Wall>,
    zones: Vec<Zone>,
    doors: Vec<Door>,
}

fn list<'v>(config: &'v Value, key: &str) -> Result<&'v [Value], EditError> {
    match config.get(key) {
        None | Some(Value::Null) => Ok(&[]),
        Some(v) => v
            .as_array()
            .map(Vec::as_slice)
            .ok_or_else(|| bad(format!("{key} is no list"))),
    }
}

impl Plan {
    fn read(config: &Value) -> Result<Plan, EditError> {
        if !config.is_object() {
            return Err(bad("the configuration is no object"));
        }
        let walls = list(config, "walls")?
            .iter()
            .enumerate()
            .map(|(k, w)| {
                let ends = w.get("points").and_then(points_of);
                let Some(&[a, b]) = ends.as_deref() else {
                    return Err(bad(format!("wall {k} has no two points")));
                };
                let kind = match w.get("kind") {
                    None | Some(Value::Null) => "wall",
                    Some(v) => v.as_str().ok_or_else(|| bad(format!("wall {k} has no kind")))?,
                };
                Ok(Wall {
                    ends: [a, b],
                    kind: kind.to_string(),
                })
            })
            .collect::<Result<_, _>>()?;
        let zones = list(config, "zones")?
            .iter()
            .enumerate()
            .map(|(k, z)| {
                let shape = z.get("shape").and_then(Value::as_str).unwrap_or("rect").to_string();
                let points = if shape == "circle" {
                    Vec::new()
                } else {
                    let pts = z.get("points").and_then(points_of);
                    pts.filter(|p| shape != "rect" || p.len() >= 2)
                        .ok_or_else(|| bad(format!("zone {k} has no points")))?
                };
                Ok(Zone {
                    id: z.get("id").cloned().unwrap_or(Value::Null),
                    shape,
                    points,
                })
            })
            .collect::<Result<_, _>>()?;
        let doors = list(config, "doors")?
            .iter()
            .enumerate()
            .map(|(k, d)| {
                let num = |key: &str| d.get(key).and_then(Value::as_f64).filter(|v| v.is_finite());
                let (Some(x), Some(y)) = (num("x"), num("y")) else {
                    return Err(bad(format!("door {k} has no position")));
                };
                Ok(Door {
                    id: d.get("id").cloned().unwrap_or(Value::Null),
                    at: [x, y],
                    width: num("width"),
                })
            })
            .collect::<Result<_, _>>()?;
        Ok(Plan { walls, zones, doors })
    }

    /// The corners a point snaps to: wall ends and zone corners, without those being moved.
    fn vertices(&self, ex: &Exclude) -> Vec<Point> {
        let mut out = Vec::new();
        for (i, w) in self.walls.iter().enumerate() {
            out.extend((0..2).filter(|&j| !ex.pairs.contains(&(i, j))).map(|j| w.ends[j]));
        }
        for z in &self.zones {
            if z.shape != "circle" && ex.zone != Some(&z.id) {
                out.extend(z.outline());
            }
        }
        out
    }

    /// The walls (all kinds) a point snaps onto, without those with a moved end.
    fn segments(&self, ex: &Exclude) -> Vec<[Point; 2]> {
        let moved = |i| ex.pairs.contains(&(i, 0)) || ex.pairs.contains(&(i, 1));
        self.walls
            .iter()
            .enumerate()
            .filter(|&(i, _)| !moved(i))
            .map(|(_, w)| w.ends)
            .collect()
    }

    /// Every wall end at p (joined ends): (wall, end), the wall `skip` left out.
    fn linked(&self, p: Point, skip: usize) -> Vec<(usize, usize)> {
        let mut out = Vec::new();
        for (i, w) in self.walls.iter().enumerate().filter(|&(i, _)| i != skip) {
            out.extend((0..2).filter(|&j| py_dist(p, w.ends[j]) < LINK_EPS).map(|j| (i, j)));
        }
        out
    }

    /// The real walls (no dividers) with a length.
    fn real_walls(&self) -> impl Iterator<Item = [Point; 2]> + '_ {
        self.walls.iter().filter(|w| w.real()).map(|w| w.ends)
    }

    /// The closest real wall within tol (nearestWall): t of the nearest point, clamped to the wall.
    fn nearest_wall(&self, p: Point, tol: f64) -> Option<OnWall> {
        let mut best: Option<(OnWall, f64)> = None;
        for [a, b] in self.real_walls() {
            let len = py_dist(a, b);
            if len < 1e-6 {
                continue;
            }
            let dir = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
            let t = ((p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1]).clamp(0.0, len);
            let d = py_dist(p, [a[0] + t * dir[0], a[1] + t * dir[1]]);
            if d < tol && best.as_ref().is_none_or(|&(_, bd)| d < bd) {
                best = Some((OnWall { a, dir, len, t }, d));
            }
        }
        best.map(|(w, _)| w)
    }

    /// The real wall a door at p sits on (doorPlacement): t of the door's centre, not clamped.
    fn door_placement(&self, p: Point) -> Option<OnWall> {
        let mut best: Option<([Point; 2], f64)> = None;
        for [a, b] in self.real_walls() {
            let d = distance_to_segment(p, a, b);
            // a wall without length has no direction (the configuration check refuses it)
            if d < DOOR_REACH && py_dist(a, b) > 0.0 && best.is_none_or(|(_, bd)| d < bd) {
                best = Some(([a, b], d));
            }
        }
        let [a, b] = best?.0;
        let len = py_dist(a, b);
        let dir = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
        Some(OnWall {
            a,
            dir,
            len,
            t: (p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1],
        })
    }

    fn zone(&self, id: &Value) -> Result<(usize, &Zone), EditError> {
        self.zones
            .iter()
            .enumerate()
            .find(|(_, z)| &z.id == id)
            .ok_or_else(|| EditError::NotFound(format!("zone {id}")))
    }

    fn door(&self, id: &Value) -> Result<(usize, &Door), EditError> {
        self.doors
            .iter()
            .enumerate()
            .find(|(_, d)| &d.id == id)
            .ok_or_else(|| EditError::NotFound(format!("door {id}")))
    }

    fn wall(&self, k: usize) -> Result<&Wall, EditError> {
        self.walls
            .get(k)
            .ok_or_else(|| EditError::NotFound(format!("wall {k}")))
    }
}

/// A place on a wall piece: from a along the unit direction dir by t, the piece len long.
#[derive(Debug, Clone, Copy)]
struct OnWall {
    a: Point,
    dir: Point,
    len: f64,
    t: f64,
}

impl OnWall {
    fn along(&self, t: f64) -> Point {
        round3([self.a[0] + self.dir[0] * t, self.a[1] + self.dir[1] * t])
    }

    /// The door centre at t, kept so that the whole door stays on the wall (clampDoor).
    fn clamp_door(&self, t: f64, width: f64) -> f64 {
        let half = (width / 2.0).min(self.len / 2.0);
        half.max(t.min(self.len - half))
    }
}

/// What snapping leaves out: the wall ends being moved (wall, end) and the zone being edited.
#[derive(Default)]
struct Exclude<'a> {
    pairs: HashSet<(usize, usize)>,
    zone: Option<&'a Value>,
}

struct Ray {
    a: Point,
    dir: Point,
    q: Point,
    d: f64,
}

struct Ctx<'a> {
    plan: &'a Plan,
    tol: Option<f64>,
    free: bool,
}

impl Ctx<'_> {
    fn tol(&self) -> Result<f64, EditError> {
        self.tol.ok_or_else(|| bad("tol is missing"))
    }

    /// A point snapped while drawing or dragging (free: only rounded). In this order:
    ///   1. an existing corner within tol (as it is, not rounded)
    ///   2. where a 45° ray from an anchor meets a wall within 1.5 tol (lines end exactly on walls)
    ///   3. the crossing of the first two rays within 1.5 tol (exact corners)
    ///   4. the nearest ray within tol
    ///   5. the first wall within tol
    fn snap(&self, p: Point, anchors: &[Point], ex: &Exclude) -> Result<Point, EditError> {
        if self.free {
            return Ok(round3(p));
        }
        let tol = self.tol()?;
        let mut best = None;
        let mut best_d = tol;
        for v in self.plan.vertices(ex) {
            let d = py_dist(v, p);
            if d < best_d {
                best = Some(v);
                best_d = d;
            }
        }
        if let Some(v) = best {
            return Ok(v);
        }

        let mut rays = Vec::new();
        for &a in anchors {
            if py_dist(a, p) < 1e-6 {
                continue;
            }
            let angle = js_round((p[1] - a[1]).atan2(p[0] - a[0]) / FRAC_PI_4) * FRAC_PI_4;
            let dir = [angle.cos(), angle.sin()];
            let q = project_on_line(p, a, dir);
            let d = py_dist(q, p);
            if d < tol {
                rays.push(Ray { a, dir, q, d });
            }
        }
        let segments = self.plan.segments(ex);
        let mut hit: Option<(Point, f64)> = None;
        for r in &rays {
            for &[a, b] in &segments {
                let len = py_dist(a, b);
                if len < 1e-6 {
                    continue;
                }
                let sd = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
                let Some(x) = intersect_lines(r.a, r.dir, a, sd) else {
                    continue;
                };
                let t = (x[0] - a[0]) * sd[0] + (x[1] - a[1]) * sd[1];
                let d = py_dist(x, p);
                if t >= -1e-6 && t <= len + 1e-6 && d < 1.5 * tol && hit.is_none_or(|(_, hd)| d < hd) {
                    hit = Some((x, d));
                }
            }
        }
        if let Some((x, _)) = hit {
            return Ok(round3(x));
        }
        if let [r0, r1, ..] = rays.as_slice()
            && let Some(x) = intersect_lines(r0.a, r0.dir, r1.a, r1.dir)
            && py_dist(x, p) < 1.5 * tol
        {
            return Ok(round3(x));
        }
        // the first of equally near ones, as JavaScript's stable sort
        if let Some(r) = rays.iter().min_by(|u, v| u.d.total_cmp(&v.d)) {
            return Ok(round3(r.q));
        }
        for [a, b] in segments {
            let len = py_dist(a, b);
            if len < 1e-6 {
                continue;
            }
            let dir = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
            let t = (p[0] - a[0]) * dir[0] + (p[1] - a[1]) * dir[1];
            if t < 0.0 || t > len {
                continue;
            }
            let q = [a[0] + t * dir[0], a[1] + t * dir[1]];
            if py_dist(q, p) < tol {
                return Ok(round3(q));
            }
        }
        Ok(round3(p))
    }

    /// The points of a path being drawn, raw clicks snapped one after another: 45° to the previous kept
    /// point and, closed with two or more, to the first; a click within DROP of the previous one is dropped.
    fn snap_path(&self, raw: &[Point], closed: bool) -> Result<Vec<Point>, EditError> {
        let mut pts: Vec<Point> = Vec::new();
        for &p in raw {
            let anchors: Vec<Point> = match pts.as_slice() {
                [] => Vec::new(),
                [first, .., last] if closed => vec![*last, *first],
                [.., last] => vec![*last],
            };
            let q = self.snap(p, &anchors, &Exclude::default())?;
            if pts.last().is_none_or(|&last| py_dist(last, q) > DROP) {
                pts.push(q);
            }
        }
        Ok(pts)
    }

    /// Edge i (points i, i+1) of a path shifted parallel to itself by the drag from-to; its ends slide along
    /// the neighbouring edges, so all angles stay (_moveEdge). The shift snaps so that the edge's line runs
    /// through a corner within tol, else to mm. The two moved points with their indices.
    fn move_edge(
        &self,
        orig: &[Point],
        i: usize,
        closed: bool,
        to: Point,
        from: Point,
        ex: &Exclude,
    ) -> Result<[(usize, Point); 2], EditError> {
        let n = orig.len();
        let j = (i + 1) % n;
        let a = orig[i];
        let dir = unit(a, orig[j]);
        let nrm = [-dir[1], dir[0]];
        let mut off = (to[0] - from[0]) * nrm[0] + (to[1] - from[1]) * nrm[1];
        if !self.free {
            let tol = self.tol()?;
            let mut best: Option<f64> = None;
            for v in self.plan.vertices(ex) {
                let ov = (v[0] - a[0]) * nrm[0] + (v[1] - a[1]) * nrm[1];
                if (ov - off).abs() < tol && best.is_none_or(|b| (ov - off).abs() < (b - off).abs()) {
                    best = Some(ov);
                }
            }
            off = best.unwrap_or_else(|| js_round(off * 1000.0) / 1000.0);
        }
        let a2 = [a[0] + off * nrm[0], a[1] + off * nrm[1]];
        let slide = |k: usize, other: Option<Point>| {
            let p = orig[k];
            let moved = [p[0] + off * nrm[0], p[1] + off * nrm[1]];
            let Some(other) = other else {
                return moved;
            };
            match intersect_lines(a2, dir, p, [other[0] - p[0], other[1] - p[1]]) {
                Some(x) if py_dist(x, moved) < 10.0 * off.abs().max(0.05) => x,
                _ => moved,
            }
        };
        let prev = if closed {
            Some(orig[(i + n - 1) % n])
        } else {
            i.checked_sub(1).map(|h| orig[h])
        };
        let next = if closed {
            Some(orig[(j + 1) % n])
        } else {
            orig.get(j + 1).copied()
        };
        Ok([(i, round3(slide(i, prev))), (j, round3(slide(j, next)))])
    }
}

// ------------------------------------------------------------------------------------ the output config

fn list_mut<'v>(config: &'v mut Value, key: &str) -> Result<&'v mut Vec<Value>, EditError> {
    let obj = config
        .as_object_mut()
        .ok_or_else(|| bad("the configuration is no object"))?;
    let v = obj.entry(key).or_insert_with(|| json!([]));
    if v.is_null() {
        *v = json!([]);
    }
    v.as_array_mut().ok_or_else(|| bad(format!("{key} is no list")))
}

fn item_mut<'v>(config: &'v mut Value, key: &str, k: usize) -> Result<&'v mut Map<String, Value>, EditError> {
    list_mut(config, key)?
        .get_mut(k)
        .and_then(Value::as_object_mut)
        .ok_or_else(|| bad(format!("{key} {k} is no object")))
}

fn points_mut<'v>(config: &'v mut Value, key: &str, k: usize) -> Result<&'v mut Vec<Value>, EditError> {
    item_mut(config, key, k)?
        .get_mut("points")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| bad(format!("{key} {k} has no points")))
}

fn set_point(config: &mut Value, key: &str, k: usize, j: usize, p: Point) -> Result<(), EditError> {
    let slot = points_mut(config, key, k)?
        .get_mut(j)
        .ok_or_else(|| bad(format!("{key} {k} has no point {j}")))?;
    *slot = pv(p);
    Ok(())
}

fn set_xy(item: &mut Map<String, Value>, [x, y]: Point) {
    item.insert("x".into(), json!(x));
    item.insert("y".into(), json!(y));
}

/// The background layer with this id: its index in background.layers.
fn layer_index(config: &Value, id: &Value) -> Result<usize, EditError> {
    config
        .get("background")
        .and_then(|b| b.get("layers"))
        .and_then(Value::as_array)
        .and_then(|layers| layers.iter().position(|l| l.get("id") == Some(id)))
        .ok_or_else(|| EditError::NotFound(format!("layer {id}")))
}

fn layer_mut(config: &mut Value, k: usize) -> Result<&mut Map<String, Value>, EditError> {
    config
        .get_mut("background")
        .and_then(|b| b.get_mut("layers"))
        .and_then(|l| l.get_mut(k))
        .and_then(Value::as_object_mut)
        .ok_or_else(|| bad("no layer"))
}

// ------------------------------------------------------------------------------------- the arguments

struct Args<'a>(&'a Map<String, Value>);

impl<'a> Args<'a> {
    fn get(&self, key: &str) -> Result<&'a Value, EditError> {
        self.0
            .get(key)
            .filter(|v| !v.is_null())
            .ok_or_else(|| bad(format!("{key} is missing")))
    }

    fn opt_number(&self, key: &str) -> Result<Option<f64>, EditError> {
        match self.0.get(key) {
            None | Some(Value::Null) => Ok(None),
            Some(v) => v
                .as_f64()
                .filter(|x| x.is_finite())
                .map(Some)
                .ok_or_else(|| bad(format!("{key} is no number"))),
        }
    }

    fn number(&self, key: &str) -> Result<f64, EditError> {
        self.opt_number(key)?.ok_or_else(|| bad(format!("{key} is missing")))
    }

    fn index(&self, key: &str) -> Result<usize, EditError> {
        self.get(key)?
            .as_u64()
            .and_then(|k| usize::try_from(k).ok())
            .ok_or_else(|| bad(format!("{key} is no index")))
    }

    /// An end of a wall or door: 0 or 1.
    fn end(&self, key: &str) -> Result<usize, EditError> {
        let end = self.index(key)?;
        if end > 1 {
            return Err(EditError::NotFound(format!("{key} {end}")));
        }
        Ok(end)
    }

    fn text(&self, key: &str) -> Result<&'a str, EditError> {
        self.get(key)?.as_str().ok_or_else(|| bad(format!("{key} is no text")))
    }

    fn flag(&self, key: &str) -> Result<bool, EditError> {
        match self.0.get(key) {
            None | Some(Value::Null) => Ok(false),
            Some(v) => v.as_bool().ok_or_else(|| bad(format!("{key} is no flag"))),
        }
    }

    fn point(&self, key: &str) -> Result<Point, EditError> {
        point_of(self.get(key)?).ok_or_else(|| bad(format!("{key} is no point")))
    }

    fn points(&self, key: &str) -> Result<Vec<Point>, EditError> {
        points_of(self.get(key)?).ok_or_else(|| bad(format!("{key} are no points")))
    }
}

// ------------------------------------------------------------------------------------- the operations

/// Applies an editor operation to a configuration (both JSON, see the module doc).
pub fn apply(config: &Value, op: &Value) -> Result<Applied, EditError> {
    let args = Args(op.as_object().ok_or_else(|| bad("the operation is no object"))?);
    let name = args.text("op")?;
    let tol = args.opt_number("tol")?;
    if tol.is_some_and(|t| t < 0.0) {
        return Err(bad("tol is negative"));
    }
    let plan = Plan::read(config)?;
    let ctx = Ctx {
        plan: &plan,
        tol,
        free: args.flag("free")?,
    };
    let mut out = config.clone();
    let mut points = None;
    let select = match name {
        "snap_path" => {
            points = Some(ctx.snap_path(&args.points("points")?, args.flag("closed")?)?);
            None
        }
        "add_walls" => add_walls(&ctx, &args, &mut out)?,
        "wall_end" => wall_end(&ctx, &args, &mut out)?,
        "wall_edge" => wall_edge(&ctx, &args, &mut out)?,
        "split_wall" => split_wall(&ctx, &args, &mut out)?,
        "join_wall" => join_wall(&ctx, &args, &mut out)?,
        "add_door" => add_door(&ctx, &args, &mut out)?,
        "move_door" => move_door(&ctx, &args, &mut out)?,
        "door_end" => door_end(&ctx, &args, &mut out)?,
        "zone_point" => zone_point(&ctx, &args, &mut out)?,
        "zone_edge" => zone_edge(&ctx, &args, &mut out)?,
        "rect" => rect(&ctx, &args, &mut out)?,
        "insert_point" => insert_point(&ctx, &args, &mut out)?,
        "add_zone" => add_zone(&ctx, &args, &mut out)?,
        "place_sensor" => place_sensor(&ctx, &args, &mut out)?,
        "move_sensor" => move_sensor(&ctx, &args, &mut out)?,
        "align_layer" => align_layer(&ctx, &args, &mut out)?,
        "fit_layer" => fit_layer(&args, &mut out)?,
        other => return Err(bad(format!("unknown operation {other}"))),
    };
    Ok(Applied {
        config: out,
        points,
        select,
    })
}

type Selected = Result<Option<Value>, EditError>;

/// A wall drawn by clicks: one wall per straight piece (clicks along a straight line don't split it).
fn add_walls(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let kind = match args.0.get("kind") {
        None | Some(Value::Null) => "wall",
        Some(_) => args.text("kind")?,
    };
    if kind != "wall" && kind != "divider" {
        return Err(bad(format!("unknown wall kind {kind}")));
    }
    let clicks = ctx.snap_path(&args.points("points")?, false)?;
    if clicks.len() < 2 {
        return Err(EditError::TooSmall);
    }
    let mut pts = vec![clicks[0]];
    for (k, &a) in clicks.iter().enumerate().skip(1) {
        let o = pts[pts.len() - 1];
        let straight = clicks.get(k + 1).is_some_and(|&b| {
            let off = ((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])).abs() / nonzero(py_dist(o, b));
            off < STRAIGHT && py_dist(o, a) + py_dist(a, b) < py_dist(o, b) + STRAIGHT
        });
        if !straight {
            pts.push(a);
        }
    }
    let walls = list_mut(out, "walls")?;
    walls.extend(
        pts.windows(2)
            .map(|w| json!({"points": [pv(w[0]), pv(w[1])], "kind": kind})),
    );
    Ok(None)
}

/// A drag of a wall's end(s) at the press: the ends joined to them move along, walls ending on the wall
/// (T-junctions) keep their direction and end on it again, doors on it keep their place relative to it.
struct WallDrag {
    wall: usize,
    orig: [Point; 2],
    /// per moved end, in the order of the ends given
    links: Vec<Link>,
    /// every end that moves (snapping leaves them out)
    pairs: HashSet<(usize, usize)>,
    /// T-junctions: (wall, end, far end, own end)
    attached: Vec<(usize, usize, Point, Point)>,
    /// doors on the wall: (index, fraction of the way from end 0)
    doors: Vec<(usize, f64)>,
}

/// The wall ends joined at a moved end of a dragged wall.
struct Link {
    end: usize,
    /// (wall, end)
    linked: Vec<(usize, usize)>,
    /// the other ends of those walls
    far: Vec<Point>,
}

impl WallDrag {
    fn start(plan: &Plan, wall: usize, ends: &[usize]) -> Self {
        let orig = plan.walls[wall].ends;
        let links: Vec<_> = ends
            .iter()
            .map(|&k| {
                let linked = plan.linked(orig[k], wall);
                let far = linked.iter().map(|&(wi, wj)| plan.walls[wi].ends[1 - wj]).collect();
                Link { end: k, linked, far }
            })
            .collect();
        let mut pairs: HashSet<_> = ends.iter().map(|&k| (wall, k)).collect();
        for link in &links {
            pairs.extend(link.linked.iter().copied());
        }
        let [a, b] = orig;
        let length2 = py_dist(a, b).powi(2);
        let mut attached = Vec::new();
        for (wi, w) in plan.walls.iter().enumerate() {
            for wj in 0..2 {
                let q = w.ends[wj];
                if wi == wall || pairs.contains(&(wi, wj)) {
                    continue;
                }
                let t = ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / length2;
                if t > 0.0 && t < 1.0 && distance_to_segment(q, a, b) < SNAP {
                    attached.push((wi, wj, w.ends[1 - wj], q));
                    pairs.insert((wi, wj));
                }
            }
        }
        let doors = (plan.doors.iter().enumerate())
            .filter(|(_, d)| distance_to_segment(d.at, a, b) < ON_WALL)
            .map(|(k, d)| (k, py_dist(d.at, a) / nonzero(py_dist(a, b))))
            .collect();
        WallDrag {
            wall,
            orig,
            links,
            pairs,
            attached,
            doors,
        }
    }

    fn exclude(&self) -> Exclude<'static> {
        Exclude {
            pairs: self.pairs.clone(),
            zone: None,
        }
    }

    /// The far ends of the walls joined at moved end number m (0: the first of `ends`).
    fn far(&self, m: usize) -> &[Point] {
        &self.links[m].far
    }

    /// Writes the moved ends (end, point) and everything that follows them.
    fn finish(&self, out: &mut Value, moved: &[(usize, Point)]) -> Result<(), EditError> {
        let mut ends = self.orig;
        for &(k, p) in moved {
            ends[k] = p;
            set_point(out, "walls", self.wall, k, p)?;
            for link in self.links.iter().filter(|l| l.end == k) {
                for &(wi, wj) in &link.linked {
                    set_point(out, "walls", wi, wj, p)?;
                }
            }
        }
        let [a, b] = ends;
        let dir = [b[0] - a[0], b[1] - a[1]];
        for &(wi, wj, far, own) in &self.attached {
            if let Some(x) = intersect_lines(far, [own[0] - far[0], own[1] - far[1]], a, dir) {
                set_point(out, "walls", wi, wj, round3(x))?;
            }
        }
        for &(k, t) in &self.doors {
            set_xy(
                item_mut(out, "doors", k)?,
                round3([a[0] + dir[0] * t, a[1] + dir[1] * t]),
            );
        }
        Ok(())
    }
}

/// An end of a wall dragged: 45° from its other end and from the far ends of the walls joined there.
fn wall_end(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let wall = args.index("wall")?;
    ctx.plan.wall(wall)?;
    let end = args.end("end")?;
    let drag = WallDrag::start(ctx.plan, wall, &[end]);
    let mut anchors = vec![drag.orig[1 - end]];
    anchors.extend_from_slice(drag.far(0));
    let p = ctx.snap(args.point("to")?, &anchors, &drag.exclude())?;
    drag.finish(out, &[(end, p)])?;
    Ok(None)
}

/// A wall dragged by its edge: shifted parallel; an end slides along the wall joined there if exactly one is.
fn wall_edge(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let wall = args.index("wall")?;
    ctx.plan.wall(wall)?;
    let (from, to) = (args.point("from")?, args.point("to")?);
    let drag = WallDrag::start(ctx.plan, wall, &[0, 1]);
    let one = |far: &[Point]| if let [p] = far { Some(*p) } else { None };
    let (prev, next) = (one(drag.far(0)), one(drag.far(1)));
    let line: Vec<Point> = prev.into_iter().chain(drag.orig).chain(next).collect();
    let k = usize::from(prev.is_some());
    let [(_, p0), (_, p1)] = ctx.move_edge(&line, k, false, to, from, &drag.exclude())?;
    drag.finish(out, &[(0, p0), (1, p1)])?;
    Ok(None)
}

/// A wall split in two where it was double-clicked; the new piece is appended.
fn split_wall(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let wall = args.index("wall")?;
    let [a, b] = ctx.plan.wall(wall)?.ends;
    let q = round3(project_on_line(args.point("at")?, a, unit(a, b)));
    if py_dist(q, a) < SPLIT_MARGIN || py_dist(q, b) < SPLIT_MARGIN {
        return Err(EditError::TooClose);
    }
    let item = item_mut(out, "walls", wall)?;
    let kind = item.get("kind").cloned();
    let pts = item
        .get_mut("points")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| bad("wall without points"))?;
    let b_stored = std::mem::replace(&mut pts[1], pv(q));
    let mut piece = Map::new();
    piece.insert("points".into(), json!([pv(q), b_stored]));
    if let Some(kind) = kind {
        piece.insert("kind".into(), kind);
    }
    list_mut(out, "walls")?.push(Value::Object(piece));
    Ok(None)
}

/// A wall joined with the one wall of the same kind joined at an end: it runs from its other end to that
/// wall's far end, and that wall is gone.
fn join_wall(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let wall = args.index("wall")?;
    let w = ctx.plan.wall(wall)?;
    let end = args.end("end")?;
    let (wi, wj) = match ctx.plan.linked(w.ends[end], wall).as_slice() {
        [] => return Err(EditError::NoneJoined),
        &[(wi, wj)] if ctx.plan.walls[wi].kind == w.kind => (wi, wj),
        _ => return Err(EditError::ManyJoined),
    };
    let far = points_mut(out, "walls", wi)?[1 - wj].clone();
    let pts = points_mut(out, "walls", wall)?;
    let keep = pts[1 - end].clone();
    *pts = vec![keep, far];
    list_mut(out, "walls")?.remove(wi);
    Ok(Some(
        json!({"kind": "wall", "id": if wi < wall { wall - 1 } else { wall }}),
    ))
}

/// A new door on the nearest real wall within tol, completely on it.
fn add_door(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let id = args.get("id")?.clone();
    let width = args.opt_number("width")?.unwrap_or(DOOR_WIDTH);
    if width <= 0.0 {
        return Err(bad("width is not positive"));
    }
    let w = ctx
        .plan
        .nearest_wall(args.point("at")?, ctx.tol()?)
        .ok_or(EditError::NoWall)?;
    let width = width.min(py_round(w.len, 3));
    let [x, y] = w.along(w.clamp_door(w.t, width));
    list_mut(out, "doors")?.push(json!({"id": id, "x": x, "y": y, "width": width}));
    Ok(Some(json!({"kind": "door", "id": id})))
}

/// A door dragged: along the nearest real wall within tol, else along its own wall, always completely on it.
fn move_door(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let id = args.get("door")?;
    let (k, door) = ctx.plan.door(id)?;
    let width = door.width.ok_or_else(|| bad("the door has no width"))?;
    let to = args.point("to")?;
    let w = ctx.plan.nearest_wall(to, ctx.tol()?).or_else(|| {
        let pl = ctx.plan.door_placement(door.at)?;
        let t = (to[0] - pl.a[0]) * pl.dir[0] + (to[1] - pl.a[1]) * pl.dir[1];
        Some(OnWall {
            t: t.min(pl.len).max(0.0),
            ..pl
        })
    });
    // away from all walls and on none: the door stays (as in the UI)
    if let Some(w) = w {
        let item = item_mut(out, "doors", k)?;
        set_xy(item, w.along(w.clamp_door(w.t, width)));
        let fits = py_round(w.len, 3);
        if fits < width {
            item.insert("width".into(), json!(fits));
        }
    }
    Ok(Some(json!({"kind": "door", "id": id})))
}

/// An end of a door dragged along its wall: the other end stays; 1 cm steps unless free, at least MIN_DOOR.
fn door_end(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let id = args.get("door")?;
    let (k, door) = ctx.plan.door(id)?;
    let end = args.end("end")?;
    let to = args.point("to")?;
    let width = door.width.ok_or_else(|| bad("the door has no width"))?;
    // on no wall: the door stays (as in the UI)
    let Some(pl) = ctx.plan.door_placement(door.at) else {
        return Ok(None);
    };
    let fixed = pl.t + (if end == 0 { 1.0 } else { -1.0 }) * width / 2.0;
    let mut t = ((to[0] - pl.a[0]) * pl.dir[0] + (to[1] - pl.a[1]) * pl.dir[1])
        .min(pl.len)
        .max(0.0);
    if !ctx.free {
        t = fixed + js_round((t - fixed) * 100.0) / 100.0;
    }
    if (t - fixed).abs() < MIN_DOOR {
        let s = t - fixed;
        t = fixed + (if s == 0.0 { -1.0 } else { s.signum() }) * MIN_DOOR;
    }
    let center = (t + fixed) / 2.0;
    let item = item_mut(out, "doors", k)?;
    item.insert("width".into(), json!(py_round((t - fixed).abs(), 3)));
    set_xy(item, pl.along(center));
    Ok(None)
}

/// The polygon zone of the arguments' `zone`: index and corners.
fn polygon<'p>(ctx: &'p Ctx, args: &Args) -> Result<(usize, &'p Zone), EditError> {
    let (k, z) = ctx.plan.zone(args.get("zone")?)?;
    if z.shape != "polygon" {
        return Err(bad("the zone is no polygon"));
    }
    Ok((k, z))
}

fn corner_index(args: &Args, key: &str, n: usize) -> Result<usize, EditError> {
    let i = args.index(key)?;
    if i >= n {
        return Err(EditError::NotFound(format!("{key} {i}")));
    }
    Ok(i)
}

/// A corner of a polygon zone dragged: 45° from its neighbours.
fn zone_point(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let (k, z) = polygon(ctx, args)?;
    let n = z.points.len();
    let i = corner_index(args, "index", n)?;
    let anchors = [z.points[(i + n - 1) % n], z.points[(i + 1) % n]];
    let ex = Exclude {
        zone: Some(&z.id),
        ..Exclude::default()
    };
    let p = ctx.snap(args.point("to")?, &anchors, &ex)?;
    set_point(out, "zones", k, i, p)?;
    Ok(None)
}

/// An edge of a polygon zone dragged: shifted parallel, its ends sliding along the neighbouring edges.
fn zone_edge(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let (k, z) = polygon(ctx, args)?;
    let i = corner_index(args, "edge", z.points.len())?;
    let ex = Exclude {
        zone: Some(&z.id),
        ..Exclude::default()
    };
    for (j, p) in ctx.move_edge(&z.points, i, true, args.point("to")?, args.point("from")?, &ex)? {
        set_point(out, "zones", k, j, p)?;
    }
    Ok(None)
}

/// A corner or side of a rectangle zone dragged. Corners: 0 (x1, y1), 1 (x2, y1), 2 (x2, y2), 3 (x1, y2);
/// sides: 0 y1, 1 x2, 2 y2, 3 x1.
fn rect(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let (k, z) = ctx.plan.zone(args.get("zone")?)?;
    if z.shape != "rect" {
        return Err(bad("the zone is no rectangle"));
    }
    let corner = match args.text("role")? {
        "corner" => true,
        "side" => false,
        other => return Err(bad(format!("unknown role {other}"))),
    };
    let i = corner_index(args, "index", 4)?;
    let ex = Exclude {
        zone: Some(&z.id),
        ..Exclude::default()
    };
    let p = ctx.snap(args.point("to")?, &[], &ex)?;
    let mut pts = [z.points[0], z.points[1]];
    if corner {
        pts[usize::from(i == 1 || i == 2)][0] = p[0];
        pts[usize::from(i >= 2)][1] = p[1];
    } else {
        match i {
            0 => pts[0][1] = p[1],
            1 => pts[1][0] = p[0],
            2 => pts[1][1] = p[1],
            _ => pts[0][0] = p[0],
        }
    }
    *points_mut(out, "zones", k)? = vec![pv(pts[0]), pv(pts[1])];
    Ok(None)
}

/// A corner inserted into a polygon zone's edge where it was double-clicked.
fn insert_point(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let (k, z) = polygon(ctx, args)?;
    let n = z.points.len();
    let i = corner_index(args, "edge", n)?;
    let (a, b) = (z.points[i], z.points[(i + 1) % n]);
    let q = round3(project_on_line(args.point("at")?, a, unit(a, b)));
    points_mut(out, "zones", k)?.insert(i + 1, pv(q));
    Ok(None)
}

/// A new zone drawn: a rectangle by two corners, a circle by centre and radius, a polygon by clicks.
fn add_zone(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let zone = Args(args.get("zone")?.as_object().ok_or_else(|| bad("zone is no object"))?);
    let id = zone.get("id")?.clone();
    let kind = zone.text("kind")?;
    let label = match kind {
        "area" => "Bereich",
        "entry" => "Eingang",
        other => return Err(bad(format!("unknown zone kind {other}"))),
    };
    let none = Exclude::default();
    let geometry = match zone.text("shape")? {
        "rect" => {
            let pts = zone.points("points")?;
            let &[a, b] = pts.as_slice() else {
                return Err(bad("a rectangle has two corners"));
            };
            let (a, b) = (ctx.snap(a, &[], &none)?, ctx.snap(b, &[], &none)?);
            if !((a[0] - b[0]).abs() > MIN_SIZE && (a[1] - b[1]).abs() > MIN_SIZE) {
                return Err(EditError::TooSmall);
            }
            json!({"shape": "rect", "points": [pv(a), pv(b)]})
        }
        "circle" => {
            let center = ctx.snap(zone.point("center")?, &[], &none)?;
            let radius = zone.number("radius")?;
            if !(radius > MIN_SIZE) {
                return Err(EditError::TooSmall);
            }
            json!({"shape": "circle", "points": [], "center": pv(center), "radius": py_round(radius, 3)})
        }
        "polygon" => {
            let pts = ctx.snap_path(&zone.points("points")?, true)?;
            if pts.len() < 3 {
                return Err(EditError::TooSmall);
            }
            json!({"shape": "polygon", "points": pts.into_iter().map(pv).collect::<Vec<_>>()})
        }
        other => return Err(bad(format!("unknown shape {other}"))),
    };
    let zones = list_mut(out, "zones")?;
    let count = zones
        .iter()
        .filter(|z| z.get("kind").and_then(Value::as_str) == Some(kind))
        .count()
        + 1;
    let mut new = json!({"id": id, "name": format!("{label} {count}"), "kind": kind});
    if let (Some(new), Value::Object(geometry)) = (new.as_object_mut(), geometry) {
        new.extend(geometry);
    }
    zones.push(new);
    Ok(Some(json!({"kind": "zone", "id": id})))
}

fn sensor_index(config: &Value, id: &Value) -> Result<Option<usize>, EditError> {
    Ok(list(config, "sensors")?.iter().position(|s| s.get("id") == Some(id)))
}

/// A sensor set onto the map; one not in the configuration yet is appended (the shell adds the defaults).
fn place_sensor(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let id = args.get("sensor")?;
    let p = round3(ctx.snap(args.point("to")?, &[], &Exclude::default())?);
    match sensor_index(out, id)? {
        Some(k) => {
            let item = item_mut(out, "sensors", k)?;
            set_xy(item, p);
            item.insert("placed".into(), json!(true));
        }
        None => {
            let name = match args.0.get("name") {
                None | Some(Value::Null) => {
                    let id = id.as_str().ok_or_else(|| bad("a new sensor needs a name"))?;
                    id.strip_prefix("presence-").unwrap_or(id)
                }
                Some(_) => args.text("name")?,
            };
            list_mut(out, "sensors")?.push(json!({
                "id": id, "name": name, "x": p[0], "y": p[1], "placed": true, "enabled": true,
            }));
        }
    }
    Ok(Some(json!({"kind": "sensor", "id": id})))
}

/// A sensor dragged.
fn move_sensor(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let id = args.get("sensor")?;
    let k = sensor_index(out, id)?.ok_or_else(|| EditError::NotFound(format!("sensor {id}")))?;
    let p = round3(ctx.snap(args.point("to")?, &[], &Exclude::default())?);
    set_xy(item_mut(out, "sensors", k)?, p);
    Ok(Some(json!({"kind": "sensor", "id": id})))
}

/// The world point of image pixel (u, v) under a layer's placement (view.js bgToWorld).
fn bg_to_world(x: f64, y: f64, scale: f64, rotation: f64, u: f64, v: f64) -> Point {
    let r = rotation * PI / 180.0;
    [
        x + scale * (u * r.cos() + v * r.sin()),
        y + scale * (u * r.sin() - v * r.cos()),
    ]
}

fn write_placement(out: &mut Value, k: usize, scale: f64, rotation: f64, [x, y]: Point) -> Result<(), EditError> {
    let layer = layer_mut(out, k)?;
    layer.insert("scale".into(), json!(scale));
    layer.insert("rotation".into(), json!(rotation));
    layer.insert("x".into(), json!(py_round(x, 4) + 0.0));
    layer.insert("y".into(), json!(py_round(y, 4) + 0.0));
    Ok(())
}

/// A background image aligned by two point pairs: clicks [a, A, b, B], a and b on the image, A and B where
/// they belong. Scales, turns and moves the image.
fn align_layer(ctx: &Ctx, args: &Args, out: &mut Value) -> Selected {
    let k = layer_index(out, args.get("layer")?)?;
    let layer = &list(out.get("background").unwrap_or(&Value::Null), "layers")?[k];
    let num = |key: &str| layer.get(key).and_then(Value::as_f64).filter(|v| v.is_finite());
    let (Some(lx), Some(ly), Some(lk)) = (num("x"), num("y"), num("scale")) else {
        return Err(bad("the layer has no placement"));
    };
    let lr = num("rotation").unwrap_or(0.0) * PI / 180.0;
    let clicks = args.points("clicks")?;
    let &[a, big_a, b, big_b] = clicks.as_slice() else {
        return Err(bad("an alignment takes four clicks"));
    };
    let none = Exclude::default();
    let [a, big_a, b, big_b] = [
        ctx.snap(a, &[], &none)?,
        ctx.snap(big_a, &[], &none)?,
        ctx.snap(b, &[], &none)?,
        ctx.snap(big_b, &[], &none)?,
    ];
    // image pixel coordinates of a and b under the current placement
    let to_px = |p: Point| {
        let (dx, dy) = (p[0] - lx, p[1] - ly);
        [
            (dx * lr.cos() + dy * lr.sin()) / lk,
            (dx * lr.sin() - dy * lr.cos()) / lk,
        ]
    };
    let (pa, pb) = (to_px(a), to_px(b));
    let k_new = py_dist(big_a, big_b) / py_dist(pa, pb);
    if !(k_new.is_finite() && k_new > 0.0) {
        return Err(EditError::TooSmall);
    }
    // the world angle of the image vector (u, -v) must match the world vector A->B
    let r = (big_b[1] - big_a[1]).atan2(big_b[0] - big_a[0]) - (-(pb[1] - pa[1])).atan2(pb[0] - pa[0]);
    let scale = precision6(k_new);
    let rotation = py_round(r * 180.0 / PI, 3) + 0.0;
    let [wx, wy] = bg_to_world(0.0, 0.0, scale, rotation, pa[0], pa[1]);
    write_placement(out, k, scale, rotation, [big_a[0] - wx, big_a[1] - wy])?;
    Ok(None)
}

/// A vacuum map placed by its calibration points ([{"vacuum": {x, y}, "map": {x, y}}], robot mm to image
/// pixels): the points of robot (0, 0) and (1000, 0) give scale, rotation and offset.
fn fit_layer(args: &Args, out: &mut Value) -> Selected {
    let k = layer_index(out, args.get("layer")?)?;
    let cal = args
        .0
        .get("calibration_points")
        .and_then(Value::as_array)
        .filter(|c| c.len() >= 2);
    let pixel = |vx: f64| -> Option<Point> {
        let c = cal?.iter().find(|c| {
            let v = |key: &str| c.get("vacuum").and_then(|v| v.get(key)).and_then(Value::as_f64);
            v("x") == Some(vx) && v("y") == Some(0.0)
        })?;
        let m = |key: &str| c.get("map")?.get(key)?.as_f64().filter(|v| v.is_finite());
        Some([m("x")?, m("y")?])
    };
    let (Some(o), Some(ax)) = (pixel(0.0), pixel(1000.0)) else {
        return Err(EditError::NoCalibration);
    };
    let (du, dv) = (ax[0] - o[0], ax[1] - o[1]);
    let k_new = 1.0 / py_dist(ax, o);
    if !k_new.is_finite() {
        return Err(EditError::NoCalibration);
    }
    // pixel direction of the robot's +x
    let r = dv.atan2(du);
    // world of pixel (0, 0): (x, y) = -Rot(r) * (o.x k, -o.y k)
    let (a, b) = (o[0] * k_new, -o[1] * k_new);
    let xy = [-(a * r.cos() - b * r.sin()), -(a * r.sin() + b * r.cos())];
    write_placement(out, k, precision6(k_new), py_round(r * 180.0 / PI, 3) + 0.0, xy)?;
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOL: f64 = 0.1;

    #[allow(clippy::needless_pass_by_value)] // the operations are built in place
    fn run(config: &Value, op: Value) -> Applied {
        apply(config, &op).unwrap_or_else(|e| panic!("{e}"))
    }

    #[allow(clippy::needless_pass_by_value)]
    fn code(config: &Value, op: Value) -> &'static str {
        apply(config, &op).unwrap_err().code()
    }

    fn pts(v: &Value) -> Vec<Point> {
        points_of(v).unwrap()
    }

    fn wall_pts(c: &Value, k: usize) -> Vec<Point> {
        pts(&c["walls"][k]["points"])
    }

    fn xy(v: &Value) -> Point {
        [v["x"].as_f64().unwrap(), v["y"].as_f64().unwrap()]
    }

    fn walls(list: &[[[f64; 2]; 2]]) -> Value {
        json!({"walls": list.iter().map(|w| json!({"points": w, "kind": "wall"})).collect::<Vec<_>>()})
    }

    #[allow(clippy::needless_pass_by_value)]
    fn snap_path(config: &Value, points: Value, closed: bool, free: bool) -> Vec<Point> {
        let op = json!({"op": "snap_path", "points": points, "closed": closed, "tol": TOL, "free": free});
        run(config, op).points.unwrap()
    }

    #[test]
    fn snapping() {
        let c = walls(&[[[0.0, 0.0], [0.0, 4.0]]]);
        // 1. corner: (0.05, 3.96) is 0.064 from the wall end (0, 4) < tol
        assert_eq!(snap_path(&c, json!([[0.05, 3.96]]), false, false), vec![[0.0, 4.0]]);
        // free (Shift): only rounded, the corner 0.014 away is not taken
        assert_eq!(
            snap_path(&c, json!([[0.01, 3.99], [0.12345, 1.98765]]), false, true),
            vec![[0.01, 3.99], [0.123, 1.988]]
        );
        // 2. the 45° ray: (2, 0) stays (2 m from everything); from it to (0.06, 1.97) the angle is 134.6° ->
        // 135°, the ray passes 0.021 from the pointer and meets the wall x = 0 at (0, 2), 0.067 < 1.5 tol away
        // (the point on the wall alone would be (0, 1.97), the ray alone (0.045, 1.955))
        assert_eq!(
            snap_path(&c, json!([[2, 0], [0.06, 1.97]]), false, false),
            vec![[2.0, 0.0], [0.0, 2.0]]
        );
        // 3. two rays (closed path, empty plan): the last point (0.04, 1.97) has the 180° ray from (2, 2)
        // (0.03 off) and the 90° ray from the first point (0, 0) (0.04 off); they cross at (0, 2)
        let path = snap_path(&json!({}), json!([[0, 0], [2, 0], [2, 2], [0.04, 1.97]]), true, false);
        assert_eq!(path, vec![[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]);
        // 5. onto a wall: (0.05, 1.0) projects to (0, 1) 0.05 away, no anchor
        assert_eq!(snap_path(&c, json!([[0.05, 1.0]]), false, false), vec![[0.0, 1.0]]);
        // a click within 0.02 of the previous point is dropped
        assert_eq!(
            snap_path(&json!({}), json!([[1, 1], [1.01, 1]]), false, true),
            vec![[1.0, 1.0]]
        );
    }

    #[test]
    fn add_walls_merges_straight_clicks() {
        // (0,0) (1,0) (2,0): (1,0) lies on the line to (2,0) (0 off, detour 0) and is no corner;
        // (2,0) is one: (2,1) is 2/sqrt(5) = 0.89 off the line (0,0)-(2,1)
        let op =
            json!({"op": "add_walls", "points": [[0, 0], [1, 0], [2, 0], [2, 1]], "kind": "divider", "free": true});
        let c = run(&json!({"walls": []}), op).config;
        assert_eq!(c["walls"].as_array().unwrap().len(), 2);
        assert_eq!(wall_pts(&c, 0), vec![[0.0, 0.0], [2.0, 0.0]]);
        assert_eq!(wall_pts(&c, 1), vec![[2.0, 0.0], [2.0, 1.0]]);
        assert_eq!(c["walls"][1]["kind"], "divider");
        // the second click 0.01 from the first is dropped: one point
        let op = json!({"op": "add_walls", "points": [[0, 0], [0.01, 0]], "kind": "wall", "free": true});
        assert_eq!(code(&json!({}), op), "too_small");
        assert_eq!(
            code(
                &json!({}),
                json!({"op": "add_walls", "points": [[0, 0], [1, 0]], "kind": "glass"})
            ),
            "bad_op"
        );
    }

    #[test]
    fn wall_end_drags_joined_and_t_walls_and_doors() {
        // wall 0 (0,0)-(4,0); wall 1 joined at (4,0); wall 2 ends on wall 0 at its middle (T); a door at (1,0)
        let mut c = walls(&[
            [[0.0, 0.0], [4.0, 0.0]],
            [[4.0, 0.0], [4.0, 3.0]],
            [[2.0, 2.0], [2.0, 0.0]],
        ]);
        c["doors"] = json!([{"id": "d", "x": 1, "y": 0, "width": 0.8}]);
        c["sensors"] = json!([{"id": "s", "x": 9, "y": 9}]);
        // end 1 to (4, 1): no corner within tol ((4,3) is the nearest, 2 m); the ray from the far end (4,3) of
        // wall 1 points straight down (-90°) and passes (4, 1) exactly; the 0° ray from (0,0) is 1 m off
        let op = json!({"op": "wall_end", "wall": 0, "end": 1, "to": [4.0, 1.0], "tol": TOL});
        let out = run(&c, op).config;
        assert_eq!(wall_pts(&out, 0), vec![[0.0, 0.0], [4.0, 1.0]]);
        assert_eq!(wall_pts(&out, 1), vec![[4.0, 1.0], [4.0, 3.0]]);
        // the T-wall keeps its direction (2,2)->(2,0) and ends on the line (0,0)+s(4,1): x = 2, y = 0.5
        assert_eq!(wall_pts(&out, 2), vec![[2.0, 2.0], [2.0, 0.5]]);
        // the door was 1/4 of the way: (0,0) + 0.25 (4,1)
        assert_eq!(xy(&out["doors"][0]), [1.0, 0.25]);
        assert_eq!(out["doors"][0]["width"], json!(0.8));
        assert_eq!(out["sensors"], c["sensors"]);
        assert_eq!(
            code(
                &c,
                json!({"op": "wall_end", "wall": 3, "end": 0, "to": [0, 0], "tol": TOL})
            ),
            "not_found"
        );
    }

    #[test]
    fn wall_edge_slides_along_joined_walls() {
        // wall 0 (0,0)-(4,0) between wall 1 (up the y axis) and wall 2 (slanted, direction (2,4)); a door in
        // the middle
        let mut c = walls(&[
            [[0.0, 0.0], [4.0, 0.0]],
            [[0.0, 0.0], [0.0, 3.0]],
            [[4.0, 0.0], [6.0, 4.0]],
        ]);
        c["doors"] = json!([{"id": "d", "x": 2, "y": 0, "width": 0.9}]);
        // dragged by 1 m along the normal (0,1): the corners (0,3) and (6,4) are 2 and 3 m off, so off = 1;
        // end 0 slides up wall 1 to (0,1); end 1 slides along wall 2: (4,0) + s(2,4) at y = 1 is (4.5, 1)
        let op = json!({"op": "wall_edge", "wall": 0, "from": [2, 0], "to": [2.03, 1.0], "tol": TOL});
        let out = run(&c, op).config;
        assert_eq!(wall_pts(&out, 0), vec![[0.0, 1.0], [4.5, 1.0]]);
        assert_eq!(wall_pts(&out, 1), vec![[0.0, 1.0], [0.0, 3.0]]);
        assert_eq!(wall_pts(&out, 2), vec![[4.5, 1.0], [6.0, 4.0]]);
        // the door stays half way: (0,1) + 0.5 (4.5, 0)
        assert_eq!(xy(&out["doors"][0]), [2.25, 1.0]);
        // the shift snaps to a corner's line: (9, 1.05) with the edge at 1.03 -> 1.05
        c["walls"]
            .as_array_mut()
            .unwrap()
            .push(json!({"points": [[9, 1.05], [9, 5]], "kind": "wall"}));
        let op = json!({"op": "wall_edge", "wall": 0, "from": [2, 0], "to": [2, 1.03], "tol": TOL});
        assert_eq!(wall_pts(&run(&c, op).config, 1), vec![[0.0, 1.05], [0.0, 3.0]]);
    }

    #[test]
    fn split_and_join() {
        let c = walls(&[
            [[0.0, 0.0], [4.0, 0.0]],
            [[4.0, 0.0], [4.0, 3.0]],
            [[4.0, 0.0], [6.0, 0.0]],
        ]);
        // (1, 0.3) projects to (1, 0): wall 0 to (0,0)-(1,0), the rest appended
        let out = run(&c, json!({"op": "split_wall", "wall": 0, "at": [1.0, 0.3]})).config;
        assert_eq!(wall_pts(&out, 0), vec![[0.0, 0.0], [1.0, 0.0]]);
        assert_eq!(wall_pts(&out, 3), vec![[1.0, 0.0], [4.0, 0.0]]);
        assert_eq!(out["walls"][3]["kind"], "wall");
        // 0.03 from end 0
        assert_eq!(
            code(&c, json!({"op": "split_wall", "wall": 0, "at": [0.03, 0.2]})),
            "too_close"
        );
        assert_eq!(code(&c, json!({"op": "join_wall", "wall": 0, "end": 0})), "none_joined");
        // walls 1 and 2 both end at (4,0)
        assert_eq!(code(&c, json!({"op": "join_wall", "wall": 0, "end": 1})), "many_joined");
        // one joined wall: wall 1 runs from its far end (4,0) to wall 0's far end (0,0), wall 0 is gone, and
        // wall 1 is index 0 now
        let c = walls(&[[[0.0, 0.0], [2.0, 0.0]], [[2.0, 0.0], [4.0, 0.0]]]);
        let applied = run(&c, json!({"op": "join_wall", "wall": 1, "end": 0}));
        assert_eq!(applied.config["walls"].as_array().unwrap().len(), 1);
        assert_eq!(wall_pts(&applied.config, 0), vec![[4.0, 0.0], [0.0, 0.0]]);
        assert_eq!(applied.select, Some(json!({"kind": "wall", "id": 0})));
        // one of another kind
        let mut c = c;
        c["walls"][0]["kind"] = json!("divider");
        assert_eq!(code(&c, json!({"op": "join_wall", "wall": 1, "end": 0})), "many_joined");
    }

    #[test]
    fn doors() {
        let mut c = walls(&[[[0.0, 0.0], [4.0, 0.0]], [[10.0, 0.0], [10.6, 0.0]]]);
        c["walls"]
            .as_array_mut()
            .unwrap()
            .push(json!({"points": [[0, 5], [4, 5]], "kind": "divider"}));
        let add = |at: Value| json!({"op": "add_door", "id": "d", "at": at, "tol": 0.2});
        // at t = 1 of the 4 m wall (0.1 off): width 0.9, centre 1 is in [0.45, 3.55]
        let applied = run(&c, add(json!([1.0, 0.1])));
        assert_eq!(
            applied.config["doors"],
            json!([{"id": "d", "x": 1.0, "y": 0.0, "width": 0.9}])
        );
        assert_eq!(applied.select, Some(json!({"kind": "door", "id": "d"})));
        // clamped: t = 0.2 -> 0.45 (half the width from the end)
        assert_eq!(xy(&run(&c, add(json!([0.2, 0.05]))).config["doors"][0]), [0.45, 0.0]);
        // a 0.6 m wall: width 0.6, centre in the middle
        let door = &run(&c, add(json!([10.1, 0.0]))).config["doors"][0];
        assert_eq!((xy(door), door["width"].as_f64()), ([10.3, 0.0], Some(0.6)));
        // 0.5 m off, and on a divider
        assert_eq!(code(&c, add(json!([1.0, 0.5]))), "no_wall");
        assert_eq!(code(&c, add(json!([1.0, 5.0]))), "no_wall");

        c["doors"] = json!([{"id": "d", "x": 1, "y": 0, "width": 0.9}]);
        let drag = |to: Value| json!({"op": "move_door", "door": "d", "to": to, "tol": 0.2});
        // to the wall's end: t = 3.9 -> 4 - 0.45
        assert_eq!(xy(&run(&c, drag(json!([3.9, 0.05]))).config["doors"][0]), [3.55, 0.0]);
        // far from every wall: along its own wall, t = 3
        assert_eq!(xy(&run(&c, drag(json!([3.0, 2.0]))).config["doors"][0]), [3.0, 0.0]);
        // onto the short wall: there t = 0.3, the width shrinks to 0.6
        let door = &run(&c, drag(json!([10.3, 0.1]))).config["doors"][0];
        assert_eq!((xy(door), door["width"].as_f64()), ([10.3, 0.0], Some(0.6)));
        assert_eq!(
            code(&c, json!({"op": "move_door", "door": "x", "to": [0, 0], "tol": 0.2})),
            "not_found"
        );
    }

    #[test]
    fn door_ends() {
        let mut c = walls(&[[[0.0, 0.0], [4.0, 0.0]]]);
        c["doors"] = json!([{"id": "d", "x": 2, "y": 0, "width": 0.9}]);
        let end = |e: usize, to: Value| json!({"op": "door_end", "door": "d", "end": e, "to": to});
        // end 1 dragged to t = 2.55 (0.4 off the wall): end 0 stays at 2 - 0.45 = 1.55; width 1, centre 2.05
        let door = &run(&c, end(1, json!([2.55, 0.4]))).config["doors"][0];
        assert_eq!((xy(door), door["width"].as_f64()), ([2.05, 0.0], Some(1.0)));
        // to t = 1.6: only 0.05 from the other end -> at least 0.3: t = 1.85, centre 1.7
        let door = &run(&c, end(1, json!([1.6, 0.0]))).config["doors"][0];
        assert_eq!((xy(door), door["width"].as_f64()), ([1.7, 0.0], Some(0.3)));
        // end 0 to t = 1.234: in 1 cm steps from the fixed end 2.45: 1.23 -> width 1.22, centre 1.84
        let door = &run(&c, end(0, json!([1.234, 0.0]))).config["doors"][0];
        assert_eq!((xy(door), door["width"].as_f64()), ([1.84, 0.0], Some(1.22)));
    }

    #[test]
    fn zones() {
        let square = json!({"id": "p", "kind": "area", "shape": "polygon", "points": [[0, 0], [2, 0], [2, 2], [0, 2]]});
        let c = json!({"zones": [square, {"id": "r", "kind": "area", "shape": "rect", "points": [[0, 0], [2, 1]]}],
                       "walls": [{"points": [[3, -1], [3, 5]], "kind": "wall"}]});
        // corner 2 to (2.03, 2.5): the 90° ray from its neighbour (2,0) passes 0.03 off -> (2, 2.5)
        let out = run(
            &c,
            json!({"op": "zone_point", "zone": "p", "index": 2, "to": [2.03, 2.5], "tol": TOL}),
        )
        .config;
        assert_eq!(
            pts(&out["zones"][0]["points"]),
            vec![[0.0, 0.0], [2.0, 0.0], [2.0, 2.5], [0.0, 2.0]]
        );
        // edge 1 ((2,0)-(2,2), normal (-1,0)) dragged 0.5 to the right: off = -0.5; its ends slide along
        // the edges to (0,0) and (0,2): (2.5, 0) and (2.5, 2)
        let op = json!({"op": "zone_edge", "zone": "p", "edge": 1, "from": [2, 1], "to": [2.5, 1.2], "tol": TOL});
        let out = run(&c, op).config;
        assert_eq!(
            pts(&out["zones"][0]["points"]),
            vec![[0.0, 0.0], [2.5, 0.0], [2.5, 2.0], [0.0, 2.0]]
        );
        // a double click at (0.7, 0.3) on edge 0: (0.7, 0) after corner 0
        let out = run(
            &c,
            json!({"op": "insert_point", "zone": "p", "edge": 0, "at": [0.7, 0.3]}),
        )
        .config;
        assert_eq!(pts(&out["zones"][0]["points"]).len(), 5);
        assert_eq!(pts(&out["zones"][0]["points"])[1], [0.7, 0.0]);
        // rect side 1 (x2) to (3.04, 0.5): onto the wall x = 3 (0.04 off) -> x2 = 3
        let op = json!({"op": "rect", "zone": "r", "role": "side", "index": 1, "to": [3.04, 0.5], "tol": TOL});
        assert_eq!(
            pts(&run(&c, op).config["zones"][1]["points"]),
            vec![[0.0, 0.0], [3.0, 1.0]]
        );
        // corner 3 (x1, y2) freely to (-1, 1.5)
        let op = json!({"op": "rect", "zone": "r", "role": "corner", "index": 3, "to": [-1, 1.5], "free": true});
        assert_eq!(
            pts(&run(&c, op).config["zones"][1]["points"]),
            vec![[-1.0, 0.0], [2.0, 1.5]]
        );
        assert_eq!(
            code(
                &c,
                json!({"op": "zone_point", "zone": "q", "index": 0, "to": [0, 0], "tol": TOL})
            ),
            "not_found"
        );
        assert_eq!(
            code(
                &c,
                json!({"op": "zone_point", "zone": "p", "index": 4, "to": [0, 0], "tol": TOL})
            ),
            "not_found"
        );
        assert_eq!(
            code(
                &c,
                json!({"op": "zone_point", "zone": "r", "index": 0, "to": [0, 0], "tol": TOL})
            ),
            "bad_op"
        );
    }

    #[test]
    fn add_zones() {
        let c = json!({"zones": [{"id": "a", "kind": "area", "shape": "circle", "center": [9, 9], "radius": 1}]});
        let add = |zone: Value| json!({"op": "add_zone", "zone": zone, "tol": TOL, "free": true});
        // a second area: "Bereich 2"
        let applied = run(
            &c,
            add(json!({"id": "z", "kind": "area", "shape": "rect", "points": [[0, 0], [1.2346, 1]]})),
        );
        let z = &applied.config["zones"][1];
        assert_eq!(
            (z["name"].as_str(), pts(&z["points"])),
            (Some("Bereich 2"), vec![[0.0, 0.0], [1.235, 1.0]])
        );
        assert_eq!(applied.select, Some(json!({"kind": "zone", "id": "z"})));
        // the first entry: "Eingang 1"; a circle keeps its radius (rounded)
        let z = &run(
            &c,
            add(json!({"id": "e", "kind": "entry", "shape": "circle", "center": [1, 1], "radius": 0.5004})),
        )
        .config["zones"][1];
        assert_eq!(
            (z["name"].as_str(), z["radius"].as_f64(), z["shape"].as_str()),
            (Some("Eingang 1"), Some(0.5), Some("circle"))
        );
        // too small: a side of 0.05, a radius of 0.05, two corners
        assert_eq!(
            code(
                &c,
                add(json!({"id": "z", "kind": "area", "shape": "rect", "points": [[0, 0], [0.05, 1]]}))
            ),
            "too_small"
        );
        assert_eq!(
            code(
                &c,
                add(json!({"id": "z", "kind": "area", "shape": "circle", "center": [0, 0], "radius": 0.05}))
            ),
            "too_small"
        );
        assert_eq!(
            code(
                &c,
                add(json!({"id": "z", "kind": "area", "shape": "polygon", "points": [[0, 0], [1, 0]]}))
            ),
            "too_small"
        );
        let applied = run(
            &c,
            add(json!({"id": "z", "kind": "area", "shape": "polygon", "points": [[0, 0], [1, 0], [1, 1]]})),
        );
        assert_eq!(pts(&applied.config["zones"][1]["points"]).len(), 3);
    }

    #[test]
    fn sensors() {
        let c = json!({"sensors": [{"id": "presence-a", "name": "A", "x": 0, "y": 0, "heading": 45, "placed": false}],
                       "walls": [{"points": [[0, 0], [0, 4]], "kind": "wall"}]});
        // a new one: appended, the name from its id; (0.05, 3.96) snaps to the wall end (0, 4)
        let applied = run(
            &c,
            json!({"op": "place_sensor", "sensor": "presence-kueche", "to": [0.05, 3.96], "tol": TOL}),
        );
        assert_eq!(
            applied.config["sensors"][1],
            json!({"id": "presence-kueche", "name": "kueche", "x": 0.0, "y": 4.0, "placed": true, "enabled": true})
        );
        assert_eq!(applied.select, Some(json!({"kind": "sensor", "id": "presence-kueche"})));
        // an existing one: placed, everything else kept
        let s = &run(
            &c,
            json!({"op": "place_sensor", "sensor": "presence-a", "to": [1.23456, 2], "tol": TOL}),
        )
        .config["sensors"][0];
        assert_eq!(
            (xy(s), &s["placed"], &s["heading"], &s["name"]),
            ([1.235, 2.0], &json!(true), &json!(45), &json!("A"))
        );
        let s = &run(
            &c,
            json!({"op": "move_sensor", "sensor": "presence-a", "to": [2, 1], "tol": TOL}),
        )
        .config["sensors"][0];
        assert_eq!((xy(s), &s["placed"]), ([2.0, 1.0], &json!(false)));
        assert_eq!(
            code(
                &c,
                json!({"op": "move_sensor", "sensor": "b", "to": [2, 1], "tol": TOL})
            ),
            "not_found"
        );
    }

    #[test]
    fn align_layer_by_two_points() {
        let c =
            json!({"background": {"layers": [{"id": "l", "x": 0, "y": 0, "scale": 1, "rotation": 0, "opacity": 0.7}]}});
        // the image as placed: pixel (u, v) at world (u, -v). Image points a = (1, 0) and b = (2, 0) are pixels
        // (1, 0) and (2, 0), 1 px apart; they belong at A = (5, 5) and B = (5, 7), 2 m apart: scale 2. The
        // image vector (1, -0) has angle 0, A->B 90°: rotation 90. Pixel (1, 0) then lies at
        // (0 + 2 cos 90°, 0 + 2 sin 90°) = (0, 2) from the origin, so the origin is A - (0, 2) = (5, 3).
        let op = json!({"op": "align_layer", "layer": "l", "clicks": [[1, 0], [5, 5], [2, 0], [5, 7]], "tol": TOL});
        let l = &run(&c, op).config["background"]["layers"][0];
        assert_eq!(
            (l["scale"].as_f64(), l["rotation"].as_f64(), xy(l)),
            (Some(2.0), Some(90.0), [5.0, 3.0])
        );
        assert_eq!(l["opacity"], json!(0.7));
        assert_eq!(
            code(
                &c,
                json!({"op": "align_layer", "layer": "m", "clicks": [[1, 0], [5, 5], [2, 0], [5, 7]], "tol": TOL})
            ),
            "not_found"
        );
    }

    #[test]
    fn fit_layer_by_calibration() {
        let c = json!({"background": {"layers": [{"id": "l", "x": 0, "y": 0, "scale": 1, "rotation": 0}]}});
        // robot (0,0) at pixel (100, 200), robot (1000 mm, 0) at pixel (100, 180): 20 px per metre, k = 0.05;
        // the robot's +x runs along pixel direction (0, -20): r = -90°. a = 100 k = 5, b = -200 k = -10:
        // x = -(5 cos r + 10 sin r) = 10, y = -(5 sin r - 10 cos r) = 5. Check: pixel (100, 200) ->
        // (10 + 0.05 (200 sin r), 5 + 0.05 (100 sin r)) = (0, 0).
        let cal = json!([{"vacuum": {"x": 0, "y": 0}, "map": {"x": 100, "y": 200}},
                         {"vacuum": {"x": 1000, "y": 0}, "map": {"x": 100, "y": 180}}]);
        let l = &run(&c, json!({"op": "fit_layer", "layer": "l", "calibration_points": cal})).config["background"]["layers"]
            [0];
        assert_eq!(
            (l["scale"].as_f64(), l["rotation"].as_f64(), xy(l)),
            (Some(0.05), Some(-90.0), [10.0, 5.0])
        );
        let one = json!([{"vacuum": {"x": 0, "y": 0}, "map": {"x": 100, "y": 200}}]);
        assert_eq!(
            code(&c, json!({"op": "fit_layer", "layer": "l", "calibration_points": one})),
            "no_calibration"
        );
    }

    #[test]
    fn bad_operations() {
        assert_eq!(code(&json!({}), json!({"op": "fly"})), "bad_op");
        assert_eq!(code(&json!({}), json!({"points": []})), "bad_op");
        assert_eq!(
            code(&json!({}), json!({"op": "snap_path", "points": [[0, "x"]], "tol": TOL})),
            "bad_op"
        );
        // snapping without a tolerance
        assert_eq!(
            code(&json!({}), json!({"op": "snap_path", "points": [[0, 0]]})),
            "bad_op"
        );
        assert_eq!(
            code(
                &json!({"walls": [{"points": [[0, 0]]}]}),
                json!({"op": "snap_path", "points": []})
            ),
            "bad_op"
        );
    }
}
