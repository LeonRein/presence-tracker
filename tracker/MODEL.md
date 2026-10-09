# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand: Code 0.25.1 mit den unveröffentlichten Änderungen vom 8./9.10.2026 (Kandidat für 0.26, 10 „Kandidat 0.26“).
Beschreibt, was der Code rechnet; was fehlt oder nur genähert ist,
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
sprechen, senken sie, wie sie die Dichte formen. In Sicht endet sonst nichts. Wo kein Sensor sie prüfen
kann (tote Winkel, offene Bereiche ohne Sensor, außer Haus), läuft ihr Eintrag nach Minuten ab (5.5): eine
Annahme über Einträge ohne Beleg, keine Bewegung. In einem geschlossenen Bereich (Balkon) läuft nichts ab:
Wer dort ist, kommt nur durch dessen Tür zurück, und dass niemand herauskommt, ist die Prüfung. Wo sie
ist, ändert sich weiter nur über Türen und Wege.

## 2. Die Welt

Aus dem Grundriss (`model.py`, `world.py`):
- **Beobachteter Bereich:** Räume, die die Sensoren zu mindestens 60 % sehen.
- **Bereiche ohne Sensor *Rₖ*:** übrige Räume der Wohnung, über Türen zusammengefasst. *Offen*, wenn
  eine Tür von dort nach draußen führt oder eine Eingangszone darin liegt, sonst *geschlossen* (Balkon,
  Schlafzimmer).
- **Außer Haus:** alles jenseits der Wohnungstür. Ein Raum mit dem Haken *Eingang* (das Treppenhaus)
  ist kein Raum der Wohnung, sondern öffentlicher Raum, den auch andere Hausbewohner nutzen (Leon,
  7.10.: „Die Personenzahl hinter Treppe macht keinen Sinn, da dies öffentlicher Raum ist.“). Seine
  Fläche gehört zu *außer Haus*; die Tür dorthin ist die Wohnungstür, eine Tür nach draußen wie eine
  in einer Außenwand. Wer hindurchgeht, hat die Wohnung verlassen; wer hereinkommt, kommt von draußen.
  Was ein Sensor dort misst, gehört zu niemandem der Wohnung und wird wie in einem geschlossenen Raum
  ohne Sensor verworfen (4.1: keine Information). Der gezeichnete Raum bleibt, weil er die Tür
  festlegt.
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
- Aufenthalt in *Rₖ*: log-normal, Median 2 min / Streuung von ln(Dauer) 1,5, für jeden Bereich gleich
  (angenommen; `unobserved.py`). Nicht gelernt: Wann jemand hinein- und herausging, kennt der Filter
  nur als Wahrscheinlichkeit; seine Urteile als Dauern zu zählen, lernte seine Fehler. Zu schätzen
  über die Evidenz (7). Endet der Aufenthalt, geht man durch eine der Türen des Bereichs, jede gleich
  wahrscheinlich: gehend in den beobachteten Bereich oder durch eine Tür nach draußen außer Haus.
- **Hinaus** geht man nur durch Türen: aus dem beobachteten Bereich gehend durch die Wohnungstür (die
  Diffusion aus 3.2 über die Tür, wie in einen Bereich ohne Sensor), aus einem offenen Bereich am
  Ende eines Aufenthalts (oben). Eine eigene Rate des Weggehens gibt es nicht mehr.
- **Zurück** mit 1/(4 h) je Weg hinein (Türen nach draußen, Eingangszonen) (angenommen); man erscheint
  gehend an der Tür (beobachteter Bereich) bzw. beginnt einen Aufenthalt im offenen Bereich.
- Bis 0.12.0 war das Treppenhaus ein offener Bereich *Rₖ* mit eigenem Aufenthalt (Median 30 min,
  Streuung 2,0) und eigener Rate nach außer Haus (1/(2 h)); die Anzeige und Home Assistant bekamen dort
  eine Personenzahl, die nichts bedeutet (wer dort war, war fort oder ein Nachbar).

### 3.4 Neuankömmlinge, Personenzahl
- Neue Personen kommen als Poisson-Prozess an jedem Weg hinein an (Türen nach draußen,
  Eingangszonen; in der Wohnung 6.10. nur die Wohnungstür zum Treppenhaus), mit ν = 1/(2 Tage) je Weg
  (angenommen); danach verhalten sie sich wie alle (3.1–3.3).
- Wer außer Haus ist, wird vergessen: die unbekannten Personen mit 1/Tag (angenommen), der Eintrag
  einer bekannten wie überall, wo kein Sensor prüft, mit 2 min (5.5). Kommt er danach wieder, ist er
  ein Neuankömmling. So bleibt die Zahl der Personen, die man erwartet, endlich.
- „Selten ein Dritter“ folgt aus ν und den Daten, nicht aus einer Regel.
- Wie viele Personen da sind, ist nirgends fest eingestellt (mal einer, mal zwei, mal keiner, mal ein
  Gast). Ein Neustart der App setzt das Wissen über sie fort (5.3); weiß das Modell nichts, sind die
  unbekannten eine Poisson-Intensität ohne feste Zahl (5.3, 5.5).
- Auch eine bekannte Person ohne Spur ist nur wahrscheinlich da: Sie existiert mit einer
  Wahrscheinlichkeit r (Bernoulli, 5.5). War ihre Spur vielleicht ein Geist oder eine schon bekannte
  Person, bleibt diese Möglichkeit als r < 1 erhalten, und was danach nicht zu ihr passt (keine neue
  Spur, wo sie gesehen würde; kein Herauskommen aus einem Bereich ohne Sensor; die Energien des
  LD2410C), senkt r. Das ist keine Regel: dieselbe Rechnung wie für ihre Dichte (1.3, Anmerkung).
  Der Teil von ihr, den kein Sensor prüfen kann, läuft mit 2 min ab (5.5).

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
  außerhalb aller Räume, in geschlossenen Räumen ohne Sensor, näher als 0,3 m am Sensor. „Hinter einer
  Wand“ heißt: kein Punkt im Umkreis von 0,4 m ist in Sicht (der Messfehler einer Person, die es ist;
  geprüft zurück entlang der Sichtlinie und am Fußpunkt auf jeder Wand im Umkreis). Bis 0.18.0 nur
  entlang der Sichtlinie: Ein Sensor an einer Wand blickt an ihr entlang, und ein Ziel 5 cm jenseits
  dieser Wand lag entlang der Sichtlinie 0,4 m dahinter (10, „Meldungen 7.10. abends“).
  **Wer gemessen wird, ist in Sicht** (seit 0.25.1): Der Sensor sieht nicht durch Wände; ein bis 0,4 m
  jenseits einer Wand angenommenes Ziel kommt von einer Person, die er sieht. Das Kalman-Update (5.2)
  kennt keine Wände: Eine Spur an der Wand zog das Mittel hinüber, die Person stand im Nachbarraum (10,
  „Durch die Wand ins Arbeitszimmer“). Nach jedem Update mit einer Spur wird die Position je Komponente
  auf die Sicht des Sensors beschnitten (`Gauss.in_sight`): N(Mittel, diag Varianz) auf einem Gitter von
  5 × 5 Gauß-Hermite-Punkten, die Punkte, deren Sichtlinie (ab 20 cm vor dem Sensor) eine Wand kreuzt,
  entfallen, Momentenabgleich (mindestens 5 cm Streuung je Achse), Geschwindigkeit und Versätze folgen
  über ihre Regression. Sieht er keinen der Punkte, werden sie an der Wand
  gespiegelt wie die Bewegung (3.2). Das ist die Likelihood mal „in Sicht“ als 0/1 (nur Wände, nicht
  das Blickfeld); die Gewichte der Hypothesen ändert der Schnitt nicht.
  Die Regel „LD2450 des Raums“ (6, `roomseen.py`) bleibt beim Ziel: Ein Ziel jenseits der Wand liegt im
  Nachbarraum und zählt dort nicht, weil nur der eigene Sensor eines Raums zählt.
- Die drei Plätze rücken auf; Ziele werden über die Nähe verknüpft (≤ 0,6 m je Frame).
- Gehaltene Frames sind keine Messung: ≥ 3 Frames mit derselben Geschwindigkeit ≠ 0 (der Sensor lässt
  ein verlorenes Ziel weiterlaufen) oder bitgleiche Koordinaten (eingefroren).
- Mehr als 6 s ohne Frame: Datenverlust, alle Spuren enden, 6 s nach dem letzten Frame, ohne auf den
  nächsten zu warten (bis 0.18 erst mit ihm: Ein ausgefallenes Board hielt seine Spuren stundenlang).

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
  wird am Tisch alle halbe Minute erfasst, auf dem Sofa minutenlang nicht. κ ~ Gamma(α, α), Mittel 1;
  neu bei jedem Stehenbleiben, innerhalb eines Aufenthalts mit 1/(600 s) neu gezogen (D_Mahler2011
  Gl. 49–52; D_Wilthil2019 Gl. 2). **Der Filter führt den Posterior von κ, nicht seine Momente** (seit
  8.10.): ein Gitter von 5 Zellen gleichen Abstands in log κ zwischen dem 0,1-%- und dem 99,9-%-Quantil
  (die äußeren Zellen reichen bis 0 und ∞), je am bedingten Mittel mit der Masse des Priors in der
  Zelle, wie die Amplitude des LD2410C (4.3; ein Punktmassenfilter über κ, 0_Arulampalam2002 Abschn.
  II-B): 0,003 / 0,020 / 0,12 / 0,61 / 2,18 mit 0,006 / 0,028 / 0,15 / 0,51 / 0,31. Warum ein Gitter
  in log κ: Was die Stufen tragen müssen, ist `E[e^(−κ·a·T)]` für eine Sitzende, die die Zeit T mit
  der Erfassungsrate a nicht erfasst wird, also die Laplace-Transformierte des Priors an s = aT; sie
  hängt nur von log κ + log s ab, ein gleichmäßiges Gitter in log κ ist deshalb über alle Zeitskalen
  gleich genau. Gerechnet (nicht gemessen) für α = 1, a = 0,034 /s (`acquire`, nah, ein Sensor) mit dem
  Neuziehen, gegen ein feines Gitter (4000 Zellen): nach 1 / 5 / 10 / 20 / 60 min ungesehen 0,91 / 0,83
  / 0,79 / 0,75 / 0,74 des Richtigen (7 Zellen 0,94–0,85, 9 Zellen 0,97–0,90; bei a = 0,1 /s ebenso).
  **Bis 0.21** 3 Punkte der verallgemeinerten Gauß-Laguerre-Quadratur (Golub & Welsch 1969), 0,42 /
  2,29 / 6,29 mit 0,71 / 0,28 / 0,01: exakt für die Momente bis zum 5., aber nicht für `e^(−κaT)`,
  das kein Polynom niedrigen Grades ist; die unterste Stufe entschied allein, wie lange eine Sitzende
  ungesehen bleiben konnte: 0,94 / 0,13 / 5·10⁻³ / 4·10⁻⁶ / 3·10⁻¹⁹ des Richtigen (Review 8.10., 1.7).
  Die konjugierte Form (Gamma(α, β) mal `e^(−κR)` ist Gamma(α, β + R), je Komponente und Kachel ein
  (α, β)) ist nur ohne Neuziehen exakt: Das Neuziehen, neue Aufenthalte und die zensierte Erfassung
  beim Start (`1/(1 + κc)`, 4.1 unten) machen Mischungen, die je Schritt wieder auf eine
  Gamma-Verteilung projiziert werden müssen (Mittel und Mittel des Logarithmus); so gerechnet 1,00 /
  0,88 / 0,74 / 0,65 / 0,83, nicht genauer als 4–5 Gitterzellen, aber mit einer Newton-Iteration über
  Digamma je Kachel und Takt statt einer Multiplikation. Vergleiche in 10, „Review 8.10.“ (bis 7.10.
  gleich wahrscheinliche Stufen an ihren Mitteln, 0,19 / 0,71 / 2,10: Log-Evidenz mit der Quadratur
  +466 / +513 auf den Meldungen bis 6.10. / vom 7.10., Lichtfehler gleich; 5 Stufen −2, 4 Stufen −0,3
  gegenüber 3 Quadraturpunkten, dieselben 11 h wie 3.1; das waren Vergleiche verschiedener diskreter
  Verteilungen, nicht verschiedener α).
  α = 1 (angenommen). Mit der Quadratur über die Evidenz dieser 11 h nicht zu bestimmen: α = 0,5 −5,
  α = 2 +17, mit 24 Hypothesen ab 10⁻⁹ aber −82 (5.1). Aus den bekannten Aufenthalten (5.5) auch nicht: Der LD2450 im
  Arbeitszimmer hatte Leon am Schreibtisch fast immer in einer Spur (6.10. abends 233 s ohne Spur in
  79 min, 7.10. 0–14 s je 10 min), und ohne Spur fand er ihn mit 0,01–0,27 /s wieder (κ ≈ 0,3–8, keine
  Masse nahe 0, aber zu wenige Fälle für eine Form). Gehende: κ = 1.
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
    EM auf 21 h), Gewicht ein Geist je Zelle (die Form α = 1 der Gamma-Verteilung wie J_Luber2014
    Gl. 6.13; bei dieser Rate entspricht das 96 h Beobachtung, bis 0.12.0 waren es 4 h = 0,04 Geister,
    10). Zählungen mit 0,3 m verschmiert (Versatz des Sensors; J_Park2020 Abschn. 1).
  - `λ_e` = 3·10⁻⁴: Mehrwegeechos laufen mit Gehenden mit (angenommen).
- **Lebensdauer:** Mischung zweier Exponentialverteilungen, 40 % mit 3 s, 60 % mit 39 s (geschätzt
  per EM 6.10. offline, `tools/ghostmap.py`). Fest: Die App lernt sie nicht weiter (bis 0.12.0 tat
  sie das mit der Karte, 10). Die Lebensdauer ist, was Geister von Sitzenden trennt; aus dem eigenen
  Urteil gelernt, folgte sie den Personen, die der Filter für Geister hielt.
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
  Startpunkt mit P(Geist) des Filters; die Beobachtungszeit jedes Sensors ist die Belichtung.
  P(Geist) wird am Ende der Spur beurteilt (Bewegung, andere Sensoren, Lebensdauer zählen mit), aber
  ohne das, was die Karte selbst an dieser Stelle sagte: Ihr Faktor bei der Geburt wird aus den Odds
  herausgerechnet, als hätte dort die Prior-Rate gegolten (J_Park2020 Gl. 37–41:
  Clutter-Wahrscheinlichkeit ohne die Clutter-Schätzung). Sonst bestätigt sich die Karte selbst
  (Simulation: Sitzplatz mit vorgegebener Karte, alt bis 0,34 je Spur gelernt, jetzt < 0,01).
  Grenze dieser Korrektur: Am Ende einer Spur ist P(Geist) oft genau 1, weil die Hypothesen, in
  denen sie einer Person gehörte, unter die Schwelle 10⁻⁷ gefallen sind (6.10. am Sitzplatz der
  zweiten Person 15 von 20 Spuren); was abgeschnitten ist, rechnet keine Korrektur zurück, die
  Zählung ist dann ein hartes Urteil. Deshalb wiegt der Prior einen Geist je Zelle: Wenige solche
  Urteile machen eine Stelle nicht zur Quelle (Test: drei Geister in 2,5 h 1,8-mal statt 13-mal die
  Prior-Rate; eine Reflexion mit 30 Spuren an einem Tag 7,6-mal statt 29-mal). Vergessen mit
  Zeitkonstante 14 Tage. Wird ein Sensor
  verschoben, gedreht, anders kalibriert (Höhe, Spiegelung, Maßstab), hinzugefügt oder entfernt,
  beginnt die Karte neu, ebenso eine mit einem anderen Prior gelernte. Die Oberfläche zeigt sie je
  Sensor. `tools/ghostmap.py` rechnet dasselbe
  offline auf gewählten Zeitfenstern.
- Begründung: Wiederkehrende Reflexionen (Möbel, Ladestation des Saugroboters) stehen an festen
  Stellen. Mit gleichverteilter Dichte erklärt das Modell sie als Person.
- Solange der Schalter *Lernen pausieren* an ist (Hintergrundaktivität, 10), lernt die Karte nichts:
  keine Beobachtungszeit, und keine Spur, deren Leben in die Pause reicht. Ebenso der Hintergrund und
  die Echorate des LD2410C (4.3), die Zielkarte (6) und die Kalibrierdaten (10); das Verfolgen bleibt.

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
  Spur gibt g·S ab, g ~ Gamma(6, 6) (gemessen β 5–16, s. o.). Der Filter führt den Posterior von g,
  nicht seine Momente: ein Gitter von 9 Zellen gleichen Abstands in log g zwischen dem 0,1-%- und dem
  99,9-%-Quantil, je am bedingten Mittel mit der Masse des Priors in der Zelle (0,21 / 0,30 / 0,40 /
  0,54 / 0,72 / 0,97 / 1,28 / 1,70 / 2,28 mit 0,004 / 0,013 / 0,041 / 0,108 / 0,211 / 0,282 / 0,228 /
  0,096 / 0,018; ein Punktmassenfilter über g wie die Kacheln über den Ort, 0_Arulampalam2002 Abschn.
  II-B). Bis 0.19.0 3 Quadraturpunkte wie κ (0,58 / 1,22 / 2,20): Eine Person, die ein Drittel des
  Profils zurückgab, erklärte die Energie schlechter als eine Echoquelle mit ihrer Stufe 0,3 (10,
  „Meldungen 7.10. abends“). Neu bei jedem Stehenbleiben, innerhalb mit 1/(600 s) neu gezogen,
  **zusammen mit κ** (seit 8.10.): Eine Haltung, die wenig zurückwirft, sieht auch der LD2450 seltener
  (oben: Spearman 0,37 bewegt / 0,47 ruhig zwischen g und seiner Wiederfinderate). Unabhängig
  gerechnet zählten beide Hinweise gegen eine solche Sitzende doppelt (Review 8.10., 1.8.5). Der
  gemeinsame Prior von κ und g ist eine Gauß-Copula (Nelsen 2006) mit der Korrelation der
  Normal-Scores ρ = 2 sin(π ρ_S / 6) = 0,44 aus dem Mittel ρ_S = 0,42 (Kruskal 1958), auf den Zellen
  beider Gitter (5 × 9, die Ränder exakt Gamma(1) und Gamma(6, 6)); E[g | κ] 0,59 / 0,68 / 0,79 /
  0,96 / 1,20. Die Gauß-Mischung führt die gemeinsame Verteilung (5 × 9 Zahlen); bis 0.21 zwei
  Vektoren, und ein Teil ohne Messung des LD2410C zählte beim Mischen mit gleich wahrscheinlichen
  Zellen statt mit dem Prior. Vorbehalt: Gemessen ist die Kopplung an die Wiederfinderate, nicht an κ
  selbst. Gehende und Personen ohne Spur: g = 1 (auf den Kacheln geht g verloren wie die
  Geschwindigkeit); wer von den Kacheln eine Spur bekommt, erhält g aus dem Prior gegeben sein κ.
- **Gemeinsamer Pegel u** je Block (1 s) und Art: 1/u ~ Gamma(κ, κ), κ = 16 bewegt, 50 ruhig (gemessen,
  s. o.), gemischt mit 2 % Schüben, 1/u ~ Gamma(4) mit u um 2,5 (angenommen; nur nach oben: ein
  niedriger Pegel darf eine fehlende Person nicht entschuldigen). Konjugiert: für die nicht gekappten
  Energien exakt herausintegriert; die gekappten (100, `P(e ≥ 99,5)`) rechnen mit dem Mittel μ ohne
  den Pegel (`Stats._mu_part`), eine kleine Abweichung vom Modell.
  Ohne ihn erklärte eine gleichmäßig erhöhte Energie aller Ringe (nachts im Arbeitszimmer ruhig
  doppelt so hoch wie gelernt, Schübe) eine Person am Rand des Strahls.
- **Hintergrund b je Sensor, Ring und Art: von der App gelernt** (wie die Geisterkarte): Online-EM der
  Überlagerung, der Anteil des Hintergrunds an einer Energie ist im Mittel `e · b/μ` (Richardson 1972;
  Shepp & Vardi 1982), μ aus dem Stand vor diesen Frames (die Energien lernen nicht ihr eigenes Urteil,
  J_Park2020). Eine gekappte Energie (100) zählt mit dem, was sie war: `b · Q(α+1, x) / Q(α, x)`,
  `x = α · 100/μ`, der Anteil des Hintergrunds an `E[e | e ≥ 100]` (E-Schritt zensierter Daten,
  Dempster, Laird & Rubin 1977; bis 0.20 zählte sie als 100, und neben einer Person, die mehr gibt,
  lernte der Hintergrund zu wenig: Arbeitszimmer Ring 2 abends 2,0 statt leer 6,5, 10 „Lernschleifen“).
  Gelernt wird nur aus der Zeit, in der der LD2450 im selben Gehäuse kein Ziel in Sicht hat (ein
  unabhängiges Signal: wo er etwas sieht, ist es eine Person oder ihr Echo, und dort sagen die Energien
  über b wenig, solange das Profil nicht genau stimmt), und nur, soweit keine Echoquelle an ist
  (gewichtet mit P(keine Quelle) nach diesen Frames): Eine Quelle, die länger an ist, als Echos leben,
  ist eine Person, die der Filter verloren hat, und ihr Profil passt nur ungefähr; was es nicht
  erklärt, wurde Hintergrund. Ohne beides lernte der Hintergrund Sitzende und Schlafende (10
  „Lernschleifen“). Nicht nach dem Urteil des Filters über Personen ohne Spur: Dort halten die Energien
  selbst eine Person, wenn b zu klein ist, und das Lernen stünde still (gemessen: 4 h Licht im leeren
  Schlafzimmer, 10). Prior: bewegt Ring 0
  13, Ring 1 9, sonst 4,5, ruhig 5 mit dem Gewicht 10 min (gemessen 6.10., 4,3 h; auf 19 h 16 / 9 /
  4,3 / 4,9, gleiche Lichtfehler, 10 „Die Stille vor einem Frame“); Vergessen 6 h (angenommen; leere
  Nächte 24 h auseinander unterscheiden sich je Sensor um höchstens 7 %, aber mit 1 Tag Log-Evidenz
  bis 6.10. −2530, 10). Neu für einen Sensor, wenn er verschoben wird. Gespeichert mit der Geisterkarte.
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
  Sensoren keine. ρ lernt die App je Sensor mit dem Hintergrund (Gamma-Poisson: erwartete Zahl
  begonnener Quellen je Beobachtungszeit; Prior 1 je 3 h mit dem Gewicht von 3 h, angenommen). Gezählt
  wird nur, wo niemand im Blick ist (beides, Zahl und Zeit, gewichtet mit P(niemand im Blick) vor den
  Frames: Echoquellen sind Energie von niemandem, und neben einer Person zählte, was ihr Profil nicht
  erklärt, als Quelle), und jede Zählung wie bei der Geisterkarte ohne die gelernte Rate beurteilt: die
  Odds „eine Quelle begann“ mit ρ_Prior/ρ statt ρ (J_Park2020 Gl. 37–41). Sonst wäre, was die Rate
  sagt, Beleg für sie selbst (bis 0.20: Simulation, eine Sitzende 1,8 m vor dem Sensor, 0,33 → 0,73
  je Stunde in einer Stunde; jetzt 0,34, 10 „Lernschleifen“).
  Verrechnung: Die Personen wie oben, die Echoquellen zuletzt (für die Gewichte der Hypothesen). Eine
  Person ohne Spur und eine Echoquelle sind Alternativen (beide selten): Die Dichte einer Person
  bekommt im Blick `P(keine Quelle) · ℓ(Person)`, außerhalb `P(keine Quelle) + Σ P(Quelle) ℓ(Quelle)`.
  „Im Blick“ heißt für eine Person mit Spur wie für Kacheln: eine Komponente, die dem LD2410C mehr als
  `FLOOR` gibt; eine Komponente außerhalb wird nicht gewogen (seit 8.10. abends, `_ld_points`). Bis 0.25
  zählte jede Komponente als im Blick jedes LD2410C, mit null Energie: Sie bekam `P(keine Quelle)`, ihr
  Teil „durch eine Tür“ (5.2) außerhalb 1. Jede wahrscheinliche Echoquelle irgendwo schob so alle
  Personen mit Spur im Haus durch eine Tür hinaus (10, „Meldungen 8.10. abends“).
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
- **Erst zusammenlegen, dann abschneiden** (seit 8.10.): Die Kinder einer Verzweigung, die über die
  laufenden Spuren dasselbe sagen, werden zuerst eine Hypothese (5.6). Der Schlüssel sagt nicht,
  *welcher* Person eine Spur gehört: „sie gehört u₁“, „u₂“, „einer neuen Person“ desselben
  Elternteils sind eine Hypothese mit der Summe ihrer Gewichte. Dann wird abgeschnitten: von der
  schwächsten an, solange die verworfenen zusammen höchstens 10⁻⁷ der Masse wiegen, und höchstens 12
  bleiben. Abschneiden nach Gewicht minimiert den L1-Fehler, und der ist dann höchstens das Doppelte
  der verworfenen Masse (B_Vo2017 Abschn. II, III-A; dort auf die Masse bezogen, nicht auf das
  stärkste Kind). Die verworfene Masse zählt in der Evidenz mit (7). Bis 0.21 wurde jedes Kind
  einzeln abgeschnitten, unter 10⁻⁷ des *stärksten*, bevor gleiche zusammengelegt wurden: viele kleine
  Alternativen gingen verloren, deren Summe geblieben wäre (Zahlen: 10, „Review 8.10.“).
