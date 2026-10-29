//! The views of the core the shell, its tools and tests read (docs/rust-core.md 4): named, read-only
//! Python objects (their fields as attributes), each the core's numbers as they are.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::core::config_from;
use presence_core::filter;
use presence_core::frames::Detection;
use presence_core::geometry::Point;
use presence_core::learn::destination::Target;
use presence_core::outputs::{Cloud, Decision, Entering, HouseNumbers, Person, Source, ZoneNumbers};
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

/// A person of the display: id, P per place, lost, how probably they exist, where to draw them (None:
/// nowhere in view).
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct PersonView {
    id: usize,
    places: Vec<f64>,
    lost: bool,
    r: f64,
    shown: Option<Shown>,
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
    pub(crate) fn of(p: Person) -> Self {
        Self {
            id: p.id,
            places: p.places,
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

/// The plan of the people model (MODEL.md 2) for the shell, the calibration and the tools: the observed
/// rooms, the places (observed, the regions, outside), the areas, per sensor its own room (None: none),
/// the regions without a sensor by id ({name, rooms, open, exits}), the closed rooms (in which the
/// sensors see nobody of the home).
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
    Ok(d)
}

/// The plan of a configuration (JSON; plan_view), without building a world.
#[pyfunction]
pub fn plan<'py>(py: Python<'py>, config: &str) -> PyResult<Bound<'py, PyDict>> {
    plan_view(py, &Plan::new(&config_from(config)?))
}

/// The name of what decided a room occupied.
pub(crate) fn source_name(source: Source) -> &'static str {
    match source {
        Source::Filter => "filter",
        Source::Ld2450 => "ld2450",
        Source::Both => "beide",
    }
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
/// sight per cell column by column.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct SensorMapView {
    corner: Point,
    cell: f64,
    cols: usize,
    rows: usize,
    prior: Vec<f32>,
}

impl SensorMapView {
    pub(crate) fn of(m: &SensorMap) -> Self {
        Self {
            corner: m.corner,
            cell: m.cell,
            cols: m.cols,
            rows: m.rows,
            prior: m.prior.clone(), // the view's own copy, for Python
        }
    }
}

/// "wird betreten" of a zone: the probability, and for who most probably enters the way, the time and
/// their id in the display (None: not known).
#[pyclass(module = "presence_core", frozen, get_all)]
#[derive(Clone)] // the getter of ZoneView.entering hands Python its own
pub struct EnteringView {
    p: f64,
    distance: Option<f64>,
    eta: Option<f64>,
    person: Option<usize>,
}

impl EnteringView {
    pub(crate) fn of(e: &Entering) -> Self {
        Self {
            p: e.p,
            distance: e.distance,
            eta: e.eta,
            person: e.person,
        }
    }
}

/// "Ziel" of an observed room (MODEL.md 6): P, on, the map's P and its weight, the walks behind it; for
/// the walker who most probably goes there their observed room (index), way, time and id in the display.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct TargetView {
    p: f64,
    on: bool,
    map: f64,
    weight: f64,
    walks: f64,
    from_room: Option<usize>,
    distance: Option<f64>,
    eta: Option<f64>,
    person: Option<usize>,
}

impl TargetView {
    pub(crate) fn of(t: &Target) -> Self {
        Self {
            p: t.p,
            on: t.on,
            map: t.map,
            weight: t.weight,
            walks: t.walks,
            from_room: t.from.map(|k| k.0),
            distance: t.distance,
            eta: t.eta,
            person: t.person,
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

/// The numbers of a zone of the home (MODEL.md 6): count, P(somebody there) where known, the decision
/// (None: none; else occupied and by what), moving and still, "wird betreten", approaching, available.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct ZoneView {
    id: String,
    count: usize,
    probability: Option<f64>,
    decided: Option<bool>,
    source: Option<&'static str>,
    moving: usize,
    still: usize,
    entering: Option<EnteringView>,
    approaching: bool,
    available: bool,
}

impl ZoneView {
    pub(crate) fn of(id: &str, n: &ZoneNumbers) -> Self {
        let (decided, source) = match n.decided {
            Decision::None => (None, None),
            Decision::Free => (Some(false), None),
            Decision::Occupied(s) => (Some(true), Some(source_name(s))),
        };
        Self {
            id: id.to_owned(),
            count: n.count,
            probability: n.probability,
            decided,
            source,
            moving: n.moving,
            still: n.still,
            entering: n.entering.as_ref().map(EnteringView::of),
            approaching: n.approaching,
            available: n.available,
        }
    }
}

/// The house's numbers: people, of them moving and still.
#[pyclass(module = "presence_core", frozen, get_all)]
pub struct HouseView {
    count: usize,
    moving: usize,
    still: usize,
}

impl HouseView {
    pub(crate) fn of(h: &HouseNumbers) -> Self {
        Self {
            count: h.count,
            moving: h.moving,
            still: h.still,
        }
    }
}
