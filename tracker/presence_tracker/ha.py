"""Home Assistant entities via MQTT discovery: one device, four entities per room/area zone."""

import json
import re

PREFIX = "presence-tracker"
AVAILABILITY = f"{PREFIX}/status"
DEVICE = {"identifiers": ["presence_tracker"], "name": "Presence Tracker", "manufacturer": "presence-tracker",
          "model": "Multi-sensor radar tracker"}


def slug(text: str) -> str:
    text = (text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "zone"


def _entities(zone_id: str, name: str) -> list:
    """(component, object_id suffix, config) for one zone."""
    state = f"{PREFIX}/zone/{zone_id}/state"
    base = {"state_topic": state, "availability_topic": AVAILABILITY, "device": DEVICE}
    return [
        ("binary_sensor", "occupancy", {**base, "name": f"{name} besetzt", "device_class": "occupancy",
                                         "value_template": "{{ 'ON' if value_json.occupied else 'OFF' }}"}),
        ("sensor", "count", {**base, "name": f"{name} Personen", "icon": "mdi:account-multiple",
                              "state_class": "measurement", "value_template": "{{ value_json.count }}",
                              "json_attributes_topic": state,
                              "json_attributes_template": "{{ {'moving': value_json.moving, 'still': value_json.still} | tojson }}"}),
        ("binary_sensor", "moving", {**base, "name": f"{name} Bewegung", "device_class": "motion",
                                      "value_template": "{{ 'ON' if value_json.moving > 0 else 'OFF' }}"}),
        ("binary_sensor", "approaching", {**base, "name": f"{name} wird betreten", "icon": "mdi:walk",
                                           "value_template": "{{ 'ON' if value_json.approaching else 'OFF' }}"}),
    ]


class Discovery:
    """Keeps the published discovery configs in sync with the zones and publishes their states."""

    def __init__(self, publish):
        self.publish = publish  # async (topic, payload: str, retain: bool)
        self.published: dict[str, str] = {}  # discovery topic -> payload
        self.last_state: dict[str, str] = {}

    async def sync(self, zones: list):
        wanted = {}
        items = [(z.id, z.name) for z in zones if z.kind in ("room", "area")] + [("_total", "Haus")]
        for zone_id, name in items:
            zid = "total" if zone_id == "_total" else slug(zone_id)
            for component, suffix, cfg in _entities(zone_id, name):
                uid = f"presence_tracker_{zid}_{suffix}"
                cfg = {**cfg, "unique_id": uid,
                       "default_entity_id": f"{component}.presence_{slug(name)}_{suffix}"}
                wanted[f"homeassistant/{component}/presence_tracker/{uid}/config"] = json.dumps(cfg)
        for topic in set(self.published) - set(wanted):
            await self.publish(topic, "", True)
            del self.published[topic]
        for topic, payload in wanted.items():
            if self.published.get(topic) != payload:
                await self.publish(topic, payload, True)
                self.published[topic] = payload
        self.last_state.clear()

    async def states(self, states: dict, force: bool = False):
        for zone_id, st in states.items():
            payload = json.dumps(st.to_dict())
            if force or self.last_state.get(zone_id) != payload:
                await self.publish(f"{PREFIX}/zone/{zone_id}/state", payload, True)
                self.last_state[zone_id] = payload
