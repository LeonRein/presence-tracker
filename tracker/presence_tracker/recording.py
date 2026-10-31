"""The app's own recording (settings, "Aufzeichnung"; DOCS.md): what comes in, one file per hour
recordings/YYYYMMDD-HH.jsonl.gz in the data directory, its lines as tools/record.py writes them, so that the replays
and fast_eval read them once unpacked. Operating, not model: neither tracker.json nor the core know of it, the
switch is kept in recording.json.

The loop only collects (add). Every FLUSH s a thread makes the lines, compresses and writes them, ending in a
Z_SYNC_FLUSH and an fsync: a crash loses at most that long, and a file left without its gzip trailer reads up to
there. Appending to such a file would make the new lines unreadable (a gzip reader takes them for the old member's
data), so a file that is opened again is first cut back to its whole lines (repair); after a clean close the new
lines are simply a new gzip member. A failed write stops the recording, not the app: logged, off, shown in the panel.
"""

import asyncio
import collections
import contextlib
import gzip
import json
import logging
import os
import pathlib
import re
import tarfile
import threading
import time
import zlib

from . import pause
from .util import Throttled, atomic_write, decoded

log = logging.getLogger(__name__)
LIMIT = 3_000_000_000  # B over all recordings: above it the oldest files go
FLUSH = 30.0  # s between writes, at most what a crash loses
LEVEL = 6  # gzip's, as the error reports'
PENDING = 30_000  # messages waiting for a write that hangs (about 10 min), then the oldest go: the app's memory holds
CHUNK = 1 << 20  # B read at a time for a download
NAME = re.compile(r"\d{8}-\d{2}\.jsonl\.gz")
CONFIG = "app/config"  # the configuration's line; every reader of recordings takes frames and the pause switch only
throttled = Throttled(log)


def line(recv: float, topic: str, payload: bytes) -> str:
    """A message as tools/record.py writes it."""
    return json.dumps({"t": round(recv, 3), "topic": topic, "payload": decoded(payload)}, separators=(",", ":"))


def repair(path: pathlib.Path):
    """A file with a gzip member not ended (a crash, a power cut): its whole lines, whole again (written whole or
    not at all). A sound file stays as it is."""
    lines = []
    try:
        with gzip.open(path) as f:
            for text in f:  # (cut off: raises where the last line would be incomplete)
                lines.append(text)
        return
    except (EOFError, zlib.error, gzip.BadGzipFile):
        pass
    log.info("recording %s was cut off: %d lines kept", path.name, len(lines))
    atomic_write(path, gzip.compress(b"".join(lines), compresslevel=LEVEL))


