//! Where a walker goes next ("Ziel", MODEL.md 6): a map, learned by the app, of where
//! the walks went from each place, in each direction and at each speed - per cell (CELL m squares split
//! at the rooms), per velocity cell (SECTORS directions x the speed classes around SPEED_MID) and per
//! outcome (the room behind the first door the walk goes through, a region, out of the house, or
//! "stays") the number of walks (J_Luber2014 ch. 6, sec. 6.5; G_Liao2003). Each step of a walk counts
//! with a Gaussian kernel on the cells around it, within its own space (rooms joined by dividers) and
//! not across a wall, normalized so that one walk straight through a cell counts 1 there. Learned from
//! the model's own walkers (Learner); an output only, it never changes the filter. Counts fade with
//! FORGET; the map belongs to its floor plan (Geometry fingerprint).
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use std::collections::{BTreeMap, VecDeque};
use std::f64::consts::PI;
use std::sync::LazyLock;

use serde_json::{Value, json};

use super::{LearnedError, PackedFault};
use crate::filter::Filter;
use crate::filter::holders::Obj;
use crate::filter::hyp::Owner;
use crate::filter::mixture::{MixtureId, SegId};
use crate::formats::{self, Fingerprint, field, number, object, round_numpy};
use crate::geometry::{Point, Walls};
use crate::params::WALK;
use crate::plan::{Place, Plan, RoomIdx};
use crate::vmath::{pairwise_sum, py_hypot};
use crate::world::World;
use crate::world::raster::{self, Raster, RoomGrid};

/// m: side of a cell of the map
const CELL: f64 = 0.3;
/// directions
const SECTORS: usize = 16;
/// m/s: centres of the speed classes (interpolated between, vel_interp)
const SPEED_MID: [f64; 3] = [0.5, 0.9, 1.35];
/// velocity cells
const V: usize = SECTORS * SPEED_MID.len();
/// m, m/s: the kernel (the investigation's kernel map)
const BW_POS: f64 = 0.3;
const BW_VEL: f64 = 0.3;
/// m/s: slower walkers have no direction worth learning or asking
const MIN_SPEED: f64 = 0.3;
/// s standing (P(walking) < 1/2): the walk ended in the room
const STOP_TIME: f64 = 2.0;
/// steps in the room behind a door: the walk went there
const ARRIVE: u32 = 2;
/// s, m: a walk whose tracks end is taken over by a walker appearing this soon and near
const LINK_TIME: f64 = 1.5;
const LINK_DIST: f64 = 1.0;
/// s: a walk's steps older than this are dropped (walks last 2.4 s on average)
const MAX_PENDING: f64 = 60.0;
/// a walker with less weight (hypothesis x P(walking)) is not counted
const MIN_WEIGHT: f64 = 0.02;
pub const FORGET: f64 = 14.0 * 86400.0;
const DECAY_EVERY: f64 = 60.0;
/// m: the kernel reaches this far
const NEAR: f64 = 2.5 * BW_POS;
/// m: asked, the map is averaged over the cells around the place with this spread
const QUERY: f64 = 0.5 * CELL;

/// The centres of the velocity cells, speed class major.
static VELOCITIES: LazyLock<[Point; V]> = LazyLock::new(|| {
    std::array::from_fn(|k| {
        let (s, a) = (SPEED_MID[k / SECTORS], 2.0 * PI * (k % SECTORS) as f64 / SECTORS as f64);
        [s * a.cos(), s * a.sin()]
    })
});

/// Velocity cells with weights summing to 1: linear interpolation over direction and speed.
fn vel_interp([vx, vy]: Point) -> ([usize; 4], [f64; 4]) {
    let s = py_hypot(vx, vy);
    let a = (vy.atan2(vx) / (2.0 * PI / SECTORS as f64)).rem_euclid(SECTORS as f64);
    let s0 = a.floor();
    let fa = a - s0;
    let secs = [s0 as usize % SECTORS, (s0 as usize + 1) % SECTORS];
    let last = SPEED_MID.len() - 1;
    let (ks, fs) = if !(s > SPEED_MID[0]) {
        ([0, 0], 0.0) // also a speed that is no number
    } else if s >= SPEED_MID[last] {
        ([last, last], 0.0)
    } else {
        let k = SPEED_MID.partition_point(|&m| m <= s) - 1; // the last at most s: one is
        ([k, k + 1], (s - SPEED_MID[k]) / (SPEED_MID[k + 1] - SPEED_MID[k]))
    };
    let cells = [
        ks[0] * SECTORS + secs[0],
        ks[0] * SECTORS + secs[1],
        ks[1] * SECTORS + secs[0],
        ks[1] * SECTORS + secs[1],
    ];
    (
        cells,
        [(1.0 - fs) * (1.0 - fa), (1.0 - fs) * fa, fs * (1.0 - fa), fs * fa],
    )
}

/// The velocity cells with the kernel's weight above 0.02.
fn vel_weights(v: Point) -> Vec<(usize, f64)> {
    VELOCITIES
        .iter()
        .enumerate()
        .map(|(k, c)| {
            (
                k,
                (-0.5 * ((c[0] - v[0]).powi(2) + (c[1] - v[1]).powi(2)) / BW_VEL.powi(2)).exp(),
            )
        })
        .filter(|&(_, w)| w > 0.02)
        .collect()
}

// ------------------------------------------------------------------ the geometry

/// A cell of the map: its centre and its observed room.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DestCell {
    pub centre: Point,
    pub room: RoomIdx,
}

/// The map's cells and outcomes for a floor plan, part of the world. Outcomes:
/// the observed rooms, the regions, out of the house, stays.
#[derive(Debug)]
pub struct DestGeometry {
    pub cells: Vec<DestCell>,
    /// per observed room its space: rooms joined by a divider are one
    space: Vec<usize>,
    /// per cell itself and its neighbours for the kernel: same space, within NEAR, no wall between
    neigh: Vec<Vec<usize>>,
    raster: Raster,
    /// per 0.1 m cell in view its cell of the map
    cell_of_fine: Vec<Option<usize>>,
    /// per space what a walk in it can end in
    allowed: Vec<Vec<bool>>,
    /// per (space, outcome) where its doors are
    doors: BTreeMap<(usize, usize), Vec<Point>>,
    /// the outcomes' names
    names: Vec<String>,
    fingerprint: String,
}

