//! Sensor calibration (MODEL.md 10, "Kalibrierung"): heading and scale of every LD2450 from people
//! walking, for all sensors at once, also for sensors that share only a door area with another one.
//!
//! The drawn positions are the ruler of the whole and stay. The mirror (x axis to the left) is how the
//! sensor is installed; it is taken from the configuration and only compared on request. One posterior
//! over the headings and scales of all sensors (bundle adjustment, Triggs et al. 2000), with four kinds
//! of evidence:
//!
//! (a) Pairs: a person walking, measured by two sensors at the same time (one moving target each, the
//!     one interpolated to the other's frame times within one LD2450 track), is at one place. Gaussian
//!     error from the measurement model (MODEL.md 4.1) plus a uniform part for wrong pairs (a second
//!     person, an echo, a ghost): the outlier mixture of Myronenko & Song 2010, fitted by EM.
//! (b) Handovers: someone leaving one sensor's view and entering another's walks on in between
//!     (Rahimi, Dunagan & Darrell 2004: a motion model over the paths connects the poses of sensors
//!     that don't overlap). The end of one LD2450 track, carried forward over the gap of up to 2 s with
//!     its velocity by the walking model (MODEL.md 3.2: Ornstein-Uhlenbeck velocity), and the start of
//!     another sensor's track are a pair with the variance of that prediction, in the same mixture.
//! (c) Floor plan: a walking person is where the sensor can see him directly: in a room, in its line
//!     of sight (through door openings, not through walls). Per LD2450 track (MODEL.md 2: every track
//!     has an owner, a person or an echo) a mixture of "person": every point uniform on the visible free
//!     space, blurred with the measurement error (the likelihood field of Thrun, Burgard & Fox 2005,
//!     6.4, for free space instead of obstacles), and "echo": uniform on the view out to 8 m. Per
//!     track, not per point: a few points of an echo track that a turn would bring into the room don't
//!     pull the heading. It judges the heading, not the scale (people don't come up to the walls, and
//!     an echo 1.5 times as far looks like a person at 2/3 the scale).
//! (d) The scales of the LD2450s: ln scale ~ N(mu, tau^2) for every sensor; mu the median of the
//!     sensors that pairs measure against another one (hierarchical), tau as measured between the
//!     LD2450s here. A sensor without such pairs gets the scale of the others, and none is proposed.
//!
//! The drawn heading is a weak prior (+-20 deg: the drawing is rough). Pairs and floor-plan points both
//! at most one per second (per sensor pair, per track): their errors wander with 3.5 s, and the same
//! rate keeps their weights fair. Fitted by EM for the mixture weights with Gauss-Newton steps; the
//! start is the best heading of a coarse search per sensor, since the floor plan has several modes.
//!
//! What is proposed (solve.rs): a heading or scale whose uncertainty is small enough. The uncertainty is
//! the Laplace one with a cluster-robust covariance (Liang & Zeger 1986) over 30 s blocks (the errors of
//! one LD2450 track are correlated over tens of seconds, MODEL.md 4.1), plus a model error measured on
//! everyday data, and a jackknife over hours of everyday situations. No proposal where the heading has a
//! second mode, and none where the floor plan alone and the pairs and handovers alone want different
//! headings: then the drawing (the sensor's position, the walls) is off, and calibrating would only move
//! the error elsewhere.
//!
//! The data: the measured targets of every LD2450 frame through the board's own tracks (`Walking`, the
//! rows the shell keeps and stores, independent of the poses). Numbers and their order follow the Python
//! calibration of 0.43.0 (calibration.py; its results on the tests' and real data were the reference).

mod fit;
mod problem;
mod solve;
mod space;

use std::collections::BTreeSet;

use crate::filter::mixture::SegId;
use crate::frames::{CallError, Frame};
use crate::geometry::Point;
use crate::ld2450::tracks::TrackStage;

pub use solve::{Reason, Report, SensorReport, Status, solve, status};

