//! The LD2450's own tracks (MODEL.md 4.1): the sensor is a tracker itself, and what
//! it reports is decoded here into its tracks - no inference about the world, only about the format.
//!
//! - The three slots are compacted (when slot 1 ends, slot 2 moves up), so a slot number is no track
//!   id. The positions of one target are continuous from frame to frame (99 % move less than 0.1 m),
//!   so targets are linked to the previous frame's by proximity in the sensor's own coordinates.
//! - When the LD2450 loses its target it keeps the track: a moving one is coasted with its last speed
//!   (slowing down smoothly), a still one is repeated bit-identically (frozen). Measured (5.10., 16 h):
//!   this happens 0.5 (still) to 0.9 (walking) times per second; 97-98 % of these phases end with the
//!   target found again, mostly within 0.1-0.3 s; otherwise the track expires after 1.25 s (coasting)
//!   or 35.5 s (frozen). Coasted and frozen frames are no measurement.
//! - A still target's hold is format noise as long as it is short (measured 17.11., 166 h: still tracks
//!   are held 14 times a minute, 56 % of their frames; 57 % of the holds last at most 0.3 s, 80 % at
//!   most 2 s, the jump when found again is 0.01 m in the median, 0.19 m at p99): a hold of a target
//!   last measured still becomes a loss only when it lasts longer than SETTLE s, ends with the target
//!   found again farther than SETTLE_JUMP m from where it was held, or ends with its track. Then it
//!   is reported as lost since its first held frame (`revealed`), and not being found again counts
//!   from there; otherwise it was a gap in the measurements of one track, no event. Alike for people,
//!   ghosts and Störquellen: it is the sensor's format. A walker's coasting is reported at once.
//! - Targets that can't be a person (Detection::hidden) carry no information; a track is born at its
//!   first visible measured frame.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::filter::mixture::SegId;
use crate::filter::tracks::MOVING;
use std::collections::BTreeMap;

use crate::frames::{CallError, Frame, Target};
use crate::geometry::Point;
use crate::params::LostKind;
use crate::vmath::py_hypot;

/// m between two frames of one target in the sensor's coordinates (6.7 m/s at 11 Hz)
pub const GATE: f64 = 0.6;
/// frames with the same nonzero raw speed: the sensor is coasting a lost target
pub const COAST_RUN: u32 = 3;
/// s without a frame: data lost, every track is over (the idle heartbeat is 5 s)
pub const LOST: f64 = 6.0;
/// s: a still target held longer than this is lost (shorter: a gap in its measurements; the default of
/// `Params::settle`)
pub const SETTLE: f64 = 2.0;
/// m: a still target found again farther than this from where it was held within SETTLE was lost
pub const SETTLE_JUMP: f64 = 0.3;

/// The ids of new tracks, unique over all sensors of a tracker (and over its rebuilds).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SegIds(u64);

impl SegIds {
    pub fn starting_at(first: u64) -> Self {
        Self(first)
    }

    pub fn fresh(&mut self) -> SegId {
        let id = SegId(self.0);
        self.0 += 1;
        id
    }
}

impl Default for SegIds {
    fn default() -> Self {
        Self(1)
    }
}

/// A target of a frame as the tracks see it: what the sensor reported, whether it can be a person, and
/// whether it moves (a walker's: its radial speed on the floor at least MOVING).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sighting {
    pub target: Target,
    pub visible: bool,
    pub moving: bool,
}

/// A track and the frame's target (its index in the frame's sightings) it is at.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TrackAt {
    pub seg: SegId,
    pub det: usize,
}

/// A track the sensor lost in this frame: how, and the target it holds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LostAt {
    pub seg: SegId,
    pub kind: LostKind,
    pub det: usize,
}

/// A still target's hold that turned out to be a loss after all (longer than SETTLE, found again too
/// far away, or its track ended): lost at `since` (its first held frame), held until `last` (its last
/// held frame before this one) at `target`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Revealed {
    pub seg: SegId,
    pub kind: LostKind,
    pub since: f64,
    pub last: f64,
    pub target: Target,
}

