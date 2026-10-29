//! The numbers of the model (MODEL.md) and the shapes derived from them
//!. Measured ones and assumptions are explained in filtermodel.py; here only
//! what the core reads. A model is given as the fields that differ from these defaults.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use serde::{Deserialize, Deserializer, Serialize};

mod shapes;
pub use shapes::Shapes;

/// A number of the model, also "inf", "-inf" or "nan" (JSON has no such numbers; a model may set a
/// time to infinity, e.g. unseen_life in the tests).
fn real<'de, D: Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Real {
        Number(f64),
        Name(String),
    }
    match Real::deserialize(d)? {
        Real::Number(x) => Ok(x),
        Real::Name(name) => match name.as_str() {
            "inf" => Ok(f64::INFINITY),
            "-inf" => Ok(f64::NEG_INFINITY),
            "nan" => Ok(f64::NAN),
            _ => Err(serde::de::Error::custom(format!("not a number: {name}"))),
        },
    }
}

/// s: the LD2450's frame period (measured)
pub const FRAME: f64 = 0.089;
/// s: frames further apart were lost on the way (the idle heartbeat is 5 s)
pub const IDLE: f64 = 6.0;
/// False alarm probability per detection cell of a CFAR radar
pub const P_FA: f64 = 1e-6;

/// Index of the standing and the walking component (WALK).
pub const STILL: usize = 0;
pub const WALK: usize = 1;

