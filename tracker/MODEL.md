# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand: Code 0.10.0 (Entwicklung, 7.10.2026). Beschreibt, was der Code rechnet; was fehlt oder nur genähert ist,
steht in 9. Zahlen sind **gemessen** (auf Aufnahmen), **geschätzt** (EM auf Aufnahmen ohne Wahrheit)
oder **angenommen**. Literaturkürzel wie im Literaturordner (`~/Documents/presence-tracker-literatur`).
Die Abschnittsnummern werden im Code zitiert (`MODEL.md 4.1`); beim Umbau beibehalten.

## 1. Vorgaben

Diese Punkte sind Anforderungen (Leon). Alles andere in diesem Dokument ist eine Umsetzung, die man
ersetzen darf, wenn etwas Besseres belegt ist.

1. **Zweck: Licht je Raum.** Schlimmster Fehler: Licht an ohne Person. Schlecht: spätes Einschalten.
   Harmlos: kurze Aussetzer bei Sitzenden, solange sie kürzer sind als die Ausschaltverzögerung in
   Home Assistant (1–2 min); falsche Anzahl in einem belegten Raum; der Saugroboter als Person.
   Vertauschte Identitäten sind egal, Personen sind anonym.
2. **Ein generatives Modell.** Personen und Geister bewegen sich, die Sensoren erzeugen daraus ihre
   Ausgaben, der Tracker rechnet rückwärts (Bayes). Alles ist eine Wahrscheinlichkeit; keine Regel und
   keine Schwelle, die einen Mechanismus an- oder ausschaltet.
3. **Personen entstehen und verschwinden nicht im Raum**, nur über Türen und Wege nach draußen. Die
   Personenzahl gehört zum Zustand; keine feste Bewohnerzahl und kein „Gästeplatz“ (die App soll auch
   in anderen Wohnungen funktionieren).
4. **Formen aus der Literatur, Skalen aus den Daten.** Keine Schlüsse aus verrauschten Einzelfällen,
   kein Tuning auf seltene Konstellationen.
5. **Was nicht nachweislich hilft, fliegt raus; was nachweislich geholfen hat, bleibt**, bis ein
   Vergleich etwas anderes zeigt.
6. **Wahrheitsdaten nur zum Bewerten**, nie zum Einstellen oder Lernen.
7. **Läuft als Home-Assistant-App**: wenig Rechenzeit (nicht mehr als 0.6.21), reproduzierbare
   Ergebnisse. Was gelernt wird (Geisterkarte), lernt die App selbst.

## 2. Die Welt

Aus dem Grundriss (`model.py`, `world.py`):
- **Beobachteter Bereich:** Räume, die die Sensoren zu mindestens 60 % sehen.
- **Bereiche ohne Sensor *Rₖ*:** übrige Räume, über Türen zusammengefasst. *Offen*, wenn man von dort
  das Haus verlassen kann (Eingang, Treppe), sonst *geschlossen* (Balkon).
- **Außer Haus.**
- Wände trennen (Personen gehen nicht hindurch, Radar sieht nicht hindurch); Türen sind Lücken.

**Zustand:** die bekannten Personen (ihre Zahl gehört zum Zustand, 3.4) und die unbekannten (eine
Poisson-Intensität), jede an einem Ort: im beobachteten Bereich mit Position und Betriebsart *steht* (mit Aufenthaltsart l und Erkennbarkeit κ) oder *geht*
(mit Geschwindigkeit), in *Rₖ* mit der bisherigen Aufenthaltsdauer, oder außer Haus. Dazu je laufende
Sensorspur ihr Eigentümer (eine Person oder ein Geist) und ihr Versatz auf ihm, je Geisterspur ihre
Quelle.

## 3. Dynamik

### 3.1 Stehen
- Position zittert: Diffusion 0,02 m/√s (gemessen).
- Aufenthaltsdauer: Überleben `S(t) = (5,6 s / (t + 5,6 s))^0,58` (Lomax, gemessen 5.10.). Als
  Mischung von 7 Exponentialverteilungen (Feldmann & Whitt 1998): Jeder Aufenthalt hat seine eigene
  Rate λ_l des Aufstehens. Wer weitersteht, verschiebt sich zu den langsamen Arten:
  `p_l ← p_l e^(−λ_l Δt) / Σ` (Hidden-Semi-Markov als HMM, D_Yu2010 Abschn. 3.2).
- Ein Aufenthalt, der zu einem zufälligen Zeitpunkt beobachtet wird (Start), ist nach
  Erneuerungstheorie länger: Gewichte ∝ w_l / λ_l.
- Nach dem Aufstehen geht man in eine gleichverteilte Richtung.

### 3.2 Gehen
Geschwindigkeitssprung-Prozess (gemessen 6.10., 8,7 h LD2450-Spuren, Wege ≥ 2 s):
- Tempo 0,85 m/s (Median; Streuung zwischen Wegen 0,36 m/s, 10–90 % 0,44–1,37 m/s).
- Richtungswechsel auf eine gleichverteilte neue mit λ_d = 0,85 /s (Autokorrelation der
  Geschwindigkeit 0,64 / 0,46 / 0,17 nach 0,5 / 1 / 2 s; danach negativ: Umkehren an Wänden).
- Anhalten mit μ = s / ℓ, mittlere Weglänge ℓ = 2 m (gemessen, Median 2,1 m über 579 Wege).
- Wände reflektieren; durch eine Tür ist man im Bereich dahinter.

**Gauß-Näherung** (für Personen mit Spur, 5.2; eigene Herleitung): Der Prozess hat
`E[v(t)·v(0)] ∝ e^(−λ_d t)`. Ein Ornstein-Uhlenbeck-Prozess mit τ = 1/λ_d und stationärer Varianz
(s² + Streuung²)/2 je Achse hat dieselben ersten und zweiten Momente; exakt diskretisiert
(Buch_SarkkaSolin2019 Bsp. 6.2). Wände kennt sie nicht.

**Diffusionsgrenze** (für Personen ohne Spur, 5.3): Über Zeiten länger als 1/λ_d breitet sich der
Prozess wie eine Diffusion aus, je Achse mit `D = E[s²] / (2 λ_d) = (s² + Streuung²) / (2 λ_d)` =
0,50 m²/s; dieselbe Langzeitstreuung wie die OU-Näherung (2 s²_OU τ). Kürzer ist Gehen eher gerade
(ballistisch); das geht auf den Kacheln verloren.

### 3.3 Türen, Bereiche ohne Sensor, außer Haus
- Aufenthalt in *Rₖ*: log-normal, Median 2 min / Streuung von ln(Dauer) 1,5 (geschlossen) bzw.
  30 min / 2,0 (offen) (angenommen; `unobserved.py`). Nicht gelernt: Wann jemand hinein- und
  herausging, kennt der Filter nur als Wahrscheinlichkeit; seine Urteile als Dauern zu zählen, lernte
  seine Fehler. Zu schätzen über die Evidenz (7). Heraus kommt man gehend an einer Tür des Bereichs.
- Aus einem offenen Bereich nach außer Haus mit 1/(2 h); zurück mit 1/(4 h) je Weg hinein (offene
  Bereiche und Eingänge im beobachteten Bereich) (angenommen).

