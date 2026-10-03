# Changelog

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
