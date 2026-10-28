"""What the core builds of a configuration, as a digest per part (core_build.json, test_core_build.py): per key of
presence_core.build_export (the plan, the raster, the tiles, per sensor its sight and LD2410C) a sha1, and the tiles'
fingerprint. Runs on 29c2fe1 (0.38.1, where the build was checked against the Python build) and on any later tree:

    OPENBLAS_NUM_THREADS=1 PT_CONFIGS=DIR uv run python THIS OUT

writes OUT/core_build.json and the private configurations' core_build_0381.json into PT_CONFIGS."""

import hashlib
import json
import os
import pathlib
import sys

import numpy as np

import presence_core


def digests(config, model: str, fingerprint: str) -> dict:
    """{key: sha1 of it} of what the core builds of the configuration and model (JSON; since 0.39 "{}", the
    core's defaults; on 0.38.1 the shell's Tracker._model(), the same numbers)."""
    core = presence_core.build_export(json.dumps(config.to_dict()), model, 30.0)

    def sha(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()[:16]

    def of(v) -> str:
        if v is None:
            return sha(b"-")
        if isinstance(v, np.ndarray):
            return sha(np.ascontiguousarray(v, dtype=float).tobytes())
        return sha(json.dumps(v, sort_keys=True, default=str).encode())
    out = {k: of(v) for k, v in core.items() if k != "sensors"}
    for si, s in enumerate(core["sensors"]):
        out.update({f"sensor {si} {k}": of(v) for k, v in s.items()})
    out["fingerprint"] = fingerprint
    return out


if __name__ == "__main__":
    sys.path[:0] = [".", "tests"]
    from presence_tracker.filter import Tracker
    from test_core_build import configs

    def digests_of(config) -> dict:
        tr = Tracker(config)
        if callable(tr.tiles):  # 0.39 on
            return digests(config, "{}", tr.tiles()["id"])
        return digests(config, tr._model(), tr.tiles.fingerprint())
    out = pathlib.Path(sys.argv[1])
    private = os.environ.get("PT_CONFIGS")
    repo, mine = {}, {}
    for name, config, *_ in configs():
        (mine if private and (pathlib.Path(private) / name).exists() else repo)[name] = digests_of(config)
    (out / "core_build.json").write_text(json.dumps(repo, separators=(",", ":")) + "\n")
    if private:
        (pathlib.Path(private) / "core_build_0381.json").write_text(json.dumps(mine, indent=1) + "\n")
    print("repo", len(repo), "private", len(mine))
