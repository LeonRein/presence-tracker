# Changelog

## 0.8.1

- Aufräumen: Was 0.8.0 nicht mehr benutzte, ist weg (gelernte Clutter-Karte und LD2410C-Statistik aus
  0.6.x, `sensormodel.json`, `dwell.json`, die Einstellungen Messrauschen Geschwindigkeit, eingefrorene
  Ziele, Gewicht der Annahme, LD2410C-Sicht und -Strahl). Aufenthaltsdauern in Räumen ohne Sensor sind
  eine Annahme und werden nicht mehr „gelernt“ (das Lernen war in 0.8.0 schon nicht mehr angeschlossen).
- Die Karte „Geister“ eines Sensors zeigt jetzt die Geisterkarte, mit der der Filter rechnet.
- Die Geisterkarte beginnt auch neu, wenn sich Höhe, Spiegelung oder Maßstab eines Sensors ändern.
- Besetzt heißt jetzt: die Wahrscheinlichkeit, dass jemand im Raum ist, liegt über der Schwelle mit den
  kleinsten erwarteten Kosten fürs Licht (bisher: die wahrscheinlichste Zahl ist größer als 0). Neue
  Einstellung „Kosten: Licht ohne Person“ (2: besetzt ab 67 %). Die Wahrscheinlichkeit steht auch für
  Räume mit Sensor als Attribut `probability` am Personen-Sensor und in der Übersicht.
- Die Geisterkarte bestätigt sich nicht mehr selbst: Ob eine Spur ein Geist war, beurteilt sie ohne
  das, was sie selbst an dieser Stelle sagt (Park et al. 2020). Bisher konnte eine Stelle, die einmal
  als Geisterort galt, immer mehr dazu werden, bis auch jemand, der dort sitzt, als Geist zählte
  (Simulation: bis 0,34 je Spur gelernt, jetzt unter 0,01). Die Karte beginnt nach dem Update neu.
- Keine feste Bewohnerzahl mehr: Neuankömmlinge (Gäste) kommen über die Wege nach draußen dazu, wer
  sicher außer Haus ist, wird vergessen (MODEL.md 3.4, 5.5). Die Einstellung „Bewohner“ heißt jetzt
  „Personen beim Start“: mit so vielen beginnt das Modell, wenn es nichts weiß. In der Simulation wird
  ein Dritter, der zu zwei Sitzenden hereinkommt, gezählt (bisher nicht möglich). Mehr Hypothesen,
  solange Spuren laufen: dort etwa doppelte Rechenzeit.

## 0.8.0

Neuer Filter (Modell: MODEL.md 0.8.0). Noch in Arbeit: Auf der Wahrheitsdatenbank ist „Licht ohne Person“ in
einigen Episoden noch häufiger als mit 0.6.21.

- Gemessen wird an den Spuren des LD2450 (wann er eine Spur beginnt, verliert, wiederfindet), nicht mehr
  jeder Frame als unabhängige Messung.
- Keine Partikel mehr: Personen mit einer Spur sind eine Mischung aus „steht“ und „geht“ (je ein
  Kalman-Filter), alle anderen eine Dichte über dem Grundriss. Der Filter enthält keine Zufallszahlen,
  gleiche Daten ergeben immer dasselbe Ergebnis; etwa ein Drittel der Rechenzeit.
- Gehen als gemessenes Bewegungsmodell: im Mittel 0,85 m/s, die Richtung ist nach gut einer Sekunde
  vergessen (bis 0.6.21 behielt ein Gehender sie viel zu lange und war zu schnell).
- Wie gut ein Sitzender erfasst wird, wird je Aufenthalt mitgeschätzt (Abend auf dem Sofa: 0 % falsch,
  9a70790: 97 %).
- Geisterkarte je Sensor, gelernt von der App selbst; beginnt neu, sobald ein Sensor verschoben,
  hinzugefügt oder entfernt wird.
- Entfernt: Körperabstand zweier Personen, LD2410C-Auswertung (nur noch angezeigt), Ziele und Wege.

## 0.6.21

- Das Modell hängt nicht mehr davon ab, wie die Zeit in Frames und Rechenschritte zerlegt wird. Drei
  Fehler, alle beim Prüfen gegen die Grundlagen gefunden und mit Tests belegt:
  - Ein Ziel nach einer Leerlauf-Lücke (leere Frames lässt die Firmware aus) zählte als „irgendwann
    in der Lücke getroffen“, richtig ist „die ganze Lücke nicht, erst im letzten Frame“. Nach 5 s
    passte das Ziel bisher bis zu 10⁶-mal zu gut zu einer Wolke, die angeblich die ganze Zeit
    ungesehen dort war.
  - Der Körperterm (zwei Personen nicht näher als 0,3 m) wurde in jedem Rechenschritt neu
    multipliziert und drückte überlappende Wolken ohne neue Messung immer weiter auseinander.
    Er gilt jetzt einmal, für den Zustand jetzt.
  - Im Leerlauf ist ein Rechenschritt bis 1,7 s lang. Gehende streuten darin dreimal zu weit und
    hielten erst am Ende des Schritts an. Jetzt exakt diskretisiert (Särkkä & Solin 2019) und in
    Schritten von höchstens 0,2 s.
