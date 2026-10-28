//! The filter with what it runs in (docs/rust-core.md 2, 4): the world, the sensors' state, the filter
//! and what it learns, and what a frame and time going on do to them (filter.Tracker.process_frame,
//! step); the outputs (MODEL.md 6). Each step lends the filter and the one learner it feeds, so they
//! learn at the places of the Python filter (docs/rust-core.md 2.0); what the shell's listeners hear is
//! returned as events.

use serde_json::Value;

use crate::filter::branch::{Category, Found, Frame};
use crate::filter::mixture::{NewTrack, SegId};
use crate::filter::{Filter, NotFinite, PeopleState, Start};
use crate::frames::{self, CallError, LdGates};
use crate::geometry::Point;
use crate::ld2410::background::{Background, Weighed};
use crate::ld2410::sensor::LdCollect;
use crate::ld2450::tracks::{LOST, SegIds, Sighting};
use crate::learn::destination::Target;
use crate::learn::ghost_map::GhostMap;
use crate::learn::pauses::Pauses;
use crate::learn::{Learned, LearnedError};
use crate::outputs::{self, Cloud, Distributions, Entering, HouseNumbers, OutputCache, Person, Source, ZoneNumbers};
use crate::params::{FRAME, IDLE};
use crate::plan::Place;
use crate::room_seen::RoomSeen;
use crate::sensors::{SensorFrame, SensorSet, SensorState};
use crate::vmath::py_hypot;
use crate::world::World;

/// s: known people who almost surely do not exist join the unknown ones this often (filter.RECYCLE_EVERY)
const RECYCLE_EVERY: f64 = 1.0;

/// What the shell's listeners hear (filter.Tracker.listeners): watching and the ends of tracks only
/// outside a pause (nothing learned from them then), the branches always.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// sensor si watched delta s and no ghost track was born
    Watch { si: usize, delta: f64 },
    /// a track of sensor si ended: where it began, P(ghost) as the ghost map learns it, how long it lived
    TrackEnd {
        si: usize,
        z0: Point,
        p_ghost: f64,
        life: f64,
        data_lost: bool,
    },
    /// a track was born or found again at z: the children the hypotheses branched into
    Branch {
        si: usize,
        seg: SegId,
        z: Point,
        how: Branched,
        children: Vec<Child>,
    },
}

/// Why a track branched the hypotheses.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Branched {
    /// a new track; the ghost map's rate of ghost births where it began (per m^2 and s)
    Born { map_rate: f64 },
    /// a lost track measured again
    FoundAgain,
}

/// A child hypothesis of a branch: its log weight and what it says the track is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Child {
    pub logw: f64,
    pub category: Category,
}

/// Why a frame or a step was not taken: a call that makes no sense, or weights that are no numbers
/// (the app starts the model over).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Fault {
    Call(CallError),
    NotFinite(NotFinite),
}

impl From<NotFinite> for Fault {
    fn from(e: NotFinite) -> Self {
        Self::NotFinite(e)
    }
}

impl From<CallError> for Fault {
    fn from(e: CallError) -> Self {
        Self::Call(e)
    }
}

/// The model's time: when it started (its first frame; None: not yet), now, and when its periodic
/// steps were last taken.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Clock {
    start: Option<f64>,
    now: f64,
    recycled: f64,
    walks_counted: f64,
    /// the time of a restored state while the model has not started: the gap to its first frame is
    /// still to move over
    gap_from: Option<f64>,
}

impl Default for Clock {
    fn default() -> Self {
        Self {
            start: None,
            now: 0.0,
            recycled: 0.0,
            walks_counted: f64::NEG_INFINITY,
            gap_from: None,
        }
    }
}

/// A sensor silent for more than LOST s whose tracks the filter still has (Tracker._end_silent): they
/// end for lost data at its last frame + LOST.
#[derive(Debug, Clone, PartialEq)]
struct DataLost {
    si: usize,
    since: f64,
    live: Vec<SegId>,
}

#[derive(Debug)]
pub struct Tracker {
    world: World,
    filter: Filter,
    sensors: Vec<SensorState>,
    seen: RoomSeen,
    outputs: OutputCache,
    seg_ids: SegIds,
    clock: Clock,
    learned: Learned,
    pauses: Pauses,
}

