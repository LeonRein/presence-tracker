//! What the filter tells (MODEL.md 6; the outputs of filter.Tracker): per observed room and per place
//! the distribution of the number of people, occupied (the filter's probability, or the room's own
//! LD2450), "wird betreten", and the people of the most probable hypothesis for the display. They only
//! read the filter. The sums run in numpy's order (vmath), the numbers are those of the Python filter;
//! nothing is rounded for the display here (the shell does).

use crate::filter::Filter;
use crate::filter::densities::{DensityRef, DensityShape};
use crate::filter::holders::Obj;
use crate::filter::mixture::{Mixture, max2};
use crate::filter::objects::Objects;
use crate::geometry::Point;
use crate::motion::imm::walk_ahead;
use crate::params::WALK;
use crate::vmath::{log_factorials, pairwise_sum};
use crate::world::World;

/// "wird betreten": a person walking with less probability can raise no zone's p_enter by more than
/// this; not moved on (filter.ENTER_MIN_WALK)
pub const ENTER_MIN_WALK: f64 = 0.02;

/// Distributions of the number of people, one per column of what was counted: [P(0), P(1), ...],
/// all as long as the longest.
pub type Distributions = Vec<Vec<f64>>;

/// The outputs computed once per state of everybody (filter.Tracker._cached): current while the
/// filter's epoch stays - it only counts up, so what was computed at another epoch never passes for
/// current.
#[derive(Debug, Default)]
pub struct OutputCache {
    epoch: Option<u64>,
    pub counts: Option<Distributions>,
    pub places: Option<Distributions>,
    pub entering: Option<Vec<Entering>>,
}

impl OutputCache {
    /// The cache at this epoch: what was computed at another is forgotten.
    pub fn at(&mut self, epoch: u64) -> &mut Self {
        if self.epoch != Some(epoch) {
            *self = Self {
                epoch: Some(epoch),
                ..Self::default()
            };
        }
        self
    }
}

// ------------------------------------------------------------------ per object

/// Expected number per tile in view of a density: r x (walking + standing over all kinds and levels,
/// summed in numpy's order) (hidden.Hidden.in_view).
fn density_in_view(d: &DensityRef, shape: DensityShape) -> Vec<f64> {
    let n = shape.tiles;
    if n == 0 {
        return Vec::new();
    }
    let mut stays = d.still[..n].to_vec();
    for row in d.still.chunks_exact(n).skip(1) {
        for (s, &x) in stays.iter_mut().zip(row) {
            *s += x;
        }
    }
    d.walk.iter().zip(&stays).map(|(&w, &s)| d.r * (w + s)).collect()
}

/// Per tile the probability of a mixture's Gaussians, mixed over the modes (gauss.Gauss.tile_mass).
fn tile_mass(world: &World, mix: &Mixture) -> Vec<f64> {
    let mut out = vec![0.0; world.tiles.n];
    for (mode, &w) in mix.modes.iter().zip(&mix.weights()) {
        if w > 0.0 {
            for (o, g) in out.iter_mut().zip(world.gauss_mass(mode.pos(), mode.pos_var())) {
                *o += w * g;
            }
        }
    }
    out
}

/// Expected number per tile in view of an object (Tracker._in_view): a mixture its Gaussians' tiles
/// and its part gone through a door.
pub fn in_view(world: &World, objects: &Objects, obj: Obj) -> Vec<f64> {
    let densities = objects.densities();
    match obj {
        Obj::Density(d) => density_in_view(&densities.get(d), densities.shape()),
        Obj::Mixture(m) => {
            let mix = objects.mixtures().get(m);
            let keep = 1.0 - mix.a;
            let mut out: Vec<f64> = tile_mass(world, mix).iter().map(|&x| keep * x).collect();
            if let Some(away) = mix.away {
                for (o, x) in out
                    .iter_mut()
                    .zip(density_in_view(&densities.get(away), densities.shape()))
                {
                    *o += mix.a * x;
                }
            }
            out
        }
    }
}

