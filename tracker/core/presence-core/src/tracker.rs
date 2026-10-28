//! The filter with what it runs in (docs/rust-core.md 2, 4): the world, the sensors' state and the
//! filter, and what a frame and time going on do to them; the outputs (MODEL.md 6). A frame is read
//! here (frames.rs, the LD2450's tracks); until the ghost map is in Rust (phase 8) the shell orders the
//! steps of the evidence it brings, so the map learns between them as in the Python filter
//! (docs/rust-core.md 2.0); each step is one call here and reads the sensor's last frame.

use crate::filter::LdLearn;
use crate::filter::branch::{Category, Found, Frame};
use crate::filter::evidence::Ended;
use crate::filter::mixture::SegId;
use crate::filter::{Filter, NotFinite, PeopleState, Start};
use crate::frames::{self, FrameError, Ld2410, LdGates};
use crate::geometry::Point;
use crate::ld2410::CELLS;
use crate::ld2410::sensor::LdCollect;
use crate::ld2450::tracks::{LOST, SegIds, Sighting};
use crate::outputs::{self, Cloud, Distributions, Entering, HouseNumbers, OutputCache, Person, Source, ZoneNumbers};
use crate::plan::Place;
use crate::room_seen::RoomSeen;
use crate::sensors::{SensorFrame, SensorSet, SensorState};
use crate::world::World;

/// A sensor silent for more than LOST s whose tracks the filter still has (Tracker._end_silent): they
/// end for lost data at its last frame + LOST.
#[derive(Debug, Clone, PartialEq)]
pub struct DataLost {
    pub si: usize,
    pub since: f64,
    pub live: Vec<SegId>,
}

#[derive(Debug)]
pub struct Tracker {
    world: World,
    filter: Filter,
    sensors: Vec<SensorState>,
    seen: RoomSeen,
    outputs: OutputCache,
    seg_ids: SegIds,
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
            seg_ids: SegIds::default(),
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

