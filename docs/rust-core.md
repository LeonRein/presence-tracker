# Entwurf: der Filter des Presence Trackers in Rust

Stand 27.10.2026, Branch `rust-core` (von `perf-core` 3cbc6ad = 0.31.1). **Von Leon freigegeben am 27.10.**
(zweite Fassung mit seinen Ergänzungen, Abschnitt 8). Auftrag: `~/.config/presence-tracker/dev/rust-port/AUFTRAG.md`; Leons Vorgaben vom 27.10.
(neue Grenze) eingearbeitet.

**Ziel:** dasselbe Modell (MODEL.md eingefroren), mindestens 2,5× weniger CPU als 0.30.0 (`ab.sh 1200`:
56,9 s → höchstens 22,8 s), ein lesbares Rust-Projekt mit von Anfang an durchdachter Ownership.

**Entschieden (Leon, 27.10.):** Prototyp ohne Pools mit Kopieren an der Grenze; Freigeben über die neu gebaute
Halterliste; amd64 mit `x86-64-v3`; `seg_replay --init pickle` entfällt, die tiefen Analysen in `tools/ld2410/`
laufen nur auf einem Arbeitsbaum von 0.31.1. Die frühere Frage „Gelerntes bleibt Python“ entfällt: Es kommt nach Rust.

## 1. Schnitt

**Grundsatz (Leon, 29.10., berichtigt): eine Regel ohne Ausnahmen, kein Code doppelt.**
- **Rust (Kern): das Modell** – alles, was MODEL.md beschreibt, aus der *gespeicherten* Konfiguration und den
  Messwerten, einschließlich dessen, was die Oberfläche über das Modell zeigt (Sicht je Sensor, Abdeckung und tote
  Winkel, Zonenzustände, Kalibrierschätzung, Prüfung der gespeicherten Konfiguration). Der Kern bekommt die fertige
  Karte, wenn der Nutzer speichert; einen ungespeicherten Entwurf rechnet er nicht.
- **JavaScript (Oberfläche): die Bedienung** – der Karteneditor (Zeichnen, Einrasten, Wände teilen und verbinden,
  mitgehende Wände und Türen, Räume aus Wänden, Türen auf Wände setzen), die Darstellung. Beispiel für „Bedienung,
  nicht Modell“: **das Hintergrundbild** – Lage, Maßstab, Drehung, Deckkraft, Ausrichten an zwei Punkten, Einpassen;
  die Oberfläche prüft und begrenzt es selbst; das Modell liest den Schlüssel `background` nie (die Hülle reicht ihn
  nicht weiter, `model.model_part`; `canonical` behält ihn unverändert), eine Änderung nur am Bild fasst das Modell
  nicht an.
- **Python: verbinden und ablegen** – MQTT, Home Assistant, HTTP, Dateien; nur formatieren.

Die Frage je Änderung: Ist es das Modell (aus gespeicherter Konfiguration und Daten) → Rust; Bedienen und Bearbeiten
→ JavaScript; Transport und Speichern → Python. (Die erste Fassung „alles, was rechnet, ist Rust“ holte den Editor in
den Kern, 0.47.0: unnötige feinteilige API für etwas, das mit dem Filter nichts zu tun hat; 0.48.0 nahm es zurück.)
Tests und Dev-Werkzeuge gehören nicht zum Programm.

**Eine Fassung der Konfiguration (0.50.0, Leon 29.10.): ihre Bedeutung legt der Kern fest.** Es gibt keinen Wert
außerhalb des Kerns, der von ihm abweichen kann: Die Standards stehen nur im Kern (`config::defaults()`:
`TrackerParams::default()`, Vorlagen für Sensor – mit Blickrichtung –, Tür und Wand), und der Editor legt jedes neue
Element nur aus dieser Vorlage an (`GET /api/config` → `defaults`). Speichern ist ein Rundweg: Der Editor arbeitet an
einem Entwurf, bis „Speichern“ gedrückt wird (Knopf, Strg+S); `PUT /api/config` → der Kern prüft und bringt die
Konfiguration in ihre eine Form (`config::canonical`: geprüft, Fehlendes aus den Standards, je Teil genau die Felder,
die das Modell liest, Zahlen, wie es sie liest; Hintergrundbild und Etikettpunkte der Räume, die Teile der Oberfläche,
unverändert, Unbekanntes weg; Ablehnung mit dem Problem des Kerns) → Python schreibt genau diese Form in die Datei und
gibt sie zurück → die Oberfläche arbeitet mit der Antwort weiter. `GET /api/config` und der Start der App lesen die
Datei durch den Kern (eine Datei, die er ablehnt: der Start bricht mit dem Grund ab). Python hat kein eigenes Modell
der Konfiguration: keine Standards, keine Prüfung, keine Feldnamen; es liest nur die IDs der Sensoren (MQTT).

**Die eine Naht zwischen Editor und Modell: Türen.** Der Editor setzt eine Tür genau auf ihre Wand (mm) und hält sie
dort (sie geht mit, wenn die Wand sich bewegt, und mit ihr, wenn die Wand gelöscht wird; eine Wand mit Tür wird keine
Raumgrenze); der Kern liest nur die Wand, auf der ihr Mittelpunkt liegt (`plan::wall_of_door`, `DOOR_ON_WALL` = 1 cm für
die mm-Rundung), und seine Prüfung weist eine Tür auf keiner Wand ab (bis 0.47.0 suchte er die nächste Wand bis
25 cm). Der Editor liest die 1 cm vom Kern (`derived.plan.door_on_wall`, 0.49.0), keine zweite Zahl. Zum Zeichnen der
Öffnungen und für den Spurbruch liefert der Kern je Tür ihre Wand und die Öffnung entlang dieser Wand, an den
Wandenden gekappt (`derived.plan.doors`: `{wall, t0, t1}`, 0.50.0); die Oberfläche rechnet die Regel nicht nach.

**Rust ist der ganze Filter: alles, was MODEL.md beschreibt.** Er ist ein reiner Kern: keine Ein-/Ausgabe
(keine Dateien, kein Netz), keine Uhr (jede Zeit kommt als Argument), keine Threads, keine Zufallszahlen.
**Python ist die Hülle.**

