//! The configuration as the app stores it (model.Config.to_dict(): sensors, zones, walls, doors and
//! the tracker's parameters; the background image is the UI's), read with serde. Lengths in metres,
//! angles in degrees, in the house frame. The rooms are already the faces between the walls
//! (floorplan.sync_rooms runs where the plan is edited, in the shell).

use std::collections::BTreeMap;

use serde::Deserialize;

use crate::geometry::{Point, Shape, line_of_sight};
use crate::vmath::py_hypot;

/// m: sight lines start this far in front of a sensor (model.SIGHT_OFFSET): it hangs on a wall, often
/// in a corner, and a few centimetres of drawing error would otherwise put it behind that wall.
pub const SIGHT_OFFSET: f64 = 0.2;

#[derive(Debug, Default, Deserialize)]
#[serde(default)]
pub struct Config {
    pub sensors: Vec<SensorConfig>,
    pub zones: Vec<ZoneConfig>,
    pub walls: Vec<WallConfig>,
    pub doors: Vec<DoorConfig>,
    pub params: TrackerParams,
}

/// A sensor (model.SensorConfig): where it hangs, where it looks, how its measurements are read.
#[derive(Debug, Deserialize)]
#[serde(default)]
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

impl Default for SensorConfig {
    fn default() -> Self {
        Self {
            id: String::new(),
            name: String::new(),
            x: 0.0,
            y: 0.0,
            heading: 0.0,
            height: 1.5,
            mirror: false,
            scale: 1.0,
            fov: 120.0,
            range: 6.0,
            enabled: true,
            placed: false,
        }
    }
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

    /// House frame -> the sensor's frame on the floor (x right, y forward), as it measures (its scale
    /// taken out; SensorConfig.to_local).
    pub fn to_local(&self, [wx, wy]: Point) -> Point {
        let [c, s] = self.axis();
        let (dx, dy) = ((wx - self.x) / self.scale, (wy - self.y) / self.scale);
        let gy = dx * c + dy * s;
        let gx = dx * s - dy * c;
        [if self.mirror { -gx } else { gx }, gy]
    }

    /// Within its field of view and range, grown by margin (SensorConfig.in_fov).
    pub fn in_fov(&self, p: Point, margin: f64) -> bool {
        let [lx, ly] = self.to_local(p);
        let r = py_hypot(lx, ly);
        if r > self.range + margin || ly <= 0.0 {
            return false;
        }
        lx.atan2(ly).to_degrees().abs() <= self.fov / 2.0 + (margin / r.max(0.3)).to_degrees()
    }

    /// Where its sight lines start (SensorConfig.sight_origin).
    pub fn sight_origin(&self) -> Point {
        let [c, s] = self.axis();
        [self.x + SIGHT_OFFSET * c, self.y + SIGHT_OFFSET * s]
    }

