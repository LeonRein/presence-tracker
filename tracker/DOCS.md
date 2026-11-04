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
  als Wärmekarte, am Umriss ihres Raums abgeschnitten, und den Punkt am wahrscheinlichsten Ort, beides in einer
  Farbe je Person (Pfeil: geht; blass: gerade nicht gesehen). Die Spur bricht ab, wo die Schätzung springt
  (mehr als 1 m oder durch eine Wand). Die Rohdaten der Sensoren (Sicht, Messpunkte) sind
  standardmäßig aus; der Schalter steht unter *Anzeige* und wird im Browser gemerkt. Die Sicht eines Sensors
  zeigt die Karte je Kachel, so wie das Modell mit ihr rechnet (je kräftiger, desto sicherer sieht er eine
  Person dort); solange eine Änderung noch nicht gespeichert ist, sein Blickfeld wie eingegeben.
- **Tab *Live*:** Ganz oben steht, welche Räume besetzt sind (Licht an), darunter die Personenzahl im Haus. In der
  Raumliste stehen die besetzten Räume zuerst. Die Zahlen an der Karte, in der Liste und oben sind dieselben, und
  *besetzt* heißt genau: mindestens eine Person im Raum (siehe *Besetzt*). Die Prozentzahl an einer Person ist die
  Wahrscheinlichkeit, dass *diese Person* dort ist. Ein blasser, gestrichelter Punkt ist eine Person, die gerade
  kein Sensor sieht; eine Wolke ohne Punkt ist eine Person, die dafür nicht sicher genug ist, sie zählt nicht. *Ziel* (mit
  Wahrscheinlichkeit und den gelernten Gängen dahinter) steht nur an Räumen und Bereichen, die noch nicht besetzt
  sind, die Zone pulsiert dann auf der Karte; es bleibt 2 s sichtbar, auch wenn es nur kurz an war.
- **Verbindung:** Oben rechts stehen Sensoren und Personen, auch am Handy. *Sensoren* zählt alle
  eingeschalteten und platzierten Sensoren, auch einen, der seit dem Start nichts gesendet hat (bis 0.26.0
  nur die, von denen Daten kamen: zwei ausgefallene Boards zeigten 5/5); der Punkt ist nur grün, wenn
  alle online sind. Die Sensorliste im Tab *Live* zeigt einen ausgefallenen als *offline*: nach 30 s ohne Frame,
  wie Home Assistant seinen Raum *nicht verfügbar* nennt (bis 0.28 schon nach 15 s). Kommen 3 s lang keine Daten
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
- **Geister**: Eine Spur, die zu keiner Person gehört. Wie oft Geister entstehen und wie lange sie leben, ist
  fest; die App lernt daran nichts (daran, wie lange eine Spur lebt, unterscheidet das Modell Geister von Sitzenden).
- **Sensor drehen oder neu kalibrieren** (gleicher Grundriss): Was über die Personen bekannt ist, bleibt
  (alles Gelernte nicht, siehe nächster Punkt).
  Nur die Spuren dieses Sensors beginnen neu, wie nach einer Datenlücke. Andere Parameter: Das Modell
  beginnt neu mit dem, was es über die Personen wusste. Ein geänderter Grundriss (Wände, Räume, Türen):
  Es beginnt ohne Wissen (siehe *Personen*).
- **Gespeicherte Änderung von Sensorlage oder Grundriss verwirft alles Gesammelte und Gelernte**, für alle
  Sensoren: die Messungen der Kalibrierung, den LD2410C-Hintergrund und die Zielkarte. Das gilt
  für Position, Blickrichtung, Höhe, x-Richtung, Maßstab,
  Öffnungswinkel und Reichweite eines Sensors, für *platziert* / *Von Karte nehmen*, für einen neuen oder
  entfernten Sensor, für Wände, Türen und Zonen, und auch, wenn ein Kalibrierergebnis übernommen und
  gespeichert wird (es ändert Richtung und Maßstab). All das wurde unter der alten Lage gesammelt und passt
  nicht mehr. Nichts verworfen wird bei Namen, beim Aus- und Einschalten eines Sensors und bei Einstellungen.
  Die Meldung nach dem Speichern sagt, was verworfen wurde; der Tab *Kalibrierung* beginnt bei 0. Auch eine
  `tracker.json`, die geändert wurde, während die App nicht lief, verwirft beim Start, was zur alten Lage
  gehört (jede Datei in `/data` trägt den Schlüssel der Lage, unter der sie gesammelt wurde).
