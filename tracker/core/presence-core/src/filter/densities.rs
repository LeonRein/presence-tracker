//! People without a measuring track (hidden.py, MODEL.md 5.3): their density over the tiles, exact
//! sums instead of samples. A known person is a Bernoulli (existence r, Garcia-Fernandez et al. 2018
//! eq. 31); the people nobody knows of are a Poisson intensity in the same arrays, not normalized
//! (hidden.Undetected, MODEL.md 3.4, 5.5).
//!
//! State of one density, as masses (a person's sum to 1):
//!   walk[c]          walking in tile c
//!   still[l, k, c]   standing in tile c, stay of kind l, detectability level k
//!   region[r, a]     behind a door in region r, the stay in age bin a
//!   out              out of the house
//!   amp[k, a, c]     the LD2410C amplitude of the stay, if one was measured (hidden.Hidden.amp)
//!
//! All densities live in one store with a row each (docs/rust-core.md 2.1): walking all of them is
//! one matrix product over the rows. An index points at its row through a table; when a density is
//! released, the last row moves into the gap.

use std::ops::Range;

use crate::filter::pool::PoolId;
use crate::vmath::pairwise_sum;

/// A share of measured amplitude below this everywhere: nothing measured (hidden.MEASURED_CUT).
pub const MEASURED_CUT: f64 = 1e-6;

/// A density's index, valid until it is released.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DensityId(u32);

impl PoolId for DensityId {
    fn from_index(index: usize) -> Self {
        Self(u32::try_from(index).expect("too many densities"))
    }
    fn index(self) -> usize {
        self.0 as usize
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// a known person: normalized, with its existence r
    Person,
    /// the people nobody knows of: expected numbers, r = 1
    Intensity,
}

/// The measured amplitudes of the stays, P(level of g | still, detectability, tile) (K x (A + 1) x
/// n, the last level g = 1: nothing measured), and the share of each still mass that is no fresh draw
/// since they were last settled (K x n; None: all of it): moving on only mixes them with g = 1,
/// applied when they are read (hidden.Hidden.amp, _keep). Clone only for a fork.
#[derive(Debug, Clone)]
pub struct Amp {
    pub levels: Vec<f64>,
    pub keep: Option<Vec<f64>>,
}

/// Factors per tile of a likelihood: for walkers (n), for still people per tile (n) or per level of
/// detectability and tile (K x n), the same for every kind of stay.
#[derive(Debug, Clone, Copy)]
pub struct TileFactors<'a> {
    pub walk: &'a [f64],
    pub still: &'a [f64],
}

impl<'a> TileFactors<'a> {
    /// The same factor per tile for walkers and still people.
    pub fn same(f: &'a [f64]) -> Self {
        Self { walk: f, still: f }
    }
}

/// TileFactors that own their arrays.
#[derive(Debug, Clone, Default)]
pub struct TileFactorsBuf {
    pub walk: Vec<f64>,
    pub still: Vec<f64>,
}

