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

| Python (Hülle) | |
|---|---|
| MQTT (`sources.py`), Home Assistant (`ha.py`, `ZoneState`-Rundung und Ausgabe-Halten), Web-Oberfläche (`app.py`, `static/`) | unverändert |
| Konfiguration bearbeiten, prüfen (`check_config`, Grenzen) und speichern (`model.py` ohne das Abgeleitete, `floorplan.py` ohne die Geometrie des Filters) | gekürzt |
| Kalibrierung (`calibration.py`, vorerst Python mit numpy) über Funktionen des Kerns: LD2450-Spuren, Wandstücke, Sensor ↔ Welt | angepasst |
| Simulation (`sim.py`), Pausen schalten und speichern (`pause.py`), Dateien und Fehlerberichte schreiben | unverändert |
| `filter.Tracker` als dünne Hülle mit der heutigen API (4) | neu, klein |

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
| `presence-py` | `cdylib`, Python-Modul `presence_core` | nur die Bindung (PyO3, numpy, `pythonize`) |
| `presence-replay` | Programm | Wiedergabe ohne Python (6) |

Module von `presence-core`, nach MODEL.md:

| Modul | MODEL.md | übernimmt |
|---|---|---|
| `config.rs` | – | serde-Typen der Konfiguration (das JSON von `Config.to_dict()`), Modellparameter mit Vorgaben und Überschreibungen |
| `plan.rs`, `geometry.rs` | 2 | Orte, beobachtete Räume, Bereiche, Wandstücke, Sichtstücke, Formen (Rechteck/Kreis/Polygon), Schnitt von Strecken, Spiegeln |
| `world/raster.rs`, `world/sight.rs` | 2, 4.1, 4.3 | Raster, Sicht je Sensor (`SensorModel.pd`), `_spread_sight`, LD2410C-Sicht und Blick in andere Räume (`ld_view`) |
| `world/tiles.rs`, `linalg.rs` | 5.3 | Kacheln, Gehmatrix (`expm`), `walk_end`, `observed`, `gauss_points`, `near_doors` |
| `params.rs` | 3, 4 | `filtermodel.Model`/`Shapes`, Aufenthalt in Bereichen (`Dwell`) |
| `frames.rs` | 4.1, 4.4 | Nutzlast prüfen, Bildzeit (`SensorClock`), Detektionen |
| `ld2450/tracks.rs` | 4.1 | Spuren des Sensors (`SensorTracks`) |
| `ld2450/rates.rs`, `ld2450/ghosts.rs` | 4.1, 4.2 | Erfassungsraten, Nicht-Wiederfinden, Geisterleben und -ende, Start |
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
dünne Gehmatrix mit den Dichten als innerer Dimension messen), `libm`, `half` (float16), `base64`, `miniz_oxide`
(zlib, rein Rust); Bindung: `pyo3`, `numpy`, `pythonize`. Keine System-BLAS, keine Systempakete.
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

**`presence_core.Tracker`** (Rust):

```
Tracker(config: dict, model: dict | None, start: float | None, people: list | None)
reconfigure(config) -> bool               people_state() -> dict / restore_people(dict) -> bool
process_frame(sid, t, frame: Frame)       (Frame: geprüfte Nutzlast, siehe unten)
step(t)                                   set_pauses(intervals)
outputs(now) -> dict                      Zonenwerte, Belegt mit Quelle, stumme Räume, Ziel (ungerundet)
count_distribution(), place_distribution(), entering(), targets(), persons(), snapshot(), hyp_weights()
learned() -> dict / load_learned(dict)    die drei Abschnitte in den heutigen Formaten
reset_people(), reset_tracks(), ld_pause(seconds)
views: plan() (Räume, Orte, Kacheln für /api/tiles), sensor_maps(sid), runtime(), hypotheses(), check()
events() -> list                          für listeners: frame, watch, track_end, branch (nur wenn welche angemeldet)
```

Dazu Funktionen für die Kalibrierung: `Ld2450Tracks` (die Spurstufe des Sensors), `wall_pieces`, Sensor ↔ Welt,
Sichtlinie.

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

**`presence_tracker.filter.Tracker`** bleibt die Klasse, die App, Werkzeuge und Tests benutzen, mit derselben
öffentlichen API: Sie reicht durch, formt `ZoneState`, ruft die `listeners` mit den Ereignissen des Kerns und hält
die Python-Objekte der Hülle (`config`, `pauses`). Die Konfiguration geht als `config.to_dict()` hinein; das
Modell als Dict der Felder von `filtermodel.Model`, die vom Standard abweichen (die Werkzeuge, die heute `tracker.m`
nach dem Bau ändern oder `filter.MAX_STEP`/`GAP_EXACT` patchen, geben das beim Bau an).

**Was sich für die Benutzer ändert** (Inventur aller Zugriffe von außen, 27.10.):