- Der **LD2410C** zählt mit seinen Energien je 0,75-m-Entfernungsring (bewegt und ruhig): Wo eine Person
  Energie machen müsste und keine ist, ist niemand; Energie, die niemand erklärt, spricht für jemanden in
  diesem Ring, auch wenn der LD2450 ihn verloren hat. Was eine andere Person schon erklärt, sagt über die
  übrigen nichts. Was jeder LD2410C ohne Personen sieht (Hintergrund), **lernt die App selbst**, nur für
  Sensoren und Grundriss, wie sie sind. Seine Flags und die gemeldete Entfernung zählen nicht mehr.
- **Wände**: Die Radare sehen nicht durch die Betonwände. Ein Messpunkt hinter einer Wand oder außerhalb aller
  Räume ist eine Reflexion und wird verworfen (*Toleranz an Wänden*).
- **Personen**: Wie viele es sind, wird nirgends eingestellt. Nach einem Neustart der App macht das Modell
  mit dem weiter, was es vorher über die Personen wusste (gespeichert alle 10 Minuten und beim Beenden
  in `/data/people.json`, über die Pause vorgerückt); nur die laufenden Spuren beginnen neu. Ohne das
  (erste Installation, neuer Grundriss, *Neu beginnen*) weiß es nichts: Wer in Sicht ist, wird
  gefunden, gleich wie viele; wer hinter einer Tür oder außer Haus ist, wenn er herauskommt. Weitere
  (Gäste) kommen über die Wege nach draußen dazu; wer sicher außer Haus ist, wird vergessen. *Neu
  beginnen* lässt auch die Spuren der Sensoren neu anfangen (wer gerade verfolgt wird, zählt sofort
  wieder), und der LD2410C-Hintergrund lernt danach 5 Minuten lang nichts.
- **Ausfall eines Sensors**: Kommt von einem Sensor 6 s lang kein Frame (Board ohne Strom, abgestürzt,
  WLAN weg), enden seine Spuren. Wen er verfolgt hat, der gilt dann als ungesehen; wo ihn kein anderer Sensor
  sieht, läuft sein Eintrag nach wenigen Minuten ab, wie an jedem Ort, den kein Sensor sieht; der Raum
  bleibt nicht stundenlang besetzt. Ist es der Sensor des Raums selbst, sind die Entitäten dieses Raums
  in Home Assistant *nicht verfügbar*, bis er wieder sendet (siehe *Verfügbarkeit*): Ohne Daten sagt die
  App „weiß ich nicht“ statt „leer“, das Licht bleibt, wie es ist. In der Raumliste steht dann *nicht
  verfügbar*.
- **Fehler melden** (Tab *Live*): Wenn etwas nicht stimmt (Licht fälschlich an oder aus, Licht zu spät,
  Person verloren, Geist, Person am falschen Ort, ungenaues Tracking, hohe Latenz), den Raum (keine
  Vorauswahl) und die Art des Fehlers wählen. Die App
  speichert dazu die Sensordaten der letzten 15 Minuten (der Dialog nennt die Zeit) mit der Konfiguration, dem Gelernten
  (LD2410C-Hintergrund, Zielkarte), dem Stand des Codes und dem, was sie in dieser Zeit sekündlich
  angezeigt hat (unter `/data/reports`, je Meldung etwa 0,2–0,7 MB, in der Liste herunterladbar und
  löschbar, etwa eine Meldung, die nur kam, weil Sensoren ausgefallen waren). Daraus
  entsteht die Wahrheitstabelle für die Bewertung; `tools/replay.py --report DATEI` spielt eine Meldung
  nach und zeigt, wie weit das Nachspiel von der Anzeige der App abweicht. Begann das Modell innerhalb der
  15 Minuten (Start der App, *Neu beginnen*, neuer Grundriss), beginnt das Nachspiel dort mit dem
  damals Gelernten und dem Zustand der Personen, mit dem das Modell begann (etwa 30 kB mehr), und glaubt
  genau, was die App glaubte; sonst beginnt es ohne Wissen am Anfang der Daten und hat das bis zum
  gemeldeten Moment vergessen. Die Meldung enthält die Konfiguration vom Beginn des Nachspiels und jede,
  die die App danach ohne Neustart übernommen hat (ein Sensor kalibriert), mit ihrer Zeit; das Nachspiel
  übernimmt sie zu denselben Zeiten.

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
   sagt die App das: Dann stimmt meist die eingezeichnete Position des Sensors nicht. *Übernehmen* und
   Speichern verwirft danach die gesammelten Messungen und alles Gelernte (sie gehören zur alten Lage); ebenso
   jede andere gespeicherte Änderung von Sensorlage oder Grundriss. *Neu sammeln* nur, wenn ein Sensor real
   gedreht oder versetzt wurde, ohne dass sich seine Zeichnung ändert.
   Unter *Gesammelt seit* steht je Sensor, wie viele Punkte in Bewegung er hat (höchstens einer je
   Sekunde und Spur, so zählt auch die Berechnung) und wie viele der nötigen 3 Stunden mit Gehenden
   schon da sind. Das Ergebnis steht direkt unter *Berechnen*; was für mehrere Sensoren gilt (zu wenig
   Stunden, mehr herumgehen), steht dort einmal, je Sensor nur das Eigene.