#[derive(Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Params {
    // 3.1 standing
    #[serde(deserialize_with = "real")]
    pub still_noise: f64,
    /// how long one stands: survival (go_time / (t + go_time))^go_share, as a mixture of exponentials
    /// matched at the stay_points (s)
    #[serde(deserialize_with = "real")]
    pub go_share: f64,
    #[serde(deserialize_with = "real")]
    pub go_time: f64,
    pub stay_points: Vec<f64>,
    /// 4.1 the detectability of a still person, kappa ~ Gamma(shape, shape), on a grid of this many levels
    #[serde(deserialize_with = "real")]
    pub kappa_shape: f64,
    pub kappa_levels: usize,
    /// 4.3 the LD2410C amplitude of a standing person, likewise; its normal scores' correlation with kappa
    #[serde(deserialize_with = "real")]
    pub ld_amp_shape: f64,
    pub ld_amp_levels: usize,
    #[serde(deserialize_with = "real")]
    pub kappa_amp_corr: f64,
    #[serde(deserialize_with = "real")]
    pub kappa_switch: f64,
    // 3.2 walking
    #[serde(deserialize_with = "real")]
    pub speed: f64,
    #[serde(deserialize_with = "real")]
    pub speed_spread: f64,
    #[serde(deserialize_with = "real")]
    pub turn_rate: f64,
    #[serde(deserialize_with = "real")]
    pub walk_length: f64,
    // 3.3, 3.4 coming home, newcomers, forgetting
    #[serde(deserialize_with = "real")]
    pub arrive_rate: f64,
    #[serde(deserialize_with = "real")]
    pub guest_rate: f64,
    #[serde(deserialize_with = "real")]
    pub forget_rate: f64,
    // 4.1 the sensor's tracks: rho and r50 [STILL, WALK]
    pub acquire: [[f64; 2]; 2],
    #[serde(deserialize_with = "real")]
    pub find_radius: f64,
    #[serde(deserialize_with = "real")]
    pub res_range: f64,
    #[serde(deserialize_with = "real")]
    pub res_cross: f64,
    #[serde(deserialize_with = "real")]
    pub const_share: f64,
    #[serde(deserialize_with = "real")]
    pub tau: f64,
    /// m: frame jitter of a track's position
    #[serde(deserialize_with = "real")]
    pub white: f64,
    /// s: a track's positions are used this often
    #[serde(deserialize_with = "real")]
    pub pos_every: f64,
    /// a lost target is found again on a reflection near the spot as on this many people there
    #[serde(deserialize_with = "real")]
    pub find_clutter: f64,
    /// s [STILL, WALK]: how long a track lasts, for the tracks there at a start
    pub track_life: [f64; 2],
    /// time to finding a lost target again, per kind of loss (4.1)
    pub refind: Refind,
    // 4.2 ghosts: echoes per m^2, s and walking person in view; the kinds (rate, mean life s)
    #[serde(deserialize_with = "real")]
    pub ghost_echo: f64,
    pub ghost_types: Vec<[f64; 2]>,
    /// the ghost map's prior: in each cell the weight of this many ghosts (MODEL.md 4.2)
    #[serde(deserialize_with = "real")]
    pub ghost_prior_ghosts: f64,
    // 5.3 nothing known: the unknown people expected at a start
    #[serde(deserialize_with = "real")]
    pub start_unknown: f64,
    // 5.5 a known person's record expires where no sensor could test it
    #[serde(deserialize_with = "real")]
    pub unseen_life: f64,
    // 5.1 the cut of the hypotheses
    pub max_hyps: usize,
    #[serde(deserialize_with = "real")]
    pub hyp_mass: f64,
    /// a known person who exists with less probability joins the unknown ones
    #[serde(deserialize_with = "real")]
    pub give_up: f64,
    /// s: motion is cut into parts no longer than this
    #[serde(deserialize_with = "real")]
    pub max_step: f64,
    // 4.3 the LD2410C: per mode [STILL, WALK] the profile of the moving and the still cells, the
    // beam (degrees: full, none), the Gamma shape and the correlation time (moving, still), the
    // common gain's prior (moving, still) and its bursts
    pub ld_moving: [Profile; 2],
    pub ld_still: [Profile; 2],
    pub ld_beam: [f64; 2],
    pub ld_shape: [f64; 2],
    pub ld_tau: [f64; 2],
    pub ld_gain: [f64; 2],
    pub ld_burst: Burst,
    /// s: the energies are weighed this often
    #[serde(deserialize_with = "real")]
    pub ld_every: f64,
    /// s: a frame of the LD2410C stands for this long
    #[serde(deserialize_with = "real")]
    pub ld_frame: f64,
    /// s: the still energies follow a person with this time constant
    #[serde(deserialize_with = "real")]
    pub ld_memory: f64,
    // echo sources: mean life s, its stages, the slants (from, to, step m) and amplitudes
    #[serde(deserialize_with = "real")]
    pub ld_echo_life: f64,
    pub ld_echo_stages: usize,
    pub ld_echo_ranges: Slants,
    pub ld_echo_amps: Vec<f64>,
    /// a tile holding less of a person standing is weighed at its mean amplitude
    #[serde(deserialize_with = "real")]
    pub ld_tile_mass: f64,
    /// the background before anything is learned: moving gates 0, 1, the other moving gates, the still
    /// gates; its weight (s), forgotten with ld_forget s towards the sensor's own level, which is learned
    /// forgotten with ld_anchor_forget s from frames after ld_anchor_quiet s with nothing in sight; the
    /// echo sources' rate (per s) with the weight ld_echo_prior_time s (MODEL.md 4.3)
    pub ld_background: [f64; 4],
    #[serde(deserialize_with = "real")]
    pub ld_prior_time: f64,
    #[serde(deserialize_with = "real")]
    pub ld_forget: f64,
    #[serde(deserialize_with = "real")]
    pub ld_anchor_forget: f64,
    #[serde(deserialize_with = "real")]
    pub ld_anchor_quiet: f64,
    #[serde(deserialize_with = "real")]
    pub ld_echo_rate: f64,
    #[serde(deserialize_with = "real")]
    pub ld_echo_prior_time: f64,
    /// "Ziel": per walker this many walks of prior weight on its own motion (MODEL.md 6)
    #[serde(deserialize_with = "real")]
    pub dest_prior_walks: f64,
    /// s: the end of a gap in the data is moved as always, what lies before in leaps of `leap` s,
    /// at most `max_leaps` of them (LEAP, MAX_LEAPS)
    #[serde(deserialize_with = "real")]
    pub gap_exact: f64,
    #[serde(deserialize_with = "real")]
    pub leap: f64,
    pub max_leaps: usize,
    /// s without any frame: a room's own sensor is gone, the room silent (MODEL.md 6 "Belegt")
    #[serde(deserialize_with = "real")]
    pub silent_after: f64,
}

