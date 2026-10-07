# Presence Tracker

Führt die Rohdaten mehrerer Radarsensoren (LD2450 + LD2410C, Firmware aus [`esphome/`](../esphome/README.md))
zu einem Bild zusammen: wo im Haus wie viele Personen sind, ob sie sich bewegen, und wie wahrscheinlich
eine Zone gleich betreten wird. Wer wer ist, spielt keine Rolle.

## Wie es funktioniert

Das vollständige Modell mit jeder Wahrscheinlichkeit und ihrer Herkunft steht in [MODEL.md](MODEL.md).

- **Gemessen wird an den Spuren des LD2450.** Der Sensor ist selbst ein Tracker: Er beginnt Spuren, hält sie
  eine Weile, wenn er sein Ziel verliert, und lässt sie fallen. Ausgewertet wird, wann eine Spur beginnt,
  wo sie liegt und ob sie wiedergefunden wird, nicht jeder Frame als neue Messung.
- **Jede Person ist eine Wahrscheinlichkeitsverteilung**, keine einzelne Spur. Wer gerade eine Spur hat, ist
  eine Mischung aus „steht“ und „geht“, jede mit Position, Geschwindigkeit und Unsicherheit. Wer keine hat,
  ist eine Dichte über Kacheln des Grundrisses (Quadrate von 0,4 m, an den Raumgrenzen geschnitten), hinter
  Türen und außer Haus. Nichts davon ist zufällig gezogen:
  Gleiche Daten ergeben immer dasselbe Ergebnis. Die Karte im Tab *Live* zeigt die Verteilung jeder Person
  als Wärmekarte, an den Wänden abgeschnitten, und den Punkt am wahrscheinlichsten Ort, beides in einer
  Farbe je Person (Pfeil: geht; blass: gerade nicht gesehen). Die Spur bricht ab, wo die Schätzung springt
  (mehr als 1 m oder durch eine Wand). Die Rohdaten der Sensoren (Sichtfelder, Messpunkte) sind
  standardmäßig aus; der Schalter steht unter *Anzeige* und wird im Browser gemerkt.
- **Tab *Live*:** Ganz oben steht, welche Räume besetzt sind (Licht an), darunter die Personenzahl. In der
  Raumliste stehen die besetzten Räume zuerst. Die Prozentzahl am Raum ist die Wahrscheinlichkeit, dass
  *jemand* im Raum ist (besetzt ab der Schwelle aus *Kosten: Licht ohne Person*); die an einer Person ist die
  Wahrscheinlichkeit, dass *diese Person* dort ist. *wird betreten* und *Ziel* (mit Wahrscheinlichkeit und
  Quelle *Bewegung* oder *Karte*) stehen nur an Räumen, die noch nicht besetzt sind, und bleiben 2 s
  sichtbar, auch wenn sie nur kurz an waren.
- **Verbindung:** Oben rechts stehen Sensoren und Personen, auch am Handy. Kommen 3 s lang keine Daten
  oder ist die Verbindung getrennt, wird die Karte grau, die Listen blass, und ein roter Balken sagt, seit
  wann der Stand alt ist.
- **Niemand taucht aus dem Nichts auf und niemand verschwindet einfach.** Personen kommen und gehen nur
  durch Türen. Wer durch eine Tür in einen Bereich ohne Sensor geht, ist dahinter (Balkon, Schlafzimmer),
  wie lange Besuche dort dauern, ist angenommen. Wer durch die Wohnungstür ins Treppenhaus geht, ist
  außer Haus: Das Treppenhaus ist öffentlicher Raum und gehört nicht zur Wohnung; dort wird niemand
  gezählt.
- **Wer geht, wird schnell gesehen; wer sitzt, nicht immer.** Wie gut ein Sitzender erfasst wird, hängt von
  Haltung und Platz ab und wird für jeden Aufenthalt mitgeschätzt: Wer am Tisch oft erfasst wurde und lange
  nicht mehr, ist wahrscheinlich gegangen; wer auf dem Sofa selten erfasst wird, nicht.
