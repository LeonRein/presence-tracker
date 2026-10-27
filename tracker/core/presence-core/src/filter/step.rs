//! Time goes on (MODEL.md 3, 5): everybody is moved and weighed by the tracks not started meanwhile,
//! in parts at most max_step long (filter.Tracker._flush_parts, _sync_part).

use std::borrow::Cow;

use crate::filter::densities::{Densities, DensityId, DensityMut, Kind};
use crate::filter::mixture::{Mixture, MixtureId};
use crate::filter::pool::Pool;
use crate::motion::imm::{self, ImmStep};
use crate::motion::walk::move_densities;
use crate::params::{STILL, WALK};
use crate::sensors::{SensorSet, SensorState, live};
use crate::vmath::pairwise_sum;
use crate::world::World;

/// How well the best of a set of live sensors sees each tile, 0..1 (tiling.Tiling.observed, MODEL.md
/// 5.5), per set of sensors that occurs.
#[derive(Debug, Default)]
pub struct Observed {
    sets: Vec<(SensorSet, Vec<f64>)>,
}

impl Observed {
    pub fn insert(&mut self, sensors: SensorSet, per_tile: Vec<f64>) {
        match self.sets.iter_mut().find(|(s, _)| *s == sensors) {
            Some((_, v)) => *v = per_tile,
            None => self.sets.push((sensors, per_tile)),
        }
    }

    /// Panics if the set was not given.
    pub fn get(&self, sensors: SensorSet) -> &[f64] {
        &self
            .sets
            .iter()
            .find(|(s, _)| *s == sensors)
            .expect("observed tiles of a set of live sensors")
            .1
    }
}

/// The rates (1/s) per tile at which the live sensors start a track: on walkers (n), on still people
/// at detectability 1 (n).
#[derive(Debug)]
struct TileRateSums {
    walk: Vec<f64>,
    still: Vec<f64>,
}

/// Factors per tile for walkers (n) and still people per level of detectability (K x n).
#[derive(Debug)]
struct TileFactors {
    walk: Vec<f64>,
    still: Vec<f64>,
}

impl TileRateSums {
    /// The factors per tile for no track started in dt: exp(-rate dt), the still people's rate times
    /// their detectability kappa_k.
    fn no_track(&self, kappa: &[f64], dt: f64) -> TileFactors {
        TileFactors {
            walk: self.walk.iter().map(|rw| (-rw * dt).exp()).collect(),
            still: kappa
                .iter()
                .flat_map(|&k| self.still.iter().map(move |rs| (-(k * rs) * dt).exp()))
                .collect(),
        }
    }
}

/// The sensors' rates per tile given their live tracks (filter.Tracker._tile_rates), each computed
/// once per move of everybody: the tracks stay as they are meanwhile.
struct TileRates<'a> {
    world: &'a World,
    sensors: &'a [SensorState],
    rates: Vec<Option<Cow<'a, [Vec<f64>; 2]>>>,
}

impl<'a> TileRates<'a> {
    fn new(world: &'a World, sensors: &'a [SensorState]) -> Self {
        Self {
            world,
            sensors,
            rates: vec![None; sensors.len()],
        }
    }

    fn get(&mut self, si: usize) -> &[Vec<f64>; 2] {
        let (world, sensors) = (self.world, self.sensors);
        self.rates[si].get_or_insert_with(|| {
            let sight = &world.sights[si];
            let spots = &sensors[si].spots;
            if spots.is_empty() {
                return Cow::Borrowed(sight.base_tiles());
            }
            let at = world
                .rates
                .masked_rates(sight, &world.tiles.points, sight.base_points(), spots);
            Cow::Owned(at.each_ref().map(|r| world.tiles.average(r)))
        })
    }

