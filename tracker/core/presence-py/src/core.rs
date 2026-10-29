//! The core behind the Python tracker (docs/rust-core.md 4): the filter, its world and what it learns
//! are presence_core's; here only the conversion, and the views the shell, its tools and tests read.

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyFloatingPointError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::config::config_from;
use crate::frames::PyFrame;
use crate::views::{
    CloudView, DetectionView, HypView, PersonView, RegionView, SensorMapView, TilesView, ZoneStateView, plan_view,
};

use presence_core::filter::{NotFinite, Start};
use presence_core::geometry::Point;
use presence_core::ld2410::CELLS;
use presence_core::ld2410::background;
use presence_core::ld2410::profile::expected;
use presence_core::learn::pauses::{Pause, Pauses};
use presence_core::learn::{Learned, LearnedError};
use presence_core::outputs::Source;
use presence_core::params::{OutputParams, Params, STILL, WALK};
use presence_core::plan::Place;
use presence_core::sensors::SensorSet;
use presence_core::tracker::{Branched, Event, Fault, Recalibrate, Tracker};
use presence_core::world::World;
use serde_json::Value;

/// The model (the JSON of filtermodel.Model's fields and filter.py's constants).
fn params_from(json: &str) -> PyResult<Params> {
    Params::from_json(json).map_err(|err| PyValueError::new_err(err.to_string()))
}

/// The world of a configuration and a model (JSON), built without the GIL.
pub(crate) fn world_of(py: Python<'_>, config: &str, model: &str) -> PyResult<World> {
    py.detach(|| World::build(&config_from(config)?, params_from(model)?).map_err(PyValueError::new_err))
}

create_exception!(
    presence_core,
    WorldChanged,
    PyException,
    "The new world of a recalibration has other tiles or sensors: the people do not live in it (the \
     shell starts the model over in it: the World given is still whole)."
);

/// A world built of a configuration and a model (JSON), once and without the GIL: a Core is made of it, a
/// recalibration takes it, and if the people do not live in it (WorldChanged) it stays here for the
/// new Core.
#[pyclass(module = "presence_core", name = "World")]
pub struct PyWorld(Option<World>);

#[pymethods]
impl PyWorld {
    #[new]
    fn new(py: Python<'_>, config: &str, model: &str) -> PyResult<Self> {
        Ok(Self(Some(world_of(py, config, model)?)))
    }

    /// The coverage of this world (presence_core::derived::coverage) as JSON.
    fn coverage(&self) -> PyResult<String> {
        let world = self
            .0
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("this world went into a core already"))?;
        Ok(serde_json::to_string(&presence_core::derived::coverage(world))
            .expect("the coverage is finite numbers and text"))
    }
}

impl PyWorld {
    fn take(&mut self) -> PyResult<World> {
        self.0
            .take()
            .ok_or_else(|| PyValueError::new_err("this world went into a core already"))
    }
}