- Wahrheitsdatenbank gegenüber 0.6.19, 48 Seeds (die Kalibrierung von 0.6.20 ändert daran nichts) (Drehbuch, Abend, Leon geht hinaus, Küche) bzw. 12:
  leeres Haus mit angedocktem Roboter 30,6 % → 1,7 % falsch (Licht an, obwohl niemand da ist: 1481 s
  → 88 s); Leon geht hinaus 2,5 % → 0,8 % (Licht ohne Person 154 s → 117 s); Drehbuch 11,2 % →
  10,0 % (Licht aus, obwohl jemand da ist: 33 s → 16 s); Abend 4,1 % → 2,4 %; Küche 7,6 % → 6,7 %;
  Nächte, Reset und Morgen unverändert. Rechenzeit im Leerlauf höher (Auswertung +50 %).
- tools/evaluate.py bewertet zusätzlich die Verteilung der Personenzahl (Log- und Brier-Score) und
  das Licht: je Raum Sekunden „an, obwohl niemand da ist“, „aus, obwohl jemand da ist“ und die Zeit
  bis zum Einschalten, bei 30, 60 und 120 s Ausschaltverzögerung.

## 0.6.20

- Kalibrierung verschiebt keine Sensoren mehr. Die eingezeichneten Positionen gelten; je Sensor
  werden Blickrichtung, x-Richtung und ein neuer **Maßstab** bestimmt (wahre Entfernung / gemessene,
  auch von Hand im Sensor einstellbar). Vorher wurde die gemessene Anordnung starr auf alle
  Positionen gelegt: ein schlecht bestimmter Sensor zog die anderen mit (Lauf am 5.10., 19:18: Küche
  1,36 m, Esszimmer 79 cm, Wohnzimmer 57 cm daneben). Am selben Lauf jetzt: Küche bleibt, wie sie ist
  (nur 13–15 gemeinsame Messungen), Esszimmer +1,3°, Wohnzimmer 0°.
- Unbrauchbare Ergebnisse einzelner Sensoren sperren das Übernehmen der anderen nicht mehr.

## 0.6.19

- Keine Schonfrist mehr an eingefrorenen Zielen. Der LD2450 meldet ein verlorenes Ziel oft bis 35 s
  bitgenau weiter; bisher galt die Person dort so lange als „nicht widerlegt“. Gemessen ist das vor
  allem die Spur von jemandem, der gerade gegangen ist. Wahrheitsdatenbank, 12 Seeds: Leon geht hinaus
  2,8 % → 2,3 % falsch (verschwindet nach 23 s statt 33 s), Küche 7,3 % → 5,4 %, Drehbuch gleich mit
  weniger Wechseln der Anzeige; leeres Haus mit angedocktem Roboter 25 % → 31 %.
- tools/replay.py: eine Aufnahme durch den Tracker abspielen und ansehen, was er zeigt.

## 0.6.18

- Gehende behalten ihre Richtung länger: Geschwindigkeitsänderungen wie gemessen (0,6 statt 1,0
  m/s² je √s, aus 4,6 h Zielspuren). Wer zur Tür hinausgeht, bleibt im Modell nicht mehr davor stehen.
  Wahrheitsdatenbank, 12 Seeds: Leon geht hinaus 7,9 % → 2,8 % falsch, Abend 8,9 % → 0 %, Drehbuch
  9,4 % → 9,3 %.

## 0.6.17

- Wie gut die Sensoren an den Rändern sehen, folgt jetzt der Messung (22 h Aufnahmen): Gehende bis 7 m
  fast wie nah, auch etwas über den Rand des Sichtfelds hinaus. Vorher galten die Ränder als fast
  blind, und die Wolke einer nicht mehr gesehenen Person floss dorthin und blieb (linke Wohnzimmerwand,
  6-m-Zonen).
- Ist niemand da, senden die Sensoren nur alle 5 s einen Frame; die ganze Lücke zählt jetzt als Zeit
  ohne Treffer (vorher höchstens 1 s). Personen ohne Bestätigung verschwinden dadurch etwa fünfmal
  schneller.
- Wahrheitsdatenbank, 12 Seeds, gegenüber 0.6.16: Leon geht durch und wieder in den Flur (5.10., 16:43)
  17 % → 8 % falsch (verschwindet im Median nach 36 s), leeres Haus nach dem Andocken 49 % → 22 %,
  Drehbuch 11,9 % → 9,4 %, Morgen 0,4 %, Nächte und Reset 0 %. Abend 0 % → 8,9 %: in einem von 12
  Durchläufen bleibt eine zweite Person auf dem Sofa.

## 0.6.16

- Eine Person, die nichts mehr bestätigt, verschwindet jetzt. Bisher wurde die Wolke einer Person als
  Ganzes neu gezogen, und Orte mit kleinem Gewicht (außer Haus, Flur, …) verloren dabei alle Partikel:
  Danach gab es „die Person ist gar nicht hier“ nicht mehr, und ein Phantom blieb stundenlang. Jetzt ist
  jeder Ort eine eigene Komponente mit exaktem Gewicht (Mixture Particle Filter), die nie ausstirbt.
- Wie lange echte Personen ungesehen bleiben, fällt jenseits von 2 Minuten wie gemessen (ein Drittel je
  Minute), nicht mehr wie 1/Zeit.
