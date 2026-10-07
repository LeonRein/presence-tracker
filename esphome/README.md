# ESPHome-Konfiguration

Firmware für den Kombi-Sensor aus [`../case`](../case/README.md): LD2450 + LD2410C an einem ESP32-S3 SuperMini.
Getestet mit ESPHome 2026.9 (`esphome config` und `esphome compile`).

| Datei | Inhalt |
|---|---|
| `packages/radar-combo.yaml` | Alles Gemeinsame: Board, UARTs, Radare, Entitäten, MQTT-Stream |
| `presence-example.yaml` | Vorlage für einen Sensor, eine Datei pro Raum |
| `secrets.yaml.example` | Benötigte Secrets |

## Zwei Wege aus dem Sensor

**Native API → Home Assistant**: nur Radar-Einstellungen (Multi-Target, LD2410C-Schwellen und -Reichweite,
Neustart) und Diagnose (Status, WLAN, Firmware) sowie OTA. Messwerte gibt es in Home Assistant nicht:
die gesamte Auswertung macht die Tracker-App ([`../tracker`](../tracker/DOCS.md)), sie legt eigene Entitäten an.

**MQTT → Tracker**: Für das Verfolgen über mehrere Sensoren braucht das Python-Skript die Rohdaten
jedes Radar-Frames (ca. 11/s). Dafür schickt der Sensor pro Frame eine JSON-Nachricht an den Mosquitto-Broker.

Warum nicht beides über die API? Die API kennt nur Entitäten. Rohdaten mit 10 Hz als Entitäten
landen auch in Home Assistant (State-Machine, Recorder, Logbuch), und das Skript bräuchte eine eigene
API-Verbindung zu jedem Sensor. Über MQTT abonniert das Skript mit einer Verbindung `presence/+/frame`
und bekommt alle Sensoren. Home Assistant merkt davon nichts. Den Stream kann man mit `mosquitto_sub`
mitschneiden und später zum Entwickeln wieder abspielen. Das Skript kann sein Ergebnis (wer ist in welchem
Raum) per MQTT-Discovery zurück an Home Assistant geben. Mosquitto läuft hier ohnehin schon.

`discovery: false` ist gesetzt, sonst würden die Entitäten in Home Assistant doppelt auftauchen.
Ohne Broker läuft der Sensor normal weiter (`reboot_timeout: 0s`), nur der Stream fehlt dann.

## Frame-Format

Topic `presence/<name>/frame`, QoS 0, nicht retained:

```json
{
  "seq": 4711,
  "uptime_ms": 123456789,
  "targets": [
    {"slot": 1, "x": -420, "y": 1830, "speed": 0, "resolution": 360},
    {"slot": 3, "x": 650, "y": 2900, "speed": -180, "resolution": 360}
  ],
  "ld2410": {
    "moving": false, "still": true,
    "moving_distance": 0, "moving_energy": 0,
    "still_distance": 1900, "still_energy": 47,
    "move_gates": [12, 30, 8, 4, 2, 1, 0, 0, 0],
    "still_gates": [100, 64, 47, 20, 9, 5, 3, 2, 1]
  }
}
```

| Feld | Bedeutung |
|---|---|
| `seq` | Zähler, Lücken = verlorene Frames. Beginnt nach jedem Neustart bei 0 |
| `uptime_ms` | Zeit auf dem ESP. Für Zeitabstände zwischen Frames desselben Sensors, ohne WLAN-Jitter |
| `targets` | Nur erkannte Ziele, 0–3 Einträge. Koordinaten des LD2450 in mm, Sensor im Ursprung |
| `x` | quer zur Blickrichtung, mm |
| `y` | Abstand nach vorn, mm (immer positiv) |
| `speed` | radial in mm/s, negativ = kommt näher, 0 = steht |
| `resolution` | Abstandsauflösung des Radars, mm |
| `slot` | Platz 1–3 im LD2450. Bleibt meist gleich, ist aber **keine** feste Personen-ID |
| `ld2410` | Statusdaten des LD2410C, Abstände in mm, Energie 0–100 |
| `move_gates`, `still_gates` | Energie 0–100 je Entfernungsstufe (0,75 m, Stufe 0 = 0–0,75 m). Der Engineering Mode ist dafür immer an |

Wann gesendet wird: bei jedem LD2450-Frame (etwa alle 90 ms).
Ist nichts erkannt (kein Ziel, LD2410C ohne Präsenz), kommt einmal ein leerer Frame und danach
nur alle `idle_interval_ms` (5 s) ein Lebenszeichen. Das lässt sich in der Gerätedatei per `substitutions` ändern.

Gesendet wird aus einer eigenen Task (`mqtt: idf_send_async: true`): Die Hauptschleife legt den Frame nur
in eine Warteschlange (29 Nachrichten, etwa 2,6 s). Hängt das WLAN, liest sie die Radare weiter, statt im
Publish zu blockieren (ohne die Option: Frames verloren, nach 5 s Neustart durch den Task-Watchdog).
Die UART-Puffer (`rx_buffer_size: 2048`) fassen einige Sekunden Radar-Frames. Eine Lücke in `seq` heißt:
Frame in der Warteschlange verworfen (voll) oder unterwegs verloren; ein Sprung in `uptime_ms` bei
fortlaufendem `seq` heißt: Frame schon vor dem Senden verloren.

