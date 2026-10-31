"""The app's own recording (recording.py): one file per hour, in the format of tools/record.py and for the replays,
a limit, a crash loses at most the last write, a failed write stops only the recording, nothing of it in tracker.json."""

import asyncio
import gzip
import io
import json
import subprocess
import tarfile
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

import presence_core
from presence_tracker import pause, recording
from presence_tracker.app import App
from presence_tracker.recording import CONFIG, Recorder, repair
from presence_tracker.sources import ReplayClock, replay_source
from test_filter import flat_config

H10 = time.mktime((2026, 10, 31, 10, 0, 0, 0, 0, -1))  # 10:00 local time
FRAME = {"uptime_ms": 1000, "targets": [], "ld2410": {}}


def recorder(tmp_path, on=True, paused=False):
    rec = Recorder(tmp_path, '{"version":"v","config":{}}', lambda: paused)
    rec.on = on
    return rec


def put(rec, n=3, t=H10, write=True, close=False):
    for k in range(n):
        rec.add(t + k, "presence/a/frame", json.dumps({**FRAME, "uptime_ms": k}).encode())
    if write:
        rec._write(rec._take(), close)


def lines(path):
    """The whole lines of a file, also a cut off one: up to where it reads."""
    out = []
    try:
        with gzip.open(path, "rt") as f:
            for text in f:
                out.append(json.loads(text))
    except EOFError:
        pass
    return out


def uptimes(path):
    return [m["payload"]["uptime_ms"] for m in lines(path) if m["topic"].endswith("/frame")]


def test_a_file_per_hour_in_the_format_of_record_py(tmp_path):
    rec = recorder(tmp_path, paused=True)
    rec.add(H10 + 1.23456, "presence/a/frame", json.dumps(FRAME).encode())
    rec.add(H10 + 2, "presence/a/status", b"online")
    rec.add(H10 + 3, pause.COMMAND, b"OFF")
    rec.add(H10 + 3600, "presence/a/frame", b'{"uptime_ms": 2}')  # the next hour, in the same write
    rec._write(rec._take(), close=True)
    assert [p.name for p, _ in rec.files()] == ["20261031-10.jsonl.gz", "20261031-11.jsonl.gz"]
    first = lines(tmp_path / "recordings" / "20261031-10.jsonl.gz")
    # each file starts with the configuration and the switch: a recording that starts in a pause knows it
    assert [m["topic"] for m in first] == [CONFIG, pause.COMMAND, "presence/a/frame", "presence/a/status", pause.COMMAND]
    assert first[0]["payload"] == {"version": "v", "config": {}} and first[1]["payload"] == "ON"
    assert first[2]["t"] == round(H10 + 1.23456, 3) and first[2]["payload"] == FRAME
    assert first[3]["payload"] == "online" and first[4]["payload"] == "OFF"
    raw = gzip.open(tmp_path / "recordings" / "20261031-10.jsonl.gz", "rt").read().splitlines()[2]
    # byte for byte what tools/record.py writes
    assert raw == json.dumps({"t": first[2]["t"], "topic": "presence/a/frame", "payload": FRAME}, separators=(",", ":"))
    assert [m["topic"] for m in lines(tmp_path / "recordings" / "20261031-11.jsonl.gz")][0] == CONFIG


def test_above_the_limit_the_oldest_files_go(tmp_path, monkeypatch):
    rec = recorder(tmp_path)
    for k in range(4):
        put(rec, t=H10 + k * 3600, close=True)
    assert len(rec.files()) == 4
    sizes = [size for _, size in rec.files()]
    monkeypatch.setattr(recording, "LIMIT", sum(sizes) - sizes[0] - 1)
    put(rec, n=1, t=H10 + 3 * 3600, close=True)  # (a little more in the newest)
    names = [p.name for p, _ in rec.files()]
    assert names == ["20261031-12.jsonl.gz", "20261031-13.jsonl.gz"]
    assert rec.status()["size"] <= recording.LIMIT
    monkeypatch.setattr(recording, "LIMIT", 1)
    put(rec, n=1, t=H10 + 3 * 3600)
    assert [p.name for p, _ in rec.files()] == ["20261031-13.jsonl.gz"]  # never the one being written


