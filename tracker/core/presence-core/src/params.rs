//! The numbers of the model (filtermodel.Model, MODEL.md) and the shapes derived from them
//! (filtermodel.Shapes). Measured ones and assumptions are explained in filtermodel.py; here only
//! what the core reads. A model is given as the fields that differ from these defaults.

use serde::Deserialize;

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
    pub still_noise: f64,
    pub kappa_switch: f64,
    // 3.2 walking
    pub speed: f64,
    pub speed_spread: f64,
    pub turn_rate: f64,
    pub walk_length: f64,
    // 3.3, 3.4 coming home, newcomers, forgetting
    pub arrive_rate: f64,
    pub guest_rate: f64,
    pub forget_rate: f64,
    // 4.1 the sensor's tracks: rho and r50 [STILL, WALK]
    pub acquire: [[f64; 2]; 2],
    pub find_radius: f64,
    pub res_range: f64,
    pub res_cross: f64,
    pub const_share: f64,
    pub tau: f64,
    // 5.5 a known person's record expires where no sensor could test it
    pub unseen_life: f64,
    /// s: motion is cut into parts no longer than this (filter.MAX_STEP)
    pub max_step: f64,
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
            unseen_life: 120.0,
            max_step: 0.2,
        }
    }
}

impl Params {
    /// The model from the fields that differ from the defaults, as JSON.
    pub fn from_json(json: &str) -> serde_json::Result<Self> {
        serde_json::from_str(json)
    }

    /// 1/s at which walkers stop: the mean walk is walk_length long (MODEL.md 3.2).
    pub fn stop_rate(&self) -> f64 {
        self.speed / self.walk_length
    }
}

/// How long a stay lasts and how well it is seen (filtermodel.Shapes): every stay draws its rate of
/// getting up go_l with weight go_w_l (L kinds), its detectability kappa_k with kappa_w_k (K
/// levels), and with it the LD2410C amplitude (A levels; ka_w the joint prior, K x A).
#[derive(Debug)]
pub struct Shapes {
    pub go: Vec<f64>,
    pub go_w: Vec<f64>,
    pub kappa: Vec<f64>,
    pub kappa_w: Vec<f64>,
    pub ka_w: Vec<f64>,
}

impl Shapes {
    pub fn kinds(&self) -> usize {
        self.go.len()
    }

    pub fn levels(&self) -> usize {
        self.kappa.len()
    }

    pub fn amp_levels(&self) -> usize {
        self.ka_w.len() / self.kappa.len()
    }
}
