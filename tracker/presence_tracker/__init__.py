"""Multi-sensor radar presence tracker for Home Assistant."""

import hashlib
import pathlib

__version__ = "0.25.1"  # as in config.yaml


def code_hash() -> str:
    """The model's code (its Python files), so that a report is replayed with the code that made it:
    builds in development share one version number."""
    h = hashlib.sha256()
    for path in sorted(pathlib.Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]
