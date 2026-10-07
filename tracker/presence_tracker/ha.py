"""Home Assistant entities via MQTT discovery: one device, four entities per room/area zone of the home
and a fifth ("Ziel") per room (not for an entry room: the stairwell is outside, MODEL.md 6)."""

import json
import re

PREFIX = "presence-tracker"
AVAILABILITY = f"{PREFIX}/status"
DEVICE = {"identifiers": ["presence_tracker"], "name": "Presence Tracker", "manufacturer": "presence-tracker",
          "model": "Multi-sensor radar tracker"}


def slug(text: str) -> str:
    text = (text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "zone"


def _entities(zone_id: str, name: str, room: bool = False) -> list:
    """(component, object_id suffix, config) for one zone; "Ziel" only for a room."""
    state = f"{PREFIX}/zone/{zone_id}/state"
    base = {"state_topic": state, "availability_topic": AVAILABILITY, "device": DEVICE}
    target = [
        # MODEL.md 6: somebody walking goes into the room next (their motion, and the learned map where
        # it knows enough); the probability that decides, from where, how far, and which part decided
        ("binary_sensor", "ziel", {**base, "name": f"{name} Ziel", "icon": "mdi:walk",
                                   "value_template": "{{ 'ON' if value_json.target else 'OFF' }}",
                                   "json_attributes_topic": state,
                                   "json_attributes_template": "{{ {'probability': value_json.p_target | default(none), 'from': value_json.target_from | default(none), 'distance': value_json.target_distance | default(none), 'eta': value_json.target_eta | default(none), 'person': value_json.target_person | default(none), 'walks': value_json.target_walks | default(none), 'source': value_json.target_source | default(none), 'weight': value_json.target_weight | default(none)} | tojson }}"}),
    ] if room else []
    return [
        ("binary_sensor", "occupancy", {**base, "name": f"{name} besetzt", "device_class": "occupancy",
                                         "value_template": "{{ 'ON' if value_json.occupied else 'OFF' }}"}),
        ("sensor", "count", {**base, "name": f"{name} Personen", "icon": "mdi:account-multiple",
                              "state_class": "measurement", "value_template": "{{ value_json.count }}",
                              "json_attributes_topic": state,
                              "json_attributes_template": "{{ {'moving': value_json.moving, 'still': value_json.still, 'probability': value_json.probability | default(none)} | tojson }}"}),
        ("binary_sensor", "moving", {**base, "name": f"{name} Bewegung", "device_class": "motion",
                                      "value_template": "{{ 'ON' if value_json.moving > 0 else 'OFF' }}"}),
        # MODEL.md 6: P(somebody walking enters within the look-ahead) above its threshold; the
        # probability, time, distance and who as attributes (for Node-RED and tuning)
        ("binary_sensor", "approaching", {**base, "name": f"{name} wird betreten", "icon": "mdi:walk",
                                           "value_template": "{{ 'ON' if value_json.approaching else 'OFF' }}",
                                           "json_attributes_topic": state,
                                           "json_attributes_template": "{{ {'p_enter': value_json.p_enter | default(none), 'eta': value_json.eta | default(none), 'distance': value_json.distance | default(none), 'person': value_json.person | default(none)} | tojson }}"}),
    ] + target


DISCOVERY = "homeassistant/+/presence_tracker/+/config"  # every discovery config of this device
# the on/off states and counts: published whenever they change
FLAGS = ("count", "occupied", "moving", "still", "approaching", "target")
# attributes kept until they move by this much (two steps of their quantization, ZoneState.to_dict)
STEADY = {"probability": 0.1, "p_enter": 0.1, "p_target": 0.1, "eta": 1.0, "distance": 1.0,
          "target_eta": 1.0, "target_distance": 1.0, "target_weight": 0.2, "target_walks": 2.0}


class Discovery:
    """Keeps the published discovery configs in sync with the zones and publishes their states.

    What the broker retains from earlier runs (DISCOVERY, subscribed at connect) and is no longer
    wanted - a zone deleted, or a room that became an entry room (the stairwell is outside), while
    the app was not running - is cleared with its state topic: empty retained payloads, so Home
    Assistant removes the entities."""

    def __init__(self, publish):
        self.publish = publish  # async (topic, payload: str, retain: bool)
        self.published: dict[str, str] = {}  # discovery topic -> payload
        self.last_state: dict[str, str] = {}
        self.wanted: dict[str, str] = {}  # discovery topic -> payload, as of the last sync
        self.stale: set[str] = set()  # retained topics of entities no longer wanted, to be cleared
        self.sent: dict[str, dict] = {}  # zone id -> the last payload (steady)

    def steady(self, zone_id: str, d: dict) -> dict:
        """The payload to publish: an attribute that moved by less than its tolerance (STEADY) keeps
        its last published value, unless an on/off state of the zone changed (then all are new).
        Each change of an attribute is a new row in Home Assistant's recorder; the states flip
        exactly when they do."""
        last = self.sent.get(zone_id)
        if last is not None and all(d.get(k) == last.get(k) for k in FLAGS):
            d = dict(d)
            for k, tol in STEADY.items():
                a, b = d.get(k), last.get(k)
                if a and b is not None and abs(a - b) < tol - 1e-9:  # 0: nothing about to happen, at once
                    d[k] = b
        self.sent[zone_id] = d
        return d

    def retained(self, topic: str, payload: bytes) -> bool:
        """A message on a discovery topic of this device (DISCOVERY): if its entity is no longer
        wanted, it and its state are to be cleared (by the next states()). False for other topics."""
        parts = topic.split("/")
        if len(parts) != 5 or parts[0] != "homeassistant" or parts[2] != "presence_tracker" or parts[4] != "config":
            return False
        if payload and topic not in self.wanted:
            self.stale.add(topic)
            try:
                state = json.loads(payload).get("state_topic")
            except (ValueError, AttributeError):
                state = None
            if isinstance(state, str) and state.startswith(f"{PREFIX}/zone/"):
                self.stale.add(state)
        return True

    async def sync(self, zones: list):
        wanted = {}
        # the rooms and areas of the home; an entry room (the stairwell) is outside, it gets none
        items = [(z.id, z.name, z.kind == "room") for z in zones if z.kind in ("room", "area") and not (z.kind == "room" and z.entry)]
        items += [("_total", "Haus", False)]
        for zone_id, name, room in items:
            zid = "total" if zone_id == "_total" else slug(zone_id)
            for component, suffix, cfg in _entities(zone_id, name, room):
                uid = f"presence_tracker_{zid}_{suffix}"
                cfg = {**cfg, "unique_id": uid,
                       "default_entity_id": f"{component}.presence_{slug(name)}_{suffix}"}
                wanted[f"homeassistant/{component}/presence_tracker/{uid}/config"] = json.dumps(cfg)
        self.wanted = wanted
        for topic in set(self.published) - set(wanted):
            await self.publish(topic, "", True)
            del self.published[topic]
        for topic, payload in wanted.items():
            if self.published.get(topic) != payload:
                await self.publish(topic, payload, True)
                self.published[topic] = payload
        self.last_state.clear()

    async def states(self, states: dict, force: bool = False):
        wanted_states = {f"{PREFIX}/zone/{zone_id}/state" for zone_id in states}
        for topic in sorted(self.stale):
            self.stale.discard(topic)
            if topic not in self.wanted and topic not in wanted_states:
                await self.publish(topic, "", True)
        for zone_id, st in states.items():
            payload = json.dumps(self.steady(zone_id, st.to_dict()))
            if force or self.last_state.get(zone_id) != payload:
                await self.publish(f"{PREFIX}/zone/{zone_id}/state", payload, True)
                self.last_state[zone_id] = payload
