# Kombi-Präsenzsensor LD2450 + LD2410C – Gehäuse

Flaches Gehäuse (56 × 39,6 × 16 mm) für beide Radare und einen ESP32-S3/C3 SuperMini.
Die untere Tasche ist für den **LD2410C** ausgelegt. Mit `static_radar = "LD2412"` in der `.scad`-Datei
wird sie für den LD2412 umgebaut (dann ist das Gehäuse 34,2 mm hoch).
Der Sensor wird auf einen Halter geschoben (Schwalbenschwanz):

- **Eckhalter** (`stl/corner.stl`): Keil für die Raumecke, der Sensor schaut diagonal in den Raum.
- **Schrankfuß** (`stl/stand.stl`): um 10° nach unten geneigt, mit Kabelkanal unten.

Quelle: `radar-combo-case.scad` (OpenSCAD). Alle Maße sind oben als Parameter änderbar.

## Druck

| Teil | Lage | Hinweis |
|---|---|---|
| `shell.stl` | Front nach unten | Front nur 0,6 mm (Radom, 3 Schichten à 0,2 mm), keine Stützen |
| `lid.stl` | Rückseite nach unten | Nut und Senkungen druckbar ohne Stützen |
| `corner.stl` | stehend | – |
| `test_beams.stl` | Front nach unten | Teststück für Federstege und Radar-Taschen |
| `stand.stl` | auf der Bodenplatte | – |

PLA in Wandfarbe, 0,2 mm Schicht. **Stützstrukturen aus** (oder „nur auf der Druckplatte“), sonst füllt der Slicer den Spalt unter den Federstegen. Die beiden Federstege zwischen den Radar-Taschen werden als Brücke 1 mm über der Front gedruckt; sie dürfen nicht mit der Front verkleben, sonst federn sie nicht. Lüfter 100 %, Brücken-Umfänge erkennen an.

**Nach dem Druck:** Unter dem LD2450-Federsteg (zwischen den beiden Radar-Taschen) sitzen vier kleine Säulen, die nur die Brücke beim Drucken stützen. Vor dem Einsetzen des LD2450 herausbrechen: den Steg an jeder Säule mit einem kleinen Schraubendreher Richtung LD2410C-Tasche drücken, bis die Säule abreißt. Erst danach federt der Steg über seine ganze Länge.

**Teststück zuerst:** `stl/test_beams.stl` ist nur die Front mit beiden Taschen, Federstegen und Rastnasen (Wände gekürzt, etwa 15 Minuten Druck). Damit prüfen, ob die Stege frei sind und beide Radare einrasten, bevor das ganze Gehäuse gedruckt wird. **Kein Silk-, Metallic- oder Carbon-Filament**, das dämpft das Radar.
Maße stammen aus den Hi-Link-Datenblättern. Beim JST-Stecker und beim ESP-Board sind es Schätzwerte.
Druck zuerst `shell.stl` und prüf die Passung.

## Teile

- HLK-LD2450, HLK-LD2410C, ESP32-S3 SuperMini (C3 SuperMini passt auch)
- dünne Litze (28–30 AWG) für den LD2410C
- 2× M2×10 Senkkopf, selbstschneidend (Deckel). Der Deckel hat Durchgangslöcher (Ø 2,6), das Gewinde greift nur im Dom (Ø 2,0). Nicht mit Gewalt anziehen.
- Eckhalter: 2 Schrauben 3–3,5 mm (Kopf bis Ø 7,5 mm, mind. 30 mm lang) + Dübel, oder doppelseitiges Klebeband
- Schrankfuß: am besten ein **gewinkeltes USB-C-Kabel**

## Verkabelung (ESP32-S3 SuperMini)

![Verdrahtung](verdrahtung.svg)

| Radar | Radar-Pin | ESP32-S3 | ESPHome |
|---|---|---|---|
| LD2450 (JST) | 5V / GND | 5V / GND | – |
| LD2450 (JST) | RX ← | GPIO6 | `tx_pin: GPIO6` |
| LD2450 (JST) | TX → | GPIO7 | `rx_pin: GPIO7` |
| LD2410C | VCC / GND | 5V / GND | – |
| LD2410C | RX ← | GPIO9 | `tx_pin: GPIO9` |
| LD2410C | TX → | GPIO8 | `rx_pin: GPIO8` |

