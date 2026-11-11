//! The sensors' live tracks as the filter knows them (filter.Tracker.segs, _live_by_sensor): where
//! each was measured last, whether its sensor holds it lost, and what the rates of new tracks see of
//! them.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::filter::mixture::SegId;
use crate::filter::small_map::SmallMap;
use crate::geometry::Point;
use crate::ld2450::rates::TrackSpot;
use crate::params::LostKind;

/// A track its sensor lost and holds (MODEL.md 4.1): how, since when, where it holds it, and for how
/// long it was already counted as not found again.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Lost {
    pub kind: LostKind,
    pub t: f64,
    pub z: Point,
    pub u: f64,
    /// time of the last frame that held it
    pub seen: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TrackInfo {
    /// its sensor's index
    pub si: usize,
    /// time of its last measurement
    pub t: f64,
    /// time of its last position used (every pos_every s)
    pub zt: f64,
    /// time up to which a ghost's life was counted (None: since its birth)
    pub gt: Option<f64>,
    /// variance per axis of where it sits on a person
    pub var: f64,
    pub z: Point,
    /// where it began
    pub z0: Point,
    pub born: f64,
    pub lost: Option<Lost>,
    /// its target moved when last measured (radial speed at least MOVING, m/s)
    pub moving: bool,
    /// while judged a ghost's, it runs with a walking person who has tracks (MODEL.md 5.1, `fusion`)
    pub run: Option<Run>,
}

/// A track running with a walking person's track of another sensor: which, since when, when last, and whether it
/// was already given to them.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Run {
    pub with: SegId,
    pub since: f64,
    pub last: f64,
    pub fused: bool,
}

/// s: measurements this far apart still make one run
pub const RUN_GAP: f64 = 0.5;

impl Run {
    /// The run after a measurement at t: near which track (None: none), the track moving or not. A run begins
    /// only while the track moves; a gap, another track or a measurement away from it ends it.
    pub fn next(run: Option<Run>, near: Option<SegId>, moving: bool, t: f64) -> Option<Run> {
        let with = near?;
        match run {
            Some(r) if r.with == with && t - r.last <= RUN_GAP => Some(Run { last: t, ..r }),
            _ if moving => Some(Run {
                with,
                since: t,
                last: t,
                fused: false,
            }),
            _ => None,
        }
    }

    /// Due to be given to the person: long enough and not given yet.
    pub fn due(&self, time: f64) -> bool {
        !self.fused && self.last - self.since >= time
    }
}

/// m/s: a target this fast along the beam is a walker's (the LD2450 reports still people at 0-0.16 m/s)
pub const MOVING: f64 = 0.3;

impl TrackInfo {
    /// Up to when a ghost's life was counted.
    pub fn ghost_t(&self) -> f64 {
        self.gt.unwrap_or(self.born)
    }

    /// A walking target its sensor lost and coasts with its last speed (the LD2450's own prediction): when and
    /// where it has it now.
    pub fn coasted(&self) -> Option<(f64, Point)> {
        self.lost
            .filter(|l| self.moving && l.kind == LostKind::Coast)
            .map(|l| (l.seen, l.z))
    }

    /// Where the rates of new tracks see it: measured, or held where it was lost.
    fn spot(&self) -> TrackSpot {
        match self.lost {
            None => TrackSpot {
                held: false,
                at: self.z,
                moving: self.moving,
            },
            Some(l) => TrackSpot {
                held: true,
                at: l.z,
                moving: false,
            },
        }
    }
}

#[derive(Debug, Default)]
pub struct Tracks {
    info: SmallMap<SegId, TrackInfo>,
    /// per sensor its live tracks in the order they began
    live: Vec<Vec<SegId>>,
    /// per sensor where its live tracks are (kept with every change)
    spots: Vec<Vec<TrackSpot>>,
}

impl Tracks {
    pub fn new(sensors: usize) -> Self {
        Self {
            info: SmallMap::default(),
            live: vec![Vec::new(); sensors],
            spots: vec![Vec::new(); sensors],
        }
    }

    pub fn get(&self, seg: SegId) -> Option<&TrackInfo> {
        self.info.get(&seg)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&SegId, &TrackInfo)> {
        self.info.iter()
    }

    pub fn is_empty(&self) -> bool {
        self.info.is_empty()
    }

    pub fn sensors(&self) -> usize {
        self.live.len()
    }

    /// The live tracks of a sensor, in the order they began.
    pub fn of(&self, si: usize) -> &[SegId] {
        &self.live[si]
    }

    /// Per sensor where its live tracks are, for the rates of new tracks.
    pub fn spots(&self) -> &[Vec<TrackSpot>] {
        &self.spots
    }

    /// The same without the tracks in `skip`.
    pub fn spots_without(&self, skip: &[SegId]) -> Vec<Vec<TrackSpot>> {
        self.live
            .iter()
            .map(|segs| {
                segs.iter()
                    .filter(|s| !skip.contains(s))
                    .map(|s| self.info.get(s).expect("a live track").spot())
                    .collect()
            })
            .collect()
    }

    /// Is the track live and not held lost by its sensor?
    pub fn measuring(&self, seg: SegId) -> bool {
        self.info.get(&seg).is_some_and(|i| i.lost.is_none())
    }

    /// A track begins (or a restored one is loaded).
    pub fn begin(&mut self, seg: SegId, info: TrackInfo) {
        let si = info.si;
        self.info.insert(seg, info);
        self.live[si].push(seg);
        self.spots[si].push(info.spot());
    }

    /// Changes a track; where the rates see it follows.
    pub fn update(&mut self, seg: SegId, change: impl FnOnce(&mut TrackInfo)) {
        let info = self.info.get_mut(&seg).expect("a live track");
        change(info);
        let at = self.live[info.si].iter().position(|&s| s == seg).expect("live");
        self.spots[info.si][at] = info.spot();
    }

    /// A track ends: its last state.
    pub fn end(&mut self, seg: SegId) -> TrackInfo {
        let info = self.info.remove(&seg).expect("a live track");
        let at = self.live[info.si].iter().position(|&s| s == seg).expect("live");
        self.live[info.si].remove(at);
        self.spots[info.si].remove(at);
        info
    }
}