fn not_finite(e: NotFinite) -> PyErr {
    PyFloatingPointError::new_err(e.to_string())
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
    /// A tracker in this world (it goes into the tracker).
    #[new]
    fn new(mut world: PyRefMut<'_, PyWorld>) -> PyResult<Self> {
        Ok(Self {
            tracker: Tracker::new(world.take()?),
        })
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

    /// The detections of sensor si's last frame.
    fn detections(&self, si: usize) -> PyResult<Vec<DetectionView>> {
        let read = self.tracker.sensor_frame(self.sensor(si)?);
        Ok(read
            .detections
            .iter()
            .zip(&read.measured)
            .map(|(d, &measured)| DetectionView::of(d, measured))
            .collect())
    }

    /// Time of sensor si's last frame (-inf: none since the start or its tracks' reset).
    fn last_frame(&self, si: usize) -> PyResult<f64> {
        Ok(self.tracker.last_frame(self.sensor(si)?))
    }

    /// Time goes on to t (Tracker::step): the events for the listeners. ValueError for a time that is no
    /// number, FloatingPointError if the weights are no numbers.
    fn step<'py>(&mut self, py: Python<'py>, t: f64) -> PyResult<Bound<'py, PyList>> {
        let tracker = &mut self.tracker;
        let events = py.detach(|| tracker.step(t));
        events_out(py, events.map_err(fault)?)
    }

    /// Sensors changed, the people's world not: their tracks end, then the new world (Tracker::recalibrate),
    /// which goes into the tracker. WorldChanged, and nothing changed, if the people do not live in it: the
    /// world stays whole for a new Core.
    fn recalibrate<'py>(
        &mut self,
        py: Python<'py>,
        changed: Vec<usize>,
        mut world: PyRefMut<'_, PyWorld>,
    ) -> PyResult<Bound<'py, PyList>> {
        let changed = changed
            .into_iter()
            .map(|si| self.sensor(si))
            .collect::<PyResult<SensorSet>>()?;
        let new = world.take()?;
        let tracker = &mut self.tracker;
        let refused = match py.detach(|| tracker.recalibrate(changed, new)) {
            Ok(events) => return events_out(py, events),
            Err(e) => e,
        };
        let why = refused.to_string();
        match refused {
            Recalibrate::WorldChanged(new) => {
                world.0 = Some(*new); // whole, for the new Core
                Err(WorldChanged::new_err(why))
            }
            Recalibrate::Fault(e) => Err(fault(e)),
        }
    }

    /// Only what the outputs read changed (the configuration's OUTPUT_PARAMS): nothing is built, nothing
    /// starts over (Tracker::set_outputs). ValueError, and nothing changed, if they do not fit.
    fn set_outputs(&mut self, config: &str) -> PyResult<()> {
        let config = config_from(config)?;
        let silent_after = self.tracker.world().params.silent_after;
        self.tracker
            .set_outputs(OutputParams::of(&config.params, silent_after))
            .map_err(PyValueError::new_err)
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

    /// The LD2410C of sensor si as the display shows it: present (gaps up to ld2410_hold bridged) and the
    /// distance (m on the floor) of its last presence.
    fn ld_shown(&self, si: usize) -> PyResult<(bool, f64)> {
        let shown = self.tracker.ld_shown(self.sensor(si)?);
        Ok((shown.present, shown.distance))
    }

    /// Whether learning is paused at any time of [t0, t1] (t1 None: the moment t0; the pauses as last set).
    #[pyo3(signature = (t0, t1=None))]
    fn paused(&self, t0: f64, t1: Option<f64>) -> bool {
        self.tracker.paused(t0, t1.unwrap_or(t0))
    }

    /// Per point the observed room it is in (id; None: in none).
    fn room_at(&self, points: Vec<Point>) -> Vec<Option<String>> {
        let plan = &self.tracker.world().plan;
        points
            .iter()
            .map(|&p| plan.room_at(p).map(|k| plan.room(k).id.clone()))
            .collect()
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
        self.tracker
            .filter()
            .hypotheses()
            .into_iter()
            .map(HypView::of)
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
        TilesView::of(self.tracker.world())
    }

    /// The tile of a point, or of the cell in view nearest to it (None: no tiles).
    fn tile_near(&self, point: Point) -> Option<usize> {
        self.tracker.world().tiles.cell_near(point)
    }

    /// The coarse sight map of sensor si for the web UI.
    fn sensor_map(&self, si: usize) -> PyResult<SensorMapView> {
        let (map, ghosts, watched_h) = self.tracker.sensor_map(self.sensor(si)?);
        Ok(SensorMapView::of(map, ghosts, watched_h))
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

    /// Per observed room what decided it occupied at now ("filter", "ld2450", "beide"), None: not.
    fn occupancy(&mut self, now: f64) -> Vec<Option<&'static str>> {
        self.tracker
            .occupancy(now)
            .into_iter()
            .map(|s| s.map(Source::name))
            .collect()
    }

    /// The people of the display.
    fn persons(&self) -> Vec<PersonView> {
        let plan = &self.tracker.world().plan;
        self.tracker
            .persons()
            .into_iter()
            .map(|p| PersonView::of(p, plan, false))
            .collect()
    }

    /// The people of the display for the live view: with where their trail breaks (Tracker::trail_breaks;
    /// remembers where they were shown).
    fn persons_shown(&mut self) -> Vec<PersonView> {
        let persons = self.tracker.persons();
        let breaks = self.tracker.trail_breaks(&persons);
        let plan = &self.tracker.world().plan;
        persons
            .into_iter()
            .zip(breaks)
            .map(|(p, jumped)| PersonView::of(p, plan, jumped))
            .collect()
    }

    /// The people in the observed rooms: (most probable number, moving, still) (Tracker::observed_total).
    fn observed_total(&mut self, py: Python<'_>) -> (usize, usize, usize) {
        let tracker = &mut self.tracker;
        let t = py.detach(|| tracker.observed_total());
        (t.count, t.moving, t.still)
    }

    /// The regions without a sensor as the display shows them (in the order of the plan's places).
    fn regions(&mut self, py: Python<'_>) -> Vec<RegionView> {
        let tracker = &mut self.tracker;
        let shown = py.detach(|| tracker.regions_shown());
        let plan = &self.tracker.world().plan;
        plan.regions
            .iter()
            .zip(shown)
            .map(|(r, s)| RegionView::of(&r.id, s))
            .collect()
    }

    /// How long people stay in a region without a sensor: the median and the 90 % quantile (s).
    #[getter]
    fn dwell(&self) -> [f64; 2] {
        self.tracker.world().outputs.dwell
    }

    /// Where they may be.
    fn clouds(&self) -> Vec<CloudView> {
        self.tracker.clouds().into_iter().map(CloudView::of).collect()
    }

    /// The zones' states at now (Tracker::zone_states): the rooms of the home, then its other zones, the house
    /// ("_total") last.
    fn zone_states(&mut self, py: Python<'_>, now: f64) -> Vec<ZoneStateView> {
        let tracker = &mut self.tracker;
        let states = py.detach(|| tracker.zone_states(now));
        states.into_iter().map(ZoneStateView::of).collect()
    }

    /// Whether the sensor with this id sent a frame lately.
    fn online(&self, id: &str, now: f64) -> bool {
        self.tracker.online(id, now)
    }

    /// Per observed room whether its own sensors in use all went silent.
    fn silent(&self, now: f64) -> Vec<bool> {
        self.tracker.silent(now)
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
