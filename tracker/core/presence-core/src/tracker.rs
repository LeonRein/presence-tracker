//! The filter with what it runs in (docs/rust-core.md 2, 4): the world, the sensors' state and the
//! filter, and what a frame and time going on do to them. Until the ghost map is in Rust (phase 8)
//! the shell orders the steps of a frame, so the map learns between them as in the Python filter
//! (docs/rust-core.md 2.0); each step is one call here.

use crate::filter::branch::{Category, Found, Frame};
use crate::filter::evidence::Ended;
use crate::filter::mixture::SegId;
use crate::filter::{Filter, NotFinite, PeopleState};
use crate::geometry::Point;
use crate::params::LostKind;
use crate::sensors::{SensorSet, SensorState};
use crate::world::World;

/// What a sensor's frame says about its tracks (sensortracks.py): measured (or found again), lost
/// and now held, still held.
#[derive(Debug, Default)]
pub struct TrackEvents {
    pub measured: Vec<(SegId, Point)>,
    pub lost: Vec<(SegId, LostKind, Point)>,
    pub held: Vec<(SegId, Point)>,
}

#[derive(Debug)]
pub struct Tracker {
    world: World,
    filter: Filter,
    sensors: Vec<SensorState>,
}

impl Tracker {
    /// A tracker without hypotheses and without frames.
    pub fn new(world: World) -> Self {
        let n = world.sights.len();
        let filter = Filter::new(world.density_shape(), n);
        let quiet = SensorState {
            active: false,
            last_frame: f64::NEG_INFINITY,
        };
        Self {
            world,
            filter,
            sensors: vec![quiet; n],
        }
    }

    pub fn world(&self) -> &World {
        &self.world
    }

    pub fn filter(&self) -> &Filter {
        &self.filter
    }

    /// A world rebuilt on the same tiles (sensors recalibrated): what is known stays; where walks
    /// end depends on the tiles only and is kept.
    pub fn set_world(&mut self, mut world: World) {
        if world.tiles.walk_end.is_empty() {
            world.tiles.walk_end = std::mem::take(&mut self.world.tiles.walk_end);
        }
        self.world = world;
        self.filter.void_cache();
    }

    /// Where a walk that begins in a tile ends, (n + R + 1) x n (for leaps over a gap).
    pub fn set_walk_end(&mut self, walk_end: Vec<f64>) {
        self.world.tiles.walk_end = walk_end;
    }

    /// Per sensor whether its frames count and when its last frame came (the shell's runtime).
    pub fn set_sensors(&mut self, sensors: Vec<SensorState>) {
        self.sensors = sensors;
    }

    /// Time of the last move of everybody (set at the first frame).
    pub fn set_flushed(&mut self, t: f64) {
        self.filter.set_flushed(t);
    }

    /// The log evidence so far and its part dropped by the cut (kept over a rebuild).
    pub fn set_loglik(&mut self, loglik: (f64, f64)) {
        self.filter.set_loglik(loglik);
    }

    /// No live tracks, nothing moved since t (Tracker._clear).
    pub fn clear(&mut self, t: f64) {
        self.filter.clear(t);
    }

    /// Everybody moves on to t (Tracker._flush without the LD2410C).
    pub fn flush(&mut self, t: f64) {
        self.filter.flush(&self.world, &self.sensors, t);
    }

    /// An event of a frame concerns everybody: all moved to t, what is cached per hypothesis void.
    pub fn event(&mut self, t: f64) {
        self.flush(t);
        self.filter.void_cache();
    }

    /// No ghost track of sensor si was born in delta s (MODEL.md 4.2).
    pub fn watch(&mut self, si: usize, delta: f64, ghost_total: f64) {
        self.filter.watch(&self.world, si, delta, ghost_total);
    }

    /// What the frame of sensor si says about its live tracks (Tracker._evidence step 2): a
    /// measured track updates its owners; one that was lost and is measured again is found again,
    /// returned to be branched on. Tracks the filter does not know (begun before a start, or of a
    /// sensor that changed) say nothing, as in the Python filter.
    pub fn track_events(&mut self, si: usize, t: f64, events: &TrackEvents) -> Vec<(SegId, Point)> {
        let mut refound = Vec::new();
        for &(seg, z) in &events.measured {
            match self.filter.tracks().get(seg).map(|i| i.lost.is_some()) {
                Some(false) => self.filter.measured(&self.world, &self.sensors, seg, z, t),
                Some(true) => refound.push((seg, z)),
                None => {}
            }
        }
        for &(seg, kind, z) in &events.lost {
            if self.filter.tracks().get(seg).is_some() {
                self.filter.lost(seg, kind, z, t);
            }
        }
        for &(seg, z) in &events.held {
            if self.filter.tracks().get(seg).is_some() {
                self.filter.held(&self.world, si, seg, z, t);
            }
        }
        refound
    }

    /// The sensor dropped a live track; None if the filter does not know it.
    pub fn end(&mut self, seg: SegId, t: f64, data_lost: bool) -> Option<Ended> {
        self.filter.tracks().get(seg)?;
        Some(self.filter.end(&self.world, seg, t, data_lost))
    }

    /// The first frame of sensor si (or after lost data).
    pub fn censor(&mut self, si: usize) {
        self.filter.censor(&self.world, si);
    }

    /// New tracks of this frame and lost ones found again.
    pub fn branch_all(&mut self, frame: &Frame, found: &[Found]) -> Result<Vec<Vec<(f64, Category)>>, NotFinite> {
        self.filter.branch_all(&self.world, frame, found)
    }

    pub fn merge(&mut self) {
        self.filter.merge();
    }

    pub fn prune(&mut self) -> Result<(), NotFinite> {
        self.filter.prune(&self.world.params)
    }

    pub fn normalize(&mut self, dropped: f64) -> Result<(), NotFinite> {
        self.filter.normalize(dropped)
    }

    /// Known people who almost surely do not exist join the unknown ones.
    pub fn recycle(&mut self) -> bool {
        self.filter.recycle(self.world.params.give_up)
    }

    /// Everybody moves on over a gap in the data; the sensors placed could have tested them.
    pub fn predict_gap(&mut self, gap: f64, placed: SensorSet) {
        self.filter.predict_gap(&self.world, placed, gap);
    }

    pub fn people_state(&mut self) -> PeopleState {
        self.filter.people_state(&self.world)
    }

    /// The hypotheses from the shell's mirror (Filter::load).
    #[cfg(any(test, feature = "mirror"))]
    pub fn load(&mut self, state: crate::filter::mirror::MirrorState) -> Result<(), String> {
        self.filter.load(state)
    }
}
