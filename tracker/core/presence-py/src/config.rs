//! The configuration at the border (docs/rust-core.md 4): read and checked by the core, its problems handed
//! to the shell by name (presence_core.ConfigError with a ConfigProblem), its limits for the web UI's fields.

use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use presence_core::config::{self, Config, ConfigError, Limits, Part, Problem};
use serde_json::Value;

create_exception!(
    presence_core,
    PyConfigError,
    PyValueError,
    "A configuration the model cannot run with: args[0] is the ConfigProblem (where, what, the limits)."
);

/// The configuration (the JSON of model.Config.to_dict()), checked; ConfigError names the first problem.
pub(crate) fn config_from(json: &str) -> PyResult<Config> {
    Config::from_json(json).map_err(config_error)
}

#[allow(clippy::needless_pass_by_value)] // map_err hands the error over by value
pub(crate) fn config_error(e: ConfigError) -> PyErr {
    PyConfigError::new_err(ConfigProblem(e))
}

/// A problem of a configuration, for the shell to word: part ("config", "params", "threshold", "sensor",
/// "wall", "door", "zone", "ld_view", "layer"), name (the sensor's or zone's name, else its id; the room
/// of a threshold, the wall's number, the pair of ld_view), field, reason, limits ((low, high, low
/// excluded, high excluded)) and the value given (or the id twice, the most allowed, why unreadable).
#[pyclass(module = "presence_core", frozen)]
pub struct ConfigProblem(ConfigError);

#[pymethods]
impl ConfigProblem {
    #[getter]
    fn part(&self) -> &'static str {
        match self.0.part {
            Part::Config => "config",
            Part::Params => "params",
            Part::Threshold(_) => "threshold",
            Part::Sensor(_) => "sensor",
            Part::Wall(_) => "wall",
            Part::Door => "door",
            Part::Zone(_) => "zone",
            Part::LdView(_) => "ld_view",
            Part::Layer => "layer",
        }
    }

    #[getter]
    fn name(&self) -> Option<String> {
        match &self.0.part {
            Part::Threshold(room) => Some(room.clone()),
            Part::Sensor(name) | Part::Zone(name) | Part::LdView(name) => name.clone(),
            Part::Wall(k) => Some(k.to_string()),
            Part::Config | Part::Params | Part::Door | Part::Layer => None,
        }
    }

    #[getter]
    fn field(&self) -> Option<&'static str> {
        self.0.field
    }

    #[getter]
    fn reason(&self) -> &'static str {
        match self.0.problem {
            Problem::NotAnObject => "not_an_object",
            Problem::NotAList => "not_a_list",
            Problem::Empty => "empty",
            Problem::NotANumber(_) => "not_a_number",
            Problem::OutOfRange { .. } => "out_of_range",
            Problem::Negative(_) => "negative",
            Problem::NotABool => "not_a_bool",
            Problem::NoId => "no_id",
            Problem::IdChars => "id_chars",
            Problem::IdTwice(_) => "id_twice",
            Problem::NotPoints => "not_points",
            Problem::NotASegment => "not_a_segment",
            Problem::UnknownKind(_) => "unknown_kind",
            Problem::UnknownShape(_) => "unknown_shape",
            Problem::RectCorners => "rect_corners",
            Problem::PolygonCorners => "polygon_corners",
            Problem::NoCentre => "no_centre",
            Problem::NoPosition => "no_position",
            Problem::TooManySensors { .. } => "too_many_sensors",
            Problem::TooLarge { .. } => "too_large",
            Problem::Unreadable(_) => "unreadable",
        }
    }

    #[getter]
    fn limits(&self) -> Option<(f64, f64, bool, bool)> {
        match self.0.problem {
            Problem::OutOfRange { limits, .. } => Some(tuple(limits)),
            _ => None,
        }
    }

    #[getter]
    fn value<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match &self.0.problem {
            Problem::NotANumber(v) | Problem::Negative(v) | Problem::UnknownKind(v) | Problem::UnknownShape(v) => {
                python(py, v)
            }
            Problem::OutOfRange { value, .. } => Ok(value.into_pyobject(py)?.into_any()),
            Problem::IdTwice(id) | Problem::Unreadable(id) => Ok(id.into_pyobject(py)?.into_any()),
            Problem::TooManySensors { most } => Ok(most.into_pyobject(py)?.into_any()),
            Problem::TooLarge { most } => Ok(most.into_pyobject(py)?.into_any()),
            _ => Ok(py.None().into_bound(py)),
        }
    }

    fn __str__(&self) -> String {
        self.0.to_string()
    }

    fn __repr__(&self) -> String {
        format!("ConfigProblem({})", self.0)
    }
}

fn tuple(l: Limits) -> (f64, f64, bool, bool) {
    (l.lo, l.hi, l.lo_open, l.hi_open)
}

/// A JSON value as Python's json.loads gives it.
fn python<'py>(py: Python<'py>, v: &Value) -> PyResult<Bound<'py, PyAny>> {
    let json = py.import("json")?;
    json.call_method1("loads", (v.to_string(),))
}

/// Checks a configuration (JSON; what is missing is not checked): ConfigError with the first problem.
#[pyfunction]
pub fn check_config(json: &str) -> PyResult<()> {
    let value: Value = serde_json::from_str(json).map_err(|e| config_error(ConfigError::unreadable(e)))?;
    config::check(&value).map_err(config_error)
}

/// The limits of the configuration's numbers, for the web UI's fields: {"params", "sensor", "door",
/// "layer"} -> {field: [low, high, low excluded, high excluded]}.
#[pyfunction]
pub fn config_limits(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let out = PyDict::new(py);
    for (group, limits) in [
        ("params", &config::PARAMS[..]),
        ("sensor", &config::SENSOR[..]),
        ("door", &config::DOOR[..]),
        ("layer", &config::LAYER[..]),
    ] {
        let fields = PyDict::new(py);
        for &(key, l) in limits {
            fields.set_item(key, tuple(l))?;
        }
        out.set_item(group, fields)?;
    }
    Ok(out)
}