impl DestGeometry {
    pub fn new(plan: &Plan, raster: &Raster, labels: &[Option<Place>], rooms: &RoomGrid, walls: &Walls) -> Self {
        let k = plan.rooms.len();
        let r = plan.regions.len();
        let space = spaces(plan, rooms);
        let fine: Vec<(usize, usize, RoomIdx)> = (0..raster.nx)
            .flat_map(|i| (0..raster.ny).map(move |j| (i, j)))
            .filter_map(|(i, j)| rooms.cells()[i * raster.ny + j].map(|room| (i, j, room)))
            .collect();
        let (cells, cell_of_fine) = map_cells(raster, &fine);
        let neigh = (0..cells.len()).map(|c| neighbours(&cells, &space, walls, c)).collect();
        // outcomes: observed rooms, regions, outside, stays
        let stay = k + r + 1;
        let mut allowed = vec![vec![false; k + r + 2]; k];
        for a in &mut allowed {
            a[stay] = true;
        }
        let mut doors: BTreeMap<(usize, usize), Vec<Point>> = BTreeMap::new();
        let other = |p: Option<Place>, room: Option<RoomIdx>| match (room, p) {
            (Some(RoomIdx(b)), _) => Some(b),
            (None, Some(Place::Region(rg))) => Some(k + rg.0),
            (None, Some(Place::Outside)) => Some(k + r),
            _ => None,
        };
        for (di, dj) in [(1i64, 0i64), (-1, 0), (0, 1), (0, -1)] {
            let mut moves: Vec<(usize, usize, Point, Point)> = Vec::new();
            for &(i, j, RoomIdx(a)) in &fine {
                let (ti, tj) = (i as i64 + di, j as i64 + dj);
                if ti < 0 || tj < 0 || ti >= raster.nx as i64 || tj >= raster.ny as i64 {
                    continue;
                }
                let t = ti as usize * raster.ny + tj as usize;
                let b_room = rooms.cells()[t];
                let Some(o) = other(labels[t], b_room) else { continue };
                if b_room.is_some_and(|RoomIdx(b)| space[b] == space[a]) {
                    continue;
                }
                let p0 = raster.centre(i, j);
                let p1 = [p0[0] + di as f64 * raster::CELL, p0[1] + dj as f64 * raster::CELL];
                moves.push((space[a], o, p0, p1));
            }
            let p0: Vec<Point> = moves.iter().map(|m| m.2).collect();
            let p1: Vec<Point> = moves.iter().map(|m| m.3).collect();
            for (&(s, o, a, b), crosses) in moves.iter().zip(walls.crosses(&p0, &p1)) {
                if !crosses {
                    allowed[s][o] = true;
                    doors
                        .entry((s, o))
                        .or_default()
                        .push([(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0]);
                }
            }
        }
        let names = plan
            .rooms
            .iter()
            .map(|z| plan.zones[z.0].id.clone())
            .chain(plan.regions.iter().map(|rg| rg.id.clone()))
            .chain(["outside".to_string(), "stays".to_string()])
            .collect();
        let mut geo = Self {
            cells,
            space,
            neigh,
            raster: *raster,
            cell_of_fine,
            allowed,
            doors,
            names,
            fingerprint: String::new(),
        };
        geo.fingerprint = geo.fingerprint_of();
        geo
    }

    fn outcomes(&self) -> usize {
        self.names.len()
    }

    fn stay(&self) -> usize {
        self.names.len() - 1
    }

    /// What a map over this geometry depends on (Geometry.fingerprint): Python's string exactly.
    fn fingerprint_of(&self) -> String {
        let mut h = Fingerprint::default();
        h.floats(self.cells.iter().flat_map(|c| c.centre.map(|v| round_numpy(v, 3))))
            .floats(self.cells.iter().map(|c| c.room.0 as f64))
            .floats(self.space.iter().map(|&s| s as f64))
            .floats(self.allowed.iter().flatten().map(|&a| if a { 1.0 } else { 0.0 }))
            .floats(VELOCITIES.iter().flatten().copied());
        for (&(s, o), pts) in &self.doors {
            h.text(&format!("({s}, {o})"))
                .floats(pts.iter().flat_map(|p| p.map(|v| round_numpy(v, 2))));
        }
        h.text(&self.names.join("|"));
        h.hex()
    }

    /// The cell of the map at a point (None: not in view).
    fn cell(&self, p: Point) -> Option<usize> {
        self.cell_of_fine[self.raster.cell_of(p)]
    }

    /// The cells to ask the map at a point, weights summing to 1: the cell there and its neighbours with
    /// a Gaussian of QUERY m (Geometry.around).
    fn around(&self, p: Point) -> Option<(&[usize], Vec<f64>)> {
        let cells = &self.neigh[self.cell(p)?];
        let w: Vec<f64> = cells.iter().map(|&c| kernel(self.cells[c].centre, p, QUERY)).collect();
        let total = pairwise_sum(&w);
        Some((cells, w.iter().map(|x| x / total).collect()))
    }

    /// Straight distance from a point to the nearest door of the space to an outcome.
    fn door_distance(&self, space: usize, outcome: usize, p: Point) -> Option<f64> {
        let pts = self.doors.get(&(space, outcome))?;
        pts.iter()
            .map(|d| ((d[0] - p[0]).powi(2) + (d[1] - p[1]).powi(2)).sqrt())
            .reduce(f64::min)
    }
}

/// exp(-|a - p|^2 / (2 bw^2))
fn kernel(a: Point, p: Point, bw: f64) -> f64 {
    (-0.5 * ((a[0] - p[0]).powi(2) + (a[1] - p[1]).powi(2)) / bw.powi(2)).exp()
}

/// Per observed room its space: rooms on the two sides of a divider are one (union-find).
fn spaces(plan: &Plan, rooms: &RoomGrid) -> Vec<usize> {
    let mut parent: Vec<usize> = (0..plan.rooms.len()).collect();
    fn find(parent: &[usize], mut a: usize) -> usize {
        while parent[a] != a {
            a = parent[a];
        }
        a
    }
    for &[[ax, ay], [bx, by]] in &plan.dividers {
        let ln = py_hypot(bx - ax, by - ay);
        let (nx, ny) = (-(by - ay) / ln, (bx - ax) / ln);
        let (cx, cy) = ((ax + bx) / 2.0, (ay + by) / 2.0);
        let side = [0.25, -0.25].map(|s| rooms.at([cx + s * nx, cy + s * ny]));
        if let [Some(RoomIdx(a)), Some(RoomIdx(b))] = side
            && a != b
        {
            let fa = find(&parent, a);
            parent[fa] = find(&parent, b);
        }
    }
    (0..parent.len()).map(|k| find(&parent, k)).collect()
}

/// The cells of the map: the 0.1 m cells in view grouped by (block i, block j, room) in the order of
/// those keys (np.unique), each at the mean of its fine cells' centres; per fine cell its cell.
fn map_cells(raster: &Raster, fine: &[(usize, usize, RoomIdx)]) -> (Vec<DestCell>, Vec<Option<usize>>) {
    let b = ((CELL / raster::CELL).round() as usize).max(1);
    let keys: Vec<(usize, usize, usize)> = fine.iter().map(|&(i, j, r)| (i / b, j / b, r.0)).collect();
    let mut uniq = keys.clone();
    uniq.sort_unstable();
    uniq.dedup();
    let index: Vec<usize> = keys.iter().map(|k| uniq.binary_search(k).expect("a key")).collect();
    let mut sum = vec![[0.0; 2]; uniq.len()];
    let mut count = vec![0.0; uniq.len()];
    let mut cell_of_fine = vec![None; raster.cells()];
    for (&(i, j, _), &c) in fine.iter().zip(&index) {
        let p = raster.centre(i, j);
        sum[c][0] += p[0];
        sum[c][1] += p[1];
        count[c] += 1.0;
        cell_of_fine[i * raster.ny + j] = Some(c);
    }
    let cells = uniq
        .iter()
        .zip(sum.iter().zip(&count))
        .map(|(&(_, _, room), (s, &n))| DestCell {
            centre: [s[0] / n, s[1] / n],
            room: RoomIdx(room),
        })
        .collect();
    (cells, cell_of_fine)
}

/// A cell and its neighbours for the kernel: in the same space, within NEAR + CELL, no wall between.
fn neighbours(cells: &[DestCell], space: &[usize], walls: &Walls, c: usize) -> Vec<usize> {
    let at = cells[c].centre;
    let near: Vec<usize> = (0..cells.len())
        .filter(|&k| {
            let p = cells[k].centre;
            k != c
                && (p[0] - at[0]).hypot(p[1] - at[1]) <= NEAR + CELL
                && space[cells[k].room.0] == space[cells[c].room.0]
        })
        .collect();
    let to: Vec<Point> = near.iter().map(|&k| cells[k].centre).collect();
    let crosses = walls.crosses(&vec![at; near.len()], &to);
    std::iter::once(c)
        .chain(near.iter().zip(crosses).filter(|(_, x)| !x).map(|(&k, _)| k))
        .collect()
}

// ------------------------------------------------------------------ the map

/// The counts: (cells, velocity cells, outcomes) walks, fading with FORGET.
#[derive(Debug, Clone, PartialEq)]
pub struct DestMap {
    fingerprint: String,
    cells: usize,
    outcomes: usize,
    count: Vec<f32>,
    forget: f64,
    /// walks counted (faded like the counts)
    walks: f64,
    faded: Option<f64>,
}

/// A step of a walk: its cells with weights, its velocity cells with weights, its weight.
#[derive(Debug, Clone, PartialEq)]
struct Step {
    t: f64,
    cells: Vec<(usize, f64)>,
    vels: Vec<(usize, f64)>,
    weight: f64,
}

impl DestMap {
    /// An empty map of a geometry.
    pub fn new(geo: &DestGeometry) -> Self {
        Self {
            fingerprint: geo.fingerprint.clone(),
            cells: geo.cells.len(),
            outcomes: geo.outcomes(),
            count: vec![0.0; geo.cells.len() * V * geo.outcomes()],
            forget: FORGET,
            walks: 0.0,
            faded: None,
        }
    }

