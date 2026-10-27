//! The filter of the core behind the Python tracker (docs/rust-core.md 7): Rust owns the hypotheses
//! and their objects and computes the outputs; the world still comes from the Python build
//! (WorldExport), and the shell orders the steps of a frame. What the shell and its tools still read
//! of the hypotheses comes as a mirror (dump, read only). The mirror and the world's export are the
//! throwaway part.

use numpy::{PyArray1, PyArrayDyn, PyArrayMethods, PyReadonlyArrayDyn};
use pyo3::exceptions::{PyFloatingPointError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use presence_core::config::Config;
use presence_core::filter::branch::{Found, Frame};
use presence_core::filter::densities::{Amp, DensityData, DensityShape, Kind};
use presence_core::filter::hyp::Owner;
use presence_core::filter::mixture::{NewTrack, SegId};
use presence_core::filter::pool::PoolId;
use presence_core::filter::{NotFinite, PeopleState, Start};
use presence_core::geometry::Point;
use presence_core::ld2410::CELLS;
use presence_core::ld2410::sensor::LdCollect;
use presence_core::outputs::Source;
use presence_core::params::{LostKind, Params, Shapes};
use presence_core::sensors::SensorSet;
use presence_core::tracker::{TrackEvents, Tracker};
use presence_core::world::{DoorTile, LdExport, LdTiles, Raster, SensorExport, Tiles, World, WorldExport};

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
    a.as_slice()?
        .iter()
        .map(|&i| usize::try_from(i).map_err(|_| PyValueError::new_err(format!("{key}: a negative index"))))
        .collect()
}

fn number<'py, T: FromPyObjectOwned<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<T> {
    item(d, key)?.extract().map_err(Into::into)
}

fn points(v: &[f64]) -> Vec<[f64; 2]> {
    v.as_chunks::<2>().0.to_vec()
}

/// Indices, -1 for none.
fn ints_or_none(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<Option<usize>>> {
    let a: PyReadonlyArrayDyn<'_, i64> = item(d, key)?.extract()?;
    Ok(a.as_slice()?.iter().map(|&k| usize::try_from(k).ok()).collect())
}

/// The configuration (the JSON of model.Config.to_dict()).
fn config_from(json: &str) -> PyResult<Config> {
    Config::from_json(json).map_err(PyValueError::new_err)
}

/// The model (the JSON of filtermodel.Model's fields and filter.py's constants).
fn params_from(json: &str) -> PyResult<Params> {
    Params::from_json(json).map_err(|err| PyValueError::new_err(err.to_string()))
}

/// The world: built here from the configuration and the model, or (the aid that checks the build,
/// docs/rust-core.md 6) as the Python tracker built it.
fn world_of(config: &str, model: &str, silent_after: f64, export: Option<&Bound<'_, PyDict>>) -> PyResult<World> {
    let config = config_from(config)?;
    match export {
        None => World::build(&config, params_from(model)?, silent_after).map_err(PyValueError::new_err),
        Some(e) => World::from_export(export_from(e)?, &config, silent_after).map_err(PyValueError::new_err),
    }
}

/// The world as the Python tracker built it (tiling.Tiling, filter._grid, filtermodel).
fn export_from(e: &Bound<'_, PyDict>) -> PyResult<WorldExport> {
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
        room: ints_or_none(e, "tile_room")?,
        region_start: floats(e, "region_start")?,
        generator: floats(e, "generator")?,
    };
    let sensors = item(e, "sensors")?
        .try_iter()?
        .map(|d| sensor_from(&d?.cast_into::<PyDict>()?))
        .collect::<PyResult<_>>()?;
    let export = WorldExport {
        params,
        shapes,
        raster,
        walls: floats(e, "walls")?.as_chunks::<4>().0.to_vec(),
        wall_dist: floats(e, "wall_dist")?,
        tiles,
        sensors,
        rooms: floats(e, "rooms")?,
    };
    Ok(export)
}

