"""Where frames come from: the MQTT broker, or a recording played back."""

import asyncio
import json
import logging
import math
import pathlib
import time

log = logging.getLogger(__name__)


class Clock:
    """Wall clock; the replay replaces it with the recording's time."""

    def __call__(self) -> float:
        return time.time()


class ReplayClock(Clock):
    """The recording's time at the speed asked, but never past the next message not yet played (`due`): a replay
    that falls behind (the model takes longer than the recording's time allows) waits for its frames, as live time
    waits for nothing but the frames come with it. Until 0.54.2 it ran on at the speed asked: an overloaded replay
    (speed 30 on hermann) stepped the model up to 40 min past the frames it had yet to play, every sensor seemed
    silent for longer than its tracks live (MODEL.md 4.4), and every late frame started its tracks anew - 25
    people from 2 within minutes."""

    def __init__(self, speed: float):
        self.speed = speed
        self.origin = None  # (recording time, monotonic time)
        self.due = math.inf  # the time of the next message not yet played

    def start(self, t: float):
        self.origin = (t, time.monotonic())

    def __call__(self) -> float:
        if self.origin is None:
            return 0.0
        t0, m0 = self.origin
        return min(t0 + (time.monotonic() - m0) * self.speed, self.due)


async def mqtt_source(settings: dict, prefix: str, on_message, on_client, publish: bool = True):
    """Subscribe to the sensor frames. Calls on_client(client or None) on (dis)connect. publish: the
    app publishes the entities' availability - then the last will says "offline" when the connection
    dies, and a stop says it before the clean goodbye (which drops the will, MQTT 3.1.2.5). One that
    publishes nothing (--no-publish, a development run against the live broker) has no will: the
    broker said "offline" for the live app's entities when it died, and the live app, which sends its
    availability only when it changes, never said "online" again (until 0.28)."""
    import aiomqtt

    from .ha import AVAILABILITY

    while True:
        try:
            async with aiomqtt.Client(
                settings["host"], int(settings.get("port", 1883)),
                username=settings.get("username") or None, password=settings.get("password") or None,
                identifier=f"presence-tracker-{int(time.time())}",
                will=aiomqtt.Will(AVAILABILITY, "offline", retain=True) if publish else None,
            ) as client:
                log.info("connected to MQTT %s:%s", settings["host"], settings.get("port", 1883))
                await client.subscribe(f"{prefix}/+/frame")
                await client.subscribe(f"{prefix}/+/status")
                await on_client(client)
                try:
                    async for msg in client.messages:
                        try:
                            on_message(str(msg.topic), msg.payload, time.time())
                        except Exception:  # noqa: BLE001 - one message must not end the connection
                            log.exception("MQTT message on %s not handled", msg.topic)
                except asyncio.CancelledError:
                    # the app stops: "offline" while the connection is still there (until 0.28 the app
                    # said it after the goodbye, in vain, and Home Assistant kept "online" and the states)
                    if publish:
                        try:
                            await asyncio.wait_for(client.publish(AVAILABILITY, "offline", qos=1, retain=True), 2.0)
                        except Exception as e:  # noqa: BLE001 - then the states stay as they were
                            log.warning("MQTT: \"offline\" not sent at the stop: %s", e)
                    raise
        except aiomqtt.MqttError as e:
            log.warning("MQTT: %s, reconnecting in 5 s", e)
            await on_client(None)
            await asyncio.sleep(5)
        except Exception:  # noqa: BLE001 - whatever it was, the app must stay connected (Node-RED keeps
            # a light's last state while it is gone)
            log.exception("MQTT loop failed, reconnecting in 5 s")
            try:
                await on_client(None)
            except Exception:  # noqa: BLE001
                log.exception("MQTT: disconnect not handled")
            await asyncio.sleep(5)


async def replay_source(paths: list, clock: ReplayClock, on_message, loop_forever: bool = True):
    """Play recordings (tools/record.py format) at clock.speed."""
    while True:
        for path in paths:
            log.info("replaying %s", path)
            with pathlib.Path(path).open() as f:
                for line in f:
                    m = json.loads(line)
                    if clock.origin is None:
                        clock.start(m["t"])
                    clock.due = m["t"]
                    wait = (m["t"] - clock()) / clock.speed
                    # always yield, so the web server stays responsive while catching up
                    await asyncio.sleep(max(wait, 0))
                    payload = m["payload"]
                    raw = json.dumps(payload).encode() if not isinstance(payload, str) else payload.encode()
                    on_message(m["topic"], raw, m["t"])
        if not loop_forever:
            return
        clock.origin = None