- Wahrheitsdatenbank, 12 Seeds, gegenüber 0.6.15: Abend 8,3 % → 0 % falsch, Morgen mit Phantom 1,0 % →
  0,5 %, Mittag mit leerem Haus (5.10., 12:18–13:35) 67 % → 49 %, Nächte und Reset 0 %. Drehbuch 9,7 % →
  11,9 %: In einem von 12 Durchläufen fehlt eine echte Person auf dem Sofa, die beide Sensoren gut zwei
  Minuten nicht sahen. Bekannt: Der angedockte Saugroboter wird vom Wohnzimmer-Sensor gesehen und kann
  als Person stehen bleiben; Station und Sofa liegen außerhalb des Bereichs, in dem der LD2410C zählt.

## 0.6.15

- „Störer“-Zonen entfernt. Sie warfen jede Messung darin weg, auch die einer echten Person, eine
  feste Regel statt einer Wahrscheinlichkeit; Stellen mit häufigen Geistern lernt die App selbst.
- Die große Zahl (oben und in der Übersicht) sind jetzt die Personen in den Räumen mit Sensor, nicht
  „im Haus“ (dort zählen auch Vermutungen über Räume ohne Sensor).
- „Spuren“ heißt jetzt „Personen im Modell“: je Person, wo sie am wahrscheinlichsten ist (Raum, Gruppe
  ohne Sensor, außer Haus, mit Prozent) und ob sie sich bewegt. „Spuren neu aufnehmen“ heißt
  „Neu beginnen“ und sagt, was es tut.
- Grundriss und Zonen: keine Personenzahlen mehr in den Raumlisten (für Räume einer Gruppe ohne Sensor
  waren sie immer 0); stattdessen „Sensor“ / „ohne Sensor“, und die Raumdetails sagen, mit welchen
  Räumen ein Raum ohne Sensor zusammenhängt und ob man dort das Haus verlassen kann.
- Einstellungen: Bewohner und Vorausschau offen, die gemessenen Werte des Modells eingeklappt unter
  „Experten“. „Alle auf Standard“ lud auch nach „Abbrechen“ neu.
- Tabs haben ihre Adresse (Zurück-Knopf, Links); auf dem Handy brechen sie um statt zu verschwinden.
  Raumnamen auf der Karte liegen über den Personen. Zahlen mit Komma, „1 Ziel“, kleinere Textfehler.

## 0.6.14

- Übersicht: „Räume mit Sensor“ und „Räume ohne Sensor“ folgen dem Grundriss. Bisher fehlten die Räume
  ohne Sensor mit Ausgang (Flur, Schlafzimmer, Bad, Arbeitszimmer, Treppe), und Küche und Balkon
  standen doppelt da. Räume ohne Sensor, die über Türen zusammenhängen, stehen als eine Gruppe da
  (gekennzeichnet „Ausgang“, wenn man dort das Haus verlassen kann); auf der Karte zeigen sie keine
  eigene Zahl mehr, denn das Modell weiß nur „irgendwo in der Gruppe“.

## 0.6.13

- Keine zweite Person mehr, die mit einer echten „mitläuft“: Jeder Sensor sieht eine Person ein Stück
  woanders, und dieser Versatz bleibt einige Sekunden (gemessen). Bisher wurde er in jedem Frame neu
  bestraft, so dass „zwei Personen, jeder Sensor sieht eine“ billiger war als „eine Person“. Jetzt merkt
  sich das Modell den Versatz je Sensor; eine anhaltende Abweichung zählt einmal je Sekunde statt zehnmal.
- LD2410C: Energie, die eine andere Person schon erklärt, zählt vor dem Mischen der Nachbarstufen nicht
  mehr. Vorher erzeugte eine Person direkt vor dem Esszimmer-Sensor eine Stufe weiter eine unsichtbare
  zweite (5.10. morgens, eine Stunde lang).
- Wahrheitsdatenbank, 12 Seeds, gegenüber 0.6.12: nach dem Zurücksetzen 15 % → 0 % falsch, Abend
  25 % → 0 %, Morgen mit Phantom 17 % → 0,4 %, Nächte 0 %, Drehbuch 8,3 % → 7,9 %.
- Der Simulator kann einen wandernden Versatz je Sensor (für Tests).

## 0.6.12

- Das Phantom an der Wohnzimmerwand (5.10. ab 09:13, ohne ein einziges Ziel): Der LD2410C im
  Esszimmer-Sensor hatte für 4,5–6 m Entfernung (das Sofa) gelernt, dass niedrige Energie dort
  häufiger mit als ohne Person vorkommt; er sieht Sitzende in dieser Entfernung kaum. So sprach
  „nichts los“ alle 3 s für eine Person in 5–6 m. Jetzt kann mehr Energie nur für eine Person
  sprechen, nie weniger. Mit dem gelernten Zustand wie auf HA: Nächte ohne Phantom (vorher 6 % und
  24 % der Zeit), der Morgen des 5.10. 17 % statt 47 % falsch.
- Neue Auswertung (tools/evaluate.py) mit einer Datenbank echter Situationen und ihrer Wahrheit; die
  Drehbuch-Seite kann die Wahrheit jetzt nebenbei festhalten („Jetzt gerade“).

## 0.6.11

