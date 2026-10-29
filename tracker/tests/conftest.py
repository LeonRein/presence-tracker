"""The tests' hooks into the core: the tests marked "hooks" need presence_core built with its test hooks
(the feature "testing": build_export, hidden_at, panic_for_test, model_defaults, ld_prior, ld_expected, Geometry,
Core.normalize/spoil_weights_for_test/ld_last and the readings of the filter's state and outputs the tests and
tools take from Tracker.core). Every test of a module that simulates frames or asks the sensor geometry
(sim.simulate, sim.geometry: presence_core.Geometry) is marked "hooks" here; the others that read the core
directly are marked by hand. Against a release wheel (CI): pytest -m "not hooks", with
PT_RELEASE_WHEEL=1 (test_filter checks the wheel has no hooks)."""

import presence_core
import pytest

HOOKS = hasattr(presence_core, "panic_for_test")


def pytest_configure(config):
    config.addinivalue_line("markers", "hooks: needs presence_core built with --features testing")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    import sim
    for item in items:
        module = getattr(item, "module", None)
        if any(getattr(module, name, None) is getattr(sim, name) for name in ("simulate", "geometry")):
            item.add_marker(pytest.mark.hooks)


def pytest_runtest_setup(item):
    if item.get_closest_marker("hooks") and not HOOKS:
        pytest.fail("needs presence_core with its test hooks: uv run maturin develop --uv --release --features "
                    "testing -m core/presence-py/Cargo.toml (against a release wheel: pytest -m 'not hooks')",
                    pytrace=False)
