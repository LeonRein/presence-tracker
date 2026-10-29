# Changelog

## Unveröffentlicht

## 0.43.0 (Branch `rust-core`, keine doppelte Logik zwischen Python und Rust; nicht live)

Modell unverändert. Spuren der drei Referenzstunden byte-gleich mit 0.42.0 (presence-replay und `trace.py` über die
Hülle); ab.sh 1200 16,5 s (0.31.1 47,3 s); 162 + 2 Rust-, 246 Python-Tests mit PT_CONFIGS (210 ohne; je 1 übersprungen: der
Test des Release-Wheels); fmt/clippy sauber.

- **Sensorgeometrie nur im Kern:** `presence_core.Geometry(config)` (über Punktlisten): `to_local`, `in_fov`,
  `sight_origin`, `sees`, `in_sight`, `free` (in einem nicht geschlossenen Raum und in Sicht), `crossings` (Schritte
  über Wandlinien und Wände), `on_floor` (Rohmeter → Bodenpunkt ohne Maßstab; im Kern `Mount::on_floor`, `to_world`
  rechnet bitgleich wie bisher). Gelöscht: `SensorConfig.to_local/in_fov/sight_origin/sees`, `SIGHT_OFFSET`,
  `Config.wall_segments`, `floorplan.wall_pieces/door_openings/sight_segments`, `geometry.segments_intersect/
  line_of_sight`, `calibration.ground/_in_polygon/_sight/_crossing`. Kalibrierung und Simulation fragen den Kern
  (`sim.simulate(..., plan=config)` statt `walls=config.wall_segments`); die Entfernung des LD2410C in der Anzeige
  rechnet der Kern (`Core.floor_distance`; unter der Höhendifferenz jetzt 0,1 m statt 0). Vorher verglichen
  (`rust-port/cmpgeometry.py`, alte Python-Fassung eingefroren): auf Punktgittern aller Dev- und Testkonfigurationen
  gleich (Bodenpunkt bis 1e-15 relativ: numpys gegen CPythons hypot); nur numpys komplexe Kreuzprodukte von
  `_crossing` rundeten auf langen Feldern an Schritten durch Wandenden anders als einzeln (27–29 von 41 000).
- **Konfiguration nur im Kern geprüft:** `config/check.rs` mit der einen Grenztabelle (Parameter, Sensor, Tür,
  Bildebene) und der Prüfung des JSON (nur was da ist); `Config::from_json` prüft erst, die getypte `Config::check`
  ist weg. Fehler strukturiert: `ConfigError { part, field, problem }`, in Python `presence_core.ConfigError`
  (ValueError) mit einem `ConfigProblem` (Teil, Name, Feld, Grund, Grenzen, Wert); `presence_core.check_config`,
  `presence_core.config_limits()` (die Oberfläche liest daraus ihre Eingabefelder). Die Grenzen sind die der
  Oberfläche (enger als die bisherigen des Kerns, etwa `lead_time` > 0, Höhe ≥ 0,1 m; alle Dev-Konfigurationen
  bestehen sie). Die Hülle hat nur noch die deutschen Bezeichnungen und Sätze (`model.LABELS`, `model.message`,
  `model.check`, `model.limits`); `check_config`, `*_LIMITS`, `limits_dict`, `Config.from_dict(check=)` sind weg.
  `PUT /api/config` prüft das Gesendete und das Normalisierte (Räume aus den Wänden).
- **Zonenzustände aus dem Kern:** `outputs::ZoneState` und `Tracker::zone_states(now)` (Räume des Heims, dann die
  übrigen Zonen, zuletzt das Haus `_total`; mit „wird betreten“, „Ziel“, Schwellen, Entscheidung und Quelle,
  `Source::name`). Die Hülle kopiert Feld für Feld in `zones.ZoneState`, presence-replay schreibt sie nur (sein
  `Layout` der Zonen ist weg). `Core.zone_numbers`, `ZoneView`, `HouseView` entfallen.
- **Kein zweiter Weltbau:** `presence_core.World(config, model)`; `Core(world)` und `Core.recalibrate(changed, world)`
  nehmen sie. Passen die Personen nicht (`Tracker::fits`), bleibt die Welt ganz und die Hülle baut den neuen Kern
  damit (Weg 3 baute sie zweimal).

## 0.42.0 (Branch `rust-core`, Aufräumrunde nach der Gesamtprüfung, REVIEW_final.md „Vor dem Release“; nicht live)

Modell unverändert; anders nur bei ungültigen Eingaben. Spuren der drei Referenzstunden byte-gleich mit 0.41.0
(presence-replay und `trace.py` über die Hülle); ab.sh 1200 16,6 s (0.31.1 47,4 s); 156 + 2 Rust-, 246 Python-Tests mit
PT_CONFIGS (1 übersprungen: der Test des Release-Wheels); fmt/clippy sauber.

- **Fehler statt Paniken:** `Tracker::step` mit einem t ohne Zahl (∞, NaN) ist `CallError::Time` (vorher:
  unendlich viele Teilschritte, Speicher voll); ein Poisson-Mittel NaN zählt niemanden Unbekannten (vorher: Zugriff
  außerhalb); eine Geschwindigkeit NaN fragt die Zielkarte nicht (vorher: `expect`). Die Konfiguration lehnt mehr als
  64 Sensoren und doppelte Sensor- oder Zonen-IDs mit Namen ab.
- **Größenschranken:** Reichweite eines Sensors ≤ 10 m (Kern und `model.SENSOR_LIMITS`), Raster ≤ 10⁶ Zellen
  (100 × 100 m), höchstens 4000 Kacheln (vor dem Bau der Gehmatrix); die Verbreiterung der Sicht hält je Versatz nur
  eine Maske (vorher alle: bis ~1,8 GB je Sensor), beide Gitter in einem Durchgang, bitgleich.
- **Modellparameter:** unbekannte Felder abgelehnt (`deny_unknown_fields`, auch in den Unterstrukturen); `validate`
  prüft jedes Feld (Divisoren, Zeiten, Anteile, Obergrenzen der Stufen, Längen und Schrägen der Echoquellen) und gibt
  `ModelError { fields }` zurück. `silent_after` ist ein Feld des Modells (Standard 30 s), kein Argument des Baus.
- **Hülle:** jeder Zugriff auf den Kern über `_Guarded` (eine Panik wird `CoreError`, auch in einem Getter; vorher
  nur über `_call`). `reconfigure`: Weg 1 (nur `OUTPUT_PARAMS`) baut nichts (`Core.set_outputs`); `Core.recalibrate`
  sagt `presence_core.WorldChanged`, nur das gilt als andere Welt (vorher jeder `ValueError`), und rechnet ohne GIL;
  `OUTPUT_PARAMS` kommt aus dem Kern (+ `ld2410_hold` der Anzeige).
- **Tot:** `Tracker::set_world/merge/prune`, `CallError::NoSensor`, `Core.epoch`; `Tracker::normalize` nur mit
  `testing`; in der Hülle `n`, `seed`, `step(targets)`, die `start`-Argumente von `seen.silent/online`, `_version`,
  `Detection.of`, `SensorRuntime.last_frame` (`tools/replay.py --seed` mit).
- **Für Phase 11:** Python ≥ 3.13; das Wheel braucht numpy nur mit `testing` (Extra); Tests mit Hooks tragen den
  Marker `hooks` (`tests/conftest.py`: ohne Hooks ein klarer Fehler; gegen ein Release-Wheel `-m "not hooks"`, mit
  `PT_RELEASE_WHEEL=1` prüft ein Test, dass es keine Hooks hat); `test_replay` scheitert in CI (`CI` gesetzt), statt
  sich zu überspringen; die Digests des Baus nur auf x86_64.
- **Tests:** je neue Prüfung ein Test, der vorher scheitert (t ohne Zahl, NaN-Mittel, NaN-Geschwindigkeit, 65
  Sensoren, doppelte IDs, Reichweite, Ausdehnung, zu viele Kacheln, unbekanntes Feld, Grenzen und Divisoren des
  Modells, Panik in einem Getter der Hülle, die drei Wege von `reconfigure`). Dazu Handtests der Bewegung und Gewichtung:
  Massenerhalt von `move_densities` mit Region und Türen, `stays` auf einer Kachel gegen MODEL.md 3.1, Altern und Ende
  in `regions`, `flush` bei fester Rate gegen exp(−r·dt), `no_find` am Median, `ghost_end` gegen eine eigene
  Quadratur, `targets` der Zielkarte von Hand.
- **Doku:** `docs/rust-core.md` (die API `Core`, Abhängigkeiten, Weg 1, Kalibrierung), `DOCS.md` (Bauen und Testen),
  README (amd64 braucht AVX2); in den Kommentaren 265 Verweise auf gelöschtes Python entfernt, je Modulkopf ein Satz.

## 0.41.0 (Branch `rust-core`, Phase 10: die Wiedergabe in Rust; nicht live)

Modell unverändert; Spuren der drei Referenzstunden über die Hülle byte-gleich mit 0.40.1; ab.sh 1200 16,5 s (0.31.1 47,4 s);
141 + 2 Rust-, 244 Python-Tests mit PT_CONFIGS; fmt/clippy sauber.

- **`presence-replay`** (`core/presence-replay`, Programm ohne Python): `trace CONFIG RECORDING SECONDS OUT [EVERY]`
  und `prof CONFIG RECORDING SECONDS`. Treibt den Kern wie die App (`trace.py`/`prof.py`): jeder Frame geprüft wie in
  der App (ungültige verworfen), Bildzeit über `SensorClock`, alle 0,1 s ein Schritt und die Zonenwerte wie
  `zone_states`. CONFIG ist das JSON von `Config.to_dict()` (`tracker.json`).
- **trace** schreibt genau die Zeilen von `trace.py` über die Hülle, byte-gleich (`pyjson.rs`: Pythons `json.dumps`
  mit `repr` der Zahlen, bei einer genauen Mitte gerade gerundet wie Python): alle drei Referenzstunden gleich;
  `tests/test_replay.py` prüft es an einer Simulation (der Test der Bindung; braucht `cargo build --release -p
  presence-replay`).
- **prof** (1200 s vom 10.10., ein Kern): Kern allein 15,9 s, über die Python-Hülle (`prof.py`) 16,3 s – die Hülle
  kostet ~0,4 s (2,5 %).
- `SILENT_AFTER` (30 s) steht nur noch im Kern (`presence_core.SILENT_AFTER`); die Hülle übernimmt ihn.
## 0.40.1 (Branch `rust-core`, nach Leons Prüfung der Aufräumrunde; nicht live)

Modell unverändert; Spuren der drei Referenzstunden byte-gleich mit 0.40.0.

- **Alle Sichten der Bindung benannt** (`presence-py/src/views.rs`): `DetectionView`, `SensorMapView`, `EnteringView`,
  `TargetView` (Raum als Index, die Hülle setzt die ID ein), `CloudView`, `ZoneView`/`HouseView` neben `HypView`,
  `TilesView`, `PersonView`; kein `allow(clippy::type_complexity)` mehr in `presence-py`.
- **Schwelle „Ziel“ nur im Kern:** `Tracker.threshold` liest `OutputParams::target_above` (mit der Klemme in (0, 1));
  `zone_states` rechnet sie nicht mehr selbst.
## 0.40.0 (Branch `rust-core`, Aufräumrunde vor Phase 10; nicht live)

Modell unverändert; Spuren der drei Referenzstunden byte-gleich mit 0.39.1 (mit der eingetragenen `ld_view`-Tabelle);
ab.sh 1200 16,5 s (0.31.1 47,3 s); 141 Rust-, 243 Python-Tests mit PT_CONFIGS; fmt/clippy sauber.

- **Orte nur noch aus dem Kern:** `presence_core.plan(config)` bzw. `Tracker.plan` (beobachtete Räume, Orte, Bereiche,
  eigener Raum je Sensor, Regionen mit Name/Räumen/offen/Ausgängen, geschlossene Räume). Snapshot, `reconfigure`
  (gleiche Regionen?), Kalibrierung (`open_rooms`), `tools/replay.py`, `report_eval.py`, `baseline_eval.py` lesen ihn.
  Aus `model.py` gelöscht: `_places`, `_room_covered`, `observed_rooms`, `regions`, `portals`, `closed_doors`,
  `closed_rooms`, `outside_rooms`; `model.py` hat nur noch Daten, Prüfung, Speichern (und die Geometrie der
  Simulation). `test_core_build` prüft jetzt die Sicht gegen den Bau des Kerns; `Core.names()` ist durch `plan()`
  ersetzt.
- `sim.ld_prior` ist der Prior des Kerns (`presence_core.ld_prior()`).
- **Benannte Typen:** `SavedHyp` statt Tupel in `PeopleState`; in der Bindung `HypView`, `TilesView`, `PersonView`
  (+ `Shown`) statt großer Tupel.
- **`ld_view` in der Konfiguration:** Der gemessene Blick des LD2410C je Raumpaar (MODEL.md 4.3) ist eine Angabe
  des Hauses in `tracker.json` (`ld_view`, oberste Ebene wie Wände und Türen; geprüft: Zahl ≥ 0), nicht mehr ein
  Standard des Modells (der Kern: keine Tabelle = Faktor 1). Einmaliger Umrechner beim Start der App
  (`app.ld_view_into_config`): eine `tracker.json` ohne `ld_view` bekommt die Tabelle von 0.39 für die Paare ihrer
  Räume; wird gelöscht, sobald ein Release damit auf HA gelaufen ist.
## 0.39.1 (Branch `rust-core`, nach Leons Prüfung von Phase 9; nicht live)

Spuren aller drei Referenzstunden byte-gleich mit 0.39.0 (6133488); ab.sh 1200 16,5 s (0.31.1 47,3 s); fast_eval
full --stages 2: Wahrheit 0,48 / 3,28 min wie 0.31.1, gegen 0.38.0 0,0 min Licht anders über 96 h; 141 Rust-, 241
Python-Tests mit PT_CONFIGS (205 ohne); fmt/clippy sauber, mit und ohne Feature `testing`. Modell unverändert.

- **H1:** `test_core_build` vergleicht wieder, was `model.py` noch ableitet (Regionen, beobachtete und geschlossene
  Räume, Ausgänge/Pforten), mit dem Plan des Kerns, für jede Konfiguration auch in `PT_CONFIGS`; der eigene Raum je
  Sensor steht in den Digests. Der Snapshot baut die Regionen aus den Orten des Kerns, ohne Ersatzwert.
- **H2, M2:** Die Fixtures sind reproduzierbar: `tests/fixtures/make/make_0381.py` und `core_build.py` laufen auf
  29c2fe1 (dort neu erzeugt: `people.json` und `ghosts.json` byte-gleich); README mit Herkunft und Erzeugung. Der
  Bau-Digest ist einer je Teil (Schlüssel von `build_export`, je Sensor), die Spiegelungsregel einer je Sensor; ein
  Bruch nennt den Teil. `devdata/` (privat, nicht im Repo) zählt nicht mehr zu den Konfigurationen des Repos.
- **M1:** von Python 0.38.1 geschriebene Fixtures für `ld2410.json` (zwei Sensoren, Pegel, Anker, Echos, gehalten)
  und eine Zielkarte mit Zählungen, in beiden Richtungen geprüft (der Kern liest wie Python und schreibt dasselbe
  zurück).
- **M3:** `filter/mirror.rs`, `WorldExport`/`from_export` nur noch in Tests; aus der Bindung `set_world`, `flushed`,
  `live_tracks` gestrichen; die Hooks der Python-Tests (`normalize`, `spoil_weights_for_test`, `ld_last`,
  `panic_for_test`, `build_export`, `hidden_at`) hinter dem Feature `testing` (`maturin develop --features testing`;
  numpy nur dafür); Doku der Bindung berichtigt.
- **M4:** `forgetting()` im Kern für `fast/compose.py` (läuft auf 0.39 mit `model_defaults()`).
- **N1:** `recalibrate` prüft die neue Welt, bevor es etwas ändert; die Hülle baut keinen zweiten Kern zur Probe
  mehr, sondern beginnt neu, wenn der Kern ablehnt. **N3:** Plan-Test „durch die Tür keine Wand“. `saved.rs`: `r`
  ab 0 gelesen; eine Dichte in beiden Rollen wird abgelehnt (Test). Veraltete Verweise berichtigt.

## 0.39.0 (Branch `rust-core`, Phase 9: die Python-Hülle fertig, der Python-Kern und numba gelöscht; nicht live)

Spuren aller drei Referenzstunden byte-gleich mit 0.38.1 (nur Umbau); ab.sh 1200 16,8 s (0.31.1 47,8 s); 140 Rust-,
216 Python-Tests mit PT_CONFIGS (199 ohne);
fmt/clippy sauber. Das Modell ist unverändert.

- **Gelöscht:** `kernels.py` (numba), `gauss.py`, `hidden.py`, `tiling.py`, `world.py`, `sensormodel.py`, `ghostmap.py`,
  `destination.py`, `ld2410.py`, `filtermodel.py`, `unobserved.py`, `roomseen.py`, aus `model.py` `Config.hidden` und
  `remounted`; aus `filter.py` der Spiegel der Hypothesen, der Bau der Welt in Python und seine Prüfung gegen den
  Kern, die Konstanten des Modells. numba/llvmlite aus `pyproject.toml` und dem `Dockerfile` (das Image bekommt das
  Kern-Wheel mit Phase 11).
- **Gespeicherter Zustand der Personen im Kern** (`filter/saved.rs`): das Format von `people.json` (float16, Anteile
  unter 1e-6 als 0, zlib, base64; der Fingerabdruck der Kacheln als dieselbe Zeichenkette), mit benannten Fehlern
  beim Lesen; was 0.38.1 schrieb, liest der Kern, und schreibt es mit denselben Massen zurück (Test gegen Fixtures).
- **Das Modell ist der Kern:** `Tracker(config, model=dict)` mit den Feldern, die vom Standard abweichen;
  `presence_core.model_defaults()` (vorher einmal geprüft: alle 64 Felder, die die Hülle schickte, gleich), die
  gemessene Tabelle `ld_view` steht jetzt in den Standards des Kerns. `sim.py` simuliert mit
  `presence_core.ld_expected` und den Standards.
- **Lesende Sichten** statt Spiegel und Python-Welt: `hypotheses()`, `tracks()`, `tiles()` (Fingerabdruck als `id`
  von `/api/tiles` und `tiling` der Live-Daten), `sensor_map()` (die grobe Sicht für die Oberfläche), `sight()`,
  `observed()`, `tile_near()`. `SILENT_AFTER` steht in `filter.py`.
- **Tests:** die Innereien-Tests (`test_kernels`, `test_unobserved`, Teile von `test_ld2410`, `test_learning`,
  `test_hypotheses`) sind Rust-Tests mit denselben Fällen; die übrigen laufen über die API, wo sie Python-Orakel
  brauchten, gegen Fixtures, die einmal mit 0.38.1 erzeugt wurden (`tests/fixtures/`, README dort); `test_core_build`
  gegen Digests des Baus und der Spiegelungsregel von 0.38.1. Tabelle „Test → wohin“ in der Übergabe.

## 0.38.1 (Branch `rust-core`, nach Leons Prüfung von Phase 8, nicht live)

Spuren aller drei Referenzstunden byte-gleich mit 0.38.0 (nur Umbau); ab.sh 1200 16,5 s (0.31.1 48,2 s); 125 Rust-,
228 Python-Tests (262 mit PT_CONFIGS), fmt/clippy sauber.

