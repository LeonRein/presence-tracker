//! The plan as the people model sees it (MODEL.md 2), derived from the configuration, never stored
//! (model.Config._places until 0.39): the places - the observed
//! area, the regions without a sensor behind doors, outside -, the doors out of the observed area,
//! the walls the radar does not see through, which room is whose sensor's, and the rule which
//! detections are reflections. Every zone of the configuration is in one table with its role; what
//! refers to a zone holds its index.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use serde::Serialize;

use crate::config::{Config, SensorConfig, WallKind, ZoneConfig, ZoneKind};
use crate::geometry::{Point, Shape, distance_to_segment, line_of_sight};
use crate::vmath::{py_dist, py_hypot};

/// m: a door belongs to the real wall its centre lies on, up to this (the editor puts it exactly on one;
/// its corners are rounded to mm). One rule: the check refuses a door on no wall (config/check.rs).
pub const DOOR_ON_WALL: f64 = 0.01;
/// m: a sensor hangs on a wall, its point lies at the room's outline
const MOUNT_MARGIN: f64 = 0.3;

/// A zone of the plan (an index into Plan::zones).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ZoneIdx(pub usize);

/// An observed room (an index into Plan::rooms, the rooms of the outputs).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct RoomIdx(pub usize);

/// A region without a sensor (an index into Plan::regions).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RegionIdx(pub usize);

/// Where a person is, as the people model has the house (MODEL.md 2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Place {
    /// the area the sensors see
    Observed,
    /// a region without a sensor behind doors
    Region(RegionIdx),
    /// out of the house (the outside rooms too)
    Outside,
}

impl Place {
    /// Its index among the places of a plan with this many regions (observed, the regions, outside):
    /// the order of the arrays per place.
    pub fn index(self, regions: usize) -> usize {
        match self {
            Self::Observed => 0,
            Self::Region(RegionIdx(r)) => r + 1,
            Self::Outside => regions + 1,
        }
    }

    /// The place of an index, None beyond them.
    pub fn of_index(index: usize, regions: usize) -> Option<Self> {
        match index {
            0 => Some(Self::Observed),
            i if i <= regions => Some(Self::Region(RegionIdx(i - 1))),
            i if i == regions + 1 => Some(Self::Outside),
            _ => None,
        }
    }
}

/// What a zone is to the people model.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ZoneRole {
    /// a room of the home the sensors see
    Observed(RoomIdx),
    /// a room of the home nobody sees, in a region
    InRegion(RegionIdx),
    /// a room outside the home (the stairwell)
    OutsideRoom,
    /// an area drawn in a room
    Area,
    /// a way in drawn by hand
    Entry,
}

#[derive(Debug)]
pub struct Zone {
    pub id: String,
    pub name: String,
    pub shape: Shape,
    pub role: ZoneRole,
}

impl Zone {
    /// A room, of the home or outside.
    pub fn is_room(&self) -> bool {
        matches!(
            self.role,
            ZoneRole::Observed(_) | ZoneRole::InRegion(_) | ZoneRole::OutsideRoom
        )
    }

    /// The place of a room (None for an area or an entry).
    pub fn place(&self) -> Option<Place> {
        match self.role {
            ZoneRole::Observed(_) => Some(Place::Observed),
            ZoneRole::InRegion(r) => Some(Place::Region(r)),
            ZoneRole::OutsideRoom => Some(Place::Outside),
            ZoneRole::Area | ZoneRole::Entry => None,
        }
    }
}

/// A region without a sensor (Config.regions): rooms connected by doors. Open if it has ways out of
/// the house (doors to the outside, entry zones drawn in it): whoever comes out of a closed one went in
/// before.
#[derive(Debug)]
pub struct Region {
    /// the id of its first room (Config.regions' key)
    pub id: String,
    pub name: String,
    /// its rooms, sorted by id
    pub rooms: Vec<ZoneIdx>,
    pub exits: usize,
}

impl Region {
    pub fn open(&self) -> bool {
        self.exits > 0
    }
}

/// A way out of the observed area (Config.portals): a door into a region or outside, or an entry zone
/// drawn in view; `watch` is where somebody coming out appears (0.5 m into the observed room).
#[derive(Debug)]
pub struct Portal {
    pub place: Place,
    pub watch: Point,
}

