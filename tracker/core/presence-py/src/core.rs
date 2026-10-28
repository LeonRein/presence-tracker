//! The core behind the Python tracker (docs/rust-core.md 4): the filter, its world and what it learns
//! are presence_core's; here only the conversion, and the views the shell, its tools and tests read.

use pyo3::exceptions::{PyFloatingPointError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::frames::PyFrame;

use presence_core::config::Config;
use presence_core::filter::mixture::SegId;
use presence_core::filter::{NotFinite, Start};
use presence_core::geometry::Point;
use presence_core::ld2410::CELLS;
use presence_core::ld2410::background;
use presence_core::ld2410::profile::expected;
use presence_core::learn::pauses::{Pause, Pauses};
use presence_core::learn::{Learned, LearnedError};
use presence_core::outputs::{Decision, Source};
use presence_core::params::{Params, STILL, WALK};
use presence_core::plan::{Place, Plan, RoomIdx};
use presence_core::sensors::SensorSet;
use presence_core::tracker::{Branched, Event, Fault, Tracker};
use presence_core::world::World;
use presence_core::world::tiles::SIZE;
use serde_json::Value;

/// The configuration (the JSON of model.Config.to_dict()).
pub(crate) fn config_from(json: &str) -> PyResult<Config> {
    Config::from_json(json).map_err(PyValueError::new_err)
}

/// The model (the JSON of filtermodel.Model's fields and filter.py's constants).
fn params_from(json: &str) -> PyResult<Params> {
    Params::from_json(json).map_err(|err| PyValueError::new_err(err.to_string()))
}

/// The world of a configuration and a model (JSON), built without the GIL.
pub(crate) fn world_of(py: Python<'_>, config: &str, model: &str, silent_after: f64) -> PyResult<World> {
    py.detach(|| World::build(&config_from(config)?, params_from(model)?, silent_after).map_err(PyValueError::new_err))
}

fn not_finite(e: NotFinite) -> PyErr {
    PyFloatingPointError::new_err(e.to_string())
}

fn source_name(source: Source) -> &'static str {
    match source {
        Source::Filter => "filter",
        Source::Ld2450 => "ld2450",
        Source::Both => "beide",
    }
}

fn fault(e: Fault) -> PyErr {
    match e {
        Fault::Call(e) => PyValueError::new_err(e.to_string()),
        Fault::NotFinite(e) => not_finite(e),
    }
}

#[allow(clippy::needless_pass_by_value)] // map_err hands the error over by value
fn learned_error(e: LearnedError) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn json_of(text: &str) -> PyResult<Value> {
    serde_json::from_str(text).map_err(|e| PyValueError::new_err(format!("not readable: {e}")))
}

/// The events for the shell's listeners: ("watch", si, delta), ("track_end", si, (x, y), p_ghost, life,
/// data_lost), ("branch", si, seg, (x, y), refind, [(logw, category)]).
fn events_out(py: Python<'_>, events: Vec<Event>) -> PyResult<Bound<'_, PyList>> {
    let out = PyList::empty(py);
    for e in events {
        match e {
            Event::Watch { si, delta } => out.append(("watch", si, delta))?,
            Event::TrackEnd {
                si,
                z0,
                p_ghost,
                life,
                data_lost,
            } => out.append(("track_end", si, (z0[0], z0[1]), p_ghost, life, data_lost))?,
            Event::Branch {
                si,
                seg,
                z,
                how,
                children,
            } => {
                let children: Vec<(f64, &str)> = children.iter().map(|c| (c.logw, c.category.name())).collect();
                let refind = how == Branched::FoundAgain;
                out.append(("branch", si, seg.0, (z[0], z[1]), refind, children))?
            }
        }
    }
    Ok(out)
}

/// What was learned, copied from the tracker (Core.learned): packed in its saved formats by json(),
/// which lets other threads run meanwhile (the app writes it in a thread).
#[pyclass(module = "presence_core", name = "Learned", frozen)]
pub struct PyLearned(Learned);

#[pymethods]
impl PyLearned {
    /// {"ghost_map", "ld_background", "dest_map"} as JSON.
    fn json(&self, py: Python<'_>) -> String {
        py.detach(|| self.0.to_json().to_string())
    }
}

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

/// The plan of the people model (MODEL.md 2) for the shell, the calibration and the tools: the observed
/// rooms, the places (observed, the regions, outside), the areas, per sensor its own room (None: none),
/// the regions without a sensor by id ({name, rooms, open, exits}), the closed rooms (in which the
/// sensors see nobody of the home).
fn plan_view<'py>(py: Python<'py>, p: &Plan) -> PyResult<Bound<'py, PyDict>> {
    let ids = |zones: &[presence_core::plan::ZoneIdx]| -> Vec<&str> {
        zones.iter().map(|z| p.zones[z.0].id.as_str()).collect()
    };
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