    /// Whether it was learned over this geometry (its rooms, doors and regions).
    pub fn fits(&self, geo: &DestGeometry) -> bool {
        self.fingerprint == geo.fingerprint && self.cells == geo.cells.len() && self.outcomes == geo.outcomes()
    }

    pub fn walks(&self) -> f64 {
        self.walks
    }

    fn fade(&mut self, t: f64) {
        let faded = *self.faded.get_or_insert(t);
        if self.forget == 0.0 || t - faded < DECAY_EVERY {
            return;
        }
        let f = (-(t - faded) / self.forget).exp();
        self.faded = Some(t);
        let f32_ = f as f32;
        self.count.iter_mut().for_each(|c| *c *= f32_);
        self.walks *= f;
    }

    /// Count a walk's steps for an outcome.
    fn add(&mut self, steps: &VecDeque<Step>, outcome: usize) {
        for s in steps {
            for &(c, cw) in &s.cells {
                for &(v, vw) in &s.vels {
                    self.count[(c * V + v) * self.outcomes + outcome] += (s.weight * cw * vw) as f32;
                }
            }
        }
        self.walks += 1.0;
    }

    /// Per outcome the walks counted near a place (its cells with weights summing to 1) for a velocity,
    /// interpolated between the neighbouring directions and speed classes.
    fn lookup(&self, cells: &[usize], cw: &[f64], v: Point) -> Vec<f64> {
        let (vc, vw) = vel_interp(v);
        (0..self.outcomes)
            .map(|k| {
                let mut s = 0.0;
                for (&c, &w) in cells.iter().zip(cw) {
                    for (&v, &u) in vc.iter().zip(&vw) {
                        s += w * u * f64::from(self.count[(c * V + v) * self.outcomes + k]);
                    }
                }
                s
            })
            .collect()
    }