/// A sensor as the plan knows it: its own room and whether it is in use.
#[derive(Debug)]
pub struct SensorPlan {
    pub id: String,
    pub own_room: Option<RoomIdx>,
    pub in_use: bool,
    /// how it hangs: what its learned parts are kept in
    pub pose: Pose,
}

/// m: a sensor whose drawn place or height moved more than this hangs elsewhere (remounted), not only
/// estimated anew (recalibrated). The calibration changes heading, scale and mirror only, the heading within
/// its prior of +-20 deg around the drawn one (320 -> 324 deg, 324.4 -> 321.6 deg); a drawing put right lies
/// within its error (MODEL.md 4.1: 0.2-0.3 m). Mounted on another wall or in another corner, a sensor is
/// further off; turned to look elsewhere, more than a quarter of its 120 deg view. Assumed, not measured
/// (MODEL.md 4.2).
const REMOUNT_SHIFT: f64 = 0.5;
/// degrees: ... or whose heading turned more than this
const REMOUNT_TURN: f64 = 30.0;

/// How a sensor hangs and how its reports are read: what the learned parts of a
/// sensor are kept in.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    /// degrees from the +x axis, counterclockwise
    pub heading: f64,
    pub height: f64,
    pub mirror: bool,
    pub scale: f64,
}

impl Pose {
    pub fn of(s: &SensorConfig) -> Self {
        Self {
            x: s.x,
            y: s.y,
            heading: s.heading,
            height: s.height,
            mirror: s.mirror,
            scale: s.scale,
        }
    }

    /// Whether a sensor that hung at `old` hangs elsewhere now (model.remounted): moved or turned;
    /// scale and mirror are how its measurements are read, never a remount.
    pub fn remounted(&self, old: &Pose) -> bool {
        let shift = ((old.x - self.x).powi(2) + (old.y - self.y).powi(2) + (old.height - self.height).powi(2)).sqrt();
        let turn = ((old.heading - self.heading + 180.0).rem_euclid(360.0) - 180.0).abs();
        shift > REMOUNT_SHIFT || turn > REMOUNT_TURN
    }
}

#[derive(Debug)]
pub struct Plan {
    /// every zone of the configuration, in its order
    pub zones: Vec<Zone>,
    /// the observed rooms (Config.observed_rooms), in the order of the configuration
    pub rooms: Vec<ZoneIdx>,
    pub regions: Vec<Region>,
    /// the rooms in which the sensors see nobody of the home: those of closed regions, then the
    /// outside rooms (Config.closed_rooms)
    pub closed: Vec<ZoneIdx>,
    pub portals: Vec<Portal>,
    pub sensors: Vec<SensorPlan>,
    /// the walls that block the radar's sight and the people: real walls without their door openings
    pub walls: Vec<[Point; 2]>,
    /// the dividers: open boundaries between rooms (no walls)
    pub dividers: Vec<[Point; 2]>,
    /// per zone, for the rooms of the home: the share of it the sensors in use see (observed from
    /// OBSERVED_SHARE on)
    pub shares: Vec<Option<f64>>,
}

/// A door or divider between rooms: the rooms on its sides (None: outside); a door's centre and its
/// wall's normal.
struct Link {
    sides: [Option<usize>; 2],
    door: Option<DoorSide>,
}

struct DoorSide {
    centre: Point,
    normal: Point,
}

/// Every wall longer than 1 µm.
fn pieces(config: &Config) -> impl Iterator<Item = ([Point; 2], WallKind)> + '_ {
    config
        .walls
        .iter()
        .filter(|w| py_dist(w.a, w.b) > 1e-6)
        .map(|w| ([w.a, w.b], w.kind))
}

/// The real walls as lines, their doors not cut out, with their index in the configuration's walls.
fn real_walls(config: &Config) -> Vec<(usize, [Point; 2])> {
    config
        .walls
        .iter()
        .enumerate()
        .filter(|(_, w)| w.kind == WallKind::Wall && py_dist(w.a, w.b) > 1e-6)
        .map(|(i, w)| (i, [w.a, w.b]))
        .collect()
}

/// The real walls as lines, their doors not cut out.
fn wall_lines(config: &Config) -> Vec<[Point; 2]> {
    real_walls(config).into_iter().map(|(_, p)| p).collect()
}