Aufgeräumt:
- Der alte Tracker vor dem Partikelmodell (Kalman/IMM, Spuren, Hypothesen) ist ganz entfernt. Die App nutzte
  davon nur noch die Aufbereitung der Rohdaten. Das Sensormodell enthält nur noch die Erkennung aus der
  Geometrie, die gelernte Geisterkarte und die LD2410C-Verteilungen. Die Geisterkarte bleibt: ohne sie war
  der Testdurchlauf in drei Messreihen jedes Mal etwas schlechter.
- Die Einstellungen zeigen nur noch Parameter, die das Modell liest (vorher 80, viele aus dem alten Tracker).
- Eine App ohne eingezeichnete Räume (z. B. während der ersten Kalibrierung) stürzt nicht mehr ab.

Testdurchlauf: 96,7 % (12 Läufe, 95–97 %), Lauf für Lauf gleich wie 0.6.10.

## 0.6.10

- Geister behalten ihre Geschwindigkeit: Ein vom Sensor nachgeführtes Geisterziel (am 5.10. um 06:20
  im leeren Esszimmer, konstant −0,72 m/s) wurde bisher nach drei Frames als gehende Person erklärt und
  blieb dann stundenlang unsichtbar im Raum. Jetzt bleibt es ein Geist. Nachttest (leere Räume,
  23:15–06:28): keine Phantom-Sekunde.

Testdurchlauf: 96,7 % (12 Läufe, 95–97 %).

## 0.6.9

- Zwei Personen am Esstisch, obwohl nur eine dort saß (nach dem Zurücksetzen am 5.10.): Die beiden
  Sensoren messen dieselbe Person am Tisch 0,5 m auseinander. Seit 0.6.5 durfte eine zweite Person
  neben der ersten ohne eigenes Ziel bleiben („zwei Nahe ergeben ein Ziel“), und so erklärte „jeder
  Sensor sieht eine andere Person“ die Messungen besser als „eine Person“. Diese Regel ist wieder
  entfernt; jede Person wird unabhängig erkannt. Beim gemeinsamen Gehen kann dafür wieder eine Person
  zurückbleiben (Drehbuch-Schritt 8).

Testdurchlauf: 95,6 % (12 Läufe, 92–97 %), ein Viertel weniger Rechenzeit.

## 0.6.8

- „bewegt“ und „ruhig“ zählen nur Personen in Sicht. Wer hinter einer Tür ist (Flur, Küche), von dem
  weiß die App nicht, ob er sich bewegt; er zählte bisher als „ruhig“. Die Zusammenfassung heißt jetzt
  „… Personen im Haus“ und nennt die Personen außer Sicht.

## 0.6.7

Aufgeräumt nach Weglass-Tests auf dem Drehbuch (je ein Teil aus, 8 Läufe, mit und ohne vorab
gelernte Daten):
- Die je Ort gelernte Erkennungswahrscheinlichkeit ist entfernt, auch ihre Karte in der Oberfläche.
  Sie brachte nichts; die Annahme aus der Geometrie reicht.
- Ein je Ort gelernter Versatz zwischen den Sensoren wurde ausprobiert und nicht übernommen: Damit
  zählte die App öfter zu viele Personen.
- Die Verhaltensraster (Anhalten, Aufstehen, Aussetzer je Ort) sind entfernt. Sie wurden weder gelernt
  noch geladen und wirkten nicht.
- Geblieben, weil messbar nötig: Körperabstand, Geister-Objekte, LD2410C, „geht / steht“ als eigener
  Zustand (ohne ihn 84,6 % statt 94,4 %).

Testdurchlauf: 94,9 % (12 Läufe, 92–97 %), etwas weniger Rechenzeit.

## 0.6.6

- Fehler seit 0.6.2: Nach einem Neustart zählte die Uhr „nicht gesehen“ der unbekannten Personen ab
  1970. Sie galten vom ersten Frame an als „seit Jahrzehnten ungesehen“ und konnten unbemerkt im gut
  beobachteten Raum stehen. So hing nach dem Update auf 0.6.5 eine zweite Person im Wohnzimmer, obwohl
  niemand dort war. Die Uhren beginnen jetzt mit dem ersten Frame.

Testdurchlauf: 94,8 % (12 Läufe, 83–97 %).

## 0.6.5

- Auch in Räumen ohne Sensor hat eine Person einen Ort. Sieht ein Sensor jemanden durch die offene
  Küchentür, ist das eine Messung wie jede andere, und wer aus der Küche herausgeht, läuft heraus.
  Bisher galt „vor der Küchentür stehen geblieben“ (Küchentest 4.10., 18:23).
- Verliert der LD2450 ein Ziel, meldet er es noch gut eine Sekunde weiter, mit immer derselben
  Geschwindigkeit (gemessen bei 70 % aller Zielenden). Solche gehaltenen Ziele zählen nicht mehr als
  Messung; der Wohnzimmer-Sensor hielt einen vor der Küchentür fest, als die Person schon drin war.
- Solange ein eingefrorenes Ziel nichts sagt, läuft dort auch die Uhr „nicht gesehen“ nicht. Bisher
  galt jemand nach 35 s an einem eingefrorenen Ziel als im langen Aussetzer und konnte danach unbemerkt
  vor der Balkontür stehen bleiben.
