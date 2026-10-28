//! The filter with what it runs in (docs/rust-core.md 2, 4): the world, the sensors' state and the
//! filter, and what a frame and time going on do to them; the outputs (MODEL.md 6). Until the ghost
//! map is in Rust (phase 8) the shell orders the steps of a frame, so the map learns between them as
//! in the Python filter (docs/rust-core.md 2.0); each step is one call here.

use crate::filter::LdLearn;
use crate::filter::branch::{Category, Found, Frame};
use crate::filter::evidence::Ended;
use crate::filter::mixture::SegId;
use crate::filter::{Filter, NotFinite, PeopleState, Start};
use crate::geometry::Point;
use crate::ld2410::CELLS;
use crate::ld2410::sensor::LdCollect;
use crate::outputs::{self, Cloud, Distributions, Entering, HouseNumbers, OutputCache, Person, Source, ZoneNumbers};
use crate::params::LostKind;
use crate::plan::Place;
use crate::room_seen::RoomSeen;
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
    seen: RoomSeen,
    outputs: OutputCache,
}

impl Tracker {
    /// A tracker without hypotheses and without frames.
    pub fn new(world: World) -> Self {
        let n = world.sights.len();
        let filter = Filter::new(world.density_shape(), n);
        let sensors = vec![SensorState::new(&world.params); n];
        Self {
            world,
            filter,
            sensors,
            seen: RoomSeen::default(),
            outputs: OutputCache::default(),
        }
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

    /// A world rebuilt on the same tiles (sensors recalibrated, the outputs' numbers changed): what is
    /// known stays. Err, and nothing changed, if the densities would not fit it: another shape of a
    /// density, other tiles (Tiles::same_as) or another number of sensors.
    pub fn set_world(&mut self, world: World) -> Result<(), String> {
        let (old, new) = (&self.world, &world);
        if new.density_shape() != old.density_shape()
            || !new.tiles.same_as(&old.tiles)
            || new.sights.len() != old.sights.len()
        {
            return Err("the new world has other tiles or sensors: the people do not fit it".into());
        }
        self.world = world;
        self.seen.follow(&self.world.plan);
        self.filter.void_cache();
        Ok(())
    }

    /// What an earlier tracker's rooms' own LD2450s saw and when its sensors were last heard (the
    /// shell's rebuild): it goes on in this plan.
    pub fn adopt_seen(&mut self, mut seen: RoomSeen) {
        seen.follow(&self.world.plan);
        self.seen = seen;
    }

    /// What the rooms' own LD2450s saw, taken out (for adopt_seen).
    pub fn take_seen(&mut self) -> RoomSeen {
        std::mem::take(&mut self.seen)
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

    /// Start over at t (Tracker.reset_people): nothing known about the people, or known people at
    /// these places and nobody unknown; Err, and nothing changed, for a place there is not.
    pub fn reset_people(&mut self, t: f64, people: Option<&[Start]>) -> Result<(), String> {
        let regions = self.world.plan.regions.len();
        let unknown = |s: &Start| matches!(s, Start::Place(Place::Region(r)) if r.0 >= regions);
        if people.into_iter().flatten().any(unknown) {
            return Err("a region there is not".into());
        }
        self.clear(t);
        self.filter.start_over(&self.world, people);
        Ok(())
    }

    /// Start over at t from a saved state (Tracker.restore_people); Err, and nothing changed, if it
    /// does not fit.
    pub fn restore(&mut self, t: f64, state: PeopleState) -> Result<(), String> {
        let state = state.check(&self.world.density_shape())?;
        self.clear(t);
        self.filter.restore(state);
        Ok(())
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

    /// The hypotheses from a mirror of them (tests: Filter::load).
    #[cfg(test)]
    pub fn load(&mut self, state: crate::filter::mirror::MirrorState) -> Result<(), String> {
        self.filter.load(state)
    }

    /// Spoils every hypothesis' weight (NaN): what the app does then (the shell's test).
    #[doc(hidden)]
    pub fn spoil_weights_for_test(&mut self) {
        self.filter.spoil_weights_for_test();
    }

    // ------------------------------------------------------------- outputs (MODEL.md 6)

    /// Per observed room the distribution of the number of people in it, then of those in view at
    /// all; computed once per epoch (Tracker.count_distribution).
    pub fn count_distribution(&mut self) -> &Distributions {
        self.outputs.at(self.filter.epoch()).counts(&self.filter, &self.world)
    }

    /// Per place the distribution of the number of people there, then of those in the house; once
    /// per epoch (Tracker.place_distribution).
    pub fn place_distribution(&mut self) -> &Distributions {
        self.outputs.at(self.filter.epoch()).places(&self.filter)
    }

    /// "wird betreten" per zone of the world's targets; once per epoch (Tracker.entering). Its
    /// look-ahead is the world's: a new one comes with a new world, and that voids the cache.
    pub fn entering(&mut self) -> &[Entering] {
        self.outputs.at(self.filter.epoch()).entering(&self.filter, &self.world)
    }

    /// Per observed room whether it is occupied at `now` and by what (Tracker.occupancy): the
    /// filter by the cost of the light, or its own LD2450 within the last seen_hold s.
    pub fn occupancy(&mut self, now: f64) -> Vec<Option<Source>> {
        let o = self.world.outputs;
        let seen = self.seen.seen(&self.world.plan, now, o.seen_hold);
        outputs::occupancy(self.count_distribution(), &seen, o.light_cost)
    }

    /// The known people of the most probable hypothesis for the display (Tracker.persons).
    pub fn persons(&self) -> Vec<Person> {
        outputs::persons(&self.filter, &self.world)
    }

    /// Where they may be, for the map (Tracker._cloud).
    pub fn clouds(&self) -> Vec<Cloud> {
        outputs::clouds(&self.filter, &self.world)
    }

    /// The people with tracks who walk, for the shell's "Ziel" until the destination map is in Rust.
    pub fn walkers(&self) -> outputs::Walkers {
        outputs::walkers(&self.filter)
    }

    /// The numbers of the zones of the home and of the house at `now` (Tracker.zone_states); `start`
    /// the model's (None: not started, nothing silent).
    pub fn zone_numbers(&mut self, now: f64, start: Option<f64>) -> (Vec<ZoneNumbers>, HouseNumbers) {
        let occupancy = self.occupancy(now);
        let silent = self.silent(now, start);
        outputs::zones_of(&mut self.outputs, &self.filter, &self.world, &occupancy, &silent)
    }

    /// A frame of the sensor with this id at t, whatever is in it (also of a sensor the plan lacks).
    pub fn heard_from(&mut self, id: &str, t: f64) {
        self.seen.heard_from(id, t);
    }

    /// The targets sensor si measured in a frame at t (not held, not hidden): its room is seen.
    pub fn seen_frame(&mut self, si: usize, t: f64, measured: &[Point]) {
        self.seen.frame(&self.world.plan, si, t, measured);
    }

    /// What the rooms' own LD2450s saw (RoomSeen).
    pub fn seen(&self) -> &RoomSeen {
        &self.seen
    }

    /// Per observed room whether its own sensors all went silent (the world's silent_after).
    pub fn silent(&self, now: f64, start: Option<f64>) -> Vec<bool> {
        self.seen
            .silent(&self.world.plan, now, start, self.world.outputs.silent_after)
    }

    /// Whether the sensor with this id sent a frame lately (the world's silent_after).
    pub fn online(&self, id: &str, now: f64, start: Option<f64>) -> bool {
        self.seen.online(id, now, start, self.world.outputs.silent_after)
    }

    /// Per observed room whether it has an own sensor in use.
    pub fn watched(&self) -> Vec<bool> {
        RoomSeen::watched(&self.world.plan)
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
