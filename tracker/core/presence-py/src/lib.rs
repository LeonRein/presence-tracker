//! Python bindings of presence-core: the module `presence_core`.

use pyo3::panic::PanicException;
use pyo3::prelude::*;

mod calibration;
mod config;
mod core;
mod frames;
#[cfg(feature = "testing")]
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
    m.add_class::<calibration::PyWalking>()?;
    m.add_class::<frames::PyFrame>()?;
    m.add_function(wrap_pyfunction!(views::plan, m)?)?;
    m.add_function(wrap_pyfunction!(views::derive, m)?)?;
    m.add_function(wrap_pyfunction!(calibration::calibrate, m)?)?;
    m.add_function(wrap_pyfunction!(calibration::calibration_status, m)?)?;
    m.add_function(wrap_pyfunction!(config::check_config, m)?)?;
    m.add_function(wrap_pyfunction!(config::changes, m)?)?;
    m.add_function(wrap_pyfunction!(config::config_limits, m)?)?;
    m.add_function(wrap_pyfunction!(config::config_defaults, m)?)?;
    m.add_function(wrap_pyfunction!(config::canonical, m)?)?;
    m.add_class::<config::ConfigProblem>()?;
    // the hooks of the shell's tests and the tools (feature "testing"; maturin develop --features testing)
    #[cfg(feature = "testing")]
    {
        m.add_function(wrap_pyfunction!(core::model_defaults, m)?)?;
        m.add_function(wrap_pyfunction!(core::ld_expected, m)?)?;
        m.add_function(wrap_pyfunction!(core::ld_prior, m)?)?;
        m.add_function(wrap_pyfunction!(testing::build_export, m)?)?;
        m.add_function(wrap_pyfunction!(testing::hidden_at, m)?)?;
        m.add_function(wrap_pyfunction!(testing::panic_for_test, m)?)?;
        m.add_class::<geometry::PyGeometry>()?;
    }
    // the fingerprint of the core's sources this module was built of (build.rs): a report names its code
    m.add("SOURCE_HASH", env!("PRESENCE_CORE_SOURCE_HASH"))?;
    m.add("WorldChanged", m.py().get_type::<core::WorldChanged>())?;
    m.add("ConfigError", m.py().get_type::<config::PyConfigError>())?;
    // a panic of the core is a BaseException in Python: the shell turns it into an Exception
    m.add("PanicException", m.py().get_type::<PanicException>())?;
    Ok(())
}
