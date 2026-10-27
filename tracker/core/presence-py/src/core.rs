//! The filter of the core behind the Python tracker (docs/rust-core.md 7, phase 3): Rust owns the
//! hypotheses and their objects; the world still comes from the Python build (WorldExport), and the
//! shell orchestrates a frame, the LD2410C and the outputs on a mirror of the hypotheses
//! (dump/load). The mirror and the world's export are the throwaway part.

use numpy::{PyArray1, PyArrayDyn, PyArrayMethods, PyReadonlyArrayDyn};
use pyo3::exceptions::{PyFloatingPointError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use presence_core::filter::NotFinite;
use presence_core::filter::branch::{Found, Frame};
use presence_core::filter::densities::{Amp, DensityData, DensityId, DensityShape, Kind};
use presence_core::filter::hyp::{GroupId, Owner};
use presence_core::filter::mirror::{MirrorHyp, MirrorMixture, MirrorState};
use presence_core::filter::mixture::{Kalman, Mixture, MixtureId, NewTrack, SegId, Slot};
use presence_core::filter::pool::PoolId;
use presence_core::params::{LostKind, Params, Shapes};
use presence_core::sensors::{SensorSet, SensorState};
use presence_core::tracker::{TrackEvents, Tracker};
use presence_core::world::{DoorTile, Raster, SensorExport, Tiles, World, WorldExport};

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
    type SensorIn<'py> = (
        f64,
        f64,
        f64,
        f64,
        PyReadonlyArrayDyn<'py, f64>,
        PyReadonlyArrayDyn<'py, f64>,
    );
    let sensors = number::<Vec<SensorIn<'_>>>(e, "sensors")?
        .into_iter()
        .map(|(x, y, ox, oy, grid, observed)| {
            Ok(SensorExport {
                at: [x, y],
                origin: [ox, oy],
                grid: grid.as_slice()?.to_vec(),
                observed: observed.as_slice()?.to_vec(),
            })
        })
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

fn density_in(d: &DensityIn<'_>) -> PyResult<(Option<DensityId>, DensityData)> {
    let (id, walk, still, region, out, clock, r, intensity, amp, keep) = d;
    let amp = match amp {
        Some(levels) => Some(Amp {
            levels: floats_of(levels)?,
            keep: keep.as_ref().map(floats_of).transpose()?,
        }),
        None => None,
    };
    let data = DensityData {
        walk: floats_of(walk)?,
        still: floats_of(still)?,
        region: floats_of(region)?,
        out: *out,
        clock: *clock,
        r: *r,
        kind: if *intensity { Kind::Intensity } else { Kind::Person },
        amp,
    };
    Ok((id.map(|i| DensityId::from_index(i as usize)), data))
}

fn mixture_in(m: &MixtureIn<'_>) -> PyResult<MirrorMixture> {
    let (id, logw, mean, cov, stay, ka, slots, phantom, a, away, t) = m;
    let (mean, cov, logw) = (mean.as_slice()?, cov.as_slice()?, logw.as_slice()?);
    if logw.len() != 2 || mean.len() % 4 != 0 || 4 * cov.len() != mean.len() * mean.len() {
        return Err(PyValueError::new_err("a mixture of another shape"));
    }
    let d = mean.len() / 4;
    let mode = |k: usize| Kalman {
        mean: mean[k * 2 * d..][..2 * d].to_vec(),
        cov: cov[k * 2 * d * d..][..2 * d * d].to_vec(),
    };
    let mixture = Mixture {
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
        away: None,
        t: *t,
    };
    Ok(MirrorMixture {
        id: id.map(|i| MixtureId::from_index(i as usize)),
        mixture,
        away: *away,
    })
}

fn hyp_in(h: HypIn<'_>) -> PyResult<MirrorHyp> {
    let (logw, tracks, ghosts, people, hidden, unknown) = h;
    Ok(MirrorHyp {
        logw,
        tracks: tracks.into_iter().map(|(s, g)| (SegId(s), g.map(GroupId))).collect(),
        ghosts: ghosts
            .into_iter()
            .map(|(s, life, source)| Ok((SegId(s), floats_of(&life)?, source)))
            .collect::<PyResult<_>>()?,
        people: people.into_iter().map(|(g, m)| (GroupId(g), m)).collect(),
        hidden,
        unknown,
    })
}