- **Geister**: Eine Spur, die zu keiner Person gehört. Wo jeder Sensor Geister sieht, **lernt die App
  selbst** (Geisterkarte); wie lange Geister leben, ist fest (daran unterscheidet das Modell sie von
  Sitzenden). Wird ein Sensor verschoben, hinzugefügt oder entfernt, beginnt die Karte neu, weil sich die
  Sensoren gegenseitig stören können. Eine Stelle wird erst nach vielen Geistern zur Geisterquelle (ein
  Platz, an dem jemand sitzt, soll nicht durch ein paar Fehlurteile dazu werden); bis die Karte einige
  Tage gelernt hat, werden Geister an festen Stellen (Möbel, Ladestation) leichter für Personen gehalten.
- **Sensor drehen oder neu kalibrieren** (gleicher Grundriss): Was über die Personen bekannt ist, bleibt.
  Nur die Spuren dieses Sensors beginnen neu, wie nach einer Datenlücke. Andere Parameter: Das Modell
  beginnt neu mit dem, was es über die Personen wusste. Ein geänderter Grundriss (Wände, Räume, Türen):
  Es beginnt ohne Wissen (siehe *Personen*).
- Der **LD2410C** zählt mit seinen Energien je 0,75-m-Entfernungsring (bewegt und ruhig): Wo eine Person
  Energie machen müsste und keine ist, ist niemand; Energie, die niemand erklärt, spricht für jemanden in
  diesem Ring, auch wenn der LD2450 ihn verloren hat. Was eine andere Person schon erklärt, sagt über die
  übrigen nichts. Was jeder LD2410C ohne Personen sieht (Hintergrund), **lernt die App selbst**; wird ein
  Sensor verschoben, beginnt das für ihn neu. Seine Flags und die gemeldete Entfernung zählen nicht mehr.
- **Wände**: Die Radare sehen nicht durch die Betonwände. Ein Messpunkt hinter einer Wand oder außerhalb aller
  Räume ist eine Reflexion und wird verworfen (*Toleranz an Wänden*).
- **Personen**: Wie viele es sind, wird nirgends eingestellt. Nach einem Neustart der App macht das Modell
  mit dem weiter, was es vorher über die Personen wusste (gespeichert alle 10 Minuten und beim Beenden
  in `/data/people.json`, über die Pause vorgerückt); nur die laufenden Spuren beginnen neu. Ohne das
  (erste Installation, neuer Grundriss, *Spuren zurücksetzen*) weiß es nichts: Wer in Sicht ist, wird
  gefunden, gleich wie viele; wer hinter einer Tür oder außer Haus ist, wenn er herauskommt. Weitere
  (Gäste) kommen über die Wege nach draußen dazu; wer sicher außer Haus ist, wird vergessen.
- **Fehler melden** (Tab *Live*): Wenn etwas nicht stimmt (Licht fälschlich an oder aus, Licht zu spät,
  Person verloren, Geist, Person am falschen Ort, ungenaues Tracking, hohe Latenz), den Raum (keine
  Vorauswahl) und die Art des Fehlers wählen. Die App
  speichert dazu die Sensordaten der letzten 15 Minuten mit der Konfiguration, dem Gelernten
  (Geisterkarte, LD2410C-Hintergrund), dem Stand des Codes und dem, was sie in dieser Zeit sekündlich
  angezeigt hat (unter `/data/reports`, je Meldung etwa 0,2–0,7 MB, in der Liste herunterladbar). Daraus
  entsteht die Wahrheitstabelle für die Bewertung; `tools/replay.py --report DATEI` spielt eine Meldung
  nach und zeigt, wie weit das Nachspiel von der Anzeige der App abweicht. Begann das Modell innerhalb der
  15 Minuten (Start der App, *Spuren zurücksetzen*, neuer Grundriss), beginnt das Nachspiel dort mit dem
  damals Gelernten und dem Zustand der Personen, mit dem das Modell begann (etwa 30 kB mehr), und glaubt
  genau, was die App glaubte; sonst beginnt es ohne Wissen am Anfang der Daten und hat das bis zum
  gemeldeten Moment vergessen.

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
   Sie werden nur benannt. *Außer Haus* markiert den öffentlichen Raum vor der Wohnungstür (Treppenhaus):
   Er gehört nicht zur Wohnung, die Tür dorthin ist die Wohnungstür. Wer hindurchgeht, ist außer Haus;
   wer hereinkommt, kommt von draußen. Für ihn gibt es keine Personenzahl und keine Entitäten, und was ein
   Sensor durch die offene Tür dort sieht (Nachbarn), zählt nicht. Den Raum trotzdem zeichnen: Er legt
   die Wohnungstür fest. Beim Verschieben von Wänden behält jeder Raum seinen Namen und damit seine
   Entitäten.