Nach dem Verbinden sucht ESPHome sonst nach 5 und 10 min einen besseren Access Point; diese Suche hielt
die Hauptschleife sekundenlang an. `wifi: post_connect_roaming: false` schaltet sie ab (7.10.2026:
Arbeitszimmer mit allem seit 13:13 ohne Neustart; Flur und Wohnzimmer mit alter Firmware im selben
Zeitraum dreimal neu gestartet, bei denselben Netz-Hängern).

Ob der Sensor online ist, steht retained in `presence/<name>/status` (`online` / `offline`, per Last Will).
Zusätzlich veröffentlicht ESPHome alle nicht-internen Entitäten unter `presence/<name>/…`.

### Hinweise für den Tracker

- **Koordinaten**: Die Position und Blickrichtung jedes Sensors im Hausplan gehört in die Konfiguration
  des Skripts, nicht in die Firmware. Dann muss man zum Einmessen nicht neu flashen.
  Umrechnung bei Sensor an `(px, py)` mit Blickrichtung `h` (Winkel zur X-Achse des Plans),
  wenn `x` vom Sensor aus gesehen nach rechts positiv ist:
  `X = px + y·cos(h) + x·sin(h)`, `Y = py + y·sin(h) − x·cos(h)`.
  **Das Vorzeichen von `x` beim Einbau prüfen** (nach rechts gehen und schauen, ob `x` steigt oder fällt).
  Es hängt davon ab, wie herum der Sensor hängt.
- Der LD2450 verliert stillsitzende Personen nach einiger Zeit. Der LD2410C erkennt sie weiter
  (`still`, nur Abstand, keine Richtung). Daran lässt sich sehen, dass ein verlorenes Ziel wahrscheinlich noch da ist.
- Der LD2410C hat **keine Haltezeit** (Timeout 0, setzt die Firmware beim Start): `moving`/`still` zeigen,
  was er gerade sieht. Wie lange Präsenz anhält, entscheidet der Tracker.
- Zeitstempel beim Empfang im Skript setzen. Im LAN sind das wenige Millisekunden.
- **Multi-Target** muss eingeschaltet sein (Schalter `LD2450 Multi Target`), sonst meldet der LD2450 nur ein Ziel.

Minimaler Empfänger (`pip install aiomqtt`):

```python
import asyncio, json, time
import aiomqtt

async def main():
    async with aiomqtt.Client("192.168.178.3", username="presence", password="...") as client:
        await client.subscribe("presence/+/frame")
        async for msg in client.messages:
            sensor = msg.topic.value.split("/")[1]
            frame = json.loads(msg.payload)
            for t in frame["targets"]:
                print(f"{time.time():.3f} {sensor} #{t['slot']} x={t['x']} y={t['y']} v={t['speed']}")

asyncio.run(main())
```

Mitschneiden zum späteren Abspielen:

```bash
mosquitto_sub -h 192.168.178.3 -u presence -P '...' -t 'presence/+/frame' -F '%U %t %p' > aufnahme.log
```

## Einrichten im ESPHome Device Builder

1. **MQTT-Benutzer** anlegen: im Mosquitto-Add-on unter *Konfiguration → logins* (oder einen eigenen HA-Benutzer).
2. **Secrets**: die Schlüssel aus `secrets.yaml.example` in `/config/esphome/secrets.yaml` ergänzen.
   `wifi_*` und `api_key` gibt es dort schon, neu sind `mqtt_broker`, `mqtt_username`, `mqtt_password`.
3. **Package**: den Ordner `packages/` nach `/config/esphome/packages/` kopieren (z. B. mit dem Studio Code Server).
   Dateien in Unterordnern zeigt der Device Builder nicht als eigenes Gerät an.
4. **Gerät**: `presence-example.yaml` als z. B. `presence-wohnzimmer.yaml` anlegen und `name`/`friendly_name` anpassen.
   `name` muss eindeutig sein, er ist auch das MQTT-Topic.
5. Das erste Mal per USB flashen, danach per OTA.

Statt Schritt 3 kann die Gerätedatei das Package auch direkt aus Git holen:

```yaml
packages:
  radar: github://LeonRein/presence-tracker/esphome/packages/radar-combo.yaml@main
```

Das geht nur, wenn das Repo öffentlich ist. Änderungen am Package landen dann bei allen Sensoren mit dem nächsten Build.

## Nach dem ersten Start

- `LD2450 Multi Target` einschalten.
- Bluetooth an beiden Radaren schaltet die Firmware selbst ab (einmalig, der Radar startet dabei kurz neu).
  Die Schalter sind intern, in Home Assistant gibt es sie nicht. Für die HLKRadarTool-App
  in `radar-combo.yaml` bei `bluetooth:` die Zeilen `internal` und `on_turn_on` entfernen.
- LD2410C-Timeout und Engineering Mode stellt die Firmware selbst ein (Timeout 0, Engineering Mode an).
  In Home Assistant gibt es dafür keine Entitäten mehr.
- LD2410C-Gates einstellen: Die Energie pro Stufe zeigt die Tracker-App im Tab *Sensoren*. Die Schwellen
  `LD2410 Gx … Threshold` im leeren Raum knapp über das Rauschen legen.
