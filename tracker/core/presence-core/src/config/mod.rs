//! The configuration as the app stores it (tracker.json in its canonical form: sensors, zones, walls, doors, the
//! LD2410Cs' measured view and the tracker's parameters; the background image and the rooms' label points are
//! the web UI's and never come here), read with serde. Lengths in metres, angles in degrees, in the house frame.
//! The map comes finished: the rooms are the faces between the walls and every door lies on a wall (the web
//! UI's editor makes them so, static/js/floorplan.js; the check refuses a door on no wall). Every field the
//! shell always writes is required: no serde defaults that could hide a renamed field. The one form a
//! configuration is stored in is the core's (`canonical`: checked, what it leaves out filled in from
//! `defaults`, the one table of the values unless set, which the web UI's editor also gives every new element);
//! the shell stores exactly that and hands it back. Before it is read it is checked
//! (check.rs: the limits of every number, also the ones of the web UI's fields). The sensors' geometry here
//! is the only one: the simulation of the tests uses it too (presence_core.Geometry, feature "testing").
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

mod check;

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub use check::{
    ConfigError, DOOR, Limits, MAX_CELLS, MAX_RANGE, MAX_SENSORS, PARAMS, Part, Problem, SENSOR, TARGET_THRESHOLD,
    check,
};

use crate::formats::Fingerprint;
use crate::geometry::{Point, Shape, line_of_sight};
use crate::vmath::py_hypot;

/// m: sight lines start this far in front of a sensor: it hangs on a wall, often
/// in a corner, and a few centimetres of drawing error would otherwise put it behind that wall.
pub const SIGHT_OFFSET: f64 = 0.2;

#[derive(Debug, Deserialize)]
pub struct Config {
    pub sensors: Vec<SensorConfig>,
    pub zones: Vec<ZoneConfig>,
    pub walls: Vec<WallConfig>,
    pub doors: Vec<DoorConfig>,
    /// per "own<-room" (room ids) how much of what the geometric sight expects an LD2410C in its own
    /// room gets from a person in the other one (measured, MODEL.md 4.3 "Blick des LD2410C"); missing: 1
    pub ld_view: BTreeMap<String, f64>,
    pub params: TrackerParams,
}

/// A sensor: where it hangs, where it looks, how its measurements are read.
#[derive(Debug, Deserialize, Serialize)]
pub struct SensorConfig {
    pub id: String,
    pub name: String,
    pub x: f64,
    pub y: f64,
    /// degrees from the +x axis, counterclockwise
    pub heading: f64,
    pub height: f64,
    pub mirror: bool,
    pub scale: f64,
    pub fov: f64,
    pub range: f64,
    pub enabled: bool,
    pub placed: bool,
}

impl SensorConfig {
    /// (cos, sin) of the heading.
    pub fn axis(&self) -> Point {
        let h = self.heading.to_radians();
        [h.cos(), h.sin()]
    }

    /// Enabled and put on the map: its frames count.
    pub fn in_use(&self) -> bool {
        self.enabled && self.placed
    }

    pub fn at(&self) -> Point {
        [self.x, self.y]
    }

    /// How it hangs: what turns its targets into floor positions and back.
    pub fn mount(&self) -> Mount {
        Mount {
            at: self.at(),
            axis: self.axis(),
            height: self.height,
            mirror: self.mirror,
            scale: self.scale,
        }
    }

    /// House frame -> the sensor's frame on the floor (Mount::to_local).
    pub fn to_local(&self, p: Point) -> Point {
        self.mount().to_local(p)
    }

    /// Within its field of view and range, grown by margin.
    pub fn in_fov(&self, p: Point, margin: f64) -> bool {
        let [lx, ly] = self.to_local(p);
        let r = py_hypot(lx, ly);
        if r > self.range + margin || ly <= 0.0 {
            return false;
        }
        lx.atan2(ly).to_degrees().abs() <= self.fov / 2.0 + (margin / r.max(0.3)).to_degrees()
    }

