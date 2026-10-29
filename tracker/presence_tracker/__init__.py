"""Multi-sensor radar presence tracker for Home Assistant."""

import hashlib
import pathlib

import presence_core

__version__ = "0.51.1"  # as in config.yaml


def code_hash() -> str:
    """The code that makes a report (its Python files and the Rust core's sources, whose fingerprint the core
    carries from its build: presence_core.SOURCE_HASH, also in the release wheel without sources), so that it
    is replayed with that code: builds in development share one version number."""
    h = hashlib.sha256()
    for path in sorted(pathlib.Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    h.update(presence_core.SOURCE_HASH.encode())
    return h.hexdigest()[:12]