    /// In its field of view and no wall between (SensorConfig.sees).
    pub fn sees(&self, p: Point, walls: &[[Point; 2]], margin: f64) -> bool {
        self.in_fov(p, margin) && line_of_sight(self.sight_origin(), p, walls)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ZoneKind {
    Room,
    Area,
    Entry,
}

/// A zone (model.ZoneConfig): a room (from the walls), an area drawn in one, or a way in.
#[derive(Debug, Deserialize)]
#[serde(try_from = "ZoneRaw")]
pub struct ZoneConfig {
    pub id: String,
    pub name: String,
    pub kind: ZoneKind,
    pub shape: Shape,
    /// a room outside the home, the public space beyond its door (the stairwell; MODEL.md 2)
    pub entry: bool,
}

impl ZoneConfig {
    pub fn contains(&self, p: Point, margin: f64) -> bool {
        self.shape.contains(p, margin)
    }

    /// A room of the home (not an outside room).
    pub fn home_room(&self) -> bool {
        self.kind == ZoneKind::Room && !self.entry
    }
}

#[derive(Deserialize)]
#[serde(default)]
struct ZoneRaw {
    id: String,
    name: String,
    kind: ZoneKind,
    shape: String,
    points: Vec<Point>,
    center: Option<Point>,
    radius: f64,
    entry: bool,
}

impl Default for ZoneRaw {
    fn default() -> Self {
        Self {
            id: String::new(),
            name: String::new(),
            kind: ZoneKind::Area,
            shape: "rect".into(),
            points: Vec::new(),
            center: None,
            radius: 0.0,
            entry: false,
        }
    }
}

impl TryFrom<ZoneRaw> for ZoneConfig {
    type Error = String;

    fn try_from(z: ZoneRaw) -> Result<Self, String> {
        let shape = match (z.shape.as_str(), z.points.as_slice(), z.center) {
            ("rect", &[a, b], _) => Shape::rect(a, b),
            ("circle", _, Some(center)) => Shape::Circle {
                center,
                radius: z.radius,
            },
            ("polygon", _, _) => Shape::polygon(z.points),
            (kind, _, _) => return Err(format!("zone {}: a {kind} that is no outline", z.id)),
        };
        Ok(Self {
            id: z.id,
            name: z.name,
            kind: z.kind,
            shape,
            entry: z.entry,
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
    #[serde(default = "wall_kind")]
    kind: WallKind,
}

fn wall_kind() -> WallKind {
    WallKind::Wall
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
    #[serde(default)]
    pub id: String,
    pub x: f64,
    pub y: f64,
    #[serde(default = "door_width")]
    pub width: f64,
}

fn door_width() -> f64 {
    0.9
}

/// The tracker's parameters of the configuration (model.TrackerParams) that the core reads.
#[derive(Debug, Deserialize)]
#[serde(default)]
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
    /// s, and the spread of ln(duration): stays in rooms without a sensor (unobserved.Dwell)
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
    /// The configuration of the JSON of model.Config.to_dict(), checked: finite numbers, sensors
    /// with a positive scale (Err names what does not fit).
    pub fn from_json(json: &str) -> Result<Self, String> {
        let config: Self = serde_json::from_str(json).map_err(|e| e.to_string())?;
        config.check()?;
        Ok(config)
    }

    fn check(&self) -> Result<(), String> {
        let finite = |v: &[f64]| v.iter().all(|x| x.is_finite());
        for s in &self.sensors {
            if !finite(&[s.x, s.y, s.heading, s.height, s.fov, s.range]) || !(s.scale > 0.0 && s.scale.is_finite()) {
                return Err(format!("sensor {}: numbers that do not fit", s.id));
            }
        }
        let points = |z: &Shape| z.outline(4).iter().all(|p| finite(p));
        if let Some(z) = self.zones.iter().find(|z| !points(&z.shape)) {
            return Err(format!("zone {}: a corner that is no number", z.id));
        }
        if !self.walls.iter().all(|w| finite(&[w.a[0], w.a[1], w.b[0], w.b[1]]))
            || !self.doors.iter().all(|d| finite(&[d.x, d.y, d.width]))
        {
            return Err("a wall or a door that is no number".into());
        }
        let p = &self.params;
        let ok = [
            p.target_height,
            p.dwell_median,
            p.dwell_spread,
            p.light_cost,
            p.seen_hold,
            p.lead_time,
        ]
        .iter()
        .all(|&x| x >= 0.0 && x.is_finite());
        if ok && p.dwell_median > 0.0 && p.dwell_spread > 0.0 {
            Ok(())
        } else {
            Err("the tracker's parameters do not fit".into())
        }
    }

    pub fn rooms(&self) -> impl Iterator<Item = &ZoneConfig> {
        self.zones.iter().filter(|z| z.kind == ZoneKind::Room)
    }

    /// The zones of these kinds that belong to the home: not the outside rooms (Config.home_zones).
    pub fn home_zones(&self, kinds: &[ZoneKind]) -> impl Iterator<Item = &ZoneConfig> {
        self.zones
            .iter()
            .filter(move |z| kinds.contains(&z.kind) && !(z.kind == ZoneKind::Room && z.entry))
    }
}