/// P(the person is in each observed room), and in view at all (the last) (Tracker._room_probs).
fn room_probs(world: &World, mass: &[f64]) -> Vec<f64> {
    let rooms = world.plan.rooms.len();
    let mut out = vec![0.0; rooms + 1];
    for (&m, room) in mass.iter().zip(&world.plan.tile_room) {
        if let Some(k) = *room {
            out[k] += m;
        }
    }
    out[rooms] = pairwise_sum(mass);
    out
}

/// Probability per place of a density (observed, regions..., outside); 1 - r: nowhere
/// (hidden.Hidden.places).
fn density_places(d: &DensityRef, shape: DensityShape) -> Vec<f64> {
    let mut out = Vec::with_capacity(shape.regions + 2);
    out.push(d.r * (pairwise_sum(d.walk) + pairwise_sum(d.still)));
    out.extend(
        d.region
            .chunks(shape.ages.max(1))
            .map(|stays| d.r * pairwise_sum(stays)),
    );
    out.push(d.r * d.out);
    out
}

/// Probability per place of an object (Tracker._place_probs): a mixture is in view but for its part
/// gone through a door.
fn place_probs(objects: &Objects, obj: Obj) -> Vec<f64> {
    let densities = objects.densities();
    let shape = densities.shape();
    match obj {
        Obj::Density(d) => density_places(&densities.get(d), shape),
        Obj::Mixture(m) => {
            let mix = objects.mixtures().get(m);
            let mut out = vec![0.0; shape.regions + 2];
            out[0] = 1.0 - mix.a;
            if let Some(away) = mix.away {
                for (o, p) in out.iter_mut().zip(density_places(&densities.get(away), shape)) {
                    *o += mix.a * p;
                }
            }
            out
        }
    }
}

// ------------------------------------------------------------------ counts

/// The number of people per column: one Bernoulli per known person (`known`, a row of probabilities
/// each), plus a Poisson number of unknown ones (`means`), cut where the rest is below 1e-9
/// (kernels.poisson_binomial; `log_k` holds log k! far enough). A column's entries beyond its cut 0.
fn poisson_binomial(known: &[&[f64]], means: &[f64], log_k: &[f64]) -> Distributions {
    let people = known.len();
    let mut dist: Distributions = means
        .iter()
        .map(|_| {
            let mut d = vec![0.0; people + 1];
            d[0] = 1.0;
            d
        })
        .collect();
    for (p, q) in known.iter().enumerate() {
        for (d, &q) in dist.iter_mut().zip(*q) {
            let mut prev = 0.0;
            for x in &mut d[..p + 2] {
                let cur = *x;
                *x = cur * (1.0 - q) + prev * q;
                prev = cur;
            }
        }
    }
    let width = means
        .iter()
        .filter(|&&m| m > 0.0)
        .map(|&m| poisson_cut(m))
        .max()
        .unwrap_or(0);
    if width == 0 {
        return dist;
    }
    dist.into_iter()
        .zip(means)
        .map(|(d, &mean)| {
            let mut out = vec![0.0; people + width];
            if mean <= 0.0 {
                out[..=people].copy_from_slice(&d);
                return out;
            }
            let lm = mean.ln();
            let pois: Vec<f64> = (0..poisson_cut(mean))
                .map(|k| (k as f64 * lm - mean - log_k[k]).exp())
                .collect();
            for (i, &di) in d.iter().enumerate() {
                for (o, &pk) in out[i..].iter_mut().zip(&pois) {
                    *o += di * pk;
                }
            }
            out
        })
        .collect()
}

/// Where a Poisson number with this mean is cut: its rest beyond is below 1e-9.
fn poisson_cut(mean: f64) -> usize {
    (mean + 10.0 * mean.max(0.0).sqrt() + 6.0) as usize
}