- **Keine laufende Spur verliert eine Alternative ganz** (seit 8.10. abends, `Tracker._cut`): Ob eine Spur
  ein Geist ist oder einer Person gehört, ist die Frage dieser Spur (in einem spurorientierten PMBM ihr
  eigener Bernoulli, B_GarciaFernandez2018 Abschn. IV; ebenso hält JIPDA je Spur ihre Existenz). Was ihre
  späteren Messungen dazu sagen (ein Geist lebt 3 / 39 s, 4.2; eine Person bleibt), wirkt nur auf eine
  Alternative, die noch da ist. Sagen alle behaltenen Hypothesen über eine laufende Spur dasselbe
  („Geist“ oder „Person“), bleibt von den verworfenen die stärkste, die das andere sagt (die Grenze von 12
  wird dafür um höchstens die Zahl der laufenden Spuren überschritten). Bis 0.25 fiel die Alternative
  „Person“ einer Spur, die dort begann, wo der Filter niemanden kannte, schon bei der Geburt unter die
  Schwelle; danach sagten alle Hypothesen „Geist“, der Faktor der Lebensdauer (`_ghost_life`) kürzte
  sich bei der Normierung heraus, und die Spur blieb ein Geist, wie lange sie auch gemessen wurde (das
  Bett 8.10. 00:29–06:14, der Schreibtisch 7.10. 08:59–09:49; 10, „Meldungen 8.10. abends“).
- Bis 0.21 (Zahlen dieses Absatzes): höchstens 12, solange über 10⁻⁷ des stärksten (Abschneiden nach
  Gewicht, B_Vo2017). Auf den 11 h aus 3.1 greift die Grenze von 12 bei 8 % der Schnitte (461 von 5588), im
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
2. Lineare Vorhersage je Komponente (3.1 bzw. OU-Näherung 3.2), Kalman-Update mit der Spur, danach
   die Position auf die Sicht des messenden Sensors beschnitten (4.1).
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
  Tür in einen Bereich ohne Sensor oder nach draußen (ins Treppenhaus, 2) ebenso, mit d = doppelter
  Abstand zur Tür. Wände sind keine
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
- **Was keine Messung prüfen kann, läuft ab** (`unseen_life` = 2 min, `Tracker._expire`, seit 8.10.;
  Review des Algorithmus 1.3). Wo ein Sensor eine bekannte Person ohne Spur sähe, entscheiden die
  Messungen allein (`1 − r + r ∫ Dichte × Likelihood`, oben); dort endet nichts (Vorgabe 1.3): Eine
  Sitzende in Sicht bleibt, solange keine Messung gegen sie spricht, wie lang ihre Lücke auch ist, und
  zwei, die der LD2450 als ein Ziel sieht, bleiben zwei. Wo kein Sensor hinsieht – ein toter Winkel
  eines Raums, ein offener Bereich ohne Sensor, außer Haus –, kann keine Messung einen Eintrag widerlegen; dort
  hielte ihn nur die Aufenthaltsdauer, mit ihren schweren Ausläufern (3.1: 2 % der Aufenthalte dauern
  über eine Stunde; 3.3) über Stunden. Bei r < 1 kostet ein Ort ohne Sicht, was sein Aufenthalt sagt
  (die Odds von r fallen mit der Überlebensfunktion, wenn das Herauskommen nicht gesehen wird); bei
  r ≈ 1 kostet er nichts, denn Bayes kann r = 1 nicht senken. Solche Einträge entstehen aber aus den
  Näherungen des Filters (Zusammenlegen zur Multi-Bernoulli-Verteilung 5.6, Abschneiden 5.1) und aus
  Fehlern der Messmodelle (Mehrwegeechos, LD2410C), nicht aus Personen, die durch eine Tür kamen.
  Deshalb läuft der Teil eines Eintrags ab, den kein Sensor prüfen kann:
  `p_S(x) = e^(−Δt/τ · (1 − s(x)))`, s(x) wie gut der beste *lebende* Sensor eine Person dort sieht
  (`Tiling.observed`: die Sicht des LD2450, 4.1, oder die des LD2410C, Strahl × Sichtlinie bis zu
  seinem letzten Ring, 4.3; je über die Kachel gemittelt), 0 in den offenen Bereichen ohne Sensor und außer
  Haus. **Geschlossene Bereiche** (keine Tür nach draußen, ihre Türen führen nur in den beobachteten
  Bereich, 2: der Balkon) laufen nicht ab (seit 9.10.): Wer dort ist, kommt nur durch eine dieser Türen
  zurück (Vorgabe 1.3), gehend in Sicht; ein Eintrag dort wird geprüft, indem niemand herauskommt (der
  Aufenthalt, 3.3, mit der Nicht-Erfassung an der Tür), nicht durch einen Ablauf. Bis 0.25 lief er auch dort
  ab: Der Gast, der 8.10. nach 15 min vom Balkon zurückkam, war „niemand“, und seine Spur bekam die Person
  auf dem Sofa (9, 10 „Kandidat 0.26“).
  r fällt um r × diesen Anteil × (1 − e^(−Δt/τ)); in voller Sicht ist p_S = 1. Das ist eine Annahme über
  Einträge ohne Beleg, kein Teil der Bewegung: die Existenz-Kette von D_MusickiEvans2005 (Markov-Kette 1,
  Gl. 6, p₁₁ < 1), aber nur, wo die Erkennbarkeit (Kette 2, hier κ, 4.1) nichts ausrichten kann (Review
  1.3: „p_S(x), das ehrlich als Annahme über Records ohne Beleg dasteht“). Vorgabe 1.3 gilt damit für
  Personen in Sicht; wer hinter einer Tür, in einem toten Winkel oder außer Haus ist, wird nach Minuten
  vergessen und kommt, wenn er herauskommt, als neue Person (5 s Bestätigung genügen, 1; die Zahl
  hinter Türen zählt nur für das Herauskommen). Über eine Datenlücke (Neustart, 5.3) zählen die
  Sensoren, die da sind (sie hätten geprüft, nur die App sah nicht hin); ein Sensor, dessen Frames
  ausbleiben (4.4), prüft nichts. τ = 2 min wie der Median der Aufenthalte in Bereichen ohne Sensor
  (3.3, angenommen); wo es überall galt, war 2 min die kürzeste Zeit ohne fälschlich an (10, „Tote
  Winkel und Existenz“). Tote Winkel der Wohnung (config10, s < 0,05 auf 1,9 m²): die Vorratsecke der
  Küche hinter der Wand bei y = 7,7 m (x 3,6–4,7, y 7,9–9,2) und ein Streifen von 0,4 m an der Südwand
  des Schlafzimmers (x −2,3 bis −1,0); s < 0,5 auf 3,1 m². **Bis 0.22** lief die Existenz überall ab, auch in
  voller Sicht (`record_life`, `e^(−t/2 min)`); in Sicht kostete das die zweite Person, die der LD2450
  nicht trennt, und Sitzende in langen Lücken (10).
- **Zurückgeben:** Eine bekannte Person ohne Spur, die zu weniger als 1 % existiert (r < 0,01), geht
  mit r × ihrer Dichte in die Intensität (Bernoulli → Poisson), wo sie auch ist. Wer das Haus verlassen
  hat, dessen Eintrag läuft draußen ab wie überall, wo kein Sensor prüft (oben), und geht erst dann
  zurück. Bis 7.10. ging zurück, wer zu
  weniger als 1 % existierte *und im Haus war* (r × P(im Haus) < 0,01): Wer hinausging, kam sofort mit
  seinem ganzen r in die Intensität außer Haus, kehrte von dort mit 1/(4 h) zurück und wurde erst nach
  einem Tag vergessen, während alle anderen ohne Stütze in Minuten verblassen (10, „Wer hinausging,
  blieb erwartet“). Das
  ändert nur P(mehrere davon kommen zurück), um höchstens 0,01²/2. Sonst würde jeder Gast für immer
  verfolgt. (Die Literatur verwirft Bernoulli-Teile unter 10⁻⁵, B_GarciaFernandez2018 Abschn. VII,
  oder r < 10⁻³, B_Reuter2017 S. 166; hier geht keine Masse verloren.) Die Intensität ist eine für alle
  Hypothesen wie im PMBM (B_GarciaFernandez2018 Gl. 7–10): Was die Hypothesen zurückgeben, kommt nach
  ihrem Gewicht hinzu. Vorher trug jede Hypothese ihre eigene Kopie; mit den vielen kleinen Bernoullis
  (r von wenigen %, aus „vielleicht eine neue Person“ jeder Spur, solange beim Start ohne Wissen
  Intensität in Sicht ist) kostete das Rechenzeit (10). 5 % statt 1 %: 14 % weniger Rechenzeit, aber
  Licht fälschlich aus 1,67 statt 1,17 von 15 (Arbeitszimmer 21:36 knapp unter der Schwelle),
  Log-Evidenz −49.

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
  nicht zusammengelegt (9: „Bekannte Personen sammelten sich an“). Ein Neustart speichert dadurch
  eine Hypothese statt einer je Zahl (5.3).

## 6. Ausgaben
- **Je beobachtetem Raum:** Verteilung der Personenzahl (5.1); an Home Assistant gehen die
  wahrscheinlichste Zahl und P(belegt) = 1 − P(0) (Attribut `probability`, in 5-%-Schritten).
- **Belegt (Licht):** `P(belegt) > c` mit `c = K_an / (K_an + K_dunkel)`, der Schwelle mit den
  kleinsten erwarteten Kosten (Bayes-Entscheidung; I_GneitingRaftery2007 S. 364–365, Satz 3).
  `light_cost` = K_an / K_dunkel = 2 (angenommen: Licht ohne Person ist der schlimmste Fehler), also
  c = 2/3. Das verzögerte Ausschalten bleibt in Home Assistant (bzw. Node-RED): Die App schaltet keine
  Lichter, sie liefert je Raum *besetzt*, *wird betreten* (unten) und die Wahrscheinlichkeiten dazu.
  **Oder der LD2450 des Raums misst jemanden** (`Tracker.occupancy`, `roomseen.py`): Ein beobachteter
  Raum ist auch belegt, solange der LD2450 dieses Raums in den letzten `T_seen` = `seen_hold` = 10 s ein
  *gemessenes* Ziel im Raum hatte. Gemessen heißt, was der Filter selbst als Messung nimmt (4.1): nicht
  gehalten (eingefroren oder fortgeschrieben), nicht hinter einer Wand oder außerhalb aller Räume, nicht am
  Sensor (0,3 m); im Raum heißt, der erste beobachtete Raum, dessen Umriss den Punkt enthält, ist dieser.
  Der Sensor des Raums ist der, dessen Name die ID des Raums ist (`kueche` für die Küche), sonst der
  Anzeigename des Raums, sonst der Raum, in dem er hängt (0,3 m um den Umriss, nur wenn eindeutig).
  - *Warum:* Hatte der Sensor des Raums in den letzten 10 s ein gemessenes Ziel im Raum, war der Raum in
    99,2 % der bewerteten Sekunden belegt (9721 zu 74), auch in den Sekunden, in denen der Filter „frei“
    sagte, in 94 % (1046 zu 65). Das liegt weit über c = 2/3: Der Posterior des Filters ist dort nicht
    kalibriert, die Entscheidung mit der gemessenen bedingten Wahrscheinlichkeit ist die
    Bayes-Entscheidung. Auf den vier Wahrheiten (6.10., 7.10. vormittags, 7.10. und 8.10. abends; Licht
    mit 2 min Nachlauf wie in Node-RED, ohne die Nacht): Filter allein 4,3 min Licht im leeren Raum /
    25,6 min dunkel mit Person, Filter oder LD2450 des Raums 5,7 / 0,6 min (`report_eval`: fälschlich an
    0,01 → 1,51 von 108, fälschlich aus 4,82 → 2,53 von 39), so in der App nachgespielt 6,3 / 0,6 min
    (10); die leere Nacht 6./7.10. 0 min wie zuvor. Bei gleichen Kosten beider Fehler (Leon 8.10.)
    rund 7 statt 29,9 Fehlerminuten. Der Filter verliert
    Personen, die sein eigener LD2450 weiter misst (9), die Regel ist die Untergrenze dafür (10, „Filter
    oder LD2450 des Raums“).
  - *Nur der Sensor des Raums:* Andere Sensoren messen Personen an Raumgrenzen aus 6–7 m im Nachbarraum
    (8.10. die Person am Esstisch im Flur: „jeder Sensor“ 21,6 statt 5,7 min Licht im leeren Raum).
    *Nur gemessene Ziele:* Der LD2450 hält ein eingefrorenes Ziel bis 35 s, nachdem die Person gegangen
    ist (mit gehaltenen 10,9 statt 5,7 min).
  - *Nur Ausgabe:* Die Regel ändert keinen Zustand, kein Gewicht und nichts, was der Filter lernt.
    `probability` bleibt P(belegt) des Filters. Die Personenzahl des Raums ist mindestens 1, solange
    die Regel ihn belegt, die des Hauses dann ebenso (sonst wäre *besetzt* ohne Person); *bewegt / ruhig*,
    *wird betreten* und *Ziel* bleiben, wie sie sind. Das Attribut `quelle` an *besetzt* sagt, was
    entschied: `filter`, `ld2450` oder `beide`. Räume ohne eigenen LD2450 und Bereiche ohne Sensor:
    unverändert. `seen_hold` = 0 schaltet die Regel ab.
  - *Sensor des Raums stumm:* Hat jeder eingeschaltete, platzierte Sensor des Raums (wie oben bestimmt)
    seit mehr als `LOST` = 6 s keinen Frame gesendet (der Filter beendet dann seine Spuren, 4.4,
    `Tracker._end_silent`), sind alle Entitäten des Raums in Home Assistant *nicht verfügbar* statt
    belegt oder frei (`roomseen.RoomSeen.silent`; eigenes Verfügbarkeits-Topic des Raums neben dem der
    App, `ha.py`). Ohne Daten sagt die Ausgabe „weiß ich nicht“; Node-RED schaltet dann nichts. Ein
    Sensor, der seit dem Start des Modells nichts gesendet hat, zählt ab diesem Start. Nur Ausgabe: Der
    Filter rechnet unverändert weiter, auch sein P(belegt) dieses Raums (9.10. 15:04 fielen die Boards
    von Wohn- und Esszimmer aus; der Raum war 1,5 h „frei“ und das Licht ging aus, während jemand auf
    dem Sofa saß). Räume ohne eigenen Sensor, Bereiche und das Haus: unverändert.
- **Bereiche ohne Sensor:** P(jemand dort); belegt wie oben, wenn der Bereich nur ein Raum ist.
- **Außer Haus** (auch das Treppenhaus): keine Zahl, kein Zustand, keine Entität in Home Assistant und
  in der Anzeige. Die Zahl im Haus (`presence_haus_count`) zählt alles außer *außer Haus*. Entitäten, die
  der Broker von früher hält (etwa die alten der Treppe), löscht die App beim Verbinden (`ha.py`: leere
  Konfiguration und leerer Zustand, beibehalten).
- **Bewegt / ruhig:** aus den Personen der wahrscheinlichsten Hypothese (geht-Gewicht > 0,5 und
  > 0,15 m/s).
- **Wird betreten** (`Tracker.entering`, je beobachtetem Raum und Bereich): `p_enter` = P(jemand, der
  gerade mit Spur geht, ist binnen der Vorausschau T = `lead_time` darin). Je Person mit Spur (5.2)
  rückt nur ihre Komponente *geht* vor (Stehende schwanken um Zentimeter, wer aufsteht, geht irgendwohin):
  die OU-Näherung aus 3.2 in einem Schritt auf T/2 und auf T, Wände wie in 5.2 über die Sigma-Punkte der
  Bewegung (Türlücken sind keine Wand), die Masse je Raum wie für die Ausgaben (Punkte hinter einer Wand,
  vom Mittel aus gesehen, zählen nicht). Wer vorher anhält (Rate s/ℓ, 3.2), bleibt, wo er anhielt; die
  Zeit des Anhaltens auf den drei Knoten 0, T/2, T. Der Anstieg max(P(dort bei T/2), P(dort bei T)) −
  P(dort jetzt), mindestens 0, ist die Wahrscheinlichkeit, dass sie hineinkommt (eine untere Schranke für
  „irgendwann darin“; wer drin ist, bleibt). Gegeben eine Hypothese sind die Personen unabhängig:
  `p_enter` = Σ_h w_h (1 − Π (1 − Anstieg)), über alle Hypothesen (5.1). Nichts gezogen, ein Horizont,
  nur für Personen, die zu mindestens 2 % gehen. Dazu für die Person, die am wahrscheinlichsten
  hereinkommt, Weg und Zeit bis in den Raum (gerade weiter mit ihrem Tempo, nicht durch Wände) und ihre
  Nummer in der Anzeige. Räume ohne Sensor: keine Vorhersage. **An**, wenn `p_enter > c_v` mit
  `c_v = K_v / (K_v + K_d)` (Bayes-Entscheidung wie oben): K_v ein Einschalten auf Verdacht, nach dem
  niemand hereinkommt (mit der Regel in Home Assistant/Node-RED: 30 s Licht im leeren Raum), K_d ein
  Eintritt ins Dunkle; `approach_cost` = K_v / K_d = 0,05 (Leons Versuch „1 s / 1 m vorher“, 7.10.),
  also c_v = 0,048. Mit den linearen Kosten von `light_cost` (30 s Licht ohne Person gegen ~1 s Dunkel
  mit Person) lohnte sich keine Vorhersage (c_v = 60/61); ein dunkler Eintritt zählt deshalb als eigenes
  Ereignis. Gemessen (10, „Vorausschauend einschalten“): mit T = 2 s und c_v = 0,048 brennt das Licht vor
  60 % der Eintritte in dunkle Räume mindestens 1 s, vor 62 % mindestens 1 m vorher (heute 2 %), für
  4,0 vergebliche 30-s-Lichter je Stunde (Tagesmittel; ohne Wohnzimmer 2,2). `p_enter` ist vorsichtig:
  Von den Momenten mit `p_enter` 0,05–0,1 vor einem leeren Raum kam in 28 % binnen T + 1 s jemand herein,
  bei 0,1–0,2 in 45 %, bei 0,3–0,5 in 76 %; das OU-Modell vergisst die Richtung nach 1,2 s, vor Türen
  geht man gerader (9). Die Schwelle aus den Kosten wirkt also auf eine zu kleine Wahrscheinlichkeit:
  c_v = 0,048 entspricht einer gemessenen Trefferquote um 0,25.
  Bis 0.15.0 war „wird betreten“ ein Flag aus den Personen der wahrscheinlichsten Hypothese: ihr Mittel
  1 s geradeaus (ab 0,3 m/s), durch Wände hindurch.
- **Ziel** (`Tracker.targets`, `destination.py`, je beobachtetem Raum): P(ein Gehender geht als Nächstes
  in diesen Raum), ohne Horizont. Zwei Quellen:
  - *Die Bewegung:* `p_enter` von *wird betreten* (oben), unverändert.
  - *Die gelernte Karte, wohin Gänge gingen* (eine ortsabhängige Karte der Raumnutzung aus Spuren,
    J_Luber2014 Kap. 6, Abschn. 6.5; Übergänge aus Daten gelernt, G_Liao2003). Je Zelle (0,3 m, an den
    Räumen geteilt wie die Kacheln), Richtung (16) und Tempo (0,3–0,7 / 0,7–1,1 / ab 1,1 m/s) zählt sie
    die Gänge je Ausgang: der Raum hinter der ersten Tür, ein Bereich ohne Sensor, außer Haus, oder
    „bleibt“ (der Gang endet ohne Tür, 2 s Stehen). Jeder Schritt eines Gangs zählt mit einem Kern
    (0,3 m, 0,3 m/s, wie in der Untersuchung) auf den Zellen um ihn, nur im eigenen Raum und nicht durch
    Wände, so normiert, dass ein Gang durch die Mitte einer Zelle in ihrer Geschwindigkeit dort 1 zählt.
    Gefragt wird sie über die Zellen um den Ort (0,15 m) und linear zwischen Richtungen und Tempi.
  - *Ein Raumteiler ist keine Tür:* Räume, die nur ein Raumteiler trennt, sind für die Gänge ein Raum
    (dort pendelt man, ZIEL-Untersuchung 3.1; Wohn- und Esszimmer sind eine Lichtgruppe). Ein Gang über
    den Raumteiler geht weiter, sein Ausgang ist die nächste Tür; die Karte sagt dort für den Raum
    jenseits des Teilers 0.
  - *Gelernt in der App aus den eigenen Gehenden* (wie die Geisterkarte 4.2): je Hypothese und Person mit
    Spur, gewichtet mit dem Gewicht der Hypothese und P(geht) (weiche Zählungen über alle behaltenen
    Hypothesen, 10 „Geisterkarte“). Weich sind nur die Schritte: Der Ausgang eines Gangs (wo die Person
    nach der Tür ist) kommt aus der schwersten Hypothese, die sie hält (`destination.py`, `observe`). Eine Person ist ihre früheste laufende Spur; endet sie, übernimmt den
    Gang, wer binnen 1,5 s höchstens 1 m daneben auftaucht (Übergabe an einer Tür). Der Ausgang: der Raum
    oder Bereich, in dem die Person zwei Takte nach einer Tür ist, oder „bleibt“ nach 2 s mit P(geht) <
    ½. Ein Gang, dessen Spuren enden, ohne dass jemand ihn übernimmt, zählt nicht. Vergessen mit 14 Tagen.
    Gespeichert mit dem Übrigen Gelernten (`destinations.json`, Fehlermeldungen). Andere Räume, Türen oder
    Bereiche: die Karte beginnt leer; ein neu kalibrierter Sensor ändert nichts an ihr.
  - *Nur Ausgabe:* Die Karte liest die Hypothesen, sie ändert keinen Zustand und kein Gewicht des Filters.
    Darum kann sie keinen Fehler des Verfolgens verstärken (10: aus den eigenen Urteilen lernt das Modell
    seine Fehler; hier lernt nur die Ausgabe).

  **Die Mischung ist ein Dirichlet-Posterior mit der Bewegung als Prior.** Je Gehendem k mit
  den Gängen n_k an seiner Stelle und Geschwindigkeit, davon der Anteil f_k(R) in den Raum R:
  `P_k(R) = (n_k f_k(R) + α q(R)) / (n_k + α)` mit dem Gewicht α des Priors in Gängen
  (`dest_prior_walks` = 1, wie der Prior der Geisterkarte einen Geist je Zelle wiegt: ein Gang allein
  macht keine Stelle zum Ziel, wenige gleiche tun es). Für den Raum, über die Gehenden und Hypothesen:

  `P(R) = (1 − λ) q_move(R) + λ q_map(R)`, `λ = n / (n + α)`,

  - `q_map(R) = Σ_h w_h (1 − Π_k (1 − P(k geht) f_k(R)))`, wie `p_enter` über alle Hypothesen;
  - n = die Gänge hinter den Gehenden, die als Nächstes nach R gehen könnten (durch eine Tür; über einen
    Raumteiler mit f = 0), gemittelt mit P(geht) und dem Gewicht ihrer Hypothese;
  - `q_move(R)` = `p_enter` auf der Skala der Schwelle des Raums: seine Odds mal odds(c_R) / odds(c_v)
    (eine Verschiebung der log-Odds, Platt-Skalierung mit Steigung 1). So ist `q_move ≥ c_R` genau
    dort, wo `p_enter > c_v`: Ohne Gelerntes ist *Ziel* **genau** *wird betreten* (Test; im Code
    für λ = 0 dieselbe Abfrage `p_enter > c_v`, also bitgleich). Das ist keine Trefferquote (`p_enter`
    ist zu klein, oben), sondern dieselbe Entscheidung auf der Skala, auf der die Karte kalibriert ist.

  **An**, wenn `P(R) ≥ c_R` mit `c_R = K_Fehl / (K_Fehl + K_spät)` je Raum (Bayes-Entscheidung wie für
  *besetzt*; K_Fehl ein Licht auf Verdacht ohne Person, K_spät ein Eintritt ins Dunkle).
  `target_threshold` = 0,8 (K_Fehl = 4 K_spät), je Raum anders (`target_thresholds`). Anders als bei
  `approach_cost` wirkt die Schwelle hier auf eine kalibrierte Wahrscheinlichkeit (die Karte: von den
  Momenten mit 0,9–0,95 traten 89 % ein, ZIEL-Untersuchung 3.4), also auf das, was die Kosten meinen.
  Warum nicht dieselben Kosten wie `approach_cost` (0,05, also c = 0,048)? Bei `p_enter` entspricht
  0,048 einer gemessenen Trefferquote um 0,25; ein Licht, das bei 25 % auf Verdacht angeht, wäre mit der
  Karte, die über den ganzen Weg rechnet, 19 Fehl-Ein je Stunde (ZIEL 3.2, P ≥ 0,3). 0,8 liegt dort,
  wo Fehl-Ein selten sind (1,2 je Stunde in der Untersuchung) und Leons Vorgabe gilt, Licht ohne Person
  sei der schlimmste Fehler (1.1, `light_cost` = 2 meint c = 2/3). Gemessen in 10 („Ziel“): mit 0,8
  halb so viele Fehl-Ein wie *wird betreten*, aber seltener früh; mit 0,5 in beidem besser. Die Wahl ist
  eine der Kosten, also Leons (je Raum einstellbar). Wo die Karte nichts weiß, gilt die Schwelle nicht:
  dort entscheidet `p_enter > c_v`.

  Attribute: die Wahrscheinlichkeit, aus welchem Raum der Gehende kommt, der am wahrscheinlichsten
  hingeht, sein Abstand zur nächsten Tür dorthin (Luftlinie) und die Zeit bei seinem Tempo, seine
  Nummer, die Gänge dahinter (n), das Gewicht der Karte (λ) und welcher Teil entschied (`karte` ab λ ≥
  ½, sonst `bewegung`). Bereiche und Räume ohne Sensor: kein Ziel. Die Karte lernt in der App nur aus
  dem, was die App live sieht; nach einem Update beginnt sie leer.