    /// The summed rates per tile of the live sensors (filter.Tracker._watching); None if no sensor
    /// watches or there are no tiles.
    fn summed(&mut self, live: SensorSet) -> Option<TileRateSums> {
        let n = self.world.tiles.n;
        if live.is_empty() || n == 0 {
            return None;
        }
        let mut sums = TileRateSums {
            walk: vec![0.0; n],
            still: vec![0.0; n],
        };
        for si in live.iter() {
            let r = self.get(si);
            for (sum, x) in sums
                .walk
                .iter_mut()
                .zip(&r[WALK])
                .chain(sums.still.iter_mut().zip(&r[STILL]))
            {
                *sum += x;
            }
        }
        Some(sums)
    }
}

/// A Gaussian mixture moved with everybody: the sensors with a measuring track of the person (none
/// of theirs starts one on them).
#[derive(Debug, Clone, Copy)]
pub struct FlushMixture {
    pub id: MixtureId,
    pub measured: SensorSet,
}

/// The log factors of one round of a move, for the hypotheses holding each object: per density in
/// view of the move (in the order given), per mixture (None: it was not weighed, no sensor live).
#[derive(Debug, Default)]
pub struct RoundFactors {
    pub densities: Vec<f64>,
    pub mixtures: Vec<Option<f64>>,
}

/// One part dt of a mixture's own move (filter.Tracker._sync_part; its part gone through a door
/// already moved): the prediction, and if sensors watch, the weighing by no track started on the
/// person in dt by those without a measuring track of them (`users`) - its part gone through a door
/// with the factors per tile `away` if given, else not weighed. Returns the log factor if weighed.
fn sync_part(
    world: &World,
    mix: &mut Mixture,
    densities: &mut Densities,
    dt: f64,
    keep_away: bool,
    watch: Option<(&[SensorState], SensorSet, Option<&TileFactors>)>,
) -> Option<f64> {
    let walls = world.tiles.n > 0 && !mix.phantom;
    imm::predict(
        world,
        mix,
        densities,
        &ImmStep::new(&world.params, &world.shapes, dt),
        walls,
        keep_away,
    );
    let (sensors, users, away) = watch?;
    let rates = world
        .rates
        .mixture_rates(&world.sights, sensors, users, &mix.sigma(world, walls));
    let d_away = match (mix.away, away) {
        (Some(id), Some(f)) => densities.get_mut(id).weigh_person(&f.walk, &f.still),
        _ => 0.0,
    };
    Some(mix.weigh_no_track(&rates, &world.shapes.kappa, dt, d_away))
}

/// The record of a known person without a track lasts only as far as a measurement could test it
/// (filter.Tracker._expire, MODEL.md 5.5): the share of them no live sensor can see expires at
/// 1 / unseen_life - in a tile by 1 - how well the live sensors see it (`fade` per tile: exp(-dt /
/// unseen_life x that)), in the regions and out of the house whole - so r falls by r x that share.
fn expire(world: &World, d: &mut DensityMut, dt: f64, fade: &[f64]) {
    if d.kind != Kind::Person || d.r.is_nan() || *d.r <= 0.0 {
        return;
    }
    let q = -(-dt / world.params.unseen_life).exp_m1();
    let mut lost = *d.out * q;
    *d.out -= lost;
    let reg = pairwise_sum(d.region);
    if reg > 0.0 {
        for v in d.region.iter_mut() {
            *v *= 1.0 - q;
        }
        lost += reg * q;
    }
    if world.tiles.n > 0 {
        lost += d.take_in_view(fade);
    }
    if lost > 0.0 {
        *d.r *= 1.0 - lost;
        d.normalize();
    }
}

