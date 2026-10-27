//! The prototype's glue (docs/rust-core.md 7, phase 2; thrown away in phase 3): the Python filter
//! keeps its objects and copies them into the core for each move of everybody, and back.

use numpy::{PyArray1, PyArrayDyn, PyArrayMethods, PyReadonlyArrayDyn, PyReadwriteArrayDyn, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use presence_core::filter::densities::{Amp, Densities, DensityId, DensityShape, Kind};
use presence_core::filter::mixture::{Kalman, Mixture, MixtureId, SegId, Slot};
use presence_core::filter::pool::Pool;
use presence_core::filter::step::{FlushMixture, Observed, flush_parts};
use presence_core::ld2450::rates::TrackSpot;
use presence_core::params::{Params, Shapes};
use presence_core::sensors::{SensorSet, SensorState};
use presence_core::world::{DoorTile, Raster, Tiles, World, WorldExport};

fn floats(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<f64>> {
    let item = d
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing {key}")))?;
    let a: PyReadonlyArrayDyn<'_, f64> = item.extract()?;
    Ok(a.as_slice()?.to_vec())
}

fn ints(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<usize>> {
    let item = d
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing {key}")))?;
    let a: PyReadonlyArrayDyn<'_, i64> = item.extract()?;
    Ok(a.as_slice()?.iter().map(|&i| i as usize).collect())
}

fn number<'py, T: FromPyObjectOwned<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<T> {
    d.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing {key}")))?
        .extract()
        .map_err(Into::into)
}

fn points(v: &[f64]) -> Vec<[f64; 2]> {
    v.as_chunks::<2>().0.to_vec()
}

/// The world as the Python tracker built it (WorldExport).
#[pyclass(frozen)]
pub struct ProtoWorld {
    world: World,
}

#[pymethods]
impl ProtoWorld {
    #[new]
    fn new(e: &Bound<'_, PyDict>) -> PyResult<Self> {
        let params =
            Params::from_json(&number::<String>(e, "params")?).map_err(|err| PyValueError::new_err(err.to_string()))?;
        let shapes = Shapes {
            go: floats(e, "go")?,
            go_w: floats(e, "go_w")?,
            kappa: floats(e, "kappa")?,
            kappa_w: floats(e, "kappa_w")?,
            ka_w: floats(e, "ka_w")?,
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
        };
        let sensors = number::<Vec<(f64, f64, PyReadonlyArrayDyn<'_, f64>)>>(e, "sensors")?
            .into_iter()
            .map(|(x, y, grid)| Ok(([x, y], grid.as_slice()?.to_vec())))
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
        Ok(Self {
            world: World::from_export(export),
        })
    }
}

type DensityIn<'py> = (
    PyReadwriteArrayDyn<'py, f64>,
    PyReadwriteArrayDyn<'py, f64>,
    PyReadwriteArrayDyn<'py, f64>,
    f64,
    f64,
    f64,
    bool,
    Option<PyReadonlyArrayDyn<'py, f64>>,
    Option<PyReadwriteArrayDyn<'py, f64>>,
);

type MixtureIn<'py> = (
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    PyReadonlyArrayDyn<'py, f64>,
    f64,
    Option<f64>,
    bool,
    Vec<(u64, usize, f64)>,
    Option<usize>,
    Vec<usize>,
);

type Out<'py> = Bound<'py, PyArrayDyn<f64>>;

fn array<'py>(py: Python<'py>, v: &[f64], shape: &[usize]) -> PyResult<Out<'py>> {
    PyArray1::from_slice(py, v).reshape(shape)
}

