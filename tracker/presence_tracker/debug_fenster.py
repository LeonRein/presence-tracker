"""DEBUGGING-WERKZEUG (vorübergehend, zum späteren Entfernen): die Fensterkontakte in der Aufzeichnung.

Nur für den Prüfstand: Zeiten mit offenem Fenster werden je Raum nicht bewertet (beim Lüften bewegt der Wind
Vorhänge und Pflanzen), und Home Assistant behält seinen Verlauf nur etwa 10 Tage. Darum schreibt die App, solange
die Aufzeichnung läuft, den Zustand jedes Fensterkontakts (binary_sensor.*_fenster_contact) in die Aufnahme: eine
Zeile, wenn er sich ändert, und zu Beginn jeder Stunde, damit jede Datei für sich vollständig ist.

Das Modell sieht davon nichts: Die Zeilen haben das Thema DEBUG_TOPIC, das weder die App noch presence-replay als
Frame oder als Schalter lesen. "Lernen pausieren" gehört NICHT hierher: Der Schalter steuert das Lernen und wird seit
jeher aufgezeichnet, damit die Replays dort pausieren, wo die App es tat (pause.py).

Entfernen: diese Datei, ihren Aufruf in App.run (app.py, "debug_fenster") und tests/test_debug_fenster.py.
"""

import asyncio
import json
import logging
import re
import time

import aiohttp

from .util import Throttled

log = logging.getLogger(__name__)
throttled = Throttled(log)
DEBUG_TOPIC = "debug/fenster"  # kein Präfix der Sensoren: kein Leser der Aufnahmen nimmt es als Frame
PATTERN = re.compile(r"binary_sensor\.[a-z0-9_]+_fenster_contact")
POLL = 10.0  # s zwischen zwei Abfragen der Kontakte
DISCOVER = 3600.0  # s zwischen zwei Suchen nach Kontakten (neue Fenster)


def line_payload(entity_id: str, state: str, last_changed: str | None) -> bytes:
    return json.dumps({"entity_id": entity_id, "state": state, "last_changed": last_changed},
                      separators=(",", ":")).encode()


class Fenster:
    """Fragt Home Assistant alle POLL s nach den Fensterkontakten und gibt Änderungen an add(t, topic, payload)."""

    def __init__(self, ha_url: str, ha_token: str, add, recording_on):
        self.url = ha_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {ha_token}"}
        self.add = add
        self.recording_on = recording_on
        self.entities: list[str] = []
        self.last: dict[str, tuple] = {}
        self.next_discover = 0.0
        self.hour = None

    async def _get(self, session, path: str):
        async with session.get(self.url + path, headers=self.headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            r.raise_for_status()
            return await r.json()

    async def poll(self, session):
        now = time.time()
        if now >= self.next_discover:
            self.next_discover = now + DISCOVER
            states = await self._get(session, "/api/states")
            self.entities = sorted(s["entity_id"] for s in states if PATTERN.fullmatch(s["entity_id"]))
        hour = int(now // 3600)
        if hour != self.hour:  # jede Stunde alle Zustände: eine Aufnahme-Datei steht für sich
            self.hour, self.last = hour, {}
        for entity_id in self.entities:
            s = await self._get(session, f"/api/states/{entity_id}")
            key = (s.get("state"), s.get("last_changed"))
            if self.last.get(entity_id) != key:
                self.last[entity_id] = key
                self.add(time.time(), DEBUG_TOPIC, line_payload(entity_id, *key))

    async def run(self):
        async with aiohttp.ClientSession() as session:
            while True:
                if self.recording_on():
                    try:
                        await self.poll(session)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - nur ein Debugging-Werkzeug: die App läuft weiter
                        throttled("poll", logging.WARNING, "debug_fenster: Home Assistant nicht erreichbar: %r", e)
                        self.next_discover = 0.0
                else:
                    self.last, self.hour = {}, None  # nach dem Einschalten zuerst alle Zustände
                await asyncio.sleep(POLL)