- **M1:** Die Reihentests der Lernreihenfolge schlagen jetzt fehl, wenn die Reihenfolge im Code vertauscht wird (je
  einmal ausprobiert): Reihe 1 vergleicht die Rate an der Geburt mit der Karte ohne die eben gelernte Geburt
  (`assert_ne!`), neu „Total vor `add_watch`“ (der gelesene Total ist der ohne die eben beobachtete Zeit); Reihe 3 mit
  einer gelesenen Echorate gleich dem doppelten Prior und `began` 0,2 (von Hand 1/9 · 0,5); Reihe 4 mit jemandem, der
  1 m/s geht (die Position des Gangs ist die nach dem Bewegen, nicht die davor).
- **M2:** `GhostMap::from_json` und `Background::from_json` nehmen den Plan und folgen ihm sofort: kein halb gebautes
  Ergebnis mehr.
- **M3:** Zielkarte und laufende Gänge sind ein Typ (`Destinations`): ein anderer Grundriss setzt beide zurück, eine
  geladene Karte gibt es nur für diese Geometrie.
- **M4 (Herkunft der Abweichung gegen 0.37.1):** In einem Arbeitsbaum von 0.37.1 die beiden Summen so gerechnet wie
  der Kern – `gained` des Hintergrunds (`w @ (b / mus)`, `w @ tail`: dgemv) und das Glätten der Geisterkarte
  (`np.convolve`: ddot) der Reihe nach –, dann gegen 0.38.0 gespurt: die erste Abweichung rückt am 10.10. von 19 s auf
  1178 s (4,9e-18) und am 8.10. von 76 s auf 130 s (2e-34). Das sind also die beiden Hauptquellen; der Rest beginnt
  später ebenso im letzten Bit, vermutlich die Erwartung gekappter Energien (`log_gammaq`/`exp` des Hintergrunds,
  zensierte Frames werden in beiden Stunden vorher gelernt; Toleranz für iterative Näherungen). Nicht nachgebaut
  (Leons Toleranzen); fast_eval: 0,0 min Licht anders über 94 h.
- Ein beschädigter Teil des Hintergrunds verwirft nur diesen Sensor (wie Python), Gelerntes ohne Pose wird gemeldet;
  die App loggt jeden verworfenen Teil als Warnung. `Event::Branch` mit `Branched::{Born { map_rate }, FoundAgain}`
  und `Child { logw, category }` statt Bool und Sentinel; `LearnedError::Json` und `half` entfernt, `PackedFault` statt
  Text, eigener Fehler `BadPause`; `Pose` im Plan (die Welt hängt nicht mehr am Lernen); Geschwindigkeitszellen einmal
  (`LazyLock`); `Kalman::velocity` statt `mean[1]`/`mean[d + 1]`; Glätten der Geisterkarte mit `convolve_same`;
  `adopt` tauscht das Gelernte statt es zu klonen. Hülle: die Pausen werden auch vor einer Rekalibrierung abgeglichen;
  `test_pause` vergleicht wieder mit etwas Unabhängigem (Gelerntes wie ein Tracker ohne Frames, Verfolgung anders als mit
  Lernen).

## 0.38.0 (Branch `rust-core`, Phase 8 des Rust-Ports, nicht live)

Der Kern lernt selbst: Geisterkarte, LD2410C-Hintergrund mit Anker und Echorate, Zielkarte samt „Ziel“, an genau den
Stellen von 0.37.1 (docs/rust-core.md 2.0); die Pausen sind seine Eingabe. Damit ordnet niemand mehr außerhalb die
Schritte eines Frames: `Tracker::frame` und `Tracker::step` tun alles, was `process_frame`/`step` taten.
Spuren (ein BLAS-Thread, numpy wie inge): 7.10. bitgleich mit 0.37.1; 8.10. und 10.10. weichen ab t = 76 s bzw. 19 s
um 1e-18 bzw. 1e-16 ab (Rundung beim Lernen des Hintergrunds, wachsend bis 7e-4/4e-4) und kippen an den bekannten
Kippstellen: 8.10. ab 459 s (gegen ref_0311 jetzt nur 0,012 und 0 Raum-Sekunden, 0.37.1 hatte 0,070 und 6), 10.10. ab
1305 s mit den 844 Raum-Sekunden, die schon ein anderes BLAS-Produkt dort auslöste (PLAN.md). Die Gegenprobe mit
fast_eval steht aus (Abnahme). ab.sh 1200: 16,5 s (0.31.1 48,1 s: −66 %; 0.37.1 19,5 s). 123 Rust-, 228 Python-Tests
(262 mit PT_CONFIGS).

- **Lernen im Kern** (`learn/`, `ld2410/background.rs`): `GhostMap` (Teile je Sensor in dessen Rahmen, Glätten,
  Vergessen, Rate und Gesamtrate je Sensor, Folgen der Posen: umgehängt = neu), `Background` (Niveau und Anker als
  `Level`, Echos als `EchoCount`, Halten nach „Tracks zurücksetzen“, Lernen und Echorate in einem Schritt), `DestGeometry`
  (in der Welt; Zellen, Räume über Trennwände, Nachbarn ohne Wand, Türen, Fingerabdruck = Pythons Zeichenkette),
  `DestMap`, `Learner` (Gänge, Übergabe, Stehen) und `targets` („Ziel“ als Ausgabe im Kern, im Ausgabe-Cache).
  `Learned` bündelt die drei; jeder Schritt leiht nur den Lerner, den er füttert. `Pauses` (Intervalle, geprüft).
- **Lernreihenfolge als Rust-Tests**, je Zeile von Entwurf 2.0 einer (Geburt liest die Karte nach den Enden desselben
  Frames; Hintergrund mit b und Mitteln vom Anfang der Bewertung; Echorate-Korrektur mit der vorher gelesenen Rate;
  Zielkarte nach Bewegen und Zurückgeben; in einer Pause nichts). `tests/test_learning_order.py` entfällt dafür.
- **M4 (REVIEW_phase7):** Die Zeit des letzten Frames steht nur noch in der Spurstufe des Sensors (`Ld2450Tracks`),
  `SensorState::last_frame` liest sie; `set_sensors`, `rt.last_frame` und `active` aus der Hülle entfallen (`active` =
  `SensorPlan.in_use`). `delta`/zensiert, das Filtern der beendeten Spuren, `_evidence`, `_end_silent`, `_flush` mit der
  LD2410C-Bewertung, `_recycle`, `_predict_gap`, der Start beim ersten Frame und `_recalibrated` sind im Kern
  (`frame`, `step`, `recalibrate`, `restore(state, gespeichert)`).
- **Ereignisse statt Rückrufe:** `frame`/`step`/`recalibrate` geben `Event::{Watch, TrackEnd, Branch}` zurück; die Hülle
  ruft damit die Listener wie bisher (Nutzlasten gleich). Ein Unterschied: Der Listener „frame“ hört jetzt nach den
  Schritten des Frames statt davor.
- **Formate unverändert** (`formats.rs`: numpys und Pythons Runden, base64/zlib, sha1-Fingerabdruck): ghostmap.json,
  ld2410.json, destinations.json werden vom Kern gelesen und geschrieben; Eingaben von außen werden geprüft
  (`LearnedError` mit Namen: Feld, Gitter, Größe je Sensor, gepackte Zahlen, anderer Grundriss), Größen vor dem Anlegen.
  Strenger als 0.37.1: ein Hintergrund mit einem kaputten Teil wird ganz abgelehnt (die App beginnt ihn neu) statt den
  Teil still zu verwerfen; Teile ohne Pose werden nicht gelesen. Python von 0.37.1 (`ghostmap.GhostMap`,
  `ld2410.Background`, `destination.DestinationMap`/`Geometry`) liest, was der Kern schreibt – die Tests prüfen das, die
  Python-Klassen bleiben dafür bis Phase 9.
- **Hülle/App:** `use_ghost_map/use_ld_background/use_dest_map` nehmen Dicts (oder None), `learned()`/`load_learned`,
  `learned_snapshot().json()` packt im Schreib-Thread, `hold_background`, `background(sid)`, `ghost_rates`;
  `sensormodel.maps` nimmt `ghost_rates`; `Pauses.changes` zählt Änderungen, die Hülle reicht sie dann an den Kern.
  `report_eval`/`baseline_eval` geben `learned()` weiter. Neue Modellparameter im JSON des Kerns: `ghost_prior_ghosts`,
  `ld_background`, `ld_prior_time`, `ld_forget`, `ld_anchor_forget`, `ld_anchor_quiet`, `ld_echo_rate`,
  `ld_echo_prior_time`, `dest_prior_walks`; `target_threshold(s)` in den Ausgabeparametern.

## 0.37.1 (Branch `rust-core`, nach Leons Prüfung von Phase 7, nicht live)

Spuren aller drei Referenzstunden byte-gleich mit 0.37.0; ab.sh 1200 19,5 s (0.31.1 48,0 s: −59 %; `Frame.of` mitgezählt);
103 Rust-, 232 Python-Tests.

- **H1:** Frames werden gegen das geprüft, was die Firmware sendet (`esphome/packages/radar-combo.yaml`), jeder Fehler
  benannt: `uptime_ms` eine ganze Zahl 0..=u32::MAX (`millis()`), im `Frame` als `Option<u32>` gespeichert, nur damit
  wird `SensorClock::at` gefüttert; Slot 1..=3 (Pflicht); x, y, speed ganze mm innerhalb ±32767; Gates beide oder
  keine, je genau 9 Energien 0..=100 (`GateFault`); Flags `moving`/`still` true/false, Abstände ganze mm. Bis 0.37.0
  machte `uptime_ms = 1e300` die Bildzeit 0 (negative Lücke, danach Datenverlust des Sensors), riesige x/y NaN,
  riesige Gates inf. Alle 5,27 Mio. Frames der Aufnahmen 6.–14.10. halten die Grenzen ein.
- **M1:** Ein geprüfter `presence_core.Frame` statt Dicts: `Frame.parse(payload)` in der App, `Frame.of(dict)` für
  Aufnahmen, Tests und Simulation; Getter `uptime_ms` und `ld2410` (Anzeige). `SensorClock`, `Tracker.process_frame`,
  `Core.frame` und `TrackStage.update` nehmen nur ihn – ein ungültiger Frame erreicht den Tracker nicht. Entscheidung
  in docs/rust-core.md 4 (kein `process_payload`). `frames.parse_frame` gelöscht; Werkzeuge und Dev-Werkzeuge
  umgestellt (die Dev-Werkzeuge fallen bei älteren Bäumen auf das Dict zurück; prof.py zählt `Frame.of` mit).
- **M2:** `ld2450::tracks::TrackStage` (Spuren mehrerer Boards, eine ID-Folge, `Measurement`) im Kern mit Rust-Test;
  „vor dem Sensor“ (`Frame::in_front`) und „gemessen“ (`TrackEvents::measurements`) je einmal; die Bindung reicht nur
  durch. `tools/calibrate_offline.py` war seit 0.37.0 kaputt (alte `track_frame`-Signatur), jetzt umgestellt.
- **M3:** `SensorFrame` trägt `t`; `track_events(si)` und `ld_frame(si)` nehmen ihre Ereignisse bzw. den LD2410C-Teil
  heraus – ein zweiter Aufruf tut nichts (Test).
- **Niedrig:** `follow` als freie Funktion mit `Report { Measuring, Holding(LostKind) }`; Slot `u8`;
  `FrameError::Target { index, field }`; `NoSensor`/`Time` als `CallError`; `to_world` und `to_local` an `Mount`
  (`SensorConfig::mount`); tote `max(1e-3)`/`ground > 0` weg (ground ≥ 0,1·scale); `LdSensor::frame` nimmt
  `Option<&LdGates>`; die Bindung prüft `si` in jeder Methode (ValueError statt Panik); tote Python-Felder
  `rt.ld_e`, `ld_t`, `ld_target_t`, `rt.frame` gelöscht. Tests neu: Gleichstand in `link`, GATE-Grenze, Coast-Zähler
  beim Geschwindigkeitswechsel, `concerns_all` beim Wiederfinden, alle Abweisungen nach H1 (Rust und App).

## 0.37.0 (Branch `rust-core`, Phase 7 des Rust-Ports, nicht live)

Der Kern liest die Frames selbst (docs/rust-core.md 1, 7): Nutzlast prüfen, Bildzeit, Detektionen und die Spurstufe
des LD2450. Spuren aller drei Referenzstunden byte-gleich mit 0.36.1; ab.sh 1200 19,4 s (0.31.1 48,0 s: −60 %;
0.36.1 20,7 s).

- **`frames.rs`:** `Frame::parse`/`from_json` (ein Prüfer für Bytes und für das Dict der Hülle) mit benannten Fehlern
  (`FrameError`: kein JSON, kein Objekt, uptime_ms, targets, Ziel k, doppelter Slot, mehr als 3 Ziele, ld2410,
  Gates, Abstände; nicht endliche Zahlen schon bei der Umwandlung aus Python); `Ld2410 { Absent, NoGates,
  Gates }` statt „Schlüssel da“ + `None`. `SensorClock` (Anker als `Option<Anchor>`), `Mount::to_world`,
  `detections` mit `MOUNT_RADIUS`; die ungenutzten `R`, `radial` und der Azimut fallen weg.
- **`ld2450/tracks.rs`:** die Spurstufe des LD2450 (sensortracks.py) mit `RawTrack { Unborn, Live { seg, lost } }`,
  `TrackEvents` mit Indizes in die Detektionen, `SegIds` (eine Folge je Tracker, über Neubauten weiter: `adopt`).
- **Tracker:** `frame(si, t, &Frame)` liest den Frame, aktualisiert die Spuren und „seinen Raum gesehen“ und behält ihn
  als letzten Frame des Sensors; `track_events`, `concerns_all` (statt `any_lost`), `ld_frame` lesen daraus, statt
  Positionen aus Python zurückzubekommen. `data_lost(t)` (heute `_end_silent`), `reset_tracks(si)`;
  `Plan::hidden` nimmt den Ursprung der Sichtlinien statt der Sensorkonfiguration.
- **Bindung/Hülle:** `presence_core.check_frame`, `SensorClock` (Python-Klasse mit `offset`/`last_up`),
  `TrackStage` für die Kalibrierung; `frames.parse_frame` nutzt den Prüfer des Kerns; `Tracker.reset_tracks()` (App
  „Tracks zurücksetzen“). Gelöscht: `sensortracks.py`, `frames.detections`, `SensorConfig.to_world`,
  `filter.MOUNT_RADIUS`, `Core.seen_frame/any_lost/adopt_seen`.
- **Tests:** Rust neu (12, jetzt 99): jeder Prüffehler, Frame und LD2410C-Teil, Uhr (Jitter, Kriechen, Neustart, Sprung),
  `to_world` von Hand und Rundweg, verdeckte Detektionen, Spurstufe (Slots rücken auf, Coasten/Einfrieren,
  verdeckt nicht geboren, Datenverlust), Tracker (`frame`, `data_lost` nur mit lebenden Spuren, IDs über `adopt`).
  Python: test_sensortracks und der Rundweg nach Rust; test_frames/test_sight über den Tracker;
  test_learning_order Zeile 1 mit einem echten Frame (Ziel springt: Ende und Geburt im selben Frame).

## 0.36.1 (Branch `rust-core`, nach Leons Prüfung von Phase 6, nicht live)

- **H1:** Die Welt ist nach dem Bau unveränderlich: `walk_end` als `OnceLock` in `Tiles` (`walk_end(&Params)`,
  gelöst beim ersten Bedarf), keine Übernahme aus der alten Welt. `Tracker::set_world -> Result` lehnt eine Welt mit
  anderer Dichteform, anderen Kacheln (`Tiles::same_as`: Zahl, Zentren, Flächen, Räume, Regionen, Altersstufen,
  Ausgänge bitgleich – statt eines Fingerabdrucks der genaue Vergleich) oder anderer Sensorzahl ab.
- **M1:** Konfiguration je Feld mit Namen geprüft (Sensor x/y/Höhe/Maßstab/fov ≤ 180/range, Sigmas > 0, Anstiege,
  Wandabstand, Haltezeiten, Kosten, Schwellen in (0, 1), Türen, Wände), höchstens 4·10⁶ Rasterzellen; die Felder,
  die die Hülle immer schreibt, sind Pflicht (keine serde-Vorgaben mehr, die eine Umbenennung verdecken).
- **M2:** `expm` gibt `Err` bei nicht endlicher Norm; `Params::validate` prüft turn_rate, go_share/go_time,
  stay_points (≥ 2, > 0), Stufen ≥ 2, kappa_amp_corr. Tests für abgelehnte Modelle und Konfigurationen.
- **M3:** Die Hülle vergleicht beim Bau und nach `_recalibrated` die Kacheln exakt (Zentren, Flächen, Räume,
  Ausgänge, Eingänge, Türkacheln je Region; `Core.tiles()`); `test_core_build.py` vergleicht `ld_view`-Werte und
  `walk_end` (Toleranz 1e-10, LU gegen LAPACK).
- **M4:** Typen statt Zahlen und Strings: der Plan ist eine Zonentabelle mit `ZoneRole` und Indizes (`ZoneIdx`,
  `RoomIdx`, `RegionIdx`), `Place { Observed, Region, Outside }`, `ZoneKind::OutsideRoom` statt `entry`,
  `ShapeKind`, `RoomGrid` statt Grid mit f64, `Decision` statt `Option<Option<Source>>`, `Link`, `Reach`,
  `Start::Place(Place)`, `Bounds`/`Away` statt der Bool-Flags von `predict`/`sigma`, `Holders::{entries,
  people}` statt `entries(bool)`.
- **M5:** `Tiles::build` in Schritten (`Cells`, `Layout`, `Moments`, `Samples`, `borders`, `door_rates`,
  `generator`, `walk_tick`, `RegionStays`), die Struct erst am Ende; `AGE_EDGES`/`AGES`; `build::sensor` in
  `point_sight`, `offset_var`, `Reach`, `spread`, `ld_tiles`, `observed`.
- **M6:** Rust-Tests ohne Python-Referenz: Plan von Hand (Regionen offen/geschlossen, Ausgänge, Pforten, ≥ 60 %,
  eigener Raum, Orte, Reflexion), zwei Kacheln (Generator, Gehschritt), `walk_end` bei zwei Zuständen gegen die
  Lösung von Hand, `zone_numbers`, `set_world` leert den Cache und lehnt andere Kacheln ab.
- **Niedrig:** `max2` nach `vmath`; `Plan::hidden` ohne `&Config`; Cache-Getter in `OutputCache`; Module, die
  nur der Kern braucht, `pub(crate)`; `retain` mit `keep_marked`; `0.0 + wi` kommentiert.
- **Fehler gefunden und behoben:** Die Bindung paarte die Zonenzahlen mit allen Zonen statt mit den Heim-Zonen;
  steht ein Außenraum vor einem Heimraum (live: `entry_treppe` vor `r4ade416`), verrutschten die Namen (beim
  Umbau entstanden, in keiner Spur von 0.36.0). `test_core_build.py` vergleicht jetzt die Zonennamen von
  `zone_states` mit den Heim-Zonen.
- **Prüfung:** Spuren aller drei Stunden mit eigener und mit Pythons Welt bitgleich mit 0.36.0 (a810241), samt
  aller Zonenfelder. `ab.sh 1200` 20,7 s gegen 48,2 s (0.31.1). 87 Rust-, 271 Python-Tests.

## 0.36.0 (Branch `rust-core`, Phase 6 des Rust-Kerns, nicht live)