/// m/s raw radial speed: walking
const MIN_SPEED: f64 = 0.05;
/// m on the floor: closer, the slant correction decides everything
const MIN_RANGE: f64 = 0.5;
/// s, interpolate pairs only between frames this close
const MAX_GAP: f64 = 0.25;
/// s, at most one pair per sensor pair, and one walking point per LD2450 track for the floor plan, per
/// this time: the error of a track wanders with 3.5 s (MODEL.md 4.1), denser points only repeat it; the
/// same rate for both keeps their weights fair
const EVERY: f64 = 1.0;
/// s, longest gap between one sensor's track end and another's start
const HANDOVER: f64 = 2.0;
/// s, velocity at a track end from its last second, at least this long
const END_SPAN: (f64, f64) = (0.3, 1.0);
/// s, correlation time of the walking velocity (MODEL.md 3.2: lambda_d = 0.85 /s)
const TAU_V: f64 = 1.0 / 0.85;
/// m^2/s^2 per axis, its variance (MODEL.md 3.2: (s^2 + spread^2) / 2)
const VAR_V: f64 = 0.425;
/// m, grid of the free-space field
const CELL: f64 = 0.05;
/// m, echoes: uniform on the view out to here
const VIEW: f64 = 8.0;
/// m^2, wrong pairs: residual uniform on this area
const OUTLIER_AREA: f64 = 30.0;
/// degrees, heading prior around the drawn one (assumed: drawn by eye)
const PRIOR_DEG: f64 = 20.0;
/// spread of ln scale between LD2450s (measured: four sensors here, 1.01-1.13)
const PRIOR_LNSCALE: f64 = 0.05;
/// ln of their mean scale (measured, 1.06), until rulers of this home say otherwise
const PRIOR_MU: f64 = 0.06;
/// agreeing pairs (one per second) for a sensor to have its own scale: it counts for the others' prior
/// and is proposed
const MIN_RULER: f64 = 25.0;
/// s, clusters of the robust covariance: about the life of a walking LD2450 track
const BLOCK: f64 = 30.0;
/// adjusted log posterior: another heading closer than this is a second mode
const SECOND_MODE: f64 = 3.0;
/// walking points (1 per second and track) for a sensor to be fitted at all
const MIN_POINTS: usize = 5;
/// A value counts as determined when its uncertainty moves a person 5 m away by less than the LD2450's
/// own error there (MODEL.md 4.1: 0.10 + 0.05 * 5 = 0.35 m across (the heading), 0.15 + 0.02 * 5 = 0.25 m
/// along (the scale)).
fn determined_heading() -> f64 {
    (0.35_f64 / 5.0).atan().to_degrees() // 4.0 deg
}
const DETERMINED_SCALE: f64 = 0.25 / 5.0;
/// Model error: how far the fits of 2-h windows of everyday data (6./7.10., 7-8 windows) scatter beyond
/// their own uncertainty, root mean square over the four sensors with pairs (measured; drawn positions
/// slightly off, the LD2450's angle error, the floor plan). Added to the uncertainty of every fit.
const MODEL_HEADING: f64 = 2.1;
const MODEL_SCALE: f64 = 0.042;
/// Independent stretches: errors of the model (a drawn position slightly off, the LD2450's angle error)
/// depend on where people walk, so a fit holds only if it holds across stretches of different everyday
/// situations. A stretch is an hour; the uncertainty is a delete-one-group jackknife over at most
/// JACK_GROUPS groups of consecutive hours (Kuensch 1989, blocks of a dependent series). With fewer than
/// MIN_STRETCHES hours with walking data of a sensor, its spread between situations can't be estimated,
/// and nothing is proposed for it: on 7.10. a 10-minute walk alone turned the hall by 17 deg,
/// self-consistent within the walk (jackknife over its minutes 1.6 deg), but it put thousands of the
/// hall's everyday measurements behind walls (MODEL.md 10).
const STRETCH: f64 = 3600.0;
pub const MIN_STRETCHES: usize = 3;
const JACK_GROUPS: usize = 8;

/// The headings of the coarse search around the drawn one: -90..=90 deg in steps of 5.
fn search() -> impl Iterator<Item = f64> {
    (-90..=90).step_by(5).map(|d| f64::from(d).to_radians())
}