Was ein Sensor sieht, zeigt der Tab *Sensoren* auf der Karte: je Kachel seine Sicht einer Person, so wie der
Filter mit ihr rechnet (der ausgewählte Sensor kräftiger), und *Tote Winkel zeigen* die Abdeckung.

**Gelerntes** (Tab *Gelerntes*): alles, was die App im Betrieb lernt, so wie das Modell es gerade verwendet, je
Ebene mit einer kurzen Erklärung. Nur zum Anschauen; geholt wird es beim Öffnen des Tabs und mit *Aktualisieren*
(gerechnet nur dann, das Modell wird dabei nur gelesen). Jede Ebene zeigt alle Sensoren zugleich, jeden in seiner
Farbe. Ein Klick auf einen Sensor auf der Karte (oder in der Liste der Ebene) hebt ihn hervor, die anderen treten
zurück; nochmal klicken oder ein Klick ins Leere: alle gleich. Verschieben lässt sich hier nichts.
- *LD2410C-Hintergrund*: je Sensor die Energie ohne Person je Entfernungsstufe (bewegt und ruhig), gelernt neben
  jetzt gemessen, mit dem Langzeitwert des Sensors, aus wie vielen Stunden beides gelernt ist und wie oft
  Echoquellen beginnen; der gewählte Sensor oben. Der Filter nutzt ihn.
- *Ziele*: die Zielkarte, je Zelle ein Pfeil für jeden Weg hindurch: Die Richtungen der Gänge dort werden zu
  Spitzen zusammengefasst (jede Richtung gehört zu der Spitze, zu der sie ansteigt), und jede Spitze mit
  mindestens 15 % der Gänge der Zelle bekommt einen Pfeil, höchstens 6. Jeder Pfeil beginnt in der Mitte der Zelle,
  so sind Hin- und Rückweg beide zu sehen, und hat die Farbe, wohin die Gänge in seiner Richtung am häufigsten
  weitergingen (Raum hinter der ersten Tür, Raum ohne Sensor, außer Haus, Bereich, grau: bleibt), kräftiger, je
  mehr Gänge. Sich kreuzende Wege sind Pfeile verschiedener Farbe in derselben Zelle. Sie dient nur der Ausgabe
  *Ziel* und ändert den Filter nicht.

Einen gelernten Messfehler nach Abstand gibt es nicht: Wie stark ein Messpunkt in Blickrichtung und seitlich streut,
sind feste Einstellungen (*Messfehler …*, Tab *Einstellungen*).