| Rust (`presence-core`) | MODEL.md | heute |
|---|---|---|
| Konfiguration lesen (serde aus dem JSON von `Config.to_dict()`) und ableiten: Orte, beobachtete Räume (≥ 60 %), offene/geschlossene Bereiche, Wände und Sichtstücke, Sensor des Raums | 2, 6 | `model.Config._places`, `hidden`, `floorplan.wall_pieces`/`sight_segments`, `roomseen.own_room` |
| Welt- und Kachelbau: Raster, Kacheln, Gehmatrix (`expm`), Ende eines Wegs (`(μI − Q)⁻¹`), Sicht je Sensor samt `_spread_sight`, LD2410C-Sicht, Regionen | 2, 5.3 | `world.py`, `tiling.py`, `sensormodel.py`, `filter._grid` |
| Modellparameter und abgeleitete Formen (Aufenthaltsarten, κ-Gitter, Amplitudengitter, Copula) | 3, 4 | `filtermodel.py`, `unobserved.py` |
| Frames → Detektionen (Boden, Maßstab, hinter der Wand, am Sensor), Bildzeit aus der Uptime (`SensorClock`), Prüfen der Nutzlast | 4.1, 4.4 | `frames.py` |
| LD2450-Spuren: Verknüpfen, gehalten/eingefroren, Ende bei Datenverlust | 4.1 | `sensortracks.py` |
| Hypothesen, Gauß-Mischungen, Kacheldichten, Unbekannte; Bewegen, Evidenz, Verzweigen, Zusammenlegen, Abschneiden, Zurückgeben, Ablauf, Datenlücken | 3, 4.1, 4.2, 5, 7 | `filter.py`, `gauss.py`, `hidden.py`, `kernels.py` |
| LD2410C: Sammeln, Likelihood, Echoquellen, Verrechnung; **Hintergrund, Anker und Echorate lernen** | 4.3 | `ld2410.py`, `filter.py` |
| **Geisterkarte** (lernen, vergessen, Rate je Ort) | 4.2 | `ghostmap.py` |
| **Zielkarte** (Geometrie, Lernen der Gänge, Abfrage) | 6 „Ziel“ | `destination.py` |
| Ausgaben: Zähl- und Ortsverteilungen, Belegt mit der **RoomSeen-Regel** und „stumm“, *wird betreten*, *Ziel*, Personen der Anzeige, Snapshot | 6 | `filter.py`, `roomseen.py` |
| Pausen als **Eingabe** (Intervalle; was in eine Pause fällt, lernt nichts) | 4.2 | `pause.Pauses.paused` |
| Gespeicherte Formate lesen und schreiben, **unverändert**: `people_state`, Geisterkarte, LD2410C-Hintergrund, Zielkarte (als Zeichenketten/Dicts; Dateien schreibt Python) | 5.3, 4.2, 4.3, 6 | `to_dict`/`from_dict` der Klassen |
| Kalibrierung: Messzeilen je Frame, Schätzer (Paare, Übergaben, Grundriss, Maßstäbe; EM, Gauß-Newton, robuste Kovarianz, Jackknife), Vorschlag mit Gründen, Fortschritt (`calibration/`, 0.45.0) | 10 | `calibration.py` |
| Was die Anzeige ableitet: Raum und wahrscheinlichster Ort einer Person, Regionen (Zahl, P, Aufenthalt), Karte eines Sensors mit Geisterrate, LD2410C-Anzeige mit Haltezeit, „belegt“ und Schwellen je Zone, Heim-Zonen für HA; der Weg einer neuen Konfiguration (`reconfigure.rs`) (0.46.0) | 6 | `filter.py`, `zones.py`, `ha.py`, `geometry.py` |
| Die Modellgrößen, die die Oberfläche zeigt (`derived.rs`), nur Daten, keine Geometrie zum Zeichnen (0.49.0): der Plan mit dem Anteil der 60-%-Regel, die Schwellen, die Zahlen, die der Editor liest (Tür auf der Wand, Länge einer LD2410C-Stufe); aus der laufenden Welt je Kachel die Zahl der Sensoren, die sie sehen, und die Sicht jedes Sensors (`Core.coverage`); je Person Mittel und Streuung ihrer Gauß-Anteile mit dem Raum des Mittels, Masse je Kachel, „bewegt“; Gesamtzahl im beobachteten Bereich | 2, 4.1, 5.3, 6 | `static/js` (util, view, panels, coverage.js); die Polygone (Sichtfläche, Wolkenschnitt, Wände mit Türlücken) und der Spurbruch von 0.47.0/0.48.0 sind wieder Zeichnen (JS) |

| Python (Hülle): Modul | was es tut (nach dem Grundsatz oben, Stand 0.48.0) |
|---|---|
| `app.py` | nimmt entgegen (MQTT-Frames, HTTP der Oberfläche, Bilder), hält den Takt (alle 0,1 s `step` und Zonenzustände), speichert und lädt (tracker.json, Gelerntes, people.json, pause.json, calibration.npz, Fehlerberichte), sendet (Home Assistant über `ha.py`, Browser), Betrieb (Neubeginn nach Fehlern des Modells, Last). Rechnet nichts am Modell; jede gespeicherte Konfiguration geht durch den Kern in seine Form (`canonical`), die App legt genau sie ab und gibt sie zurück; eine Änderung nur an Hintergrundbild oder Etikettpunkten fasst das Modell nicht an (0.50.0). |
| `filter.py` | die Hülle des Kerns (`presence_core.Core`): reicht Konfiguration, Frames, Zeit und Pausen hinein, wandelt um (JSON, Ortsnamen ↔ Indizes), ruft die Listener, rundet für die Anzeige (auch die Abdeckung, 0.50.0); welcher Weg eine neue Konfiguration nimmt, entscheidet der Kern (`presence_core.changes`). Tests und Werkzeuge lesen den Kern direkt (`Tracker.core`: Hypothesen, Verteilungen, „belegt“, Sicht …, nur mit dem Feature `testing`), die Hülle hat dafür keine eigenen Hüllen mehr (0.50.0). |
| `calibration.py` | sammelt die Messzeilen, die der Kern je Frame liefert (`presence_core.Walking`), hält einen Tag, speichert `calibration.npz`, gibt sie dem Kern im Thread (`presence_core.calibrate`, `calibration_status`) und fasst seine Gründe in die Sätze der Oberfläche. |
| `model.py` | die Konfiguration in der Form des Kerns (`presence_core.canonical`) als Dict, Laden und Speichern genau dieser Form; was davon das Modell liest (`model_part`/`model_json`: alles außer Hintergrundbild und Etikettpunkten der Räume); das Problem des Kerns als Daten für die Oberfläche (`problem`). Keine Standards, keine Prüfung, keine Bezeichnungen (0.50.0: die Bezeichnungen und Sätze stehen in `static/js/util.js`). |
| `ha.py`, `zones.py` | Home Assistant: Entitäten der Heim-Zonen des Kerns (`plan()["home"]`), Verfügbarkeit, Rundung und Halten der Ausgabe (wann gesendet wird). |
| `pause.py` | der Schalter „Lernen pausieren“: Nachrichten lesen, Intervalle halten und speichern; ob eine Zeit pausiert ist, sagt der Kern. |
| `frames.py`, `sources.py`, `util.py`, `__main__.py` | Daten der Anzeige eines Frames; MQTT und Wiedergabe; atomar schreiben, Log drosseln; Start. |
| `static/js/*` | die Oberfläche, die Bedienung: der Karteneditor (`tools.js`: Zeichnen mit Einrasten und Hilfslinien, mitgehende Wände und Türen, teilen/verbinden, Zonen, Sensor setzen, Hintergrundbild ausrichten und einpassen; `floorplan.js`: Räume aus Wänden; `util.js`: Tür auf ihrer Wand, Längen, Flächen, Etiketten, Grenzen der Bildebenen), das ganze Zeichnen (`view.js`, `panels.js`: Wände wie gezeichnet mit den Türöffnungen, Sicht und Abdeckung als Kacheln, Wolken am Umriss ihres Raums geschnitten, wo eine Spur abbricht). Die Modellgrößen dazu kommen vom Kern als Daten für den gespeicherten Stand (`derived` mit `GET`/`PUT /api/config`, `GET /api/coverage`, die Live-Nachricht); bis eine Änderung gespeichert ist, zeigt die Karte die Sensoren mit ihrem Blickfeld wie eingegeben (0.48.0, 0.49.0). Keine Zahl des Modells steht in JS: „Tür auf der Wand“ (`derived.plan.door_on_wall`), die Öffnung jeder Tür (`derived.plan.doors`), die Länge einer LD2410C-Stufe (`derived.ld2410_gate`), die Sicht, ab der ein Sensor eine Kachel sieht (`coverage.sees`), die Grenzen der Felder (`config_limits`, auch die Breite einer Tür), die Sensoren in Gebrauch (`derived.plan.in_use`), die Zonen des Heims (`derived.plan.home`), welche Parameter keinen Neustart brauchen (`derived.output_params`), die Werte neuer Elemente (`defaults`), ob die Kalibrierung die x-Richtung ändert (`apply.mirror`). Die Bezeichnungen der Felder und die Sätze der Probleme des Kerns stehen nur hier (`util.LABELS`, `problemText`, 0.50.0). |