/// The real wall a door's centre lies on (an index into walls): the nearest within DOOR_ON_WALL; a wall
/// without length is none.
pub fn wall_of_door(c: Point, walls: &[[Point; 2]]) -> Option<usize> {
    let mut best: Option<(f64, usize)> = None;
    for (i, &[a, b]) in walls.iter().enumerate() {
        let dist = distance_to_segment(c, a, b);
        if py_dist(a, b) > 1e-6 && dist <= DOOR_ON_WALL && best.is_none_or(|(d0, _)| dist < d0) {
            best = Some((dist, i));
        }
    }
    best.map(|(_, i)| i)
}

/// A door's opening in its wall: the wall (an index into the configuration's walls) and where the opening
/// begins and ends along it, metres from the wall's first end, cut at its ends.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Opening {
    pub wall: usize,
    pub t0: f64,
    pub t1: f64,
}

/// Per door of the configuration the real wall it is on (wall_of_door) and its opening there; None: on no
/// wall (the check refuses that).
pub fn door_openings(config: &Config) -> Vec<Option<Opening>> {
    let real = real_walls(config);
    let lines: Vec<[Point; 2]> = real.iter().map(|&(_, p)| p).collect();
    config
        .doors
        .iter()
        .map(|d| {
            let c = [d.x, d.y];
            wall_of_door(c, &lines).map(|k| {
                let (wall, [a, b]) = real[k];
                let length = py_dist(a, b);
                let t = ((c[0] - a[0]) * (b[0] - a[0]) + (c[1] - a[1]) * (b[1] - a[1])) / length;
                let half = d.width / 2.0;
                Opening {
                    wall,
                    t0: (t - half).max(0.0),
                    t1: (t + half).min(length),
                }
            })
        })
        .collect()
}

/// The point t metres along the piece from a to b.
fn along([a, b]: [Point; 2], t: f64) -> Point {
    let length = py_dist(a, b);
    let u = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
    [a[0] + u[0] * t, a[1] + u[1] * t]
}

/// The walls that block the radar: real walls without their door openings.
fn sight_walls(config: &Config) -> Vec<[Point; 2]> {
    // per wall of the configuration the openings of its doors, along it in metres
    let mut cuts: Vec<Vec<(f64, f64)>> = vec![Vec::new(); config.walls.len()];
    for o in door_openings(config).into_iter().flatten() {
        cuts[o.wall].push((o.t0, o.t1));
    }
    let mut out = Vec::new();
    for (i, [a, b]) in real_walls(config) {
        let cuts = &mut cuts[i];
        let length = py_dist(a, b);
        let at = |t: f64| along([a, b], t);
        let mut start = 0.0;
        cuts.sort_by(|x, y| x.0.total_cmp(&y.0).then(x.1.total_cmp(&y.1)));
        for &(t0, t1) in cuts.iter() {
            if t0 > start {
                out.push([at(start), at(t0)]);
            }
            start = f64::max(start, t1);
        }
        if start < length {
            out.push([at(start), b]);
        }
    }
    out
}

/// The share of a room the sensors see (Config._room_covered): of the points of a 0.4 m raster in it, those
/// some sensor sees; 0 without points. Sensors see most of the room (it is observed) at OBSERVED_SHARE or
/// more. Seeing a part through a door (31 % of the balcony through the glass door) does not make it observed.
fn seen_share(room: &ZoneConfig, sensors: &[&SensorConfig], walls: &[[Point; 2]]) -> f64 {
    let [x0, y0, x1, y1] = room.shape.bounds();
    let (mut n, mut seen) = (0usize, 0usize);
    let mut y = y0 + 0.2;
    while y < y1 {
        let mut x = x0 + 0.2;
        while x < x1 {
            if room.contains([x, y], 0.0) {
                n += 1;
                seen += usize::from(sensors.iter().any(|s| s.sees([x, y], walls, 0.0)));
            }
            x += 0.4;
        }
        y += 0.4;
    }
    if n > 0 { seen as f64 / n as f64 } else { 0.0 }
}

/// A room of the home is observed when the sensors see this share of it (MODEL.md 2).
pub const OBSERVED_SHARE: f64 = 0.6;

/// Python's str.casefold for the names here (lower case, ß as ss).
fn casefold(s: &str) -> String {
    s.to_lowercase().replace('ß', "ss")
}