/// What a frame says about the sensor's tracks.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct TrackEvents {
    /// losses of still targets reported late, to be taken before the other events of the frame
    pub revealed: Vec<Revealed>,
    pub born: Vec<TrackAt>,
    /// continued or found again
    pub measured: Vec<TrackAt>,
    /// coasting or frozen from now on, at the held position
    pub lost: Vec<LostAt>,
    /// still lost, at the held position
    pub held: Vec<TrackAt>,
    /// gone from the frames
    pub ended: Vec<SegId>,
    /// the sensor was silent for more than LOST s: its tracks ended for lost data
    pub data_lost: bool,
}

impl TrackEvents {
    /// The targets measured in a track: born, then continued or found again.
    pub fn measurements(&self) -> impl Iterator<Item = &TrackAt> {
        self.born.iter().chain(&self.measured)
    }

    /// Whether the target `det` was measured.
    pub fn measures(&self, det: usize) -> bool {
        self.measurements().any(|a| a.det == det)
    }
}

/// What a raw target is to the tracks.
#[derive(Debug, Clone, Copy, PartialEq)]
enum RawTrack {
    /// not yet measured where it can be a person
    Unborn,
    Live {
        seg: SegId,
        hold: Hold,
        /// its target moved when last measured where it can be a person
        moving: bool,
    },
}

/// Whether the sensor holds a live track's target.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Hold {
    Measuring,
    /// a still target held for at most SETTLE s so far: not yet a loss
    Gap(Gap),
    /// reported lost
    Lost(LostKind),
}

/// A still target's hold not yet reported: how, since when, its last held frame, the target held.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Gap {
    kind: LostKind,
    since: f64,
    last: f64,
    target: Target,
}

/// How long a still target's hold may last (s), and how far from where it was held it may be found
/// again (m), to be a gap in its measurements; a time of 0: every hold is a loss.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Settle {
    time: f64,
    jump: f64,
}

impl Gap {
    /// Measured again at t as `tg`: whether the hold was only a gap in its measurements.
    fn closes(&self, settle: Settle, t: f64, tg: &Target) -> bool {
        let (a, b) = (tg.local(), self.target.local());
        t - self.since <= settle.time && py_hypot(a[0] - b[0], a[1] - b[1]) <= settle.jump
    }

    fn revealed(&self, seg: SegId) -> Revealed {
        Revealed {
            seg,
            kind: self.kind,
            since: self.since,
            last: self.last,
            target: self.target,
        }
    }
}

/// One target of the sensor as it reports it, linked from frame to frame.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Raw {
    /// raw mm, compared bit for bit (frozen)
    xy: [f64; 2],
    local: Point,
    /// raw mm/s
    speed: f64,
    /// frames in a row with this nonzero speed
    run: u32,
    track: RawTrack,
}

impl Raw {
    fn seg(&self) -> Option<SegId> {
        match self.track {
            RawTrack::Unborn => None,
            RawTrack::Live { seg, .. } => Some(seg),
        }
    }

    /// Its track's hold not yet reported, as a loss.
    fn revealed(&self) -> Option<Revealed> {
        match self.track {
            RawTrack::Live {
                seg,
                hold: Hold::Gap(g),
                ..
            } => Some(g.revealed(seg)),
            _ => None,
        }
    }
}

/// The tracks of one LD2450.
#[derive(Debug, Clone, PartialEq)]
pub struct Ld2450Tracks {
    raw: Vec<Raw>,
    /// time of the last frame
    last: f64,
    settle: Settle,
}

impl Default for Ld2450Tracks {
    fn default() -> Self {
        Self::new([SETTLE, SETTLE_JUMP])
    }
}

