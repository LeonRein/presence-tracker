"""Record the raw sensor stream from MQTT to JSON lines for later replay.

One line per message: {"t": <receive time, s>, "topic": "...", "payload": <JSON or string>}.
A new file is started every hour, so a long recording can be cut into scenes easily.

    uv run --with paho-mqtt --with pyyaml tools/record.py recordings/ 2>> recordings/record.log

Broker and login are read from esphome/secrets.yaml (mqtt_broker, mqtt_username, mqtt_password).
"""

import argparse
import json
import pathlib
import sys
import time

import paho.mqtt.client as mqtt
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", type=pathlib.Path)
    parser.add_argument("--topic", default="presence/#")
    parser.add_argument("--secrets", type=pathlib.Path, default=ROOT / "esphome" / "secrets.yaml")
    parser.add_argument("--broker", help="overrides mqtt_broker from the secrets")
    args = parser.parse_args()

    secrets = yaml.safe_load(args.secrets.read_text())
    args.out_dir.mkdir(parents=True, exist_ok=True)

    current = {"hour": None, "file": None, "count": 0}

    def output():
        hour = time.strftime("%Y%m%d-%H")
        if hour != current["hour"]:
            if current["file"]:
                current["file"].close()
            path = args.out_dir / f"{hour}.jsonl"
            current["file"] = path.open("a", buffering=1)
            current["hour"] = hour
            print(f"writing {path}", file=sys.stderr)
        return current["file"]

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"connected: {reason_code}", file=sys.stderr)
        if reason_code.is_failure:
            client.disconnect()
            sys.exit(f"broker refused the connection: {reason_code}")
        client.subscribe(args.topic)

    def on_message(client, userdata, msg):
        t = time.time()
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            payload = msg.payload.decode(errors="replace")
        line = json.dumps({"t": round(t, 3), "topic": msg.topic, "payload": payload}, separators=(",", ":"))
        output().write(line + "\n")
        current["count"] += 1

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"presence-recorder-{int(time.time())}")
    client.username_pw_set(secrets["mqtt_username"], secrets["mqtt_password"])
    client.on_connect = on_connect
    client.on_message = on_message
    # the network may drop for a moment (5.10., 22:11 the recorder died with it and the night was
    # lost): try again every 5 s instead of ending
    while True:
        try:
            client.connect(args.broker or secrets["mqtt_broker"], int(secrets.get("mqtt_port", 1883)))
            client.loop_forever(retry_first_connection=True)
            break
        except KeyboardInterrupt:
            break
        except OSError as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} connection lost ({e}), retrying in 5 s", file=sys.stderr)
            time.sleep(5)
    print(f"{current['count']} messages", file=sys.stderr)


if __name__ == "__main__":
    main()
