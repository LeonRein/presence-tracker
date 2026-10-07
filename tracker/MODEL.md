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

Leon, 7.10.: „Die Annahme einer festen Personenzahl ist schlichtweg falsch und führt zu anderen schweren
Fehlern. […] Eine neue Person darf nur entstehen, wenn es dafür harte Evidenz gibt. Wenn es erst 5
Sekunden dauert, bis eine Person als solche identifiziert wird, ist das nicht schlimm. Und Geister
dürfen sich ohne Belege in den Messwerten nicht halten können!“

*Anmerkung zu 3 (Umsetzung):* Wer nie da war (eine Spur, die ein Geist war, oder dieselbe Person
doppelt), verschwindet nicht „im Raum“, er hat nie existiert. Das Modell trägt deshalb für jede
bekannte Person ohne Spur die Wahrscheinlichkeit, dass es sie gibt (5.5); Messungen, die gegen sie
sprechen, senken sie, wie sie die Dichte formen. Eine wirkliche Person verschwindet weiter nur über
Türen.

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
  Mischung von 5 Exponentialverteilungen (Feldmann & Whitt 1998, angepasst an Punkten in gleichen
  log-Abständen von 2 s bis 1 Tag): Jeder Aufenthalt hat seine eigene Rate λ_l des Aufstehens. Wer
  weitersteht, verschiebt sich zu den langsamen Arten: `p_l ← p_l e^(−λ_l Δt) / Σ` (Hidden-Semi-Markov
  als HMM mit parallelen Phasen, D_Yu2010 Abschn. 3.2, 4.1.3). Fehler der Anpassung zwischen 1 s und
  1 Tag: höchstens 0,024 in S, 16 % relativ, 42 % in der Rate des Aufstehens (mit 7 Arten 0,004 /
  2 % / 18 %). Die Evidenz (7) unterscheidet das nicht: 6.10. 19:28 bis 7.10. 06:15 (11 h, 5 Sensoren,
  Neustarts und Nacht), gegenüber 5 Arten mit 7 Arten +1, 6 Arten +2, 4 Arten −11, 3 Arten −53;
  mit 24 Hypothesen ab 10⁻⁹ (5.1) 7 Arten und 5 Stufen κ (0.10.0) +3, 4 Arten −9, 3 Arten −100.
  Lichtfehler gleich, mit 3 Arten mehr (fälschlich aus 3,5 statt 3,0 bzw. 2,5 statt 2,0 von 15).
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
(Buch_SarkkaSolin2019 Bsp. 6.2). Wände: Die Bewegung eines Schritts trifft sie über Sigma-Punkte
(5.2), nicht die Verteilung selbst.

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
- Wie viele Personen da sind, ist nirgends fest eingestellt (mal einer, mal zwei, mal keiner, mal ein
  Gast). Ein Neustart der App setzt das Wissen über sie fort (5.3); weiß das Modell nichts, sind die
  unbekannten eine Poisson-Intensität ohne feste Zahl (5.3, 5.5).
- Auch eine bekannte Person ohne Spur ist nur wahrscheinlich da: Sie existiert mit einer
  Wahrscheinlichkeit r (Bernoulli, 5.5). War ihre Spur vielleicht ein Geist oder eine schon bekannte
  Person, bleibt diese Möglichkeit als r < 1 erhalten, und was danach nicht zu ihr passt (keine neue
  Spur, wo sie gesehen würde; kein Herauskommen aus einem Bereich ohne Sensor; die Energien des
  LD2410C), senkt r. Das ist keine Regel: dieselbe Rechnung wie für ihre Dichte (1.3, Anmerkung).

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
  **Sicht einer Person, nicht eines Punkts** (seit 7.10., `Tracker._spread_sight`): Der Sensor misst
  die Person dort, wo ihre Spur auf ihr sitzt, `z = x + c + o + w` (unten). Er sieht sie also so weit,
  wie er diese Stelle sieht: `g(x) = E[g_s(x + c + o)]`, Versatz gaußsch mit der Varianz von unten je
  Entfernung, gemittelt über das, was von x aus ohne Wand erreichbar ist (durch eine Türlücke ja,
  durch eine Wand nicht; in den Bereichen ohne Sensor bleibt die Sicht der Punkte). Vorher war der
  Schatten einer Wand eine scharfe Kante auf 0,1 m: Wer 0,2 m im Schatten stand, war unsichtbar,
  obwohl seine Spur in Sicht saß (6.10. 20:55, Flur aus Sicht des Arbeitszimmers: „die Spur ist
  Leons“ hatte das Gewicht 0, 10), und ein 0,2 m breiter Streifen der Küche an ihrer Wand (x 2,2–2,4)
  war für jeden Sensor und das LD2410C unsichtbar (Kacheln 0,08 m²): Dort sammelten sich Personen
  (7.10. 07:10 Küche 0,76, 10). Jetzt erfasst der LD2450 dort Gehende mit 0,26–0,32 /s (Raummitte
  0,48 /s), das LD2410C erwartet 26–40 statt 0.
- `q(x)`: Auflösung neben einem gemessenen Ziel,
  `1 − exp(−ln 2 ((Δr/0,6 m)² + (Δquer/0,9 m)²))` (Form A_Svensson2012 Gl. 18–20); neben einem
  gehaltenen Ziel stattdessen `1 − exp(−d²/(2·0,7²))` (dort würde es wiedergefunden).