3. **Zonen** (Tab *Zonen*), frei gezeichnet als Rechteck, Kreis oder Polygon:
   - *Bereich*: z. B. Sofa, Esstisch, für Home Assistant
   - *Eingang*: kleine Bereiche, wo Personen von außen kommen oder das Haus verlassen (Haustür im
     beobachteten Raum). Räume ohne Sensor, die über Türen zusammenhängen, erkennt die App selbst.
   Stellen, an denen ein Sensor oft Geister meldet (Ventilator, Vorhang), lernt die App selbst
   (Tab *Sensoren*, Karte „Geister“); eine eigene Zone dafür gibt es nicht mehr.
4. **Sensoren** (Tab *Sensoren*): jeden Sensor platzieren, Blickrichtung drehen, Montagehöhe eintragen.
   *Tote Winkel zeigen* färbt Stellen, die kein Sensor sieht, rot.
5. **Kalibrierung** (Tab *Kalibrierung*): Die App sammelt laufend, wo Gehende gemessen werden (die letzten
   24 Stunden, auch über einen Neustart); ein Tag normales Leben reicht meist. Ein Gang allein kreuz und
   quer durch die Räume und Türen ergänzt das, ersetzt es aber nicht. *Berechnen* rechnet für
   alle Sensoren gemeinsam Blickrichtung und Maßstab (misst er ein paar Prozent zu kurz oder zu lang),
   jeweils mit Unsicherheit: aus dem, was zwei Sensoren gleichzeitig sehen, aus den Übergängen von einem
   Blickfeld ins nächste (auch wenn sich zwei Sensoren nur in einer Tür überschneiden) und aus dem
   Grundriss (Gehende sind in Räumen und gehen durch Türen). Die eingezeichneten Positionen und die
   x-Richtung (wie der Sensor eingebaut ist) bleiben; die x-Richtung prüft die App nur auf Wunsch.
   Übernommen wird nur, was sicher bestimmt ist: aus mindestens 3 verschiedenen Stunden mit Gehenden,
   stabil zwischen ihnen, und ohne dass mehr Gehende außerhalb der Sicht des Sensors lägen. Widersprechen
   sich Grundriss und gemeinsame Messungen,
   sagt die App das: Dann stimmt meist die eingezeichnete Position des Sensors nicht. Nach dem Drehen
   oder Versetzen eines Sensors *Neu sammeln*.
   Unter *Gesammelt seit* steht je Sensor, wie viele Punkte in Bewegung er hat (höchstens einer je
   Sekunde und Spur, so zählt auch die Berechnung) und wie viele der nötigen 3 Stunden mit Gehenden
   schon da sind. Das Ergebnis steht direkt unter *Berechnen*; was für mehrere Sensoren gilt (zu wenig
   Stunden, mehr herumgehen), steht dort einmal, je Sensor nur das Eigene.

**Sensormodell** (Tab *Sensoren*, Sensor auswählen): Karten, wo der Sensor wie zuverlässig erkennt (angenommen und
aus dem Betrieb gelernt) und wo er Geister meldet, dazu der gelernte Messfehler nach Abstand. Die App lernt das
nebenbei; das Tracking nutzt es in dieser Version noch nicht.

