//! The configuration as the app stores it (model.Config.to_dict(): sensors, zones, walls, doors, the
//! LD2410Cs' measured view and the tracker's parameters; the background image is the UI's), read with serde. Lengths in metres,
//! angles in degrees, in the house frame. The rooms are already the faces between the walls
//! (floorplan.sync_rooms runs where the plan is edited, in the shell). Every field the shell always
//! writes is required: no defaults here that could hide a renamed field. Before it is read it is checked
//! (check.rs: the limits of every number, also the ones of the web UI's fields). The sensors' geometry here
//! is the only one: the shell's calibration and simulation use it too (presence_core.Geometry).
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

mod check;

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::Value;

pub use check::{
    ConfigError, DOOR, LAYER, Limits, MAX_CELLS, MAX_RANGE, MAX_SENSORS, PARAMS, Part, Problem, SENSOR,
    TARGET_THRESHOLD, check, check_edit,
};

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

/// A sensor (model.SensorConfig): where it hangs, where it looks, how its measurements are read.
#[derive(Debug, Deserialize)]
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

/// How a sensor hangs (model.SensorConfig): what turns its targets into floor positions.
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

/// What a zone is (model.ZoneConfig.kind with its flag entry).
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

/// A zone (model.ZoneConfig).
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
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
#[derive(Debug, Deserialize)]
pub struct DoorConfig {
    pub id: String,
    pub x: f64,
    pub y: f64,
    pub width: f64,
}

/// The tracker's parameters of the configuration (model.TrackerParams) that the core reads.
#[derive(Debug, Deserialize)]
pub struct TrackerParams {
    /// m: height of the reflecting body (chest), for the slant correction
    pub target_height: f64,
    /// m: the LD2450's error (1 sigma) = base + slope x distance, along and across the line of sight
    pub range_sigma_base: f64,
    pub range_sigma_slope: f64,
    pub lateral_sigma_base: f64,
    pub lateral_sigma_slope: f64,
    /// m: detections farther behind a wall or outside all rooms are reflections
    pub wall_margin: f64,
    /// s, and the spread of ln(duration): stays in rooms without a sensor
    pub dwell_median: f64,
    pub dwell_spread: f64,
    /// s: gaps in the LD2410C presence up to this long are bridged (display)
    pub ld2410_hold: f64,
    pub light_cost: f64,
    pub seen_hold: f64,
    pub lead_time: f64,
    pub approach_cost: f64,
    pub target_threshold: f64,
    pub target_thresholds: BTreeMap<String, f64>,
}

/// model.TrackerParams' defaults, for the tests' configurations.
#[cfg(test)]
impl Default for TrackerParams {
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

impl Config {
    /// The configuration of the JSON of model.Config.to_dict(), checked field by field (check.rs; the
    /// error names the first problem).
    pub fn from_json(json: &str) -> Result<Self, ConfigError> {
        let value: Value = serde_json::from_str(json).map_err(ConfigError::unreadable)?;
        check(&value)?;
        Self::deserialize(value).map_err(ConfigError::unreadable)
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
