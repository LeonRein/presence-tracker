# Kombi-Präsenzsensor LD2450 + LD2412 – Gehäuse

Flaches Gehäuse (54,7 × 34,2 × 16 mm) für beide Radare und einen ESP32-C3/S3 SuperMini.
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

- HLK-LD2450 (mit Kabel), HLK-LD2412, ESP32-C3 SuperMini (S3 SuperMini passt auch)
- 2× M2×10 Kunststoff-/Blechschraube (Deckel)
- Eckhalter: 2 Schrauben 3–3,5 mm + Dübel, oder doppelseitiges Klebeband
- Schrankfuß: am besten ein **gewinkeltes USB-C-Kabel**

## Verkabelung (ESP32-C3 SuperMini)

| Radar | Radar-Pin | ESP32-C3 |
|---|---|---|
| LD2450 | 5V / GND | 5V / GND |
| LD2450 | TX → | GPIO20 (RX) |
| LD2450 | RX ← | GPIO21 (TX) |
| LD2412 | +5V / GND | 5V / GND |
| LD2412 | TX → | GPIO4 (RX) |
| LD2412 | RX ← | GPIO3 (TX) |

GPIO2/8/9 meiden (Strapping-Pins). Logs laufen über USB, damit bleiben beide UARTs frei.
Baudrate: LD2450 256000, LD2412 laut Datenblatt 115200.

## Zusammenbau

1. LD2450: JST-Kabel einstecken, Dupont-Enden abschneiden. Den Radar mit den goldenen Antennen nach vorne in die obere Tasche drücken, bis er einrastet. Der Stecker sitzt rechts (von vorne gesehen).
2. LD2412: 4 Litzen (5V, GND, TX, RX) von hinten anlöten und den Radar mit den Antennen nach vorne in die untere Tasche drücken.
3. Alle Kabel an den ESP löten. Den ESP mit der Bauteilseite zum Radar auf die Schienen im Deckel legen, USB-C nach unten, und mit einem Streifen doppelseitigem Klebeband fixieren.
4. Deckel einsetzen (die USB-Buchse gleitet in den Ausschnitt unten) und mit 2× M2 verschrauben.
5. Halter montieren und den Sensor von oben aufschieben.

Bei Bedarf ein Tropfen Heißkleber am Platinenrand, falls ein Radar wackelt.

Montagehöhe laut Datenblatt: 1,5–2 m.