impl TileFactorsBuf {
    pub fn view(&self) -> TileFactors<'_> {
        TileFactors {
            walk: &self.walk,
            still: &self.still,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DensityShape {
    pub tiles: usize,
    /// kinds of stay (L)
    pub kinds: usize,
    /// levels of detectability (K)
    pub levels: usize,
    pub regions: usize,
    pub ages: usize,
    /// levels of the LD2410C amplitude (A, without g = 1)
    pub amps: usize,
}

impl DensityShape {
    pub fn still_len(&self) -> usize {
        self.kinds * self.levels * self.tiles
    }

    pub fn region_len(&self) -> usize {
        self.regions * self.ages
    }

    /// K x (A + 1) x n
    pub fn amp_len(&self) -> usize {
        self.levels * (self.amps + 1) * self.tiles
    }

    /// The tiles of still[l, k, :].
    pub fn still_row(&self, l: usize, k: usize) -> Range<usize> {
        let at = (l * self.levels + k) * self.tiles;
        at..at + self.tiles
    }

    /// The tiles of level k of an array per level of detectability and tile (K x n).
    pub fn level_row(&self, k: usize) -> Range<usize> {
        k * self.tiles..(k + 1) * self.tiles
    }

    /// The tiles of amp[k, a, :] (a = amps: g = 1).
    pub fn amp_row(&self, k: usize, a: usize) -> Range<usize> {
        let at = (k * (self.amps + 1) + a) * self.tiles;
        at..at + self.tiles
    }

    /// The amplitudes of stays nothing measured: g = 1 everywhere (hidden.amp_prior).
    pub fn amp_prior(&self) -> Vec<f64> {
        let mut out = vec![0.0; self.amp_len()];
        for k in 0..self.levels {
            out[self.amp_row(k, self.amps)].fill(1.0);
        }
        out
    }

    /// The still masses per level of detectability, summed over the kinds of stay (K x n), in
    /// numpy's order (still.sum(axis=0)).
    pub fn still_per_level(&self, still: &[f64]) -> Vec<f64> {
        let kn = self.levels * self.tiles;
        let mut out = still[..kn].to_vec();
        for kind in still.chunks_exact(kn.max(1)).skip(1) {
            for (o, &x) in out.iter_mut().zip(kind) {
                *o += x;
            }
        }
        out
    }
}

/// One density as owned arrays (for saving it, or loading it from the shell).
#[derive(Debug, Clone)]
pub struct DensityData {
    pub walk: Vec<f64>,
    pub still: Vec<f64>,
    pub region: Vec<f64>,
    pub out: f64,
    pub clock: f64,
    pub r: f64,
    pub kind: Kind,
    pub amp: Option<Amp>,
}

#[derive(Debug)]
pub struct Densities {
    shape: DensityShape,
    walk: Vec<f64>,
    still: Vec<f64>,
    region: Vec<f64>,
    out: Vec<f64>,
    clock: Vec<f64>,
    r: Vec<f64>,
    kind: Vec<Kind>,
    amp: Vec<Option<Amp>>,
    /// index -> row (None: free)
    row_of: Vec<Option<usize>>,
    /// row -> index
    id_of: Vec<DensityId>,
    free: Vec<DensityId>,
}

/// One density, borrowed for reading.
#[derive(Debug)]
pub struct DensityRef<'a> {
    pub walk: &'a [f64],
    pub still: &'a [f64],
    pub region: &'a [f64],
    pub out: f64,
    pub clock: f64,
    pub r: f64,
    pub kind: Kind,
    pub amp: Option<&'a Amp>,
}

/// One density, borrowed for changing it.
#[derive(Debug)]
pub struct DensityMut<'a> {
    pub shape: DensityShape,
    pub walk: &'a mut [f64],
    pub still: &'a mut [f64],
    pub region: &'a mut [f64],
    pub out: &'a mut f64,
    pub clock: &'a mut f64,
    pub r: &'a mut f64,
    pub kind: Kind,
    pub amp: &'a mut Option<Amp>,
}

impl Densities {
    pub fn new(shape: DensityShape) -> Self {
        Self {
            shape,
            walk: Vec::new(),
            still: Vec::new(),
            region: Vec::new(),
            out: Vec::new(),
            clock: Vec::new(),
            r: Vec::new(),
            kind: Vec::new(),
            amp: Vec::new(),
            row_of: Vec::new(),
            id_of: Vec::new(),
            free: Vec::new(),
        }
    }

    pub fn shape(&self) -> DensityShape {
        self.shape
    }

    /// Number of live densities (rows).
    pub fn len(&self) -> usize {
        self.id_of.len()
    }

    pub fn is_empty(&self) -> bool {
        self.id_of.is_empty()
    }

    /// One more than the largest index ever given (for tables by index).
    pub fn capacity(&self) -> usize {
        self.row_of.len()
    }

    /// The live densities' indices, in the order of their rows.
    pub fn ids(&self) -> &[DensityId] {
        &self.id_of
    }

    pub fn contains(&self, id: DensityId) -> bool {
        self.row_of.get(id.index()).is_some_and(Option::is_some)
    }

    /// A new density with all masses 0 (and r = 1).
    pub fn alloc(&mut self, kind: Kind) -> DensityId {
        let s = self.shape;
        let row = self.id_of.len();
        self.walk.resize(self.walk.len() + s.tiles, 0.0);
        self.still.resize(self.still.len() + s.still_len(), 0.0);
        self.region.resize(self.region.len() + s.region_len(), 0.0);
        self.out.push(0.0);
        self.clock.push(0.0);
        self.r.push(1.0);
        self.kind.push(kind);
        self.amp.push(None);
        let id = match self.free.pop() {
            Some(id) => id,
            None => {
                self.row_of.push(None);
                DensityId::from_index(self.row_of.len() - 1)
            }
        };
        self.row_of[id.index()] = Some(row);
        self.id_of.push(id);
        id
    }