/// Moves everybody from t0 (the last move of everybody) to t (filter.Tracker._flush_parts): the
/// densities in `in_view` in parts of the move, each mixture in parts from its own time, round by
/// round - the parts gone through a door of the mixtures move with the densities (one
/// move_densities per round). The densities are weighed by no track started (the people nobody
/// knows of by its Poisson void probability), a known person's record expires where nobody could
/// test it; a mixture's part gone through a door is weighed with the factors of the move's last
/// part, scaled to its own part. Returns the log factors per round.
#[allow(clippy::too_many_arguments)]
pub fn flush_parts(
    world: &World,
    sensors: &[SensorState],
    observed: &Observed,
    densities: &mut Densities,
    mixtures: &mut Pool<Mixture, MixtureId>,
    in_view: &[DensityId],
    moving: &[FlushMixture],
    (t0, t): (f64, f64),
) -> Vec<RoundFactors> {
    let max_step = world.params.max_step;
    let count = |span: f64| (span / max_step - 1e-9).ceil() as usize;
    let parts = count(t - t0);
    let dt = (t - t0) / parts as f64;
    let mut rates = TileRates::new(world, sensors);
    let kappa = &world.shapes.kappa;
    let watch: Vec<(SensorSet, Option<TileRateSums>)> = (0..parts)
        .map(|k| {
            let at = live(sensors, t0 + (t - t0) * (k + 1) as f64 / parts as f64);
            (at, rates.summed(at))
        })
        .collect();
    // a mixture's part gone through a door is weighed by the rates of the move's last part over its
    // own part (the Python filter took the last part's factors to the power of the share: the same
    // up to rounding), per length of part
    let last = watch.last().and_then(|(_, r)| r.as_ref());
    let mut away_factors: Vec<(f64, TileFactors)> = Vec::new();
    // per mixture: its own time, number of parts and their length
    let own: Vec<(f64, usize, f64)> = moving
        .iter()
        .map(|m| {
            let g0 = mixtures.get(m.id).t.unwrap_or(t0);
            let n = if t > g0 { count(t - g0) } else { 0 };
            (g0, n, if n > 0 { (t - g0) / n as f64 } else { 0.0 })
        })
        .collect();
    let rounds = own.iter().map(|&(_, n, _)| n).max().unwrap_or(0).max(parts);
    let mut out = Vec::with_capacity(rounds);
    for j in 0..rounds {
        let mut movers: Vec<(DensityId, f64)> = if j < parts {
            in_view.iter().map(|&id| (id, dt)).collect()
        } else {
            Vec::new()
        };
        for (m, &(_, n, g_dt)) in moving.iter().zip(&own) {
            if let Some(away) = mixtures.get(m.id).away.filter(|_| j < n) {
                movers.push((away, g_dt));
            }
        }
        move_densities(world, densities, &movers);
        let mut round = RoundFactors::default();
        if j < parts {
            let (at, sums) = &watch[j];
            let factors = sums.as_ref().map(|r| r.no_track(kappa, dt));
            let observed = observed.get(*at);
            let fade: Vec<f64> = observed
                .iter()
                .map(|o| (-dt / world.params.unseen_life * (1.0 - o)).exp())
                .collect();
            for &id in in_view {
                let mut d = densities.get_mut(id);
                expire(world, &mut d, dt, &fade);
                round
                    .densities
                    .push(factors.as_ref().map_or(0.0, |f| d.weigh(&f.walk, &f.still)));
            }
        }
        for (m, &(g0, n, g_dt)) in moving.iter().zip(&own) {
            if j >= n {
                round.mixtures.push(None);
                continue;
            }
            let mix = mixtures.get_mut(m.id);
            let at = if mix.phantom {
                SensorSet::default()
            } else {
                live(sensors, g0 + g_dt * (j + 1) as f64)
            };
            let away = match last {
                Some(r) if !at.is_empty() => {
                    if !away_factors.iter().any(|(len, _)| *len == g_dt) {
                        away_factors.push((g_dt, r.no_track(kappa, g_dt)));
                    }
                    away_factors.iter().find(|(len, _)| *len == g_dt).map(|(_, f)| f)
                }
                _ => None,
            };
            let watch = (!at.is_empty()).then_some((sensors, at.without(m.measured), away));
            round.mixtures.push(sync_part(world, mix, densities, g_dt, true, watch));
        }
        out.push(round);
    }
    for (m, &(_, n, _)) in moving.iter().zip(&own) {
        if n > 0 {
            mixtures.get_mut(m.id).t = Some(t);
        }
    }
    out
}