    /// Where its sight lines start: SIGHT_OFFSET in front of it.
    pub fn sight_origin(&self) -> Point {
        let [c, s] = self.axis();
        [self.x + SIGHT_OFFSET * c, self.y + SIGHT_OFFSET * s]
    }

    /// In its field of view and no wall between.
    pub fn sees(&self, p: Point, walls: &[[Point; 2]], margin: f64) -> bool {
        self.in_fov(p, margin) && line_of_sight(self.sight_origin(), p, walls)
    }
}

/// How a sensor hangs: what turns its targets into floor positions.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Mount {
    pub at: Point,
    /// (cos, sin) of its heading
    pub axis: Point,
    pub height: f64,
    /// its x axis points to the left
    pub mirror: bool,
    /// true distance on the floor / measured one (from the calibration)
    pub scale: f64,
}

/// A target on the floor: where, and its distance to the sensor on the floor and along the beam.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OnFloor {
    pub pos: Point,
    pub ground: f64,
    pub slant: f64,
}

impl Mount {
    /// The house frame -> the sensor's frame on the floor (x right, y forward), as it measures (its
    /// scale taken out).
    pub fn to_local(&self, [wx, wy]: Point) -> Point {
        let [c, s] = self.axis;
        let (dx, dy) = ((wx - self.at[0]) / self.scale, (wy - self.at[1]) / self.scale);
        let gy = dx * c + dy * s;
        let gx = dx * s - dy * c;
        [if self.mirror { -gx } else { gx }, gy]
    }

    /// A target (m, sensor frame) in the house frame (the inverse of to_local on the floor). The radar measures the
    /// slant distance from its antenna; the height difference to the reflecting body is taken out so
    /// that the position lies on the floor. Then the sensor's scale corrects the distance.
    pub fn to_world(&self, local: Point, target_height: f64) -> OnFloor {
        let ([lx, ly], ground, slant) = self.slant_out(local, target_height);
        let k = if slant > 0.0 {
            self.scale * ground / slant
        } else {
            self.scale
        };
        let (gx, gy) = (lx * k, ly * k);
        // sensor frame: y forward along the heading, x to the right
        let [c, s] = self.axis;
        OnFloor {
            pos: [self.at[0] + gy * c + gx * s, self.at[1] + gy * s - gx * c],
            ground: self.scale * ground,
            slant: self.scale * slant,
        }
    }

    /// A target (m, sensor frame) on the floor in the sensor's own frame (x right, y forward), its scale
    /// not applied: the slant taken out as in to_world (the calibration fits the scale itself).
    pub fn on_floor(&self, local: Point, target_height: f64) -> Point {
        let ([lx, ly], ground, slant) = self.slant_out(local, target_height);
        let k = if slant > 0.0 { ground / slant } else { 1.0 };
        [lx * k, ly * k]
    }

    /// A target's sensor coordinates with the mirror taken out, its distance on the floor (at least 0.1 m)
    /// and along the beam.
    fn slant_out(&self, [lx, ly]: Point, target_height: f64) -> (Point, f64, f64) {
        let lx = if self.mirror { -lx } else { lx };
        let slant = py_hypot(lx, ly);
        let dh = self.height - target_height;
        let ground = (slant * slant - dh * dh).max(0.01).sqrt();
        ([lx, ly], ground, slant)
    }
}

/// What a zone is (its kind with the flag entry).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ZoneKind {
    /// a room of the home (from the walls)
    Room,
    /// a room outside the home, the public space beyond its door (the stairwell; MODEL.md 2)
    OutsideRoom,
    /// an area drawn in a room
    Area,
    /// a way in drawn by hand
    Entry,
}

/// A zone.
#[derive(Debug, Deserialize)]
#[serde(try_from = "ZoneRaw")]
pub struct ZoneConfig {
    pub id: String,
    pub name: String,
    pub kind: ZoneKind,
    pub shape: Shape,
}