/// The distributions of the number of people per column of per_object - for a known person the
/// probabilities, for the unknown ones the expected numbers - mixed over the hypotheses
/// (Tracker._counts). Each object once, in the order of the holders.
fn counts(filter: &Filter, per_object: impl Fn(Obj) -> Vec<f64>) -> Distributions {
    let holders = filter.holders();
    let mut values: Vec<Vec<f64>> = vec![Vec::new(); holders.len()];
    for (e, obj) in holders.entries(false) {
        values[e] = per_object(obj);
    }
    let unknown = |hy: &crate::filter::hyp::Hyp| &values[holders.entry(Obj::Density(hy.unknown))];
    let top = filter
        .hyps()
        .iter()
        .flat_map(|hy| unknown(hy).iter().copied())
        .fold(0.0, f64::max);
    let log_k = log_factorials(poisson_cut(top).max(64));
    let dists: Vec<Distributions> = filter
        .hyps()
        .iter()
        .map(|hy| {
            let people = hy.people.values().map(|&m| Obj::Mixture(m));
            let known: Vec<&[f64]> = people
                .chain(hy.hidden.iter().map(|&d| Obj::Density(d)))
                .map(|obj| values[holders.entry(obj)].as_slice())
                .collect();
            poisson_binomial(&known, unknown(hy), &log_k)
        })
        .collect();
    let columns = dists.first().map_or(0, Vec::len);
    let width = dists.iter().flatten().map(Vec::len).max().unwrap_or(0);
    let mut out = vec![vec![0.0; width]; columns];
    for (&w, dist) in filter.hyp_weights().iter().zip(&dists) {
        for (row, d) in out.iter_mut().zip(dist) {
            for (o, &x) in row.iter_mut().zip(d) {
                *o += w * x;
            }
        }
    }
    out
}

/// Per observed room the distribution of the number of people in it, then of those in view at all
/// (Tracker.count_distribution).
pub fn count_distribution(filter: &Filter, world: &World) -> Distributions {
    let objects = filter.objects();
    counts(filter, |obj| room_probs(world, &in_view(world, objects, obj)))
}

/// Per place (observed, regions..., outside) the distribution of the number of people there, then of
/// those anywhere but outside (Tracker.place_distribution).
pub fn place_distribution(filter: &Filter) -> Distributions {
    let objects = filter.objects();
    counts(filter, |obj| {
        let mut p = place_probs(objects, obj);
        let house = pairwise_sum(&p[..p.len() - 1]);
        p.push(house);
        p
    })
}

/// What decided that an observed room is occupied (MODEL.md 6 "Belegt").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Filter,
    Ld2450,
    Both,
}

/// Per observed room whether it is occupied and by what (Tracker.occupancy): P(somebody there) above
/// c = light_cost / (light_cost + 1), the threshold with the least expected cost of the light (a second
/// of light without anybody costs light_cost seconds dark), or its own LD2450 measured somebody in it
/// lately (`seen`).
pub fn occupancy(counts: &Distributions, seen: &[bool], light_cost: f64) -> Vec<Option<Source>> {
    let c = light_cost / (light_cost + 1.0);
    counts
        .iter()
        .zip(seen)
        .map(|(dist, &s)| match (1.0 - dist[0] > c, s) {
            (true, true) => Some(Source::Both),
            (true, false) => Some(Source::Filter),
            (false, true) => Some(Source::Ld2450),
            (false, false) => None,
        })
        .collect()
}

// ------------------------------------------------------------------ the display

/// numpy's argmax: the first largest, or the first NaN.
pub(crate) fn argmax(values: &[f64]) -> usize {
    let mut best = 0;
    for (i, &v) in values.iter().enumerate() {
        if v.is_nan() {
            return i;
        }
        if v > values[best] {
            best = i;
        }
    }
    best
}

/// The index of the most probable hypothesis (the first of the heaviest).
pub fn most_probable(filter: &Filter) -> usize {
    argmax(&filter.hyps().iter().map(|h| h.logw).collect::<Vec<_>>())
}