Änderungen sind ein Entwurf, bis *Speichern* (Knopf unten rechts auf der Karte, hervorgehoben, solange etwas
nicht gespeichert ist, oder Strg+S) gedrückt wird; erst dann rechnet das Modell damit. Der Server bringt die
Konfiguration dabei in die eine Form des Modells (was fehlt, mit seinen Standards; neue Sensoren und Türen
bekommen im Editor schon dieselben Werte), legt genau diese in `/data/tracker.json` ab und schickt sie zurück; die
Oberfläche arbeitet damit weiter. Eine kurze Meldung sagt *Gespeichert* und, wenn das Modell dabei neu startet,
*Modell neu gestartet* (bei fast jeder Änderung an Grundriss, Zonen und
Einstellungen; nicht, wenn nur Sensoren kalibriert oder verschoben wurden, nicht bei Einstellungen, die nur
die Ausgaben betreffen: *Kosten: Licht ohne Person*, *Besetzt bis Wahrscheinlichkeit*,
*Haltezeit LD2410C (Anzeige)*, die Schwellen „Ziel“, und nicht beim Umbenennen), und, wenn sich Sensorlage oder
Grundriss geändert haben, was dabei verworfen wurde. Rückgängig und Wiederholen mit den Knöpfen ↶ ↷ unten
rechts auf der Karte oder mit Strg+Z / Strg+Y. Sie wirken nur auf Änderungen im aktuellen Tab; liegt die
letzte Änderung in einem anderen Tab, sagt die App, in welchem, statt sie unsichtbar zurückzunehmen.
Löschen mit Entf oder Rücktaste, *Von Karte nehmen*, *Neu sammeln* (Kalibrierung) und *Neu beginnen*
(Live) fragen vorher nach. Ein Element bewegt sich erst, wenn es schon ausgewählt war und dann gezogen
wird; einen eigenen Bearbeiten-Modus gibt es nicht.

Zahlenfelder nehmen nur Werte in ihren Grenzen an. Ein leeres Feld oder ein Wert außerhalb (etwa
*Kosten: Licht ohne Person* = 0, was jeden Raum besetzt und jedes Licht eingeschaltet hätte) wird nicht
gespeichert: Der alte Wert bleibt, das Feld wird rot markiert und sagt, was erlaubt ist. Die Grenzen sind die
des Modells: Sein Kern prüft jede Konfiguration, und die Felder lesen ihre Grenzen von dort. Der Server lehnt
eine Änderung außerhalb mit einer Meldung ab, ebenso doppelte IDs von Sensoren oder
Zonen, Wände ohne genau zwei Punkte, unbekannte Zonenarten und -formen und Kreise ohne Mittelpunkt; eine
Konfiguration, mit der das Modell nicht läuft, gilt gar nicht erst (die vorige bleibt). Nach einer Ablehnung
bleibt der Entwurf, wie er ist, mit dem Grund; eine `tracker.json`, die das Modell ablehnt, hält die App beim Start
mit dem Grund im Log an. Ein
Hintergrundbild darf bis 10 m je Pixel haben (ein SVG-Plan in Metern).

## Entitäten in Home Assistant

Gerät **Presence Tracker**, für jeden Raum und Bereich:

| Entität | Bedeutung |
|---|---|
| `binary_sensor.presence_<zone>_occupancy` | Jemand ist in der Zone (siehe unten) |
| `sensor.presence_<zone>_count` | Anzahl Personen, wie die App sie zeigt, Attribute `moving` / `still`, `probability` (nur Auskunft) |
| `binary_sensor.presence_<zone>_moving` | Mindestens eine Person bewegt sich |
| `binary_sensor.presence_<zone>_ziel` | *Ziel*: Jemand, der gerade geht, geht als Nächstes in diesen Raum oder Bereich; aus einer gelernten Karte (siehe unten) |

Einmal für das Gerät: `switch.presence_lernen_pausieren`, *Lernen pausieren* bei Hintergrundaktivität
(Saugroboter, Besuch, Putzen, Feier; siehe unten).

Dazu `presence_haus_*` für das ganze Haus (ohne *außer Haus*). Ein Raum mit *Außer Haus* (Treppenhaus)
bekommt keine Entitäten; hatte er von einer früheren Version welche, entfernt die App sie beim nächsten
Verbinden mit dem Broker. Eine gelöschte Zone nimmt ihren Zustand im Broker mit.

