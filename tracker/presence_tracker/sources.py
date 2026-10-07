"""Where frames come from: the MQTT broker, or a recording played back."""

import asyncio
import json
import logging
import pathlib
import time

log = logging.getLogger(__name__)


class Clock:
    """Wall clock; the replay replaces it with the recording's time."""

    def __call__(self) -> float:
        return time.time()


class ReplayClock(Clock):
    def __init__(self, speed: float):
        self.speed = speed
        self.origin = None  # (recording time, monotonic time)

    def start(self, t: float):
        self.origin = (t, time.monotonic())

    def __call__(self) -> float:
        if self.origin is None:
            return 0.0
        t0, m0 = self.origin
        return t0 + (time.monotonic() - m0) * self.speed


async def mqtt_source(settings: dict, prefix: str, on_message, on_client):
    """Subscribe to the sensor frames. Calls on_client(client or None) on (dis)connect."""
    import aiomqtt

    from .ha import AVAILABILITY

    while True:
        try:
            async with aiomqtt.Client(
                settings["host"], int(settings.get("port", 1883)),
                username=settings.get("username") or None, password=settings.get("password") or None,
                identifier=f"presence-tracker-{int(time.time())}",
                will=aiomqtt.Will(AVAILABILITY, "offline", retain=True),
            ) as client:
                log.info("connected to MQTT %s:%s", settings["host"], settings.get("port", 1883))
                await client.subscribe(f"{prefix}/+/frame")
                await client.subscribe(f"{prefix}/+/status")
                await on_client(client)
                async for msg in client.messages:
                    try:
                        on_message(str(msg.topic), msg.payload, time.time())
                    except Exception:  # noqa: BLE001 - one message must not end the connection
                        log.exception("MQTT message on %s not handled", msg.topic)
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
                    wait = (m["t"] - clock()) / clock.speed
                    # always yield, so the web server stays responsive while catching up
                    await asyncio.sleep(max(wait, 0))
                    payload = m["payload"]
                    raw = json.dumps(payload).encode() if not isinstance(payload, str) else payload.encode()
                    on_message(m["topic"], raw, m["t"])
        if not loop_forever:
            return
        clock.origin = None