impl Ld2450Tracks {
    /// Its tracks with this [s, m] for a still target's hold to be a loss (`Params::settle`).
    pub fn new([time, jump]: [f64; 2]) -> Self {
        Self {
            raw: Vec::new(),
            last: f64::NEG_INFINITY,
            settle: Settle { time, jump },
        }
    }

    /// Whether the sensor sent no frame for more than LOST s at t.
    pub fn silent(&self, t: f64) -> bool {
        t - self.last > LOST
    }

    /// Time of the last frame (-inf: none yet).
    pub fn last(&self) -> f64 {
        self.last
    }

    /// Its tracks are forgotten: its next frame is a start (lost data). The holds of still targets not
    /// yet reported, as losses (their tracks end now).
    pub fn drop_raw(&mut self) -> Vec<Revealed> {
        let revealed = self.raw.iter().filter_map(Raw::revealed).collect();
        self.raw.clear();
        revealed
    }

    /// The frame at t with these targets (in the frame's order): what it says about the tracks.
    pub fn update(&mut self, t: f64, seen: &[Sighting], ids: &mut SegIds) -> TrackEvents {
        let mut ev = TrackEvents::default();
        if self.silent(t) {
            ev.revealed.extend(self.raw.iter().filter_map(Raw::revealed));
            ev.ended.extend(self.raw.iter().filter_map(Raw::seg));
            ev.data_lost = !self.raw.is_empty() && self.last > f64::NEG_INFINITY;
            self.raw.clear();
        }
        self.last = t;
        let links = self.link(seen);
        for (i, r) in self.raw.iter().enumerate() {
            if !links.contains(&Some(i)) {
                ev.revealed.extend(r.revealed());
                ev.ended.extend(r.seg());
            }
        }
        let raw = seen
            .iter()
            .zip(&links)
            .enumerate()
            .map(|(det, (s, &link))| follow(self.settle, t, det, s, link.map(|i| self.raw[i]), ids, &mut ev))
            .collect();
        self.raw = raw;
        ev
    }

    /// Per sighting the raw target of the last frame it continues: the closest pairs first (ties in the
    /// order of the raw targets, then of the sightings), each at most GATE apart and used once.
    fn link(&self, seen: &[Sighting]) -> Vec<Option<usize>> {
        let mut pairs: Vec<(f64, usize, usize)> = self
            .raw
            .iter()
            .enumerate()
            .flat_map(|(i, r)| {
                seen.iter().enumerate().map(move |(j, s)| {
                    let l = s.target.local();
                    (py_hypot(r.local[0] - l[0], r.local[1] - l[1]), i, j)
                })
            })
            .collect();
        pairs.sort_by(|a, b| a.0.total_cmp(&b.0));
        let mut links = vec![None; seen.len()];
        let mut used = vec![false; self.raw.len()];
        for (dist, i, j) in pairs {
            if dist <= GATE && !used[i] && links[j].is_none() {
                used[i] = true;
                links[j] = Some(i);
            }
        }
        links
    }
}

/// What the LD2450 does with a target it continues.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Report {
    Measuring,
    /// it lost the target and holds it: coasted with its last speed, or repeated bit-identically
    Holding(LostKind),
}

impl Raw {
    /// The target `tg` continues this one: frames in a row with its nonzero speed, and what the sensor
    /// does with it.
    fn continued_by(&self, tg: &Target) -> (u32, Report) {
        let run = if tg.speed == self.speed && tg.speed != 0.0 {
            self.run + 1
        } else {
            1
        };
        let report = if [tg.x, tg.y] == self.xy {
            Report::Holding(LostKind::Frozen)
        } else if run >= COAST_RUN {
            Report::Holding(LostKind::Coast)
        } else {
            Report::Measuring
        };
        (run, report)
    }
}

