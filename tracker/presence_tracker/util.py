"""Small helpers of the app: files written so that a power cut leaves the old or the new one, and log
lines that a stream of bad input can't flood."""

import logging
import os
import pathlib
import time


def atomic_write(path, data: bytes):
    """Write a file whole or not at all: into a temporary file, flushed to the disk (fsync), then
    renamed over the old one, and the directory flushed too. Without the fsync a power cut after the
    rename can leave an empty file (ext4 delayed allocation): the app then started with nothing."""
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class Throttled:
    """A log line at most once per `every` s per key, with the number of the ones left out since."""

    def __init__(self, logger: logging.Logger, every: float = 60.0):
        self.log = logger
        self.every = every
        self.last: dict = {}  # key -> (time of the last line, lines left out since)

    def __call__(self, key, level: int, msg: str, *args, exc_info=False):
        now = time.monotonic()
        last, skipped = self.last.get(key, (-float("inf"), 0))
        if now - last < self.every:
            self.last[key] = (last, skipped + 1)
            return
        self.last[key] = (now, 0)
        if skipped:
            msg += f" ({skipped} more like this in the last {now - last:.0f} s)"
        self.log.log(level, msg, *args, exc_info=exc_info)
