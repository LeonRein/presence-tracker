"""Home Assistant entities via MQTT discovery: one device, four entities per room/area zone of the home
and a fifth ("Ziel") per room (not for an entry room: the stairwell is outside, MODEL.md 6), and the
switch "Lernen pausieren" (pause.py).

Availability: every entity is available while the app is (AVAILABILITY). The entities of a room with an
own sensor in use (roomseen.py) have a second topic, the room's own (zone_availability): "offline" while
its own sensors are all silent, so Home Assistant shows "unavailable" - "I don't know" - instead of
"empty" (MODEL.md 6 "Belegt"). Both must say "online" (availability_mode "all")."""

import json
import re
import time

PREFIX = "presence-tracker"
AVAILABILITY = f"{PREFIX}/status"
DEVICE = {"identifiers": ["presence_tracker"], "name": "Presence Tracker", "manufacturer": "presence-tracker",
          "model": "Multi-sensor radar tracker"}


def slug(text: str) -> str:
    text = (text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "zone"


def zone_availability(zone_id: str) -> str:
    """The room's own availability topic ("online" / "offline", retained)."""
    return f"{PREFIX}/zone/{zone_id}/availability"


def _entities(zone_id: str, name: str, room: bool = False, own: bool = False) -> list:
    """(component, object_id suffix, config) for one zone; "Ziel" only for a room; own: the room has an
    own sensor in use, its entities are also unavailable while it is silent."""
    state = f"{PREFIX}/zone/{zone_id}/state"
    if own:
        avail = {"availability": [{"topic": AVAILABILITY}, {"topic": zone_availability(zone_id)}],
                 "availability_mode": "all"}
    else:
        avail = {"availability_topic": AVAILABILITY}
    base = {"state_topic": state, **avail, "device": DEVICE}
    target = [
        # MODEL.md 6: somebody walking goes into the room next (their motion, and the learned map where
        # it knows enough); the probability that decides, from where, how far, and which part decided
        ("binary_sensor", "ziel", {**base, "name": f"{name} Ziel", "icon": "mdi:walk",
                                   "value_template": "{{ 'ON' if value_json.target else 'OFF' }}",
                                   "json_attributes_topic": state,
                                   "json_attributes_template": "{{ {'probability': value_json.p_target | default(none), 'from': value_json.target_from | default(none), 'distance': value_json.target_distance | default(none), 'eta': value_json.target_eta | default(none), 'person': value_json.target_person | default(none), 'walks': value_json.target_walks | default(none), 'source': value_json.target_source | default(none), 'weight': value_json.target_weight | default(none)} | tojson }}"}),
    ] if room else []
    return [
        # MODEL.md 6 "Belegt": the filter's probability, or the room's own LD2450 measuring somebody; the
        # attribute quelle says which: filter / ld2450 / beide (none while free)
        ("binary_sensor", "occupancy", {**base, "name": f"{name} besetzt", "device_class": "occupancy",
                                         "value_template": "{{ 'ON' if value_json.occupied else 'OFF' }}",
                                         "json_attributes_topic": state,
                                         "json_attributes_template": "{{ {'quelle': value_json.source | default(none)} | tojson }}"}),
        ("sensor", "count", {**base, "name": f"{name} Personen", "icon": "mdi:account-multiple",
                              "state_class": "measurement", "value_template": "{{ value_json.count }}",
                              "json_attributes_topic": state,
                              "json_attributes_template": "{{ {'moving': value_json.count_moving | default(value_json.moving), 'still': value_json.count_still | default(value_json.still), 'probability': value_json.probability | default(none)} | tojson }}"}),
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
# "Lernen pausieren" (pause.py): a switch for Node-RED, while background activity is expected (the vacuum
# robot, visitors, a cleaning person, a party). Home Assistant publishes its commands retained (the app gets
# the last one at every connect, also one sent while it was down); the app publishes its state retained.
PAUSE_COMMAND = f"{PREFIX}/learning_pause/set"
PAUSE_STATE = f"{PREFIX}/learning_pause/state"
PAUSE_SWITCH = ("switch", "lernen_pausieren", {
    "name": "Lernen pausieren", "icon": "mdi:school-outline", "entity_category": "config",
    "command_topic": PAUSE_COMMAND, "state_topic": PAUSE_STATE, "payload_on": "ON", "payload_off": "OFF",
    # no availability topic: switchable also while the app is down (Home Assistant drops a command to an
    # unavailable entity); the app takes the retained command when it connects
    "retain": True, "device": DEVICE})
# Each change of an entity's state or attributes is a row in Home Assistant's recorder. The states go
# out whenever they change; the attributes of each entity (a group of the payload) are held
# (Discovery.steady): new with every flip of the entity's state, else only when one moved by more than
# its tolerance and the group's last change is at least GAP s old. With "wird betreten" / "Ziel" off they
# are one steady payload (probability 0, the rest empty).
GROUPS = {
    # entity state -> (its attributes {key: tolerance (None: any change)}, GAP s, quiet while off)
    # "quelle" of "besetzt" changes between filter and beide whenever the room's LD2450 measures somebody
    "occupied": ({"source": None}, 10.0, True),
    "count": ({"probability": 0.1, "count_moving": None, "count_still": None}, 60.0, False),
    "approaching": ({"p_enter": 0.1, "eta": 1.0, "distance": 1.0, "person": None}, 10.0, True),
    "target": ({"p_target": 0.1, "target_from": None, "target_distance": 1.0, "target_eta": 1.0, "target_person": None,
                "target_walks": 2.0, "target_source": None, "target_weight": 0.2}, 10.0, True),
}
QUIET = {"p_enter": 0.0, "p_target": 0.0}  # the rest None


class Discovery:
    """Keeps the published discovery configs in sync with the zones and publishes their states.

    What the broker retains from earlier runs (DISCOVERY, subscribed at connect) and is no longer
    wanted - a zone deleted, or a room that became an entry room (the stairwell is outside), while
    the app was not running - is cleared with its state topic: empty retained payloads, so Home
    Assistant removes the entities. So is a room's own availability topic no wanted config refers to
    any more (the zone deleted, or its own sensor gone, renamed, switched off)."""

    def __init__(self, publish):
        self.publish = publish  # async (topic, payload: str, retain: bool)
        self.published: dict[str, str] = {}  # discovery topic -> payload
        self.last_state: dict[str, str] = {}
        self.wanted: dict[str, str] = {}  # discovery topic -> payload, as of the last sync
        self.stale: set[str] = set()  # retained topics of entities no longer wanted, to be cleared
        self.sent: dict[str, dict] = {}  # zone id -> the last payload (steady)
        self.stamps: dict[str, dict] = {}  # zone id -> entity state -> when its attributes last changed
        self.state_zones: set[str] = set()  # zones whose state this run published (retained)
        self.referenced: set[str] = set()  # the zone topics the wanted configs refer to (state, availability)
        self.watched: set[str] = set()  # zone ids with their own availability (an own sensor in use), as of the last sync
        self.avail_sent: dict[str, str] = {}  # zone id -> what its availability topic last got on this connection

    def reconnected(self):
        """A new connection: every state and availability goes out again with the next states()."""
        self.last_state.clear()
        self.avail_sent.clear()

    def steady(self, zone_id: str, d: dict, t: float | None = None) -> dict:
        """The payload to publish for Home Assistant (GROUPS): the states as they are, each entity's
        attributes held. The count sensor's own copies of moving / still (count_moving, count_still)
        are held with it; the moving sensor's state uses the live moving."""
        t = time.monotonic() if t is None else t
        d = dict(d, count_moving=d.get("moving"), count_still=d.get("still"))
        last = self.sent.get(zone_id)
        stamps = self.stamps.setdefault(zone_id, {})
        for flag, (keys, gap, quiet) in GROUPS.items():
            if flag not in d:
                continue
            if quiet and not d[flag]:
                for k in keys:
                    if k in d:
                        d[k] = QUIET.get(k)
                continue
            if last is None or last.get(flag) != d[flag] or flag not in stamps:
                stamps[flag] = t
                continue
            moved = False
            for k, tol in keys.items():
                a, b = d.get(k), last.get(k)
                if a == b:
                    continue
                if a is None or b is None or tol is None or isinstance(a, str) or abs(a - b) >= tol - 1e-9:
                    moved = True
            if moved and not 0.0 <= t - stamps[flag] < gap:  # (a clock gone back: as passed)
                stamps[flag] = t
            else:
                for k in keys:
                    if k in last:
                        d[k] = last[k]
        self.sent[zone_id] = d
        return d

    def retained(self, topic: str, payload: bytes) -> bool:
        """A message on a discovery topic of this device (DISCOVERY): if its entity is no longer
        wanted, it and its state are to be cleared (by the next states()). False for other topics."""
        parts = topic.split("/")
        if len(parts) != 5 or parts[0] != "homeassistant" or parts[2] != "presence_tracker" or parts[4] != "config":
            return False
        if not payload:
            return True
        try:
            cfg = json.loads(payload)
            refs = [cfg.get("state_topic")] + [a.get("topic") for a in cfg.get("availability") or []]
        except (ValueError, AttributeError, TypeError):
            refs = []
        if topic not in self.wanted:
            self.stale.add(topic)
        # its state topic if the entity goes; a room's own availability topic also when only that went
        # (the entity stays, its own sensor is gone)
        self.stale.update(r for r in refs if isinstance(r, str) and r.startswith(f"{PREFIX}/zone/")
                          and (topic not in self.wanted or r not in self.referenced))
        return True

    async def sync(self, zones: list, watched=()):
        """The wanted discovery configs for the zones; watched: the rooms with an own sensor in use
        (roomseen.RoomSeen.watched), their entities get the room's own availability too."""
        watched = set(watched)
        wanted = {}
        # the rooms and areas of the home; an entry room (the stairwell) is outside, it gets none
        items = [(z.id, z.name, z.kind == "room") for z in zones if z.kind in ("room", "area") and not (z.kind == "room" and z.entry)]
        items += [("_total", "Haus", False)]
        for zone_id, name, room in items:
            zid = "total" if zone_id == "_total" else slug(zone_id)
            for component, suffix, cfg in _entities(zone_id, name, room, own=room and zone_id in watched):
                uid = f"presence_tracker_{zid}_{suffix}"
                cfg = {**cfg, "unique_id": uid,
                       "default_entity_id": f"{component}.presence_{slug(name)}_{suffix}"}
                wanted[f"homeassistant/{component}/presence_tracker/{uid}/config"] = json.dumps(cfg)
        component, suffix, cfg = PAUSE_SWITCH
        uid = f"presence_tracker_{suffix}"
        cfg = {**cfg, "unique_id": uid, "default_entity_id": f"{component}.presence_{suffix}"}
        wanted[f"homeassistant/{component}/presence_tracker/{uid}/config"] = json.dumps(cfg)
        self.wanted = wanted
        self.watched = {zone_id for zone_id, _, room in items if room and zone_id in watched}
        self.referenced = {f"{PREFIX}/zone/{zone_id}/state" for zone_id, _, _ in items}
        self.referenced |= {zone_availability(z) for z in self.watched}
        for topic in set(self.published) - set(wanted):
            await self.publish(topic, "", True)
            del self.published[topic]
        for topic, payload in wanted.items():
            if self.published.get(topic) != payload:
                await self.publish(topic, payload, True)
                self.published[topic] = payload
        self.last_state.clear()

    async def _availability(self, zone_id: str, value: str):
        """A room's own availability, retained, when it changed (or a new connection)."""
        if self.avail_sent.get(zone_id) != value:
            await self.publish(zone_availability(zone_id), value, True)
            self.avail_sent[zone_id] = value

    async def states(self, states: dict, force: bool = False, t: float | None = None):
        """Publish the zones' states (held, steady); t: the model's time (default: the clock). A room's
        own availability (ZoneState.available) goes "offline" before its state, "online" after it: back
        online, Home Assistant shows the fresh state at once."""
        wanted_states = {f"{PREFIX}/zone/{zone_id}/state" for zone_id in states}
        for topic in sorted(self.stale):
            self.stale.discard(topic)
            if topic not in self.wanted and topic not in wanted_states and topic not in self.referenced:
                await self.publish(topic, "", True)
        # a room deleted, or without an own sensor in use now: its availability topic goes
        for zone_id in sorted(set(self.avail_sent) - self.watched):
            await self.publish(zone_availability(zone_id), "", True)
            del self.avail_sent[zone_id]
        avail = {z: "online" if getattr(states[z], "available", True) else "offline"
                 for z in sorted(self.watched) if z in states}
        for zone_id, value in avail.items():
            if value == "offline":
                await self._availability(zone_id, value)
        # a zone deleted (or made the stairwell) while the app runs: its retained state goes too
        for zone_id in sorted(self.state_zones - set(states)):
            await self.publish(f"{PREFIX}/zone/{zone_id}/state", "", True)
            self.state_zones.discard(zone_id)
            for d in (self.last_state, self.sent, self.stamps):
                d.pop(zone_id, None)
        for zone_id, st in states.items():
            payload = json.dumps(self.steady(zone_id, st.to_dict(), t))
            if force or self.last_state.get(zone_id) != payload:
                await self.publish(f"{PREFIX}/zone/{zone_id}/state", payload, True)
                self.last_state[zone_id] = payload
                self.state_zones.add(zone_id)
        for zone_id, value in avail.items():
            if value == "online":
                await self._availability(zone_id, value)
