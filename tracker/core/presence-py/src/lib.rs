//! Python bindings of presence-core: the module `presence_core`.

use pyo3::panic::PanicException;
use pyo3::prelude::*;

mod core;
mod frames;
#[cfg(feature = "testing")]
mod testing;
mod views;

#[pymodule]
fn presence_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<core::Core>()?;
    m.add_class::<core::PyLearned>()?;
    m.add_class::<frames::PyClock>()?;
    m.add_class::<frames::PyTrackStage>()?;
    m.add_class::<frames::PyFrame>()?;
    m.add_function(wrap_pyfunction!(core::model_defaults, m)?)?;
    m.add_function(wrap_pyfunction!(core::ld_expected, m)?)?;
    m.add_function(wrap_pyfunction!(core::forgetting, m)?)?;
    m.add_function(wrap_pyfunction!(core::ld_prior, m)?)?;
    m.add_function(wrap_pyfunction!(views::plan, m)?)?;
    // the hooks of the shell's tests (feature "testing"; maturin develop --features testing)
    #[cfg(feature = "testing")]
    {
        m.add_function(wrap_pyfunction!(testing::build_export, m)?)?;
        m.add_function(wrap_pyfunction!(testing::hidden_at, m)?)?;
        m.add_function(wrap_pyfunction!(testing::panic_for_test, m)?)?;
    }
    m.add("SILENT_AFTER", ::presence_core::SILENT_AFTER)?; // the crate, not this function
    // a panic of the core is a BaseException in Python: the shell turns it into an Exception
    m.add("PanicException", m.py().get_type::<PanicException>())?;
    Ok(())
}