/// The Rust tracker for the Python shell: converts, nothing more (the steps are presence_core's).
#[pyclass(module = "presence_core")]
pub struct Core {
    tracker: Tracker,
    /// counts the calls that changed the tracker: the shell's mirror and outputs are current while
    /// it stays
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
        Ok(Self {
            tracker: Tracker::new(world_from(export)?),
            serial: 0,
        })
    }

    /// A rebuilt world on the same tiles (sensors recalibrated): what is known stays.
    fn set_world(&mut self, export: &Bound<'_, PyDict>) -> PyResult<()> {
        self.tracker.set_world(world_from(export)?);
        self.changed();
        Ok(())
    }

    fn has_walk_end(&self) -> bool {
        !self.tracker.world().tiles.walk_end.is_empty()
    }

    fn set_walk_end(&mut self, walk_end: PyReadonlyArrayDyn<'_, f64>) -> PyResult<()> {
        self.tracker.set_walk_end(floats_of(&walk_end)?);
        Ok(())
    }

    /// Per sensor whether its frames count and when its last frame came.
    fn set_sensors(&mut self, sensors: Vec<(bool, f64)>) {
        let sensors = sensors
            .into_iter()
            .map(|(active, last_frame)| SensorState { active, last_frame })
            .collect();
        self.tracker.set_sensors(sensors);
    }

    #[getter]
    fn serial(&self) -> u64 {
        self.serial
    }

    /// What the outputs are cached by: the moves of everybody and the events that concern all.
    #[getter]
    fn epoch(&self) -> u64 {
        self.tracker.filter().epoch()
    }

    #[getter]
    fn flushed(&self) -> f64 {
        self.tracker.filter().flushed()
    }

    #[setter]
    fn set_flushed(&mut self, t: f64) {
        self.tracker.set_flushed(t);
        self.changed();
    }

    #[getter]
    fn loglik(&self) -> (f64, f64) {
        self.tracker.filter().loglik()
    }

    #[setter]
    fn set_loglik(&mut self, v: (f64, f64)) {
        self.tracker.set_loglik(v);
    }

    fn clear(&mut self, t: f64) {
        self.tracker.clear(t);
        self.changed();
    }

    fn flush(&mut self, py: Python<'_>, t: f64) {
        let tracker = &mut self.tracker;
        py.detach(|| tracker.flush(t));
        self.changed();
    }

    fn event(&mut self, py: Python<'_>, t: f64) {
        let tracker = &mut self.tracker;
        py.detach(|| tracker.event(t));
        self.changed();
    }

    fn watch(&mut self, si: usize, delta: f64, ghost_total: f64) {
        self.tracker.watch(si, delta, ghost_total);
        self.changed();
    }

    /// Step 2 of a frame: measured (seg, x, y), lost (seg, kind, x, y), held (seg, x, y); returns
    /// the tracks found again (seg, x, y).
    fn track_events(
        &mut self,
        si: usize,
        t: f64,
        measured: Vec<(u64, f64, f64)>,
        lost: Vec<(u64, String, f64, f64)>,
        held: Vec<(u64, f64, f64)>,
    ) -> PyResult<Vec<(u64, f64, f64)>> {
        let events = TrackEvents {
            measured: measured.into_iter().map(|(s, x, y)| (SegId(s), [x, y])).collect(),
            lost: lost
                .into_iter()
                .map(|(s, kind, x, y)| Ok((SegId(s), lost_kind(&kind)?, [x, y])))
                .collect::<PyResult<_>>()?,
            held: held.into_iter().map(|(s, x, y)| (SegId(s), [x, y])).collect(),
        };
        let refound = self.tracker.track_events(si, t, &events);
        self.changed();
        Ok(refound.into_iter().map(|(s, [x, y])| (s.0, x, y)).collect())
    }

    fn has_track(&self, seg: u64) -> bool {
        self.tracker.filter().tracks().get(SegId(seg)).is_some()
    }

    /// Whether any of these tracks is live and held lost.
    fn any_lost(&self, segs: Vec<u64>) -> bool {
        let tracks = self.tracker.filter().tracks();
        segs.into_iter()
            .any(|s| tracks.get(SegId(s)).is_some_and(|i| i.lost.is_some()))
    }

    fn live_tracks(&self, si: usize) -> Vec<u64> {
        self.tracker.filter().tracks().of(si).iter().map(|s| s.0).collect()
    }

    /// A track ends: (z0, P(ghost), life, born) for what the ghost map learns; None if not live.
    fn end(&mut self, seg: u64, t: f64, data_lost: bool) -> Option<((f64, f64), f64, f64, f64)> {
        let e = self.tracker.end(SegId(seg), t, data_lost);
        self.changed();
        e.map(|e| ((e.z0[0], e.z0[1]), e.p_ghost, e.life, e.born))
    }

    fn merge(&mut self) {
        self.tracker.merge();
        self.changed();
    }

    fn prune(&mut self) -> PyResult<()> {
        self.changed();
        self.tracker.prune().map_err(not_finite)
    }

    #[pyo3(signature = (dropped=0.0))]
    fn normalize(&mut self, dropped: f64) -> PyResult<()> {
        self.changed();
        self.tracker.normalize(dropped).map_err(not_finite)
    }

    fn censor(&mut self, si: usize) {
        self.tracker.censor(si);
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
        let out = self.tracker.branch_all(&frame, &found).map_err(not_finite)?;
        Ok(out
            .into_iter()
            .map(|cs| cs.into_iter().map(|(w, c)| (w, c.name())).collect())
            .collect())
    }

    fn recycle(&mut self) -> bool {
        self.changed();
        self.tracker.recycle()
    }

    fn predict_gap(&mut self, gap: f64, placed: Vec<usize>) {
        self.tracker.predict_gap(gap, placed.into_iter().collect::<SensorSet>());
        self.changed();
    }

    fn hyp_weights(&self) -> Vec<f64> {
        self.tracker.filter().hyp_weights()
    }

    /// The bookkeeping's invariants (Filter::check): ValueError with what is wrong.
    fn check(&self) -> PyResult<()> {
        self.tracker.filter().check().map_err(PyValueError::new_err)
    }

    /// What is known about the people (Filter::people_state): per hypothesis (logw, people,
    /// unknown) and the densities (walk, still, region, out, clock, r, amp, keep).
    #[allow(clippy::type_complexity)]
    fn people_state<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<(Vec<(f64, Vec<usize>, usize)>, Vec<Bound<'py, PyAny>>)> {
        let state = self.tracker.people_state();
        let shape = self.tracker.world().density_shape();
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
        let filter = self.tracker.filter();
        let objects = filter.objects();
        let shape = objects.densities().shape();
        let mut densities = Vec::new();
        for &d in objects.densities().ids() {
            let data = objects.densities().data(d);
            let intensity = data.kind == Kind::Intensity;
            let out = Self::density_out(py, &data, &shape)?;
            densities.push((d.index(), out, intensity).into_pyobject(py)?.into_any());
        }
        let (levels, amps) = (shape.levels, shape.amps);
        let mut mixtures = Vec::new();
        for m in objects.mixtures().ids() {
            let mix = objects.mixtures().get(m);
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
        for hy in filter.hyps() {
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
        for (s, i) in filter.tracks().iter() {
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

    /// The hypotheses from the shell's mirror (Filter::load): ValueError if they do not fit.
    fn load(
        &mut self,
        densities: Vec<DensityIn<'_>>,
        mixtures: Vec<MixtureIn<'_>>,
        hyps: Vec<HypIn<'_>>,
    ) -> PyResult<()> {
        let state = MirrorState {
            densities: densities.iter().map(density_in).collect::<PyResult<_>>()?,
            mixtures: mixtures.iter().map(mixture_in).collect::<PyResult<_>>()?,
            hyps: hyps.into_iter().map(hyp_in).collect::<PyResult<_>>()?,
        };
        self.changed();
        self.tracker.load(state).map_err(PyValueError::new_err)
    }
}