`sim.py` (erzeugt Frames, rechnet) gehört nur zu Tests und Werkzeugen: `tracker/tests/sim.py` (0.47.0).

Was dabei aus Python verschwindet: `kernels.py`, `gauss.py`, `hidden.py`, `filter.py` (bis auf die Hülle),
`tiling.py`, `world.py`, `sensormodel.py`, `sensortracks.py`, `frames.detections`, `ghostmap.py`, `destination.py`,
`roomseen.py`, `filtermodel.py`, `unobserved.py`, `ld2410.py`, das Abgeleitete aus `model.py` – zusammen etwa
8000 Zeilen, dazu numba/llvmlite.

## 2. Datenmodell und Ownership

```
Tracker (Rust, presence_core::Tracker)       besitzt alles
├── world: World            einmal gebaut aus Konfiguration + Parametern, unveränderlich bis reconfigure
│   ├── params: Params      Modellparameter und abgeleitete Formen (filtermodel)
│   ├── plan: Plan          Orte, Räume, Bereiche, Wände, Türen, Sensoren (aus der Konfiguration abgeleitet)
│   ├── raster: Raster      0,1-m-Raster: Ort je Zelle, Raum je Zelle, Sicht je Sensor (LD2450, LD2410C)
│   └── tiles: Tiles        Kacheln, Gehmatrix, Türraten, Regionen, Raten und Sicht je Kachel
├── sensors: Vec<SensorState>   je Sensor: Uhr, LD2450-Spuren, Laufzeitstand, LD2410C-Sammlung/Gedächtnis/Echoquellen, RoomSeen-Stand
├── filter: Filter              Hypothesen und Objekte
│   ├── hyps: Vec<Hyp>
│   ├── mixtures: Pool<Mixture>   Gauß-Mischungen (Personen mit Spur, Quellen von Geistern)
│   ├── densities: Densities      Kacheldichten (Personen ohne Spur, Unbekannte, Teile hinter der Tür)
│   ├── holders: Holders          Objekt → Hypothesen (CSR), neu nach jedem Ereignis
│   └── tracks: Tracks            laufende Spuren im Filter (heute segs, _live_by_sensor)
├── learned: Learned            Geisterkarte, LD2410C-Hintergrund und Echorate, Zielkarte
├── pauses: Pauses              Eingabe
└── clock: Clock, loglik, Ausgabe-Cache
```

**Geliehen wird nach der Vorgabe:** Jede Schritt-Funktion bekommt `&World` und `&mut` genau auf das, was sie ändert,
z. B. `motion::move_densities(&world.tiles, &mut densities, dt)`, `imm::step(&world, &mut mixture, …) -> f64`,
`ghost_map.add_birth(&mut self, …)`. Funktionen, die Objekte gewichten, **geben die Log-Faktoren je Objekt
zurück**; eine eigene Funktion verteilt sie über die Halter auf `hyp.logw`. So braucht nie etwas gleichzeitig
`&mut` auf Hypothesen und Pools.

### 2.0 Lernen in der Reihenfolge von heute

Die Lerner (`learned.ghost_map`, `learned.ld_background`, `learned.destinations`) sind eigene Felder neben `filter`.
Ein Schritt, der lernt, leiht beide **getrennt** aus (`&mut Filter` und `&mut GhostMap` usw., disjunkte Felder von
`Tracker`) und lernt an derselben Stelle wie der Python-Code – keine Lernsätze, die erst nach dem Frame angewandt
werden. Belegt im Code von 0.31.1 (`filter.py`):

| Lerner | wo gelernt | wo gelesen, danach im selben Ablauf |
|---|---|---|
| Geisterkarte | `_evidence` Schritt 1: `add_watch(delta)`, dann `fade(t)` (verwirft bei `True` die Summen `_ghost_total`); Schritt 2: `_end` → `add_birth(z0, P(Geist))`, verwirft `_ghost_total[si]` | Schritt 4: `_branch` liest `_ghost_rate(si, z)` (Karte an der Geburt) – nach Beobachtungszeit, Vergessen und den Geburten der in diesem Frame beendeten Spuren. Schritt 1 liest `_ghost_rate_total(si)` **vor** `add_watch`. |
| LD2410C-Hintergrund, Anker | `_ld_weigh` am Ende: `learn(sid, b, mus, prior_w, …)` mit `b` und `mus` vom **Anfang** dieser Bewertung (Stand vor den Frames, J_Park2020), vor `_normalize` | die nächste Bewertung dieses Sensors (`b(sid)` am Anfang); die Sensoren eines Flush nacheinander in `sorted(si)` |
| Echorate | `_ld_weigh` direkt nach `learn`: `learn_echoes(sid, began, rate, nobody, time)` mit der Rate, die **vor** `echo.predict` gelesen wurde | die nächste Bewertung (`rate(sid)` vor `predict`) |
| Zielkarte | `step` nach `_flush` und `_recycle`, höchstens alle `MAX_STEP`: `Learner.observe` → `fade(t)`, dann Gänge vorrücken, `add` | die Ausgaben (`targets`) danach |
| alle | in einer Pause nichts (`pauses.paused(t0, t1)`); die Pausen sind Eingabe des Kerns | – |

Diese Tabelle wird ein Test je Zeile (die Reihenfolge an einem kleinen Fall, z. B. eine Spur endet und eine neue
beginnt im selben Frame: die Rate der Karte bei der Geburt schließt die eben gelernte Geburt ein).

### 2.1 Pools mit stabilen Indizes

- `MixtureId`, `DensityId`: Indizes (newtypes über `u32`), stabil, solange das Objekt lebt.
- **Gauß-Mischungen:** `Pool<Mixture>` = `Vec<Option<Mixture>>` + Freiliste. Eine Mischung ist klein
  (d = 2 + 2 je Spur, meist ≤ 6) und wird in einem Stück bearbeitet; ihre Vektoren werden nur beim Verzweigen
  angelegt.
- **Kacheldichten zusammenhängend:** `Densities` hält je Feld eine Matrix mit einer Zeile je Dichte
  (`walk: rows × n`, `still: rows × (L·K·n)`, `region: rows × (R·A)`, dazu `out`, `clock`, `r`, Intensität ja/nein je
  Zeile; die gemessene Amplitude `amp`/`keep` als `Option` je Zeile, weil sie selten ist). Die Zeilen sind
  **dicht**: Ein Index zeigt über eine Tabelle auf seine Zeile; beim Freigeben rückt die letzte Zeile in die Lücke.
  Damit ist das Gehen aller Dichten ein Matrixprodukt über die ersten `rows` Zeilen ohne Zusammenkopieren.
  (Ticken nicht alle Zeilen gleich oft, werden die tickenden in einen Puffer gesammelt.)
- Die Teile „hinter der Tür“ einer Gauß-Mischung sind Zeilen derselben `Densities` (sie gehen im selben Produkt),
  gehören aber **genau ihrer Mischung**: Kopiert man die Mischung, bekommt sie eine eigene Zeile.

### 2.2 Teilen und Freigeben

- `Hyp` hält nur Indizes. Was für alle Halter gilt (Bewegen, keine neue Spur, gehalten nicht wiedergefunden,
  LD2410C), ändert die Pool-Zeile **einmal**; die Faktoren gehen über die Halterliste an die Hypothesen.