- Zwei Personen dicht nebeneinander geben dem LD2450 oft nur ein Ziel (gemessen). Wer neben jemandem
  hergeht, ohne eigenes Ziel, ist deshalb kein Widerspruch mehr; vorher blieb beim gemeinsamen Gehen
  oft eine Person auf dem Sofa zurück.

Testdurchlauf: 95,6 % (12 Läufe, 92–97,5 %; 0.6.4: 90,5 %). Der Küchenschritt klappt in allen Läufen
(0.6.4: in 4 von 12).

## 0.6.4

- Geister nach Messung: In den leeren Räumen nachts gibt es kaum welche (0,4 pro Stunde), neben
  Sitzenden keine. Echos entstehen, solange jemand geht. Die angenommene Geisterdichte war um
  Größenordnungen zu hoch und ist jetzt gemessen.
- Geister mit Lebensdauer: Ein Echo, das 1–2 s an derselben Stelle steht, ist ein Geist und kein
  wiederholter Beweis für eine Person (Phantom-Person nach dem Kalibrierlauf am 4.10.).
- Gehende im Blickfeld werden nach etwa 0,7 s wieder gefunden (gemessen), nicht erst nach vielen
  Sekunden. Wer geht, kann nicht unbemerkt durch einen gut gesehenen Raum laufen.
- Der LD2410C zählt je Entfernungsstufe erst als Beleg, wenn dort genug gelernt ist. Nach dem
  Umhängen bestätigte die angenommene Verteilung sonst eine Person an einer Stelle, an der nie jemand war.

Testdurchlauf: 91 % (12 Läufe, 84–96 %).

## 0.6.3

- Räume ohne Sensor, die zusammen einen Bereich bilden (Flur, Bad, Schlafzimmer, Arbeitszimmer, Treppe),
  bekommen keine eigene Personenzahl mehr, nur die Wahrscheinlichkeit „jemand ist in diesem Bereich“ (wie
  in 0.5.0). Küche und Balkon sind je ein Bereich für sich und zählen weiter.
- Wächter: Hängt die App länger als 15 s, schreibt sie ins Protokoll, an welcher Stelle (am 4.10. hing
  sie nach dem Verschieben eines Sensors; lokal ließ sich das nicht nachstellen).

## 0.6.2

- Start ohne Wissen: Die Beobachtung beginnt beim Start. Bisher galt jeder mögliche Ort als „schon
  lange ungesehen“, und eine Person konnte nach einem Neustart unbemerkt mitten im Raum hängen bleiben.
- Eingefrorene Ziele des LD2450 sagen nur bis 35 s nichts über ihre Stelle (so lange friert eine sitzende
  Person höchstens ein, gemessen). Länger eingefroren ist kein Mensch.
- Ziele näher als 0,3 m am Sensor werden verworfen: Dort kann der Sensor in 1,5 m Höhe keine Person
  sehen, sie kommen von der Montage.

Testdurchlauf: 92 % (12 Läufe), wie 0.6.1.

## 0.6.1

- Der LD2410C fließt ein: Alle 3 s (seine gemessene Korrelationszeit) gewichtet die Energie in der
  Entfernungsstufe jede mögliche Lage einer Person, gegen die gelernte Energie ohne Person. Die
  Verteilungen lernt die App von sicher gesehenen Personen; wer nach einem langen Aussetzer am selben Ort
  wieder gefunden wird, zählt rückwirkend als dort sitzend.
- Jede Person kann außer Haus sein: Aus dem Flur-Bereich mit der Treppe verlässt man das Haus und kommt
  wieder. Wer nicht da ist, wird nicht mehr irgendwo im Raum angezeigt.
- Reichweite: Hinter der eingestellten Reichweite fällt die angenommene Erkennung weich ab statt auf 0.

Testdurchlauf (wie 0.6.0, 12 Läufe, mit vorher gelernten Sensorkarten): richtige Personenzahl 92 % im
Mittel (0.6.0: 88–89 %). Unbekannte Neuankömmlinge sind noch nicht drin (MODEL.md 3.2).

## 0.6.0

Neues Modell: Jede Person ist eine Partikelwolke, keine Spur mehr (MODEL.md). Kein Kalman-Filter, keine
Hypothesenverwaltung, keine Sonderregeln mehr für verlorene, neue oder doppelte Spuren.

- Jede Person ist eine Überlagerung möglicher Aufenthaltsorte: im Raum (Ort, Geschwindigkeit, steht /
  geht) oder hinter einer Tür. Jede Messung gewichtet sie neu, Wände halten sie auf, Türen führen in die
  Bereiche dahinter. Wer auf den Balkon geht, ist auf dem Balkon und kommt als dieselbe Person zurück. Es
  bleiben keine Geister an Türen liegen.
- Welche Messung zu wem gehört, wird über alle Zuordnungen summiert. Zwei Körper stehen nicht am selben Fleck.
- Nicht gesehen werden: eine Wahrscheinlichkeit aus Erkennung je Stelle und gemessener Aussetzer-Dauer,
  statt der Grenze „nach 1,5 s verloren“. Eingefrorene Ziele des LD2450 sagen nichts über die Stelle.
