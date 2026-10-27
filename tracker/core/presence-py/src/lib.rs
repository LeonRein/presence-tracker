//! Python bindings of presence-core: the module `presence_core`.

use pyo3::prelude::*;

mod proto;

#[pymodule]
fn presence_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<proto::ProtoWorld>()?;
    m.add_function(wrap_pyfunction!(proto::proto_flush, m)?)?;
    Ok(())
}
