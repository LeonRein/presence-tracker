//! The floor-plan editor's geometry for the web UI (presence_core::editor): the configuration and an
//! operation in, the new configuration out, as JSON.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::{Value, json};

use presence_core::editor;

/// The configuration (JSON) after the operation (JSON, editor::apply): JSON of {"config", "points", "select"}
/// ("points" and "select" null if the operation gives none). ValueError with the code ("no_wall", ...) as its
/// only argument if it changes nothing; "bad_op" also for text that is no JSON.
#[pyfunction]
pub fn edit(config: &str, op: &str) -> PyResult<String> {
    let parse = |text: &str| serde_json::from_str::<Value>(text).map_err(|_| PyValueError::new_err("bad_op"));
    let applied = editor::apply(&parse(config)?, &parse(op)?).map_err(|e| PyValueError::new_err(e.code()))?;
    Ok(json!({"config": applied.config, "points": applied.points, "select": applied.select}).to_string())
}