/// The raw target of sighting `det` at t (continuing `prev`, if linked) and the events of its track.
fn follow(
    settle: Settle,
    t: f64,
    det: usize,
    s: &Sighting,
    prev: Option<Raw>,
    ids: &mut SegIds,
    ev: &mut TrackEvents,
) -> Raw {
    let tg = s.target;
    let (run, track, report) = match prev {
        Some(r) => {
            let (run, report) = r.continued_by(&tg);
            (run, r.track, report)
        }
        None => (1, RawTrack::Unborn, Report::Measuring),
    };
    let track = match (track, report) {
        (RawTrack::Unborn, Report::Measuring) if s.visible => {
            let seg = ids.fresh();
            ev.born.push(TrackAt { seg, det });
            RawTrack::Live {
                seg,
                hold: Hold::Measuring,
                moving: s.moving,
            }
        }
        (RawTrack::Unborn, _) => RawTrack::Unborn,
        (RawTrack::Live { seg, hold, moving }, Report::Holding(kind)) => {
            let hold = match hold {
                // a still target: not yet a loss
                Hold::Measuring if !moving && settle.time > 0.0 => Hold::Gap(Gap {
                    kind,
                    since: t,
                    last: t,
                    target: tg,
                }),
                Hold::Measuring => {
                    ev.lost.push(LostAt { seg, kind, det });
                    Hold::Lost(kind)
                }
                Hold::Gap(g) if t - g.since <= settle.time => Hold::Gap(Gap { last: t, ..g }),
                // held longer than SETTLE: lost since it was first held, and still held
                Hold::Gap(g) => {
                    ev.revealed.push(g.revealed(seg));
                    ev.held.push(TrackAt { seg, det });
                    Hold::Lost(g.kind)
                }
                Hold::Lost(kind) => {
                    ev.held.push(TrackAt { seg, det });
                    Hold::Lost(kind)
                }
            };
            RawTrack::Live { seg, hold, moving }
        }
        (RawTrack::Live { seg, hold, moving }, Report::Measuring) => {
            if let Hold::Gap(g) = hold
                && !g.closes(settle, t, &tg)
            {
                // too late or too far away: it was lost, and this finds it again
                ev.revealed.push(g.revealed(seg));
            }
            if s.visible {
                ev.measured.push(TrackAt { seg, det });
            }
            RawTrack::Live {
                seg,
                hold: Hold::Measuring,
                moving: if s.visible { s.moving } else { moving },
            }
        }
    };
    Raw {
        xy: [tg.x, tg.y],
        local: tg.local(),
        speed: tg.speed,
        run,
        track,
    }
}

/// A target measured in a track, in the sensor's own frame (the calibration's measurements).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Measurement {
    pub seg: SegId,
    /// m, the sensor's frame
    pub local: Point,
    /// m/s along the beam, as measured
    pub speed: f64,
}

/// The LD2450's own tracks of several boards with one sequence of track ids and without a plan:
/// every target in front of its sensor counts (calibration.track_frame).
#[derive(Debug, Clone, PartialEq)]
pub struct TrackStage {
    boards: BTreeMap<String, Ld2450Tracks>,
    ids: SegIds,
}

impl TrackStage {
    pub fn starting_at(first: u64) -> Self {
        Self {
            boards: BTreeMap::new(),
            ids: SegIds::starting_at(first),
        }
    }

