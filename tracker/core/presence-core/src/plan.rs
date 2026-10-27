//! The plan as the people model sees it (MODEL.md 2), derived from the configuration, never stored
//! (model.Config._places, floorplan.sight_segments, roomseen.own_room): the places - the observed
//! area, the regions without a sensor behind doors, outside -, the doors out of the observed area,
//! the walls the radar does not see through, which room is whose sensor's, and the rule which
//! detections are reflections.

use crate::config::{Config, SensorConfig, WallKind, ZoneConfig, ZoneKind};
use crate::geometry::{Point, Shape, distance_to_segment, line_of_sight};
use crate::vmath::{py_dist, py_hypot};

/// m: a door belongs to a wall piece this close to its centre (floorplan.DOOR_REACH)
const DOOR_REACH: f64 = 0.25;
/// m: a sensor hangs on a wall, its point lies at the room's outline (roomseen.MOUNT_MARGIN)
const MOUNT_MARGIN: f64 = 0.3;

/// A room the sensors see, or a zone of the outputs.
#[derive(Debug)]
pub struct Room {
    pub id: String,
    pub shape: Shape,
}

/// A region without a sensor (Config.regions): rooms connected by doors. Open if it has ways out of
/// the house (doors to the outside, entry zones drawn in it): whoever comes out of a closed one went in
/// before.
#[derive(Debug)]
pub struct Region {
    /// the id of its first room (Config.regions' key)
    pub id: String,
    pub name: String,
    pub rooms: Vec<String>,
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
    /// the place it leads to (a region's, or outside)
    pub place: usize,
    pub watch: Point,
}

/// A sensor as the plan knows it: its own room (an index into the observed rooms) and whether it is
/// in use (enabled and placed).
#[derive(Debug)]
pub struct SensorPlan {
    pub id: String,
    pub own_room: Option<usize>,
    pub in_use: bool,
}

/// A zone of the home the outputs report (Config.home_zones("room", "area")).
#[derive(Debug)]
pub struct HomeZone {
    pub id: String,
    pub kind: ZoneKind,
    pub shape: Shape,
    /// the region a room without a sensor belongs to (an index into regions)
    pub region: Option<usize>,
}

#[derive(Debug)]
pub struct Plan {
    /// the walls that block the radar's sight and the people: real walls without their door openings
    pub walls: Vec<[Point; 2]>,
    pub regions: Vec<Region>,
    /// the doors and entries out of the observed area
    pub portals: Vec<Portal>,
    /// the rooms the sensors see (Config.observed_rooms), in the order of the configuration
    pub rooms: Vec<Room>,
    /// the rooms in which the sensors see nobody of the home: those of closed regions, and the
    /// outside rooms (Config.closed_rooms)
    pub closed_rooms: Vec<Shape>,
    /// every room, the outside rooms too: (shape, its place) - for the raster of places
    pub all_rooms: Vec<(Shape, usize)>,
    pub sensors: Vec<SensorPlan>,
    pub zones: Vec<HomeZone>,
    /// the areas (Config.home_zones("area")): with the observed rooms the zones of "wird betreten"
    pub areas: Vec<Shape>,
}

/// floorplan.wall_pieces: every wall longer than 1 µm.
fn pieces(config: &Config) -> impl Iterator<Item = ([Point; 2], WallKind)> + '_ {
    config
        .walls
        .iter()
        .filter(|w| py_dist(w.a, w.b) > 1e-6)
        .map(|w| ([w.a, w.b], w.kind))
}