/// The most probable hypothesis' known people that more probably exist than not (Garcia-Fernandez et
/// al. 2018, sec. VI): their number in the display (the place among its people, from 1, those with a
/// track first) and the object.
fn shown_people(filter: &Filter) -> Vec<(usize, Obj)> {
    let Some(hy) = filter.hyps().get(most_probable(filter)) else {
        return Vec::new();
    };
    let densities = filter.objects().densities();
    let people = hy.people.values().map(|&m| Obj::Mixture(m));
    people
        .chain(hy.hidden.iter().map(|&d| Obj::Density(d)))
        .enumerate()
        .filter(|&(_, obj)| match obj {
            Obj::Density(d) => densities.get(d).r >= 0.5,
            Obj::Mixture(_) => true,
        })
        .map(|(k, obj)| (k + 1, obj))
        .collect()
}

/// A known person for the display (Tracker._display), not rounded.
#[derive(Debug, Clone, PartialEq)]
pub struct Person {
    pub id: usize,
    /// probability per place (observed, regions..., outside)
    pub places: Vec<f64>,
    /// without a measuring track (a density over the tiles)
    pub lost: bool,
    pub r: f64,
    /// where to draw them, if they are most probably in view
    pub shown: Option<Shown>,
}

/// Where to draw a person: with a track the mean of the mixture (it moves as smoothly as the estimate
/// does); without, the densest tile of the most probable place.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Shown {
    pub at: Point,
    pub velocity: Point,
    pub sigma: f64,
    pub walk: f64,
}

/// The known people of the most probable hypothesis that more probably exist than not, for the
/// display: those with a track first (Tracker.persons).
pub fn persons(filter: &Filter, world: &World) -> Vec<Person> {
    let objects = filter.objects();
    shown_people(filter)
        .into_iter()
        .map(|(id, obj)| {
            let places = place_probs(objects, obj);
            let in_view = argmax(&places) == 0;
            let (lost, r, shown) = match obj {
                Obj::Density(d) => {
                    let d = objects.densities().get(d);
                    let shown = in_view.then(|| densest(world, &d, objects.densities().shape()));
                    (true, d.r, shown)
                }
                Obj::Mixture(m) => (false, 1.0, in_view.then(|| mean_of(objects.mixtures().get(m)))),
            };
            Person {
                id,
                places,
                lost,
                r,
                shown,
            }
        })
        .collect()
}

/// A person without a track at the centre of their densest tile (by mass per m^2), their spread around
/// it and the share of them walking.
fn densest(world: &World, d: &DensityRef, shape: DensityShape) -> Shown {
    let tiles = &world.tiles;
    let mass = density_in_view(d, shape);
    let per_area: Vec<f64> = mass.iter().zip(&tiles.area).map(|(m, a)| m / a).collect();
    let at = tiles.centers[argmax(&per_area)];
    let spread = mass
        .iter()
        .zip(tiles.centers.iter().zip(&tiles.var))
        .fold(0.0, |s, (&m, (c, v))| {
            let (dx, dy) = (c[0] - at[0], c[1] - at[1]);
            s + m * ((dx * dx + dy * dy) + (v[0] + v[1]))
        });
    let total = max2(pairwise_sum(&mass), 1e-12);
    let walking: Vec<f64> = d.walk.iter().map(|&w| d.r * w).collect();
    Shown {
        at,
        velocity: [0.0, 0.0],
        sigma: (spread / total).sqrt(),
        walk: pairwise_sum(&walking) / total,
    }
}

/// A person with a track at the mean of their mixture, its velocity and spread.
fn mean_of(mix: &Mixture) -> Shown {
    let w = mix.weights();
    let mix2 = |f: &dyn Fn(usize) -> f64| w[0] * f(0) + w[1] * f(1);
    let pos = |k: usize| mix.modes[k].pos();
    let at = [mix2(&|k| pos(k)[0]), mix2(&|k| pos(k)[1])];
    let d = mix.modes[0].dim();
    let velocity = [mix2(&|k| mix.modes[k].mean[1]), mix2(&|k| mix.modes[k].mean[d + 1])];
    let spread = mix2(&|k| {
        let (p, v) = (pos(k), mix.modes[k].pos_var());
        let (dx, dy) = (p[0] - at[0], p[1] - at[1]);
        (dx * dx + dy * dy) + (v[0] + v[1])
    });
    Shown {
        at,
        velocity,
        sigma: spread.sqrt(),
        walk: w[WALK],
    }
}