- **Halterliste** Objekt → Hypothesen als CSR, neu gebaut nach jedem Ereignis, das Hypothesen ändert
  (Verzweigen, Zusammenlegen, Abschneiden, Zurückgeben), sonst wiederverwendet.
- **Freigabe beim Neubau der Halterliste:** Ein Objekt ohne Halter (und der Teil hinter der Tür einer freien
  Mischung) geht zurück in den Pool. Kein Pfad kann ein `retain`/`release` vergessen.
- **Kopiert wird nur, wo das Modell verzweigt:** `pool.fork(id) -> id`. Stellen: Verzweigen (ein Kind bekommt
  die Spur, die Geschwister nicht), Abgeben einer Spur (`_release`), Zusammenlegen (die Mischung ist ein neues
  Objekt), Zurückgeben (die Unbekannten aller Hypothesen werden eine neue Intensität), `people_state` (rechnet
  auf Kopien). Jede Stelle trägt den Grund als Kommentar; `Clone` gibt es für die Objekte nur für `fork`.

### 2.3 Hypothesen und Gauß-Mischung

```rust
struct Hyp {
    logw: f64,
    tracks: SmallMap<SegId, Owner>,       // heute kind: Spur -> Person (GroupId) oder Geist
    ghosts: SmallMap<SegId, Ghost>,       // heute ghost + phantom: Lebensdauer je Art, Quelle (MixtureId)
    people: SmallMap<GroupId, MixtureId>, // heute groups
    hidden: Vec<DensityId>,               // bekannte ohne Spur (Bernoullis)
    unknown: DensityId,                   // ppp
}

struct Mixture {
    logw: [f64; 2],                 // STILL, WALK
    modes: [Kalman; 2],             // je Betriebsart, je Achse Mittel (d) und Kovarianz (d×d)
    stay: Vec<f64>,                 // gow (L)
    ka: Vec<f64>,                   // K×A gemeinsam
    slots: SmallMap<SegId, Slot>,   // Index des Versatzes c (o folgt), Varianz
    phantom: bool,
    away: Option<Away>,             // Gewicht a und Zeile in Densities
    t: Option<f64>,
}
```

`SmallMap` ist ein Vektor von Paaren in **Einfügereihenfolge**, wie Pythons `dict`: Die Reihenfolge, in der Personen
summiert und nacheinander verrechnet werden (`_ld_weigh`), bleibt die von heute. **Keine `HashMap` in rechnenden
Pfaden** (Rusts Iterationsreihenfolge ist je Prozess zufällig); Schlüssel der Hypothesen als sortierte Vektoren.
Lineare Algebra der Mischungen von Hand (je Achse, die Messung ist je Achse skalar: Rang-1-Updates), in der
Reihenfolge von `kernels.py`.

## 3. Projekt und Module

Cargo-Workspace `tracker/core/`:

| Crate | Art | Inhalt |
|---|---|---|
| `presence-core` | Bibliothek, **ohne PyO3** | der ganze Filter; mit `cargo test` ohne Python prüfbar |
| `presence-py` | `cdylib`, Python-Modul `presence_core` | nur die Bindung (PyO3; numpy nur für die Test-Hooks) |
| `presence-replay` | Programm | Wiedergabe ohne Python (6) |

Module von `presence-core`, nach MODEL.md:

| Modul | MODEL.md | übernimmt |
|---|---|---|
| `config/` (`mod.rs`, `check.rs`) | – | serde-Typen der Konfiguration (das JSON von `Config.to_dict()`), ihre Prüfung mit der einen Grenztabelle (auch der Eingabefelder der Oberfläche), die Sensorgeometrie (`SensorConfig`, `Mount`) |
| `plan.rs`, `geometry.rs` | 2 | Orte, beobachtete Räume, Bereiche, Wandstücke, Sichtstücke, Formen (Rechteck/Kreis/Polygon), Schnitt von Strecken, Spiegeln |
| `world/raster.rs`, `world/sight.rs` | 2, 4.1, 4.3 | Raster, Sicht je Sensor (`SensorModel.pd`), `_spread_sight`, LD2410C-Sicht und Blick in andere Räume (`ld_view`) |
| `world/tiles.rs`, `linalg.rs` | 5.3 | Kacheln, Gehmatrix (`expm`), `walk_end`, `observed`, `gauss_points`, `near_doors` |
| `params.rs` | 3, 4 | `filtermodel.Model`/`Shapes`, Aufenthalt in Bereichen (`Dwell`) |
| `frames.rs` | 4.1, 4.4 | Nutzlast prüfen, Bildzeit (`SensorClock`), Detektionen |
| `ld2450/tracks.rs` | 4.1 | Spuren des Sensors (`SensorTracks`) |
| `ld2450/rates.rs` | 4.1, 4.2 | Erfassungsraten (Nicht-Wiederfinden, Geisterleben und -ende: `filter/`) |
| `ld2410/{likelihood,echoes,background}.rs` | 4.3 | `Stats`, Profile, Likelihood (auch zensiert), Echoquellen, Hintergrund/Anker/Echorate lernen |
| `vmath.rs` | 4.3 | `vlog`, `vexp` (fdlibm, vektorisierbar), `lgamma`, `erfc`, `erfinv` |
| `motion/{stay,walk,imm}.rs` | 3, 5.2 | Aufenthalte, Gehen je Tick, Regionen, Ankünfte, `leap`; Vorhersage der Mischung mit Wänden und Türen (`sync_step`) |
| `filter/{pool,densities,mixture,hyp}.rs` | 5.2–5.6 | Pools, `Hidden`/`Undetected`, `Gauss`, Hypothesen und ihr Schlüssel |
| `filter/{step,evidence,branch,merge,ld_weigh}.rs` | 4, 5, 7 | Takt und Flush, Evidenz je Frame, Verzweigen, Zusammenlegen/Abschneiden, LD2410C-Verrechnung |
| `learn/{ghost_map,destination}.rs` | 4.2, 6 | Geisterkarte, Zielkarte samt Geometrie und Lernen der Gänge |
| `outputs.rs`, `room_seen.rs` | 6 | Zähl-/Ortsverteilungen, Belegt (Filter oder LD2450 des Raums, stumm), *wird betreten*, *Ziel*, Anzeige |
| `formats.rs` | 4.2, 4.3, 5.3, 6 | die gespeicherten Formate (base64/zlib, float16/float32), byte-kompatibel lesbar |
| `tracker.rs` | – | `Tracker`: die öffentliche Rust-API, die Python-Bindung und Wiedergabe gemeinsam nutzen |

Kommentare auf Englisch im Stil des Python-Codes (was, MODEL.md-Abschnitt, Literatur), Bezeichner aus MODEL.md.

**Abhängigkeiten:** `serde`, `serde_json`, `faer` (Matrixprodukt, LU für `walk_end`; gegen `matrixmultiply` und eine
dünne Gehmatrix mit den Dichten als innerer Dimension messen), `libm`, `half` (float16), `base64`, `flate2` mit dem
reinen Rust-Backend (zlib), `sha1_smol` (Fingerabdrücke); Bindung: `pyo3` (abi3, Python ≥ 3.13), `numpy` nur mit dem
Feature `testing`. Keine System-BLAS, keine Systempakete.
**`faer` ausdrücklich auf einem Thread:** ohne das Feature `rayon` (`default-features = false`), und jeder Aufruf mit
`Par::Seq` (nicht über eine globale Einstellung). Der Kern startet keine Threads (OpenBLAS kostete mit Threads ein
Vielfaches an CPU, MODEL.md 10).