- **Der Kern baut seine Welt selbst** aus der Konfiguration (`config.rs`: serde von `Config.to_dict()`) und dem
  Modell (alle Felder als JSON): Plan (`plan.rs`: Sichtstücke ohne Türöffnungen, Regionen mit offen/Ausgängen,
  Pforten, beobachtete Räume ≥ 60 %, eigener Raum je Sensor, Heim-Zonen, die Regel „Reflexion“ `hidden`),
  Raster der Orte und Räume, Wandabstand, Kacheln (`Tiles::build`: Schlüssel wie `np.unique`, Stützpunkte, Ränder,
  Türraten, Generator, eine Gehstunde über `linalg::expm`, `walk_end` mit faer-LU erst bei Bedarf im Kern),
  Aufenthalte in Regionen, Sicht je Sensor (Sensorkarte als float32 wie `SensorModel`, Verbreiterung über
  `reach`), LD2410C-Sichtlinie, -Blick und -Kacheln, observed; `Shapes::new` (Aufenthaltsmischung,
  Gamma-Gitter, Gauß-Kopula). CPythons `math.hypot` nachgebaut (`vmath::py_hypot`, weicht in 13 % der Fälle von
  libm ab und entscheidet Sichtfeld und Kreise), ebenso `NormalDist.inv_cdf` (AS241) – beide bitgleich getestet.
- **Bau gegen Python:** `rust-port/cmpworld.py` und `tests/test_core_build.py` (alle Konfigurationen der Tests,
  devdata und mit `PT_CONFIGS` die privaten, 23): Plan, Raster, Kacheln, Stützpunkte, Generator, Sicht je Sensor
  und LD2410C-Kacheln **bitgleich**, `hidden` auf einem Punktgitter gleich; nur über BLAS gerechnet weicht ab: die
  Gehmatrix (`expm`, ≤ 2,8e-15), der Wandabstand in 30 von 13104 Zellen (2e-16), Kopula und Aufenthaltsraten
  (≤ 2e-15 relativ). Der Export bleibt als Prüfhilfe: `PT_WORLD=python` lässt den Kern in Pythons Welt rechnen.
  Python baut seine Welt noch für Zielkarte, Formate, Oberfläche und Tests (bis Phase 8/9); beim Bau prüft die
  Hülle, dass Räume, Orte und Kachelform beider gleich sind.
- **M1:** Ausgabeparameter (`light_cost`, `seen_hold`, `lead_time`, `approach_cost`, `SILENT_AFTER`) in der Welt;
  ihre Änderung ist eine neue Welt und leert den Cache. `test_filter`/`test_roomseen` ändern sie über
  `reconfigure` statt direkt in `config.params`.
- **M2:** `zone_numbers` im Kern (Schwelle der Regionen, Anhebung durch den eigenen LD2450, bewegt/ruhig,
  approaching, verfügbar); `zone_states` formt nur noch und füllt „Ziel“; die doppelten Schwellen und
  `ENTER_MIN_WALK` in Python entfallen (`walkers()` filtert im Kern, `Core.thresholds()`). bewegt/ruhig aus den
  ungerundeten Personen (in einer von 3367 Sekunden anders als 0.35.0).
- **M3/M4:** fester Testfall (zwei Hypothesen, Mischung mit a/away, Person mit r = 0,4; Zählungen, Orte,
  Anzeige, Wolke von Hand) und dichteste Kachel; `PeopleState::check -> CheckedState` (endlich, ≥ 0, r ∈ [0, 1],
  eine Person ist irgendwo), `Filter::restore(CheckedState)` crate-intern; `Core.restore` gibt die Meldung.
- **Niedrig:** `mean_of`, `standing_anywhere(f64)`, Tabelle Eintrag → Gehender in `entering`, `walkers` mit
  `Walker`/`Held` in `outputs.rs`, Sichtbarkeit, `occupancy` über die Raumzeilen, Umrisse streng (`polygon`
  ausdrücklich), `Start::Place` außerhalb ein Fehler, `poisson_cut` begrenzt, `load`-Test gelöscht.
- **Prüfung:** Spuren mit Pythons Welt bitgleich mit 0.35.0 (numpy wie auf inge; außer den bekannten Feldern von
  *wird betreten*). Mit eigener Welt: 7.10. ≤ 2e-16; 10.10. Rundung (3e-12 bis 600 s, 2e-8 bis 1300 s), kippt
  bei 1305 s wie alle Phasen und das gestörte 0.31.1 (844 Raum-Sekunden); 8.10. kippt bei 462 s (dieselbe
  Entscheidung wie in Phase 3, 459 s), 8 Raum-Sekunden. `ab.sh 1200` 20,8 s gegen 48,1 s (0.31.1), −57 %.
  Weltbau im Kern 1,1 s CPU. 79 Rust-, 271 Python-Tests.

## 0.35.0 (Branch `rust-core`, Phase 5 des Rust-Kerns, nicht live)

- **Die Ausgaben im Kern** (`core/presence-core/src/outputs.rs`, MODEL.md 6): Zähl- und Ortsverteilungen (je
  Objekt die Masse in Sicht bzw. je Ort, Poisson-Binomial der Bekannten mit den Unbekannten; log k! wie CPythons
  `math.lgamma`, eigener Lanczos-Code, der bei 690 von 2000 Ganzzahlen von libm abweicht), Belegt (Filter oder der
  LD2450 des Raums), *wird betreten* (der Gehende über die lineare Vorhersage und die Wände der IMM:
  `motion::imm::walk_ahead`, `reflect_at_walls` jetzt auf `(&Kalman vorher, &mut Kalman nachher)`), Personen und
  Wolken der Anzeige, ungerundet – die Hülle rundet (Leon).
- **Neu gerechnet genau dann, wann 0.34.1 neu rechnete:** `OutputCache` (Zähl-, Ortsverteilungen, *wird betreten*)
  hängt am `epoch` des Filters; der zählt jetzt nur noch hoch, auch `clear` zählt weiter statt auf 0 (Leon: kein
  alter Cache kann mit einer neuen Epoche 0 verwechselt werden). Personen und Belegt je Aufruf wie bisher.
- **RoomSeen im Kern** (`room_seen.rs`): Räume und Sensoren über ihre IDs, folgt einer neuen Welt und geht über
  den Neubau der Hülle (`Core.adopt_seen`). Was der Kern vom Plan braucht (beobachtete Räume mit Umriss, eigener
  Raum je Sensor, Bereiche, Raum je Kachel, `region_start`) kommt bis Phase 6 aus dem Export (`world/plan.rs`,
  `geometry::Shape`). `light_cost`, `seen_hold`, `lead_time` und `roomseen.SILENT_AFTER` gibt die Hülle je Aufruf
  mit – 0.34.1 las sie je Aufruf.
- **Nichts schreibt mehr über den Spiegel:** `reset_people`/`restore_people` im Kern (`filter/start.rs`: nichts
  bekannt, Personen an Orten, geprüfter gespeicherter Zustand; die Hülle dekodiert nur das Format); `hyps` nur
  lesend; `Core.load`, das Cargo-Feature `mirror` und `_push` gelöscht (`filter/mirror.rs` nur noch für Rust-Tests).
  Tests: `test_restart`/`test_existence` bauen einen gespeicherten Zustand (`saved`) und nehmen `restore_people`;
  `test_robust` verdirbt die Gewichte mit `Core.spoil_weights_for_test`.
- **Hülle:** `zone_states` und `snapshot` setzen nur noch zusammen (Regionen, Bereiche, bewegt/ruhig aus den
  gerundeten Personen wie bisher); „Ziel“ bleibt Python bis Phase 8, liest aber `Core.walkers()` statt des
  Spiegels. `roomseen.py` nur noch `own_room` und die Konstanten. Gelöscht: `_counts`, `_poisson_binomial`,
  `_lgamma_table`, `_logsumexp`, `_in_view`, `_room_probs`, `_place_probs`, `_display`, `_cloud`, `_entry_targets`,
  `_walk_ahead`, `_target_mass`, `_entering`, `_ray_distance`, `_objects`, `unknown_start`; `kernels.poisson_binomial`,
  `Hidden.in_view/walking`, `Gauss.tile_mass`; `persons()` ohne `obj` (kein Benutzer). `restore_people` lehnt
  negative Indizes ab (vorher von hinten gezählt).
- **Prüfung:** Referenz 0.34.1 mit numpy wie auf inge (`NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL"`: auf hermann
  rechnet numpy `exp` mit AVX-512, 4,6 % der Werte im letzten Bit anders als libm; `OPENBLAS_CORETYPE=Haswell`
  stürzt auf Zen 5 bei großen Produkten ab, BLAS also wie auf hermann). Alle drei Stunden: Gewichte, counts, places,
  P(belegt) und alle Zonenfelder bitgleich außer *wird betreten*/Ziel (`p_enter`, `p_target`, `eta`: ≤ 1,8e-15 in
  1–34 von ~3400 Sekunden, keine Entscheidung gekippt; 0.34.1 rechnete dort mit BLAS-Produkten und `einsum`, deren
  Reihenfolge vom BLAS-Kern der Maschine abhängt). zone_states, snapshot, entering, persons, occupancy je Sekunde
  über die Stunde 10.10. 12 Uhr (`rust-port/outtrace.py`, `cmpout.py`): Snapshot und Belegt bitgleich, mit 1e-12
  Toleranz 0 Felder. Mit numpy auf AVX-512: Ausgaben ≤ 4,4e-16 anders, über die Stunde nicht wachsend.
- **Messung:** `ab.sh 1200` 21,3 s gegen 48,3 s (0.31.1), −56 %; gegen 0.34.1 (eigene venv) 27,4 → 21,5 s (−22 %);
  gegen 0.30.0 (56,9 s) 2,67× – das Ziel von AUFTRAG 1.3 (≤ 22,8 s). 73 Rust-, 225 Python-Tests.

## 0.34.1 (Branch `rust-core`, nach Leons Prüfung von Phase 4, nicht live)

- **Referenz aus Python 16d148f** (`core/presence-core/fixtures/ld_reference.py`, `.json.gz`, Test
  `ld_reference.rs`): ein fester Fall in der Testwohnung (drei Hypothesen, Person mit Spur und Teil hinter der Tür,
  bekannte Person mit gemessener Amplitude, eine überall, die Unbekannten), zwei Runden Frames zweier Sensoren mit
  zensierten, Nullen, Stille, Zielen; logw, ka, Amplitudenstufen, Dichten, Echo-p, Gedächtnis, mus, prior_w,
  nobody, began auf 1e-12. Dazu: Likelihood gegen numerische Integration über die Verstärkung (1e-8, zensiert 1e-5),
  `ld_frame` (Heartbeat, IDLE, alles 0, Lücke), je Zeile der Tabelle aus Entwurf 2.0 ein Test
  (`tests/test_learning_order.py`; Pausen: `test_pause`).
- **`ld_weigh` neu gebaut:** Präfixbaum der Hypothesen (`Chains`: Knoten mit Vorgänger, Verhältnis, Summe danach),
  Caches nach Struktur (`(Blatt, Eintrag)`, Echo-Normierung je Knoten) statt Float-Bits, `LdContext` statt
  Closures, Schritte als Funktionen (`settle`, `tile_points`/`mixture_points` auf `&Densities`, `marginal`,
  `apply` auf `&mut Objects`, `echo_factors`), Objekt → Eintrag über eine Tabelle in `Holders`
  (`entry`, `holding`). `Standing::{Unmeasured, Measured { big }}` statt leerem `big`. Spuren bitgleich mit 0.34.0.
- **LD-Zustand je Sensor in `SensorState`** (`ld2410/sensor.rs`: Sammlung, Frames, Gedächtnis, Echoquellen, ab
  der Erzeugung); `ld_weigh` gibt immer `LdLearn` (mit `target_t`) zurück, ob gelernt wird, entscheidet die Hülle;
  die Bindung gibt ein Dict.
- **vlog/vexp in zwei Durchgängen** wie `kernels.py`, Ganzzahl ↔ Gleitkomma über 2^52 (ohne AVX-512):
  vektorisiert in der gelinkten `.so` nachgewiesen (ymm, `vdivpd`, `vroundpd`, `vpcmpgtq`); in der rlib nicht
  sichtbar, weil mit `lto = "fat"` erst beim Linken vektorisiert wird. Tests nahe 1 und an √2.
- **Puffer statt Allokationen:** `Scratch` für die Likelihood-Zeilen, Spalten-logsumexp ohne Vektor; Spuren-Spots
  je neuer Spur einmal, Faktoren je Satz wachender Sensoren einmal je Bewegung, Spots in place, `P H'` einmal je
  Update.
- **Eingaben geprüft:** `Params::validate`, `WorldExport::check` (Längen, Indizes, Räume), `World::from_export ->
  Result`; nicht endliche Zeiten/Energien sind „kein Frame“; `at_least` statt `max(NaN, …)`; negative Indizes aus
  Python ein ValueError.
- **REVIEW_phase3 4–7, 9:** Halter einmal je `take` (zählen, dann füllen); `release -> Obj`, `Hyp::set_person`;
  `attach -> Option<(MixtureId, f64)>` (bei −inf keine verwaiste Mischung); `with_away` für `weigh_no_track` und
  `reweigh` (NaN in einem Faktor verschwindet dort nicht mehr still als −inf), `log_add`/`logaddexp` dokumentiert;
  Kopien auf heißen Pfaden. Dazu `GroupIds`, toter Parameter `logf`, benannte Modellzahlen (`Profile`, `Burst`,
  `Slants`), `CellKind`, `Mixture::weigh_amplitude`.
- **Python:** die toten numba-LD-Kerne, `Stats.ratio`/`Likelihood`/`Echoes`/`log_censored` gelöscht, ihre Tests in
  Rust; `_ld_stats` und `Core.ld_stats` weg.
- **Messung:** `ab.sh 1200` 27,1 s gegen 47,8 s (0.31.1), −43 % (0.34.0: 29,8 s). Spuren aller drei Stunden
  bitgleich mit 0.34.0 (ein BLAS-Thread). 59 Rust-, 225 Python-Tests.

## 0.34.0 (Branch `rust-core`, Phase 4 des Rust-Kerns, nicht live)

- **LD2410C im Kern** (`ld2410/`, `filter/ld_weigh.rs`, `world/ld.rs`, ~1500 Zeilen): Sammeln der Frames
  (`Stats`, Ziel der LD2450 in Sicht), Erwartungsprofil, Gamma-Likelihood mit Zensur bei 99,5 (Tabellen und
  Interpolation wie `np.interp`), zusammengesetzte Likelihood dt/τ, Echoquellen (K Stufen × 36 Quellen),
  Verrechnung je Hypothese „Personen nacheinander“ mit Gedächtnis je Präfix und Cache nach dem, was die anderen
  beitragen, Gewichten je Objekt gemischt über die haltenden Hypothesen. `log`/`exp` als fdlibm-Nachbau
  (`vmath::fdlibm`, wie numpy), `lgamma` aus `libm`. Die Kacheln, Achse und Sicht je Sensor kommen mit dem
  Welt-Export.
- **Kein Schreiben mehr über den Spiegel:** `_ld_weigh` in der Hülle ruft nur `Core.ld_weigh` und gibt das
  Gelernte an den Hintergrund (bis Phase 8 Python); `_ld_points`, `_ld_apply`, `_ld_split`, `_ld_ratio` sind weg.
  Die alten numba-Kerne dazu bleiben bis Phase 9 (nur noch Tests nutzen sie).
- **Lernreihenfolge wie Entwurf 2.0:** Hintergrund und Anker lernen aus `b` und den Mitteln vom Anfang der
  Bewertung, `learn_echoes` mit der Rate vor `predict` (wie 0.31.1).
- **Form über Typ statt Länge** (REVIEW_phase3 Punkt 8): `StillFactors::{PerTile, PerLevel}` statt
  `still.len() != n`; `weigh_masses(f, idx, kind)`.
- Tests: Echoquellen beginnen, leben, enden; Gamma oben/zensiert; Verhältnis gegen Likelihood-Differenz;
  eine Person erklärt Energie, die Leere nicht; Energien begünstigen die Hypothese mit Person; Determinismus
  (51 Rust-Tests).
- **Messung** (`ab.sh 1200`, zweites Paar): 29,8 s gegen 47,5 s (0.31.1), −37 % (0.33.1: 33,9 s).
- **Spuren** (ein BLAS-Thread, gegen `ref_0311`): 7.10. max 3,9e-15. 10.10. bis 1125 s ≤ 7e-13, dann ein
  Verstärkungspunkt bei 1128 s (Gewichte 1,9e-10), den 0.33.1 und das in der Rundung gestörte 0.31.1 genauso
  zeigen; max 1,0e-4, 0 gekippte Raum-Sekunden (die Kippung bei 1305 s aus 0.33.1 tritt nicht mehr auf).
  8.10. bis 614 s ≤ 4e-8, bei 614,5 s dieselbe Entscheidung wie das gestörte 0.31.1 (gegen dieses max 9e-4,
  3 Raum-Sekunden); gegen die Referenz max 0,070, 6 Raum-Sekunden.

## 0.33.1 (Branch `rust-core`, nach Leons Prüfung von Phase 3, nicht live)

- **`presence_core::Tracker`** (`tracker.rs`) besitzt Welt, Filter und Sensorstand und führt die Schritte
  aus (`flush`, `event`, `track_events`, `end`, `branch_all`, …); die Bindung `Core` wandelt nur noch um. Die
  Sicht je Sensor und Kachel kommt mit dem Welt-Export, die Menge lebender Sensoren bildet der Kern selbst
  (`World::observed`, das Maximum wie `Tiling.observed`, bitgleich): `_flush` in Python rechnet keine
  Teilschritte mehr nach, nichts panict bei einer fehlenden Menge.
- **Spiegel nur noch geprüft geladen:** `Filter::load(MirrorState) -> Result` prüft Indizes, Längen und Arten
  vor jeder Änderung und am Ende `check()`; das gibt es nur mit dem Feature `mirror` (die Bindung), die
  Zugriffe `objects_mut`/`tracks_mut` nur in Tests, die Felder von `Objects` sind `pub(crate)`.
- **Caches:** `WalkerCache` statt Versionsvergleich, vom Kern selbst geleert; die Ausgaben der Hülle hängen an
  `epoch` (Bewegen und Ereignisse, die alle betreffen, wie `_version` in 0.31.1, ohne Setter). Ein Versuch mit
  `serial` als Schlüssel rechnete die Ausgaben öfter neu als 0.31.1 und wich bis 0,2 ab (8.10.) – verworfen.
- **Zahlen exakt lesen:** `serde_json` mit `float_roundtrip` (ohne las es `forget_rate` = 1/86400 um ein ulp
  anders als Python).
- **Spurvergleich mit einem BLAS-Thread:** `ref_0311` ist bitgleich mit 0.31.1 bei `OPENBLAS_NUM_THREADS=1`;
  die Übergabe zu 0.33.0 hatte 8.10. mit mehreren Threads gemessen (max 0,012, 0 gekippt). Richtig ist:
  8.10. 19 Uhr bis 93 s gleich, dann 1e-14, Entscheidung bei 434 s (die Paarung zweier fast leerer Personen
  in einer Hypothese mit w ≈ 5e-4), sichtbar ab 459 s, max 0,070, 5 Raum-Sekunden – so groß wie bei 0.31.1,
  nur in der Rundung gestört (gegen die Referenz max 0,070, 3 Raum-Sekunden). `trace.py` setzt jetzt einen
  Thread.
- Tests: Determinismus (zweimal dieselbe Folge, bitgleich), `people_state` lässt den Filter bitgleich (Person
  mit Spur und gemessenem Teil hinter der Tür), Masse beim Zurückgeben, ein nicht passender Spiegel ändert
  nichts, Modellzahlen exakt; die wirkungslose Prüfung in `tests.rs` ersetzt.

## 0.33.0 (Branch `rust-core`, Phase 3 des Rust-Kerns, nicht live)

