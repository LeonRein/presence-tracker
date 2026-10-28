//! A sensor's LD2410C while the filter runs (MODEL.md 4.3): when its frames came, what they gathered
//! since its last weighing, what the people put into its cells lately, its echo sources.

use super::CELLS;
use super::echoes::Echoes;
use super::likelihood::Stats;
use crate::frames::LdGates;
use crate::params::{IDLE, Params};

/// When a sensor's LD2410C frames came and what the last one brought, when its LD2450 last had
/// something in sight (the shell's runtime until 0.33: ld_t, ld_e, ld_target_t).
#[derive(Debug, Clone, Copy)]
pub struct LdCollect {
    pub t: f64,
    pub last: Option<[f64; CELLS]>,
    pub target_t: f64,
}

impl Default for LdCollect {
    fn default() -> Self {
        Self {
            t: f64::NEG_INFINITY,
            last: None,
            target_t: f64::NEG_INFINITY,
        }
    }
}

#[derive(Debug, Clone)]
pub struct LdSensor {
    /// kept when it starts over
    pub collect: LdCollect,
    /// its frames since they were last weighed (time 0: none)
    pub(crate) stats: Stats,
    /// what the people put into its cells lately (the still ones lag; None before its first weighing)
    pub(crate) memory: Option<[f64; CELLS]>,
    pub(crate) echoes: Echoes,
}

impl LdSensor {
    pub fn new(p: &Params) -> Self {
        Self {
            collect: LdCollect::default(),
            stats: Stats::default(),
            memory: None,
            echoes: Echoes::new(p),
        }
    }

    /// Starts over (recalibrated, the filter cleared); when its frames came stays.
    pub fn start_over(&mut self, p: &Params) {
        *self = Self {
            collect: self.collect,
            ..Self::new(p)
        };
    }

    /// What it gathered since its last weighing.
    pub fn stats(&self) -> &Stats {
        &self.stats
    }

    /// Whether it has gathered long enough to be weighed.
    pub fn due(&self, every: f64) -> bool {
        self.stats.time >= every
    }

    /// The energies of a frame (moving 0..8, still 0..8), gathered until they are weighed
    /// (Tracker._ld_frame). A frame stands for one frame (`frame` s). Without anything to report the
    /// firmware sends only a heartbeat every 5 s, and the first frame after such a silence is there
    /// because something rose: the silence stands for the energies of the frame before it. A gap
    /// longer than IDLE is lost data. All energies 0 are the firmware's "no value", no measurement,
    /// and so is a time or an energy that is not finite. target: the housing's LD2450 has something
    /// in sight.
    pub fn frame(&mut self, t: f64, gates: Option<&LdGates>, target: bool, frame: f64) {
        let collect = &mut self.collect;
        let dt = t - collect.t;
        if !(dt > 0.0) || !t.is_finite() {
            return;
        }
        collect.t = t;
        let prev = collect.last.take();
        let Some(g) = gates else {
            return;
        };
        let e: [f64; CELLS] = std::array::from_fn(|c| if c < 9 { g.moving[c] } else { g.still[c - 9 + 2] });
        if e.iter().all(|&x| x == 0.0) || !e.iter().all(|x| x.is_finite()) {
            return;
        }
        collect.last = Some(e);
        if target || dt > IDLE {
            // how long it has seen nothing (a gap in the data says nothing: from now)
            collect.target_t = t;
        }
        let st = &mut self.stats;
        match prev {
            _ if dt > IDLE => st.add(&e, frame, target),
            Some(prev) if dt > 2.0 * frame => {
                st.add(&prev, dt - frame, false);
                st.add(&e, frame, target);
            }
            _ => st.add(&e, dt, target), // the next frame (or one lost on the way)
        }
    }
}
