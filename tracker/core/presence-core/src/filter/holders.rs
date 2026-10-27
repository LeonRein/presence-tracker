//! Which hypotheses hold each object (docs/rust-core.md 2.2; filter.Tracker._objects): built anew
//! whenever the hypotheses change, as compressed rows - the objects in the order of their first
//! appearance over the hypotheses (per hypothesis its people with tracks, the known ones without,
//! the unknown ones, then the sources of its ghost tracks), each with the hypotheses holding it, as
//! often as they hold it. The order sets the order in which sums over the objects run.

use crate::filter::densities::DensityId;
use crate::filter::hyp::Hyp;
use crate::filter::mixture::MixtureId;
use crate::filter::pool::PoolId;

/// An object the hypotheses hold.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Obj {
    Mixture(MixtureId),
    Density(DensityId),
}

#[derive(Debug, Default, PartialEq)]
pub struct Holders {
    objects: Vec<Obj>,
    /// the source of a ghost track (not a person)
    source: Vec<bool>,
    ptr: Vec<usize>,
    hyps: Vec<usize>,
}

impl Holders {
    /// `mixtures`, `densities`: one more than the largest index of each (for tables by index).
    pub fn build(hyps: &[Hyp], mixtures: usize, densities: usize) -> Self {
        const NONE: usize = usize::MAX;
        let (mut by_mixture, mut by_density) = (vec![NONE; mixtures], vec![NONE; densities]);
        let mut objects: Vec<(Obj, bool)> = Vec::new();
        let mut refs: Vec<Vec<usize>> = Vec::new();
        for (h, hy) in hyps.iter().enumerate() {
            let people = hy.people.values().map(|&m| (Obj::Mixture(m), false));
            let hidden = hy.hidden.iter().chain([&hy.unknown]).map(|&d| (Obj::Density(d), false));
            let sources = hy.ghosts.values().map(|g| (Obj::Mixture(g.source), true));
            for (obj, source) in people.chain(hidden).chain(sources) {
                let slot = match obj {
                    Obj::Mixture(m) => &mut by_mixture[m.index()],
                    Obj::Density(d) => &mut by_density[d.index()],
                };
                if *slot == NONE {
                    *slot = objects.len();
                    objects.push((obj, source));
                    refs.push(Vec::new());
                }
                refs[*slot].push(h);
            }
        }
        let mut ptr = Vec::with_capacity(objects.len() + 1);
        ptr.push(0);
        let mut flat = Vec::new();
        for r in &refs {
            flat.extend_from_slice(r);
            ptr.push(flat.len());
        }
        let (objects, source) = objects.into_iter().unzip();
        Self {
            objects,
            source,
            ptr,
            hyps: flat,
        }
    }

    /// The objects by their entry; with `sources` also the sources of ghost tracks
    /// (Tracker._objects(phantoms)).
    pub fn entries(&self, sources: bool) -> impl Iterator<Item = (usize, Obj)> + '_ {
        self.objects
            .iter()
            .copied()
            .enumerate()
            .filter(move |&(e, _)| sources || !self.source[e])
    }

    /// The hypotheses holding an entry's object, as often as they hold it.
    pub fn refs(&self, entry: usize) -> &[usize] {
        &self.hyps[self.ptr[entry]..self.ptr[entry + 1]]
    }

    /// A log factor of an entry's object goes to the hypotheses holding it. Every weighing of the
    /// objects books through here.
    pub fn book(&self, hyps: &mut [Hyp], entry: usize, d: f64) {
        for &h in self.refs(entry) {
            hyps[h].logw += d;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::hyp::{Ghost, GroupId};
    use crate::filter::mixture::SegId;

    #[test]
    fn objects_come_in_the_order_they_first_appear() {
        let (d, m) = (DensityId::from_index, MixtureId::from_index);
        let mut a = Hyp::new(0.0, vec![d(3), d(3)], d(0));
        a.people.insert(GroupId(1), m(2));
        let mut b = Hyp::new(0.0, vec![d(4)], d(0));
        b.people.insert(GroupId(7), m(2));
        b.ghosts.insert(
            SegId(5),
            Ghost {
                life: vec![0.0],
                source: m(1),
            },
        );
        let h = Holders::build(&[a, b], 3, 5);
        let all: Vec<(Obj, Vec<usize>)> = h.entries(true).map(|(e, o)| (o, h.refs(e).to_vec())).collect();
        assert_eq!(
            all,
            vec![
                (Obj::Mixture(m(2)), vec![0, 1]),
                (Obj::Density(d(3)), vec![0, 0]),
                (Obj::Density(d(0)), vec![0, 1]),
                (Obj::Density(d(4)), vec![1]),
                (Obj::Mixture(m(1)), vec![1]),
            ]
        );
        assert_eq!(h.entries(false).count(), 4);
        let mut hyps = [Hyp::new(0.0, Vec::new(), d(0)), Hyp::new(1.0, Vec::new(), d(0))];
        h.book(&mut hyps, 1, 0.5);
        assert_eq!((hyps[0].logw, hyps[1].logw), (1.0, 1.0));
    }
}