/// Where a person of the display may be, for the map (Tracker._cloud), not rounded.
#[derive(Debug, Clone, PartialEq)]
pub struct Cloud {
    pub id: usize,
    /// with a track: their Gaussians holding at least 1 %, [weight, x, y, sd x, sd y]
    pub gauss: Vec<[f64; 5]>,
    /// without (and their part gone through a door): (tile, mass per m^2) of the tiles holding at
    /// least 0.2 %
    pub tiles: Vec<(usize, f64)>,
}

/// The clouds of the people of the display (persons()).
pub fn clouds(filter: &Filter, world: &World) -> Vec<Cloud> {
    let objects = filter.objects();
    let densities = objects.densities();
    shown_people(filter)
        .into_iter()
        .map(|(id, obj)| {
            let (gauss, mass) = match obj {
                Obj::Density(d) => (Vec::new(), density_in_view(&densities.get(d), densities.shape())),
                Obj::Mixture(m) => {
                    let mix = objects.mixtures().get(m);
                    let gauss = mix
                        .modes
                        .iter()
                        .zip(mix.weights())
                        .map(|(mode, w)| ((1.0 - mix.a) * w, mode.pos(), mode.pos_var()))
                        .filter(|&(w, _, _)| w >= 0.01)
                        .map(|(w, p, v)| [w, p[0], p[1], v[0].sqrt(), v[1].sqrt()])
                        .collect();
                    let mass = match mix.away {
                        Some(away) => density_in_view(&densities.get(away), densities.shape())
                            .iter()
                            .map(|&x| mix.a * x)
                            .collect(),
                        None => vec![0.0; world.tiles.n],
                    };
                    (gauss, mass)
                }
            };
            let tiles = mass
                .iter()
                .zip(&world.tiles.area)
                .enumerate()
                .filter(|&(_, (&m, _))| m > 0.002)
                .map(|(c, (m, a))| (c, m / a))
                .collect();
            Cloud { id, gauss, tiles }
        })
        .collect()
}

// ------------------------------------------------------------------ "wird betreten"

/// "wird betreten" of a zone (Tracker.entering): the probability that somebody walking with a track
/// enters it within the look-ahead, and for the one who most probably does (if it is at least 1 %)
/// the way along their velocity to it (None: a wall, or farther than 5 m), the time at their speed
/// and their number in the display (None: not in the most probable hypothesis).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Entering {
    pub p: f64,
    pub distance: Option<f64>,
    pub eta: Option<f64>,
    pub person: Option<usize>,
}

/// A walker with a track and how much they may rise each zone's mass.
struct Rise {
    entry: usize,
    rise: Vec<f64>,
    at: Point,
    velocity: Point,
}