impl ZoneConfig {
    pub fn contains(&self, p: Point, margin: f64) -> bool {
        self.shape.contains(p, margin)
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum KindRaw {
    Room,
    Area,
    Entry,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum ShapeKind {
    Rect,
    Circle,
    Polygon,
}

/// A zone as stored: its centre and radius only for a circle, entry only for a room.
#[derive(Deserialize)]
struct ZoneRaw {
    id: String,
    name: String,
    kind: KindRaw,
    shape: ShapeKind,
    points: Vec<Point>,
    #[serde(default)]
    center: Option<Point>,
    #[serde(default)]
    radius: f64,
    #[serde(default)]
    entry: bool,
}

impl TryFrom<ZoneRaw> for ZoneConfig {
    type Error = String;

    fn try_from(z: ZoneRaw) -> Result<Self, String> {
        let shape = match (z.shape, z.points.as_slice(), z.center) {
            (ShapeKind::Rect, &[a, b], _) => Shape::rect(a, b),
            (ShapeKind::Circle, _, Some(center)) => Shape::Circle {
                center,
                radius: z.radius,
            },
            (ShapeKind::Polygon, _, _) => Shape::polygon(z.points),
            _ => {
                return Err(format!(
                    "zone {}: a rectangle without two corners or a circle without a centre",
                    z.id
                ));
            }
        };
        let kind = match (z.kind, z.entry) {
            (KindRaw::Room, false) => ZoneKind::Room,
            (KindRaw::Room, true) => ZoneKind::OutsideRoom,
            (KindRaw::Area, _) => ZoneKind::Area,
            (KindRaw::Entry, _) => ZoneKind::Entry,
        };
        Ok(Self {
            id: z.id,
            name: z.name,
            kind,
            shape,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum WallKind {
    /// a real wall: blocks the radar's sight and the people
    Wall,
    /// a room boundary without a wall: splits rooms, blocks nothing
    Divider,
}

/// One straight piece of the plan.
#[derive(Debug, Deserialize)]
#[serde(try_from = "WallRaw")]
pub struct WallConfig {
    pub a: Point,
    pub b: Point,
    pub kind: WallKind,
}

#[derive(Deserialize)]
struct WallRaw {
    points: Vec<Point>,
    kind: WallKind,
}

impl TryFrom<WallRaw> for WallConfig {
    type Error = String;

    fn try_from(w: WallRaw) -> Result<Self, String> {
        match w.points.as_slice() {
            &[a, b] => Ok(Self { a, b, kind: w.kind }),
            _ => Err("a wall that is no segment".into()),
        }
    }
}

/// A door on a wall: its centre and width.
#[derive(Debug, Deserialize, Serialize)]
pub struct DoorConfig {
    pub id: String,
    pub x: f64,
    pub y: f64,
    pub width: f64,
}

/// The tracker's parameters of the configuration (its "params"), all of them read by the core; their values
/// unless set are `TrackerParams::default()`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct TrackerParams {
    /// m: height of the reflecting body (chest), for the slant correction
    pub target_height: f64,
    /// m: the LD2450's error (1 sigma) = base + slope x distance on the floor, along and across the line of
    /// sight
    pub range_sigma_base: f64,
    pub range_sigma_slope: f64,
    pub lateral_sigma_base: f64,
    pub lateral_sigma_slope: f64,
    /// m: detections farther behind a wall or outside all rooms are reflections
    pub wall_margin: f64,
    /// s, and the spread of ln(duration): stays in rooms without a sensor (MODEL.md 3.3, assumed)
    pub dwell_median: f64,
    pub dwell_spread: f64,
    /// s: gaps in the LD2410C presence up to this long are bridged (display)
    pub ld2410_hold: f64,
    /// a second of light without anybody costs as much as this many seconds dark with somebody there
    pub light_cost: f64,
    /// s: "Belegt" (MODEL.md 6) also while the room's own LD2450 measured somebody in it within this long
    /// (room_seen.rs); 0: the filter alone
    pub seen_hold: f64,
    /// s and the cost of a light switched on in vain against an entry into a dark room: "wird betreten"
    /// (MODEL.md 6) is P(a walker enters within lead_time) above approach_cost / (approach_cost + 1)
    pub lead_time: f64,
    pub approach_cost: f64,
    /// "Ziel" (MODEL.md 6): on at P >= this threshold c = K_false / (K_false + K_late), per room where it
    /// differs (room id -> threshold)
    pub target_threshold: f64,
    pub target_thresholds: BTreeMap<String, f64>,
}

impl Default for TrackerParams {
    /// The parameters unless set: the LD2450's error from a sweep over the 2026-10-03 recordings; the stays
    /// assumed, wide so that long ones stay possible; light without a person is the worst (Leon: twice a
    /// second dark with somebody there); seen_hold 10 s measured on the truth of 6.-8.10.; lead_time 2 s and
    /// approach_cost 0.05 measured 6./7.10. (light >= 1 s / 1 m before 60 / 62 % of the entries, 4.0 vain
    /// 30-s lights per hour); target_threshold 0.8: a vain light costs as much as 4 late ones.
    fn default() -> Self {
        Self {
            target_height: 1.0,
            range_sigma_base: 0.15,
            range_sigma_slope: 0.02,
            lateral_sigma_base: 0.10,
            lateral_sigma_slope: 0.05,
            wall_margin: 0.4,
            dwell_median: 120.0,
            dwell_spread: 1.5,
            ld2410_hold: 1.5,
            light_cost: 2.0,
            seen_hold: 10.0,
            lead_time: 2.0,
            approach_cost: 0.05,
            target_threshold: 0.8,
            target_thresholds: BTreeMap::new(),
        }
    }
}

/// A sensor unless its configuration says otherwise, and every new one the editor puts on the map: the LD2450's
/// field of view and range, the mounting height assumed, looking along +y until turned, read as measured (not
/// mirrored, scale 1), in use once placed. Its id (its MQTT name), name and place have none: they are its own.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct SensorTemplate {
    pub heading: f64,
    pub height: f64,
    pub fov: f64,
    pub range: f64,
    pub mirror: bool,
    pub scale: f64,
    pub enabled: bool,
    pub placed: bool,
}

pub const SENSOR_TEMPLATE: SensorTemplate = SensorTemplate {
    heading: 90.0,
    height: 1.5,
    fov: 120.0,
    range: 6.0,
    mirror: false,
    scale: 1.0,
    enabled: true,
    placed: false,
};

/// A door unless set, and every new one (at most as wide as its wall): an ordinary inner door.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct DoorTemplate {
    pub width: f64,
}

pub const DOOR_TEMPLATE: DoorTemplate = DoorTemplate { width: 0.9 };

/// A wall unless set: a real wall.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct WallTemplate {
    pub kind: WallKind,
}

pub const WALL_TEMPLATE: WallTemplate = WallTemplate { kind: WallKind::Wall };

/// What a configuration may leave out, with the values the model takes then (`canonical` fills them in), and
/// what the web UI's editor gives every new element: {"params", "sensor", "door", "wall"}.
#[derive(Debug, Serialize)]
pub struct Defaults {
    pub params: TrackerParams,
    pub sensor: SensorTemplate,
    pub door: DoorTemplate,
    pub wall: WallTemplate,
}

pub fn defaults() -> Defaults {
    Defaults {
        params: TrackerParams::default(),
        sensor: SENSOR_TEMPLATE,
        door: DOOR_TEMPLATE,
        wall: WALL_TEMPLATE,
    }
}

/// An object's fields, those of `template` it leaves out filled in from it.
fn filled<T: Serialize>(given: &Map<String, Value>, template: &T) -> Map<String, Value> {
    let Ok(Value::Object(mut out)) = serde_json::to_value(template) else {
        unreachable!("a template is an object")
    };
    out.extend(given.iter().map(|(k, v)| (k.clone(), v.clone())));
    out
}

/// A value as the model reads it, written back (its fields, numbers as numbers of the model).
fn as_read<T: Serialize + for<'de> Deserialize<'de>>(v: Map<String, Value>) -> Result<Value, ConfigError> {
    let read: T = serde_json::from_value(Value::Object(v)).map_err(ConfigError::unreadable)?;
    serde_json::to_value(read).map_err(ConfigError::unreadable)
}

/// The keys of a zone the model reads, by its shape and kind, and the room's label point (the editor's, as it
/// came: the model never reads it).
fn zone_canonical(z: &Map<String, Value>) -> Value {
    let mut out = Map::new();
    for key in ["id", "name", "kind", "shape", "points"] {
        if let Some(v) = z.get(key) {
            out.insert(key.into(), v.clone());
        }
    }
    if z.get("shape").and_then(Value::as_str) == Some("circle") {
        out.insert("center".into(), z.get("center").cloned().unwrap_or(Value::Null));
        out.insert("radius".into(), z.get("radius").cloned().unwrap_or(0.0.into()));
    }
    if z.get("kind").and_then(Value::as_str) == Some("room") {
        out.insert("entry".into(), z.get("entry").cloned().unwrap_or(false.into()));
        if let Some(anchor) = z.get("anchor") {
            out.insert("anchor".into(), anchor.clone());
        }
    }
    Value::Object(out)
}

/// The one form of a configuration (JSON) the app stores and the web UI edits: checked (ConfigError names the
/// first problem), what it leaves out filled in from `defaults`, the parameters, sensors and doors with exactly
/// the fields the model reads and their numbers as it reads them, a zone with the keys of its shape and kind;
/// the web UI's own parts (the background image, a room's label point) as they came, never read. Whatever
/// else it holds is dropped.
pub fn canonical(json: &str) -> Result<Value, ConfigError> {
    let value: Value = serde_json::from_str(json).map_err(ConfigError::unreadable)?;
    check(&value)?;
    let empty = Map::new();
    let o = value.as_object().unwrap_or(&empty);
    let objects = |key: &str| -> Vec<&Map<String, Value>> {
        o.get(key)
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_object).collect())
            .unwrap_or_default()
    };
    let mut out = Map::new();
    let sensors = objects("sensors")
        .into_iter()
        .map(|s| as_read::<SensorConfig>(filled(s, &SENSOR_TEMPLATE)))
        .collect::<Result<Vec<_>, _>>()?;
    out.insert("sensors".into(), Value::Array(sensors));
    out.insert(
        "zones".into(),
        Value::Array(objects("zones").into_iter().map(zone_canonical).collect()),
    );
    let walls = objects("walls")
        .into_iter()
        .map(|w| {
            let w = filled(w, &WALL_TEMPLATE);
            Value::Object(w.into_iter().filter(|(k, _)| k == "points" || k == "kind").collect())
        })
        .collect();
    out.insert("walls".into(), Value::Array(walls));
    let doors = objects("doors")
        .into_iter()
        .map(|d| as_read::<DoorConfig>(filled(d, &DOOR_TEMPLATE)))
        .collect::<Result<Vec<_>, _>>()?;
    out.insert("doors".into(), Value::Array(doors));
    out.insert(
        "ld_view".into(),
        o.get("ld_view")
            .filter(|v| v.is_object())
            .cloned()
            .unwrap_or_else(|| Value::Object(Map::new())),
    );
    let params = o.get("params").and_then(Value::as_object).unwrap_or(&empty);
    let params = filled(params, &TrackerParams::default());
    out.insert("params".into(), as_read::<TrackerParams>(params)?);
    if let Some(background) = o.get("background") {
        out.insert("background".into(), background.clone());
    }
    let out = Value::Object(out);
    Config::deserialize(&out).map_err(ConfigError::unreadable)?;
    Ok(out)
}

/// The configuration (JSON, the canonical form) without what was measured with the sensors where they hung and
/// the plan as it was: the LD2410Cs' view of the other rooms (ld_view, MODEL.md 4.3), and how many room pairs of
/// it went. The shell takes it in place of a new configuration of another geometry (`Config::geometry_key`), as
/// it discards what was collected and learned under the old one.
pub fn forget_measured(json: &str) -> Result<(Value, usize), ConfigError> {
    let mut value: Value = serde_json::from_str(json).map_err(ConfigError::unreadable)?;
    let gone = value
        .as_object_mut()
        .and_then(|o| o.insert("ld_view".into(), Value::Object(Map::new())))
        .and_then(|v| v.as_object().map(Map::len))
        .unwrap_or(0);
    Ok((value, gone))
}

impl Config {
    /// The configuration of its JSON (the model's part of `canonical`), checked field by field (check.rs; the
    /// error names the first problem).
    pub fn from_json(json: &str) -> Result<Self, ConfigError> {
        let value: Value = serde_json::from_str(json).map_err(ConfigError::unreadable)?;
        check(&value)?;
        Self::deserialize(value).map_err(ConfigError::unreadable)
    }

