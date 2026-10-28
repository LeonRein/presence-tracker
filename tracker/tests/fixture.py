"""The fixtures of the tests (fixtures/README.md): made once with 0.38.1 and its Python oracles, which are
gone since."""

import base64
import json
import pathlib
import zlib

import numpy as np

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    """A fixture made once with 0.38.1 and its Python oracles (fixtures/README.md): how a test puts
    people where it wants them (people.json: saved states of one hypothesis, these known people,
    nobody unknown; the core's state is written only by the filter), learned maps, what the Python
    build said."""
    return json.loads((FIXTURES / name).read_text())


def unpack(text: str, dtype="<f2") -> np.ndarray:
    """Numbers of a saved format: base64 of zlib."""
    return np.frombuffer(zlib.decompress(base64.b64decode(text)), dtype=dtype).astype(float)