### 3.4 Neuankömmlinge, Personenzahl
- Neue Personen kommen als Poisson-Prozess an jedem Weg hinein an (offene Bereiche, Eingänge), mit
  ν = 1/(2 Tage) je Weg (angenommen); danach verhalten sie sich wie alle (3.1–3.3).
- Wer außer Haus ist, wird mit 1/Tag vergessen (angenommen): Kommt er danach wieder, ist er ein
  Neuankömmling. So bleibt die Zahl der Personen, die man erwartet, endlich.
- „Selten ein Dritter“ folgt aus ν und den Daten, nicht aus einer Regel.

## 4. Messmodell

### 4.1 LD2450: seine Spuren
Der LD2450 ist selbst ein Tracker (gemessen 5.10., 16 h: ~23 Spuren je Sensor und Stunde, Median
60 s; Fehler zweier Sensoren auf derselben Person nach 20 s noch zu 0,41 korreliert). Jeden Frame als
unabhängige Messung zu nehmen, macht überkonfident (E_Aeberhard2017 S. 82). Gemessen wird deshalb auf
der Ebene seiner Spuren: wann er eine beginnt, verliert, wiederfindet, und wo sie liegt.

**Vorverarbeitung** (`frames.py`, `sensortracks.py`, nur das Format):
- Schrägentfernung → Boden (Sensorhöhe, Reflexionshöhe 1,0 m), Maßstab des Sensors aus der
  Kalibrierung.
- Keine Information: Ziele mehr als 0,4 m hinter einer Wand (Sichtlinie ab 20 cm vor dem Sensor),
  außerhalb aller Räume, in geschlossenen Räumen ohne Sensor, näher als 0,3 m am Sensor.
- Die drei Plätze rücken auf; Ziele werden über die Nähe verknüpft (≤ 0,6 m je Frame).
- Gehaltene Frames sind keine Messung: ≥ 3 Frames mit derselben Geschwindigkeit ≠ 0 (der Sensor lässt
  ein verlorenes Ziel weiterlaufen) oder bitgleiche Koordinaten (eingefroren).
- Mehr als 6 s ohne Frame: Datenverlust, alle Spuren enden.

**Erfassung (neue Spur)** einer Person ohne messende Spur dieses Sensors mit der Rate
`ρ_m · P_m(r) · g_s(x) · q(x)`, je Betriebsart m:
- `P_m(r)`: Swerling-I-Kurve über der Radargleichung, `P(r) = P_FA^(1/(1 + SNR₅₀ (r₅₀/r)⁴))`,
  P_FA = 10⁻⁶. ρ, r₅₀ per Maximum-Likelihood (gemessen, 17 h): steht 0,034 /s, 5,0 m; geht 0,51 /s,
  4,55 m. Für Sitzende ist die Form eine empirische Wahl (der LD2450 unterdrückt Ruhendes).
- `g_s(x)`: Sicht nach Winkel, Reichweite und Wänden (`sensormodel.py`): voll bis 15°
  vor dem Rand des Blickfelds, 0,5 am Rand, 0 bei 15° jenseits; voll bis Reichweite + 1 m, dann
  Abfall mit e je 1,5 m (gemessen 5.10. an Gehenden).
- `q(x)`: Auflösung neben einem gemessenen Ziel,
  `1 − exp(−ln 2 ((Δr/0,6 m)² + (Δquer/0,9 m)²))` (Form A_Svensson2012 Gl. 18–20); neben einem
  gehaltenen Ziel stattdessen `1 − exp(−d²/(2·0,7²))` (dort würde es wiedergefunden).
- **Erkennbarkeit κ** einer stehenden Person in diesem Aufenthalt (Haltung, Platz): dieselbe Person
  wird am Tisch alle halbe Minute erfasst, auf dem Sofa minutenlang nicht. κ ~ Gamma(α, α), Mittel 1,
  in 5 gleich wahrscheinlichen Stufen; neu bei jedem Stehenbleiben, innerhalb eines Aufenthalts mit
  1/(600 s) neu gezogen (D_Mahler2011 Gl. 49–52; D_Wilthil2019 Gl. 2). α = 1 (angenommen).
  Gehende: κ = 1.
- Wer keine Spur bekommt, wird mit `exp(−Rate·Δt)` gewichtet; das gilt für jede Person, die von einem
  Sensor nicht schon gemessen wird.

**Verlieren und Wiederfinden:**
- Verlieren ist bei Personen und Geistern gleich häufig: keine Information.
- Zeit bis zum Wiederfinden (gemessen): log-normal, Median 0,13 s / Streuung 1,0 (weiterlaufend) bzw.
  0,2 s / 2,0 (eingefroren); 3 % bzw. 2 % nie.
- Wiedergefunden wird auf dem Eigentümer, auf einer anderen Person nahe der gehaltenen Stelle
  (Gauß-Kern 0,7 m × Sicht), auf dem Geist, der sie war, oder auf einer Reflexion dort (wie 0,05
  Personen). Nicht-Wiederfinden spricht gegen alle diese, ohne Frist.
- Fallenlassen der Spur: keine eigene Information (für Geister: Lebensdauer, 4.2).

**Position:** `z = x + c + o + w`. Versatz c konstant je Spur, o wandert (OU, τ = 3,5 s), w = 5 cm.
57 % der Versatzvarianz bleibt, 43 % wandert (gemessen). Gesamtstreuung entlang 0,15 m + 2 %, quer
0,10 m + 5 % der Entfernung; im Filter je Achse ihr Mittel. c und o sind Teil des Kalman-Zustands
(E_BrysonHenrikson1967). Verwendet wird höchstens alle 0,25 s eine Position.

**Start und Datenlücken:** Was ein Sensor beim ersten Frame (oder nach einer Lücke) schon verfolgt,
wird nicht als Geburt gewertet: Eine Person ist zu einem beliebigen Zeitpunkt mit dem Anteil
`a·L / (1 + a·L)` erfasst (a Erfassungsrate, L Lebensdauer einer Spur, gemessen 60 s stehend, 30 s
gehend); Geister entsprechend mit ihrer Lebensdauer.

### 4.2 Geister
Eine Spur, die zu keiner Person gehört.
- **Entstehen** je Sensor mit `λ_s(x) + λ_e · (erwartete Gehende im Blickfeld)` je m² und s.
  - `λ_s(x)`: **Geisterkarte** (`ghostmap.py`), je Sensor und Zelle (0,4 m) ein Poisson-Prozess mit
    Gamma-Prior (J_Luber2014 Kap. 6, Gl. 6.8–6.13). Prior-Mittel 1,8·10⁻⁵ /m²/s (geschätzt 6.10. per
    EM auf 21 h), Gewicht 4 h Beobachtung je Zelle. Zählungen mit 0,3 m verschmiert (Versatz des
    Sensors; J_Park2020 Abschn. 1).
  - `λ_e` = 3·10⁻⁴: Mehrwegeechos laufen mit Gehenden mit (angenommen).
- **Lebensdauer:** Mischung zweier Exponentialverteilungen, Startwert 40 % mit 3 s, 60 % mit 39 s
  (geschätzt per EM 6.10.); wird mit der Karte weitergelernt.
- **Quelle:** bewegt sich wie eine Person (3.1, 3.2), mit demselben Versatz. Geist und Person
  unterscheiden sich durch Entstehungsort, Lebensdauer und Verhalten der Spur, nicht durch ein anderes
  Rauschen.