- **An Home Assistant** (`ha.py`, `ZoneState.to_dict`): Jede Änderung von Zustand oder Attributen einer
  Entität ist eine Zeile im Recorder von Home Assistant. Deshalb: Wahrscheinlichkeiten in 5-%-Schritten,
  Zeiten in 0,5 s, Wege in 0,5 m; die Zustände (*besetzt*, Zahl, *Bewegung*, *wird betreten*, *Ziel*)
  gehen in dem Takt hinaus, in dem sie wechseln; die Attribute einer Entität (`Discovery.steady`) neu mit
  jedem Wechsel ihres Zustands, sonst nur, wenn eines um mindestens zwei Stufen wanderte (eine Stufe ist
  Rundungsflackern) und die letzte Änderung mindestens 10 s (Personenzahl: 60 s) her ist. *Wird betreten*
  und *Ziel* aus: feste Attribute (Wahrscheinlichkeit 0, der Rest leer). Nur die Ausgabe an Home
  Assistant; die Anzeige der App bekommt die gerundeten Werte ungehalten.
- Anzeige: Personen der wahrscheinlichsten Hypothese, die eher existieren als nicht (r ≥ 0,5, 5.5),
  ihre Dichten (r × Dichte) als Wärmekarten.

## 7. Evidenz
Die Summe der Normierungen ist die Log-Evidenz der Aufnahmen (`Tracker.loglik`; Summe der
prequentiellen Log-Scores, I_GneitingRaftery2007 S. 372): je Schritt die Vorhersagedichte der neuen
Daten unter dem Posterior, den der Filter davor behalten hat. Was beim Abschneiden (5.1) wegfällt,
zählt in seinem Schritt mit (die Masse aller Kinder ist die Vorhersage); `Tracker.loglik_cut` weist
aus, wie viel davon auf weggefallene Hypothesen kommt (`report_eval.py`: „dropped hypotheses“). Weil
der Filter deterministisch ist, brauchen Vergleiche keine Seeds (I_Kantas2015 S. 8–9). **Glatt in den
Parametern ist sie nicht** (bis 8.10. stand das hier): Das Abschneiden, die Obergrenze der Hypothesen,
die Paarung beim Zusammenlegen (5.6, ein argmin), die Rückgabe bei r < 0,01 (5.5) und die Grenze,
ab der eine Kachel im Blick des LD2410C liegt (`ld2410.FLOOR`), springen; deshalb hängt sie nicht monoton von den
Grenzen ab (5.1), und Gradienten oder EM-Schritte über sie sind unzuverlässig. Vergleichbar ist sie
außerdem nur bei derselben Vorverarbeitung (welche Messungen verworfen werden, hängt von Kalibrierung
und Grundriss ab) und derselben Temperierung der LD2410C-Terme (4.3: Potenzen von Dichten, keine
Dichten). Gedacht ist sie für grobe Vergleiche von Parametern und Varianten auf Aufnahmen ohne
Wahrheit, bei mehr als einer Einstellung der Grenzen (5.1). Bisher ist damit nur die Lebensdauer der
Geister geschätzt (4.2).

## 8. Prüfung
- **Wahrheitsdaten:** am 6.10.2026 gelöscht, die Bewertung wird neu aufgebaut. Maßstab sind die
  Lichtfehler aus 1.1 je Raum (keine einzelne Prozentzahl).
- **Fehlermeldungen** (App, *Fehler melden*): 15 Minuten Sensordaten, die Anzeige der App, der Start
  des Modells und das Gelernte von diesem Start (sonst vom Moment der Meldung). Das Nachspiel ab dem
  Start glaubt genau, was die App glaubte; mit dem Gelernten vom Moment der Meldung lag es am 6.10.
  um bis zu 1,0 daneben. Lag der Start früher, hat ein Nachspiel ohne Wissen nach 15 Minuten vergessen,
  was es nicht wusste (22:40, 22:42: |ΔP| < 0,01 am gemeldeten Moment, anfangs bis 0,11).
- **Hintergrundaktivität** (10, „Lernen pausieren“): Fenster, die in eine Pause reichen (Schalter in den
  Aufnahmen, für die Tage davor die Zeiten des Saugroboters, `--pauses`), werden nicht bewertet; die
  Nachspiele lernen dort nichts, wie die App.
- **Tests** (`tracker/tests`): bitgleiche Läufe, Unabhängigkeit von der Zeitzerlegung,
  Bewegungsstatistik, Szenen aus `sim.py`, Invarianten der Buchführung (`Tracker.check`).
- **Geplant:** simulationsbasierte Kalibrierung (0_Talts2018 Alg. 1): Welten aus diesem Modell
  erzeugen; der Rang der wahren Zahl unter der Filterverteilung muss gleichverteilt sein.
- Werkzeuge: `tools/record.py` (Aufnahme), `tools/replay.py` (Aufnahmen durch den Tracker),
  `tools/ghostmap.py` (Geisterkarte offline), `tools/calibrate_offline.py` (Kalibrierung aus
  Aufnahmen mit dem Modell der App, `calibration.py`, dazu Block-Bootstrap, Spiegel- und Maßstabsprofile
  und freie Positionen; 10).

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
sieht (die Vorratsecke der Küche hinter der Wand bei y = 7,7 m, x 3,6–4,7), hält eine Person nur noch so
lange, bis ihr Eintrag abläuft (5.5, 2 min); wer dort wirklich länger steht, wird ebenso vergessen.
Schwach Gesehenes (Sicht 0,1–0,5, etwa der Rand der Vorratsecke bei x 3,4) prüfen die Messungen nur
langsam; dort läuft der Eintrag mit dem ungesehenen Anteil ab. Ob ein schwacher, aber beständiger
Überschuss der LD2410C-Energie eine Person dort hält (7.10. 11:22–11:37: r 0,09 → 0,99 ohne Ablauf), hängt
an der Kalibrierung seiner Likelihood (4.3, Review 1.8).

Offen: **Der Filter verliert Personen, die sein eigener LD2450 weiter misst**, und findet sie nicht wieder
(10, „Filter oder LD2450 des Raums“). 8.10. abends war er 38,9 von 64 belegten Minuten „frei“, der LD2450
des Raums nur 2,4 min (Bad: sitzt an der Wand, 98 % „frei“); 7.10. 08:59–09:49 im Arbeitszimmer 50 min
P = 0,00 bei einer gemessenen Spur am Schreibtisch, 8.10. 00:29–06:14 im Schlafzimmer 5,8 h bei Spuren am
Bett bis 45 min. Vermuteter Mechanismus: Die Spur wird bei ihrer Geburt in jeder überlebenden Hypothese
ein Geist (neue Person nur bei harter Evidenz, danach H = 1–2), und mit nur einer Hypothese normiert sich
jede Evidenz gegen „Geist“ weg (e^−71 für 46 min Geist). Bis das im Filter (5.1, Abschneiden, Geister)
behoben ist, hält die Ausgaberegel *besetzt* (6) die Lichter an; die Personenzahl, *wird betreten* und
*Ziel* hängen weiter an den verlorenen Personen.

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
- Höchstens 12 Hypothesen; Paarung beim Zusammenlegen bis 6 Personen (darüber in der Reihenfolge,
  „niemand“ am Ende). Jede neue Spur kann auch „unbekannt“ sein:
  Solange Spuren laufen, gibt es mehr Hypothesen (Simulation, zwei kommen herein: etwa doppelte
  Rechenzeit gegenüber fester Personenzahl).
- Wer aus dem beobachteten Bereich durch eine Tür nach draußen geht, die nicht in einen gezeichneten
  Raum führt (Tür in einer Außenwand ohne Raum dahinter) oder durch eine Eingangszone, verlässt das Haus
  nicht: Das Gehen über die Kacheln braucht Zellen hinter der Tür. Hereinkommen geht dort. Abhilfe:
  den Raum dahinter zeichnen und *Eingang* setzen.
- ν, das Vergessen und `start_unknown` sind angenommen.
- **Bekannte Personen sammelten sich an** (bis 7.10., behoben, 10): Nachgespielt ab 6.10. 19:28 mit den
  App-Starts bis 7.10. 13:30 (e0e26e8 → jetzt), im Haus erwartet je volle Stunde: 22 Uhr 3,9 → 2,1
  (wahr 2); 9 Uhr 4,7 → 2,9 (die zweite Person ging um 9:07); 10–13 Uhr 3,1–3,6 → 1,07–1,15 (wahr
  1); Küche 12/13 Uhr 0,81 / 0,77 → 0,02 / 0,01 (mit 30 min Abklingen). Der Preis des Ablaufs
  (5.5): Wer in einem Raum ohne Sensor schläft, ist nach wenigen Minuten nicht mehr bekannt; morgens
  kommt er als neue Person heraus. Die Zahl hinter Türen zählt nur fürs Herauskommen (Leon), das Licht
  der beobachteten Räume ist davon nicht berührt (report_eval, `phantom.py`). Seit Bad und Schlafzimmer
  Sensoren haben (config10), schlafen beide in Sicht; dort läuft seit 8.10. nichts mehr ab (Zahlen im
  Haus gegen die Telefone: 10, „Tote Winkel und Existenz“).
- **Ein Sitzplatz, dessen Spuren der Filter für Geister hält:** Die zweite Person saß am 6.10. von
  etwa 19:25 bis 22:05 im Arbeitszimmer bei (−2,3; 8,6) (aus den Daten: LD2450-Ziel dort, Energie des
  LD2410C im Ring 3,75–4,5 m im Median 29–33 statt 16–19, wenn Leon allein am Schreibtisch saß; allein
  20:51–20:55, 21:08–21:11, 21:36–22:00, 22:03–22:06). Von ihren 20 Spuren dort hielt der Filter 19
  für Geister (P > 0,5), mit der Prior-Rate überall statt der Karte 15 (bis 21:20 alle). Das kommt
  nicht von der Karte und nicht vom Abschneiden: Bei der Geburt ist sie unbekannt (P(Geist)
  0,81–0,9999), und danach sprechen die Frames des Arbeitszimmer-Sensors gegen eine Person dort (bis
  zu 7 log je Frame, die großen Sprünge mit denen der LD2410C-Energie im Ring 3–3,75 m; die erste
  Spur mit 466 s Leben endete bei P(Person) ≈ e⁻³⁷, obwohl ihr Leben allein 12 log für eine Person
  gab). Die Geisterkarte verstärkte das bis 0.12.0 (10): Die Stelle wurde 21-mal die Prior-Rate, und
  die Person war in den Fenstern, in denen sie allein dort saß (33 min), zu 0 im Arbeitszimmer; jetzt
  1,9-mal, 21:37–22:00 zu 0,97, 20:51 0,55, 21:08 und 22:03 weiter 0.
  Das Fenster 21:36 der Meldungen („aus“ 1,50 statt 1,17 von 15 mit 30 min Abklingen) hing nicht an
  der Karte (mit der Prior-Rate überall ebenso 0,46), sondern daran, dass ihre bekannte Person
  verblasste, bevor die neue Spur dort begann; seit 8.10. läuft in Sicht nichts ab (5.5), und es ist
  richtig (0,47 → 1,00; aus 1,50 → 1,00 von 15). Offen: das Messmodell des LD2410C an diesem Platz
  (4.3); solange der Filter ihre Spuren für Geister hält, zählt die Karte sie auch. Der Prior bremst
  das nur: Säße sie jeden Abend so, stünde die Stelle im Gleichgewicht mit 14 Tagen Vergessen bei
  etwa 7-mal der Prior-Rate (mit dem alten Prior etwa 10-mal; nach einem Abend 2,8- statt 17,6-mal).
- **Doppelte Personen bleiben möglich** (10, Fälle 2 und 5): Bekommt eine Spur die „falsche“ bekannte
  Person (eine überzählige, die näher an der Tür ist, als die wirkliche ungesehen hätte gehen können),
  ist dieselbe Person zweimal da. In Sicht widerlegen die Messungen die überzählige; wo kein Sensor
  hinsieht, läuft sie in Minuten ab (5.5); sie entsteht aber weiter. Eine Hypothese „diese beiden sind
  dieselbe“ kennt das Modell nicht.
- **Zwei in einer Spur** (10, „Zwei an einer Stelle“): Stehen zwei so dicht, dass der LD2450 nur ein
  Ziel meldet, stützt keine Messung die zweite (das LD2410C trennt zwei nicht von einer mit größerer
  Amplitude); bis 0.22 klang ihre Existenz deshalb ab, und zwei wurden nach Minuten eine. Seit 8.10.
  läuft in Sicht nichts ab (5.5): Zwei im Bett 7.10. 22:20–22:26 P(=2) 1,00 / 0,94 / 1,00 statt
  1,00 / 0,32 / 0,87 (10, „Tote Winkel und Existenz“). Wer beim Betreten eines Raums ohne eigene Spur
  bleibt, wird weiter eher hinter einer Tür vermutet (7.10. Bad: Licht richtig, Zahl 1 statt 2).
- Der Start ohne Wissen kostet Rechenzeit: Solange Intensität in Sicht ist, hat jede neue Spur eine
  kleine Alternative „neue Person“, und daraus werden nach dem Zusammenlegen viele Bernoullis mit
  r von wenigen %, je eine Dichte (latency.py 21 Uhr: 30 s statt 18,5 s mit einer bekannten Person
  „irgendwo“ statt der Intensität; 5 % statt 1 % beim Zurückgeben 26 s, 5.5).
- Eine neue Kalibrierung (Richtung, Maßstab) lässt weiterhin die Geisterkarte aller Sensoren und den
  LD2410C-Hintergrund dieses Sensors neu beginnen, obwohl sich der Sensor selbst nicht bewegt hat (die
  App kann Drehen und Kalibrieren nicht unterscheiden). Für die Meldung 7.10. 08:07 war das nicht die
  Ursache (Hintergrund im Nachspiel unverändert).
- **Wen der Filter verliert, lernt der LD2410C-Hintergrund** (10, „Lernschleifen“), soweit der LD2450 im
  Gehäuse ihn nicht sieht und keine Echoquelle seine Energie hält. Die Schlafenden im Schlafzimmer
  7./8.10. verlor der Filter um 00:25 an eine Echoquelle, die bis zum Morgen an blieb: Eine ungesehene
  Person zählt im Blick nur mit P(keine Quelle), die Quelle selbst wird aber gegen sie gewogen, als
  wäre sie da (4.3; oben und 10 „Energie aufteilen“). Ihre Spuren im Bett endeten als Geister und zählen in
  die Geisterkarte (das Bett 3,5-mal die Prior-Rate nach einer Nacht).

- **Kalibrierung (10, „Kalibrierung der App neu“):** Beim Flur widersprechen sich Grundriss (36–41°)
  und Paare (50° mit Ess- und Wohnzimmer durch die Tür, 29° beim Gang im Flur selbst); frei gefittet
  liegt seine Position in 6 von 7 2-h-Fenstern 0,4–0,8 m weiter nördlich an der Badwand (y 3,05–3,49
  statt 2,67). Nachmessen. Die Küche hat keine verlässlichen eigenen Paare (ihre Echos jenseits der
  Wand laufen mit den Personen mit). Der Wohnzimmer-Maßstab ist auf 8 m (durch die Tür in den Flur) 1,07,
  auf 2–6 m 1,13: ein Entfernungsversatz statt eines Maßstabs ist ungeprüft. Ein Faktor auf
  sin(Azimut) je Sensor (Phasen-Monopuls) machte die Fenster nicht einheitlicher.

- **„Wird betreten“ kennt nur das allgemeine Gehen** (6): Die OU-Näherung vergisst die Richtung mit
  τ = 1,2 s, das Mittel eines Gehenden läuft höchstens v·τ ≈ 1,2 m weit. Vor Türen geht man gerader
  (Autokorrelation der Geschwindigkeit in den letzten 3 s vor einem Durchgang 0,94 / 0,78 / 0,52 nach
  0,4 / 1 / 2 s, über alle Wege 0,64 / 0,46 / 0,17). Folgen: `p_enter` ist zu klein (6), und wer in
  weniger als etwa 1,3 m an einer Tür vorbeigeht, erreicht in der Simulation `p_enter` 0,05–0,16 (bei
  c_v = 0,048: Verdacht), wer geradewegs auf sie zugeht 0,3–0,4. Der Raumteiler Wohn-/Esszimmer ist
  keine Wand: Dort entsteht fast die Hälfte der vergeblichen Verdachte, unabhängig von der Schwelle
  (10). Abhilfe wäre ein zielgerichtetes Gehen (je Tür eine Komponente mit Zug zur Tür; J_Luber2014
  Abschn. 6.5, G_Liao2003), erst wenn diese Stufe nicht reicht.

- **Der Versatz einer Spur bleibt, solange sie lebt** (Meldung 7.10. 18:31: „Der LD2450 ortet mich
  immer leicht oberhalb meiner Position. Müsste sich meine Position nicht annähern?“). Mit einem Sensor
  sind Ort x und Versatz c der Spur nicht zu trennen (`z = x + c + o + w`); was c ist, legt der Moment
  fest, in dem die Spur beginnt, danach ändert keine Messung mehr die Aufteilung. Nachgespielt (config10):
  18:58:17 begann die Arbeitszimmer-Spur auf Leon in der Tür, 0,5 m neben der Flur-Spur derselben
  Person; c = (0,32; 0,26) m. Die Spur lebte 50 min (der LD2450 hält einen Sitzenden), und die ganze
  Zeit lag die Messung im Mittel (0,28; 0,24) m neben der Schätzung, nach oben rechts. 18:30–18:41
  (Spur begonnen ohne zweiten Sensor) nur (−0,04; +0,01). Die 57 % konstanter Versatz sind an Spuren um
  60 s gemessen; über einen Weg von der Tür zum Schreibtisch ist der Fehler zweier Sensoren nicht
  derselbe (Kalibrierung und Winkel hängen vom Ort ab). Abhilfe wäre ein Versatz, der mit dem Weg oder
  der Zeit dekorreliert (Gauß-Markov mit gemessener Länge); zu messen an Paaren langer Spuren.
- **Kalibrierung des Schlafzimmers** (Meldungen 7.10. abends): offline 47,9° ± 2,8° / 1,04 ± 0,04 statt
  50,2° / 1,02 (aus der alten App-Rechnung), aber Grundriss allein 44°, Paare allein 51°; frei
  gefittet wandert die Lage 0,8 m an der Wand entlang. Lage des Sensors und der Wände nachmessen.
