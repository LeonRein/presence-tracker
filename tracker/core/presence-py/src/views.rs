//! The views of the core the shell, its tools and tests read (docs/rust-core.md 4): named, read-only
//! Python objects (their fields as attributes), each the core's numbers as they are.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::config_from;
use presence_core::filter;
use presence_core::frames::Detection;
use presence_core::geometry::Point;
use presence_core::outputs::{Cloud, Person, RegionShown, Source, ZoneState, argmax};
use presence_core::plan::{Plan, RoomIdx, ZoneIdx};
use presence_core::world::World;
use presence_core::world::sight::SensorMap;
use presence_core::world::tiles::SIZE;

/// A hypothesis for reading (tests, tools): per person with tracks their track ids, the ghost tracks,
/// per known person how probably they exist, the mass of the unknown ones outside.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct HypView {
    logw: f64,
    people: Vec<Vec<u64>>,
    ghosts: Vec<u64>,
    known: Vec<f64>,
    unknown_out: f64,
}

impl HypView {
    pub(crate) fn of(h: filter::HypView) -> Self {
        let ids = |segs: Vec<filter::mixture::SegId>| segs.into_iter().map(|s| s.0).collect();
        Self {
            logw: h.logw,
            people: h.people.into_iter().map(ids).collect(),
            ghosts: ids(h.ghosts),
            known: h.known,
            unknown_out: h.unknown_out,
        }
    }
}

/// The tiles for drawing and reading: their fingerprint, the side of a square, per tile the lower left
/// corner of its square, its observed room (None: in none) and its centre.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct TilesView {
    fingerprint: String,
    size: f64,
    squares: Vec<Point>,
    rooms: Vec<Option<usize>>,
    centers: Vec<Point>,
}

impl TilesView {
    pub(crate) fn of(w: &World) -> Self {
        let t = &w.tiles;
        Self {
            fingerprint: w.fingerprint(),
            size: SIZE,
            squares: t.squares.clone(), // the view's own copy, for Python
            rooms: t.room.iter().map(|r| r.map(|RoomIdx(k)| k)).collect(),
            centers: t.centers.clone(),
        }
    }
}

/// A person of the display: id, P per place, the most probable place (its index), lost, how probably they
/// exist, where to draw them (None: nowhere in view) and the room (id) there.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct PersonView {
    id: usize,
    places: Vec<f64>,
    place: usize,
    lost: bool,
    r: f64,
    shown: Option<Shown>,
    room: Option<String>,
}

/// Where a person of the display is drawn: position, velocity, spread (m), P(walking).
#[pyclass(module = "presence_core", frozen, get_all)]
#[derive(Clone)] // the getter of PersonView.shown hands Python its own
pub struct Shown {
    at: Point,
    velocity: Point,
    sigma: f64,
    walk: f64,
}

impl PersonView {
    pub(crate) fn of(p: Person, plan: &Plan) -> Self {
        Self {
            id: p.id,
            place: argmax(&p.places),
            places: p.places,
            room: p.shown.and_then(|s| plan.room_of(s.at)).map(|z| z.id.clone()),
            lost: p.lost,
            r: p.r,
            shown: p.shown.map(|s| Shown {
                at: s.at,
                velocity: s.velocity,
                sigma: s.sigma,
                walk: s.walk,
            }),
        }
    }
}

/// The plan of the people model (MODEL.md 2) for the shell and the tools: the observed rooms, the places
/// (observed, the regions, outside), the areas, per sensor its own room (None: none), the regions without a
/// sensor by id ({name, rooms, open, exits}), the closed rooms (in which the sensors see nobody of the
/// home), the zones of the home that Home Assistant gets ([id, name, is a room], its rooms and areas).
pub(crate) fn plan_view<'py>(py: Python<'py>, p: &Plan) -> PyResult<Bound<'py, PyDict>> {
    let ids = |zones: &[ZoneIdx]| -> Vec<&str> { zones.iter().map(|z| p.zones[z.0].id.as_str()).collect() };
    let regions = PyDict::new(py);
    for r in &p.regions {
        let d = PyDict::new(py);
        d.set_item("name", &r.name)?;
        d.set_item("rooms", ids(&r.rooms))?;
        d.set_item("open", r.open())?;
        d.set_item("exits", r.exits)?;
        regions.set_item(&r.id, d)?;
    }
    let own: Vec<Option<&str>> = p
        .sensors
        .iter()
        .map(|s| s.own_room.map(|k| p.room(k).id.as_str()))
        .collect();
    let d = PyDict::new(py);
    d.set_item("observed", ids(&p.rooms))?;
    d.set_item("places", p.place_names())?;
    d.set_item("areas", p.areas().map(|z| z.id.as_str()).collect::<Vec<_>>())?;
    d.set_item("own_rooms", own)?;
    d.set_item("regions", regions)?;
    d.set_item("closed", ids(&p.closed))?;
    let home: Vec<(&str, &str, bool)> = p
        .home()
        .map(|z| (z.id.as_str(), z.name.as_str(), z.is_room()))
        .collect();
    d.set_item("home", home)?;
    Ok(d)
}

