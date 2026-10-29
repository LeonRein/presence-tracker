//! Rooms from walls for the shell's editor (presence_core::floorplan): the stored form in, the room zones
//! out, as JSON.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;

use presence_core::floorplan::{self, PlanError};
use presence_core::geometry::Point;

#[allow(clippy::needless_pass_by_value)] // map_err hands the error over by value
fn plan_error(e: PlanError) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn json(text: &str) -> PyResult<Value> {
    serde_json::from_str(text).map_err(|e| PyValueError::new_err(format!("no JSON: {e}")))
}

/// The closed areas between the walls (JSON of [{"points": [a, b], "kind"}]): counterclockwise polygons.
#[pyfunction]
pub fn faces(walls: &str) -> PyResult<Vec<Vec<Point>>> {
    Ok(floorplan::faces(
        &floorplan::walls_of(&json(walls)?).map_err(plan_error)?,
    ))
}

/// The zones (JSON list) with the room zones for the walls (JSON): floorplan::sync_rooms, new rooms with the
/// id null. ValueError for a wall without two points or a room without an id.
#[pyfunction]
pub fn sync_rooms(zones: &str, walls: &str) -> PyResult<String> {
    let walls = floorplan::walls_of(&json(walls)?).map_err(plan_error)?;
    let zones = json(zones)?;
    let zones = zones
        .as_array()
        .ok_or_else(|| PyValueError::new_err("the zones are no list"))?;
    let synced = floorplan::sync_rooms(zones, &walls).map_err(plan_error)?;
    Ok(Value::Array(synced).to_string())
}