/// How a lost target is held: coasting on, or frozen where it was lost.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LostKind {
    Coast,
    Frozen,
}

/// Per kind of loss the time to finding the target again: median s, log-normal spread, the share
/// never found.
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Refind {
    pub coast: [f64; 3],
    pub frozen: [f64; 3],
}

impl Refind {
    pub fn of(&self, kind: LostKind) -> [f64; 3] {
        match kind {
            LostKind::Coast => self.coast,
            LostKind::Frozen => self.frozen,
        }
    }
}

impl Default for Params {
    fn default() -> Self {
        Self {
            still_noise: 0.02,
            go_share: 0.58,
            go_time: 5.6,
            stay_points: vec![86400.0, 5993.0, 416.0, 29.0, 2.0],
            kappa_shape: 1.0,
            kappa_levels: 5,
            ld_amp_shape: 6.0,
            ld_amp_levels: 9,
            kappa_amp_corr: 0.44,
            kappa_switch: 1.0 / 600.0,
            speed: 0.85,
            speed_spread: 0.36,
            turn_rate: 0.85,
            walk_length: 2.0,
            arrive_rate: 1.0 / (4.0 * 3600.0),
            guest_rate: 1.0 / (2.0 * 86400.0),
            forget_rate: 1.0 / 86400.0,
            acquire: [[0.034, 5.0], [0.51, 4.55]],
            find_radius: 0.7,
            res_range: 0.6,
            res_cross: 0.9,
            const_share: 0.57,
            tau: 3.5,
            white: 0.05,
            pos_every: 0.25,
            find_clutter: 0.05,
            track_life: [60.0, 30.0],
            refind: Refind {
                coast: [0.13, 1.0, 0.03],
                frozen: [0.2, 2.0, 0.02],
            },
            ghost_echo: 3e-4,
            ghost_types: vec![[0.4 * 1.8e-5, 3.0], [0.6 * 1.8e-5, 39.0]],
            ghost_prior_ghosts: 1.0,
            start_unknown: 1.0,
            unseen_life: 120.0,
            max_hyps: 12,
            hyp_mass: 1e-7,
            give_up: 0.01,
            max_step: 0.2,
            ld_moving: [
                Profile::new([63.0, 2.52, 0.41, 0.55, 0.27, 1.34]),
                Profile::new([159.4, 2.16, 0.41, 0.57, 0.47, 1.26]),
            ],
            ld_still: [
                Profile::new([574.0, 2.03, 0.19, 0.77, 0.40, 1.70]),
                Profile::new([949.0, 2.19, 0.58, 1.44, 1.00, 1.25]),
            ],
            ld_beam: [50.0, 70.0],
            ld_shape: [2.5, 2.0],
            ld_tau: [4.0, 13.0],
            ld_gain: [16.0, 50.0],
            ld_burst: Burst {
                shape: 4.0,
                gain: 2.5,
                weight: 0.02,
            },
            ld_every: 1.0,
            ld_frame: 0.089,
            ld_memory: 2.0,
            ld_echo_life: 20.0,
            ld_echo_stages: 2,
            ld_echo_ranges: Slants {
                from: 0.75,
                to: 6.5,
                step: 0.5,
            },
            ld_echo_amps: vec![0.3, 1.0, 3.0],
            ld_tile_mass: 1e-4,
            ld_background: [13.0, 9.0, 4.5, 5.0],
            ld_prior_time: 600.0,
            ld_forget: 0.7 * 3600.0,
            ld_anchor_forget: 24.0 * 3600.0,
            ld_anchor_quiet: 600.0,
            ld_echo_rate: 1.0 / (3.0 * 3600.0),
            ld_echo_prior_time: 3.0 * 3600.0,
            dest_prior_walks: 1.0,
            gap_exact: 120.0,
            leap: 15.0,
            max_leaps: 2000,
            silent_after: crate::room_seen::SILENT_AFTER,
        }
    }
}