- **Sitzende verliert der Filter weiter, wo der LD2450 minutenlang keine gemessene Spur hat** (10,
  „Meldungen 8.10. abends“; nach den zwei Korrekturen dort):
  1. *Personen ohne Spur haben beim LD2410C die Amplitude g = 1* (4.3). Wer an einem Platz weniger
     zurückwirft (Bad, Sitz an der Westwand: g ≈ 0,3, 7.10. und 8.10.), wird in Sekunden widerlegt, sobald
     seine Spur endet: 8.10. 18:51 und 18:59 im Bad r 0,76 → 0,39 → 0,05 in 10 s bei Energien von 6–10×
     dem Hintergrund (Lücken ohne gemessene Spur 140 s und 122 s). Die Amplitude der letzten Spur mit der
     Person weiterzutragen (Posterior des Gitters, mit der Erkennbarkeit neu gezogen; der geparkte Zweig
     `algo-ld2410` trug ihr Mittel) hielt das Bad (0,70 → 0,99), kostete aber anderswo (bis 6.10. aus 1,00 →
     2,00 von 15; 7.10. früh an 0,39 → 0,50 von 13; Test: Geister ohne Wissen 5,6 s Licht): Ein Mittelfeld
     (Ort und g unabhängig) gibt die Amplitude des alten Aufenthalts auch einem neuen, und eine aus einer
     Geisterspur gelernte kleine Amplitude macht eine erfundene Person schwer widerlegbar. Nicht übernommen.
     *g je Aufenthalt auf den Kacheln* (9.10. gebaut und verworfen, 10 „Kandidat 0.26“): dasselbe Gitter wie
     bei Personen mit Spur, je Kachel bedingt auf „steht dort“, neu bei jedem neuen Aufenthalt und mit κ;
     Rechenzeit +9 %. Das Bad 8.10. bleibt ganz (Filter allein 328 s → 0 s aus), verloren in Sicht 133 → 76
     min (Schlafzimmer 61 → 7). Aber eine Person ohne Spur mit kleinem g ist jetzt dieselbe Erklärung für eine
     beständige Energie wie eine Echoquelle, und die Lebensdauer spricht dann für die Person: Das Schlafzimmer
     war 7.10. abends stundenlang besetzt (P > 2/3 ohne eigenen LD2450 690 statt 18 min in den 26 h bis 8.10. 19:10; Licht im leeren Raum
     auf den vier Wahrheiten 8,8 → 17,1 min). Offen: was eine wenig zurückwerfende Person von einer
     Echoquelle trennt (wie sie kam: durch eine Tür; Echoquellen nicht), ohne die Amplitude zu verlieren.
  2. *Wer aus einem Bereich ohne Sensor zurückkommt, nachdem sein Eintrag dort abgelaufen ist* (5.5, 2 min),
     hat keinen Eintrag mehr; seine neue Spur bekommt die Person, die ungesehen am nächsten sitzt. 8.10.
     19:04:19: Ein Gast kam nach etwa 15 min vom Balkon zurück an den Esstisch, die Spur bekam die Person auf
     dem Sofa (die Alternative „neue Person“ hat im Blick die Intensität 0,002), das Wohnzimmer war 19:04–19:09
     zu 0,02–0,16 belegt, und die weiteren Spuren auf dem Sofa (19:05:06, 19:06:52) wurden Geister. Für
     geschlossene Bereiche (eine Tür, nur in den beobachteten Bereich) ist das Ablaufen keine gute Annahme:
     Wer dort ist, kommt durch diese Tür zurück. **Erledigt** (9.10., 5.5): Dort läuft nichts mehr ab; Sofa
     und Esstisch 8.10. dunkel 0,4 → 0,1 min (10, „Kandidat 0.26“).
  3. *Was die Sensoren des Esszimmers durch die Balkontür sehen* (7.10. 22:19–22:25, 10 „Kandidat 0.26“): Das
     LD2410C des Esszimmers zeigt ruhende Energien von 30–67 (Hintergrund etwa 7), der LD2450 ein Ziel an der
     Ostwand neben der Balkontür (3,6; 4,2), danach eines auf dem Balkon (4,7; 2,2), durch die Türöffnung. Beide
     Bewohner waren im Schlafzimmer. Der Filter macht daraus eine Person an der Balkontür (P bis 0,99), die
     „vom Balkon kam“ (dort 0,013 erwartete Unbekannte: der Start ohne Wissen um 17:13 und zurückgegebene
     Bernoullis, keine Geburt). Das LD2410C sieht nach dem Modell nicht durch Wände und Türen (4.3), und eine
     beständige Energie dort erklärt eine Person besser als eine Echoquelle (Lebensdauer 20 s). Ob der Fall
     kommt, hängt an der Vorgeschichte (gleiche Kette mit g je Aufenthalt auf den Kacheln: 9 s statt 3 min,
     dazu der Balkon ohne Ablauf: 4,4 min). Offen; wohl nur mit einem Modell der Sicht durch Türen (Glas) in
     geschlossene Bereiche zu lösen.

Nicht geprüft (Ablationen ausstehend): λ_d = 0,85 gegen langsamere Richtungswechsel; OU-Näherung
gegen weißes Rauschen in der Beschleunigung; Swerling-I gegen logistisch; Form der Erkennbarkeit.
(Geisterkarte gegen globale Rate: 10.)

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
- **Rechenzeit: zwei weitere Kernel, Schleife ohne Stillstände (8.10., Performance-Review von
  0.18.0)**. In `Stats._mu_part` steckte der größte Teil nicht im Logarithmus, sondern in
  `log_censored` (`np.unique`, Masken, `np.interp`: 242 von 278 µs bei 500 Zeilen). Jetzt interpoliert
  `kernels.ld_censored` die zensierten Zellen auf dem gleichmäßigen Gitter genau wie `np.interp` und
  nimmt den Logarithmus von numpy; die Summe über die Zellen bleibt ein numpy-Produkt.
  `Tiling.gauss_points` rechnet die Exponenten über die 2410 Stützpunkte als Schleife
  (`kernels.gauss_exponents`), `np.exp` bleibt bei numpy: Numbas skalares `exp` weicht bei etwa 5 %
  der Werte im letzten Bit ab, und schon solche Rundungsunterschiede schaukelten sich im Review über
  Stunden zu Evidenz ±6 auf. So sind die Ergebnisse bitgleich: `report_eval --trace` auf den Meldungen
  bis 6.10. (config5, 15 815 Zeilen) und vom 7.10. früh (config7, 4765 Zeilen) zeichengleich mit
  9c0f294, Log-Evidenz je Lauf gleich, Licht gleich (an 0 von 41 / aus 1,50 von 15, an 0 von 13 / aus 0
  von 7), leere Nacht 0 min. CPU (App-Harness des Reviews: die echte `App` mit rohen MQTT-Frames, Takt,
  HA-Zuständen, Live-Ansicht und Speichern; `config10`, vollste Stunde 7.10. 17 Uhr mit 107 210 Frames,
  ein Kern, je dreimal abwechselnd, Mediane): 0.19.0 215,1 s, mit dem Amplitudengitter (4.3) 218,1 s,
  mit den Kerneln 197,4 s (−9,5 %, 6,1 → 5,5 % eines Kerns; Takt 58,0 → 44,4 s, Frames 155,1 →
  148,4 s). Ruhige Stunde (3 Uhr) 8,7 s vorher wie nachher.
  Stillstände der Schleife, die Frames, Takt und Home Assistant warten lassen (gemessen mit einem
  Herzschlag von 1 ms, ein Tag Kalibrierdaten mit 119 491 Zeilen, 15 min Meldepuffer mit 24 398
  Nachrichten, hier; auf Home Assistant etwa ×8): Fehlerbericht 522 → 5–10 ms (Zeilen und gzip jetzt im
  Thread, Stufe 6 statt 9: 523 → 248 ms bis zur Datei, 0,95 statt 0,86 MB); Kalibrierstatus der
  Live-Ansicht (alle 30 s, solange ein Browser offen ist) 24 → 5 ms (gezählt im Thread an einer
  Kopie); Speichern alle 10 min 14 → 5 ms (`calibration.npz` unkomprimiert: 6,0 statt 1,5 MB, 4 statt
  80 ms; die Zielkarte wird in der Loop kopiert und im Thread komprimiert). Was bleibt, sind der
  Personenzustand (4 ms, er wird aus dem Modell gebaut) und bis zu 5 ms, die ein rechnender Thread den
  GIL hält (`sys.getswitchinterval`).
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
- **Kalibrierung der App neu** (7.10. abends, `calibration.py`): Leon ging 16:13–16:22 allein kreuz
  und quer (neuer Badsensor, Küche nicht betreten). Die alte App-Rechnung (Paare je Sensorpaar mit
  RANSAC, gemeinsame kleinste Quadrate, Spiegel frei) schlug vor: Arbeitszimmer gespiegelt (294°),
  Esszimmer Maßstab 1,27, Flur −15°. Ursachen, nachgerechnet auf denselben Frames:
  1. *Spiegelsuche:* Der alphabetisch erste Sensor (Arbeitszimmer) war immer ungespiegelt, die
     Alternative „alle umgedreht“ ist nur bei zwei Sensoren eine Symmetrie. Die eingebaute Lage (alle
     gespiegelt) hätte nach dem eigenen Maß die meisten passenden Paare gehabt (1463 gegen 1361),
     wurde aber nie gerechnet.
  2. *Paare in einem Fleck:* 6 der 7 Sensorpaare hatten ihre Paare im Flur zwischen den Türen um
     (−1,3 / 4,2), quer 0,15–0,23 m Streuung, das Wohnzimmer 8 m entfernt durch die Tür. Ein Fleck legt
     je Sensorpaar nur eine Kombination fest; drinnen entscheidet er den Spiegel kaum (passende Paare
     richtig gegen gespiegelt 3–30 % auseinander).
  3. *Falsche Paare:* bei den gezeichneten Lagen lagen nur 45–70 % der Bad-Paare innerhalb 1 m.
  4. *Maßstab frei, Kette:* Das Esszimmer hing nur über 251 Paare an einer Stelle am Wohnzimmer, dessen
     Maßstab der Fleck auf 8 m setzte (1,07 statt 1,15) – Esszimmer 1,27.
  5. *Frames als unabhängig:* Paare alle 0,1 s; die Unsicherheit wurde nicht ausgewiesen.
  Neu (eine Rechnung für App und `tools/calibrate_offline.py`): alle Sensoren gemeinsam (Bündelausgleich,
  Triggs et al. 2000); Paare mit Ausreißeranteil (Myronenko & Song 2010); Übergänge zwischen
  Sichtfeldern wie Rahimi, Dunagan & Darrell 2004, mit dem OU-Gehmodell aus 3.2 über bis zu 2 s;
  Grundriss je LD2450-Spur (Person im sichtbaren freien Raum oder Echo); Maßstabsprior mit der
  gemessenen Streuung der LD2450 (ln-Maßstab 0,05) um den Median der Sensoren mit eigenen Paaren;
  Spiegel aus der Konfiguration (nur auf Wunsch verglichen); Paare und Grundrisspunkte je 1 s.
  Unsicherheit: cluster-robuste Kovarianz über 30-s-Blöcke (Liang & Zeger 1986) plus Modellfehler.
  Vorgeschlagen wird ein Wert nur, wenn er bestimmt ist (Richtung ≤ 4°, Maßstab ≤ 0,05: so viel wie
  der LD2450 selbst in 5 m), die Richtung keinen zweiten Modus hat und Grundriss allein und Paare
  allein dieselbe Richtung wollen. Die App sammelt laufend die Messungen Gehender der letzten 24 h.
  Gemessen:
  - *Modellfehler:* 2-h-Fenster vom 6./7.10. streuen über ihre eigene Unsicherheit hinaus um
    0,9–3,3° (Flur 3,3) und 0,017–0,073 im Maßstab (Esszimmer 0,073); quadratisch gemittelt 2,1° und
    0,042, das steht jetzt in jeder Unsicherheit. Die Streuung zwischen den Fenstern ist 2–4-mal so
    groß wie die robuste Kovarianz, der Block-Bootstrap (120 s) innerhalb eines Laufs 1–2-mal.
  - *Spiegel:* auf Leons Gang ist die eingebaute Lage für jeden Sensor besser (bereinigt um die
    Korrelation, Log-Posterior: Arbeitszimmer +29, Esszimmer +54, Wohnzimmer +52, Bad +45, Flur +250).
  - *Nur Leons Gang gegen alle Daten:* Arbeitszimmer 324,2° / 1,11 gegen 324,4° / 1,05, Bad 50,8° /
    1,05 gegen 55,1° / 0,99, aber Esszimmer 326,8° / 1,20 gegen 319,6° / 1,07 und Flur 33,6° gegen
    (Widerspruch, bleibt 50°). Der Gang allein verbessert die Wände (Schritte durch Wände auf dem
    Gang 39 → 9) und verschlechtert den Alltag: Paare innerhalb 0,5 m 54 % → 24 %, Schritte durch
    Wände 232 → 121 von 646. Alle Daten: 54 % → 56 %, Wände 232 → 214, Punkte außer Sicht 3,4 → 2,8 %,
    Übergänge innerhalb 1 m 36 → 38 %. Daher sammelt die App jetzt laufend.
  - *report_eval* (Meldungen 7.10., config7-Lagen): alle Varianten Licht falsch an 0 von 13, aus 0
    von 7 (alte App-Rechnung aus 0,01); P im Fenster der Meldungen 08:45 (zwei im Arbeitszimmer):
    Bad 0,12 (config7) / 0,09 (alle Daten) / 0,24 (nur Gang) / 0,70 (alt); Küche 11:39 0,08 / 0,10 /
    0,32 / 0,00.
  - *Log-Evidenz* (mit dem Jacobi-Term 2 ln k je Positionsdichte, damit Maßstäbe vergleichbar sind;
    gegen config7/config8 auf denselben Frames): alle Daten 7.10. 07:44–12:55 +1945, 6.10. 19–22 Uhr
    −1000, Stunde des Gangs +159 (zusammen +1104); nur Gang −5785 / −4002 / +1063 (auf seinen eigenen
    Daten); alt −3913 / −27728 / +886. „Nur Gang“ und „alt“ verwenden dabei 3200–4000
    Flur-Messungen weniger (mit 34–35° liegen sie hinter Wänden), „alt“ abends 9700
    Arbeitszimmer-Messungen weniger (gespiegelt). Die Änderungen mit allen Daten sind klein (außer dem
    neuen Bad, 45° → 55°) und liegen bis auf das Esszimmer (−4,1° ± 2,1°) innerhalb ihrer
    Unsicherheit; die Evidenz entscheidet zwischen ihnen und config8 nicht.
  Offen: Flur-Position, Küche, Entfernungsversatz (9).
- **Ein Gang ist kein Alltag** (7.10. abends, Test der App 0.13.0 mit config8 und der Aufnahme 16:00–16:40):
  Vorgeschlagen wurden Flur −17° (33,6° ± 2,7°) und Esszimmer +3–5°, beide „bestimmt“. Beim Esszimmer
  lagen danach mehr Gehende außer Sicht (32 → 37 %, 65 Punkte aus 12 Spuren); der Flur mit 34° legt im
  Alltag 3200–4000 seiner Messungen hinter Wände (report_eval, oben). Warum die Prüfungen hielten: Der
  Gang ist in sich stimmig. Ein Jackknife über seine Minuten gibt für den Flur ± 1,6° (Esszimmer ± 4,1°),
  Grundriss allein und Paare allein liegen beide bei 30–36°; der Widerspruch zu den Paaren durch die
  Wohnzimmertür (50°) steckt nur in Alltagsdaten. Der Modellfehler (2,1°) war an 2-h-Fenstern gemessen:
  kurze Alltagsfenster (10 min, 1–10 Spuren) weichen vom Gesamtfit im Mittel nur 3–4° ab, der Gang um
  15°: Er ist eine ungewöhnliche Auswahl von Orten (fast nur der Fleck im Flur zwischen den Türen), keine
  Stichprobe des Alltags. Keine Statistik innerhalb des Gangs kann das zeigen. Deshalb jetzt (`solve`):
  Ein Wert wird nur vorgeschlagen, wenn der Sensor Gehende aus mindestens 3 verschiedenen Stunden hat;
  seine Unsicherheit enthält ein Jackknife, bei dem je eine von bis zu 8 Gruppen aufeinanderfolgender
  Stunden wegfällt (Künsch 1989); und nicht, wenn die neue Lage Punkt für Punkt mehr Gehende außer Sicht
  legt (gepaart: mehr hinaus als herein um mehr als die Wurzel ihrer Summe). Die gesammelten Daten
  überstehen jetzt einen Neustart. 16:00–16:40 allein: nichts vorgeschlagen, auch das Bad nicht (sein
  Ergebnis hängt am Flur: 50,8° mit dem Flur bei 34°, 55,1° mit den Alltagsdaten, Grundriss allein 54,3°).
  Alle Daten (6.10. 18:00 – 7.10. 16:39): Esszimmer, Wohnzimmer, Arbeitszimmer wie zuvor (Jackknife
  0,3–0,8° und 0,004–0,020), Flur Widerspruch, Küche mehr außer Sicht (19 → 20 %), Bad erst eine Stunde.
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
  Raten, Spiegeln in der Vorhersage, Masse hinter Wänden und Wiederfinden. Mehr Punkte für die Raten
  (7.10., Stand 0.11.0): statt der 5 Punkte die Gauß-Hermite-Produktregel mit 3×3 = 9 (Grad 5 je
  Achse, mit gemischten Termen) oder 5×5 = 25 Punkten (Grad 9). Licht in allen Fenstern gleich
  (Meldungen bis 6.10.: an 0 von 41, aus 1,17 von 15; 7.10.: 0 von 13 / 0 von 7, Küche und Flur
  unverändert); Log-Evidenz bis 6.10. −42 / −11, 7.10. +9 / +0,5 (im Rauschen der Hypothesen-Grenze,
  5.1); Rechenzeit +1–2 %. Der Fehler der Näherung ist nicht, was fehlt: 5 Punkte bleiben.
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
- **Woher erfundene Personen kamen und warum sie blieben** (7.10., Leons Vorgabe in 1; nachgespielt
  ab 6.10. 19:28 mit den App-Starts, `diag.py` im Arbeitsordner: je neue Spur die Anteile Geist /
  Person mit Spur / bekannte ohne Spur / neue, je Abschneiden das verworfene Gewicht). Neue Personen
  aus der Intensität waren selten (über den ganzen Tag zusammen 0,95 neue Personen, davon 0,86 beim
  Start um 19:28); die überzähligen bekannten Personen entstanden anders:
  1. *Das Ende einer Geisterspur sprach für eine Person* (4.2, Ende der Spur): bis 26-mal. Im Test
     „ohne Wissen niemand in Sicht“ (sim mit Herzschlag-Ausdünnung) wurde dadurch eine Geisterspur bei
     52,8 s zur Hälfte eine Person (P(Geist) am Ende 0,49 statt 0,80), 2,7 s Licht.
  2. *Harte Schattenkanten* (4.1, Sicht einer Person): 6.10. 20:55:41 begann der Arbeitszimmer-Sensor
     eine Spur auf Leon im Flur bei (−2,01; 4,50), am Rand des Türschattens; an allen Sigma-Punkten
     seiner Vorhersage (y ≤ 4,45) war die Sicht 0, „das ist Leon“ hatte das Gewicht 0. Es blieben
     Geist (0,96) und „neue Person“ (0,0005); der Geist starb, als die Spur durchs Zimmer ging, und Leon
     war zweimal da – einmal am Schreibtisch, einmal ohne Spur an der Badtür (21:00 im Haus 2,66 statt 2).
  3. *Unsichtbare Streifen*: Die Küche hatte entlang ihrer Wand (x 2,2–2,4) Kacheln ohne jede Sicht,
     auch für das LD2410C. Die Person der Live-App vom 7.10. (seit 10:57) stand genau dort (2,29; 7,70);
     im Nachspiel 7.10. 07:10 Küche 0,76 durch eine Person an dieser Stelle.
  4. *Abschneiden statt Zusammenlegen* (5.1, 5.6): „Die Spur war ein Geist“ und „sie war eine neue
     Person“ blieben zwei Hypothesen mit verschiedener Personenzahl; fiel die erste aus den 12, gab es
     keinen Weg zurück. Nachgespielt (e0e26e8) bekannte Personen 4 ab 22:00, 5 ab 8:00, 6 um 13:00;
     im Haus erwartet 3,9 um 22:00 und 4,7 um 9:00 statt 2 (Telefone: beide zu Hause bis 9:07, danach
     eine Person).
  5. *Doppelte Personen in Ecken ohne Sicht*: Eine überzählige Person (aus dem Zustand von 9:28
     übernommen) übernahm um 10:58–11:21 Leons Weg durch die Küche, Leon behielt seine; als er ging,
     blieb die doppelte in der Vorratsecke der Küche (x 3,4–4,6, y 7,7–9,2, Sicht 0 für jeden LD2450 und
     das LD2410C, auch über den Versatz gemittelt). Sie hatte r = 1 (ihre Spur), keine Messung konnte
     sie treffen, und die Aufenthaltsdauer (3.1) ließ sie nur langsam aufstehen: Meldungen 11:39–12:55
     Küche 0,94 / 0,80 / 0,71 (bacc9ce). Weder das LD2410C (Sicht dort 0) noch der Neustart um 9:28 als
     solcher hielten sie, nur dass sie existierte.
  Geändert: 1. Ende einer Geisterspur als konkurrierende Risiken (4.2); 2./3. Sicht einer Person über
  ihren Versatz (4.1); 4. Bernoulli-Existenz der bekannten Personen ohne Spur und Zusammenlegen über
  die Personenzahl hinweg (5.5, 5.6); 5. Existenz klingt ohne Stütze ab (5.5, zuerst 30 min, nach
  Messung der Lücken echter Sitzender 2 min). Nicht geändert:
  die Zuordnung, die eine Person verdoppelt (2, 5: eine Spur auf der „falschen“ bekannten Person), und
  der Start ohne Wissen. Zahlen in 5.5 und im CHANGELOG.
- **Die Geisterkarte lernte einen Sitzplatz als Geisterquelle** (bis 0.12.0; nachgespielt ab 6.10.
  19:28 mit den App-Starts, je endende Spur P(Geist) bei der Geburt und am Ende, was die Karte zählte,
  `p_spy.py` im Arbeitsordner). Zwei Schleifen, beide aus dem eigenen Urteil des Filters (wie das
  Verhaltenslernen in 0.6.0, unten):
  1. *Ort:* Der Prior wog 4 h Beobachtung, bei 1,8·10⁻⁵ /m²/s 0,04 Geister je Zelle; eine ganz
     gezählte Spur (davon 0,28 in ihrer Zelle) machte die Stelle schon 5–8-mal so wahrscheinlich.
     Die Korrektur nach J_Park2020 (der Faktor der Karte bei der Geburt herausgerechnet) half nicht,
     weil P(Geist) am Ende meist genau 1 war (15 von 20 Spuren am Platz): die Hypothesen mit der
     Person waren unter 10⁻⁷ gefallen. Am Platz (−2,3; 8,6) 1,1-mal die Prior-Rate um 19:37, 4,2 um
     20:44, 9,1 um 20:51, 12,7 um 21:48, 21 um 22:09; die Spuren dort wurden bei der Geburt mit bis zu
     33-mal der Prior-Rate gewogen.
  2. *Lebensdauer:* Die Arten der Geister wurden mit derselben Gewichtung online weitergelernt. Die
     erste Spur am Platz (466 s, P(Geist) 1) hob die lange Art von 39 auf 72 s, bis 20:51 auf 148 s
     (am Morgen 101 s). Damit sprach ein langes Leben kaum noch gegen einen Geist – das Merkmal, an
     dem Sitzende von Geistern zu trennen sind. Auch Spuren am Schreibtisch (bis 744 s) zählten als
     Geister.
  Nicht die Ursache des Fensters 21:36: Mit der Prior-Rate überall (Karte nicht gelernt) hielt der
  Filter 15 der 20 Spuren am Platz ebenso für Geister (9). Geändert: Prior ein Geist je Zelle (Form
  α = 1 wie J_Luber2014 Gl. 6.13), Lebensdauern fest (4.2). Nicht übernommen: P(Geist) für die Karte
  aus den Odds bei der Geburt (alle Alternativen, ohne die Karte) mal dem Überleben der Geister über
  das Leben der Spur, das kein Abschneiden verliert – am Platz zählte das 11,9 statt 15,7 der 20
  Spuren (die Odds bei der Geburt sind schon 10³–10⁴), in den Sitzfenstern keine Änderung (3,78 von 4
  „aus“ wie ohne). Nachgespielt (report_eval, 12 Hypothesen; Meldungen bis 6.10. / 7.10.; dazu die
  vier Fenster aus den Daten, in denen sie allein am Platz saß, 33 min):

  | Karte | bis 6.10.: an / aus, Evidenz | 7.10.: an / aus, Evidenz | allein am Platz: aus von 4 | leere Nacht |
  |---|---|---|---|---|
  | 0.12.0 (0,04 Geister, Leben gelernt) | 0 / 1,68, −1121539 | 0 / 0, −449321 | 4,00 | 0 min |
  | Leben fest | 0 / 1,50, −1121619 | | 3,78 | |
  | **Leben fest, ein Geist je Zelle** | **0 / 1,50, −1121544** | **0 / 0, −449405** | **2,64** | **0 min** |
  | nicht gelernt (Prior-Rate überall) | 0 / 1,50, −1149617 | 0 / 0, −458489 | 2,88 | 0 min |

  Küche 7.10. 11:39–12:55 unverändert 0,04 / 0,01 / 0,00, Flur 9:49 0,07 statt 0,05. Die Lichtfehler
  bis 6.10.: Flur 21:35 (1,00, wie 0.11.0) und Arbeitszimmer 21:36 (0,50); weg sind Wohnzimmer 21:22
  (0,17) und Schreibtisch 22:40 (0,02), die die gelernten langen Geister kosteten. Die Karte bleibt
  nützlich: ohne sie Log-Evidenz −28 073 bis 6.10., −9 084 am 7.10.; Lichtfehler und leere Nacht sind
  in diesen Fenstern ohne sie gleich (dass ohne Karte Reflexionen zu Personen werden, oben, zeigt sich
  hier nicht; eine Karte, die nicht lernt, wäre die einfachere Form – die Evidenz spricht dagegen).
  Am Ende des Abends (10,8 h) stehen die stärksten Stellen je Sensor bei 1,2–2,8-mal statt
  2,1–17,6-mal der Prior-Rate (Esszimmer (4,2; 3,0) 1,9 statt 7,3; der Platz 2,8 statt 17,6).