- **Phase 3 des Entwurfs `docs/rust-core.md`: der Filterzustand in Rust.** Der Kern besitzt die Hypothesen und
  ihre Objekte (Gauß-Mischungen im Pool, Kacheldichten in einem Speicher, Objekt → Hypothesen als Halterliste,
  neu gebaut, wo sich Hypothesen ändern, danach Freigabe ohne Halter) und die laufenden Spuren. In Rust rechnen
  jetzt: Bewegen aller Personen (`flush`), Evidenz je Frame (keine Geisterspur, gemessen, verloren, gehalten,
  beendet, zensiert), Verzweigen samt Wiederfinden, Zusammenlegen (Paarung über Permutationen), Abschneiden,
  Normieren, Zurückgeben an die Unbekannten, Datenlücken (`leap`), `people_state` auf Kopien. Python bleibt Hülle:
  es ordnet den Frame (die Geisterkarte lernt zwischen den Schritten wie bisher), und LD2410C, Ausgaben und
  Zielkarte lesen bis zu ihren Phasen einen Spiegel der Hypothesen als Python-Objekte (Wegwerfschicht: `dump`,
  nach dem LD2410C `load`). Der Prototyp (`proto.rs`, `_flush_parts`) ist entfernt; jeder Aufruf geht über
  `filter._core`. Modell unverändert; `GAP_EXACT`, `LEAP`, `MAX_LEAPS`, `GIVE_UP` sind Parameter des Kerns (beim
  Bau übergeben). Die Fälle aus `test_hypotheses` (Zusammenlegen vor dem Abschneiden, Masse des Schnitts,
  Spurende bei fast verschwundener Geister-Alternative, Odds) und `test_gauss` (Gehen unabhängig von der
  Zerlegung, Richtung vergessen, Wände, Raten über die Komponente) sind Rust-Tests, dazu Halter/Freigabe,
  Paarung, Kalman-Update, `in_sight`, `from_tiles` und ein Ereignistest mit `Filter::check()` nach jedem
  Schritt. 10.10. 12–13 Uhr, erste 20 min (`ab.sh 1200`, zweites Paar): CPU 48,0 s (0.31.1) → 33,8 s (−30 %);
  davon die Spiegelschicht ≈ 1,8 s (py-spy: 4,7 % der Proben). Spuren gegen 0.31.1: 7.10. 03 Uhr bis 4e-15
  gleich; 8.10. 19 Uhr bis 459 s ≤ 3e-9, danach höchstens 0,012, keine gekippte Schwelle; 10.10. 12 Uhr bis
  1290 s ≤ 7e-10, dann die bekannte Kippstelle bei 1305 s (844 Raum-Sekunden) – dieselbe wie 0.31.1, nur in der
  Rundung gestört (`np.stack([w @ T for w in W])`); gegen diese Fassung kippt nichts (höchstens 2,4e-4).

## 0.32.1 (Branch `rust-core`, nicht live)

- **Ein Panic des Rust-Kerns ist ein Modellfehler wie ein `FloatingPointError`.** PyO3 meldet ihn als
  `BaseException`, die das `except Exception` der App (`_model_failed`) nicht fängt: die App wäre ausgestiegen.
  Jeder Aufruf in den Kern geht jetzt über `filter._core`, das ihn als `CoreError(RuntimeError)` weitergibt; die
  App protokolliert und beginnt neu (Test `test_a_panic_of_the_rust_core_is_a_model_failure`).

## 0.32.0 (Branch `rust-core`, Prototyp des Rust-Kerns, nicht live)

- **Phase 2 des Entwurfs `docs/rust-core.md`: Bewegen und Gewichten aller Personen in Rust.** Neuer
  Cargo-Workspace `tracker/core/` (`presence-core` ohne PyO3, `presence-py` als Modul `presence_core`, gebaut mit
  `maturin`). Was `Tracker._flush_parts` tat, rechnet der Kern: die Kacheldichten (Aufenthalte, Gehen als ein
  Matrixprodukt je Takt mit `faer` auf einem Thread, Regionen, Neuankömmlinge, Ablauf eines Datensatzes,
  Gewichten durch keine neue Spur) und die Gauß-Mischungen (IMM-Vorhersage mit Wänden und Türen, Gewichten an den
  Sigma-Punkten). Im Prototyp kopiert eine Klebeschicht die Python-Objekte je Flush hinein und zurück; ohne
  gebautes Modul (oder mit `PT_PY_FLUSH=1`) rechnet Python wie 0.31.1. Das Teil hinter der Tür wird mit
  exp(−Rate·dt) des letzten Teilschritts gewichtet statt mit der Potenz der Faktoren (gleich bis auf Rundung).
  Modell unverändert. 10.10. 12–13 Uhr, erste 20 min (`ab.sh 1200`, zweites Paar): CPU 47,8 s (0.31.1) →
  42,2 s (−12 %), gegen 0.30.0 56,9 → 42,3 s (−26 %). Spuren gegen 0.31.1: 7.10. 03 Uhr bis 4e-15 gleich; 10.10.
  12 Uhr und 8.10. 19 Uhr beginnen als Rundung (1e-14) und kippen nach 20 bzw. 8 min eine Hypothese – genauso
  wie 0.31.1 selbst, wenn nur das Gehen je Dichte statt als ein BLAS-Produkt gerechnet wird (dieselben 844
  Raum-Sekunden); gegen diese Fassung bleibt der Prototyp am 10.10. über die Stunde bis auf 7e-6 gleich.

## 0.31.1

- **Rechenzeit −16 % gegenüber 0.30.0, Ausgabe wie 0.31.0.** Die LD2410C-Likelihood interpoliert die
  Wahrscheinlichkeit zensierter Zellen (Energie 100) ohne Funktionsaufruf je Punkt: vorher 27 ns je Punkt und
  zensierter Zelle, jetzt 3. 10.10. 12–13 Uhr, erste 20 min: CPU 56,9 → 47,8 s; über die ganze Stunde gleich
  0.31.0 (|ΔP(belegt)| 0 auf 6 Stellen).

## 0.31.0

- **Rechenzeit −14 %, dasselbe Modell bis auf Rundung** (MODEL.md 10 „Rechenzeit: ein Aufruf je Gauß-Mischung
  und Schritt, vektorisiertes log/exp“). Die Bewegung einer Gauß-Mischung samt Wänden, Türen und der Gewichtung
  durch keine neue Spur ist ein kompilierter Aufruf je Schritt (`kernels.sync_step`, auch für `Gauss.predict`);
  ihre Teile „durch eine Tür“ werden mit den Personen ohne Spur in einem Matrixprodukt je Takt bewegt, und vor
  einer Messung, die diesen Teil ohnehin verwirft, gar nicht mehr. Die LD2410C-Likelihood ist ganz kompiliert
  (`kernels.ld_ratio`), mit einem eigenen vektorisierten log und exp (fdlibm, höchstens 1 ulp von numpy); Punkte
  ohne Masse werden nicht gerechnet. 10.10. 12–13 Uhr: CPU 56,7 → 48,9 s für die ersten 20 min; über die ganze
  Stunde höchstens 0,003 Unterschied in P(belegt), keine Schwelle gekippt.

## 0.30.0

- **Gemessener Blick des LD2410C in andere Räume** (MODEL.md 4.3 „Blick des LD2410C in einen anderen Raum“,
  10 „Blick des LD2410C je Raum“). Was ein LD2410C von einer Person in einem anderen Raum bekommt, ist nicht mehr
  die geometrische Sicht allein, sondern trägt je Paar „Sensor ← Raum“ einen gemessenen Faktor ≤ 1
  (`Model.ld_view`, gemessen 6.–10.10. mit `tools/ld2410/footprint.py`, zur Geometrie geschrumpft). Bewertet über
  die ganze Aufnahme: Wahrheit 0,48 / 3,28 min wie 0.29 (gewichtet 1:5 jeweils 16,9), leere Nacht und leeres Haus
  0; nach den Indizien von `proxy_seated.py` 4,4 min mehr Licht bei wahrscheinlich anwesender Person und kein
  zusätzliches Licht ohne Person (6,4 neu, 7,9 weg).

## 0.29.0

- **Aufgeräumt: toter Code, Umrechner und einmalige Analysen entfernt, Ausgabe zeichengleich.** Der Umrechner
  der Geisterkarte von 0.28 fällt wie angekündigt weg: Eine Karte von 0.27 gilt jetzt als unlesbar, die App
  beginnt eine neue (live ist sie längst umgerechnet). Weg sind Modellschalter, die immer gleich standen
  (`sight_spread`, `ghost_end_exact`, `ghost_end_unheld` samt dem Vergleichszweig von vor 0.10), ungenutzte
  Funktionen und Felder (`destination.vel_cell`, `Config.closed_region_at`, `door_of`, `visible_sensors`,
  `filtermodel.gamma_levels` u. a.; zusammen 77 Zeilen im Modell) und in `tools/ld2410/` die einmaligen
  Prüfungen des LD2410C-Entwurfs und der verworfenen Textur (12 Skripte, 1030 Zeilen; die in MODEL.md
  genannten bleiben). Oberfläche: Der Hinweis zu *besetzt* im Tab *Live* nennt die Regel „LD2450 des Raums“
  nur noch, wenn *Haltezeit eigener LD2450* über 0 ist; bis zur ersten Live-Nachricht stand der Balkon
  unter „Räume mit Sensor“ und darunter „Alle Räume haben einen Sensor.“, jetzt bleiben beide Listen bis
  dahin leer. Entitäten bleiben alle: *Personen*, *Bewegung* und *wird betreten* nutzt im Moment weder der
  Node-RED-Flow noch ein Dashboard, sie bleiben für andere Automationen.
- **Rechenzeit −32 %, Ergebnisse bitgleich** (MODEL.md 10 „Rechenzeit: weniger Aufrufe, bitgleich“). Die Zeit
  ging nicht an eine Stelle, sondern an Pythons und numpys Aufwand je Objekt und Schritt. Mehr kompilierte
  Kernel (Gewichten der Kacheldichten, Raten an den Sigma-Punkten, Sigma-Punkte an Wänden, Vorhersage der
  Gauß-Mischungen, LD2410C-Likelihood, -Profile und -Punkte), und was für alle Objekte eines Schritts gleich
  ist, wird einmal gerechnet. numpys exp/log und die BLAS-Produkte bleiben, so sind die Zahlen dieselben:
  vollste Stunde (10.10. 12–13 Uhr, ein Kern, Median aus drei) 386 → 263 s, jeder Schritt der Hypothesen
  zeichengleich; über alle Aufnahmen die Ausgabe zeichengleich mit 0.28.0.
- **Fehler: Speichern veränderte das Modell** (MODEL.md 5.3 „Neustart der App“). `Tracker.people_state`, alle
  10 min und beim Beenden gelesen, wandte beim Lesen einer gemessenen LD2410C-Amplitude deren aufgeschobene
  Mischung (`Hidden.amp`) in den Dichten des laufenden Modells an. Mathematisch gleich, aber anders gerundet
  und mit anderem Zeitpunkt für `MEASURED_CUT`: Der Live-Lauf ging nach jedem Speichern anders weiter als ein
  Nachspiel derselben Frames. Jetzt rechnet `people_state` auf Kopien; das gespeicherte `people.json` ist
  dasselbe. Nachspiele ändern sich nicht (sie speichern nicht).
- **Fehler: Ein dauernd scheiterndes Modell war kurz wieder „online“** (DOCS.md *Verfügbarkeit*). Nach drei
  Fehlern ruht das Modell eine Minute und soll erst nach einer Minute fehlerfreien Laufens wieder verfügbar
  sein. Die Ruhe zählte aber als Laufen: Brach jeder Frame eines Sensors das Modell, während die Takte
  dazwischen liefen, bekam Home Assistant nach jeder Ruhe für einen Moment „online“ mit frischen Zuständen,
  und das Modell wurde dreimal neu gebaut. Jetzt zählt die Minute ab dem Ende der Ruhe.
- **Fehler: Eine abgeschnittene Fehlermeldung legte die Liste lahm.** Ein Bericht wurde direkt in seine Datei
  geschrieben; las die Liste ihn halb geschrieben, oder blieb er nach einem Stromausfall abgeschnitten, brach
  gzip mit `EOFError` ab und `GET /api/reports` gab 500 – keine Meldung war mehr zu sehen oder zu löschen.
  Jetzt wird ein Bericht ganz oder gar nicht geschrieben (wie das Gelernte), und eine kaputte Datei fehlt nur
  selbst in der Liste. Zwei Meldungen derselben Sekunde, desselben Raums und derselben Art überschrieben
  einander; die zweite bekommt jetzt `-2` im Namen.
- **Fehler: Beim Beenden blieben die Entitäten „online“** (DOCS.md *Verfügbarkeit*). Die App sagte „offline“
  erst, nachdem die MQTT-Verbindung schon sauber geschlossen war – vergeblich, und ein sauberer Abschied
  verwirft den letzten Willen. Bei jedem Update oder Neustart der App zeigte Home Assistant so die letzten
  Zustände als aktuell, bis sie wieder lief. Jetzt geht „offline“ vor dem Abschied hinaus.
- **Fehler: Ein Entwicklungslauf mit `--no-publish` konnte die Live-Entitäten abschalten.** Auch ohne zu
  veröffentlichen meldete er sich mit dem letzten Willen „offline“ auf dem Verfügbarkeitstopic der App an;
  starb er (Rechner im Ruhezustand, `kill -9`, WLAN weg), setzte der Broker die Entitäten der Live-App auf
  *nicht verfügbar*, und die Live-App, die ihre Verfügbarkeit nur bei einer Änderung sendet, sagte nie
  wieder „online“. Ohne Veröffentlichen gibt es jetzt keinen letzten Willen.
- Einstellungen: Der Hinweis sagte, nur die Schwellen „Ziel“ starteten das Modell nicht neu; das gilt für alles
  unter „Ausgabe“ (Kosten, Haltezeit eigener LD2450, Vorausschau) und die Haltezeit des LD2410C.
- Live, Räume ohne Sensor: Die Prozentzahl hieß „Wahrscheinlichkeit, dass diese Person dort ist, je Person“;
  seit 0.8 ist es eine Zahl je Gruppe, die Wahrscheinlichkeit, dass jemand dort ist. So heißt sie jetzt.
- **Fehler: Ein abgelehnter Grundriss vergaß die Personen.** Scheiterte das Modell an einer neuen Konfiguration,
  nachdem es schon die neuen Kacheln gebaut hatte, galt zwar die alte weiter, aber die Personen wurden aus dem
  halb umgebauten Modell gespeichert: Das scheiterte oder schrieb den Fingerabdruck der neuen Kacheln, und das
  alte Modell wusste danach nichts über die Personen. Jetzt wird der Zustand von vor der Änderung gespeichert.
- **Fehler: Zwei gleichzeitig gesendete Meldungen überschrieben einander** (ein Doppelklick). Der Name einer
  Meldung wird jetzt beim Wählen belegt (`O_EXCL`), nicht erst, wenn der Thread sie schreibt.
- `ld2410.json`: Ein Teil ohne seine Zeit (`num` ohne `den`, nur in einer von Hand verdorbenen Datei) wird
  beim Laden verworfen; bisher scheiterte das Modell an jedem LD2410C-Frame dieses Sensors.
- Fehlermeldung: `minutes_ago` muss eine endliche Zahl sein; „nan“ kam bisher durch und schrieb NaN in den Kopf.
- **Sensor „online“ heißt in der Web-Oberfläche dasselbe wie in Home Assistant** (DOCS.md *Verbindung*): ein
  Frame in den letzten 30 s (`roomseen.SILENT_AFTER`). Bisher sagte die Oberfläche nach 15 s „offline“ und
  nach „Neu beginnen“ für jeden Sensor bis zu seinem nächsten Frame, Home Assistant erst nach 30 s.

## 0.28.0

- **Der LD2410C-Hintergrund kehrt zu seinem eigenen Niveau zurück, nicht zum Prior** (MODEL.md 4.3 „Anker“,
  10 „Licht im leeren Schlafzimmer“). Bis 0.27 zog der feste Prior (ruhig 5, Gewicht 10 min) den gelernten
  Hintergrund in Stunden ohne Lernen nach unten, im Schlafzimmer von leer 9 auf 5–7; den Überschuss im leeren
  Raum erklärten unbekannte Personen, deren Intensität ohne Messung wuchs, und ohne die Regel „eigener LD2450 in
  10 s“ (live seit 11.10. aus) ging das Licht im leeren Schlafzimmer an: 9.10. 08:25 und 16:58–17:53, 10.10.
  12:18–14:35. Jetzt vergisst der Hintergrund zu einem Anker je Sensor, demselben Online-EM über einen Tag, nur
  aus Frames nach 10 min ohne Ziel des LD2450 im Gehäuse. Nachgespielt über alle Aufnahmen (6.–10.10., mit
  Lernpausen für Saugroboter und Geräte, ohne die LD2450-Regel): Wahrheit 0,48 / 3,28 = 3,77 min wie 0.27.0,
  leere Nacht und leeres Haus 0; über die ganze Aufnahme (Indizien aus Telefonen und Sensoren,
  `tools/proxy_seated.py`) 546 min weniger Licht im leeren Schlafzimmer, 2,3 min mehr dunkel mit
  wahrscheinlich Person, 26 min unklar (Schlafzimmer 10.10. 09:40); Rechenzeit +0,5 %. Nach dem Update
  beginnt der Anker leer und lernt sich über Stunden ein.
- **Gelerntes bleibt bei neuer Kalibrierung** (MODEL.md 4.2, 4.3, 10 „Gelerntes bleibt bei neuer Kalibrierung“).
  Bis 0.27 begann die Geisterkarte aller Sensoren neu, sobald sich die Lage eines Sensors um mehr als 5 cm, 2°
  oder 2 % Maßstab änderte, und der LD2410C-Hintergrund des Sensors bei jeder Änderung; am 8.10. um 10:07 warf
  die Kalibrierung des Arbeitszimmers so 285 gelernte Geister aus 35 h weg. Jetzt liegt jeder Teil der Karte im
  Rahmen seines Sensors und wandert mit einer neuen Richtung oder einem neuen Maßstab mit; der Hintergrund
  bleibt (seine Ringe sind Entfernungen vom Sensor). Neu beginnt nur, was zu einem anders montierten Sensor
  gehört (Ort oder Höhe mehr als 0,5 m, Richtung mehr als 30° geändert), die anderen behalten ihres.
  Zielkarte und Kalibrierdaten blieben schon. Nachgespielt mit dem Wechsel vom 8.10.: Wahrheit 3,67 → 3,77 min
  (6 s auf dem Sofa 8.10. 19:04, gleich wie ohne Wechsel), leere Nacht und leeres Haus 0; ohne Wechsel gleich
  wie 0.27.0. Rechenzeit ohne Wechsel −3 %, mit Wechsel +4,5 % gegen zurückgesetzt (eine leere Karte rechnet
  sich schneller). Eine gespeicherte Geisterkarte wird beim ersten Start einmal in die Rahmen der
  Sensoren umgerechnet; der Umrechner fällt im Release danach weg.

## 0.27.0

Mit korrigierter Wahrheit für den 8.10. abends (MODEL.md 10 „0.27: korrigierte Wahrheit 8.10.“): Ein Gast war
nur am Nachmittag da, auf dem Balkon war niemand, und die „Person am Esstisch“ 19:04–19:10 war ein Geist. Alle
Zahlen hier auf den vier Wahrheiten ohne Ausnahme, Licht aus dem veröffentlichten *besetzt* mit 2 min Nachlauf.