- Gelernt werden nur Erkennung und Geisterdichte je Sensor, jeweils mit einem zweiten Sensor als
  unabhängigem Beleg. Verhalten wird bewusst nicht gelernt.
- Die Live-Karte zeigt die Wolke jeder Person als Wärmekarte.
- Noch nicht gebaut: Ankünfte von draußen (vorerst *Bewohner* Personen), LD2410C-Energie als Beweis,
  gelernter Sensorversatz.

Gemessen auf einem Testdurchlauf zu zweit mit notierter Wahrheit (4.10., 16 Schritte, 26 min: Sofa, Tisch,
Küche, Balkon, nebeneinander, Kreuzen, Weggehen): richtige Personenzahl in Wohn- und Esszimmer 89 % der
Sekunden (12 Läufe mit verschiedenen Zufallszahlen: 82–94 %), 0.5.0 auf denselben Daten 80 %.

## 0.5.0

Dritter Schritt: Verdeckte Personen sind irgendwo, und alle Regeln dafür sind Wahrscheinlichkeiten.

- Eine verdeckte Person ist im Raum (am Kalman-Ort), hinter einer der Türen (Küche, Balkon, Flur-Bereich mit
  Schlafzimmer und Haustür) oder war keine eigene Person (Geist, doppelte Spur). Kein „weg“: Menschen
  verschwinden nicht.
- Zu einer Tür kommt man nur zu Fuß: wer beim Verlust auf sie zuging (Richtung aus dem Kalman-Filter, ein
  laufender Aussetzer dauert mit der gemessenen Statistik gehender Personen an), oder wer aufsteht (Rate aus
  790 gemessenen Sitzphasen, sinkt mit der Sitzdauer), jeweils nur zu dem Teil, den kein Sensor auf dem Weg
  gesehen hätte.
- Beweise im Raum: gelernte Aussetzer-Statistik des LD2450 (inklusive Wiedererkennung durch eine neue Spur),
  LD2410C-Energie mit getrennten Verteilungen für Sitzende, Gehende und leer (rückwirkend gelernt, wenn ein
  Sitzender wieder erkannt wird), als Mischung über die unsichere Entfernungsstufe; wer im Strahl ist,
  erklärt die Energie in seiner Entfernung.
- Doppelte Spuren: statt der Verschmelzungsregel ein Beweis aus der gemessenen Trennschärfe des LD2450 (zwei
  Personen dicht nebeneinander geben meist ein Ziel, eine Person fast nie zwei). Dicht neben jemandem
  Gemessenem ist ein fehlendes eigenes Ziel kein Beweis gegen eine sitzende Person.
- Wer eine neue Spur ist, entscheidet die Wahrscheinlichkeit: eine bekannte Person (Kalman-Ort und Zeit seit
  dem Verlust, oder hinter der Tür, an der die Spur entsteht) gegen jemand Neues. Rückkehrer behalten ihre
  Nummer, ihre Aufenthaltsdauer wird gelernt.
- Die Ausgabe gewichtet jede Spur mit der Zahl der Bewohner (*Bewohner*, *Besuch*).
- Zuordnung über die volle Messwahrscheinlichkeit (Position und Radialgeschwindigkeit) der Bewegungsmodelle.
- Ersetzt: Austragen, Übernahme- und Wiederaufnahme-Fenster, Verschmelzen, Mitzählen in Räumen ohne Sensor,
  feste LD2410C-Werte, maximale Verdeckungszeit.

Gemessen: auf simulierten Abenden mit bekannter Wahrheit (Sensorstatistik an die Aufnahmen angepasst)
stimmt die Personenzahl im Raum in 88–95 % der Sekunden (0.4.0: 48–51 %), auf zwei weiteren, nie zum
Abstimmen benutzten Abenden in 82–87 % (0.4.0: 50–58 %). Wohnzimmer/Esszimmer belegt zu 98–100 % richtig
(0.4.0: 76–94 %). Auf den Aufnahmen vom 3.10. (14,7 h) etwa gleichauf mit 0.4.0: mehr als zwei
Personen 0,7 % / 0,1 % (bis 21 Uhr / danach; 0.4.0: 0,5 % / 0,0 %), zwei getrennt Gesehene als zwei gezählt
82 % / 94 % (0.4.0: 83 % / 78 %), nachts leer 100 %, „gesehen, aber niemand gezählt“ 2,0 min (0.4.0: 1,0 min).

Experimentell, nicht in der App benutzt: `pf.py`, ein Partikelfilter über die Bewohner (keine Spuren). Auf
den simulierten Abenden derzeit schlechter (69–75 %).

## 0.4.0

Zweiter Schritt zum probabilistischen Tracker: Ob eine neue Spur eine Person ist, entscheidet eine
Wahrscheinlichkeit statt fester Regeln.

- Jede neue Spur startet mit einer Wahrscheinlichkeit je nach Ort (Eingang/Tür zu einem Raum ohne Sensor 30 %,
  sonst 2 %). Jede Messung eines Sensors erhöht sie, abhängig davon, wie zuverlässig der Sensor dort erkennt
  und wie oft er dort Geister meldet (beides aus dem Sensormodell, gelernt wo genug Daten da sind). Sieht ein
  Sensor die Stelle gut und meldet nichts, sinkt sie. Neben einer gehenden Person sind Geister (Echos) häufiger.
  Ab 50 % ist es eine Person.