/// The walls that block the radar: real walls without their door openings (floorplan.sight_segments).
fn sight_walls(config: &Config) -> Vec<[Point; 2]> {
    let walls: Vec<[Point; 2]> = pieces(config)
        .filter(|&(_, k)| k == WallKind::Wall)
        .map(|(p, _)| p)
        .collect();
    // per piece the openings of its doors (door_openings), along it in metres
    let mut cuts: Vec<Vec<(f64, f64)>> = vec![Vec::new(); walls.len()];
    for d in &config.doors {
        let c = [d.x, d.y];
        let mut best: Option<(f64, usize)> = None;
        for (i, &[a, b]) in walls.iter().enumerate() {
            let dist = distance_to_segment(c, a, b);
            if dist < DOOR_REACH && best.is_none_or(|(d0, _)| dist < d0) {
                best = Some((dist, i));
            }
        }
        if let Some((_, i)) = best {
            let [a, b] = walls[i];
            let length = py_dist(a, b);
            let t = ((c[0] - a[0]) * (b[0] - a[0]) + (c[1] - a[1]) * (b[1] - a[1])) / length;
            let half = d.width / 2.0;
            cuts[i].push(((t - half).max(0.0), (t + half).min(length)));
        }
    }
    let mut out = Vec::new();
    for (&[a, b], cuts) in walls.iter().zip(&mut cuts) {
        let length = py_dist(a, b);
        let u = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
        let along = |t: f64| [a[0] + u[0] * t, a[1] + u[1] * t];
        let mut start = 0.0;
        cuts.sort_by(|x, y| x.0.total_cmp(&y.0).then(x.1.total_cmp(&y.1)));
        for &(t0, t1) in cuts.iter() {
            if t0 > start {
                out.push([along(start), along(t0)]);
            }
            start = f64::max(start, t1);
        }
        if start < length {
            out.push([along(start), b]);
        }
    }
    out
}

/// Sensors see most of the room: at least 60 % of the points of a 0.4 m raster in it (Config.
/// _room_covered). Seeing a part through a door (31 % of the balcony through the glass door) does not
/// make it observed.
fn covered(room: &ZoneConfig, sensors: &[&SensorConfig], walls: &[[Point; 2]]) -> bool {
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
    n > 0 && seen as f64 / n as f64 >= 0.6
}

/// Python's str.casefold for the names here (lower case, ß as ss).
fn casefold(s: &str) -> String {
    s.to_lowercase().replace('ß', "ss")
}

/// The room of a sensor among the observed rooms (roomseen.own_room): named like the room's id, else
/// like its name, else the one it is mounted in (within MOUNT_MARGIN), if exactly one.
fn own_room(s: &SensorConfig, rooms: &[&ZoneConfig]) -> Option<usize> {
    let name = s.name.trim();
    if !name.is_empty() {
        if let Some(k) = rooms.iter().position(|z| z.id == name) {
            return Some(k);
        }
        if let Some(k) = rooms.iter().position(|z| casefold(z.name.trim()) == casefold(name)) {
            return Some(k);
        }
    }
    let mut inside = rooms
        .iter()
        .enumerate()
        .filter(|(_, z)| z.contains(s.at(), MOUNT_MARGIN))
        .map(|(k, _)| k);
    match (inside.next(), inside.next()) {
        (Some(k), None) => Some(k),
        _ => None,
    }
}

/// The middle of a zone's bounds.
fn middle(z: &ZoneConfig) -> Point {
    let [x0, y0, x1, y1] = z.shape.bounds();
    [(x0 + x1) / 2.0, (y0 + y1) / 2.0]
}