- **Lernen in der App** (online-EM, I_Kantas2015 Abschn. 5): Endet eine Spur, zählt sie an ihrem
  Startpunkt mit P(Geist) des Filters; die Beobachtungszeit jedes Sensors ist die Belichtung; die
  Lebensdauern werden gleich gewichtet. P(Geist) wird am Ende der Spur beurteilt (Bewegung, andere
  Sensoren, Lebensdauer zählen mit), aber ohne das, was die Karte selbst an dieser Stelle sagte: Ihr
  Faktor bei der Geburt wird aus den Odds herausgerechnet, als hätte dort die Prior-Rate gegolten
  (J_Park2020 Gl. 37–41: Clutter-Wahrscheinlichkeit ohne die Clutter-Schätzung). Sonst bestätigt
  sich die Karte selbst (Simulation: Sitzplatz mit vorgegebener Karte, alt bis 0,34 je Spur gelernt,
  jetzt < 0,01). Vergessen mit Zeitkonstante 14 Tage. Wird ein Sensor
  verschoben, gedreht, anders kalibriert (Höhe, Spiegelung, Maßstab), hinzugefügt oder entfernt,
  beginnt die Karte neu. Die Oberfläche zeigt sie je Sensor. `tools/ghostmap.py` rechnet dasselbe
  offline auf gewählten Zeitfenstern.
- Begründung: Wiederkehrende Reflexionen (Möbel, Ladestation des Saugroboters) stehen an festen
  Stellen. Mit gleichverteilter Dichte erklärt das Modell sie als Person.

### 4.3 LD2410C
Gemessen wird seine **Energie je Entfernungsring** (0,75 m Schrägentfernung, `ld2410.py`): bewegt in Ring
0–8, ruhig in Ring 2–8 (ruhig 0 und 1 sind immer 0), 0–100, 16 Zahlen je Frame. Seine Flags und die
gemeldete Entfernung zählen nicht (10). Gemessen 6.10. (4,3 h, 5 Sensoren, `tools/ld2410/`), Bezug die
Ziele des LD2450 im selben Gehäuse (der unterdrückt Sitzende und hat Geister: nur zum Bedingen):
- Das **Ruhig-Flag ist die Firmware-Schwelle** auf die Ruhig-Energien (gleich in 90–99 % der Frames); die
  Energien enthalten es. Das **Bewegt-Flag** ist keine Schwelle auf die gemeldeten Energien (68–84 %);
  ohne jedes Ziel ist es 2–4 % der Zeit an (die Blitze, 1,1 s), ohne dass die Energien etwas zeigen.
- **Ohne Person** (kein LD2450-Ziel ±30 s, nach Zeit gewichtet): Mittel je Sensor und Ring verschieden,
  bewegt Ring 0 9–16, Ring 1 6–12, sonst 3–5; ruhig 3–7; 99 % unter 30. Stundenweise schwankt es
  (Flur ruhig Ring 5–6 18–19 Uhr 8–10 statt 3,4). Die Streuung wächst mit dem Pegel (Variationskoeffizient
  ≈ 0,5): eine lineare Größe, keine dB-Skala.
- **Profil einer Person** über alle Ringe (Frames mit genau einem LD2450-Ziel, Maximum-Likelihood,
  `tools/ld2410/fit.py`): `A · r^−n · k(c − r − δ)`, r Schrägentfernung, c Mitte des Rings, k eine
  Gauß-Hauptkeule (σ) und dahinter ein exponentieller Schweif (Anteil τ, Länge ℓ; Mehrwegeechos haben
  einen längeren Weg: Leistungs-Verzögerungs-Profil, Saleh & Valenzuela 1987):

  | | A (1 m) | n | δ | σ | τ | ℓ |
  |---|---|---|---|---|---|---|
  | bewegt, geht | 159 | 2,16 | 0,41 m | 0,57 m | 0,47 | 1,26 m |
  | bewegt, steht | 63 | 2,52 | 0,41 m | 0,55 m | 0,27 | 1,34 m |
  | ruhig, geht | 949 | 2,19 | 0,58 m | 1,44 m | 1,00 | 1,25 m |
  | ruhig, steht | 574 | 2,03 | 0,19 m | 0,77 m | 0,40 | 1,70 m |

  n ≈ 2: eine Amplitude (Radargleichung). Der Schweif ist kein Sonderfall eines Raums (Arbeitszimmer
  allein und die übrigen vier: τ 0,42 / 0,39, ℓ 1,59 / 1,67 m). Nach dem Winkel flach bis 45–60°
  (Strahl voll bis 50°, 0 ab 70°, angenommen darüber). Die Amplitude einzelner Aufenthalte streut um
  das Profil (`tools/ld2410/amplitude.py`, Aufenthalte einer Person ≥ 30 s, 6.10. 18–23 Uhr, Gewinn g
  auf dem Profil je Aufenthalt): log g Streuung 0,93 ruhig / 0,66 bewegt (nach Abzug der
  Schätzunsicherheit), als Gamma(β, β) β 1,6 / 2,8, 10–90 % g 0,14–2,3; innerhalb eines Aufenthalts
  beständig (Hälften korreliert 0,88 / 0,71), ruhig und bewegt derselbe Gewinn (0,88); er geht mit der
  Erkennbarkeit des LD2450 (Wiederfinderate je Aufenthalt, innerhalb Sensor und 1 m Entfernung,
  Spearman 0,62). Im Modell ist er nicht (10).
- **Zeit:** Die Ruhig-Energien folgen einer Person mit etwa 2 s (zwei Fälle: Kommen und Gehen; nah
  gekappt bei 100, darum dort 3 s länger voll). Korrelationszeit des Log-Likelihood-Verhältnisses „Person
  / niemand“: 4 s bewegt, 13 s ruhig.
- **Gemeinsamer Pegel:** Alle Ringe einer Art schwanken zusammen (Korrelation zwischen Ringen ohne
  Person 0,2–0,7; Streuung des gemeinsamen log-Pegels je Sekunde 0,14–0,27 bewegt, 0,09–0,18 ruhig);
  dazu Breitband-Schübe (alle Bewegt-Ringe 15–30 für etwa eine Sekunde).

Das Modell, je Sensor:
- **Messung:** jede Energie ~ Gamma(Form α, Mittel μ), 100 zensiert (`P(e ≥ 99,5)`); α 2,5 bewegt, 2,0
  ruhig (gemessen 2,4–3,0 / 1,6–2,4). Die Form der Likelihood je Auflösungszelle wie bei
  Track-before-detect (Salmond & Birch 2001; Boers & Driessen 2004), Amplitude als Merkmal wie Lerro &
  Bar-Shalom 1993.
- **Mittel = (Hintergrund + Summe der Personen) × gemeinsamer Pegel** (inkohärente Überlagerung,
  superpositionaler Sensor): `μ_g = u · (b_g + w_m·M_g + (1 − w_m)·Σ_i S_g(x_i))`. S ist das Profil oben
  mal Strahl mal Sicht (Wände wie beim LD2450). Ruhig zählt das Verzögerte: M ist, was die Personen
  zuletzt hineingaben (geglättet mit 2 s), w_m sein Anteil im Block; bewegt w_m = 0.