**Zahlen:** IEEE, kein fast-math, ein Thread, deterministisch (gleiche Daten → gleiche Bits). `f64::exp/ln` sind
libm; wo sie die Zeit bestimmen (LD2410C), `vmath::vlog/vexp` (Test: ≤ 1 ulp gegen libm, Sonderfälle exakt).
Bitgleichheit mit Python ist nicht das Ziel (AUFTRAG 1.2); die erste Abweichung in den Spuren muss eine Rundung sein.
Weil jetzt auch der Bau in Rust rechnet (`expm`, Sicht), weicht schon die Gehmatrix in den letzten Bits ab; damit
sich Bau und Filter getrennt prüfen lassen, gibt es eine Prüfhilfe (6).

## 4. Python-Grenze

PyO3 + maturin, Modul `presence_core`.

**Der Tracker wird nur aus dem asyncio-Loop benutzt** (Leon, 27.10.; heute ebenso: `app.py` gibt Threads nur
Kopien – Dateien schreiben, Kalibrierstatus, Fehlerbericht). Der Rust-`Tracker` ist `Send` (keine `Rc`/`RefCell`),
PyO3 prüft den exklusiven Zugriff. Während der Kern rechnet, gibt er den GIL frei (`allow_threads`), damit die
Threads der App nicht bis zu 5 ms warten (MODEL.md 10). Was die App im Thread komprimiert und schreibt (heute
`dest_map.snapshot()`: im Loop kopiert, im Thread komprimiert), bleibt so geteilt: `learned_snapshot()` kopiert im
Loop, das Formatieren (zlib/base64) läuft im Thread ohne den Tracker.

**`presence_core.Core`** (Rust, so umgesetzt; die Skizze des Entwurfs hieß `Tracker`): wandelt nur um, die Schritte
sind die des Kerns (`presence_core::tracker::Tracker`).

```
World(config_json, model_json)              einmal gebaut (ohne GIL); model: die Felder, die vom Standard abweichen
Core(world)                                  die Welt geht in den Kern
frame(sid, t, Frame) / step(t) -> events     ("watch" | "track_end" | "branch", ...), ValueError für t ohne Zahl
recalibrate(changed, world)                  WorldChanged, wenn die Personen nicht in die neue Welt passen: die
                                             Welt bleibt dann ganz, die Hülle baut den neuen Kern damit
set_outputs(config)                          nur die Ausgabeparameter (params::OUTPUT_FIELDS): nichts gebaut
adopt(old)                                   was nach einem Neubau weitergeht
reset_people(places) / reset_tracks()        people_state() -> JSON / restore_people(JSON) -> None | Grund
set_pauses(intervals) / hold_background(s)   learned() -> Learned (json() ohne den Tracker, im Thread)
use_ghost_map / use_ld_background / use_dest_map(JSON)
zone_states(now), persons(), clouds(), online(id, now), silent(now), seen_last(), watched(), observed_total(),
regions(), dwell, ld_shown(si)
Sichten: plan(), tiles_view(), sensor_map(si), detections(si), last_frame(si), coverage()
Modul: plan(config), check_config(config), canonical(config), config_defaults(), config_limits(), derive(config),
geometry_key(config), forget_measured(config) (0.51.0),
calibrate, calibration_status, Walking, Frame, SensorClock, WorldChanged, ConfigError (ValueError; args[0] ein
ConfigProblem: Teil, Name, Feld, Grund, Grenzen, Wert), PanicException, SOURCE_HASH (Fingerabdruck der Quellen
des Kerns, beim Bauen eingebettet: build.rs, 0.50.0)
nur mit dem Feature "testing" (Tests und Werkzeuge, nie im Image; 0.50.0 alles, was nur sie lesen):
build_export, hidden_at, panic_for_test, Geometry(config), model_defaults(), ld_prior(), ld_expected(...);
Core.normalize/spoil_weights_for_test/ld_last, loglik, hyp_weights(), check(), hypotheses(), tracks() (Spur ->
Sensor-ID), room_at(pts), tile_near(p), sight(id, pts), observed(), ghost_rates(id, pts), background(id),
count_distribution() / place_distribution() (nach Raum- bzw. Ortsnamen, "_observed"/"_house"), occupancy()
(Raum -> Quelle oder None)
```

Die Hülle (`filter.py`) führt jeden Zugriff auf den Kern über einen Schutz (`_Guarded`): eine Panik des Kerns wird
`CoreError`, auch in einem Getter. Die Hooks der Tests und das, was nur Tests und Werkzeuge lesen (Liste oben), gibt
es nur mit dem Feature `testing` (eines für beide); Tests und Werkzeuge rufen es über `Tracker.core` auf.

**Entschieden in Phase 7 (Prüfung 28.10.): kein `process_payload`, sondern ein geprüfter `presence_core.Frame`.**
Die App braucht den Frame auch für die Uhr und die Kalibrierung; eine Nutzlast wird darum genau einmal geprüft:
`Frame.parse(payload)` (MQTT) bzw. `Frame.of(dict)` (Aufnahmen, Tests, Simulation) gegen das, was die Firmware sendet
(`esphome/packages/radar-combo.yaml`: `uptime_ms` als `millis()` in 0..=u32::MAX, Slots 1..=3, x/y/speed ganze mm
innerhalb ±32767, Gates beide oder keine, je 9 Energien 0..=100), sonst ValueError mit dem Grund. `SensorClock`
(eine je Board, auch ohne Konfiguration, in der App), `Tracker.process_frame`, `Core.frame` und `TrackStage.update`
nehmen nur diesen Typ; ein ungültiger Frame erreicht den Tracker nicht. Die Anzeige liest `Frame.ld2410` (Flags,
Abstände, Gates), die Uhr `Frame.uptime_ms`.

**Entschieden in Phase 8 (28.10.):** Die Zeit des letzten Frames steht nur in der Spurstufe des Sensors
(`Ld2450Tracks`; `SensorState::last_frame` liest sie, die Kalibrierung hat ihre eigene); `Tracker::frame(id, t, &Frame)`
und `Tracker::step(t)` tun alles, was `process_frame`/`step` taten, und geben die Ereignisse für die Listener zurück
(`Watch`, `TrackEnd`, `Branch`); die Hülle ruft die Listener damit (der Listener „frame“ hört nach den Schritten). Das
Gelernte gibt `learned()` als Kopie (`presence_core.Learned`, `json()` packt ohne den Tracker, im Thread der App);
`use_ghost_map/use_ld_background/use_dest_map` nehmen das gespeicherte JSON. Die Python-Lerner (`ghostmap.py`,
`destination.py`, `ld2410.Background`) bleiben bis Phase 9 als Orakel der Formate in den Tests.

**Umgesetzt in Phase 9 (28.10.):** Der Python-Kern ist gelöscht (Liste im CHANGELOG 0.39.0). Das Modell sind die
Standards des Kerns (`Params::default`, `presence_core.model_defaults()`); `Tracker(config, model=dict)` nimmt nur, was
abweicht. Das Format von `people_state` liest und schreibt der Kern (`filter/saved.rs`). Statt des Spiegels gibt es
lesende Sichten (`hypotheses`, `tracks`, `tiles_view`, `sensor_map`, `sight`, `observed`, `tile_near`). Die Bindung
nimmt keine Welt aus Python mehr an (der Parameter `export` ist weg); `build_export` bleibt als Prüfhilfe, der Kern
behält `WorldExport` für seine eigenen Tests. Was die Python-Orakel sagten, steht in `tracker/tests/fixtures/`
(einmal mit 0.38.1 erzeugt).

