//! What the filter knows of each sensor while it runs (MODEL.md 4.4): whether its frames count, when
//! its last frame came, its LD2410C.

use crate::ld2410::sensor::LdSensor;
use crate::params::{IDLE, Params};

#[derive(Debug, Clone)]
pub struct SensorState {
    /// enabled and placed in the plan: its frames count
    pub active: bool,
    /// time of its last frame (-inf: none yet)
    pub last_frame: f64,
    pub ld: LdSensor,
}

impl SensorState {
    /// A sensor without frames, not counting yet.
    pub fn new(p: &Params) -> Self {
        Self {
            active: false,
            last_frame: f64::NEG_INFINITY,
            ld: LdSensor::new(p),
        }
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
        .filter(|(_, s)| s.active && t - s.last_frame <= IDLE)
        .map(|(si, _)| si)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_active_sensors_with_recent_frames_watch() {
        let p = Params::default();
        let s = |active, last_frame| SensorState {
            active,
            last_frame,
            ..SensorState::new(&p)
        };
        let sensors = [s(true, 10.0), s(false, 10.0), s(true, 4.0), s(true, f64::NEG_INFINITY)];
        let w = live(&sensors, 9.5);
        assert_eq!(w.iter().collect::<Vec<_>>(), vec![0, 2]);
        assert_eq!(live(&sensors, 10.5).without(w), SensorSet::default());
        assert!(!live(&sensors, 10.5).contains(2));
    }
}