- **Gemeinsamer Pegel u** je Block (1 s) und Art: 1/u ~ Gamma(κ, κ), κ = 16 bewegt, 50 ruhig (gemessen,
  s. o.), gemischt mit 2 % Schüben, 1/u ~ Gamma(4) mit u um 2,5 (angenommen; nur nach oben: ein
  niedriger Pegel darf eine fehlende Person nicht entschuldigen). Konjugiert: exakt herausintegriert.
  Ohne ihn erklärte eine gleichmäßig erhöhte Energie aller Ringe (nachts im Arbeitszimmer ruhig
  doppelt so hoch wie gelernt, Schübe) eine Person am Rand des Strahls.
- **Hintergrund b je Sensor, Ring und Art: von der App gelernt** (wie die Geisterkarte): Online-EM der
  Überlagerung, der Anteil des Hintergrunds an einer Energie ist im Mittel `e · b/μ` (Richardson 1972;
  Shepp & Vardi 1982), μ aus dem Stand vor diesen Frames (die Energien lernen nicht ihr eigenes Urteil,
  J_Park2020). Prior: bewegt Ring 0 13, Ring 1 9, sonst 4,5, ruhig 5 mit dem Gewicht 10 min; Vergessen
  6 h (angenommen). Neu für einen Sensor, wenn er verschoben wird. Gespeichert mit der Geisterkarte.
- **Zeit:** Die Frames werden je Sekunde gesammelt (Gamma: die Summen von Δt, Δt·e, Δt·ln e und Δt der
  gekappten genügen), jeder Frame mit Δt / τ, Δt = Zeit seit dem vorigen Frame des Sensors bis 6 s
  (4.4: ohne Anlass sendet die Firmware nur alle 5 s, die Energien lagen dazwischen unter den
  Schwellen), τ 4 s bewegt / 13 s ruhig (zusammengesetzte Likelihood mit der Korrelationszeit als
  effektiver Stichprobengröße; Varin, Reid & Firth 2011).
- **Verrechnung:** Kein Produkt über die Personen. Je Hypothese die Personen nacheinander (erst die mit
  Spur, dann die ohne, zuletzt die unbekannten), jede gegeben die vorigen mit dem, was sie danach
  hineingeben (exakt für Personen an bekanntem Ort). Jede Person (Kacheln bzw. Komponenten an ihrem
  Mittel) wird dann mit `ℓ(e | Rest + S(x)) / ℓ(e | Rest)` gewichtet, der Rest = alle anderen, gemischt
  über die Hypothesen, die sie halten (Marginale wie bei JIPDA; was andere erklären, sagt über diese
  Person nichts, 0.6.13). Die unbekannten Personen: höchstens eine von ihnen im Blick (Poisson nach
  einer abgeschnitten, danach wieder per Momentenabgleich); wer hinter einer Tür oder außer Haus ist,
  gibt nichts hinein.
- **Echoquellen** (`ld2410.Echoes`): Energie, die von keiner Person kommt (7.10. 01:07:52–01:08:34 in
  der leeren Küche: bewegt Ring 0–1 Schübe bis 100, ruhig Ring 2–4 bis 60–90, kein LD2450-Ziel, niemand
  ging). Wie die Geister des LD2450 (4.2) unterscheidet sie, wie sie beginnt und endet: Je Sensor aus
  oder eine Quelle an – das Profil einer Stehenden auf der Achse in 0,75–6,5 m (Schritt 0,5 m) mal
  0,3 / 1 / 3 –, Beginn mit der Rate ρ (gleich über die Quellen), Lebensdauer Erlang(2) mit Mittel 20 s.
  Gemessen in der leeren Nacht 22:56–02:15 (3,3 h, Ruhig-Energie über 3× ihrem Pegel ≥ 3 s, ohne
  LD2450-Ziel ±60 s): Küche 3,6 je Stunde (Dauer Median 10 s, Mittel 15 s, bis 59 s), die anderen vier
  Sensoren keine. ρ lernt die App je Sensor mit dem Hintergrund (erwartete Zahl begonnener Quellen
  je Beobachtungszeit; Prior 1 je 3 h mit dem Gewicht von 3 h, angenommen).
  Verrechnung: Die Personen wie oben, die Echoquellen zuletzt (für die Gewichte der Hypothesen). Eine
  Person ohne Spur und eine Echoquelle sind Alternativen (beide selten): Die Dichte einer Person
  bekommt im Blick `P(keine Quelle) · ℓ(Person)`, außerhalb `P(keine Quelle) + Σ P(Quelle) ℓ(Quelle)`.
  Die Echoquellen selbst werden gegen die Personen mit Spur und die übrigen so, wie sie vor diesen
  Frames waren, gewichtet (sonst erklärt eine von der Energie hereingezogene Person die Quelle weg).
  Exakt gerechnet (Prototyp `tools/ld2410/clutter_hmm.py`) spricht das Ereignis von 01:08 gegen eine
  Person (log Bayes-Faktor Person + Quelle / Quelle −5,0 über 56 s, höchstens +2,5 zwischendurch), eine
  Sitzende im Esszimmer (21:03) dafür (+9,0 nach 40 s, +17,3 nach 2 min).
- Alles in Log-Größen; Verhältnisse auf e^600 begrenzt.

### 4.4 Lücken zwischen Frames
Die Firmware sendet leere Frames nur alle 5 s. Eine Lücke bis 6 s zählt als beobachtet und leer;
eine längere sagt nichts.

## 5. Inferenz

Deterministisch: Was diskret ist und wenige Werte hat, wird aufgezählt, nicht gezogen. Das Ergebnis
hängt nicht davon ab, wie die Zeit zerlegt wird (Test).

**Takt:** Alle Personen und Geisterquellen werden gemeinsam vorgerückt und mit „keine neue Spur“
gewichtet, höchstens alle 0,2 s (`MAX_STEP`), und sofort vor jedem Ereignis, das alle betrifft:
neue Spur, wiedergefundene, endende, Start oder Datenlücke. Eine gemessene Position rückt nur die
Gauß-Mischungen vor, zu denen ihre Spur gehört; jede hat dafür ihre eigene Zeit. Zwischen zwei
Takten wirkt „gehalten, nicht wiedergefunden“ auf den Stand des letzten Takts, und die Ausgaben
werden nur nach einem Takt oder Ereignis neu berechnet (bis 0,2 s alt). Vorher (0.8) wurde bei jedem
Frame jedes Sensors alles vorgerückt: etwa 23-mal je Sekunde, ein Hauptteil der Rechenzeit.

### 5.1 Hypothesen über die Spuren
- Welche laufenden Spuren zu derselben Person gehören und welche Geister sind, ist eine diskrete
  Hypothese mit exaktem Gewicht (Datenassoziation eines PMBM, B_GarciaFernandez2018; δ-GLMB,
  B_Reuter2014).
- Eine neue Spur verzweigt jede Hypothese: Geist; eine Person, die schon Spuren anderer Sensoren hat;
  eine bekannte Person ohne Spur; eine unbekannte (5.5). Eine wiedergefundene: ihr Eigentümer; eine
  andere Person nahe der Stelle; Geist/Reflexion.
- Behalten werden höchstens 12, solange über 10⁻⁷ des stärksten (Abschneiden nach Gewicht,
  B_Vo2017).
- Gegeben eine Hypothese sind die Personen unabhängig. Die Zählverteilung je Raum ist die Faltung
  der Einzelwahrscheinlichkeiten der bekannten (Poisson-Binomial) mit einer Poisson-Verteilung für
  die unbekannten, gemischt über die Hypothesen.