impl Params {
    /// The model from the fields that differ from the defaults, as JSON (non-finite numbers as
    /// "inf", "-inf", "nan").
    pub fn from_json(json: &str) -> serde_json::Result<Self> {
        serde_json::from_str(json)
    }

    /// Whether the numbers make a model: what divides, counts or is a probability in range (a step
    /// of 0 in the echo sources' slants would make endless sources, an ld_memory of 0 NaN), and what
    /// is counted or held per element far below what takes all memory (a typing error is refused,
    /// not tried: 1e308 as the last slant made endless sources).
    pub fn validate(&self) -> Result<(), ModelError> {
        let positive = |x: f64| x > 0.0 && x.is_finite();
        let at_least_0 = |x: f64| x >= 0.0 && x.is_finite();
        let share = |x: f64| (0.0..1.0).contains(&x);
        // a time that may be endless (unseen_life in the tests: nothing expires)
        let time = |x: f64| x > 0.0;
        let up_to = |n: usize, lo: usize, hi: usize| (lo..=hi).contains(&n);
        let profiles = self.ld_moving.iter().chain(&self.ld_still);
        let refind = [self.refind.coast, self.refind.frozen];
        let slants = (self.ld_echo_ranges.to - self.ld_echo_ranges.from) / self.ld_echo_ranges.step;
        let checks = [
            ("still_noise", positive(self.still_noise)),
            ("go_share", positive(self.go_share)),
            ("go_time", positive(self.go_time)),
            (
                "stay_points",
                up_to(self.stay_points.len(), 2, 16) && self.stay_points.iter().all(|&t| positive(t)),
            ),
            ("kappa_shape", positive(self.kappa_shape)),
            ("kappa_levels", up_to(self.kappa_levels, 2, 64)),
            ("ld_amp_shape", positive(self.ld_amp_shape)),
            ("ld_amp_levels", up_to(self.ld_amp_levels, 2, 64)),
            ("kappa_amp_corr", self.kappa_amp_corr.abs() < 1.0),
            ("kappa_switch", at_least_0(self.kappa_switch)),
            ("speed", positive(self.speed)),
            ("speed_spread", at_least_0(self.speed_spread)),
            ("turn_rate", positive(self.turn_rate)),
            ("walk_length", positive(self.walk_length)),
            ("arrive_rate", at_least_0(self.arrive_rate)),
            ("guest_rate", at_least_0(self.guest_rate)),
            ("forget_rate", at_least_0(self.forget_rate)),
            ("acquire", self.acquire.iter().flatten().all(|&x| positive(x))),
            ("find_radius", positive(self.find_radius)),
            ("res_range", positive(self.res_range)),
            ("res_cross", positive(self.res_cross)),
            ("const_share", (0.0..=1.0).contains(&self.const_share)),
            ("tau", positive(self.tau)),
            ("white", positive(self.white)),
            ("pos_every", positive(self.pos_every)),
            ("find_clutter", at_least_0(self.find_clutter)),
            ("track_life", self.track_life.iter().all(|&t| time(t))),
            (
                "refind",
                refind
                    .iter()
                    .all(|&[median, spread, never]| positive(median) && positive(spread) && share(never)),
            ),
            ("ghost_echo", at_least_0(self.ghost_echo)),
            (
                "ghost_types",
                up_to(self.ghost_types.len(), 1, 16)
                    && self
                        .ghost_types
                        .iter()
                        .all(|&[rate, life]| at_least_0(rate) && positive(life))
                    && self.ghost_types.iter().map(|t| t[0]).sum::<f64>() > 0.0,
            ),
            ("ghost_prior_ghosts", positive(self.ghost_prior_ghosts)),
            ("start_unknown", at_least_0(self.start_unknown)),
            ("unseen_life", time(self.unseen_life)),
            ("max_hyps", up_to(self.max_hyps, 1, 1000)),
            ("hyp_mass", share(self.hyp_mass)),
            ("give_up", (0.0..=1.0).contains(&self.give_up)),
            ("max_step", positive(self.max_step)),
            (
                "ld_moving, ld_still",
                profiles.clone().all(|q| {
                    at_least_0(q.a)
                        && q.n.is_finite()
                        && q.delta.is_finite()
                        && positive(q.sigma)
                        && positive(q.ell)
                        && (0.0..=1.0).contains(&q.tail)
                }),
            ),
            (
                "ld_beam",
                self.ld_beam[0] < self.ld_beam[1] && self.ld_beam.iter().all(|&a| a.is_finite()),
            ),
            ("ld_shape", self.ld_shape.iter().all(|&x| positive(x))),
            ("ld_tau", self.ld_tau.iter().all(|&x| positive(x))),
            ("ld_gain", self.ld_gain.iter().all(|&x| positive(x))),
            (
                "ld_burst",
                positive(self.ld_burst.shape)
                    && positive(self.ld_burst.gain)
                    && self.ld_burst.weight > 0.0
                    && share(self.ld_burst.weight),
            ),
            ("ld_every", positive(self.ld_every)),
            ("ld_frame", positive(self.ld_frame)),
            ("ld_memory", positive(self.ld_memory)),
            ("ld_echo_life", positive(self.ld_echo_life)),
            ("ld_echo_stages", up_to(self.ld_echo_stages, 1, 16)),
            (
                "ld_echo_ranges",
                positive(self.ld_echo_ranges.from)
                    && positive(self.ld_echo_ranges.step)
                    && (0.0..=1000.0).contains(&slants),
            ),
            (
                "ld_echo_amps",
                up_to(self.ld_echo_amps.len(), 1, 16) && self.ld_echo_amps.iter().all(|&a| positive(a)),
            ),
            ("ld_tile_mass", at_least_0(self.ld_tile_mass)),
            ("ld_background", self.ld_background.iter().all(|&b| positive(b))),
            ("ld_prior_time", positive(self.ld_prior_time)),
            ("ld_forget", positive(self.ld_forget)),
            ("ld_anchor_forget", positive(self.ld_anchor_forget)),
            ("ld_anchor_quiet", at_least_0(self.ld_anchor_quiet)),
            ("ld_echo_rate", positive(self.ld_echo_rate)),
            ("ld_echo_prior_time", positive(self.ld_echo_prior_time)),
            ("dest_prior_walks", positive(self.dest_prior_walks)),
            ("gap_exact", at_least_0(self.gap_exact)),
            ("leap", positive(self.leap)),
            ("max_leaps", up_to(self.max_leaps, 1, 100_000)),
            ("silent_after", at_least_0(self.silent_after)),
        ];
        let fields: Vec<&'static str> = checks.iter().filter(|&&(_, ok)| !ok).map(|&(name, _)| name).collect();
        if fields.is_empty() {
            Ok(())
        } else {
            Err(ModelError { fields })
        }
    }