class Recorder:
    """config: the JSON of the configuration's line; paused: whether "Lernen pausieren" is on now; active: False in
    a replay, it never records."""

    def __init__(self, data_dir: pathlib.Path, config: str, paused, active: bool = True):
        self.dir = data_dir / "recordings"
        self.state = data_dir / "recording.json"
        self.config_text = config
        self.paused = paused
        self.on = False
        self.since = None  # when it was switched on
        self.error = None  # why it stopped, for the panel ({"error": key, "detail"})
        self.pending = collections.deque(maxlen=PENDING)
        self.next_write = 0.0  # monotonic
        self.task = None  # the running write
        self.file = None  # the open file and its hour, used under the lock
        self.hour = None
        self.lock = threading.RLock()
        if active and self.state.exists():
            try:
                saved = json.loads(self.state.read_text())
                self.on, self.since = saved["on"] is True, saved["since"]
            except Exception as e:  # noqa: BLE001 - the switch then is off until it is set
                log.warning("recording.json unreadable (%r): recording off", e)

    # ---------------------------------------------------------------- in the loop

    def add(self, recv: float, topic: str, payload: bytes):
        if self.on:
            self.pending.append((recv, topic, payload))

    def config(self, text: str):
        """The configuration as saved: a line of its own if it changed."""
        if text != self.config_text:
            self.config_text = text
            self.add(time.time(), CONFIG, text.encode())

    def tick(self):
        """Called often: a write every FLUSH s."""
        now = time.monotonic()
        if self.on and now >= self.next_write and (self.task is None or self.task.done()):
            self.next_write = now + FLUSH
            self.task = asyncio.create_task(self.flush())

    def _take(self) -> collections.deque:
        batch, self.pending = self.pending, collections.deque(maxlen=PENDING)
        return batch

    async def flush(self, close: bool = False):
        """Write what came in, in a thread; close: and end the file (the next message goes on in a new member)."""
        await asyncio.to_thread(self._write, self._take(), close)

    async def switch(self, on: bool):
        if on != self.on:
            self.on, self.since, self.error = on, time.time() if on else None, None
            await self.flush(close=True)
            await asyncio.to_thread(self._save)

    async def delete(self):
        await asyncio.to_thread(self._delete)

    async def stream(self, write):
        """All files as one tar through write (async): they are compressed already, and read in pieces. Each ends
        as it was at the start, also the one being written (the next lines go on behind that)."""
        for path, size in await asyncio.to_thread(self._whole, self._take()):
            try:
                f = path.open("rb")
            except FileNotFoundError:  # above the limit or deleted meanwhile
                continue
            with f:
                info = tarfile.TarInfo(f"recordings/{path.name}")
                info.size, info.mtime = size, int(os.fstat(f.fileno()).st_mtime)
                await write(info.tobuf(tarfile.USTAR_FORMAT))
                left = size
                while left:
                    chunk = await asyncio.to_thread(f.read, min(left, CHUNK))
                    if not chunk:
                        raise OSError(f"{path.name} became shorter")
                    await write(chunk)
                    left -= len(chunk)
                await write(bytes(-size % 512))
        await write(bytes(1024))

    def status(self) -> dict:
        files = self.files()
        return {"on": self.on, "since": self.since, "error": self.error, "files": len(files),
                "size": sum(size for _, size in files), "oldest": files[0][0].name[:11] if files else None,
                "limit": LIMIT}

    def files(self) -> list:
        """[(path, size)], oldest first: regular files of the recordings' names only, no link, nothing else there."""
        try:
            entries = list(os.scandir(self.dir))
        except OSError:  # none yet, or unreadable: the panel still answers
            return []
        out = []
        for entry in entries:
            if NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                with contextlib.suppress(FileNotFoundError):  # deleted meanwhile
                    out.append((pathlib.Path(entry.path), entry.stat(follow_symlinks=False).st_size))
        return sorted(out)

    # -------------------------------------------------------------- in the thread

    def _write(self, batch, close: bool = False):
        with self.lock:
            try:
                lines = []
                for recv, topic, payload in batch:
                    hour = time.strftime("%Y%m%d-%H", time.localtime(recv))
                    if hour != self.hour:
                        self._store(lines)
                        lines = self._open(hour, recv)
                    lines.append(line(recv, topic, payload))
                self._store(lines)
                if close:
                    self._close()
                if batch:
                    self._prune()
            except Exception as e:  # noqa: BLE001 - a full disk, any error: the app goes on without the recording
                self._failed(e)

    def _open(self, hour: str, t: float) -> list:
        """The file of this hour (repaired, if there is one); its first lines. Every file stands alone: the
        configuration, and the switch "Lernen pausieren", which the broker sends only at the connect."""
        self._close()
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{hour}.jsonl.gz"
        if path.exists():
            repair(path)
        self.file, self.hour = gzip.open(path, "ab", compresslevel=LEVEL), hour
        return [line(t, CONFIG, self.config_text.encode()),
                line(t, pause.COMMAND, b"ON" if self.paused() else b"OFF")]

    def _store(self, lines: list):
        if lines:
            self.file.write(("\n".join(lines) + "\n").encode())
            self.file.flush()  # Z_SYNC_FLUSH
            os.fsync(self.file.fileno())

    def _close(self):
        file, self.file, self.hour = self.file, None, None
        if file is not None:
            file.close()

    def _prune(self):
        files = self.files()
        total = sum(size for _, size in files)
        for path, size in files[:-1]:  # (the newest is the one being written)
            if total <= LIMIT:
                break
            path.unlink(missing_ok=True)
            total -= size
            log.info("recording %s deleted: above the limit", path.name)

    def _whole(self, batch) -> list:
        with self.lock:
            self._write(batch, close=True)
            return self.files()

    def _delete(self):
        with self.lock:
            with contextlib.suppress(OSError):  # a full disk takes no trailer; the file goes anyway
                self._close()
            for path, _ in self.files():
                path.unlink(missing_ok=True)

    def _failed(self, e: Exception):
        throttled("recording", logging.ERROR, "recording stopped: %r", e)
        self.on, self.since, self.error = False, None, {"error": "recording_failed", "detail": str(e)}
        self.pending.clear()
        with contextlib.suppress(Exception):
            self._close()
        self._save()

    def _save(self):
        try:
            atomic_write(self.state, json.dumps({"on": self.on, "since": self.since}).encode())
        except OSError as e:  # the switch then is as it was at the next start
            throttled("recording.json", logging.WARNING, "recording.json not saved: %r", e)