Alles wird sofort gespeichert (`/data/tracker.json`); eine kurze Meldung sagt *Gespeichert* und, wenn
das Modell dabei neu startet, *Modell neu gestartet* (bei fast jeder Änderung an Grundriss, Zonen, Sensoren
und Einstellungen, nicht bei den Schwellen „Ziel“). Rückgängig und Wiederholen mit den Knöpfen ↶ ↷ unten
rechts auf der Karte oder mit Strg+Z / Strg+Y. Sie wirken nur auf Änderungen im aktuellen Tab; liegt die
letzte Änderung in einem anderen Tab, sagt die App, in welchem, statt sie unsichtbar zurückzunehmen.
Löschen mit Entf oder Rücktaste, *Von Karte nehmen*, *Neu sammeln* (Kalibrierung) und *Neu beginnen*
(Live) fragen vorher nach. Ein Element bewegt sich erst, wenn es schon ausgewählt war und dann gezogen
wird; einen eigenen Bearbeiten-Modus gibt es nicht.

Zahlenfelder nehmen nur Werte in ihren Grenzen an. Ein leeres Feld oder ein Wert außerhalb (etwa
*Kosten: Licht ohne Person* = 0, was jeden Raum besetzt und jedes Licht eingeschaltet hätte) wird nicht
gespeichert: Der alte Wert bleibt, das Feld wird rot markiert und sagt, was erlaubt ist. Der Server prüft
dieselben Grenzen und lehnt solche Änderungen mit einer Meldung ab.

## Entitäten in Home Assistant

Gerät **Presence Tracker**, für jeden Raum und Bereich:

| Entität | Bedeutung |
|---|---|
| `binary_sensor.presence_<zone>_occupancy` | Jemand ist in der Zone |
| `sensor.presence_<zone>_count` | Anzahl Personen, Attribute `moving` / `still` |
| `binary_sensor.presence_<zone>_moving` | Mindestens eine Person bewegt sich |
| `binary_sensor.presence_<zone>_approaching` | *wird betreten*: Jemand, der gerade geht, kommt wahrscheinlich gleich herein (Vorhersage, siehe unten) |
| `binary_sensor.presence_<raum>_ziel` | *Ziel* (nur Räume): Jemand, der gerade geht, geht als Nächstes in diesen Raum; aus der Bewegung und einer gelernten Karte (siehe unten) |

Dazu `presence_haus_*` für das ganze Haus (ohne *außer Haus*). Ein Raum mit *Außer Haus* (Treppenhaus)
bekommt keine Entitäten; hatte er von einer früheren Version welche, entfernt die App sie beim nächsten
Verbinden mit dem Broker.

**Besetzt** ist an, solange die Wahrscheinlichkeit, dass jemand im Raum ist, über der Schwelle aus
*Kosten: Licht ohne Person* liegt (bei 2: 67 %, Attribut `probability` am Personenzähler). Es geht im
Median etwa 0,3 s nach dem Betreten an und nach dem Verlassen meist binnen einer halben Sekunde aus.

**Wird betreten** ist eine Vorhersage: Das Modell rechnet jede Person, die gerade geht, mit seinem
eigenen Bewegungsmodell voraus (sie kann umdrehen, anhalten, abbiegen; Wände halten sie auf, Türen
nicht) und rechnet aus, wie wahrscheinlich sie binnen der *Vorausschau* in die Zone kommt, über alle
Hypothesen des Modells. Es ist an, wenn diese Wahrscheinlichkeit über der Schwelle aus *Kosten:
Einschalten auf Verdacht* liegt. Für Räume ohne Sensor gibt es keine Vorhersage (aus). Attribute:

| Attribut | Bedeutung |
|---|---|
| `p_enter` | Wahrscheinlichkeit, dass binnen der Vorausschau jemand hereinkommt (5-%-Schritte; in Home Assistant nur, solange *wird betreten* an ist, sonst 0, siehe *Recorder*) |
| `eta` | in wie vielen Sekunden die wahrscheinlichste Person drin ist, bei ihrem Tempo |
| `distance` | wie viele Meter sie noch hat |
| `person` | ihre Nummer in der Anzeige (Tab *Live*) |