    /// A new density with these masses.
    pub fn insert(&mut self, data: DensityData) -> DensityId {
        let id = self.alloc(data.kind);
        self.set(id, data);
        id
    }

    /// Overwrites a density's masses (its kind stays). Panics if the arrays do not fit the shape.
    pub fn set(&mut self, id: DensityId, data: DensityData) {
        let d = self.get_mut(id);
        d.walk.copy_from_slice(&data.walk);
        d.still.copy_from_slice(&data.still);
        d.region.copy_from_slice(&data.region);
        (*d.out, *d.clock, *d.r) = (data.out, data.clock, data.r);
        *d.amp = data.amp;
    }

    /// A density's masses as owned arrays.
    pub fn data(&self, id: DensityId) -> DensityData {
        let d = self.get(id);
        DensityData {
            walk: d.walk.to_vec(),
            still: d.still.to_vec(),
            region: d.region.to_vec(),
            out: d.out,
            clock: d.clock,
            r: d.r,
            kind: d.kind,
            amp: d.amp.cloned(),
        }
    }

    /// Frees a density's row: the last row moves into it.
    pub(super) fn release(&mut self, id: DensityId) {
        let row = self.row_of[id.index()].take().expect("a density released twice");
        let last = self.id_of.len() - 1;
        let s = self.shape;
        for (data, len) in [
            (&mut self.walk, s.tiles),
            (&mut self.still, s.still_len()),
            (&mut self.region, s.region_len()),
        ] {
            data.copy_within(last * len..(last + 1) * len, row * len);
            data.truncate(last * len);
        }
        self.out.swap_remove(row);
        self.clock.swap_remove(row);
        self.r.swap_remove(row);
        self.kind.swap_remove(row);
        self.amp.swap_remove(row);
        self.id_of.swap_remove(row);
        if row != last {
            self.row_of[self.id_of[row].index()] = Some(row);
        }
        self.free.push(id);
    }

    fn row(&self, id: DensityId) -> usize {
        self.row_of[id.index()].expect("a density used after its release")
    }

