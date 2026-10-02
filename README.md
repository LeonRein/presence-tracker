# Presence Tracker

Raumgenaue Anwesenheit über mehrere 24-GHz-Radarsensoren (LD2450 + LD2410C) für Home Assistant.

| Ordner | Inhalt |
|---|---|
| [`case/`](case/README.md) | Gehäuse für den Kombi-Sensor (OpenSCAD, STL, STEP) |
| [`esphome/`](esphome/README.md) | Firmware: Home Assistant per API, Rohdaten per MQTT |
| [`tracker/`](tracker/DOCS.md) | Home-Assistant-App: führt alle Sensoren zusammen, Karte, Zonen, Kalibrierung |
| `tools/` | Mitschneiden des MQTT-Rohdatenstroms für Entwicklung und Tests |

## App installieren

In Home Assistant unter *Einstellungen → Apps → App Store → ⋮ → Repositories* diese URL hinzufügen:

```
https://github.com/LeonRein/presence-tracker
```

Danach erscheint **Presence Tracker** im Store. Voraussetzung ist die Mosquitto-App.