**Verfügbarkeit:** Die Entitäten sind *nicht verfügbar*, solange die App nicht läuft, und nach dem
(Wieder-)Verbinden, bis sie frische Zustände gesendet hat (vorher zeigte Home Assistant kurz die Zustände
vom letzten Lauf als aktuell). Scheitert das Modell dreimal, ohne dazwischen eine Minute zu laufen (etwa
an einer Konfiguration, mit der es nicht rechnen kann), sind sie ebenfalls *nicht verfügbar*, bis es
wieder eine Minute ohne Fehler läuft; im Protokoll steht dann ein Fehler in Großbuchstaben, und das Modell
wird nur noch einmal je Minute neu versucht. Die Entitäten eines Raums mit eigenem Sensor (der Sensor,
der im Raum hängt: sein Name ist die ID des Raums, etwa `kueche`; eingeschaltet und platziert) sind außerdem *nicht verfügbar*, solange
dieser Sensor seit mehr als 30 s keinen Frame gesendet hat (Board ohne Strom, abgestürzt, WLAN weg): alle
vier, *besetzt*, *Personen*, *Bewegung* und *Ziel*. Mit seinem nächsten Frame sind sie
wieder verfügbar, mit dem aktuellen Zustand. Hat ein Raum mehrere eigene Sensoren, erst wenn alle schweigen.
Ein Sensor, der seit dem Start der App noch nichts gesendet hat, zählt erst 30 s nach dem ersten Frame
irgendeines Sensors als stumm. Schweigen alle Sensoren des Hauses zugleich, wartet das Modell zuerst 60 s mit dem
letzten Stand (meist hängt dann HA, der Broker oder der Host, und die Frames kommen danach gestaut; sie werden zu
ihrer Zeit verarbeitet); erst dann gelten sie als stumm. Dafür hat jede dieser Entitäten zwei Verfügbarkeits-Topics,
`presence-tracker/status` und `presence-tracker/zone/<raum>/availability` (beide `online`/`offline`,
beibehalten; verfügbar nur, wenn beide `online` sagen). Räume ohne eigenen Sensor, Bereiche und das Haus
haben nur das erste. Der Node-RED-Flow behält bei *nicht verfügbar* den letzten Zustand. Kaputte MQTT-Nachrichten, ein Browser, der die Live-Ansicht nicht mehr abholt, ein Fehler beim
Speichern oder eine leere bzw. kaputte Datei in `/data` (nach einem Stromausfall) halten die App nicht an:
Sie werden protokolliert, eine kaputte Datei gilt als „nichts gelernt“ für ihren Teil.

**Besetzt** folgt dem, was die App anzeigt: Ein Raum ist besetzt, wenn mindestens eine Person in ihm gezählt
ist, und sonst nicht. Jede Person, die die App kennt, gehört zu ihrem wahrscheinlichsten Ort und hat eine
Wahrscheinlichkeit, dass sie wirklich dort ist. Ein Raum geht an, wenn eine ihm zugeordnete Person die Schwelle aus
*Kosten: Licht ohne Person* erreicht (bei 2: 67 %), und aus, wenn keine Person mehr zugeordnet ist oder keine
*Besetzt bis Wahrscheinlichkeit* erreicht. Der Abstand der beiden verhindert, dass ein Raum flackert. Die App hat
keine Nachlaufzeit: Das verzögerte Ausschalten gehört zu Node-RED (siehe unten). Nach einem Neustart der App sind
alle Räume zuerst aus und gehen nach derselben Regel wieder an.
Für das Schlafzimmer heißt das: Wer schläft und gemessen wird, macht es
besetzt; das Licht dort braucht eine eigene Nacht-Regel in Node-RED.

**Personen:** Die Zahl eines Raums sind die Personen, die in ihm gezählt sind. Jede zählt einmal; die Räume und die
Bereiche ohne Sensor ergeben zusammen `presence_haus_count`. Wer geht, zählt sofort nicht mehr im alten Raum. Eine
Person, die für einen dunklen Raum noch nicht sicher genug ist, zählt nicht: Der Raum zeigt 0 und bleibt aus, in der
Karte steht nur ihre Wolke. Die Vorhersage *Ziel* hängt nicht daran, sie sagt, wohin jemand gleich geht.

Die App schaltet selbst keine Lichter. Was aus den Sensoren wird (Licht an, Verzögerung beim
Ausschalten, Nachtmodus, Helligkeit), entscheidet die Automation in Home Assistant oder Node-RED.
Eine einfache Regel je Raum, die nie schlechter ist als *besetzt* allein:
- *besetzt* an → Licht an, ein laufendes Ausschalten abbrechen;
- *Ziel* an, Licht aus → Licht an „auf Verdacht“; wird der Raum binnen 30 s nicht *besetzt*, wieder aus;
- *besetzt* aus → nach 2 min aus, wenn bis dahin weder *besetzt* noch *Ziel* wieder an war.