/// One move of everybody (filter.Tracker._flush_parts) on copies of the Python objects. The
/// densities' arrays are written back in place; returns the log factors per round, per density its
/// out, clock and r, per mixture its new state, and the densities made as parts gone through a door.
#[pyfunction]
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn proto_flush<'py>(
    py: Python<'py>,
    world: &ProtoWorld,
    t0: f64,
    t: f64,
    sensors: Vec<(bool, f64, Vec<(bool, f64, f64)>)>,
    densities: &Bound<'py, PyList>,
    in_view: Vec<usize>,
    mixtures: &Bound<'py, PyList>,
    observed: Vec<(Vec<usize>, PyReadonlyArrayDyn<'py, f64>)>,
) -> PyResult<(
    Vec<(Vec<f64>, Vec<Option<f64>>)>,
    Vec<(f64, f64, f64)>,
    Vec<Bound<'py, PyAny>>,
    Vec<Bound<'py, PyAny>>,
)> {
    let world = &world.world;
    let (tiles, shapes) = (&world.tiles, &world.shapes);
    let shape = DensityShape {
        tiles: tiles.n,
        kinds: shapes.kinds(),
        levels: shapes.levels(),
        regions: tiles.regions,
        ages: tiles.ages,
    };
    let mut store = Densities::new(shape);
    let mut inputs: Vec<DensityIn<'py>> = densities.iter().map(|d| d.extract()).collect::<PyResult<_>>()?;
    let mut ids = Vec::with_capacity(inputs.len());
    for (walk, still, region, out, clock, r, intensity, amp, keep) in &inputs {
        let id = store.alloc(if *intensity { Kind::Intensity } else { Kind::Person });
        let d = store.get_mut(id);
        d.walk.copy_from_slice(walk.as_slice()?);
        d.still.copy_from_slice(still.as_slice()?);
        d.region.copy_from_slice(region.as_slice()?);
        (*d.out, *d.clock, *d.r) = (*out, *clock, *r);
        if let (Some(levels), Some(keep)) = (amp, keep) {
            *d.amp = Some(Amp {
                levels: levels.as_slice()?.to_vec(),
                keep: keep.as_slice()?.to_vec(),
            });
        }
        ids.push(id);
    }
    let mut pool: Pool<Mixture, MixtureId> = Pool::default();
    let mut moving = Vec::new();
    for m in mixtures.iter() {
        let (mean, cov, logw, gow, ka, a, mt, phantom, slots, away, measured): MixtureIn<'py> = m.extract()?;
        let d = mean.shape()[2];
        let (mean, cov) = (mean.as_slice()?, cov.as_slice()?);
        let mode = |k: usize| Kalman {
            mean: mean[k * 2 * d..][..2 * d].to_vec(),
            cov: cov[k * 2 * d * d..][..2 * d * d].to_vec(),
        };
        let logw = logw.as_slice()?;
        let id = pool.insert(Mixture {
            logw: [logw[0], logw[1]],
            modes: [mode(0), mode(1)],
            stay: gow.as_slice()?.to_vec(),
            ka: ka.as_slice()?.to_vec(),
            slots: slots
                .into_iter()
                .map(|(seg, index, var)| (SegId(seg), Slot { index, var }))
                .collect(),
            phantom,
            a,
            away: away.map(|i| ids[i]),
            t: mt,
        });
        moving.push(FlushMixture {
            id,
            measured: measured.into_iter().collect(),
        });
    }
    let states: Vec<SensorState> = sensors
        .into_iter()
        .map(|(active, last_frame, spots)| SensorState {
            active,
            last_frame,
            spots: spots
                .into_iter()
                .map(|(held, x, y)| TrackSpot { held, at: [x, y] })
                .collect(),
        })
        .collect();
    let mut seen = Observed::default();
    for (set, per_tile) in observed {
        seen.insert(set.into_iter().collect::<SensorSet>(), per_tile.as_slice()?.to_vec());
    }
    let in_view: Vec<DensityId> = in_view.into_iter().map(|i| ids[i]).collect();
    let rounds = py.detach(|| flush_parts(world, &states, &seen, &mut store, &mut pool, &in_view, &moving, (t0, t)));

    let mut scalars = Vec::with_capacity(ids.len());
    for (&id, (walk, still, region, _, _, _, _, _, keep)) in ids.iter().zip(&mut inputs) {
        let d = store.get(id);
        walk.as_slice_mut()?.copy_from_slice(d.walk);
        still.as_slice_mut()?.copy_from_slice(d.still);
        region.as_slice_mut()?.copy_from_slice(d.region);
        if let (Some(keep), Some(amp)) = (keep, d.amp) {
            keep.as_slice_mut()?.copy_from_slice(&amp.keep);
        }
        scalars.push((d.out, d.clock, d.r));
    }
    let mut made = Vec::new();
    let mut outs = Vec::with_capacity(moving.len());
    for m in &moving {
        let mix = pool.get(m.id);
        let d = mix.dim();
        let away = match mix.away {
            None => None,
            Some(id) => Some(match ids.iter().position(|&i| i == id) {
                Some(i) => i,
                None => {
                    let p = store.get(id);
                    made.push(
                        (
                            array(py, p.walk, &[p.walk.len()])?,
                            array(py, p.still, &[p.still.len()])?,
                            array(py, p.region, &[p.region.len()])?,
                            p.out,
                            p.clock,
                            p.r,
                        )
                            .into_pyobject(py)?
                            .into_any(),
                    );
                    ids.len() + made.len() - 1
                }
            }),
        };
        let mean: Vec<f64> = mix.modes.iter().flat_map(|k| k.mean.iter().copied()).collect();
        let cov: Vec<f64> = mix.modes.iter().flat_map(|k| k.cov.iter().copied()).collect();
        let amps = mix.ka.len() / shapes.levels();
        outs.push(
            (
                array(py, &mean, &[2, 2, d])?,
                array(py, &cov, &[2, 2, d, d])?,
                array(py, &mix.logw, &[2])?,
                array(py, &mix.stay, &[mix.stay.len()])?,
                array(py, &mix.ka, &[shapes.levels(), amps])?,
                mix.a,
                mix.t,
                away,
            )
                .into_pyobject(py)?
                .into_any(),
        );
    }
    let rounds = rounds.into_iter().map(|r| (r.densities, r.mixtures)).collect();
    Ok((rounds, scalars, outs, made))
}