### 5.2 Personen mit Spur: Gauß-Mischung (IMM)
`gauss.py`. Gilt, solange eine Person mindestens eine laufende Spur hat (gemessen oder gehalten).
Zwei Komponenten *steht* / *geht*, je ein Kalman-Filter über Position, Geschwindigkeit und je Spur
(c, o); *steht* führt die Wahrscheinlichkeiten der Aufenthaltsarten und Erkennbarkeitsstufen. Je
Schritt (Buch_SarkkaSvensson2023 S. 352; B_Li2019 Gl. 26–33):
1. Übergänge *steht → geht* (`Σ p_l (1 − e^(−λ_l Δt))`) und *geht → steht* (`1 − e^(−μΔt)`), je
   Ziel-Betriebsart per Momentenabgleich zu einer Komponente zusammengefasst.
2. Lineare Vorhersage je Komponente (3.1 bzw. OU-Näherung 3.2), Kalman-Update mit der Spur.
3. Nicht-Erfassung durch andere Sensoren: Faktor je Komponente, am Mittel der Komponente.
4. **Durch eine Tür** in einen Bereich ohne Sensor: Die Komponente *geht* verliert dorthin mit der
   Rate der Türkacheln (5.3), gewichtet mit ihrer Masse dort (jede Türkachel als ihr kleiner Gauß).
   Das wandert in einen Teil „durch eine Tür“ (eine Dichte über Kacheln und Bereiche mit Gewicht a).
   Eine Messung der Spur sagt „in Sicht“ und streicht ihn. Zwischen zwei Räumen mit Sensor gibt es
   keinen solchen Teil: Dort ist eine Tür nur eine Lücke in der Wand.

### 5.3 Personen ohne Spur: Kacheln
`tiling.py`, `hidden.py`. Punktmassenfilter (0_Arulampalam2002 Abschn. II-B) über Kacheln statt über
ein gleichmäßiges Raster:
- **Kacheln:** Quadrate von 0,4 m, an den Raumgrenzen geschnitten (ein Quadrat über eine Wand sind zwei
  Kacheln). Innerhalb einer Kachel wird der Ort nicht unterschieden: gleichverteilt. Wo ein Ort
  gebraucht wird (eine Spur beginnt, Gehen durch eine Tür), zählt die Kachel als die
  Gauß-Verteilung mit ihrem Schwerpunkt und ihrer Streuung (dieselben ersten zwei Momente).
  Verglichen am 6.10. auf 20 min (CPU / Log-Evidenz): 0,6 m an den Sichtgrenzen der Sensoren
  geschnitten 53 s / 17039,6; einfache Quadrate 0,6 m 50 s / 17030,1, 0,4 m 55 s / 17039,5, 0,3 m
  62 s / 17036,6; 0,4 m mit der Gleichverteilung exakt integriert (Fehlerfunktion) 62 s / 17032,2.
  Der Schnitt an den Sichtgrenzen und das exakte Integral bringen nichts, was den Code wert wäre.
  Wohnung 6.10.: 341 Kacheln statt 1153 Zellen zu 0,2 m.
- **Raten** (Erfassung, Wiederfinden) sind je Kachel das Mittel über ihre Punkte eines 0,2-m-Rasters:
  die Projektion der Likelihood auf den gröberen Zustand (G_Liao2003 Gl. 2).
- **Zustand:** `geht[Kachel]`, `steht[Art l, Stufe κ, Kachel]`, `Bereich[k, Alter]` (Altersklassen
  2 s … 36 h), `außer Haus`.
- **Gehen** zwischen Kacheln als Diffusion (3.2) in finiten Volumen: von Kachel i zum Nachbarn j mit
  `D · L_ij / (A_i · d_ij)` (gemeinsame Kante L, Fläche A, Abstand der Schwerpunkte d), durch eine
  Tür in einen Bereich ohne Sensor ebenso, mit d = doppelter Abstand zur Tür. Wände sind keine
  Kante. Exakt diskretisiert als Matrixexponential je Takt von 0,1 s. Anhalten mit μ, Aufstehen mit
  λ_l wie in 3.1/3.2. Eine Tür zwischen zwei Räumen mit Sensor ist eine Kante wie jede andere.
- Nicht-Erfassung, Nicht-Wiederfinden und die Rate einer neuen Spur sind exakte Summen über die
  Kacheln.

Start („nichts bekannt“): `start_people` = 2 bekannte Personen, je 1/3 im beobachteten Bereich
(stehend, gleichverteilt nach Fläche), 1/3 in den Bereichen ohne Sensor, 1/3 außer Haus; keine
unbekannten. Ein Poisson-Start erwartet auch nach zwei gefundenen Personen noch genauso viele weitere
(seine Zahlen sind unabhängig): Simulation, zwei kommen herein, danach P(3 im Haus) = 0,35.

### 5.4 Wechsel der Darstellung
- **Kacheln → Gauß**, wenn eine Spur auf der Person beginnt oder wiedergefunden wird: je Kachel
  Masse × Erfassungsrate × Dichte der Messung (Kachel-Gauß gefaltet mit dem Messrauschen), die
  Position je Kachel als Produkt der beiden Gauß-Verteilungen, je Betriebsart per Momentenabgleich
  zu einer Komponente („Partikel zu einem Gauß zusammenfassen“, J_Luber2014 Abschn. 6.5, Gl. 6.32).
  Die Geschwindigkeit eines Gehenden ohne Spur ist unbekannt (Mittel 0). Die Aufteilung von `z − x`
  auf c und o folgt aus ihren Varianzen (eigene Herleitung).
- **Gauß → Kacheln**, erst wenn die letzte laufende Spur endet, nicht schon beim Verlieren (gehaltene
  Spuren behalten so ihren Versatz). Komponenten werden mit ihrer Dichte auf die Kacheln verteilt;
  verloren gehen Geschwindigkeit und Versätze.

### 5.5 Die unbekannten Personen
`hidden.Undetected`: der unentdeckte Teil des PMBM (B_GarciaFernandez2018 Gl. 7–10, 18–24) über den
Kacheln aus 5.3, als Intensität (Massen sind erwartete Zahlen). Dieselbe lineare Bewegung, dazu die
Ankünfte und das Vergessen aus 3.4.
- Wer nicht erfasst wird, wiegt mit der Leerwahrscheinlichkeit `exp(−(Masse vorher − nachher))`.
- Eine neue Spur auf einer unbekannten Person hat das Gewicht `∫ Intensität × Erfassungsrate ×
  Dichte der Messung`; daraus wird eine bekannte Person (5.2). Die Intensität bleibt (eine Geburt ist
  ein Punkt des Prozesses).
- **Zurückgeben:** Eine bekannte Person ohne Spur, die zu weniger als 1 % im Haus ist, geht mit ihrer
  ganzen Dichte in die Intensität (Bernoulli → Poisson). Das ändert nur P(mehrere davon kommen
  zurück), um höchstens 0,01²/2. Sonst würde jeder Gast für immer verfolgt. (Die Literatur verwirft
  Bernoulli-Teile unter 10⁻⁵, B_GarciaFernandez2018 Abschn. VII; hier geht keine Masse verloren.)

