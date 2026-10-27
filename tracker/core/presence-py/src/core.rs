//! The filter of the core behind the Python tracker (docs/rust-core.md 7, phase 3): Rust owns the
//! hypotheses and their objects; the world still comes from the Python build (WorldExport), and the
//! shell orchestrates a frame, the LD2410C and the outputs on a mirror of the hypotheses
//! (dump/load). The mirror and the world's export are the throwaway part.

use numpy::{PyArray1, PyArrayDyn, PyArrayMethods, PyReadonlyArrayDyn};
use pyo3::exceptions::{PyFloatingPointError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use presence_core::filter::branch::{Found, Frame};
use presence_core::filter::densities::{Amp, DensityData, DensityId, DensityShape, Kind};
use presence_core::filter::hyp::{Ghost, GroupId, Hyp, Owner};
use presence_core::filter::mixture::{Kalman, Mixture, MixtureId, NewTrack, SegId, Slot};
use presence_core::filter::pool::PoolId;
use presence_core::filter::step::Observed;
use presence_core::filter::tracks::{Lost, TrackInfo};
use presence_core::filter::{Filter, NotFinite};
use presence_core::params::{LostKind, Params, Shapes};
use presence_core::sensors::{SensorSet, SensorState};
use presence_core::world::{DoorTile, Raster, Tiles, World, WorldExport};

fn item<'py>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Bound<'py, PyAny>> {
    d.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing {key}")))
}

fn floats(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<f64>> {
    let a: PyReadonlyArrayDyn<'_, f64> = item(d, key)?.extract()?;
    Ok(a.as_slice()?.to_vec())
}

fn ints(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<usize>> {
    let a: PyReadonlyArrayDyn<'_, i64> = item(d, key)?.extract()?;
    Ok(a.as_slice()?.iter().map(|&i| i as usize).collect())
}

fn number<'py, T: FromPyObjectOwned<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<T> {
    item(d, key)?.extract().map_err(Into::into)
}

fn points(v: &[f64]) -> Vec<[f64; 2]> {
    v.as_chunks::<2>().0.to_vec()
}

/// The world as the Python tracker built it (tiling.Tiling, filter._grid, filtermodel).
fn world_from(e: &Bound<'_, PyDict>) -> PyResult<World> {
    let params =
        Params::from_json(&number::<String>(e, "params")?).map_err(|err| PyValueError::new_err(err.to_string()))?;
    let shapes = Shapes {
        go: floats(e, "go")?,
        go_w: floats(e, "go_w")?,
        go_w_ongoing: floats(e, "go_w_ongoing")?,
        kappa: floats(e, "kappa")?,
        kappa_w: floats(e, "kappa_w")?,
        amp: floats(e, "amp")?,
        ka_w: floats(e, "ka_w")?,
        amp_given_kappa: floats(e, "amp_given_kappa")?,
    };
    let raster = Raster {
        x0: number(e, "x0")?,
        y0: number(e, "y0")?,
        nx: number(e, "nx")?,
        ny: number(e, "ny")?,
    };
    let (n, regions): (usize, usize) = (number(e, "n")?, number(e, "R")?);
    let door_ptr = ints(e, "door_ptr")?;
    let door_list = ints(e, "door_list")?;
    let rates = floats(e, "door_rates")?;
    let door_tiles = points(&floats(e, "door_center")?)
        .into_iter()
        .zip(points(&floats(e, "door_var")?))
        .zip(floats(e, "door_area")?)
        .zip(rates.chunks_exact(regions + 1))
        .map(|(((center, var), area), rates)| DoorTile {
            center,
            var,
            area,
            rates: rates.to_vec(),
        })
        .collect();
    let tiles = Tiles {
        n,
        centers: points(&floats(e, "centers")?),
        var: points(&floats(e, "tile_var")?),
        area: floats(e, "area")?,
        fine: ints(e, "fine")?,
        fine_xy: points(&floats(e, "fine_xy")?),
        regions,
        ages: number(e, "A")?,
        tick: number(e, "tick")?,
        walk_t: floats(e, "walk_t")?,
        into_t: floats(e, "into_t")?,
        door_tiles,
        region_doors: door_ptr.windows(2).map(|w| door_list[w[0]..w[1]].to_vec()).collect(),
        exits: ints(e, "exits")?,
        entries: ints(e, "entries")?,
        region_rates: floats(e, "region_rates")?,
        widths: floats(e, "widths")?,
        points: points(&floats(e, "points")?),
        point_tile: ints(e, "point_tile")?,
        point_count: floats(e, "point_count")?,
        walk_end: Vec::new(),
    };
    type SensorIn<'py> = (f64, f64, f64, f64, PyReadonlyArrayDyn<'py, f64>);
    let sensors = number::<Vec<SensorIn<'_>>>(e, "sensors")?
        .into_iter()
        .map(|(x, y, ox, oy, grid)| Ok(([x, y], [ox, oy], grid.as_slice()?.to_vec())))
        .collect::<PyResult<_>>()?;
    let export = WorldExport {
        params,
        shapes,
        raster,
        walls: floats(e, "walls")?.as_chunks::<4>().0.to_vec(),
        wall_dist: floats(e, "wall_dist")?,
        tiles,
        sensors,
    };
    Ok(World::from_export(export))
}

fn shape_of(world: &World) -> DensityShape {
    let (tiles, shapes) = (&world.tiles, &world.shapes);
    DensityShape {
        tiles: tiles.n,
        kinds: shapes.kinds(),
        levels: shapes.levels(),
        regions: tiles.regions,
        ages: tiles.ages,
        amps: shapes.amps(),
    }
}

fn not_finite(e: NotFinite) -> PyErr {
    PyFloatingPointError::new_err(e.to_string())
}

type Out<'py> = Bound<'py, PyArrayDyn<f64>>;

fn array<'py>(py: Python<'py>, v: &[f64], shape: &[usize]) -> PyResult<Out<'py>> {
    PyArray1::from_slice(py, v).reshape(shape)
}