**Aufräumrunde (0.40.0, 28.10.):** Das Abgeleitete aus `model.py` ist weg: Orte, Regionen, beobachtete und geschlossene
Räume liest jeder aus dem Plan des Kerns (`presence_core.plan(config)`, `Tracker.plan`). Die Sichten der Bindung sind
benannte Typen (`HypView`, `TilesView`, `PersonView`). Der gemessene Blick des LD2410C (`ld_view`) ist eine Angabe der
Konfiguration, kein Standard des Modells mehr.

**Keine doppelte Logik (0.43.0, 29.10.):** Die Sensorgeometrie (Sensorrahmen, Blickfeld, Sicht durch Türen, Bodenpunkt)
gibt es nur im Kern; die Simulation der Tests fragt `presence_core.Geometry` (über Punktlisten; seit 0.49.0 nur mit
dem Feature „testing“, die Kalibrierung ist seit 0.45.0 ganz im Kern). Die Konfiguration
prüft nur der Kern (`config/check.rs`, eine Tabelle der Grenzen, auch der Bildebenen der Oberfläche). Die Zonenzustände
liefert der Kern fertig (`Core.zone_states`: geordnet, mit Quelle, „Ziel“ und Schwellen, das Haus als `_total`); Hülle
und `presence-replay` schreiben sie nur. `recalibrate` nimmt eine gebaute `World`; passt sie nicht, baut die Hülle den
neuen Kern mit ihr (eine Welt je Neubau). Was an Logik in Python bleibt, steht in der Übergabe von Sitzung 11
(`rust-port/PLAN.md`).

**`presence_tracker.filter.Tracker`** bleibt die Klasse, die App, Werkzeuge und Tests benutzen, mit derselben
öffentlichen API: Sie reicht durch, kopiert die `ZoneState`s des Kerns, ruft die `listeners` mit den Ereignissen des Kerns und hält
die Python-Objekte der Hülle (`config`, `pauses`). Die Konfiguration geht als `config.to_dict()` hinein; das
Modell als Dict der Felder von `Params` (`presence_core.model_defaults()`), die vom Standard abweichen; unbekannte
Felder lehnt der Kern ab, ebenso Zahlen, die kein Modell ergeben (`ModelError` mit den Namen). Auch die Zeit, nach
der ein stummer Sensor als weg gilt (`silent_after`), ist ein Feld des Modells.

**Was sich für die Benutzer ändert** (Inventur aller Zugriffe von außen, 27.10.):

| Zugriff | wer | danach |
|---|---|---|
| `tracker.ghost_map`, `ld_background`, `dest_map` als Python-Objekte (`to_dict`, `load`, `use`, `snapshot`, `add_birth`, `count`, `num`/`den` …); `use_ghost_map/use_dest_map/use_ld_background(obj)` | App (Dateien `ghostmap.json`, `ld2410.json`, `destinations.json`), `report_eval`, `baseline_eval`, `entries` (reichen Objekte von Tracker zu Tracker) | über `learned()`/`load_learned()` als Dicts in den heutigen Formaten; die App schreibt die drei Abschnitte in dieselben Dateien |
| `tracker.tracks[sid] = SensorTracks(…)`, `runtime[*].last_frame = -inf`, `ld_background.pause(…)` | App `h_reset_tracks` | `reset_tracks()`, `ld_pause(s)` |
| `runtime[sid].detections` (`hidden`, `stale`, `pos`), `sensor_model.maps(…)`, `tiles.squares/square/room`, `seen.watched()` | App (Oberfläche), `report_eval` | `runtime()`, `sensor_maps(sid)`, `plan()`, `outputs()` |
| `persons()[i]["obj"]` (lebendes Objekt) | `replay.py`, `report_eval.py` | Mittel/Kovarianz bzw. Dichte als Arrays |
| `tracker.hyps` (Anzahl, Gruppen, Gauß-Felder) | `report_eval`, `replay` | `hypotheses()` (lesend) |
| `config.observed_rooms()`, `closed_rooms`, `zones_of`, Orte | App, HA, Kalibrierung | Abgeleitetes nur aus dem Kern (`plan()`); `model.py` behält Daten, Prüfung, Speichern |
| `SensorTracks`, `floorplan.wall_pieces`, `SensorConfig.to_world` | Kalibrierung | `TrackStage` des Kerns; Wandstücke, Sicht und Bodenpunkt aus `presence_core.Geometry` (0.43.0) |
| `GhostMap` offline bauen | `tools/ghostmap.py` | auf 0.31.1 belassen oder später als Modus der Wiedergabe |
| Pickle des ganzen Trackers | `seg_replay --init pickle` | entfällt (entschieden) |
| `_ld_weigh` gepatcht, `ld2410.Likelihood/Stats` direkt | `tools/ld2410/*` | auf 0.31.1 belassen (entschieden) |

**Tests** (240): Verhalten über die API bleibt grün (`test_entering`, `test_together`, `test_time_slicing`,
`test_sight`, `test_tools`, `test_config_check` und die Verhaltensteile der gemischten Dateien; Patches von
`zone_states`, `process_frame`, `use_dest_map` treffen die Python-Hülle). Was Innereien liest oder schreibt
(`test_kernels`, `test_gauss`, `test_hypotheses`, `test_sensortracks`, `test_unobserved`, die Kernteile von
`test_ld2410`, `test_frames`, `test_learning`, `test_roomseen`, `test_filter`, `test_existence`, `test_restart`,
`test_robust`), wird ein Rust-Test mit denselben Fällen. Die Liste „was wohin“ kommt in die Übergabe.

Fehler: Nicht endliche Gewichte werden in Python ein `FloatingPointError` wie heute (die App beginnt neu).

**`reconfigure` behält seine drei Wege** (`filter.reconfigure`, `_changed_sensors`):
1. nur Ausgabeparameter (`params::OUTPUT_FIELDS` des Kerns, der Oberfläche als `derived.output_params`: `light_cost`, `seen_hold`, `lead_time`, `approach_cost`,
   `target_threshold`, `target_thresholds`; dazu `ld2410_hold`, das nur die Anzeige der Hülle liest) geändert:
   **kein Neubau** (`Core.set_outputs`), nichts beginnt neu, nur die Parameter und der Ausgabe-Cache;
2. nur Sensoren oder Namen geändert (gedreht, verschoben, kalibriert, ein/aus; die Namen liest der Plan, z. B. den
   eigenen Raum eines Sensors): Personen bleiben, deren Spuren enden wie bei Datenverlust, die Welt neu gebaut
   (`Core.recalibrate`), das Gelernte nur unter derselben Geometrie (unten); passen die Personen nicht in die neue
   Welt, sagt der Kern `WorldChanged` (nur das fängt die Hülle; jeder andere Fehler bleibt ein Fehler), und Weg 3
   nimmt die schon gebaute Welt;
3. sonst: Neubau, Personen aus `people_state`, wenn die Kacheln gleich bleiben.

Je Weg ein Test; für 1 zusätzlich: Hypothesen, Gewichte und Spuren sind danach bitgleich wie davor, und der Lauf geht
bitgleich weiter wie ohne die Änderung (bis auf die geänderten Ausgaben).

