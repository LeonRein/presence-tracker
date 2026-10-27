//! The filter with what it runs in (docs/rust-core.md 2, 4): the world, the sensors' state and the
//! filter, and what a frame and time going on do to them. Until the ghost map is in Rust (phase 8)
//! the shell orders the steps of a frame, so the map learns between them as in the Python filter
//! (docs/rust-core.md 2.0); each step is one call here.

use crate::filter::LdLearn;
use crate::filter::branch::{Category, Found, Frame};
use crate::filter::evidence::Ended;
use crate::filter::mixture::SegId;
use crate::filter::{Filter, NotFinite, PeopleState};
use crate::geometry::Point;
use crate::ld2410::CELLS;
use crate::ld2410::sensor::LdCollect;
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
        let sensors = vec![SensorState::new(&world.params); n];
        Self { world, filter, sensors }
    }

    pub fn world(&self) -> &World {
        &self.world
    }

    #[cfg(test)]
    pub(crate) fn filter_mut(&mut self) -> &mut Filter {
        &mut self.filter
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
    pub fn set_sensors(&mut self, state: &[(bool, f64)]) {
        for (s, &(active, last_frame)) in self.sensors.iter_mut().zip(state) {
            (s.active, s.last_frame) = (active, last_frame);
        }
    }

    /// Time of the last move of everybody (set at the first frame).
    pub fn set_flushed(&mut self, t: f64) {
        self.filter.set_flushed(t);
    }

    /// The log evidence so far and its part dropped by the cut (kept over a rebuild).
    pub fn set_loglik(&mut self, loglik: (f64, f64)) {
        self.filter.set_loglik(loglik);
    }

    /// No live tracks, nothing moved since t, nothing of the LD2410Cs gathered (Tracker._clear).
    pub fn clear(&mut self, t: f64) {
        self.filter.clear(t);
        for s in &mut self.sensors {
            s.ld.start_over(&self.world.params);
        }
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

    /// The energies of a frame of sensor si's LD2410C (moving 0..8, still 0..8; LdSensor::frame).
    pub fn ld_frame(&mut self, si: usize, t: f64, gates: Option<(&[f64; 9], &[f64; 9])>, target: bool) {
        let frame = self.world.params.ld_frame;
        self.sensors[si].ld.frame(t, gates, target, frame);
    }

    /// What sensor si's LD2410C frames left.
    pub fn ld_collect(&self, si: usize) -> LdCollect {
        self.sensors[si].ld.collect
    }

    /// The same of an earlier tracker of this sensor (the shell's rebuild).
    pub fn set_ld_collect(&mut self, si: usize, collect: LdCollect) {
        self.sensors[si].ld.collect = collect;
    }

    /// The sensors whose LD2410C has gathered long enough to be weighed, in the order of their index.
    pub fn ld_due(&self) -> Vec<usize> {
        let every = self.world.params.ld_every;
        (0..self.sensors.len())
            .filter(|&si| self.sensors[si].ld.due(every))
            .collect()
    }

    /// Weighs by the LD2410C si's frames since its last weighing (Filter::ld_weigh) given its
    /// background and its echo sources' rate read before; what the background learns, None if there
    /// were no frames.
    pub fn ld_weigh(&mut self, si: usize, b: &[f64; CELLS], rate: f64) -> Result<Option<LdLearn>, NotFinite> {
        let sensor = &mut self.sensors[si].ld;
        self.filter.ld_weigh(&self.world, si, sensor, b, rate)
    }

    /// Sensor si's LD2410C as the filter knows it.
    pub fn ld_sensor(&self, si: usize) -> &crate::ld2410::sensor::LdSensor {
        &self.sensors[si].ld
    }

    /// The LD2410C of sensor si starts over (recalibrated).
    pub fn ld_reset(&mut self, si: usize) {
        self.sensors[si].ld.start_over(&self.world.params);
    }

    /// The hypotheses from the shell's mirror (Filter::load).
    #[cfg(any(test, feature = "mirror"))]
    pub fn load(&mut self, state: crate::filter::mirror::MirrorState) -> Result<(), String> {
        self.filter.load(state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::IDLE;
    use crate::testing;

    const MOVING: [f64; 9] = [13.0, 9.0, 4.0, 3.0, 3.0, 2.0, 2.0, 2.0, 2.0];
    const STILL: [f64; 9] = [0.0, 0.0, 6.0, 5.0, 5.0, 5.0, 4.0, 4.0, 4.0];

    fn frame(tr: &mut Tracker, t: f64, scale: f64, target: bool) {
        let (m, s) = (MOVING.map(|x| x * scale), STILL.map(|x| x * scale));
        tr.ld_frame(0, t, Some((&m, &s)), target);
    }

    /// The time and the time x energy of cell 0 (moving gate 0) gathered so far.
    fn gathered(tr: &Tracker) -> (f64, f64) {
        let st = tr.ld_sensor(0).stats();
        (st.time, st.t_e[0])
    }

    fn close(got: (f64, f64), want: (f64, f64)) {
        assert!(
            (got.0 - want.0).abs() < 1e-12 && (got.1 - want.1).abs() < 1e-9,
            "{got:?} against {want:?}"
        );
    }

    #[test]
    fn a_frame_stands_for_the_time_since_the_last_one_a_silence_for_the_frame_before_it() {
        let mut tr = Tracker::new(testing::flat());
        let fr = tr.world().params.ld_frame;
        // the first frame (or after a gap longer than IDLE): one frame, and from now on the LD2450
        // has seen nothing
        frame(&mut tr, 10.0, 1.0, false);
        close(gathered(&tr), (fr, fr * 13.0));
        assert_eq!(tr.ld_collect(0).target_t, 10.0);
        // the next frame: the time since the last one
        frame(&mut tr, 10.1, 1.0, true);
        close(gathered(&tr), (fr + 0.1, (fr + 0.1) * 13.0));
        assert_eq!(tr.ld_collect(0).target_t, 10.1);
        // after a heartbeat's silence of 3 s: the silence the energies of the frame before it
        frame(&mut tr, 13.1, 2.0, false);
        close(
            gathered(&tr),
            (fr + 3.1, (fr + 0.1) * 13.0 + (3.0 - fr) * 13.0 + fr * 26.0),
        );
        assert_eq!(tr.ld_collect(0).target_t, 10.1, "a silence is no target out of sight");
        // all 0: the firmware's "no value", nothing gathered; the silence after it stands for nothing before
        frame(&mut tr, 13.2, 0.0, false);
        close(
            gathered(&tr),
            (fr + 3.1, (fr + 0.1) * 13.0 + (3.0 - fr) * 13.0 + fr * 26.0),
        );
        assert!(tr.ld_collect(0).last.is_none());
        frame(&mut tr, 15.2, 1.0, false);
        close(
            gathered(&tr),
            (fr + 5.1, (fr + 0.1) * 13.0 + (3.0 - fr) * 13.0 + fr * 26.0 + 2.0 * 13.0),
        );
        // a gap longer than IDLE is lost data: one frame
        frame(&mut tr, 15.2 + IDLE + 0.5, 1.0, false);
        close(
            gathered(&tr),
            (
                2.0 * fr + 5.1,
                (fr + 0.1) * 13.0 + (3.0 - fr) * 13.0 + fr * 26.0 + 2.0 * 13.0 + fr * 13.0,
            ),
        );
        assert_eq!(tr.ld_collect(0).target_t, 15.2 + IDLE + 0.5);
        // a frame not later than the last, and one without gates, add nothing
        let before = gathered(&tr);
        frame(&mut tr, 15.2, 1.0, true);
        tr.ld_frame(0, 30.0, None, true);
        close(gathered(&tr), before);
        assert!(tr.ld_collect(0).last.is_none());
    }
}