/// The plan of a configuration (JSON; plan_view), without building a world.
#[pyfunction]
pub fn plan<'py>(py: Python<'py>, config: &str) -> PyResult<Bound<'py, PyDict>> {
    plan_view(py, &Plan::new(&config_from(config)?))
}

/// A detection of a sensor's last frame: where in the house and in the sensor's frame, its radial speed,
/// whether it is no person (hidden) or only held by the LD2450 (stale: no measurement).
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct DetectionView {
    pos: Point,
    local: Point,
    speed: f64,
    hidden: bool,
    stale: bool,
}

impl DetectionView {
    pub(crate) fn of(d: &Detection, measured: bool) -> Self {
        Self {
            pos: d.pos,
            local: d.target.local(),
            speed: d.speed,
            hidden: d.hidden,
            stale: !d.hidden && !measured,
        }
    }
}

/// A sensor's coarse sight map for the web UI: the lower left corner, the cell side, columns, rows, the
/// sight per cell column by column, the ghost map's rate there (ghost tracks per m^2 and hour; None where
/// the sensor does not see) and the hours it watched.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct SensorMapView {
    corner: Point,
    cell: f64,
    cols: usize,
    rows: usize,
    prior: Vec<f32>,
    ghosts: Vec<Option<f64>>,
    watched_h: f64,
}

impl SensorMapView {
    pub(crate) fn of(m: &SensorMap, ghosts: Vec<Option<f64>>, watched_h: f64) -> Self {
        Self {
            corner: m.corner,
            cell: m.cell,
            cols: m.cols,
            rows: m.rows,
            prior: m.prior.clone(), // the view's own copy, for Python
            ghosts,
            watched_h,
        }
    }
}

/// A region without a sensor as the display shows it: its id, the most probable number of people in it,
/// P(somebody there).
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct RegionView {
    id: String,
    count: usize,
    p_any: f64,
}

impl RegionView {
    pub(crate) fn of(id: &str, r: RegionShown) -> Self {
        Self {
            id: id.to_owned(),
            count: r.count,
            p_any: r.p_any,
        }
    }
}

/// Where a person of the display may be: with a track their Gaussians [weight, x, y, sd x, sd y], without
/// (tile, mass per m^2).
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct CloudView {
    id: usize,
    gauss: Vec<[f64; 5]>,
    tiles: Vec<(usize, f64)>,
}

impl CloudView {
    pub(crate) fn of(c: Cloud) -> Self {
        Self {
            id: c.id,
            gauss: c.gauss,
            tiles: c.tiles,
        }
    }
}

/// A zone's state (the core's ZoneState; the shell's zones.ZoneState, field for field): decided None where no
/// decision is made, source what decided it occupied ("filter", "ld2450", "beide").
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct ZoneStateView {
    id: String,
    count: usize,
    moving: usize,
    still: usize,
    approaching: bool,
    p_enter: Option<f64>,
    eta: Option<f64>,
    distance: Option<f64>,
    person: Option<usize>,
    c_enter: Option<f64>,
    target: bool,
    p_target: Option<f64>,
    c_target: Option<f64>,
    target_weight: f64,
    target_walks: f64,
    target_from: Option<String>,
    target_distance: Option<f64>,
    target_eta: Option<f64>,
    target_person: Option<usize>,
    probability: Option<f64>,
    decided: Option<bool>,
    source: Option<&'static str>,
    available: bool,
    occupied: bool,
    c_occupied: Option<f64>,
    target_source: Option<&'static str>,
}

impl ZoneStateView {
    pub(crate) fn of(z: ZoneState) -> Self {
        Self {
            id: z.id,
            count: z.count,
            moving: z.moving,
            still: z.still,
            approaching: z.approaching,
            p_enter: z.p_enter,
            eta: z.eta,
            distance: z.distance,
            person: z.person,
            c_enter: z.c_enter,
            target: z.target,
            p_target: z.p_target,
            c_target: z.c_target,
            target_weight: z.target_weight,
            target_walks: z.target_walks,
            target_from: z.target_from,
            target_distance: z.target_distance,
            target_eta: z.target_eta,
            target_person: z.target_person,
            probability: z.probability,
            decided: z.decided.occupied(),
            source: z.decided.source().map(Source::name),
            available: z.available,
            occupied: z.occupied,
            c_occupied: z.c_occupied,
            target_source: z.target_source,
        }
    }
}