- **Erkennbarkeit κ** einer stehenden Person in diesem Aufenthalt (Haltung, Platz): dieselbe Person
  wird am Tisch alle halbe Minute erfasst, auf dem Sofa minutenlang nicht. κ ~ Gamma(α, α), Mittel 1,
  in 3 gleich wahrscheinlichen Stufen (Mittel 0,19 / 0,71 / 2,10); neu bei jedem Stehenbleiben,
  innerhalb eines Aufenthalts mit 1/(600 s) neu gezogen (D_Mahler2011 Gl. 49–52; D_Wilthil2019 Gl. 2).
  5 Stufen (bis 0.10.0) −2, 4 Stufen −0,3 gegenüber 3 (dieselben 11 h wie 3.1), Lichtfehler gleich.
  α = 1 (angenommen). Über die Evidenz dieser 11 h nicht zu bestimmen: α = 0,5 −5, α = 2 +17, mit 24
  Hypothesen ab 10⁻⁹ aber −82 (5.1). Gehende: κ = 1.
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
gehend); Geister entsprechend mit ihrer Lebensdauer. Ebenso nach einer neuen Lage oder Kalibrierung
des Sensors (5.3): Seine Spuren enden ohne Information, sein nächster Frame ist ein Start.

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
- **Ende der Spur** (`Tracker._ghost_end`): Der Sensor gibt eine gehaltene Spur auf, wenn er sie nicht
  wiederfindet (4.1). Bei einer Person ist das „u s nicht wiedergefunden“, S(u). Beim Geist ist die
  Quelle entweder noch da und nicht wiedergefunden, S_D(u) S(u), oder sie verschwand bei τ < u, bevor
  sie wiedergefunden wurde, ∫ f_D(τ) S(τ) dτ (konkurrierende Risiken, Kalbfleisch & Prentice 2002 Kap.
  8). Ohne Halten geendet: für Person und Geist gleich (kein Faktor). Bis 0.10.0 bekam der Geist
  S(u) und dazu `1 − S_D(u)`, als müsse die Quelle sterben *und* nicht gefunden werden: Am Ende jeder
  Geisterspur wurde „Person“ um 1/(1 − e^(−u/L)) wahrscheinlicher, bei 1,5 s gehalten und L = 39 s
  etwa 26-mal (10).
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
  das Profil (Gewinn g auf dem Profil je Aufenthalt einer Person ≥ 30 s). Nur aus Frames, die auch der
  Filter als Messung nimmt (`tools/ld2410/amplitude2.py`: das LD2450-Ziel nicht gehalten und nicht
  hinter einer Wand): innerhalb eines Sensors log g Streuung 0,47 ruhig (6.10. 18–23 Uhr, 88
  Aufenthalte) bzw. 0,25 (7.10. 6–8 Uhr, 39), als Gamma(β, β) β 5–16; ruhig und bewegt derselbe
  Gewinn (0,82–0,88); mit der Wiederfinderate des LD2450 innerhalb Sensor und 1 m Entfernung Spearman
  0,37 bewegt / 0,47 ruhig. Die erste Messung (`amplitude.py`: 0,93, β 1,6, Flur −1,1, Spearman 0,62)
  nahm auch gehaltene Ziele: Der LD2450 hält ein eingefrorenes Ziel bis 35 s, nachdem die Person
  gegangen ist, und die Küche hat ihre Echos hinter der Wand; dort war wenig Energie, daher die
  niedrigsten Gewinne und ein Teil der Kopplung an κ. Je Sensor (Mittel von log g ruhig, beide Tage):
  Arbeitszimmer −0,2 / +0,2, Esszimmer +0,3 / −0,1, Flur −0,2 / +0,3, Küche +0,9 / +1,5 (je ein
  Aufenthalt), Wohnzimmer +1,0 / +1,1 (alle in 5–7,5 m). Wo im Profil die Abweichung liegt
  (`tools/ld2410/shape2.py`, gemessen/Profil je Abstand des Rings hinter der Person): Die Hauptkeule
  passt in Arbeitszimmer, Flur und Küche (0,9–1,3), der Schweif hinter der Person ist je Raum
  verschieden (Küche ×2,5–4,6, Flur ×1,5–2,4, Arbeitszimmer ×1–1,3, Esszimmer ×0,5–0,7); im
  Wohnzimmer liegt alles ×1,5–5 höher. Vorbehalt: Bezug ist „genau ein LD2450-Ziel“, und der LD2450
  unterdrückt Sitzende; wo jemand sitzt (Ess-, Wohnzimmer), zählt dessen Energie mit. Nach dem
  Winkel kein beständiger Verlauf (6.10. +0,17 je 10°, 7.10. 0,0). Im Modell ist davon nur die
  Amplitude je Aufenthalt (unten; Maßstab und Schweif je Sensor: 10).
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
  mal Strahl mal Sicht (Wände wie beim LD2450: der Anteil der Person, den der LD2450 im selben Gehäuse
  ohne Wand dazwischen sehen könnte, über ihren Versatz gemittelt wie in 4.1; bis 0.10.0 ja/nein). Ruhig zählt das Verzögerte: M ist, was die Personen
  zuletzt hineingaben (geglättet mit 2 s), w_m sein Anteil im Block; bewegt w_m = 0.
- **Amplitude je Aufenthalt** (Swerling III: langsam, fest über einen Aufenthalt): Eine Stehende mit
  Spur gibt g·S ab, g ~ Gamma(6, 6) in 3 gleich wahrscheinlichen Stufen (gemessen β 5–16, s. o.), neu
  bei jedem Stehenbleiben, innerhalb mit 1/(600 s) neu gezogen wie κ, aber unabhängig von κ (a priori
  unabhängig und von verschiedenen Sensoren gemessen: als eigener Vektor neben κ an der
  Gauß-Mischung exakt). Gehende und Personen ohne Spur: g = 1 (auf den Kacheln geht g verloren wie die
  Geschwindigkeit).
- **Gemeinsamer Pegel u** je Block (1 s) und Art: 1/u ~ Gamma(κ, κ), κ = 16 bewegt, 50 ruhig (gemessen,
  s. o.), gemischt mit 2 % Schüben, 1/u ~ Gamma(4) mit u um 2,5 (angenommen; nur nach oben: ein
  niedriger Pegel darf eine fehlende Person nicht entschuldigen). Konjugiert: exakt herausintegriert.
  Ohne ihn erklärte eine gleichmäßig erhöhte Energie aller Ringe (nachts im Arbeitszimmer ruhig
  doppelt so hoch wie gelernt, Schübe) eine Person am Rand des Strahls.
- **Hintergrund b je Sensor, Ring und Art: von der App gelernt** (wie die Geisterkarte): Online-EM der
  Überlagerung, der Anteil des Hintergrunds an einer Energie ist im Mittel `e · b/μ` (Richardson 1972;
  Shepp & Vardi 1982), μ aus dem Stand vor diesen Frames (die Energien lernen nicht ihr eigenes Urteil,
  J_Park2020). Prior: bewegt Ring 0 13, Ring 1 9, sonst 4,5, ruhig 5 mit dem Gewicht 10 min (gemessen
  6.10., 4,3 h; auf 19 h 16 / 9 / 4,3 / 4,9, gleiche Lichtfehler, 10 „Die Stille vor einem Frame“);
  Vergessen 6 h (angenommen). Neu für einen Sensor, wenn er verschoben wird. Gespeichert mit der Geisterkarte.
- **Zeit:** Die Frames werden je Sekunde gesammelt (Gamma: die Summen von Δt, Δt·e, Δt·ln e und Δt der
  gekappten genügen), jeder mit Δt / τ, τ 4 s bewegt / 13 s ruhig (zusammengesetzte Likelihood mit der
  Korrelationszeit als effektiver Stichprobengröße; Varin, Reid & Firth 2011). Ein Frame steht für
  einen Frame des LD2450 (0,089 s, gemessen 0,0891 s an allen fünf). Ohne Anlass (kein LD2450-Ziel,
  beide Flags aus) sendet die Firmware nur alle 5 s einen Herzschlag (4.4); der erste Frame danach
  kommt, weil etwas gestiegen ist. Die Stille davor steht deshalb für die Energien des Frames davor
  (des Herzschlags), wie die nicht gesendeten Frames bis dahin waren; eine Lücke über 6 s sagt nichts
  (10, „Die Stille vor einem Frame“).
