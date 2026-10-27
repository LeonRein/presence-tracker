//! The numbers of the model (filtermodel.Model, MODEL.md) and the shapes derived from them
//! (filtermodel.Shapes). Measured ones and assumptions are explained in filtermodel.py; here only
//! what the core reads. A model is given as the fields that differ from these defaults.

use serde::{Deserialize, Deserializer};

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

/// s: the LD2450's frame period (measured; filtermodel.FRAME)
pub const FRAME: f64 = 0.089;
/// s: frames further apart were lost on the way (the idle heartbeat is 5 s, filtermodel.IDLE)
pub const IDLE: f64 = 6.0;
/// False alarm probability per detection cell of a CFAR radar (filtermodel.P_FA)
pub const P_FA: f64 = 1e-6;

/// Index of the standing and the walking component (filtermodel.STILL, WALK).
pub const STILL: usize = 0;
pub const WALK: usize = 1;

#[derive(Debug, Deserialize)]
#[serde(default)]
pub struct Params {
    // 3.1 standing
    #[serde(deserialize_with = "real")]
    pub still_noise: f64,
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
    // 5.5 a known person's record expires where no sensor could test it
    #[serde(deserialize_with = "real")]
    pub unseen_life: f64,
    // 5.1 the cut of the hypotheses
    pub max_hyps: usize,
    #[serde(deserialize_with = "real")]
    pub hyp_mass: f64,
    /// a known person who exists with less probability joins the unknown ones (filter.GIVE_UP)
    #[serde(deserialize_with = "real")]
    pub give_up: f64,
    /// s: motion is cut into parts no longer than this (filter.MAX_STEP)
    #[serde(deserialize_with = "real")]
    pub max_step: f64,
    // 4.3 the LD2410C: per mode [STILL, WALK] the profile (A, n, delta, sigma, tail, ell) of the
    // moving and the still cells, the beam (degrees: full, none), the Gamma shape and the
    // correlation time (moving, still), the common gain's prior (moving, still) and bursts (shape,
    // gain, weight)
    pub ld_moving: [[f64; 6]; 2],
    pub ld_still: [[f64; 6]; 2],
    pub ld_beam: [f64; 2],
    pub ld_shape: [f64; 2],
    pub ld_tau: [f64; 2],
    pub ld_gain: [f64; 2],
    pub ld_burst: [f64; 3],
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
    pub ld_echo_ranges: [f64; 3],
    pub ld_echo_amps: Vec<f64>,
    /// a tile holding less of a person standing is weighed at its mean amplitude (filter.LD_TILE_MASS)
    #[serde(deserialize_with = "real")]
    pub ld_tile_mass: f64,
    /// s: the end of a gap in the data is moved as always, what lies before in leaps of `leap` s,
    /// at most `max_leaps` of them (filter.GAP_EXACT, LEAP, MAX_LEAPS)
    #[serde(deserialize_with = "real")]
    pub gap_exact: f64,
    #[serde(deserialize_with = "real")]
    pub leap: f64,
    pub max_leaps: usize,
}

/// How a lost target is held (sensortracks.py): coasting on, or frozen where it was lost.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LostKind {
    Coast,
    Frozen,
}

/// Per kind of loss the time to finding the target again: median s, log-normal spread, the share
/// never found (filtermodel.Model.refind).
#[derive(Debug, Clone, Deserialize)]
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
            unseen_life: 120.0,
            max_hyps: 12,
            hyp_mass: 1e-7,
            give_up: 0.01,
            max_step: 0.2,
            ld_moving: [
                [63.0, 2.52, 0.41, 0.55, 0.27, 1.34],
                [159.4, 2.16, 0.41, 0.57, 0.47, 1.26],
            ],
            ld_still: [
                [574.0, 2.03, 0.19, 0.77, 0.40, 1.70],
                [949.0, 2.19, 0.58, 1.44, 1.00, 1.25],
            ],
            ld_beam: [50.0, 70.0],
            ld_shape: [2.5, 2.0],
            ld_tau: [4.0, 13.0],
            ld_gain: [16.0, 50.0],
            ld_burst: [4.0, 2.5, 0.02],
            ld_every: 1.0,
            ld_frame: 0.089,
            ld_memory: 2.0,
            ld_echo_life: 20.0,
            ld_echo_stages: 2,
            ld_echo_ranges: [0.75, 6.5, 0.5],
            ld_echo_amps: vec![0.3, 1.0, 3.0],
            ld_tile_mass: 1e-4,
            gap_exact: 120.0,
            leap: 15.0,
            max_leaps: 2000,
        }
    }
}

impl Params {
    /// The model from the fields that differ from the defaults, as JSON (non-finite numbers as
    /// "inf", "-inf", "nan").
    pub fn from_json(json: &str) -> serde_json::Result<Self> {
        serde_json::from_str(json)
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

/// How long a stay lasts and how well it is seen (filtermodel.Shapes): every stay draws its rate of
/// getting up go_l with weight go_w_l (L kinds), its detectability kappa_k with kappa_w_k (K
/// levels), and with it the LD2410C amplitude (A levels; ka_w the joint prior, K x A).
#[derive(Debug)]
pub struct Shapes {
    pub go: Vec<f64>,
    pub go_w: Vec<f64>,
    /// the kinds of the stays going on at a random moment (longer ones more likely)
    pub go_w_ongoing: Vec<f64>,
    pub kappa: Vec<f64>,
    pub kappa_w: Vec<f64>,
    /// the levels of the LD2410C amplitude g (A)
    pub amp: Vec<f64>,
    pub ka_w: Vec<f64>,
    /// P(level of the amplitude | detectability), K x A
    pub amp_given_kappa: Vec<f64>,
}

impl Shapes {
    pub fn kinds(&self) -> usize {
        self.go.len()
    }

    pub fn levels(&self) -> usize {
        self.kappa.len()
    }

    /// levels of the LD2410C amplitude (A)
    pub fn amps(&self) -> usize {
        self.ka_w.len() / self.kappa.len()
    }

    /// The prior of a stay's kind and detectability, L x K (independent a priori; Shapes.stay_prior).
    pub fn stay_prior(&self, ongoing: bool) -> Vec<f64> {
        let go_w = if ongoing { &self.go_w_ongoing } else { &self.go_w };
        go_w.iter()
            .flat_map(|&g| self.kappa_w.iter().map(move |&k| g * k))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_model_may_have_infinite_times() {
        let p = Params::from_json(r#"{"unseen_life": "inf", "white": 0.1}"#).expect("a model");
        assert_eq!(p.unseen_life, f64::INFINITY);
        assert_eq!(p.white, 0.1);
        assert_eq!(p.speed, Params::default().speed);
        assert!(Params::from_json(r#"{"white": "much"}"#).is_err());
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