    /// A frame of a board at t: its targets measured in a track (born, then continued), in that order.
    pub fn update(&mut self, board: &str, t: f64, frame: &Frame) -> Result<Vec<Measurement>, CallError> {
        if !t.is_finite() {
            return Err(CallError::Time);
        }
        let seen: Vec<Sighting> = frame
            .in_front()
            .map(|&target| Sighting {
                target,
                visible: true,
                moving: target.radial_speed().abs() >= MOVING,
            })
            .collect();
        let tracks = self.boards.entry(board.to_owned()).or_default();
        let ev = tracks.update(t, &seen, &mut self.ids);
        Ok(ev
            .measurements()
            .map(|a| {
                let tg = seen[a.det].target;
                Measurement {
                    seg: a.seg,
                    local: tg.local(),
                    speed: tg.radial_speed(),
                }
            })
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAME: f64 = 0.089;

    fn sighting(x: f64, y: f64, speed: f64) -> Sighting {
        let target = Target { slot: 0, x, y, speed };
        Sighting {
            target,
            visible: true,
            moving: target.radial_speed().abs() >= MOVING,
        }
    }

    /// Frames of (x mm, y mm, speed) every FRAME s: the events per frame.
    fn feed(frames: &[Vec<(f64, f64, f64)>]) -> Vec<TrackEvents> {
        let (mut tracks, mut ids) = (Ld2450Tracks::default(), SegIds::default());
        frames
            .iter()
            .enumerate()
            .map(|(k, targets)| {
                let seen: Vec<Sighting> = targets.iter().map(|&(x, y, v)| sighting(x, y, v)).collect();
                tracks.update(k as f64 * FRAME, &seen, &mut ids)
            })
            .collect()
    }

    fn segs(at: &[TrackAt]) -> Vec<SegId> {
        at.iter().map(|a| a.seg).collect()
    }

    #[test]
    fn a_track_keeps_its_id_when_the_slots_move_up() {
        // two targets; the first ends, the second moves up into slot 1: still the same track
        let ev = feed(&[
            vec![(0.0, 2000.0, 10.0), (1000.0, 3000.0, 20.0)],
            vec![(0.0, 2010.0, 30.0), (1000.0, 3010.0, 40.0)],
            vec![(1000.0, 3020.0, 50.0)],
        ]);
        let [a, b] = segs(&ev[0].born)[..] else {
            panic!("two born")
        };
        assert_eq!((a, b), (SegId(1), SegId(2)));
        assert_eq!(segs(&ev[1].measured), vec![a, b]);
        assert_eq!(ev[2].ended, vec![a]);
        assert_eq!(ev[2].measured, vec![TrackAt { seg: b, det: 0 }]);
    }

    #[test]
    fn coasting_and_freezing_are_no_measurement() {
        let mut frames: Vec<Vec<(f64, f64, f64)>> = (0..3)
            .map(|k| vec![(0.0, 2000.0 + 10.0 * k as f64, 100.0 + k as f64)])
            .collect();
        // the same speed: coasting from the third such frame
        frames.extend((0..4).map(|k| vec![(0.0, 2030.0 + 10.0 * k as f64, 300.0)]));
        // then repeated bit-identically: frozen
        frames.extend((0..3).map(|_| vec![(0.0, 2060.0, 300.0)]));
        frames.push(vec![(10.0, 2110.0, 20.0)]); // found again
        frames.push(vec![]); // gone
        let ev = feed(&frames);
        let seg = ev[0].born[0].seg;
        let lost: Vec<(usize, LostKind)> = ev
            .iter()
            .enumerate()
            .flat_map(|(k, e)| e.lost.iter().map(move |l| (k, l.kind)))
            .collect();
        assert_eq!(lost, vec![(5, LostKind::Coast)]);
        assert!(
            ev[5..10].iter().all(|e| e.measured.is_empty()),
            "coasting, then frozen: held"
        );
        assert_eq!(
            ev.iter().map(|e| e.held.len()).sum::<usize>(),
            4,
            "held until found again"
        );
        assert_eq!(segs(&ev[10].measured), vec![seg], "found again: the same track");
        assert_eq!(ev[11].ended, vec![seg]);
        assert!(!ev[11].data_lost);
    }

    #[test]
    fn a_hidden_target_is_born_when_it_comes_into_sight_and_a_jump_is_a_new_one() {
        let (mut tracks, mut ids) = (Ld2450Tracks::default(), SegIds::starting_at(7));
        let hidden = |y| Sighting {
            visible: false,
            ..sighting(0.0, y, 10.0)
        };
        assert!(tracks.update(0.0, &[hidden(9000.0)], &mut ids).born.is_empty());
        assert!(tracks.update(0.09, &[hidden(8500.0)], &mut ids).born.is_empty());
        // a jump of 5.5 m is no continuation: a new target, born where it is seen
        let ev = tracks.update(0.18, &[sighting(0.0, 3000.0, 30.0)], &mut ids);
        assert_eq!(ev.born, vec![TrackAt { seg: SegId(7), det: 0 }]);
        assert!(ev.ended.is_empty(), "the unborn one ends without a track");
        // measured where it can't be a person: no measurement, but the track goes on
        let ev = tracks.update(0.27, &[hidden(3050.0)], &mut ids);
        assert!(ev.measured.is_empty() && ev.ended.is_empty() && !ev.measures(0));
        assert!(
            tracks
                .update(0.36, &[sighting(0.0, 3100.0, 40.0)], &mut ids)
                .measures(0)
        );
    }

    #[test]
    fn after_a_silence_of_more_than_lost_every_track_ended_for_lost_data() {
        let (mut tracks, mut ids) = (Ld2450Tracks::default(), SegIds::default());
        let first = tracks.update(10.0, &[sighting(0.0, 2000.0, 0.0)], &mut ids);
        assert!(!first.data_lost, "the first frame is no loss");
        assert!(!tracks.silent(10.0 + LOST) && tracks.silent(10.0 + LOST + 0.01));
        let ev = tracks.update(10.0 + LOST + 0.01, &[sighting(0.0, 2000.0, 0.0)], &mut ids);
        assert_eq!(ev.ended, vec![SegId(1)]);
        assert!(ev.data_lost);
        assert_eq!(segs(&ev.born), vec![SegId(2)], "the same spot: a new track");
        tracks.drop_raw();
        let ev = tracks.update(20.0, &[], &mut ids);
        assert!(ev.ended.is_empty() && !ev.data_lost, "nothing left to lose");
    }

    #[test]
    fn a_target_continues_within_the_gate_and_ties_go_to_the_first_raw_target() {
        // 0.599 m: the same target; 0.601 m: a new one (and the old one ends)
        let ev = feed(&[
            vec![(0.0, 2000.0, 10.0)],
            vec![(0.0, 2599.0, 20.0)],
            vec![(0.0, 3200.0, 30.0)],
        ]);
        assert_eq!(segs(&ev[1].measured), vec![SegId(1)]);
        assert_eq!(
            (ev[2].ended.clone(), segs(&ev[2].born)),
            (vec![SegId(1)], vec![SegId(2)])
        );
        // one target as far from both of two: it continues the first, the second ends
        let ev = feed(&[
            vec![(-300.0, 2000.0, 10.0), (300.0, 2000.0, 20.0)],
            vec![(0.0, 2000.0, 30.0)],
        ]);
        assert_eq!(ev[1].measured, vec![TrackAt { seg: SegId(1), det: 0 }]);
        assert_eq!(ev[1].ended, vec![SegId(2)]);
    }

    #[test]
    fn coasting_needs_the_same_speed_three_frames_in_a_row() {
        let speeds = [300.0, 300.0, 301.0, 301.0, 301.0];
        let frames: Vec<Vec<(f64, f64, f64)>> = speeds
            .iter()
            .enumerate()
            .map(|(k, &v)| vec![(0.0, 2000.0 + 10.0 * k as f64, v)])
            .collect();
        let ev = feed(&frames);
        let lost: Vec<usize> = (0..ev.len()).filter(|&k| !ev[k].lost.is_empty()).collect();
        assert_eq!(lost, vec![4], "the change of speed starts the count over");
        assert_eq!(ev[4].lost[0].kind, LostKind::Coast);
    }

    /// The events of all frames: (frame, revealed (seg, since, last), lost, held, measured segs).
    fn losses(ev: &[TrackEvents]) -> (Vec<(usize, SegId, f64, f64)>, usize, usize) {
        let revealed = ev
            .iter()
            .enumerate()
            .flat_map(|(k, e)| e.revealed.iter().map(move |r| (k, r.seg, r.since, r.last)))
            .collect();
        let lost = ev.iter().map(|e| e.lost.len()).sum();
        let held = ev.iter().map(|e| e.held.len()).sum();
        (revealed, lost, held)
    }

    #[test]
    fn a_still_target_s_short_holds_are_gaps_in_one_track_not_losses() {
        // a still target (speed 0) frozen for 1, 2, 3, ... frames (at most SETTLE), each time found again
        // within a few cm of where it was held: the measurements of one track with gaps
        let mut frames: Vec<Vec<(f64, f64, f64)>> = vec![vec![(0.0, 2000.0, 0.0)]];
        let mut x = 0.0;
        for n in [1, 2, 3, 1, 5, 2, 1, 10, 22] {
            frames.extend((0..n).map(|_| vec![(x, 2000.0, 0.0)])); // frozen: bit-identical
            x += 10.0 * if n % 2 == 0 { 1.0 } else { -1.0 };
            frames.push(vec![(x, 2000.0, 0.0)]); // found again 1 cm away
        }
        assert!(22.0 * FRAME <= SETTLE, "the longest hold is still a gap");
        let ev = feed(&frames);
        let seg = ev[0].born[0].seg;
        assert_eq!(losses(&ev), (vec![], 0, 0), "no loss, no hold, nothing revealed");
        let measured: Vec<usize> = (0..ev.len()).filter(|&k| ev[k].measures(0)).collect();
        assert_eq!(measured.len(), 10, "born, then every frame it is found again");
        assert!(ev[1..].iter().all(|e| e.measured.iter().all(|m| m.seg == seg)));
        assert!(ev.iter().all(|e| e.ended.is_empty() && e.born.len() <= 1));

        // the same with the gaps not settled (a time of 0): a loss and a finding again each
        let (mut tracks, mut ids) = (Ld2450Tracks::new([0.0, 0.0]), SegIds::default());
        let old: Vec<TrackEvents> = frames
            .iter()
            .enumerate()
            .map(|(k, f)| {
                let seen: Vec<Sighting> = f.iter().map(|&(x, y, v)| sighting(x, y, v)).collect();
                tracks.update(k as f64 * FRAME, &seen, &mut ids)
            })
            .collect();
        let (revealed, lost, held) = losses(&old);
        assert_eq!((revealed.len(), lost, held), (0, 9, 47 - 9));
    }

    #[test]
    fn a_still_target_held_longer_than_settle_was_lost_since_its_first_held_frame() {
        let held = (SETTLE / FRAME) as usize + 3; // frozen frames 1..=held
        let mut frames: Vec<Vec<(f64, f64, f64)>> = vec![vec![(0.0, 2000.0, 0.0)]];
        frames.extend((0..held).map(|_| vec![(0.0, 2000.0, 0.0)]));
        frames.push(vec![(10.0, 2000.0, 0.0)]); // found again
        let ev = feed(&frames);
        let seg = ev[0].born[0].seg;
        // the first frame more than SETTLE after the first held one (frame 1) reveals the loss
        let k = (1..frames.len())
            .find(|&k| (k - 1) as f64 * FRAME > SETTLE)
            .expect("held long enough");
        let (revealed, lost, n_held) = losses(&ev);
        assert_eq!(revealed, vec![(k, seg, FRAME, (k - 1) as f64 * FRAME)]);
        assert_eq!(ev[k].revealed[0].kind, LostKind::Frozen);
        assert_eq!(ev[k].revealed[0].target.local(), [0.0, 2.0], "where it is held");
        assert_eq!(lost, 0, "no loss of its own: revealed");
        assert_eq!(n_held, held + 1 - k, "held from then on, in this frame too");
        assert_eq!(segs(&ev[k].held), vec![seg]);
        assert_eq!(segs(&ev[held + 1].measured), vec![seg], "found again");
    }

    #[test]
    fn a_still_target_found_again_too_far_away_or_whose_track_ends_while_held_was_lost() {
        // found again 0.35 m from where it was held, after 2 frames: lost since then, found again now
        let ev = feed(&[
            vec![(0.0, 2000.0, 0.0)],
            vec![(0.0, 2000.0, 0.0)],
            vec![(0.0, 2000.0, 0.0)],
            vec![(0.0, 2350.0, 0.0)],
        ]);
        let seg = ev[0].born[0].seg;
        assert_eq!(losses(&ev), (vec![(3, seg, FRAME, 2.0 * FRAME)], 0, 0));
        assert_eq!(
            segs(&ev[3].measured),
            vec![seg],
            "the loss first, then this finds it again"
        );
        // its track ends while it is held: lost since then, then ended
        let ev = feed(&[vec![(0.0, 2000.0, 0.0)], vec![(0.0, 2000.0, 0.0)], vec![]]);
        assert_eq!(losses(&ev), (vec![(2, seg, FRAME, FRAME)], 0, 0));
        assert_eq!(ev[2].ended, vec![seg]);
        // the sensor falls silent while it is held: the same, for lost data
        let (mut tracks, mut ids) = (Ld2450Tracks::default(), SegIds::default());
        tracks.update(0.0, &[sighting(0.0, 2000.0, 0.0)], &mut ids);
        tracks.update(0.1, &[sighting(0.0, 2000.0, 0.0)], &mut ids);
        let ev = tracks.update(0.2 + LOST, &[], &mut ids);
        assert!(ev.data_lost && ev.revealed.len() == 1 && ev.ended == vec![seg]);
        // or its tracks are forgotten
        tracks.update(10.0, &[sighting(0.0, 2000.0, 0.0)], &mut ids);
        tracks.update(10.1, &[sighting(0.0, 2000.0, 0.0)], &mut ids);
        let revealed = tracks.drop_raw();
        assert_eq!((revealed.len(), revealed[0].since, revealed[0].last), (1, 10.1, 10.1));
    }

    #[test]
    fn a_walker_s_hold_is_a_loss_at_once() {
        // last measured moving (0.5 m/s along the beam), then frozen: lost in that frame, as up to 0.65
        let ev = feed(&[
            vec![(0.0, 2000.0, 500.0)],
            vec![(0.0, 2050.0, 400.0)],
            vec![(0.0, 2050.0, 400.0)],
            vec![(0.0, 2060.0, 0.0)],
        ]);
        let (revealed, lost, held) = losses(&ev);
        assert_eq!((revealed.len(), lost, held), (0, 1, 0));
        assert_eq!(ev[2].lost[0].kind, LostKind::Frozen);
        assert!(ev[3].measures(0), "found again: a branch in the filter");
    }

    #[test]
    fn the_calibration_s_stage_measures_in_front_with_one_sequence_of_ids() {
        let frame = |targets: &[(f64, f64, f64)]| Frame {
            uptime_ms: None,
            targets: targets
                .iter()
                .zip(1..)
                .map(|(&(x, y, speed), slot)| Target { slot, x, y, speed })
                .collect(),
            ld2410: None,
        };
        let mut stage = TrackStage::starting_at(10);
        let a = stage
            .update("a", 0.0, &frame(&[(100.0, 2000.0, -250.0), (0.0, -10.0, 0.0)]))
            .unwrap();
        assert_eq!(
            a,
            vec![Measurement {
                seg: SegId(10),
                local: [0.1, 2.0],
                speed: -0.25
            }],
            "behind the sensor: no target"
        );
        let b = stage.update("b", 0.05, &frame(&[(0.0, 3000.0, 0.0)])).unwrap();
        assert_eq!(b[0].seg, SegId(11), "one sequence over the boards");
        let a = stage.update("a", 0.1, &frame(&[(110.0, 2010.0, -250.0)])).unwrap();
        assert_eq!(a[0].seg, SegId(10), "each board its own tracks");
        assert_eq!(stage.update("a", f64::NAN, &frame(&[])), Err(CallError::Time));
    }
}
