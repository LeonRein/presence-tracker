//! The calibration for the shell (presence_core::calibration): the walking rows of every LD2450 frame
//! (`Walking`, the shell keeps and stores them), the solve and the progress of the data. The rows go in as
//! the bytes of their float64 rows (t, track, x, y, speed, single), as the shell stores them.

use std::collections::BTreeMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::config::config_from;
use crate::frames::PyFrame;
use presence_core::calibration::{self, COLUMNS, Row, Walking};

/// The walking targets of every LD2450 frame through the boards' own tracks (one sequence of track ids
/// for all): the rows to keep (calibration::Walking).
#[pyclass(name = "Walking", module = "presence_core")]
pub struct PyWalking(Walking);

#[pymethods]
impl PyWalking {
    /// Track ids from first_id on (after the ones of stored rows).
    #[new]
    fn new(first_id: u64) -> Self {
        Self(Walking::starting_at(first_id))
    }

    /// A frame of a board at t (learning paused: nothing kept, and its walking tracks never): the rows
    /// (t, track, x, y, speed, single 1/0) to keep; ValueError for a time that is no number.
    fn frame(
        &mut self,
        sensor_id: &str,
        t: f64,
        frame: &Bound<'_, PyFrame>,
        paused: bool,
    ) -> PyResult<Vec<[f64; COLUMNS]>> {
        let rows = self
            .0
            .frame(sensor_id, t, &frame.get().0, paused)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(rows.iter().map(Row::values).collect())
    }
}

/// The rows per sensor id from their bytes.
fn rows_of(data: BTreeMap<String, Vec<u8>>) -> PyResult<BTreeMap<String, Vec<Row>>> {
    data.into_iter()
        .map(|(id, bytes)| {
            let rows = Row::of_bytes(&bytes).map_err(|e| PyValueError::new_err(format!("{id}: {e}")))?;
            Ok((id, rows))
        })
        .collect()
}

/// New heading and scale for every placed sensor with walking measurements (calibration::solve), as JSON:
/// {"sensors": [...], "unsolved", "min_hours", "inside", "scale_prior"} or {"error": "no_walking"}. Without
/// the GIL: it takes seconds for a day of data.
#[pyfunction]
#[pyo3(signature = (config, data, check_mirror=false))]
pub fn calibrate(
    py: Python<'_>,
    config: &str,
    data: BTreeMap<String, Vec<u8>>,
    check_mirror: bool,
) -> PyResult<String> {
    let config = config_from(config)?;
    let data = rows_of(data)?;
    let report = py.detach(|| calibration::solve(&config, &data, check_mirror));
    serde_json::to_string(&report).map_err(|e| PyValueError::new_err(e.to_string()))
}

/// The progress of the data before a solve (calibration::status), as JSON: {"frames", "points", "hours",
/// "min_hours", "pairs"}.
#[pyfunction]
pub fn calibration_status(py: Python<'_>, config: &str, data: BTreeMap<String, Vec<u8>>) -> PyResult<String> {
    let config = config_from(config)?;
    let data = rows_of(data)?;
    let status = py.detach(|| calibration::status(&config, &data));
    serde_json::to_string(&status).map_err(|e| PyValueError::new_err(e.to_string()))
}