- **Das Treppenhaus war ein Bereich der Wohnung** (bis 0.12.0): ein offener Bereich ohne Sensor mit
  Aufenthalt 30 min / 2,0 und 1/(2 h) nach außer Haus. Wer hinausging, blieb dort minutenlang „im
  Haus“; die Anzeige und Home Assistant bekamen eine Personenzahl für öffentlichen Raum (Leon, 7.10.:
  „Die Personenzahl hinter Treppe macht keinen Sinn, da dies öffentlicher Raum ist.“). Jetzt außer
  Haus (2, 3.3): Licht gleich (bis 6.10. an 0 / aus 1,68; 7.10. 0 / 0), Log-Evidenz −5 bzw. −35
  (davon +1 durch das Verwerfen der Messungen dort, also fast alles aus der Dynamik: kein eigener
  Aufenthalt mehr, zurück mit 1/(4 h) je Weg statt nach einem Aufenthalt im Treppenhaus). Weggehen
  7.10. 8:57 (zweite Person; Telefon „not_home“ 9:06:59): im Haus erwartet 8:57:00 1,10 statt 1,99,
  8:58:31 1,20 statt 1,85; angezeigt 1 statt 2 bis 8:59. Ab 9:01 verblasst die gebliebene Person in
  beiden Fassungen hinter Türen (Bad, mit config7 ohne Sensor; 9, 5.5). Leere Nacht 0 min.
- **Wer hinausging, blieb erwartet** (7.10., nach dem Zusammenführen des Treppenhauses außer Haus mit der
  Geisterkarte 0.14.0): Licht fälschlich an im Flur 09:48:50–09:49:18 (P 0,14, 11 % des Fensters; jede
  Änderung allein 0,05 / 0,07). Ein Geist des Flur-LD2450 bei (−0,15; 4,33), 1,6 m vor der
  Wohnungstür, während Leon am Schreibtisch saß. Die Hypothese „der Geist ist Leon, die Spur am
  Schreibtisch eine neue Person“ hatte 09:48:46–50 0,15–0,79 statt höchstens 0,01 (Geisterkarte allein),
  weil die unbekannten Personen in Sicht 0,014–0,058 statt 0,004–0,006 wogen. Ursache: Zurückgegeben wurde, wer zu weniger als 1 % *im Haus*
  existierte; wer hinausging, also sofort mit seinem ganzen r (09:01:05: r 0,995, die Unbekannten
  draußen 0,27 → 1,25). Draußen verblasst die Intensität nicht (vergessen nach einem Tag), und mit
  1/(4 h) kehrten diese Unbekannten zurück: Zufluss an der Tür etwa 7·10⁻⁵/s, der sich als Unbekannte
  an schlecht sichtbaren Plätzen sammelte. Vorher (Treppe als Bereich) blieb, wer hinausging, eine
  bekannte Person im Haus und verblasste mit 2 min wie jeder (5.5). Jetzt geht zurück, wer zu weniger als
  1 % existiert, wo er auch ist (5.5): bis 6.10. an 0 / aus 1,50, −1121573 (vorher −1121572); 7.10.
  0 / 0, −449450 (vorher 0,11 / 0, −449502); leere Nacht 0 min.
- **Bewegung als Merkmal einer Person?** (7.10., Leons Vorschlag, nicht übernommen.) Leon: Ein Ziel
  des LD2450, das sich bewegt, eine Weile bleibt und neben dem sonst niemand ist, ist ein sicheres
  Zeichen einer neuen Person; das soll beim Urteil Geist oder Person mehr zählen. Im Modell bewegt sich
  die Quelle eines Geists wie eine Person (4.2): Tempo, Weglänge und Richtung sagen nichts; es trennen
  Entstehungsort (Karte, Türen), Lebensdauer und die anderen Sensoren. Die Literaturform wäre ein
  Merkmal mit eigenem Likelihood-Verhältnis je Klasse (merkmalsgestütztes Tracking, Amplitude bei Lerro
  & Bar-Shalom 1993; Klassen mit eigener Bewegung, Bar-Shalom, Kirubarajan & Gokberk 2005) oder eine
  eigene Bewegung der Geisterquellen, beides mit Verteilungen aus den Daten, nicht mit einem Gewicht.
  *Gemessen* (alle Aufnahmen 6.10. 18:00 bis 7.10. 17:58, die Spuren, wie der Filter sie sieht, config8;
  `kin.py` im Arbeitsordner):
  - Leere Nacht 22:56–06:15 (beide im Bett, Schlafzimmer ohne Sensor): in 7,3 h und fünf Sensoren
    eine einzige Spur in einem beobachteten Raum (05:23, der Arbeitszimmer-Sensor durch seine Tür in
    den Flur, 18 s, 1,3 m in den ersten 5 s, Geschwindigkeit springend ±2,5 m/s); insgesamt 18 s mit
    einem gemessenen Ziel.
  - Eine Person zu Hause (7.10. 09:10–16:50, Telefone): 171 Spuren; 144 bestätigt ein zweiter Sensor
    (≥ 50 % ihrer gemeinsamen Zeit näher als 1 m), 2 sind sicher Geister (fern einer bestätigten
    Spur), 25 unbestätigt (meist Küche, deren Sicht sich mit keiner anderen überschneidet). In 110 von
    26 110 Sekunden mit Zielen lagen zwei Ziele mehr als 2 m auseinander (0,4 %; obere Grenze für
    Geister fern der Person, Kalibrierfehler eingeschlossen).
  - Die 144 bestätigten Personenspuren in ihren ersten 5 s: Weg (zwischen den Mitteln je Sekunde)
    Median 0,80 m, ≥ 1 m 46 %, ≥ 1 m und Leben ≥ 5 s 37 %; Leben Median 9,8 s (10–90 % 2–120 s).
  Geister des LD2450 sind hier nach dem Verwerfen hinter Wänden (4.1) so selten, dass sich keine
  Verteilung ihrer Bewegung schätzen lässt (drei Spuren, die eine der Nacht bewegte sich); was es an
  Mehrwegekopien nahe bei Personen gibt, ist von den Versätzen zwischen Sensoren nicht zu trennen. Ein
  Merkmal ohne gemessene Verteilung wäre ein Gewicht von Hand (1.4). *Wie schnell bestätigt der Filter
  jetzt?* Nachgespielt 7.10. 06:50–17:58 wie die App (Neustarts, config8; `judge.py` im Arbeitsordner),
  P(Geist) jeder Spur nach 0, 1, 2, 3, 5, 10 s: Von den 148 Spuren, die in 5 s mindestens 1 m gingen und
  so lange lebten, sind im Median schon bei der Geburt 0,05 und nach 5 s 0,00 Geist; 16–18 % liegen
  nach 5 s über 0,5, fast alle zweite Spuren einer Person, die schon eine hat (sie zählt ohnehin).
  Nur 6 begannen mehr als 1,5 m von jeder anderen Spur; eine davon hielt der Filter länger als 5 s für
  einen Geist: 17:32:14 die Person, die das Bad verließ (2,1 m in 5 s; P(Geist) 1,00 / 0,92 nach 5 s /
  0,51 nach 10 s / 0,02 am Ende nach 25 s), weil die zweite Person im Bad verblasst war (unten, „Zwei
  an einer Stelle“): Ihr fehlte nicht die Bewegung als Merkmal, sondern jede bekannte Person, die dort
  hätte gehen können. *Die andere Hälfte des Vorschlags, „sonst niemand in der Nähe“,* gehört in die
  Entstehungsrate der Geister (4.2): fern von Gehenden nur die Karte λ_s, mit Gehenden im Blick dazu
  λ_e. Wie oft Spuren entstehen, wenn kein LD2450 in den 30 s davor irgendwo im Haus ein Ziel gemessen
  hat (aus den Sensoren bestimmt, ohne Wahrheit; `quiet.py` im Arbeitsordner): 14 Spuren in je 8,2 h
  stiller Zeit der fünf Sensoren, 9 davon näher als 1,5 m an einer Tür (meist Leute, die aus Bad oder
  Schlafzimmer kommen), 2 am Schreibtisch (Leon nach langem ungesehenem Sitzen). Auch alle 14 als
  Geister gezählt sind das höchstens 3,2·10⁻⁶ /m²/s, fern von Türen 1,1·10⁻⁶; das Prior-Mittel der Karte
  ist 1,8·10⁻⁵ (per EM über Tag und Nacht geschätzt, wo λ_e = 3·10⁻⁴ angenommen ist und Echos der Gehenden
  mit in λ_s landen können). Geprüft mit 3,2·10⁻⁶ als Prior-Mittel (λ_e unverändert): Licht auf allen
  Sätzen gleich (bis 6.10. an 0 / aus 1,50; 7.10. an 0,11 / aus 0; Bad-Fenster gleich; leere Nacht 0 min),
  Zahlen gemischt (Arbeitszimmer 6.10. 21:36 0,44 → 0,52, Flur 7.10. 9:49 0,14 → 0,09 besser; Bad ohne
  Sensor 6.10. 21:36 0,96 → 0,71, Schlafzimmer 7.10. 08:52 0,24 → 0,16 schlechter), im Haus je Stunde
  gegen die Telefone gleich (0,22 daneben), die Spur von 17:32:14 nach 5 s weiter 0,90 Geist; Log-Evidenz
  +81 / +2226 / Bad +46 bzw. −6; Rechenzeit in report_eval +21–24 % (unter Last). Die Evidenz spricht für
  eine kleinere Rate fern von Personen, Licht und Zahl ändern sich nicht: nicht übernommen. Offen: λ_s und
  λ_e gemeinsam per EM schätzen (I_Kantas2015, wie die Karte); dann trüge die Rate ohne Gehende, was Leon
  mit „sonst niemand in der Nähe“ meint. Simulation (ein Sensor, Start ohne Person im Raum): Wer
  durch die Tür hereinkommt, ist nach 3–4 s eine Person (P(Geist) der Spur 0,96 → 0,14 nach 3 s → 0,01
  nach 4 s; ohne Wissen 0,78 → 0,04 nach 4 s); wer mitten im Raum auftaucht (ohne Tür), nach 6–7 s.
  Das trägt schon die Lebensdauer der Geister (40 % mit 3 s) und dass Personen nur durch Türen kommen. Die
  Tests in `tests/test_together.py` halten fest: Wer allein hereinkommt, macht 5 s nach dem ersten Frame
  Licht; eine Kopie 0,8 m neben einem Gehenden (6 s, ein Sensor) ist eine Minute später keine zweite
  Person (dazwischen bis 1,5 erwartete Personen im ohnehin besetzten Raum).
- **Zwei an einer Stelle: Wer steht, kann zwei sein?** (7.10., Leons Vorschlag, nicht übernommen.)
  Leon: Zwei Personen standen um 17:22 lange an einer Stelle im Bad, angezeigt wurde nach einer Weile
  nur eine; wer steht, könnte zwei sein, wer geht, kaum (niemand geht dieselbe Route dicht neben einem
  anderen). *Die Episode aus den Daten* (Spuren aller sechs LD2450 in Hauskoordinaten mit config9 und
  die Energien des Bad-LD2410C, je 5–30 s): 17:12:45 geht Person A vom Schreibtisch durch den Flur ins
  Bad (Flur-, dann zwei Bad-Spuren); die zweite Person ist im Ess- und Wohnbereich, steht 17:14:00–17:14:45
  im Flur an der Badtür (Flur-, Wohnzimmer- und Arbeitszimmer-Sensor) und geht 17:14:50 hinein. Von
  17:15:05 bis 17:31:35 misst kein Sensor außerhalb des Bads ein Ziel, der Bad-LD2450 genau eine Spur
  (bei (−4,4…−3,3; 3,4…4,3), 0,05–0,15 m/s), sein LD2410C ruhig Ring 2–4 bei 100 und Ring 5–8 etwa
  doppelt so hoch wie bei einer Person allein im selben Abstand vorher (35–47 / 24–30 / 17–29 statt
  15–22 / 10–15 / 7–9). 17:31:35 geht eine hinaus (Flur → Wohnzimmer → Küche), die andere bleibt bis
  etwa 17:40. Wahrheitsfenster (Arbeitsordner, nicht im Repository): Bad = 2 von 17:15:30 bis 17:31:00,
  Bad = 1 von 17:13:10 bis 17:14:00 und von 17:33 bis 17:39, die übrigen beobachteten Räume dabei leer.
  *Was das Modell tut* (Stand 0f6da68, Start ohne Wissen um 16:00; config8 mit Bad 45° / config9 mit
  55°): Licht in allen Fenstern richtig (Bad besetzt, sonst leer), aber Bad = 2 nur zu 0,06 / 0,01; nach
  17:33 (eine Person) mit config8 zu 0,97 zwei. Ursache ist nicht das Abklingen allein: Als die drei
  Spuren der zweiten Person an der Badtür enden (17:14:48), liegt sie 3 s später zu 0,89 im Schlafzimmer
  (ohne Sensor). Ungesehen im Bad zu gehen kostet die Erfassungsrate Gehender (0,5 /s), ins Schlafzimmer
  zu gehen nichts; die erste Bad-Spur (17:14:51) bekommt sie nur zu 0,30 (Geist 0,31, A 0,39), ihre
  zweite lebt 16 s und endet 0,7 m neben A. Danach stützt nichts eine zweite Person: der LD2450 sieht ein
  Ziel, und die Energien des LD2410C trennen eins und zwei hier nicht. Gerechnet mit der Likelihood aus
  4.3 je Sekunde (Amplitude beider über ihre Stufen gemittelt, Hintergrund des leeren Bads): zwei an
  A's Platz gegen A allein im Fenster mit zweien +0,74 je Sekunde, aber nach 17:33 mit einer Person
  ebenso +0,89 / +1,08, an der Stelle bei der Wand (eine Person) −0,70 / −0,36. Im Filter nimmt die
  Amplitude je Aufenthalt (4.3) den Überschuss. Im Arbeitszimmer trennen sie besser (7.10. 08:43–08:44,
  zwei in einer Spur: +1,26 je Sekunde; Leon allein am Schreibtisch 09:52–10:04: −0,16), dort nahm die
  Echoquelle den Überschuss (oben, „Energie auf mehrere Personen aufteilen?“; P(=2) 0,05).
  *Literaturform* (Koch & van Keuk 1997; Svensson et al. 2012, A_Svensson2012; Beard et al. 2015,
  A_Beard2015): Eine unaufgelöste Messung entsteht aus einer Gruppe; die Likelihood ist eine Summe über
  die Aufteilungen der Ziele in Gruppen (A_Beard2015 Gl. 16–18). Für die zweite Person heißt das: Neben
  einem gemessenen Ziel spricht ihr Fehlen nicht gegen sie (das ist q in 4.1, schon im Modell), aber die
  gemeinsame Messung hebt ihre Existenz auch nicht – sie ist mit und ohne sie gleich wahrscheinlich.
  Was sie dann hält oder nicht, ist allein ihr Überleben. Die Abhängigkeit von der Geschwindigkeit
  braucht keine eigene Annahme: Die zweite Person bewegt sich unabhängig; geht A, verlässt die
  Auflösungszelle sie, und „keine eigene Spur“ spricht wieder gegen sie (Simulation unten: niemand geht
  mit). *Geprüft:* Überleben abhängig vom Ort (p_S(x), B_GarciaFernandez2018 Abschn. II), kein Abklingen
  dort, wo sie von einem gemessenen Ziel nicht zu trennen wäre: `p_S = exp(−Δt/τ · (1 − u(x)))`,
  `u(x) = 1 − Π_k q_k(x)` über die messenden Spuren k (q wie in 4.1). Simulation mit einem Sensor, zwei
  stehen 0,25 m nebeneinander 5 min: P(=2) 0,70 → 0,95; mit zwei Sensoren 0,97 / 0,99; danach geht eine
  hinaus, eine weg: 1,00 / 1,00 (niemand bleibt, niemand folgt); eine Mehrwegekopie neben einem Gehenden
  unverändert. Auf den Aufnahmen (12 Hypothesen): Bad 2 0,01 → 0,01 (config9), 0,06 → 0,06 (config8),
  Arbeitszimmer 08:45 0,05 → 0,05; Licht bis 6.10. an 0 / aus 1,50 wie vorher, Log-Evidenz −7; 7.10.
  an 0,11 / aus 0 wie vorher, −20; Bad-Fenster Licht gleich; leere Nacht 0 min; im Haus erwartet je
  Stunde gegen die Telefone (7.10. 06:50–17:58) gleich (im Mittel 0,22 daneben); Rechenzeit +3–5 %
  (report_eval). Es hilft nicht, weil die zweite Person zu dem Zeitpunkt, an dem sie mit A
  verschmilzt, schon im Schlafzimmer vermutet wird und ihre Masse an A's Platz klein ist; das Überleben
  dort erhält
  nur diesen kleinen Teil. Was fehlt, ist eine Messung, die zwei von einem mit größerer Amplitude
  unterscheidet; ohne sie ist „eine stehende Person kann zwei sein“ genau das Festhalten ohne Beleg, das
  1 ausschließt. Für das Licht ist der Fall harmlos (der Raum bleibt besetzt). Die Tests in
  `tests/test_together.py` halten fest: zwei, die zusammen stehen, bleiben (mit zwei Sensoren) zwei;
  eine Kopie neben einem Gehenden ist eine Minute später keine zweite Person.
- **Vorausschauend einschalten** (7.10., 6: „wird betreten“). Leon: „Ich würde gerne versuchen, ob es
  mit 1 s / 1 m Vorlauf beim Einschalten und 2 Minuten Nachlauf machbar ist. […] Ich will nicht, dass
  die Präsenz-App direkt Lichter steuert.“ *Wahrheit:* Türdurchgänge aus den eigenen Spuren der LD2450
  (eine Spur durch eine Türöffnung, Übergaben zwischen Sensoren, Spuren in Bereiche ohne Sensor; gegen
  die von Hand geschalteten Deckenlichter 74 % gefunden, im Median 0,45 s vor dem Tastendruck), Aufnahmen
  6.10. 18:00 – 7.10. 18:25 nachgespielt wie in der App (Takt 0,2 s, Konfiguration je Zeitraum), 23,0 h
  bewertet: 418 Eintritte in Räume mit Sensor, 329 davon in einen dunklen (P(belegt) 4–6 s vorher unter
  c). *Vorlauf* = Eintritt minus der Moment, ab dem das Licht bis dahin an war; *Weg* = wo die Person da
  war (ihre Spur). *Vergeblich* = mit der Regel aus DOCS.md (auf Verdacht an, ohne *besetzt* binnen 30 s
  wieder aus; *besetzt* aus → 2 min Nachlauf, ein Verdacht darin hält ≥ 30 s), Verdachte, nach denen der
  Raum nie besetzt war; je Stunde über 23 h gemittelt (also auch die Nacht). Anteil der Eintritte mit
  Licht mindestens so lange / so weit vorher:

  | Signal | ≥ 0,5 s | ≥ 1 s | ≥ 2 s | ≥ 1 m | 1 s und 1 m | Median | vergeblich je h | Licht vergeblich |
  |---|---|---|---|---|---|---|---|---|
  | *besetzt* allein (bis jetzt) | 3 % | 2 % | 2 % | 2 % | 2 % | −0,34 s | 0 | 0 |
  | Flag bis 0.15.0 (1 s) | 44 % | 9 % | 2 % | 10 % | 5 % | 0,47 s | 2,0 | 1,2 min/h |
  | Flag 2 s | 68 % | 46 % | 5 % | 50 % | 42 % | 0,95 s | 4,1 | 2,7 min/h |
  | Flag 2 s, nicht durch Wände | 58 % | 36 % | 4 % | 39 % | 32 % | 0,78 s | 2,5 | 1,6 min/h |
  | `p_enter` T = 1 s, c_v 0,048 | 74 % | 39 % | 6 % | 45 % | 30 % | 0,91 s | 2,9 | 1,9 min/h |
  | T = 1,5 s, c_v 0,048 | 76 % | 57 % | 9 % | 60 % | 50 % | 1,08 s | 3,4 | 2,3 min/h |
  | **T = 2 s, c_v 0,048** | **76 %** | **60 %** | **10 %** | **62 %** | **55 %** | **1,14 s** | **4,0** | **2,7 min/h** |
  | T = 2 s, c_v 0,10 | 72 % | 40 % | 6 % | 46 % | 34 % | 0,93 s | 2,8 | 1,8 min/h |
  | T = 2 s, c_v 0,15 | 65 % | 24 % | 3 % | 29 % | 19 % | 0,76 s | 2,4 | 1,5 min/h |
  | T = 2 s, c_v 0,20 | 58 % | 13 % | 3 % | 17 % | 9 % | 0,60 s | 2,1 | 1,3 min/h |
  | T = 2 s, c_v 0,30 | 32 % | 6 % | 2 % | 6 % | 4 % | 0,32 s | 1,6 | 0,9 min/h |
  | T = 3 s, c_v 0,048 | 78 % | 66 % | 14 % | 64 % | 59 % | 1,29 s | 4,2 | 2,9 min/h |

  Je Raum (T = 2 s, c_v 0,048; Anteil ≥ 1 s, vergeblich je h): Flur 59 % / 0,30, Arbeitszimmer 61 % /
  0,22, Esszimmer 59 % / 0,61, Küche 69 % / 0,70, Wohnzimmer 62 % / 1,79, Bad 3 von 5 / 0,26,
  Schlafzimmer 2 von 4 / 0,09. Ohne Wohnzimmer 60 % / 61 % bei 2,2 vergeblichen je Stunde. Im
  Wohnzimmer bleiben 1,6–1,8 je Stunde bei jeder Schwelle (auch das alte Flag 1,05): Der offene Raumteiler
  zum Esszimmer ist keine Wand; dort entweder Ess- und Wohnzimmer als eine Lichtgruppe oder ohne
  Vorausschau. Was das zeigt:
  - 1 s / 1 m vorher geht für etwa 60 % der Eintritte, mit ~4 vergeblichen 30-s-Lichtern je Stunde im
    Tagesmittel (~1 h Licht im leeren Raum je Tag). Aus Ort und Geschwindigkeit allein zielt man im Median
    erst 1,0 s / 1,4 m vor der Tür auf sie (Tempo an der Tür 1,07 m/s); mehr gäbe nur Wissen über Wege
    und Absichten.
  - **Zeit oder Weg:** Eine Vorausschau als Weg (je Gehendem D / Tempo, D = 1 / 1,5 / 2 m, auch kombiniert
    mit T = 1 s) lag gleichauf mit der Zeit, die ihm im Tempo entspricht (c_v 0,05; D = 1,5 m: 55 % ≥ 1 s,
    61 % ≥ 1 m, 3,8 vergeblich je h; T = 1,5 s: 54 % / 59 %, 3,4; D = 2 m: 61 % / 63 %, 3,9; T = 2 s:
    60 % / 61 %, 3,9): Bei ~1 m/s an der Tür sind 1 s und 1 m dasselbe. Nur die Zeit, ein Parameter
    weniger.
  - **Gegen das alte Flag:** Bei gleich vielen vergeblichen Verdachten wie das Flag mit 2 s (4,1 je h)
    60 % statt 46 % ≥ 1 s; bei der Hälfte (Flag 2 s ohne Wände, 2,5 je h) etwa gleich (`p_enter`
    T = 2 s, c_v 0,10: 40 % statt 36 %). Der Gewinn liegt beim großen Vorlauf. Wände zu beachten halbiert
    beim Flag die kurzen Fehl-Ein (15,5 → 8,1 je h, Zählung der Untersuchung).
  - **Kalibrierung:** `p_enter` ist zu klein (6). Momente vor einem leeren Raum, Anteil mit Eintritt
    oder *besetzt* binnen T + 1 s (T = 2 s): `p_enter` 0,02–0,05 → 21 %, 0,05–0,1 → 28 %, 0,1–0,2 → 45 %,
    0,2–0,3 → 55 %, 0,3–0,5 → 76 %, ab 0,5 → 93 %. Reihenfolge richtig, Höhe nicht: die OU-Näherung (9).
  - *Besetzt* unverändert: report_eval an 0 / aus 1,50 von 41 / 15 (bis 6.10.) und 0 / 0 von 13 / 7
    (7.10.), Log-Evidenz bitgleich zu 93feb02; leere Nacht (`phantom.py`) 0 min.
  - **Rechenzeit** (6.10. 21:00–21:20, config7, ein Kern, je drei Läufe): 26,3 → 27,5 s, 2,19 → 2,29 %
    eines Kerns (+0,1 Prozentpunkte); je Takt mit Ausgaben 0,37 → 0,54 ms. Im Nachspiel über 23 h im
    Mittel 0,13 ms je Aufruf (p99 2,5 ms). Der Prototyp der Untersuchung (5 Horizonte in Schritten von
    0,2 s) brauchte 4,8 ms je Aufruf.