**Ziel** ist die Vorhersage: Wie wahrscheinlich geht ein Gehender *als Nächstes* in diesen Raum oder
Bereich? Die App lernt dafür selbst eine Karte, wohin die Gänge von jeder Stelle, in jeder Richtung und
bei jedem Tempo bisher gingen (aus ihren eigenen Gehenden, vergessen über 14 Tage, gespeichert in
`/data/destinations.json`; ändern sich Räume, Türen oder Zonen, beginnt sie leer). Ein Gang endet am Raum
hinter der ersten Tür, in einem Raum ohne Sensor, außer Haus, in dem Bereich, in dem er stehen bleibt oder
seine Spur endet (etwa eine Speisekammer, die der Sensor nur zum Teil sieht), oder er bleibt (stehen
geblieben, in keinem Bereich). Ein Gang durch mehrere Räume zählt je Raum für den nächsten: die Schritte
in der Küche für das Esszimmer, die im Esszimmer für den Flur. Sie lernt nur aus dem, was die App nach
dem Update live sieht: Nach dem Update ist sie leer, aus alten Aufnahmen kommt nichts hinein.

*Ziel* kommt allein aus der Karte. Wo sie noch nichts weiß, ist es aus; die Bewegung allein schaltet nichts.
Je Gehendem zählt sie die Gänge an seiner Stelle in seiner Richtung, und ein angenommener Gang, der nirgends
hinging, zählt dagegen: Die Wahrscheinlichkeit ist der Anteil der Gänge dorthin, mal n / (n + 1) bei n
Gängen. Zwei Gänge genau durch diese Stelle in dieser Richtung ergeben 0,67, vier 0,8; Gänge daneben zählen
weniger (im Test nach 10 gleichen Wegen n = 2–5 an einer Stelle, nach 20 n = 5–10). Auf einem Weg, der Routine ist (vom Schreibtisch zur
Tür), geht *Ziel* nach wenigen Tagen früh an; wo Gänge oft im Türrahmen enden (Küche), seltener. Über einen
Raumteiler sagt die Karte nichts voraus (Wohn- und Esszimmer gelten für die Gänge als ein Raum). Attribute:

| Attribut | Bedeutung |
|---|---|
| `probability` | die Wahrscheinlichkeit, die entscheidet (an ab der Schwelle der Zone) |
| `from` | der Raum des Gehenden, der am wahrscheinlichsten hingeht |
| `distance`, `eta` | sein Abstand zur Tür (ein Bereich: zu seinem nächsten Teil; Luftlinie, m) und die Zeit bei seinem Tempo (s) |
| `person` | seine Nummer in der Anzeige |
| `walks` | wie viele gelernte Gänge hinter der Vorhersage stehen |

Die Schwelle (*Schwelle „Ziel“*, je Raum und Bereich einstellbar, Tab *Einstellungen*) ist
K_Fehl / (K_Fehl + K_spät): 0,8 heißt, ein Licht ohne Person ist so schlimm wie 4 späte Lichter.

Bis 0.55 gab es daneben *wird betreten* (`binary_sensor.presence_<zone>_approaching`): das eigene
Bewegungsmodell 2 s vorausgerechnet, und *Ziel* nahm es, wo die Karte wenig wusste. Beides ist weg; die
Entitäten *wird betreten* entfernt die App beim nächsten Verbinden mit dem Broker. Eine Automation, die
*wird betreten* las, nimmt `binary_sensor.presence_<zone>_ziel`.

**Recorder:** Home Assistant schreibt bei jeder Änderung des Zustands *oder eines Attributs* einer
Entität eine Zeile in seine Datenbank. Die App rundet deshalb grob (Wahrscheinlichkeiten auf 5 %, Zeiten
auf 0,5 s, Wege auf 0,5 m) und schickt die Attribute einer Entität nur neu, wenn ihr Zustand umschaltet
oder sich eines um mindestens zwei Stufen geändert hat und die letzte Änderung mindestens 10 s her ist
(Personenzähler: 60 s). Solange *besetzt* oder *Ziel* aus ist, sind ihre Attribute fest
(Wahrscheinlichkeit 0, der Rest leer). Die Zustände selbst schalten genau wie vorher, im selben Moment.
Nachgespielt auf 26 h (6./7.10., alle Zonen zusammen, damals noch mit *wird betreten*): 0.16.0 31 500
Zeilen am Tag, dann 14 600; in der vollsten Stunde 4 950 → 2 640 (Personenzähler 2 970 → 980, *Ziel* 240;
*besetzt* 210 und *Bewegung* 930 unverändert, das sind nur Zustände). Wer die Vorhersagen
und Zähler trotzdem nicht im Verlauf haben will, schließt sie in `configuration.yaml` aus (Node-RED
sieht sie weiter, denn es liest die Zustände live; die Personenzähler haben dann auch keine
Langzeitstatistik mehr, wer die will, lässt `sensor.presence_*_count` weg):