/// The Rust tracker for the Python shell: converts, nothing more (the steps are presence_core's).
#[pyclass(module = "presence_core")]
pub struct Core {
    tracker: Tracker,
}

impl Core {
    /// si if the world has sensor si, else ValueError (the shell's index; a wrong one is a bug there).
    fn sensor(&self, si: usize) -> PyResult<usize> {
        let n = self.tracker.world().sights.len();
        if si < n {
            Ok(si)
        } else {
            Err(PyValueError::new_err(format!("no sensor {si} of {n}")))
        }
    }
}

// PyO3 extracts the arguments of a method as owned values
#[allow(clippy::needless_pass_by_value)]
#[pymethods]
impl Core {
    /// A tracker in the world of a configuration and model (JSON: the fields that differ from the
    /// defaults); `silent_after`: s until a room's own sensor counts as gone.
    #[new]
    fn new(py: Python<'_>, config: &str, model: &str, silent_after: f64) -> PyResult<Self> {
        Ok(Self {
            tracker: Tracker::new(world_of(py, config, model, silent_after)?),
        })
    }

    /// What the outputs are cached by: the moves of everybody and the events that concern all.
    #[getter]
    fn epoch(&self) -> u64 {
        self.tracker.filter().epoch()
    }

    #[getter]
    fn loglik(&self) -> (f64, f64) {
        self.tracker.filter().loglik()
    }

    /// When the model started (None: not yet) and its time now.
    #[getter]
    fn start(&self) -> Option<f64> {
        self.tracker.start()
    }

    #[getter]
    fn now(&self) -> f64 {
        self.tracker.now()
    }

    /// The model starts at t (a tool's given start).
    fn start_at(&mut self, t: f64) {
        self.tracker.start_at(t);
    }

