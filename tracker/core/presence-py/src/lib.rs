//! Python bindings of presence-core: the module `presence_core`.

use pyo3::panic::PanicException;
use pyo3::prelude::*;

mod proto;

/// Panics with this message: lets the shell's tests check that a panic of the core starts the model
/// over like any other error of the model.
#[pyfunction]
fn panic_for_test(message: &str) {
    panic!("{message}");
}

#[pymodule]
fn presence_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<proto::ProtoWorld>()?;
    m.add_function(wrap_pyfunction!(proto::proto_flush, m)?)?;
    m.add_function(wrap_pyfunction!(panic_for_test, m)?)?;
    // a panic of the core is a BaseException in Python: the shell turns it into an Exception
    m.add("PanicException", m.py().get_type::<PanicException>())?;
    Ok(())
}