```yaml
recorder:
  exclude:
    entity_globs:
      - binary_sensor.presence_*_ziel
      - sensor.presence_*_count
```

**Mit Node-RED** (`node-red-contrib-home-assistant-websocket`): je Raum eine Instanz eines Subflows mit
den beiden Sensoren als Eingang (`server-state-changed`) und dem Licht als Umgebungsvariable; die
Zustände aus, Verdacht, an, Nachlauf in einer Funktion, geschaltet mit `light.turn_on` / `light.turn_off`
(Helligkeit und Farbe kann dann Adaptive Lighting übernehmen). Schaltet jemand das Licht von Hand ein
oder aus, sollte die Automatik für diesen Raum pausieren, bis er eine Weile leer war: erkennbar daran,
dass der Wechsel nicht kurz nach einem eigenen Befehl mit genau diesem Ziel kam.

**Lernen pausieren** (`switch.presence_lernen_pausieren`, ein Schalter am Gerät): Die App lernt laufend
mit, was jeder LD2410C ohne Personen misst und wie oft
dort Echoquellen beginnen (Hintergrund), wohin Gänge führen (*Ziel*) und wie die Sensoren zueinander
stehen (Kalibrierdaten). Bei **Hintergrundaktivität**, die anders ist als der Alltag, lernt sie damit das
Falsche: Der Saugroboter wird minutenlang als langsam gehende Person verfolgt, und wo seine Spuren
beginnen, würde für Wochen eine Geisterquelle; ähnlich Besuch, eine Putzkraft oder eine Feier. Solange der
Schalter an ist, lernt die App nichts davon; das Verfolgen, die Räume und die Lichter laufen genau wie
sonst (der Saugroboter darf also weiter das Licht einschalten). Die Live-Ansicht zeigt dann oben
*Lernen pausiert*. Der Zustand übersteht einen Neustart (gespeichert in `/data/pause.json`); der Schalter
hat keine Verfügbarkeit, er lässt sich auch schalten, während die App nicht läuft: Home Assistant legt
den Befehl im Broker ab (*retained*), die App übernimmt ihn beim nächsten Verbinden. Eine Nachlaufzeit
(etwa 2 min, nachdem der Saugroboter angedockt hat) gehört in die Automation, die App nimmt den
Schalter, wie er ist. Fehlermeldungen enthalten die Pausen, die *Aufzeichnung* und `tools/record.py` zeichnen den
Schalter mit auf; die Werkzeuge pausieren beim Nachspielen an denselben Stellen und bewerten dort nichts.

Mit Node-RED: `server-state-changed` auf den Saugroboter (`vacuum.<name>`); jeder Zustand außer
`docked`, `idle`, `charging` und `error` (also `cleaning`, `returning`, `paused`) → `switch.turn_on` auf
`switch.presence_lernen_pausieren`; wieder angedockt (`docked`) und 2 min dort geblieben (*for*: 2 min) →
`switch.turn_off`. `unavailable` und `unknown` (die Integration verbindet sich neu) ändern nichts.

## Aufzeichnung

Für ein paar Tage Testbetrieb, ohne einen Rechner nebenher: Im Tab *Einstellungen*, Abschnitt *Aufzeichnung*, schreibt
die App auf Wunsch alles mit, was sie von den Sensoren bekommt. Standardmäßig aus; der Schalter übersteht einen Neustart
(`/data/recording.json`), er gehört nicht zur Konfiguration, und das Modell sieht davon nichts. Die Wiedergabe zeichnet
nicht auf.