    // ------------------------------------------------------------ the format

    pub fn to_json(&self) -> Value {
        let bytes: Vec<u8> = self.count.iter().flat_map(|c| c.to_le_bytes()).collect();
        json!({"fingerprint": self.fingerprint, "shape": [self.cells, V, self.outcomes], "walks": self.walks,
               "forget": self.forget, "count": formats::pack(&bytes, 6)})
    }

    /// A saved map over this geometry; Err(OtherPlan) if it was learned over
    /// another one (its sizes are not trusted before that).
    pub fn from_json(v: &Value, geo: &DestGeometry) -> Result<Self, LearnedError> {
        let m = object(v, "dest_map")?;
        let shape = formats::numbers(field(m, "shape")?, "shape", 3, 0.0)?;
        let fingerprint = field(m, "fingerprint")?
            .as_str()
            .ok_or(LearnedError::Field("fingerprint"))?;
        let empty = Self::new(geo);
        if fingerprint != empty.fingerprint || shape != [empty.cells as f64, V as f64, empty.outcomes as f64] {
            return Err(LearnedError::OtherPlan);
        }
        let text = field(m, "count")?.as_str().ok_or(LearnedError::Field("count"))?;
        let bytes = formats::unpack(text, empty.count.len() * 4)?;
        let count: Vec<f32> = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|&b| f32::from_le_bytes(b))
            .collect();
        if !count.iter().all(|c| c.is_finite() && *c >= 0.0) {
            return Err(LearnedError::Packed(PackedFault::Value));
        }
        Ok(Self {
            count,
            walks: m.get("walks").map_or(Ok(0.0), |x| number(x, "walks", 0.0))?,
            forget: m.get("forget").map_or(Ok(FORGET), |x| number(x, "forget", 0.0))?,
            ..empty
        })
    }
}

// ------------------------------------------------------------------ the walks

/// A walk under way.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Walk {
    /// its space; None beyond a door or nowhere yet
    space: Option<usize>,
    pending: VecDeque<Step>,
    stand: f64,
    pub(crate) last: f64,
    /// the outcome it may have reached and in how many steps in a row
    cand: Option<(usize, u32)>,
    pub(crate) pos: Point,
}

impl Walk {
    fn new(space: Option<usize>, t: f64, pos: Point) -> Self {
        Self {
            space,
            pending: VecDeque::new(),
            stand: 0.0,
            last: t,
            cand: None,
            pos,
        }
    }

    /// A walk begins anew in this space.
    fn restart(&mut self, space: Option<usize>) {
        (self.space, self.pending, self.stand, self.cand) = (space, VecDeque::new(), 0.0, None);
    }
}

/// What a person with a track is, once per object (Learner.observe's features).
#[derive(Debug, Clone, Copy)]
struct Feature {
    walking: f64,
    /// x, y, vx, vy of the walking component
    state: [f64; 4],
    /// the observed room, region or outside it is in
    loc: Option<usize>,
    pos: Point,
}

/// A walker as all hypotheses see it: summed with their weights.
#[derive(Debug, Clone, Copy)]
struct Seen {
    weight: f64,
    walking: f64,
    state: [f64; 4],
    loc: Option<usize>,
    best: f64,
    pos: Point,
}

/// The destination map with the walks under way: both refer to the cells of one geometry, so they
/// start over only together (a map of another plan never meets walks of this one).
#[derive(Debug, Clone, PartialEq)]
pub struct Destinations {
    map: DestMap,
    walks: Learner,
}

impl Destinations {
    pub fn new(geo: &DestGeometry) -> Self {
        Self {
            map: DestMap::new(geo),
            walks: Learner::default(),
        }
    }

    pub fn map(&self) -> &DestMap {
        &self.map
    }

    #[cfg(test)]
    pub(crate) fn walks(&self) -> &[(SegId, Walk)] {
        &self.walks.walks
    }

    /// A new world: what was learned holds on the same geometry, else the map and the walks start over.
    pub fn follow(&mut self, geo: &DestGeometry) {
        if !self.map.fits(geo) {
            *self = Self::new(geo);
        }
    }

    /// A saved map in place of the learned one if it was learned on this geometry (the walks under way
    /// go on: the same cells), else an empty one (also for None). Whether the saved one is used; Err,
    /// and nothing changed, if it cannot be read.
    pub fn load(&mut self, saved: Option<&Value>, geo: &DestGeometry) -> Result<bool, LearnedError> {
        let map = match saved.map(|v| DestMap::from_json(v, geo)) {
            Some(Err(LearnedError::OtherPlan)) | None => None,
            Some(map) => Some(map?),
        };
        let used = map.is_some();
        self.map = map.unwrap_or_else(|| DestMap::new(geo));
        Ok(used)
    }

    /// The walks under way are dropped, none counted (a pause, a start over).
    pub fn drop_walks(&mut self) {
        self.walks = Learner::default();
    }

    /// Follow the model's walkers at t and count their finished walks.
    pub fn observe(&mut self, filter: &Filter, world: &World, t: f64) {
        self.walks.observe(&mut self.map, filter, world, t);
    }
}

/// Follows the model's walkers and counts their finished walks into the map.
/// A person is followed by their earliest live track; a walk whose tracks end is taken over by a
/// walker who appears within LINK_DIST and LINK_TIME. Reads the hypotheses only.
#[derive(Debug, Clone, Default, PartialEq)]
struct Learner {
    walks: Vec<(SegId, Walk)>,
    orphans: Vec<(f64, Walk)>,
}

