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

use crate::filter::pool::PoolId;
use crate::vmath::pairwise_sum;

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

/// The measured amplitudes of the stays (K x (A + 1) x n) and the share of each still mass that is
/// no fresh draw since they were last settled (K x n): moving on only mixes them with g = 1, applied
/// when they are read (hidden.Hidden._keep).
#[derive(Debug)]
pub struct Amp {
    pub levels: Vec<f64>,
    pub keep: Vec<f64>,
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
}

impl DensityShape {
    pub fn still_len(&self) -> usize {
        self.kinds * self.levels * self.tiles
    }

    pub fn region_len(&self) -> usize {
        self.regions * self.ages
    }
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

    /// Frees a density's row: the last row moves into it.
    pub fn release(&mut self, id: DensityId) {
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
            walk: &self.walk[row * s.tiles..(row + 1) * s.tiles],
            still: &self.still[row * s.still_len()..(row + 1) * s.still_len()],
            region: &self.region[row * s.region_len()..(row + 1) * s.region_len()],
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
            walk: &mut self.walk[row * s.tiles..(row + 1) * s.tiles],
            still: &mut self.still[row * s.still_len()..(row + 1) * s.still_len()],
            region: &mut self.region[row * s.region_len()..(row + 1) * s.region_len()],
            out: &mut self.out[row],
            clock: &mut self.clock[row],
            r: &mut self.r[row],
            kind: self.kind[row],
            amp: &mut self.amp[row],
        }
    }
}

impl DensityMut<'_> {
    pub fn scale(&mut self, k: f64) {
        for part in [&mut *self.walk, &mut *self.still, &mut *self.region] {
            for v in part.iter_mut() {
                *v *= k;
            }
        }
        *self.out *= k;
    }

    /// Multiplies walkers by f_walk (n) and still people by f_still, per level of detectability
    /// (K x n) or the same for all (n), for every kind of stay (kernels._hidden_weigh). As a
    /// person (intensity false): normalized, returns the log of the mass that was left (-inf and
    /// nothing normalized if none was). As an intensity: returns the mass after less the mass
    /// before, in view. The sums run in numpy's order.
    fn weigh_masses(&mut self, f_walk: &[f64], f_still: &[f64], intensity: bool) -> f64 {
        let n = self.shape.tiles;
        let before = if intensity {
            pairwise_sum(self.walk) + pairwise_sum(self.still)
        } else {
            0.0
        };
        for (w, f) in self.walk.iter_mut().zip(f_walk) {
            *w *= f;
        }
        let per_level = f_still.len() != n;
        for (lk, row) in self.still.chunks_exact_mut(n.max(1)).enumerate() {
            let f = if per_level {
                &f_still[(lk % self.shape.levels) * n..][..n]
            } else {
                f_still
            };
            for (s, f) in row.iter_mut().zip(f) {
                *s *= f;
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

    /// Multiplies by a likelihood per tile (hidden.Hidden.weigh, Undetected.weigh): walkers f_walk
    /// (n), still people f_still (n or K x n). Returns the log of the factor for the hypotheses
    /// holding it: for a person 1 - r + r x (mass that is left), and r's update (the Bernoulli
    /// update, Garcia-Fernandez et al. 2018 eq. 31); for the unknown ones the Poisson void
    /// probability's log (eq. 18-24).
    pub fn weigh(&mut self, f_walk: &[f64], f_still: &[f64]) -> f64 {
        if self.shape.tiles == 0 {
            return 0.0;
        }
        match self.kind {
            Kind::Person => self.weigh_person(f_walk, f_still),
            Kind::Intensity => self.weigh_masses(f_walk, f_still, true),
        }
    }

    /// weigh as a person, whatever its kind (the part of a Gaussian gone through a door).
    pub fn weigh_person(&mut self, f_walk: &[f64], f_still: &[f64]) -> f64 {
        let q = self.weigh_masses(f_walk, f_still, false);
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
        let d = self.weigh_masses(f, f, true);
        -d
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
    }

    #[test]
    fn a_person_is_normalized_and_its_existence_updated() {
        let mut d = Densities::new(SHAPE);
        let id = person(&mut d, 0.1);
        *d.get_mut(id).r = 0.5;
        // everybody in view gone: only out and the regions remain
        let q = d.get_mut(id).weigh(&[0.0; 3], &[0.0; 3]);
        let p = d.get(id);
        let left = p.out;
        assert!((left - 1.0).abs() < 1e-15);
        let before_out = 0.1 / (0.3 + 0.6 + 0.1);
        assert!((q - (0.5f64 + 0.5 * before_out).ln()).abs() < 1e-15);
        assert!((p.r - 0.5 * before_out / (0.5 + 0.5 * before_out)).abs() < 1e-15);
    }

    #[test]
    fn the_unknown_ones_lose_the_mass_taken() {
        let mut d = Densities::new(SHAPE);
        let id = d.alloc(Kind::Intensity);
        d.get_mut(id).walk.fill(1.0);
        let f_still = [1.0; 6]; // per level
        let q = d.get_mut(id).weigh(&[0.5, 1.0, 1.0], &f_still);
        assert_eq!(q, -0.5);
        assert_eq!(d.get(id).walk, &[0.5, 1.0, 1.0]);
    }
}
