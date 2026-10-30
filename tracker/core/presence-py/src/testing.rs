//! Hooks for the shell's tests only (feature "testing", never in the image): the world as arrays and the rule
//! of reflections for test_core_build.py and its fixtures, a panic to test that the app starts the model over.

use numpy::{PyArray1, PyArrayDyn, PyArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::config::config_from;
use crate::core::world_of;
use presence_core::geometry::Point;

fn array<'py>(py: Python<'py>, v: &[f64], shape: &[usize]) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    PyArray1::from_slice(py, v).reshape(shape)
}

/// Panics with this message: lets the shell's tests check that a panic of the core starts the model
/// over like any other error of the model.
#[pyfunction]
pub fn panic_for_test(message: &str) {
    panic!("{message}");
}

/// The world the core builds of a configuration and model (JSON) as a dict in the format of the Python
/// tracker's `_export()` of 0.38.1, with the plan: the aid that checks the build (test_core_build.py,
/// tests/fixtures/make/core_build.py).
#[pyfunction]
pub fn build_export<'py>(py: Python<'py>, config: &str, model: &str) -> PyResult<Bound<'py, PyDict>> {
    let w = world_of(py, config, model)?;
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
    // per cell its observed room, the number of rooms for none (as the Python tracker exports it)
    let none = w.plan.rooms.len() as f64;
    let rooms: Vec<f64> = w.rooms.cells().iter().map(|r| r.map_or(none, |k| k.0 as f64)).collect();
    d.set_item("rooms", a(&rooms)?)?;
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
        t.room.iter().map(|r| r.map_or(-1, |k| k.0 as i64)).collect::<Vec<_>>(),
    )?;
    let sensors = PyList::empty(py);
    for (s, ld) in w.sights.iter().zip(&w.ld) {
        let e = PyDict::new(py);
        e.set_item("grid", a(s.grid.values())?)?;
        e.set_item("observed", a(&s.observed)?)?;
        e.set_item("ld_los", a(ld.los.values())?)?;
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
            .map(|r| {
                let rooms: Vec<&str> = r.rooms.iter().map(|z| p.zones[z.0].id.as_str()).collect();
                (r.id.as_str(), r.name.as_str(), rooms, r.exits)
            })
            .collect::<Vec<_>>(),
    )?;
    d.set_item(
        "observed_rooms",
        p.observed().map(|r| r.id.as_str()).collect::<Vec<_>>(),
    )?;
    d.set_item(
        "own_rooms",
        p.sensors
            .iter()
            .map(|s| s.own_room.map(|k| p.room(k).id.as_str()))
            .collect::<Vec<_>>(),
    )?;
    d.set_item(
        "portals",
        p.portals
            .iter()
            .map(|q| (q.place.index(p.regions.len()), q.watch[0], q.watch[1]))
            .collect::<Vec<_>>(),
    )?;
    d.set_item("closed_rooms", p.closed.len())?;
    d.set_item("walk_end", a(w.tiles.walk_end(&w.params))?)?;
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
        .map(|(&p, &u)| plan.hidden(s.sight_origin(), p, u, margin))
        .collect())
}