    pub fn get(&self, id: DensityId) -> DensityRef<'_> {
        let row = self.row(id);
        let s = self.shape;
        DensityRef {
            walk: &self.walk[row * s.tiles..][..s.tiles],
            still: &self.still[row * s.still_len()..][..s.still_len()],
            region: &self.region[row * s.region_len()..][..s.region_len()],
            out: self.out[row],
            clock: self.clock[row],
            r: self.r[row],
            kind: self.kind[row],
            amp: self.amp[row].as_ref(),
        }
    }

    pub fn get_mut(&mut self, id: DensityId) -> DensityMut<'_> {
        let row = self.row(id);
        let s = self.shape;
        DensityMut {
            shape: s,
            walk: &mut self.walk[row * s.tiles..][..s.tiles],
            still: &mut self.still[row * s.still_len()..][..s.still_len()],
            region: &mut self.region[row * s.region_len()..][..s.region_len()],
            out: &mut self.out[row],
            clock: &mut self.clock[row],
            r: &mut self.r[row],
            kind: self.kind[row],
            amp: &mut self.amp[row],
        }
    }

    /// Two densities, the first borrowed for changing it. Panics if they are the same.
    fn pair_mut(&mut self, id: DensityId, other: DensityId) -> (DensityMut<'_>, DensityRef<'_>) {
        let (row, from) = (self.row(id), self.row(other));
        assert_ne!(row, from, "a density borrowed twice");
        let s = self.shape;
        fn split<T>(v: &mut [T], len: usize, a: usize, b: usize) -> (&mut [T], &[T]) {
            if a < b {
                let (lo, hi) = v.split_at_mut(b * len);
                (&mut lo[a * len..(a + 1) * len], &hi[..len])
            } else {
                let (lo, hi) = v.split_at_mut(a * len);
                (&mut hi[..len], &lo[b * len..(b + 1) * len])
            }
        }
        let (walk, walk_b) = split(&mut self.walk, s.tiles, row, from);
        let (still, still_b) = split(&mut self.still, s.still_len(), row, from);
        let (region, region_b) = split(&mut self.region, s.region_len(), row, from);
        let (out, out_b) = split(&mut self.out, 1, row, from);
        let (clock, clock_b) = split(&mut self.clock, 1, row, from);
        let (r, r_b) = split(&mut self.r, 1, row, from);
        let (amp, amp_b) = split(&mut self.amp, 1, row, from);
        (
            DensityMut {
                shape: s,
                walk,
                still,
                region,
                out: &mut out[0],
                clock: &mut clock[0],
                r: &mut r[0],
                kind: self.kind[row],
                amp: &mut amp[0],
            },
            DensityRef {
                walk: walk_b,
                still: still_b,
                region: region_b,
                out: out_b[0],
                clock: clock_b[0],
                r: r_b[0],
                kind: self.kind[from],
                amp: amp_b[0].as_ref(),
            },
        )
    }

    /// A copy of a density as a new one: the model branches (Hidden.copy).
    pub fn fork(&mut self, id: DensityId) -> DensityId {
        let kind = self.get(id).kind;
        let new = self.alloc(kind);
        let (to, from) = self.pair_mut(new, id);
        to.walk.copy_from_slice(from.walk);
        to.still.copy_from_slice(from.still);
        to.region.copy_from_slice(from.region);
        (*to.out, *to.clock, *to.r) = (from.out, from.clock, from.r);
        *to.amp = from.amp.cloned();
        new
    }

    /// A person who does not exist (r = 0), out of the house: the partner of a known person in a
    /// hypothesis that lacks them when hypotheses are merged (Hidden.nobody).
    pub fn nobody(&mut self) -> DensityId {
        let id = self.alloc(Kind::Person);
        let d = self.get_mut(id);
        (*d.out, *d.r) = (1.0, 0.0);
        id
    }

    /// Whether any stay carries a measured amplitude (Hidden.measured); settles it first.
    pub fn measured(&mut self, id: DensityId) -> bool {
        self.get_mut(id).settle_amp();
        self.get(id).amp.is_some()
    }

    /// The mixture sum_i w_i x Bernoulli_i of [(w_i, density_i)], weights summing to 1, as a new
    /// density (Hidden.mixture): exists with r = sum_i w_i r_i, then the density sum_i w_i r_i
    /// density_i / r. A part with r_i = 0 (nobody: a hypothesis without this person) adds only to
    /// not existing. The amplitudes mix with the still mass they belong to. The parts are settled,
    /// as reading them in the Python filter did.
    pub fn mixture(&mut self, parts: &[(f64, DensityId)]) -> DensityId {
        let r = parts.iter().fold(0.0, |s, &(w, id)| s + w * self.get(id).r);
        if !(r > 0.0) {
            let h = self.fork(parts[0].1);
            *self.get_mut(h).r = 0.0;
            return h;
        }
        let mut measured = false;
        for &(w, id) in parts {
            if w * self.get(id).r > 0.0 {
                measured |= self.measured(id);
            }
        }
        let s = self.shape;
        let prior = if measured { s.amp_prior() } else { Vec::new() };
        let mut num: Option<Vec<f64>> = None;
        let mut h: Option<DensityId> = None;
        for &(w, id) in parts {
            let k = w * self.get(id).r / r;
            if !(k > 0.0) {
                continue;
            }
            if measured {
                let o = self.get(id);
                let part = scaled_amp(&s, k, &s.still_per_level(o.still), o.amp.map_or(&prior, |a| &a.levels));
                num = Some(match num {
                    None => part,
                    Some(mut acc) => {
                        for (a, p) in acc.iter_mut().zip(&part) {
                            *a += p;
                        }
                        acc
                    }
                });
            }
            match h {
                None => {
                    let new = self.fork(id);
                    self.get_mut(new).scale(k);
                    h = Some(new);
                }
                Some(h) => {
                    let (to, from) = self.pair_mut(h, id);
                    let pairs = to
                        .walk
                        .iter_mut()
                        .zip(from.walk)
                        .chain(to.still.iter_mut().zip(from.still))
                        .chain(to.region.iter_mut().zip(from.region));
                    for (t, f) in pairs {
                        *t += k * f;
                    }
                    *to.out += k * from.out;
                }
            }
        }
        let h = h.expect("a mixture of densities with a part");
        let mut d = self.get_mut(h);
        *d.amp = None;
        if let Some(num) = num {
            d.set_amp(&num);
        }
        *d.r = r.min(1.0);
        h
    }

    /// The L1 distance between two Bernoullis, r x density (Hidden.distance, for pairing
    /// exchangeable people, MODEL.md 5.6), summed in numpy's order.
    pub fn distance(&self, a: DensityId, b: DensityId) -> f64 {
        let (x, y) = (self.get(a), self.get(b));
        let diff = |p: &[f64], q: &[f64]| {
            let d: Vec<f64> = p.iter().zip(q).map(|(u, v)| (x.r * u - y.r * v).abs()).collect();
            pairwise_sum(&d)
        };
        diff(x.walk, y.walk) + diff(x.still, y.still) + diff(x.region, y.region) + (x.r * x.out - y.r * y.out).abs()
    }

    /// Adds k x r x the density of other (a person, or another intensity: r = 1): a person given up
    /// on joins the unknown ones (Undetected.add_scaled). Both are settled.
    pub fn add_scaled(&mut self, id: DensityId, other: DensityId, k: f64) {
        let k = k * self.get(other).r;
        let (mine, theirs) = (self.measured(id), self.measured(other));
        let measured = mine || (theirs && k > 0.0);
        let s = self.shape;
        let (mut to, from) = self.pair_mut(id, other);
        let num = measured.then(|| {
            let prior = s.amp_prior();
            let mine = to.amp.as_ref().map_or(&prior, |a| &a.levels);
            let mut num = scaled_amp(&s, 1.0, &s.still_per_level(to.still), mine);
            let theirs = scaled_amp(
                &s,
                k,
                &s.still_per_level(from.still),
                from.amp.map_or(&prior, |a| &a.levels),
            );
            for (a, b) in num.iter_mut().zip(&theirs) {
                *a += b;
            }
            num
        });
        for (t, f) in to
            .walk
            .iter_mut()
            .zip(from.walk)
            .chain(to.still.iter_mut().zip(from.still))
        {
            *t += k * f;
        }
        if let Some(num) = num {
            to.set_amp(&num);
        }
        for (t, f) in to.region.iter_mut().zip(from.region) {
            *t += k * f;
        }
        *to.out += k * from.out;
    }
}