    /// The key of the geometry: where every sensor hangs and how it sees and is read (place, heading, height,
    /// mirror, scale, field of view, range, placed or not), the walls, the doors and the zones' shapes and kinds:
    /// the one definition of "the sensors or the floor plan changed". What was collected or learned holds only
    /// under the key it was collected under (the calibration's measurements, the LD2410C
    /// background, the destination map, the measured ld_view): a sensor's sight, its frame and the rooms are
    /// all read from these. Not in it: names, a sensor switched on or off, the parameters, ld_view itself.
    /// The numbers by value (1 is 1.0, -0 is 0), in the order of the configuration; sha1, 16 hex digits.
    pub fn geometry_key(&self) -> String {
        let mut h = Fingerprint::default();
        let flag = |b: bool| f64::from(u8::from(b));
        let points = |pts: &[Point]| pts.iter().flatten().map(|&v| v + 0.0).collect::<Vec<_>>();
        for s in &self.sensors {
            let read = [
                s.x,
                s.y,
                s.heading,
                s.height,
                flag(s.mirror),
                s.scale,
                s.fov,
                s.range,
                flag(s.placed),
            ];
            h.text("sensor\n").text(&s.id).text("\n").floats(read.map(|v| v + 0.0));
        }
        for z in &self.zones {
            let (shape, pts) = match &z.shape {
                Shape::Rect { lo, hi } => ("rect", points(&[*lo, *hi])),
                Shape::Circle { center, radius } => ("circle", [points(&[*center]), vec![radius + 0.0]].concat()),
                Shape::Polygon { corners, .. } => ("polygon", points(corners)),
            };
            h.text("zone\n")
                .text(&z.id)
                .text(&format!("\n{:?}\n{shape}\n", z.kind))
                .floats(pts);
        }
        for w in &self.walls {
            h.text(&format!("wall\n{:?}\n", w.kind)).floats(points(&[w.a, w.b]));
        }
        for d in &self.doors {
            h.text("door\n")
                .text(&d.id)
                .text("\n")
                .floats([d.x, d.y, d.width].map(|v| v + 0.0));
        }
        h.hex()
    }