- **Wer sitzt, bleibt, wenn seine Spur endet** (MODEL.md 4.3, 5.4, 10 „Sitzende im Bad und auf dem Sofa“). Endete
  die Spur einer Sitzenden, rechneten die Kacheln mit der Amplitude g = 1 einer Stehenden; wer weniger zurückwirft
  (im Bad an der Westwand g 0,35, auf dem Sofa 0,58, gemessen), wurde vom LD2410C in Sekunden widerlegt. Jetzt
  trägt die Person ohne Spur die Amplitude weiter, die ihre Spur gemessen hat (je Erkennbarkeit und Kachel; neu
  gezogen nur mit der Erkennbarkeit oder einem neuen Aufenthalt); was ohne Messung beginnt, hat weiter g = 1.
  Gegen 0.26.1 (beide noch mit der Balkon-Regel): Bad veröffentlicht „frei“ mit Person 5,8 → 0 min, Licht im
  leeren Raum 10,1 min wie 0.26.1, leere Nacht 0, verloren in Sicht 172 → 136 min, Rechenzeit +8 %. Schlechter:
  8.10. 19:04–19:10 im Wohnzimmer („frei“ 3,8 → 4,65 min, dunkel mit Person 0 → 0,3 min): Der Geist am Esstisch
  bekommt den Eintrag der Person auf dem Sofa. 0.26.1 hatte das nur zufällig richtig: Ein Eintrag auf dem
  Balkon, hinter dem niemand stand, war als Ersatz übrig.
- **Der LD2410C-Hintergrund vergisst mit 0,7 h statt 6 h** (MODEL.md 4.3): gemessen, wie schnell er wandert (im
  Flur springt bewegt Ring 4 morgens von 1,7 auf 5,1; lokales Niveau-Modell über alle Sensoren). Mit 6 h hing er
  stundenlang beim Nachtwert, und eine Person mit gemessener kleiner Amplitude blieb 8.10. 06:55 zwölf Minuten im
  Flur der leeren Wohnung. Allein ändert es Licht und Meldungen nicht (verloren 130 → 114 min, gemessen auf der
  alten Wahrheit; auf der korrigierten nur zusammen mit dem Punkt oben).
- Geprüft, nicht übernommen: **der volle Amplituden-Prior auf den Kacheln** (Licht im leeren Raum 4,1 → 31,8 min:
  eine Person mit kleinem g erklärt jeden kleinen Fehler des Hintergrunds), dazu **ein Hintergrund-Prior nach der
  Streuung zwischen den Sensoren** (8,0 min) und **ein Ablauf nach Prüfbarkeit** (Existenz läuft ab, soweit LD2450
  und LD2410C eine Stehende mit ihrer Erkennbarkeit und Amplitude ungeprüft lassen; 9,4 min; alle drei auf der
  alten Wahrheit).
- **Balkon-Regel zurückgenommen** (MODEL.md 5.5, 10 „0.27“): Einträge in geschlossenen Bereichen ohne Sensor
  (dem Balkon) laufen wieder ab wie überall, wo kein Sensor prüft (2 min). 0.26.0 hielt sie dort, begründet mit
  einem Gast, der 8.10. um 19:04 nach 15 min vom Balkon an den Esstisch zurückkam. Den gab es nicht; die
  Wahrheit war falsch. Was die Regel hielt, waren Einträge aus Geistern an der Balkontür (ein wiederkehrender
  Spiegel- oder Winkelgeist des Esszimmer-LD2450), die später als Personen „zurückkamen“. Ohne sie (gegen
  den Punkt oben mit Regel): Licht im leeren Raum 10,1 → 7,6 min (ganz das Esszimmer, 8,55 → 6,0 min: das
  Fenster an der Balkontür 7.10. 22:22, 2,5 → 0 min), dunkel mit Person 0,3 wie vorher, `report_eval`
  veröffentlicht fälschlich an / aus 3,19 / 0,76 → 2,54 / 0,76, Rechenzeit −6 %.
- **0.27.0 gegen 0.26.1:** Licht im leeren Raum / dunkel mit Person 10,1 / 0,0 → 7,6 / 0,3 min, Bad „frei“
  mit Person 5,8 → 0 min, leere Nacht 0, Rechenzeit +1,4 %. Was bleibt, ist der Geist am Esstisch 8.10.
  19:04–19:10 (LD2450-Spur und LD2410C-Energie ohne Person, Ursache unbekannt; MODEL.md 9).

## 0.26.1

Hotfix nach dem Ausfall zweier Sensor-Boards (9.10. 15:04, Wohn- und Esszimmer ohne Frames):

- **Ein Raum, dessen eigener Sensor schweigt, ist in Home Assistant *nicht verfügbar*, nicht frei**
  (MODEL.md 6 „Belegt“). Der Filter nimmt die Sicht eines stummen Sensors als unbeobachtet (4.4, 5.5); der
  Eintrag der Person auf dem Sofa lief nach etwa 2 min ab, das Wohnzimmer war 1,5 h „besetzt: aus“, und
  Node-RED schaltete das Licht aus, während dort jemand saß. Jetzt sind alle Entitäten eines Raums
  (*besetzt*, *Personen*, *Bewegung*, *wird betreten*, *Ziel*) nicht verfügbar, sobald jeder eingeschaltete,
  platzierte Sensor des Raums mehr als 30 s keinen Frame gesendet hat (kurze WLAN-Lücken von
  6–20 s kamen in 64 h Aufnahmen 88-mal vor und lassen den Raum verfügbar); mit dem nächsten Frame wieder verfügbar, mit dem aktuellen Zustand. Ohne Daten sagt die App
  „weiß ich nicht“; der Präsenzlicht-Flow schaltet bei unbekanntem *besetzt* nichts, das Licht bleibt, wie es
  ist. Umgesetzt mit zwei Verfügbarkeits-Topics je Entität (`presence-tracker/status` und
  `presence-tracker/zone/<raum>/availability`, `availability_mode: all`); das Topic des Raums geht beibehalten
  nur bei einer Änderung und beim (Wieder-)Verbinden hinaus und wird gelöscht, wenn der Raum keinen eigenen
  Sensor mehr hat oder gelöscht ist. Ein Sensor, der seit dem Start nichts gesendet hat, zählt erst 30 s nach
  dem Start des Modells als stumm (kein Flackern beim Start). Räume ohne eigenen Sensor, Bereiche und das
  Haus: unverändert. Der Filter selbst: unverändert. In der Raumliste steht *nicht verfügbar*.
- **Kopfzeile: alle Sensoren in Gebrauch.** *n/m Sensoren* zählte nur Sensoren, von denen seit dem Start des
  Modells Daten kamen: Mit zwei ausgefallenen Boards stand dort 5/5. Jetzt ist m die Zahl der
  eingeschalteten und platzierten Sensoren (hier 5/7), n die mit frischen Frames; der Punkt ist nur grün, wenn
  alle online sind.
- **Meldungen löschen:** In der Liste unter *Fehler melden* hat jede Meldung *Löschen* (mit Rückfrage), etwa
  für Meldungen, die nur kamen, weil Sensoren ausgefallen waren (`DELETE /api/reports/<name>`; nur
  vorhandene Meldungen unter `/data/reports`, kein Pfad).

## 0.26.0

*Nachtrag 10.10.:* Die Begründung der Balkon-Regel unten (der Gast, der 8.10. vom Balkon zurückkam) beruhte auf
einer falschen Wahrheit: Am 8.10. war niemand auf dem Balkon, und die „Person am Esstisch“ 19:04–19:10 war ein
Geist. Ihr gemessener Nutzen kam aus genau diesem Fenster. 0.27.0 nimmt die Regel zurück.

Kandidat 0.26 (9.10.; MODEL.md 10 „Kandidat 0.26“):

- **Kein Absturz mehr am Ende einer Spur.** `Tracker._end` nahm die Geisterkarte aus den Odds von P(Geist) mit
  `exp(…)`; seit keine laufende Spur eine Alternative verliert, kann P(Geist) bei 10⁻³⁰⁰ liegen, und das lief
  über (`OverflowError`; in der App startet das Modell danach neu). Jetzt in einer Form ohne Überlauf, ebenso
  die Existenz einer Person ohne Spur (`Hidden.weigh`).
- **In geschlossenen Bereichen ohne Sensor läuft ein Eintrag nicht mehr ab** (der Balkon; MODEL.md 5.5): Wer
  dort ist, kommt nur durch dessen Tür zurück. Bis 0.25 lief er nach Minuten ab; der Gast, der 8.10. nach 15
  min vom Balkon zurückkam, war „niemand“, und seine Spur bekam die Person auf dem Sofa. Über die vier
  Wahrheiten (veröffentlicht, Licht mit 2 min Nachlauf): dunkel mit Person 0,4 → 0,1 min, Licht im leeren Raum
  8,8 min wie vorher; Filter allein dunkel 5,0 → 0,1 min; `report_eval` veröffentlicht fälschlich an / aus
  2,13 / 1,73 → 2,15 / 1,02; leere Nacht 0 min; Rechenzeit gleich. Hinter der Balkontür bleiben dafür
  morgens Einträge länger (8.10. 7 Uhr 0,8 erwartete Personen im Haus bei null Telefonen); Licht hat das keins.
- Gegen 0.25.1 (6,3 / 0,6 = 6,9 min) ist das 8,8 / 0,1 = 8,9 min: dunkel besser, Licht im leeren Raum um das
  eine Fenster 7.10. 22:22 im Esszimmer schlechter (2,5 min; die Sensoren sehen dort etwas an und hinter der
  Balkontür, MODEL.md 9 Punkt 3). Geprüft und nicht übernommen: eine kleine Geburtsrate an neuen Spuren (eine
  zweite Person im Arbeitszimmer, 12,4 min), die Amplitude je Aufenthalt auf den Kacheln (Bad und Bett gehalten,
  aber stundenlang jemand im leeren Schlafzimmer, 17,5 min), ein strengeres Tor für Ziele im Balkon (ohne
  Wirkung).
- Werkzeuge: `tools/baseline_eval.py --patch`, `tools/report_eval.py --json` (Filter allein und veröffentlicht
  aus einem Nachspiel).

Meldungen 8.10. abends (Licht im Bad und im Wohnzimmer aus, während dort jemand saß; MODEL.md 10
„Meldungen 8.10. abends“):

- **LD2410C: Wer nicht in seinem Blick ist, wird nicht gewogen – auch nicht als Person mit Spur.** Bis 0.25
  zählte jede Komponente einer Person mit Spur als im Blick jedes LD2410C (mit null Energie) und bekam
  dort `P(keine Echoquelle)`, ihr Teil „durch eine Tür“ dagegen 1. War irgendwo eine Echoquelle
  wahrscheinlich, wanderten so alle Personen mit Spur im Haus Block für Block durch eine Tür hinaus (8.10.
  18:47:44–18:48:00 die Person im Bad von 10⁻²⁸ auf 1 „außer Haus“, 18:48:19 die auf dem Sofa); ihre
  weiteren Spuren wurden Geister, ihr Eintrag lief draußen ab. Jetzt wie bei Kacheln: nur, was dem
  LD2410C mehr als `FLOOR` gibt, ist in seinem Blick.
- **Abschneiden: Keine laufende Spur verliert eine ihrer Alternativen ganz** (Geist / Person, MODEL.md
  5.1). Bis 0.25 blieb eine Spur, deren Alternative „Person“ bei der Geburt unter die Schwelle fiel, für
  immer ein Geist, wie lange sie auch gemessen wurde (Schlafzimmer 8.10. 00:29–06:14, Schreibtisch 7.10.
  08:59–09:49): Sagten alle Hypothesen „Geist“, kürzte sich die Lebensdauer der Geister heraus. Jetzt
  bleibt die stärkste verworfene Hypothese, die das andere sagt; die Messungen entscheiden.
- Beide zusammen mit 0.25.1 nachgespielt (vier Wahrheiten, `report_eval` veröffentlicht, fälschlich an / aus):
  1,65 / 2,51 → 2,13 / 1,73; Licht im leeren Raum 6,3 → 8,8 min, dunkel mit Person 0,6 → 0,4 min; verloren
  in Sicht (26 h) 297,4 → 129,6 min; leere Nacht 0 min; 8.10. 21:30–21:40 Arbeitszimmer nie besetzt;
  Rechenzeit gleich. Das Mehr an „an“ ist ein Fenster (7.10. 22:22, Geist an der Balkontür als Person;
  MODEL.md 10 „Meldungen 8.10. abends“).

## 0.25.1

- **Nicht durch die Wand** (MODEL.md 4.1, 10 „Durch die Wand ins Arbeitszimmer“): Eine Messung an einer Wand
  setzte die Person auf die andere Seite. 8.10. 21:35: Die Spur des Bad-LD2450 sprang zur Wand des
  Arbeitszimmers (2 cm davor, danach bis 0,37 m dahinter), das Kalman-Update zog die Person 0,3 m hinter die
  Wand, das Licht im Arbeitszimmer ging an (`quelle: filter`), bis 21:36:48 noch dreimal kurz. Jetzt wird die
  Position einer Person nach jeder Messung auf das beschnitten, was der messende Sensor sieht (nichts hinter
  einer Wand). Nachgespielt 21:30–21:40: Arbeitszimmer nie besetzt (vorher 8 s), P höchstens 0,07. Ziele an
  der Wand, an der der Sensor hängt, bleiben Messungen (0.20.0); die Wahrheiten unverändert, die leere Nacht
  0 min.

## 0.25.0

- **Besetzt: Filter oder der LD2450 des Raums** (MODEL.md 6 „Belegt“, `roomseen.py`, `Tracker.occupancy`): Ein
  Raum ist auch besetzt, solange sein eigener LD2450 in den letzten 10 s ein gemessenes Ziel im Raum hatte
  (nicht gehalten, nicht hinter einer Wand, nicht am Sensor; nur der Sensor, der wie der Raum heißt). Live
  gingen Lichter aus, während Personen in Räumen saßen, die der LD2450 klar sah: Der Filter verliert solche
  Personen (MODEL.md 9). Auf den vier Wahrheiten mit 2 min Nachlauf, in der App nachgespielt: dunkel mit Person 25,6 → 0,6 min,
  Licht im leeren Raum 4,3 → 6,3 min; leere Nacht 0 min. Nur eine Ausgaberegel: Zustand, Gewichte und
  Lernen des Filters bleiben gleich, ebenso `probability`, *wird betreten* und *Ziel*.
  - Attribut `quelle` an `binary_sensor.presence_<raum>_occupancy`: `filter`, `ld2450` oder `beide`
    (leer, solange frei); in der Live-Ansicht neben *besetzt*.
  - Personenzahl mindestens 1 (Raum und Haus), solange die Regel allein einen Raum besetzt.
  - Einstellung *Haltezeit eigener LD2450* (`seen_hold`, 0–120 s, Vorgabe 10 s; 0 = nur der Filter), eine
    Ausgabe-Einstellung: Ändern startet das Modell nicht neu.
  - Das Schlafzimmer ist damit nachts besetzt, wenn Schlafende gemessen werden: Das Licht dort braucht eine
    Nacht-Regel in Node-RED.
- Werkzeuge: `tools/baseline_eval.py` (einfache Raumbelegung gegen den Filter; zeichnet das veröffentlichte
  *besetzt* als `app` auf), `report_eval.py --published` bewertet das veröffentlichte *besetzt* statt P > c.

## 0.24.0

- **Schalter „Lernen pausieren“** (`switch.presence_lernen_pausieren`, MODEL.md 10 „Hintergrundaktivität“):
  Solange er an ist, lernt die App nichts – Geisterkarte, Hintergrund und Echorate der LD2410C, Zielkarte,
  Kalibrierdaten –, das Verfolgen und die Lichter laufen wie immer. Für den Saugroboter, Besuch, eine
  Putzkraft, eine Feier; Node-RED schaltet ihn (Saugroboter nicht angedockt, bis 2 min nach dem Andocken).
  Der Befehl liegt im Broker (*retained*), die App übernimmt ihn auch, wenn sie gerade nicht lief; ihr Zustand steht in
  `/data/pause.json`. Live-Ansicht: *Lernen pausiert*. Fehlermeldungen enthalten die Pausen, das
  Nachspiel pausiert an denselben Stellen (bitgleich).
- Werkzeuge: `tools/record.py` zeichnet den Schalter mit auf; `report_eval.py`, `entries.py`, `replay.py`,
  `calibrate_offline.py`, `ghostmap.py` und `ld2410/phantom.py` folgen ihm und den Intervallen aus
  `--pauses` (privat, für die Tage davor aus dem Verlauf des Saugroboters: `tools/vacuum_history.py`) und
  bewerten dort nichts.

## 0.23.0

Review des Algorithmus 1.3 (MODEL.md 5.5, 10 „Tote Winkel und Existenz“):

- **Kein Abklingen der Existenz mehr in Sicht.** Bis 0.22 verlor jede bekannte Person ohne Spur ihre
  Existenz mit `e^(−t/2 min)`, wo sie auch war, auch mit r = 1 und in voller Sicht (ein Sterbeprozess
  gegen Vorgabe 1.3). Jetzt entscheiden dort die Messungen allein: Eine Sitzende, die keine Messung stützt
  und keine widerlegt, bleibt; zwei, die der LD2450 als ein Ziel sieht, bleiben zwei.
- **Was keine Messung prüfen kann, läuft ab** (`unseen_life` = 2 min, `Tracker._expire`): der Teil eines
  Eintrags in toten Winkeln, in Bereichen ohne Sensor und außer Haus, in einer Kachel nach dem Anteil,
  den der beste lebende Sensor nicht sieht (`Tiling.observed`: LD2450-Sicht oder LD2410C-Strahl ×
  Sichtlinie). Eine ehrlich gekennzeichnete Annahme über Einträge ohne Beleg (Existenz-Kette nach
  Musicki & Evans 2005, nur wo die Erkennbarkeit nichts ausrichten kann), kein Teil der Bewegung. Fällt
  ein Sensor aus, prüft er nichts mehr; über eine Datenlücke zählen die Sensoren, die da sind.
- Geprüft und verworfen: tote Winkel als Pseudo-Bereiche mit eigenem Aufenthalt (Empfehlung des
  Reviews). Ein Aufenthalt senkt r nur bei r < 1; Einträge mit r ≈ 1 (aus Identitätsteilungen beim
  Zusammenlegen) hielt er beliebig lange, und Gehende an der Grenze blieben einen ganzen Aufenthalt
  darin: Küche 7.10. 11:39–12:55 0,98 / 0,97 / 0,82, Licht fälschlich an 3,14 von 13. Ohne Abklingen und
  ohne Ablauf 0,80 / 0,55 / 0,29 (1,16 von 13).
- Zusammen gegen 0.22.0 (12 Hypothesen; fälschlich an / aus): Abend 0 / 0,13 → 0 / 0,01 von 43 / 11, bis
  6.10. 0 / 1,50 → 0 / 1,00 von 41 / 15 (Arbeitszimmer 21:36 richtig), 7.10. früh 0 / 0 → 0 / 0 von 13 / 7,
  Küche 0,05 / 0,02 / 0,01 wie bisher; mit 16 Hypothesen 0 / 0,01, 0 / 1,00, 0,11 / 0 (wie 0.22.0). Zwei im
  Bett P(=2) 1,00 / 0,32 / 0,87 → 1,00 / 0,94 / 1,00. Leere Nacht 0 min. Im Haus je Stunde gegen die Telefone (6.10. 19:28 – 8.10. 06:00) im Mittel 1,00 → 0,98
  daneben, keine Anhäufung. Rechenzeit vollste Stunde 51,3 → 52,7 s (+2,7 %).
- Gespeicherte Personen (`people.json`) passen weiter zu den Kacheln.

## 0.22.0