/// Per zone of plan.targets (the observed rooms, then the areas) "wird betreten" for the look-ahead h
/// (s; Tracker._entering, MODEL.md 6). Per person with a track (not a ghost's source) only
/// their walking component moves on (standing people sway by centimetres; getting up goes anywhere):
/// where it is h/2 and h s ahead - unless the walk ends first (rate speed / walk length, 3.2): then
/// where it stopped, the time of stopping on the nodes 0, h/2, h -, against where it is now. Their
/// rise in each zone, max(P(in it at h/2), P(in it at h)) - P(in it now), at least 0, is the
/// probability that they enter it within h (a lower bound of being in it at some time; whoever is in
/// it stays). People of a hypothesis are independent: P(somebody enters) = 1 - prod(1 - rise), mixed
/// over the hypotheses (5.1). Deterministic, no samples.
pub fn entering(filter: &Filter, world: &World, h: f64) -> Vec<Entering> {
    let targets = &world.plan.targets;
    let none = Entering {
        p: 0.0,
        distance: None,
        eta: None,
        person: None,
    };
    let mut out = vec![none; targets.len()];
    if targets.is_empty() || h <= 0.0 {
        return out;
    }
    let mass = |at: Point, var: Point| match world.gauss_points(at, var) {
        Some(d) => targets.sums(&d),
        None => vec![0.0; targets.len()],
    };
    let mu = world.params.stop_rate();
    // who stops stays where they stopped: the time of stopping on the three nodes (0, h/2, h), each
    // taking the stops nearest to it
    let (q1, q3) = ((-mu * h * 0.25).exp(), (-mu * h * 0.75).exp());
    let objects = filter.objects();
    let mut rises: Vec<Rise> = Vec::new();
    for (entry, obj) in filter.holders().entries(false) {
        let Obj::Mixture(m) = obj else { continue };
        let mix = objects.mixtures().get(m);
        let w = mix.weights()[WALK] * (1.0 - mix.a);
        if mix.phantom || w < ENTER_MIN_WALK {
            continue;
        }
        let walking = &mix.modes[WALK];
        let d = walking.dim();
        let (at, velocity) = (walking.pos(), [walking.mean[1], walking.mean[d + 1]]);
        let now = mass(at, walking.pos_var());
        let (half_at, half_var) = walk_ahead(world, walking, 0.5 * h);
        let half = mass(half_at, half_var);
        let (end_at, end_var) = walk_ahead(world, walking, h);
        let end = mass(end_at, end_var);
        let rise: Vec<f64> = (0..targets.len())
            .map(|z| {
                let at_half = (1.0 - q1) * now[z] + q1 * half[z];
                let at_end = (1.0 - q1) * now[z] + (q1 - q3) * half[z] + q3 * end[z];
                w * maximum(maximum(at_half, at_end) - now[z], 0.0)
            })
            .collect();
        if crate::vmath::nan_max(&rise) > 0.0 {
            rises.push(Rise {
                entry,
                rise,
                at,
                velocity,
            });
        }
    }
    if rises.is_empty() {
        return out;
    }
    let holders = filter.holders();
    let mut p = vec![0.0; targets.len()];
    let mut share = vec![vec![0.0; targets.len()]; rises.len()];
    for (&wh, hy) in filter.hyp_weights().iter().zip(filter.hyps()) {
        let mut empty = vec![1.0; targets.len()];
        for &m in hy.people.values() {
            let entry = holders.entry(Obj::Mixture(m));
            let Some(k) = rises.iter().position(|r| r.entry == entry) else {
                continue;
            };
            for ((e, s), &r) in empty.iter_mut().zip(&mut share[k]).zip(&rises[k].rise) {
                *e *= 1.0 - r;
                *s += wh * r;
            }
        }
        for (pz, e) in p.iter_mut().zip(&empty) {
            *pz += wh * (1.0 - e);
        }
    }
    let shown = shown_people(filter);
    for (z, (o, &pz)) in out.iter_mut().zip(&p).enumerate() {
        o.p = pz.clamp(0.0, 1.0);
        if pz < 0.01 {
            continue;
        }
        // the first of the most probable
        let mut best = 0;
        for (k, s) in share.iter().enumerate() {
            if s[z] > share[best][z] {
                best = k;
            }
        }
        let rise = &rises[best];
        let speed = rise.velocity[0].hypot(rise.velocity[1]);
        o.distance = ray_distance(world, z, rise.at, rise.velocity);
        o.eta = o.distance.filter(|_| speed > 0.05).map(|dist| dist / speed);
        let obj = holders.objects()[rise.entry];
        o.person = shown.iter().find(|&&(_, s)| s == obj).map(|&(id, _)| id);
    }
    out
}

/// numpy's maximum: NaN if either is.
fn maximum(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() { f64::NAN } else { a.max(b) }
}