/// The room of a sensor among the observed rooms: named like the room's id, else
/// like its name, else the one it is mounted in (within MOUNT_MARGIN), if exactly one.
fn own_room(s: &SensorConfig, rooms: &[&ZoneConfig]) -> Option<RoomIdx> {
    let name = s.name.trim();
    if !name.is_empty() {
        if let Some(k) = rooms.iter().position(|z| z.id == name) {
            return Some(RoomIdx(k));
        }
        if let Some(k) = rooms.iter().position(|z| casefold(z.name.trim()) == casefold(name)) {
            return Some(RoomIdx(k));
        }
    }
    let mut inside = rooms
        .iter()
        .enumerate()
        .filter(|(_, z)| z.contains(s.at(), MOUNT_MARGIN))
        .map(|(k, _)| RoomIdx(k));
    match (inside.next(), inside.next()) {
        (Some(k), None) => Some(k),
        _ => None,
    }
}

/// The middle of a zone's bounds.
fn middle(shape: &Shape) -> Point {
    let [x0, y0, x1, y1] = shape.bounds();
    [(x0 + x1) / 2.0, (y0 + y1) / 2.0]
}

impl Plan {
    /// The plan of a configuration (Config.rebuild, _places; World's places).
    pub fn new(config: &Config) -> Self {
        let walls = sight_walls(config);
        // the rooms of the home, as indices into the configuration's zones
        let home: Vec<usize> = (0..config.zones.len())
            .filter(|&z| config.zones[z].kind == ZoneKind::Room)
            .collect();
        let placed: Vec<&SensorConfig> = config.sensors.iter().filter(|s| s.in_use()).collect();
        let shares: Vec<f64> = home
            .iter()
            .map(|&z| seen_share(&config.zones[z], &placed, &walls))
            .collect();
        let (regions, portals) = if placed.is_empty() || home.is_empty() {
            (Vec::new(), Vec::new())
        } else {
            places(config, &home, &shares)
        };
        let region_of = |z: usize| {
            regions
                .iter()
                .position(|r| r.rooms.contains(&ZoneIdx(z)))
                .map(RegionIdx)
        };
        let mut rooms = Vec::new();
        let zones: Vec<Zone> = config
            .zones
            .iter()
            .enumerate()
            .map(|(k, z)| {
                let role = match z.kind {
                    ZoneKind::Room => region_of(k).map_or_else(
                        || {
                            rooms.push(ZoneIdx(k));
                            ZoneRole::Observed(RoomIdx(rooms.len() - 1))
                        },
                        ZoneRole::InRegion,
                    ),
                    ZoneKind::OutsideRoom => ZoneRole::OutsideRoom,
                    ZoneKind::Area => ZoneRole::Area,
                    ZoneKind::Entry => ZoneRole::Entry,
                };
                Zone {
                    id: z.id.clone(),
                    name: z.name.clone(),
                    shape: z.shape.clone(), // the plan's own table of the zones
                    role,
                }
            })
            .collect();
        let closed = regions
            .iter()
            .filter(|r| !r.open())
            .flat_map(|r| r.rooms.iter().copied())
            .chain(
                (0..zones.len())
                    .filter(|&z| zones[z].role == ZoneRole::OutsideRoom)
                    .map(ZoneIdx),
            )
            .collect();
        let observed: Vec<&ZoneConfig> = rooms.iter().map(|&ZoneIdx(z)| &config.zones[z]).collect();
        let sensors = config
            .sensors
            .iter()
            .map(|s| SensorPlan {
                id: s.id.clone(),
                own_room: own_room(s, &observed),
                in_use: s.in_use(),
                pose: Pose::of(s),
            })
            .collect();
        let mut share_of = vec![None; zones.len()];
        for (&z, &share) in home.iter().zip(&shares) {
            share_of[z] = Some(share);
        }
        Self {
            zones,
            rooms,
            regions,
            closed,
            portals,
            sensors,
            walls,
            shares: share_of,
            dividers: pieces(config)
                .filter(|&(_, kind)| kind == WallKind::Divider)
                .map(|(piece, _)| piece)
                .collect(),
        }
    }

    /// The names of the places: "observed", the regions' ids, "outside".
    pub fn place_names(&self) -> Vec<&str> {
        std::iter::once("observed")
            .chain(self.regions.iter().map(|r| r.id.as_str()))
            .chain(["outside"])
            .collect()
    }

    /// The number of places: observed, the regions, outside.
    pub fn places(&self) -> usize {
        self.regions.len() + 2
    }

    /// An observed room's zone.
    pub fn room(&self, RoomIdx(k): RoomIdx) -> &Zone {
        &self.zones[self.rooms[k].0]
    }

    /// The observed rooms' zones, in order.
    pub fn observed(&self) -> impl Iterator<Item = &Zone> {
        self.rooms.iter().map(|&ZoneIdx(z)| &self.zones[z])
    }

