//! What the filter knows of each sensor while it runs (MODEL.md 4.4): whether its frames count, its
//! LD2450's own tracks (they keep the time of its last frame: the one place for it) and its last
//! frame, its LD2410C.

use crate::frames::{Detection, Ld2410};
use crate::ld2410::sensor::LdSensor;
use crate::ld2450::tracks::{Ld2450Tracks, TrackEvents};
use crate::params::{IDLE, Params};

#[derive(Debug, Clone)]
pub struct SensorState {
    /// enabled and placed in the plan: its frames count
    pub active: bool,
    pub ld: LdSensor,
    pub tracks: Ld2450Tracks,
    pub frame: SensorFrame,
}

impl SensorState {
    /// A sensor without frames, not counting yet.
    pub fn new(p: &Params, active: bool) -> Self {
        Self {
            active,
            ld: LdSensor::new(p),
            tracks: Ld2450Tracks::default(),
            frame: SensorFrame::default(),
        }
    }

    /// Time of its last frame (-inf: none since the start, its tracks' reset or a rebuild).
    pub fn last_frame(&self) -> f64 {
        self.tracks.last()
    }
}

/// A sensor's last frame as the tracker read it: its targets in the house frame, what they said about
/// its tracks, its LD2410C part (default: no frame yet).
#[derive(Debug, Default, Clone)]
pub struct SensorFrame {
    pub t: f64,
    pub detections: Vec<Detection>,
    /// per detection whether its LD2450 measured it in a track (not hidden, not held)
    pub measured: Vec<bool>,
    /// the events not yet taken out by the steps of the evidence
    pub events: TrackEvents,
    /// its LD2410C part until it is gathered (Tracker::ld_frame)
    pub ld2410: Option<Ld2410>,
}

impl SensorFrame {
    /// The detections its LD2450 measured (in a track: not hidden, not held), in the frame's order.
    pub fn measured(&self) -> impl Iterator<Item = &Detection> {
        self.detections
            .iter()
            .zip(&self.measured)
            .filter(|&(_, &m)| m)
            .map(|(d, _)| d)
    }

    /// Whether its LD2450 has anything in sight that can be a person (the LD2410C's anchor).
    pub fn target_in_sight(&self) -> bool {
        self.detections.iter().any(|d| !d.hidden)
    }
}

/// A set of sensors by their index (at most 64), iterated in the order of the indices.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Hash)]
pub struct SensorSet(u64);

impl SensorSet {
    pub fn insert(&mut self, si: usize) {
        assert!(si < 64, "at most 64 sensors");
        self.0 |= 1 << si;
    }

    pub fn contains(self, si: usize) -> bool {
        si < 64 && self.0 & (1 << si) != 0
    }

    pub fn is_empty(self) -> bool {
        self.0 == 0
    }

    pub fn without(self, other: SensorSet) -> SensorSet {
        SensorSet(self.0 & !other.0)
    }

    pub fn iter(self) -> impl Iterator<Item = usize> {
        (0..64).filter(move |&si| self.contains(si))
    }
}

impl FromIterator<usize> for SensorSet {
    fn from_iter<I: IntoIterator<Item = usize>>(iter: I) -> Self {
        let mut s = SensorSet::default();
        for si in iter {
            s.insert(si);
        }
        s
    }
}

/// The sensors watching at t: active, their last frame at most IDLE ago (filter.Tracker._live).
pub fn live(sensors: &[SensorState], t: f64) -> SensorSet {
    sensors
        .iter()
        .enumerate()
        .filter(|(_, s)| s.active && t - s.last_frame() <= IDLE)
        .map(|(si, _)| si)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ld2450::tracks::SegIds;

    #[test]
    fn only_active_sensors_with_recent_frames_watch() {
        let p = Params::default();
        let s = |active, last_frame: f64| {
            let mut s = SensorState::new(&p, active);
            if last_frame.is_finite() {
                s.tracks.update(last_frame, &[], &mut SegIds::default());
            }
            s
        };
        let sensors = [s(true, 10.0), s(false, 10.0), s(true, 4.0), s(true, f64::NEG_INFINITY)];
        let w = live(&sensors, 9.5);
        assert_eq!(w.iter().collect::<Vec<_>>(), vec![0, 2]);
        assert_eq!(live(&sensors, 10.5).without(w), SensorSet::default());
        assert!(!live(&sensors, 10.5).contains(2));
    }
}