fn lost_kind(kind: &str) -> PyResult<LostKind> {
    match kind {
        "coast" => Ok(LostKind::Coast),
        "frozen" => Ok(LostKind::Frozen),
        _ => Err(PyValueError::new_err(format!("unknown kind of lost track {kind}"))),
    }
}

fn kind_name(kind: LostKind) -> &'static str {
    match kind {
        LostKind::Coast => "coast",
        LostKind::Frozen => "frozen",
    }
}

fn floats_of(a: &PyReadonlyArrayDyn<'_, f64>) -> PyResult<Vec<f64>> {
    Ok(a.as_slice()?.to_vec())
}

/// A density as the mirror holds it: id (None: made by the shell), walk, still, region, out, clock,
/// r, an intensity, amp, keep.
type DensityIn<'py> = (
    Option<u32>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    f64,
    f64,
    f64,
    bool,
    Option<PyReadonlyArrayDyn<'py, f64>>,
    Option<PyReadonlyArrayDyn<'py, f64>>,
);

/// A mixture as the mirror holds it: id, logw, mean, cov, stay, ka, slots, phantom, a, away (index
/// into the densities given), t.
type MixtureIn<'py> = (
    Option<u32>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    Vec<(u64, usize, f64)>,
    bool,
    f64,
    Option<usize>,
    Option<f64>,
);

/// A hypothesis as the mirror holds it: logw, per track its owner (None: a ghost), the ghosts'
/// lives and sources (index into the mixtures), the people (index into the mixtures), the hidden
/// and the unknown ones (indices into the densities).
type HypIn<'py> = (
    f64,
    Vec<(u64, Option<u64>)>,
    Vec<(u64, PyReadonlyArrayDyn<'py, f64>, usize)>,
    Vec<(u64, usize)>,
    Vec<usize>,
    usize,
);

/// A live track as dump gives it: (seg, si, (t, zt, gt, var), z, z0, born, lost, map_odds).
type TrackIn = (
    u64,
    usize,
    (f64, f64, Option<f64>, f64),
    (f64, f64),
    (f64, f64),
    f64,
    Option<(String, f64, (f64, f64), f64)>,
    f64,
);

#[pyclass(module = "presence_core")]
pub struct Core {
    world: World,
    filter: Filter,
    sensors: Vec<SensorState>,
    observed: Observed,
    /// counts the calls that changed the filter (the shell's mirror is current while it stays)
    serial: u64,
}

impl Core {
    fn changed(&mut self) {
        self.serial += 1;
    }