    /// The areas, in the order of the configuration.
    pub fn areas(&self) -> impl Iterator<Item = &Zone> {
        self.zones.iter().filter(|z| z.role == ZoneRole::Area)
    }

    /// The zones of the home the outputs report: its rooms and areas (Config.home_zones("room", "area")).
    pub fn home(&self) -> impl Iterator<Item = &Zone> {
        self.zones
            .iter()
            .filter(|z| matches!(z.role, ZoneRole::Observed(_) | ZoneRole::InRegion(_) | ZoneRole::Area))
    }

    /// The rooms in which the sensors may see people of the home: not those of closed regions nor the outside
    /// rooms (the calibration's free space).
    pub fn open_rooms(&self) -> impl Iterator<Item = &Zone> {
        self.zones
            .iter()
            .enumerate()
            .filter(|&(k, z)| z.is_room() && !self.closed.contains(&ZoneIdx(k)))
            .map(|(_, z)| z)
    }

    /// The room (of the home or outside) a point is in: the first whose outline holds it.
    pub fn room_of(&self, p: Point) -> Option<&Zone> {
        self.zones
            .iter()
            .filter(|z| z.is_room())
            .find(|z| z.shape.contains(p, 0.0))
    }

    /// The observed room a point is in: the first whose outline holds it.
    pub fn room_at(&self, p: Point) -> Option<RoomIdx> {
        self.observed().position(|z| z.shape.contains(p, 0.0)).map(RoomIdx)
    }

    /// The place of a point: the first room (of the home or outside) holding it.
    pub fn place_at(&self, p: Point) -> Option<Place> {
        self.zones
            .iter()
            .filter(|z| z.is_room())
            .find(|z| z.shape.contains(p, 0.0))
            .and_then(Zone::place)
    }