### 5.6 Zusammenlegen
Hypothesen, die über alle laufenden Spuren dasselbe sagen, werden eine:
- Personen mit denselben Spuren: Gauß-Mischungen vereinigt, je Betriebsart per Momentenabgleich.
- Personen ohne Spur sind austauschbar: so gepaart, dass die Summe der L1-Abstände minimal ist, dann
  gemischt (eigene Herleitung; beliebige Reihenfolge erzeugt „je zu 50 % hier und dort“ statt „einer
  hier, einer dort“, B_Reuter2014 Fig. 1–3). Für mehr als eine Person ist das die
  Multi-Bernoulli-Näherung.

## 6. Ausgaben
- **Je beobachtetem Raum:** Verteilung der Personenzahl (5.1); an Home Assistant gehen die
  wahrscheinlichste Zahl und P(belegt) = 1 − P(0) (Attribut `probability`, in 5-%-Schritten).
- **Belegt (Licht):** `P(belegt) > c` mit `c = K_an / (K_an + K_dunkel)`, der Schwelle mit den
  kleinsten erwarteten Kosten (Bayes-Entscheidung; I_GneitingRaftery2007 S. 364–365, Satz 3).
  `light_cost` = K_an / K_dunkel = 2 (angenommen: Licht ohne Person ist der schlimmste Fehler), also
  c = 2/3. Das verzögerte Ausschalten bleibt in Home Assistant.
- **Bereiche ohne Sensor:** P(jemand dort); belegt wie oben, wenn der Bereich nur ein Raum ist.
- **Bewegt / ruhig, „wird gleich betreten“:** aus den Personen der wahrscheinlichsten Hypothese
  (geht-Gewicht > 0,5 und > 0,15 m/s; Vorausschau 1 s ab 0,3 m/s).
- Anzeige: Personen der wahrscheinlichsten Hypothese, ihre Dichten als Wärmekarten.

## 7. Evidenz
Die Summe der Normierungen ist die Log-Evidenz der Aufnahmen (`Tracker.loglik`; Summe der
prequentiellen Log-Scores, I_GneitingRaftery2007 S. 372). Weil der Filter deterministisch ist, ist
sie glatt in den Parametern und Vergleiche brauchen keine Seeds (I_Kantas2015 S. 8–9). Damit sollen
Parameter und Modellvarianten auf Aufnahmen ohne Wahrheit verglichen werden. Bisher ist damit nur die
Lebensdauer der Geister geschätzt (4.2).

## 8. Prüfung
- **Wahrheitsdaten:** am 6.10.2026 gelöscht, die Bewertung wird neu aufgebaut. Maßstab sind die
  Lichtfehler aus 1.1 je Raum (keine einzelne Prozentzahl).
- **Tests** (`tracker/tests`): bitgleiche Läufe, Unabhängigkeit von der Zeitzerlegung,
  Bewegungsstatistik, Szenen aus `sim.py`, Invarianten der Buchführung (`Tracker.check`).
- **Geplant:** simulationsbasierte Kalibrierung (0_Talts2018 Alg. 1): Welten aus diesem Modell
  erzeugen; der Rang der wahren Zahl unter der Filterverteilung muss gleichverteilt sein.
- Werkzeuge: `tools/record.py` (Aufnahme), `tools/replay.py` (Aufnahmen durch den Tracker),
  `tools/ghostmap.py` (Geisterkarte offline), `tools/calibrate_offline.py` (Kalibrierung aus
  Alltagsaufnahmen, auch ohne große Überschneidung; 10).

## 9. Bekannte Schwächen und Offenes
Offen beim LD2410C (4.3): Die Echoquellen schlucken auch, was eine Person mit Spur über das mittlere
Profil hinaus abgibt (Flur 21:35 und 21:56: Eintretende zu 0,5 statt 0,9 im Flur); eine Amplitude je
Aufenthalt half dagegen nicht (10). Ob und wie stark er durch Wände und Türen sieht (bisher: gar nicht; die zweite Person im
Bad erscheint im Arbeitszimmer, wenn doch); die Amplitude einzelner Aufenthalte streut um einen Faktor
2–4 um das Profil (4.3), das Modell nimmt das mittlere; δ ist gegen teils
unkalibrierte LD2450 gemessen (0,07–0,5 m je nach Sensor); die Addition mehrerer Personen ist nicht an
Zwei-Personen-Zeiten geprüft; α, τ, κ, die Verzögerung und das Vergessen des Hintergrunds sind über die
Evidenz (7) zu schätzen; das Bewegt-Flag ist ohne Ablation weggelassen. Ein Raumteil, den kein Sensor
sieht (Küche hinter der Wand bei y = 7,7 m), kann Personen halten, die niemand ausschließt.

Näherungen, die man prüfen oder ersetzen kann:
- Nicht-Erfassung und Erfassungsrate einer Gauß-Komponente am Mittel statt über ihre Verteilung.
- Auf den Kacheln ist Gehen eine Diffusion: Kurzzeitig gerades Gehen und die Richtung gehen verloren,
  beim Wechsel Gauß → Kacheln auch Geschwindigkeit und Versätze. Innerhalb einer Kachel ist die
  Masse nicht weiter aufgelöst; Raten sind über die Kachel gemittelt, auch wo eine Sichtgrenze sie
  schneidet. Die
  Gauß-Näherung kennt keine Wände.
- Zwischen zwei Takten (0,2 s) wirken „gehalten, nicht wiedergefunden“ und die Ausgaben auf einen
  bis 0,2 s alten Stand.
- Versatzvarianz je Achse gemittelt, obwohl entlang/quer verschieden gemessen.
- Jenseits von 7 m zählt die Entfernung zweimal: in `g_s` (Abfall ab Reichweite + 1 m, aus 0.6.17)
  und in `P_m(r)`. Gegen welches `g_s` ρ und r₅₀ gefittet wurden, ist nicht festgehalten.
- Erkennbarkeit nur für Stehende; α, Wechselrate, Ankunft/Weggang, Aufenthalts-Prior und λ_e sind
  angenommen, nicht über die Evidenz geschätzt. Das Kostenverhältnis `light_cost` ist zu klären.
- Die Geisterkarte lernt weiter aus dem Urteil des Filters, nur ohne ihren eigenen Beitrag an der
  Stelle. Ein reines Urteil bei der Geburt war zu früh (Hereinkommende an der Tür: P(Geist) 0,56).
- Höchstens 12 Hypothesen; Paarung beim Zusammenlegen bis 6 Personen. Hypothesen mit verschieden
  vielen bekannten Personen werden nicht zusammengelegt. Jede neue Spur kann auch „unbekannt“ sein:
  Solange Spuren laufen, gibt es mehr Hypothesen (Simulation, zwei kommen herein: etwa doppelte
  Rechenzeit gegenüber fester Personenzahl).
- Wer ins Schlafzimmer oder zur Treppe geht, bleibt wegen des breiten Aufenthalts-Priors lange
  „bekannt“ (6 h nach dem Gehen noch zu 21 % im Haus) und kostet so lange Rechenzeit.
- ν, das Vergessen und `start_people` sind angenommen.

Nicht geprüft (Ablationen ausstehend): λ_d = 0,85 gegen langsamere Richtungswechsel; OU-Näherung
gegen weißes Rauschen in der Beschleunigung; Swerling-I gegen logistisch; Geisterkarte gegen globale
Rate; Form der Erkennbarkeit.

