# Fixtures of the tests

What the Python oracles of 0.38.1 (commit 29c2fe1) said, kept for the tests after phase 9 of the Rust port deleted
them (docs/rust-core.md). The tests check the core against them. Made on 28.10.2026 by the scripts in `make/`, which
run on 29c2fe1 only (they import the oracles); how, in their docstrings. Run again there, they give the same files
(checked for `people.json` and the geometry in `destinations.json`). They were deleted in 0.54.4;
a8e8de0 is the last commit with them: `git show a8e8de0:tracker/tests/fixtures/make/make_0381.py` (and
`core_build.py`) into a worktree of 29c2fe1. The digests of `core_build.json` on later trees come from
`tests/test_core_build.py` itself (run as a script, its `__main__`).

- `people.json` (`make/make_0381.py`, `hidden.Hidden.to_dict`): saved states of the people, `Tracker.people_state`'s
  format, each one hypothesis, nobody unknown: `nook` somebody standing in the nook of `test_existence._nook_config`
  (the stays' prior at its tiles, r = 1), `sitter` somebody standing at (3, 2.5) in `test_filter.flat_config(entry=
  True)`, `measured` two standing at (3, 3) in `flat_config()` with an LD2410C amplitude a track measured (0.3;
  `gauss.Gauss.source(...).to_tiles`, `measured_level` its level).
- `destinations.json` (`destination.Geometry`, `DestinationMap`) for `test_entering.two_rooms`: the geometry's
  fingerprint and number of cells, `around(3.5, 2.5)` (cells, weights), `vel_interp` of 1 m/s towards b (`ahead`)
  and towards a (`back`); `map` the map after two trips desk -> sofa and back (`test_destination.routine(2)`, as the
  filter of 0.38.1 counted them) as `DestinationMap.to_dict` wrote it, `map_lookup` its `lookup` there.
- `ld2410.json` (`ld2410.Background.to_dict`): two sensors of `room_config`, each with its level, anchor and echoes
  counted, b held for 42.5 s; `b`, `anchor`, `rate` what the Python background read from it.
- `core_build.json` (`make/core_build.py`, now `test_core_build.digests`): per configuration of the tests a sha1 per part of what the core builds
  (`presence_core.build_export`: the plan, the raster, the tiles, per sensor its sight and LD2410C) and the tiles'
  fingerprint - made with the core of 0.38.1, whose build was checked against the Python build there.
- `core_reflections.json` (`make/make_0381.py`, `model.Config.hidden`): per configuration and sensor a sha1 of the
  rule of reflections on a grid of points.

The private configurations (`PT_CONFIGS`) have theirs in that directory, `core_build_0381.json` and
`core_reflections_0381.json`, never here: the repository is public.