    /// 1/s at which walkers stop: the mean walk is walk_length long (MODEL.md 3.2).
    pub fn stop_rate(&self) -> f64 {
        self.speed / self.walk_length
    }

    /// The walkers' stationary velocity variance per axis.
    pub fn walk_var(&self) -> f64 {
        0.5 * (self.speed.powf(2.0) + self.speed_spread.powf(2.0))
    }
}

/// Numbers of a model that do not make one, by the names of their fields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelError {
    pub fields: Vec<&'static str>,
}

impl std::fmt::Display for ModelError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "the model's numbers do not fit: {}", self.fields.join(", "))
    }
}

impl std::error::Error for ModelError {}

/// What only the outputs read (MODEL.md 6): a change of
/// them restarts nothing, but it is a new world (the outputs' cache is void).
#[derive(Debug, Clone, PartialEq)]
pub struct OutputParams {
    /// a second of light without anybody costs as much as this many seconds dark: occupied above
    /// light_cost / (light_cost + 1)
    pub light_cost: f64,
    /// s: a room is occupied while its own LD2450 measured somebody in it this recently (0: never)
    pub seen_hold: f64,
    /// s: the look-ahead of "wird betreten", on above approach_cost / (approach_cost + 1)
    pub lead_time: f64,
    pub approach_cost: f64,
    /// s without a frame: a room's own sensor is gone, the room silent
    pub silent_after: f64,
    /// "Ziel": on above this, per room id its own if it has one (MODEL.md 6)
    pub target_threshold: f64,
    pub target_thresholds: std::collections::BTreeMap<String, f64>,
}