Die App schaltet selbst keine Lichter. Was aus den beiden Sensoren wird (Licht an, Verzögerung beim
Ausschalten, Nachtmodus, Helligkeit), entscheidet die Automation in Home Assistant oder Node-RED.
Eine einfache Regel je Raum, die nie schlechter ist als *besetzt* allein:
- *besetzt* an → Licht an, ein laufendes Ausschalten abbrechen;
- *wird betreten* an, Licht aus → Licht an „auf Verdacht“; wird der Raum binnen 30 s nicht *besetzt*,
  wieder aus;
- *besetzt* aus → nach 2 min aus, wenn bis dahin weder *besetzt* noch *wird betreten* wieder an war.

*Wird betreten* irrt öfter als *besetzt*: Wer nah an einer Tür vorbeigeht oder davor umdreht, kann das
Licht für 30 s einschalten. Gemessen (MODEL.md 6, 10) mit den Vorgaben (*Vorausschau* 2 s, *Kosten:
Einschalten auf Verdacht* 0,05): Das Licht brennt vor 60 % der Eintritte mindestens 1 s (1 m) vorher,
dafür im Tagesmittel etwa 4 vergebliche Verdachte je Stunde; die meisten an offenen Raumgrenzen ohne
Wand (Wohn-/Essbereich), dort besser ohne *wird betreten*. Höhere Kosten: weniger Verdachte, weniger
Vorlauf. Wer nicht vorausschauen will, nimmt nur *besetzt*.

**Ziel** ist die zweite Vorhersage, ohne Horizont: Wie wahrscheinlich geht ein Gehender *als Nächstes* in
diesen Raum? Die App lernt dafür selbst eine Karte, wohin die Gänge von jeder Stelle, in jeder Richtung
und bei jedem Tempo bisher gingen (aus ihren eigenen Gehenden, vergessen über 14 Tage, gespeichert in
`/data/destinations.json`; ändern sich Räume oder Türen, beginnt sie leer). Sie lernt nur aus dem, was
die App nach dem Update live sieht: Nach dem Update ist sie leer, aus alten Aufnahmen kommt nichts hinein.
Wo die Karte noch nichts weiß, ist *Ziel* **genau** *wird betreten* (Zustand für Zustand, auch in Home
Assistant): Man kann es vom ersten Tag an statt *wird betreten* nehmen. Je mehr Gänge sie an einer Stelle
kennt, desto mehr entscheidet sie: Auf einem Weg, der Routine ist (vom Schreibtisch zur Tür), geht *Ziel*
früher an als *wird betreten*; wo Gänge oft im Türrahmen enden (Küche), seltener. Über einen Raumteiler sagt die Karte nichts voraus (Wohn- und
Esszimmer gelten für die Gänge als ein Raum). Attribute:

| Attribut | Bedeutung |
|---|---|
| `probability` | die Wahrscheinlichkeit, die entscheidet (an ab der Schwelle des Raums) |
| `from` | der Raum des Gehenden, der am wahrscheinlichsten hingeht |
| `distance`, `eta` | sein Abstand zur Tür (Luftlinie, m) und die Zeit bei seinem Tempo (s) |
| `person` | seine Nummer in der Anzeige |
| `walks` | wie viele gelernte Gänge hinter der Vorhersage stehen |
| `source`, `weight` | was entschied: `bewegung` (wie *wird betreten*) oder `karte`; `weight` das Gewicht der Karte (0 … 1) |

Die Schwelle (*Schwelle „Ziel“*, je Raum einstellbar, Tab *Einstellungen*) ist
K_Fehl / (K_Fehl + K_spät): 0,8 heißt, ein Licht ohne Person ist so schlimm wie 4 späte Lichter. Sie
wirkt nur, wo die Karte etwas weiß. Gemessen (MODEL.md 10, „Ziel“; Aufnahmen 6.10. 18:30 – 7.10. 20:40,
Karte von leer an gelernt, *Vorausschau* 1 s wie in der App, 342 Eintritte in dunkle Räume; Licht
mindestens 1 s / 2 s vorher, kurze Fehl-Ein je Stunde):