Review des Algorithmus, P0 (MODEL.md 10, „Review 8.10.“):

- **Erst zusammenlegen, dann abschneiden** (MODEL.md 5.1): Die Alternativen einer neuen Spur, die
  über die laufenden Spuren dasselbe sagen („sie gehört u₁“, „u₂“, „einer neuen Person“), werden nach
  ihrer Summe gewogen, nicht einzeln unter 10⁻⁷ des stärksten verworfen. Abgeschnitten wird nach der
  Masse (die verworfenen zusammen höchstens 10⁻⁷, Vo et al. 2017), höchstens 12 bleiben; die
  verworfene Masse zählt in der Log-Evidenz und wird ausgewiesen (`Tracker.loglik_cut`,
  `report_eval.py`: „dropped hypotheses“).
- **Erkennbarkeit κ als Posterior-Gitter** (MODEL.md 4.1): 5 Zellen in log κ mit der Masse von
  Gamma(1) statt 3 Gauß-Laguerre-Punkten, deren unterste Stufe (0,42) langes ungesehenes Sitzen um
  Größenordnungen zu unwahrscheinlich machte (nach 10 min 200-mal, nach 20 min 3·10⁵-mal; jetzt
  höchstens 25 % neben der Gamma-Verteilung, gerechnet).
- **κ und LD2410C-Amplitude g gemeinsam** (MODEL.md 4.3): Gauß-Copula mit ρ = 0,44 aus der gemessenen
  Rangkorrelation; eine schlecht zurückwerfende Haltung zählt nicht mehr doppelt gegen eine Sitzende.
  Die Gauß-Mischung führt die gemeinsame Verteilung; ein Teil ohne LD2410C-Messung mischte bisher mit
  gleich wahrscheinlichen Amplitudenzellen statt mit dem Prior.
- MODEL.md richtiggestellt: Kopfzeile (Code 0.10.0), Evidenz „glatt in den Parametern“ (7), gekappte
  Energien nicht über den Pegel integriert (4.3), der Ausgang der Zielkarte aus der schwersten
  Hypothese (6).
- Zusammen gegen 0.21.0 (12 Hypothesen): Licht fälschlich an überall 0, aus Abend 0,14 → 0,13 von 11,
  bis 6.10. 1,50 von 15, 7.10. früh 0; mit 16 Hypothesen fälschlich an 7.10. früh 0,14 → 0,11 von 13.
  Leere Nacht 0 min. Log-Evidenz +100 / −144 / −50 (16 Hypothesen +42 / −117 / −62): nicht sicher
  (MODEL.md 5.1). Die Schlafzimmernacht 7./8.10. bleibt (die harten Zählungen der Geisterkarte kommen
  von der Echoquelle, nicht vom Abschneiden). Rechenzeit: vollste Stunde 51,9 → 50,6 s (−2,5 %), über die
  ganzen Nachspiele +6 % (16 Hypothesen +10 %).
- Gespeicherte Personen (`people.json`) passen nach dem Update nicht mehr zu den Kacheln (andere
  Stufen von κ): Die App beginnt einmal ohne Wissen über die Personen.

## 0.21.0

- **Lernschleifen abgesichert** (Review des Algorithmus 3.2/3.3, MODEL.md 4.3, 10 „Lernschleifen“): Der
  LD2410C-Hintergrund lernte Personen, die der Filter nicht oder falsch hielt (Schlafzimmer 7./8.10.
  nachts Ring 2 7,7 → 29 bei leer 7,8; Arbeitszimmer 6.10. abends Ring 5–6 am Platz der zweiten Person
  +30 %, Ring 2 an Leons Schreibtisch 2,0 statt 6,5). Jetzt lernt er nur aus der Zeit, in der der LD2450
  im selben Gehäuse nichts sieht, nicht während eine Echoquelle die Energie hält, und eine gekappte
  Energie zählt mit dem, was sie war (E-Schritt zensierter Daten): Schlafzimmer höchstens 12,1,
  Arbeitszimmer 6.10. abends höchstens 17 % neben dem leeren Raum (vorher −69 % / +34 %). Die Echorate lernt nur, wo niemand im Blick ist, und
  ohne ihre eigene Rate (wie die Geisterkarte): in der Simulation einer Sitzenden 0,34 statt 0,73 je
  Stunde. Licht auf allen Sätzen gleich (bis 6.10. aus 1,50 von 15; 7.10. früh 0; 7.10. abends 0,14 von
  11; an überall 0), Log-Evidenz +2116 / +1553 / +616, leere Nacht 0 min. Lernen nur, wo der Filter
  niemanden sieht, wurde verworfen: Der neue Schlafzimmersensor lernte so seinen Pegel nie, und eine
  erfundene Person hielt das leere Schlafzimmer vier Stunden „besetzt“. Offen: Wen der Filter verliert
  und der LD2450 nicht sieht, lernt der Hintergrund weiter (MODEL.md 9).

## 0.20.1

- **Rechenzeit** (Performance-Review von 0.18.0, MODEL.md 10): zwei weitere kompilierte Kernel, für
  die zensierten Zellen der LD2410C-Likelihood und die Exponenten von `Tiling.gauss_points`. Die
  Ergebnisse sind bitgleich (`report_eval --trace` zeichengleich, leere Nacht 0 min); die vollste Stunde
  (7.10. 17 Uhr, 7 Sensoren) kostet 197 statt 218 s CPU (−9,5 %), die ruhige gleich viel.
- **Keine Stillstände mehr in der Schleife**, die das Modell taktet: Ein Fehlerbericht wird im Thread
  gebaut und mit gzip-Stufe 6 gepackt (bisher 0,5 s Stillstand hier, Sekunden auf Home Assistant), der
  Kalibrierstatus der Live-Ansicht im Thread gezählt (bisher 24 ms alle 30 s), die Zielkarte beim
  Speichern im Thread komprimiert, `calibration.npz` unkomprimiert gespeichert (6 statt 1,5 MB für einen
  Tag, 4 statt 80 ms). Ältere komprimierte Dateien werden weiter gelesen.

## 0.20.0

- **Personen an der Wand, an der der Sensor hängt** (Meldungen 7.10. abends, Bad und Schlafzimmer):
  Ein Ziel gilt erst als Reflexion hinter einer Wand, wenn kein Punkt im Umkreis von 0,4 m in Sicht ist.
  Bisher wurde nur entlang der Sichtlinie zurückgegangen; ein Sensor in der Ecke blickt an seiner Wand
  entlang, und ein Ziel wenige Zentimeter jenseits davon war keine Messung (ruhige Ziele verworfen:
  Bad 11 % → 2 %, Schlafzimmer 20 % → 0,4 %, Flur 39 % → 16 %).
- **LD2410C-Amplitude je Aufenthalt auf einem Gitter** statt 3 Quadraturpunkten: 9 Zellen in log g
  (0,21–2,28) mit der Masse von Gamma(6, 6). Eine Person, die nur ein Drittel des Profils zurückgibt,
  wird nicht mehr von einer Echoquelle verdrängt (MODEL.md 4.3).
- Zusammen (Nachspiel ab 17:13 wie die App, 12 Hypothesen): Bad mit der zweiten Person 22:09–22:20
  P(belegt) 0,00 → 0,94 / 0,92 / 0,98, Licht fälschlich aus 3,69 → 0,14 von 11 Fenstern, an 0 von 43;
  Meldungen bis 6.10. und 7.10. früh Licht gleich (aus 1,50 von 15 / 0 von 7, an 0), Log-Evidenz
  +488 / +880; leere Nacht 0 min (MODEL.md 10, „Meldungen 7.10. abends“).

## 0.19.0

Robustheit (zwei Reviews von 0.18.0). Der Node-RED-Flow behält bei *nicht verfügbar* den letzten
Zustand: Alles, was die App anhielt oder ihre Ausgaben einfror, ließ ein brennendes Licht brennen.

- **Live-Ansicht hält nichts mehr auf:** Ein Browser, der nicht mehr liest (Handy schläft, Proxy hängt),
  hielt über den vollen Sendepuffer die Schleife an, die das Modell taktet und die Räume an Home Assistant
  schickt. Jetzt hat jeder Browser eine eigene Sendeaufgabe, nur die neueste Nachricht wartet; wer 5 s für
  eine braucht, wird getrennt.
- **Verstummter Sensor:** Seine Spuren enden 6 s nach seinem letzten Frame (MODEL.md 4.1), nicht erst mit
  dem nächsten. Ein Board, das ausfiel, während es jemanden verfolgte, hielt den Raum 1–2 h besetzt.
- **MQTT:** Frames werden beim Eintreffen geprüft; gültiges JSON, das kein Frame ist (`null`, eine Liste,
  `uptime_ms` als Text, ein Ziel ohne `y`, `x: null`, `targets: null`), beendete die App. Ungültige Frames
  werden verworfen und höchstens einmal je Minute und Sensor protokolliert; die MQTT-Schleife verbindet
  sich nach jedem Fehler neu.
- **Speichern und Laden:** Ein Fehler beim Speichern (volle Platte, ein Zustand, den das Modell nicht
  speichert) beendete die App; jetzt wird er protokolliert. Das Speichern alle 10 Minuten packt und
  schreibt in einem Thread, mit `fsync` und atomarem Umbenennen. Eine leere, kaputte oder falsch getypte
  `calibration.npz`, `people.json`, `ghostmap.json`, `ld2410.json` oder `destinations.json` verhinderte
  den Start; jetzt gilt sie für ihren Teil als „nichts gelernt“.
- **NaN:** Ein nicht endliches Gewicht oder eine nicht endliche Wahrscheinlichkeit startet das Modell neu
  (mit Protokollzeile), statt die App zu beenden oder still „niemand da“ zu zeigen. Ein mögliches
  `log(0)` im LD2410C-Gewicht (derselbe Fehler wie bei 0.9.3) wird im Log-Raum gerechnet.
- **Kein Neustart für Ausgabe-Einstellungen:** *Kosten: Licht ohne Person*, *Kosten: Einschalten auf
  Verdacht*, *Vorausschau*, *Haltezeit LD2410C* und das Umbenennen von Zonen und Sensoren behalten Spuren
  und Personen (bisher ein voller Neustart, 2,6 s Rechenzeit mit 7 Sensoren).
- **Konfigurationsprüfung:** abgelehnt werden doppelte Sensor- und Zonen-IDs, Sensor-IDs mit `/ + #`,
  Wände ohne genau zwei verschiedene Punkte, unbekannte Wand- und Zonenarten und Formen, Rechtecke ohne
  zwei Ecken, Flächen mit weniger als drei Punkten, Kreise ohne Mittelpunkt (die gingen durch und ließen
  danach jeden Takt scheitern, auch nach jedem Neustart). Eine Wand mit einem Punkt gibt 400 statt 500.
  Eine neue Konfiguration gilt erst, wenn das Modell mit ihr läuft; sonst bleibt die alte in App und
  Modell. Hintergrundbilder dürfen bis 10 m je Pixel haben (SVG-Plan in Metern).
- **Verfügbarkeit:** *online* erst nach den ersten frischen Zuständen einer Verbindung (vorher zeigte HA
  die vom Broker gehaltenen Zustände des letzten Laufs als aktuell). Scheitert das Modell dreimal, ohne
  dazwischen 60 s zu laufen, sind die Entitäten *nicht verfügbar*, im Protokoll steht ein lauter Fehler,
  und das Modell wird nur noch einmal je Minute neu gebaut (bisher bei jedem Frame, Sekunden Rechenzeit
  je Versuch). Eine gespeicherte `tracker.json`, die die Prüfung nicht besteht, steht beim Start als
  Fehler im Protokoll.
- **Neu beginnen:** lässt auch die laufenden Spuren der Sensoren neu anfangen; wer ruhig saß, war bisher
  für das Modell dauerhaft weg (P 0,004 nach 5 min), und der LD2410C-Hintergrund lernte ihn als
  Hintergrund. Der lernt nach *Neu beginnen* 5 Minuten lang nichts.
- **Fehlermeldungen nach einer Live-Kalibrierung:** Die Meldung enthält die Konfiguration vom Beginn des
  Nachspiels und jede seither ohne Neustart übernommene mit ihrer Zeit (`config_changes`);
  `tools/replay.py --report` übernimmt sie zu denselben Zeiten (vorher bis 0,12 daneben).
  `tools/report_eval.py --config-at "Zeit=Datei"` spielt Konfigurationswechsel ebenso nach.
- **LD2410C:** Ein Frame mit allen Energien 0 ist das „kein Wert“ der Firmware, keine Messung (in
  1,5 Millionen aufgenommenen Frames nie vorgekommen); gewogen überstimmte er zwei LD2450-Spuren eines
  Sitzenden.
- **Kleineres:** Gelöschte Zonen nehmen ihren gehaltenen Zustand im Broker mit. Hochgeladene Bilder
  kommen mit einer Content-Security-Policy, die jedes Skript verbietet (ein SVG lief sonst im Origin von
  Home Assistant). *Alle auf Standard* wartet aufs Speichern, bevor die Seite neu lädt. Lehnt der Server
  eine Speicherung ab, geht nur die abgelehnte Änderung zurück, spätere bleiben. `tools/report_eval.py`:
  Der *walks*-Filter eines Fensters galt auch für gleichzeitige andere Fenster. *Fehler melden* mit einer
  Minutenangabe, die keine Zahl ist, gibt 400 statt 500.

## 0.18.0

- **Bearbeiten sicherer:** Nach jedem Speichern sagt eine Meldung *Gespeichert* und, wenn das Modell
  neu startet, *Modell neu gestartet* (`PUT /api/config` antwortet mit `restarted`). Rückgängig und
  Wiederholen gibt es als Knöpfe auf der Karte, auch am Handy. Strg+Z wirkt nur noch im Tab der Änderung;
  sonst sagt die App, in welchem Tab sie war, statt still etwas Unsichtbares zurückzunehmen. Löschen per
  Entf/Rücktaste, *Von Karte nehmen*, *Neu sammeln* und *Neu beginnen* fragen nach. Die Hinweisbox über
  der Karte fängt keine Klicks mehr ab (eine Wand unter ihr lässt sich ziehen).
- **Live-Ansicht:** Oben steht *Besetzt: …*, besetzte Räume stehen in der Liste zuerst und sind
  hervorgehoben (auch auf der Karte). Rohdaten der Sensoren sind standardmäßig aus, der Schalter wird im
  Browser gemerkt. Je Person eine Farbe für Punkt, Wolke und Spur; der zusätzliche, nicht an Wänden
  abgeschnittene 2σ-Kreis entfällt. Die Spur bricht bei Sprüngen (über 1 m oder durch eine Wand) ab.
  Raum-% und Person-% sind beschriftet (P(jemand im Raum) gegen P(diese Person dort)). *wird betreten*
  und *Ziel* (jetzt mit Wahrscheinlichkeit und Quelle) bleiben 2 s stehen und erscheinen nur an noch
  nicht besetzten Räumen. Die Listen werden nur noch zeilenweise und höchstens zweimal je Sekunde
  erneuert: Tooltips bleiben, Zeilen springen nicht. Badges und Kartenbeschriftungen haben mindestens
  4,5 : 1 Kontrast.
- **Kalibrierung:** Das Ergebnis steht direkt unter *Berechnen* und wird ins Bild gerollt; der Knopf ist
  während der Rechnung gesperrt. Gemeinsame Gründe stehen einmal oben („Noch zu wenig Daten: … (1/3 h)“),
  je Sensor nur das Eigene; die Entwicklungsnotiz zum 7.10. ist aus dem Text. Je Sensor ein Fortschritt
  „Stunden mit Gehenden x/3“. Vorher und nachher dieselbe Zahl: *Punkte in Bewegung*, wie die Berechnung
  sie zählt (`Calibrator.status()` liefert `points`, `hours`, `min_hours`; `solve` je Sensor `few`,
  `walk_more`). *bleibt* ist grau statt rot, die Paarliste ist eingeklappt.
- **Layout:** Jeder Tab beginnt oben (das Panel behielt die Scrollposition des vorigen Tabs). Bei
  1024 px (Home Assistant mit Seitenleiste) sind alle Tabs ganz zu sehen: Unter 1150 px entfällt der
  Schriftzug, und der Kopf bricht um statt abzuschneiden. Am Handy steht der Verbindungszustand über den
  Tabs. Veraltete Daten (getrennt oder 3 s ohne Nachricht) sind markiert: Karte grau, Listen blass, roter
  Balken mit dem Alter des Stands. CPU im Kopf als „Modell 12,3 % CPU“ mit Erklärung (% eines Kerns).
- **Kleineres:** *Fehler melden* kennt *Licht fälschlich an*, *Licht fälschlich aus* und *Licht kam zu
  spät* (neue Schlüssel `light_on`, `light_off`, `light_late`; alte Meldungen bleiben gültig) und wählt
  keinen Raum mehr vor. Das Raum-Häkchen *Eingang* heißt jetzt *Außer Haus* (der Zonentyp *Eingang*
  bleibt). Keine Verweise auf MODEL.md und auf eine Bewohner-Einstellung mehr in der Oberfläche; neben den
  Kosten steht die Schwelle, die daraus folgt („besetzt ab 67 %“). Listenzeilen (Räume, Sensoren, Zonen,
  Ebenen) per Tastatur erreichbar, sichtbarer Fokusrahmen. LD2410C-Stufen mit zwei Nachkommastellen, die
  Prüfanleitung zur gespiegelten x-Achse steht unter ihrem Häkchen.

## 0.17.1

- **Zahlenfelder abgesichert:** Ein geleertes Feld wurde als 0 gespeichert; bei *Kosten: Licht ohne
  Person* galt dann jeder Raum als besetzt, im Live-System wären alle Lichter angegangen. Jetzt behält
  ein leeres oder ungültiges Feld seinen alten Wert und zeigt, was erlaubt ist (Grenzen je Einstellung,
  Sensorwert, Türbreite und Bildlage). `PUT /api/config` prüft dieselben Grenzen (`model.PARAM_LIMITS`,
  `SENSOR_LIMITS`, `Config.from_dict(check=True)`) und lehnt Werte außerhalb, `NaN` und leere Werte mit
  400 und einer deutschen Meldung ab; die Oberfläche lädt dann den gespeicherten Stand neu. Eine
  gespeicherte Konfiguration lädt wie bisher ohne Prüfung.

## 0.17.0

- **Ziel** (MODEL.md 6, 10 „Ziel“): neue Entität `binary_sensor.presence_<raum>_ziel` je Raum, neben *wird
  betreten*. P(ein Gehender geht als Nächstes in diesen Raum), ohne Horizont: aus seiner Bewegung (`p_enter`
  von *wird betreten*) und einer Karte, die die App selbst lernt, wohin die Gänge von jeder Stelle, in jeder
  Richtung und bei jedem Tempo bisher gingen (`destination.py`, aus ihren eigenen Gehenden über alle
  Hypothesen, vergessen über 14 Tage, gespeichert in `/data/destinations.json`; nur Ausgabe, der Filter
  bleibt unberührt). Gemischt als Dirichlet-Posterior mit der Bewegung als Prior (ein Gang Gewicht).
  **Ohne Gelerntes ist *Ziel* genau *wird betreten*** (Test; Nachspiel mit leerer Karte: in keinem Takt
  verschieden), man kann es also sofort in Node-RED statt *wird betreten* nehmen. Die Karte lernt nur
  live, nach dem Update beginnt sie leer; ändern sich Räume oder Türen, beginnt sie neu. Schwelle
  *Schwelle „Ziel“* (Vorgabe 0,8), je Raum einstellbar. Attribute `probability`, `from`, `distance`,
  `eta`, `person`, `walks`, `source` (`bewegung` / `karte`), `weight`. Gemessen auf 26 h, Karte von
  leer an (342 Eintritte in dunkle Räume; ≥ 1 s vorher / Fehl-Ein je h): *wird betreten* 39 % / 15,2,
  *Ziel* 0,8 26 % / 8,1, *Ziel* 0,5 46 % / 11,3; in keiner Stunde des Lernens in beidem schlechter als
  *wird betreten*. Werkzeug `tools/entries.py` (Vorlauf und Fehl-Ein beider aus den Türdurchgängen der
  Aufnahmen, je Raum, Tür, Stunde und Zeitraum).