/// A measured target of a walking LD2450 track (the rows the shell stores: t, track, raw x, raw y (m),
/// raw radial speed (m/s), whether it was the only moving target of its frame).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Row {
    pub t: f64,
    pub seg: f64,
    pub local: Point,
    pub v: f64,
    pub single: bool,
}

/// Values per row of the stored form.
pub const COLUMNS: usize = 6;

impl Row {
    /// The stored form: float64 rows (t, track, x, y, speed, single), little-endian, one after the other.
    pub fn of_bytes(bytes: &[u8]) -> Result<Vec<Self>, String> {
        let (rows, rest) = bytes.as_chunks::<{ 8 * COLUMNS }>();
        if !rest.is_empty() {
            return Err(format!("{} bytes are no rows of {COLUMNS} numbers", bytes.len()));
        }
        Ok(rows
            .iter()
            .map(|row| {
                let (numbers, _) = row.as_chunks::<8>();
                let x: [f64; COLUMNS] = std::array::from_fn(|k| f64::from_le_bytes(numbers[k]));
                Self {
                    t: x[0],
                    seg: x[1],
                    local: [x[2], x[3]],
                    v: x[4],
                    single: x[5] > 0.0,
                }
            })
            .collect())
    }

    /// The row in the stored form's order.
    pub fn values(&self) -> [f64; COLUMNS] {
        [
            self.t,
            self.seg,
            self.local[0],
            self.local[1],
            self.v,
            f64::from(u8::from(self.single)),
        ]
    }
}

/// The walking targets of every LD2450 frame, through the boards' own tracks (one sequence of track ids
/// for all): coasted and frozen frames are no measurement (MODEL.md 4.1), and only walking counts (pairs,
/// handovers, floor plan). While learning is paused (pause.py) nothing is kept, and the tracks walking
/// then are never kept (else such a track's first row after the pause would look like a start, a
/// handover).
#[derive(Debug, Clone)]
pub struct Walking {
    tracks: TrackStage,
    skip: BTreeSet<SegId>,
}

impl Walking {
    /// Track ids from `first` (on after the ones of stored rows).
    pub fn starting_at(first: u64) -> Self {
        Self {
            tracks: TrackStage::starting_at(first),
            skip: BTreeSet::new(),
        }
    }

    /// A frame of a board at t: the rows to keep (none while paused).
    pub fn frame(&mut self, board: &str, t: f64, frame: &Frame, paused: bool) -> Result<Vec<Row>, CallError> {
        let measured = self.tracks.update(board, t, frame)?;
        let moving = measured.iter().filter(|m| m.speed.abs() >= MIN_SPEED).count();
        let mut rows = Vec::new();
        for m in measured.iter().filter(|m| m.speed.abs() >= MIN_SPEED) {
            if paused {
                self.skip.insert(m.seg);
            } else if !self.skip.contains(&m.seg) {
                rows.push(Row {
                    t,
                    seg: m.seg.0 as f64,
                    local: m.local,
                    v: m.speed,
                    single: moving == 1,
                });
            }
        }
        Ok(rows)
    }
}

/// A complex number as the calibration uses them: a point in a plane (x + iy), or a turn and scale.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
struct C {
    re: f64,
    im: f64,
}

impl C {
    const fn new(re: f64, im: f64) -> Self {
        Self { re, im }
    }

    /// |c| (numpy's and Python's abs: libm's hypot)
    fn abs(self) -> f64 {
        self.re.hypot(self.im)
    }
}

impl std::ops::Add for C {
    type Output = Self;
    fn add(self, o: Self) -> Self {
        Self::new(self.re + o.re, self.im + o.im)
    }
}

impl std::ops::Sub for C {
    type Output = Self;
    fn sub(self, o: Self) -> Self {
        Self::new(self.re - o.re, self.im - o.im)
    }
}

impl std::ops::Mul for C {
    type Output = Self;
    fn mul(self, o: Self) -> Self {
        Self::new(self.re * o.re - self.im * o.im, self.re * o.im + self.im * o.re)
    }
}