- **Ziel** (7.10., 6: „Ziel“). Leon: „Ist es möglich, zu bestimmen, mit welcher Wahrscheinlichkeit eine
  Person gerade in einen anderen Raum läuft? […] Und es nicht von der Zeit/Strecke abhängig machen.“ Die
  Untersuchung dazu (gelernte Karte gegen Kinematik, zielgerichtete Bewegung und einen Klassifikator,
  nur Ausgabe) fand: Wer 1 m vor einer Tür auf sie zugeht, geht nur in 75–85 % der Fälle hindurch (Küche
  33–48 %, Raumteiler 34–72 %); mehr als 0,8 gibt es vor der Tür nur aus Wissen über Wege, und das nur
  für Routinen (Arbeitszimmer → Flur). Umgesetzt als Karte, die die App aus ihren Gehenden lernt (6).
  *Nachspiel* wie in der App mit `tools/entries.py` (Takt 0,2 s, Konfiguration je Zeitraum, neues Modell
  bei jedem Start der App, Gelerntes weiter, Karte von leer an gelernt, *Vorausschau* 1 s wie in allen
  Konfigurationen und in der App), 6.10. 18:34 – 7.10. 20:40, 25,8 h bewertet, 342 Eintritte in dunkle
  Räume. Wahrheit und Vorlauf wie „Vorausschauend einschalten“; *Fehl-Ein* hier: das Signal an, der Raum
  dunkel, kein Eintritt bis Vorausschau + 3 s nach dem Ende und der Raum nicht besetzt (Mittel 0,8–1,1 s;
  nicht die 30-s-Verdachte von oben). Zeiträume: (a) bis 7.10. 17:09 (kein Schlafzimmersensor, Bad erst ab
  16:15), (b) ab 17:09 (config10, alle Sensoren). Eintritte in Bad und Schlafzimmer, bevor sie einen Sensor
  hatten (nur aus einer an der Tür endenden Spur erschlossen: Bad 7, Schlafzimmer 9, ein Raum ohne Sensor
  7), zählen nicht. Anteil der Eintritte mit Licht ≥ 1 s / ≥ 2 s / ≥ 1 m vorher, Fehl-Ein je Stunde:

  | Signal | ganz | (a) 22,3 h, 255 Eintritte | (b) 3,5 h, 87 Eintritte |
  |---|---|---|---|
  | *wird betreten* | 39 / 6 / 52 %, 15,2 | 41 / 7 / 51 %, 13,9 | 33 / 3 / 56 %, 24,0 |
  | *Ziel* 0,8 | 26 / 3 / 37 %, 8,1 | 28 / 4 / 37 %, 7,1 | 21 / 2 / 39 %, 14,8 |
  | *Ziel* 0,7 | 34 / 4 / 45 %, 9,0 | 35 / 4 / 45 %, 7,9 | 29 / 2 / 46 %, 16,0 |
  | *Ziel* 0,6 | 42 / 5 / 51 %, 10,0 | | |
  | *Ziel* 0,5 | 46 / 8 / 55 %, 11,3 | 49 / 9 / 55 %, 10,2 | 40 / 6 / 55 %, 18,3 |

  - *Ohne Gelerntes gleich:* Nachspiel mit leerer Karte (`--no-learning`): in allen 1 561 880 Raum-Takten
    *Ziel* = *wird betreten*, alle Zahlen gleich. Mit Lernen: in den 98 % der Raum-Takte, in denen die
    Karte für keinen Gehenden etwas weiß (meist geht niemand), ebenfalls nie verschieden.
  - *Lernkurve:* Je Stunde (4–60 Eintritte) war *Ziel* mit 0,8 in keiner Stunde in beidem schlechter als
    *wird betreten*: Fehl-Ein nie mehr (meist halb so viele, schon in der ersten Stunde 20 statt 50 je h),
    Vorlauf ≥ 1 s meist weniger (Stunde 3 mit 58 Eintritten 19 statt 34 %, Stunde 21 62 statt 73 %). Mit 0,5
    ebenfalls nie in beidem schlechter (von 19 Stunden mit Eintritten mehr Vorlauf ≥ 1 s in 11, weniger
    in einer; mehr Fehl-Ein in 2, dort mit gleichem oder mehr Vorlauf). Das Gewicht der Karte, wenn sie gefragt wird, steigt in den ersten Stunden auf Median 0,6–0,8.
    Mit config8 (Bad) und config10 (Schlafzimmer) änderten sich die Räume: Die Karte begann zweimal leer
    (855 Gänge verloren), daher wieder Gewicht 0,2–0,3 am 7.10. ab 16 Uhr.
  - *Je Raum* (ganz, ≥ 1 s, Fehl-Ein je h, 0,8): Arbeitszimmer 18 statt 28 %, 0,6 statt 1,0; Flur 37 statt
    35 %, 1,4 statt 2,1; Esszimmer 22 statt 47 %, 2,5 statt 4,1; Küche 17 statt 42 %, 0,6 statt 2,0;
    Wohnzimmer 30 statt 56 %, 2,3 statt 4,7. Je Tür gewinnt die Routine: Arbeitszimmer → Flur 60 statt
    36 % (in (b) 86 statt 43 %, n = 7). In (b) allein mit 0,5: Flur 52 statt 31 % (n = 29), Esszimmer 50
    statt 45 % (22), Küche 67 statt 17 % (6), Arbeitszimmer 8 statt 33 % (12), Bad 0 / 0 % (7),
    Schlafzimmer 40 / 40 % (5); Fehl-Ein in jedem Raum weniger oder gleich. Dünn.
  - *Kalibrierung:* Momente mit P(Ziel) 0,8–0,9 vor einem dunklen Raum: 53 % mit Eintritt binnen 5 s, ab
    0,9 70 % (die Untersuchung: 77 / 89 %, Karte aus 18 h ohne Neubeginn). Die Mischung mit `q_move`, das
    keine Trefferquote ist (6), und die zweimal neu begonnene Karte drücken das. Darum ist 0,8 hier
    vorsichtiger, als die Kosten meinen; die Wahl der Schwelle bleibt Leons.
  - *Recorder* (`ha.py`, Nachspiel derselben 26 h mit den Vorlagen der Entitäten in Jinja wie in Home
    Assistant, je Takt; alle Zonen): Zeilen je Stunde in der vollsten Stunde (7.10. 17 Uhr) / im Mittel
    der 25 ganzen Stunden, 0.16.0 → jetzt:

    | Entitäten | vollste Stunde | Mittel |
    |---|---|---|
    | Personenzahl (9) | 2 969 → 975 | 741 → 151 |
    | *wird betreten* (9) | 838 → 274 | 274 → 88 |
    | *Ziel* (8, neu) | – → 244 | – → 68 |
    | *besetzt* (9), *Bewegung* (9) | 213, 932 (gleich) | 67, 238 (gleich) |
    | zusammen | 4 952 → 2 638 | 1 320 → 611 |

    Am Tag 31 500 → 14 600 Zeilen. Ohne die Mindestabstände (nur zwei Stufen) wären es 25 600, vor allem
    die Wahrscheinlichkeit am Personenzähler (12 000 statt 3 600). Alle Wechsel von *besetzt*, Zahl,
    *Bewegung* und *wird betreten* (1 734 / 2 807 / 6 219 / 2 269) liegen in beiden Versionen im selben
    Takt. Leons Messung live (0.16.0, ruhige Stunde): *wird betreten* etwa 74, Personenzahl 75–79 Zeilen
    je Stunde und Entität; die App veröffentlicht alle 0,1 s, das Nachspiel nur bei Frames, live sind es
    also eher mehr.
  - *Besetzt* unverändert: report_eval an 0 / aus 1,50 von 41 / 15 und 0 / 0 von 13 / 7, die Ausgabe
    Zeile für Zeile gleich 0.16.0 (Log-Evidenz −1 121 572,7 / −449 450,2); leere Nacht 0 min.
  - *Rechenzeit* (6.10. 21:00–21:20, config7, ein Kern, je drei Läufe, mit dem Bau der Nachrichten an
    Home Assistant): 27,7 → 28,7 s, 2,31 → 2,39 % eines Kerns (+4 %; auf Home Assistant etwa ×8, also
    18,5 → 19,1 %). `Tracker.targets` im Nachspiel über 26 h im Mittel 0,04 ms je Takt (p99 0,3 ms).

- **Meldungen 7.10. abends** (Bad und Schlafzimmer mit eigenem Sensor; config10; Wahrheit aus den
  Texten und den Wegen der Sensoren, Arbeitsordner). Nachgespielt wie die App ab dem Start 17:13 (Starts
  17:48, 18:04, 18:41, 19:38, 21:39). 0.19.0: Licht fälschlich aus in 3,69 von 11 belegten Fenstern, an
  0 von 43. Die zweite Person saß 22:09–22:20 im Bad an der Westwand (Spur des Bad-LD2450 bei (−5,1; 4,2–4,7),
  LD2410C ruhig Ring 2–3 bei 30–100): Bad P = 0,00 in allen drei Fenstern. Zwei Ursachen, beide
  nachgewiesen:
  1. *Hinter der Wand, an der der Sensor hängt* (4.1): Bad- und Schlafzimmersensor hängen in Ecken
     derselben Westwand und blicken an ihr entlang. „0,4 m zurück entlang der Sichtlinie“ lag für ein
     Ziel 5–10 cm jenseits dieser Wand noch hinter ihr: 11 % der ruhigen Ziele des Bad-LD2450 und 20 %
     des Schlafzimmer-LD2450 waren keine Messung (7.10. 18–23 Uhr; Flur 39 %, Küche 39 %). Jetzt quer
     zur Wand gemessen: 2 % / 0,4 % (Flur 16 %, Küche 26 %, dort die Echos hinter der Außenwand). Allein
     diese Änderung: Bad 0,00 → 0,87 / 0,75 / 0,98.
  2. *Amplitude je Aufenthalt als 3 Quadraturpunkte* (4.3): Die Person gab ein Drittel des Profils
     zurück (Gamma-Likelihood je Sekunde, 22:12:40–22:13:05, über 25 s: g = 0,3–0,4 am besten, +95,8
     gegen niemand; g = 0,58 +93,7, 1,22 +78,7). Mit der untersten Stufe 0,58 erklärte eine Echoquelle
     (Stufe 0,3) die Energie besser: Je Sekunde gewann „die Spur ist ein Geist, eine Quelle im Bad“
     +0,3 bis +0,8 log über „die Spur ist sie“ (LD2410C des Bads und die Frames zu gleichen Teilen),
     bis die Person-Hypothese nach 20–60 s unter 10⁻⁷ fiel. Mit dem Gitter in log g (4.3): Bad 0,94 /
     0,92 / 0,98.
  Geprüft und **nicht** die Ursache: der gelernte Hintergrund (Bad ruhig 4,7–4,9 die ganze Zeit, das
  Schlafzimmer 7,5–8,3 bei gemessen 7–8 im leeren Raum: er nahm keine Person auf); die Kalibrierung
  (Bad offline 53,7° ± 2,4° / 1,011 ± 0,045 statt 55,06° / 0,991, im Rahmen der Unsicherheit;
  Schlafzimmer 47,9° ± 2,8° / 1,04, aber Grundriss 44° gegen Paare 51°: Lage nachmessen). Verworfen:
  eine Person mit Spur nicht als Alternative zu einer Echoquelle zu wiegen (Bad unverändert 0,00, dafür
  im Flur 7.10. 09:49 Licht fälschlich an 0,32 von 13); die Schwelle 10⁻⁷ erst nach dem Zusammenlegen
  der Kinder in `_branch` (Licht gleich, Log-Evidenz +416 / +107 / −13, Rechenzeit +5 %: nicht in
  diesem Zweig). Zwei im Bett (22:20–22:26): P(=2) im Fenster 22:22–22:24 0,00 → 0,29; ohne das
  Abklingen der Existenz (5.5) 1,00: Die zweite liegt ungesehen neben der ersten, und keine Messung
  stützt sie (9, „Zwei in einer Spur“); das Licht ist richtig. Die Meldung 18:32 (Geist im
  Schlafzimmer, 10 min) ist ohne die Datei der Meldung nicht nachzuspielen (die App hatte bis 18:29
  andere Kalibrierungen); nachgespielt ab 18:04 steigt die Intensität der Unbekannten im Schlafzimmer
  von 0,11 auf 0,21, P(belegt) bleibt unter 0,2.

  | | Abend (11 / 43) | 6.10. (15 / 41) | 7.10. früh (7 / 13) | leere Nacht |
  |---|---|---|---|---|
  | 0.19.0 | aus 3,69, an 0; −710 089 | aus 1,50, an 0; −1 121 532 | aus 0, an 0; −449 439 | 0 min |
  | + quer zur Wand (auf 0.18.0) | aus 0,54, an 0; −703 333 | aus 1,50, an 0 | aus 0, an 0 | 0 min |
  | **+ Gitter in log g** | **aus 0,14, an 0; −702 755** | **aus 1,50, an 0; −1 121 044** | **aus 0, an 0; −448 559** | **0 min** |

  Die Zahlen hinter Türen (6.10., Bad und Schlafzimmer ohne Sensor) verschieben sich in beide
  Richtungen; sie zählen nur für das Herauskommen.
- **Lernschleifen** (8.10., Review des Algorithmus 3.2–3.5: „Hintergrund und Echorate lernen ungesichert
  aus dem Urteil des Filters“). Vier Dinge lernt die App aus ihrem eigenen Urteil; je Schleife, ob sie in
  den Daten wirkt und was sie schließt. Nachgespielt wie die App (Starts, Gelerntes weiter, 12
  Hypothesen); `replay_bg.py` und die Simulationen im Arbeitsordner.
  - *Der Hintergrund lernte Personen* (4.3), in beiden Richtungen:
    - 6.10. abends, Arbeitszimmer: Ring 5–6 (3,75–5,25 m, der Platz der zweiten Person, deren Spuren der
      Filter für Geister hält, 9) 7,5–8,6 statt leer 5,8; Ring 2 (Leon am Schreibtisch in 1,5 m, Energien
      bei 100 gekappt) 2,0–2,3 statt leer 6,5. Der zweite Fall ist eine Verzerrung des E-Schritts: Eine
      gekappte Energie zählte als 100, obwohl die Person mehr gibt; so lernte der Hintergrund nur 100/μ
      seines Anteils. Ein zu kleiner Hintergrund lässt den leeren Platz nach dem Gehen wie eine Person
      aussehen. Simulation (Person 1,8 m vor Sensor a, eine Stunde, der LD2450 sieht sie alle 15 min eine
      Sekunde): Ring 2 5,0 → 2,2.
    - 7./8.10. Schlafzimmer (neuer Sensor, zwei schlafen): bis 00:25 gehalten (P 0,95–1,00), dann nimmt
      eine Echoquelle des Schlafzimmers die Energie und bleibt die ganze Nacht an (0,93–1,00; Echos
      leben 20 s, 4.3), die Spuren im Bett (Leben 8–22 min) enden mit P(Geist) 1,000. Der Hintergrund
      lernte die Schlafenden: Ring 2 7,7 → 29 (06:00), Ring 3 7,5 → 19, ruhig im Mittel 7,7 → 12,8; leer
      gibt das Schlafzimmer 7,8–8,3 (17:20–21:40 und 06:20). Ihr LD2450 hatte in 67–100 % jeder
      10 Minuten ein Ziel in Sicht.
    - Der leere Raum ist stationär: leere Nächte 6./7. und 7./8. (ruhig, Mittel der Ringe) Arbeitszimmer
      5,8 / 6,2, Esszimmer 6,2 / 6,2, Flur 3,4 / 3,4, Küche 2,7 / 2,5, Wohnzimmer 4,9 / 4,9; Schlafzimmer
      abends 7,8, morgens 8,0–8,3. Zwischen Sensoren aber bis Faktor 2 (Küche 2,5, Schlafzimmer 7,8,
      Prior 5).
  - *Geprüft und verworfen:* (1) Lernen nur, wo nach dem Posterior niemand im Blick ist (EM mit der
    Verantwortung „niemand im Blick“ über alle Hypothesen, mit diesen Frames; selektive Aktualisierung,
    Koller et al. 1994, Toyama et al. 1999; Vorschlag des Reviews). In der Simulation richtig (Ring 2
    4,9, Echorate gleich), aber der neue Schlafzimmersensor lernte seinen Pegel nie: 5,0 statt 7,8 von
    17:13 bis 22:43, weil die um ein Drittel zu leise Annahme eine Person erscheinen ließ (P(Schlafzimmer)
    1,00 von 18:23 bis 22:03, der Raum leer), die das Lernen anhielt – vier Stunden Licht im leeren Raum.
    Dasselbe gilt für „nicht lernen, solange eine Hypothese jemanden im Raum hält“: Die Energien selbst
    halten die Person, die ein zu kleiner Hintergrund erfindet. (2) Vergessen 1 Tag statt 6 h (die
    Stationarität oben): Schlafzimmer Ring 2 06:43 26 statt 40, aber Log-Evidenz bis 6.10. −2530.
  - *Jetzt* (4.3): gekappte Energien mit `E[e | e ≥ 100]` (E-Schritt zensierter Daten); gelernt nur aus
    der Zeit ohne LD2450-Ziel im selben Gehäuse (ein Signal, das von den Energien unabhängig ist, also
    kein Stillstand durch eine erfundene Person) und nur, soweit keine Echoquelle an ist. Ergebnisse:
    - Arbeitszimmer 6.10. 21:00 / 23:00 ruhig 5,4 5,3 5,2 5,4 5,1 5,0 4,9 / 5,9 5,6 5,7 5,9 5,6 5,4 5,2
      (vorher 2,0 3,1 3,6 7,8 7,5 5,6 3,9 / 2,3 3,1 4,0 7,9 7,9 6,4 5,5), am Morgen 6,5 6,0 5,8 5,8 5,8
      5,8 5,8 (leer gemessen 6,54 6,04 5,84 5,83 5,83 5,84 5,79; vorher 5,8 5,5 5,5 6,2 6,2 5,9 5,8).
    - Schlafzimmer 7./8.10.: Ring 2 höchstens 12,1 statt 29, ruhig im Mittel 06:00 8,4 statt 12,8. Nur
      gekappte Energien richtig: 43,5 (der E-Schritt gibt den Schlafenden jetzt, was sie gaben); dazu
      ohne Lernen bei Echoquelle: 24,5.
    - Simulation wie oben: Ring 2 5,8 statt 2,2, alle Ringe beider Sensoren innerhalb 30 % (Test), die
      Person bleibt, nach dem Gehen niemand.
    - report_eval: bis 6.10. an 0 / aus 1,50 von 41 / 15 wie vorher, Log-Evidenz +2116; 7.10. früh 0 / 0
      von 13 / 7, +1553; 7.10. abends 0 / 0,14 von 43 / 11, +616; leere Nacht (`phantom.py`) 0 min.
    - *Offen:* Wen der Filter verliert und der LD2450 nicht sieht, lernt der Hintergrund weiter, soweit
      nichts seine Energie erklärt (Simulation, Start ohne Wissen, eine Sitzende 1,8 m vor Sensor a: a
      bleibt richtig, weil dort eine Echoquelle an ist, der 5 m entfernte Sensor b lernt 87 % ihrer
      Energie in einer Stunde; ein schwach gehaltener Platz in 4,6 m 15 %). Aus den Energien allein ist
      eine Person, die sich nicht bewegt, von einem anderen Hintergrund nicht zu trennen; schließen
      muss das der Filter: dass er sie verliert (bis 0.22 das Abklingen auch in Sicht, 5.5; eine
      Echoquelle, die stundenlang an bleibt, weil sie gegen ungesehene Personen anders gewogen wird als
      diese gegen sie, 9).
  - *Die Echorate lernte aus Personen* (4.3): neben einer Person zählte, was ihr Profil nicht erklärt,
    als begonnene Quelle (Simulation oben: 0,33 → 0,73 je Stunde am nahen, 0,47 am fernen Sensor). Jetzt
    nur, wo niemand im Blick ist, und ohne ihre eigene Rate beurteilt (J_Park2020, wie die Karte): 0,34 /
    0,33. Auf den Aufnahmen 0,17–0,53 je Stunde (vorher 0,15–0,37).
  - *Die Geisterkarte* (4.2, Review 3.1: der Kartenfaktor herausgerechnet, aber durch das Abschneiden
    harte Zählungen): Nach dem Abend 6.10. der Platz der zweiten Person 2,9-mal die Prior-Rate (der
    Arbeitszimmersensor zählte 25 Geister in 10,4 h), nach der Nacht 7./8.10. das Bett 3,5-mal (der
    Schlafzimmersensor 73 in 13,4 h, darunter die langen Spuren der Schlafenden mit P(Geist) 1,000), mit
    und ohne die Änderungen oben. Bei 14 Tagen Vergessen stünde ein Bett, in dem jede Nacht so gezählt
    wird, bei etwa 10-mal (überschlagen). Die Zählung hängt am Abschneiden und an der
    Echoquelle, die die Schlafenden nimmt (beides im Filter); die Statistik, die vom Abschneiden
    unabhängig ist (je Spur die Odds bei der Geburt ohne die Karte mal dem Überleben eines Geists über
    ihr Leben, oben „Die Geisterkarte lernte einen Sitzplatz“), ist nach der Arbeit am Abschneiden neu zu
    prüfen. Hier nicht geändert.
  - *Die Zielkarte* (6) ist nur Ausgabe: Mit gelernter, mit leerer und ohne Lernen sind Zählverteilung
    und Log-Evidenz bitgleich (`test_destination.py`). Keine Schleife.