## 10. Erfahrungen
Was frühere Versionen gezeigt haben. Die Zahlen stammen von gelöschten Wahrheitsdaten oder anderen
Filtern; das sind Hinweise, keine Verbote. Ein neuer Ansatz darf sie neu prüfen.

- **Frames als unabhängige Messungen** machen überkonfident; Kovarianz aufblähen half in 0.6.x nicht.
  Daher die Spuren als Messgröße.
- **Partikel über diskrete Zustände mit wenigen Werten** (steht/geht, Aufenthaltsart) streuen stark
  zwischen Seeds und verpassen Seltenes („kein Partikel ist aufgestanden“). Daher aufzählen statt
  ziehen.
- **Person aufs Raster schon beim Verlieren der Spur:** Der gelernte Versatz ging verloren, beim
  Wiederfinden passte die Messung ~9× besser zu einem Geist.
- **Ohne Erkennbarkeit κ** ist eine Sitzende, die minutenlang nicht erfasst wird, extrem
  unwahrscheinlich, und das Modell schickt sie zur Tür.
- **Ohne Geisterkarte** werden wiederkehrende Reflexionen (Ecken, Ladestation, Küchentür) zu Personen
  (0.6.x und 0.8-Entwicklung).
- **Zeitbasierte Leckraten** („nach x s ungesehen ist sie weg“) schicken Sitzende in Sekunden zur
  Tür. Masse darf nur durch Gehen wandern.
- **Wiener-Prozess für die Geschwindigkeit** (bis 0.6.21): Gehende behalten ihre Richtung viel zu
  lange und sind zu schnell. Eine OU-Geschwindigkeit war im Partikelfilter 0.6.x trotzdem schlechter
  (Commit 5379fd1); im IMM ist sie ungeprüft drin.
- **Rechenzeit** (gemessen 6.10., dieselbe Stunde, 3 Sensoren, Anteil eines Kerns): 0.6.21 4,4 %,
  0.8.1 14,7 % (Raster 0,2 m mit 8 Richtungen, je Person mit Spur ein zweites Raster für „durch eine
  Tür“, alles bei jedem Frame vorgerückt; einzelne Frames bis 28 ms, die App ruckelte), 0.9 mit
  Kacheln und Takt 4,0 % (Log-Evidenz +29 gegenüber 0.8.1 auf 20 min), mit LD2410C 4,6 %. Die Form der
  Kacheln macht dabei kaum etwas aus (5.3); gespart haben die wegfallenden 8 Richtungen und der Takt.
  7.10., 5 Sensoren, je 20 min der Stunden 21/22: 0.9.3 2,2 / 3,5 %; die Zählverteilungen aller Spalten
  auf einmal und die Bewegung der Kacheldichten in zwei statt sechs Durchgängen über `steht` 2,1 /
  3,3 % (−4 %, Ergebnisse bis auf Rundung gleich). Alle Kacheldichten in gemeinsamen Arrays, gemeinsam
  bewegt und gewichtet (Branch `stack-densities`), sparte darüber hinaus nichts: die Zeit steckt in
  der Arithmetik über die L·K = 35 Schichten von `steht` und in der Gehmatrix (518 Kacheln, ~20 µs je
  Dichte und Takt), nicht in den numpy-Aufrufen. Als ein Matrixprodukt über alle Dichten startet
  OpenBLAS ab drei Dichten Threads, deren Warten ein Vielfaches an CPU kostet. Größte Posten danach:
  `Gauss.predict` 22 %, Kacheldichten bewegen 15 % und gewichten 8 %, LD2410C 16 %, Ausgaben 13 %.
  Mit den Energien des LD2410C (7.10., 5 Sensoren, 21 Uhr): 3,8 % statt 2,8 % mit Flag und
  Entfernung. Die Energien selbst kosten etwa ein Sechstel (je Sensor und Sekunde ein Verhältnis je
  Kachel und Person); der Rest kommt von mehr getrennten Dichten über die Hypothesen (im Mittel 8,6
  statt 4,6 Personen auf Kacheln).
- **LD2410C ohne Modell seiner Reichweite** (0.9-Entwicklung): Mit fester Reichweite 3,3 m musste das
  lange „an“ des Wohnzimmersensors (Sitzende im Esszimmer in 6,2 m) von einer erfundenen Person nahe
  am Sensor kommen. Daher hält jetzt, wer in der gemeldeten Entfernung ist. Ohne LD2410C blieb in der
  Küche nach dem Gehen minutenlang „zu 80 % jemand da“, obwohl beide Sensoren nichts sahen.
- **Gauß an Wänden abgeschnitten** (7.10., verworfen): abgeschnittener Kalman-Filter
  (H_SimonSimon2006) nach jeder Vorhersage, an den achsparallelen Wänden auf der Seite des Mittels.
  Half in einem Fall: Wer auf den Küchensensor zuging (Lichtschalter, 6.10. 21:57), lief ohne ihn in
  der Vorhersage durch die Wand, die wiedergefundene Spur wurde ein Geist (Küche 0,3, Esszimmer 0,6;
  mit ihm 1,0 / 0,0). Schadete aber mehr: Der nicht kalibrierte Küchensensor misst Personen etwa 1 m
  zu weit hinten, also hinter der Wand zum Vorratszimmer (y 7,72). Mit dem Abschneiden blieb die
  Gauß-Verteilung dort gefangen, wenn die Spur abriss, und wurde eine ungesehene Person im
  Vorratszimmer; bis zu vier solcher Personen sammelten sich in der Ecke (4,5 / 8,1). Küche 22:01–22:15
  P(belegt) 0,8–1,0, obwohl leer (Energien auf Grundniveau, Licht nur vergessen); danach die ganze
  Nacht 0,1–0,2 und um 01:08 bei einem Energiestoß 38 s über der Schwelle. Wände setzen einen
  Grundriss und Kalibrierungen voraus, zu denen die Messungen passen.
- **Küchensensor „hinter der Wand“** (7.10., Aufnahmen ab 6.10. 18:10): Seine Ziele jenseits der
  Außenwand sind Mehrwegechos von Personen in Ess-, Wohnzimmer oder Flur, kein falscher Maßstab:
  Bei allen misst gleichzeitig (±0,5 s) ein anderer Sensor dort jemanden (bei Zielen in der Küche
  47 %), und ihre Entfernung wächst mit dem Abstand dieser Person zum Küchensensor (4,8 m + 0,71·d,
  Korrelation 0,79). Kein Maßstab holt sie in den Raum (bei 0,7 noch 17 %). Die Wege zur
  Vorratstür kreuzen die Wandlinie schon bei Maßstab 1 und 90° zu 92 % in der Öffnung.
