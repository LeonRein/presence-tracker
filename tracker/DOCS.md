# Presence Tracker

Führt die Rohdaten mehrerer Radarsensoren (LD2450 + LD2410C, Firmware aus [`esphome/`](../esphome/README.md))
zu einem Bild zusammen: wo im Haus wie viele Personen sind, ob sie sich bewegen, und welche Zone gleich
betreten wird. Wer wer ist, spielt keine Rolle.

## Wie es funktioniert

- Jede Person ist eine **Spur** in Hauskoordinaten (Meter, Achsen der Saugroboter-Karte). Ein
  IMM-Kalman-Filter mit zwei Bewegungsmodellen (gehen / ruhig) glättet die Position und liefert nebenbei,
  ob sich jemand bewegt.
- **Niemand taucht aus dem Nichts auf und niemand verschwindet einfach.** Neue Personen werden in
  Eingangszonen (Treppe, Haustür, Balkon) schnell bestätigt, mitten im Raum erst nach etwa 2 s stimmiger
  Messungen. Eine Person, die der LD2450 nicht mehr sieht (stillsitzend, verdeckt, mehr als 3 Ziele),
  bleibt an ihrem Platz stehen, bis sie durch einen Eingang geht oder der LD2410C dort länger nichts mehr sieht.
- Der **LD2410C** erzeugt nie selbst Personen. Seine Haltezeit steht auf 0, die App überbrückt kurze Lücken selbst
  (*LD2410C-Haltezeit*). Seine Störungen durch den LD2450 dauern etwa 1 s. Nur längere Präsenz im passenden
  Abstand bestätigt eine verdeckte Person. Die Energie pro Entfernungsstufe steht im Tab *Sensoren*.
- **Wände**: Die Radare sehen nicht durch die Betonwände. Ein Messpunkt hinter einer Wand oder außerhalb aller
  Räume ist eine Reflexion und wird verworfen (*Toleranz an Wänden*).
- **Mehrere Sensoren**: Messungen aller Sensoren gehen in dieselben Spuren. Sieht ein Sensor dieselbe Person
  wie ein anderer, wird sie nicht doppelt gezählt.

## Einrichten

1. **Sensoren** mit der ESPHome-Konfiguration aus diesem Repo flashen. Die App bekommt die MQTT-Zugangsdaten
   vom Supervisor (Mosquitto-App muss laufen) und findet die Sensoren unter `presence/+/frame` selbst.
2. **Grundriss** (Tab *Grundriss*): *Saugroboter-Karte* lädt die Karte aus Home Assistant und
   passt sie ein. Alternativ ein Bild hochladen und per *2-Punkt-Ausrichtung* einpassen. Dann nachzeichnen:
   - *Wand*: echte Wände, sie bestimmen, was ein Sensor sehen kann. Jede Wand ist ein gerades Stück von Ecke
     zu Ecke; angrenzende Wände bewegen sich beim Ziehen mit. Punkte rasten in 45°-Schritten und an Wänden ein.
   - *Tür*: auf eine Wand klicken. Eine Tür trennt zwei Räume, ist für Radar und Personen aber offen.
     Ein Durchgang ohne Tür bleibt eine Lücke in der Wand, die Räume auf beiden Seiten sind dann ein Raum.
   - *Raumgrenze*: teilt einen Raum ohne Wand, z. B. Wohn- und Essbereich.

   Die **Räume** entstehen automatisch als geschlossene Flächen zwischen Wänden, Türen und Raumgrenzen.
   Sie werden nur benannt. *Eingang* markiert Räume, in denen Personen auftauchen und verschwinden dürfen
   (Treppenhaus). Beim Verschieben von Wänden behält jeder Raum seinen Namen und damit seine Entitäten.
3. **Zonen** (Tab *Zonen*), frei gezeichnet als Rechteck, Kreis oder Polygon:
   - *Bereich*: z. B. Sofa, Esstisch, für Home Assistant
   - *Eingang*: kleine Bereiche, wo Personen das Gebiet betreten oder verlassen (Balkontür)
   - *Störer*: Ventilator, Vorhang, Pflanze. Dort entstehen keine neuen Personen.
4. **Sensoren** (Tab *Sensoren*): jeden Sensor platzieren, Blickrichtung drehen, Montagehöhe eintragen.
   *Tote Winkel zeigen* färbt Stellen, die kein Sensor sieht, rot.
5. **Kalibrierung** (Tab *Kalibrierung*): allein 2–3 Minuten durch die Überschneidungen der Sensoren gehen.
   Aus den Messungen ergibt sich, wie die Sensoren zueinander stehen (Abstand, Drehung, x-Richtung). Diese
   starre Anordnung wird auf die eingezeichneten Positionen gelegt; die Blickrichtungen müssen nur grob stimmen.

Rückgängig mit Strg+Z, Wiederholen mit Strg+Y. Alles wird automatisch gespeichert (`/data/tracker.json`).

## Entitäten in Home Assistant

Gerät **Presence Tracker**, für jeden Raum und Bereich:

| Entität | Bedeutung |
|---|---|
| `binary_sensor.presence_<zone>_occupancy` | Jemand ist in der Zone |
| `sensor.presence_<zone>_count` | Anzahl Personen, Attribute `moving` / `still` |
| `binary_sensor.presence_<zone>_moving` | Mindestens eine Person bewegt sich |
| `binary_sensor.presence_<zone>_approaching` | Jemand geht auf die Zone zu und ist in etwa 1 s drin (*Vorausschau* in den Einstellungen) |

Dazu `presence_haus_*` für das ganze Haus. Mit *wird betreten* kann das Licht schon angehen, bevor jemand den Raum betritt.

## Optionen

| Option | Bedeutung |
|---|---|
| `log_level` | `debug` zeigt jede neue und beendete Spur mit Grund |
| `topic_prefix` | MQTT-Präfix der Sensoren (`mqtt_prefix` in ESPHome), Standard `presence` |

## Entwicklung

Rohdaten mitschneiden und abspielen, ohne Home Assistant:

```bash
uv run --with paho-mqtt --with pyyaml tools/record.py recordings/
cd tracker && uv run python -m presence_tracker --data devdata --replay ../recordings/*.jsonl
```

Die Web-UI läuft dann auf http://localhost:8099. Tests: `cd tracker && uv run pytest`.