- **Was:** je Stunde eine Datei `/data/recordings/JJJJMMTT-HH.jsonl.gz` (lokale Zeit) mit den Frames, den Statusmeldungen
  der Sensoren und dem Schalter *Lernen pausieren*, jede Zeile wie bei `tools/record.py` (`{"t", "topic", "payload"}`).
  Jede Datei beginnt mit der Konfiguration samt Version (Topic `app/config`, auch bei jeder gespeicherten Änderung; sie
  enthält den Grundriss) und dem Stand des Schalters. Die Wiedergabe und die Werkzeuge überlesen `app/config`.
  Keine Telefon-Zustände. Frames, die die App verwirft, stehen wie in den Fehlermeldungen nicht darin.
- **Größe:** etwa 5 MB je Stunde bei allen Sensoren und 43 Frames/s, in ruhigen Stunden weit weniger. Über alle Aufnahmen
  gilt eine feste Grenze von 3 GB, das sind gut drei Wochen; darüber fallen die ältesten Dateien weg.
- **Absturz:** Die Daten gehen alle 30 s auf die Platte, mehr geht nicht verloren. Nach einem Neustart in derselben Stunde
  geht die Datei weiter; eine abgebrochene Datei wird dabei zuerst bis zur letzten ganzen Zeile repariert.
- **Schreibfehler** (Platte voll): Die Aufzeichnung geht aus und sagt im Abschnitt warum; die App läuft weiter. Wieder
  einschalten geht von Hand.
- **Sicherungen:** `recordings/` ist von den Sicherungen in Home Assistant ausgenommen (`backup_exclude` in
  `config.yaml`). Wer eine Sicherung der App wiederherstellt, verliert deshalb die Aufnahmen: Home Assistant leert dabei
  `/data`. Vorher herunterladen. Auch Deinstallieren löscht sie.

*Alle herunterladen* gibt eine Tar-Datei; *Alle löschen* geht auch während einer Aufnahme, sie läuft dann in einer neuen
Datei weiter. Zum Nachspielen und für `fast_eval` entpacken:

```bash
tar xf aufnahmen-*.tar
cd recordings
for f in *.jsonl.gz; do zcat "$f" > "${f%.gz}"; done
cd ../tracker && uv run python -m presence_tracker --data devdata --replay ../recordings/*.jsonl
python tools/replay.py --recordings recordings --config KONFIGURATION --from "2026-10-31 10:00:00" --to "2026-10-31 11:00:00"
```

Nicht `gunzip`: Das löscht bei einer abgeschnittenen Datei (die letzte eines Absturzes, ohne Neustart in derselben Stunde)
die Ausgabe; `zcat` meldet dort *unexpected end of file*, und alles bis zur letzten Leerung ist da. Für die Wiedergabe
dann die letzte, unvollständige Zeile entfernen, falls es eine gibt.

## Nutzer

Das Panel *Präsenz* steht in der Seitenleiste jedes Home-Assistant-Nutzers. Wer kein Administrator ist, sieht die Tabs
*Live* und *Gelerntes* und kann einen Fehler melden; Bearbeiten, Kalibrierung, Einstellungen, Aufnahmen, *Neu beginnen* und das
Löschen von Meldungen bleiben Administratoren vorbehalten, auch auf dem Server (403). Die Rolle meldet der Supervisor
(Header `X-Remote-User-Groups`, ab Supervisor 2026.10.4); mit einem älteren hat niemand Schreibrechte.

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

Die Web-UI läuft dann auf http://localhost:8099.

Der Filter ist ein Rust-Kern (`tracker/core`, docs/rust-core.md). Bauen und testen:

```bash
cd tracker
uv run maturin develop --uv --release --features testing -m core/presence-py/Cargo.toml  # mit den Hooks der Tests
(cd core && cargo build --release -p presence-replay)  # die Wiedergabe ohne Python, test_replay braucht sie
(cd core && cargo test && cargo clippy --all-targets -- -D warnings)
uv run pytest
```

Gegen ein Wheel ohne die Hooks (wie im Image): `PT_RELEASE_WHEEL=1 uv run pytest -m "not hooks"`.

Das Image baut nur CI, bei einem Tag `v<version>` (`.github/workflows/release.yml`, docs/rust-core.md 5); lokal
zum Ausprobieren:

```bash
cd tracker
uv run maturin build --release -m core/presence-py/Cargo.toml --out dist/amd64  # das Wheel, das das Image installiert
podman build --build-arg BUILD_ARCH=amd64 --build-arg BUILD_VERSION=dev -t presence-tracker .
```