- **Kalibrieren ohne große Überschneidung** (7.10., `tools/calibrate_offline.py`): Paare gleichzeitiger
  Messungen (mit Ausreißeranteil, Myronenko & Song 2010), dazu der Grundriss je LD2450-Spur: Person
  im sichtbaren freien Raum oder Echo (Likelihood-Feld wie Thrun, Burgard & Fox 2005, 6.4;
  Kameraposen aus Wegen und Karte wie Mohedano, Cavallaro & García 2014). Der Grundriss bestimmt
  Richtung und Lage, keinen Maßstab: gleichverteilt auf der sichtbaren Fläche schiebt er die Punkte
  an die Wände (Küche 1,55), ohne Jacobi-Faktor zieht er die Echos herein (0,53). Ohne Paare kommt
  der Maßstab deshalb von den übrigen LD2450 (hierarchisch, hier 1,05–1,08 ± 0,06–0,09).
  Ergebnisse: Arbeitszimmer 320° → 324° (±0,3°, je nach Variante 323–326°), Maßstab 1,04–1,06,
  Spiegel wie gezeichnet (die gespiegelte Lösung, die die Paare allein erlauben, 295°, ist um 465 log
  schlechter); Schritte über eine Wandlinie durch die Tür 11 von 82 → 90 von 98, Paare mit dem Flur
  im Median 0,55 → 0,32 m. Flur: Die Paare wollen 50° (Esszimmer–Flur 0,53 → 0,18 m), dann gehen
  aber mehr Schritte durch die Wand neben der Arbeitszimmertür; mit freier Lage passt beides (0,6 m
  weiter an seiner Wand, 44°, Maßstab 0,94). Die eingezeichnete Lage ist zu prüfen. Küche: 94° ± 4°,
  ohne eigenen Maßstab. report_eval: keine Variante nachweislich besser (falsch aus 2,0–4,7 von 15
  statt 3,0, falsch an immer 0 von 41); die Unterschiede liegen an Lichtschaltern in Türen, wo 0,2 m
  den Raum entscheiden. Die Log-Evidenz ist zwischen Maßstäben nicht vergleichbar (die Dichte der
  Messungen im Haus ändert sich mit dem Maßstab).
- **Die gemeldete Entfernung des LD2410C erfindet Personen** (0.9.3): Sie ist der Ring, in dem die
  Energie gerade über ihrer Schwelle liegt. Leon allein am Schreibtisch in 1,5 m (Arbeitszimmer
  6.10. 22:20–22:50): in 27 % der Ruhig-Frames 2,6–6 m. Das Modell brauchte dafür eine zweite Person
  und stellte die zweite Person (im Schlafzimmer) 25 Minuten lang zu 100 % ins Arbeitszimmer (Fehlerberichte
  22:40, 22:42). Die Energie in den fernen Ringen kommt vom Schweif seines eigenen Profils (4.3). Mit
  den Energien dort: Arbeitszimmer genau 1, Schlafzimmer 0,82.
- **Energien gegen einzelne LD2450-Frames geprüft** wollten eine zweite Person (dieselbe halbe
  Stunde: +224 log für eine zweite in 2,5 m); gegen die geglättete Position der Person nicht (−543).
  Die Energien zählen deshalb gegen die Personen des Filters, nie gegen Frames.
- **Energien ohne Verzögerung und ohne gemeinsamen Pegel** (Entwicklung der Energien, 7.10.):
  Nachdem Leon an der Küche vorbei durch die Tür gegangen war, blieben die Ruhig-Energien 3 s bei 100
  und fielen dann mit etwa 2 s; das Modell holte eine zweite Person in die Küche, die dort in den
  Teil hinter der Wand ging und blieb (Licht an ohne Person in 3,3 von 41 leeren Raum-Fenstern).
  Mit der Verzögerung 2,3; mit dem gemeinsamen Pegel je Sekunde 0 (Schübe und gleichmäßig erhöhte
  Ringe nachts im Arbeitszimmer hatten Personen am Rand des Strahls erklärt).
- **Textur je Ring statt Echoquellen** (7.10.): Ein langsam veränderlicher Gamma-Pegel je Ring
  (Compound-Gauß- / K-verteiltes Clutter, Ward 1981; Ward, Tough & Watts 2006; als Markov-Kette mit
  30–120 s, Verteilung aus der leeren Nacht) trennt das Ereignis von 01:08 nicht von Personen: log
  Bayes-Faktor Person / niemand mit Textur +14–18 für das Ereignis (56 s), +18–39 für echte Personen
  (40–60 s); eine Person in 0,75 m in der Küche (22:15) nur noch +3 statt +1877. Eine Textur, die
  das Ereignis erklärt, erklärt jede Person. Auch eine Textur je Sekunde (ohne Gedächtnis) half
  nicht. Die Echoquellen haben eine Form (ein Profil) und eine Lebensdauer; daran trennen sie.
- **Echoquellen zuerst, gegen die Personen, wie sie vorher waren** (7.10.): nahmen eintretenden
  Personen mit und ohne Spur ihre Energie (Licht zu spät aus in 5,3 von 15 Fenstern statt 2,0).
  Jetzt sind sie Alternative nur zu Personen ohne Spur und zuletzt in der Kette: 3,0.
- **Amplitude je Aufenthalt beim LD2410C** (7.10., verworfen): der Gewinn g auf dem Profil einer
  Stehenden (gemessen 4.3) als Gamma(2, 2) in 5 Stufen (Swerling III: langsam, fest über einen
  Aufenthalt), neu bei jedem Stehenbleiben, innerhalb mit 1/(600 s) neu gezogen wie κ. Geprüft (a) an
  κ gekoppelt (k-te Stufe von κ = k-te Stufe von g) auf Kacheln und Gauß-Komponenten, (b) nur auf den
  Personen mit Spur, gekoppelt oder eigene Stufen, (c) dazu auf Gehenden (Gamma(3,5)), (d) wie (b)
  mit Gamma(4, 4). Log-Evidenz um +1800 bis +2250 besser, Licht fälschlich aus aber in 3,50 / 3,17 /
  4,42 / 3,17 statt 3,0 von 15 Fenstern (an weiterhin 0 von 41). Der Flur um 21:56 wird besser (0,40 →
  0,57, mit (d) 0,55), der um 21:35 und das Wohnzimmer um 21:24 nicht; schlechter werden das Wohnzimmer
  um 21:21 (0,89 → 0,52–0,81) und die zweite Person im Bad (21:30: 0,86 → 0,02–0,08): Mit freier
  Amplitude erklärt die Person in der Küche (auf einer Spur, die teils aus Mehrwegechos hinter der Wand besteht, siehe „Küchensensor hinter der Wand“)
  ihre Energie mit niedrigem g, und eine ungesehene Person nahe dem Sensor nimmt den Rest. Mit
  Gehenden zusätzlich das Arbeitszimmer 21:36 (0,86 → 0,33). Die Streuung ist echt, aber solange
  Kalibrierung und Profilform nicht stimmen, nutzt das Modell die Freiheit für Fehlpassungen.
- **Ohne Prüfung entfernt** (0.7/0.8): LD2410C (in der 0.6.7-Ablation nützlich, in 0.6.12/0.6.13
  verbessert; in 0.9 wieder drin, 4.3), Körperabstand zweier
  Personen, Ziele und Wege um Wände, Nachbilder.
- **In 0.6.x getestet und verworfen:** gelernte P_D-Karte, Verhaltenskarten je Ort, harte
  Störzonen, ein fester Versatz je Sensor (mehr Doppelzählungen; jetzt als Versatz je Spur gelöst).
- **Lernen nur aus unabhängiger Evidenz**; aus den eigenen Urteilen lernt das Modell seine Fehler
  (Verhaltenslernen in 0.6.0 deshalb aus).
- **Eine einzelne Prozentzahl verdeckt Fehler** (in 0.6.4 versteckte sie einen ganz falschen
  Küchenschritt). **Gute Werte in der Simulation** sagten wenig: 0.5.0 war dort gut und in der Praxis
  schlecht.