    /// Whether a target at p of a sensor whose sight lines start at origin (u: the unit vector from the
    /// sensor to it) is a reflection (Config.hidden): outside all rooms by more than margin, inside a closed room without a sensor
    /// (the balcony: people are counted at its door), or behind a wall by more than margin - no point
    /// within margin of it is in sight, not only back along the line of sight (a sensor on a wall looks
    /// along it, MODEL.md 4.1).
    pub fn hidden(&self, origin: Point, p: Point, u: Point, margin: f64) -> bool {
        let mut rooms = self.zones.iter().filter(|z| z.is_room()).peekable();
        if rooms.peek().is_some() && !rooms.any(|z| z.shape.contains(p, margin)) {
            return true;
        }
        if self
            .closed
            .iter()
            .any(|&ZoneIdx(z)| self.zones[z].shape.contains(p, -margin))
        {
            return true;
        }
        if line_of_sight(origin, p, &self.walls) {
            return false;
        }
        let mut candidates = vec![[p[0] - u[0] * margin, p[1] - u[1] * margin]];
        for &[a, b] in &self.walls {
            let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
            let l2 = dx * dx + dy * dy;
            if l2 <= 0.0 {
                continue;
            }
            let t = (((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2).clamp(0.0, 1.0);
            let (fx, fy) = (a[0] + t * dx, a[1] + t * dy);
            if py_hypot(p[0] - fx, p[1] - fy) > margin {
                continue;
            }
            // the foot on the wall, a centimetre to the sensor's side of it
            let (nx, ny) = (-dy, dx);
            let side = (origin[0] - fx) * nx + (origin[1] - fy) * ny;
            let k = (if side >= 0.0 { 0.01 } else { -0.01 }) / l2.sqrt();
            candidates.push([fx + nx * k, fy + ny * k]);
        }
        !candidates.iter().any(|&c| line_of_sight(origin, c, &self.walls))
    }
}

/// The regions without a sensor and the ways out of the observed area (Config._places); `home` the
/// rooms of the home as indices into the configuration's zones.
fn places(config: &Config, home: &[usize], shares: &[f64]) -> (Vec<Region>, Vec<Portal>) {
    let zone = |r: usize| &config.zones[home[r]];
    let seen: Vec<bool> = shares.iter().map(|&s| s >= OBSERVED_SHARE).collect();
    let pieces: Vec<([Point; 2], WallKind)> = pieces(config).collect();
    let real = wall_lines(config);
    let normal = |[a, b]: [Point; 2]| {
        let length = py_hypot(b[0] - a[0], b[1] - a[1]);
        [-(b[1] - a[1]) / length, (b[0] - a[0]) / length]
    };
    // the rooms on both sides of a door or divider (an index into home; None: outside)
    let sides = |c: Point, piece: [Point; 2]| {
        let n = normal(piece);
        [0.4, -0.4].map(|k| (0..home.len()).position(|r| zone(r).contains([c[0] + k * n[0], c[1] + k * n[1]], 0.0)))
    };
    let mut links: Vec<Link> = Vec::new();
    for d in &config.doors {
        let centre = [d.x, d.y];
        let nearest = real.iter().fold(None, |best: Option<(f64, [Point; 2])>, &p| {
            let dist = distance_to_segment(centre, p[0], p[1]);
            if best.is_none_or(|(b, _)| dist < b) {
                Some((dist, p))
            } else {
                best
            }
        });
        if let Some((_, piece)) = nearest {
            links.push(Link {
                sides: sides(centre, piece),
                door: Some(DoorSide {
                    centre,
                    normal: normal(piece),
                }),
            });
        }
    }
    for &(piece @ [a, b], kind) in &pieces {
        if kind == WallKind::Divider {
            links.push(Link {
                sides: sides([(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0], piece),
                door: None,
            });
        }
    }
    // the rooms nobody sees, joined across doors and dividers (union-find, Python's dict order)
    let mut parent: Vec<Option<usize>> = (0..home.len()).map(|r| (!seen[r]).then_some(r)).collect();
    fn find(parent: &[Option<usize>], mut r: usize) -> usize {
        while let Some(p) = parent[r]
            && p != r
        {
            r = p;
        }
        r
    }
    for link in &links {
        if let [Some(a), Some(b)] = link.sides
            && parent[a].is_some()
            && parent[b].is_some()
        {
            let root = find(&parent, b);
            let at = find(&parent, a);
            parent[at] = Some(root);
        }
    }
    let mut groups: Vec<(usize, Vec<usize>)> = Vec::new();
    for r in (0..home.len()).filter(|&r| parent[r].is_some()) {
        let root = find(&parent, r);
        match groups.iter_mut().find(|(g, _)| *g == root) {
            Some((_, members)) => members.push(r),
            None => groups.push((root, vec![r])),
        }
    }
    let entries: Vec<&ZoneConfig> = config.zones.iter().filter(|z| z.kind == ZoneKind::Entry).collect();
    let regions: Vec<Region> = groups
        .iter()
        .map(|(root, members)| {
            let doors = links
                .iter()
                .filter(|l| match l.sides {
                    [Some(r), None] | [None, Some(r)] => members.contains(&r),
                    _ => false,
                })
                .count();
            let drawn = entries
                .iter()
                .filter(|z| members.iter().any(|&r| zone(r).contains(middle(&z.shape), 0.0)))
                .count();
            let mut rooms = members.clone();
            rooms.sort_by(|&x, &y| zone(x).id.cmp(&zone(y).id));
            Region {
                id: zone(*root).id.clone(),
                name: rooms
                    .iter()
                    .map(|&r| zone(r).name.as_str())
                    .collect::<Vec<_>>()
                    .join(" + "),
                rooms: rooms.iter().map(|&r| ZoneIdx(home[r])).collect(),
                exits: doors + drawn,
            }
        })
        .collect();
    let region_of_root = |root: usize| groups.iter().position(|(g, _)| *g == root).map(RegionIdx);
    let mut portals = Vec::new();
    for link in &links {
        let Some(door) = &link.door else { continue };
        let [a, b] = link.sides;
        let side = |r: &Option<usize>| r.is_some_and(|r| seen[r]);
        let inner: Vec<Option<usize>> = [a, b].into_iter().filter(side).collect();
        let other: Vec<Option<usize>> = [a, b].into_iter().filter(|r| !side(r)).collect();
        if inner.len() != 1 || other.len() != 1 {
            continue;
        }
        let place = match other[0] {
            None => Place::Outside,
            Some(r) => Place::Region(region_of_root(find(&parent, r)).expect("a room nobody sees is in a region")),
        };
        // a point 0.5 m into the observed room: if a sensor sees it, it sees people at the door
        let k = if a == inner[0] { 0.5 } else { -0.5 };
        let (c, n) = (door.centre, door.normal);
        portals.push(Portal {
            place,
            watch: [c[0] + k * n[0], c[1] + k * n[1]],
        });
    }
    // entry zones drawn in the observed area lead outside
    for z in &entries {
        let mut unseen = regions
            .iter()
            .flat_map(|r| &r.rooms)
            .map(|&ZoneIdx(k)| &config.zones[k]);
        if unseen.any(|r| r.contains(middle(&z.shape), 0.0)) {
            continue;
        }
        portals.push(Portal {
            place: Place::Outside,
            watch: middle(&z.shape),
        });
    }
    (regions, portals)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_remount_is_a_move_or_a_turn_not_a_new_scale_or_mirror() {
        let p = Pose {
            x: 1.0,
            y: 2.0,
            heading: 350.0,
            height: 2.0,
            mirror: false,
            scale: 1.0,
        };
        let read_anew = Pose {
            mirror: true,
            scale: 1.1,
            heading: 15.0, // 25 degrees across north
            ..p
        };
        assert!(!read_anew.remounted(&p));
        assert!(Pose { heading: 25.0, ..p }.remounted(&p));
        assert!(Pose { x: 1.4, y: 2.4, ..p }.remounted(&p));
        assert!(
            !Pose {
                x: 1.3,
                height: 2.3,
                ..p
            }
            .remounted(&p)
        );
    }

    use crate::testing::{config, wall};

    /// The living room (6 x 5 m) with its sensor in a corner; right of it a bath (closed: one door,
    /// into the living room) and a bedroom (a door from the living room and one out to the stairwell),
    /// a wall without a door between them.
    fn flat() -> Config {
        let walls = [
            wall([0.0, 0.0], [11.0, 0.0]),
            wall([11.0, 0.0], [11.0, 5.0]),
            wall([11.0, 5.0], [0.0, 5.0]),
            wall([0.0, 5.0], [0.0, 0.0]),
            wall([6.0, 0.0], [6.0, 5.0]),
            wall([9.0, 0.0], [9.0, 5.0]),
            wall([6.0, 3.0], [9.0, 3.0]),
        ];
        let room = |id: &str, x0: f64, y0: f64, x1: f64, y1: f64| {
            format!(
                r#"{{"id": "{id}", "name": "{id}", "kind": "room", "shape": "rect", "points": [[{x0}, {y0}], [{x1}, {y1}]]}}"#
            )
        };
        let stair = r#"{"id": "stair", "name": "stair", "kind": "room", "entry": true, "shape": "rect", "points": [[9, 0], [11, 5]]}"#;
        let door = |id: &str, x: f64, y: f64| format!(r#"{{"id": "{id}", "x": {x}, "y": {y}, "width": 0.9}}"#);
        config(
            &[("w", "wohn", [0.05, 0.05], 45.0)],
            &format!(
                "[{}, {}, {}, {stair}]",
                room("wohn", 0.0, 0.0, 6.0, 5.0),
                room("bad", 6.0, 0.0, 9.0, 3.0),
                room("schlaf", 6.0, 3.0, 9.0, 5.0)
            ),
            &format!("[{}]", walls.join(", ")),
            &format!(
                "[{}, {}, {}]",
                door("b", 6.0, 1.5),
                door("s", 6.0, 4.0),
                door("o", 9.0, 4.0)
            ),
        )
    }

    #[test]
    fn the_places_of_a_plan_worked_out_by_hand() {
        let plan = Plan::new(&flat());
        // wohn is seen (>= 60 % of its points), the others not: two regions, the bath closed
        let observed: Vec<&str> = plan.observed().map(|z| z.id.as_str()).collect();
        assert_eq!(observed, ["wohn"]);
        let regions: Vec<(&str, usize)> = plan.regions.iter().map(|r| (r.id.as_str(), r.exits)).collect();
        assert_eq!(regions, [("bad", 0), ("schlaf", 1)]);
        assert!(!plan.regions[0].open() && plan.regions[1].open());
        let closed: Vec<&str> = plan.closed.iter().map(|z| plan.zones[z.0].id.as_str()).collect();
        assert_eq!(closed, ["bad", "stair"]);
        assert_eq!(plan.places(), 4);
        // the doors out of the observed area: 0.5 m into the living room
        let portals: Vec<(Place, Point)> = plan.portals.iter().map(|p| (p.place, p.watch)).collect();
        assert_eq!(
            portals,
            [
                (Place::Region(RegionIdx(0)), [5.5, 1.5]),
                (Place::Region(RegionIdx(1)), [5.5, 4.0])
            ]
        );
        // the walls a person meets: none through the door to the bath, one beside it
        let walls = crate::geometry::Walls::new(
            plan.walls
                .iter()
                .map(|w| [w[0][0], w[0][1], w[1][0], w[1][1]])
                .collect(),
        );
        assert_eq!(
            walls.crosses(&[[5.8, 1.5], [5.8, 0.5]], &[[6.2, 1.5], [6.2, 0.5]]),
            [false, true]
        );
        // the sensor named like the room is its own; every room has its place
        assert_eq!(plan.sensors[0].own_room, Some(RoomIdx(0)));
        let place = |p: Point| plan.place_at(p);
        assert_eq!(place([1.0, 1.0]), Some(Place::Observed));
        assert_eq!(place([7.0, 1.0]), Some(Place::Region(RegionIdx(0))));
        assert_eq!(place([10.0, 1.0]), Some(Place::Outside));
        assert_eq!(place([12.0, 1.0]), None);
        assert_eq!(Place::of_index(Place::Outside.index(2), 2), Some(Place::Outside));
        // a reflection: behind the wall in the bath, not in the living room
        let s = &flat().sensors[0];
        let unit = |p: Point| {
            let d = py_hypot(p[0] - s.x, p[1] - s.y);
            [(p[0] - s.x) / d, (p[1] - s.y) / d]
        };
        assert!(plan.hidden(s.sight_origin(), [7.5, 1.0], unit([7.5, 1.0]), 0.4));
        assert!(!plan.hidden(s.sight_origin(), [3.0, 3.0], unit([3.0, 3.0]), 0.4));
    }

    #[test]
    fn a_configuration_that_does_not_fit_is_refused_by_name() {
        let good = crate::testing::config(&[("w", "w", [0.0, 0.0], 0.0)], "[]", "[]", "[]");
        assert_eq!(good.sensors.len(), 1);
        let json = |sensor: &str, params: &str, zones: &str, view: &str| {
            format!(
                r#"{{"sensors": [{sensor}], "zones": {zones}, "walls": [], "doors": [], "ld_view": {view}, "params": {params}}}"#
            )
        };
        let params = &crate::testing::params_json();
        for (sensor, params, zones, view, name) in [
            (
                r#"{"id": "s", "name": "", "x": 0, "y": 0, "heading": 0, "height": 1.5, "mirror": false, "scale": 1, "fov": 120, "range": 0, "enabled": true, "placed": true}"#,
                params.to_string(),
                "[]",
                "{}",
                "range",
            ),
            (
                r#"{"id": "s", "name": "", "x": 0, "y": 0, "heading": 0, "height": 1.5, "mirror": false, "scale": 1, "fov": 120, "range": 6, "enabled": true, "placed": true}"#,
                params.replace(r#""range_sigma_base": 0.15"#, r#""range_sigma_base": 0"#),
                "[]",
                "{}",
                "range_sigma_base",
            ),
            (
                r#"{"id": "s", "name": "", "x": 0, "y": 0, "heading": 0, "height": 1.5, "mirror": false, "scale": 1, "fov": 120, "range": 6, "enabled": true, "placed": true}"#,
                params.replace(r#""target_threshold": 0.8"#, r#""target_threshold": 1.0"#),
                "[]",
                "{}",
                "target_threshold",
            ),
            (
                r#"{"id": "s", "name": "", "x": 0, "y": 0, "heading": 0, "height": 1.5, "mirror": false, "scale": 1, "fov": 120, "range": 6, "enabled": true, "placed": true}"#,
                params.to_string(),
                r#"[{"id": "r", "name": "R", "kind": "room", "shape": "rect", "points": [[0, 0], [1000000, 5]]}]"#,
                "{}",
                "extent",
            ),
            (
                r#"{"id": "s", "name": "", "x": 0, "y": 0, "heading": 0, "height": 1.5, "mirror": false, "scale": 1, "fov": 120, "range": 6, "enabled": true, "placed": true}"#,
                params.to_string(),
                "[]",
                r#"{"a<-b": -0.5}"#,
                "ld_view",
            ),
        ] {
            let err = Config::from_json(&json(sensor, &params, zones, view))
                .expect_err(name)
                .to_string();
            assert!(err.contains(name), "{err}");
        }
        // a field the shell always writes is required
        let missing = json(r#"{"id": "s", "x": 0}"#, params, "[]", "{}");
        assert!(Config::from_json(&missing).is_err());
    }
}