- **Weniger Zeilen im Recorder von Home Assistant:** Wahrscheinlichkeiten in 5-%-Schritten, Zeiten auf
  0,5 s, Wege auf 0,5 m; Attribute nur neu mit einem Wechsel des Zustands oder wenn eines um mindestens
  zwei Stufen wanderte (frühestens 10 s, beim Personenzähler 60 s nach der letzten Änderung);
  *wird betreten* / *Ziel* aus: feste Attribute (`p_enter` dann 0). Die Zustände schalten genau wie in
  0.16.0 (Nachspiel 26 h: alle Wechsel von *besetzt*, Zahl, *Bewegung*, *wird betreten* im selben Takt).
  Zeilen am Tag 31 500 → 14 600 trotz der neuen Entität, in der vollsten Stunde 4 950 → 2 640.
  DOCS.md: wie man die Entitäten aus dem Recorder ausschließt (`recorder: exclude: entity_globs`).
- *Besetzt* unverändert: report_eval an 0 / aus 1,50 von 41 / 15 und 0 / 0 von 13 / 7, Log-Evidenz
  gleich 0.16.0.

## 0.16.0

- **„Wird betreten“ ist eine Vorhersage** (MODEL.md 6, 10 „Vorausschauend einschalten“). Statt eines
  Flags aus der wahrscheinlichsten Hypothese (ihr Mittel 1 s geradeaus, auch durch Wände) rechnet das
  Modell jede Person, die mit Spur geht, mit seinem eigenen Bewegungsmodell voraus (Wände halten auf,
  Türen nicht, wer anhält, bleibt stehen), über alle Hypothesen: `p_enter` = P(binnen der Vorausschau
  kommt jemand herein). *Wird betreten* ist an, wenn `p_enter` über der Schwelle aus den Kosten liegt
  (neu: *Kosten: Einschalten auf Verdacht*, `approach_cost` = 0,05; Vorausschau `lead_time` jetzt 2 s;
  *Mindesttempo* entfällt). Attribute der Entität `binary_sensor.presence_<raum>_approaching` (Namen
  und IDs bleiben): `p_enter`, `eta`, `distance`, `person`; im Tab *Live* mit Prozent, Zeit und Weg.
  Gemessen auf 23 h (6./7.10., 329 Eintritte in dunkle Räume): Licht mindestens 1 s / 1 m vor dem
  Eintritt bei 60 / 62 % (bisheriges Flag 9 / 10 %, *besetzt* allein 2 / 2 %), mit der Regel „auf
  Verdacht an, nach 30 s ohne *besetzt* aus“ 4,0 vergebliche Verdachte je Stunde (bisher 2,0). *Besetzt*
  ist unverändert (report_eval: an 0 / aus 1,50 von 41 / 15 und 0 / 0 von 13 / 7, Log-Evidenz gleich;
  leere Nacht 0 min). Die App schaltet weiter keine Lichter: DOCS.md beschreibt die Regel für Home
  Assistant / Node-RED.

- **Geprüft, am Modell nichts geändert** (MODEL.md 10, „Bewegung als Merkmal einer Person?“ und „Zwei
  an einer Stelle“):
  - *Bewegung als Zeichen einer Person:* Geister des LD2450 sind in den Aufnahmen so selten, dass sich
    keine Verteilung ihrer Bewegung messen lässt: in der leeren Nacht 6./7.10. (7,3 h, fünf Sensoren)
    eine einzige Spur in einem beobachteten Raum, allein zu Hause (7.10. 09:10–16:50) 2 sichere Geister
    unter 171 Spuren, nur 0,4 % der Sekunden mit Zielen zeigen zwei mehr als 2 m auseinander. Ein
    Gewicht ohne gemessene Verteilung wäre eine Regel. Nachgespielt 7.10.: Spuren, die in 5 s mindestens
    1 m gehen, sind im Median schon bei der Geburt zu 0,95 Person. Geprüft dazu „sonst niemand in der
    Nähe“ als Entstehungsrate der Geister fern von Gehenden, gemessen in stiller Zeit (kein Ziel im Haus
    30 s lang): höchstens 3,2·10⁻⁶ statt 1,8·10⁻⁵ je m² und s. Licht und Zahlen gleich, Log-Evidenz
    +81 / +2226, Rechenzeit +21–24 %: nicht übernommen.
  - *Zwei an einer Stelle (Bad 7.10. 17:15–17:31, aus den Daten bestimmt):* Licht richtig, aber Bad = 2
    nur zu 0,01–0,06. Die zweite Person wird beim Betreten des Bads im Schlafzimmer vermutet (ungesehen
    im Bad zu gehen kostet, ins Schlafzimmer nicht), und danach unterscheidet keine Messung zwei von
    einer Person mit mehr Amplitude (die Energien des LD2410C sprechen nach 17:33 mit einer Person
    ebenso für „zwei“). Geprüft: kein Abklingen der Existenz, wo eine Person von einem gemessenen Ziel
    nicht zu trennen wäre (Überleben abhängig vom Ort; die Abhängigkeit von der Geschwindigkeit folgt
    aus der unabhängigen Bewegung). In der Simulation zwei Stehende mit einem Sensor P(=2) 0,70 → 0,95;
    auf den Aufnahmen Bad 0,01 → 0,01 / 0,06 → 0,06, Arbeitszimmer 7.10. 08:45 0,05 → 0,05, Licht
    gleich (bis 6.10. an 0 / aus 1,50 von 41 / 15; 7.10. an 0,11 / aus 0 von 13 / 7), Log-Evidenz −7 /
    −20, leere Nacht 0 min, Rechenzeit +3–5 %. Nicht übernommen.
- Neue Tests (`tests/test_together.py`): zwei, die zusammen stehen, bleiben zwei; eine Mehrwegekopie
  neben einem Gehenden ist eine Minute später keine zweite Person; wer allein hereinkommt, macht 5 s
  nach dem ersten Frame Licht.

## 0.15.0

- **Kalibrierung: Ergebnis lesbar.** Je Sensor ein Block (Drehung alt → neu, Maßstab, Bewertung,
  Erklärung) statt einer Tabelle, die aus der Seitenleiste ragte.
- **ESPHome** (`esphome/`): Frames werden aus einer eigenen Task gesendet (`idf_send_async`), größere
  UART-Puffer, `post_connect_roaming: false`. Seit 7.10. auf allen Boards: keine Neustarts mehr bei
  Netz-Hängern (vorher Task-Watchdog, Arbeitszimmer 15-mal in 18 h).
- **Kalibrierung schlägt nichts mehr vor, das nur auf einem Gang beruht** (MODEL.md 10). Im
  Test (config8, Aufnahme 7.10. 16:00–16:40: Leons Gang und wenig Alltag) schlug 0.13.0 vor, den Flur um
  −17° und das Esszimmer um +5° zu drehen; beim Esszimmer lagen danach mehr Gehende außerhalb der Sicht
  (32 → 37 %), und der Flur mit 34° legt im Alltag tausende Messungen hinter Wände. Innerhalb des Gangs
  war das Ergebnis stabil (Jackknife über seine Minuten: Flur ± 1,6°), die Unsicherheit also nicht das
  Problem: Ein Gang zeigt nur eine Situation. Jetzt:
  - Vorgeschlagen wird für einen Sensor nur, was auf Gehenden aus mindestens 3 verschiedenen Stunden
    beruht; die Unsicherheit enthält das Jackknife über Gruppen von Stunden (Künsch 1989). Sonst:
    „Messungen in Bewegung erst aus 1 Stunde …“.
  - Kein Vorschlag, der mehr Gehende eines Sensors außerhalb seiner Sicht legt (gepaarter Vergleich
    Punkt für Punkt).
  - Die gesammelten Messungen überstehen einen Neustart (`/data/calibration.npz`).
  Gemessen: 16:00–16:40 allein nichts vorgeschlagen (auch das Bad nicht: sein Ergebnis hängt am
  falschen Flur, 50,8° gegen 55,1° mit den Alltagsdaten der anderen). Mit allen Daten 6.10. 18:00 bis
  7.10. 16:39 (11–17 Stunden je Sensor) wie bisher vorgeschlagen: Esszimmer 319,6° / 1,071, Wohnzimmer
  138,3° / 1,132, Arbeitszimmer 324,4° / 1,049 (Jackknife 0,3–0,8°, 0,004–0,020); es bleiben der Flur
  (Grundriss gegen Paare), die Küche (20 % statt 19 % außer Sicht) und das Bad (eine Stunde).
- **Wer hinausgeht, verblasst draußen wie überall** (MODEL.md 5.5, 10). Eine bekannte Person ohne Spur
  geht erst in die unbekannten Personen über, wenn sie zu weniger als 1 % existiert, nicht schon, wenn
  sie außer Haus ist. Seit das Treppenhaus außer Haus liegt, kam jeder, der hinausging, sofort mit
  seinem ganzen r in die unbekannten Personen außer Haus; dort verblasst nichts, sie kehrten mit
  1/(4 h) zurück und wurden erst nach einem Tag vergessen (7.10. 09:01: 0,27 → 1,25 erwartete
  Unbekannte draußen). Diese Ankünfte sammelten sich als Unbekannte in Sicht (09:48 0,014–0,058 statt
  0,004), und um 09:48:46 wurde ein Geist im Flur zu einer Person: Licht fälschlich an im Flur 0,14
  (11 % des Fensters). Gemessen (12 Hypothesen): Meldungen bis 6.10. an 0 von 41, aus 1,50 von 15,
  Log-Evidenz −1121573 (−1); 7.10. an 0 von 13 (vorher 0,11), aus 0 von 7, −449450 (+52), Flur 09:49
  0,07; Küche 0,04 / 0,01 / 0,01; leere Nacht 0 min.

- **Das Treppenhaus ist außer Haus** (MODEL.md 2, 3.3, 3.4, 6, 10). Ein Raum mit dem Haken *Eingang*
  gehört nicht mehr zur Wohnung: Er ist öffentlicher Raum vor der Wohnungstür, den auch andere
  Hausbewohner nutzen. Bisher war er ein Bereich ohne Sensor mit eigener Personenzahl, Aufenthaltsdauer
  (Median 30 min) und Rate nach draußen (1/(2 h)); Anzeige und Home Assistant zeigten dort eine Zahl,
  die nichts bedeutete, und die Zahl im Haus zählte ihn mit. Jetzt:
  - Die Tür zum Treppenhaus ist die Wohnungstür. Wer hindurchgeht, ist außer Haus (gehend über die
    Tür wie in jeden Bereich ohne Sensor); wer zurückkommt oder neu ist, erscheint gehend an ihr.
  - Für das Treppenhaus gibt es keine Zahl, keinen Zustand und keine Entitäten mehr; `presence_haus_*`
    zählt nur die Wohnung. Was ein Sensor durch die offene Tür im Treppenhaus misst, wird wie im Balkon
    verworfen.
  - Alte Entitäten, die der Broker noch hält (`presence_treppe_*`), löscht die App beim Verbinden:
    Sie liest die beibehaltenen Discovery-Konfigurationen ihres Geräts und leert, was sie nicht mehr
    veröffentlicht, mit dem Zustand.
  - Ein Bereich ohne Sensor ist *offen*, wenn eine Tür von dort nach draußen führt; endet ein
    Aufenthalt dort, geht man durch eine seiner Türen, auch nach draußen. Die Parameter
    `dwell_median_open`, `dwell_spread_open` und die Rate `leave_rate` entfallen.
  - Im Editor ist nichts zu ändern: Der Raum *Treppe* behält seinen Haken *Eingang*; der gezeichnete
    Raum legt die Wohnungstür fest. `people.json` passt danach nicht mehr (andere Orte): Die App beginnt
    einmal ohne Wissen über die Personen.

  Gemessen (12 Hypothesen): Meldungen bis 6.10. Licht fälschlich an 0 von 41, aus 1,68 von 15 (wie
  0.12.0), Log-Evidenz −1121544 (−5); Meldungen vom 7.10. an 0 von 13, aus 0 von 7 (wie 0.12.0),
  −449356 (−35; ohne das Verwerfen der Messungen im Treppenhaus −449357). Wertungen fürs Treppenhaus in
  der Wahrheitstabelle (7.10. 08:29) werden nicht mehr bewertet. Leere Nacht 6./7.10. 0 min Licht an.
  Weggehen 7.10. um 8:57 (zweite Person, Telefon „not_home“ erst 9:06:59): im Haus erwartet 1,10
  statt 1,99 um 8:57:00, Anzeige 1 statt 2 bis 8:59.

## 0.14.0

- **Die Geisterkarte lernt keinen Sitzplatz mehr als Geisterquelle** (MODEL.md 4.2, 9, 10). Am 6.10.
  saß die zweite Person abends lange an ihrem Platz im Arbeitszimmer; der Filter hielt ihre Spuren dort
  für Geister, und die Karte lernte daraus in 2,5 h eine Geisterquelle mit 21-mal der Prior-Rate. In den
  33 Minuten, in denen sie allein dort saß, war das Arbeitszimmer im Nachspiel durchgehend leer. Zwei
  Rückkopplungen, beide behoben:
  - Der Prior der Karte wog nur 0,04 Geister je Zelle: Schon eine Spur, die der Filter für einen Geist
    hielt, machte die Stelle 5–8-mal so wahrscheinlich. Die Korrektur, die den Beitrag der Karte aus
    dem Urteil herausrechnet, griff nicht, weil das Urteil meist genau 1 war (die Alternative „Person“
    war schon abgeschnitten). Jetzt wiegt der Prior einen Geist je Zelle (wie Luber 2014): drei solche
    Spuren machen 1,8- statt 13-mal, eine Reflexion mit 30 Spuren am Tag weiter 7,6-mal.
  - Wie lange Geister leben, lernte die App mit: Lange Spuren von Sitzenden, für Geister gehalten,
    machten die langlebige Art in einer Stunde von 39 auf 148 s, sodass ein langes Leben nicht mehr
    gegen einen Geist sprach. Die Lebensdauern sind jetzt fest (offline geschätzt, `tools/ghostmap.py`
    gibt sie aus).
  Die gespeicherte Karte beginnt nach dem Update neu (sie ist mit dem alten Prior gelernt).
  Gemessen (`tools/report_eval.py`, 12 Hypothesen): Meldungen bis 6.10. Licht fälschlich an 0 von 41,
  aus 1,50 statt 1,68 von 15, Log-Evidenz −5; vom 7.10. an 0 von 13, aus 0 von 7, Küche 0,04 / 0,01 /
  0,00 wie bisher, Log-Evidenz −84; die vier Fenster, in denen sie allein am Platz saß: das Licht wäre
  in 2,64 statt 4,00 von 4 aus (21:37–22:00 jetzt zu 0,97 besetzt); leere Nacht 0 min Licht an. Das
  Fenster 21:36 der Meldungen bleibt falsch: Es liegt nicht an der Karte (ohne gelernte Karte ebenso),
  sondern daran, wie der Filter ihre Spuren an diesem Platz beurteilt (MODEL.md 9). Ganz ohne gelernte
  Karte wären die Lichtfehler hier gleich, die Log-Evidenz aber um 28 073 bzw. 9 084 schlechter: Die
  Karte bleibt. Neue Tests
  (`test_frames.py`, `test_filter.py`: eine Sitzende, die der Filter erst für Geister hält, macht ihren
  Platz nicht zur Geisterquelle).

## 0.13.0

- **Kalibrierung neu: alle Sensoren gemeinsam, auch wenn sie sich nur in einer Tür überschneiden**
  (MODEL.md 10, `calibration.py`). Die alte Rechnung schlug nach einem Gang am 7.10. vor, das
  Arbeitszimmer zu spiegeln (294°), das Esszimmer auf Maßstab 1,27 und den Flur um 15° zu drehen.
  Ursachen: Die Spiegelsuche ließ den ersten Sensor immer ungespiegelt (die eingebaute Lage wurde nie
  gerechnet); fast alle gemeinsamen Messungen lagen in einem Fleck im Flur zwischen den Türen, der die
  Drehung kaum bestimmt; falsche Paare; der Maßstab des Esszimmers hing über eine Kette an 8 m
  entfernten Messungen des Wohnzimmers. Jetzt:
  - Eine Rechnung für alle Sensoren aus drei Dingen: gleichzeitige Messungen zweier Sensoren (mit
    Anteil falscher Paare), Übergänge von einem Blickfeld ins nächste (Rahimi, Dunagan & Darrell 2004,
    mit dem Gehmodell aus MODEL.md 3.2) und der Grundriss (Gehende sind in Räumen und gehen durch
    Türen); dazu ein Prior auf die Maßstäbe der LD2450, gemessen 1,06 ± 5 %.
  - Die x-Richtung bleibt, wie eingebaut (auf Wunsch prüft die App sie: auf dem Gang vom 7.10. ist die
    eingebaute für jeden Sensor klar besser).
  - Jeder Wert mit Unsicherheit (robust gegen korrelierte Messfehler, dazu der an Alltagsdaten
    gemessene Modellfehler von 2,1° und 0,042). Übernommen wird nur, was bestimmt ist; „Drehung
    unsicher“, „mehrdeutig“ oder „Grundriss und gemeinsame Messungen widersprechen sich“ statt Unsinn.
  - Die App sammelt laufend die Messungen Gehender der letzten 24 Stunden (etwa 5 MB); ein eigener
    Gang ergänzt sie. *Neu sammeln* nach dem Drehen oder Versetzen eines Sensors. Leons Gang allein
    hätte die Wände verbessert und den Alltag verschlechtert (Paare innerhalb 0,5 m 54 % → 24 %); mit
    allen Daten 54 % → 56 %, Schritte durch Wände 232 → 214 von 646.
  - `tools/calibrate_offline.py` rechnet mit demselben Code.
  Gemessen mit `tools/report_eval.py` (Meldungen 7.10.): Licht falsch an 0 von 13, aus 0 von 7 wie
  bisher; Log-Evidenz (mit Jacobi-Term für die Maßstäbe) über 9 h Nachspiel +1104 (die alte Rechnung
  −30755). Offen: Der Flursensor sitzt wahrscheinlich 0,4–0,8 m weiter nördlich an der Badwand als
  eingezeichnet (nachmessen).

## 0.12.0

- **Wer nicht mehr gemessen wird, ist nach 2 statt 30 Minuten vergessen** (MODEL.md 3.4, 5.5, 9, 10).
  Eine bekannte Person ohne stützende Messung klingt jetzt mit 2 min ab (Existenz als Markov-Kette,
  Musicki & Evans 2005). Gemessen an 5,6 h bekannter Sitzender in Sicht (Schreibtisch 6./7.10.,
  Esszimmer 7.10.): Die längste Zeit ohne jede stützende Messung (LD2450 oder LD2410C über dem
  Hintergrund) war 18,2 s, 99 % unter 0,3 s; nur der LD2450: höchstens 85 s. Ein leerer Platz in Sicht
  wird vom LD2410C mit −48 bis −125 log je Minute widerlegt; das Abklingen zählt daher nur, wo nichts
  misst (Ecke der Vorratskammer, Räume hinter Türen). 2 min lassen einer Sitzenden in der längsten
  gemessenen Lücke r ≥ 0,86; 45 s machten das Licht an anderer Stelle schlechter. Wer hinter einer Tür
  schläft, ist nach wenigen Minuten „unbekannt“ und kommt als neue Person heraus.