impl Plan {
    /// The plan of a configuration (Config.rebuild, _places; World's places).
    pub fn new(config: &Config) -> Self {
        let walls = sight_walls(config);
        let rooms: Vec<&ZoneConfig> = config.rooms().filter(|z| !z.entry).collect();
        let placed: Vec<&SensorConfig> = config.sensors.iter().filter(|s| s.in_use()).collect();
        let (regions, portals) = if placed.is_empty() || rooms.is_empty() {
            (Vec::new(), Vec::new())
        } else {
            places(config, &rooms, &placed, &walls)
        };
        let region_of = |id: &str| regions.iter().position(|r| r.rooms.iter().any(|x| x == id));
        let outside = regions.len() + 1;
        let observed: Vec<&ZoneConfig> = config
            .home_zones(&[ZoneKind::Room])
            .filter(|z| region_of(&z.id).is_none())
            .collect();
        let closed_rooms = regions
            .iter()
            .filter(|r| !r.open())
            .flat_map(|r| r.rooms.iter())
            .filter_map(|id| rooms.iter().find(|z| &z.id == id))
            .map(|z| z.shape.clone())
            .chain(config.rooms().filter(|z| z.entry).map(|z| z.shape.clone()))
            .collect();
        let all_rooms = config
            .rooms()
            .map(|z| {
                let place = if z.entry {
                    outside
                } else {
                    region_of(&z.id).map_or(0, |r| r + 1)
                };
                (z.shape.clone(), place)
            })
            .collect();
        let sensors = config
            .sensors
            .iter()
            .map(|s| SensorPlan {
                id: s.id.clone(),
                own_room: own_room(s, &observed),
                in_use: s.in_use(),
            })
            .collect();
        let zones = config
            .home_zones(&[ZoneKind::Room, ZoneKind::Area])
            .map(|z| HomeZone {
                id: z.id.clone(),
                kind: z.kind,
                shape: z.shape.clone(),
                region: region_of(&z.id),
            })
            .collect();
        Self {
            walls,
            portals,
            closed_rooms,
            all_rooms,
            sensors,
            zones,
            areas: config.home_zones(&[ZoneKind::Area]).map(|z| z.shape.clone()).collect(),
            rooms: observed
                .iter()
                .map(|z| Room {
                    id: z.id.clone(),
                    shape: z.shape.clone(),
                })
                .collect(),
            regions,
        }
    }

    /// The number of places: observed, the regions, outside.
    pub fn places(&self) -> usize {
        self.regions.len() + 2
    }

    /// The index of outside among the places.
    pub fn outside(&self) -> usize {
        self.regions.len() + 1
    }

    /// The observed room a point is in: the first whose outline holds it (roomseen.RoomSeen.frame).
    pub fn room_at(&self, p: Point) -> Option<usize> {
        self.rooms.iter().position(|room| room.shape.contains(p, 0.0))
    }

    /// Whether a target of sensor s at p (u: the unit vector from the sensor to it) is a reflection
    /// (Config.hidden): outside all rooms by more than margin, inside a closed room without a sensor
    /// (the balcony: people are counted at its door), or behind a wall by more than margin - no point
    /// within margin of it is in sight, not only back along the line of sight (a sensor on a wall looks
    /// along it, MODEL.md 4.1).
    pub fn hidden(&self, config: &Config, s: &SensorConfig, p: Point, u: Point, margin: f64) -> bool {
        let mut rooms = config.rooms().peekable();
        if rooms.peek().is_some() && !rooms.any(|z| z.contains(p, margin)) {
            return true;
        }
        if self.closed_rooms.iter().any(|z| z.contains(p, -margin)) {
            return true;
        }
        let o = s.sight_origin();
        if line_of_sight(o, p, &self.walls) {
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
            let side = (o[0] - fx) * nx + (o[1] - fy) * ny;
            let k = (if side >= 0.0 { 0.01 } else { -0.01 }) / l2.sqrt();
            candidates.push([fx + nx * k, fy + ny * k]);
        }
        !candidates.iter().any(|&c| line_of_sight(o, c, &self.walls))
    }
}

/// A door or divider: the rooms on its sides (an index into the rooms; None: outside), and for a door
/// its centre, width and its wall's normal.
type Link = ([Option<usize>; 2], Option<(Point, f64, Point)>);