impl std::ops::Mul<f64> for C {
    type Output = Self;
    fn mul(self, k: f64) -> Self {
        Self::new(self.re * k, self.im * k)
    }
}

/// By a real number as numpy divides a complex one by it (Smith's division with a zero imaginary part:
/// times the reciprocal).
impl std::ops::Div<f64> for C {
    type Output = Self;
    fn div(self, k: f64) -> Self {
        let scale = 1.0 / k;
        Self::new(self.re * scale, self.im * scale)
    }
}

/// Python's max(a, b) of two numbers: b only if it is larger (NaN in a stays).
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// numpy's clip: NaN stays NaN.
fn clip(x: f64, lo: f64, hi: f64) -> f64 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

/// x mod m as Python's % of floats (float_rem: the sign of m; 0 as +0 for m > 0).
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r != 0.0 {
        if (m < 0.0) != (r < 0.0) { r + m } else { r }
    } else {
        0.0_f64.copysign(m)
    }
}

/// x // m as Python's and numpy's floor division of floats (float_divmod).
fn py_floordiv(x: f64, m: f64) -> f64 {
    let r = x % m;
    let mut div = (x - r) / m;
    if r != 0.0 && (m < 0.0) != (r < 0.0) {
        div -= 1.0;
    }
    if div != 0.0 {
        let f = div.floor();
        if div - f > 0.5 { f + 1.0 } else { f }
    } else {
        0.0_f64.copysign(x / m)
    }
}

/// The heading (degrees from the +x axis) of a sensor whose frame is turned by the angle of c (its
/// forward axis is y).
fn heading_of(c: C) -> f64 {
    py_mod(c.im.atan2(c.re).to_degrees() + 90.0, 360.0)
}

/// a - b in (-180, 180] degrees.
fn angle_diff(a: f64, b: f64) -> f64 {
    py_mod(a - b + 180.0, 360.0) - 180.0
}

/// Indices keeping at most one entry per `every` seconds per key, in time order (ties by index).
fn thin(t: &[f64], key: &[f64], every: f64) -> Vec<usize> {
    let mut order: Vec<usize> = (0..t.len()).collect();
    order.sort_by(|&a, &b| t[a].total_cmp(&t[b]));
    let mut last: Vec<(f64, f64)> = Vec::new(); // (key, its last time)
    let mut keep = Vec::new();
    for i in order {
        let k = key[i];
        let slot = last.iter().position(|&(x, _)| x == k);
        let before = slot.map_or(-1e18, |s| last[s].1);
        if t[i] - before >= every {
            keep.push(i);
            match slot {
                Some(s) => last[s].1 = t[i],
                None => last.push((k, t[i])),
            }
        }
    }
    keep.sort_unstable();
    keep
}

/// numpy's median: the middle one, or the mean of the two middle ones.
fn median(v: &[f64]) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(f64::total_cmp);
    let n = s.len();
    if n == 0 {
        return f64::NAN;
    }
    if n % 2 == 1 {
        s[n / 2]
    } else {
        (s[n / 2 - 1] + s[n / 2]) / 2.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_s_remainder_and_floor_division() {
        assert_eq!(py_mod(-10.0, 360.0), 350.0);
        assert_eq!(py_mod(-1e-17, 360.0), 360.0);
        assert!(py_mod(-0.0, 360.0).is_sign_positive());
        assert_eq!(py_floordiv(7199.999, 3600.0), 1.0);
        assert_eq!(py_floordiv(-1.0, 3600.0), -1.0);
        assert_eq!(angle_diff(350.0, 10.0), -20.0);
        assert_eq!(heading_of(C::new(1.0, 0.0)), 90.0);
    }

    #[test]
    fn thinned_to_one_per_second_and_key() {
        let t = [0.0, 0.5, 1.0, 0.2, 1.5, 2.1];
        let key = [1.0, 1.0, 1.0, 2.0, 2.0, 1.0];
        assert_eq!(thin(&t, &key, 1.0), [0, 2, 3, 4, 5]);
        assert_eq!(median(&[3.0, 1.0, 2.0, 10.0]), 2.5);
    }
}