impl Learner {
    fn observe(&mut self, map: &mut DestMap, filter: &Filter, world: &World, t: f64) {
        map.fade(t);
        let geo = &world.destinations;
        let seen = walkers(filter, world);
        // keys gone: their walks wait to be taken over
        let (gone, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.walks)
            .into_iter()
            .partition(|(key, _)| !seen.iter().any(|(k, _)| k == key));
        self.walks = kept;
        self.orphans.extend(
            gone.into_iter()
                .filter(|(_, w)| !w.pending.is_empty() && w.space.is_some())
                .map(|(_, w)| (t, w)),
        );
        self.orphans.retain(|(t0, _)| t - t0 <= LINK_TIME);
        let k_rooms = world.plan.rooms.len();
        for (key, s) in seen {
            let space = s.loc.filter(|&l| l < k_rooms).map(|l| geo.space[l]);
            let at = match self.walks.iter().position(|(k, _)| *k == key) {
                Some(at) => at,
                None => {
                    // a new key: the walk of one gone nearby goes on (a hand-over, also at a door)
                    let near = self
                        .orphans
                        .iter()
                        .position(|(_, w)| py_hypot(w.pos[0] - s.pos[0], w.pos[1] - s.pos[1]) <= LINK_DIST);
                    let walk = near.map_or_else(|| Walk::new(space, t, s.pos), |k| self.orphans.remove(k).1);
                    self.walks.push((key, walk));
                    self.walks.len() - 1
                }
            };
            let walk = &mut self.walks[at].1;
            let dt = (t - walk.last).clamp(0.0, 0.5);
            walk.last = t;
            walk.pos = s.pos;
            advance(walk, &s, space, dt, t, geo, map);
        }
    }
}

/// The people with tracks, by their earliest live track, summed over the hypotheses (in the order
/// they first appear).
fn walkers(filter: &Filter, world: &World) -> Vec<(SegId, Seen)> {
    let k_rooms = world.plan.rooms.len();
    let objects = filter.objects();
    let mut features: Vec<(MixtureId, Feature)> = Vec::new();
    let mut seen: Vec<(SegId, Seen)> = Vec::new();
    for (wh, hy) in filter.hyp_weights().into_iter().zip(filter.hyps()) {
        for (&gid, &m) in hy.people.iter() {
            let mix = objects.mixtures().get(m);
            let key = hy
                .tracks
                .iter()
                .filter(|(_, o)| **o == Owner::Person(gid))
                .map(|(s, _)| *s)
                .min();
            let Some(key) = key.filter(|_| !mix.phantom) else {
                continue;
            };
            let f = match features.iter().find(|(k, _)| *k == m) {
                Some(&(_, f)) => f,
                None => {
                    let w = mix.weights();
                    let ([x, y], [vx, vy]) = (mix.modes[WALK].pos(), mix.modes[WALK].velocity());
                    let [ps, pw] = [mix.modes[0].pos(), mix.modes[WALK].pos()];
                    let pos = [w[0] * ps[0] + w[1] * pw[0], w[0] * ps[1] + w[1] * pw[1]];
                    let loc = match mix.away {
                        Some(away) if mix.a > 0.5 => {
                            let places = crate::outputs::places_of(objects, Obj::Density(away));
                            Some(k_rooms + crate::outputs::argmax(&places[1..]))
                        }
                        _ => world.rooms.at(pos).map(|r| r.0),
                    };
                    let f = Feature {
                        walking: w[WALK] * (1.0 - mix.a),
                        state: [x, y, vx, vy],
                        loc,
                        pos,
                    };
                    features.push((m, f));
                    f
                }
            };
            let at = match seen.iter().position(|(k, _)| *k == key) {
                Some(at) => at,
                None => {
                    seen.push((
                        key,
                        Seen {
                            weight: 0.0,
                            walking: 0.0,
                            state: [0.0; 4],
                            loc: None,
                            best: -1.0,
                            pos: [0.0; 2],
                        },
                    ));
                    seen.len() - 1
                }
            };
            let s = &mut seen[at].1;
            s.weight += wh;
            s.walking += wh * f.walking;
            for (a, x) in s.state.iter_mut().zip(f.state) {
                *a += wh * f.walking * x;
            }
            if wh > s.best {
                (s.loc, s.best, s.pos) = (f.loc, wh, f.pos);
            }
        }
    }
    seen
}

/// A walker's step (Learner._advance): through a door its walk ends there after ARRIVE steps, standing
/// STOP_TIME s it stays; else, walking, the step counts on the cells and velocities around it.
fn advance(walk: &mut Walk, s: &Seen, space: Option<usize>, dt: f64, t: f64, geo: &DestGeometry, map: &mut DestMap) {
    let Some(loc) = s.loc else { return };
    let Some(own) = walk.space else {
        // beyond a door, or nowhere yet: a walk begins in the next space in view
        if space.is_some() {
            walk.restart(space);
        }
        return;
    };
    if space != Some(own) {
        // through a door (or into a region, out)
        let n = match walk.cand {
            Some((c, n)) if c == loc => n + 1,
            _ => 1,
        };
        walk.cand = Some((loc, n));
        if n >= ARRIVE {
            if geo.allowed[own][loc] && !walk.pending.is_empty() {
                map.add(&walk.pending, loc);
            }
            walk.restart(space);
        }
        return;
    }
    walk.cand = None;
    let frac = if s.weight > 0.0 { s.walking / s.weight } else { 0.0 };
    if frac < 0.5 {
        walk.stand += dt;
        if walk.stand >= STOP_TIME {
            if !walk.pending.is_empty() {
                map.add(&walk.pending, geo.stay());
            }
            walk.pending.clear();
            return;
        }
    } else {
        walk.stand = 0.0;
    }
    if s.walking < MIN_WEIGHT || dt <= 0.0 {
        return;
    }
    let [x, y, vx, vy] = s.state.map(|v| v / s.walking);
    let speed = py_hypot(vx, vy);
    if speed < MIN_SPEED {
        return;
    }
    let Some(c) = geo.cell([x, y]) else { return };
    let cells = geo.neigh[c]
        .iter()
        .map(|&k| (k, kernel(geo.cells[k].centre, [x, y], BW_POS)))
        .filter(|&(_, w)| w > 0.02)
        .collect();
    // one walk straight through a cell's centre counts 1 there: the kernel integrates to
    // sqrt(2 pi) BW_POS along the path, the step covers speed x dt of it
    let weight = s.walking * speed * dt / ((2.0 * PI).sqrt() * BW_POS);
    walk.pending.push_back(Step {
        t,
        cells,
        vels: vel_weights([vx, vy]),
        weight,
    });
    while walk.pending.front().is_some_and(|p| t - p.t > MAX_PENDING) {
        walk.pending.pop_front();
    }
}