    /// What goes on from an earlier tracker (the shell's rebuild): what its rooms' own LD2450s saw
    /// and when its sensors were last heard, in this plan; the ids of new tracks.
    pub fn adopt(&mut self, old: &mut Tracker) {
        self.seen = std::mem::take(&mut old.seen);
        self.seen.follow(&self.world.plan);
        self.seg_ids = old.seg_ids;
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

    /// A frame of sensor si at t (Tracker.process_frame): its targets in the house frame, what they
    /// say about its LD2450's tracks; its room is seen if it measured somebody in it (output only).
    /// Kept as the sensor's last frame for the steps of its evidence. Err, and nothing changed, for a
    /// sensor there is not or a time that is no number.
    pub fn frame(&mut self, si: usize, t: f64, frame: &frames::Frame) -> Result<&SensorFrame, FrameError> {
        if si >= self.sensors.len() {
            return Err(FrameError::NoSensor(si));
        }
        if !t.is_finite() {
            return Err(FrameError::Time);
        }
        let detections = frames::detections(&self.world, si, &frame.targets);
        let seen: Vec<Sighting> = detections
            .iter()
            .map(|d| Sighting {
                target: d.target,
                visible: !d.hidden,
            })
            .collect();
        let sensor = &mut self.sensors[si];
        let events = sensor.tracks.update(t, &seen, &mut self.seg_ids);
        sensor.frame = SensorFrame {
            detections,
            events,
            ld2410: frame.ld2410,
        };
        let measured: Vec<Point> = sensor.frame.measured().map(|d| d.pos).collect();
        self.seen.frame(&self.world.plan, si, t, &measured);
        Ok(&self.sensors[si].frame)
    }

    /// Whether the last frame of sensor si concerns everybody (Tracker._evidence): a track was born or
    /// ended, or one the filter holds lost was measured (found again: a branch).
    pub fn concerns_all(&self, si: usize) -> bool {
        let ev = &self.sensors[si].frame.events;
        let tracks = self.filter.tracks();
        !ev.born.is_empty()
            || !ev.ended.is_empty()
            || ev
                .measured
                .iter()
                .any(|m| tracks.get(m.seg).is_some_and(|i| i.lost.is_some()))
    }

    /// What the last frame of sensor si says about its live tracks (Tracker._evidence step 2): a
    /// measured track updates its owners; one that was lost and is measured again is found again,
    /// returned to be branched on. Tracks the filter does not know (begun before a start, or of a
    /// sensor that changed) say nothing, as in the Python filter.
    pub fn track_events(&mut self, si: usize, t: f64) -> Vec<(SegId, Point)> {
        let frame = &self.sensors[si].frame;
        let at = |det: usize| frame.detections[det].pos;
        let mut refound = Vec::new();
        for m in &frame.events.measured {
            match self.filter.tracks().get(m.seg).map(|i| i.lost.is_some()) {
                Some(false) => self.filter.measured(&self.world, &self.sensors, m.seg, at(m.det), t),
                Some(true) => refound.push((m.seg, at(m.det))),
                None => {}
            }
        }
        for l in &frame.events.lost {
            if self.filter.tracks().get(l.seg).is_some() {
                self.filter.lost(l.seg, l.kind, at(l.det), t);
            }
        }
        for h in &frame.events.held {
            if self.filter.tracks().get(h.seg).is_some() {
                self.filter.held(&self.world, si, h.seg, at(h.det), t);
            }
        }
        refound
    }

    /// The sensors without a frame for more than LOST s at t whose tracks the filter still has
    /// (Tracker._end_silent): their LD2450 tracks are forgotten (the next frame is a start); the
    /// filter's end at their last frame + LOST, as the next frame would end them, without waiting for
    /// it. Until 0.18 they lived on while it was silent: a board that died while tracking somebody
    /// held them as a person with a track for hours.
    pub fn data_lost(&mut self, t: f64) -> Vec<DataLost> {
        let mut out = Vec::new();
        for (si, s) in self.sensors.iter_mut().enumerate() {
            let live = self.filter.tracks().of(si);
            if s.tracks.silent(t) && !live.is_empty() {
                out.push(DataLost {
                    si,
                    since: s.tracks.last() + LOST,
                    live: live.to_vec(),
                });
                s.tracks.drop_raw();
            }
        }
        out
    }

    /// The LD2450 tracks of sensor si start over (its next frame is a start, as after lost data).
    pub fn reset_tracks(&mut self, si: usize) {
        self.sensors[si].tracks = Default::default();
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

    /// The LD2410C part of the last frame of sensor si (LdSensor::frame), if it has one; whether its
    /// LD2450 has anything in sight is the background's anchor.
    pub fn ld_frame(&mut self, si: usize, t: f64) {
        let frame = &self.sensors[si].frame;
        let target = frame.target_in_sight();
        match frame.ld2410 {
            Ld2410::Absent => {}
            Ld2410::NoGates => self.gather_ld(si, t, None, target),
            Ld2410::Gates(g) => self.gather_ld(si, t, Some(&g), target),
        }
    }

    /// The energies of a frame of sensor si's LD2410C.
    pub(crate) fn gather_ld(&mut self, si: usize, t: f64, gates: Option<&LdGates>, target: bool) {
        let frame = self.world.params.ld_frame;
        self.sensors[si]
            .ld
            .frame(t, gates.map(|g| (&g.moving, &g.still)), target, frame);
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
        let g = LdGates {
            moving: MOVING.map(|x| x * scale),
            still: STILL.map(|x| x * scale),
        };
        tr.gather_ld(0, t, Some(&g), target);
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
        tr.gather_ld(0, 30.0, None, true);
        close(gathered(&tr), before);
        assert!(tr.ld_collect(0).last.is_none());
    }
}

#[cfg(test)]
mod frame_tests {
    use super::*;
    use crate::filter::mixture::NewTrack;
    use crate::frames::Target;
    use crate::params::FRAME;
    use crate::testing;

    /// A frame with one target at (x, y) mm in the sensor's frame.
    fn one(x: f64, y: f64) -> frames::Frame {
        frames::Frame {
            targets: vec![Target {
                slot: 1,
                x,
                y,
                speed: 0.0,
            }],
            ld2410: Ld2410::Absent,
        }
    }

    #[test]
    fn a_frame_is_read_kept_and_its_room_seen() {
        let mut tr = Tracker::new(testing::flat());
        assert_eq!(tr.frame(5, 1.0, &one(0.0, 2500.0)).err(), Some(FrameError::NoSensor(5)));
        assert_eq!(tr.frame(0, f64::NAN, &one(0.0, 2500.0)).err(), Some(FrameError::Time));
        let read = tr.frame(0, 1.0, &one(0.0, 2500.0)).expect("a frame");
        assert_eq!(read.events.born.len(), 1);
        assert!(read.target_in_sight() && read.measured().count() == 1);
        assert!(tr.concerns_all(0), "a track was born");
        assert_eq!(tr.seen().seen(&tr.world().plan, 1.0, 10.0), vec![true]);
        // held (the same raw numbers: frozen) is no measurement
        let read = tr.frame(0, 1.1, &one(0.0, 2500.0)).expect("a frame");
        assert_eq!(read.events.lost.len(), 1);
        assert_eq!(read.measured().count(), 0);
    }

    #[test]
    fn a_silent_sensor_drops_its_tracks_only_where_the_filter_has_them() {
        let mut tr = Tracker::new(testing::flat());
        tr.reset_people(1.0, Some(&[Start::Anywhere])).expect("a start");
        let born = tr.frame(0, 1.0, &one(0.0, 2500.0)).expect("a frame").events.born[0];
        let z = tr.sensors[0].frame.detections[born.det].pos;
        let found = Found {
            track: NewTrack {
                seg: born.seg,
                z,
                var: 0.04,
            },
            map_rate: 1.8e-5,
        };
        let frame = Frame {
            si: 0,
            t: 1.0,
            last: FRAME,
            censored: true,
            skip: &[born.seg],
            prior_rate: 1.8e-5,
        };
        tr.branch_all(&frame, &[found]).expect("finite");
        // sensor 1 tracks somebody the filter does not know
        tr.frame(1, 1.0, &one(0.0, 2500.0)).expect("a frame");
        assert!(tr.data_lost(1.0 + LOST).is_empty(), "not silent for more than LOST yet");
        let lost = tr.data_lost(1.5 + LOST);
        assert_eq!(
            lost,
            vec![DataLost {
                si: 0,
                since: 1.0 + LOST,
                live: vec![born.seg]
            }]
        );
        // sensor 0's tracks were forgotten: its next frame ends nothing; sensor 1 kept them
        assert!(
            tr.frame(0, 8.0, &one(0.0, 2500.0))
                .expect("a frame")
                .events
                .ended
                .is_empty()
        );
        let ev = &tr.frame(1, 8.0, &one(0.0, 2500.0)).expect("a frame").events;
        assert!(ev.data_lost && ev.ended.len() == 1);
        // the ids of new tracks go on in a rebuilt tracker
        let mut rebuilt = Tracker::new(testing::flat());
        rebuilt.adopt(&mut tr);
        let next = rebuilt
            .frame(0, 9.0, &one(1000.0, 2500.0))
            .expect("a frame")
            .events
            .born[0]
            .seg;
        assert_eq!(next, SegId(5));
    }
}