| Zugriff | wer | danach |
|---|---|---|
| `tracker.ghost_map`, `ld_background`, `dest_map` als Python-Objekte (`to_dict`, `load`, `use`, `snapshot`, `add_birth`, `count`, `num`/`den` …); `use_ghost_map/use_dest_map/use_ld_background(obj)` | App (Dateien `ghostmap.json`, `ld2410.json`, `destinations.json`), `report_eval`, `baseline_eval`, `entries` (reichen Objekte von Tracker zu Tracker) | über `learned()`/`load_learned()` als Dicts in den heutigen Formaten; die App schreibt die drei Abschnitte in dieselben Dateien |
| `tracker.tracks[sid] = SensorTracks(…)`, `runtime[*].last_frame = -inf`, `ld_background.pause(…)` | App `h_reset_tracks` | `reset_tracks()`, `ld_pause(s)` |
| `runtime[sid].detections` (`hidden`, `stale`, `pos`), `sensor_model.maps(…)`, `tiles.squares/square/room`, `seen.watched()` | App (Oberfläche), `report_eval` | `runtime()`, `sensor_maps(sid)`, `plan()`, `outputs()` |
| `persons()[i]["obj"]` (lebendes Objekt) | `replay.py`, `report_eval.py` | Mittel/Kovarianz bzw. Dichte als Arrays |
| `tracker.hyps` (Anzahl, Gruppen, Gauß-Felder) | `report_eval`, `replay` | `hypotheses()` (lesend) |
| `config.observed_rooms()`, `closed_rooms`, `zones_of`, Orte | App, HA, Kalibrierung | Abgeleitetes nur aus dem Kern (`plan()`); `model.py` behält Daten, Prüfung, Speichern |
| `SensorTracks`, `floorplan.wall_pieces`, `SensorConfig.to_world` | Kalibrierung | Funktionen des Kerns |
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
1. nur Ausgabeparameter (`OUTPUT_PARAMS`: `target_threshold`, `target_thresholds`, `light_cost`, `seen_hold`,
   `approach_cost`, `lead_time`, `ld2410_hold`) oder Namen von Zonen geändert: **kein Neubau**, nichts beginnt neu,
   nur die Parameter und der Ausgabe-Cache;
2. nur Sensoren geändert (gedreht, verschoben, kalibriert, ein/aus): Personen bleiben, deren Spuren enden wie bei
   Datenverlust, Sicht und Raten neu, Geisterkarte folgt (ein anders montierter beginnt neu);
3. sonst: Neubau, Personen aus `people_state`, wenn die Kacheln gleich bleiben.

Je Weg ein Test; für 1 zusätzlich: Hypothesen, Gewichte und Spuren sind danach bitgleich wie davor, und der Lauf geht
bitgleich weiter wie ohne die Änderung (bis auf die geänderten Ausgaben).

## 5. Build

- **Lokal (hermann):** Rust stable 1.99.1 ist da. `maturin` als Entwicklungsabhängigkeit über `uv`;
  `maturin develop --release` baut `presence_core` in `tracker/.venv`. `cargo run --release -p presence-replay`
  für die Wiedergabe. Keine Systempakete.
- **Image (Variante B):** GitHub Actions bei einem Tag `v<version>`: Wheels mit `maturin-action` (amd64
  `RUSTFLAGS=-C target-cpu=x86-64-v3`, aarch64 generisch, quer kompiliert; cp313, abi3 falls `numpy`/PyO3 es
  hergeben), dann `docker buildx` für beide Architekturen: `python:3.13-slim` + Wheels, ohne numba/llvmlite, nach
  `ghcr.io/leonrein/{arch}-presence-tracker:<version>`; `config.yaml` bekommt `image:`. Leon stellt das ghcr-Paket
  einmal auf öffentlich. Release: Tag → Image fertig → erst dann main vorspulen. Im README: amd64 braucht AVX2.
- **Prüfung in CI:** `cargo fmt --check`, `cargo clippy -- -D warnings -W clippy::redundant_clone
  -W clippy::needless_pass_by_value`, `cargo test`, `pytest`.

## 6. Wiedergabe in Rust und Prüfplan

**`presence-replay`** (ohne Python): liest Konfiguration (JSON), Aufnahme (`.jsonl`, Bildzeit über dieselbe Uhr wie
die App), optional Gelerntes und `people_state` als Start, und treibt den Tracker wie die App (`prof.py`: Frames,
`step` und Ausgaben alle 0,1 s). Modi:
- `trace`: schreibt je Sekunde genau das Format von `rust-port/trace.py`, damit `cmptrace.py` unverändert vergleicht;
  dieselbe Spur über die Python-Hülle muss **bitgleich** sein (Test der Bindung);
- `prof`: CPU-Zeit wie `prof.py` (ohne Python-Anteil: zeigt, was die Hülle kostet);
- später `segments`: was `seg_replay.py` für `fast_eval` tut (Abschnitte aus den `learned.json.gz`-Prüfpunkten, dieselben
  Ausgaben), parallel über Prozesse statt Threads im Kern. Bis dahin läuft `fast_eval` über die Python-Hülle.

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
3. Die Kalibrierung bleibt vorerst Python mit numpy und ruft den Kern für LD2450-Spuren und Geometrie.
4. Lernreihenfolge wie heute, mit getrennt geliehenen Lernern (2.0); `faer` auf einem Thread (3); der schnelle Weg
   von `reconfigure` bleibt (4); Test aller Konfigurationen (6); Schwelle −8 % für den Prototyp (7); nur aus dem
   asyncio-Loop (4).
