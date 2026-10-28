//! What the filter tells (MODEL.md 6; the outputs of filter.Tracker): per observed room and per place
//! the distribution of the number of people, occupied (the filter's probability, or the room's own
//! LD2450), "wird betreten", and the people of the most probable hypothesis for the display. They only
//! read the filter. The sums run in numpy's order (vmath), the numbers are those of the Python filter;
//! nothing is rounded for the display here (the shell does).

use crate::filter::Filter;
use crate::filter::densities::{DensityRef, DensityShape};
use crate::filter::holders::Obj;
use crate::filter::mixture::Mixture;
use crate::filter::objects::Objects;
use crate::geometry::Point;
use crate::motion::imm::walk_ahead;
use crate::params::WALK;
use crate::plan::{Place, RoomIdx, Zone, ZoneRole};
use crate::vmath::max2;
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
    counts: Option<Distributions>,
    places: Option<Distributions>,
    entering: Option<Vec<Entering>>,
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

    pub fn counts(&mut self, filter: &Filter, world: &World) -> &Distributions {
        self.counts.get_or_insert_with(|| count_distribution(filter, world))
    }

    pub fn places(&mut self, filter: &Filter) -> &Distributions {
        self.places.get_or_insert_with(|| place_distribution(filter))
    }

    pub fn entering(&mut self, filter: &Filter, world: &World) -> &[Entering] {
        self.entering
            .get_or_insert_with(|| entering(filter, world, world.outputs.lead_time))
    }

    /// The three at once (for the zones), each computed if missing, in this order.
    fn all(&mut self, filter: &Filter, world: &World) -> (&Distributions, &Distributions, &[Entering]) {
        self.counts(filter, world);
        self.places(filter);
        self.entering(filter, world);
        let Self {
            counts,
            places,
            entering,
            ..
        } = self;
        match (counts, places, entering) {
            (Some(c), Some(p), Some(e)) => (c, p, e),
            _ => unreachable!("all three computed above"),
        }
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
pub(crate) fn in_view(world: &World, objects: &Objects, obj: Obj) -> Vec<f64> {
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
    for (&m, room) in mass.iter().zip(&world.tiles.room) {
        if let Some(RoomIdx(k)) = *room {
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
    // an expected number beyond 10^4 people is no house but a broken state: cut there, not in memory
    (mean + 10.0 * mean.max(0.0).sqrt() + 6.0).min(1e4) as usize
}

/// The distributions of the number of people per column of per_object - for a known person the
/// probabilities, for the unknown ones the expected numbers - mixed over the hypotheses
/// (Tracker._counts). Each object once, in the order of the holders.
fn counts(filter: &Filter, per_object: impl Fn(Obj) -> Vec<f64>) -> Distributions {
    let holders = filter.holders();
    let mut values: Vec<Vec<f64>> = vec![Vec::new(); holders.len()];
    for (e, obj) in holders.people() {
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
    // the rooms' rows (the last row is in view at all)
    counts[..seen.len()]
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
    // the weighted mean of a number per mode
    let mean = |v: [f64; 2]| w[0] * v[0] + w[1] * v[1];
    let pos = mix.modes.each_ref().map(|g| g.pos());
    let at = [mean(pos.map(|p| p[0])), mean(pos.map(|p| p[1]))];
    let velocity = [0, 1].map(|ax| mean(mix.modes.each_ref().map(|g| g.mean[ax * g.dim() + 1])));
    let spread = mean([0, 1].map(|k| {
        let (p, v) = (pos[k], mix.modes[k].pos_var());
        let (dx, dy) = (p[0] - at[0], p[1] - at[1]);
        (dx * dx + dy * dy) + (v[0] + v[1])
    }));
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
    let targets = &world.targets;
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
    for (entry, obj) in filter.holders().people() {
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
    // per entry of the holders its rise, if it has one
    let mut rise_of = vec![None; holders.len()];
    for (k, r) in rises.iter().enumerate() {
        rise_of[r.entry] = Some(k);
    }
    let mut p = vec![0.0; targets.len()];
    let mut share = vec![vec![0.0; targets.len()]; rises.len()];
    for (&wh, hy) in filter.hyp_weights().iter().zip(filter.hyps()) {
        let mut empty = vec![1.0; targets.len()];
        for &m in hy.people.values() {
            let Some(k) = rise_of[holders.entry(Obj::Mixture(m))] else {
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
        .find(|&(_, p)| world.targets.holds(z, world.raster.cell_of(p)))
        .filter(|&(s, p)| s == 0.0 || !world.walls.crosses(&[at], &[p])[0])
        .map(|(s, _)| s)
}

// ------------------------------------------------------------------ the zones (MODEL.md 6)

/// What the outputs say of a zone of the home (Tracker.zone_states; the shell forms its ZoneState).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ZoneNumbers {
    /// the most probable number of people (an area: those of the display in it)
    pub count: usize,
    /// P(somebody there), where the filter knows it (the observed rooms, the rooms of regions)
    pub probability: Option<f64>,
    /// occupied by the decision (MODEL.md 6)
    pub decided: Decision,
    /// the people of the display in it, walking or not (at most count)
    pub moving: usize,
    pub still: usize,
    /// "wird betreten" (the observed rooms and the areas), and whether above its threshold
    pub entering: Option<Entering>,
    pub approaching: bool,
    /// false: its own sensors all went silent (Home Assistant gets it as unavailable)
    pub available: bool,
}

/// Whether a zone is occupied by the decision (MODEL.md 6) and what decided.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    /// none is made (areas, rooms of regions of several rooms)
    None,
    Free,
    Occupied(Source),
}

impl Decision {
    fn of(source: Option<Source>) -> Self {
        source.map_or(Self::Free, Self::Occupied)
    }
}

/// The house as a whole: the people in it, and of those in view walking or not.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct HouseNumbers {
    pub count: usize,
    pub moving: usize,
    pub still: usize,
}

/// What the zone numbers are made of, computed before (the cached outputs and the rule of the rooms'
/// own LD2450).
pub struct ZoneInputs<'a> {
    pub counts: &'a Distributions,
    pub places: &'a Distributions,
    pub entering: &'a [Entering],
    pub occupancy: &'a [Option<Source>],
    pub silent: &'a [bool],
    pub persons: &'a [Person],
}

/// The zone numbers of the filter in this world (Tracker.zone_states) from the cached outputs at the
/// filter's epoch and the rooms' own LD2450 (occupancy, silent).
pub fn zones_of(
    cache: &mut OutputCache,
    filter: &Filter,
    world: &World,
    occupancy: &[Option<Source>],
    silent: &[bool],
) -> (Vec<ZoneNumbers>, HouseNumbers) {
    let persons = persons(filter, world);
    let (counts, places, entering) = cache.at(filter.epoch()).all(filter, world);
    let inputs = ZoneInputs {
        counts,
        places,
        entering,
        occupancy,
        silent,
        persons: &persons,
    };
    zone_numbers(world, &inputs)
}

/// Per zone of the home (plan.zones) and for the house (Tracker.zone_states): observed rooms by the
/// count of all hypotheses, occupied by the filter or their own LD2450 (a count raised to 1 then, the
/// house's too); a region's rooms by the region's distribution, decided only if it is one room; areas
/// by the people of the display in them. Moving or still from the display's people (walking above 1/2
/// and faster than 0.15 m/s), "wird betreten" above approach_cost / (approach_cost + 1).
pub fn zone_numbers(world: &World, x: &ZoneInputs) -> (Vec<ZoneNumbers>, HouseNumbers) {
    let (plan, o) = (&world.plan, &world.outputs);
    let mut raised = false;
    let home: Vec<&Zone> = plan.home().collect();
    let mut zones: Vec<ZoneNumbers> = home
        .iter()
        .map(|z| {
            let mut n = ZoneNumbers {
                count: 0,
                probability: None,
                decided: Decision::None,
                moving: 0,
                still: 0,
                entering: None,
                approaching: false,
                available: true,
            };
            match z.role {
                ZoneRole::Observed(RoomIdx(k)) => {
                    n.count = argmax(&x.counts[k]);
                    n.probability = Some(1.0 - x.counts[k][0]);
                    n.decided = Decision::of(x.occupancy[k]);
                    if matches!(x.occupancy[k], Some(Source::Ld2450 | Source::Both)) && n.count == 0 {
                        (n.count, raised) = (1, true);
                    }
                    n.available = !x.silent[k];
                }
                ZoneRole::InRegion(r) => {
                    let dist = &x.places[Place::Region(r).index(plan.regions.len())];
                    let probability = 1.0 - dist[0];
                    n.probability = Some(probability);
                    if plan.regions[r.0].rooms.len() == 1 {
                        n.count = argmax(dist);
                        n.decided = Decision::of((probability > o.occupied_above()).then_some(Source::Filter));
                    }
                }
                ZoneRole::Area | ZoneRole::OutsideRoom | ZoneRole::Entry => {}
            }
            n
        })
        .collect();
    let house = x.places.last().map_or(0, |d| argmax(d));
    let mut total = HouseNumbers {
        count: house.max(usize::from(raised)),
        ..HouseNumbers::default()
    };
    for p in x.persons {
        let Some(shown) = p.shown else { continue };
        let moving = !p.lost && shown.walk > 0.5 && crate::vmath::py_hypot(shown.velocity[0], shown.velocity[1]) > 0.15;
        let (m, s) = (usize::from(moving), usize::from(!moving));
        (total.moving, total.still) = (total.moving + m, total.still + s);
        for (z, n) in home.iter().zip(&mut zones) {
            if z.shape.contains(shown.at, 0.0) {
                n.count += usize::from(z.role == ZoneRole::Area);
                (n.moving, n.still) = (n.moving + m, n.still + s);
            }
        }
    }
    // "wird betreten": the targets are the observed rooms, then the areas
    let observed = (0..home.len()).filter(|&k| matches!(home[k].role, ZoneRole::Observed(_)));
    let targets = observed.chain((0..home.len()).filter(|&k| home[k].role == ZoneRole::Area));
    for (k, e) in targets.zip(x.entering) {
        zones[k].entering = Some(*e);
        zones[k].approaching = e.p > o.entering_above();
    }
    for n in &mut zones {
        n.moving = n.moving.min(n.count);
        n.still = n.still.min(n.count - n.moving);
    }
    total.moving = total.moving.min(total.count);
    total.still = total.still.min(total.count - total.moving);
    (zones, total)
}

// ------------------------------------------------------------------ "Ziel" (until it is in Rust)

/// A person with a track walking with P(walking) at least ENTER_MIN_WALK (filter.Tracker.
/// _target_outputs, until the destination map is in Rust): that probability without the part gone
/// through a door, where the walking component is and its velocity.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Walker {
    pub walking: f64,
    pub at: Point,
    pub velocity: Point,
}

/// A walker a hypothesis holds: its index among the walkers, its place among the hypothesis' people.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Held {
    pub walker: usize,
    pub place: usize,
}

/// The walkers, each once in the order of the holders; per hypothesis those it holds; the most
/// probable hypothesis.
#[derive(Debug)]
pub struct Walkers {
    pub walkers: Vec<Walker>,
    pub held: Vec<Vec<Held>>,
    pub best: usize,
}

pub fn walkers(filter: &Filter) -> Walkers {
    let (objects, holders) = (filter.objects(), filter.holders());
    let mut index = vec![None; holders.len()];
    let mut walkers = Vec::new();
    for (e, obj) in holders.people() {
        let Obj::Mixture(m) = obj else { continue };
        let mix = objects.mixtures().get(m);
        let walking = mix.weights()[WALK] * (1.0 - mix.a);
        if mix.phantom || walking < ENTER_MIN_WALK {
            continue;
        }
        let mode = &mix.modes[WALK];
        let d = mode.dim();
        index[e] = Some(walkers.len());
        walkers.push(Walker {
            walking,
            at: mode.pos(),
            velocity: [mode.mean[1], mode.mean[d + 1]],
        });
    }
    let held = filter
        .hyps()
        .iter()
        .map(|hy| {
            hy.people
                .values()
                .enumerate()
                .filter_map(|(place, &m)| index[holders.entry(Obj::Mixture(m))].map(|walker| Held { walker, place }))
                .collect()
        })
        .collect();
    Walkers {
        walkers,
        held,
        best: most_probable(filter),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::Start;
    use crate::filter::densities::{DensityData, Kind};
    use crate::filter::hyp::{GroupId, Hyp};
    use crate::filter::mixture::Kalman;
    use crate::filter::small_map::SmallMap;
    use crate::testing;
    use crate::tracker::Tracker;
    use crate::world::World;

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
        tr.reset_people(
            0.0,
            Some(&[Start::Place(Place::Observed), Start::Place(Place::Outside)]),
        )
        .expect("places");
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
        tr.reset_people(0.0, Some(&[Start::Place(Place::Observed)]))
            .expect("places");
        let before = tr.count_distribution().clone();
        let epoch = tr.filter().epoch();
        // the person goes out without a new epoch (as a weighing does between moves): the outputs
        // stay as they were computed
        let world = testing::flat();
        let f = tr.filter_mut();
        f.start_over(&world, Some(&[Start::Place(Place::Outside)]));
        assert_eq!(f.epoch(), epoch);
        assert_eq!(tr.count_distribution(), &before);
        // a new epoch: computed anew
        tr.event(0.0);
        assert!(tr.count_distribution()[0][0] > 0.99);
        // starting over is a new epoch, and the counter never goes back
        let at = tr.filter().epoch();
        tr.reset_people(0.0, Some(&[Start::Place(Place::Observed)]))
            .expect("places");
        assert!(tr.filter().epoch() > at);
        assert_eq!(tr.count_distribution(), &before);
    }

    /// A person with a track walking at `at` with velocity v (the filter holds them in one
    /// hypothesis, with the unknown ones none).
    fn walking(at: Point, v: Point) -> Tracker {
        let mut tr = Tracker::new(testing::flat());
        tr.reset_people(0.0, Some(&[])).expect("places");
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
        let enter = tr.entering().to_vec();
        let (room, sofa) = (enter[0], enter[1]);
        assert!(room.p < 1e-12, "already in the room: nobody enters it ({})", room.p);
        assert!(sofa.p > 0.3, "{sofa:?}");
        assert!((sofa.distance.expect("a way") - 0.8).abs() < 1e-9);
        assert!((sofa.eta.expect("a time") - 0.8).abs() < 1e-9);
        assert_eq!(sofa.person, Some(1));
        // walking away from it: hardly anybody enters (the velocity's spread over 2 s is 0.8 m)
        let mut away = walking([1.0, 2.2], [0.0, -1.0]);
        let p_away = away.entering()[1].p;
        assert!(p_away < 0.05 * sofa.p, "{p_away} against {}", sofa.p);
        // no look-ahead: nothing
        assert_eq!(entering(tr.filter(), tr.world(), 0.0)[1].p, 0.0);
        // shown at the mixture's mean, walking
        let shown = tr.persons()[0].shown.expect("in view");
        assert_eq!((shown.at, shown.velocity), ([1.0, 2.2], [0.0, 1.0]));
        assert!(shown.walk > 0.999 && !tr.persons()[0].lost);
    }

    /// A density of one person (r) with all of it standing in these tiles in these shares.
    fn standing(world: &World, r: f64, at: &[(usize, f64)]) -> DensityData {
        let shape = world.density_shape();
        let mut still = vec![0.0; shape.still_len()];
        for &(tile, share) in at {
            still[tile] = share; // kind 0, level 0
        }
        DensityData {
            walk: vec![0.0; shape.tiles],
            still,
            region: vec![0.0; shape.region_len()],
            out: 0.0,
            clock: 0.0,
            r,
            kind: Kind::Person,
            amp: None,
        }
    }

    /// Two hypotheses (0.6, 0.4): the first holds a person with a track, standing in view but for a
    /// quarter gone through a door (out of the house), and a known person without one who exists with
    /// 0.4, standing in tile 0; the second only that one. Nobody unknown.
    fn two_hypotheses() -> Tracker {
        let mut tr = walking([3.0, 2.5], [0.0, 0.0]);
        let world = testing::flat();
        let f = tr.filter_mut();
        let densities = &mut f.objects_mut().densities;
        let known = densities.insert(standing(&world, 0.4, &[(0, 1.0)]));
        let away = densities.insert(DensityData {
            out: 1.0,
            ..standing(&world, 1.0, &[])
        });
        let mut a = f.hyps()[0].clone();
        let m = *a.people.values().next().expect("the walker");
        let mix = f.objects_mut().mixtures.get_mut(m);
        (mix.logw, mix.a, mix.away) = ([0.0, 1e-300f64.ln()], 0.25, Some(away));
        a.logw = 0.6f64.ln();
        a.hidden.push(known);
        let mut b = Hyp::new(0.4f64.ln(), vec![known], a.unknown);
        b.logw = 0.4f64.ln();
        f.set_hyps(vec![a, b]);
        f.void_cache();
        tr
    }

    #[test]
    fn two_hypotheses_count_as_worked_out_by_hand() {
        let mut tr = two_hypotheses();
        // in view (and in the room): the first P(0) = 0.25 x 0.6, P(1) = 0.75 x 0.6 + 0.25 x 0.4,
        // P(2) = 0.75 x 0.4; the second 0.6, 0.4; mixed 0.6 / 0.4
        let want = [0.6 * 0.15 + 0.4 * 0.6, 0.6 * 0.55 + 0.4 * 0.4, 0.6 * 0.3];
        let counts = tr.count_distribution().clone();
        assert!(counts.iter().all(|row| close(&row[..3], &want, 1e-12)), "{counts:?}");
        // out of the house: the first's walker for a quarter
        let places = tr.place_distribution().clone();
        assert!(close(&places[0][..3], &want, 1e-12));
        assert!(
            close(&places[1][..2], &[0.6 * 0.75 + 0.4, 0.6 * 0.25], 1e-12),
            "{places:?}"
        );
        assert!(close(&places[2][..3], &want, 1e-12), "the house: in view");
        // per object: the walker's in view (1 - a), the known one's r
        let objects = tr.filter().objects();
        // the holders: the walker, the known one, the unknown ones
        let (m, d) = (tr.filter().holders().objects()[0], tr.filter().holders().objects()[1]);
        assert!((in_view(tr.world(), objects, m).iter().sum::<f64>() - 0.75).abs() < 1e-12);
        assert!(close(&place_probs(objects, m), &[0.75, 0.25], 1e-15));
        assert!(close(&place_probs(objects, d), &[0.4, 0.0], 1e-15));
        // the display: the first hypothesis' people that rather exist than not - the walker, not the
        // known one (r 0.4); its cloud the standing Gaussian with 3/4, no tiles (its part is out)
        let shown = tr.persons();
        assert_eq!(shown.iter().map(|p| (p.id, p.lost)).collect::<Vec<_>>(), [(1, false)]);
        let clouds = tr.clouds();
        assert_eq!(clouds.len(), 1);
        assert_eq!(clouds[0].gauss.len(), 1);
        assert!((clouds[0].gauss[0][0] - 0.75).abs() < 1e-15 && clouds[0].tiles.is_empty());
    }

    #[test]
    fn a_person_without_a_track_is_shown_at_the_densest_tile() {
        let mut tr = Tracker::new(testing::flat());
        tr.reset_people(0.0, Some(&[])).expect("places");
        let world = testing::flat();
        let f = tr.filter_mut();
        let d = f
            .objects_mut()
            .densities
            .insert(standing(&world, 0.9, &[(0, 0.6), (11, 0.4)]));
        let mut hy = f.hyps()[0].clone();
        hy.hidden.push(d);
        f.set_hyps(vec![hy]);
        let p = tr.persons();
        let shown = p[0].shown.expect("in view");
        // the tiles are equal: the densest is tile 0; the spread is the masses' around its centre, each
        // tile with its own variance (0.25^2 / 12 x 2 axes), per unit of mass (r x 1)
        let c = &world.tiles.centers;
        let d2 = |t: usize| (c[t][0] - c[0][0]).powi(2) + (c[t][1] - c[0][1]).powi(2);
        let var = 2.0 * testing::TILE * testing::TILE / 12.0;
        let spread = (0.9 * 0.6 * (d2(0) + var) + 0.9 * 0.4 * (d2(11) + var)) / 0.9;
        assert_eq!((shown.at, shown.velocity, shown.walk), (c[0], [0.0, 0.0], 0.0));
        assert!(
            (shown.sigma - spread.sqrt()).abs() < 1e-12,
            "{} against {}",
            shown.sigma,
            spread.sqrt()
        );
        assert!(p[0].lost && (p[0].r - 0.9).abs() < 1e-15);
    }

    #[test]
    fn the_zones_numbers_from_a_person_standing_on_the_sofa() {
        // a person with a track standing in the sofa area (walking at 0 m/s: still): the room counts
        // them by the filter and is occupied by it; the area counts the display's person in it
        let mut tr = walking([1.0, 4.0], [0.0, 0.0]);
        let (zones, house) = tr.zone_numbers(0.0, Some(0.0));
        let (room, sofa) = (zones[0], zones[1]);
        assert_eq!((room.count, room.moving, room.still), (1, 0, 1));
        assert!((room.probability.expect("the filter's") - 1.0).abs() < 1e-12);
        assert_eq!(room.decided, Decision::Occupied(Source::Filter));
        assert_eq!(
            (sofa.count, sofa.moving, sofa.still, sofa.probability, sofa.decided),
            (1, 0, 1, None, Decision::None)
        );
        assert!(sofa.entering.is_some() && room.entering.is_some() && !sofa.approaching);
        assert_eq!((house.count, house.moving, house.still), (1, 0, 1));
        // nothing heard from its own sensor for longer than silent_after: not available
        let (zones, _) = tr.zone_numbers(31.0, Some(0.0));
        assert!(!zones[0].available && zones[1].available);
    }

    #[test]
    fn a_new_world_voids_the_cache_and_other_tiles_are_refused() {
        let mut tr = walking([1.0, 2.2], [0.0, 1.0]);
        assert!(tr.entering()[1].p > 0.3);
        // the same tiles, no look-ahead: "wird betreten" anew from the new world
        let mut config = testing::flat_config();
        config.params.lead_time = 0.0;
        let world = World::from_export(testing::flat_export(), &config, 30.0).expect("a world");
        tr.set_world(world).expect("the same tiles");
        assert_eq!(tr.entering()[1].p, 0.0);
        // other tiles (the core's own build of the flat room: 0.4 m squares): refused, nothing changed
        let other = World::build(&testing::flat_config(), crate::params::Params::default(), 30.0).expect("a world");
        assert!(tr.set_world(other).is_err());
        assert_eq!(tr.world().tiles.n, testing::flat_export().tiles.n);
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