/// A sensor of the world's export: where it is and its sight; its LD2410C's axis, height above a
/// person, line of sight, measured view of the rooms and tiles.
fn sensor_from(d: &Bound<'_, PyDict>) -> PyResult<SensorExport> {
    let pair = |key: &str| -> PyResult<[f64; 2]> {
        let (x, y): (f64, f64) = number(d, key)?;
        Ok([x, y])
    };
    let rows = |key: &str| -> PyResult<Vec<[f64; CELLS]>> {
        let v = floats(d, key)?;
        Ok(v.as_chunks::<CELLS>().0.to_vec())
    };
    let view = match item(d, "ld_view")? {
        v if v.is_none() => None,
        v => Some(v.extract::<PyReadonlyArrayDyn<'_, f64>>()?.as_slice()?.to_vec()),
    };
    Ok(SensorExport {
        at: pair("at")?,
        origin: pair("origin")?,
        grid: floats(d, "grid")?,
        observed: floats(d, "observed")?,
        ld: LdExport {
            axis: pair("ld_axis")?,
            dh2: number(d, "ld_dh2")?,
            los: floats(d, "ld_los")?,
            view,
            tiles: LdTiles {
                idx: ints(d, "ld_idx")?,
                still: rows("ld_still")?,
                walk: rows("ld_walk")?,
            },
        },
    })
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

/// A density of a saved state as the shell decoded it (hidden.Hidden.from_dict): walk, still, region,
/// out, r, an intensity (the unknown ones), amp.
type DensityIn<'py> = (
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    f64,
    f64,
    bool,
    Option<PyReadonlyArrayDyn<'py, f64>>,
);

fn density_in(d: &DensityIn<'_>) -> PyResult<DensityData> {
    let (walk, still, region, out, r, intensity, amp) = d;
    let amp = match amp {
        Some(levels) => Some(Amp {
            levels: floats_of(levels)?,
            keep: None,
        }),
        None => None,
    };
    Ok(DensityData {
        walk: floats_of(walk)?,
        still: floats_of(still)?,
        region: floats_of(region)?,
        out: *out,
        clock: 0.0,
        r: *r,
        kind: if *intensity { Kind::Intensity } else { Kind::Person },
        amp,
    })
}

fn source_name(source: Source) -> &'static str {
    match source {
        Source::Filter => "filter",
        Source::Ld2450 => "ld2450",
        Source::Both => "beide",
    }
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
    /// A tracker in the world of a configuration and model (JSON); `silent_after`: s until a room's
    /// own sensor counts as gone. With `export`: in the world the Python tracker built (the check aid).
    #[new]
    #[pyo3(signature = (config, model, silent_after, export=None))]
    fn new(
        py: Python<'_>,
        config: &str,
        model: &str,
        silent_after: f64,
        export: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let world = match export {
            Some(_) => world_of(config, model, silent_after, export)?,
            None => py.detach(|| {
                World::build(&config_from(config)?, params_from(model)?, silent_after).map_err(PyValueError::new_err)
            })?,
        };
        Ok(Self {
            tracker: Tracker::new(world),
            serial: 0,
        })
    }

    /// A world rebuilt on the same tiles (sensors recalibrated, outputs' numbers changed): what is
    /// known stays.
    #[pyo3(signature = (config, model, silent_after, export=None))]
    fn set_world(
        &mut self,
        config: &str,
        model: &str,
        silent_after: f64,
        export: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<()> {
        self.tracker.set_world(world_of(config, model, silent_after, export)?);
        self.changed();
        Ok(())
    }

    /// (tiles, regions, ages) of the world: the shell's own tiles must be the same.
    fn shape(&self) -> (usize, usize, usize) {
        let t = &self.tracker.world().tiles;
        (t.n, t.regions, t.ages)
    }

    /// Per sensor whether its frames count and when its last frame came.
    fn set_sensors(&mut self, sensors: Vec<(bool, f64)>) {
        self.tracker.set_sensors(&sensors);
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

    /// The energies of a frame of sensor si's LD2410C (move_gates, still_gates; None: none in it),
    /// and whether the housing's LD2450 has something in sight.
    fn ld_frame(&mut self, si: usize, t: f64, gates: Option<([f64; 9], [f64; 9])>, target: bool) {
        self.tracker
            .ld_frame(si, t, gates.as_ref().map(|(m, s)| (m, s)), target);
        self.changed();
    }

    /// The energies of sensor si's last LD2410C frame that counted (16), or None.
    fn ld_last<'py>(&self, py: Python<'py>, si: usize) -> PyResult<Option<Out<'py>>> {
        self.tracker
            .ld_collect(si)
            .last
            .map(|e| array(py, &e, &[CELLS]))
            .transpose()
    }

    /// What sensor si's LD2410C frames left: (t, last energies, target_t).
    fn ld_collect(&self, si: usize) -> (f64, Option<Vec<f64>>, f64) {
        let c = self.tracker.ld_collect(si);
        (c.t, c.last.map(|e| e.to_vec()), c.target_t)
    }

    fn set_ld_collect(&mut self, si: usize, collect: (f64, Option<[f64; CELLS]>, f64)) {
        let (t, last, target_t) = collect;
        self.tracker.set_ld_collect(si, LdCollect { t, last, target_t });
    }

    /// The sensors whose LD2410C has gathered long enough to be weighed.
    fn ld_due(&self) -> Vec<usize> {
        self.tracker.ld_due()
    }

    /// Weighs by the LD2410C si's frames given its background b and its echo sources' rate; what the
    /// background learns (mus (H x 16), prior_w, nobody, echo_off, began, the frames' time, t_e,
    /// t_cens and t_target, alpha, target_t), or None if there were no frames.
    fn ld_weigh<'py>(
        &mut self,
        py: Python<'py>,
        si: usize,
        b: [f64; CELLS],
        rate: f64,
    ) -> PyResult<Option<Bound<'py, PyDict>>> {
        self.changed();
        let Some(l) = self.tracker.ld_weigh(si, &b, rate).map_err(not_finite)? else {
            return Ok(None);
        };
        let mus: Vec<f64> = l.mus.iter().flatten().copied().collect();
        let st = &l.stats;
        let out = PyDict::new(py);
        out.set_item("mus", array(py, &mus, &[l.mus.len(), CELLS])?)?;
        out.set_item("prior_w", array(py, &l.prior_w, &[l.prior_w.len()])?)?;
        out.set_item("nobody", l.nobody)?;
        out.set_item("echo_off", l.echo_off)?;
        out.set_item("began", l.began)?;
        out.set_item("time", st.time)?;
        out.set_item("t_e", array(py, &st.t_e, &[CELLS])?)?;
        out.set_item("t_cens", array(py, &st.t_cens, &[CELLS])?)?;
        out.set_item("t_target", st.t_target)?;
        out.set_item("alpha", array(py, &self.tracker.world().ld_lik.alpha, &[CELLS])?)?;
        out.set_item("target_t", l.target_t)?;
        Ok(Some(out))
    }

    /// The LD2410C of sensor si starts over (recalibrated).
    fn ld_reset(&mut self, si: usize) {
        self.tracker.ld_reset(si);
        self.changed();
    }

    // ---------------------------------------------------------------- starts

    /// Start over at t (Tracker::reset_people): nothing known (people None), or known people at
    /// these places (a place's index, None: anywhere) and nobody unknown; ValueError for a place there
    /// is not.
    #[pyo3(signature = (t, people=None))]
    fn reset_people(&mut self, t: f64, people: Option<Vec<Option<usize>>>) -> PyResult<()> {
        let people: Option<Vec<Start>> =
            people.map(|v| v.into_iter().map(|p| p.map_or(Start::Anywhere, Start::Place)).collect());
        self.tracker
            .reset_people(t, people.as_deref())
            .map_err(PyValueError::new_err)?;
        self.changed();
        Ok(())
    }

    /// Start over at t from a saved state the shell decoded (Tracker::restore): the densities and per
    /// hypothesis (logw, people, unknown) as indices into them. What does not fit (None: it fits; then
    /// nothing changed).
    fn restore(
        &mut self,
        t: f64,
        densities: Vec<DensityIn<'_>>,
        hyps: Vec<(f64, Vec<usize>, usize)>,
    ) -> PyResult<Option<String>> {
        let densities = densities.iter().map(density_in).collect::<PyResult<_>>()?;
        let refused = self.tracker.restore(t, PeopleState { hyps, densities }).err();
        self.changed();
        Ok(refused)
    }

    /// Every hypothesis' weight NaN: the shell's test of a failing model.
    fn spoil_weights_for_test(&mut self) {
        self.tracker.spoil_weights_for_test();
        self.changed();
    }

    /// What the rooms' own LD2450s saw in an earlier core goes on here (the shell's rebuild).
    fn adopt_seen(&mut self, mut old: PyRefMut<'_, Core>) {
        let seen = old.tracker.take_seen();
        self.tracker.adopt_seen(seen);
    }

    // ---------------------------------------------------------------- outputs (MODEL.md 6)

    /// Per observed room the distribution of the number of people [P(0), P(1), ...], then of those
    /// in view at all.
    fn count_distribution<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let tracker = &mut self.tracker;
        let rows = py.detach(|| tracker.count_distribution());
        Ok(rows.into_pyobject(py)?.into_any())
    }

    /// Per place (observed, regions..., outside) the distribution of the number of people, then of
    /// those in the house.
    fn place_distribution<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let tracker = &mut self.tracker;
        let rows = py.detach(|| tracker.place_distribution());
        Ok(rows.into_pyobject(py)?.into_any())
    }

    /// Per zone (the observed rooms, then the areas) "wird betreten": (p, distance, eta, person).
    #[allow(clippy::type_complexity)]
    fn entering(&mut self, py: Python<'_>) -> Vec<(f64, Option<f64>, Option<f64>, Option<usize>)> {
        let tracker = &mut self.tracker;
        py.detach(|| tracker.entering())
            .iter()
            .map(|e| (e.p, e.distance, e.eta, e.person))
            .collect()
    }

    /// Per observed room what decided it occupied at now ("filter", "ld2450", "beide"), None: not.
    fn occupancy(&mut self, now: f64) -> Vec<Option<&'static str>> {
        self.tracker
            .occupancy(now)
            .into_iter()
            .map(|s| s.map(source_name))
            .collect()
    }

    /// The people of the display: (id, P per place, lost, r, (x, y, vx, vy, sigma, walk) or None).
    #[allow(clippy::type_complexity)]
    fn persons(&self) -> Vec<(usize, Vec<f64>, bool, f64, Option<(f64, f64, f64, f64, f64, f64)>)> {
        self.tracker
            .persons()
            .into_iter()
            .map(|p| {
                let shown = p
                    .shown
                    .map(|s| (s.at[0], s.at[1], s.velocity[0], s.velocity[1], s.sigma, s.walk));
                (p.id, p.places, p.lost, p.r, shown)
            })
            .collect()
    }

    /// Where they may be: (id, [[weight, x, y, sd x, sd y]], [(tile, mass per m^2)]).
    #[allow(clippy::type_complexity)]
    fn clouds(&self) -> Vec<(usize, Vec<[f64; 5]>, Vec<(usize, f64)>)> {
        self.tracker
            .clouds()
            .into_iter()
            .map(|c| (c.id, c.gauss, c.tiles))
            .collect()
    }

    /// The people with tracks walking with P(walking) >= ENTER_MIN_WALK, (P(walking), x, y, vx, vy); per
    /// hypothesis the walkers it holds with their place among its people; the most probable hypothesis.
    #[allow(clippy::type_complexity)]
    fn walkers(&self) -> (Vec<(f64, f64, f64, f64, f64)>, Vec<Vec<(usize, usize)>>, usize) {
        let found = self.tracker.walkers();
        let walkers = found
            .walkers
            .iter()
            .map(|w| (w.walking, w.at[0], w.at[1], w.velocity[0], w.velocity[1]))
            .collect();
        let held = found
            .held
            .iter()
            .map(|h| h.iter().map(|x| (x.walker, x.place)).collect())
            .collect();
        (walkers, held, found.best)
    }

    /// The thresholds of the outputs: of P(somebody there) for occupied, of p_enter for "wird betreten".
    fn thresholds(&self) -> (f64, f64) {
        let o = &self.tracker.world().outputs;
        (o.occupied_above(), o.entering_above())
    }

    /// Per zone of the home (in the plan's order: the configuration's) its id and numbers - count,
    /// probability, decided (None: no decision; else (occupied, source)), moving, still, entering
    /// (p, distance, eta, person), approaching, available -, and the house's (count, moving, still).
    #[allow(clippy::type_complexity)]
    fn zone_numbers<'py>(
        &mut self,
        py: Python<'py>,
        now: f64,
        start: Option<f64>,
    ) -> PyResult<(Bound<'py, PyList>, (usize, usize, usize))> {
        let tracker = &mut self.tracker;
        let (zones, house) = py.detach(|| tracker.zone_numbers(now, start));
        let out = PyList::empty(py);
        for (z, n) in self.tracker.world().plan.zones.iter().zip(&zones) {
            let decided = n.decided.map(|s| (s.is_some(), s.map(source_name)));
            let entering = n.entering.map(|e| (e.p, e.distance, e.eta, e.person));
            out.append((
                z.id.as_str(),
                n.count,
                n.probability,
                decided,
                n.moving,
                n.still,
                entering,
                n.approaching,
                n.available,
            ))?;
        }
        Ok((out, (house.count, house.moving, house.still)))
    }

    /// A frame of the sensor with this id at t, whatever is in it.
    fn heard_from(&mut self, id: &str, t: f64) {
        self.tracker.heard_from(id, t);
    }

    /// The targets sensor si measured in a frame at t (not held, not hidden, not at the mount).
    fn seen_frame(&mut self, si: usize, t: f64, measured: Vec<(f64, f64)>) {
        let measured: Vec<Point> = measured.into_iter().map(|(x, y)| [x, y]).collect();
        self.tracker.seen_frame(si, t, &measured);
    }

    /// Whether the sensor with this id sent a frame lately (start: the model's, None: not started).
    fn online(&self, id: &str, now: f64, start: Option<f64>) -> bool {
        self.tracker.online(id, now, start)
    }

    /// Per observed room whether its own sensors in use all went silent.
    fn silent(&self, now: f64, start: Option<f64>) -> Vec<bool> {
        self.tracker.silent(now, start)
    }

    /// The names of the plan: the observed rooms, the places (observed, regions, outside), the areas,
    /// per sensor its own room (None: none).
    #[allow(clippy::type_complexity)]
    fn names(&self) -> (Vec<String>, Vec<String>, Vec<String>, Vec<Option<String>>) {
        let p = &self.tracker.world().plan;
        let places = std::iter::once("observed".to_string())
            .chain(p.regions.iter().map(|r| r.id.clone()))
            .chain(["outside".to_string()])
            .collect();
        let areas = p
            .zones
            .iter()
            .filter(|z| z.kind == presence_core::config::ZoneKind::Area)
            .map(|z| z.id.clone())
            .collect();
        let own = p
            .sensors
            .iter()
            .map(|s| s.own_room.map(|k| p.rooms[k].id.clone()))
            .collect();
        (p.rooms.iter().map(|r| r.id.clone()).collect(), places, areas, own)
    }

    /// Per room id the last time its own LD2450 measured somebody in it.
    fn seen_last(&self) -> Vec<(String, f64)> {
        self.tracker.seen().last().to_vec()
    }

    /// Per observed room whether it has an own sensor in use.
    fn watched(&self) -> Vec<bool> {
        self.tracker.watched()
    }
}