/// The fields of the configuration's parameters that only the outputs read (OutputParams::of): a change
/// of them alone builds nothing (Tracker::set_outputs).
pub const OUTPUT_FIELDS: [&str; 6] = [
    "light_cost",
    "seen_hold",
    "lead_time",
    "approach_cost",
    "target_threshold",
    "target_thresholds",
];

impl OutputParams {
    pub fn of(p: &crate::config::TrackerParams, silent_after: f64) -> Self {
        Self {
            light_cost: p.light_cost,
            seen_hold: p.seen_hold,
            lead_time: p.lead_time,
            approach_cost: p.approach_cost,
            silent_after,
            target_threshold: p.target_threshold,
            target_thresholds: p.target_thresholds.clone(), // the world's own copy of the configuration's
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        let ok = |x: f64| x >= 0.0 && x.is_finite();
        if [
            self.light_cost,
            self.seen_hold,
            self.lead_time,
            self.approach_cost,
            self.silent_after,
        ]
        .iter()
        .all(|&x| ok(x))
        {
            Ok(())
        } else {
            Err(format!("the outputs' numbers do not fit: {self:?}"))
        }
    }

    /// The threshold of P(somebody there) for occupied (MODEL.md 6: the Bayes decision).
    pub fn occupied_above(&self) -> f64 {
        self.light_cost / (self.light_cost + 1.0)
    }

    /// The threshold of "Ziel" in a room: its own, else the default, kept inside (0, 1)
    /// (filter.Tracker.threshold).
    pub fn target_above(&self, room: &str) -> f64 {
        let c = self
            .target_thresholds
            .get(room)
            .copied()
            .unwrap_or(self.target_threshold);
        c.clamp(1e-6, 1.0 - 1e-6)
    }

    /// The threshold of p_enter for "wird betreten".
    pub fn entering_above(&self) -> f64 {
        self.approach_cost / (self.approach_cost + 1.0)
    }
}

/// What a person puts into a kind of cell of the LD2410C (MODEL.md 4.3, measured 6.10.): a r^-n
/// times the main lobe, a Gaussian of width sigma centred delta behind the person, and beyond the
/// centre the tail mixed in by `tail`, falling off over ell (m). Read as the model's list.
#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    pub a: f64,
    pub n: f64,
    pub delta: f64,
    pub sigma: f64,
    pub tail: f64,
    pub ell: f64,
}

impl Profile {
    pub const fn new([a, n, delta, sigma, tail, ell]: [f64; 6]) -> Self {
        Self {
            a,
            n,
            delta,
            sigma,
            tail,
            ell,
        }
    }
}

/// The bursts of the LD2410C's common gain: 1/u ~ Gamma(shape, rate shape x gain) with this weight.
#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Burst {
    pub shape: f64,
    pub gain: f64,
    pub weight: f64,
}