def test_a_restart_in_the_same_hour_goes_on_in_the_file(tmp_path):
    rec = recorder(tmp_path)
    put(rec, close=True)
    size = rec.files()[0][1]
    rec = recorder(tmp_path)
    put(rec, n=2, t=H10 + 10, close=True)
    path = rec.files()[0][0]
    assert rec.files()[0][1] > size and uptimes(path) == [0, 1, 2, 0, 1]
    assert sum(m["topic"] == CONFIG for m in lines(path)) == 2
    assert subprocess.run(["gzip", "-t", str(path)], check=False).returncode == 0  # another reader agrees


@pytest.mark.parametrize("cut", [0, 1, 7, 2000])
def test_a_crash_loses_at_most_the_last_write_and_breaks_no_file(tmp_path, cut):
    # a file as a crash leaves it: written and flushed, not closed; cut: a power cut in the middle of the write
    rec = recorder(tmp_path / "live")
    put(rec, n=1000)
    path = rec.files()[0][0]
    flushed = path.stat().st_size
    put(rec, n=1000, t=H10 + 10)
    crash = tmp_path / "crash"
    (crash / "recordings").mkdir(parents=True)
    copy = crash / "recordings" / path.name
    copy.write_bytes(path.read_bytes()[:path.stat().st_size - cut])
    rec.file.close()
    if cut < path.stat().st_size - flushed:
        assert len(uptimes(copy)) >= 1000  # readable up to the last flush, whole lines only

    rec = recorder(crash)
    put(rec, n=2, t=H10 + 20, close=True)
    got = uptimes(copy)
    assert got[-2:] == [0, 1] and got[:1000] == list(range(1000))  # what was before and what came after
    assert subprocess.run(["gzip", "-t", str(copy)], check=False).returncode == 0
    with gzip.open(copy) as f:
        assert f.read().endswith(b"\n")  # read to the end, nothing broken in between


def test_repair_leaves_a_sound_file_alone(tmp_path):
    rec = recorder(tmp_path)
    put(rec, close=True)
    path = rec.files()[0][0]
    before = path.stat().st_mtime_ns, path.read_bytes()
    repair(path)
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_garbage_at_the_end_of_a_file_is_cut_off(tmp_path):
    rec = recorder(tmp_path)
    put(rec, n=1000)
    path = rec.files()[0][0]
    data = path.read_bytes()
    rec.file.close()
    path.write_bytes(data + bytes(4096))  # what a power cut can leave: blocks of zeros
    repair(path)
    got = uptimes(path)  # (the last few lines go with the broken block)
    assert len(got) > 950 and got == list(range(len(got)))


def test_a_failed_write_stops_the_recording_not_the_app(tmp_path, caplog):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    (tmp_path / "recordings").write_text("a file where the directory belongs")  # a real OSError
    app = App(tmp_path, publish=False)
    app.recorder.on = True
    frame = json.dumps(FRAME).encode()
    app.on_message("presence/a/frame", frame, 1000.0)

    async def main():
        await app.recorder.flush()

    asyncio.run(main())
    assert app.recorder.on is False and app.recorder.error["error"] == "recording_failed"
    assert app.recorder.status()["error"]["detail"]
    assert json.loads((tmp_path / "recording.json").read_text())["on"] is False
    assert sum("recording stopped" in r.message for r in caplog.records) == 1
    app.on_message("presence/a/frame", json.dumps({**FRAME, "uptime_ms": 2000}).encode(), 1001.0)
    assert app.tracker.runtime["a"].frames == 2  # the model goes on
    assert not app.recorder.pending


