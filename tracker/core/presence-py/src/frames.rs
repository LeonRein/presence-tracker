//! Frames from Python (MODEL.md 4.1): a frame checked once by the core (presence_core.Frame, from the
//! MQTT payload or a recording's dict), the boards' clocks, and the LD2450's tracks for the calibration.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use serde_json::{Map, Number, Value};

use presence_core::frames::{Anchor, Frame, SensorClock};
use presence_core::ld2450::tracks::TrackStage;

/// Deeper than this a frame is not (its gates are lists in an object in an object).
const MAX_DEPTH: usize = 8;

fn invalid(why: impl Into<String>) -> PyErr {
    PyValueError::new_err(why.into())
}

fn finite(x: f64) -> PyResult<Value> {
    Number::from_f64(x)
        .map(Value::Number)
        .ok_or_else(|| invalid(format!("{x} is no finite number")))
}

/// A Python value as JSON (what json.dumps would write; numbers that are not finite refused).
fn json_of(obj: &Bound<'_, PyAny>, depth: usize) -> PyResult<Value> {
    if depth > MAX_DEPTH {
        return Err(invalid("nested too deep"));
    }
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(b) = obj.cast::<PyBool>() {
        return Ok(Value::Bool(b.is_true()));
    }
    if let Ok(i) = obj.cast::<PyInt>() {
        return match i.extract::<i64>() {
            Ok(i) => Ok(Value::from(i)),
            Err(_) => finite(i.extract::<f64>().map_err(|_| invalid("an integer too large"))?),
        };
    }
    if let Ok(x) = obj.cast::<PyFloat>() {
        return finite(x.value());
    }
    if let Ok(s) = obj.cast::<PyString>() {
        return Ok(Value::String(s.to_str()?.to_owned()));
    }
    if let Ok(d) = obj.cast::<PyDict>() {
        let mut map = Map::new();
        for (k, v) in d.iter() {
            let k = k.cast::<PyString>().map_err(|_| invalid("a key that is no string"))?;
            map.insert(k.to_str()?.to_owned(), json_of(&v, depth + 1)?);
        }
        return Ok(Value::Object(map));
    }
    let items: Vec<Bound<'_, PyAny>> = if let Ok(l) = obj.cast::<PyList>() {
        l.iter().collect()
    } else if let Ok(t) = obj.cast::<PyTuple>() {
        t.iter().collect()
    } else {
        return Err(invalid(format!("no JSON value but {}", obj.get_type().name()?)));
    };
    items
        .iter()
        .map(|v| json_of(v, depth + 1))
        .collect::<PyResult<_>>()
        .map(Value::Array)
}

/// A frame as MQTT brings it (presence/<id>/frame), checked once against what the firmware sends;
/// what the tracker, the clocks and the calibration take.
#[pyclass(name = "Frame", module = "presence_core", frozen)]
pub struct PyFrame(pub Frame);

#[pymethods]
impl PyFrame {
    /// The MQTT payload; ValueError (why) if it is no frame.
    #[staticmethod]
    fn parse(payload: &[u8]) -> PyResult<Self> {
        Frame::parse(payload).map(Self).map_err(|e| invalid(e.to_string()))
    }

    /// A payload as JSON already read (a recording's line, a test, the simulation); ValueError (why).
    #[staticmethod]
    fn of(frame: &Bound<'_, PyAny>) -> PyResult<Self> {
        let value = json_of(frame, 0).map_err(|e| invalid(format!("no JSON frame: {e}")))?;
        Frame::from_json(&value).map(Self).map_err(|e| invalid(e.to_string()))
    }

    /// millis() of the board at the frame (None: not sent)
    #[getter]
    fn uptime_ms(&self) -> Option<u32> {
        self.0.uptime_ms
    }

    /// The LD2410C part for the web UI: moving, still, moving_distance, still_distance (mm),
    /// move_gates, still_gates (None: not sent); None without a part.
    #[getter]
    fn ld2410<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(ld) = self.0.ld2410 else {
            return Ok(None);
        };
        let out = PyDict::new(py);
        out.set_item("moving", ld.moving)?;
        out.set_item("still", ld.still)?;
        out.set_item("moving_distance", ld.moving_distance)?;
        out.set_item("still_distance", ld.still_distance)?;
        out.set_item("move_gates", ld.gates.map(|g| g.moving.to_vec()))?;
        out.set_item("still_gates", ld.gates.map(|g| g.still.to_vec()))?;
        Ok(Some(out))
    }
}

/// The frame time of a board's frames from its uptime (frames.SensorClock): clock(recv, frame).
#[pyclass(name = "SensorClock", module = "presence_core")]
pub struct PyClock(SensorClock);

#[pymethods]
impl PyClock {
    /// A new clock, or one that goes on from a saved anchor (offset and last_up both or neither).
    #[new]
    #[pyo3(signature = (offset=None, last_up=None))]
    fn new(offset: Option<f64>, last_up: Option<f64>) -> PyResult<Self> {
        match (offset, last_up) {
            (None, None) => Ok(Self(SensorClock::default())),
            (Some(offset), Some(last_up)) => Ok(Self(SensorClock::anchored(Anchor { offset, last_up }))),
            _ => Err(invalid("a clock's anchor is offset and last_up together")),
        }
    }

    /// The time of a frame received at recv (by its uptime_ms).
    fn __call__(&mut self, recv: f64, frame: &Bound<'_, PyFrame>) -> f64 {
        self.0.at(recv, frame.get().0.uptime_ms)
    }

    /// receive time - uptime as the clock holds it (None before the first frame with an uptime)
    #[getter]
    fn offset(&self) -> Option<f64> {
        self.0.anchor().map(|a| a.offset)
    }

    /// the uptime (s) of the frame that moved the anchor last
    #[getter]
    fn last_up(&self) -> Option<f64> {
        self.0.anchor().map(|a| a.last_up)
    }
}

/// The LD2450's own tracks of several boards with one sequence of track ids, for the calibration
/// (ld2450::tracks::TrackStage).
#[pyclass(name = "TrackStage", module = "presence_core")]
pub struct PyTrackStage(TrackStage);

#[pymethods]
impl PyTrackStage {
    #[new]
    fn new(first_id: u64) -> Self {
        Self(TrackStage::starting_at(first_id))
    }

    /// A frame of a board at t: its targets measured in a track as (track, x, y (m, sensor frame),
    /// radial speed (m/s)); ValueError for a time that is no number.
    fn update(&mut self, sensor_id: &str, t: f64, frame: &Bound<'_, PyFrame>) -> PyResult<Vec<(u64, f64, f64, f64)>> {
        let measured = self
            .0
            .update(sensor_id, t, &frame.get().0)
            .map_err(|e| invalid(e.to_string()))?;
        Ok(measured
            .into_iter()
            .map(|m| (m.seg.0, m.local[0], m.local[1], m.speed))
            .collect())
    }
}