// ------------------------------------------------------------------ "Ziel" (MODEL.md 6)

/// "Ziel" of an observed room: P(a walker goes there next), whether it is on, the learned map's part
/// of it and its weight, the walks counted for its walkers; for the walker who most probably goes
/// there (if P >= 0.01) the room they are in, the way to the door, the time at their speed and their
/// number in the display.
#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub p: f64,
    pub on: bool,
    pub map: f64,
    pub weight: f64,
    pub walks: f64,
    pub from: Option<RoomIdx>,
    pub distance: Option<f64>,
    pub eta: Option<f64>,
    pub person: Option<usize>,
}

/// A walker as the map sees them: P(walking), their room and its space, per outcome the share of the
/// walks counted there that went so, and how many were counted.
struct Asked {
    walking: f64,
    room: usize,
    space: usize,
    shares: Vec<f64>,
    walks: f64,
    at: Point,
    velocity: Point,
}

/// "Ziel" per observed room. The walkers' own motion ("wird betreten",
/// p_enter) is the prior, the learned map its data, per walker a Dirichlet posterior with
/// dest_prior_walks walks of prior weight: P = (1 - lam) q_move + lam q_map with q_move = p_enter on the
/// scale of the room's threshold c (odds x odds(c) / odds(c_v)), q_map = sum_h w_h (1 - prod_k (1 - P(k
/// walks) f_k(room))), lam = n / (n + prior), n the walks counted for the walkers who could go there.
/// On if P >= c; with nothing learned (lam = 0) exactly when "wird betreten" is on.
pub fn targets(filter: &Filter, world: &World, map: &DestMap, entering: &[crate::outputs::Entering]) -> Vec<Target> {
    let geo = &world.destinations;
    let k_rooms = world.plan.rooms.len();
    let found = crate::outputs::walkers(filter);
    let asked: Vec<Option<Asked>> = found.walkers.iter().map(|w| ask(geo, map, w)).collect();
    let hw = if found.held.is_empty() {
        Vec::new()
    } else {
        filter.hyp_weights()
    };
    let mut q_map = vec![0.0; k_rooms];
    let (mut num, mut den) = (vec![0.0; k_rooms], vec![0.0; k_rooms]);
    let mut share: Vec<(usize, Vec<f64>)> = Vec::new(); // per walker, in the order they come
    for (wh, mine) in hw.iter().zip(&found.held) {
        let mut empty = vec![1.0; k_rooms];
        for held in mine {
            let Some(a) = &asked[held.walker] else { continue };
            // the rooms this walker could go to next: by a door, or across a divider (there f = 0)
            let rel: Vec<f64> = (0..k_rooms)
                .map(|k| f64::from(k != a.room && (geo.allowed[a.space][k] || geo.space[k] == a.space)))
                .collect();
            let at = match share.iter().position(|(i, _)| *i == held.walker) {
                Some(at) => at,
                None => {
                    share.push((held.walker, vec![0.0; k_rooms]));
                    share.len() - 1
                }
            };
            for k in 0..k_rooms {
                empty[k] *= 1.0 - a.walking * a.shares[k] * rel[k];
                num[k] += wh * a.walking * a.walks * rel[k];
                den[k] += wh * a.walking * rel[k];
                share[at].1[k] += wh * a.walking * rel[k] * (a.shares[k] + 1e-3);
            }
        }
        for (q, e) in q_map.iter_mut().zip(&empty) {
            *q += wh * (1.0 - e);
        }
    }
    let best = found.held.get(found.best);
    let o = &world.outputs;
    let c_v = o.entering_above();
    (0..k_rooms)
        .map(|k| {
            let c = o.target_above(&world.plan.room(RoomIdx(k)).id);
            let pe = entering.get(k).map_or(0.0, |e| e.p);
            let n = if den[k] > 0.0 { num[k] / den[k] } else { 0.0 };
            let lam = if n > 0.0 {
                n / (n + world.params.dest_prior_walks)
            } else {
                0.0
            };
            let q_move = if pe <= 0.0 {
                0.0
            } else if pe >= 1.0 {
                1.0
            } else {
                let odds = pe / (1.0 - pe) * (c / (1.0 - c)) / (c_v / (1.0 - c_v));
                odds / (1.0 + odds)
            };
            let (p, on) = if lam > 0.0 {
                let p = (1.0 - lam) * q_move + lam * q_map[k];
                (p, p >= c)
            } else {
                (q_move, pe > c_v) // nothing learned for these walkers: "wird betreten" decides
            };
            let mut t = Target {
                p: p.clamp(0.0, 1.0),
                on,
                map: q_map[k],
                weight: lam,
                walks: n,
                from: None,
                distance: None,
                eta: None,
                person: None,
            };
            if p >= 0.01 {
                let top = share.iter().reduce(|a, b| if b.1[k] > a.1[k] { b } else { a });
                if let Some((walker, _)) = top.filter(|(_, s)| s[k] > 0.0) {
                    let a = asked[*walker].as_ref().expect("a walker with shares was asked");
                    let distance = geo.door_distance(a.space, k, a.at);
                    let speed = py_hypot(a.velocity[0], a.velocity[1]);
                    t.from = Some(RoomIdx(a.room));
                    t.distance = distance;
                    t.eta = distance.filter(|_| speed > 0.05).map(|d| d / speed);
                    t.person = best
                        .and_then(|b| b.iter().find(|h| h.walker == *walker))
                        .map(|h| h.place + 1);
                }
            }
            t
        })
        .collect()
}