/// The world the core builds of a configuration and model (JSON), as a dict in the format of the
/// Python tracker's `_export()` (and the plan): for the aid that checks the build against Python's
/// (rust-port/cmpworld.py).
#[pyfunction]
pub fn build_export<'py>(
    py: Python<'py>,
    config: &str,
    model: &str,
    silent_after: f64,
) -> PyResult<Bound<'py, PyDict>> {
    let w = world_of(config, model, silent_after, None)?;
    let (t, sh) = (&w.tiles, &w.shapes);
    let flat = |v: &[[f64; 2]]| -> Vec<f64> { v.iter().flatten().copied().collect() };
    let d = PyDict::new(py);
    let a = |v: &[f64]| array(py, v, &[v.len()]);
    d.set_item("x0", w.raster.x0)?;
    d.set_item("y0", w.raster.y0)?;
    d.set_item("nx", w.raster.nx)?;
    d.set_item("ny", w.raster.ny)?;
    d.set_item(
        "walls",
        a(&w.plan
            .walls
            .iter()
            .flat_map(|s| [s[0][0], s[0][1], s[1][0], s[1][1]])
            .collect::<Vec<_>>())?,
    )?;
    d.set_item("wall_dist", a(w.wall_dist.values())?)?;
    d.set_item("rooms", a(w.rooms.values())?)?;
    for (k, v) in [
        ("go", &sh.go),
        ("go_w", &sh.go_w),
        ("go_w_ongoing", &sh.go_w_ongoing),
        ("kappa", &sh.kappa),
        ("kappa_w", &sh.kappa_w),
        ("amp", &sh.amp),
        ("ka_w", &sh.ka_w),
        ("amp_given_kappa", &sh.amp_given_kappa),
    ] {
        d.set_item(k, a(v)?)?;
    }
    d.set_item("n", t.n)?;
    d.set_item("R", t.regions)?;
    d.set_item("A", t.ages)?;
    d.set_item("centers", a(&flat(&t.centers))?)?;
    d.set_item("tile_var", a(&flat(&t.var))?)?;
    d.set_item("area", a(&t.area)?)?;
    d.set_item("fine", t.fine.clone())?;
    d.set_item("walk_t", a(&t.walk_t)?)?;
    d.set_item("into_t", a(&t.into_t)?)?;
    d.set_item("generator", a(&t.generator)?)?;
    d.set_item(
        "door_rates",
        a(&t.door_tiles
            .iter()
            .flat_map(|x| x.rates.iter().copied())
            .collect::<Vec<_>>())?,
    )?;
    d.set_item("region_doors", t.region_doors.clone())?;
    d.set_item("exits", t.exits.clone())?;
    d.set_item("entries", t.entries.clone())?;
    d.set_item("region_rates", a(&t.region_rates)?)?;
    d.set_item("region_start", a(&t.region_start)?)?;
    d.set_item("widths", a(&t.widths)?)?;
    d.set_item("points", a(&flat(&t.points))?)?;
    d.set_item("point_tile", t.point_tile.clone())?;
    d.set_item(
        "tile_room",
        t.room.iter().map(|r| r.map_or(-1, |k| k as i64)).collect::<Vec<_>>(),
    )?;
    let sensors = PyList::empty(py);
    for (s, ld) in w.sights.iter().zip(&w.ld) {
        let e = PyDict::new(py);
        e.set_item("grid", a(s.grid.values())?)?;
        e.set_item("observed", a(&s.observed)?)?;
        e.set_item("ld_los", a(ld.los.values())?)?;
        e.set_item("ld_view", ld.view.as_ref().map(|v| a(v)).transpose()?)?;
        e.set_item("ld_idx", ld.tiles.idx.clone())?;
        e.set_item(
            "ld_still",
            a(&ld.tiles.still.iter().flatten().copied().collect::<Vec<_>>())?,
        )?;
        e.set_item(
            "ld_walk",
            a(&ld.tiles.walk.iter().flatten().copied().collect::<Vec<_>>())?,
        )?;
        sensors.append(e)?;
    }
    d.set_item("sensors", sensors)?;
    let p = &w.plan;
    d.set_item(
        "regions",
        p.regions
            .iter()
            .map(|r| (r.id.clone(), r.name.clone(), r.rooms.clone(), r.exits))
            .collect::<Vec<_>>(),
    )?;
    d.set_item(
        "observed_rooms",
        p.rooms.iter().map(|r| r.id.clone()).collect::<Vec<_>>(),
    )?;
    d.set_item(
        "own_rooms",
        p.sensors
            .iter()
            .map(|s| s.own_room.map(|k| p.rooms[k].id.clone()))
            .collect::<Vec<_>>(),
    )?;
    d.set_item(
        "portals",
        p.portals
            .iter()
            .map(|q| (q.place, q.watch[0], q.watch[1]))
            .collect::<Vec<_>>(),
    )?;
    d.set_item("closed_rooms", p.closed_rooms.len())?;
    Ok(d)
}

/// Per point whether a target of sensor si there is a reflection (Plan::hidden; the aid that checks
/// the plan against model.Config.hidden): `us` the unit vectors from the sensor to the points. (PyO3
/// extracts the arguments as owned values.)
#[allow(clippy::needless_pass_by_value)]
#[pyfunction]
pub fn hidden_at(config: &str, si: usize, points: Vec<Point>, us: Vec<Point>, margin: f64) -> PyResult<Vec<bool>> {
    let config = config_from(config)?;
    let plan = presence_core::plan::Plan::new(&config);
    let s = config
        .sensors
        .get(si)
        .ok_or_else(|| PyValueError::new_err("no such sensor"))?;
    Ok(points
        .iter()
        .zip(&us)
        .map(|(&p, &u)| plan.hidden(&config, s, p, u, margin))
        .collect())
}