- **Review 8.10.: Abschneiden, κ als Gitter, κ und g gemeinsam** (Review des Algorithmus, P0; 5.1,
  4.1, 4.3, 7). Drei Näherungen, die in der Theorie falsch waren:
  1. *Abschneiden vor dem Zusammenlegen* (5.1): Jetzt werden die Kinder einer Verzweigung nach ihrem
     Schlüssel gewogen (die Summe der gleichen), dann wird abgeschnitten (höchstens 10⁻⁷ der Masse
     verworfen statt jedes unter 10⁻⁷ des stärksten, höchstens 12), dann zusammengelegt; was verworfen
     wird, wird nicht mehr gemischt. Die verworfene Masse zählt in der Evidenz (7): auf allen Sätzen
     unter 1 (bei 12 und 16 Hypothesen); die Grenzen schneiden also fast nur die Zahl, kaum Masse ab.
     Allein die Masse-Schwelle ohne das Zusammenlegen davor kostete auf dem Abend −476 (auf 0.20.1,
     Licht gleich); mit dem Zusammenlegen davor sind Masse- und relative Schwelle fast gleich (−2).
  2. *κ als Posterior-Gitter* (4.1): 5 Zellen in log κ statt 3 Quadraturpunkten. 3 / 5 / 7 Zellen (auf
     0.20.1, 12 Hypothesen, Abend / bis 6.10. / 7.10. früh, gegen 0.20.1): Licht gleich (aus 0,16 /
     0,15 / 0,16 von 11 statt 0,14, sonst gleich), Log-Evidenz −34 / −16 / −16, −13 / −9 / −9, 0 / +83
     / +6. 5 genügt (gerechnet höchstens 25 % neben der Gamma-Verteilung, 4.1).
  3. *κ und g gemeinsam* (4.3): Gauß-Copula, ρ = 0,44 aus der gemessenen Rangkorrelation. Kosten keine
     messbaren (5 × 9 Zahlen je Gauß-Mischung, die Kacheln tragen g nicht).

  Nachgespielt wie die App (report_eval, 0.21.0 als Basis; Licht fälschlich an / aus, Log-Evidenz
  gegen 0.21.0; Spalten: Abend 7.10. (43 / 11 Fenster), bis 6.10. (41 / 15), 7.10. früh (13 / 7)).
  Die Varianten „nur 1“, „nur 2“ usw. waren Vergleiche auf dem Entwicklungsstand; im Code gibt es nur
  noch alle drei (ρ ist ein Parameter, `kappa_amp_corr`):

  | 12 Hypothesen | Abend | bis 6.10. | 7.10. früh |
  |---|---|---|---|
  | 0.21.0 | 0 / 0,14; −702 139 | 0 / 1,50; −1 118 928 | 0 / 0; −447 006 |
  | nur 1 (Zusammenlegen, Masse-Schwelle) | 0 / 0,14; +105 | 0 / 1,50; −139 | 0 / 0; −29 |
  | nur 2 (κ-Gitter) | 0 / 0,13; −67 | 0 / 1,50; −11 | 0 / 0; −291 |
  | 2 und 3 | 0 / 0,13; −66 | 0 / 1,50; −135 | 0 / 0; −423 |
  | **1, 2 und 3** | **0 / 0,13; +100** | **0 / 1,50; −144** | **0 / 0; −50** |
  | 1 und 2, ρ = 0 | 0 / 0,14; +105 | 0 / 1,50; −144 | 0 / 0; −104 |
  | 1, 2, 3 mit α = 2 | 0 / 0,13; +98 | 0 / 1,50; −162 | 0 / 0; −240 |

  | 16 Hypothesen | Abend | bis 6.10. | 7.10. früh |
  |---|---|---|---|
  | 0.21.0 | 0 / 0,14; −702 103 | 0 / 1,50; −1 118 945 | 0,14 / 0; −447 072 |
  | nur 1 | 0 / 0,14; +62 | 0 / 1,50; −114 | 0,07 / 0; +122 |
  | nur 2 | 0 / 0,12; −4 | 0 / 1,50; −7 | 0,14 / 0; −267 |
  | **1, 2 und 3** | **0 / 0,14; +42** | **0 / 1,50; −117** | **0,11 / 0; −62** |
  | 1 und 2, ρ = 0 | 0 / 0,14; +63 | 0 / 1,50; −106 | 0,11 / 0; −69 |
  | 1, 2, 3 mit α = 2 | 0 / 0,14; +35 | 0 / 1,50; −117 | 0,11 / 0; −226 |

  Leere Nacht (`phantom.py`) 0 min wie 0.21.0. Das Licht bleibt, wo es war; fälschlich an (16
  Hypothesen, Flur 7.10. 09:49, 9) 0,14 → 0,11. Wo jemand war, steigt P(belegt) etwas (bis 6.10.:
  Wohnzimmer 0,93 → 0,96, Küche 21:57 0,75 → 0,87; hinter Türen Schlafzimmer 22:15 0,32 → 0,57, Bad
  0,76 → 0,81), Bad 7.10. 22:09–22:20 0,95 / 0,95 / 0,97 → 0,96 / 0,94 / 0,97, zwei im Bett
  (22:22–22:24) P(=2) 0,29 → 0,32. Die Evidenz bewegt sich in beide Richtungen und je Satz um bis zu
  einige Hundert, mit wechselndem Vorzeichen zwischen 12 und 16 Hypothesen (5.1: nicht sicher); einzig
  das κ-Gitter allein kostet auf 7.10. früh bei beiden Grenzen (−291 / −267, fast ganz im Lauf ab
  07:44, Start ohne Wissen), mit dem Zusammenlegen davor nur −50 / −62. α = 2 ist nicht besser.
  *Die Schlafzimmernacht 7./8.10.* (10, „Lernschleifen“: die Geisterkarte zählte das Bett, weil die
  Spuren der Schlafenden mit P(Geist) 1,000 endeten; nachgespielt ab 17:13 bis 05:30): unverändert. Das
  Schlafzimmer 00:30–05:30 P = 0,00 in beiden, von 40 Geisterspuren des Schlafzimmersensors 00:00–06:30
  38 bzw. 37 mit P(Geist) über 0,999, die Karte danach 3,58- bzw. 3,66-mal die Prior-Rate. Die harten
  Zählungen kommen also nicht vom Abschneiden, sondern von der Echoquelle, die die Schlafenden nimmt (9).
  Rechenzeit: die vollste Stunde (7.10. 17:00–17:20, config10, wie die App, ein Kern, je dreimal
  abwechselnd) 0.21.0 51,9 s, nur 1 50,1 s, 1 und 2 50,6 s, alle drei 50,6 s (−2,5 %; mit 3 / 7
  Zellen 50,3 / 52,2 s): Was verworfen wird, wird nicht mehr gemischt, das wiegt die 25 statt 15
  Schichten von `steht` auf. Über die ganzen Nachspiele (report_eval, je drei gleichzeitig) +6 %
  (887 → 938 s), mit 16 Hypothesen +10 % (921 → 1016 s; nicht weiter zerlegt).
- **Tote Winkel und Existenz** (8.10., Review des Algorithmus 1.3, P1.6, Ablation A5; 5.5). Der Befund
  des Reviews: Das Abklingen (`record_life`, `e^(−t/2 min)` für jede bekannte Person ohne Spur, auch mit
  r = 1, auch in voller Sicht) ist ein Sterbeprozess und widerspricht Vorgabe 1.3; es ersetzte ehrliches
  r < 1 und ein Modell der toten Winkel; empfohlen: tote Winkel als Pseudo-Bereiche mit eigenem
  Aufenthalt (3.3). Geprüft, nachgespielt wie die App (report_eval, 12 Hypothesen; fälschlich an / aus;
  Küche in den drei Fenstern 7.10. 11:39–12:55; zwei im Bett 7.10. 22:20–22:26 P(=2); CPU des Nachspiels):

  | Variante | Abend (43 / 11) | bis 6.10. (41 / 15) | 7.10. früh (13 / 7), Küche | Bett P(=2) | CPU s |
  |---|---|---|---|---|---|
  | 0.22.0 (Abklingen überall) | 0 / 0,13 | 0 / 1,50 | 0 / 0; 0,05 / 0,02 / 0,01 | 1,00 / 0,32 / 0,87 | 457 / 226 / 357 |
  | ohne Abklingen | 0 / 0,04 | 0 / 1,00 | 1,16 / 0; 0,80 / 0,55 / 0,29 | 0,99 / 0,24 / 0,65 | 1186 / 391 / 896 |
  | tote Winkel als Bereiche (Aufenthalt log-normal wie 3.3), ohne Abklingen | | | 3,14 / 0; 0,98 / 0,97 / 0,82 | | – / – / 945 |
  | dazu Ablauf in Bereichen und toten Winkeln (Sicht < 0,05) | 0 / 0 | 0 / 1,00 | 0,11 / 0; 0,42 / 0,05 / 0,00 | 0,99 / 0,28 / 0,52 | 834 / 261 / 538 |
  | dasselbe, tote Winkel bei Sicht < 0,1 | | | 0,11 / 0; 0,39 / 0,05 / 0,01 | | – / – / 553 |
  | Ablauf nach Sicht `p_S(x)`, keine Bereiche, außer Haus nach 1 Tag vergessen | 0 / 0,10 | 0 / 1,00 | 0,07 / 0; 0,01 / 0,01 / 0,00 | 1,00 / 0,24 / 0,55 | 807 / 265 / 544 |
  | **`p_S(x)`, auch außer Haus (5.5)** | **0 / 0,01** | **0 / 1,00** | **0 / 0; 0,05 / 0,02 / 0,01** | **1,00 / 0,94 / 1,00** | **458 / 228 / 323** |

  Mit 16 Hypothesen (0.22.0 aus der Tabelle „Review 8.10.“ oben): Abend 0 / 0,01 (0.22.0: 0 / 0,14), bis 6.10.
  0 / 1,00 (0 / 1,50), 7.10. früh 0,11 / 0 wie 0.22.0 (Flur 9:49, 9), Küche 0,05 / 0,02 / 0,01, Bett 1,00 /
  0,94 / 1,00. Leere Nacht (`phantom.py`) 0 min. Was die Varianten zeigten:
  1. *Ohne Abklingen* kommt die Küche wieder (Licht an 1,16 von 13): Einträge mit r 0,8–0,99 aus
     Identitätsteilungen (welche bekannte Person eine Spur bekam; zusammengelegt zu Bernoullis, 5.6)
     sammeln sich in der Vorratsecke und an ihrem schwach gesehenen Rand (x 3,4, Sicht 0,07–0,1). Dazu
     hob das Küchen-LD2410C eine Person dort 11:22–11:37 von r 0,09 auf 0,99 (Log-Odds +7 in 15 min, so
     viel, wie das Abklingen mit 1/120 s abzog). Die Rechenzeit wächst um das Doppelte: Wer das Haus
     verließ, blieb bekannt.
  2. *Tote Winkel als Bereiche* (die Kacheln ohne jede Sicht als ein Ort mit Aufenthalt und Alter wie
     3.3, betreten durch Gehen über ihre Grenze, verlassen gehend auf die Kacheln daneben) machten es
     schlechter: Gehende an der Grenze kommen per Diffusion hinein und bleiben dann den Aufenthalt der
     Bereiche (Median 2 min) – ein Durchgang wird ein Aufenthalt; wer herauskommt, landet auf schwach
     gesehenen Kacheln und geht zurück (in der Simulation r 0,5 → 0,15 erst nach einer Stunde). Vor allem
     senkt ein Aufenthalt r nur bei r < 1 (die Odds fallen mit seiner Überlebensfunktion); Einträge mit
     r ≈ 1 hält er beliebig lange. Die Kacheln geben einem toten Winkel schon den Aufenthalt des Raums
     (Gehen, Anhalten, Stehen 3.1–3.2): kein Gewinn, der Code fiel weg.
  3. *Ablauf nur, wo kein Sensor prüft* (`p_S(x)`, 5.5): In Sicht endet nichts mehr (Arbeitszimmer
     6.10. 21:36 richtig, zwei im Bett bleiben zwei); die Vorratsecke und alles, was nur ein toter
     Winkel, ein Bereich ohne Sensor oder das Draußen hält, wird nach Minuten vergessen wie bis 0.22.
     Draußen musste es mitgelten: Mit Vergessen nach einem Tag (3.4) kamen Bekannte, die gegangen waren,
     mit 1/(4 h) zurück (Flur 9:49 2 s an) und kosteten Rechenzeit (+77 % am Abend).
  Im Haus erwartet je Stunde gegen die Telefone (`person.*` home/not_home, Geofence mit Verzug;
  nachgespielt wie die App 6.10. 19:28 – 8.10. 06:00 mit den App-Starts und Konfigurationen, Mittel je
  Stunde): im Mittel 1,00 → 0,98 daneben, keine Anhäufung (höchstens 2,23 um 17 Uhr bei zwei Telefonen,
  1,39 um 11 Uhr bei einem, wie 0.22.0); 22 Uhr 1,33 → 1,86 (zwei). Nachts 0 statt 2 in beiden: 6./7.
  schliefen beide im Schlafzimmer ohne Sensor (config5: Ablauf wie bisher); 7./8. verliert der Filter
  beide um 00:25 an eine Echoquelle des Schlafzimmers (9), in Sicht, also nicht durch den Ablauf.
  Rechenzeit (vollste Stunde 7.10. 17:00–17:20, config10, ein Kern, je dreimal abwechselnd, Mediane):
  51,3 → 52,7 s (+2,7 %); die Nachspiele oben gleich oder schneller. Bis 0.22 (5.5, Herleitung der 2 min, als das Abklingen überall galt):
  *Die Skala, gemessen* (`supp.py` im Arbeitsordner, Aufnahmen
  nachgespielt wie in der App): bekannte Aufenthalte in Sicht, 5,6 h (Schreibtisch 6.10. 19:31–20:50,
  22:20–22:50; 7.10. 08:06–08:09, 09:50–10:56, 11:40–12:54; Esszimmer 7.10. 08:15–08:29). Stütze: eine
  Messung des LD2450 (keine gehaltene) näher als 1 m am Platz oder eine Sekunde LD2410C, deren
  Energien eine Person am Platz dem gelernten Hintergrund vorziehen. Lücken zwischen Stützen: höchstens
  18,2 s, 99 % unter 0,3 s, 99,9 % unter 1,1 s (LD2450 allein bis 85 s, LD2410C allein bis 19,3 s). Ein
  leerer Platz in Sicht wird schnell widerlegt: Das LD2410C spricht dort mit −48 bis −125 log je Minute
  gegen eine Person (Schreibtisch −78, Esszimmer −125, Küchenstreifen −48, 7.10.); der LD2450 allein
  mit κ in der untersten Stufe nur etwa −0,4 je Minute. Das Abklingen wirkt also fast nur, wo nichts
  misst. Untergrenze: Eine Sitzende darf in ihrer längsten Lücke nicht unter die Lichtschwelle c = 2/3
  fallen, `18,2 s / ln(3/2)` = 45 s. Durchgerechnet (report_eval, 12 Hypothesen; fälschlich an / aus,
  Log-Evidenz; Küche in den drei Fenstern 7.10.):

  | Form | τ | bis 6.10.: an / aus, Evidenz | 7.10.: an / aus, Evidenz, Küche |
  |---|---|---|---|
  | überall, Stufen | 30 min | 0 / 1,17, −1121919 | 0 / 0, −449585, 0,08 / 0,02 / 0,01 |
  | überall, Stufen | 10 min | 0 / 1,67, −1121915 | 0 / 0, −450136, 0,18 / 0,02 / 0,01 |
  | überall, Stufen | 5 min | 0 / 1,67, −1121928 | 0 / 0, −450146, 0,10 / 0,01 / 0,01 |
  | überall, Stufen | 2 min | 0 / 1,68, −1122005 | 0 / 0, −449835, 0,03 / 0,01 / 0,01 |
  | überall, Stufen | 45 s | 0 / 2,00, −1122043 | 0 / 0, −449827, 0,07 / 0,01 / 0,01 |
  | **überall, Quadratur** | **2 min** | **0 / 1,68, −1121539** | **0 / 0, −449321, 0,04 / 0,01 / 0,00** |
  | überall, Quadratur | 45 s / 5 / 30 min | 0 / 1,65–1,67, −1121498…−1121542 | 0,11 / 0,00 / 0,08 an |
  | nur im beobachteten Bereich, hinter Türen vergessen | 45 s – 30 min | 0 / 2,00–2,59 | 0,18–0,21 an |
  | nur im beobachteten Bereich, hinter Türen bekannt | 45 s / 2 min | 0 / 1,75 / 1,68 | 0,11 / 0,14 an |

  Vergessen hinter Türen (Bernoulli → Poisson, die Masse bleibt) schadet: Die Intensität nimmt keine
  Rücksicht darauf, dass genau einer dort war, und nachdem er herauskam, blieb das Bad mit 0,44
  „besetzt“; neue Personen kamen leichter an Türen heraus (Flur 9:49 0,20 statt 0,05). Die einfachste
  Form, die die Daten tragen: ein τ überall. 2 min ist die kürzeste ohne fälschlich an in beiden
  Sätzen; eine Sitzende behält in ihrer längsten gemessenen Lücke r ≥ e^(−18,2/120) = 0,86. Was kürzer
  als 30 min mehr „aus“ kostet, ist ein einziges 6-s-Fenster (Arbeitszimmer 21:36: die zweite Person an
  ihrem Platz (−2,3; 8,6), deren Spuren dort der Filter für Geister hält, 9; an der Geisterkarte liegt
  es nicht, mit der Prior-Rate überall ebenso, 10); mit der
  Quadratur kostet es das auch bei 30 min. Mit 16 Hypothesen: bis 6.10. 0 / 1,68, −1121540; 7.10.
  0,11 / 0 (Flur 9:49 0,10), −449289. Meldungen 7.10. früh (Start 06:50): Esszimmer 8:29 1,00, Arbeitszimmer
  8:52 genau eine Person 1,00, Schreibtisch 8:07 1,00; leere Nacht (`phantom.py`) 0 min.
- **Hintergrundaktivität: Lernen pausieren** (8.10., Leons Frage: „Kann man alle Zeiten, in denen der
  Saugroboter aktiv war, automatisch aus den Trainingsdaten nehmen?“; `pause.py`). Der Saugroboter ist
  nach 1.1 harmlos als Person, aber was die App dabei lernt, bleibt: Die LD2450 verfolgen ihn fast in
  jedem Frame mit 0,05–0,3 m/s, Spuren von Minuten zählen als Person, kürzere als Geist. Nachgespielt mit
  config10, ab nichts gelernt, einmal ohne und einmal mit Pause (Saugroboter unterwegs bis 2 min nach
  dem Andocken, aus seinem Verlauf in Home Assistant):
  - 7.10. 17:13–18:30 (unterwegs 17:40–17:57): 50,0 statt 32,2 Geistgeburten in der Karte (Schlafzimmer
    11,1 statt 4,9, Wohnzimmer 3,6 statt 0,3); an Stellen auf seinem Weg die doppelte bis knapp dreifache
    Prior-Rate. Zielkarte 233 statt 183 Gänge. LD2410C-Hintergrund je Sensor bis ±11 %.
  - 8.10. 10:00–13:13 (unterwegs ab 11:30, sonst niemand unterwegs): 41,4 statt 1,0 Geistgeburten
    (Esszimmer 15,3 aus 35 seiner Spuren, Wohnzimmer 9,1, Küche 6,3), an Zellen auf seinem Weg 1,9- bis
    2,9-mal die Prior-Rate; 49 statt 0 Gänge in der Zielkarte, alle von ihm; Hintergrund Küche Ring 2
    (bewegt) +55 %, Flur und Esszimmer Ring 5 +32 % / +28 %; Echorate kaum (0,23 statt 0,24 je h).

  Mit 14 Tagen Vergessen und einem Lauf alle ein, zwei Tage würden das Geisterquellen entlang seines
  Wegs. Deshalb ein Schalter, kein Wissen über den Saugroboter in der App (Leon: „dass die App quasi
  einen Schalter hat, der ‚Lernen pausieren‘ oder ‚Hintergrundaktivität erwartet‘ heißt, und dass
  dieser dann über Node-RED angesteuert wird“): Solange er an ist, lernen Geisterkarte (4.2),
  LD2410C-Hintergrund und Echorate (4.3), Zielkarte (6) und Kalibrierdaten nichts; eine Spur, deren
  Leben in eine Pause reicht, zählt nicht; laufende Gänge der Zielkarte werden verworfen. Das Verfolgen
  ist dasselbe wie mit abgeschaltetem Lernen (Test bitgleich), der Saugroboter darf weiter Licht
  einschalten. Die Nachlaufzeit nach dem Andocken gehört zur Automation. Der Schalter liegt *retained*
  im Broker und hat keine Verfügbarkeit (auch schaltbar, während die App nicht läuft); die App hält ihn
  in `pause.json`, Fehlermeldungen tragen die Pausen (das Nachspiel lernt bitgleich wie die App),
  `tools/record.py` zeichnet ihn auf. Für die Tage davor schreibt `tools/vacuum_history.py` die Zeiten
  aus dem Verlauf (privat), die Werkzeuge nehmen sie mit `--pauses` und bewerten Fenster in einer Pause
  nicht. Wahrheitssätze 6.10. bis 7.10. früh: unverändert (0 / 1,00 von 41 / 15; 0 / 0 von 13 / 7; leere
  Nacht 0 min), dort war er nicht unterwegs. Abend 7.10. (er lief 17:40–17:57, vor allen Fenstern):
  fälschlich an 0 von 43, aus 0,01 → 0,06 von 11 (Bad 22:09 P 1,00 → 0,94, 4 % des Fensters; die zwei im
  Schlafzimmer P(=1) 1,00 → 0,64 im selben Fenster): Was 17 Minuten Saugroboter mitgelernt hatten, kippt
  vier Stunden später ein Fenster leicht, in beide Richtungen möglich; Evidenz −702 229 → −702 322.
- **Filter oder LD2450 des Raums** (8.10., nach 0.24.0; Leon: „Lichter gehen aus, während wir in Räumen sitzen,
  die der LD2450 klar sieht“; 6 „Belegt“, 9). Einfache Raumbelegungen gegen den Filter (`tools/baseline_eval.py`,
  bewertet wie `report_eval` und als Licht mit 2 min Nachlauf wie in Node-RED, vier Wahrheiten ohne die Nacht):

  | Licht in min | im leeren Raum | dunkel mit Person |
  |---|---:|---:|
  | Filter 0.24.0 (c = 2/3) | 4,3 | 25,6 |
  | Filter, c = ½ | 4,8 | 25,3 |
  | LD2450 des Raums allein (gemessen, 10 s) | 5,7 | 2,5 |
  | Filter oder LD2450 des Raums (gemessen, 10 s) | 5,7 | 0,6 |
  | … auch gehaltene Ziele | 10,9 | 0,2 |
  | Filter oder jeder LD2450 (gemessen, 5 s) | 21,6 | 0,7 |
  | Filter oder LD2410C-Energie ≥ 3× Hintergrund (10 s) | 93 | 0 |
  | Raum-Latch bis zum Hinausgehen (höchstens 5 min) | 104 | 0,1 |

  Die LD2410C-Flags waren in der leeren Nacht 74 % der Zeit an; ein Latch scheitert an Übergaben zwischen
  Sensoren (Ausgänge nicht erkannt). Gewählt: Filter oder LD2450 des Raums, gemessen, 10 s. Bei gleichen
  Kosten beider Fehler 6,3 statt 29,9 Fehlerminuten. Die Wahl „nur der Sensor des Raums“ fiel nach dem
  Flur-Fehler vom 8.10. (die Person am Esstisch aus 6–7 m im Flur gemessen), eine strukturelle Wahl, keine
  Abstimmung. In der App nachgespielt (`baseline_eval` Zeile `app`, `report_eval --published`): Licht im leeren Raum 6,3 min, dunkel mit Person 0,6 min (offline 5,7 / 0,6);
  fälschlich an 1,65 von 113 Raum-Fenstern, fälschlich aus 2,49 von 39 (Filter allein 0,01 / 4,82, unverändert:
  P(belegt) ist Sekunde für Sekunde dieselbe). Die leere Nacht 6./7.10.: 0 min. Die 0,6 min mehr Licht als
  offline kommen aus der Haltezeit: Die App hält genau 10 s, `baseline_eval` auf seinem 1-s-Raster 9–10 s.
  98,5–99,2 % der besetzten Sekunden sind gleich, die übrigen um eine Sekunde verschoben, in beide
  Richtungen; eine Sekunde am Anfang eines leeren Fensters zählt mit dem Nachlauf bis zu 2 min. Rechenzeit:
  13 µs je Frame mit drei Zielen, bei sieben Sensoren mit 10 Hz unter 0,1 % eines Kerns.