/// k x (still mass per level (K x n)) x amp (K x (A + 1) x n), as numpy's
/// k * still.sum(axis=0)[:, None, :] * amp.
fn scaled_amp(s: &DensityShape, k: f64, per_level: &[f64], amp: &[f64]) -> Vec<f64> {
    let mut out = vec![0.0; amp.len()];
    for kk in 0..s.levels {
        let mass = &per_level[s.level_row(kk)];
        for a in 0..=s.amps {
            let row = s.amp_row(kk, a);
            for ((o, &m), &g) in out[row.clone()].iter_mut().zip(mass).zip(&amp[row]) {
                *o = k * m * g;
            }
        }
    }
    out
}

impl DensityRef<'_> {
    /// The sum of all masses (Hidden.total).
    pub fn total(&self) -> f64 {
        pairwise_sum(self.walk) + pairwise_sum(self.still) + pairwise_sum(self.region) + self.out
    }

    /// sum of r x density x f over the tiles in view (Hidden.integrate; f.still per tile).
    pub fn integrate(&self, f: TileFactors, shape: &DensityShape) -> f64 {
        if shape.tiles == 0 {
            return 0.0;
        }
        let walk = self.walk.iter().zip(f.walk).fold(0.0, |s, (w, x)| s + w * x);
        let per_level = shape.still_per_level(self.still);
        let still: Vec<f64> = per_level
            .chunks_exact(shape.tiles)
            .flat_map(|row| row.iter().zip(f.still).map(|(m, x)| m * x))
            .collect();
        self.r * (walk + pairwise_sum(&still))
    }

    /// The expected number walking in the tiles marked (seen 0 or 1): r x walk @ seen.
    pub fn walking_in(&self, seen: &[f64]) -> f64 {
        self.walk.iter().zip(seen).fold(0.0, |s, (w, f)| s + self.r * w * f)
    }
}