- Ersetzt die festen Bestätigungszeiten, die Zwei-Sensoren-Regel, die Randregel und die Echo-Regel. In Räumen
  mit nur einem Sensor gibt es keine Gegenbeweise; dort entscheidet die Zeit.
- Messungen eines Sensors zählen nur einmal je 3 s als unabhängige Beobachtung (der LD2450 glättet intern).
- Auf den Aufnahmen vom 3.10.: mehr als 2 Personen 0,43 % → 0,38 % der Zeit, sonst gleich.

## 0.3.1

- Gelerntes übernehmen: `POST api/learned` nimmt ein offline aus Aufnahmen gelerntes Sensormodell und
  Aufenthaltsdauern entgegen.
- Tab *Sensoren*: Der Abschnitt „Was der Sensor gelernt hat“ ist als reine Anzeige erkennbar (Auswahl
  „Karte einblenden“), der gelernte Messfehler steht immer da.

## 0.3.0

Erster Schritt zu einem durchgehend probabilistischen Tracker.

- Messfehler wachsen mit dem Abstand (in Blickrichtung 15 cm + 2 cm/m, seitlich 10 cm + 5 cm/m), statt fester
  15 cm bzw. 5°. Gewählt per Vergleich auf den aufgezeichneten Daten: weniger Fehlerkennungen (0,7 % → 0,4 %).
- Sensormodell (vorerst nur Anzeige, das Tracking nutzt es noch nicht): pro Sensor die Erkennungswahrscheinlichkeit
  aus der Geometrie und gelernt aus dem Betrieb, eine Geisterkarte (nur aus unabhängigen Beweisen gelernt: ein
  anderer Sensor sieht die Stelle gut und misst nichts) und der gelernte Messfehler nach Abstand. Gespeichert in
  `/data/sensormodel.json`, ältere Daten verlieren mit 7 Tagen Halbwertszeit an Gewicht. Tab *Sensoren* →
  *Sensormodell*.

## 0.2.7

- Räume ohne Sensor: statt eines festen Zählers eine Wahrscheinlichkeit pro Besuch, die mit der Zeit sinkt.
  Sie hängt davon ab, wie sicher jemand hineinging, wie lange Besuche dort üblicherweise dauern (gelernt aus
  den beobachteten Besuchen, gespeichert in `/data/dwell.json`) und ob ein Sensor die Tür überhaupt sieht.
  Fällt der Sensor an der Tür aus, wird ein Besuch nach wenigen Minuten vergessen statt nach Stunden.
  Live-Ansicht und Home Assistant (Attribut `probability`) zeigen die Wahrscheinlichkeit.
- Zwei Menschen verschmelzen nicht mehr: Nur eine Spur, die ohne Herkunft mitten im Raum entstanden ist,
  darf in eine andere eingeschmolzen werden. Wer durch eine Tür kam, aus einem Raum zurückkam oder beim
  Start da war, bleibt eine eigene Person.
- Ausgefallene Sensoren zählen bei der Bestätigung neuer Personen und bei der Türbeobachtung nicht mit.

## 0.2.6

- Räume ohne Sensor bilden unbeobachtete Bereiche. *Offen* (mit Eingang wie dem Treppenhaus): Türen dorthin
  wirken wie Eingänge. *Geschlossen* (Balkon, Küche mit einer Tür): Die App zählt, wer hineingeht; heraus
  kommt nur, wer vorher hineinging. Spiegelbilder in der Balkontür werden so nicht zu Personen, und Balkon
  und Küche melden „besetzt“ und die Anzahl an Home Assistant. Ohne gezählte Person (z. B. nach einem
  Neustart) zählt nur, wer mindestens 1,5 m aus der Tür in den Raum geht.
- Messpunkte in geschlossenen Räumen ohne Sensor (durch eine Glastür gesehen) werden verworfen.
- Ein Raum gilt erst als von einem Sensor beobachtet, wenn der Sensor mindestens 60 % davon sieht.

## 0.2.5

- Eingefrorene LD2450-Ziele: Der Radar meldet ein Ziel manchmal bis zu 35 s mit exakt gleichen Koordinaten
  weiter, nachdem die Person gegangen ist. Solche Ziele werden nicht mehr verfolgt (*Eingefrorene Ziele nach*).
- Echos: Neben jemandem, der gerade geht, entsteht mitten im Raum keine neue Person (*Kein Auftauchen neben
  Gehenden*). Mehrwege-Reflexionen laufen dort mit.
- Wer an einer Tür kurz aus dem Blick gerät, wird ohne neue Bestätigung weiterverfolgt (bis 2,5 m bzw. 10 s
  nach dem Austragen). Ausgetragen wird an Türen nur, wer kurz vorher ging; Austragungskreis 1,2 m.
- Zwei Sensoren müssen eine neue Person nur dort beide sehen, wo beide sicher hinschauen (15° innerhalb des
  Sichtfeldrands).
- Kalibrierung: Echos weit hinter Wänden und eingefrorene Ziele zählen nicht. Bewertet wird nach der Zahl der
  passenden Messungen (mindestens 150) statt nach ihrem Anteil.

## 0.2.4