- **Die Erkennbarkeit κ und die Amplitude des LD2410C sind Gauß-Laguerre-Punkte** statt gleich
  wahrscheinlicher Stufen (wie die Sigma-Punkte für die Position): Ein Drittel der Masse von κ lag nahe
  0 und machte „ungesehen sitzen“ billig. κ: 0,42 / 2,29 / 6,29 mit 0,71 / 0,28 / 0,01 statt
  0,19 / 0,71 / 2,10; Log-Evidenz +466 bzw. +513 bei gleichen Lichtfehlern.

Gemessen (12 Hypothesen): Meldungen vom 7.10. Licht fälschlich an 0 von 13, aus 0 von 7; Küche
11:39–12:55 0,04 / 0,01 / 0,00 (0.11.0: 0,08 / 0,02 / 0,01). Meldungen bis 6.10.: an 0 von 41, aus 1,68
statt 1,17 von 15: ein 6-s-Fenster im Arbeitszimmer um 21:36, an einem Platz, den die Geisterkarte als
Geisterquelle gelernt hat (MODEL.md 9); Log-Evidenz +380. Leere Nacht 0 min Licht an.

## 0.11.0

Gemessen mit `tools/report_eval.py`: Meldungen bis 6.10. (5 Sensoren, config5) Licht fälschlich an 0 von
41, aus 1,17 von 15 (0.10.0: 0 / 3,00); Meldungen vom 7.10. ab 07:44 (config7) an 0 von 13, aus 0 von 7
(0.10.0 nachgespielt 0,07 / 1,00; live hing der Küchengeist über eine Stunde); leere Räume nachts 0 min.
Rechenzeit auf derselben Stunde, je Lauf ein Kern, Median aus drei: 32,8 s gegen 33,5 s (0.10.0).

- **Keine erfundenen Personen mehr, die sich ohne Messung halten** (MODEL.md 1, 4.1, 4.2, 5.5, 10).
  Am 7.10. hing über eine Stunde eine zweite Person in der Küche, obwohl nur eine zu Hause war, und
  über den Tag sammelten sich bekannte Personen an (nachgespielt bis zu 6, im Haus erwartet 4,7 statt
  2). Ursachen und Änderungen:
  - Eine bekannte Person ohne Spur existiert nur noch mit einer Wahrscheinlichkeit (Bernoulli wie im
    PMBM): „die Spur war ein Geist“ und „sie war eine Person“ werden nach dem Ende der Spur eine
    Hypothese mit r < 1, statt dass die erste abgeschnitten wird und nie zurückkommt. Ohne Messungen,
    die sie stützen, klingt r mit 30 min ab (Existenz als Markov-Kette, Musicki & Evans 2005); die
    Energien des LD2410C bei Sitzenden halten sie. Die Anzeige zeigt Personen ab r = 0,5.
  - Das Ende einer Geisterspur sprach bis zu 26-mal für eine Person (die Quelle musste sterben *und*
    nicht wiedergefunden werden); jetzt konkurrierende Risiken.
  - Die Sicht eines Sensors auf eine Person ist die Sicht auf die Stelle, an der ihre Spur sitzt (über
    den Versatz gemittelt, nicht durch Wände): Am Rand eines Türschattens war „das ist dieselbe
    Person“ unmöglich und Leon wurde doppelt; ein Streifen der Küche an ihrer Wand war für jeden Sensor
    unsichtbar (dort stand die Person der Live-App, 2,29 / 7,70).
  - Die unbekannten Personen sind eine Intensität für alle Hypothesen, nicht eine Kopie je Hypothese.
  Gemessen: Meldungen vom 7.10. (13 leere, 7 belegte Raum-Fenster) Licht fälschlich an 3,14 → 0, aus
  0 → 0; Küche 11:39–12:55 im Mittel 0,97 / 0,96 / 0,96 (e0e26e8) → 0,08 / 0,02 / 0,01, Flur 9:49
  0,13 → 0,05. Meldungen bis 6.10.: fälschlich an 0 von 41, aus 1,17 von 15 wie bisher, Log-Evidenz +28
  (12 Hypothesen) bzw. +196 (16); die leere Nacht 0 min Licht an. Im Haus erwartet tagsüber 1,1 statt
  3,1–3,6 (wahr 1); dafür gelten Schlafende nach einer Stunde als unbekannt (MODEL.md 9). Rechenzeit
  (latency.py, 21 Uhr, ein Kern) 37,4 → 32,8 s. Neue Tests (`tests/test_existence.py`).

- **Weniger Rechenzeit: die inneren Schleifen sind kompiliert (Numba).** Die meiste Zeit ging nicht in
  die Rechnung, sondern in den Aufwand je numpy-Aufruf auf kleinen Arrays. Die Vorhersage der
  Gauß-Komponenten, die Wände, die Erfassungsraten der Sensoren, die Bewegung der Kacheldichten und
  die Zählverteilungen laufen jetzt als kompilierte Schleifen (`kernels.py`), mit denselben Ergebnissen
  (`tools/report_eval.py` unverändert). Gemessen im Container auf denselben 15 Minuten (5 Sensoren, Live-Ansicht
  offen): 4,0 % statt 6,6 % eines Kerns. Das Image beruht dafür auf `python:3.13-slim` statt Alpine
  (llvmlite gibt es nicht für musl; schon das allein spart 17 %), ist 430 statt 150 MB groß, und die App
  braucht etwa 200 statt 85 MB Speicher. Die Kernel werden beim Bauen des Images kompiliert, der Start
  bleibt schnell; das erste Bauen dauert etwas länger (MODEL.md 10).
- **LD2410C: Die Stille vor einem Frame zählt mit den Energien des Frames davor.** Ohne Anlass sendet
  die Firmware nur alle 5 s einen Herzschlag; der erste Frame danach kommt, weil eine Energie gestiegen
  ist, stand aber bisher für die ganze Stille davor (2–3 s statt 0,089 s). Dadurch lernte die App den
  Hintergrund ohne Person 13–15 % zu hoch (Arbeits-, Ess-, Wohnzimmer; Flur 5 %, Küche 0; bewegt allein
  25–30 %). Jetzt steht jeder Frame für einen Frame und die Stille für den Frame davor, wie die nicht
  gesendeten Frames waren (auf Strecken mit vollem Takt nachgespielt: bewegt 0–2 %, ruhig 1–7 % neben
  dem vollen Strom; „unter den Schwellen“ lag 5–65 % daneben, die Flags sind keine Schwellen auf die
  Energien; MODEL.md 4.3, 10). `tools/report_eval.py`: Licht fälschlich aus 1,00 statt 2,08 von 15
  (Wohnzimmer 21:24), an 0 von 41; leere Räume nachts weiter 0 min. Rechenzeit (6.10. 21:00–21:20, 5 Sensoren, je zweimal
  gleichzeitig) 2,7 statt 3,6 % eines Kerns. `sim.py` sendet wie die Firmware
  nur einen Herzschlag, wenn es nichts zu melden gibt. Neu `tools/ld2410/thinning.py`.
- **Fehlermeldungen lassen sich genau nachspielen.** Eine Meldung speichert jetzt auch, wann das Modell
  zuletzt ohne Wissen begann, was es da gelernt hatte (Geisterkarte und, neu, den LD2410C-Hintergrund mit
  den Echoraten), den Stand des Codes und, was die App sekündlich angezeigt hat (Zahl und
  Wahrscheinlichkeit je Raum, Personen; etwa 12–14 kB mehr je Meldung, 3 %). `tools/replay.py --report`
  beginnt am Start des Modells, wenn er in der Meldung liegt, und vergleicht mit der Anzeige. Gemessen an
  den 10 Meldungen vom 6.10. (Aufnahmen, durchgehend seit dem Start der App gegen das Nachspiel aus
  der Meldung): Mit dem Gelernten vom Moment der Meldung wich das Nachspiel bis zu 1,0 ab, und der
  gemeldete Fehler von 21:24 verschwand; mit dem Gelernten vom Start des Modells ist es gleich. Lag der
  Start vor den 15 Minuten (22:40, 22:42), wich das Nachspiel ohne Wissen am gemeldeten Moment um weniger als 0,01 ab; ein
  längeres Fenster oder ein gespeicherter Zustand des Modells (1,5 MB je Meldung) bringen nichts.
- Das Herunterladen einer Meldung ist im Test geprüft: Die App schickt sie unverändert als
  `application/gzip` ohne `Content-Encoding`, so dass auch der Browser hinter Ingress sie nicht entpackt.
- **Einen Sensor zu drehen oder neu zu kalibrieren, setzt die Personen nicht mehr zurück.** Bisher begann
  das Modell bei jeder neuen Konfiguration ohne Wissen. Waren dabei zwei Personen in Sicht und
  `start_people` 1, blieb eine von ihnen ein Geist, auch wenn der LD2450 sie minutenlang maß und der
  LD2410C bei 100 lag (Meldung 7.10. 08:07, Arbeitszimmer; nachgespielt in 4 von 6 Neubeginnen um
  08:05–08:06 P(belegt) 0,000 bis 08:08). Jetzt beginnen nur die Spuren des geänderten Sensors neu wie
  nach einer Datenlücke; ohne Wissen beginnt das Modell nur bei neuem Grundriss (MODEL.md 5.3, 10).
  `tools/report_eval.py` unverändert (Licht fälschlich an 0 von 41, aus 3,0 von 15).
- `tools/ld2410/second.py`: Will der LD2410C eine zweite Person, wo eine Person mit Spur allein ist?
  Log-Bayes-Faktor einer ruhenden zweiten Person im Raum, der Anteil der gekappten Werte und dasselbe
  mit dem Gewinn der Person auf dem Profil (MODEL.md 10, „Energie aufteilen“: die Energien werden
  schon richtig aufgeteilt; die Meldungen vom 7.10. 08:29, 08:45 und 08:52 kommen aus der Amplitude
  je Aufenthalt und, um 08:52, aus der Erfassungsrate am Mittel einer Gauß-Komponente).
- **Weniger Rechenarbeit für stehende Personen ohne Spur:** 5 statt 7 Arten, wie lange jemand steht,
  und 3 statt 5 Stufen der Erkennbarkeit (15 statt 35 Schichten je Kachel). Auf den 11 Stunden vom
  6./7.10. erklärt das die Messungen gleich gut und schaltet das Licht gleich; Rechenzeit 3,0 statt
  3,3 % eines Kerns (MODEL.md 3.1, 4.1). Die Zahl der Hypothesen (höchstens 12) bleibt; wie stark die
  Ergebnisse davon abhängen, steht in MODEL.md 5.1.
- **Ein Neustart der App vergisst die Personen nicht mehr; *Personen beim Start* gibt es nicht mehr**
  (mal ist einer zuhause, mal zwei, mal keiner, mal ein Gast). Die App speichert alle 10 Minuten und beim
  Beenden, was sie über die Personen weiß (`people.json`, 27–40 kB), und macht nach dem Neustart damit
  weiter, über die Pause mit dem Bewegungsmodell vorgerückt; ebenso nach einem Fehler des Modells und bei
  neuen Parametern (MODEL.md 5.3). Laufende Spuren beginnen nach dem Neustart neu. Ohne gespeicherten
  Zustand (erste Installation, neuer Grundriss, *Spuren zurücksetzen*) weiß das Modell nichts: keine feste
  Zahl, sondern unbekannte Personen, je ein Drittel in Sicht, hinter Türen und außer Haus (1 erwartet);
  wer in Sicht ist, wird gefunden, auch wenn es mehrere sind. Eine Fehlermeldung enthält den Zustand, mit
  dem das Modell begann, wenn der Start in ihren 15 Minuten liegt, und `tools/replay.py --report` sowie
  `tools/report_eval.py` beginnen damit wie die App. Gemessen mit `tools/report_eval.py` (Wahrheit
  6.10.): Licht fälschlich an 0,88 statt 0 von 41 leeren Fenstern, eine Episode von etwa 20 s um 21:28
  (mit 30 s Pause vor jedem App-Start 0,14), fälschlich aus 1,67 von 15 wie vorher (mit Pause 1,17).
  Meldung 7.10. 08:07 mit einem Neustart um 08:06: Arbeitszimmer durchgehend 1,000, mit dem gespeicherten
  Zustand und ohne. Bekannt: Personen, die das Modell irrtümlich hinter Türen vermutet, verschwanden
  bisher bei jedem Neustart der App; jetzt bleiben sie, bis sie herauskommen oder vergessen werden (am
  7.10. um 08:06 sechs, MODEL.md 9, 10).

## 0.10.0

Gemessen mit `tools/report_eval.py` gegen eine Wahrheit aus Leons Fehlermeldungen vom 6.10., von Hand
geschalteten Deckenlichtern und der Nacht (alle im Bett): Licht fälschlich aus in 3,0 statt 4,0 von 15
belegten Fenstern, fälschlich an in 0 von 41 leeren Fenstern und nachts 0 s (vorher auch 0 in den
Fenstern; im leeren Raum 8,1 Minuten mit den Energien ohne Echoquellen). Die beiden Meldungen „die zweite Person im
Arbeitszimmer, obwohl im Schlafzimmer“ (22:40, 22:42) kommen jetzt richtig heraus.


- **Der LD2410C zählt mit seinen Energien je Entfernungsring** (bewegt und ruhig) statt mit Flag und
  gemeldeter Entfernung (MODEL.md 4.3). Die gemeldete Entfernung erfand Personen: Leon allein am
  Schreibtisch in 1,5 m, in 27 % der Ruhig-Frames meldete er 2,6–6 m, und das Modell stellte die zweite Person
  (im Schlafzimmer) 25 Minuten lang zu 100 % ins Arbeitszimmer. Jetzt erklärt das gemessene Profil einer
  Person über alle Ringe (mit Mehrwege-Schweif) die Energie in den fernen Ringen; die Ruhig-Energien
  folgen mit 2 s Verzögerung; gleichmäßige Schwankungen aller Ringe (Schübe) zählen nicht als Person.
- **Echoquellen:** Energie, die von keiner Person kommt (nachts in der Küche 3–4-mal je Stunde für
  10–60 s), erklärt eine Echoquelle mit Profil und kurzer Lebensdauer statt einer Person, die niemand
  hereinkommen sah. Wie oft sie beginnen, lernt die App je Sensor. Im leeren Raum war das Licht
  dadurch 0 statt 8,1 Minuten (7 Mal) fälschlich an (6.10. 18:00 bis 7.10. 02:17).
- Was jeder LD2410C ohne Personen sieht, **lernt die App selbst** (gespeichert in `ld2410.json` neben der
  Geisterkarte); wird ein Sensor verschoben, beginnt es für ihn neu.
- `tools/ld2410/`: Messungen der Energien, die Prüfung „Licht an im leeren Raum“ (`phantom.py`) und eine
  Diagnose (`diag.py`).
- `tools/report_eval.py`: spielt die Aufnahmen so ab, wie die App lief (Neustarts, gelernte Karten), und
  bewertet je Raum gegen eine Wahrheitstabelle aus Meldungen, Lichtschaltungen und Nächten.
- Behoben: Der alte LD2410C-Code konnte bei langem „an“ abstürzen (`log(0)`). Das ist in der Nacht zum 7.10.
  passiert: Die App lag bis zum nächtlichen Neustart von Home Assistant (2:07) still, und weil sich der
  MQTT-Client dabei sauber abmeldete, ging ihr „offline“ nie raus; Home Assistant zeigte stundenlang die
  letzten Zustände. Jetzt hält ein Fehler im Modell die App nicht mehr an: Er wird mit Traceback
  protokolliert, und das Modell beginnt neu mit dem, was gelernt und gespeichert ist.
- Rechenzeit: Zählverteilungen aller Räume auf einmal und die Bewegung der Kacheldichten in zwei statt sechs
  Durchgängen (−4 %, gleiche Ergebnisse); OpenBLAS auf einem Thread (−7 % CPU, die Threads warteten nur).
  Insgesamt etwa so viel wie 0.9.3 (3-Sensor-Bezugsstunde 2,6 % eines Kerns, 0.6.21: 4,4 %).

## 0.9.3

- Kacheln sind einfache Quadrate von 0,4 m, an den Raumgrenzen geschnitten (statt 0,6 m, zusätzlich an den
  Sichtgrenzen jedes Sensors geschnitten). Gleiche Rechenzeit und gleich gute Erklärung der Daten
  (MODEL.md 5.3), deutlich weniger Code. Die Karte zeigt sie als Quadrate.

## 0.9.2

- Karte: Personen mit Spur sind Ellipsen mit dem Verlauf ihrer Gauß-Verteilung (bis 3 Standardabweichungen),
  an den Wänden abgeschnitten (was vom Mittelpunkt aus in Sicht ist, durch Türlücken hindurch). Personen
  ohne Spur füllen die Umrisse der Kacheln, in denen sie sein können, nach Masse je m². Statt Hunderter
  20-cm-Kästchen je Person gehen je Bild ein paar Zahlen an die Oberfläche; die Umrisse einmal.

## 0.9.1

- Fehler melden: Die Arten sind jetzt Person verloren, Geist, Person am falschen Ort, ungenaues Tracking,
  hohe Latenz und Sonstiges (statt Licht an/aus: Es gibt noch keine Lichtautomatisierung).
- Karte: Personen mit Spur werden wieder als ihre Gauß-Verteilung gezeichnet (in 0.9.0 als grobe Rechtecke
  der Kacheln). Grobe Rechtecke zeigen jetzt nur Personen ohne Spur: Genauer weiß das Modell es dort nicht.

## 0.9.0

- **Etwa ein Drittel der Rechenzeit von 0.8.1**, ungefähr so viel wie 0.6.21 (gemessen auf derselben Stunde,
  Anteil eines Kerns: 0.8.1 14,7 %, 0.9.0 4,6 %, 0.6.21 4,4 %; einzelne Frames dauerten bis 28 ms, die App
  ruckelte). Personen ohne Spur sind jetzt eine Dichte über Kacheln (Raumstücke bis 0,6 m, an Wänden und an
  den Sichtgrenzen der Sensoren geschnitten) statt über ein 0,2-m-Raster mit 8 Gehrichtungen; alles wird
  höchstens alle 0,2 s gemeinsam vorgerückt statt bei jedem Frame jedes Sensors; das zweite Raster je
  Person für „durch eine Tür“ ist weg (MODEL.md 5).
- **Der LD2410C zählt wieder** (in 0.6.x half er nachweislich, in 0.7/0.8 war er ohne Prüfung entfernt worden).
  Neues Modell aus 2,5 h Messungen gegen den LD2450 im selben Gehäuse (MODEL.md 4.3): aus heißt „niemand
  nahe vor ihm“, kurzes Aufblitzen (1,1 s) ist eine Störung, länger an heißt „jemand in der gemeldeten
  Entfernung“. Auf der Aufnahme vom 6.10. ist die Küche nach dem Gehen in wenigen Minuten leer (0.8.1: noch
  80 % belegt, obwohl beide Sensoren nichts sahen); eine Sitzende, die der LD2450 verliert, bleibt, solange
  der LD2410C sie sieht (Simulation: ohne LD2410C nach 3 min 1 %, mit über 90 %).
- **Fehler melden** im Tab *Live*: Raum, Art des Fehlers, wie lange her, ein Satz Text. Gespeichert werden die
  Sensordaten der letzten 15 Minuten mit Konfiguration und Geisterkarte, herunterladbar in der Liste.
  Daraus wird die neue Wahrheitstabelle.

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