    /// (walk, still, region, out, clock, r, amp, keep) of a density.
    fn density_out<'py>(py: Python<'py>, d: &DensityData, s: &DensityShape) -> PyResult<Bound<'py, PyAny>> {
        let amp = match &d.amp {
            Some(a) => Some(array(py, &a.levels, &[s.levels, s.amps + 1, s.tiles])?),
            None => None,
        };
        let keep = match d.amp.as_ref().and_then(|a| a.keep.as_ref()) {
            Some(k) => Some(array(py, k, &[s.levels, s.tiles])?),
            None => None,
        };
        Ok((
            array(py, &d.walk, &[s.tiles])?,
            array(py, &d.still, &[s.kinds, s.levels, s.tiles])?,
            array(py, &d.region, &[s.regions, s.ages])?,
            d.out,
            d.clock,
            d.r,
            amp,
            keep,
        )
            .into_pyobject(py)?
            .into_any())
    }
}

// PyO3 extracts the arguments of a method as owned values
#[allow(clippy::needless_pass_by_value)]
#[pymethods]
impl Core {
    #[new]
    fn new(export: &Bound<'_, PyDict>) -> PyResult<Self> {
        let world = world_from(export)?;
        let filter = Filter::new(shape_of(&world), world.sights.len());
        let sensors = vec![
            SensorState {
                active: false,
                last_frame: f64::NEG_INFINITY,
            };
            world.sights.len()
        ];
        Ok(Self {
            world,
            filter,
            sensors,
            observed: Observed::default(),
            serial: 0,
        })
    }

    /// A rebuilt world on the same tiles (sensors recalibrated): what is known stays.
    fn set_world(&mut self, export: &Bound<'_, PyDict>) -> PyResult<()> {
        let walk_end = std::mem::take(&mut self.world.tiles.walk_end); // it depends on the tiles only
        self.world = world_from(export)?;
        self.world.tiles.walk_end = walk_end;
        self.observed = Observed::default();
        Ok(())
    }

    fn has_walk_end(&self) -> bool {
        !self.world.tiles.walk_end.is_empty()
    }

    fn set_walk_end(&mut self, walk_end: PyReadonlyArrayDyn<'_, f64>) -> PyResult<()> {
        self.world.tiles.walk_end = floats_of(&walk_end)?;
        Ok(())
    }

    /// Per sensor whether its frames count and when its last frame came.
    fn set_sensors(&mut self, sensors: Vec<(bool, f64)>) {
        self.sensors = sensors
            .into_iter()
            .map(|(active, last_frame)| SensorState { active, last_frame })
            .collect();
    }

    /// How well the best of these sensors sees each tile (tiling.Tiling.observed).
    fn set_observed(&mut self, sensors: Vec<usize>, per_tile: PyReadonlyArrayDyn<'_, f64>) -> PyResult<()> {
        self.observed
            .insert(sensors.into_iter().collect::<SensorSet>(), floats_of(&per_tile)?);
        Ok(())
    }

    #[getter]
    fn serial(&self) -> u64 {
        self.serial
    }

    #[getter]
    fn version(&self) -> u64 {
        self.filter.version()
    }

    #[setter]
    fn set_version(&mut self, v: u64) {
        self.filter.set_version(v);
    }

    #[getter]
    fn flushed(&self) -> f64 {
        self.filter.flushed()
    }

    #[setter]
    fn set_flushed(&mut self, t: f64) {
        self.filter.set_flushed(t);
    }

    #[getter]
    fn loglik(&self) -> (f64, f64) {
        self.filter.loglik()
    }

    #[setter]
    fn set_loglik(&mut self, v: (f64, f64)) {
        self.filter.set_loglik(v);
    }

    fn clear(&mut self, t: f64) {
        self.filter.clear(t);
        self.changed();
    }

    fn flush(&mut self, py: Python<'_>, t: f64) {
        let Self {
            world,
            filter,
            sensors,
            observed,
            ..
        } = self;
        py.detach(|| filter.flush(world, sensors, observed, t));
        self.changed();
    }

    fn watch(&mut self, si: usize, delta: f64, ghost_total: f64) {
        self.filter.watch(&self.world, si, delta, ghost_total);
        self.changed();
    }

