# Changelog

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
