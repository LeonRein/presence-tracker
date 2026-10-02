"""Entry point.

As a Home Assistant app: no arguments; options come from /data/options.json, the MQTT login
from the Supervisor and the web UI is served through ingress.

For development:
    python -m presence_tracker --data devdata --replay ../recordings/20261003-11.jsonl --speed 2
    python -m presence_tracker --data devdata --mqtt-host 192.168.178.3 --mqtt-user ... --no-publish
"""

import argparse
import asyncio
import json
import logging
import os
import pathlib
import urllib.request

from .app import App

log = logging.getLogger("presence_tracker")


def supervisor_mqtt(token: str) -> dict | None:
    req = urllib.request.Request("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)["data"]
    except (OSError, ValueError, KeyError) as e:
        log.error("no MQTT service from the Supervisor (is the Mosquitto app installed?): %s", e)
        return None
    return {"host": data["host"], "port": data["port"], "username": data.get("username"),
            "password": data.get("password")}


def main():
    parser = argparse.ArgumentParser(description="Multi-sensor radar presence tracker")
    parser.add_argument("--data", type=pathlib.Path, default=pathlib.Path("/data"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--replay", nargs="*", help="recordings to play instead of MQTT")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--mqtt-host")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-user")
    parser.add_argument("--mqtt-password")
    parser.add_argument("--no-publish", action="store_true", help="don't create Home Assistant entities")
    parser.add_argument("--ha-url", default=os.environ.get("HA_URL"))
    parser.add_argument("--ha-token", default=os.environ.get("HA_TOKEN"))
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()

    options = {}
    options_file = args.data / "options.json"
    if options_file.exists():
        options = json.loads(options_file.read_text())
    logging.basicConfig(level=(args.log_level or options.get("log_level", "info")).upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    mqtt = None
    if args.mqtt_host:
        mqtt = {"host": args.mqtt_host, "port": args.mqtt_port, "username": args.mqtt_user,
                "password": args.mqtt_password}
    elif options.get("mqtt_host"):
        mqtt = {"host": options["mqtt_host"], "port": options.get("mqtt_port", 1883),
                "username": options.get("mqtt_username"), "password": options.get("mqtt_password")}
    elif supervisor_token and not args.replay:
        mqtt = supervisor_mqtt(supervisor_token)

    ha_url, ha_token = args.ha_url, args.ha_token
    if supervisor_token and not ha_url:
        ha_url, ha_token = "http://supervisor/core", supervisor_token

    app = App(args.data, prefix=options.get("topic_prefix", "presence"),
              publish=not args.no_publish and not args.replay,
              replay=args.replay, speed=args.speed, mqtt=mqtt, ha_url=ha_url, ha_token=ha_token)
    try:
        asyncio.run(app.run(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