impl Tracker {
    /// A tracker without hypotheses, without frames, nothing learned, not started.
    pub fn new(world: World) -> Self {
        let filter = Filter::new(world.density_shape(), world.sights.len());
        let sensors = sensor_states(&world);
        Self {
            learned: Learned::new(&world),
            world,
            filter,
            sensors,
            seen: RoomSeen::default(),
            outputs: OutputCache::default(),
            seg_ids: SegIds::default(),
            clock: Clock::default(),
            pauses: Pauses::default(),
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

    /// When the model started (its first frame; None: not yet) and its time now.
    pub fn start(&self) -> Option<f64> {
        self.clock.start
    }

    pub fn now(&self) -> f64 {
        self.clock.now
    }

    /// The model starts at t (the first frame, or given by a tool): what a restored state was saved
    /// at is moved over to t.
    pub fn start_at(&mut self, t: f64) {
        self.clock.start = Some(t);
        (self.clock.now, self.clock.recycled) = (t, t);
        self.filter.set_flushed(t);
        if let Some(saved) = self.clock.gap_from.take() {
            self.predict_gap(t - saved);
        }
    }

    /// A world rebuilt on the same tiles (sensors recalibrated, the outputs' numbers changed): what is
    /// known stays, and what was learned for the sensors that hang where they hung. Err, and nothing
    /// changed, if the densities would not fit it: another shape of a density, other tiles
    /// (Tiles::same_as) or another number of sensors.
    pub fn set_world(&mut self, world: World) -> Result<(), String> {
        let (old, new) = (&self.world, &world);
        if new.density_shape() != old.density_shape()
            || !new.tiles.same_as(&old.tiles)
            || new.sights.len() != old.sights.len()
        {
            return Err("the new world has other tiles or sensors: the people do not fit it".into());
        }
        self.world = world;
        for (s, plan) in self.sensors.iter_mut().zip(&self.world.plan.sensors) {
            s.active = plan.in_use;
        }
        self.seen.follow(&self.world.plan);
        self.learned.follow(&self.world);
        self.filter.void_cache();
        self.outputs = OutputCache::default();
        Ok(())
    }

    /// What goes on from an earlier tracker (the shell's rebuild): what its rooms' own LD2450s saw,
    /// the ids of new tracks, the model's time and evidence, what was learned (for this world) and the
    /// pauses, what its sensors' LD2410Cs gathered (by sensor id).
    pub fn adopt(&mut self, old: &mut Tracker) {
        self.seen = std::mem::take(&mut old.seen);
        self.seen.follow(&self.world.plan);
        self.seg_ids = old.seg_ids;
        self.clock = old.clock;
        self.filter.set_loglik(old.filter.loglik());
        std::mem::swap(&mut self.learned, &mut old.learned); // the old tracker is dropped right after
        self.learned.follow(&self.world);
        self.pauses = std::mem::take(&mut old.pauses);
        for (s, plan) in self.sensors.iter_mut().zip(&self.world.plan.sensors) {
            let was = old.world.plan.sensors.iter().position(|o| o.id == plan.id);
            if let Some(k) = was {
                s.ld.collect = old.sensors[k].ld.collect;
            }
        }
    }

    /// The intervals in which nothing is learned (the app's switch, the tools' known ones).
    pub fn set_pauses(&mut self, pauses: Pauses) {
        self.pauses = pauses;
    }

    /// Time of the last frame of sensor si (-inf: none since the start or its tracks' reset).
    pub fn last_frame(&self, si: usize) -> f64 {
        self.sensors[si].last_frame()
    }

    /// No live tracks, nothing moved since t, nothing of the LD2410Cs gathered (Tracker._clear).
    fn clear(&mut self, t: f64) {
        self.filter.clear(t);
        for s in &mut self.sensors {
            s.ld.start_over(&self.world.params);
        }
    }

    /// What a run keeps besides the people, after the model started over (Tracker._forget).
    fn forget_run(&mut self) {
        self.clock.recycled = self.clock.now;
        self.clock.gap_from = None;
        self.clock.walks_counted = f64::NEG_INFINITY;
        self.learned.destinations.drop_walks();
        self.outputs = OutputCache::default();
    }

    /// Start over now (Tracker.reset_people): nothing known about the people, or known people at
    /// these places and nobody unknown; Err, and nothing changed, for a place there is not.
    pub fn reset_people(&mut self, people: Option<&[Start]>) -> Result<(), String> {
        let regions = self.world.plan.regions.len();
        let unknown = |s: &Start| matches!(s, Start::Place(Place::Region(r)) if r.0 >= regions);
        if people.into_iter().flatten().any(unknown) {
            return Err("a region there is not".into());
        }
        self.clear(self.clock.now);
        self.filter.start_over(&self.world, people);
        self.forget_run();
        Ok(())
    }

    /// Start over now from a state saved at `saved` (Tracker.restore_people), moved on over the time
    /// between (a gap in the data says nothing, MODEL.md 4.4) - at the first frame if the model has not
    /// started yet. Err, and nothing changed, if it does not fit.
    pub fn restore(&mut self, state: PeopleState, saved: f64) -> Result<(), String> {
        if !saved.is_finite() {
            return Err("the time of the saved state is no number".into());
        }
        let state = state.check(&self.world.density_shape())?;
        self.clear(self.clock.now);
        self.filter.restore(state);
        self.forget_run();
        match self.clock.start {
            None => self.clock.gap_from = Some(saved),
            Some(_) => self.predict_gap(self.clock.now - saved),
        }
        Ok(())
    }

    /// Everybody moves on over a gap in the data (MODEL.md 4.4, 5.3): what the sensors placed could
    /// have tested lasts.
    fn predict_gap(&mut self, gap: f64) {
        if gap > 0.0 {
            let placed: SensorSet = (0..self.sensors.len()).filter(|&si| self.sensors[si].active).collect();
            self.filter.predict_gap(&self.world, placed, gap);
        }
    }

    pub fn people_state(&mut self) -> PeopleState {
        self.filter.people_state(&self.world)
    }

    /// What is known about the people in the saved format (people.json; Tracker.people_state), at the
    /// time of the last move of everybody. Err if a density is no density.
    pub fn saved_people(&mut self) -> Result<Value, String> {
        let t = self.filter.flushed();
        self.people_state().to_json(t, &self.world.fingerprint())
    }

    /// Start over from a state in the saved format (Tracker.restore_people); Err, and nothing changed,
    /// if it cannot be read, was saved on other tiles or does not fit.
    pub fn restore_saved(&mut self, saved: &Value) -> Result<(), String> {
        let (t, state) = PeopleState::from_json(saved, &self.world.density_shape(), &self.world.fingerprint())
            .map_err(|e| e.to_string())?;
        self.restore(state, t)
    }

    /// The LD2450 tracks of all sensors start over (app "Tracks zurücksetzen"): their next frames are a
    /// start, so whoever a sensor is tracking now counts.
    pub fn reset_tracks(&mut self) {
        for s in &mut self.sensors {
            s.tracks = Default::default();
        }
    }

    // ------------------------------------------------------------------ frames and time

    /// A frame of the sensor with this id at t (Tracker.process_frame): the model starts at the
    /// first; a sensor of the plan in use moves everybody on to t, then its frame is evidence (MODEL.md
    /// 4.1, 4.2) and its LD2410C part is gathered. Its targets are read also when it is not in use (the
    /// display); a sensor the plan lacks only counts as heard.
    pub fn frame(&mut self, id: &str, t: f64, frame: &frames::Frame) -> Result<Vec<Event>, Fault> {
        if !t.is_finite() {
            return Err(CallError::Time.into());
        }
        if self.clock.start.is_none() {
            self.start_at(t);
        }
        self.seen.heard_from(id, t); // its room is not silent (output only, roomseen.py)
        let Some(si) = self.world.plan.sensors.iter().position(|s| s.id == id) else {
            return Ok(Vec::new());
        };
        let mut events = Vec::new();
        self.end_silent(t, &mut events)?; // this sensor's own tracks too, if its last frame is too long ago
        let prev = self.sensors[si].last_frame();
        if self.sensors[si].active && t > self.clock.now {
            // the time since this sensor's last frame counts as watched by it (MODEL.md 4.4); it is
            // read after this: the move sees it with its last frame before
            events.extend(self.step(t)?);
        }
        self.read(si, t, frame);
        if !self.sensors[si].active {
            return Ok(events);
        }
        let gap = t - prev;
        let delta = if gap <= IDLE { gap } else { 0.0 }; // longer: lost data, says nothing
        self.evidence(si, t, delta, &mut events)?;
        self.ld_frame(si);
        Ok(events)
    }

    /// The last frame of sensor si as read (its detections for the display).
    pub fn sensor_frame(&self, si: usize) -> &SensorFrame {
        &self.sensors[si].frame
    }

    /// Reads a frame of sensor si: its targets in the house frame, what they say about its LD2450's
    /// tracks; its room is seen if it measured somebody in it (output only). Kept as the sensor's last
    /// frame for the steps of its evidence.
    fn read(&mut self, si: usize, t: f64, frame: &frames::Frame) {
        let detections = frames::detections(&self.world, si, frame);
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
            t,
            measured: (0..detections.len()).map(|j| events.measures(j)).collect(),
            detections,
            events,
            ld2410: frame.ld2410,
        };
        let measured: Vec<Point> = sensor.frame.measured().map(|d| d.pos).collect();
        self.seen.frame(&self.world.plan, si, t, &measured);
    }

    /// Time goes on to t (Tracker.step, MODEL.md 3): everybody is moved and weighed by the tracks not
    /// started meanwhile together, at most max_step apart; the known people who almost surely do not
    /// exist are given back every RECYCLE_EVERY s; the walks are counted into the destination map.
    pub fn step(&mut self, t: f64) -> Result<Vec<Event>, NotFinite> {
        let mut events = Vec::new();
        if self.clock.start.is_none() || !(t > self.clock.now) {
            return Ok(events);
        }
        self.end_silent(t, &mut events)?;
        self.clock.now = t;
        if t - self.filter.flushed() >= self.world.params.max_step - 1e-9 {
            self.flush_to(t, false)?;
        }
        if t - self.clock.recycled >= RECYCLE_EVERY {
            self.clock.recycled = t;
            if self.filter.recycle(self.world.params.give_up) {
                self.flush_to(t, false)?;
                self.filter.merge();
            }
        }
        if t - self.clock.walks_counted >= self.world.params.max_step - 1e-9 {
            // the walks counted into the destination map (MODEL.md 6 "Ziel"): reads the hypotheses only
            self.clock.walks_counted = t;
            if self.pauses.paused(t, t) {
                self.learned.destinations.drop_walks(); // none counted
            } else {
                self.learned.destinations.observe(&self.filter, &self.world, t);
            }
        }
        Ok(events)
    }

    /// Everybody moved to t (in parts no longer than max_step), then the LD2410Cs that gathered long
    /// enough are weighed, and their backgrounds learn (Tracker._flush). event: what a frame brings
    /// concerns everybody (what is cached per hypothesis is void even if nobody moves).
    fn flush_to(&mut self, t: f64, event: bool) -> Result<(), NotFinite> {
        let moves = t > self.filter.flushed();
        if !(moves || event) {
            return Ok(());
        }
        self.filter.flush(&self.world, &self.sensors, t);
        if event {
            self.filter.void_cache();
        }
        if !moves {
            return Ok(());
        }
        let every = self.world.params.ld_every;
        for si in 0..self.sensors.len() {
            if self.sensors[si].ld.due(every) {
                self.ld_weigh(si)?;
            }
        }
        Ok(())
    }

    /// The LD2410C si's energies since they were last weighed (MODEL.md 4.3); its background learns from
    /// b and the means from before the frames and from the echo rate read before the echo sources moved
    /// on (docs/rust-core.md 2.0); nothing while learning is paused.
    fn ld_weigh(&mut self, si: usize) -> Result<(), NotFinite> {
        let p = &self.world.params;
        let bg: &mut Background = &mut self.learned.background;
        let (b, rate) = (bg.b(si, p), bg.rate(si, p));
        let Some(learn) = self
            .filter
            .ld_weigh(&self.world, si, &mut self.sensors[si].ld, &b, rate)?
        else {
            return Ok(());
        };
        let now = self.clock.now;
        if self.pauses.paused(now - learn.stats.time, now) {
            return Ok(());
        }
        let weighed = Weighed {
            learn: &learn,
            b: &b,
            rate,
            alpha: &self.world.ld_lik.alpha,
            quiet: now - learn.target_t,
        };
        bg.learn(si, &weighed, p);
        Ok(())
    }

    /// The sensors without a frame for more than LOST s whose tracks the filter still has
    /// (Tracker._end_silent): their tracks end at their last frame + LOST, as the next frame would end
    /// them, without waiting for it; their LD2450 tracks are forgotten (the next frame is a start).
    /// Until 0.18 they lived on while it was silent: a board that died while tracking somebody held
    /// them as a person with a track for hours.
    fn end_silent(&mut self, t: f64, events: &mut Vec<Event>) -> Result<(), NotFinite> {
        let mut lost = Vec::new();
        for (si, s) in self.sensors.iter_mut().enumerate() {
            let live = self.filter.tracks().of(si);
            if s.tracks.silent(t) && !live.is_empty() {
                lost.push(DataLost {
                    si,
                    since: s.tracks.last() + LOST,
                    live: live.to_vec(),
                });
                s.tracks.drop_raw();
            }
        }
        for DataLost { si, since, live } in lost {
            let t_end = since.max(self.clock.now);
            self.flush_to(t_end, true)?; // the end weighs and releases everybody: all at its time
            for seg in live {
                self.end(si, seg, t_end, true, events);
            }
            self.filter.merge();
            self.filter.normalize(0.0)?;
        }
        Ok(())
    }

    /// The sensor dropped a live track (Tracker._end): for a person this follows from not finding it
    /// again; a ghost died. The ghost map learns where ghosts begin, with P(ghost) as judged now but
    /// without what the map said at the birth (MODEL.md 4.2); a track of a paused time teaches nothing.
    fn end(&mut self, si: usize, seg: SegId, t: f64, data_lost: bool, events: &mut Vec<Event>) {
        if self.filter.tracks().get(seg).is_none() {
            return;
        }
        let ended = self.filter.end(&self.world, seg, t, data_lost);
        if self.pauses.paused(ended.born, t) {
            return;
        }
        if ended.p_ghost > 0.0 {
            self.learned.ghosts.add_birth(si, ended.z0, ended.p_ghost);
        }
        events.push(Event::TrackEnd {
            si,
            z0: ended.z0,
            p_ghost: ended.p_ghost,
            life: ended.life,
            data_lost,
        });
    }

    /// What the frame of sensor si says (Tracker._evidence, MODEL.md 4.1, 4.2), step by step, the
    /// ghost map learning in between (docs/rust-core.md 2.0): 1. no ghost track was born meanwhile;
    /// 2. its tracks measured, lost, held, ended; 3. nothing seen before (a start): who has no track
    /// now was not tracked; 4. new tracks and lost ones found again branch the hypotheses - the map at
    /// a birth includes this frame's ends.
    fn evidence(&mut self, si: usize, t: f64, delta: f64, events: &mut Vec<Event>) -> Result<(), NotFinite> {
        let censored = delta <= 0.0;
        if censored || self.concerns_all(si) {
            self.flush_to(t, true)?; // these weigh, branch or merge everybody: all at t
        }
        if delta > 0.0 {
            // the total is read before the map learns this time
            let total = self.learned.ghosts.total(si, &self.world.sights[si]);
            self.filter.watch(&self.world, si, delta, total);
            if !self.pauses.paused(t - delta, t) {
                self.learned.ghosts.add_watch(si, delta);
                self.learned.ghosts.fade(t);
                events.push(Event::Watch { si, delta });
            }
        }
        let refound = self.track_events(si);
        let frame = &mut self.sensors[si].frame;
        let (ended, data_lost) = (std::mem::take(&mut frame.events.ended), frame.events.data_lost);
        let ends = ended
            .iter()
            .filter(|&&seg| self.filter.tracks().get(seg).is_some())
            .count();
        for seg in ended {
            self.end(si, seg, t, data_lost, events);
        }
        if ends > 0 {
            self.filter.merge();
        }
        if censored {
            self.filter.censor(&self.world, si);
        }
        self.branch(si, t, delta, &refound, events)?;
        self.filter.normalize(0.0)
    }

    /// Whether the last frame of sensor si concerns everybody: a track was born or ended, or one the
    /// filter holds lost was measured (found again: a branch).
    fn concerns_all(&self, si: usize) -> bool {
        let ev = &self.sensors[si].frame.events;
        let tracks = self.filter.tracks();
        !ev.born.is_empty()
            || !ev.ended.is_empty()
            || ev
                .measured
                .iter()
                .any(|m| tracks.get(m.seg).is_some_and(|i| i.lost.is_some()))
    }

    /// Step 2: a measured track updates its owners; one that was lost and is measured again is found
    /// again, returned to be branched on. Tracks the filter does not know (begun before a start, or of
    /// a sensor that changed) say nothing. The events are taken out.
    fn track_events(&mut self, si: usize) -> Vec<(SegId, Point)> {
        let frame = &mut self.sensors[si].frame;
        let (t, events) = (frame.t, &mut frame.events);
        let (measured, lost, held) = (
            std::mem::take(&mut events.measured),
            std::mem::take(&mut events.lost),
            std::mem::take(&mut events.held),
        );
        let at = |det: usize| self.sensors[si].frame.detections[det].pos;
        let mut refound = Vec::new();
        for m in measured {
            match self.filter.tracks().get(m.seg).map(|i| i.lost.is_some()) {
                Some(false) => self.filter.measured(&self.world, &self.sensors, m.seg, at(m.det), t),
                Some(true) => refound.push((m.seg, at(m.det))),
                None => {}
            }
        }
        for l in lost {
            if self.filter.tracks().get(l.seg).is_some() {
                self.filter.lost(l.seg, l.kind, at(l.det), t);
            }
        }
        for h in held {
            if self.filter.tracks().get(h.seg).is_some() {
                self.filter.held(&self.world, si, h.seg, at(h.det), t);
            }
        }
        refound
    }

    /// Step 4: the tracks born in this frame and the lost ones found again branch every hypothesis; a
    /// birth with the variance of where a track sits on a person there and the ghost map's rate there.
    fn branch(
        &mut self,
        si: usize,
        t: f64,
        delta: f64,
        refound: &[(SegId, Point)],
        events: &mut Vec<Event>,
    ) -> Result<(), NotFinite> {
        let frame = &mut self.sensors[si].frame;
        let born: Vec<(SegId, Point)> = std::mem::take(&mut frame.events.born)
            .iter()
            .map(|b| (b.seg, frame.detections[b.det].pos))
            .collect();
        if refound.is_empty() && born.is_empty() {
            return Ok(());
        }
        let at = self.world.sights[si].at;
        let found: Vec<Found> = refound
            .iter()
            .map(|&(seg, z)| Found {
                track: NewTrack { seg, z, var: 0.0 },
                map_rate: 0.0,
            })
            .chain(born.iter().map(|&(seg, z)| Found {
                track: NewTrack {
                    seg,
                    z,
                    var: self.world.reading.offset_var(py_hypot(z[0] - at[0], z[1] - at[1])),
                },
                map_rate: self.learned.ghosts.rate(si, z),
            }))
            .collect();
        let censored = delta <= 0.0;
        let skip: Vec<SegId> = born.iter().map(|&(seg, _)| seg).collect();
        let frame = Frame {
            si,
            t,
            last: if censored { FRAME } else { delta.min(FRAME) },
            censored,
            skip: &skip,
            prior_rate: self.learned.ghosts.prior_rate(),
        };
        let children = self.filter.branch_all(&self.world, &frame, &found)?;
        for (k, (f, children)) in found.iter().zip(children).enumerate() {
            if !children.is_empty() {
                events.push(Event::Branch {
                    si,
                    seg: f.track.seg,
                    z: f.track.z,
                    how: if k < refound.len() {
                        Branched::FoundAgain
                    } else {
                        Branched::Born { map_rate: f.map_rate }
                    },
                    children: children
                        .into_iter()
                        .map(|(logw, category)| Child { logw, category })
                        .collect(),
                });
            }
        }
        Ok(())
    }

    /// The LD2410C part of the last frame of sensor si, if it has one; whether its LD2450 has anything
    /// in sight is the background's anchor. Taken out: a second call does nothing.
    fn ld_frame(&mut self, si: usize) {
        let frame = &mut self.sensors[si].frame;
        let target = frame.target_in_sight();
        if let Some(ld) = frame.ld2410.take() {
            let t = frame.t;
            self.gather_ld(si, t, ld.gates.as_ref(), target);
        }
    }

    /// The energies of a frame of sensor si's LD2410C.
    pub(crate) fn gather_ld(&mut self, si: usize, t: f64, gates: Option<&LdGates>, target: bool) {
        let frame = self.world.params.ld_frame;
        self.sensors[si].ld.frame(t, gates, target, frame);
    }

    /// What sensor si's LD2410C frames left.
    pub fn ld_collect(&self, si: usize) -> LdCollect {
        self.sensors[si].ld.collect
    }

    /// Sensor si's LD2410C as the filter knows it.
    pub fn ld_sensor(&self, si: usize) -> &crate::ld2410::sensor::LdSensor {
        &self.sensors[si].ld
    }

    /// Sensors changed, the people's world not (Tracker._recalibrated): the people stay as they are;
    /// the changed sensors' tracks end as at lost data (the ghost map learns from them), their next
    /// frame is a start, their LD2410C starts over; then the new world.
    pub fn recalibrate(&mut self, changed: SensorSet, world: World) -> Result<Vec<Event>, Fault> {
        let mut events = Vec::new();
        let now = self.clock.now;
        if changed.iter().any(|si| !self.filter.tracks().of(si).is_empty()) {
            self.flush_to(now, false)?; // the ends weigh and release everybody: all at now
        }
        for si in changed.iter() {
            for seg in self.filter.tracks().of(si).to_vec() {
                self.end(si, seg, now, true, &mut events);
            }
            self.sensors[si].tracks = Default::default();
            self.sensors[si].ld.start_over(&self.world.params);
        }
        if !changed.is_empty() && !self.filter.hyps().is_empty() {
            self.filter.merge();
            self.filter.prune(&self.world.params)?;
            self.filter.normalize(0.0)?;
        }
        self.set_world(world).map_err(|_| CallError::World)?;
        Ok(events)
    }

    // ------------------------------------------------------------------ what was learned

    /// Use a saved ghost map (MODEL.md 4.2) if it was learned with the prior the model has now, else
    /// the prior's (also for None); per sensor only where it hangs where it hung. Whether the saved
    /// one is used; Err, and nothing changed, if it cannot be read.
    pub fn use_ghost_map(&mut self, saved: Option<&Value>) -> Result<bool, LearnedError> {
        let plan = &self.world.plan;
        let map = saved.map(|v| GhostMap::from_json(v, plan)).transpose()?;
        let used = map.as_ref().is_some_and(|m| m.learned_with(&self.world.params));
        self.learned.ghosts = map.filter(|_| used).unwrap_or_else(|| {
            let mut prior = GhostMap::from_prior(&self.world.params);
            prior.follow(plan);
            prior
        });
        Ok(used)
    }

    /// Use a saved LD2410C background (MODEL.md 4.3), per sensor where it hangs where it hung; None:
    /// nothing learned. The parts not taken (a sensor's part out of range: dropped alone); Err, and
    /// nothing changed, if it is no background at all.
    pub fn use_background(&mut self, saved: Option<&Value>) -> Result<Vec<LearnedError>, LearnedError> {
        let plan = &self.world.plan;
        let (bg, dropped) = match saved {
            Some(v) => Background::from_json(v, plan)?,
            None => {
                let mut bg = Background::default();
                bg.follow(plan);
                (bg, Vec::new())
            }
        };
        self.learned.background = bg;
        Ok(dropped)
    }

    /// Use a saved destination map (MODEL.md 6 "Ziel") if it was learned with these rooms, doors and
    /// regions, else an empty one (also for None). Whether the saved one is used; Err, and nothing
    /// changed, if it cannot be read.
    pub fn use_dest_map(&mut self, saved: Option<&Value>) -> Result<bool, LearnedError> {
        let used = self.learned.destinations.load(saved, &self.world.destinations)?;
        self.outputs = OutputCache::default();
        Ok(used)
    }

    /// What was learned, a copy (the shell packs it in a thread while the model goes on).
    pub fn learned(&self) -> &Learned {
        &self.learned
    }

    /// The LD2410Cs learn nothing from their next `seconds` s of frames (after "Tracks zurücksetzen").
    pub fn hold_background(&mut self, seconds: f64) {
        self.learned.background.hold(seconds);
    }

    /// The background of sensor si's LD2410C cells and its echo sources' rate (per s), as the next
    /// weighing reads them.
    pub fn background(&self, si: usize) -> ([f64; crate::ld2410::CELLS], f64) {
        let (bg, p) = (&self.learned.background, &self.world.params);
        (bg.b(si, p), bg.rate(si, p))
    }

    /// The ghost map's rate (per m^2 and s) of sensor si at the points, and how long it watched (s).
    pub fn ghost_rates(&self, si: usize, points: &[Point]) -> (Vec<f64>, f64) {
        let g = &self.learned.ghosts;
        (points.iter().map(|&p| g.rate(si, p)).collect(), g.watched(si))
    }

    // ------------------------------------------------------------------ the filter's bookkeeping

    /// Hypotheses that now say the same about every live track are one (MODEL.md 5.6).
    pub fn merge(&mut self) {
        self.filter.merge();
    }

    /// Keep the strongest hypotheses (MODEL.md 5.1).
    pub fn prune(&mut self) -> Result<(), NotFinite> {
        self.filter.prune(&self.world.params)
    }

    pub fn normalize(&mut self, dropped: f64) -> Result<(), NotFinite> {
        self.filter.normalize(dropped)
    }

    pub fn hyp_weights(&self) -> Vec<f64> {
        self.filter.hyp_weights()
    }

    /// What the LD2410C of sensor si gathered (the reference test sets it).
    #[cfg(test)]
    pub(crate) fn set_ld_collect(&mut self, si: usize, collect: LdCollect) {
        self.sensors[si].ld.collect = collect;
    }

    /// The sensors whose LD2410C has gathered long enough to be weighed (the reference test).
    #[cfg(test)]
    pub(crate) fn ld_due(&self) -> Vec<usize> {
        let every = self.world.params.ld_every;
        (0..self.sensors.len())
            .filter(|&si| self.sensors[si].ld.due(every))
            .collect()
    }

    /// The weighing of sensor si's LD2410C alone, with a given background and echo rate (the
    /// reference test against Python's numbers).
    #[cfg(test)]
    pub(crate) fn weigh_ld_with(
        &mut self,
        si: usize,
        b: &[f64; crate::ld2410::CELLS],
        rate: f64,
    ) -> Result<Option<crate::filter::LdLearn>, NotFinite> {
        self.filter.ld_weigh(&self.world, si, &mut self.sensors[si].ld, b, rate)
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

    /// "Ziel" per observed room (Tracker.targets): the walkers' own motion with the learned
    /// destination map; once per epoch.
    pub fn targets(&mut self) -> &[Target] {
        let Self {
            outputs,
            filter,
            world,
            learned,
            ..
        } = self;
        outputs
            .at(filter.epoch())
            .targets(filter, world, learned.destinations.map())
    }

    /// Per observed room whether it is occupied at `now` and by what (Tracker.occupancy): the
    /// filter by the cost of the light, or its own LD2450 within the last seen_hold s.
    pub fn occupancy(&mut self, now: f64) -> Vec<Option<Source>> {
        let (light_cost, hold) = (self.world.outputs.light_cost, self.world.outputs.seen_hold);
        let seen = self.seen.seen(&self.world.plan, now, hold);
        outputs::occupancy(self.count_distribution(), &seen, light_cost)
    }

    /// The known people of the most probable hypothesis for the display (Tracker.persons).
    pub fn persons(&self) -> Vec<Person> {
        outputs::persons(&self.filter, &self.world)
    }

    /// Where they may be, for the map (Tracker._cloud).
    pub fn clouds(&self) -> Vec<Cloud> {
        outputs::clouds(&self.filter, &self.world)
    }

    /// The numbers of the zones of the home and of the house at `now` (Tracker.zone_states); `start`
    /// the model's (None: not started, nothing silent).
    pub fn zone_numbers(&mut self, now: f64) -> (Vec<ZoneNumbers>, HouseNumbers) {
        let occupancy = self.occupancy(now);
        let silent = self.silent(now);
        outputs::zones_of(&mut self.outputs, &self.filter, &self.world, &occupancy, &silent)
    }

    /// What the rooms' own LD2450s saw (RoomSeen).
    pub fn seen(&self) -> &RoomSeen {
        &self.seen
    }

    /// Per observed room whether its own sensors all went silent (the world's silent_after), counted
    /// from the start for one that sent nothing.
    pub fn silent(&self, now: f64) -> Vec<bool> {
        self.seen
            .silent(&self.world.plan, now, self.clock.start, self.world.outputs.silent_after)
    }

    /// Whether the sensor with this id sent a frame lately (the world's silent_after).
    pub fn online(&self, id: &str, now: f64) -> bool {
        self.seen
            .online(id, now, self.clock.start, self.world.outputs.silent_after)
    }

    /// Per observed room whether it has an own sensor in use.
    pub fn watched(&self) -> Vec<bool> {
        RoomSeen::watched(&self.world.plan)
    }
}

/// Per sensor of the world its state without frames, in use if the plan says so.
fn sensor_states(world: &World) -> Vec<SensorState> {
    world
        .plan
        .sensors
        .iter()
        .map(|s| SensorState::new(&world.params, s.in_use))
        .collect()
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
    use crate::frames::{Ld2410, Target};
    use crate::learn::pauses::Pause;
    use crate::testing;

    /// A frame with targets at (x, y) mm in the sensor's frame.
    fn targets(at: &[(f64, f64)]) -> frames::Frame {
        frames::Frame {
            uptime_ms: None,
            targets: at
                .iter()
                .enumerate()
                .map(|(k, &(x, y))| Target {
                    slot: k as u8 + 1,
                    x,
                    y,
                    speed: 0.0,
                })
                .collect(),
            ld2410: None,
        }
    }

    fn one(x: f64, y: f64) -> frames::Frame {
        targets(&[(x, y)])
    }

    /// A tracker started at 0 with one person anywhere.
    fn started() -> Tracker {
        let mut tr = Tracker::new(testing::flat());
        tr.start_at(0.0);
        tr.reset_people(Some(&[Start::Anywhere])).expect("a start");
        tr
    }

    fn branches(events: &[Event]) -> Vec<(SegId, Branched)> {
        events
            .iter()
            .filter_map(|e| match e {
                Event::Branch { seg, how, .. } => Some((*seg, *how)),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn a_frame_starts_the_model_is_read_and_its_room_seen() {
        let mut tr = Tracker::new(testing::flat());
        tr.reset_people(Some(&[Start::Anywhere])).expect("a start");
        assert_eq!(
            tr.frame("wohn", f64::NAN, &one(0.0, 2500.0)),
            Err(Fault::Call(CallError::Time))
        );
        assert_eq!(tr.start(), None);
        // a sensor the plan lacks: heard, the model starts, nothing else
        assert_eq!(tr.frame("elsewhere", 1.0, &one(0.0, 2500.0)), Ok(Vec::new()));
        assert_eq!((tr.start(), tr.now()), (Some(1.0), 1.0));
        let events = tr.frame("wohn", 1.5, &one(0.0, 2500.0)).expect("a frame");
        assert_eq!(branches(&events).len(), 1, "a track born, branched on");
        let read = tr.sensor_frame(0);
        assert_eq!((read.t, read.detections.len()), (1.5, 1));
        assert!(
            read.events.born.is_empty() && read.events.measured.is_empty(),
            "the steps took them out"
        );
        assert_eq!(tr.last_frame(0), 1.5);
        assert_eq!(tr.seen().seen(&tr.world().plan, 1.5, 10.0), vec![true]);
    }

    #[test]
    fn a_track_is_measured_held_and_found_again() {
        let mut tr = started();
        let born = branches(&tr.frame("wohn", 1.0, &one(0.0, 2500.0)).expect("a frame"))[0].0;
        let events = tr.frame("wohn", 1.1, &one(50.0, 2500.0)).expect("a frame");
        assert!(branches(&events).is_empty());
        assert!(events.contains(&Event::Watch {
            si: 0,
            delta: 1.1 - 1.0
        }));
        assert_eq!(tr.filter().tracks().get(born).and_then(|i| i.gt), Some(1.1), "measured");
        // frozen (the same raw numbers): lost; measured again: found again
        tr.frame("wohn", 1.2, &one(50.0, 2500.0)).expect("a frame");
        assert!(tr.filter().tracks().get(born).is_some_and(|i| i.lost.is_some()));
        let events = tr.frame("wohn", 1.3, &one(60.0, 2500.0)).expect("a frame");
        assert_eq!(branches(&events), vec![(born, Branched::FoundAgain)]);
        // the LD2410C part is gathered once
        let gates = frames::LdGates {
            moving: [10.0; 9],
            still: [5.0; 9],
        };
        let with_ld = frames::Frame {
            ld2410: Some(Ld2410 {
                gates: Some(gates),
                ..Ld2410::default()
            }),
            ..one(70.0, 2500.0)
        };
        tr.frame("wohn", 1.4, &with_ld).expect("a frame");
        assert!(tr.ld_sensor(0).stats().time > 0.0 && tr.ld_collect(0).t == 1.4);
        assert!(tr.sensor_frame(0).ld2410.is_none());
    }

    #[test]
    fn a_silent_sensor_ends_its_tracks_for_lost_data_without_waiting_for_its_next_frame() {
        let mut tr = started();
        let born = branches(&tr.frame("wohn", 1.0, &one(0.0, 2500.0)).expect("a frame"))[0].0;
        assert!(
            tr.step(1.0 + LOST).expect("finite").is_empty(),
            "not silent for more than LOST yet"
        );
        let events = tr.step(1.5 + LOST).expect("finite");
        assert!(matches!(
            events[..],
            [Event::TrackEnd {
                si: 0,
                data_lost: true,
                ..
            }]
        ));
        assert!(tr.filter().tracks().get(born).is_none());
        // its raw tracks were forgotten: its next frame ends nothing and is a start
        let events = tr.frame("wohn", 8.0, &one(0.0, 2500.0)).expect("a frame");
        assert!(
            !events
                .iter()
                .any(|e| matches!(e, Event::TrackEnd { .. } | Event::Watch { .. }))
        );
        // the ids of new tracks go on in a rebuilt tracker
        let mut rebuilt = Tracker::new(testing::flat());
        rebuilt.adopt(&mut tr);
        rebuilt.reset_people(Some(&[Start::Anywhere])).expect("a start");
        let next = branches(&rebuilt.frame("wohn", 9.0, &one(1000.0, 2500.0)).expect("a frame"))[0].0;
        assert_eq!(next, SegId(born.0 + 2));
    }

    // ------------------------------------------- the learning order (docs/rust-core.md 2.0), per row

    #[test]
    fn row_1_a_birth_reads_the_ghost_map_with_the_births_of_the_tracks_ended_in_the_same_frame() {
        let mut tr = started();
        tr.frame("wohn", 1.0, &one(0.0, 2500.0)).expect("a frame");
        let z0 = tr.sensor_frame(0).detections[0].pos;
        tr.frame("wohn", 1.1, &one(20.0, 2500.0)).expect("a frame");
        // the map as it would be had this frame only been watched, without the birth it learns
        let mut watched_only = tr.learned().ghosts.clone();
        watched_only.add_watch(0, 1.2 - 1.1);
        // the target jumps back 1 m (more than the LD2450 tracks' gate): its track ends, a new one is born
        let events = tr.frame("wohn", 1.2, &one(20.0, 1500.0)).expect("a frame");
        let Some(&Event::TrackEnd { z0: began, p_ghost, .. }) =
            events.iter().find(|e| matches!(e, Event::TrackEnd { .. }))
        else {
            panic!("no end in {events:?}");
        };
        assert!(p_ghost > 0.0 && began == z0, "{events:?}");
        let z = tr.sensor_frame(0).detections[0].pos;
        let [(_, Branched::Born { map_rate })] = branches(&events)[..] else {
            panic!("no birth in {events:?}");
        };
        assert_eq!(
            map_rate,
            tr.learned().ghosts.rate(0, z),
            "read after the end was learned"
        );
        assert_ne!(map_rate, watched_only.rate(0, z), "not before");
    }

    #[test]
    fn row_1_the_total_rate_is_read_before_the_map_learns_the_time_watched() {
        let mut tr = started();
        tr.frame("wohn", 1.0, &one(0.0, 2500.0)).expect("a frame"); // a start: nothing watched
        tr.frame("wohn", 1.1, &one(20.0, 2500.0)).expect("a frame");
        // read (and kept) with nothing watched yet, then 0.1 s learned
        let mut fresh = Learned::new(tr.world()).ghosts;
        let mut later = fresh.clone();
        later.add_watch(0, 1.1 - 1.0);
        let sight = &tr.world.sights[0];
        let read = tr.learned.ghosts.total(0, sight);
        assert_eq!(read, fresh.total(0, sight));
        assert_ne!(read, later.total(0, sight));
        assert!(tr.learned().ghosts.watched(0) > 0.0);
    }

    /// Frames of sensor 0's LD2410C with constant energies, gathered until t.
    fn energies(tr: &mut Tracker, from: f64, to: f64) {
        let gates = LdGates {
            moving: [13.0; 9],
            still: [0.0, 0.0, 6.0, 5.0, 5.0, 5.0, 4.0, 4.0, 4.0],
        };
        let mut t = from;
        while t < to {
            tr.gather_ld(0, t, Some(&gates), false);
            t += 0.1;
        }
    }

    #[test]
    fn row_2_the_background_learns_from_b_and_the_means_both_from_before_the_weighing() {
        // nobody anywhere and (nearly) no echo sources: the means are the background the weighing used,
        // its whole share is the background's, so in the moving cells it learns the energies themselves -
        // only if b is the one the means were made of, in each weighing one after the other
        let mut world = testing::flat();
        world.params.ld_echo_rate = 1e-300;
        let mut tr = Tracker::new(world);
        tr.start_at(0.0);
        tr.reset_people(Some(&[])).expect("nobody");
        for k in 1..=3 {
            energies(&mut tr, k as f64 - 1.0, k as f64);
            tr.step(k as f64).expect("finite");
            let (num, den) = tr.learned().background.level(0).expect("learned");
            assert!(den > 0.0);
            for (c, n) in num.iter().enumerate().take(crate::ld2410::MOVING) {
                assert!((n / den - 13.0).abs() < 1e-12, "weighing {k}, cell {c}: {}", n / den);
            }
        }
    }

    /// Where the first person with a track of the first hypothesis is, its modes weighed.
    fn person_at(tr: &Tracker) -> Point {
        let m = tr.filter().hyps()[0]
            .people
            .values()
            .next()
            .copied()
            .expect("a person with a track");
        let mix = tr.filter().objects().mixtures().get(m);
        let w = mix.weights();
        let [ps, pw] = [mix.modes[0].pos(), mix.modes[1].pos()];
        [w[0] * ps[0] + w[1] * pw[0], w[0] * ps[1] + w[1] * pw[1]]
    }

    #[test]
    fn row_4_the_walks_are_counted_after_everybody_moved_and_the_recycling() {
        let mut tr = started();
        // somebody walking 1 m/s across the sensor's view
        for k in 0..=10 {
            tr.frame("wohn", 1.0 + 0.1 * k as f64, &one(-500.0 + 100.0 * k as f64, 2500.0))
                .expect("a frame");
        }
        let before = person_at(&tr);
        tr.step(2.3).expect("finite");
        // the walk follows the person where the move left them at 2.3, not where they were
        let (_, walk) = &tr.learned.destinations.walks()[0];
        assert_eq!((walk.last, tr.filter().flushed()), (2.3, 2.3));
        assert_eq!(walk.pos, person_at(&tr));
        assert_ne!(walk.pos, before);
    }

    #[test]
    fn row_5_in_a_pause_nothing_is_learned() {
        let mut tr = started();
        tr.set_pauses(Pauses::new(vec![Pause { start: 0.5, end: None }]).expect("sound"));
        tr.frame("wohn", 1.0, &one(0.0, 2500.0)).expect("a frame");
        let mut all = Vec::new();
        for k in 1..30 {
            let x = 20.0 * k as f64;
            all.extend(
                tr.frame("wohn", 1.0 + 0.1 * k as f64, &one(x, 2500.0))
                    .expect("a frame"),
            );
        }
        energies(&mut tr, 4.0, 6.0);
        all.extend(tr.step(6.0).expect("finite"));
        all.extend(tr.frame("wohn", 6.1, &one(0.0, 1000.0)).expect("a frame"));
        assert!(
            !all.iter()
                .any(|e| matches!(e, Event::Watch { .. } | Event::TrackEnd { .. }))
        );
        assert_eq!(tr.learned().to_json(), Learned::new(tr.world()).to_json());
        assert!(tr.learned.destinations.walks().is_empty());
    }
}
