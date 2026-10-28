//! The objects the hypotheses hold (docs/rust-core.md 2.1, 2.2): the Gaussian mixtures in a pool and
//! the densities in one store. What is made for one hypothesis and not the others is a new object;
//! what nobody holds after the hypotheses changed is released.

use crate::filter::densities::{Densities, DensityId, DensityShape};
use crate::filter::holders::{Holders, Obj};
use crate::filter::hyp::{GroupId, Hyp, Owner};
use crate::filter::mixture::{Mixture, MixtureId, SegId};
use crate::filter::pool::{Pool, PoolId};
use crate::world::World;

#[derive(Debug)]
pub struct Objects {
    pub(crate) mixtures: Pool<Mixture, MixtureId>,
    pub(crate) densities: Densities,
}

/// The people a track leaving its owner leaves, per (mixture, track, other tracks left) once: the
/// mixture without the track's offset, or the person over the tiles (Tracker._release's cache).
pub type ReleaseCache = Vec<((MixtureId, SegId, bool), Obj)>;

impl Objects {
    pub fn new(shape: DensityShape) -> Self {
        Self {
            mixtures: Pool::default(),
            densities: Densities::new(shape),
        }
    }

    pub fn mixtures(&self) -> &Pool<Mixture, MixtureId> {
        &self.mixtures
    }

    pub fn densities(&self) -> &Densities {
        &self.densities
    }

    /// Releases what no hypothesis holds; a mixture's part gone through a door goes with it.
    pub(super) fn release_unheld(&mut self, holders: &Holders) {
        let mut mixture_held = vec![false; self.mixtures.capacity()];
        let mut density_held = vec![false; self.densities.capacity()];
        for (_, obj) in holders.entries() {
            match obj {
                Obj::Mixture(m) => mixture_held[m.index()] = true,
                Obj::Density(d) => density_held[d.index()] = true,
            }
        }
        let gone: Vec<MixtureId> = self.mixtures.ids().filter(|m| !mixture_held[m.index()]).collect();
        for m in gone {
            if let Some(away) = self.mixtures.remove(m).away {
                self.densities.release(away);
            }
        }
        for m in self.mixtures.ids() {
            if let Some(away) = self.mixtures.get(m).away {
                density_held[away.index()] = true;
            }
        }
        let gone: Vec<DensityId> = self
            .densities
            .ids()
            .iter()
            .copied()
            .filter(|d| !density_held[d.index()])
            .collect();
        for d in gone {
            self.densities.release(d);
        }
    }

    /// In hypothesis hy, the person of group gid no longer owns seg (it ended, or it is somebody
    /// else's in this child): what they are now - their mixture with the offset marginalized out,
    /// or without any live track left their density on the tiles (MODEL.md 5.4; Tracker._release;
    /// Hyp::set_person puts it in). Shared between the hypotheses through the cache.
    pub fn release(&mut self, world: &World, hy: &Hyp, gid: GroupId, seg: SegId, cache: &mut ReleaseCache) -> Obj {
        let id = *hy.people.get(&gid).expect("a person with tracks");
        let rest = hy.tracks.iter().any(|(&s, &o)| o == Owner::Person(gid) && s != seg);
        let key = (id, seg, rest);
        match cache.iter().find(|(k, _)| *k == key) {
            Some(&(_, new)) => new,
            None => {
                let new = if rest {
                    // the mixture without this track: another object, the others keep theirs
                    let mut mix = self.mixtures.get(id).fork(&mut self.densities);
                    if mix.slots.contains_key(&seg) {
                        mix.drop_track(seg);
                    }
                    Obj::Mixture(self.mixtures.insert(mix))
                } else {
                    Obj::Density(self.mixtures.get(id).to_tiles(world, &mut self.densities))
                };
                cache.push((key, new));
                new
            }
        }
    }

    /// The bookkeeping's invariants against these hypotheses and holders (Filter::check): the
    /// holders are those of the hypotheses; every object a hypothesis holds is live; every live
    /// object is held, or a mixture's part gone through a door; every such part belongs to exactly
    /// one mixture.
    pub(super) fn check(&self, hyps: &[Hyp], holders: &Holders) -> Result<(), String> {
        let fresh = Holders::build(hyps, self.mixtures.capacity(), self.densities.capacity());
        if fresh != *holders {
            return Err("the holders are not those of the hypotheses".into());
        }
        let live_mixtures: Vec<MixtureId> = self.mixtures.ids().collect();
        let mut held_mixtures = Vec::new();
        let mut held_densities = Vec::new();
        for (_, obj) in holders.entries() {
            match obj {
                Obj::Mixture(m) if self.mixtures.contains(m) => held_mixtures.push(m),
                Obj::Density(d) if self.densities.contains(d) => held_densities.push(d),
                _ => return Err(format!("a hypothesis holds a released object {obj:?}")),
            }
        }
        if let Some(m) = live_mixtures.iter().find(|m| !held_mixtures.contains(m)) {
            return Err(format!("a mixture nobody holds: {m:?}"));
        }
        let mut aways: Vec<DensityId> = Vec::new();
        for &m in &live_mixtures {
            if let Some(away) = self.mixtures.get(m).away {
                if !self.densities.contains(away) {
                    return Err(format!("the part gone through a door of {m:?} is released"));
                }
                if aways.contains(&away) || held_densities.contains(&away) {
                    return Err(format!("a part gone through a door shared: {away:?}"));
                }
                aways.push(away);
            }
        }
        if let Some(d) = self
            .densities
            .ids()
            .iter()
            .find(|d| !held_densities.contains(d) && !aways.contains(d))
        {
            return Err(format!("a density nobody holds: {d:?}"));
        }
        Ok(())
    }
}