/// The slants (m) of the echo sources: from, to (with it), step.
#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Slants {
    pub from: f64,
    pub to: f64,
    pub step: f64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_model_that_cannot_be_built_is_refused_by_name() {
        for (json, name) in [
            (r#"{"turn_rate": 0}"#, "turn_rate"),
            (r#"{"stay_points": [2]}"#, "stay_points"),
            (r#"{"kappa_levels": 1}"#, "kappa_levels"),
            (r#"{"speed": "nan"}"#, "speed"),
        ] {
            let err = Params::from_json(json).expect("parsed").validate().expect_err(name);
            assert!(err.fields.contains(&name), "{err}");
        }
        Params::default().validate().expect("the defaults fit");
    }

    #[test]
    fn a_field_the_model_does_not_have_is_refused() {
        // a typing error ran silently with the default
        let err = Params::from_json(r#"{"ld_memroy": 3.0}"#).expect_err("unknown");
        assert!(err.to_string().contains("unknown field `ld_memroy`"), "{err}");
        assert!(Params::from_json(r#"{"ld_burst": {"shape": 4.0, "gain": 2.5, "weight": 0.02, "x": 1}}"#).is_err());
    }

    #[test]
    fn what_would_take_all_memory_or_divide_by_zero_is_refused_by_name() {
        for (json, name) in [
            // endless echo sources (echoes.rs) from one number
            (r#"{"ld_echo_ranges": [0.75, 1e308, 0.5]}"#, "ld_echo_ranges"),
            (r#"{"kappa_levels": 100000}"#, "kappa_levels"),
            (r#"{"ld_amp_levels": 100000}"#, "ld_amp_levels"),
            (r#"{"max_hyps": 100000}"#, "max_hyps"),
            (r#"{"ld_echo_stages": 100000}"#, "ld_echo_stages"),
            (
                r#"{"stay_points": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]}"#,
                "stay_points",
            ),
            (r#"{"find_radius": 0}"#, "find_radius"),
            (r#"{"res_cross": 0}"#, "res_cross"),
            (r#"{"tau": 0}"#, "tau"),
            (r#"{"pos_every": 0}"#, "pos_every"),
            (r#"{"white": "nan"}"#, "white"),
            (r#"{"leap": 0}"#, "leap"),
            (r#"{"gap_exact": -1}"#, "gap_exact"),
            (r#"{"ghost_types": [[1e-5, 0]]}"#, "ghost_types"),
            (
                r#"{"refind": {"coast": [0.13, 0, 0.03], "frozen": [0.2, 2.0, 0.02]}}"#,
                "refind",
            ),
            (r#"{"acquire": [[0.034, 0], [0.51, 4.55]]}"#, "acquire"),
            (r#"{"track_life": [0, 30]}"#, "track_life"),
            (r#"{"still_noise": 0}"#, "still_noise"),
            (r#"{"silent_after": -1}"#, "silent_after"),
        ] {
            let err = Params::from_json(json).expect(json).validate().expect_err(json);
            assert_eq!(err.fields, [name], "{json}");
        }
    }

    #[test]
    fn a_model_may_have_infinite_times() {
        let p = Params::from_json(r#"{"unseen_life": "inf", "white": 0.1}"#).expect("a model");
        assert_eq!(p.unseen_life, f64::INFINITY);
        assert_eq!(p.white, 0.1);
        assert_eq!(p.speed, Params::default().speed);
        assert!(Params::from_json(r#"{"white": "much"}"#).is_err());
    }

    #[test]
    fn numbers_that_make_no_model_are_refused() {
        assert!(Params::default().validate().is_ok());
        let p = Params::from_json(r#"{"ld_echo_ranges": [0.75, 6.5, 0.0], "ld_memory": 0.0}"#).expect("read");
        let err = p.validate().expect_err("no model").to_string();
        assert!(err.contains("ld_memory") && err.contains("ld_echo_ranges"), "{err}");
        assert!(
            Params::from_json(r#"{"ld_frame": "nan"}"#)
                .expect("read")
                .validate()
                .is_err()
        );
    }

    #[test]
    fn numbers_are_read_exactly() {
        // serde_json reads floats correctly rounded only with float_roundtrip: without it
        // 1 / 86400 came one ulp off and the traces of 8.10. depended on how the JSON was read
        let p = Params::from_json(r#"{"forget_rate": 1.1574074074074073e-05, "arrive_rate": 6.944444444444444e-05}"#)
            .expect("a model");
        assert_eq!(p.forget_rate, 1.0 / 86400.0);
        assert_eq!(p.arrive_rate, 1.0 / (4.0 * 3600.0));
    }
}
