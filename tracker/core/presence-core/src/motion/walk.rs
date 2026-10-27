//! The motion of the people without a track over the tiles (MODEL.md 3.1-3.4, 5.3; hidden.Hidden.move):
//! the stays, walking between the tiles in ticks, the stays in the regions without a sensor, coming
//! home and newcomers. Walking all densities on the same tiles is one matrix product per tick: it
//! reads the matrix of walking once for all of them.

use faer::linalg::matmul::matmul;
use faer::{Accum, MatMut, MatRef, Par};

use super::stay::{StayStep, stays};
use crate::filter::densities::{Densities, DensityId, DensityMut, Kind};
use crate::params::Params;
use crate::world::{Tiles, World};

/// Moves each density by its own step dt (Hidden.move_all): the stays, then the walkers in ticks of
/// the tiles' tick (each density keeps the time since its last tick), those with the same number
/// of ticks together; then the regions and the newcomers.
pub fn move_densities(world: &World, densities: &mut Densities, movers: &[(DensityId, f64)]) {
    let (tiles, p, shapes) = (&world.tiles, &world.params, &world.shapes);
    if tiles.n > 0 {
        let mut by_ticks: Vec<(usize, Vec<DensityId>)> = Vec::new();
        for &(id, dt) in movers {
            let step = StayStep::new(p.stop_rate(), p.kappa_switch, shapes, dt);
            let d = densities.get_mut(id);
            stays(
                d.walk,
                d.still,
                d.amp.as_mut().map(|a| a.keep.as_mut_slice()),
                &step,
                shapes,
            );
            *d.clock += dt;
            let mut ticks = 0;
            while *d.clock >= tiles.tick {
                *d.clock -= tiles.tick;
                ticks += 1;
            }
            if ticks > 0 {
                match by_ticks.iter_mut().find(|(k, _)| *k == ticks) {
                    Some((_, ids)) => ids.push(id),
                    None => by_ticks.push((ticks, vec![id])),
                }
            }
        }
        for (ticks, ids) in &by_ticks {
            walk_ticks(tiles, densities, ids, *ticks);
        }
    }
    let ways = tiles.ways();
    for &(id, dt) in movers {
        let mut d = densities.get_mut(id);
        if tiles.regions > 0 || ways > 0 {
            regions(tiles, p, &mut d, dt);
        }
        if d.kind == Kind::Intensity {
            arrive(tiles, p, &mut d, dt);
        }
    }
}

/// `ticks` ticks of walking for these densities together: per tick what walks through a door goes
/// into its region (the stay there begins) or out, the rest moves between the tiles.
fn walk_ticks(tiles: &Tiles, densities: &mut Densities, ids: &[DensityId], ticks: usize) {
    let (n, m, outs) = (tiles.n, ids.len(), tiles.regions + 1);
    let mut walk: Vec<f64> = ids
        .iter()
        .flat_map(|&id| densities.get(id).walk.iter().copied())
        .collect();
    let mut next = vec![0.0; m * n];
    let mut gone = vec![0.0; m * outs];
    let walk_t = MatRef::from_row_major_slice(&tiles.walk_t, n, n);
    let into_t = MatRef::from_row_major_slice(&tiles.into_t, n, outs);
    for _ in 0..ticks {
        let w = MatRef::from_row_major_slice(&walk, m, n);
        matmul(
            MatMut::from_row_major_slice_mut(&mut gone, m, outs),
            Accum::Replace,
            w,
            into_t,
            1.0,
            Par::Seq,
        );
        matmul(
            MatMut::from_row_major_slice_mut(&mut next, m, n),
            Accum::Replace,
            w,
            walk_t,
            1.0,
            Par::Seq,
        );
        for (&id, g) in ids.iter().zip(gone.chunks_exact(outs)) {
            let d = densities.get_mut(id);
            let ages = d.shape.ages;
            for (r, &v) in g[..tiles.regions].iter().enumerate() {
                d.region[r * ages] += v;
            }
            *d.out += g[tiles.regions];
        }
        std::mem::swap(&mut walk, &mut next);
    }
    for (&id, row) in ids.iter().zip(walk.chunks_exact(n)) {
        densities.get_mut(id).walk.copy_from_slice(row);
    }
}

/// The stays in the regions without a sensor (kernels.hidden_regions): a stay ends by its hazard and
/// who comes out leaves through one of the region's doors alike - walking into view at a door, or
/// out of the house through one of its ways out (none at all: the stay goes on); coming home by
/// every way in alike; the stays age.
fn regions(tiles: &Tiles, p: &Params, d: &mut DensityMut, dt: f64) {
    let ages = tiles.ages;
    let ways = tiles.ways();
    let arrive = -(-p.arrive_rate * ways as f64 * dt).exp_m1();
    for (r, doors) in tiles.region_doors.iter().enumerate() {
        let row = &mut d.region[r * ages..(r + 1) * ages];
        let mut left = 0.0;
        for (v, &rate) in row.iter_mut().zip(&tiles.region_rates[r * ages..]) {
            let e = *v * -(-rate * dt).exp_m1();
            *v -= e;
            left += e;
        }
        let exits = tiles.exits[r];
        let ways_r = doors.len() + exits;
        if ways_r == 0 {
            row[0] += left;
        } else {
            for &c in doors {
                d.walk[c] += left / ways_r as f64;
            }
            *d.out += left * exits as f64 / ways_r as f64;
        }
    }
    if ways > 0 && *d.out > 0.0 {
        let come = *d.out * arrive;
        *d.out -= come;
        for (r, &exits) in tiles.exits.iter().enumerate() {
            d.region[r * ages] += come * exits as f64 / ways as f64;
        }
        for &c in &tiles.entries {
            d.walk[c] += come / ways as f64;
        }
    }
    for row in d.region.chunks_exact_mut(ages) {
        let mut prev = 0.0;
        for (a, v) in row.iter_mut().enumerate() {
            let older = if a + 1 < ages {
                *v * -(-dt / tiles.widths[a]).exp_m1()
            } else {
                0.0
            };
            *v = *v - older + prev;
            prev = older;
        }
    }
}

/// Newcomers at the ways in, and the people out of the house forgotten now and then (only the
/// people nobody knows of; hidden.Undetected._arrive, MODEL.md 3.4).
fn arrive(tiles: &Tiles, p: &Params, d: &mut DensityMut, dt: f64) {
    *d.out *= (-p.forget_rate * dt).exp();
    if tiles.ways() > 0 {
        let come = p.guest_rate * dt;
        for (r, &exits) in tiles.exits.iter().enumerate() {
            d.region[r * tiles.ages] += come * exits as f64;
        }
        for &c in &tiles.entries {
            d.walk[c] += come;
        }
    }
}