- **Verrechnung:** Kein Produkt über die Personen. Je Hypothese die Personen nacheinander (erst die mit
  Spur, dann die ohne, zuletzt die unbekannten), jede gegeben die vorigen mit dem, was sie danach
  hineingeben (exakt für Personen an bekanntem Ort). Jede Person (Kacheln bzw. Komponenten an ihrem
  Mittel) wird dann mit `ℓ(e | Rest + S(x)) / ℓ(e | Rest)` gewichtet, der Rest = alle anderen, gemischt
  über die Hypothesen, die sie halten (Marginale wie bei JIPDA; was andere erklären, sagt über diese
  Person nichts, 0.6.13). Eine zweite Person am selben Ort bekommt so nur, was die erste nicht erklärt;
  gekappte Werte (100) sprechen dabei nur für mehr Energie, nie dagegen (geprüft 7.10., 10). Die unbekannten Personen: höchstens eine von ihnen im Blick (Poisson nach
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
Die Firmware sendet leere Frames (kein LD2450-Ziel, beide Flags des LD2410C aus) nur alle 5 s, den
ersten leeren nach einem vollen sofort. Eine Lücke bis 6 s zählt als beobachtet und leer (für den
LD2410C: mit den Energien des Frames vor der Lücke, 4.3); eine längere sagt nichts.
Ein Neustart der App ist eine solche Lücke für alle Sensoren: Die Personen werden über sie nur
vorgerückt, nicht gewichtet (5.3).

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
  eine bekannte Person ohne Spur (mit ihrem r, 5.5); eine unbekannte (5.5). Eine wiedergefundene: ihr Eigentümer; eine
  andere Person nahe der Stelle; Geist/Reflexion.
- Behalten werden höchstens 12, solange über 10⁻⁷ des stärksten (Abschneiden nach Gewicht,
  B_Vo2017). Auf den 11 h aus 3.1 greift die Grenze von 12 bei 8 % der Schnitte (461 von 5588), im
  Mittel bleiben 4,7 Hypothesen. Log-Evidenz gegenüber 12 ab 10⁻⁷ / Lichtfehler (fälschlich an von
  41, aus von 15) / Rechenzeit (21 Uhr, 5 Sensoren, Anteil eines Kerns):

  | höchstens | ab | Evidenz | an / aus | CPU |
  |---|---|---|---|---|
  | 6 | 10⁻⁷ | +53 | 0 / 3,0 | |
  | 8 | 10⁻⁷ | +67 | 0 / 2,1 | 2,8 % |
  | 12 | 10⁻⁵ | −367 | 0 / 3,1 | |
  | **12** | **10⁻⁷** | **0** | **0 / 3,0** | **3,0 %** |
  | 12 | 10⁻⁹ | +228 | 0,9 / 2,0 | 3,8 % |
  | 12 | 10⁻¹² | +75 | 0 / 3,0 | |
  | 16 | 10⁻⁷ | +168 | 0,9 / 2,0 | 3,5 % |
  | 24 | 10⁻⁷ | +177 | 0,9 / 2,0 | |
  | 24 | 10⁻⁹ | +275 | 0,9 / 2,0 | 5,9 % |
  | 24 | 10⁻¹² | +215 | 0,9 / 2,0 | |

  Die Evidenz ist nicht konvergiert und hängt nicht monoton von den Grenzen ab. Der Unterschied
  zwischen 12 ab 10⁻⁷ und 24 ab 10⁻⁹ entsteht in wenigen Minuten des belebten Abends (22:02 +69,
  22:10 +93), nachts nicht: Eine Hypothese unter 10⁻⁷ wurde später die beste. Die Lichtfehler kennen
  zwei Ausgänge; mit mehr Hypothesen verliert das Modell um 21:24 die Person im Wohnzimmer nicht
  mehr (das war das Abschneiden), lässt sie dafür um 21:28 zu lange dort (Licht an im leeren Raum;
  das ist das Modell, 12 ab 10⁻⁷ verdeckt es). Vergleiche von Modellvarianten über die Evidenz brauchen deshalb mehr als eine
  Einstellung der Grenzen; Unterschiede unter etwa 60 über diese 11 h sind nicht sicher (3.1, 4.1).
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
3. Nicht-Erfassung durch andere Sensoren: Faktor je Komponente, als Erwartung über ihre Position
   (`E[e^(−r(x)Δt)]`), ebenso die Rate einer neuen Spur, die Sicht beim Wiederfinden (unter dem Kern
   gewichtet) und „geht in Sicht“: Unscented-Transformation der Position je Achse (diagonale
   Kovarianz, n + κ = 3: Mitte 1/3, ±√3 σ je 1/6; Buch_SarkkaSvensson2023 Gl. 8.70–8.71). Punkte
   hinter einer Wand, vom Mittel aus gesehen, werden an ihr gespiegelt (Wände reflektieren). Das
   LD2410C sieht die Komponenten weiter an ihrem Mittel (über Sigma-Punkte: Log-Evidenz −524).
   **Wände in der Vorhersage:** Die Sigma-Punkte (Kubatur, 2n = 16 Punkte; ebd. Alg. 8.11/8.16)
   von Position, Geschwindigkeit und Rauschen der Komponente *geht* vor dem Schritt werden linear
   bewegt; wer dabei eine Wand kreuzt, endet an ihr gespiegelt, die Geschwindigkeit auch. Danach
   Momentenabgleich, die Versätze der Spuren folgen über ihre Regression. Was schon an oder hinter
   einer Wand liegt, bleibt (anders als das verworfene Abschneiden, 10). Für Ausgaben und den
   Wechsel auf Kacheln (5.4) zählt die Masse hinter einer Wand, vom Mittel aus gesehen, nicht
   (außer es läge alles dahinter).
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

**Neustart der App:** Was über die Personen bekannt ist, speichert die App alle 10 Minuten und beim
Beenden (`people.json`, `Tracker.people_state`): je Hypothese ihr Gewicht, die bekannten und die
unbekannten Personen als Dichten über den Kacheln. Laufende Spuren überleben den Neustart nicht (die
nächsten Frames der Sensoren sind ein Start, 4.1): Personen mit Spur gehen auf die Kacheln wie am Ende
ihrer letzten Spur (5.4), Geisterspuren enden, und Hypothesen, die danach dasselbe sagen, werden eine
(5.6; eine je Zahl bekannter Personen). Beim Start ist dieser Zustand der Prior, die PMBM-Rekursion
geht von ihm aus weiter (B_GarciaFernandez2018). Die Zeit dazwischen ist eine Datenlücke (4.4): Alle
werden mit der Dynamik aus 3 vorgerückt, nichts gewichtet; die letzten 120 s wie immer, davor in
Sprüngen von 15 s (höchstens 2000, `Hidden.leap`): Wer aufsteht, geht seinen Weg in einem Sprung ganz,
bis er anhält oder durch eine Tür geht (wo ein Weg endet: `μ (μI − Q)⁻¹`, Q die Gehmatrix, eigene
Herleitung aus 3.2); ein kurzer Aufenthalt, der in einem Sprung beginnt, endet frühestens im nächsten.
Gegen 3 h in Takten liegen die Räume damit bis 0,022 daneben (7.10. 08:06, 8 Personen); 8 h kosten
1,7 s CPU. Gespeichert werden die Massen als Anteile in float16 (unter 10⁻⁶ als 0), zlib, base64:
27–40 kB für 8 Personen (7.10. 08:00 und 08:06; als float32 0,7–1,3 MB). Passt der Zustand nicht zu den
Kacheln (anderer Grundriss) oder fehlt er, weiß das Modell nichts. Nach einem Fehler des Modells beginnt
es aus dem Zustand unmittelbar davor, wenn der sich noch speichern lässt, sonst aus dem letzten
gespeicherten.

**Nichts bekannt** (erster Start, anderer Grundriss, *Spuren zurücksetzen*): keine bekannten Personen;
die unbekannten (5.5) als Poisson-Intensität mit `start_unknown` = 1 erwarteten Person (angenommen),
je ein Drittel im beobachteten Bereich (stehend, gleichverteilt nach Fläche, ein laufender Aufenthalt
zu zufälliger Zeit gesehen), in den Bereichen ohne Sensor und außer Haus. Keine feste Zahl: Wer in
Sicht ist, wird über seine Spur zur bekannten Person (5.5), gleich wie viele es sind; wo niemand
erfasst wird, schwindet die Intensität mit der Nicht-Erfassung. Ohne Anteil in Sicht könnte, wer beim
Start in Sicht sitzt, nur ein Geist sein (wie mit der früheren festen Zahl `start_people` unter der
Zahl der Anwesenden, 10, Meldung 7.10. 08:07). Der Preis: Hinter Türen und außer Haus bleibt die
Intensität, wie viele auch gefunden werden (die Zahlen eines Poisson-Prozesses sind unabhängig), bis
dort niemand herauskommt oder sie vergessen wird (3.4). 0,3 / 1 / 3 Personen ändern die Lichtfehler
nicht (bei jedem App-Start ohne Wissen, 6./7.10.: 0,14 von 41 fälschlich an), die Log-Evidenz steigt
mit der Zahl (−279 / 0 / +265).

Neu beginnt das Modell, wenn sich die Welt der Personen ändert (Wände, Räume, Türen, Parameter,
welche Sensoren es gibt, der beobachtete Bereich), wie nach einem Neustart: aus dem Zustand davor,
solange die Kacheln dieselben bleiben (etwa nur andere Parameter), sonst ohne Wissen. Wird nur ein
Sensor gedreht, verschoben oder neu kalibriert, bleibt, was über die Personen bekannt ist, ganz (die
Konfiguration sagt etwas über den Sensor, nichts über sie); seine Spuren beginnen neu wie nach einer
Datenlücke (4.1).

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
- **Bekannte Personen ohne Spur sind Bernoullis** (`hidden.Hidden.r`, B_GarciaFernandez2018 Gl. 31;
  der Spurteil eines PMBM): Es gibt sie mit der Wahrscheinlichkeit r, dann mit ihrer Dichte. Was gegen
  sie spricht (keine neue Spur, kein Wiederfinden, die Energien, 4.1–4.3), wirkt als
  `1 − r + r ∫ Dichte × Likelihood`: Dichte und r werden zusammen aktualisiert, ohne Regel. Eine neue
  Spur auf ihr hat das Gewicht `r ∫ …` und macht sie gewiss (r = 1, eine Person mit Spur). r < 1
  entsteht beim Zusammenlegen (5.6): Sagt eine Hypothese „die Spur war ein Geist“, eine andere „sie
  war eine neue Person“, und endet die Spur, sind beide über alle laufenden Spuren gleich; zusammen
  sind sie die Person mit r = Gewicht der zweiten. Vorher blieben das zwei Hypothesen mit
  verschiedener Personenzahl; wurde die mit weniger Personen abgeschnitten (höchstens 12, 5.1), kam
  sie nie zurück, und die erfundene Person blieb mit r = 1 (9, 10). Ausgaben zählen r × Dichte; die
  Anzeige zeigt Personen mit r ≥ 0,5 (Schätzer der Literatur, B_GarciaFernandez2018 Abschn. VI).
- **Zurückgeben:** Eine bekannte Person ohne Spur, die zu weniger als 1 % existiert und im Haus ist
  (r × P(im Haus) < 0,01), geht mit r × ihrer Dichte in die Intensität (Bernoulli → Poisson). Das
  ändert nur P(mehrere davon kommen zurück), um höchstens 0,01²/2. Sonst würde jeder Gast für immer
  verfolgt. (Die Literatur verwirft Bernoulli-Teile unter 10⁻⁵, B_GarciaFernandez2018 Abschn. VII,
  oder r < 10⁻³, B_Reuter2017 S. 166; hier geht keine Masse verloren.) Mit 5 % statt 1 %: report_eval
  gleich, Log-Evidenz −8.

### 5.6 Zusammenlegen
Hypothesen, die über alle laufenden Spuren dasselbe sagen, werden eine:
- Personen mit denselben Spuren: Gauß-Mischungen vereinigt, je Betriebsart per Momentenabgleich.
- Personen ohne Spur sind austauschbar: so gepaart, dass die Summe der L1-Abstände (von r × Dichte)
  minimal ist, dann gemischt (eigene Herleitung; beliebige Reihenfolge erzeugt „je zu 50 % hier und
  dort“ statt „einer hier, einer dort“, B_Reuter2014 Fig. 1–3). Für mehr als eine Person ist das die
  Multi-Bernoulli-Näherung.
- Auch Hypothesen mit verschieden vielen Personen ohne Spur werden eine (seit 7.10.): Wo eine
  Hypothese eine Person weniger hat, steht dort „niemand“ (r = 0); gemischt wird daraus ein
  Bernoulli mit r = Σ Gewicht × r (`Hidden.mixture`). Für genau eine fehlende Person exakt; vorher
  nicht zusammengelegt (9: „Bekannte Personen sammeln sich an“). Ein Neustart speichert dadurch
  eine Hypothese statt einer je Zahl (5.3).

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
- Anzeige: Personen der wahrscheinlichsten Hypothese, die eher existieren als nicht (r ≥ 0,5, 5.5),
  ihre Dichten (r × Dichte) als Wärmekarten.

## 7. Evidenz
Die Summe der Normierungen ist die Log-Evidenz der Aufnahmen (`Tracker.loglik`; Summe der
prequentiellen Log-Scores, I_GneitingRaftery2007 S. 372). Weil der Filter deterministisch ist, ist
sie glatt in den Parametern und Vergleiche brauchen keine Seeds (I_Kantas2015 S. 8–9). Damit sollen
Parameter und Modellvarianten auf Aufnahmen ohne Wahrheit verglichen werden. Bisher ist damit nur die
Lebensdauer der Geister geschätzt (4.2).

## 8. Prüfung
- **Wahrheitsdaten:** am 6.10.2026 gelöscht, die Bewertung wird neu aufgebaut. Maßstab sind die
  Lichtfehler aus 1.1 je Raum (keine einzelne Prozentzahl).
- **Fehlermeldungen** (App, *Fehler melden*): 15 Minuten Sensordaten, die Anzeige der App, der Start
  des Modells und das Gelernte von diesem Start (sonst vom Moment der Meldung). Das Nachspiel ab dem
  Start glaubt genau, was die App glaubte; mit dem Gelernten vom Moment der Meldung lag es am 6.10.
  um bis zu 1,0 daneben. Lag der Start früher, hat ein Nachspiel ohne Wissen nach 15 Minuten vergessen,
  was es nicht wusste (22:40, 22:42: |ΔP| < 0,01 am gemeldeten Moment, anfangs bis 0,11).
- **Tests** (`tracker/tests`): bitgleiche Läufe, Unabhängigkeit von der Zeitzerlegung,
  Bewegungsstatistik, Szenen aus `sim.py`, Invarianten der Buchführung (`Tracker.check`).
- **Geplant:** simulationsbasierte Kalibrierung (0_Talts2018 Alg. 1): Welten aus diesem Modell
  erzeugen; der Rang der wahren Zahl unter der Filterverteilung muss gleichverteilt sein.
- Werkzeuge: `tools/record.py` (Aufnahme), `tools/replay.py` (Aufnahmen durch den Tracker),
  `tools/ghostmap.py` (Geisterkarte offline), `tools/calibrate_offline.py` (Kalibrierung aus
  Alltagsaufnahmen, auch ohne große Überschneidung; 10).

## 9. Bekannte Schwächen und Offenes
Offen beim LD2410C (4.3): Flur 21:35 und 21:56 (Eintretende zu 0,5 statt 0,9 im Flur) liegen nicht
am LD2410C: ohne seine Echoquellen im Flur 0,54 / 0,46, ganz ohne den Flur-LD2410C 0,51 / 0,62
(sonst 0,52 / 0,40). Die Person steht in der Tür zum Arbeitszimmer (−1,5 / 5,0), und welche Seite,
entscheiden 0,2 m. Dass eine Person ohne Spur und eine Echoquelle Alternativen sind, die Quelle selbst aber gegen
die Personen ohne Spur gewichtet wird, als wären sie da, ist nicht konsistent (10, „Energie aufteilen“).
Ob und wie stark er durch Wände und Türen sieht (bisher: gar nicht; die zweite Person im
Bad erscheint im Arbeitszimmer, wenn doch); die Amplitude einzelner Aufenthalte streut um einen Faktor
1,3–1,6 um das Profil, der Schweif je Raum um bis zu 3 (4.3), das Modell nimmt das mittlere; δ ist gegen teils
unkalibrierte LD2450 gemessen (0,07–0,5 m je nach Sensor); die Addition mehrerer Personen ist nicht an
Zwei-Personen-Zeiten geprüft; α, τ, κ, die Verzögerung und das Vergessen des Hintergrunds sind über die
Evidenz (7) zu schätzen; das Bewegt-Flag ist ohne Ablation weggelassen. Ein Raumteil, den kein Sensor
sieht (Küche hinter der Wand bei y = 7,7 m), kann Personen halten, die niemand ausschließt.

Näherungen, die man prüfen oder ersetzen kann:
- Auf den Kacheln ist Gehen eine Diffusion: Kurzzeitig gerades Gehen und die Richtung gehen verloren,
  beim Wechsel Gauß → Kacheln auch Geschwindigkeit und Versätze. Innerhalb einer Kachel ist die
  Masse nicht weiter aufgelöst; Raten sind über die Kachel gemittelt, auch wo eine Sichtgrenze sie
  schneidet. Die Gauß-Näherung kennt Wände nur über ihre Sigma-Punkte (5.2): Fünf Punkte je
  Komponente, Spiegelung höchstens einmal; was hinter einer Wand liegt, entscheidet die Sicht
  vom Mittel aus.
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
- ν, das Vergessen und `start_unknown` sind angenommen.
- **Bekannte Personen sammeln sich an.** Eine Spur, die an einer Tür endet, kann eine Person werden,
  die dahinter verschwindet; ist die Alternative (Geist) abgeschnitten (höchstens 12 Hypothesen),
  kommt sie nicht wieder, und wer nicht herauskommt, bleibt nach dem breiten Aufenthalts-Prior lange
  drin. Bisher räumte das jeder Neustart der App ab, seit dem gespeicherten Zustand (5.3) nicht mehr.
  Nachgespielt ab 6.10. 19:28 mit den App-Starts: drei bekannte Personen ab 20:56 (auch mit der
  früheren festen Zahl), 5–6 um 22:19 und 2:07, 8 um 7.10. 08:06, davon 6 zu 0,7–0,98 in den Bereichen
  ohne Sensor (im Haus erwartet 6,9 statt 2). In den beobachteten Räumen fiel das bisher kaum auf (Nacht
  0 s, `phantom.py` 0,0 min; 21:28, 10), die Rechenzeit von `report_eval.py` verdoppelt sich aber
  (634 statt 333 s).
- Eine neue Kalibrierung (Richtung, Maßstab) lässt weiterhin die Geisterkarte aller Sensoren und den
  LD2410C-Hintergrund dieses Sensors neu beginnen, obwohl sich der Sensor selbst nicht bewegt hat (die
  App kann Drehen und Kalibrieren nicht unterscheiden). Für die Meldung 7.10. 08:07 war das nicht die
  Ursache (Hintergrund im Nachspiel unverändert).

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
  der Arithmetik über die L·K = 35 Schichten von `steht` (seit 7.10. 15) und in der Gehmatrix
  (518 Kacheln, ~20 µs je Dichte und Takt), nicht in den numpy-Aufrufen. Als ein Matrixprodukt über alle Dichten startet
  OpenBLAS ab drei Dichten Threads, deren Warten ein Vielfaches an CPU kostet. Größte Posten danach:
  `Gauss.predict` 22 %, Kacheldichten bewegen 15 % und gewichten 8 %, LD2410C 16 %, Ausgaben 13 %.
  Mit den Energien des LD2410C (7.10., 5 Sensoren, 21 Uhr): 3,8 % statt 2,8 % mit Flag und
  Entfernung. Die Energien selbst kosten etwa ein Sechstel (je Sensor und Sekunde ein Verhältnis je
  Kachel und Person); der Rest kommt von mehr getrennten Dichten über die Hypothesen (im Mittel 8,6
  statt 4,6 Personen auf Kacheln).
- **Rechenzeit: kompilierte Kernel (Numba, 7.10.)**. Profil von 2eea738 (py-spy mit nativen Frames,
  5 Sensoren, 21:00–21:20, 14 916 Proben): 41 % im Python-Interpreter, 39 % in numpys Aufwand je
  Aufruf (Typauflösung, Iteratoren, Prüfungen), 7 % Speicher anlegen und freigeben, nur 13 % in
  numpys Rechenschleifen. Die meisten Arrays sind klein (2 Achsen × 4–6 Zustände, 5–16 Punkte); bei
  ihnen kostet der Aufruf, nicht die Rechnung. In `kernels.py` (`@njit(cache=True)`, ohne fastmath)
  stehen deshalb als Schleifen: IMM-Mischung und lineare Vorhersage von `Gauss.predict`, die
  Sigma-Punkte an den Wänden, Spiegeln und Wandkreuzung, die Erfassungsraten eines Sensors samt
  Maske, `Hidden.move` und `_regions`, die Poisson-Binomial-Zählverteilung. Ergebnisse gleich
  (report_eval auf den Meldungen vom 6.10.: Log-Evidenz −1186122,4 in beiden, Licht falsch an 0 von
  41, aus 2,08 von 15 in beiden); CPU-Zeit (bench.py, 6.10. 21:00–21:20, je dreimal abwechselnd,
  gleiche Python-Umgebung): 36,7–39,2 → 26,1–27,5 s (−29 %); Antwortzeit je Frame im Mittel
  1,39 → 0,99 ms, p99 16 → 12 ms. Im Container (Podman, derselbe Rechner, je dreimal): Alpine wie
  bisher 50,0–54,7 s, Debian slim ohne Numba 41,6–44,1 s (−18 %: musl ist für diese vielen kleinen
  Aufrufe langsamer), slim mit Numba 29,5–31,4 s (−42 % gegenüber Alpine). Die ganze App im
  Container (15 min Nachspiel ab 21:00 mit offener Live-Ansicht, CPU-Zeit des Prozesses): Alpine
  6,6 %, slim 5,5 %, slim mit Numba 4,0 % eines Kerns. Kosten: Image 430 statt 150 MB (llvmlite
  173 MB), Speicher des Prozesses 201 statt 84 MB (LLVM wird zum Laden der kompilierten Kernel
  gebraucht), Bauen ~10 s länger (hier; die Kernel werden beim Bauen kompiliert und liegen in
  `NUMBA_CACHE_DIR`, Start dann 0,2 s statt 6 s Kompilieren). Ein Aufruf mit anderen Typen oder
  einem nicht zusammenhängenden Array kompiliert zur Laufzeit neu (hier 1–4 s, auf Home Assistant
  ein Vielfaches); `tests/test_kernels.py` prüft, dass das nicht vorkommt. Nicht übernommen: die
  LD2410C-Likelihood (`Stats._mu_part`) als Kernel war genauso schnell wie numpy – dort rechnen
  k × 16 Logarithmen je Aufruf, und numpys vektorisierter Logarithmus ist schneller als der skalare.
  Was danach bleibt (Profil mit Kerneln): LD2410C 28 %, Ausgaben 11 %, `Hidden.move` 8 % (reine
  Arithmetik über die 15 Schichten), der Rest verteilt.
- **Weniger Schichten je Kachel** (7.10., 11 h aus 3.1): 5 statt 7 Arten des Aufenthalts und 3 statt
  5 Stufen κ, also 15 statt 35 Schichten von `steht`: Evidenz −1 (bei 24 Hypothesen ab 10⁻⁹ −3,
  ab 10⁻⁷ +56), Lichtfehler gleich, Rechenzeit 3,0 statt 3,3 % (21 Uhr). Mehr spart das nicht,
  weil Gauß-Mischungen, LD2410C und Ausgaben gleich bleiben. 4 Arten −11
  bzw. −9, 3 Arten −53 bzw. −100 und mehr Lichtfehler. Die Grenzen der Hypothesen bewegen die
  Evidenz um Hunderte (5.1); kleine Unterschiede zwischen Varianten sind nur sicher, wenn sie bei
  mehreren Grenzen dasselbe Vorzeichen haben.
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
- **Sigma-Punkte statt Mittel, Wände über Sigma-Punkte** (7.10., 5.2): Fehlerberichte 6.10.
  (Stand 0.10.0 → mit allem): Licht zu spät aus 3,00 → 1,67 von 15, fälschlich an 0 von 41 (beide),
  Log-Evidenz +470. Küche 21:57 (zum Lichtschalter, wie oben): Tiefpunkt Küche 0,02 → 0,75; Küche
  22:01–22:15 und die Nacht wie vorher (0,00 / 0,02, keine Sekunde über der Schwelle). Ablationen
  (zu spät aus von 15 / Log-Evidenz gegenüber allem): nur Sigma-Punkte ohne Wände 4,17 / −27;
  ohne Spiegeln in der Vorhersage 3,50 / −44; ohne Masse hinter Wänden 2,67 / −21; ohne Spiegeln
  der Sigma-Punkte bei den Raten 1,67 / −41; Wände, aber Raten am Mittel 2,17 / −205; zusätzlich das
  LD2410C über Sigma-Punkte 1,79 / −524 (nicht übernommen). Ein Fenster (Wohnzimmer 21:24) kippt
  zwischen den Varianten ganz; die Log-Evidenz ist das stabilere Maß. Rechenzeit: +10 % (CPU-Zeit,
  6.10. 21:00–21:20, 5 Sensoren, je dreimal: 33,9 → 37,1 s), verteilt auf die Sigma-Punkte bei den
  Raten, Spiegeln in der Vorhersage, Masse hinter Wänden und Wiederfinden.
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
- **Neubeginn bei neuer Kalibrierung** (bis 0.10.0, Meldung 7.10. 08:07 „Person verloren“: Leon am
  Schreibtisch, der LD2450 misst ihn minutenlang, der LD2410C ruhig Ring 2–3 bei 100): Jede neue
  Konfiguration, auch nur Richtung und Maßstab eines Sensors, ließ das Modell ohne Wissen beginnen:
  `start_people` (in Leons Konfiguration 1) bekannte Personen, keine unbekannten (Intensität im Blick
  danach ~10⁻⁸; Neuankömmlinge nur über die Treppe, ν = 1/(2 Tage)). Waren dabei beide Bewohner in Sicht,
  erklärte die eine bekannte Person die andere (im Esszimmer), die Spur am Schreibtisch blieb ein Geist:
  Nachgespielt (Gelerntes ab 6.10. 19:28, App-Start 06:50:17, neue Kalibrierung 07:44:30) mit einem
  weiteren Neubeginn um 08:05:30, 08:06:00, 08:06:15 (Modellfehler) bzw. 08:05:30, 08:05:55, 08:06:15
  (Konfiguration): in 4 von 6 Fällen Arbeitszimmer P(belegt) 0,000 bis 08:08, die Person-Alternative der
  Spur binnen 15 s unter 10⁻⁷ abgeschnitten und nie wieder da, die Energien von einer immer neu
  beginnenden Echoquelle erklärt (P(an) 1,000), der Hintergrund unverändert (5,4). Der Neubeginn um
  07:44:30 allein schadete nicht (Leon allein in Sicht). Jetzt bleiben die Personen bei neuer Kalibrierung
  (5.3): dieselben Konfigurationen (Richtung 325° statt 324°) um 08:05:30, 08:05:55, 08:06:15 ergeben
  1,000 bis 08:08. Ein Neubeginn nach einem Modellfehler bleibt wie bisher. Verworfen: eine Intensität
  unbekannter Personen beim Start (Poisson, 0,1 bzw. 0,5 Personen „irgendwo“, B_GarciaFernandez2018):
  behebt den Fall, holt aber um 21:28 (6.10., 10 min nach einem App-Start) eine dritte Person ins leere
  Wohnzimmer (Licht fälschlich an 0,88 von 41 statt 0, aus 2,0 statt 3,0 von 15). `start_people` = 2 statt
  1: report_eval unverändert (0 / 3,0, Log-Evidenz +83), der Fall auch mit dem Neubeginn um 08:06:15
  richtig.
- **Maßstab und Schweif je Sensor, Amplitude je Aufenthalt, zweiter Versuch** (7.10., verworfen bzw.
  offen): Die Streuung war zu groß gemessen (4.3: gehaltene Ziele und Echos), echt sind β 5–16. Geprüft
  mit report_eval (Basis: fälschlich aus 3,00 von 15, an 0 von 41, Log-Evidenz −1187650):
  (a) ein Maßstab je Sensor auf dem ganzen Profil, von der App gelernt wie der Hintergrund (Lösung der
  Likelihood-Gleichung der Gamma-Verteilung, Σ w (e − μ) U/μ² = 0, aus den Personen vor den Frames,
  Prior log-Streuung 0,5): gelernt Esszimmer 1,7, Wohnzimmer 1,9, Küche 2,4, Flur 0,9, Arbeitszimmer
  0,9; aus 3,67, Evidenz +717. (b) Wohnzimmer fest ×2,7 (der Offline-Wert): 3,92, +173. (c) ein
  Faktor auf den Schweif je Sensor, ebenso gelernt (Nachhall des Raums): Küche 3,5, Esszimmer 1,5, die
  anderen 0,9–1,0; 3,83, +636. In allen dreien kippt das Wohnzimmer um 21:21 (0,89 → 0,46–0,58): Wer
  einen Sensor lauter macht, lässt die Person, die dort wenig abgibt, woanders hin. Online gelernt
  nehmen Maßstab und Schweif außerdem die Energie Sitzender, die der Filter nur ungefähr kennt
  (Esszimmer 1,5–1,7, offline um 1). (d) Amplitude je Aufenthalt Gamma(6) an κ gekoppelt: 3,08,
  +1598, aber die zweite Person im Bad (21:28–21:30) wieder in der Küche (Bad 0,02–0,05). (e) dieselbe
  mit eigenen 3 Stufen (unabhängig von κ, a priori unabhängig, also exakt als eigener Vektor neben κ
  an der Gauß-Mischung): Gamma(6) 3,08 (Wohnzimmer 21:21 eine Sekunde unter der Schwelle), +1361,
  Bad und Küche richtig, Flur 21:56 0,40 → 0,51; leere Räume nachts 0 min wie die Basis; Rechenzeit
  gleich. Gamma(10) 3,08 / +1027; Gamma(3) 3,17 / +1795, aber das Bad wieder falsch. Die Kopplung an
  κ ist zu stark (gemessen Spearman 0,4, nicht 1): Sie zieht über die Energie κ und damit das Urteil
  des LD2450. Keine Variante macht weniger Lichtfehler; (e) ist die einzige ohne neuen Fehler und ist drin (Branch
  `ld-amplitude-2-s1`).
- **Energie auf mehrere Personen aufteilen?** (7.10., Leons Frage zur Meldung 08:52 „zwei im
  Schlafzimmer, eine geht ins Arbeitszimmer, gemeldet werden beide dort“; am Modell nichts geändert.)
  Die Überlagerung teilt schon: Das Mittel ist die Summe, jede Person wird gegeben das gewichtet, was
  die anderen hineingeben; eine zweite am selben Ort bekommt nur, was die erste nicht erklärt.
  Gekappte Werte (100) können nur für mehr Energie sprechen (P(e ≥ 100) wächst mit dem Mittel); eine
  zweite Person kostet in den übrigen Ringen. Eine Regel „wo schon jemand ist, bestätigt die Energie
  niemanden“ wäre keine Wahrscheinlichkeit und hätte um 08:43–08:45 geschadet, als wirklich zwei im
  Arbeitszimmer waren (der LD2410C sprach mit +1 bis +3 je Sekunde für die zweite). Die drei Meldungen
  des Morgens, nachgerechnet (Aufnahmen ab 6.10. 19:28 abgespielt wie in der App, mit Neustarts und
  der Umkalibrierung um 07:44):
  - 08:52: (1) Der LD2450 im Arbeitszimmer begann um 08:51:31,6 eine Spur auf Leon im Flur, 0,6 m neben
    dessen Vorhersage. Am Mittel der Vorhersage sieht dieser Sensor nichts (1 bzw. 6 % der beiden
    Komponenten liegen in seiner Sicht), die Rate am Mittel ist 0 (9: am Mittel statt über die
    Verteilung), „die Spur ist Leons“ hatte das Gewicht 0. Sie ging an die zweite Person (zu 0,97 in
    Schlafzimmer und Bad, 0,64) oder einen Geist (0,35); mit dem Wiederfinden der Flur-Spur tauschten
    die Personen, und die zweite lag ohne Spur dicht bei Leon (Arbeitszimmer 0,40). Die Auflösung q
    trug dazu kaum bei (ohne sie 0,43 statt 0,47). Mit der Rate über die Verteilung (5 Sigma-Punkte,
    Prototyp) gehört die Spur zu 0,999 Leon, im Arbeitszimmer sind 08:51:40–08:55:10 im Mittel 1,03
    statt 1,96 Personen. (2) Der LD2410C hielt die zweite Person dort (1,00 nach 30 s, bis 08:55;
    ohne seine Energien 0,18, ohne die gekappten Werte 0,91). Leon allein am Schreibtisch
    (08:50:05–08:50:55, 08:52:15–08:56:05) gab mehr zurück als das mittlere Profil: bewegt Ring 1–3
    1,5–1,9-fach, ruhig Ring 5–8 (3,75–6,75 m) 2–2,5-fach. Eine ruhende zweite Person irgendwo im Raum:
    log-Bayes-Faktor +86 (die gekappten Werte allein +109); mit seinem Gewinn auf dem Profil (ML 2,3,
    log-Likelihood +87 gegenüber 1) noch +14. Am selben Platz am 6.10. 22:22–22:50: Gewinn 0,8, eine
    zweite −71 (`tools/ld2410/second.py`). Ob der ferne Überschuss am Morgen von Leon kommt oder von
    der zweiten Person hinter der Wand (das Bad liegt in 4–7 m, 35° neben der Achse, 9), ist offen.
  - 08:45 (zwei im Arbeitszimmer, eine im Bad gezeigt): Der Überschuss über die beiden Profile schaltete
    ab 08:42:56 eine Echoquelle ein (0,10 → 0,98 bis 08:43:06). Mit der Quelle an zählt die zweite
    Person im Blick nur mit P(keine Quelle) und ging ins Bad (08:43–08:45 im Mittel 1,04 Personen im
    Arbeitszimmer; ohne Echoquellen 2,12, ohne diesen LD2410C 1,72). Das ist nicht konsistent: Die
    Quelle wird gegen die Personen ohne Spur gewichtet, als wären sie da, die Person dann, als nähme
    die Quelle ihren Platz ein. Geprüft und verworfen: (a) Person und Quelle addieren sich wie zwei
    Personen, die Person gegeben die Quellen als Mischung über deren Zustände (exakt für das Paar):
    08:45 richtig (2,00); Licht fälschlich aus 2,67 statt 3,00 von 15, an 0 von 41, Log-Evidenz +809;
    aber im leeren Raum 71 statt 0 Minuten Licht an (Küche 6.10. 19:10–20:15), Personen ohne Spur
    bleiben (Arbeitszimmer 08:46 25 s nach dem Gehen), Rechenzeit 3,7 statt 2,7 %. (b) Dasselbe mit
    dem Mittel der Quellen statt der Mischung, und (c) die Quellen auch in ihrer eigenen Gewichtung
    gegen die Personen ohne Spur als Alternative: die unbekannten Personen wuchsen auf 1,4 bzw. 5,7 im
    Blick. Der Ausschluss unterdrückt Geister, solange eine Person mehr zurückgibt als ihr Profil.
  - 08:29 (die zweite Person „durch die Wand ins Treppenhaus“): Sie saß 1 m vor dem Esszimmersensor
    und gab weniger zurück als das Profil (Gewinn 0,6, ruhig fern 0,2–0,3-fach); der LD2410C sprach je
    Sekunde mit −0,4 bis −1,7 gegen sie, und als ihre Spur riss, ging sie durch die Tür (08:29:22–29
    Treppenhaus 0,99). Ohne diesen LD2410C blieb sie (1,00).
  Alle drei kommen aus der Amplitude einer Person je Aufenthalt (4.3: 10–90 % 0,14–2,3), die das
  Modell bis 0.10.0 nicht hatte (seitdem drin, siehe den Eintrag davor): zu groß erklären eine zweite Person oder eine Echoquelle den Rest, zu klein schiebt
  die Energie die Person hinaus.
- **Neustart mit dem gespeicherten Zustand statt `start_people`** (7.10., 5.3). `report_eval.py`
  gegen die Wahrheit vom 6.10. (41 leere, 15 belegte Fenster), wie die App gelaufen ist (App-Starts
  19:28, 21:02, 21:14, 21:18, 22:19, 2:07): vorher (b3669ff, bei jedem Start `start_people` = 1)
  Licht fälschlich an 0 von 41, aus 1,67 von 15, Log-Evidenz −1187179,8, CPU 333 s; mit dem
  gespeicherten Zustand 0,88 / 1,67, −1186965,1 (+215), 634 s; dasselbe mit 30 s Pause vor jedem Start
  (die App lief nicht, die Aufnahme schon) 0,14 / 1,17; bei jedem Start ohne Wissen 0,14 / 1,67.
  Durchgehend ohne Neustart: Start ohne Wissen 0,88 / 1,67 (−1192861,3), mit `start_people` = 1
  0 / 1,67 (−1193001,8). Die 0,88 sind eine Episode von etwa 20 s in zwei Meldungen (21:28:14 und
  21:28:36): Leon geht aus dem Wohnzimmer in die Küche, das Modell hält dort noch jemanden (0,98 → 0,26
  in 20 s), durchgehend mit `start_people` = 1 sofort 0. Sie hängt an der dritten bekannten Person seit
  20:56 (9), die alle Varianten haben; ob jemand im Wohnzimmer zurückbleibt, kippt mit Kleinigkeiten
  (Spuren, die der Neustart beendet; 30 s Pause; der Start um 19:28), auch mit der Intensität nur in
  Sicht oder halb in Sicht, halb hinter Türen (je 0,88). Vorher verschwand die dritte Person beim Start
  um 21:02. Die oben verworfene Intensität beim Start ist jetzt der Start ohne Wissen; ihr Fehler um 21:28
  war diese Episode. Meldung 7.10. 08:07 nachgespielt (Gelerntes und Zustand ab 6.10. 19:28 mit den
  App-Starts, neue Kalibrierung 07:44:30 mit config6, Neustart 08:06:00): mit dem Zustand von 08:06 und
  mit dem gespeicherten von 08:00:17 Arbeitszimmer und Esszimmer ab 10 s nach dem Start durchgehend
  1,000 bis 08:09, ohne Wissen ebenso (die Intensität in Sicht nimmt beide); die Küche dabei
  mindestens 0,34 / 0,95 / 0,01 (wer dort war, ist nicht bekannt). `phantom.py` 18:00–6:15 0,0 min.
- **Die Stille vor einem Frame** (7.10., 4.3, 4.4): Die Firmware sendet einen Frame je LD2450-Frame
  (0,089 s), solange der LD2450 ein Ziel hat oder ein Flag des LD2410C an ist, sonst alle 5 s einen
  Herzschlag; den ersten leeren Frame nach einem vollen sofort. Bis 2eea738 stand jeder Frame für die
  Zeit seit dem vorigen: Der erste Frame nach einer Stille, gesendet, weil eine Energie gestiegen war,
  stand für die ganze Stille davor (im Mittel 2–3 s statt 0,089 s; 70–120 solche Frames je Stunde und
  Sensor, Küche 3, fast alle Episoden nur der Flags). Nachgespielt auf Strecken mit vollem Takt (der
  LD2450 hatte ein Ziel, die Flags meist aus; 11–82 min je Sensor, `tools/ld2410/thinning.py`), die
  mittlere Energie gegen den vollen Strom: so bewegt ×1,01–1,30, ruhig ×0,99–1,02; „die Stille gehört
  zum Frame davor“ ×0,99–1,02 / ×1,01–1,07; „in der Stille liegt jeder Ring unter seiner Schwelle“
  (zensierte Gamma-Beobachtung mit den Schwellen der Firmware) ×1,05–1,44 / ×1,05–1,65. Die Flags sind
  keine Schwellen auf die gemeldeten Energien: Bei Bewegt-Flag aus liegt ein Ring in 2–32 % der Frames
  über seiner Schwelle, bei Ruhig-Flag aus in 4–18 %; und die Gamma-Form trägt die Aussage „unter 15–50“
  nicht. Ohne Person (kein LD2450-Ziel ±30 s, 6./7.10., 19 h) war die Energie mit der alten Gewichtung
  bewegt um 25–30 % zu hoch (Arbeits-, Ess-, Wohnzimmer; Flur 8 %, Küche 0), ruhig gleich. Gelernt im
  Filter (6.10. 18:00 bis 7.10. 06:00), alle Ringe zusammen alt gegen neu: Arbeitszimmer +14 %,
  Esszimmer +13 %, Wohnzimmer +15 %, Flur +5 %, Küche 0; bewegt Ring 2–8 5,6–5,8 → 4,1–4,3 (offline
  4,0–4,3); die Echorate im Flur 0,51 → 0,24 je Stunde. Die Messungen in 4.3 (Hintergrund, Profil,
  Formen, Gewinn, Amplitude) waren schon „bis zum nächsten Frame“ gewichtet (`tools/ld2410/geom.py`),
  τ auf Strecken mit vollem Takt gemessen: Sie bleiben. report_eval (Fenster bis 6.10.): Licht
  fälschlich aus 2,08 → 1,00 von 15 (Wohnzimmer 21:24 0,23 → 0,86), an 0 von 41; mit 16 Hypothesen
  ab 10⁻⁷ aus 2,08 → 1,00, an 0,12 → 0; leere Räume 18:00–06:15 0 min wie vorher; Rechenzeit (21:00–21:20, je zweimal
  gleichzeitig) 2,7 statt 3,6 %. Niedriger werden
  Bereiche ohne Sensor (Bad 22:18 0,99 → 0,52, Schlafzimmer 21:31 0,94 → 0,51; welcher der beiden,
  zählt nur für die Türen) und die Sitzende im Esszimmer 21:03 (0,93 → 0,74, über der Schwelle). Die
  Log-Evidenz ist zwischen den Gewichtungen nicht vergleichbar (die Gewichte der Messungen ändern
  sich). Der Prior des Hintergrunds aus den 19 h (bewegt Ring 0 16, Ring 1 9, sonst 4,3, ruhig 4,9
  statt 13 / 9 / 4,5 / 5): Lichtfehler gleich, Evidenz +17, nicht übernommen. Offen: Der erste leere
  Frame nach einem vollen steht für die Stille danach, obwohl die Ruhig-Energien noch abklingen (ruhig
  bis ×1,07).
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