def test_a_write_that_fails_halfway_stops_too(tmp_path, monkeypatch):
    rec = recorder(tmp_path)
    put(rec)
    monkeypatch.setattr(recording.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    put(rec, t=H10 + 10)  # must not raise
    monkeypatch.undo()
    assert rec.on is False and "No space left" in rec.error["detail"] and rec.file is None
    rec.on = True  # switched on again: the file is whole up to what was flushed
    put(rec, n=2, t=H10 + 20, close=True)
    assert uptimes(rec.files()[0][0])[:3] == [0, 1, 2] and uptimes(rec.files()[0][0])[-2:] == [0, 1]


def test_a_stop_ends_the_file(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    app.recorder.on = True

    async def main():
        run = asyncio.create_task(app.run("127.0.0.1", 0))
        await asyncio.sleep(0.2)
        put(app.recorder, write=False)
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)

    asyncio.run(main())
    path = app.recorder.files()[0][0]
    assert uptimes(path) == [0, 1, 2]
    assert subprocess.run(["gzip", "-t", str(path)], check=False).returncode == 0


def test_a_replay_records_nothing(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    (tmp_path / "recording.json").write_text('{"on": true, "since": 1.0}')
    app = App(tmp_path, publish=False, replay=["x"])
    assert app.recorder.on is False
    app.on_message("presence/a/frame", json.dumps(FRAME).encode(), 1000.0)
    assert not app.recorder.pending

    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            r = await client.put("/api/recording", json={"on": True})
            return r.status, await r.json()

    status, body = asyncio.run(main())
    assert status == 409 and body == {"error": "recording_replay"}
    assert app.recorder.on is False and not (tmp_path / "recordings").exists()
    assert json.loads((tmp_path / "recording.json").read_text())["on"] is True  # the live switch stays as it was


def test_only_what_the_app_takes_is_recorded(tmp_path):
    # nothing but the frames, the boards' status and the switch; a frame the app drops is not in it, as in a report
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    app.recorder.on = True
    t = H10 + 1
    app.on_message("presence/a/frame", json.dumps(FRAME).encode(), t)
    app.on_message("presence/a/frame", b"not a frame", t)
    app.on_message("presence/a/status", b"online", t)
    app.on_message("presence/a/sensor/wifi_signal/state", b"-60", t)
    app.on_message("homeassistant/person/leon/state", b"home", t)
    app.on_message("person.leon", b"not_home", t)
    app.on_message(pause.COMMAND, b"ON", t)
    assert [m[1] for m in app.recorder.pending] == ["presence/a/frame", "presence/a/status", pause.COMMAND]


def test_nothing_of_it_in_tracker_json_and_the_switch_survives_a_restart(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    before = (tmp_path / "tracker.json").read_bytes()
    app = App(tmp_path, publish=False)
    geometry = app.geometry

    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            r = await client.get("/api/recording")
            assert (await r.json())["on"] is False  # off by default
            r = await client.put("/api/recording", json={"on": True})
            return await r.json()

    status = asyncio.run(main())
    assert status["on"] is True and status["since"] and status["limit"] == recording.LIMIT
    assert (tmp_path / "tracker.json").read_bytes() == before
    again = App(tmp_path, publish=False)
    assert again.recorder.on is True and again.recorder.since == status["since"]
    assert again.geometry == geometry
    assert "recording" not in again.config.to_dict() and "recording" not in again.config.model_json()
    assert presence_core.geometry_key(again.config.model_json()) == geometry


def test_a_saved_configuration_is_a_line_when_it_changed(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    app.recorder.on = True

    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            config = (await (await client.get("/api/config")).json())["config"]
            assert (await client.put("/api/config", json=config)).status == 200
            first = len(app.recorder.pending)  # the same configuration: no line
            config["params"]["occupied_off"] = 0.25
            assert (await client.put("/api/config", json=config)).status == 200
            return first, config

    first, config = asyncio.run(main())
    assert first == 0
    t, topic, payload = app.recorder.pending[0]
    saved = json.loads(payload)
    assert topic == CONFIG and saved["config"]["params"]["occupied_off"] == 0.25
    assert saved["version"] and len(app.recorder.pending) == 1


def test_download_has_every_recording_and_nothing_else(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    rec = app.recorder
    rec.on = True
    put(rec, t=H10, close=True)
    put(rec, t=H10 + 3600)  # the one being written
    folder = tmp_path / "recordings"
    secret = tmp_path / "secret.jsonl.gz"
    secret.write_bytes(b"not a recording")
    (folder / "20261031-09.jsonl.gz").symlink_to(secret)
    (folder / "../20261031-08.jsonl.gz").write_bytes(b"outside")
    (folder / "note.jsonl.gz").write_bytes(b"another name")
    (folder / "20261031-07.jsonl.gz").mkdir()

    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            r = await client.get("/api/recording/download")
            assert r.headers["Content-Type"] == "application/x-tar" and "attachment" in r.headers["Content-Disposition"]
            body = await r.read()
            assert (await client.get("/api/recording/..%2Fsecret.jsonl.gz")).status in (404, 405)
            return body

    body = asyncio.run(main())
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as tar:
        names = tar.getnames()
        assert names == ["recordings/20261031-10.jsonl.gz", "recordings/20261031-11.jsonl.gz"]
        for name in names:
            data = tar.extractfile(name).read()
            assert uptimes_of(data) == [0, 1, 2]  # both whole: also the one being written
    put(rec, n=1, t=H10 + 3610)  # it goes on, in the same file
    assert uptimes(folder / "20261031-11.jsonl.gz") == [0, 1, 2, 0]


def uptimes_of(data: bytes):
    return [json.loads(t)["payload"]["uptime_ms"] for t in gzip.decompress(data).splitlines() if b"/frame" in t]


def test_delete_all_also_while_recording(tmp_path):
    flat_config(entry=True).save(tmp_path / "tracker.json")
    app = App(tmp_path, publish=False)
    rec = app.recorder
    rec.on = True
    put(rec)

    async def main():
        async with TestClient(TestServer(app.web_app())) as client:
            r = await client.delete("/api/recording")
            return await r.json()

    status = asyncio.run(main())
    assert status["files"] == 0 and status["oldest"] is None and status["on"] is True
    put(rec, n=2, t=H10 + 10, close=True)  # a new file, not one that was unlinked
    path = rec.files()[0][0]
    assert uptimes(path) == [0, 1] and lines(path)[0]["topic"] == CONFIG


def test_a_big_download_is_streamed(tmp_path):
    # no file in memory: pieces, each at most CHUNK
    rec = recorder(tmp_path)
    put(rec, n=2, close=True)
    (tmp_path / "recordings" / "20261031-09.jsonl.gz").write_bytes(bytes(5 * recording.CHUNK // 2))
    pieces = []

    async def write(data):
        pieces.append(len(data))

    asyncio.run(rec.stream(write))
    assert max(pieces) <= recording.CHUNK and sum(pieces) % 512 == 0


def test_the_replay_passes_over_the_configuration_line(tmp_path):
    # gunzip | --replay: app/config means nothing to the app, the switch is taken as recorded
    rec = recorder(tmp_path / "rec")
    frame = {**FRAME, "uptime_ms": 5000}
    rec.add(H10 + 1, "presence/a/frame", json.dumps(frame).encode())
    rec.add(H10 + 2, pause.COMMAND, b"ON")
    rec.add(H10 + 3, "presence/a/frame", json.dumps({**frame, "uptime_ms": 7000}).encode())
    rec._write(rec._take(), close=True)
    plain = tmp_path / "20261031-10.jsonl"
    plain.write_bytes(gzip.open(rec.files()[0][0]).read())
    data = tmp_path / "data"
    data.mkdir()
    flat_config(entry=True).save(data / "tracker.json")
    app = App(data, publish=False, replay=[str(plain)], speed=1000.0)
    asyncio.run(replay_source([plain], ReplayClock(1000.0), app.on_message, loop_forever=False))
    assert app.tracker.runtime["a"].frames == 2 and app.pauses.on is True


def test_backups_leave_out_the_recordings_only():
    # the Supervisor matches backup_exclude with PurePath.match against the file's full path
    import pathlib

    text = (pathlib.Path(__file__).parents[1] / "config.yaml").read_text()
    pattern = text.split("backup_exclude:\n  - ")[1].split("\n")[0]
    data = pathlib.PurePosixPath("/data/addons/data/abc_presence_tracker")
    assert (data / "recordings/20261031-10.jsonl.gz").match(pattern)
    for keep in ("reports/20261031-100000-bad-other.jsonl.gz", "tracker.json", "recording.json", "images/plan.png"):
        assert not (data / keep).match(pattern), keep
