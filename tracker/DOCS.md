# Presence Tracker

Führt die Rohdaten mehrerer Radarsensoren (LD2450 + LD2410C, Firmware aus [`esphome/`](../esphome/README.md))
zu einem Bild zusammen: wo im Haus wie viele Personen sind, ob sie sich bewegen, und welche Zone gleich
betreten wird. Wer wer ist, spielt keine Rolle.

## Wie es funktioniert

Das vollständige Modell mit jeder Wahrscheinlichkeit und ihrer Herkunft steht in [MODEL.md](MODEL.md).

- **Gemessen wird an den Spuren des LD2450.** Der Sensor ist selbst ein Tracker: Er beginnt Spuren, hält sie
  eine Weile, wenn er sein Ziel verliert, und lässt sie fallen. Ausgewertet wird, wann eine Spur beginnt,
  wo sie liegt und ob sie wiedergefunden wird, nicht jeder Frame als neue Messung.
- **Jede Person ist eine Wahrscheinlichkeitsverteilung**, keine einzelne Spur. Wer gerade eine Spur hat, ist
  eine Mischung aus „steht“ und „geht“, jede mit Position, Geschwindigkeit und Unsicherheit. Wer keine hat,
  ist eine Dichte über Kacheln des Grundrisses (Raumstücke bis 0,6 m, in denen jeder Sensor etwa gleich gut
  sieht), hinter Türen und außer Haus. Nichts davon ist zufällig gezogen:
  Gleiche Daten ergeben immer dasselbe Ergebnis. Die Karte im Tab *Live* zeigt die Verteilung jeder Person
  als Wärmekarte, den Punkt am wahrscheinlichsten Ort.
- **Niemand taucht aus dem Nichts auf und niemand verschwindet einfach.** Personen kommen und gehen nur
  durch Türen. Wer durch eine Tür in einen Bereich ohne Sensor geht, ist dahinter (Balkon, Flur-Bereich mit
  Schlafzimmer und Treppe), wie lange Besuche dort dauern, ist angenommen.
- **Wer geht, wird schnell gesehen; wer sitzt, nicht immer.** Wie gut ein Sitzender erfasst wird, hängt von
  Haltung und Platz ab und wird für jeden Aufenthalt mitgeschätzt: Wer am Tisch oft erfasst wurde und lange
  nicht mehr, ist wahrscheinlich gegangen; wer auf dem Sofa selten erfasst wird, nicht.
- **Geister**: Eine Spur, die zu keiner Person gehört. Wo jeder Sensor Geister sieht und wie lange sie leben,
  **lernt die App selbst** (Geisterkarte). Wird ein Sensor verschoben, hinzugefügt oder entfernt, beginnt
  die Karte neu, weil sich die Sensoren gegenseitig stören können. Bis sie ein paar Stunden gelernt hat,
  werden Geister an festen Stellen (Möbel, Ladestation) leichter für Personen gehalten.
- Der **LD2410C** zählt mit: Ist er aus, sitzt niemand nahe vor ihm (bis etwa 3 m sieht er Sitzende
  zuverlässig). Bleibt er länger als ein kurzes Aufblitzen an, ist jemand in der Entfernung, die er meldet,
  auch wenn der LD2450 ihn verloren hat. Was eine andere Person schon erklärt, sagt über die übrigen nichts.
- **Wände**: Die Radare sehen nicht durch die Betonwände. Ein Messpunkt hinter einer Wand oder außerhalb aller
  Räume ist eine Reflexion und wird verworfen (*Toleranz an Wänden*).
- **Personen**: Mit *Personen beim Start* beginnt das Modell, wenn es nichts weiß. Weitere (Gäste) kommen über
  die Wege nach draußen dazu; wer sicher außer Haus ist, wird vergessen.
- **Fehler melden** (Tab *Live*): Wenn etwas nicht stimmt (Person verloren, Geist, Person am falschen Ort,
  ungenaues Tracking, hohe Latenz), den Raum und die Art des Fehlers wählen. Die App
  speichert dazu die Sensordaten der letzten 15 Minuten mit der Konfiguration (unter `/data/reports`, in
  der Liste herunterladbar). Daraus entsteht die Wahrheitstabelle für die Bewertung;
  `tools/replay.py --report DATEI` spielt eine Meldung nach.

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
   Die eingezeichneten Positionen bleiben; aus den Messungen folgen je Sensor Blickrichtung, Maßstab (misst er
   ein paar Prozent zu kurz oder zu lang) und x-Richtung. Die Blickrichtungen müssen nur grob stimmen. Ein
   Sensor ohne genug gemeinsame Messungen mit einem anderen (z. B. Küche) bleibt, wie er ist.

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
