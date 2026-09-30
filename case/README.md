# Kombi-Präsenzsensor LD2450 + LD2410C – Gehäuse

Flaches Gehäuse (54,7 × 39 × 16 mm) für beide Radare und einen ESP32-S3/C3 SuperMini.
Die untere Tasche ist für den **LD2410C** ausgelegt. Mit `static_radar = "LD2412"` in der `.scad`-Datei
wird sie für den LD2412 umgebaut (dann ist das Gehäuse 34,2 mm hoch).
Der Sensor wird auf einen Halter geschoben (Schwalbenschwanz):

- **Eckhalter** (`stl/corner.stl`): Keil für die Raumecke, der Sensor schaut diagonal in den Raum.
- **Schrankfuß** (`stl/stand.stl`): um 10° nach unten geneigt, mit Kabelkanal unten.

Quelle: `radar-combo-case.scad` (OpenSCAD). Alle Maße sind oben als Parameter änderbar.

## Druck

| Teil | Lage | Hinweis |
|---|---|---|
| `shell.stl` | Front nach unten | Front nur 0,8 mm (Radom), keine Stützen |
| `lid.stl` | Rückseite nach unten | Nut und Senkungen druckbar ohne Stützen |
| `corner.stl` | stehend | – |
| `stand.stl` | auf der Bodenplatte | – |

PLA in Wandfarbe, 0,2 mm Schicht. **Kein Silk-, Metallic- oder Carbon-Filament**, das dämpft das Radar.
Maße stammen aus den Hi-Link-Datenblättern. Beim JST-Stecker und beim ESP-Board sind es Schätzwerte.
Druck zuerst `shell.stl` und prüf die Passung.

## Teile

- HLK-LD2450 (mit Kabel), HLK-LD2410C **ohne** Stiftleiste, ESP32-S3 SuperMini (C3 SuperMini passt auch)
- dünne Litze (28–30 AWG) für den LD2410C
- 2× M2×10 Senkkopf, selbstschneidend (Deckel)
- Eckhalter: 2 Schrauben 3–3,5 mm + Dübel, oder doppelseitiges Klebeband
- Schrankfuß: am besten ein **gewinkeltes USB-C-Kabel**

## Verkabelung (ESP32-S3 SuperMini)

| Radar | Radar-Pin | ESP32-S3 |
|---|---|---|
| LD2450 | 5V / GND | 5V / GND |
| LD2450 | TX → | GPIO1 (RX) |
| LD2450 | RX ← | GPIO2 (TX) |
| LD2410C | VCC / GND | 5V / GND |
| LD2410C | TX → | GPIO4 (RX) |
| LD2410C | RX ← | GPIO5 (TX) |

Beim S3 lassen sich die UARTs auf beliebige GPIOs legen. GPIO0, 3, 45 und 46 meiden, das sind Strapping-Pins.
Die Logs laufen über USB, ein dritter UART bleibt frei.
Baudrate: LD2450 und LD2410C 256000 (ein LD2412 hätte laut Datenblatt 115200).

## Zusammenbau

1. LD2450: JST-Kabel einstecken, Dupont-Enden abschneiden. Den Radar mit den goldenen Antennen nach vorne in die obere Tasche drücken, bis er einrastet. Der Stecker sitzt rechts (von vorne gesehen).
2. LD2410C: 4 dünne Litzen (VCC, GND, TX, RX) von hinten in die Lötlöcher an der Oberkante löten und flach nach hinten wegführen. Hinter dem LD2410C sitzt der ESP mit nur etwa 3 mm Abstand, deshalb keine Stiftleiste verwenden (falls eine eingelötet ist, Pins kurz abschneiden). Dann den Radar mit den Antennen nach vorne in die untere Tasche drücken, die Lötlöcher oben.
3. Alle Kabel an den ESP löten. Den ESP mit der Bauteilseite zum Radar in den Deckel setzen, USB-C nach unten: erst das obere Ende (gegenüber USB-C) schräg unter die Rastlippe am oberen Anschlag schieben, dann das USB-Ende auf die Schienen drücken. Die seitlichen Führungen halten ihn seitlich, die Lippe oben und der USB-Ausschnitt im Gehäuse unten halten ihn in Richtung Radar. Ein Streifen doppelseitiges Klebeband auf den Schienen ist optional, ein Stück Kapton-Band hinten auf dem LD2410C schützt zusätzlich vor Kurzschluss.
4. Deckel einsetzen (die USB-Buchse gleitet in den Ausschnitt unten) und mit 2× M2 verschrauben.
5. Halter montieren und den Sensor von oben aufschieben.

Bei Bedarf ein Tropfen Heißkleber am Platinenrand, falls ein Radar wackelt.

Montagehöhe laut Datenblatt: 1,5–2 m.