    /// The rooms, the outside rooms too.
    pub fn rooms(&self) -> impl Iterator<Item = &ZoneConfig> {
        self.zones.iter().filter(|z| z.kind.is_room())
    }
}

impl ZoneKind {
    /// A room of the home or an outside room.
    pub fn is_room(self) -> bool {
        matches!(self, Self::Room | Self::OutsideRoom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::config_json;

    #[test]
    fn on_the_floor_the_slant_is_taken_out_but_not_the_scale() {
        // 1.5 m up, a chest at 1 m: 1.3 m along the beam straight ahead is 1.2 m on the floor; 1 m at
        // 36.87 deg (0.6, 0.8) is sqrt(0.75) m, in the same direction, x to the left when mirrored
        let mount = |mirror| Mount {
            at: [5.0, 5.0],
            axis: [0.0, 1.0],
            height: 1.5,
            mirror,
            scale: 2.0,
        };
        let close = |a: Point, b: Point| (a[0] - b[0]).abs() < 1e-15 && (a[1] - b[1]).abs() < 1e-15;
        assert!(close(mount(false).on_floor([0.0, 1.3], 1.0), [0.0, 1.2]));
        let k = 0.75_f64.sqrt();
        assert!(close(mount(false).on_floor([0.6, 0.8], 1.0), [0.6 * k, 0.8 * k]));
        assert!(close(mount(true).on_floor([0.6, 0.8], 1.0), [-0.6 * k, 0.8 * k]));
    }

    #[test]
    fn the_canonical_form_fills_in_what_is_left_out_and_keeps_the_web_uis_parts() {
        let given = r#"{"sensors": [{"id": "s", "name": "S", "x": 1, "y": 2, "fov": 90, "old": 1}],
            "zones": [{"id": "r", "name": "R", "kind": "room", "shape": "rect", "points": [[0, 0], [4, 4]],
                       "anchor": [2, 2], "radius": 3}],
            "walls": [{"points": [[0, 0], [4, 0]]}], "doors": [{"id": "d", "x": 1, "y": 0}],
            "params": {"light_cost": 3, "gone": 1}, "background": {"layers": [{"x": 5}]}, "other": 1}"#;
        let c = canonical(given).expect("fits");
        let t = SENSOR_TEMPLATE;
        assert_eq!(
            c["sensors"][0],
            serde_json::json!({"id": "s", "name": "S", "x": 1.0, "y": 2.0, "heading": t.heading, "height": t.height,
                "mirror": t.mirror, "scale": t.scale, "fov": 90.0, "range": t.range, "enabled": t.enabled,
                "placed": t.placed})
        );
        assert_eq!(
            c["zones"][0],
            serde_json::json!({"id": "r", "name": "R", "kind": "room", "shape": "rect", "points": [[0, 0], [4, 4]],
                "entry": false, "anchor": [2, 2]})
        );
        assert_eq!(c["walls"][0]["kind"], "wall");
        assert_eq!(c["doors"][0]["width"], DOOR_TEMPLATE.width);
        let mut params = serde_json::to_value(TrackerParams::default()).expect("JSON");
        params["light_cost"] = 3.0.into();
        assert_eq!(c["params"], params);
        assert_eq!(c["background"], serde_json::json!({"layers": [{"x": 5}]}));
        assert!(c.get("other").is_none() && c["ld_view"] == serde_json::json!({}));
        // the canonical form is its own canonical form, and the model reads it
        assert_eq!(canonical(&c.to_string()).expect("fits"), c);
        Config::from_json(&c.to_string()).expect("read");
        // what has no value unless given, and what does not fit, is refused
        let unnamed = given.replace(r#""name": "S", "#, "");
        assert!(matches!(
            canonical(&unnamed).expect_err("refused").problem,
            Problem::Unreadable(_)
        ));
        let far = given.replace(r#""fov": 90"#, r#""fov": 200"#);
        assert_eq!(canonical(&far).expect_err("refused").field, Some("fov"));
    }

    #[test]
    fn the_geometry_key_changes_with_a_sensors_place_and_the_plan_only() {
        let zone = r#"[{"id": "r", "name": "R", "kind": "room", "shape": "rect", "points": [[0, 0], [4, 4]]}]"#;
        let walls = format!("[{}]", crate::testing::wall([0.0, 0.0], [4.0, 0.0]));
        let doors = r#"[{"id": "d", "x": 2, "y": 0, "width": 0.9}]"#;
        let old = config_json(&[("s", "S", [0.5, 0.5], 0.0)], zone, &walls, doors);
        let key = |json: &str| {
            Config::from_json(json)
                .unwrap_or_else(|e| panic!("{json}: {e:?}"))
                .geometry_key()
        };
        let k = key(&old);
        assert_eq!(k.len(), 16);
        // what does not change where and how the sensors see, nor the plan
        for (from, to) in [
            (r#""name": "S""#, r#""name": "Bad""#),
            (r#""name": "R""#, r#""name": "Raum""#),
            (r#""enabled": true"#, r#""enabled": false"#),
            (r#""light_cost": 2.0"#, r#""light_cost": 3"#),
            (r#""wall_margin": 0.4"#, r#""wall_margin": 0.5"#),
            (r#""ld_view": {}"#, r#""ld_view": {"r<-r": 0.5}"#),
            (r#""x": 0.5"#, r#""x": 0.50"#),
            (r#""heading": 0.0"#, r#""heading": -0.0"#),
            (r#"[[0, 0], [4, 4]]"#, r#"[[0.0, 0], [4, 4.0]]"#),
        ] {
            let new = old.replace(from, to);
            assert_ne!(new, old, "{from}");
            assert_eq!(key(&new), k, "{from} -> {to}");
        }
        // every part of a sensor's place, sight and reading, placed or not, the walls, doors and zones
        for (from, to) in [
            (r#""x": 0.5"#, r#""x": 0.51"#),
            (r#""y": 0.5"#, r#""y": 1.5"#),
            (r#""heading": 0.0"#, r#""heading": 1"#),
            (r#""height": 1.5"#, r#""height": 2.0"#),
            (r#""mirror": false"#, r#""mirror": true"#),
            (r#""scale": 1.0"#, r#""scale": 1.05"#),
            (r#""fov": 120.0"#, r#""fov": 100"#),
            (r#""range": 6.0"#, r#""range": 5"#),
            (r#""placed": true"#, r#""placed": false"#),
            (r#"[4.0, 0.0]]"#, r#"[5.0, 0.0]]"#),
            (r#""x": 2, "y": 0"#, r#""x": 3, "y": 0"#),
            (r#""width": 0.9"#, r#""width": 1"#),
            (r#"[[0, 0], [4, 4]]"#, r#"[[0, 0], [4, 5]]"#),
            (r#""kind": "room""#, r#""kind": "area""#),
            (r#""id": "s""#, r#""id": "t""#),
        ] {
            let new = old.replace(from, to);
            assert_ne!(new, old, "{from}");
            assert_ne!(key(&new), k, "{from} -> {to}");
        }
        let more = old.replace(
            r#""walls": ["#,
            &format!(r#""walls": [{}, "#, crate::testing::wall([4.0, 0.0], [4.0, 4.0])),
        );
        assert_ne!(key(&more), k);
        let divided = more.replacen(r#""kind": "wall""#, r#""kind": "divider""#, 1);
        assert_ne!(key(&divided), key(&more));
        // what was measured under a geometry goes with it, the rest stays as it was
        let measured = old.replace(r#""ld_view": {}"#, r#""ld_view": {"r<-r": 0.5}"#);
        let forgotten = forget_measured(&measured).expect("JSON");
        assert_eq!(forgotten, (serde_json::from_str::<Value>(&old).expect("JSON"), 1));
    }

    #[test]
    fn a_configuration_is_checked_before_it_is_read() {
        let one = [("s", "", [0.5, 0.5], 0.0)];
        let far = config_json(&one, "[]", "[]", "[]").replace(r#""range": 6.0"#, r#""range": 50.0"#);
        let err = Config::from_json(&far).expect_err("refused");
        assert_eq!((err.part, err.field), (Part::Sensor(Some("s".into())), Some("range")));
        // a field the shell always writes is required (what the check lets pass as missing)
        let missing = config_json(&one, "[]", "[]", "[]").replace(r#""ld_view": {}, "#, "");
        assert!(matches!(
            Config::from_json(&missing).expect_err("refused").problem,
            Problem::Unreadable(_)
        ));
        assert!(matches!(
            Config::from_json("{").expect_err("refused").problem,
            Problem::Unreadable(_)
        ));
        Config::from_json(&config_json(&one, "[]", "[]", "[]")).expect("fits");
    }
}
