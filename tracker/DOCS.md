# Presence Tracker

Führt die Rohdaten mehrerer Radarsensoren (LD2450 + LD2410C, Firmware aus [`esphome/`](../esphome/README.md))
zu einem Bild zusammen: wo im Haus wie viele Personen sind, ob sie sich bewegen, und welche Zone gleich
betreten wird. Wer wer ist, spielt keine Rolle.

## Wie es funktioniert

Das vollständige Modell mit jeder Wahrscheinlichkeit und ihrer Herkunft steht in [MODEL.md](MODEL.md).

- **Jede Person ist eine Wolke möglicher Aufenthaltsorte** (Partikel), keine einzelne Spur. Ein Teil der
  Wolke kann im Raum sitzen, ein anderer auf dem Balkon sein: Das ist die *Superposition*. Jede Messung
  gewichtet die Möglichkeiten neu. Die Karte im Tab *Live* zeigt die Wolke jeder Person als Wärmekarte,
  den Punkt am wahrscheinlichsten Ort.
- **Niemand taucht aus dem Nichts auf und niemand verschwindet einfach.** Personen kommen und gehen nur
  durch Türen. Eine Messung mitten im Raum, zu der niemand von einer Tür hätte kommen können, ohne gesehen
  zu werden, ist ein Geist.
- **Wer durch eine Tür geht, ist dahinter** (Küche, Balkon, Flur-Bereich mit Schlafzimmer und Treppe) und
  wird dort angezeigt, nicht als Punkt an der Tür. Wer herauskommt, ist dieselbe Person mit derselben
  Nummer. Wie lange Besuche hinter einer Tür üblicherweise dauern, wird gelernt. Die Schätzung bleibt breit,
  wer länger bleibt, bleibt plausibel.
- **Nicht gesehen werden**: Still Sitzende verliert der LD2450 oft lange, an manchen Stellen (Sofalehne) mehr
  als an anderen. Die Wahrscheinlichkeit, unbemerkt am selben Ort zu bleiben, kommt aus der Erkennung je
  Stelle und der gemessenen Dauer solcher Aussetzer. Wer an einer gut sichtbaren Stelle länger nicht
  gesehen wird, ist wahrscheinlich gegangen; an einer schlecht sichtbaren Stelle ist er wahrscheinlich
  noch da. Friert der LD2450 ein (bit-identische Werte), sagt das nichts über die Stelle.
- **Zwei Körper stehen nicht am selben Fleck.** Zwei Personen dicht nebeneinander (Sofa) bleiben zwei.
- **Gelernt** werden je Sensor die Geisterdichte je Ort (nur wo ein zweiter Sensor gut hinsieht und nichts
  meldet), die LD2410C-Energie je Entfernungsstufe mit und ohne Person und, wie lange Besuche hinter einer
  Tür dauern. Die Erkennungswahrscheinlichkeit je Ort zu lernen brachte im Testdurchlauf nichts (entfernt in
  0.6.7). Verhalten (wo man stehen bleibt, losgeht) wird
  bewusst nicht gelernt: Messlücken würden sonst als Verhalten gedeutet und verstärkt.
- **Geister**: Ein Ziel, das ein Sensor meldet, kann ein Geist sein (Echo, nachgeführtes Ziel). Ein Geist
  lebt etwa 1,5 s, behält seine Geschwindigkeit und rückt mit seinen Zielen mit; so wird aus einer
  Geisterspur keine Person.
- Der **LD2410C** gewichtet alle 3 s jede mögliche Lage einer Person mit der Energie in ihrer
  Entfernungsstufe: Viel Energie spricht für jemanden dort, keine dagegen. So wird auch eine verdeckte
  Sitzende gestützt und eine vermeintlich versteckte Person, die längst gegangen ist, widerlegt.
- **Reichweite**: Hinter der eingestellten Reichweite fällt die angenommene Erkennung weich ab statt auf 0.
- **Wände**: Die Radare sehen nicht durch die Betonwände. Ein Messpunkt hinter einer Wand oder außerhalb aller
  Räume ist eine Reflexion und wird verworfen (*Toleranz an Wänden*). Was ein Sensor durch eine offene Tür in
  einem Raum ohne Sensor sieht (z. B. jemand in der Küche), zählt als Messung dort.
- **Personen**: Verfolgt werden die *Bewohner*. Jede kann auch außer Haus sein (über den Flur-Bereich mit der
  Treppe) und wird dann nirgends angezeigt. Noch nicht gebaut: unbekannte Neuankömmlinge (Gäste).

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
   Auch Räume ohne Sensor einzeichnen (Balkon, Küche, Flur): Daraus weiß die App, wo Personen
   herkommen können.
   Sie werden nur benannt. *Eingang* markiert Räume, in denen Personen auftauchen und verschwinden dürfen
   (Treppenhaus). Beim Verschieben von Wänden behält jeder Raum seinen Namen und damit seine Entitäten.
3. **Zonen** (Tab *Zonen*), frei gezeichnet als Rechteck, Kreis oder Polygon:
   - *Bereich*: z. B. Sofa, Esstisch, für Home Assistant
   - *Eingang*: kleine Bereiche, wo Personen von außen kommen oder das Haus verlassen (Haustür im
     beobachteten Raum). Räume ohne Sensor, die über Türen zusammenhängen, erkennt die App selbst.
   Stellen, an denen ein Sensor oft Geister meldet (Ventilator, Vorhang), lernt die App selbst
   (Tab *Sensoren*, Karte „Geister“); eine eigene Zone dafür gibt es nicht mehr.
4. **Sensoren** (Tab *Sensoren*): jeden Sensor platzieren, Blickrichtung drehen, Montagehöhe eintragen.
   *Tote Winkel zeigen* färbt Stellen, die kein Sensor sieht, rot.
5. **Kalibrierung** (Tab *Kalibrierung*): allein 2–3 Minuten durch die Überschneidungen der Sensoren gehen.
   Aus den Messungen ergibt sich, wie die Sensoren zueinander stehen (Abstand, Drehung, x-Richtung). Diese
   starre Anordnung wird auf die eingezeichneten Positionen gelegt; die Blickrichtungen müssen nur grob stimmen.

**Sensormodell** (Tab *Sensoren*, Sensor auswählen): Karten, wo der Sensor wie zuverlässig erkennt (angenommen und
aus dem Betrieb gelernt) und wo er Geister meldet, dazu der gelernte Messfehler nach Abstand. Die App lernt das
nebenbei; das Tracking nutzt es in dieser Version noch nicht.

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
| `log_level` | Ausführlichkeit des Protokolls |
| `topic_prefix` | MQTT-Präfix der Sensoren (`mqtt_prefix` in ESPHome), Standard `presence` |

## Entwicklung

Rohdaten mitschneiden und abspielen, ohne Home Assistant:

```bash
uv run --with paho-mqtt --with pyyaml tools/record.py recordings/
cd tracker && uv run python -m presence_tracker --data devdata --replay ../recordings/*.jsonl
```

Die Web-UI läuft dann auf http://localhost:8099. Tests: `cd tracker && uv run pytest`.