/// What the map says for a walker fast enough and in view.
fn ask(geo: &DestGeometry, map: &DestMap, w: &crate::outputs::Walker) -> Option<Asked> {
    if !(py_hypot(w.velocity[0], w.velocity[1]) >= MIN_SPEED) {
        return None; // also a velocity that is no number
    }
    let (cells, cw) = geo.around(w.at)?;
    let room = geo.cells[cells[0]].room.0;
    let count = map.lookup(cells, &cw, w.velocity);
    let walks = pairwise_sum(&count);
    Some(Asked {
        walking: w.walking,
        room,
        space: geo.space[room],
        shares: count
            .iter()
            .map(|c| if walks > 0.0 { c / walks } else { 0.0 })
            .collect(),
        walks,
        at: w.at,
        velocity: w.velocity,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::Params;
    use crate::testing::{config, wall};

    /// Two rooms of 3 x 3 m side by side, a sensor in each; between them a wall with a door at y 1.5,
    /// or a divider.
    fn two_rooms(divider: bool) -> World {
        let middle = if divider {
            r#"{"points": [[3.0, 0.0], [3.0, 3.0]], "kind": "divider"}"#.to_string()
        } else {
            wall([3.0, 0.0], [3.0, 3.0])
        };
        let walls = [
            wall([0.0, 0.0], [6.0, 0.0]),
            wall([6.0, 0.0], [6.0, 3.0]),
            wall([6.0, 3.0], [0.0, 3.0]),
            wall([0.0, 3.0], [0.0, 0.0]),
            middle,
        ];
        let doors = if divider {
            "[]"
        } else {
            r#"[{"id": "d", "x": 3.0, "y": 1.5, "width": 0.9}]"#
        };
        let c = config(
            &[("sa", "a", [0.05, 0.05], 45.0), ("sb", "b", [5.95, 0.05], 135.0)],
            r#"[{"id": "a", "name": "A", "kind": "room", "shape": "rect", "points": [[0, 0], [3, 3]]},
                {"id": "b", "name": "B", "kind": "room", "shape": "rect", "points": [[3, 0], [6, 3]]}]"#,
            &format!("[{}]", walls.join(", ")),
            doors,
        );
        World::build(&c, Params::default()).expect("a world")
    }

    #[test]
    fn a_velocity_is_read_between_its_speed_classes_and_one_that_is_no_number_does_not_panic() {
        // between two classes: the lower one and the share of the step to the next
        let s = 0.5 * (SPEED_MID[0] + SPEED_MID[1]);
        let (cells, w) = vel_interp([s, 0.0]);
        assert_eq!(cells, [0, 1 % SECTORS, SECTORS, SECTORS + 1 % SECTORS]);
        assert!((w[0] - 0.5).abs() < 1e-12 && (w[2] - 0.5).abs() < 1e-12);
        // NaN went past every class to an expect
        let (cells, _) = vel_interp([f64::NAN, 0.0]);
        assert!(cells.iter().all(|&c| c < V));
    }

    #[test]
    fn rooms_with_a_door_are_two_spaces_rooms_with_a_divider_one() {
        let w = two_rooms(false);
        let g = &w.destinations;
        assert_eq!(g.names, ["a", "b", "outside", "stays"]);
        assert!(g.cells.iter().all(|c| (c.centre[0] < 3.0) == (c.room == RoomIdx(0))));
        let in_a = g.cells.iter().filter(|c| c.room == RoomIdx(0)).count();
        assert!(
            (100..=121).contains(&in_a) && (100..=121).contains(&(g.cells.len() - in_a)),
            "0.3 m cells, split at x = 3"
        );
        assert_ne!(g.space[0], g.space[1]);
        assert_eq!(
            g.allowed[g.space[0]],
            [false, true, false, true],
            "through the door, or it stays"
        );
        let doors = &g.doors[&(g.space[0], 1)];
        assert!(
            doors
                .iter()
                .all(|d| (d[0] - 3.0).abs() < 0.1 && (d[1] - 1.5).abs() <= 0.5)
        );
        assert_eq!(g.fingerprint.len(), 16);
        assert_eq!(g.fingerprint, two_rooms(false).destinations.fingerprint);
        // the kernel's neighbours stay on their side of the wall
        let c = g.cell([2.85, 0.45]).expect("in view");
        assert!(g.neigh[c].iter().all(|&k| g.cells[k].room == RoomIdx(0)) && g.neigh[c][0] == c);
        let w = two_rooms(true);
        let g = &w.destinations;
        assert_eq!(g.space[0], g.space[1]);
        assert_eq!(
            g.allowed[g.space[0]],
            [false, false, false, true],
            "no door: it can only stay"
        );
        assert_ne!(g.fingerprint, two_rooms(false).destinations.fingerprint);
    }

    /// A walker seen alike by all hypotheses at this place and velocity, walking or standing.
    fn seen(world: &World, at: Point, v: Point, walking: f64) -> Seen {
        let loc = world.rooms.at(at).map(|r| r.0);
        Seen {
            weight: 1.0,
            walking,
            state: [at[0] * walking, at[1] * walking, v[0] * walking, v[1] * walking],
            loc,
            best: 1.0,
            pos: at,
        }
    }

    #[test]
    fn a_walk_through_the_door_counts_one_walk_there_a_stop_counts_one_that_stays() {
        let w = two_rooms(false);
        let g = &w.destinations;
        let mut map = DestMap::new(g);
        let start = seen(&w, [1.0, 1.5], [1.0, 0.0], 1.0);
        let mut walk = Walk::new(start.loc.map(|l| g.space[l]), 0.0, start.pos);
        let mut t = 0.0;
        for k in 0..30 {
            t = 0.1 * k as f64;
            let s = seen(&w, [1.0 + t, 1.5], [1.0, 0.0], 1.0);
            let space = s.loc.map(|l| g.space[l]);
            let dt = (t - walk.last).clamp(0.0, 0.5);
            walk.last = t;
            advance(&mut walk, &s, space, dt, t, g, &mut map);
        }
        assert_eq!(map.walks, 1.0, "counted once it was ARRIVE steps in b");
        let (cells, cw) = g.around([2.0, 1.5]).expect("in view");
        let towards = map.lookup(cells, &cw, [1.0, 0.0]);
        assert!(towards[1] > 0.5 && towards[g.stay()] == 0.0, "{towards:?}");
        assert!(
            map.lookup(cells, &cw, [-1.0, 0.0])[1] < 0.1 * towards[1],
            "not walking the other way"
        );
        // in b it walks a bit, then stands for STOP_TIME s: a walk that stayed
        for k in 1..=40 {
            let at = t + 0.1 * k as f64;
            let walking = if k <= 5 { 1.0 } else { 0.0 };
            let s = seen(
                &w,
                [4.0 + 0.1 * k.min(5) as f64, 1.5],
                [walking, 0.0],
                walking.max(1e-9),
            );
            let space = s.loc.map(|l| g.space[l]);
            advance(&mut walk, &s, space, 0.1, at, g, &mut map);
        }
        assert_eq!(map.walks, 2.0);
        let (cells, cw) = g.around([4.2, 1.5]).expect("in view");
        assert!(map.lookup(cells, &cw, [1.0, 0.0])[g.stay()] > 0.0);
    }

    #[test]
    fn a_saved_map_comes_back_only_on_its_plan_and_a_broken_one_is_named() {
        let w = two_rooms(false);
        let mut map = DestMap::new(&w.destinations);
        map.count[7] = 0.5;
        map.walks = 3.0;
        let v = map.to_json();
        assert_eq!(DestMap::from_json(&v, &w.destinations), Ok(map.clone()));
        assert_eq!(
            DestMap::from_json(&v, &two_rooms(true).destinations),
            Err(LearnedError::OtherPlan)
        );
        let mut bad = v.clone();
        bad["count"] = json!(formats::pack(&[0u8; 12], 6));
        assert_eq!(
            DestMap::from_json(&bad, &w.destinations),
            Err(LearnedError::Packed(PackedFault::Length))
        );
        let mut bad = v;
        bad["walks"] = json!(-1.0);
        assert_eq!(
            DestMap::from_json(&bad, &w.destinations),
            Err(LearnedError::Field("walks"))
        );
    }

    /// A filter of one hypothesis holding one walker at `at` with velocity v (as outputs' tests'
    /// `walking`), nobody unknown.
    fn walker(world: &World, at: Point, v: Point) -> Filter {
        use crate::filter::densities::Kind;
        use crate::filter::hyp::{GroupId, Hyp};
        use crate::filter::mixture::{Kalman, Mixture};
        let mut f = Filter::new(world.density_shape(), world.sights.len());
        let nobody = f.objects_mut().densities.alloc(Kind::Intensity);
        let mut modes = [Kalman::zeros(2), Kalman::zeros(2)];
        for (k, g) in modes.iter_mut().enumerate() {
            for ax in 0..2 {
                let (mean, cov) = g.axis_mut(ax);
                (mean[0], cov[0]) = (at[ax], 0.01);
                if k == WALK {
                    (mean[1], cov[3]) = (v[ax], 0.05);
                }
            }
        }
        let s = &world.shapes;
        let m = f.objects_mut().mixtures.insert(Mixture {
            logw: [1e-300f64.ln(), 0.0],
            modes,
            stay: s.go_w.clone(),
            ka: s.ka_w.clone(),
            slots: Default::default(),
            phantom: false,
            a: 0.0,
            away: None,
            t: None,
        });
        let mut hy = Hyp::new(0.0, Vec::new(), nobody);
        hy.people.insert(GroupId(1), m);
        f.set_hyps(vec![hy]);
        f
    }

    #[test]
    fn a_walker_s_target_is_the_map_s_share_weighted_by_its_walks_against_the_prior() {
        // MODEL.md 6 "Ziel": nobody enters by their own motion (q_move = 0); the map counted 9 walks
        // from here into b and 1 that stayed, so q_map = 0.9, lam = 10 / (10 + dest_prior_walks = 1)
        // and P = lam q_map = 9/11 >= 0.8: on. a, the walker's own room, is no target.
        let w = two_rooms(false);
        let g = &w.destinations;
        let mut map = DestMap::new(g);
        for cell in map.count.chunks_exact_mut(g.outcomes()) {
            (cell[1], cell[g.stay()]) = (9.0, 1.0);
        }
        let f = walker(&w, [1.0, 1.5], [1.0, 0.0]);
        let got = targets(&f, &w, &map, &[]);
        let lam = 10.0 / (10.0 + w.params.dest_prior_walks);
        let (a, b) = (&got[0], &got[1]);
        assert!((b.map - 0.9).abs() < 1e-12 && (b.walks - 10.0).abs() < 1e-12, "{b:?}");
        assert!(
            (b.weight - lam).abs() < 1e-12 && (b.p - lam * 0.9).abs() < 1e-12,
            "{b:?}"
        );
        assert!(b.on && b.p >= w.outputs.target_above("b"));
        assert_eq!((b.from, b.person), (Some(RoomIdx(0)), Some(1)));
        let d = b.distance.expect("a way to the door");
        assert!((d - 2.0).abs() < 0.5 && b.eta == Some(d / 1.0), "{b:?}");
        assert_eq!((a.p, a.on, a.weight, a.walks), (0.0, false, 0.0, 0.0));
    }
}