/// The regions without a sensor and the ways out of the observed area (Config._places).
fn places(
    config: &Config,
    rooms: &[&ZoneConfig],
    placed: &[&SensorConfig],
    walls: &[[Point; 2]],
) -> (Vec<Region>, Vec<Portal>) {
    let seen: Vec<bool> = rooms.iter().map(|z| covered(z, placed, walls)).collect();
    let pieces: Vec<([Point; 2], WallKind)> = pieces(config).collect();
    let real: Vec<[Point; 2]> = pieces
        .iter()
        .filter(|&&(_, k)| k == WallKind::Wall)
        .map(|&(p, _)| p)
        .collect();
    let normal = |[a, b]: [Point; 2]| {
        let length = py_hypot(b[0] - a[0], b[1] - a[1]);
        [-(b[1] - a[1]) / length, (b[0] - a[0]) / length]
    };
    // the rooms on both sides of a door or divider (an index into rooms; None: outside)
    let sides = |c: Point, piece: [Point; 2]| {
        let n = normal(piece);
        [0.4, -0.4].map(|k| {
            rooms
                .iter()
                .position(|z| z.contains([c[0] + k * n[0], c[1] + k * n[1]], 0.0))
        })
    };
    // (sides, the door and its wall's normal; None: a divider)
    let mut links: Vec<Link> = Vec::new();
    for d in &config.doors {
        let c = [d.x, d.y];
        let nearest = real.iter().fold(None, |best: Option<(f64, [Point; 2])>, &p| {
            let dist = distance_to_segment(c, p[0], p[1]);
            if best.is_none_or(|(b, _)| dist < b) {
                Some((dist, p))
            } else {
                best
            }
        });
        if let Some((_, piece)) = nearest {
            links.push((sides(c, piece), Some((c, d.width, normal(piece)))));
        }
    }
    for &(piece @ [a, b], kind) in &pieces {
        if kind == WallKind::Divider {
            links.push((sides([(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0], piece), None));
        }
    }
    // the rooms nobody sees, joined across doors and dividers (union-find, Python's dict order)
    let mut parent: Vec<Option<usize>> = (0..rooms.len()).map(|k| (!seen[k]).then_some(k)).collect();
    fn find(parent: &[Option<usize>], mut r: usize) -> usize {
        while let Some(p) = parent[r]
            && p != r
        {
            r = p;
        }
        r
    }
    for ([a, b], _) in &links {
        if let (Some(a), Some(b)) = (*a, *b)
            && parent[a].is_some()
            && parent[b].is_some()
        {
            let root = find(&parent, b);
            let at = find(&parent, a);
            parent[at] = Some(root);
        }
    }
    let mut groups: Vec<(usize, Vec<usize>)> = Vec::new();
    for r in (0..rooms.len()).filter(|&r| parent[r].is_some()) {
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
                .filter(|([a, b], _)| a.is_none() != b.is_none() && members.contains(&a.or(*b).expect("one side")))
                .count();
            let drawn = entries
                .iter()
                .filter(|z| members.iter().any(|&r| rooms[r].contains(middle(z), 0.0)))
                .count();
            let mut ids: Vec<&ZoneConfig> = members.iter().map(|&r| rooms[r]).collect();
            ids.sort_by(|x, y| x.id.cmp(&y.id));
            Region {
                id: rooms[*root].id.clone(),
                name: ids.iter().map(|z| z.name.as_str()).collect::<Vec<_>>().join(" + "),
                rooms: ids.iter().map(|z| z.id.clone()).collect(),
                exits: doors + drawn,
            }
        })
        .collect();
    let region_of_root = |root: usize| groups.iter().position(|(g, _)| *g == root);
    let mut portals = Vec::new();
    for ([a, b], door) in &links {
        let Some((c, _, n)) = door else { continue };
        let side = |r: &Option<usize>| r.is_some_and(|r| seen[r]);
        let inner: Vec<Option<usize>> = [*a, *b].into_iter().filter(side).collect();
        let other: Vec<Option<usize>> = [*a, *b].into_iter().filter(|r| !side(r)).collect();
        if inner.len() != 1 || other.len() != 1 {
            continue;
        }
        let place = match other[0] {
            None => regions.len() + 1,
            Some(r) => region_of_root(find(&parent, r)).expect("a room nobody sees is in a region") + 1,
        };
        // a point 0.5 m into the observed room: if a sensor sees it, it sees people at the door
        let k = if *a == inner[0] { 0.5 } else { -0.5 };
        portals.push(Portal {
            place,
            watch: [c[0] + k * n[0], c[1] + k * n[1]],
        });
    }
    // entry zones drawn in the observed area lead outside
    for z in &entries {
        let unseen = regions
            .iter()
            .flat_map(|r| &r.rooms)
            .filter_map(|id| rooms.iter().find(|x| &x.id == id));
        if unseen.into_iter().any(|r| r.contains(middle(z), 0.0)) {
            continue;
        }
        portals.push(Portal {
            place: regions.len() + 1,
            watch: middle(z),
        });
    }
    (regions, portals)
}