    /// Step 2 of a frame for its tracks that are live: measured ones (those lost before: found
    /// again, returned to be branched on), lost ones, held ones.
    fn track_events(
        &mut self,
        si: usize,
        t: f64,
        measured: Vec<(u64, f64, f64)>,
        lost: Vec<(u64, String, f64, f64)>,
        held: Vec<(u64, f64, f64)>,
    ) -> PyResult<Vec<(u64, f64, f64)>> {
        self.changed();
        let mut refound = Vec::new();
        for (seg, x, y) in measured {
            match self.filter.tracks().get(SegId(seg)).map(|i| i.lost.is_none()) {
                Some(true) => self.filter.measured(&self.world, &self.sensors, SegId(seg), [x, y], t),
                Some(false) => refound.push((seg, x, y)),
                None => {}
            }
        }
        for (seg, kind, x, y) in lost {
            if self.filter.tracks().get(SegId(seg)).is_some() {
                self.filter.lost(SegId(seg), lost_kind(&kind)?, [x, y], t);
            }
        }
        for (seg, x, y) in held {
            if self.filter.tracks().get(SegId(seg)).is_some() {
                self.filter.held(&self.world, si, SegId(seg), [x, y], t);
            }
        }
        Ok(refound)
    }

    fn has_track(&self, seg: u64) -> bool {
        self.filter.tracks().get(SegId(seg)).is_some()
    }

    /// Whether any of these tracks is live and held lost.
    fn any_lost(&self, segs: Vec<u64>) -> bool {
        segs.into_iter()
            .any(|s| self.filter.tracks().get(SegId(s)).is_some_and(|i| i.lost.is_some()))
    }

    fn live_tracks(&self, si: usize) -> Vec<u64> {
        self.filter.tracks().of(si).iter().map(|s| s.0).collect()
    }

    /// A track ends: (z0, P(ghost), life, born) for what the ghost map learns.
    fn end(&mut self, seg: u64, t: f64, data_lost: bool) -> ((f64, f64), f64, f64, f64) {
        let e = self.filter.end(&self.world, SegId(seg), t, data_lost);
        self.changed();
        ((e.z0[0], e.z0[1]), e.p_ghost, e.life, e.born)
    }

    fn merge(&mut self) {
        self.filter.merge();
        self.changed();
    }

    fn prune(&mut self) -> PyResult<()> {
        self.changed();
        self.filter.prune(&self.world.params).map_err(not_finite)
    }

    #[pyo3(signature = (dropped=0.0))]
    fn normalize(&mut self, dropped: f64) -> PyResult<()> {
        self.changed();
        self.filter.normalize(dropped).map_err(not_finite)
    }

    fn censor(&mut self, si: usize) {
        self.filter.censor(&self.world, si);
        self.changed();
    }

    /// Step 4 of a frame: per track found (seg, x, y, var, map_rate) the children's weights and
    /// what each says.
    #[allow(clippy::too_many_arguments)]
    fn branch_all(
        &mut self,
        si: usize,
        t: f64,
        last: f64,
        censored: bool,
        skip: Vec<u64>,
        prior_rate: f64,
        found: Vec<(u64, f64, f64, f64, f64)>,
    ) -> PyResult<Vec<Vec<(f64, &'static str)>>> {
        let skip: Vec<SegId> = skip.into_iter().map(SegId).collect();
        let frame = Frame {
            si,
            t,
            last,
            censored,
            skip: &skip,
            prior_rate,
        };
        let found: Vec<Found> = found
            .into_iter()
            .map(|(seg, x, y, var, map_rate)| Found {
                track: NewTrack {
                    seg: SegId(seg),
                    z: [x, y],
                    var,
                },
                map_rate,
            })
            .collect();
        self.changed();
        let out = self
            .filter
            .branch_all(&self.world, &frame, &found)
            .map_err(not_finite)?;
        Ok(out
            .into_iter()
            .map(|cs| cs.into_iter().map(|(w, c)| (w, c.name())).collect())
            .collect())
    }

    fn recycle(&mut self) -> bool {
        self.changed();
        self.filter.recycle(self.world.params.give_up)
    }

    fn predict_gap(&mut self, gap: f64, placed: Vec<usize>) {
        let placed: SensorSet = placed.into_iter().collect();
        self.filter.predict_gap(&self.world, &self.observed, placed, gap);
        self.changed();
    }

    fn hyp_weights(&self) -> Vec<f64> {
        self.filter.hyp_weights()
    }

    /// The bookkeeping's invariants (Filter::check): ValueError with what is wrong.
    fn check(&self) -> PyResult<()> {
        self.filter.check().map_err(PyValueError::new_err)
    }

