# Fixtures of the tests

Made once, on 28.10.2026, with 0.38.1 (commit 29c2fe1) and its Python oracles, which phase 9 of the Rust port
deleted (docs/rust-core.md). They keep what those oracles said; the tests check the core against them.

- `people.json`: saved states of the people (`Tracker.people_state`'s format, written by `hidden.Hidden.to_dict`):
  `nook` somebody standing in the nook of `test_existence._nook_config` (the stays' prior at its tiles, r = 1),
  `sitter` somebody standing at (3, 2.5) in `test_filter.flat_config(entry=True)`, `measured` two standing at
  (3, 3) in `flat_config()` with an LD2410C amplitude a track measured (0.3; `gauss.Gauss.source(...).to_tiles`,
  `measured_level` its level). Each one hypothesis, nobody unknown.
- `ghosts.json`: ghost maps (`learned()["ghost_map"]`) learned by `ghostmap.GhostMap.add_birth`/`add_watch` from a
  new tracker's: `room_few` (3 ghosts at (2, 2) for sensor a in 2.5 h of `test_frames.room_config`), `room_many`
  (27 more in 21.5 h more), `room_other_prior` (one ghost, the prior's time / 24), `room_spot` (a day watched, 12
  ghosts at (2.2, 2.6); `room_spot_rate` the Python map's rate there), `room_source` (30 ghosts at sensor a's
  raw (0.6, 4.0), `room_source_spot` on the floor, a day watched by a and b, 5 at (3, 2.5) for b), `flat_seat`
  (200 at (4.5, 3.5) for a and b in `flat_config(entry=True)`).
- `destinations.json`: `destination.Geometry` of `test_entering.two_rooms`: its fingerprint and number of cells,
  `around(3.5, 2.5)` (cells, weights) and `vel_interp` of 1 m/s towards b (`ahead`) and towards a (`back`).
- `core_build.json`, `core_reflections.json`: per configuration of the tests and `devdata/` a sha1 of what the core
  builds (`presence_core.build_export` and the tiles' fingerprint; at 0.38.1 checked against the Python build) and
  of the rule of reflections on a grid (`model.Config.hidden` of 0.38.1); `test_core_build.py` says how. The
  private configurations' (`PT_CONFIGS`) are in that directory, `core_build_0381.json` and
  `core_reflections_0381.json`.