    /// A frame of the sensor with this id at t (Tracker::frame): the events for the listeners. ValueError
    /// for a time that is no number, FloatingPointError if the weights are no numbers.
    fn frame<'py>(
        &mut self,
        py: Python<'py>,
        id: &str,
        t: f64,
        frame: &Bound<'py, PyFrame>,
    ) -> PyResult<Bound<'py, PyList>> {
        let (tracker, frame) = (&mut self.tracker, &frame.get().0);
        let events = py.detach(|| tracker.frame(id, t, frame));
        events_out(py, events.map_err(fault)?)
    }

    /// The detections of sensor si's last frame: (x, y, lx, ly, speed, hidden, stale - held by the LD2450,
    /// no measurement).
    #[allow(clippy::type_complexity)]
    fn detections(&self, si: usize) -> PyResult<Vec<(f64, f64, f64, f64, f64, bool, bool)>> {
        let read = self.tracker.sensor_frame(self.sensor(si)?);
        Ok(read
            .detections
            .iter()
            .zip(&read.measured)
            .map(|(d, &measured)| {
                let [lx, ly] = d.target.local();
                (d.pos[0], d.pos[1], lx, ly, d.speed, d.hidden, !d.hidden && !measured)
            })
            .collect())
    }

    /// Time of sensor si's last frame (-inf: none since the start or its tracks' reset).
    fn last_frame(&self, si: usize) -> PyResult<f64> {
        Ok(self.tracker.last_frame(self.sensor(si)?))
    }

    /// Time goes on to t (Tracker::step): the events for the listeners.
    fn step<'py>(&mut self, py: Python<'py>, t: f64) -> PyResult<Bound<'py, PyList>> {
        let tracker = &mut self.tracker;
        let events = py.detach(|| tracker.step(t));
        events_out(py, events.map_err(not_finite)?)
    }

    /// Sensors changed, the people's world not: their tracks end, then the new world (Tracker::recalibrate).
    fn recalibrate<'py>(
        &mut self,
        py: Python<'py>,
        changed: Vec<usize>,
        config: &str,
        model: &str,
        silent_after: f64,
    ) -> PyResult<Bound<'py, PyList>> {
        let changed = changed
            .into_iter()
            .map(|si| self.sensor(si))
            .collect::<PyResult<SensorSet>>()?;
        let world = world_of(py, config, model, silent_after)?;
        let events = self.tracker.recalibrate(changed, world);
        events_out(py, events.map_err(fault)?)
    }

    /// The LD2450 tracks of all sensors start over (their next frames are a start).
    fn reset_tracks(&mut self) {
        self.tracker.reset_tracks();
    }

    /// The intervals [start, end) in which nothing is learned (end None: still paused).
    fn set_pauses(&mut self, pauses: Vec<(f64, Option<f64>)>) -> PyResult<()> {
        let pauses = pauses.into_iter().map(|(start, end)| Pause { start, end }).collect();
        let pauses = Pauses::new(pauses).map_err(|e| PyValueError::new_err(e.to_string()))?;
        self.tracker.set_pauses(pauses);
        Ok(())
    }

    /// Use a saved ghost map (its JSON; None: the prior's): whether it is used (learned with this prior).
    #[pyo3(signature = (saved=None))]
    fn use_ghost_map(&mut self, saved: Option<&str>) -> PyResult<bool> {
        let saved = saved.map(json_of).transpose()?;
        let used = self.tracker.use_ghost_map(saved.as_ref()).map_err(learned_error)?;
        Ok(used)
    }

    /// Use a saved LD2410C background (its JSON; None: nothing learned): why the parts not taken were
    /// dropped (each a sensor's part, the others are used).
    #[pyo3(signature = (saved=None))]
    fn use_ld_background(&mut self, saved: Option<&str>) -> PyResult<Vec<String>> {
        let saved = saved.map(json_of).transpose()?;
        let dropped = self.tracker.use_background(saved.as_ref()).map_err(learned_error)?;
        Ok(dropped.iter().map(ToString::to_string).collect())
    }

    /// Use a saved destination map (its JSON; None: empty): whether it is used (learned on this plan).
    #[pyo3(signature = (saved=None))]
    fn use_dest_map(&mut self, saved: Option<&str>) -> PyResult<bool> {
        let saved = saved.map(json_of).transpose()?;
        let used = self.tracker.use_dest_map(saved.as_ref()).map_err(learned_error)?;
        Ok(used)
    }

    /// A copy of what was learned, to be packed (json()) without the tracker, e.g. in a thread.
    fn learned(&self) -> PyLearned {
        PyLearned(self.tracker.learned().clone()) // a snapshot: the model goes on meanwhile
    }

    /// The LD2410Cs learn nothing from their next `seconds` s of frames.
    fn hold_background(&mut self, seconds: f64) {
        self.tracker.hold_background(seconds);
    }

    /// The ghost map's rate (per m^2 and s) of sensor si at the points, and s it watched.
    fn ghost_rates(&self, si: usize, points: Vec<Point>) -> PyResult<(Vec<f64>, f64)> {
        Ok(self.tracker.ghost_rates(self.sensor(si)?, &points))
    }

    /// The background of sensor si's LD2410C (16 cells) and its echo sources' rate (per s).
    fn background(&self, si: usize) -> PyResult<(Vec<f64>, f64)> {
        let (b, rate) = self.tracker.background(self.sensor(si)?);
        Ok((b.to_vec(), rate))
    }

    #[cfg(feature = "testing")]
    #[pyo3(signature = (dropped=0.0))]
    fn normalize(&mut self, dropped: f64) -> PyResult<()> {
        self.tracker.normalize(dropped).map_err(not_finite)
    }

    fn hyp_weights(&self) -> Vec<f64> {
        self.tracker.filter().hyp_weights()
    }

    /// The bookkeeping's invariants (Filter::check): ValueError with what is wrong.
    fn check(&self) -> PyResult<()> {
        self.tracker.filter().check().map_err(PyValueError::new_err)
    }

    /// What is known about the people in the saved format (JSON; Tracker::saved_people).
    fn people_state(&mut self, py: Python<'_>) -> PyResult<String> {
        let tracker = &mut self.tracker;
        let saved = py.detach(|| tracker.saved_people()).map_err(PyValueError::new_err)?;
        Ok(saved.to_string())
    }

    /// Start over from a state in the saved format (JSON): None, or why it was not taken (then nothing
    /// changed).
    fn restore_people(&mut self, py: Python<'_>, saved: &str) -> Option<String> {
        let tracker = &mut self.tracker;
        py.detach(|| {
            let saved: Value = serde_json::from_str(saved).map_err(|e| e.to_string())?;
            tracker.restore_saved(&saved)
        })
        .err()
    }

    /// The hypotheses for reading.
    fn hypotheses(&self) -> Vec<HypView> {
        let ids = |segs: Vec<SegId>| segs.into_iter().map(|s| s.0).collect();
        self.tracker
            .filter()
            .hypotheses()
            .into_iter()
            .map(|h| HypView {
                logw: h.logw,
                people: h.people.into_iter().map(ids).collect(),
                ghosts: ids(h.ghosts),
                known: h.known,
                unknown_out: h.unknown_out,
            })
            .collect()
    }

    /// The live tracks of the filter: (track id, sensor index).
    fn tracks(&self) -> Vec<(u64, usize)> {
        self.tracker
            .filter()
            .tracks()
            .iter()
            .map(|(s, i)| (s.0, i.si))
            .collect()
    }

    /// The tiles for drawing and reading.
    fn tiles_view(&self) -> TilesView {
        let w = self.tracker.world();
        let t = &w.tiles;
        TilesView {
            fingerprint: w.fingerprint(),
            size: SIZE,
            squares: t.squares.clone(), // the view's own copy, for Python
            rooms: t.room.iter().map(|r| r.map(|RoomIdx(k)| k)).collect(),
            centers: t.centers.clone(),
        }
    }

    /// The tile of a point, or of the cell in view nearest to it (None: no tiles).
    fn tile_near(&self, point: Point) -> Option<usize> {
        self.tracker.world().tiles.cell_near(point)
    }

    /// The coarse sight map of sensor si for the web UI: (lower left corner, cell side, columns, rows,
    /// the sight per cell column by column).
    #[allow(clippy::type_complexity)]
    fn sensor_map(&self, si: usize) -> PyResult<(Point, f64, usize, usize, Vec<f32>)> {
        let m = &self.tracker.world().sights[self.sensor(si)?].map;
        Ok((m.corner, m.cell, m.cols, m.rows, m.prior.clone()))
    }

    /// How well sensor si sees a person at the points, 0..1 (MODEL.md 4.1).
    fn sight(&self, si: usize, points: Vec<Point>) -> PyResult<Vec<f64>> {
        let grid = &self.tracker.world().sights[self.sensor(si)?].grid;
        Ok(points.iter().map(|&p| grid.at(p)).collect())
    }

    /// How well the best sensor can test a person in each tile, 0..1 (MODEL.md 5.5).
    fn observed(&self) -> Vec<f64> {
        let w = self.tracker.world();
        w.observed((0..w.sights.len()).collect())
    }

    /// The energies of sensor si's last LD2410C frame that counted (16), or None.
    #[cfg(feature = "testing")]
    fn ld_last(&self, si: usize) -> PyResult<Option<[f64; CELLS]>> {
        Ok(self.tracker.ld_collect(self.sensor(si)?).last)
    }

    // ---------------------------------------------------------------- starts

    /// Start over now (Tracker::reset_people): nothing known (people None), or known people at these
    /// places (a place's index, None: anywhere) and nobody unknown; ValueError for a place there is not.
    #[pyo3(signature = (people=None))]
    fn reset_people(&mut self, people: Option<Vec<Option<usize>>>) -> PyResult<()> {
        let regions = self.tracker.world().plan.regions.len();
        let start = |p: Option<usize>| match p {
            None => Ok(Start::Anywhere),
            Some(i) => Place::of_index(i, regions)
                .map(Start::Place)
                .ok_or_else(|| PyValueError::new_err(format!("no place {i}"))),
        };
        let people: Option<Vec<Start>> = people.map(|v| v.into_iter().map(start).collect()).transpose()?;
        self.tracker
            .reset_people(people.as_deref())
            .map_err(PyValueError::new_err)?;
        Ok(())
    }

    /// Every hypothesis' weight NaN: the shell's test of a failing model.
    #[cfg(feature = "testing")]
    fn spoil_weights_for_test(&mut self) {
        self.tracker.spoil_weights_for_test();
    }

    /// What an earlier core saw, learned and gathered goes on here (the shell's rebuild).
    fn adopt(&mut self, mut old: PyRefMut<'_, Core>) {
        self.tracker.adopt(&mut old.tracker);
    }

    // ---------------------------------------------------------------- outputs (MODEL.md 6)

    /// Per observed room the distribution of the number of people [P(0), P(1), ...], then of those
    /// in view at all.
    fn count_distribution<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let tracker = &mut self.tracker;
        let rows = py.detach(|| tracker.count_distribution());
        Ok(rows.into_pyobject(py)?.into_any())
    }

    /// Per place (observed, regions..., outside) the distribution of the number of people, then of
    /// those in the house.
    fn place_distribution<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let tracker = &mut self.tracker;
        let rows = py.detach(|| tracker.place_distribution());
        Ok(rows.into_pyobject(py)?.into_any())
    }

    /// Per zone (the observed rooms, then the areas) "wird betreten": (p, distance, eta, person).
    #[allow(clippy::type_complexity)]
    fn entering(&mut self, py: Python<'_>) -> Vec<(f64, Option<f64>, Option<f64>, Option<usize>)> {
        let tracker = &mut self.tracker;
        py.detach(|| tracker.entering())
            .iter()
            .map(|e| (e.p, e.distance, e.eta, e.person))
            .collect()
    }

    /// Per observed room "Ziel": (p, on, map, weight, walks, from (room id), distance, eta, person).
    #[allow(clippy::type_complexity)]
    fn targets(
        &mut self,
        py: Python<'_>,
    ) -> Vec<(
        f64,
        bool,
        f64,
        f64,
        f64,
        Option<String>,
        Option<f64>,
        Option<f64>,
        Option<usize>,
    )> {
        let tracker = &mut self.tracker;
        let targets = py.detach(|| tracker.targets().to_vec());
        let plan = &self.tracker.world().plan;
        targets
            .into_iter()
            .map(|t| {
                let from = t.from.map(|k| plan.room(k).id.clone());
                (t.p, t.on, t.map, t.weight, t.walks, from, t.distance, t.eta, t.person)
            })
            .collect()
    }

    /// Per observed room what decided it occupied at now ("filter", "ld2450", "beide"), None: not.
    fn occupancy(&mut self, now: f64) -> Vec<Option<&'static str>> {
        self.tracker
            .occupancy(now)
            .into_iter()
            .map(|s| s.map(source_name))
            .collect()
    }

    /// The people of the display.
    fn persons(&self) -> Vec<PersonView> {
        self.tracker
            .persons()
            .into_iter()
            .map(|p| PersonView {
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
            })
            .collect()
    }

    /// Where they may be: (id, [[weight, x, y, sd x, sd y]], [(tile, mass per m^2)]).
    #[allow(clippy::type_complexity)]
    fn clouds(&self) -> Vec<(usize, Vec<[f64; 5]>, Vec<(usize, f64)>)> {
        self.tracker
            .clouds()
            .into_iter()
            .map(|c| (c.id, c.gauss, c.tiles))
            .collect()
    }

    /// Per zone of the home (in the plan's order: the configuration's) its id and numbers - count,
    /// probability, decided (None: no decision; else (occupied, source)), moving, still, entering
    /// (p, distance, eta, person), approaching, available -, and the house's (count, moving, still).
    #[allow(clippy::type_complexity)]
    fn zone_numbers<'py>(
        &mut self,
        py: Python<'py>,
        now: f64,
    ) -> PyResult<(Bound<'py, PyList>, (usize, usize, usize))> {
        let tracker = &mut self.tracker;
        let (zones, house) = py.detach(|| tracker.zone_numbers(now));
        let out = PyList::empty(py);
        for (z, n) in self.tracker.world().plan.home().zip(&zones) {
            let decided = match n.decided {
                Decision::None => None,
                Decision::Free => Some((false, None)),
                Decision::Occupied(s) => Some((true, Some(source_name(s)))),
            };
            let entering = n.entering.map(|e| (e.p, e.distance, e.eta, e.person));
            out.append((
                z.id.as_str(),
                n.count,
                n.probability,
                decided,
                n.moving,
                n.still,
                entering,
                n.approaching,
                n.available,
            ))?;
        }
        Ok((out, (house.count, house.moving, house.still)))
    }

    /// Whether the sensor with this id sent a frame lately.
    fn online(&self, id: &str, now: f64) -> bool {
        self.tracker.online(id, now)
    }

    /// Per observed room whether its own sensors in use all went silent.
    fn silent(&self, now: f64) -> Vec<bool> {
        self.tracker.silent(now)
    }

    /// The thresholds of the outputs: of P(somebody there) for occupied, of p_enter for "wird betreten".
    fn thresholds(&self) -> (f64, f64) {
        let o = &self.tracker.world().outputs;
        (o.occupied_above(), o.entering_above())
    }

    /// The plan of the world (plan_view).
    fn plan<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        plan_view(py, &self.tracker.world().plan)
    }

    /// Per room id the last time its own LD2450 measured somebody in it.
    fn seen_last(&self) -> Vec<(String, f64)> {
        self.tracker.seen().last().to_vec()
    }

    /// Per observed room whether it has an own sensor in use.
    fn watched(&self) -> Vec<bool> {
        self.tracker.watched()
    }
}

/// The model's defaults (JSON of Params::default()): what a model given as the fields that differ is
/// read against (the simulation's numbers).
#[pyfunction]
pub fn model_defaults() -> String {
    serde_json::to_value(Params::default())
        .expect("the defaults are finite numbers")
        .to_string()
}

/// s: how long what the ghost map and the destination map learned lasts (each fades with it; the tools that
/// compose learned states).
#[pyfunction]
pub fn forgetting() -> (f64, f64) {
    (
        presence_core::learn::ghost_map::FORGET,
        presence_core::learn::destination::FORGET,
    )
}

/// The LD2410C's background (16 cells) before anything is learned, with the default model.
#[pyfunction]
pub fn ld_prior() -> [f64; CELLS] {
    background::prior(&Params::default())
}

/// The energy a person puts into the 16 cells of an LD2410C at `angle` degrees off its axis and `slant`
/// m, in sight, with the default model: (standing, walking) (ld2410/profile.rs; the simulation's).
#[pyfunction]
pub fn ld_expected(angle: f64, slant: f64) -> ([f64; CELLS], [f64; CELLS]) {
    let p = Params::default();
    (
        expected(&p, STILL, angle, slant, 1.0),
        expected(&p, WALK, angle, slant, 1.0),
    )
}
