# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand: Code 0.9.0 (Entwicklung, 6.10.2026). Beschreibt, was der Code rechnet; was fehlt oder nur genähert ist,
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
Wird nur angezeigt, nicht ausgewertet (siehe 9 und 10).

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
- **Kacheln:** Der beobachtete Bereich wird in Blöcke von höchstens 0,6 m geteilt, jeder Block nach
  Raum und danach, wie gut jeder Sensor dort sieht (nicht / Rand / voll, `g_s` < 0,1, < 0,9, sonst),
  jedes zusammenhängende Stück eine Kachel; Stücke unter 0,04 m² gehen an den Nachbarn im selben Raum.
  So sind Wände und Sichtgrenzen Kachelgrenzen, und innerhalb einer Kachel sieht jeder Sensor etwa
  gleich gut (Wohnung 6.10.: 237 Kacheln statt 1153 Zellen zu 0,2 m). Jede Kachel ist eine kleine
  Gauß-Verteilung (Schwerpunkt, Streuung je Achse); damit rechnen der Beginn einer Spur (5.4) und
  der Weg durch eine Tür (5.2).
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
  `tools/ghostmap.py` (Geisterkarte offline).

## 9. Bekannte Schwächen und Offenes
Abweichungen von den Vorgaben:
- **LD2410C nicht ausgewertet**, obwohl er in 0.6.x nachweislich half (10).

Näherungen, die man prüfen oder ersetzen kann:
- Nicht-Erfassung und Erfassungsrate einer Gauß-Komponente am Mittel statt über ihre Verteilung.
- Auf den Kacheln ist Gehen eine Diffusion: Kurzzeitig gerades Gehen und die Richtung gehen verloren,
  beim Wechsel Gauß → Kacheln auch Geschwindigkeit und Versätze. Innerhalb einer Kachel ist die
  Masse nicht weiter aufgelöst (wer im Sichtrand sitzt, ist über die ganze Randkachel verteilt). Die
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
  Kacheln und Takt 4,0 % (Log-Evidenz +29 gegenüber 0.8.1 auf 20 min).
- **Ohne Prüfung entfernt** (0.7/0.8): LD2410C (in der 0.6.7-Ablation nützlich, in 0.6.12/0.6.13
  verbessert; braucht ein Modell seiner eigenen Haltezeit und Torenergien), Körperabstand zweier
  Personen, Ziele und Wege um Wände, Nachbilder.
- **In 0.6.x getestet und verworfen:** gelernte P_D-Karte, Verhaltenskarten je Ort, harte
  Störzonen, ein fester Versatz je Sensor (mehr Doppelzählungen; jetzt als Versatz je Spur gelöst).
- **Lernen nur aus unabhängiger Evidenz**; aus den eigenen Urteilen lernt das Modell seine Fehler
  (Verhaltenslernen in 0.6.0 deshalb aus).
- **Eine einzelne Prozentzahl verdeckt Fehler** (in 0.6.4 versteckte sie einen ganz falschen
  Küchenschritt). **Gute Werte in der Simulation** sagten wenig: 0.5.0 war dort gut und in der Praxis
  schlecht.
