//! The LD2450's own tracks (MODEL.md 4.1; sensortracks.py): the sensor is a tracker itself, and what
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
//! - Targets that can't be a person (Detection::hidden) carry no information; a track is born at its
//!   first visible measured frame.

use crate::filter::mixture::SegId;
use crate::frames::Target;
use crate::geometry::Point;
use crate::params::LostKind;
use crate::vmath::py_hypot;

/// m between two frames of one target in the sensor's coordinates (6.7 m/s at 11 Hz)
pub const GATE: f64 = 0.6;
/// frames with the same nonzero raw speed: the sensor is coasting a lost target
pub const COAST_RUN: u32 = 3;
/// s without a frame: data lost, every track is over (the idle heartbeat is 5 s)
pub const LOST: f64 = 6.0;

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

/// A target of a frame as the tracks see it: what the sensor reported, and whether it can be a person.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sighting {
    pub target: Target,
    pub visible: bool,
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

/// What a frame says about the sensor's tracks.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct TrackEvents {
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
    /// Whether the target `det` was measured (in a track born or continued).
    pub fn measures(&self, det: usize) -> bool {
        self.born.iter().chain(&self.measured).any(|a| a.det == det)
    }
}

/// What a raw target is to the tracks.
#[derive(Debug, Clone, Copy, PartialEq)]
enum RawTrack {
    /// not yet measured where it can be a person
    Unborn,
    Live {
        seg: SegId,
        lost: Option<LostKind>,
    },
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
}

/// The tracks of one LD2450.
#[derive(Debug, Clone, PartialEq)]
pub struct Ld2450Tracks {
    raw: Vec<Raw>,
    /// time of the last frame
    last: f64,
}

impl Default for Ld2450Tracks {
    fn default() -> Self {
        Self {
            raw: Vec::new(),
            last: f64::NEG_INFINITY,
        }
    }
}

impl Ld2450Tracks {
    /// Whether the sensor sent no frame for more than LOST s at t.
    pub fn silent(&self, t: f64) -> bool {
        t - self.last > LOST
    }

    /// Time of the last frame (-inf: none yet).
    pub fn last(&self) -> f64 {
        self.last
    }

    /// Its tracks are forgotten: its next frame is a start (lost data).
    pub fn drop_raw(&mut self) {
        self.raw.clear();
    }

    /// The frame at t with these targets (in the frame's order): what it says about the tracks.
    pub fn update(&mut self, t: f64, seen: &[Sighting], ids: &mut SegIds) -> TrackEvents {
        let mut ev = TrackEvents::default();
        if self.silent(t) {
            ev.ended.extend(self.raw.iter().filter_map(Raw::seg));
            ev.data_lost = !self.raw.is_empty() && self.last > f64::NEG_INFINITY;
            self.raw.clear();
        }
        self.last = t;
        let links = self.link(seen);
        for (i, r) in self.raw.iter().enumerate() {
            if !links.contains(&Some(i)) {
                ev.ended.extend(r.seg());
            }
        }
        let raw = seen
            .iter()
            .zip(&links)
            .enumerate()
            .map(|(det, (s, &link))| self.follow(det, s, link.map(|i| self.raw[i]), ids, &mut ev))
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

    /// The raw target of sighting `det` (continuing `prev`, if linked) and the events of its track.
    fn follow(&self, det: usize, s: &Sighting, prev: Option<Raw>, ids: &mut SegIds, ev: &mut TrackEvents) -> Raw {
        let tg = s.target;
        let xy = [tg.x, tg.y];
        let (run, track, kind) = match prev {
            Some(r) => {
                let run = if tg.speed == r.speed && tg.speed != 0.0 {
                    r.run + 1
                } else {
                    1
                };
                let kind = if xy == r.xy {
                    Some(LostKind::Frozen)
                } else if run >= COAST_RUN {
                    Some(LostKind::Coast)
                } else {
                    None
                };
                (run, r.track, kind)
            }
            None => (1, RawTrack::Unborn, None),
        };
        let track = match (track, kind) {
            (RawTrack::Unborn, None) if s.visible => {
                let seg = ids.fresh();
                ev.born.push(TrackAt { seg, det });
                RawTrack::Live { seg, lost: None }
            }
            (RawTrack::Unborn, _) => RawTrack::Unborn,
            (RawTrack::Live { seg, lost }, Some(kind)) => {
                match lost {
                    None => ev.lost.push(LostAt { seg, kind, det }),
                    Some(_) => ev.held.push(TrackAt { seg, det }),
                }
                RawTrack::Live { seg, lost: Some(kind) }
            }
            (RawTrack::Live { seg, .. }, None) => {
                if s.visible {
                    ev.measured.push(TrackAt { seg, det });
                }
                RawTrack::Live { seg, lost: None }
            }
        };
        Raw {
            xy,
            local: tg.local(),
            speed: tg.speed,
            run,
            track,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAME: f64 = 0.089;

    fn sighting(x: f64, y: f64, speed: f64) -> Sighting {
        Sighting {
            target: Target { slot: 0, x, y, speed },
            visible: true,
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
}
