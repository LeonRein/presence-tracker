//! Python bindings of presence-core: the module `presence_core`.

use pyo3::panic::PanicException;
use pyo3::prelude::*;

mod config;
mod core;
mod frames;
mod geometry;
#[cfg(feature = "testing")]
mod testing;
mod views;

#[pymodule]
fn presence_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<core::Core>()?;
    m.add_class::<core::PyWorld>()?;
    m.add_class::<core::PyLearned>()?;
    m.add_class::<frames::PyClock>()?;
    m.add_class::<frames::PyTrackStage>()?;
    m.add_class::<frames::PyFrame>()?;
    m.add_class::<geometry::PyGeometry>()?;
    m.add_function(wrap_pyfunction!(core::model_defaults, m)?)?;
    m.add_function(wrap_pyfunction!(core::ld_expected, m)?)?;
    m.add_function(wrap_pyfunction!(core::forgetting, m)?)?;
    m.add_function(wrap_pyfunction!(core::ld_prior, m)?)?;
    m.add_function(wrap_pyfunction!(views::plan, m)?)?;
    m.add_function(wrap_pyfunction!(config::check_config, m)?)?;
    m.add_function(wrap_pyfunction!(config::config_limits, m)?)?;
    m.add_class::<config::ConfigProblem>()?;
    // the hooks of the shell's tests (feature "testing"; maturin develop --features testing)
    #[cfg(feature = "testing")]
    {
        m.add_function(wrap_pyfunction!(testing::build_export, m)?)?;
        m.add_function(wrap_pyfunction!(testing::hidden_at, m)?)?;
        m.add_function(wrap_pyfunction!(testing::panic_for_test, m)?)?;
    }
    // what only the outputs read: a change of them alone builds nothing (Core.set_outputs)
    m.add("OUTPUT_PARAMS", ::presence_core::params::OUTPUT_FIELDS)?; // the crate, not this function
    m.add("WorldChanged", m.py().get_type::<core::WorldChanged>())?;
    m.add("ConfigError", m.py().get_type::<config::PyConfigError>())?;
    // a panic of the core is a BaseException in Python: the shell turns it into an Exception
    m.add("PanicException", m.py().get_type::<PanicException>())?;
    Ok(())
}