Die Pins sind so gewählt, dass die Drähte im Gehäuse kurz bleiben: Die JST-Buchse des LD2450 sitzt
(von vorne gesehen) rechts, gegenüber von GPIO6/7. Die Lötlöcher des LD2410C (TX/RX) liegen nah bei GPIO8/9.
Am ESP-Pin 5V und GND kommen je zwei Drähte an, einer zu jedem Radar.

LD2450-Anschluss: **JST-ZH-Kabel (1,5 mm Raster, 4-polig)**, Belegung an der Buchse von oben: 5V · RX · TX · GND
(laut Datenblatt, mit dem Aufdruck vergleichen). Nach Position anschließen, die Kabelfarben sind nicht genormt.
Die 2×4-Stiftleiste auf der Rückseite bleibt ungenutzt und darf drauf bleiben. Ohne JST-Kabel geht auch sie
(5V · 3.3V · PA9 · GND | RX · TX · DP · DM): Pins kürzen und Litzen an die Pins löten, Auslöten ist nicht nötig.

Der LD2410C ist vorne mit TX · RX · OUT · GND · VCC bedruckt, von hinten (Lötseite) ist die Reihenfolge gespiegelt. OUT bleibt frei.

Beim S3 lassen sich die UARTs auf beliebige GPIOs legen. GPIO0, 3, 45 und 46 meiden, das sind Strapping-Pins.
Die Logs laufen über USB, ein dritter UART bleibt frei.
Baudrate: LD2450 und LD2410C 256000 (ein LD2412 hätte laut Datenblatt 115200).

## Zusammenbau

1. LD2450: JST-ZH-Kabel in die Buchse stecken, die Stiftleiste bleibt ungenutzt. Den Radar mit den goldenen Antennen nach vorne einsetzen: zuerst die Oberkante unter die festen Nasen an der Gehäusewand schieben, dann die Unterkante über die Nasen auf dem federnden Steg zwischen den Taschen drücken. Die Buchse sitzt von vorne gesehen rechts, dort ist im Gehäuse Platz für den Stecker.
2. LD2410C: Die eingelötete 5-polige Stiftleiste **muss ab** (auslöten, oder den Kunststoff aufschneiden und die Pins einzeln ziehen). Hinter dem LD2410C sitzt der ESP mit nur etwa 3 mm Abstand. Dann 4 dünne Litzen (VCC, GND, TX, RX) von hinten in die Lötlöcher an der Oberkante löten und flach zur Seite wegführen. Dann den Radar mit den Antennen nach vorne einsetzen, die Lötlöcher oben: zuerst die Unterkante unter die festen Nasen am Boden schieben, dann die Oberkante über die Nasen auf dem federnden Steg drücken.
3. Alle Kabel an den ESP löten. Den ESP wie die Radare einsetzen, Bauteilseite zum Radar, USB-C nach unten: zuerst das USB-Ende schräg über die beiden festen Keilnasen neben der Buchse auf die Deckelnase setzen, dann das obere Ende auf die Schienen drücken, bis es unter der Rastnase am Federsteg einrastet. Ausgelegt für eine 1,65 mm dicke Platine. Die seitlichen Führungen halten den ESP seitlich. Ein Streifen doppelseitiges Klebeband auf den Schienen ist optional, ein Stück Kapton-Band hinten auf dem LD2410C schützt zusätzlich vor Kurzschluss.
4. Deckel einsetzen (die USB-Buchse gleitet in den Ausschnitt unten) und mit 2× M2 verschrauben.
5. Halter montieren und den Sensor von oben aufschieben. Beim Eckhalter sitzt pro Wand eine Schraube, die senkrecht in die Wand geht. Den Schraubendreher schräg von vorne durch die Senkbohrung neben der Schiene ansetzen.

Bei Bedarf ein Tropfen Heißkleber am Platinenrand, falls ein Radar wackelt.

Montagehöhe laut Datenblatt: 1,5–2 m.