- **Durch die Wand ins Arbeitszimmer** (8.10. 21:35, live 0.25.0; Leon: der Bad-LD2450 meldete kurz ein
  Ziel hinter der Wand zum Arbeitszimmer). `binary_sensor.presence_arbeitszimmer_occupancy` ging mit
  `quelle: filter` an, das Licht auch, kurze Wiederholungen bis 21:36:48. Die Person saß im Bad bei
  y ≈ 4,4; ihre Spur sprang in 0,6 s an die Wand (y 5,30, 1,5 cm vor ihr) und zurück, später bis 0,37 m
  dahinter. Nicht der Wandtest von 0.20.0 war die Ursache (das Ziel um 21:35:00 lag noch im Bad), sondern
  das Kalman-Update: Mit der Geschwindigkeit des Sprungs und den Versätzen der Spur lag das Mittel danach
  bei y 5,6, und für Ausgaben zählt die Masse auf der Seite des Mittels (5.2). Nachgespielt mit der
  Live-Konfiguration ab 21:20 genau die Sekunden der App (21:35:00, 21:35:27, 21:36:09–12, 21:36:46–47).
  Jetzt wird die Position nach jeder Messung auf die Sicht des messenden Sensors beschnitten (4.1):
  Arbeitszimmer 21:30–21:40 nie besetzt, P höchstens 0,07 (21:38, jemand ging aus dem Bad in den Flur).
  Zuerst geprüft und verworfen: die Person auf die Seite des gemessenen Ziels zu spiegeln (bzw. für
  Ziele jenseits der Wand des nächsten Punkts in Sicht) und die Regel „LD2450 des Raums“ mit diesem Punkt
  zu zählen. Diese Regel ließ das Licht im Arbeitszimmer 7.10. 22:19–22:26 brennen (zwei im Schlafzimmer,
  der Arbeitszimmer-LD2450 sah Echos knapp hinter der Wand zum Bad: fälschlich an 0,19 → 1,96 von 43); die
  Spiegelung zur Seite des Ziels ist strenger als das Modell (dort muss die Person nur in Sicht sein, das
  Ziel liegt bis zu 0,3 m neben ihr) und kostete den Filter 6.10. aus 1,00 → 2,93 von 15 (Esszimmer 21:03
  P 0,73 → 0,53), die leere Nacht 0 → 1,4 min (Wohnzimmer 19:21). Mit dem Schnitt auf die Sicht (`report_eval --published` und
  `baseline_eval`, vier Wahrheiten, Zeile `app`): fälschlich an 1,65 von 108, aus 2,52 → 2,54 von 39 (ein
  Wohnzimmer-Fenster 8.10. P 0,42 → 0,32); Licht im leeren Raum 6,3 min, dunkel mit Person 0,6 min wie
  0.25.0; Filter allein aus 4,82 → 4,39 (6.10. 1,00 → 0,57, 7.10. abends 0,05 → 0,01); leere Nacht 0 min.
  Log-Evidenz 6.10. +191, 7.10. früh +401, 7.10. abends +415, 8.10. +3 435.
- **Meldungen 8.10. abends: Licht aus mit Personen im Raum** (App 0.24.0; Leon: „Sowas darf nicht
  passieren.“). Gemeldet 19:10:14 Bad, 19:10:21 Wohnzimmer (beide ohne Text) und 17:35:00 Küche („Zwei
  Personen saßen im Esszimmer“). Wahrheit aus LD2450, LD2410C, Telefonen (Bermuda) und Lichtverlauf
  (privat): im Bad eine Person 18:46:40–19:15 (sitzend an der Westwand bei (−5,0…−5,3; 4,3) bis 19:05:45,
  dann stehend bei (−4,0…−4,3; 4,4–5,1)); auf dem Sofa im Wohnzimmer mindestens eine 18:48–19:15 (Fernsehen);
  19:04:15–19:10:30 eine weitere am Esstisch (1,8; 3,7), vorher etwa 15 min auf dem Balkon; 17:21–17:43 zwei
  sitzend im Esszimmer bei (0,8; 4,8) und (2,7; 4,8), die Küche leer. Nachgespielt wie die App (Starts ab
  7.10. 17:13, 10:07:38 ohne Wissen; Wahrheit `truth_0810e` im Arbeitsordner).
  *Was der LD2450 meldete* (`sensortracks`, wie der Filter seine Spuren sieht): Bad 18:46:30–19:11 zehn
  Spuren am Sitz, meist gehalten (z. B. 731 gemessene gegen 2160 gehaltene Frames), Lücken ohne gemessene
  Spur bis 140 s (18:50:38), 122 s (18:59:21), 117 s, 90 s; ab 19:06:07 eine frische Spur im Stehen, 2358
  gemessene Frames in 229 s. Sofa: zwölf Spuren, Lücken bis 238 s (18:59:04), 147 s, 117 s, 102 s. Das
  LD2410C ruhig die ganze Zeit 6–20× dem Hintergrund (Bad Ring 2–3 20–100, Wohnzimmer Ring 3–4 30–100,
  Hintergrund ≈ 5). Der Filter (0.24.0) im Nachspiel: Bad 0,02, Sofa 19:04–19:10 0,02.
  *Ursache 1, ein Fehler* (4.3): Jede Komponente einer Person mit Spur zählte als im Blick jedes LD2410C,
  mit null Energie, und bekam dort `P(keine Echoquelle)`, ihr Teil „durch eine Tür“ (5.2) außerhalb 1. Je
  Block und Sensor mit einer wahrscheinlichen Echoquelle wuchs dieser Teil um das 1/P-fache, bei allen
  Personen mit Spur im Haus zugleich (die gleichen Faktoren −2,8 / −3,9 / −5,3 / −9,1 bei Personen in Bad,
  Flur und Schlafzimmer). Die Person im Bad ging 18:47:44–18:48:00 von 10⁻²⁸ auf 1 „außer Haus“, ihre
  weiteren Spuren wurden Geister (P 1,00), ihr Eintrag lief draußen in 2 min ab; die auf dem Sofa folgte
  18:48:19. Live dasselbe Bild (Bad aus ab 19:07:31, das Arbeitszimmer 19:07:29–39 kurz an).
  *Ursache 2* (5.1): Begann eine Spur, wo der Filter niemanden kannte, fiel ihre Alternative „Person“ unter
  die Schwelle des Abschneidens; danach sagten alle Hypothesen „Geist“, die Lebensdauer der Geister (39 s)
  kürzte sich heraus, und die Spur blieb ein Geist. Nachgewiesen im Schlafzimmer 8.10. 00:28:58: Der
  Schlafende wurde jenseits der Westwand gemessen (−5,5; 0,0), seine Spur endete, das LD2410C widerlegte
  ihn ohne Spur in 5 s (g = 1, 9), und jede neue Spur am Bett war bis 06:14 ein Geist.
  *Korrekturen:* LD2410C wiegt nur Komponenten in seinem Blick (4.3); keine laufende Spur verliert eine
  Alternative ganz (5.1). Nachgespielt (report_eval, 12 Hypothesen; fälschlich an / aus; Log-Evidenz des
  8.10.), „verloren in Sicht“ = Raumminuten mit P ≤ 2/3, während der LD2450 des Raums in den letzten 10 s
  dort ein gemessenes Ziel hatte, „an ohne Stütze“ = Raumminuten mit P > 2/3 ohne ein solches Ziel seit
  10 min und mit dem LD2410C auf Hintergrund-Niveau, beide über 26 h (7.10. 17:13 – 8.10. 19:11):

  | | 8.10. abends (16 / 6) | bis 6.10. (41 / 15) | 7.10. früh (13 / 7) | 7.10. Abend (43 / 11) | leere Nacht | verloren in Sicht / an ohne Stütze |
  |---|---|---|---|---|---|---|
  | 0.24.0 / 0.25.0 (Filter) | 0,01 / 3,77; −3 821 913 | 0 / 1,00 | 0 / 0 | 0 / 0,06 | 0 min | 350,6 / 1,4 min |
  | nur Blick des LD2410C | 0,01 / 2,30; −3 821 018 | 0 / 1,00 | 0,39 / 0 | 0 / 0,09 | | 332,4 / 2,1 min |
  | **beide** | **0,01 / 2,53; −3 814 284** | **0 / 1,00** | **0,39 / 0** | **0 / 0,02** | **0 min** | **139,8 / 2,0 min** |
  | beide und die Amplitude mit der Person (9) | 0,01 / 2,19; −3 811 163 | 0 / 2,00 | 0,50 / 0 | 0 / 0,00 | | |

  Verloren in Sicht je Raum (0.24.0 → beide): Schlafzimmer 257 → 62 min, Bad 23 → 12 min, die übrigen
  fast gleich (Flur 19, Esszimmer 22 → 19, Wohnzimmer 12 → 11, Küche 10 → 9, Arbeitszimmer 7 min: meist
  Übergänge zwischen Räumen und Lücken unter 2 min). Das Schlafzimmer
  kommt nach dem Ende einer Spur jetzt in etwa 2 min zurück (00:29:30 0,00 → 00:31:30 0,99) statt nach
  Stunden. Das Mehr an „7.10. früh“ ist ein Fenster von 28 s im Flur (09:48:50–09:49:18), 5 s, nachdem Leon
  durch den Flur ging: P 0,94 → 0,08 in 20 s, das Licht brennt dort mit dem Nachlauf ohnehin. In den
  Fenstern des 8.10.: Bad 0,02 → 0,70 (Rest: 9, Punkt 1), Wohnzimmer bis 19:04 0,91 / 0,71, danach weiter
  0,03 (9, Punkt 2), Esszimmer (der Gast am Tisch) 0,39. Die Küche 17:35 war kein Fehler der Belegung (dort
  0,00 im ganzen Fenster), sondern *wird betreten*/*Ziel* für 0,1 s (17:34:02,86–02,97): eine Sitzende 0,8 m
  vor der Küchentür, deren Spur mit 0,13 / 0,14 m/s zitterte; im Nachspiel `p_enter` 0,026 (Schwelle 0,048)
  und *Ziel* 0,31 (Schwelle 0,5), live knapp darüber. Das entscheiden die Kosten (`approach_cost` 0,05,
  `target_threshold` 0,5; 6), nicht das Modell. Mit dem veröffentlichten *besetzt* (0.25.0, Filter oder
  LD2450 des Raums, `report_eval --published`): 8.10. abends 0,03 / 2,48 → 0,04 / 1,75; über alle vier
  Wahrheiten fälschlich an 1,65 → 1,69 von 113, fälschlich aus 2,49 → 1,77 von 39 (bis 6.10. 1,07 / 0,
  7.10. früh 0,39 / 0 mit dem Flur-Fenster oben, 7.10. Abend 0,19 / 0,02). Was die Regel
  nicht abdeckt, sind die Lücken ohne gemessene Spur (9).
  *Zusammen mit 0.25.1* (Schnitt auf die Sicht, 4.1; dieselben Läufe, `report_eval` 12 Hypothesen, fälschlich
  an / aus; Filter allein und veröffentlicht):

  | | bis 6.10. | 7.10. früh | 7.10. Abend | 8.10. abends | Summe Filter | Summe veröffentlicht |
  |---|---|---|---|---|---|---|
  | 0.25.1 | 0 / 0,50; 1,07 / 0 | 0 / 0; 0,36 / 0 | 0 / 0,01; 0,19 / 0,01 | 0,01 / 3,80; 0,03 / 2,50 | 0,01 / 4,31 | 1,65 / 2,51 |
  | 0.25.1 + beide | 0 / 0,50; 1,07 / 0 | 0,39 / 0; 0,39 / 0 | 0,45 / 0,01; 0,64 / 0,01 | 0,01 / 2,29; 0,03 / 1,72 | 0,85 / 2,80 | 2,13 / 1,73 |

  Log-Evidenz gegen 0.25.1: 6.10. −25, 7.10. früh +1 711, 7.10. Abend +20, 8.10. +5 670. `baseline_eval` (Zeile `app`):
  Licht im leeren Raum 6,3 → 8,8 min, dunkel mit Person 0,6 → 0,4 min (Filter allein 4,3 / 26,3 → 7,3 /
  5,0). Verloren in Sicht (26 h) 0.25.1 297,4 → 129,6 min (0.25.0 350,6, beide ohne 0.25.1 139,8), an ohne
  Stütze 1,2 → 1,5 min; leere Nacht 0 min; 8.10. 21:30–21:40 das Arbeitszimmer nie besetzt (P höchstens 0,07
  wie 0.25.1); Rechenzeit der vollsten Stunde gleich (60,3 / 59,8 s). Das Mehr an „Licht im leeren Raum“
  ist ganz ein Fenster, 7.10. 22:22:00–22:24:30, Esszimmer (beide im Schlafzimmer): Eine Spur des
  Esszimmer-LD2450 an der Ostwand neben der Balkontür (3,6; 4,2), 22:19:55–22:23:40, wird mit P bis 0,92
  eine Person, die vom Balkon kam. Ihre Alternative „Person“ hält erst der zweite Punkt oben am Leben (in
  0.25.1 fällt sie bei der Geburt weg); ohne 0.25.1 erreicht sie 22:20:07 auch 0,53, ist aber, als die Spur
  22:20:27 wieder misst, schon zu 0,95 durch die Balkontür hinaus und wird Geist, mit 0.25.1 erst zu 0,59
  und bleibt. Nicht der Schnitt in diesen Minuten entscheidet das (ohne ihn 22:19–22:21 dasselbe), sondern
  der Zustand von 22:12: Mit den Personen von 22:12 ohne 0.25.1 gibt es den Geist auch mit 0.25.1 nicht,
  mit denen mit 0.25.1 auch ohne 0.25.1; gelernte Karten tauschen ändert nichts, ebenso 1–2 cm an den
  Personen. Es sind die Unbekannten (PPP; nur sie getauscht, kommt der Geist): Als 21:59–22:00 zwei durch
  Esszimmer und Küche gingen (mit 0.25.1 hielten die Hypothesen dabei noch eine unwahrscheinliche dritte
  Person, r 0,04), wuchsen sie mit 0.25.1 im beobachteten Teil von 0,003 auf 0,04 erwartete Personen, ohne
  nicht, und liefen danach auf den Balkon (22:02 0,019 statt 0,008; 22:12 dort 0,0128 statt 0,0080, im
  Esszimmer 7,4·10⁻⁶ statt 1,2·10⁻⁶). Ein Fall an der Schwelle aus der Vorgeschichte, keine Wechselwirkung
  der beiden Korrekturen im selben Augenblick.
- **Kandidat 0.26** (9.10.; von 0.25.1 mit den zwei Korrekturen der Meldungen 8.10. abends, ab0ed71 und
  fbd6c73, „combine-026“). Maß (Leon): Lichtminuten aus dem *veröffentlichten* „besetzt“ (Filter oder LD2450
  des Raums, 0.25.0) mit 2 min Nachlauf, über die vier Wahrheiten bis 6.10., 7.10. früh, 7.10. Abend, 8.10.
  abends; „Licht im leeren Raum“ und „dunkel mit Person“ zählen gleich. Dazu die leere Nacht 6./7.10. (muss 0
  bleiben), die Minuten „verloren, während der eigene LD2450 misst“ (P ≤ 2/3 und gemessenes Ziel des eigenen
  LD2450 in den letzten 10 s, 7.10. 17:13 – 8.10. 19:10, aus dem Nachspiel des 8.10.) und die Rechenzeit.
  Schwelle für eine Veröffentlichung: Summe unter 6,9 min, keine der beiden Fehlerarten deutlich schlechter,
  leere Nacht 0. `tools/baseline_eval.py` (Zeile `app` und `F`), `tools/report_eval.py --json` (beide
  Bewertungen in einem Nachspiel; 7.10. Abend und 8.10. in einem, die Starts des 7.10. sind dieselben).

  | | Licht leer / dunkel / Summe (veröffentlicht) | Filter allein | report_eval Filter an / aus | veröffentlicht an / aus | verloren 26 h | CPU 8.10. |
  |---|---|---|---|---|---|---|
  | 0.25.1 | 6,3 / 0,6 / 6,9 | 4,3 / 26,3 | 0,01 / 4,31 | 1,65 / 2,51 | 310 | |
  | combine-026 | 8,8 / 0,4 / 9,2 | 7,3 / 5,0 | 0,85 / 2,80 | 2,13 / 1,73 | 133 | |
  | + geschlossene Bereiche laufen nicht ab (a) | **8,8 / 0,1 / 8,9** | 7,3 / 0,1 | 0,87 / 1,09 | 2,15 / 1,02 | 130 | 1504 s |
  | + Geburt an neuen Spuren β = 10⁻⁶ /m²/s (b) | 12,3 / 0,1 / 12,4 | 11,1 / 0,1 | 2,76 / 1,04 | 4,01 / 0,99 | 108 | 1458 s |
  | + (a) und (b) | 12,3 / 0,1 / 12,4 | 11,1 / 0,1 | 2,73 / 1,11 | 4,01 / 1,05 | 110 | 1956 s |
  | + g je Aufenthalt auf den Kacheln (g) | 17,1 / 0,4 / 17,5 | 15,6 / 5,0 | 2,30 / 2,54 | 3,64 / 1,00 | 76 | 1396 s |
  | + (a) und (g) | 24,6 / 0,4 / 25,0 | 23,1 / 4,9 | 3,56 / 2,02 | 4,87 / 1,00 | 73 | 1607 s |
  | + (a), Balkon-Tor (c), nur 7.10. Abend und 8.10. | 7,9 / 0,1 (wie (a)) | 6,8 / 0,1 | | | 130 | 1491 s |

  Leere Nacht überall 0 min. Verloren aus dem Nachspiel des 8.10. mit `baseline_eval` (Ziel des eigenen LD2450
  wie dort gezählt; die Sonde `p_lost` gab für 0.25.1 / combine-026 297 / 130). CPU: das Nachspiel des 8.10.
  (26 h), je drei gleichzeitig (±20 %); auf der vollsten Stunde (7.10. 17:00–17:20, ein Kern) combine-026 / (a) 61,4 / 60,5 s.
  *Absturz* (vor allem anderen): `Tracker._end` nahm die Karte aus den Odds von P(Geist) mit
  `exp(log(1 − p) − log p + map_odds)`; seit keine laufende Spur eine Alternative verliert, kann p ~10⁻³⁰⁰
  sein, und das lief über (`OverflowError`, im Nachspiel der Ablation). Jetzt in der Form, die nicht
  überläuft (`_odds_shifted`); `Hidden.weigh` ebenso. Gleiche Zahlen, wo die alte Form endlich war.
  *(a) Geschlossene Bereiche laufen nicht ab* (5.5): 8.10. Sofa und Esstisch (der Gast vom Balkon) dunkel 0,3 /
  0,1 → 0 / 0,1 min, Filter allein dunkel 5,0 → 0,1 min, report_eval „aus“ halbiert; „Licht im leeren Raum“
  gleich. Im Haus erwartet gegen die Telefone (Mittel je Stunde, 7.10. 17:13 – 8.10. 19:10, nachgespielt wie die
  App): im Mittel 0,46 → 0,53 daneben. Der Unterschied liegt auf dem Balkon: 8.10. 7–9 Uhr 0,78 / 0,49 / 0,32
  erwartete Personen bei null Telefonen (vorher 0,01), Einträge der Nacht dort, die nur noch so schnell
  verblassen, wie Aufenthalte enden (3.3); 8.10. 19 Uhr 1,53 → 2,25 bei zwei Telefonen und dem Gast. Licht hat
  das nicht (Zahlen hinter Türen dienen nur dem Herauskommen). Das Esszimmer 7.10. 22:22 bleibt (P > 2/3 113
  → 176 s).
  *(b) Geburt an neuen Spuren* (die Dichte neuer Ziele im MHT, Reid 1979, als „eine Person, die der Filter
  verloren hat“): auf 7.10. Abend und 8.10. wie in der Ablation dunkel gleich gut wie (a), aber 7.10.
  22:20:45–22:24:30 eine zweite Person im Arbeitszimmer zu 100 % (+3,5 min); bis 6.10. und 7.10. früh gleich.
  Mit (a) zusammen wie (b) allein. Nicht übernommen.
  *(g) Amplitude je Aufenthalt auf den Kacheln* (statt g = 1 für Personen ohne Spur, 4.3, 9): das Gitter der
  Personen mit Spur, je Kachel als Verteilung bedingt auf „steht dort“, neu bei jedem neuen Aufenthalt und mit κ,
  von einer Spur auf die Kacheln mitgenommen, gewogen nur, wo eine Kachel mehr als 10⁻⁴ Personen hält (sonst am
  Mittel). Rechenzeit +9 % (vollste Stunde 59,9 → 65,2 s; ohne die Schwelle +25 %). Im Bad 8.10. hält es die
  Person ganz (Filter allein 328 s aus → 0 s), verloren in Sicht 133 → 76 min (Schlafzimmer 61 → 7, Bad 8 →
  5). Aber im Schlafzimmer 7.10. 17:34–21:42 (Leon allein im Arbeitszimmer) war fast durchgehend jemand ohne
  Spur (P 0,7–0,8; 18:32–18:41 10,8 min Licht im leeren Raum; bis 6.10. Filter allein aus 0,5 → 1,0): Eine
  Person mit kleinem g erklärt die beständige Energie dort so gut wie eine Echoquelle, und die Lebensdauer
  (Echoquelle 20 s) spricht dann für die Person. Nicht übernommen (9, Punkt 1).
  *Das Esszimmer 7.10. 22:22* (der Rückschritt von combine-026, 9 Punkt 3): Weder (a) noch (b) ändert es. Die
  Sensoren sehen dort etwas an und hinter der Balkontür (LD2410C ruhend 30–67 bei Hintergrund ~7 von 22:19
  bis 22:25; LD2450 22:19:50–22:21:50 an der Wand neben der Tür, 22:22–22:25 auf dem Balkon bei (4,7; 2,2),
  durch die Türöffnung). Nachgespielt ab dem Zustand von 22:12 (Hypothesen-Ledger): Die Alternative „Person“
  der Spur an der Tür beginnt bei Odds −3 gegen „Geist“ (ihre Herkunft ist der Balkon, 0,013 erwartete
  Unbekannte), schwankt um 0, und als die Spur 22:20:50 endet, wird sie eine bekannte Person ohne Spur an der
  Tür (r 0,47), die das LD2410C auf 0,9 hebt. *Ist die Intensität auf dem Balkon prinzipientreu?* Geburten gibt
  es dort keine (Neuankömmlinge nur an Wegen hinein, 3.4); die 0,013 sind ein Drittel der 1 erwarteten Person
  beim Start ohne Wissen um 17:13 (auf die Bereiche verteilt, ein laufender Aufenthalt zu zufälliger Zeit
  gesehen, schwerer Ausläufer) und zurückgegebene Bernoullis, die dorthin gingen. Das folgt aus dem Modell.
  *(c) Balkon-Tor*, geprüft: Ein Ziel in einem geschlossenen Raum ohne Sensor gilt nur noch als Messfehler einer
  Person in Sicht an seiner Tür (bisher wurde nur verworfen, was tiefer als `wall_margin` 0,4 m darin lag;
  (4,7; 2,2) liegt 0,2 m neben dem Wohnzimmer, hinter einer Wand). Das verwirft 7.10. abends 1803 Frames des
  Esszimmer-LD2450 (22:08–22:09, 22:16, 22:22–22:25), 8.10. 1191 (der Gast auf dem Balkon 18:50–18:52, 19:02),
  6.10. 264. Auf 7.10. Abend und 8.10. ändert es nichts (7,9 / 0,1 min wie (a); im Esszimmer P > 2/3 181 s):
  Spur an der Tür und LD2410C bleiben. Nicht übernommen (1.5).
  Wie empfindlich der Fall ist: Dieselbe Kette mit (g) allein 9 s P > 2/3, mit (a) und (g) 265 s. Ein Fall an
  der Schwelle aus der Vorgeschichte (wie combine-026 gefunden).
  **Ergebnis:** (a) ist die beste Kombination (8,8 / 0,1 / 8,9 min; dunkel besser als 0.25.1, 0,6 → 0,1), erfüllt
  die Schwelle aber nicht: 2,5 min des „Licht im leeren Raum“ sind das eine Fenster im Esszimmer 7.10. 22:22
  (ohne es 6,4 min). Verloren in Sicht 310 → 130 min, leere Nacht 0, Rechenzeit gleich.
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