impl DensityMut<'_> {
    /// Applies the mixing of the amplitude that moving on deferred (hidden.Hidden.amp): the share
    /// of the still mass that is a fresh draw has g = 1. Nothing measured is left if the share
    /// with g = 1 is within MEASURED_CUT of 1 everywhere.
    pub fn settle_amp(&mut self) {
        let s = self.shape;
        let Some(amp) = self.amp.as_mut() else {
            return;
        };
        let Some(keep) = amp.keep.take() else {
            return;
        };
        for k in 0..s.levels {
            let w = &keep[s.level_row(k)];
            for a in 0..=s.amps {
                let fresh = if a == s.amps { 1.0 } else { 0.0 };
                for (g, &w) in amp.levels[s.amp_row(k, a)].iter_mut().zip(w) {
                    *g = w * *g + (1.0 - w) * fresh;
                }
            }
        }
        let lowest = (0..s.levels)
            .flat_map(|k| amp.levels[s.amp_row(k, s.amps)].iter().copied())
            .fold(f64::INFINITY, f64::min);
        if 1.0 - lowest < MEASURED_CUT {
            *self.amp = None;
        }
    }

    /// The amplitudes from sum_i (still mass_i per level) x amp_i (K x (A + 1) x n), divided by the
    /// still mass now; where there is none, nothing measured (Hidden._set_amp).
    pub fn set_amp(&mut self, num: &[f64]) {
        let s = self.shape;
        let per_level = s.still_per_level(self.still);
        let mut levels = vec![0.0; s.amp_len()];
        for k in 0..s.levels {
            let mass = &per_level[s.level_row(k)];
            for a in 0..=s.amps {
                let prior = if a == s.amps { 1.0 } else { 0.0 };
                let row = s.amp_row(k, a);
                for ((g, &x), &m) in levels[row.clone()].iter_mut().zip(&num[row]).zip(mass) {
                    *g = if m > 0.0 { x / m } else { prior };
                }
            }
        }
        *self.amp = Some(Amp { levels, keep: None });
    }

    pub fn scale(&mut self, k: f64) {
        for part in [&mut *self.walk, &mut *self.still, &mut *self.region] {
            for v in part.iter_mut() {
                *v *= k;
            }
        }
        *self.out *= k;
    }

    /// Multiplies walkers by f.walk and still people by f.still for every kind of stay
    /// (kernels._hidden_weigh). As a person (intensity false): normalized, returns the log of the
    /// mass that was left (-inf and nothing normalized if none was). As an intensity: returns the
    /// mass after less the mass before, in view. The sums run in numpy's order.
    fn weigh_masses(&mut self, f: TileFactors, intensity: bool) -> f64 {
        let s = self.shape;
        let before = if intensity {
            pairwise_sum(self.walk) + pairwise_sum(self.still)
        } else {
            0.0
        };
        for (w, x) in self.walk.iter_mut().zip(f.walk) {
            *w *= x;
        }
        let per_level = f.still.len() != s.tiles;
        for l in 0..s.kinds {
            for k in 0..s.levels {
                let factors = if per_level { &f.still[s.level_row(k)] } else { f.still };
                for (x, g) in self.still[s.still_row(l, k)].iter_mut().zip(factors) {
                    *x *= g;
                }
            }
        }
        if intensity {
            return -(before - (pairwise_sum(self.walk) + pairwise_sum(self.still)));
        }
        self.normalize_masses()
    }

    /// Scales the masses to sum 1; the log of what they summed to, -inf if nothing.
    fn normalize_masses(&mut self) -> f64 {
        let tot = pairwise_sum(self.walk) + pairwise_sum(self.still) + pairwise_sum(self.region) + *self.out;
        if tot <= 0.0 {
            return f64::NEG_INFINITY;
        }
        let s = 1.0 / tot;
        self.scale(s);
        tot.ln()
    }

    /// A person's masses scaled to sum 1 (hidden.Hidden._normalize); the people nobody knows of
    /// are not normalized (0).
    pub fn normalize(&mut self) -> f64 {
        match self.kind {
            Kind::Person => self.normalize_masses(),
            Kind::Intensity => 0.0,
        }
    }

    /// Multiplies by a likelihood per tile (hidden.Hidden.weigh, Undetected.weigh). Returns the log
    /// of the factor for the hypotheses holding it: for a person 1 - r + r x (mass that is left),
    /// and r's update (the Bernoulli update, Garcia-Fernandez et al. 2018 eq. 31); for the unknown
    /// ones the Poisson void probability's log (eq. 18-24).
    pub fn weigh(&mut self, f: TileFactors) -> f64 {
        if self.shape.tiles == 0 {
            return 0.0;
        }
        match self.kind {
            Kind::Person => self.weigh_person(f),
            Kind::Intensity => self.weigh_masses(f, true),
        }
    }

    /// weigh as a person, whatever its kind (the part of a Gaussian gone through a door).
    pub fn weigh_person(&mut self, f: TileFactors) -> f64 {
        let q = self.weigh_masses(f, false);
        self.bernoulli(q)
    }

    /// The factor for the hypotheses from the log q of the mass that was left, and r's update
    /// (hidden.Hidden._bernoulli).
    fn bernoulli(&mut self, q: f64) -> f64 {
        let r = *self.r;
        if r >= 1.0 {
            return q;
        }
        if q == f64::NEG_INFINITY {
            *self.r = 0.0;
            return (-r).ln_1p();
        }
        if q > 0.0 {
            // in the form that cannot overflow
            let z = r + (1.0 - r) * (-q).exp();
            *self.r = r / z;
            return q + z.ln();
        }
        let z = 1.0 - r + r * q.exp();
        *self.r = r * q.exp() / z;
        z.ln()
    }

    /// The mass in view taken out by factors f per tile, for walkers and still people alike: the
    /// mass before less after (the expiry of a record, filter.Tracker._expire).
    pub fn take_in_view(&mut self, f: &[f64]) -> f64 {
        -self.weigh_masses(TileFactors::same(f), true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHAPE: DensityShape = DensityShape {
        tiles: 3,
        kinds: 2,
        levels: 2,
        regions: 1,
        ages: 2,
        amps: 2,
    };

    fn person(d: &mut Densities, w: f64) -> DensityId {
        let id = d.alloc(Kind::Person);
        let mut m = d.get_mut(id);
        m.walk.fill(w);
        m.still.fill(0.05);
        *m.out = 0.1;
        m.normalize();
        id
    }

    #[test]
    fn released_rows_are_filled_by_the_last() {
        let mut d = Densities::new(SHAPE);
        let a = person(&mut d, 1.0);
        let b = person(&mut d, 2.0);
        let c = person(&mut d, 3.0);
        let walk_c = d.get(c).walk.to_vec();
        d.release(a);
        assert_eq!(d.len(), 2);
        assert_eq!(d.get(c).walk, &walk_c[..]);
        let e = d.alloc(Kind::Intensity);
        assert_eq!(e, a);
        assert_eq!(d.get(e).walk, &[0.0; 3]);
        assert_eq!(d.get(b).kind, Kind::Person);
        assert!(d.contains(b) && !d.contains(DensityId::from_index(7)));
    }

    #[test]
    fn a_person_is_normalized_and_its_existence_updated() {
        let mut d = Densities::new(SHAPE);
        let id = person(&mut d, 0.1);
        *d.get_mut(id).r = 0.5;
        // everybody in view gone: only out and the regions remain
        let q = d.get_mut(id).weigh(TileFactors::same(&[0.0; 3]));
        let p = d.get(id);
        assert!((p.out - 1.0).abs() < 1e-15);
        let before_out = 0.1 / (0.3 + 0.6 + 0.1);
        assert!((q - (0.5f64 + 0.5 * before_out).ln()).abs() < 1e-15);
        assert!((p.r - 0.5 * before_out / (0.5 + 0.5 * before_out)).abs() < 1e-15);
    }

    #[test]
    fn the_unknown_ones_lose_the_mass_taken() {
        let mut d = Densities::new(SHAPE);
        let id = d.alloc(Kind::Intensity);
        d.get_mut(id).walk.fill(1.0);
        let still = [1.0; 6]; // per level
        let q = d.get_mut(id).weigh(TileFactors {
            walk: &[0.5, 1.0, 1.0],
            still: &still,
        });
        assert_eq!(q, -0.5);
        assert_eq!(d.get(id).walk, &[0.5, 1.0, 1.0]);
    }

    #[test]
    fn weighing_is_the_plain_arithmetic() {
        // test_kernels.test_the_weigh_kernel_is_the_numpy_arithmetic_bit_for_bit, its formula
        let mut rng = crate::testing::Rng::new(5);
        for trial in 0..60 {
            let shape = DensityShape {
                tiles: 1 + rng.below(300),
                kinds: 5,
                levels: 3,
                regions: 2,
                ages: 18,
                amps: 2,
            };
            let mut d = Densities::new(shape);
            let n = shape.tiles;
            let walk: Vec<f64> = (0..n)
                .map(|_| rng.uniform() * 10f64.powf(-9.0 * rng.uniform()))
                .collect();
            let still: Vec<f64> = (0..shape.still_len()).map(|_| rng.uniform() * 1e-3).collect();
            let fw: Vec<f64> = (0..n).map(|_| rng.uniform()).collect();
            let fs: Vec<f64> = (0..if trial % 2 == 1 { 3 * n } else { n })
                .map(|_| rng.uniform())
                .collect();
            for kind in [Kind::Person, Kind::Intensity] {
                let id = d.alloc(kind);
                let mut m = d.get_mut(id);
                m.walk.copy_from_slice(&walk);
                m.still.copy_from_slice(&still);
                m.region.fill(0.01);
                *m.out = 0.3;
                let q = m.weigh(TileFactors { walk: &fw, still: &fs });
                let w: Vec<f64> = walk.iter().zip(&fw).map(|(a, b)| a * b).collect();
                let s: Vec<f64> = still
                    .iter()
                    .enumerate()
                    .map(|(i, x)| {
                        x * if fs.len() == n {
                            fs[i % n]
                        } else {
                            fs[(i / n) % 3 * n + i % n]
                        }
                    })
                    .collect();
                let p = d.get(id);
                match kind {
                    Kind::Intensity => {
                        let before = pairwise_sum(&walk) + pairwise_sum(&still);
                        assert_eq!(q, -(before - (pairwise_sum(&w) + pairwise_sum(&s))));
                        assert_eq!(p.walk, &w[..]);
                    }
                    Kind::Person => {
                        let tot = pairwise_sum(&w) + pairwise_sum(&s) + pairwise_sum(&[0.01; 36]) + 0.3;
                        assert_eq!(q, tot.ln());
                        assert_eq!(p.walk, &w.iter().map(|x| x * (1.0 / tot)).collect::<Vec<_>>()[..]);
                    }
                }
            }
        }
    }

    #[test]
    fn the_distance_is_the_plain_sum() {
        // test_kernels.test_the_tile_mass_and_distance_kernels_are_the_numpy_bit_for_bit
        let mut d = Densities::new(SHAPE);
        let (a, b) = (person(&mut d, 0.3), person(&mut d, 0.1));
        (*d.get_mut(a).r, *d.get_mut(b).r) = (0.7, 0.4);
        let (x, y) = (d.data(a), d.data(b));
        let diff = |p: &[f64], q: &[f64]| {
            pairwise_sum(
                &p.iter()
                    .zip(q)
                    .map(|(u, v)| (0.7 * u - 0.4 * v).abs())
                    .collect::<Vec<_>>(),
            )
        };
        let want = diff(&x.walk, &y.walk)
            + diff(&x.still, &y.still)
            + diff(&x.region, &y.region)
            + (0.7 * x.out - 0.4 * y.out).abs();
        assert_eq!(d.distance(a, b), want);
        assert_eq!(d.distance(a, a), 0.0);
    }

    #[test]
    fn a_mixture_with_nobody_lowers_the_existence() {
        let mut d = Densities::new(SHAPE);
        let a = person(&mut d, 0.3);
        let nobody = d.nobody();
        let m = d.mixture(&[(0.25, a), (0.75, nobody)]);
        let (x, y) = (d.get(m), d.get(a));
        assert_eq!(x.r, 0.25);
        assert_eq!(x.walk, y.walk); // only the part that exists counts for the density
        let none = d.mixture(&[(1.0, nobody)]);
        assert_eq!(d.get(none).r, 0.0);
    }

    #[test]
    fn settling_the_amplitude_mixes_towards_nothing_measured() {
        let mut d = Densities::new(SHAPE);
        let id = person(&mut d, 0.3);
        let mut levels = SHAPE.amp_prior();
        for k in 0..2 {
            levels[SHAPE.amp_row(k, 0)].fill(1.0);
            levels[SHAPE.amp_row(k, 2)].fill(0.0);
        }
        *d.get_mut(id).amp = Some(Amp {
            levels,
            keep: Some(vec![0.5; 6]),
        });
        assert!(d.measured(id));
        let amp = d.get(id).amp.expect("measured");
        assert!(amp.keep.is_none());
        assert_eq!(amp.levels[SHAPE.amp_row(1, 0)], [0.5; 3]);
        assert_eq!(amp.levels[SHAPE.amp_row(1, 2)], [0.5; 3]);
        // almost all of it fresh: nothing measured
        d.get_mut(id).amp.as_mut().expect("measured").keep = Some(vec![1e-7; 6]);
        assert!(!d.measured(id));
    }
}