    /// What is known about the people (Filter::people_state): per hypothesis (logw, people,
    /// unknown) and the densities (walk, still, region, out, clock, r, amp, keep).
    #[allow(clippy::type_complexity)]
    fn people_state<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<(Vec<(f64, Vec<usize>, usize)>, Vec<Bound<'py, PyAny>>)> {
        let state = self.filter.people_state(&self.world);
        let shape = self.filter.objects().densities.shape();
        let densities = state
            .densities
            .iter()
            .map(|d| Self::density_out(py, d, &shape))
            .collect::<PyResult<_>>()?;
        Ok((state.hyps, densities))
    }

    /// The hypotheses and their objects for the shell's mirror: (densities, mixtures, hyps, tracks).
    #[allow(clippy::type_complexity)]
    fn dump<'py>(
        &self,
        py: Python<'py>,
    ) -> PyResult<(
        Vec<Bound<'py, PyAny>>,
        Vec<Bound<'py, PyAny>>,
        Vec<Bound<'py, PyAny>>,
        Vec<Bound<'py, PyAny>>,
    )> {
        let objects = self.filter.objects();
        let shape = objects.densities.shape();
        let mut densities = Vec::new();
        for &d in objects.densities.ids() {
            let data = objects.densities.data(d);
            let intensity = data.kind == Kind::Intensity;
            let out = Self::density_out(py, &data, &shape)?;
            densities.push((d.index(), out, intensity).into_pyobject(py)?.into_any());
        }
        let (levels, amps) = (shape.levels, shape.amps);
        let mut mixtures = Vec::new();
        for m in objects.mixtures.ids() {
            let mix = objects.mixtures.get(m);
            let d = mix.dim();
            let mean: Vec<f64> = mix.modes.iter().flat_map(|k| k.mean.iter().copied()).collect();
            let cov: Vec<f64> = mix.modes.iter().flat_map(|k| k.cov.iter().copied()).collect();
            let slots: Vec<(u64, usize, f64)> = mix.slots.iter().map(|(s, sl)| (s.0, sl.index, sl.var)).collect();
            mixtures.push(
                (
                    m.index(),
                    array(py, &mix.logw, &[2])?,
                    array(py, &mean, &[2, 2, d])?,
                    array(py, &cov, &[2, 2, d, d])?,
                    array(py, &mix.stay, &[mix.stay.len()])?,
                    array(py, &mix.ka, &[levels, amps])?,
                    slots,
                    mix.phantom,
                    mix.a,
                    mix.away.map(|a| a.index()),
                    mix.t,
                )
                    .into_pyobject(py)?
                    .into_any(),
            );
        }
        let mut hyps = Vec::new();
        for hy in self.filter.hyps() {
            let tracks: Vec<(u64, Option<u64>)> = hy
                .tracks
                .iter()
                .map(|(s, o)| {
                    let owner = match o {
                        Owner::Person(g) => Some(g.0),
                        Owner::Ghost => None,
                    };
                    (s.0, owner)
                })
                .collect();
            let mut ghosts = Vec::new();
            for (s, g) in hy.ghosts.iter() {
                ghosts.push((s.0, array(py, &g.life, &[g.life.len()])?, g.source.index()));
            }
            let people: Vec<(u64, usize)> = hy.people.iter().map(|(g, m)| (g.0, m.index())).collect();
            let hidden: Vec<usize> = hy.hidden.iter().map(|d| d.index()).collect();
            hyps.push(
                (hy.logw, tracks, ghosts, people, hidden, hy.unknown.index())
                    .into_pyobject(py)?
                    .into_any(),
            );
        }
        let mut tracks = Vec::new();
        for (s, i) in self.filter.tracks().iter() {
            let lost = i.lost.map(|l| (kind_name(l.kind), l.t, (l.z[0], l.z[1]), l.u));
            tracks.push(
                (
                    s.0,
                    i.si,
                    (i.t, i.zt, i.gt, i.var),
                    (i.z[0], i.z[1]),
                    (i.z0[0], i.z0[1]),
                    i.born,
                    lost,
                    i.map_odds,
                )
                    .into_pyobject(py)?
                    .into_any(),
            );
        }
        Ok((densities, mixtures, hyps, tracks))
    }

    /// The hypotheses from the shell's mirror (changed by the LD2410C, or by tests): objects with
    /// an id are overwritten, those without one are made; what nobody holds afterwards is released.
    fn load(
        &mut self,
        densities: Vec<DensityIn<'_>>,
        mixtures: Vec<MixtureIn<'_>>,
        hyps: Vec<HypIn<'_>>,
    ) -> PyResult<()> {
        let shape = self.filter.objects().densities.shape();
        let objects = self.filter.objects_mut();
        let mut dids = Vec::with_capacity(densities.len());
        for (id, walk, still, region, out, clock, r, intensity, amp, keep) in &densities {
            let kind = if *intensity { Kind::Intensity } else { Kind::Person };
            let data = DensityData {
                walk: floats_of(walk)?,
                still: floats_of(still)?,
                region: floats_of(region)?,
                out: *out,
                clock: *clock,
                r: *r,
                kind,
                amp: match amp {
                    Some(levels) => Some(Amp {
                        levels: floats_of(levels)?,
                        keep: keep.as_ref().map(floats_of).transpose()?,
                    }),
                    None => None,
                },
            };
            if data.walk.len() != shape.tiles || data.still.len() != shape.still_len() {
                return Err(PyValueError::new_err("a density of other tiles"));
            }
            let live = id
                .map(|i| DensityId::from_index(i as usize))
                .filter(|&d| objects.densities.contains(d) && objects.densities.get(d).kind == kind);
            dids.push(match live {
                Some(d) => {
                    objects.densities.set(d, data);
                    d
                }
                None => objects.densities.insert(data),
            });
        }
        let mut mids = Vec::with_capacity(mixtures.len());
        for (id, logw, mean, cov, stay, ka, slots, phantom, a, away, t) in &mixtures {
            let (mean, cov, logw) = (mean.as_slice()?, cov.as_slice()?, logw.as_slice()?);
            let d = mean.len() / 4;
            let mode = |k: usize| Kalman {
                mean: mean[k * 2 * d..][..2 * d].to_vec(),
                cov: cov[k * 2 * d * d..][..2 * d * d].to_vec(),
            };
            let mix = Mixture {
                logw: [logw[0], logw[1]],
                modes: [mode(0), mode(1)],
                stay: floats_of(stay)?,
                ka: floats_of(ka)?,
                slots: slots
                    .iter()
                    .map(|&(s, index, var)| (SegId(s), Slot { index, var }))
                    .collect(),
                phantom: *phantom,
                a: *a,
                away: away.map(|i| dids[i]),
                t: *t,
            };
            let live = id
                .map(|i| MixtureId::from_index(i as usize))
                .filter(|&m| objects.mixtures.contains(m));
            mids.push(match live {
                Some(m) => {
                    *objects.mixtures.get_mut(m) = mix;
                    m
                }
                None => objects.mixtures.insert(mix),
            });
        }
        let mut new = Vec::with_capacity(hyps.len());
        for (logw, tracks, ghosts, people, hidden, unknown) in hyps {
            let mut hy = Hyp::new(logw, hidden.iter().map(|&i| dids[i]).collect(), dids[unknown]);
            for (s, owner) in tracks {
                hy.tracks
                    .insert(SegId(s), owner.map_or(Owner::Ghost, |g| Owner::Person(GroupId(g))));
            }
            for (s, life, source) in ghosts {
                hy.ghosts.insert(
                    SegId(s),
                    Ghost {
                        life: floats_of(&life)?,
                        source: mids[source],
                    },
                );
            }
            for (g, m) in people {
                hy.people.insert(GroupId(g), mids[m]);
            }
            new.push(hy);
        }
        self.filter.set_hyps(new);
        self.changed();
        Ok(())
    }

    /// Loads the live tracks (the shell's tests), as dump gives them.
    fn set_tracks(&mut self, tracks: Vec<TrackIn>) -> PyResult<()> {
        let flushed = self.filter.flushed();
        self.filter.clear(flushed);
        for (seg, si, (t, zt, gt, var), z, z0, born, lost, map_odds) in tracks {
            let lost = match lost {
                Some((kind, t, z, u)) => Some(Lost {
                    kind: lost_kind(&kind)?,
                    t,
                    z: [z.0, z.1],
                    u,
                }),
                None => None,
            };
            let info = TrackInfo {
                si,
                t,
                zt,
                gt,
                var,
                z: [z.0, z.1],
                z0: [z0.0, z0.1],
                born,
                lost,
                map_odds,
            };
            self.filter.tracks_mut().begin(SegId(seg), info);
        }
        self.changed();
        Ok(())
    }
}