**Gelerntes und Geometrie (0.51.0, Leon 29.10., ohne Ausnahme):** Was gesammelt und gelernt ist, gilt nur unter der
Geometrie, unter der es gesammelt wurde. Eine Quelle: `Config::geometry_key` (Bindung `presence_core.geometry_key`,
sha1 16 Hex) über Lage, Blickrichtung, Höhe, Spiegelung, Maßstab, Öffnungswinkel, Reichweite, `placed` und ID jedes
Sensors, Wände (Strecke, Art), Türen (ID, Mitte, Breite) und Zonen (ID, Art, Form), Zahlen nach Wert; nicht: Namen,
`enabled`, Parameter, `ld_view`. Öffnungswinkel und Reichweite gehören dazu, weil aus ihnen die Sicht je Kachel, die
beobachteten Räume und das „außerhalb der Sicht“ der Kalibrierung folgen. Die `World` trägt den Schlüssel
(`World::geometry`), `Learned` den, unter dem es lernt: `Learned::follow` beginnt bei einem anderen ganz neu (alle
Sensoren, alle drei Teile), sonst bleibt es; `Pose::remounted` (0,5 m / 30°) und das Folgen je Sensor sind weg.
`reconfigure::change` bleibt nur der Weg des Modells (er definiert keine Geometrie). Die Hülle führt aus: Jede Datei
des Gesammelten trägt den Schlüssel (`ghostmap.json`, `ld2410.json`, `destinations.json` als
`{"geometry", "learned"}`, `calibration.npz` mit `geometry`); passt er beim Start nicht, wird sie gelöscht
(`App._discard`). PUT mit einem anderen Schlüssel: `presence_core.forget_measured` leert `ld_view` (gemessen unter der
alten Geometrie; gibt die Zahl der Raumpaare zurück, Python liest das Feld nicht) vor dem Rekonfigurieren, danach `App._forget` (Kalibrierdaten zurückgesetzt, die vier Dateien
gelöscht) und `"discarded": {"calibration": Messungen, "ld_view": Raumpaare}` in der Antwort; die Oberfläche sagt es
(`util.discardedText`). Die Sonderfälle bleiben, was sie prüfen: die Geisterkarte ihren Prior (Modellparameter, keine
Geometrie), die Zielkarte ihr Raster (`fingerprint`, hängt auch am Code); „andere Räume oder Türen“ fängt jetzt der
Schlüssel vorher ab.

## 5. Build

- **Lokal (hermann):** Rust stable 1.99.1 ist da. `maturin` als Entwicklungsabhängigkeit über `uv`;
  `maturin develop --release` baut `presence_core` in `tracker/.venv`. `cargo run --release -p presence-replay`
  für die Wiedergabe. Keine Systempakete.
- **Prüfung lokal auf hermann, vor jedem Commit (nicht in CI):** `cargo fmt --check`, `cargo clippy --all-targets --
  -D warnings` ohne und mit allen Features, `cargo test --all-features`, `pytest` mit dem Kern mit Hooks und gebautem
  `presence-replay`; vor einem Release zusätzlich das Release-Wheel (`maturin build --release`, installiert) mit
  `PT_RELEASE_WHEEL=1 pytest -m "not hooks"` und der Spurvergleich im amd64-Image.
- **Image (Variante B), nur bei einem Tag `v<version>` (`.github/workflows/release.yml`, ein Job auf `ubuntu-24.04`,
  sonst kein Workflow):** prüft, dass der Tag `version` in `tracker/config.yaml` ist; baut je Architektur genau ein
  Wheel (`maturin build --release`, `cp313-abi3`): amd64 nativ mit `RUSTFLAGS=-C target-cpu=x86-64-v3` (ausdrücklich,
  `tracker/.cargo/config.toml` greift nur für ein `cargo` unter `tracker/`), aarch64 generisch, quer mit `--zig`
  (`ziglang` aus PyPI über `uv`), Cargo-Cache `Swatinem/rust-cache`. Dann `docker build --platform` je Architektur
  mit `tracker/Dockerfile`: eine erste Stufe auf der Bau-Maschine stellt mit `pip install --target --platform
  manylinux_2_17…2_41_<arch> --only-binary=:all:` das Wheel aus `dist/<arch>` und numpy, aiomqtt, aiohttp für die
  Zielarchitektur zusammen, die zweite (`python:3.13-slim`) kopiert sie nur – keine Emulation, kein QEMU. Labels
  `io.hass.type/arch/version`, `org.opencontainers.image.source`. Rauchtest nur im amd64-Image (`import
  presence_core, presence_tracker.app`), dann `docker push` nach `ghcr.io/leonrein/{arch}-presence-tracker:<version>`.
  `config.yaml` hat `image:`; HA setzt `{arch}` ein und zieht den Tag `version`, gebaut wird auf inge nichts mehr.
  Geschätzt 5–8 min je Release (erster Lauf ohne Cache eher 8). Kein `home-assistant/builder`: er baut in Docker mit
  QEMU für fremde Architekturen und erwartet seine Basis-Images; hier reichen `docker build` und die Wheels.
- **Release:** (1) `version` in `config.yaml` und `presence_tracker/__init__.py`, CHANGELOG, Commit auf dem Branch,
  lokal alles grün (oben). (2) Tag `v<version>` auf genau diesen Commit pushen → CI baut und pusht beide Images. (3) Beim ersten Mal:
  die Pakete `amd64-presence-tracker` und `aarch64-presence-tracker` unter github.com/LeonRein → Packages →
  Package settings auf **Public** stellen (HA zieht ohne Anmeldung). (4) Prüfen, dass beide Tags ohne Anmeldung
  ziehbar sind (`podman pull ghcr.io/leonrein/amd64-presence-tracker:<version>`). (5) Erst dann main auf den Commit
  vorspulen: HA zeigt das Update, sobald `version` auf main steigt, und das Image muss dann schon da sein.
- **Gleitkomma und C-Bibliothek:** `f64::hypot` ist die `hypot` der C-Bibliothek. Die von hermann (CachyOS glibc
  2.44) weicht im letzten Bit von Debians (Image, 2.41) ab; die Spuren im Image sind deshalb nicht
  byte-gleich mit denen auf hermann (`exp`, `log`, `pow`, `atan2`, `expm1`, `log1p`, `sin`, `cos` sind gleich). Die
  Digests von `test_core_build` gelten nur unter hermanns `hypot` (sonst übersprungen).

## 6. Wiedergabe in Rust und Prüfplan

**`presence-replay`** (ohne Python): liest Konfiguration (JSON), Aufnahme (`.jsonl`, Bildzeit über dieselbe Uhr wie
die App), optional Gelerntes und `people_state` als Start, und treibt den Tracker wie die App (`prof.py`: Frames,
`step` und Ausgaben alle 0,1 s). Modi:
- `trace`: schreibt je Sekunde genau das Format von `rust-port/trace.py`, damit `cmptrace.py` unverändert vergleicht;
  dieselbe Spur über die Python-Hülle muss **bitgleich** sein (Test der Bindung);
- `prof`: CPU-Zeit wie `prof.py` (ohne Python-Anteil: zeigt, was die Hülle kostet);
- später `segments`: was `seg_replay.py` für `fast_eval` tut (Abschnitte aus den `learned.json.gz`-Prüfpunkten, dieselben
  Ausgaben), parallel über Prozesse statt Threads im Kern. Bis dahin läuft `fast_eval` über die Python-Hülle.

**Umgesetzt in Phase 10 (0.41.0):** `trace` und `prof` wie oben; CONFIG ist das JSON von `Config.to_dict()`, das
Modell die Standards des Kerns. Die Spur schreibt ein eigener kleiner Schreiber im Format von Pythons `json.dumps`
(`presence-replay/src/pyjson.rs`), weil serde_json Zahlen anders schreibt; byte-gleich mit `trace.py` über die Hülle
(drei Referenzstunden, `tests/test_replay.py`). Nicht dabei: Start aus Gelerntem/`people_state`, `--model`, `segments`.

