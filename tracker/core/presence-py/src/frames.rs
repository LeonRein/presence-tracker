//! Frames from Python (MODEL.md 4.1): a frame as the shell has it (a dict from JSON) checked by the
//! core's checker, the sensors' clocks, and the LD2450's tracks for the calibration.

use std::collections::BTreeMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use serde_json::{Map, Number, Value};

use presence_core::frames::{Anchor, Frame, SensorClock};
use presence_core::ld2450::tracks::{Ld2450Tracks, SegIds, Sighting};

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

/// A frame as the shell has it, checked (ValueError with the reason).
pub fn frame_of(frame: &Bound<'_, PyAny>) -> PyResult<Frame> {
    let value = json_of(frame, 0).map_err(|e| invalid(format!("no JSON frame: {e}")))?;
    Frame::from_json(&value).map_err(|e| invalid(e.to_string()))
}

/// ValueError (why) unless the frame (a dict as from JSON) is one: an object; uptime_ms a number >= 0
/// (or missing); targets a list of at most three objects with numbers x, y (speed, an integer slot if
/// there), each slot once; ld2410 an object (or missing) with its gates lists of numbers.
#[pyfunction]
pub fn check_frame(frame: &Bound<'_, PyAny>) -> PyResult<()> {
    frame_of(frame).map(drop)
}

/// The frame time of a sensor's frames from its uptime (frames.SensorClock): clock(recv, uptime_ms).
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

    #[pyo3(signature = (recv, uptime_ms))]
    fn __call__(&mut self, recv: f64, uptime_ms: Option<f64>) -> f64 {
        self.0.at(recv, uptime_ms)
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

/// The LD2450's own tracks of several boards with one sequence of track ids (the calibration's
/// measurements: calibration.track_frame). Every target in front of a sensor counts; there is no plan.
#[pyclass(module = "presence_core")]
pub struct TrackStage {
    boards: BTreeMap<String, Ld2450Tracks>,
    ids: SegIds,
}

#[pymethods]
impl TrackStage {
    #[new]
    fn new(first_id: u64) -> Self {
        Self {
            boards: BTreeMap::new(),
            ids: SegIds::starting_at(first_id),
        }
    }

    /// A frame of a board at t: its targets measured in a track (born or continued) as (track, x, y
    /// (m, sensor frame), radial speed (m/s)); ValueError for a frame that is none.
    fn update(&mut self, sensor_id: &str, t: f64, frame: &Bound<'_, PyAny>) -> PyResult<Vec<(u64, f64, f64, f64)>> {
        if !t.is_finite() {
            return Err(invalid("the frame's time is no number"));
        }
        let frame = frame_of(frame)?;
        let seen: Vec<Sighting> = frame
            .targets
            .iter()
            .filter(|tg| tg.local()[1] > 0.0)
            .map(|&target| Sighting { target, visible: true })
            .collect();
        let tracks = self.boards.entry(sensor_id.to_owned()).or_default();
        let ev = tracks.update(t, &seen, &mut self.ids);
        Ok(ev
            .born
            .iter()
            .chain(&ev.measured)
            .map(|a| {
                let tg = seen[a.det].target;
                let [x, y] = tg.local();
                (a.seg.0, x, y, tg.speed / 1000.0)
            })
            .collect())
    }
}