- Kalibrierung ohne Anker: Aus den Messungen ergibt sich, wie die Sensoren zueinander stehen; diese starre
  Anordnung wird auf die eingezeichneten Positionen gelegt. Blickrichtungen müssen nur noch grob stimmen.
  Eine falsche Blickrichtung des bisherigen Ankers hat vorher andere Sensoren um Meter verschoben.
- Die Radare sehen nicht durch Wände: Messpunkte hinter einer Wand werden verworfen (*Toleranz an Wänden*).
  Sichtlinien beginnen 20 cm vor dem Sensor, damit er nicht hinter seiner eigenen Wand landet.
- Neue Person: Sehen zwei Sensoren die Stelle, müssen beide sie erkennen. Würde mitten im Raum jemand neu
  erscheinen, den eine verdeckte Person erreicht haben könnte, übernimmt die verdeckte Spur.
- Türen zu Räumen ohne Sensor wirken wie Eingänge: schneller erkannt, nach dem Hinausgehen nach 3 s ausgetragen.
- Doppelte Ziele und Reflexionen bis 1 m neben einer Person werden zusammengefasst; Eingänge bestätigen nach 1 s.

## 0.2.3

- Kalibrierung nutzt nur Personen in Bewegung. Wer still sitzt, stört nicht mehr, und lange Sitzphasen
  ergeben kein unsinniges Ergebnis (vorher z. B. 7 m Versatz und 100° Drehung aus Alltagsdaten).
- Kalibrierung bleibt in der Nähe der eingezeichneten Lage (1,5 m, 45°) und bewertet jedes Ergebnis
  (gut / unsicher / unbrauchbar mit Begründung). Unbrauchbare Ergebnisse sind nicht vorausgewählt.
- Messpunkte außerhalb aller Räume sind Reflexionen (z. B. an der Fensterwand) und werden verworfen
  (*Toleranz außerhalb der Räume*). In der Live-Ansicht erscheinen sie als graue Kreise.

## 0.2.2

- Statische Dateien liegen unter einem Pfad, der sich mit jeder Änderung ändert: Der Browser mischt nie mehr
  Module zweier Versionen (Fehler „list.map is not a function“ nach einem Update).
- Übergangscode entfernt: Umwandlung alter Wand-Linienzüge, Modus mit von Hand gezeichneten Räumen,
  Raum-Import aus der Saugroboter-Karte.

## 0.2.1

- Jede Wand ist ein gerades Segment von Ecke zu Ecke. Bestehende Linienzüge werden beim Laden einmalig in
  Segmente geteilt, gerade aneinanderstoßende Stücke zusammengefasst.
- Wand parallel ziehen: angrenzende Wände an den Ecken gleiten mit, Wände mit T-Stoß verlängern sich, Türen gehen mit.
- Doppelklick auf eine Wand teilt sie, auf ein Ende verbindet es mit der anschließenden Wand.
- Linien rasten exakt auf Wänden ein, wo eine 45°-Richtung eine Wand trifft.
- Türen bleiben vollständig auf ihrer Wand; beim Ziehen eines Türendes bleibt das andere stehen.

## 0.2.0

- Räume entstehen aus den Wänden: geschlossene Flächen zwischen Wänden, Türen und Raumgrenzen. Räume werden
  nur noch benannt und können als *Eingang* markiert werden. Namen und Entitäten bleiben beim Bearbeiten erhalten.
- Türen sind eigene Objekte auf einer Wand (verschieben, Breite ziehen). Sie trennen Räume, sind für die
  Sichtlinien der Radare aber offen. Durchgänge ohne Tür bleiben Lücken.
- Neue Linienart *Raumgrenze* (ohne Wand), z. B. zwischen Wohn- und Essbereich.
- Bestehende Konfigurationen behalten ihre gezeichneten Räume, bis sie im Tab *Grundriss* umgestellt werden.
- Doppelklick wird beim Loslassen erkannt: Anklicken und sofort Ziehen verschiebt, statt einen Punkt einzufügen.

## 0.1.4

- Wände, Zonen und Sensoren lassen sich nur noch im jeweiligen Tab auswählen und bearbeiten.
- Wände und Polygone rasten in 45°-Schritten ein, beim Ziehen eines Punkts zu beiden Nachbarn (exakte rechte Winkel).
- Kanten lassen sich parallel verschieben, die Nachbarkanten bleiben in ihrem Winkel. Rechteckzonen: Seiten ziehen.
- Verbundene Wandenden bewegen sich gemeinsam. Neue Punkte per Doppelklick auf eine Kante.

## 0.1.3

- Saugroboter-Karte wird über die Kalibrierpunkte des Roboters exakt eingepasst (statt über die Raumrechtecke).

## 0.1.2

- Hintergrundbilder lassen sich auch per JSON hochladen (base64 oder https-URL), für Skripte.

## 0.1.1

- LD2410C-Haltezeit in der App statt im Radar (Firmware setzt den Timeout auf 0).
- Energie des LD2410C pro Entfernungsstufe im Tab *Sensoren* (Firmware mit Engineering Mode).

## 0.1.0

- Erste Version: Tracker (IMM-Kalman-Filter, Zuordnung, Ein-/Ausgänge), Karte mit Wänden, Zonen und Sensoren,
  tote Winkel, automatische Kalibrierung, Zonen-Entitäten per MQTT-Discovery.