**Prüfplan:**
- **Rust-Tests** je Modul mit den Fällen der heutigen Python-Tests; `vlog/vexp` ≤ 1 ulp; Pool-Invarianten
  (`check`); die Formate: Dateien, die 0.31.1 geschrieben hat, lesen und gleich wieder schreiben (Inhalt gleich,
  zlib-Bytes dürfen sich unterscheiden), und Python von 0.31.1 liest, was Rust schreibt.
- **Konfigurationen:** Ein Test schickt jede vorhandene Konfiguration durch `model.py` (`Config.from_dict`, das
  Abgeleitete von 0.31.1) und durch die serde-Typen des Kerns und vergleicht das Abgeleitete: Orte und ihre Raster,
  beobachtete Räume, offene/geschlossene Bereiche, Ausgänge, Wand- und Sichtstücke, Sensor je Raum, die Regel
  „hinter der Wand/außerhalb/am Sensor“ an einem Punktgitter. Im Repo mit den Konfigurationen der Tests und
  `devdata`; die privaten (`~/.config/presence-tracker/dev/config*.json`, `live_config.json`, die der Auswertung)
  über eine Umgebungsvariable mit dem Verzeichnis, lokal vor jedem Commit von Phase 6.
- **Bau gegen Python:** Ein Skript exportiert einmal, was 0.31.1 aus `live_config.json` baut (Orte, Kacheln,
  Gehmatrix, Sicht je Sensor, LD2410C-Kacheln, Formen); der Rust-Bau muss es bis auf Rundung treffen (Toleranz je
  Größe). Zum Eingrenzen kann der Tracker die exportierte Welt statt der eigenen nehmen: Dann trennt der
  Spurvergleich Bau- und Filterabweichungen. **Das ist dieselbe Schnittstelle wie die Übergabe der Welt in Phase
  3–5**, solange der Bau noch in Python ist (ein `WorldExport`: Plan, Raster, Kacheln, Gehmatrix, Sicht, Formen als
  Arrays); sie bleibt danach als Prüfhilfe.
- **Spurvergleich** gegen 0.31.1 (`ref_0311/`, `cmptrace.py`): je Teil die erste Stunde von `20261010-12` bis zur ersten
  Abweichung > 1e-6 eng verfolgen (sie muss als Rundung beginnen), über die Stunde |ΔP(belegt)| ≤ 0,01 und keine
  gekippte Schwelle; vor jedem Commit alle drei Spuren.
- **Python-Tests** (`uv run pytest`) grün; Ausgaben an HA über `test_ha_steady`/App-Tests.
- **CPU:** `ab.sh 1200` (zweites Paar zählt) je Teil; am Ende ein Lauf auf inge mit dem CI-Image.
- **Wahrheit:** am Ende `fast_eval.py --mode full --stages 2` gegen `runs/perf0311_s2` (Rauschgrenze 6,3 min in ~90 h).
- Jede Phase: `cargo fmt`, `clippy`, Version + CHANGELOG, Branch pushen (main nicht).

## 7. Phasen und Aufwand

Reihenfolge: zuerst messen, ob es sich lohnt (Prototyp), dann der Filter von innen nach außen, dann die Ränder
(Bau, Frames, Gelerntes), weil sie bis dahin die heutigen Python-Teile als Vorlage und Orakel bleiben.

| Phase | Inhalt | Rust (Zeilen) | Aufwand |
|---|---|---|---|
| 1 | Entwurf | – | erledigt |
| 2 | **Prototyp:** Workspace; Bewegen und Gewichten aller Kacheldichten und Gauß-Mischungen (`_flush_parts` ohne LD2410C) als endgültige Rust-Funktionen, im Prototyp mit Kopieren an der Grenze aus dem Python-Filter gerufen; `faer`/`matrixmultiply`/dünn messen; `ab.sh`, Spurvergleich; **Ergebnis an Leon** | 1200 | 1,5 Tage |
| 3 | Filter-Zustand in Rust: Pools, Hypothesen, Takt, Ablauf, Zurückgeben, Lücken; Evidenz je Frame, Verzweigen, Zusammenlegen, Abschneiden (Welt und Spurereignisse noch aus Python) | +2300 | 3 Tage |
| 4 | LD2410C: Sammeln, Likelihood, Echoquellen, Verrechnung | +1000 | 1,5 Tage |
| 5 | Ausgaben, RoomSeen-Regel, *wird betreten*, Anzeige | +700 | 1 Tag |
| 6 | Konfiguration (serde), Plan, Welt- und Kachelbau samt `expm`/`walk_end`, Sicht; Prüfhilfe Bau gegen Python | +1600 | 2 Tage |
| 7 | Frames, Uhr, LD2450-Spuren; Funktionen für die Kalibrierung | +500 | 1 Tag |
| 8 | Gelerntes: Geisterkarte, Hintergrund/Anker/Echorate, Zielkarte; Formate (`people_state`, `learned`); Pausen | +1300 | 2 Tage |
| 9 | Python-Hülle fertig, App/Werkzeuge/Tests umstellen, alten Python-Code und numba löschen | (Python) | 1,5 Tage |
| 10 | `presence-replay` (`trace`, `prof`) | +500 | 0,5 Tag |
| 11 | CI, Image, Test-Tag, Image auf inge ziehen (Leon fragen) | Workflow | 0,5 Tag |
| 12 | Abnahme: `fast_eval` full (läuft Stunden), Lauf auf inge, MODEL.md 10, CHANGELOG, DOCS.md; Leon um OK | – | 1 Tag |
| später | `presence-replay segments` für `fast_eval` | +400 | 1 Tag |

Zusammen etwa **9500 Zeilen Rust** und **15–16 Arbeitstage** (geschätzt) bis zur Abnahme. Gegenüber der ersten
Fassung (6000 Zeilen, 8–9 Tage) kommen Bau, Frames, Spuren, Gelerntes, Formate und die Wiedergabe dazu.
Ein Release ist erst nach Phase 9 sinnvoll (vorher läuft numba noch mit); Zwischenstände 2–8 sind Commits auf
`rust-core` mit Spurvergleich und `ab.sh`, jeweils mit Version und CHANGELOG.

Zum Prototyp: `_flush_parts` ist heute ~20 % der Zeit; in Rust ohne Aufwand je Aufruf etwa ein Drittel davon, also
gesamt −10 bis −15 % schon im Prototyp (geschätzt; das Kopieren misst den Gewinn eher zu klein). **Die Klebeschicht
des Prototyps ist Wegwerfcode** (sie fällt in Phase 3 mit den Pools weg); endgültig sind nur die Rust-Funktionen.
**Bringt er weniger als −8 %**, wird erst mit py-spy (`-n`, native Rahmen) geklärt, wohin die Zeit geht, und an Leon
berichtet, bevor Phase 3 beginnt.

## 8. Entschieden (Leon, 27.10., zweite Runde)

1. Reihenfolge wie in 7: Filter von innen nach außen; die Python→Rust-Übergabe der Welt in Phase 3–5 ist dieselbe
   Schnittstelle wie die Prüfhilfe (6).
2. `tools/ghostmap.py` bleibt auf 0.31.1.
3. Die Kalibrierung bleibt vorerst Python mit numpy und ruft den Kern für die LD2450-Spuren; ihre Geometrie bleibt
   Python (Leon, 29.10.; überholt mit 0.43.0: die Geometrie ist die des Kerns, der Schätzer bleibt Python).
4. Lernreihenfolge wie heute, mit getrennt geliehenen Lernern (2.0); `faer` auf einem Thread (3); der schnelle Weg
   von `reconfigure` bleibt (4); Test aller Konfigurationen (6); Schwelle −8 % für den Prototyp (7); nur aus dem
   asyncio-Loop (4).