| Signal | ≥ 1 s | ≥ 2 s | ≥ 1 m | Fehl-Ein je h |
|---|---|---|---|---|
| *wird betreten* | 39 % | 6 % | 52 % | 15,2 |
| *Ziel*, Schwelle 0,8 (Vorgabe) | 26 % | 3 % | 37 % | 8,1 |
| *Ziel*, Schwelle 0,7 | 34 % | 4 % | 45 % | 9,0 |
| *Ziel*, Schwelle 0,5 | 46 % | 8 % | 55 % | 11,3 |

Mit 0,8 halbiert *Ziel* die vergeblichen Lichter und kommt dafür seltener früh; mit 0,5 ist es in beiden
besser als *wird betreten*. Wer früh Licht will, stellt 0,5 ein; wem ein Licht ohne Person schlimmer
ist, bleibt bei 0,8.

**Ziel statt wird betreten in Node-RED:** im Subflow den Eingang *wird betreten* durch
`binary_sensor.presence_<raum>_ziel` ersetzen, sonst nichts. Die Regel bleibt dieselbe (auf Verdacht an,
ohne *besetzt* nach 30 s wieder aus). Beide Entitäten laufen nebeneinander; *wird betreten* bleibt zum
Vergleich. Nachts im Schlafzimmer sperrt man *Ziel* wie bisher *wird betreten* in Node-RED. Ob *Ziel*
im Alltag besser ist, zeigt `tools/entries.py` auf den Aufnahmen (Vorlauf vor jedem Eintritt und
vergebliche Lichter je Stunde, für beide).

**Recorder:** Home Assistant schreibt bei jeder Änderung des Zustands *oder eines Attributs* einer
Entität eine Zeile in seine Datenbank. Die App rundet deshalb grob (Wahrscheinlichkeiten auf 5 %, Zeiten
auf 0,5 s, Wege auf 0,5 m) und schickt die Attribute einer Entität nur neu, wenn ihr Zustand umschaltet
oder sich eines um mindestens zwei Stufen geändert hat und die letzte Änderung mindestens 10 s her ist
(Personenzähler: 60 s). Solange *wird betreten* oder *Ziel* aus ist, sind ihre Attribute fest
(Wahrscheinlichkeit 0, der Rest leer). Die Zustände selbst schalten genau wie vorher, im selben Moment.
Nachgespielt auf 26 h (6./7.10., alle Zonen zusammen): 0.16.0 31 500 Zeilen am Tag, jetzt 14 600, mit
*Ziel*; in der vollsten Stunde 4 950 → 2 640 (Personenzähler 2 970 → 980, *wird betreten* 840 → 270,
*Ziel* 240; *besetzt* 210 und *Bewegung* 930 unverändert, das sind nur Zustände). Wer die Vorhersagen
und Zähler trotzdem nicht im Verlauf haben will, schließt sie in `configuration.yaml` aus (Node-RED
sieht sie weiter, denn es liest die Zustände live; die Personenzähler haben dann auch keine
Langzeitstatistik mehr, wer die will, lässt `sensor.presence_*_count` weg):

```yaml
recorder:
  exclude:
    entity_globs:
      - binary_sensor.presence_*_approaching
      - binary_sensor.presence_*_ziel
      - sensor.presence_*_count
```

**Mit Node-RED** (`node-red-contrib-home-assistant-websocket`): je Raum eine Instanz eines Subflows mit
den beiden Sensoren als Eingang (`server-state-changed`) und dem Licht als Umgebungsvariable; die
Zustände aus, Verdacht, an, Nachlauf in einer Funktion, geschaltet mit `light.turn_on` / `light.turn_off`
(Helligkeit und Farbe kann dann Adaptive Lighting übernehmen). Schaltet jemand das Licht von Hand ein
oder aus, sollte die Automatik für diesen Raum pausieren, bis er eine Weile leer war: erkennbar daran,
dass der Wechsel nicht kurz nach einem eigenen Befehl mit genau diesem Ziel kam.

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