/// How far a walker at `at` walks along their velocity until they are in zone z (its cells), if no
/// wall is in the way and within 5 m, in steps of 5 cm (Tracker._ray_distance).
fn ray_distance(world: &World, z: usize, at: Point, velocity: Point) -> Option<f64> {
    const REACH: f64 = 5.0;
    const STEP: f64 = 0.05;
    let speed = velocity[0].hypot(velocity[1]);
    if speed < 1e-6 {
        return None;
    }
    let unit = [velocity[0] / speed, velocity[1] / speed];
    // numpy's arange(0, REACH + STEP / 2, STEP)
    let steps = ((REACH + STEP / 2.0) / STEP).ceil() as usize;
    (0..steps)
        .map(|i| i as f64 * STEP)
        .map(|s| (s, [at[0] + s * unit[0], at[1] + s * unit[1]]))
        .find(|&(_, p)| world.plan.targets.holds(z, world.raster.cell_of(p)))
        .filter(|&(s, p)| s == 0.0 || !world.walls.crosses(&[at], &[p])[0])
        .map(|(s, _)| s)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::Start;
    use crate::filter::hyp::{GroupId, Hyp};
    use crate::filter::mixture::Kalman;
    use crate::filter::small_map::SmallMap;
    use crate::testing;
    use crate::tracker::Tracker;

    fn close(a: &[f64], b: &[f64], tol: f64) -> bool {
        a.len() == b.len() && a.iter().zip(b).all(|(x, y)| (x - y).abs() <= tol)
    }

    #[test]
    fn the_number_of_people_is_the_known_ones_bernoullis_and_a_poisson_number_of_the_unknown() {
        let log_k = log_factorials(64);
        // two known people (0.5, 0.2), nobody unknown: exactly
        let got = poisson_binomial(&[&[0.5], &[0.2]], &[0.0], &log_k);
        assert!(close(&got[0], &[0.4, 0.5, 0.1], 1e-15), "{got:?}");
        // nobody known, 1.5 unknown expected: the Poisson numbers, cut where the rest is below 1e-9
        let got = poisson_binomial(&[], &[1.5], &log_k);
        let pois: Vec<f64> = (0..got[0].len())
            .map(|k| (-1.5f64).exp() * 1.5f64.powi(k as i32) / (1..=k).product::<usize>() as f64)
            .collect();
        assert_eq!(got[0].len(), poisson_cut(1.5));
        assert!(close(&got[0], &pois, 1e-15) && 1.0 - got[0].iter().sum::<f64>() < 1e-9);
        // both: the convolution, per column on its own
        let got = poisson_binomial(&[&[1.0, 0.0]], &[1.5, 0.0], &log_k);
        assert!(close(&got[0][1..], &pois, 1e-15) && got[0][0] == 0.0);
        assert!(close(&got[1][..2], &[1.0, 0.0], 0.0) && got[1][2..].iter().all(|&x| x == 0.0));
    }

    #[test]
    fn a_person_at_a_place_is_counted_there() {
        let mut tr = Tracker::new(testing::flat());
        let outside = tr.world().tiles.regions + 1;
        tr.reset_people(0.0, Some(&[Start::Place(0), Start::Place(outside)]));
        // the room wohn and in view: one person
        let counts = tr.count_distribution().clone();
        assert!(
            counts.iter().all(|row| close(&row[..3], &[0.0, 1.0, 0.0], 1e-12)),
            "{counts:?}"
        );
        // observed one, outside one, the house one
        let places = tr.place_distribution().clone();
        assert!(close(&places[0][..3], &[0.0, 1.0, 0.0], 1e-12));
        assert!(close(&places[1][..3], &[0.0, 1.0, 0.0], 1e-12));
        assert!(close(&places[2][..3], &[0.0, 1.0, 0.0], 1e-12));
        // for the display only the one in view has a place to be drawn at; the other is shown nowhere
        let shown = tr.persons();
        assert_eq!(
            shown.iter().map(|p| (p.id, p.lost)).collect::<Vec<_>>(),
            [(1, true), (2, true)]
        );
        assert!(shown[0].shown.is_some() && shown[1].shown.is_none());
    }

    #[test]
    fn the_outputs_are_computed_once_per_epoch_and_never_pass_for_another() {
        let mut tr = Tracker::new(testing::flat());
        tr.reset_people(0.0, Some(&[Start::Place(0)]));
        let before = tr.count_distribution().clone();
        let epoch = tr.filter().epoch();
        // the person goes out without a new epoch (as a weighing does between moves): the outputs
        // stay as they were computed
        let outside = tr.world().tiles.regions + 1;
        let world = testing::flat();
        let f = tr.filter_mut();
        f.start_over(&world, Some(&[Start::Place(outside)]));
        assert_eq!(f.epoch(), epoch);
        assert_eq!(tr.count_distribution(), &before);
        // a new epoch: computed anew
        tr.event(0.0);
        assert!(tr.count_distribution()[0][0] > 0.99);
        // starting over is a new epoch, and the counter never goes back
        let at = tr.filter().epoch();
        tr.reset_people(0.0, Some(&[Start::Place(0)]));
        assert!(tr.filter().epoch() > at);
        assert_eq!(tr.count_distribution(), &before);
    }

    /// A person with a track walking at `at` with velocity v (the filter holds them in one
    /// hypothesis, with the unknown ones none).
    fn walking(at: Point, v: Point) -> Tracker {
        let mut tr = Tracker::new(testing::flat());
        tr.reset_people(0.0, Some(&[]));
        let s = testing::shapes();
        let mut modes = [Kalman::zeros(2), Kalman::zeros(2)];
        for (k, g) in modes.iter_mut().enumerate() {
            for ax in 0..2 {
                let (mean, cov) = g.axis_mut(ax);
                (mean[0], cov[0]) = (at[ax], 0.01);
                if k == WALK {
                    (mean[1], cov[3]) = (v[ax], 0.05);
                }
            }
        }
        let mix = Mixture {
            logw: [1e-300f64.ln(), 0.0],
            modes,
            stay: s.go_w,
            ka: s.ka_w,
            slots: SmallMap::default(),
            phantom: false,
            a: 0.0,
            away: None,
            t: None,
        };
        let f = tr.filter_mut();
        let m = f.objects_mut().mixtures.insert(mix);
        let mut hy: Hyp = f.hyps()[0].clone();
        hy.people.insert(GroupId(1), m);
        f.set_hyps(vec![hy]);
        f.void_cache();
        tr
    }

    #[test]
    fn a_walker_about_to_enter_an_area_makes_it_entered() {
        // the sofa (0..2 x 3..5) 0.8 m ahead of somebody walking at 1 m/s: within 2 s
        let mut tr = walking([1.0, 2.2], [0.0, 1.0]);
        let enter = tr.entering(2.0).to_vec();
        let (room, sofa) = (enter[0], enter[1]);
        assert!(room.p < 1e-12, "already in the room: nobody enters it ({})", room.p);
        assert!(sofa.p > 0.3, "{sofa:?}");
        assert!((sofa.distance.expect("a way") - 0.8).abs() < 1e-9);
        assert!((sofa.eta.expect("a time") - 0.8).abs() < 1e-9);
        assert_eq!(sofa.person, Some(1));
        // walking away from it: hardly anybody enters (the velocity's spread over 2 s is 0.8 m)
        let mut away = walking([1.0, 2.2], [0.0, -1.0]);
        let p_away = away.entering(2.0)[1].p;
        assert!(p_away < 0.05 * sofa.p, "{p_away} against {}", sofa.p);
        // no look-ahead: nothing
        let mut none = walking([1.0, 2.2], [0.0, 1.0]);
        assert_eq!(none.entering(0.0)[1].p, 0.0);
        // shown at the mixture's mean, walking
        let shown = tr.persons()[0].shown.expect("in view");
        assert_eq!((shown.at, shown.velocity), ([1.0, 2.2], [0.0, 1.0]));
        assert!(shown.walk > 0.999 && !tr.persons()[0].lost);
    }

    #[test]
    fn occupied_by_the_filter_or_the_rooms_own_ld2450() {
        let rows = vec![vec![0.2, 0.8], vec![0.9, 0.1]];
        // light_cost 2: above 2/3
        assert_eq!(occupancy(&rows, &[false, false], 2.0), [Some(Source::Filter), None]);
        assert_eq!(
            occupancy(&rows, &[true, true], 2.0),
            [Some(Source::Both), Some(Source::Ld2450)]
        );
        assert_eq!(occupancy(&rows, &[false, false], 1000.0), [None, None]);
    }
}
