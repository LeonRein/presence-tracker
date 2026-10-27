//! Loading the hypotheses from the Python shell's mirror (docs/rust-core.md 7, phases 3-5; feature
//! "mirror"): what the LD2410C and the outputs still compute in Python, and tests that set a state,
//! come back here. Everything is checked before anything changes; the bookkeeping's invariants after.
//! Thrown away when nothing writes the mirror any more (after phase 5).

use crate::filter::Filter;
use crate::filter::densities::{DensityData, DensityId, Kind};
use crate::filter::hyp::{Ghost, GroupId, Hyp, Owner};
use crate::filter::mixture::{Mixture, MixtureId, SegId};

/// A mixture of the mirror: its index in the core (None: made by the shell), the mixture without its
/// part gone through a door, and that part as an index into the state's densities.
#[derive(Debug)]
pub struct MirrorMixture {
    pub id: Option<MixtureId>,
    pub mixture: Mixture,
    pub away: Option<usize>,
}

/// A hypothesis of the mirror, its objects as indices into the state's lists.
#[derive(Debug)]
pub struct MirrorHyp {
    pub logw: f64,
    /// per live track its owner (None: a ghost)
    pub tracks: Vec<(SegId, Option<GroupId>)>,
    /// per ghost track its life and its source
    pub ghosts: Vec<(SegId, Vec<f64>, usize)>,
    pub people: Vec<(GroupId, usize)>,
    pub hidden: Vec<usize>,
    pub unknown: usize,
}

/// The hypotheses and their objects as the shell holds them.
#[derive(Debug, Default)]
pub struct MirrorState {
    pub densities: Vec<(Option<DensityId>, DensityData)>,
    pub mixtures: Vec<MirrorMixture>,
    pub hyps: Vec<MirrorHyp>,
}

impl Filter {
    /// The hypotheses from the shell's mirror: objects with an index are overwritten, those without
    /// one are made; what nobody holds afterwards is released. Err, and nothing changed, if the
    /// state does not fit (indices, lengths, kinds); Err if the invariants fail afterwards.
    pub fn load(&mut self, state: MirrorState) -> Result<(), String> {
        self.fits(&state)?;
        let MirrorState {
            densities,
            mixtures,
            hyps,
        } = state;
        let objects = &mut self.objects;
        let dids: Vec<DensityId> = densities
            .into_iter()
            .map(|(id, data)| match id.filter(|&d| objects.densities.contains(d)) {
                Some(d) if objects.densities.get(d).kind == data.kind => {
                    objects.densities.set(d, data);
                    d
                }
                _ => objects.densities.insert(data),
            })
            .collect();
        let mids: Vec<MixtureId> = mixtures
            .into_iter()
            .map(|m| {
                let mixture = Mixture {
                    away: m.away.map(|i| dids[i]),
                    ..m.mixture
                };
                match m.id.filter(|&id| objects.mixtures.contains(id)) {
                    Some(id) => {
                        *objects.mixtures.get_mut(id) = mixture;
                        id
                    }
                    None => objects.mixtures.insert(mixture),
                }
            })
            .collect();
        let hyps = hyps
            .into_iter()
            .map(|h| {
                let mut hy = Hyp::new(h.logw, h.hidden.iter().map(|&i| dids[i]).collect(), dids[h.unknown]);
                for (s, owner) in h.tracks {
                    hy.tracks.insert(s, owner.map_or(Owner::Ghost, Owner::Person));
                }
                for (s, life, source) in h.ghosts {
                    hy.ghosts.insert(
                        s,
                        Ghost {
                            life,
                            source: mids[source],
                        },
                    );
                }
                for (g, m) in h.people {
                    hy.people.insert(g, mids[m]);
                }
                hy
            })
            .collect();
        self.set_hyps(hyps);
        self.check()
    }

    /// Whether a mirror's state fits this filter: indices within its lists, arrays of the right
    /// lengths, the unknown ones an intensity and the known ones persons, the parts gone through a
    /// door persons that no hypothesis holds and no two mixtures share.
    fn fits(&self, state: &MirrorState) -> Result<(), String> {
        let s = self.objects.densities.shape();
        let (n_d, n_m) = (state.densities.len(), state.mixtures.len());
        for (k, (_, d)) in state.densities.iter().enumerate() {
            let amp_ok = d.amp.as_ref().is_none_or(|a| {
                a.levels.len() == s.amp_len() && a.keep.as_ref().is_none_or(|k| k.len() == s.levels * s.tiles)
            });
            if d.walk.len() != s.tiles || d.still.len() != s.still_len() || d.region.len() != s.region_len() || !amp_ok
            {
                return Err(format!("density {k}: arrays of another shape"));
            }
        }
        let (kinds, levels, amps) = (s.kinds, s.levels, s.amps);
        let mut aways = Vec::new();
        for (k, m) in state.mixtures.iter().enumerate() {
            let mix = &m.mixture;
            let d = 2 + 2 * mix.slots.len();
            let modes_ok = mix
                .modes
                .iter()
                .all(|g| g.mean.len() == 2 * d && g.cov.len() == 2 * d * d);
            let slots_ok = mix.slots.values().all(|slot| slot.index + 1 < d && slot.index >= 2);
            if !modes_ok || !slots_ok || mix.stay.len() != kinds || mix.ka.len() != levels * amps {
                return Err(format!("mixture {k}: arrays of another shape"));
            }
            if let Some(a) = m.away {
                if a >= n_d || state.densities[a].1.kind != Kind::Person || aways.contains(&a) {
                    return Err(format!(
                        "mixture {k}: its part gone through a door is not its own person"
                    ));
                }
                aways.push(a);
            }
        }
        for (k, h) in state.hyps.iter().enumerate() {
            let in_range = h.hidden.iter().chain([&h.unknown]).all(|&i| i < n_d)
                && h.people
                    .iter()
                    .map(|&(_, m)| m)
                    .chain(h.ghosts.iter().map(|g| g.2))
                    .all(|m| m < n_m);
            if !in_range {
                return Err(format!("hypothesis {k}: an object out of range"));
            }
            if h.hidden.iter().chain([&h.unknown]).any(|i| aways.contains(i)) {
                return Err(format!("hypothesis {k}: holds a part gone through a door"));
            }
            if state.densities[h.unknown].1.kind != Kind::Intensity
                || h.hidden.iter().any(|&i| state.densities[i].1.kind != Kind::Person)
            {
                return Err(format!(
                    "hypothesis {k}: the unknown ones not an intensity, or a known person not a person"
                ));
            }
        }
        Ok(())
    }
}
