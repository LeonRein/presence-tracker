# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand: Code 0.65.0 (0.63.0 geprüft gegen den Code am 10.11.2026; seit 0.64 nur die geänderten Abschnitte).
Beschreibt, was der Code rechnet; was fehlt oder nur genähert ist,
steht in 9. Zahlen sind **gemessen** (auf Aufnahmen), **geschätzt** (EM auf Aufnahmen ohne Wahrheit)
oder **angenommen**. Literaturkürzel wie im Literaturordner (`~/Documents/presence-tracker-literatur`).
Die Abschnittsnummern werden im Code zitiert (`MODEL.md 4.1`); beim Umbau beibehalten.

## 1. Vorgaben

Nur die Punkte in diesem Abschnitt sind Leons Anforderungen, jeweils mit Datum seiner Aussage.
Alles andere in diesem Dokument ist eine Umsetzung, die Agenten gewählt haben; sie darf ersetzt
werden, wenn etwas Besseres belegt ist. Neue Vorgaben nur mit Datum und Leons Wortlaut eintragen.

1. **Zweck: Licht je Raum.** „korrekte lichtsteuerung ist immer wichtiger als korrekte personenzahl“
   (7.10.). Die Personenzahl kommt an zweiter Stelle.
2. **Dunkel mit Person ist schlimmer als Licht ohne Person.** „Licht aus und eine person ist im raum ist
   schlimmer als licht an obwohl niemand da ist“ (14.10.). Am 10.11. auf Nachfrage: etwa 5× so schlimm;
   das Verhältnis ist schwer genau zu sagen. Ist niemand zu Hause, darf nirgends Licht angehen (4.11.).
3. **Sitzende nicht verlieren, Schlafende sind kein Ziel.** „Das Modell [sollte] nicht darauf optimiert
   werden, jemand schlafenden zu erkennen. Schlafende Personen wollen eh das Licht aus haben. Es ist viel
   wichtiger, dass leute im Badezimmer oder auf dem Sofa im Wohnzimmer nicht verloren werden.“ (9.10.)
   Ob nachts eine oder zwei Schlafende gezählt werden, ist egal, solange die Bleibende morgens wieder
   erkannt wird (5.11.). Zu zweit an einer Stelle, eine geht, die andere bleibt, das Licht geht aus: „Dieser
   Fehler darf nicht mehr auftreten!“ (11.11.)
4. **Keine feste Personenzahl.** „Die Annahme einer festen Personenzahl ist schlichtweg falsch und führt
   zu anderen schweren Fehlern. […] Eine neue Person darf nur entstehen, wenn es dafür harte Evidenz gibt.
   Wenn es erst 5 Sekunden dauert, bis eine Person als solche identifiziert wird, ist das nicht schlimm.
   Und Geister dürfen sich ohne Belege in den Messwerten nicht halten können!“ (7.10.) Kein Wert für
   eine Bewohnerzahl, auch kein „Gästeplatz“ (7.10.).
5. **Personen entstehen und verschwinden nur mit begründendem Bewegungsmuster.** Eine Person kann nicht
   „vom Himmel fallen“ oder sich „auf der Stelle materialisieren“ (4.11.). „Es muss dennoch möglich sein,
   dass personen auch in der wonung verschwinden oder neu entstehen. Aber das sollte beides gut durch
   das bewegungsmuster begründet sein. […] Eben damit sich keine Fehler endlos fortführen können sondern
   korrigiert werden“ (4.11.). Keine harte Regel „nur durch Türen“. Fenster sind kein Eingang des
   Modells (4.11.). „Personen können sich nicht in Luft auflösen.“ (8.11.) „Wenn ein ld2450 ein bewegtes ziel
   reportet, welches sich mit üblicher geschwindigkeit ohne sprünge irgendwo hinbewegt, ist das ein ziemlich
   sicheres zeichen, dass dort eine echte person geht, welche sich nicht in luft auflösen kann!“ (11.11.)
6. **Keine Sonderregeln für Geräte.** „Das modell sollte gut genug sein, um eine Person von einem
   Luftentfeuchter zu unterscheiden!“ (5.11.) Ob der Saugroboter Licht einschaltet, ist egal (8.10.).
7. **Nicht auf Fehlerfälle optimieren.** „Das Modell/der Filter sollte nicht auf solche Fehlerfälle
   optimiert werden. Erstmal muss der normale Betrieb funktionieren.“ (9.10., Sensorausfall)
8. **Personen sind anonym; die App soll auch in anderen Wohnungen funktionieren** (bestätigt 10.11.).
   Vertauschte Identitäten sind egal.

Bewertet wird nach Leons Fehlermeldungen aus der App (6.10.), nicht nach geskripteten Wahrheitstabellen.

## 2. Die Welt

Aus dem Grundriss (`plan.rs`, `world/`):
- **Beobachteter Bereich:** Räume, die die Sensoren zu mindestens 60 % sehen.
- **Bereiche ohne Sensor *Rₖ*:** übrige Räume der Wohnung, über Türen und Trennlinien zusammengefasst.
  *Offen*, wenn eine Tür von dort nach draußen führt oder eine Eingangszone darin liegt, sonst
  *geschlossen* (Balkon).
- **Außer Haus:** alles jenseits der Wohnungstür. Ein Raum mit dem Haken *Eingang* (das Treppenhaus)
  ist kein Raum der Wohnung, sondern öffentlicher Raum, den auch andere Hausbewohner nutzen (Leon,
  7.10.: „Die Personenzahl hinter Treppe macht keinen Sinn, da dies öffentlicher Raum ist.“). Seine
  Fläche gehört zu *außer Haus*; die Tür dorthin ist die Wohnungstür. Wer hindurchgeht, hat die
  Wohnung verlassen; wer hereinkommt, kommt von draußen. Eine Tür in einer Außenwand ohne gezeichneten
  Raum dahinter ist nicht ganz dasselbe: Aus einem Bereich ohne Sensor führt sie in beide Richtungen,
  aus dem beobachteten Bereich nur herein (das Gehen über die Kacheln kennt nur Nachbarn in einem
  gezeichneten Raum, `door_rates` in `world/tiles.rs`); ebenso eine Eingangszone im beobachteten Bereich.
  Was ein Sensor dort misst, gehört zu niemandem der Wohnung und wird wie in einem geschlossenen Raum
  ohne Sensor verworfen (4.1: keine Information). Der gezeichnete Raum bleibt, weil er die Tür
  festlegt.
- Wände trennen (Personen gehen nicht hindurch, Radar sieht nicht hindurch); Türen sind Lücken.

**Zustand:** die bekannten Personen (ihre Zahl gehört zum Zustand, 3.4) und die unbekannten (eine
Poisson-Intensität), jede an einem Ort: im beobachteten Bereich mit Position und Betriebsart *steht* (mit Aufenthaltsart l und Erkennbarkeit κ) oder *geht*
(mit Geschwindigkeit), in *Rₖ* mit der bisherigen Aufenthaltsdauer, oder außer Haus. Dazu je laufende
Sensorspur ihr Eigentümer (eine Person, ein Geist oder eine Störquelle) und ihr Versatz auf ihm, je
Geisterspur ihre Quelle, und die bekannten Störquellen je Sensor (4.5, 5.5).

## 3. Dynamik

### 3.1 Stehen
- Position zittert: Diffusion 0,02 m/√s (gemessen).
- Aufenthaltsdauer: Überleben `S(t) = (5,6 s / (t + 5,6 s))^0,58` (Lomax, gemessen 5.10.). Als
  Mischung von 5 Exponentialverteilungen (Feldmann & Whitt 1998, angepasst an Punkten in gleichen
  log-Abständen von 2 s bis 1 Tag): Jeder Aufenthalt hat seine eigene Rate λ_l des Aufstehens. Wer
  weitersteht, verschiebt sich zu den langsamen Arten: `p_l ← p_l e^(−λ_l Δt) / Σ` (Hidden-Semi-Markov
  als HMM mit parallelen Phasen, D_Yu2010 Abschn. 3.2, 4.1.3). Fehler der Anpassung zwischen 1 s und
  1 Tag: höchstens 0,024 in S, 16 % relativ, 42 % in der Rate des Aufstehens (mit 7 Arten 0,004 /
  2 % / 18 %). Alte Messung (Python-Kern, Aufnahme gelöscht): Die Evidenz (7) unterschied das nicht,
  6.10. 19:28 bis 7.10. 06:15 (11 h, 5 Sensoren,
  Neustarts und Nacht), gegenüber 5 Arten mit 7 Arten +1, 6 Arten +2, 4 Arten −11, 3 Arten −53;
  mit 24 Hypothesen ab 10⁻⁹ (5.1) 7 Arten und 5 Stufen κ (0.10.0) +3, 4 Arten −9, 3 Arten −100.
  Lichtfehler (nach der damaligen, ungenauen Wahrheitstabelle) gleich, mit 3 Arten mehr (fälschlich
  aus 3,5 statt 3,0 bzw. 2,5 statt 2,0 von 15).
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
  (angenommen; `dwell_median`, `dwell_spread`, `world/tiles.rs`). Nicht gelernt: Wann jemand hinein- und herausging, kennt der Filter
  nur als Wahrscheinlichkeit; seine Urteile als Dauern zu zählen, lernte seine Fehler. Zu schätzen
  über die Evidenz (7). Endet der Aufenthalt, geht man durch eine der Türen des Bereichs, jede gleich
  wahrscheinlich: gehend in den beobachteten Bereich oder durch eine Tür nach draußen außer Haus.
- **Hinaus** führt die Bewegung durch Türen: aus dem beobachteten Bereich gehend durch die Wohnungstür
  (die Diffusion aus 3.2 über die Tür, wie in einen Bereich ohne Sensor; zu Türen ohne gezeichneten
  Raum dahinter 2), aus einem offenen Bereich am Ende eines Aufenthalts (oben). Eine eigene Rate des
  Weggehens gibt es nicht. Ohne Tür kann ein Eintrag nur über seine Existenz r enden (3.4, 5.5).
- **Zurück** mit 1/(4 h) je Weg hinein (Türen nach draußen, Eingangszonen) (angenommen); man erscheint
  gehend an der Tür (beobachteter Bereich) bzw. beginnt einen Aufenthalt im offenen Bereich.

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
  LD2410C), senkt r. Das ist keine Regel: dieselbe Rechnung wie für ihre Dichte (1, Punkt 5).
  Der Teil von ihr, den kein Sensor prüfen kann, läuft mit 2 min ab (5.5).

## 4. Messmodell

Messungen mit Datum bis 30.10. liefen auf Aufnahmen, die am 31.10. gelöscht wurden; sie lassen sich nicht
nachrechnen. Vergleiche gegen die Wahrheitstabellen dieser Zeit sind unsicher, die Tabellen waren ungenau.

### 4.1 LD2450: seine Spuren
Der LD2450 ist selbst ein Tracker (gemessen 5.10., 16 h: ~23 Spuren je Sensor und Stunde, Median
60 s; Fehler zweier Sensoren auf derselben Person nach 20 s noch zu 0,41 korreliert). Jeden Frame als
unabhängige Messung zu nehmen, macht überkonfident (E_Aeberhard2017 S. 82). Gemessen wird deshalb auf
der Ebene seiner Spuren: wann er eine beginnt, verliert, wiederfindet, und wo sie liegt.

**Vorverarbeitung** (`frames.rs`, `ld2450/tracks.rs`, nur das Format):
- Schrägentfernung → Boden (Sensorhöhe, Reflexionshöhe 1,0 m), Maßstab des Sensors aus der
  Kalibrierung.
- Keine Information: Ziele mehr als 0,4 m hinter einer Wand (Sichtlinie ab 20 cm vor dem Sensor),
  mehr als 0,4 m außerhalb aller Räume, mehr als 0,4 m innerhalb eines geschlossenen Raums ohne Sensor
  (`wall_margin`), näher als 0,3 m am Sensor. „Hinter einer
  Wand“ heißt: kein Punkt im Umkreis von 0,4 m ist in Sicht (der Messfehler einer Person, die es ist;
  geprüft zurück entlang der Sichtlinie und am Fußpunkt auf jeder Wand im Umkreis). Bis 0.18.0 nur
  entlang der Sichtlinie: Ein Sensor an einer Wand blickt an ihr entlang, und ein Ziel 5 cm jenseits
  dieser Wand lag entlang der Sichtlinie 0,4 m dahinter (10, „Meldungen 7.10. abends“).
  **Wer gemessen wird, ist in Sicht** (seit 0.25.1): Der Sensor sieht nicht durch Wände; ein bis 0,4 m
  jenseits einer Wand angenommenes Ziel kommt von einer Person, die er sieht. Das Kalman-Update (5.2)
  kennt keine Wände: Eine Spur an der Wand zog das Mittel hinüber, die Person stand im Nachbarraum (10,
  „Durch die Wand ins Arbeitszimmer“). Nach jedem Update mit einer Spur wird die Position je Komponente
  auf die Sicht des Sensors beschnitten (`Mixture::in_sight`, `cut_to_sight` in `filter/mixture.rs`; nicht die
  Quelle eines Geists, 4.2): N(Mittel, diag Varianz) auf einem Gitter von
  5 × 5 Gauß-Hermite-Punkten, die Punkte, deren Sichtlinie (ab 20 cm vor dem Sensor) eine Wand kreuzt,
  entfallen, Momentenabgleich (mindestens 5 cm Streuung je Achse), Geschwindigkeit und Versätze folgen
  über ihre Regression. Sieht er keinen der Punkte, werden sie an der Wand
  gespiegelt wie die Bewegung (3.2). Das ist die Likelihood mal „in Sicht“ als 0/1 (nur Wände, nicht
  das Blickfeld); die Gewichte der Hypothesen ändert der Schnitt nicht.
- Die drei Plätze rücken auf; Ziele werden über die Nähe verknüpft (≤ 0,6 m je Frame).
- Gehaltene Frames sind keine Messung: ≥ 3 Frames mit derselben Geschwindigkeit ≠ 0 (der Sensor lässt
  ein verlorenes Ziel weiterlaufen) oder bitgleiche Koordinaten (eingefroren).
- Mehr als 6 s ohne Frame: Datenverlust, alle Spuren enden, 6 s nach dem letzten Frame, ohne auf den
  nächsten zu warten (bis 0.18 erst mit ihm: Ein ausgefallenes Board hielt seine Spuren stundenlang).

**Erfassung (neue Spur)** einer Person ohne messende Spur dieses Sensors mit der Rate
`ρ_m · P_m(r) · g_s(x) · q(x)`, je Betriebsart m:
- `P_m(r)`: Swerling-I-Kurve über der Radargleichung, `P(r) = P_FA^(1/(1 + SNR₅₀ (r₅₀/r)⁴))`,
  P_FA = 10⁻⁶, r mindestens 0,3 m. ρ, r₅₀ per Maximum-Likelihood (gemessen, 17 h): steht 0,034 /s, 5,0 m;
  geht 0,51 /s, 4,55 m. Für Sitzende ist die Form eine empirische Wahl (der LD2450 unterdrückt Ruhendes).
- `g_s(x)`: Sicht nach Winkel, Reichweite und Wänden (`world/build.rs`, `prior_pd`): 0,95 bis 15°
  vor dem Rand des Blickfelds, die Hälfte davon am Rand, 0 bei 15° jenseits; voll bis Reichweite + 1 m, dann
  Abfall mit e je 1,5 m (gemessen 5.10. an Gehenden), 0 ab Reichweite + 4 m und in geschlossenen Räumen ohne
  Sensor.
  **Sicht einer Person, nicht eines Punkts** (seit 7.10., `spread` in `world/build.rs`): Der Sensor misst
  die Person dort, wo ihre Spur auf ihr sitzt, `z = x + c + o + w` (unten). Er sieht sie also so weit,
  wie er diese Stelle sieht: `g(x) = E[g_s(x + c + o)]`, Versatz gaußsch mit der Varianz von unten je
  Entfernung, gemittelt über das, was von x aus ohne Wand erreichbar ist (durch eine Türlücke ja,
  durch eine Wand nicht; in den Bereichen ohne Sensor bleibt die Sicht der Punkte). Vorher war der
  Schatten einer Wand eine scharfe Kante auf 0,1 m: Wer 0,2 m im Schatten stand, war unsichtbar,
  obwohl seine Spur in Sicht saß (6.10. 20:55, Flur aus Sicht des Arbeitszimmers: „die Spur ist
  Leons“ hatte das Gewicht 0, 10), und ein 0,2 m breiter Streifen der Küche an ihrer Wand (x 2,2–2,4)
  war für jeden Sensor und das LD2410C unsichtbar (Kacheln 0,08 m²): Dort sammelten sich Personen
  (7.10. 07:10 Küche 0,76, 10). Danach erfasste der LD2450 dort Gehende mit 0,26–0,32 /s (Raummitte
  0,48 /s), das LD2410C erwartete 26–40 statt 0 (gerechnet 7.10. im Python-Kern, Grundriss von damals).
- `q(x)`: Auflösung neben einem gemessenen Ziel,
  `1 − exp(−ln 2 ((Δr/R_r)² + (Δquer/R_q)²))` (Form A_Svensson2012 Gl. 18–20); neben einem
  gehaltenen Ziel stattdessen `1 − exp(−d²/(2·0,7²))` (dort würde es wiedergefunden). **Je Betriebsart** (seit 0.63,
  `resolution`): eine Stehende neben jedem Ziel R = 1,4 / 2,1 m, eine Gehende neben dem Ziel einer Gehenden 0,6 / 0,9 m,
  neben einem ruhenden Ziel gar nicht (q = 1; ruhend ist ein Ziel, dessen gemessene Geschwindigkeit entlang des Strahls
  unter 0,3 m/s liegt, und eine neue Spur bis zu ihrer ersten Messung). Gemessen 8.11.: Zwei Ruhende nah beieinander (Sofa 8.11. 36 min, Bett in 7 Nächten zu je 4 h)
  geben in 1–4 % der Frames zwei Ziele, das Modell erwartete mit 0,6 / 0,9 m bei 0,6 m Abstand etwa die Hälfte;
  Maximum-Likelihood der Wiedererfassung einer verlorenen Zweiten neben der Ersten (13 Episoden, `dev/personenzahl` 8.11.
  `reacq.py`, Rate `ρ κ (1 − exp(−ln 2 (d/R)²))`): R 1,5–2,0 m (κ 0,5–1), mit 0,6 m −30 log. Unabhängig davon: Ein ruhendes
  Ziel, das bleibt, meldet der LD2450, während eine Gehende in < 0,6 / 0,6–1 / 1–2 m daneben ist, zu 0,4 / 13 / 37 % der
  Frames, sonst zu 89 % (`doppler2.py`, 7.–8.11.): dieselbe Form mit R ≈ 1,75 m. Er trennt Ruhende also nicht über Doppler,
  sondern meldet die Gehende und lässt das ruhende Ziel fallen. Bis 0.62 galt 0,6 / 0,9 m für alle, und eine Verdeckte
  neben dem Ziel konnte ungesehen gehen (10 „Nacht 8./9.11.“).
- **Erkennbarkeit κ** einer stehenden Person in diesem Aufenthalt (Haltung, Platz): dieselbe Person
  wird am Tisch alle halbe Minute erfasst, auf dem Sofa minutenlang nicht. κ ~ Gamma(α, α), Mittel 1;
  neu bei jedem Stehenbleiben, innerhalb eines Aufenthalts mit 1/(600 s) neu gezogen (D_Mahler2011
  Gl. 49–52; D_Wilthil2019 Gl. 2). **Der Filter führt den Posterior von κ, nicht seine Momente** (seit
  8.10.): ein Gitter von 5 Zellen gleichen Abstands in log κ zwischen dem 0,1-%- und dem 99,9-%-Quantil
  (die äußeren Zellen reichen bis 0 und ∞), je am bedingten Mittel mit der Masse des Priors in der
  Zelle, wie die Amplitude des LD2410C (4.3; ein Punktmassenfilter über κ, 0_Arulampalam2002 Abschn.
  II-B): 0,105 / 0,249 / 0,541 / 1,11 / 2,12 mit 0,010 / 0,066 / 0,302 / 0,496 / 0,127 (α = 3). Warum ein Gitter
  in log κ: Was die Stufen tragen müssen, ist `E[e^(−κ·a·T)]` für eine Sitzende, die die Zeit T mit
  der Erfassungsrate a nicht erfasst wird, also die Laplace-Transformierte des Priors an s = aT; sie
  hängt nur von log κ + log s ab, ein gleichmäßiges Gitter in log κ ist deshalb über alle Zeitskalen
  gleich genau. Gerechnet (nicht gemessen) für das damalige α = 1, a = 0,034 /s (`acquire`, nah, ein Sensor) mit dem
  Neuziehen, gegen ein feines Gitter (4000 Zellen): nach 1 / 5 / 10 / 20 / 60 min ungesehen 0,91 / 0,83
  / 0,79 / 0,75 / 0,74 des Richtigen (7 Zellen 0,94–0,85, 9 Zellen 0,97–0,90; bei a = 0,1 /s ebenso).
  **Bis 0.21** 3 Punkte der verallgemeinerten Gauß-Laguerre-Quadratur (Golub & Welsch 1969), 0,42 /
  2,29 / 6,29 mit 0,71 / 0,28 / 0,01: exakt für die Momente bis zum 5., aber nicht für `e^(−κaT)`,
  das kein Polynom niedrigen Grades ist; die unterste Stufe entschied allein, wie lange eine Sitzende
  ungesehen bleiben konnte: 0,94 / 0,13 / 5·10⁻³ / 4·10⁻⁶ / 3·10⁻¹⁹ des Richtigen (Review 8.10.).
  Die konjugierte Form (Gamma(α, β) mal `e^(−κR)` ist Gamma(α, β + R), je Komponente und Kachel ein
  (α, β)) ist nur ohne Neuziehen exakt: Das Neuziehen, neue Aufenthalte und die zensierte Erfassung
  beim Start (`1/(1 + κc)`, 4.1 unten) machen Mischungen, die je Schritt wieder auf eine
  Gamma-Verteilung projiziert werden müssen (Mittel und Mittel des Logarithmus); so gerechnet (α = 1) 1,00 /
  0,88 / 0,74 / 0,65 / 0,83, nicht genauer als 4–5 Gitterzellen, aber mit einer Newton-Iteration über
  Digamma je Kachel und Takt statt einer Multiplikation. Vergleiche in 10, „Review 8.10.“.
  **α = 3 (seit 0.60.0, gemessen 5.11.):** In Zeiten mit nur einer Person zu Hause (Telefone, 31.10.–4.11.) je Aufenthalt
  in einem Raum (von der ersten bis zur letzten Spur ≥ 10 s dort, bevor eine solche in einem anderen Raum beginnt; 46
  Aufenthalte ≥ 3 min, 15,9 h) die Lücken zwischen frischen Zielen des LD2450 dieses Raums (`dev/personenzahl/luecken.py`):
  die längste 240 s (Schlafzimmer), im Bad 98 s; nach 10 s ohne Ziel kommt eines mit 0,059 /s, nach 30 s mit 0,018 /s
  (die Rate fällt: verschiedene κ). Mit α = 1 wären von den 20 Lücken über 30 s etwa 4 länger als 4 min, gemessen keine;
  mit α = 3 eine. Maximum-Likelihood (je Aufenthalt Exp(λκ), κ ~ Gamma(α, α)) α 1,9 (α 1 −2,2, α 3 −1,1 log). Das Gitter
  aus 5 Zellen trägt `E[e^(−κs)]` bei α = 3 bis s = 20 (ein Neuziehen, 600 s bei 0,034 /s) auf 0,74 genau; darüber ist es
  strenger als sein Prior (keine Zelle unter 0,1; bei s = 60 0,16 davon, wo der Prior selbst 10⁻⁴ gibt).
  Bis 0.59 α = 1 (angenommen). Gehende: κ = 1.
- Wer keine Spur bekommt, wird mit `exp(−Rate·Δt)` gewichtet; das gilt für jede Person, die von einem
  Sensor nicht schon gemessen wird.

**Verlieren und Wiederfinden:**
- Verlieren ist bei Personen und Geistern gleich häufig: keine Information.
- Zeit bis zum Wiederfinden (gemessen): log-normal, Median 0,13 s / Streuung 1,0 (weiterlaufend) bzw.
  0,2 s / 2,0 (eingefroren); 3 % bzw. 2 % nie.
- Wiedergefunden wird auf dem Eigentümer, auf einer anderen Person nahe der gehaltenen Stelle
  (Gauß-Kern 0,7 m × Sicht, `k(x)`), auf dem Geist, der sie war, auf der Störquelle, die sie war (4.5), oder auf
  einer Reflexion dort (wie 0,05 Personen). Nicht-Wiederfinden spricht gegen alle diese, ohne Frist; gegen eine andere
  Person aber nur, soweit der Sensor sie vom gehaltenen Ziel auflöst (seit 0.62): mit dem Faktor
  `S(u)^(k(x)·(1 − P_u(x, z)))`, `P_u` die Form der Auflösung von oben (A_Svensson2012 Gl. 18–20, `resolved_from` in
  `filter/evidence.rs`). Das gehaltene Ziel gilt dabei als ruhend: Seit 0.63 gilt für Personen ohne Spur und für die
  Komponente *steht* der anderen die Auflösung der Stehenden, 1,4 / 2,1 m; für die Komponente *geht* einer anderen
  Person mit Spur zählt das Nicht-Wiederfinden voll (q neben einem ruhenden Ziel ist 1 für Gehende, oben). Wer
  mit dem Eigentümer in einer Auflösungszelle steht, ist mit ihm eine Quelle, die höchstens ein Ziel gibt
  (A_Beard2015 Gl. 16–17: je Gruppe einer Partition höchstens eine Messung; Mahlers Punkt-Cluster); sein Ausbleiben
  ist das Ausbleiben dieses einen Ziels und zählt schon beim Eigentümer. Bis 0.61 zählte es für jede Person in der
  Nähe noch einmal, als könne der Sensor sie getrennt wiederfinden: Die zweite Person neben Leon im Bad verlor in
  100 s Δlogit r = −6,0 allein dadurch (6.11. 19:28, 10 „0.62: Zwei als ein Ziel“).
- Fallenlassen der Spur: keine eigene Information (für Geister: Lebensdauer, 4.2).

**Position:** `z = x + c + o + w`. Versatz c konstant je Spur, o wandert (OU, τ = 3,5 s), w = 5 cm.
57 % der Versatzvarianz bleibt, 43 % wandert (gemessen). Gesamtstreuung entlang 0,15 m + 2 %, quer
0,10 m + 5 % der Entfernung; im Filter je Achse ihr Mittel. c und o sind Teil des Kalman-Zustands
(E_BrysonHenrikson1967). Verwendet wird höchstens alle 0,25 s eine Position.
**Breiter Versatz einer neuen Spur** (seit 0.58.0, `offset_wide`): Beginnt eine Spur auf einer Person, die schon eine
Spur hat, oder wird eine gehaltene Spur auf einer solchen anderen Person wiedergefunden, sitzt sie mit 0,8 auf dem
engen Versatz oben und mit 0,2 auf einem breiten, σ = 2,0 m je Achse auf c
(Skalenmischung, die Form eines Ausläufers wie bei einer t-Verteilung). Behalten wird der Anteil, der die Spur besser
erklärt, gewogen mit beiden. Gemessen 4.11. in Zeiten mit nur einer Person zu Hause (lange gehende Spuren zweier
Sensoren, gehaltene Frames verworfen, ohne Dobby): Abstand zweier Sensoren auf der einen Person im Median 0,64 m, 27 %
über 1 m; Mischung zweier Rayleigh-Verteilungen 0,79 mit 0,47 m und 0,21 mit 2,5 m je Achse (beim Beginn einer Spur
0,61 / 0,48 m und 0,39 / 1,9 m, n = 54). Der enge Teil des Modells ergibt etwa 0,3 m. Bis 0.57 machte ein Versatz von
1–1,5 m beim Gang durch eine Tür eine neue Person (4.11. 17:13:55, später ein Geist im Esszimmer). Wirkung (Aufnahmen
3.11./4.11., Kennzahlen in 10 „Personen nur mit Bewegungsmuster“): Minuten mit zwei belegten Räumen, während nur
eine Person zu Hause war, 3.11. 65 → 5.

**Start und Datenlücken:** Was ein Sensor beim ersten Frame (oder nach einer Lücke) schon verfolgt,
wird nicht als Geburt gewertet: Eine Person ist zu einem beliebigen Zeitpunkt mit dem Anteil
`a·L / (1 + a·L)` erfasst (a Erfassungsrate, L Lebensdauer einer Spur, gemessen 60 s stehend, 30 s
gehend); Geister entsprechend mit ihrer Lebensdauer. Ebenso nach einer neuen Lage oder Kalibrierung
des Sensors (5.3): Seine Spuren enden ohne Information, sein nächster Frame ist ein Start.

### 4.2 Geister
Eine Spur, die zu keiner Person gehört.
- **Entstehen** je Sensor mit einer festen Rate `λ` = 1,8·10⁻⁵ je m² und s, überall in seinem Blick (Sicht
  über 0,05), für alle Sensoren gleich, unabhängig davon, wo Personen sind. Das ist die gemessene mittlere Rate
  (30.10.): 692 Geister in 437 Sensorstunden, 1,6 je Sensorstunde, bei 17–26 m² Blick. Keine Karte, nichts wird
  gelernt.
- **Dazu, während jemand geht** (seit 0.58.0, `walker_ghosts`): je Sensor und gehender Person (Gewicht ihrer
  Gehen-Komponente) 0,0057 Geister je s, gleich über seinen Blick verteilt, von der kurzen Art (3 s). Gemessen 4.11.
  in Zeiten mit nur einer Person zu Hause: Spuren ≤ 10 s, die > 1,5 m von jedem anderen Ziel beginnen, während eine
  Spur geht: 62 in 1 545 Gehsekunden (Küche 0,014, Wohnzimmer 0,012, Bad 0,002 je s). Damit ist eine zweite gehende
  Spur neben einer Gehenden zuerst ein Geist; eine eigene Person wird sie, wenn sie sich länger wie eine verhält.
  Im Code (`filter/branch.rs`) steht dieser Teil nur in der Dichte, mit der eine neue Spur als Geist beginnt. Gezählt
  wird das Gehen-Gewicht aller Personen mit Spur im ganzen Haus, für jeden Sensor, auch wer nicht in seinem Blick ist;
  Personen ohne Spur zählen nicht. Der Faktor „kein Geist entstanden“ je Frame (`Filter::watch`, `filter/evidence.rs`)
  enthält nur die feste Rate `λ` (und die Geburt der Störquellen, 4.5), nicht diesen Teil.
- **Lebensdauer:** Mischung zweier Exponentialverteilungen, 40 % mit 3 s, 60 % mit 39 s (geschätzt
  per EM 6.10. offline). Fest: Die App lernt sie nicht weiter (bis 0.12.0 tat sie das, 10). Die Lebensdauer ist, was Geister von Sitzenden trennt; aus dem eigenen
  Urteil gelernt, folgte sie den Personen, die der Filter für Geister hielt.
- **Ende der Spur** (`ghost_end` in `filter/evidence.rs`): Der Sensor gibt eine gehaltene Spur auf, wenn er sie nicht
  wiederfindet (4.1). Bei einer Person ist das „u s nicht wiedergefunden“, S(u). Beim Geist ist die
  Quelle entweder noch da und nicht wiedergefunden, S_D(u) S(u), oder sie verschwand bei τ < u, bevor
  sie wiedergefunden wurde, ∫ f_D(τ) S(τ) dτ (konkurrierende Risiken, Kalbfleisch & Prentice 2002 Kap.
  8). Ohne Halten oder durch Datenverlust geendet: für Person und Geist gleich (kein Faktor). Bis 0.10.0 bekam der Geist
  S(u) und dazu `1 − S_D(u)`, als müsse die Quelle sterben *und* nicht gefunden werden: Am Ende jeder
  Geisterspur wurde „Person“ um 1/(1 − e^(−u/L)) wahrscheinlicher, bei 1,5 s gehalten und L = 39 s
  etwa 26-mal (10).
- **Quelle** (`ghost_source` in `filter/mixture.rs`): eine Gauß-Mischung wie die einer Person, mit demselben Versatz
  (4.1) und derselben Dynamik (3.1, 3.2), aber ohne Wände und Türen. Ihr Ort beginnt ohne Vorwissen bei der ersten
  Position (die ganze Unsicherheit von z liegt auf x), je zur Hälfte stehend und gehend. Sie wird nach einem Update
  nicht auf die Sicht beschnitten (4.1), und kein Sensor gewichtet sie mit „keine neue Spur“. Geist und Person
  unterscheiden sich nicht durch ein anderes Rauschen der Spur.
- **Warum keine Karte:** Bis 0.54 lernte jeder Sensor je Zelle (0,4 m) eine Rate aus dem Urteil des Filters
  (J_Luber2014 Kap. 6, J_Park2020 Gl. 37–41). Sie bildete ab, wo Menschen sind, nicht wo Reflexionen stehen: Hält der
  Filter zwei dicht Sitzende für eine Person, ist jede zweite Spur daneben ein „Geist“, am Bett bis 46-mal die
  Prior-Rate. J_Luber2014 Kap. 5.3.2 und 6.6 lernt seine Karte deshalb aus annotierten Daten ohne Personen und
  getrennt vom Filter; die Korrektur nach J_Park2020 bremst die Rückkopplung nur. Ohne Lernen gibt es keine
  Rückkopplung. Auf den Aufnahmen 6.–14.10. waren feste Rate und Karte nicht zu unterscheiden (30.10.: 44 min Licht
  anders in 96 h bei einem Rauschboden von 34–56 min).
- **Geister hängen an Personen:** Im leeren Haus (6,2 h, 7 Sensoren) begannen überhaupt nur 2 Spuren, 0,05 je
  Sensorstunde, also etwa 3 % der Rate; 62 % der Geistermasse lagen ≤ 1 m neben einem gemessenen Ziel (Zufall 9 %).
  Dieses Ziel misst aber fast immer ein *anderer* Sensor (0,49; derselbe 0,08, wie der Zufall): Geburten neben einer
  Person, die der Sensor noch nicht hat, nimmt schon „die Spur gehört ihr“ auf (`ρ·N(z; x)` gegen `λ`, um
  Größenordnungen mehr). Den Ort der Personen nutzt die Geisterrate nicht, nur ob jemand geht (oben). Ein Kern
  `ν Σ r_i N(x; x_i, (0,4 m)²)` (J_Park2020 Gl. 16) und eine Rate je erwarteter Person im Blick oder im Haus waren auf
  den Aufnahmen 6.–14.10. (30.10.) nicht messbar besser als die feste Rate. Mit der niedrigen Rate des leeren Hauses
  für Blicke ohne Person wird ein Geist in einem leeren Raum zur Person (Esszimmer 7.10. 22:20: 6,3 min Licht leer).
  Neu gemessen 10.11. mit 0.63 (10 „Geister brauchen eine Quelle“): im Code als `ghost_near`, seit 0.64 an (ν 5·10⁻⁴ /s, σ 0,5 m; Grundrate 3 % der festen Rate).
- **Offen:** Auf den Aufnahmen 6.–14.10. (30.10.) gewann die feste Rate keine Sitzenden zurück: Sitzende mit Spuren von
  Minuten blieben „Geist“ (9, „Ein Sitzplatz“); ohne Geister wären etwa 30 min weniger Sitzende verloren gewesen, dafür 85 min mehr
  Licht ohne Person.

### 4.3 LD2410C
Gemessen wird seine **Energie je Entfernungsring** (0,75 m Schrägentfernung, `ld2410/`): bewegt in Ring
0–8, ruhig in Ring 2–8 (ruhig 0 und 1 sind immer 0), 0–100, 16 Zahlen je Frame. Seine Flags und die
gemeldete Entfernung zählen nicht (10). Gemessen 6.10. (4,3 h, 5 Sensoren, `tools/ld2410/`; die Aufnahmen sind
seit 31.10. gelöscht), Bezug die Ziele des LD2450 im selben Gehäuse (der unterdrückt Sitzende und hat Geister: nur
zum Bedingen):
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
  | bewegt, geht | 178 | 1,80 | 0,52 m | 0,67 m | 0,48 | 1,50 m |
  | bewegt, steht | 63 | 2,52 | 0,41 m | 0,55 m | 0,27 | 1,34 m |
  | ruhig, geht | 949 | 2,19 | 0,58 m | 1,44 m | 1,00 | 1,25 m |
  | ruhig, steht | 574 | 2,03 | 0,19 m | 0,77 m | 0,40 | 1,70 m |

  *bewegt, geht* neu gemessen 8.11. (seit 0.63; `dev/personenzahl` `fitprof.py`, dieselbe Form, Frames mit genau einem
  gehenden Ziel im Gehäuse, |v| ≥ 0,3 m/s, weder gehalten noch eingefroren, 7.–8.11., 41 883 Frames, Gamma(2,5), 100
  zensiert, gelernter Hintergrund): Log-Likelihood +1,3 je Frame gegenüber 6.10. (159 / 2,16 / 0,41 / 0,57 / 0,47 / 1,26),
  in keinem Sensor schlechter (Küche +4,1, Flur +1,5, Wohnzimmer +0,5). In Zeiten mit einer Person zu Hause (24 665 Frames)
  166 / 1,71 / 0,55 / 0,69 / 0,48 / 1,43. In 2–4 m gibt ein Gehender damit 1,6–2-mal so viel wie mit dem Profil vom 6.10.
  Die übrigen drei Profile, ebenso neu gemessen (eine Person zu Hause), passen besser: ruhig, geht +1,3 je Frame (312 /
  1,46 / 1,72 m / 2,70 m / 0,92 / 1,49 m, viel breiter), stehend bewegt +0,19, ruhig +0,23. Nicht übernommen: an einem
  Abend (8.11.) ohne Wirkung, über alle Fenster nicht geprüft.
  n ≈ 2: eine Amplitude (Radargleichung). Der Schweif ist kein Sonderfall eines Raums (Arbeitszimmer
  allein und die übrigen vier: τ 0,42 / 0,39, ℓ 1,59 / 1,67 m). Nach dem Winkel flach bis 45–60°.
  Die Amplitude einzelner Aufenthalte streut um das Profil (Gewinn g auf dem Profil je Aufenthalt einer Person
  ≥ 30 s; nur Frames, deren LD2450-Ziel weder gehalten noch hinter einer Wand ist, `tools/ld2410/amplitude2.py`):
  innerhalb eines Sensors log g Streuung 0,47 ruhig (6.10. 18–23 Uhr, 88 Aufenthalte) bzw. 0,25 (7.10. 6–8 Uhr, 39),
  als Gamma(β, β) β 5–16; ruhig und bewegt derselbe Gewinn (0,82–0,88); mit der Wiederfinderate des LD2450
  innerhalb Sensor und 1 m Entfernung Spearman 0,37 bewegt / 0,47 ruhig. Je Sensor (Mittel von log g ruhig,
  beide Tage): Arbeitszimmer −0,2 / +0,2, Esszimmer +0,3 / −0,1, Flur −0,2 / +0,3, Küche +0,9 / +1,5 (je ein
  Aufenthalt), Wohnzimmer +1,0 / +1,1 (alle in 5–7,5 m). Die Abweichung liegt vor allem im Schweif hinter der
  Person (`tools/ld2410/shape2.py`, gemessen/Profil je Abstand des Rings hinter der Person: Hauptkeule 0,9–1,3 in
  Arbeitszimmer, Flur und Küche; Schweif Küche ×2,5–4,6, Flur ×1,5–2,4, Arbeitszimmer ×1–1,3, Esszimmer ×0,5–0,7;
  im Wohnzimmer alles ×1,5–5). Vorbehalt: Bezug ist „genau ein LD2450-Ziel“, und der LD2450 unterdrückt Sitzende;
  wo jemand sitzt (Ess-, Wohnzimmer), zählt dessen Energie mit. Nach dem Winkel kein beständiger Verlauf (6.10.
  +0,17 je 10°, 7.10. 0,0). Im Modell ist davon nur die Amplitude je Aufenthalt (unten; Maßstab und Schweif je
  Sensor: 10).
- **Zeit:** Die Ruhig-Energien folgen einer Person mit etwa 2 s (zwei Fälle: Kommen und Gehen; nah
  gekappt bei 100, darum dort 3 s länger voll). Korrelationszeit des Log-Likelihood-Verhältnisses „Person
  / niemand“: 4 s bewegt, 13 s ruhig.
- **Gemeinsamer Pegel:** Alle Ringe einer Art schwanken zusammen (Korrelation zwischen Ringen ohne
  Person 0,2–0,7; Streuung des gemeinsamen log-Pegels je Sekunde 0,14–0,27 bewegt, 0,09–0,18 ruhig);
  dazu Breitband-Schübe (alle Bewegt-Ringe 15–30 für etwa eine Sekunde).

Das Modell, je Sensor:
- **Messung:** jede Energie ~ Gamma(Form α, Mittel μ), gekappt bei 100 (`P(e ≥ 99,5)`); α 2,5 bewegt, 2,0
  ruhig (gemessen 2,4–3,0 / 1,6–2,4). Die Energien sind ganze Zahlen; eine 0 zählt als 0,5. Ein Frame mit lauter
  Nullen ist das „kein Wert“ der Firmware und zählt nicht. Die Form der Likelihood je Auflösungszelle wie bei
  Track-before-detect (Salmond & Birch 2001; Boers & Driessen 2004), Amplitude als Merkmal wie Lerro &
  Bar-Shalom 1993.
- **Mittel = (Hintergrund + Summe der Personen) × gemeinsamer Pegel** (inkohärente Überlagerung,
  superpositionaler Sensor): `μ_g = u · (b_g + w_m·M_g + (1 − w_m)·Σ_i S_g(x_i))`. S ist das Profil oben
  mal Strahl mal Sicht. Der Strahl ist voll bis 50° neben der Achse und fällt linear auf 0 bei 70° (darüber
  angenommen); r ist unten bei 0,3 m begrenzt. Die Sicht: Wände wie beim LD2450, der Anteil der Person, den der
  LD2450 im selben Gehäuse ohne Wand dazwischen sehen könnte, über ihren Versatz gemittelt wie in 4.1. Ruhig zählt
  das Verzögerte: M ist, was die Personen zuletzt hineingaben (geglättet mit 2 s), w_m sein Anteil im Block; bewegt
  w_m = 0. Auch die Echoquellen (unten) geben ruhig nur (1 − w_m) ihres Profils; in M gehen nur die Personen ein.
- **Blick in einen anderen Raum:** Außerhalb seines eigenen Raums sieht das LD2410C eine Person nur bis 1,5 m hinter einer
  Öffnung (seit 0.64, `ld_other_reach`): Die Sicht einer Stelle in einem anderen Raum ist dort die geometrische, weiter als
  1,5 m von jeder Tür (ihrer Öffnung in der Wand) und jedem Raumteiler 0; das vor dem Mitteln über den Versatz (4.1), also
  mit weichem Übergang. Gemessen 10.11. (`dev/personenzahl/sitz/ldsee*.py`, App-Aufnahmen 31.10.–8.11.): Frames eines
  LD2410C mit genau einer ruhenden Person in seinem Blick (die Ziele aller Sensoren dort innerhalb 1 m, Geschwindigkeit 0);
  Überschuss der ruhigen Ringe 2–8 über den Hintergrund derselben Stunde (Frames ohne jedes Ziel in seinem Blick) gegen das
  Profil (g = 1). Eigener Raum ab 3 m: 0,74 (782 min). Fremder Raum ab 2 m nach Abstand zur nächsten Öffnung: 0–0,5 /
  0,5–1 / 1–1,5 m 0,57 / 0,73 / 1,05 (234 / 168 / 246 min), 1,5–2 / 2–2,5 / 2,5–3 / 3–3,5 m −0,01 / −0,68 / −0,64 / −0,10
  (159 / 475 / 26 / 86 min). Darunter das Sofa im Wohnzimmer aus Sicht des Esszimmers über den Raumteiler (5–7 m, −0,7,
  566 min) und die Schreibtischecke des Arbeitszimmers aus dem Flur durch die Tür (5,5–6,5 m: 0 bei einer Erwartung von
  20–45). Was hinter einer Öffnung weiter im Raum liegt, verdecken Möbel, Türblätter und Haltung, die das Modell nicht
  kennt. Bis 0.63 geometrisch: Eine Sitzende am Schreibtisch sollte dem Flur-LD2410C in 5,3 m 8–12 je ruhigem Ring 6–7
  geben, er sah den Hintergrund, und „Spur = Geist, Person verborgen bei (−2,6 | 6,1)“ gewann um 6–22 nats (Meldung
  10.11.). Erste Fassung (10.11., verworfen): gar keine Sicht außerhalb des eigenen Raums. Sie nahm dem Flur-LD2410C auch
  die Stelle gleich hinter der Arbeitszimmertür, die er gut sieht: Im leeren Haus 7.11. hielt sich dort ab 09:21 eine
  Person mit der Spur eines Geists, an ihr sammelten sich 33 Personen ohne Spur mit r 0,01–0,12, und ein Takt kostete
  das 50- bis 100-fache. Die Kante einer Tür entscheidet nichts: Leon 10.11.: „dass ich die Türkanten auf den mm genau in
  den Plan übertragen bekomme ist etwas utopisch.“ Die Sicht ist über den Versatz gemittelt (σ 0,2–0,35 m, mehr als die
  Unsicherheit des Plans), und die Grenze von 1,5 m liegt im Raum, nicht an der Kante.
  Was dem LD2410C nicht mehr als `FLOOR` gibt, ist außerhalb seines Blicks und wird nicht gewogen (Faktor 1, neutral)
  statt „mit null Energie gesehen“. Wände sind undurchsichtig: Gemessen lässt eine Innenwand (alle gemauert) etwa 4 %
  durch, und ein Wert je Wand erklärte die Raumpaare nicht besser. Einen gemessenen Faktor je Paar „Sensor ← Raum“
  (`ld_view`) gibt es seit 30.10. nicht mehr: Er galt nur für die Sensorlage, unter der er gemessen war.
- **Amplitude je Aufenthalt** (Swerling III: langsam, fest über einen Aufenthalt): Eine Stehende mit
  Spur gibt g·S ab, g ~ Gamma(6, 6) (gemessen β 5–16, s. o.). Der Filter führt den Posterior von g,
  nicht seine Momente: ein Gitter von 9 Zellen gleichen Abstands in log g zwischen dem 0,1-%- und dem
  99,9-%-Quantil, je am bedingten Mittel mit der Masse des Priors in der Zelle (0,21 / 0,30 / 0,40 /
  0,54 / 0,72 / 0,97 / 1,28 / 1,70 / 2,28 mit 0,004 / 0,013 / 0,041 / 0,108 / 0,211 / 0,282 / 0,228 /
  0,096 / 0,018; ein Punktmassenfilter über g wie die Kacheln über den Ort, 0_Arulampalam2002 Abschn.
  II-B). Mit 3 Punkten erklärte eine Person, die ein Drittel des Profils zurückgab, die Energie schlechter als
  eine Echoquelle mit ihrer Stufe 0,3 (10, „Meldungen 7.10. abends“). Neu bei jedem Stehenbleiben, innerhalb mit
  1/(600 s) neu gezogen, **zusammen mit κ**: Eine Haltung, die wenig zurückwirft, sieht auch der LD2450 seltener
  (oben: Spearman 0,37 bewegt / 0,47 ruhig zwischen g und seiner Wiederfinderate). Unabhängig
  gerechnet zählten beide Hinweise gegen eine solche Sitzende doppelt. Der
  gemeinsame Prior von κ und g ist eine Gauß-Copula (Nelsen 2006) mit der Korrelation der
  Normal-Scores ρ = 2 sin(π ρ_S / 6) = 0,44 aus dem Mittel ρ_S = 0,42 (Kruskal 1958), auf den Zellen
  beider Gitter (5 × 9, die Ränder exakt die Priors Gamma(3, 3) von κ, 4.1, und Gamma(6, 6) von g); E[g | κ]
  0,60 / 0,72 / 0,86 / 1,05 / 1,29. Die Gauß-Mischung führt die gemeinsame Verteilung (5 × 9 Zahlen).
  Vorbehalt: Gemessen ist die Kopplung an die Wiederfinderate, nicht an κ selbst. Gehende: g = 1.
  **Personen ohne Spur** (`Amp` in `filter/densities.rs`): *Das Ende einer Spur ist nicht das Ende des
  Aufenthalts.* Die Kacheln führen je Stufe von κ und Kachel die bedingte Verteilung von g auf den neun
  Stufen des Gitters und einer zehnten, g = 1. Ein Aufenthalt, dessen Amplitude eine Spur gemessen hat, behält
  ihren Posterior dort, wo er steht (Swerling III: g fest über einen Aufenthalt; im PMBM ist die Dichte
  einer Bernoulli der ganze Zustand der Person, der Wechsel Gauß → Kacheln, 5.4, ändert nur die Darstellung,
  B_GarciaFernandez2018 Gl. 31). Neu gezogen wird g nur mit κ (1/(600 s)) oder mit einem neuen Aufenthalt,
  und was so ohne Messung beginnt, hat g = 1 (das Mittel des Priors; der volle Prior auch für nie
  gemessene Aufenthalte erfindet Personen, 10 „Sitzende im Bad und auf dem Sofa“). Gewogen wird eine Kachel
  Stufe für Stufe nur, wo mehr als 10⁻⁴ Personen mit gemessener Amplitude stehen (`ld_tile_mass`), sonst an
  ihrem Mittel (ohne Gemessenes: g = 1). Bekommt die Person wieder eine Spur,
  bringt sie ihr g mit (der Teil g = 1: der Prior gegeben κ). Gemessen (9.10., Aufnahmen gelöscht; Profil einer
  Stehenden am Ort, Maximum-Likelihood je 30 s, ruhige Ringe 3–8): Sitzende im Bad an der Westwand g 0,35 (10–90 %
  0,21–0,61, ohne LD2450-Ziel 0,27), auf dem Sofa 0,58 (0,40–1,14; ohne Ziel 0,46); je Platz streut
  log g um 0,3–0,6 (Gamma(6, 6): 0,43), zwischen den Plätzen liegen die Mediane bei 0,3–2,9 (alle Stillen in
  Bad, Wohn- und Esszimmer, 881 Blöcke: Median 0,76, 10–90 % 0,24–2,4). Mit g = 1 widerlegte das LD2410C
  eine Sitzende im Bad in Sekunden, sobald ihre Spur endete (9 Punkt 1).
- **Über die Existenz einer Person, neben der jemand steht, entscheiden die Energien nicht** (seit 0.65; `ld_exist` 0,
  `ld_alone` 1, `together` in `filter/ld_weigh.rs`). Wo eine Person ohne Spur ist, wiegen die Energien wie oben ihren
  Ort und die Hypothesen. Ihre Existenz r ändern sie nur mit dem Anteil `1 − Nähe`: Nähe = wie sehr eine andere Person
  der Hypothese (mit Spur, oder ohne mit r ≥ 0,5: eine der Anzeige, 5.5) bei ihr steht, `min(1, Σ r·e^(−ln 2·d²/1,5²))`
  über die Abstände d ihrer mittleren Orte (bei 1,5 m die Hälfte; ab 1,4 m löst der LD2450 Ruhende voneinander, 4.1), gemischt über die
  Hypothesen, die sie halten. Zwei, die zusammen sitzen, sind dem LD2410C eine Quelle: Es kann sie nicht zählen und
  entscheidet nicht, dass es die zweite nicht gibt. Steht niemand bei ihr, entscheidet es wie bisher (Bayes): Liest es
  nur den Hintergrund, ist sie in Sekunden widerlegt (Test `one_alone_where_the_ld2410c_reads_nothing_is_refuted`),
  ebenso eine zweite 3,5 m entfernt (`two_apart_the_ld2410c_decides_as_before`). Warum: Gemessen geben zwei Sitzende nicht
  mehr Energie als eine (10, „Nacht 11./12.11.“: Sofa 8.11., Betten), eine Person je
  Haltung streut um den Faktor 2–3; das Modell addiert Beträge. Mit der Summe sagte jede Minute, in der das LD2410C so
  viel las wie von einer, dass es die zweite nicht gibt (Test `two_at_one_spot_stay_two_whatever_the_ld2410c_reads`: bis
  0.64 r 0,99 → < 0,01 in 60 s, ohne jede Senke der Energie). Ein Abstand im Raum statt gleicher Entfernung zum Sensor:
  Die erste Fassung deckte jede Person in derselben Entfernung, auch 3 m daneben im Nachbarraum, und hielt so eine
  überzählige Person in der Küche neben der Sitzenden am Esstisch (2.11., 10 „Nacht 11./12.11.“). Nur Personen der
  Anzeige: Schützten auch schwache Einträge (Reste von Geistern, r 0,01–0,3) einander, hielten sie sich gegenseitig, und
  die Rechenzeit im leeren Haus stieg auf das Vierfache (10 „Nacht 11./12.11.“).
- **Gemeinsamer Pegel u** je Block (1 s) und Art: 1/u ~ Gamma(k, k), Form k = 16 bewegt, 50 ruhig (`ld_gain`;
  gemessen, s. o.), gemischt mit 2 % Schüben, 1/u ~ Gamma(4) mit u um 2,5 (angenommen; nur nach oben: ein
  niedriger Pegel darf eine fehlende Person nicht entschuldigen). Konjugiert: für die nicht gekappten
  Energien exakt herausintegriert; die gekappten (100, `P(e ≥ 99,5)`) rechnen mit dem Mittel μ ohne
  den Pegel (`Prepared::mu_part` in `ld2410/likelihood.rs`), eine kleine Abweichung vom Modell.
  Ohne ihn erklärte eine gleichmäßig erhöhte Energie aller Ringe (nachts im Arbeitszimmer ruhig
  doppelt so hoch wie gelernt, Schübe) eine Person am Rand des Strahls.
- **Hintergrund b je Sensor, Ring und Art: von der App gelernt** (`ld2410/background.rs`): Online-EM der
  Überlagerung, der Anteil des Hintergrunds an einer Energie ist im Mittel `e · b/μ` (Richardson 1972;
  Shepp & Vardi 1982), μ aus dem Stand vor diesen Frames (die Energien lernen nicht ihr eigenes Urteil,
  J_Park2020). Die Personen ohne Spur zählen in μ mit: Sonst lernte der Hintergrund Sitzende und Schlafende, die
  der LD2450 selten sieht. Der Preis ist ein Fixpunkt des EM bei `e = b + r·S`: Ein zu niedriger Hintergrund hält
  einen Eintrag und der Eintrag den Hintergrund (10 „Störquellen“); darum zählen sie nach langer Stille nicht
  (unten). Eine gekappte Energie (100) zählt mit dem, was sie war: `b · Q(α+1, x) / Q(α, x)`,
  `x = α · 99,5/μ`, der Anteil des Hintergrunds an `E[e | e ≥ 99,5]` (E-Schritt zensierter Daten,
  Dempster, Laird & Rubin 1977; als 100 gezählt lernte der Hintergrund neben einer Person, die mehr gibt,
  zu wenig, 10 „Lernschleifen“).
  Gelernt wird nur aus der Zeit, in der der LD2450 im selben Gehäuse kein Ziel in Sicht hat (ein
  unabhängiges Signal: wo er etwas sieht, ist es eine Person oder ihr Echo, und dort sagen die Energien
  über b wenig, solange das Profil nicht genau stimmt), und nur, soweit keine Echoquelle an ist
  (gewichtet mit P(keine Quelle) nach diesen Frames): Eine Quelle, die länger an ist, als Echos leben,
  ist eine Person, die der Filter verloren hat, und ihr Profil passt nur ungefähr; was es nicht
  erklärt, wurde Hintergrund. Ohne beides lernte der Hintergrund Sitzende und Schlafende (10
  „Lernschleifen“). Nicht nach dem Urteil des Filters über Personen ohne Spur: Dort halten die Energien
  selbst eine Person, wenn b zu klein ist, und das Lernen stünde still (gemessen: 4 h Licht im leeren
  Schlafzimmer, 10). Nichts gelernt wird (Hintergrund, Anker, ρ), solange „Lernen pausieren“ an ist (10 „Lernen
  pausieren“), und aus den ersten 300 s Frames je Sensor nach „Tracks zurücksetzen“ (`Background::hold`): Wen der
  Filter dann noch nicht kennt, lernte sonst der Hintergrund.
  **Nach langer Stille zählen Personen ohne Spur nicht** (seit 0.61.0): Hielt die Energie selbst einen Eintrag
  (eine Person ohne Spur), erklärte er, was über b lag, b lernte nur seinen eigenen Wert zurück, und der Eintrag
  blieb (3.11. 14:00–15:00: die Waschmaschine im Bad, ruhig Ring 2 um 55 statt 4,5, gelernt blieb 7; kein Ziel des
  Bad-LD2450 in einer Stunde). Darum nimmt μ nur die Personen mit Spur, sobald der LD2450 im selben Gehäuse seit
  `ld_anchor_quiet` (10 min, wie für den Anker) nichts in Sicht hatte: So lange sieht er niemanden, der in seinem
  Blick ist (gemessen 5.11., `dev/personenzahl/luecken.py`: in 46 Aufenthalten einer Person die längste Lücke 240 s,
  im Bad 98 s). Die Stille beginnt mit jedem Ziel dieses LD2450 neu, ebenso mit dem ersten Frame und nach einer Lücke
  in den Daten über 6 s (4.4). Was dann an Energie bleibt, lernt b mit seinem Vergessen (0,7 h), und der Eintrag
  hängt wieder an den Messungen des LD2450. Gemessen (alle Fenster, je mehrere Starts, gegen 0.60): Bad 3.11. 69 → 59
  belegte Minuten, Licht zu viel 464 → 453 Raumminuten, Personen zu viel 167 → 145; leeres Haus, Nächte, Morgen und
  Meldungen gleich. Schneller geht es nicht: b folgt mit 0,7 h, und jedes Ziel des LD2450 (3.11. 14:45 eines an der
  Badtür) beginnt die Stille neu. Der Preis: Wer im Blick eines Sensors länger als 10 min ohne ein Ziel seines LD2450
  sitzt oder liegt, dessen Energie wird nach etwa 0,7 h Hintergrund; ihn hält dann nur noch κ. Bei wachen Personen
  kam das in den Aufnahmen nicht vor (s. o.), bei einer Schlafenden allein im Zimmer kann es vorkommen (zu zweit
  sieht der LD2450 den anderen; Schlafende sind kein Ziel, 1).
  Prior: bewegt Ring 0 13, Ring 1 9, sonst 4,5, ruhig 5 mit dem Gewicht 10 min (gemessen 6.10., 4,3 h; Aufnahmen
  gelöscht). **Vergessen 0,7 h** (gemessen): Der Hintergrund wandert; im Flur springt bewegt Ring 4 morgens von 1,7
  auf 5,1 und Ring 5 von 3,6 auf 1,5 (Stundenmittel ohne LD2450-Ziel im Blick ±60 s) und bleibt so stundenlang. Als
  lokales Niveau-Modell (Irrfahrt von log b plus das Rauschen eines Stundenmittels, aus den zwei Hälften jeder Stunde;
  Harvey 1989) ist das beste exponentielle Vergessen 0,7 h, bewegt und ruhig gleich, über alle sieben Sensoren
  6.–9.10. (je Sensor 0,4–2,5 h; Aufnahmen gelöscht). Mit 6 h (bis 0.26) hing der Hintergrund des Flurs morgens
  stundenlang beim Nachtwert, und eine Person mit kleinem g erklärte den Überschuss (10 „Sitzende im Bad und auf dem
  Sofa“). Seine Zellen sind Entfernungen vom Sensor; er gilt nur unter der Geometrie, unter der er gelernt wurde:
  Bei jeder gespeicherten Änderung von Sensorlage oder Grundriss beginnt er für alle Sensoren neu, mit Anker und ρ
  (`learn/mod.rs`; 9, „Gelerntes und Geometrie“). Gespeichert mit dem Schlüssel der Geometrie.
  **Anker (seit 0.28):** Vergessen wird nicht zum Prior hin (ruhig 5), sondern zum eigenen Niveau des
  Sensors: derselbe Online-EM, mit 1 Tag vergessen und nur aus Frames nach 10 min, in denen der LD2450 des
  Gehäuses nichts sah (ein leerer Raum: Stunden; Sitzende und Schlafende lassen Lücken von Sekunden bis
  Minuten). Ein lokales Niveau, das um das Mittel seines Sensors wandert (stationäres AR(1)-Niveau, Harvey
  1989; der Anker wie ein empirischer Bayes-Prior). Zum festen Prior hin zog das Vergessen in Stunden ohne Lernen
  (der LD2450 sieht nachts jemanden) den Schlafzimmer-Hintergrund von leer 9 auf 5 zurück; den Überschuss im leeren
  Raum erklärten dann unbekannte Personen, deren Intensität ohne Messung wuchs, bis das Licht anging (bis 0.27;
  10 „Lernschleifen“). Der Anker wird mit dem Hintergrund gespeichert; leer (der Prior) beginnt er nur
  mit einer neuen Geometrie und lernt sich dann über Stunden ein.
- **Zeit:** Die Frames werden je Sekunde gesammelt (Gamma: die Summen von Δt, Δt·e, Δt·ln e und Δt der
  gekappten genügen), jeder mit Δt / τ, τ 4 s bewegt / 13 s ruhig (zusammengesetzte Likelihood mit der
  Korrelationszeit als effektiver Stichprobengröße; Varin, Reid & Firth 2011). Ein Frame steht für
  einen Frame des LD2450 (0,089 s, gemessen 0,0891 s an allen fünf). Ohne Anlass (kein LD2450-Ziel,
  beide Flags aus) sendet die Firmware nur alle 5 s einen Herzschlag (4.4); der erste Frame danach
  kommt, weil etwas gestiegen ist. Die Stille davor steht deshalb für die Energien des Frames davor
  (des Herzschlags), wie die nicht gesendeten Frames bis dahin waren; eine Lücke über 6 s sagt nichts
  (10, „Die Stille vor einem Frame“). Folgt ein Frame auf einen ohne Wert (lauter Nullen), steht er selbst für die
  Zeit seit diesem.
- **Verrechnung:** Kein Produkt über die Personen. Je Hypothese die Personen nacheinander (erst die mit
  Spur, dann die ohne, zuletzt die unbekannten), jede gegeben die vorigen mit dem, was sie danach
  hineingeben (exakt für Personen an bekanntem Ort). Jede Person (Kacheln bzw. Komponenten an ihrem
  Mittel) wird dann mit `ℓ(e | Rest + S(x)) / ℓ(e | Rest)` gewichtet, der Rest = alle anderen, gemischt
  über die Hypothesen, die sie halten (Marginale wie bei JIPDA; was andere erklären, sagt über diese
  Person nichts). Eine zweite Person am selben Ort bekommt so nur, was die erste nicht erklärt;
  gekappte Werte (100) sprechen dabei nur für mehr Energie, nie dagegen (geprüft 7.10., 10). **Die unbekannten Personen: höchstens eine von ihnen im
  Blick**, gerechnet wie eine Person ohne Spur (ein Bernoulli mit r = ihrer Masse M im Blick; `M' = M L̄ /
  (1 − M + M L̄)` ≤ 1, wobei L̄ das mittlere Verhältnis über diese Masse ist, per Momentenabgleich zurück in
  die Intensität); wer hinter einer Tür oder außer Haus ist, gibt nichts hinein. Das ist, was Energie allein
  belegen kann: dass jemand da ist, nicht wie viele. Sie addiert sich zwar je Person, aber die Amplitude
  einer einzelnen Person streut um mehr als eine weitere Person ausmacht (oben: 881 Blöcke 10–90 % 0,24–2,4,
  Wohnzimmer das Profil ×1,5–5), und wo gekappt ist, sagt sie nach oben nichts. Die Zahl kommt von den Spuren
  des LD2450 und den Türen; dafür gibt es keine Grenze (1, 3.4), nur die Energie legt allein keine zweite
  Person je Blick dazu (10, „Kaltstart“).
- **Echoquellen** (`Echoes`, `ld2410/echoes.rs`): Energie, die von keiner Person kommt (7.10. 01:07:52–01:08:34 in
  der leeren Küche: bewegt Ring 0–1 Schübe bis 100, ruhig Ring 2–4 bis 60–90, kein LD2450-Ziel, niemand
  ging). Wie die Geister des LD2450 (4.2) unterscheidet sie, wie sie beginnt und endet: Je Sensor aus
  oder eine Quelle an – das Profil einer Stehenden auf der Achse in 0,75–6,5 m (Schritt 0,25 m) mal
  8 Stärken 0,2–5 (gleich in log) –, Beginn mit der Rate ρ (gleich über die Quellen), Lebensdauer Erlang(2).
  **So beweglich wie eine Person** (seit 0.60.0): Eine Person ohne Spur liegt auf Kacheln von 0,4 m mit einer
  Verstärkung, deren Gitter (Gamma(6, 6), 9 Stufen) fein ist. Mit dem Schritt 0,5 m und 3 Stärken (bis 0.59)
  erklärte sie gleichmäßige Energie, die zwischen zwei Quellen lag, in jedem Block besser, und das wog mehr als das
  Nicht-Sehen des LD2450 (3.11. 14:00–15:44: die Waschmaschine im Bad hielt eine Person 1,4 m vor dem Sensor, ohne
  ein einziges Ziel; ohne den Bad-LD2410C entsteht sie nicht). Jetzt ist der Schritt kleiner als eine Kachel, und
  eine Stufe (0,46 in log) so groß wie die Streuung der Verstärkung einzelner Aufenthalte (0,47, oben); der Prior
  einer Quelle verteilt sich über mehr Zellen, ρ bleibt. 0,1 m und 12 Stärken (doppelte Rechenzeit) machten im
  leeren Haus 1.11. bei einem Start 12 min Licht im Flur; 0,25 m und 8 Stärken kosten etwa 10 % Rechenzeit.
  **Kurz- und langlebige Quellen** (seit 0.59.0, `ld_echo_long` = [0,11; 245 s]): Eine beginnende Quelle lebt mit 0,89
  Erlang(2) mit Mittel 20 s, mit 0,11 Erlang(2) mit Mittel 245 s (zwei parallele Ketten, Mischung wie bei den Geistern 4.2).
  Gemessen 4.11. über 103 h App-Aufnahmen: Episoden mit der Summe der ruhigen Ringe 2–4 über 3 × ihrem Pegel ohne
  LD2450-Ziel des Sensors ±60 s (ohne Dobby), Lücken < 3 s überbrückt: Mischung zweier Exponentialverteilungen 0,89 mit
  12 s, 0,11 mit 245 s (n = 179; die längste 26 min, die Waschmaschine im Bad 3.11. 12:00–15:55). Mit Lücken < 60 s
  überbrückt 0,93 mit 52 s, 0,07 mit 36 min: Die Waschmaschine zerfällt an der Schwelle in Stücke, die lange Lebensdauer
  liegt also zwischen 4 und 36 min. Mit nur der kurzen Kette (bis 0.58) erklärte nach 20 s nur noch eine Person
  beständige Energie ohne Person (9, „Lang anhaltende Energie ohne Person“). Mit 0,07 × 36 min blieb bei einem von
  sechs Starts im leeren Haus (1.11.) der Flur 5,5 h belegt; mit 0,11 × 245 s in allen 16 Starts der vier
  Leer-Fenster kein Licht.
  Gemessen in der leeren Nacht 6./7.10. 22:56–02:15 (3,3 h, Ruhig-Energie über 3× ihrem Pegel ≥ 3 s, ohne
  LD2450-Ziel ±60 s; Aufnahme gelöscht): Küche 3,6 je Stunde (Dauer Median 10 s, Mittel 15 s, bis 59 s), die anderen
  vier Sensoren keine. ρ lernt die App je Sensor mit dem Hintergrund (Gamma-Poisson: erwartete Zahl
  begonnener Quellen je Beobachtungszeit; Prior 1 je 3 h mit dem Gewicht von 3 h, angenommen). Zahl und Zeit
  vergessen wie der Hintergrund mit 0,7 h; gegen die 3 h des Priors wiegen die Daten also höchstens etwa 0,7 h.
  Gezählt wird nur, wo niemand im Blick ist (beides, Zahl und Zeit, gewichtet mit P(niemand im Blick) vor den
  Frames: Echoquellen sind Energie von niemandem, und neben einer Person zählte, was ihr Profil nicht
  erklärt, als Quelle), und jede Zählung ohne die gelernte Rate beurteilt: die
  Odds „eine Quelle begann“ mit ρ_Prior/ρ statt ρ (J_Park2020 Gl. 37–41). Sonst wäre, was die Rate
  sagt, Beleg für sie selbst (bis 0.20: Simulation, eine Sitzende 1,8 m vor dem Sensor, 0,33 → 0,73
  je Stunde in einer Stunde; jetzt 0,34, 10 „Lernschleifen“).
  Verrechnung: Die Personen wie oben, die Echoquellen zuletzt (für die Gewichte der Hypothesen). Eine
  Quelle ist, was die bekannten Personen nicht erklären: Sie wird gegen die Personen mit Spur, wie diese Frames sie
  gewogen haben, und die bekannten ohne Spur, wie sie vor diesen Frames waren, gewichtet (sonst erklärt eine von der
  Energie hereingezogene Person die Quelle weg); die Unbekannten zählen dabei nicht. Eine Person und eine Quelle
  sind Alternativen (beide selten), gerechnet wie Ziel und Clutter in der
  Bernoulli-Aktualisierung (B_GarciaFernandez2018 Gl. 45: `ρ = e + c`, die Alternative steht im Nenner,
  auf dem Ziel liegt kein Faktor): Jede Person im Blick, mit Spur, ohne Spur und die Unbekannten, bekommt dort
  `ℓ(Person) / Z` und bleibt außerhalb, wie sie ist; `Z = max(1, P(keine Quelle) + Σ P(Quelle) ℓ(Quelle))`, gegeben
  die übrigen Personen (`marginal` in `filter/ld_weigh.rs`). Hält eine Quelle die Energie über ihre
  Lebensdauer hinaus, steht sie nach jedem Block wieder auf „an“, weil im Zustand der Quellen nichts
  sonst sie erklärt; die Vorhersage gibt ihr in der kurzen Kette nur `≈ 0,9` je Sekunde (Mittel 20 s), in der
  langen `≈ 0,99` (Mittel 245 s). Was anhält, wandert in die lange Kette, und eine Person, die gleich gut erklärt,
  wächst gegen sie nur um etwa 1 % je Sekunde statt 11 % gegen die kurze: Was viele Minuten bleibt, wird eine
  Person, was Sekunden bis wenige Minuten dauert, bleibt eine Quelle (gerechnet, nicht gemessen). `Z ≥ 1`, weil
  eine Quelle, die nichts mehr erklärt, kein Beleg für eine Person ist: Ohne die Grenze steigt die Person um das, was
  die Quelle verliert (10, „Echoquellen als Alternative“).
  „Im Blick“ heißt für eine Person mit Spur wie für Kacheln: eine Komponente, die dem LD2410C mehr als
  `FLOOR` gibt; eine Komponente außerhalb wird nicht gewogen (`tile_points`, `mixture_points` in
  `filter/ld_weigh.rs`). Zählte jede Komponente als im Blick jedes LD2410C, mit null Energie, schob jede
  wahrscheinliche Echoquelle irgendwo alle Personen mit Spur im Haus durch eine Tür hinaus (bis 0.25; 10, „Meldungen
  8.10. abends“).
  Exakt gerechnet (Prototyp `tools/ld2410/clutter_hmm.py`, auf den gelöschten Aufnahmen vom Oktober) spricht das
  Ereignis von 01:08 gegen eine Person (log Bayes-Faktor Person + Quelle / Quelle −5,0 über 56 s, höchstens +2,5
  zwischendurch), eine Sitzende im Esszimmer (21:03) dafür (+9,0 nach 40 s, +17,3 nach 2 min).
- Alles in Log-Größen; Verhältnisse auf e^600 begrenzt.

### 4.4 Lücken zwischen Frames
Die Firmware sendet leere Frames (kein LD2450-Ziel, beide Flags des LD2410C aus) nur alle 5 s, den
ersten leeren nach einem vollen sofort. Eine Lücke bis 6 s zählt als beobachtet und leer (für den
LD2410C: mit den Energien des Frames vor der Lücke, 4.3); eine längere sagt nichts.
Ein Neustart der App ist eine solche Lücke für alle Sensoren: Die Personen werden über sie nur
vorgerückt, nicht gewichtet (5.3).
Das Modell rechnet vorwärts: Ein Frame vor seiner Zeit wirkt auf den Stand jetzt, und was es aus dem Fehlen des
Frames schloss (Lücke, Spuren zu Ende), bleibt. Deshalb geht es nicht über Frames hinaus, die nur unterwegs
aufgehalten werden:
- **Zeit eines Frames:** aus der Laufzeit des Boards (`uptime_ms`), verankert an der schnellsten Zustellung
  (Empfangszeit − Laufzeit, kriecht um 1 ms/s nach oben: Drift zwischen Board- und Empfängeruhr, ein langsamerer
  Broker). Ein Frame, der mehr als 2 s zu spät kommt, behält diese Zeit, solange das Modell nicht mehr als 2 s über
  sie hinaus ist; sonst (die anderen Sensoren sendeten, nur er kam zu spät) kommt er zu seiner Empfangszeit, und die
  Uhr des Boards wird dort neu verankert, ebenso nach einem Neustart des Boards (Laufzeit zurück) und für einen Frame
  früher als der Anker (Empfängeruhr zurückgestellt). Ein Frame kommt so höchstens 2 s nach der Zeit, auf die das
  Modell schon ging, weniger als eine Lücke.
- **Zeit des Modells:** Es geht mit den Frames, live nie über den neuesten hinaus. Kommt von keinem Sensor ein Frame,
  hält es an, bis wieder welche kommen, und die Ausgaben behalten ihren letzten Stand; bei einem Stau (HA, Broker oder
  inge hängen, 18.10. etwa 35 s, danach alle Frames auf einmal) werden die gestauten Frames zu ihren Zeiten
  verarbeitet, als wäre nichts aufgehalten worden. Erst nach `STALL` = 60 s ohne jeden Frame (länger als ein Stau,
  alle Boards wirklich weg) gilt die Wanduhr: Dann sind alle Sensoren still, ihre Spuren enden, und ein Raum ist
  nach 60 s *nicht verfügbar* (6) statt nach 30 s. Fällt ein einzelnes Board aus, geht das Modell mit den anderen
  weiter, und es ist still wie oben. Die Wiedergabe wartet ebenso auf ihre Frames: Lief ihre Uhr bei Überlast weiter
  (bis 0.54.2), ging das Modell bis zu 40 min über Frames hinaus, die noch kamen, jeder Sensor schien länger still
  als seine Spuren leben, und jeder späte Frame begann sie neu (25 Personen statt 2).
- Nicht abgedeckt: ein Stau länger als 60 s (die Frames danach kommen zu ihrer Empfangszeit) und ein
  Board, das während eines Staus neu startet (seine Frames nach dem Neustart kommen zu ihrer Empfangszeit, also nach
  den gestauten der anderen, die das Modell dann überholt hat).

### 4.5 Störquellen
**Seit 0.61** (5.11.; 10 „Störquellen“). Ein Objekt, das keine Person
ist und an einem festen Ort immer wieder Spuren eines LD2450 macht: ein Luftentfeuchter
(5.11., Esszimmer-LD2450 auf ein Gerät im Wohnzimmer, 08:23–13:50+, 46 Spuren der App, eine bis 71 min), ein
Spiegelbild. Ohne sie wäre im Modell das einzige dauerhafte Objekt die Person: Ein Geist lebt 3 / 39 s (4.2), also
gewann bei einer Spur von Minuten die Person, und jede weitere Spur an derselben Stelle hing sich billig an sie (5.11.:
stundenlang Licht im leeren Wohnzimmer). Darum eine eigene Objektklasse neben den Personen, die Clutter-Generatoren von
D_Mahler2011 (Abschn. III-A, Gl. 13–18: eigene Geburt, eigenes Überleben, eigene Erfassung; Personen werden nie
Störquellen und umgekehrt), im PMBM wie die Personen (B_GarciaFernandez2018; `filter/clutter.rs`):
- **Gehört zu einem LD2450** und macht nur in ihm Spuren. Die anderen Sensoren und das LD2410C sehen sie nicht, sie geht
  nicht, braucht keine Tür, gibt kein Licht und zählt nicht.
- **Ort:** ihre Spuren sitzen auf ihr wie auf einer Person (4.1: `z = y + c + o + w`, derselbe Versatz); ein
  Kalman-Filter über (y, c, o) je Achse. y zittert wie der Ort einer Stehenden (3.1, 0,02 m/√s), geht aber nie, und wird
  wie eine Person auf die Sicht ihres Sensors beschnitten (4.1 „Wer gemessen wird, ist in Sicht“): Wie beim Geist (4.2)
  unterscheiden sich Störquelle und Person nicht durch ein anderes Rauschen. Mit festem y gewann bei jeder ruhenden Spur
  die Störquelle je Position um etwa 0,016 (0,07 je s): Die Diffusion der Person verbreitert ihre Vorhersage, ein fester
  Ort nicht (Kaltstart einer Sitzenden, die nur ein Sensor sieht: P(belegt) nach 60 s 0,02; mit Zittern bleibt es beim
  Verhältnis der Anfangsdichten, 0,72). Der gemessene Ort des Luftentfeuchters streut weniger als der Versatz einer
  Person (je Achse 0,07 m innerhalb von 10 s, 0,05 m die 10-s-Mittel), aber ruhende Spuren von Personen ebenso
  (0,04–0,08 m und 0,05–0,11 m, Ein-Personen-Fenster 2.–4.11.); ein eigener, enger Versatz der Störquelle (0,047 /
  0,05 m) machte eine Sitzende mit gemessenem Zittern in 10 s zur Störquelle (P(belegt) 0,06).
- **Entstehen:** Eine Störquelle, die der Filter noch nicht kennt, beginnt mit ihrer ersten Spur. Diese Spur kommt
  mit `clutter_birth` = 2,2·10⁻⁷ je m² und s, gleich verteilt im Blick ihres Sensors, keine Karte (im Gleichgewicht
  bekommen so viele ihre erste Spur, wie entstehen; D_Mahler2011 Gl. 13–15); unbekannte Störquellen ohne Spur führt
  der Filter nicht (`clutter_children` in `filter/branch.rs`, `watch` in `filter/evidence.rs`).
  Gemessen in den leeren Fenstern (17 h ohne Dobby, 7 Sensoren, im Mittel 21 m² Blick): zwei Orte, an denen eine Spur
  länger als jeder Geist lebte, der Luftentfeuchter (Esszimmer) und 25 min an einer Stelle 0,3 m vor dem
  Wohnzimmer-Sensor; 2 / (120 Sensorstunden × 21 m²). Unsicher (2 Ereignisse, ein Faktor 3 nach jeder Seite).
- **Lebensdauer** exponentiell, Mittel `clutter_life` = 1,9 h; es stirbt nur eine Störquelle ohne Spur, eine mit
  laufender Spur nicht. 1,9 h ist die Zeit, in der die zwei Orte in den leeren Fenstern da waren (3,4 h und 0,4 h), je
  Geburt (Little). So ist die Dichte der Störquellen beim Start ohne Wissen
  (`clutter_birth` × `clutter_life` × Anteil mit Spur ≈ 1,5·10⁻³ je m²) die gemessene Belegung. Die Maximum-Likelihood mit
  Zensur gab 5,3 h und damit beim Start die dreifache Dichte.
- **Erfassung:** Ohne laufende Spur beginnt ihr Sensor eine mit `clutter_acquire` = 0,115 /s (gemessen am Luftentfeuchter:
  21 Lücken zwischen den Spuren der App, zusammen 182 s, Median 5 s; 24 weitere Spuren begannen, während die vorige
  noch gehalten wurde), überall gleich (die feste Erfassung bei D_Mahler2011), weniger neben seinen laufenden Zielen
  (Auflösung, 4.1). Eine Spur auf ihr dauert im Mittel 420 s. Das gilt beim ersten Frame eines Sensors und nach einer
  Lücke (4.1): für die Spuren, die dann schon laufen, und für die bekannten Störquellen ohne Spur, die mit dem
  stationären Anteil `1/(1 + a · 420 s)` keine haben (`clutter_censor`).
  Verlieren, Wiederfinden (auf ihr wie der Geist auf seiner Quelle, mit dem Kern um die gehaltene Stelle) und das
  Ende einer Spur wie bei einer Person: Das Ende sagt nichts, sie bleibt.
- **Was sie von einer Person trennt**, kommt allein aus dem Modell: Eine Person an der Stelle sähen die anderen
  Sensoren mit Sicht (4.1), sie gäbe dem LD2410C Energie (4.3) und kam durch eine Tür (3.3, 3.4); eine Störquelle sieht
  nur ihr Sensor, und sie entsteht irgendwo. Was sie von einem Geist trennt: Sie lebt Stunden, und an derselben Stelle
  beginnt nach Sekunden wieder eine Spur. Was gegen sie spricht: Ihr Sensor findet sie nicht wieder (mit 0,115 /s, nach
  60 s ohne Spur e⁻⁷). Eine Sitzende, deren Spur minutenlang ausbleibt (4.1, κ), ist deshalb keine.
- Geteilt mit den Personen: dieselben Messungen, dieselbe Zuordnung (5.1), dasselbe Zusammenlegen (5.6). Nicht gespeichert:
  Nach einem Neustart der App (5.3) ist keine Störquelle bekannt.

## 5. Inferenz

Deterministisch: Was diskret ist und wenige Werte hat, wird aufgezählt, nicht gezogen. Das Ergebnis
hängt nicht davon ab, wie die Zeit zerlegt wird (Test).

**Takt:** Alle Personen werden gemeinsam vorgerückt und mit „keine neue Spur“ gewichtet, sobald
0,2 s (`max_step`) seit dem letzten Mal vergangen sind (`Tracker::step`), und sofort vor jedem
Ereignis, das alle betrifft: neue Spur, wiedergefundene, endende, Start, Datenlücke oder eine Rückgabe
an die Intensität (5.5). Die Quellen der Geister rücken mit vor, aber ohne Wände und Türen und ohne
Gewicht (4.2). Eine gemessene Position rückt nur die
Gauß-Mischungen vor, zu denen ihre Spur gehört; jede hat dafür ihre eigene Zeit. Zwischen zwei
Takten wirkt „gehalten, nicht wiedergefunden“ auf den Stand des letzten Takts, und die Ausgaben
werden nur nach einem Takt oder Ereignis neu berechnet (bis 0,2 s alt). Vorher (0.8) wurde bei jedem
Frame jedes Sensors alles vorgerückt: etwa 23-mal je Sekunde, ein Hauptteil der Rechenzeit.

### 5.1 Hypothesen über die Spuren
- Welche laufenden Spuren zu derselben Person gehören und welche Geister sind, ist eine diskrete
  Hypothese mit exaktem Gewicht (Datenassoziation eines PMBM, B_GarciaFernandez2018; δ-GLMB,
  B_Reuter2014).
- Eine neue Spur verzweigt jede Hypothese: Geist; eine bekannte Störquelle ihres Sensors ohne Spur (mit ihrem r und
  `1 − e^(−a·Δt)`, mal der Dichte von z; Δt die Zeit seit dem letzten Frame des Sensors, höchstens eine
  Frameperiode) oder eine neue (`clutter_birth`, gleich verteilt wie ein Geist; 4.5);
  eine Person mit Spur, außer sie hat schon eine messende Spur desselben Sensors (eine gehaltene zählt
  nicht); eine bekannte Person ohne Spur (mit ihrem r, 5.5); eine unbekannte
  (5.5). Eine wiedergefundene: ihr Eigentümer (Person, Geist oder Störquelle); eine andere Person nahe der Stelle
  (mit Spur, bekannt ohne Spur oder unbekannt); Geist/Reflexion (`filter/branch.rs`).
- **Übergabe, Spur-zu-Spur** (Zweig `tuer-fusion`, 11.11.; `fusion`, Standard aus, geprüft mit [0,6, 0,75 m, 0,75 s];
  nicht übernommen, 10 „Tür: Spur-zu-Spur“): Ein Sensor verliert eine
  Gehende und führt ihre Spur weiter (gehalten, „coast“: seine eigene Vorhersage mit der letzten Geschwindigkeit, 4.1),
  während die gemessene Spur eines anderen Sensors, die die stärkste Hypothese für die eines Geists (oder einer
  Störquelle) hält, mit ihr läuft: gehend (radial ≥ 0,3 m/s), ≤ 0,75 m von der weitergeführten Stelle, ohne Unterbrechung
  ≥ 0,75 s (Lücken bis 0,5 s). Dann verzweigt einmal je Lauf jede Hypothese, in der die zweite Spur Geist/Störquelle ist
  und die weitergeführte einer Person ohne messende Spur dieses Sensors gehört: mit 0,4 bleibt sie, wem sie gehört (ihre
  Messung wie sonst), mit 0,6 gehört sie ab jetzt dieser Person (Kalman-Update mit ihrem Versatz wie eine neue Spur auf
  ihr, 4.1); was sie vorher maß, bleibt bei der Quelle. Hypothesen ohne solche Person messen sie wie sonst. Die Form ist
  die Spur-zu-Spur-Zuordnung der Objekt-Fusion (E_Aeberhard2017 Abschn. 3.3), hier an einem Ereignis statt in jedem
  Zyklus; der Lauf ist das Tor, die Gewichte danach sind exakt. Wozu: Der LD2450 nimmt eine gehaltene Spur auch auf einer
  Person wieder auf, die noch 1,5–2 m entfernt ist (4.1 Wiederfinden, Kern 0,7 m); dann bleibt sie die des Geists, und
  die Person ist nach dem Ende ihrer anderen Spur „verloren“, obwohl ein Sensor sie misst (10, „Tür: Spur-zu-Spur“).
  Nicht betroffen: Ruhende (beide müssen gehen) und zwei Spuren, die beide gemessen werden (dort entscheiden ihre Orte
  wie überall; zwei, die zusammen zum Sofa gehen, wurden sonst eine Person, 10).
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
- **Keine laufende Spur verliert eine Alternative ganz** (seit 8.10. abends, `cut_hyps` in `filter/merge.rs`): Ob eine Spur
  ein Geist ist oder einer Person gehört, ist die Frage dieser Spur (in einem spurorientierten PMBM ihr
  eigener Bernoulli, B_GarciaFernandez2018 Abschn. IV; ebenso hält JIPDA je Spur ihre Existenz). Was ihre
  späteren Messungen dazu sagen (ein Geist lebt 3 / 39 s, 4.2; eine Person bleibt), wirkt nur auf eine
  Alternative, die noch da ist. Sagen alle behaltenen Hypothesen über eine laufende Spur dasselbe
  („Geist“ oder „Person“), bleibt von den verworfenen die stärkste, die das andere sagt. Mit Störquellen gibt es drei
  Antworten (Person, Geist, Störquelle, 4.5): Von den verworfenen bleibt je fehlender Antwort einer laufenden Spur die
  stärkste; die Grenze von 12 wird dafür um höchstens das Doppelte der Zahl der laufenden Spuren überschritten. Bis 0.25 fiel die Alternative
  „Person“ einer Spur, die dort begann, wo der Filter niemanden kannte, schon bei der Geburt unter die
  Schwelle; danach sagten alle Hypothesen „Geist“, der Faktor der Lebensdauer (`ghost_life`) kürzte
  sich bei der Normierung heraus, und die Spur blieb ein Geist, wie lange sie auch gemessen wurde (das
  Bett 8.10. 00:29–06:14, der Schreibtisch 7.10. 08:59–09:49; 10, „Meldungen 8.10. abends“).
- Alte Messung (Python-Kern bis 0.21, Aufnahme gelöscht, Lichtfehler nach der damaligen, ungenauen
  Wahrheitstabelle): höchstens 12, solange über 10⁻⁷ des stärksten (Abschneiden nach
  Gewicht, B_Vo2017). Auf den 11 h aus 3.1 griff die Grenze von 12 bei 8 % der Schnitte (461 von 5588), im
  Mittel blieben 4,7 Hypothesen. Log-Evidenz gegenüber 12 ab 10⁻⁷ / Lichtfehler (fälschlich an von
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

  Die Evidenz war nicht konvergiert und hing nicht monoton von den Grenzen ab. Der Unterschied
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
`filter/mixture.rs`, `motion/imm.rs`. Gilt, solange eine Person mindestens eine laufende Spur hat (gemessen oder gehalten).
Zwei Komponenten *steht* / *geht*, je ein Kalman-Filter über Position, Geschwindigkeit und je Spur
(c, o); *steht* führt die Wahrscheinlichkeiten der Aufenthaltsarten und Erkennbarkeitsstufen. Je
Schritt (Buch_SarkkaSvensson2023 S. 352; B_Li2019 Gl. 26–33):
1. Übergänge *steht → geht* (`Σ p_l (1 − e^(−λ_l Δt))`) und *geht → steht* (`1 − e^(−μΔt)`), je
   Ziel-Betriebsart per Momentenabgleich zu einer Komponente zusammengefasst.
2. Lineare Vorhersage je Komponente (3.1 bzw. OU-Näherung 3.2). **Wände in der Vorhersage**
   (`motion/imm.rs`): Die Sigma-Punkte (Kubatur, 2n = 16 Punkte; Buch_SarkkaSvensson2023 Alg.
   8.11/8.16) von Position, Geschwindigkeit und Rauschen der Komponente *geht* vor dem Schritt werden
   linear bewegt; wer dabei eine Wand kreuzt, endet an ihr gespiegelt, die Geschwindigkeit auch.
   Danach Momentenabgleich, die Versätze der Spuren folgen über ihre Regression. Was schon an oder
   hinter einer Wand liegt, bleibt (anders als das verworfene Abschneiden, 10). Für Ausgaben und den
   Wechsel auf Kacheln (5.4) zählt die Masse hinter einer Wand, vom Mittel aus gesehen, nicht
   (außer es läge alles dahinter). Mit einer Messung der Spur folgt das Kalman-Update, danach wird
   die Position auf die Sicht des messenden Sensors beschnitten (4.1).
3. Nicht-Erfassung durch andere Sensoren: Faktor je Komponente, als Erwartung über ihre Position
   (`E[e^(−r(x)Δt)]`), ebenso die Rate einer neuen Spur und die Sicht beim Wiederfinden (unter dem
   Kern gewichtet): Unscented-Transformation der Position je Achse (diagonale Kovarianz, n + κ = 3:
   Mitte 1/3, ±√3 σ je 1/6; ebd. Gl. 8.70–8.71). Punkte hinter einer Wand, vom Mittel aus gesehen,
   werden an ihr gespiegelt (Wände reflektieren). Das LD2410C sieht die Komponenten an ihrem Mittel.
4. **Durch eine Tür** in einen Bereich ohne Sensor: Die Komponente *geht* verliert dorthin mit der
   Rate der Türkacheln (5.3), gewichtet mit ihrer Masse dort (jede Türkachel als ihr kleiner Gauß).
   Das wandert in einen Teil „durch eine Tür“ (eine Dichte über Kacheln und Bereiche mit Gewicht a).
   Eine Messung der Spur sagt „in Sicht“ und streicht ihn. Zwischen zwei Räumen mit Sensor gibt es
   keinen solchen Teil: Dort ist eine Tür nur eine Lücke in der Wand.

### 5.3 Personen ohne Spur: Kacheln
`world/tiles.rs`, `filter/densities.rs`, `motion/walk.rs`, `motion/stay.rs`, `filter/step.rs`. Punktmassenfilter (0_Arulampalam2002 Abschn. II-B) über Kacheln statt über
ein gleichmäßiges Raster:
- **Kacheln:** Quadrate von 0,4 m, an den Grenzen der beobachteten Räume geschnitten (ein Quadrat über
  zwei Räume sind zwei Kacheln). Eine Wand innerhalb eines Raums schneidet nicht: Eine Kachel kann auf
  beiden Seiten liegen und ihr Schwerpunkt in der Wand. Innerhalb einer Kachel wird der Ort nicht unterschieden: gleichverteilt. Wo ein Ort
  gebraucht wird (eine Spur beginnt, Gehen durch eine Tür), zählt die Kachel als die
  Gauß-Verteilung mit ihrem Schwerpunkt und ihrer Streuung (dieselben ersten zwei Momente).
  Alte Messung (Python-Kern, 6.10., 20 min einer gelöschten Aufnahme; CPU / Log-Evidenz): 0,6 m an
  den Sichtgrenzen der Sensoren geschnitten 53 s / 17039,6; einfache Quadrate 0,6 m 50 s / 17030,1,
  0,4 m 55 s / 17039,5, 0,3 m 62 s / 17036,6; 0,4 m mit der Gleichverteilung exakt integriert
  (Fehlerfunktion) 62 s / 17032,2. Der Schnitt an den Sichtgrenzen und das exakte Integral änderten
  dort kaum etwas. Die Wohnung hat heute etwa 700 Kacheln (Kommentar zu `MAX_TILES` = 4000).
- **Raten** (Erfassung, Wiederfinden) sind je Kachel das Mittel über ihre Punkte eines 0,2-m-Rasters:
  die Projektion der Likelihood auf den gröberen Zustand (G_Liao2003 Gl. 2).
- **Zustand:** `geht[Kachel]`, `steht[Art l, Stufe κ, Kachel]`, `Bereich[k, Alter]`, `außer Haus` und
  `amp[κ, Stufe a, Kachel]`, die gemessene LD2410C-Amplitude des Aufenthalts (4.3, 5.4). Altersklassen:
  0, 2, 4 … 131 072 s (36,4 h), die Grenzen verdoppeln sich, die letzte reicht bis zum Doppelten. Das
  Altern ist exponentiell: Eine Klasse geht mit der Rate 1/Breite in die nächste über.
- **Gehen** zwischen Kacheln als Diffusion (3.2) in finiten Volumen: von Kachel i zum Nachbarn j mit
  `D · L_ij / (A_i · d_ij)` (gemeinsame Kante L, Fläche A, Abstand der Schwerpunkte d), durch eine
  Tür in einen Bereich ohne Sensor oder nach draußen (ins Treppenhaus, 2) ebenso, mit d = doppelter
  Abstand zur Tür. Wände sind keine
  Kante. Ein Takt von 0,1 s Gehen ist das Matrixexponential des Generators (Anteile unter 10⁻¹² als
  0); gegangen wird nur in ganzen Takten, der Rest eines Schritts wartet auf den nächsten. Anhalten
  mit μ, Aufstehen mit λ_l und neues κ (3.1/3.2) rechnet jeder Schritt (höchstens 0,2 s) vorher
  getrennt (`motion/stay.rs`): eine Aufteilung der Operatoren, kein gemeinsames Matrixexponential.
  Eine Tür zwischen zwei Räumen mit Sensor ist eine Kante wie jede andere.
- Nicht-Erfassung, Nicht-Wiederfinden und die Rate einer neuen Spur sind exakte Summen über die
  Kacheln.

**Neustart der App:** Was über die Personen bekannt ist, speichert die App alle 10 Minuten und beim
Beenden (`people.json`, `Filter::people_state` in `filter/mod.rs`): je Hypothese ihr Gewicht, die
bekannten und die unbekannten Personen als Dichten über den Kacheln. Das Speichern ändert am Modell
nichts (es rechnet auf Kopien). Laufende Spuren überleben den Neustart nicht (die
nächsten Frames der Sensoren sind ein Start, 4.1): Personen mit Spur gehen auf die Kacheln wie am Ende
ihrer letzten Spur (5.4), Geisterspuren enden, und Hypothesen, die danach dasselbe sagen, werden eine
(5.6). Ohne laufende Spuren sagen alle dasselbe: Gespeichert wird eine Hypothese, verschieden viele
Personen sind darin Bernoullis mit r < 1. Bekannte Störquellen (4.5) werden nicht gespeichert; nach
einem Neustart sind sie wieder unbekannt. Beim Start ist dieser Zustand der Prior, die PMBM-Rekursion
geht von ihm aus weiter (B_GarciaFernandez2018). Die Zeit dazwischen ist eine Datenlücke (4.4): Alle
werden mit der Dynamik aus 3 vorgerückt, nichts gewichtet; die letzten 120 s wie immer, davor in
Sprüngen von 15 s (`leap` in `motion/walk.rs`; höchstens 2000, bei einer längeren Lücke, ab gut 8 h,
werden die Sprünge länger: Vorlauf / 2000): Wer aufsteht, geht seinen Weg in einem Sprung ganz,
bis er anhält oder durch eine Tür geht (wo ein Weg endet: `μ (μI − Q)⁻¹`, Q die Gehmatrix, eigene
Herleitung aus 3.2); ein kurzer Aufenthalt, der in einem Sprung beginnt, endet frühestens im nächsten.
Gegen 3 h in Takten lagen die Räume damit bis 0,022 daneben (alte Messung, Python-Kern, 7.10.,
8 Personen). Gespeichert werden die Massen als Anteile in float16 (unter 10⁻⁶ als 0), zlib, base64
(`filter/saved.rs`): etwa 6 kB je Person statt 80 kB als float32 (7.10. 08:06). Passt der Zustand nicht zu den
Kacheln (anderer Grundriss) oder fehlt er, weiß das Modell nichts. Nach einem Fehler des Modells beginnt
es aus dem Zustand unmittelbar davor, wenn der sich noch speichern lässt, sonst aus dem letzten
gespeicherten.

**Nichts bekannt** (erster Start, anderer Grundriss, *Spuren zurücksetzen*): keine bekannten Personen;
die unbekannten (5.5) als Poisson-Intensität mit `start_unknown` = 1 erwarteten Person (angenommen),
je ein Drittel im beobachteten Bereich (stehend, gleichverteilt nach Fläche, ein laufender Aufenthalt
zu zufälliger Zeit gesehen), in den Bereichen ohne Sensor und außer Haus. Keine feste Zahl: Wer in
Sicht ist, wird über seine Spur zur bekannten Person (5.5), gleich wie viele es sind; wo niemand
erfasst wird, schwindet die Intensität mit der Nicht-Erfassung. Ohne Anteil in Sicht könnte, wer beim
Start in Sicht sitzt, nur ein Geist sein. Der Preis: Hinter Türen und außer Haus bleibt die
Intensität, wie viele auch gefunden werden (die Zahlen eines Poisson-Prozesses sind unabhängig), bis
dort niemand herauskommt oder sie vergessen wird (3.4). Alte Messung (Python-Kern, 6./7.10., bei jedem
App-Start ohne Wissen; Aufnahmen gelöscht, Wahrheit ungenau): 0,3 / 1 / 3 Personen änderten die
Lichtfehler nicht (0,14 von 41 fälschlich an), die Log-Evidenz stieg mit der Zahl (−279 / 0 / +265).

Neu beginnt das Modell, wenn sich die Welt der Personen ändert (Wände, Räume, Türen, Parameter,
welche Sensoren es gibt, der beobachtete Bereich), wie nach einem Neustart: aus dem Zustand davor,
solange die Dichten dazu passen, sonst ohne Wissen. Sie passen bei denselben Kacheln, Bereichen und
Altersklassen und denselben Arten des Aufenthalts und Stufen von κ (`World::fingerprint`); `go_share`,
`go_time`, `stay_points`, `kappa_shape`, `kappa_levels` oder `ld_amp_levels` zu ändern, verwirft also
das Wissen. Wird nur ein
Sensor gedreht, verschoben oder neu kalibriert, bleibt, was über die Personen bekannt ist, ganz (die
Konfiguration sagt etwas über den Sensor, nichts über sie); seine Spuren beginnen neu wie nach einer
Datenlücke (4.1). Ändert sich dadurch, welche Räume beobachtet sind, beginnt das Modell neu wie oben
(`Tracker::recalibrate`).

### 5.4 Wechsel der Darstellung
- **Kacheln → Gauß**, wenn eine Spur auf der Person beginnt oder wiedergefunden wird: je Kachel
  Masse × Erfassungsrate × Dichte der Messung (Kachel-Gauß gefaltet mit dem Messrauschen), die
  Position je Kachel als Produkt der beiden Gauß-Verteilungen, je Betriebsart per Momentenabgleich
  zu einer Komponente („Partikel zu einem Gauß zusammenfassen“, J_Luber2014 Abschn. 6.5, Gl. 6.32).
  Die Geschwindigkeit eines Gehenden ohne Spur ist unbekannt (Mittel 0). Die Aufteilung von `z − x`
  auf c und o folgt aus ihren Varianzen (eigene Herleitung).
- **Gauß → Kacheln**, erst wenn die letzte laufende Spur endet, nicht schon beim Verlieren (gehaltene
  Spuren behalten so ihren Versatz). Komponenten werden mit ihrer Dichte auf die Kacheln verteilt;
  verloren gehen Geschwindigkeit und Versätze. Die Amplitude des Aufenthalts (je κ) geht mit, wo die
  Komponente *steht* steht (4.3).

### 5.5 Die unbekannten Personen
Eine Dichte der Art `Intensity` (`filter/densities.rs`): der unentdeckte Teil des PMBM (B_GarciaFernandez2018 Gl. 7–10, 18–24) über den
Kacheln aus 5.3, als Intensität (Massen sind erwartete Zahlen). Dieselbe lineare Bewegung, dazu die
Ankünfte und das Vergessen aus 3.4.
- Wer nicht erfasst wird, wiegt mit der Leerwahrscheinlichkeit `exp(−(Masse vorher − nachher))`.
- Eine neue Spur auf einer unbekannten Person hat das Gewicht `∫ Intensität × Erfassungsrate ×
  Dichte der Messung`; daraus wird eine bekannte Person (5.2). Die Intensität bleibt (eine Geburt ist
  ein Punkt des Prozesses).
- **Bekannte Personen ohne Spur sind Bernoullis** (Dichte der Art `Person` mit Existenz r, B_GarciaFernandez2018 Gl. 31;
  der Spurteil eines PMBM): Es gibt sie mit der Wahrscheinlichkeit r, dann mit ihrer Dichte. Was gegen
  sie spricht (keine neue Spur, kein Wiederfinden, die Energien, 4.1–4.3), wirkt als
  `1 − r + r ∫ Dichte × Likelihood`: Dichte und r werden zusammen aktualisiert, ohne Regel. Die Energien des
  LD2410C nur, soweit niemand bei ihr steht (4.3, seit 0.65): ihr Ort voll, r mit `1 − r + r (∫ …)^w`, w = 1 − Nähe. Eine neue
  Spur auf ihr hat das Gewicht `r ∫ …` und macht sie gewiss (r = 1, eine Person mit Spur). r < 1
  entsteht beim Zusammenlegen (5.6): Sagt eine Hypothese „die Spur war ein Geist“, eine andere „sie
  war eine neue Person“, und endet die Spur, sind beide über alle laufenden Spuren gleich; zusammen
  sind sie die Person mit r = Gewicht der zweiten. Vorher blieben das zwei Hypothesen mit
  verschiedener Personenzahl; wurde die mit weniger Personen abgeschnitten (höchstens 12, 5.1), kam
  sie nie zurück, und die erfundene Person blieb mit r = 1 (9, 10). Ausgaben zählen r × Dichte; die
  Anzeige zeigt Personen mit r ≥ 0,5 (Schätzer der Literatur, B_GarciaFernandez2018 Abschn. VI).
- **Was keine Messung prüfen kann, läuft ab** (`unseen_life` = 2 min, `expire` in `filter/step.rs`, seit
  8.10.). Wo ein Sensor eine bekannte Person ohne Spur sähe, entscheiden die
  Messungen (`1 − r + r ∫ Dichte × Likelihood`, oben); dort endet sonst nur `record_life` (unten): Eine
  Sitzende in Sicht bleibt, solange keine Messung gegen sie spricht, wie lang ihre Lücke auch ist, und
  zwei, die der LD2450 als ein Ziel sieht, bleiben zwei (weder Erfassung noch Wiederfinden zählen gegen die
  Unaufgelöste, 4.1; bis 0.61 tat es das Wiederfinden). Wo kein Sensor hinsieht – ein toter Winkel
  eines Raums, ein Bereich ohne Sensor, außer Haus –, kann keine Messung einen Eintrag widerlegen; dort
  hielte ihn nur die Aufenthaltsdauer, mit ihren schweren Ausläufern (3.1: 2 % der Aufenthalte dauern
  über eine Stunde; 3.3) über Stunden. Bei r < 1 kostet ein Ort ohne Sicht, was sein Aufenthalt sagt
  (die Odds von r fallen mit der Überlebensfunktion, wenn das Herauskommen nicht gesehen wird); bei
  r ≈ 1 kostet er nichts, denn Bayes kann r = 1 nicht senken. Solche Einträge entstehen aber aus den
  Näherungen des Filters (Zusammenlegen zur Multi-Bernoulli-Verteilung 5.6, Abschneiden 5.1) und aus
  Fehlern der Messmodelle (Mehrwegeechos, LD2410C), nicht aus Personen, die durch eine Tür kamen.
  Deshalb läuft der Teil eines Eintrags ab, den kein Sensor prüfen kann:
  `p_S(x) = e^(−Δt/τ · (1 − s(x)))`, s(x) wie gut der beste *lebende* Sensor eine Person dort sieht
  (`World::observed`: die Sicht des LD2450, 4.1, oder die des LD2410C, Strahl × Sichtlinie bis zu
  seinem letzten Ring, 4.3; je über die Kachel gemittelt), 0 in allen Bereichen ohne Sensor (offen wie
  geschlossen, 2) und außer Haus. **Auch geschlossene Bereiche** (keine Tür nach draußen, ihre Türen führen
  nur in den beobachteten Bereich: der Balkon) laufen ab. 0.26 ließ sie nicht ablaufen, begründet mit
  einer Wahrheit, die falsch war; gehalten wurden dort Einträge aus Geistern an der Balkontür (10 „0.27“).
  r fällt um r × diesen Anteil × (1 − e^(−Δt/τ)); in voller Sicht ist p_S = 1. Das ist eine Annahme über
  Einträge ohne Beleg, kein Teil der Bewegung: die Existenz-Kette von D_MusickiEvans2005 (Markov-Kette 1,
  Gl. 6, p₁₁ < 1), aber nur, wo die Erkennbarkeit (Kette 2, hier κ, 4.1) nichts ausrichten kann. In
  Sicht verschwindet so niemand ohne Messung dagegen (1, Punkt 3 und 5); wer hinter einer Tür, in
  einem toten Winkel oder außer Haus ist, wird nach Minuten
  vergessen und kommt, wenn er herauskommt, als neue Person (5 s Bestätigung genügen, 1, Punkt 4; die Zahl
  hinter Türen zählt nur für das Herauskommen). Über eine Datenlücke (Neustart, 5.3) zählen die
  Sensoren, die da sind (sie hätten geprüft, nur die App sah nicht hin); ein Sensor, dessen Frames
  ausbleiben (4.4), prüft nichts. τ = 2 min wie der Median der Aufenthalte in Bereichen ohne Sensor
  (3.3, angenommen); wo es überall galt, war 2 min die kürzeste Zeit ohne fälschlich an (alte Messung
  auf gelöschten Daten, 10, „Tote Winkel und Existenz“). Tote Winkel der Wohnung (alte Konfiguration
  config10, s < 0,05 auf 1,9 m²): die Vorratsecke der
  Küche hinter der Wand bei y = 7,7 m (x 3,6–4,7, y 7,9–9,2) und ein Streifen von 0,4 m an der Südwand
  des Schlafzimmers (x −2,3 bis −1,0); s < 0,5 auf 3,1 m². **Bis 0.22** lief die Existenz überall ab, auch in
  voller Sicht (`record_life`, `e^(−t/2 min)`); in Sicht kostete das die zweite Person, die der LD2450
  nicht trennt, und Sitzende in langen Lücken (10).
- **Kein Eintrag ist gewiss** (`record_life` = 1 Tag, seit 0.60.0): Jeder Eintrag einer bekannten Person ohne Spur läuft
  überall zusätzlich mit 1/Tag ab, wie die unbekannten Personen außer Haus (3.4). Für Sitzende ist das nichts (in 4 min
  0,3 %), aber r bleibt unter 1: Bei r = 1 ändert `1 − r + r ∫ …` nichts, und wenn alle Hypothesen den Eintrag haben, auch
  kein Gewicht; ein Eintrag, der einmal eine Spur hatte, konnte in Sicht nie mehr enden, was auch die Sensoren sagten
  (die Existenzkette von D_MusickiEvans2005 mit p₁₁ < 1, jetzt überall). Allein: 3.11. Bad 111 → 67 belegte Minuten.
- **Zurückgeben:** Eine bekannte Person ohne Spur, die zu weniger als 1 % existiert (r < 0,01), geht
  mit r × ihrer Dichte in die Intensität (Bernoulli → Poisson), wo sie auch ist. Wer das Haus verlassen
  hat, dessen Eintrag läuft draußen ab wie überall, wo kein Sensor prüft (oben), und geht erst dann
  zurück (sonst käme, wer hinausgeht, mit seinem ganzen r in die Intensität außer Haus und würde erst
  nach einem Tag vergessen; bis 7.10. so, 10, „Wer hinausging, blieb erwartet“). Geprüft wird einmal je
  Sekunde (`RECYCLE_EVERY`); gibt eine Hypothese jemanden zurück, rücken alle vor, und es wird
  zusammengelegt (5.6). Das Zurückgeben
  ändert nur P(mehrere davon kommen zurück), um höchstens 0,01²/2. Sonst würde jeder Gast für immer
  verfolgt. (Die Literatur verwirft Bernoulli-Teile unter 10⁻⁵, B_GarciaFernandez2018 Abschn. VII,
  oder r < 10⁻³, B_Reuter2017 S. 166; hier geht keine Masse verloren.) Die Intensität ist eine für alle
  Hypothesen wie im PMBM (B_GarciaFernandez2018 Gl. 7–10): Was die Hypothesen zurückgeben, kommt nach
  ihrem Gewicht hinzu. Vorher trug jede Hypothese ihre eigene Kopie; mit den vielen kleinen Bernoullis
  (r von wenigen %, aus „vielleicht eine neue Person“ jeder Spur, solange beim Start ohne Wissen
  Intensität in Sicht ist) kostete das Rechenzeit (10). Alte Messung (Python-Kern, Aufnahme gelöscht,
  Wahrheit ungenau): 5 % statt 1 % sparten 14 % Rechenzeit, aber Licht fälschlich aus 1,67 statt 1,17
  von 15 (Arbeitszimmer 21:36 knapp unter der Schwelle), Log-Evidenz −49.

- **Störquellen** (4.5; `filter/clutter.rs`) wie die Personen: Die unbekannten sind eine Poisson-Intensität,
  gleich über den Blick jedes Sensors und gleich in allen Hypothesen; im Gleichgewicht bekommen so viele ihre erste Spur,
  wie entstehen (`clutter_birth` je m² und s, D_Mahler2011 Gl. 13–15), darum steht die Intensität nicht im Zustand. Die
  bekannten sind Bernoullis je Hypothese, benannt nach der Spur, mit der sie zuerst gesehen wurden (so ist „diese Spur
  ist eine neue Störquelle“ in allen Hypothesen dieselbe). Ohne Spur überlebt eine mit `e^(−Δt/clutter_life)`, und
  solange ihr Sensor lebt, gewichtet „keine neue Spur auf ihr“ mit `1 − r + r e^(−a Δt)` und senkt r (dieselbe
  Bernoulli-Aktualisierung wie oben). Unter r < 1 % fällt sie weg (zurück in die Intensität, die so bleibt, wie sie ist). Beim Zusammenlegen (5.6) wird
  dieselbe Störquelle nach ihrem Namen gemischt (Momentenabgleich, r = Σ Gewicht × r, wo sie fehlt r = 0).

### 5.6 Zusammenlegen
Hypothesen, die über alle laufenden Spuren dasselbe sagen, werden eine (`merge_alike` in
`filter/merge.rs`). Was in allen dasselbe Objekt ist, bleibt unverändert; sonst:
- Personen mit denselben Spuren: Gauß-Mischungen vereinigt, je Betriebsart per Momentenabgleich.
  Ebenso die Quellen derselben Geisterspur und die Intensität der Unbekannten (gewichtet gemischt).
- Personen ohne Spur sind austauschbar: so gepaart, dass die Summe der L1-Abstände (von r × Dichte)
  minimal ist (alle Paarungen durchprobiert, für 2 bis 6 Personen; über 6 wird nicht gepaart), dann
  gemischt (eigene Herleitung; beliebige Reihenfolge erzeugt „je zu 50 % hier und
  dort“ statt „einer hier, einer dort“, B_Reuter2014 Fig. 1–3). Für mehr als eine Person ist das die
  Multi-Bernoulli-Näherung.
- Auch Hypothesen mit verschieden vielen Personen ohne Spur werden eine (seit 7.10.): Wo eine
  Hypothese eine Person weniger hat, steht dort „niemand“ (r = 0); gemischt wird daraus ein
  Bernoulli mit r = Σ Gewicht × r (`Densities::mixture`); ist r = 0, fällt er weg. Für genau eine
  fehlende Person exakt. Ein Neustart speichert dadurch eine Hypothese (5.3).
- Bekannte Störquellen nach ihrem Namen (5.5).

## 6. Ausgaben
- **Je beobachtetem Raum:** Verteilung der Personenzahl (5.1); an Home Assistant geht P(belegt) = 1 − P(0) als
  Attribut `probability` der Entität *Personen* (in 5-%-Schritten, neben `moving` und `still`). Ein Raum ohne
  Sensor bekommt die seines Bereichs ohne Sensor. Nur Auskunft: Nichts entscheidet danach,
  und die Oberfläche zeigt sie nicht, weil sie den Unbekannten (5.5) mitzählt: Ein Schlafzimmer ohne Person stand
  in zwei Stunden der Aufnahme drei Viertel der Zeit über 50 % (Messung vor dem 31.10., Aufnahme gelöscht; 10).
- **Personen und Belegt, eine Zuordnung:** Jede Zahl von Personen und jedes *besetzt* kommt aus einer Quelle
  (Leon, 30.10.: „Ich will, dass das Licht dem folgt, was angezeigt wird“; bis 0.54 Maximum je Randverteilung für die
  Zahl, P(belegt) über c für das Licht, Punkte aus der wahrscheinlichsten Hypothese: drei Quellen).
  - *Zuordnung:* Die Personen der Anzeige (unten) stehen an ihrem wahrscheinlichsten Ort (`Person::place`), im
    beobachteten Bereich im Raum ihres Punkts (liegt er in keinem, in einer Wand etwa: Raum der nächsten Kachel).
  - *Wie sicher:* `sure_at` = P(die Person ist dort) über alle Hypothesen: je Hypothese die Person, der sie
    eine der lebenden Spuren gibt (die Nummer einer Gruppe gilt nur innerhalb einer Hypothese), ohne Spur die Dichte
    selbst; ihr Anteil am Ort (enthält r und den Teil durch eine Tür), gewichtet mit der Hypothese. Eine Hypothese,
    die die Spur einen Geist nennt, trägt nichts bei. Damit hängt die Wahrscheinlichkeit an der Person, keine je
    Raum wird ausgewertet.
  - *Regel* (`Occupied::update` in `outputs.rs`, Zustand bei den Ausgaben, nicht im Filter): Ein Raum geht an, wenn
    eine ihm zugeordnete Person `sure` ≥ c erreicht, c = K_an / (K_an + K_dunkel) = `light_cost` / (`light_cost` + 1)
    (Bayes-Entscheidung; I_GneitingRaftery2007 S. 364–365, Satz 3, hier je Person; mit dem Default `light_cost` = 0,2
    ist c = 1/6, seit 0.64 aus 1: dunkel mit Person wiegt etwa 5× Licht ohne Person), und aus, wenn keine zugeordnete
    Person `occupied_off` erreicht (Default 1/12, höchstens c; bis 0.63 2 und 1/3). Offline auf den Läufen b063q/gq1q
    nachgerechnet (`schwelle.py`): aus bei 1/12 oder 1/6 gleich, der Nachlauf von Präsenzlicht (120 s) überdeckt es. Keine Nachlaufzeit: Das verzögerte Ausschalten bleibt in Home
    Assistant (bzw. Node-RED). Die App schaltet keine Lichter, sie liefert je Raum *besetzt* und *Ziel* (unten).
    Die Regel ist zustandsarm und doppelt angewandt dieselbe; nach einem Neustart beginnt der Zustand leer und baut
    sich aus ihr auf. Bereiche ohne Sensor genauso, ein Bereich aus mehreren Räumen als ein Ort.
  - *Wer zählt:* Wer `occupied_off` erreicht und in einem Raum steht, der an ist. Wer zugeordnet ist, aber nicht
    zählt, ist für die Anzeige keine Person, nur eine Wolke ohne Punkt: Der dunkle Raum zeigt 0, nicht 1. Es
    gilt ohne Ausnahme: *besetzt* ⇔ Zahl ≥ 1, Räume + Bereiche ohne Sensor = Haus, und Karte, Liste, Kopfzeile,
    `*_count` und `*_occupancy` sagen dasselbe. *Bewegt* + *ruhig* = Zahl gilt nur in beobachteten Räumen: Wer in
    einem Bereich ohne Sensor zählt, ist nicht gezeichnet und weder bewegt noch ruhig (so auch im Haus).
    Unbekannte ohne Person der Anzeige zählen nicht.
  - *Warum so:* Die Personen der Anzeige und ihre Orte gab es schon, sie sind der Schätzer der Literatur
    (B_GarciaFernandez2018 Abschn. VI); sie allein zu zählen ließ Hypothesen zu 0,51 / 0,46 Räume schalten (2 h
    Aufnahme, 30.10., gelöscht), die Sicherheit über die Hypothesen nimmt das heraus, die Hysterese das Pendeln um
    die Schwelle.
  - *Sensor des Raums stumm:* Der Sensor des Raums ist der, dessen Name die ID des Raums ist (`kueche` für die
    Küche), sonst der Anzeigename des Raums, sonst der Raum, in dem er hängt (0,3 m um den Umriss, nur wenn
    eindeutig). Hat jeder eingeschaltete, platzierte Sensor des Raums seit mehr als `SILENT_AFTER` = 30 s keinen Frame gesendet (länger als die WLAN-Lücken von 6–20 s,
    nach denen der Filter schon seine Spuren beendet, 4.4; in der Zeit des Modells, das bei Stille aller Sensoren erst
    60 s hält), sind alle Entitäten des Raums in Home Assistant *nicht verfügbar* statt
    belegt oder frei (`silent.rs`; eigenes Verfügbarkeits-Topic des Raums neben dem der
    App, `ha.py`). Ohne Daten sagt die Ausgabe „weiß ich nicht“; Node-RED schaltet dann nichts. Ein
    Sensor, der seit dem Start des Modells nichts gesendet hat, zählt ab diesem Start. Nur Ausgabe: Der
    Filter rechnet unverändert weiter, auch sein P(belegt) dieses Raums (9.10. 15:04 fielen die Boards
    von Wohn- und Esszimmer aus; der Raum war 1,5 h „frei“ und das Licht ging aus, während jemand auf
    dem Sofa saß). Räume ohne eigenen Sensor, Bereiche und das Haus: unverändert.
- **Bereiche ohne Sensor:** wie oben; eine Zahl am Raum nur, wenn der Bereich ein Raum ist.
- **Bereiche** (im Grundriss gezeichnet, etwa das Sofa oder das Vorratszimmer): in Home Assistant wie ein Raum mit
  *besetzt*, *Personen*, *Bewegung* und *Ziel*. Sie zählen die gezählten Personen, deren gezeichneter Punkt in
  ihrem Umriss liegt (`zone_numbers` in `outputs.rs`), zusätzlich zu deren Raum; in die Zahl des Hauses gehen sie
  nicht noch einmal ein. Kein `probability`, nie *nicht verfügbar*.
- **Außer Haus** (auch das Treppenhaus): keine Zahl, kein Zustand, keine Entität in Home Assistant und
  in der Anzeige. Die Zahl im Haus (`presence_haus_count`) zählt alles außer *außer Haus*. Entitäten, die
  der Broker von früher hält (etwa die alten der Treppe), löscht die App beim Verbinden (`ha.py`: leere
  Konfiguration und leerer Zustand, beibehalten).
- **Bewegt / ruhig:** aus den gezeichneten Personen. *Bewegt*: mit messender Spur, geht-Gewicht > 0,5 und
  > 0,15 m/s (`Person::moving`); *ruhig*: gezeichnet und nicht bewegt.
- **Ziel** (`targets` in `learn/destination.rs`, je beobachtetem Raum und je Bereich): P(ein Gehender geht
  als Nächstes dorthin), ohne Horizont, allein aus der gelernten Karte, wohin Gänge gingen (eine ortsabhängige
  Karte der Raumnutzung aus Spuren, J_Luber2014 Kap. 6, Abschn. 6.5; Übergänge aus Daten gelernt, G_Liao2003).
  - *Die Karte:* Je Zelle (0,3 m, an den Räumen geteilt wie die Kacheln), Richtung (16) und Tempo (Klassen um
    0,5 / 0,9 / 1,35 m/s) zählt sie die Gänge je Ausgang: der Raum hinter der ersten Tür, ein Bereich ohne
    Sensor, außer Haus, ein Bereich (unten) oder „bleibt“ (der Gang endet ohne Tür, 2 s Stehen, in keinem
    Bereich). Jeder Schritt eines Gangs zählt mit einem Kern (0,3 m, 0,3 m/s, wie in der Untersuchung) auf
    den Zellen um ihn, nur im eigenen Raum (Räume an einem Raumteiler zählen als einer) und nicht durch Wände, so
    normiert, dass ein Gang durch die Mitte einer Zelle in ihrer Geschwindigkeit dort 1 zählt. Gefragt wird sie
    über die Zellen um den Ort (0,15 m) und linear zwischen Richtungen und Tempi. Langsamer als 0,3 m/s zählt kein
    Schritt, und die Karte wird nicht gefragt. Ein Gang durch mehrere Räume ist je Raum einer: Nach jeder Tür
    beginnt ein neuer, die Schritte davor zählen für den Raum dahinter (fürs Licht genügt der nächste Raum;
    zwei Räume voraus verteilten die Gänge auf viel mehr Ausgänge).
  - *Bereiche als Ausgang* (seit 0.56; Leon, 4.11.: die Speisekammer hinter der Küche, „Vorratszimmer“, ohne
    Tür, der Küchensensor sieht durch die Lücke nur einen Streifen). Ein Bereich ist Ausgang der Räume, deren
    Zellen er enthält. Ein Gang endet in ihm, wenn die Person dort 2 s steht (wo sich Bereiche überlappen, im
    kleinsten), oder wenn ihre Spuren dort enden und binnen 1,5 s niemand den Gang übernimmt: Wer in einen
    toten Winkel geht, bleibt nicht sichtbar stehen. Sonst zählt ein Gang ohne Übernahme weiter nicht. Ein
    Geist, dessen Spur in einem Bereich endet, zählt so als Gang dorthin; die Karte ist nur Ausgabe (unten).
  - *Ein Raumteiler ist keine Tür:* Räume, die nur ein Raumteiler trennt, sind für die Gänge ein Raum
    (dort pendelt man, ZIEL-Untersuchung 3.1; Wohn- und Esszimmer sind eine Lichtgruppe). Ein Gang über
    den Raumteiler geht weiter, sein Ausgang ist die nächste Tür; die Karte sagt dort für den Raum
    jenseits des Teilers 0.
  - *Gelernt in der App aus den eigenen Gehenden*: je Person mit Spur über alle Hypothesen, die sie halten,
    gewichtet mit dem Gewicht der Hypothese und P(geht) (weiche Zählungen). Weich sind nur die Schritte: Der
    Ausgang eines Gangs (wo die Person nach der Tür ist) kommt aus der schwersten Hypothese, die sie hält
    (`Learner::observe`). Eine Person ist ihre früheste laufende Spur; endet sie, übernimmt den
    Gang, wer binnen 1,5 s höchstens 1 m daneben auftaucht (Übergabe an einer Tür). Der Ausgang: der Raum
    oder Bereich ohne Sensor, in dem die Person zwei Takte nach einer Tür ist, ein Bereich (oben), oder
    „bleibt“ nach 2 s mit P(geht) < ½. Schritte, die älter als 60 s sind, fallen aus dem laufenden Gang. Während
    „Lernen pausieren“ werden laufende Gänge verworfen und nichts gezählt. Vergessen mit 14 Tagen.
    Gespeichert in `destinations.json` (und in jeder Fehlermeldung); sie übersteht Neustarts und Updates. Jede
    Änderung der Geometrie (Räume, Türen, Zonen, ein Sensor platziert, verschoben, gedreht oder neu kalibriert)
    verwirft sie mit allem Gelernten (`Learned::follow` in `learn/mod.rs`); die Karte beginnt leer.
  - *Nur Ausgabe:* Die Karte liest die Hypothesen, sie ändert keinen Zustand und kein Gewicht des Filters.
    Darum kann sie keinen Fehler des Verfolgens verstärken (10: aus den eigenen Urteilen lernt das Modell
    seine Fehler; hier lernt nur die Ausgabe).

  **Ein Dirichlet-Posterior mit einem Prior, der nirgends hingeht.** Je Gehendem k mit den Gängen n_k an
  seiner Stelle und Geschwindigkeit, davon der Anteil f_k(Z) zur Zone Z:
  `P_k(Z) = n_k f_k(Z) / (n_k + α)` mit dem Gewicht α des Priors in Gängen (`dest_prior_walks` = 1: ein
  Gang allein macht keine Stelle zum Ziel, zwei gleiche ergeben 2/3, vier 0,8). Für die Zone, über die
  Gehenden und Hypothesen:

  `P(Z) = λ q_map(Z)`, `λ = n / (n + α)`,

  - `q_map(Z) = Σ_h w_h (1 − Π_k (1 − P(k geht) f_k(Z)))`, über alle Hypothesen (5.1);
  - n = die Gänge hinter den Gehenden, die als Nächstes nach Z gehen könnten (ein Raum durch eine Tür, über
    einen Raumteiler mit f = 0; ein Bereich ihres Raums, in dem sie nicht schon sind), gemittelt mit P(geht)
    und dem Gewicht ihrer Hypothese.

  Wo die Karte nichts weiß (n = 0), ist *Ziel* aus. Bis 0.55 mischte P die eigene Bewegung (*wird betreten*,
  das OU-Modell 2 s vorausgerechnet) mit der Karte; Leon (4.11.) hielt die Vorhersage aus der Bewegung für
  überflüssig, seither zählt nur die Karte. Gemessen ist der Wechsel nicht. Im Test (`test_destination`, 20
  gleiche Wege, Schwelle 0,66) ist *Ziel* mindestens 2 s vor der Tür an und danach in mindestens 80 % der Takte
  bis zur Tür.

  **An**, wenn `n > 0` und `P(Z) ≥ c_Z` mit `c_Z = K_Fehl / (K_Fehl + K_spät)` je Zone (Bayes-Entscheidung
  wie für *besetzt*; K_Fehl ein Licht auf Verdacht ohne Person, K_spät ein Eintritt ins Dunkle).
  `target_threshold` = 0,8 (K_Fehl = 4 K_spät), je Raum oder Bereich anders (`target_thresholds`). Die
  Schwelle wirkt auf eine kalibrierte Wahrscheinlichkeit (von den Momenten mit 0,9–0,95 traten 89 % ein,
  ZIEL-Untersuchung 3.4 vom 7.10., auf den gelöschten Aufnahmen und mit der damaligen Formel für P). Die Wahl
  ist eine der Kosten.

  Attribute: die Wahrscheinlichkeit, aus welchem Raum der Gehende kommt, der am wahrscheinlichsten
  hingeht, sein Abstand zur nächsten Tür dorthin (ein Bereich: zu seiner nächsten Zelle; Luftlinie) und die
  Zeit bei seinem Tempo, seine Nummer und die Gänge dahinter (n). Räume ohne Sensor: Das Modell rechnet kein
  Ziel; die Entität *Ziel* gibt es in Home Assistant trotzdem, immer aus mit Wahrscheinlichkeit 0. Die Karte
  lernt in der App nur aus dem, was die App live sieht.
  *Anzeige* (Tab *Gelerntes*, `DestMap::shown`): je Zelle die Richtungen (über die Tempi) zu Spitzen
  zusammengefasst, jede Richtung zu der, zu der sie ansteigt; jede Spitze mit mindestens 15 % der Gänge der
  Zelle ein Pfeil (höchstens 6) mit dem mittleren Tempo, ihren Gängen und dem häufigsten Ausgang mit seinem
  Anteil. Ein Weg verschmiert beim Lernen über 2–3 Nachbarrichtungen (Kern 0,3 m/s); einzelne Richtungen über
  einer Schwelle wären ein Fächer aus Pfeilen für einen Weg.
- **An Home Assistant** (`ha.py`, `ZoneState.to_dict` in `zones.py`): Jede Änderung von Zustand oder Attributen einer
  Entität ist eine Zeile im Recorder von Home Assistant. Deshalb: Wahrscheinlichkeiten in 5-%-Schritten,
  Zeiten in 0,5 s, Wege in 0,5 m; die Zustände (*besetzt*, Zahl, *Bewegung*, *Ziel*)
  gehen in dem Takt hinaus, in dem sie wechseln; die Attribute einer Entität (`Discovery.steady`) neu mit
  jedem Wechsel ihres Zustands, sonst nur, wenn eines um mindestens zwei Stufen wanderte (eine Stufe ist
  Rundungsflackern) und die letzte Änderung mindestens 10 s (Personenzahl: 60 s) her ist. *Ziel* aus:
  feste Attribute (Wahrscheinlichkeit 0, der Rest leer). Nur die Ausgabe an Home
  Assistant; die Anzeige der App bekommt dieselben gerundeten Werte ungehalten. Dort wie in Home Assistant ist
  ein *Ziel* unter c/2 (oder unter 0,01) 0 ohne Angaben; die Oberfläche hält ein *Ziel*, das anging, 2 s sichtbar
  (`static/js/store.js`).
- Anzeige: Personen der wahrscheinlichsten Hypothese, die eher existieren als nicht (r ≥ 0,5, 5.5),
  ihre Dichten (r × Dichte) als Wärmekarten; einen Punkt und eine Zeile in der Liste hat nur, wer zählt (oben).
  Die LD2410C-Präsenz je Sensor (bewegt oder ruhig) mit Lücken bis `ld2410_hold` = 1,5 s überbrückt (`sensors.rs`;
  nur Anzeige).

## 7. Evidenz
Die Summe der Normierungen ist die Log-Evidenz der Aufnahmen (`Filter::loglik` in `filter/mod.rs`, das Paar
(Log-Evidenz, Anteil der weggefallenen Hypothesen); Summe der
prequentiellen Log-Scores, I_GneitingRaftery2007 S. 372): je Schritt die Vorhersagedichte der neuen
Daten unter dem Posterior, den der Filter davor behalten hat. Was beim Abschneiden (5.1) wegfällt,
zählt in seinem Schritt mit (die Masse aller Kinder ist die Vorhersage); der zweite Wert weist
aus, wie viel davon auf weggefallene Hypothesen kommt (`report_eval.py`: „dropped hypotheses“). Weil
der Filter deterministisch ist, brauchen Vergleiche keine Seeds (I_Kantas2015 S. 8–9). **Glatt in den
Parametern ist sie nicht:** Das Abschneiden, die Obergrenze der Hypothesen,
die Paarung beim Zusammenlegen (5.6, ein argmin), die Rückgabe bei r < 0,01 (5.5) und die Grenze,
ab der eine Kachel im Blick des LD2410C liegt (`ld2410::FLOOR`), springen; deshalb hängt sie nicht monoton von den
Grenzen ab (5.1), und Gradienten oder EM-Schritte über sie sind unzuverlässig. Vergleichbar ist sie
außerdem nur bei derselben Vorverarbeitung (welche Messungen verworfen werden, hängt von Kalibrierung
und Grundriss ab) und derselben Temperierung der LD2410C-Terme (4.3: Potenzen von Dichten, keine
Dichten). Gedacht ist sie für grobe Vergleiche von Parametern und Varianten auf Aufnahmen ohne
Wahrheit, bei mehr als einer Einstellung der Grenzen (5.1). Bisher verglichen damit: Varianten auf den
gelöschten Aufnahmen vom 6./7.10., etwa die Zahl der Arten des Aufenthalts (3.1) und die Stufen κ (4.1).

## 8. Prüfung
- **Aufnahmen:** Der Rekorder in der App (Einstellung *Aufzeichnung*, `presence_tracker/recording.py`) schreibt je
  Stunde eine Datei aller eingehenden Nachrichten; er ist die Quelle der Aufnahmen seit dem 31.10. Alle älteren
  Aufnahmen und die Wahrheitstabellen sind am 31.10. gelöscht (die Tabellen waren ungenau). `tools/record.py`
  schreibt dasselbe Format von außen über MQTT.
- **Fehlermeldungen** (App, *Fehler melden*) sind die Wahrheit: aus ihnen entsteht eine private Wahrheitsdatei
  (Fenster und Zahlen je Raum, nur wo die Meldung etwas sagt). Maßstab sind die Lichtfehler je Raum (1: dunkel mit
  Person wiegt etwa 5× Licht ohne Person), keine einzelne Prozentzahl. Eine Meldung enthält 15 Minuten
  Sensordaten, die Anzeige der App, die Konfiguration samt den später ohne Neustart übernommenen
  (`config_changes`), die Pausen, den Start des Modells, das Gelernte und den gespeicherten Zustand der Personen
  von diesem Start (sonst das Gelernte vom Moment der Meldung). Das Nachspiel ab dem Start glaubt genau, was die
  App glaubte; mit dem Gelernten vom Moment der Meldung wich es am 6.10. ab (gelöschte Daten). Lag der Start
  früher, hat ein Nachspiel ohne Wissen nach 15 Minuten vergessen, was es nicht wusste (6.10. 22:40, 22:42:
  |ΔP| < 0,01 am gemeldeten Moment, anfangs bis 0,11; gelöschte Daten).
- **Hintergrundaktivität** (10, „Lernen pausieren“): Fenster, die in eine Pause reichen (Schalter in den
  Aufnahmen, für die Tage davor die Zeiten des Saugroboters, `--pauses`), werden nicht bewertet; die
  Nachspiele lernen dort nichts, wie die App.
- **Tests:** die Rust-Tests im Kern (`#[cfg(test)]` in `presence-core/src`, etwa `outputs.rs`,
  `learn/destination.rs`, `silent.rs`, `filter/tests.rs`) und die Python-Tests (`tracker/tests`): bitgleiche Läufe,
  Unabhängigkeit von der Zeitzerlegung, Bewegungsstatistik, Szenen aus `sim.py`, Invarianten der Buchführung
  (`Filter::check`, aus Python `core.check()`).
- **Geplant:** simulationsbasierte Kalibrierung (0_Talts2018 Alg. 1): Welten aus diesem Modell
  erzeugen; der Rang der wahren Zahl unter der Filterverteilung muss gleichverteilt sein.
- Werkzeuge:
  - `tools/replay.py`: Aufnahmen oder eine Fehlermeldung durch den Tracker, druckt je Raum und Person, was er zeigt.
  - `tools/report_eval.py`: bewertet den Tracker gegen die Wahrheit aus den Fehlermeldungen; die Aufnahmen
    laufen wie in der App (frisches Modell bei jedem Start, Gelerntes und Zustand der Personen weitergetragen),
    je gemeldetem Moment und Raum P(belegt), P(gemeldete Zahl), Lichtfehler und die Log-Evidenz (7).
  - `tools/baseline_eval.py`: einfache Grundlinien ohne Modell (rohe LD2450-Ziele mit Haltezeit, dazu LD2410C)
    gegen den Filter, bewertet wie `report_eval.py`, mit dem Licht der Node-RED-Regel.
  - `tools/vacuum_history.py`: die Zeiten des Saugroboters aus der Historie von Home Assistant als Pausen
    (`--pauses`) für die Tage vor dem Schalter.
  - `core/presence-replay`: eine Aufnahme durch den Rust-Kern ohne Python (`trace`: Zustände Zeile für Zeile,
    `prof`: Rechenzeit).
  - `tools/calibrate_offline.py`: Kalibrierung aus Aufnahmen mit dem Modell der App, dazu Block-Bootstrap,
    Spiegel- und Maßstabsprofile und freie Positionen (10); seit 0.45.0 rechnet der Kern den Schätzer
    (`presence-core/src/calibration/`), die Analysen des Werkzeugs laufen auf einem Baum von 0.43.0.

## 9. Bekannte Schwächen und Offenes
Zahlen aus den Aufnahmen 6.–14.10. und den Wahrheitstabellen sind mit diesen am 31.10. gelöscht. Wo sie unten
stehen, sind sie alte Messungen: neu messen, an Meldungen und Aufnahmen der App.

**Offen nach 0.63: Personen verschwinden noch über fremde Spuren** (10 „Nacht 8./9.11.“). (1) Die Spur eines fernen
Sensors (Esszimmer auf das Sofa in 6–7,5 m) misst eine Person 1,5 m daneben (Mehrweg, 20 min dieselbe Stelle an der Wand).
Bekommt eine Person ohne Spur sie, steht sie dort fest (die Gauß-Mischung verschiebt sich durch Nicht-Erfassung nicht, sie
wird nur gewogen), der Sensor des Raums sieht die Stelle leer, und nach 1–2 min ist ihr Rest „gab es nie“ (r ≈ 0,98 aus
dem Zusammenlegen, 5.6) billiger als Umziehen. (2) Eine neue Spur eines Sensors im Nachbarraum geht mit dem breiten Versatz
(4.1) an eine Person, die durch die Wand von ihm getrennt ist, und der Schnitt auf die Sicht des Sensors zieht sie hinüber
(31.10. 19:19:30 Bad → Arbeitszimmer, 4,5 m). Beides ist ein Fehler der Darstellung, nicht der Messung: Wer gesehen wird, ist
in Sicht des Sensors, aber eine Person, die er durch eine Wand nicht sehen kann, kann auch seine Spur nicht haben, und eine
Person, die der Sensor ihres Raums nicht sieht, ist woanders, nicht fort. Ein „Wer nicht da ist, wo er erwartet wird, ist
woanders im Raum“ (der Musicki-Zustand „unsichtbar“, D_MusickiEvans2005 Kette 2, mit eigener Dauer) und eine Prüfung der
Sicht beim Beginn einer Spur auf einer Person mit Spur sind die nächsten Kandidaten; beide nicht gemessen.

**Der Filter verliert Personen, die der LD2450 ihres Raums weiter misst** (10, „Filter oder LD2450 des Raums“). Im
Oktober gemessen (7./8.10., vor 0.55–0.63, Daten gelöscht): Bad, Arbeitszimmer und Schlafzimmer minutenlang bis stundenlang
„frei“ bei Spuren am Platz. Vermuteter Mechanismus: Die Spur wird bei ihrer Geburt in jeder überlebenden Hypothese ein
Geist, und mit nur einer Hypothese normiert sich jede Evidenz gegen „Geist“ weg (5.1). Ob es heute noch vorkommt, ist
neu zu messen. Solange es vorkommt, hängen *besetzt*, die Personenzahl und *Ziel* an den verlorenen Personen.

**Zwei beieinander (seit 0.65, 4.3, 10 „Nacht 11./12.11.“):** Über die Existenz einer Person, neben der eine der Anzeige
steht, entscheidet das LD2410C nicht mehr. Offen: Direkt nach dem Gehen der einen ist die Bleibende 10–30 s nicht belegt
(0.64: 0–2 s; Präsenzlicht überbrückt es), und wo zwei lange beieinander sind (Bett, Sofa), rechnet der Filter das 2- bis
4-Fache, weil überzählige Einträge neben einer Person bis zu ihrem Gehen bleiben.

Offen beim LD2410C (4.3):
- Durch Wände sieht er im Modell nicht (am 30.10. etwa 4 % gemessen, bewusst undurchsichtig gelassen), durch
  Türöffnungen geometrisch wie jeder Sensor. In einen geschlossenen Bereich ohne Sensor sieht kein Sensor
  (`prior_pd`, `world/build.rs:178`), auch nicht durch eine Glastür.
- Gemessen sind inzwischen κ (`kappa_shape`, 0.60) und das Profil der Gehenden (0.63). Offen: die Ruhe-Profile
  (`ld_still`), α, die Addition mehrerer Personen (nicht an Zwei-Personen-Zeiten geprüft) und das Bewegt-Flag
  (ohne Ablation weggelassen). Die Amplitude einzelner Aufenthalte streut um das Profil; das Modell nimmt das mittlere.
- Ob die langlebigen Echoquellen (`ld_echo_long`, 0.59) und die Klasse Störquelle (4.5, 0.61) beständige Energie ohne
  Person und wiederkehrende Spiegelziele (Balkontür, Esstisch) abdecken, ohne wache Sitzende im Bad und auf dem Sofa
  zu verlieren, ist an neuen Meldungen zu prüfen.

**Sitzende ohne gemessene Spur** (10, „Meldungen 8.10. abends“, „Sitzende im Bad und auf dem Sofa“):
1. *Personen ohne Spur haben beim LD2410C die Amplitude g = 1* (4.3, `amp_prior`, `filter/densities.rs:154`), soweit
   keine Spur eine gemessen hat: Die Kacheln tragen die Amplitude weiter, die eine Spur gemessen hat (je Stufe und
   Kachel), und der Hintergrund vergisst mit `ld_forget` = 0,7 h. Wer an einem Platz weniger zurückwirft (Bad, Sitz an
   der Westwand), ohne dass eine Spur das je gemessen hat, wird von der Energie schnell widerlegt. Zwei Varianten
   wurden im Oktober an den gelöschten Aufnahmen gemessen und nicht übernommen: die Amplitude der letzten Spur als
   Mittelfeld weitertragen, und g je Aufenthalt auf den Kacheln mit vollem Prior (machte eine Person mit kleinem g zur
   gleichwertigen Erklärung für beständige Energie). Beides war vor den langlebigen Echoquellen (0.59); neu zu messen.
   Offen: was eine wenig zurückwerfende Person von einer Echoquelle trennt (wie sie kam: durch eine Tür).
2. *Wer aus einem Bereich ohne Sensor zurückkommt, nachdem sein Eintrag dort abgelaufen ist* (5.5, `unseen_life`
   2 min, `filter/step.rs:190`), hat keinen Eintrag mehr; seine neue Spur bekommt die Person, die ungesehen am
   nächsten ist, oder sie beginnt als neue. Das ist der Preis des Ablaufs: Wer in einem Raum ohne Sensor bleibt, ist
   nach wenigen Minuten nicht mehr bekannt.
3. *Ein Raumteil, den kein Sensor sieht* (etwa die Vorratsecke der Küche hinter der Wand bei y = 7,7 m, x 3,6–4,7),
   hält eine Person nur so lange, bis ihr Eintrag abläuft (5.5, 2 min). Schwach Gesehenes (Sicht 0,1–0,5) prüfen die
   Messungen nur langsam; dort läuft der Eintrag mit dem ungesehenen Anteil ab (`fade`).

**Wen der Filter verliert, lernt der LD2410C-Hintergrund** (10, „Lernschleifen“), soweit keine Echoquelle seine Energie
hält. Der langfristige Pegel (Anker) lernt seit 0.61 nur aus Frames nach 10 min, in denen der LD2450 im Gehäuse nichts
sieht (`ld_anchor_quiet`, `ld2410/background.rs:6`); der Hintergrund selbst lernt mit den Personen, wie der Filter sie sah.

- **Doppelte Personen bleiben möglich** (10, Fälle 2 und 5): Bekommt eine Spur die „falsche“ bekannte
  Person (eine überzählige, die näher an der Tür ist, als die wirkliche ungesehen hätte gehen können),
  ist dieselbe Person zweimal da. In Sicht widerlegen die Messungen die überzählige; wo kein Sensor
  hinsieht, läuft sie in Minuten ab (5.5); sie entsteht aber weiter. Eine Hypothese „diese beiden sind
  dieselbe“ kennt das Modell nicht.
- **Der Versatz einer Spur bleibt, solange sie lebt** (Meldung 7.10. 18:31). Mit einem Sensor sind Ort x und
  Versatz c der Spur nicht zu trennen (`z = x + c + o + w`, `filter/evidence.rs:60`); was c ist, legt der Moment fest,
  in dem die Spur beginnt, danach ändert keine Messung mehr die Aufteilung. Beginnt eine Spur neben der eines
  anderen Sensors, trägt sie dessen Abstand die ganze Zeit mit (im Oktober 0,3 m über 50 min am Schreibtisch
  gemessen, Daten gelöscht). Der konstante Anteil (`const_share` 0,57) ist an Spuren um 60 s gemessen; über einen Weg
  ist der Fehler zweier Sensoren nicht derselbe. Abhilfe wäre ein Versatz, der mit dem Weg oder der Zeit dekorreliert
  (Gauß-Markov mit gemessener Länge); zu messen an Paaren langer Spuren.
- **Gelerntes und Geometrie** (seit 0.51, Leon 29.10.): Ändert der Nutzer Lage oder Sicht eines Sensors (Ort,
  Blickrichtung, Höhe, Spiegelung, Maßstab, Öffnungswinkel, Reichweite, platziert oder nicht) oder den Grundriss
  (Wände, Türen, Zonen) und speichert, werden alle Messungen der Kalibrierung, der LD2410C-Hintergrund und die
  Zielkarte (aller Sensoren) verworfen; ebenso, wenn ein Kalibrierergebnis übernommen wird (es ändert Richtung und
  Maßstab). Eine Quelle: der Schlüssel der Geometrie (`Config::geometry_key`, `config/mod.rs:541`), den jede Datei
  des Gesammelten trägt; passt er beim Start nicht, wird sie verworfen. Namen, ein Sensor aus- oder eingeschaltet und
  die Parameter verwerfen nichts. Das ist keine Schwäche, sondern der Preis: Nach jeder Änderung lernt alles neu.

Näherungen, die man prüfen oder ersetzen kann:
- Auf den Kacheln ist Gehen eine Diffusion: Kurzzeitig gerades Gehen und die Richtung gehen verloren,
  beim Wechsel Gauß → Kacheln auch Geschwindigkeit und Versätze. Innerhalb einer Kachel ist die
  Masse nicht weiter aufgelöst; Raten sind über die Kachel gemittelt, auch wo eine Sichtgrenze sie
  schneidet. Die Gauß-Näherung kennt Wände nur über ihre Sigma-Punkte (5.2): Fünf Punkte je
  Komponente, Spiegelung höchstens einmal (`filter/mixture.rs`); nur die Bewegung der Gehenden an Wänden nutzt
  16 Kubaturpunkte (`motion/imm.rs:238`). Was hinter einer Wand liegt, entscheidet die Sicht vom Mittel aus.
- Zwischen zwei Takten (`max_step` 0,2 s) wirken „gehalten, nicht wiedergefunden“ und die Ausgaben auf einen
  bis 0,2 s alten Stand.
- Versatzvarianz je Achse gemittelt, obwohl entlang/quer verschieden gemessen (`offset_var`, `frames.rs:401`).
- Jenseits der Reichweite + 1 m zählt die Entfernung zweimal: in der Sicht des Sensors (`prior_pd`,
  `world/build.rs:198`) und in der Erfassung (`RateModel::pd`, `ld2450/rates.rs:51`). Gegen welche Sicht ρ und r₅₀
  gefittet wurden, ist nicht festgehalten.
- Erkennbarkeit nur für Stehende; α, Wechselrate, Ankunft/Weggang und Aufenthalts-Prior sind
  angenommen, nicht über die Evidenz geschätzt. Ebenso `guest_rate` (ν), `forget_rate` und `start_unknown`.
- Höchstens 12 Hypothesen (`max_hyps`); Paarung beim Zusammenlegen bis 6 Personen (`filter/merge.rs:376`, darüber in
  der Reihenfolge). Jede neue Spur kann auch „unbekannt“ sein: Solange Spuren laufen, gibt es mehr Hypothesen und mehr
  Rechenzeit.

**Unstimmigkeiten zwischen Code und Absicht (Prüfung 10.11.)**, jeweils zu klären (Pfade unter
`core/presence-core/src/`, sonst unter `tracker/`):
- a) **Kosten der Lichtfehler.** Seit 0.64 im Standard 0,2 (Schwelle 1/6, aus 1/12) nach 1 (Leon 10.11.: etwa 5×).
  Gespeicherte Konfigurationen behalten ihren Wert: Live gilt 0,2 erst, wenn er in der Konfiguration gesetzt ist.
- b) **Geister von Gehenden** (`walker_ghosts`, 4.2). Die Rate steht nur in der Geburtsdichte der Verzweigung
  (`filter/branch.rs:341–349`). Der Faktor „kein Geist von Gehenden entstanden“ fehlt in `Filter::watch`
  (`filter/evidence.rs:52–57`), der nur die übrigen Geisterarten und Störquellen zählt. Es zählen alle Personen mit
  Spur der Hypothese mit ihrem Gewicht auf *geht*, für jeden Sensor, unabhängig von seinem Blick.
- c) **Die Quelle eines Geists** (`Mixture::phantom`, 4.2) bewegt sich ohne Wände (`Bounds::Free`,
  `filter/step.rs:161–165`), wird nicht auf die Sicht beschnitten (`Mixture::in_sight`, `filter/mixture.rs:528`) und
  nie mit „keine neue Spur“ gewichtet (`filter/step.rs:312`, `399`). Eine Person bekommt alle drei.
- d) **Nicht-Wiederfinden (0.62/0.63) gegen *geht*.** `resolved_from` (`filter/evidence.rs:319–333`) übergibt das
  gehaltene Ziel als ruhend (`moving: false`); `RateModel::masks` überspringt dann *geht* (`ld2450/rates.rs:99`). Gegen
  die Komponente *geht* einer anderen Person zählt das Nicht-Wiederfinden so ohne Auflösung, voll.
- e) **Kacheln an Wänden innerhalb eines Raums.** Kacheln werden nur an Raumgrenzen geschnitten (Schlüssel Block und
  Raum, `world/tiles.rs:163–166`); die Wege zwischen Kacheln achten Wände, die Masse innerhalb einer Kachel nicht.
  Eine Kachel kann so beide Seiten einer Wand im selben Raum umfassen, etwa an der Vorratsecke der Küche.
- f) **Außentür ohne gezeichneten Raum dahinter: hinein ja, hinaus nein.** Eine Tür ohne Raum auf der anderen Seite
  ist ein Weg nach draußen (`plan.rs:684`) und damit ein Eingang (`world/tiles.rs:464`). Hinaus geht es nur zu Zellen
  eines Bereichs oder eines Außenraums (`world/tiles.rs:288`); hinter einer solchen Tür liegt keine. Ebenso eine
  Eingangszone im beobachteten Bereich. Abhilfe heute: den Raum dahinter zeichnen und *Eingang* setzen.
- g) **„Ziel“ auch für Räume ohne Sensor.** Die Hülle meldet die HA-Entität „Ziel“ für jede Zone außer dem Haus
  (`presence_tracker/ha.py:30–46`, `180`); der Kern rechnet *Ziel* nur für beobachtete Räume und Flächen
  (`outputs.rs:810–818`), sonst bleibt es aus.
- h) **Bekannte Störquellen gehen beim Neustart verloren.** Der gespeicherte Zustand (`Filter::people_state`,
  `filter/mod.rs:234–298`) trägt Personen und Unbekannte, keine Störquellen (4.5).
- i) **`RESET_HOLD` = 300 s** (wie lange der LD2410C-Hintergrund nach einem Zurücksetzen nicht lernt) ist eine
  Modellzahl in der Hülle (`presence_tracker/app.py:49`), nicht im Kern.
- j) **Die Oberfläche nennt noch die Geisterkarte** (seit 0.55 entfernt): `presence_tracker/static/js/util.js:284`,
  Test `tests/test_geometry_change.py:142`.

Nicht geprüft (Ablationen ausstehend): λ_d (`turn_rate` 0,85) gegen langsamere Richtungswechsel; OU-Näherung
gegen weißes Rauschen in der Beschleunigung; Swerling I (`ld2450/rates.rs:49`) gegen logistisch; Form der Erkennbarkeit.

## 10. Erfahrungen
Was frühere Versionen gezeigt haben: was passierte und warum. Das sind Hinweise, keine Verbote; ein neuer
Ansatz darf alles neu prüfen. Fast alles hier stammt aus der Zeit vor dem 31.10. Die Aufnahmen 6.–14.10.,
die Wahrheitsfenster (die vom 8.10. waren falsch) und die Bewertungswerkzeuge (report_eval, fast_eval)
sind am 31.10. gelöscht; die Zahlen lassen sich nicht nachrechnen. Viele beruhen auf einem Abend, wenigen
Fenstern oder einem einzigen Start. „Schlechter“ heißt dann: auf diesen Daten, nicht allgemein. Die
damaligen Maße gewichteten die Fehler anders als heute (1). Die Versionsgeschichte steht in
`CHANGELOG.md` und in git, nicht hier.

- **Student-t für die Spur einer Ruhenden** (10.11., Branch `sitz-messmodell`, nicht übernommen). Gemessen (App-Aufnahmen
  31.10.–8.11., Abschnitte ≥ 60 s mit genau einem ruhenden Ziel, 116 h): Abweichungen vom Median mit schweren Ausläufern,
  Student-t ν 1,5–2; nur in Zeiten mit einer Person zu Hause (18 h) ν ≈ 3 und weniger Sprünge über 0,5 m (0,3–3,8 %
  statt 2–9 % der ruhenden Zeit): Ein Teil der „Ausläufer“ ist ein Ziel, das zwischen zwei nebeneinander Sitzenden springt.
  Eingebaut als t-Vorhersage in der stehenden Komponente, der Überschuss als Sprung in den wandernden Versatz o (die erste
  Fassung, die ganze Kovarianz mal g, lief bei korrelierten x, c, o bis ±∞/NaN). Das nahm genau diesen Beleg für eine
  zweite, unaufgelöste Person: `evalp` (QUICK, ein Start je Fenster, Sofa/Bad alle Starts) mit `ld_other_room` 0: Sofa
  8.11. dunkel in 10 von 10 Starts (0.63: 1), zwei Personen 7.11. dunkel mit Sitzer 0,7 → 23,4 min, Ein-Personen-Tage
  dunkel mit Sitzer 23,8 → 55,2 min, Schlafzimmer nachts dunkel 1,3 → 10,3 min. Einzelne Starts (sofa10 Start 9 MUSS
  dunkel / zwei0711 Start 0 dunkel mit Sitzer): ν 2 1,5 / 21,2 min, ν 10 0 / 5,8 min, ν ∞ 0 / 0,6 min. Den Sprung von o
  auf 0,3–1 m zu begrenzen änderte am Sofa nichts. Die Meldung 10.11. (Arbeitszimmer) braucht es nicht: ohne t 54,6 % der
  Schritte „gesehen“ (0.63: 7,8 %).

- **Frames als unabhängige Messungen** machen überkonfident; Kovarianz aufblähen half in 0.6.x nicht.
  Daher die Spuren als Messgröße.
- **Partikel über diskrete Zustände mit wenigen Werten** (steht/geht, Aufenthaltsart) streuen stark
  zwischen Seeds und verpassen Seltenes („kein Partikel ist aufgestanden“). Daher aufzählen statt
  ziehen.
- **Person aufs Raster schon beim Verlieren der Spur:** Der gelernte Versatz ging verloren, beim
  Wiederfinden passte die Messung ~9× besser zu einem Geist.
- **Ohne Erkennbarkeit κ** ist eine Sitzende, die minutenlang nicht erfasst wird, extrem
  unwahrscheinlich, und das Modell schickt sie zur Tür.
- **Ohne Geisterkarte** wurden in 0.6.x wiederkehrende Reflexionen (Ecken, Ladestation, Küchentür) zu
  Personen. Die Karte ist seit 30.10. weg, heute gibt es eine globale Geisterrate (4.2); mit dem heutigen
  Filter ist der Effekt nicht neu gemessen.
- **Schnelle zeitbasierte Leckraten** („nach x s ungesehen ist sie weg“) schickten in 0.5/0.6 Sitzende in
  Sekunden zur Tür (ohne Zahlen, Daten gelöscht). Heute laufen Einträge ab, aber nur wo kein Sensor sie
  prüfen konnte (`unseen_life`, 2 min) und sonst langsam (`record_life`, 1 Tag; 5.5).
- **Wiener-Prozess für die Geschwindigkeit** (bis 0.6.21): Gehende behalten ihre Richtung viel zu
  lange und sind zu schnell. Eine OU-Geschwindigkeit war im Partikelfilter 0.6.x trotzdem schlechter
  (Commit 5379fd1); im IMM ist sie ungeprüft drin.
- **Rechenzeit.** Im Python-Kern (bis 0.38) steckte die Zeit im Aufwand je Aufruf auf winzigen Arrays,
  nicht in der Rechnung; Numba-Kernel brachten je Runde 10–30 %, der Kern in Rust (0.39) knapp das Dreifache
  (dieselben 20 min: 47,8 s → 16,8 s CPU). Die Lehre, die bleibt: **Rundung schaukelt sich chaotisch auf.**
  Schon ein BLAS-Produkt in anderer Form (0.31) oder ein anders gerundetes `hypot` (0.53) kippte in
  einer Stunde rund 840 Raum-Sekunden, weil der Schnitt der Hypothesen dort auf Rundungsniveau
  entscheidet. Unterschiede dieser Größe zwischen zwei Fassungen sind Rauschen.
- **Die Zeit des Modells darf den Frames nicht davonlaufen** (0.54.2/0.54.3). Ging das Modell weiter,
  während Frames noch unterwegs waren (Wiedergabe unter Überlast; live ein Stau von HA, Broker oder inge,
  18.10. ~35 s), galt jeder Sensor als still: Spuren endeten als verlorene Daten und begannen mit den
  späten Frames neu, aus 2 Personen wurden 23–25 (Wiedergabe) bzw. kurz 3–4 (Stau). Seitdem geht die
  Zeit des Modells mit dem neuesten Frame, und gestaute Frames bekommen ihre Zeit aus der Laufzeit des
  Boards (4.4).
- **Weniger Schichten je Kachel** (7.10., 11 h, Daten gelöscht): 5 statt 7 Arten des Aufenthalts
  kosteten nichts Messbares, 4 Arten etwas Evidenz, 3 Arten deutlich mehr und mehr Lichtfehler. Heute 5 Arten × 5
  Stufen κ = 25 Schichten von `steht`. Dabei gelernt: Die Grenzen der Hypothesen bewegen die Evidenz um
  Hunderte (5.1). Kleine Unterschiede zwischen Varianten sind nur sicher, wenn sie bei mehreren Grenzen
  dasselbe Vorzeichen haben.
- **Die gemeldete Entfernung des LD2410C erfindet Personen** (0.9.x, Daten gelöscht). Mit fester
  Reichweite 3,3 m musste das lange „an“ des Wohnzimmersensors (Sitzende im Esszimmer in 6,2 m) von einer
  erfundenen Person nahe am Sensor kommen. Die gemeldete Entfernung ist nur der Ring, in dem die Energie
  gerade über ihrer Schwelle liegt: eine Person allein am Schreibtisch in 1,5 m (6.10. 22:20–22:50) lag
  in 27 % der Ruhig-Frames bei 2,6–6 m; das Modell stellte dafür die zweite Person 25 min ins
  Arbeitszimmer. Die Energie in den fernen Ringen ist der Schweif des eigenen Profils (4.3). Heute
  nutzt das Modell die Energien je Ring; die Entfernung dient nur der Weboberfläche.
- **Gauß an Wänden abgeschnitten** (6./7.10., ein Abend, je ein Fall, Daten gelöscht): abgeschnittener
  Kalman-Filter (H_SimonSimon2006) an den achsparallelen Wänden. Half in einem Fall (zum Lichtschalter
  in der Küche), schadete in einem anderen: Der damals nicht kalibrierte Küchensensor maß Personen etwa
  1 m zu weit hinten, hinter der Wand zum Vorratszimmer; abgeschnitten blieb die Verteilung dort
  gefangen und wurde zu ungesehenen Personen in der Ecke (bis zu vier, Küche 14 min fälschlich belegt).
  Lehre: Wände setzen einen Grundriss und Kalibrierungen voraus, zu denen die Messungen passen. Mit
  kalibrierten Sensoren nicht neu geprüft; heute wirken Wände über gespiegelte Sigma-Punkte (5.2).
- **Küchensensor „hinter der Wand“** (7.10., Daten gelöscht): Seine Ziele jenseits der Außenwand sind
  Mehrwegechos von Personen in Ess-, Wohnzimmer oder Flur, kein falscher Maßstab. Bei allen misst
  gleichzeitig ein anderer Sensor dort jemanden, und ihre Entfernung wächst mit dem Abstand dieser Person
  zum Küchensensor (Korrelation 0,79). Kein Maßstab holt sie in den Raum.
- **Kalibrierung der App neu** (7.10., Daten gelöscht). Die alte Rechnung (Paare je Sensorpaar mit
  RANSAC, Spiegel frei) schlug nach einem Gang von 9 min falsche Lagen vor (Arbeitszimmer gespiegelt,
  Esszimmer Maßstab 1,27). Ursachen: die Spiegelsuche hielt einen Sensor fest; fast alle Paare lagen in
  einem Fleck im Flur, der je Sensorpaar nur eine Kombination festlegt; viele falsche Paare; freie
  Maßstäbe in einer Kette; Frames als unabhängig ohne ausgewiesene Unsicherheit. Der Grundriss bestimmt
  Richtung und Lage, keinen Maßstab: gleichverteilt auf der Sichtfläche schiebt er die Punkte an die
  Wände. Heute (`calibration/`): alle Sensoren gemeinsam (Bündelausgleich, Triggs et al. 2000), Paare mit
  Ausreißeranteil (Myronenko & Song 2010), Übergänge zwischen Sichtfeldern (Rahimi, Dunagan & Darrell
  2004), Grundriss je Spur, Maßstabsprior um die anderen Sensoren, Spiegel aus der Konfiguration;
  cluster-robuste Kovarianz plus Modellfehler. Vorgeschlagen wird nur, was bestimmt ist.
- **Ein Gang ist kein Alltag** (7.10., ein Gang und ein Tag Alltag, Daten gelöscht). Ein bewusster Gang
  durch die Wohnung ist in sich stimmig, aber eine ungewöhnliche Auswahl von Orten. Er schlug Flur −17°
  und Esszimmer +3–5° vor, beide „bestimmt“; im Alltag lagen danach mehr Gehende außer Sicht bzw. Tausende
  Flur-Messungen hinter Wänden. Keine Statistik innerhalb des Gangs kann das zeigen. Daher: Ein Wert wird
  nur vorgeschlagen, wenn der Sensor Gehende aus mindestens 3 verschiedenen Stunden hat; die
  Unsicherheit enthält ein Jackknife über bis zu 8 Gruppen von Stunden (Künsch 1989); und nicht, wenn
  die neue Lage mehr Gehende außer Sicht legt.
- **Sigma-Punkte statt Mittel** (7.10., 15 belegte / 41 leere Fenster vom 6.10., Daten gelöscht, 5.2):
  Die Erfassungsraten am Mittel der Vorhersage sind 0, wo das Mittel knapp außer Sicht liegt; dann hat
  „die neue Spur ist diese Person“ das Gewicht 0. Raten über 5 Sigma-Punkte mit Wänden: Licht zu spät
  aus 3,00 → 1,67 von 15, Evidenz +470. 9 oder 25 Punkte änderten nichts: Der Fehler der Näherung ist
  nicht, was fehlt. Das LD2410C ebenfalls über Sigma-Punkte: Evidenz −524, Licht etwas schlechter.
  Ein einzelnes Fenster kippte zwischen Varianten ganz; die Evidenz war das stabilere Maß.
- **Energien gegen einzelne LD2450-Frames** (6.10. 22:20–22:50, eine halbe Stunde, Daten gelöscht)
  wollten eine zweite Person (+224 log für eine zweite in 2,5 m); gegen die geglättete Position der
  Person nicht (−543). Daher zählen die Energien gegen die Personen des Filters.
- **Energien ohne Verzögerung und ohne gemeinsamen Pegel** (7.10., Daten gelöscht): Nach einem Gang
  durch die Küche blieben die Ruhig-Energien 3 s bei 100 und fielen dann langsam; ohne Modell dieser
  Verzögerung holte das Modell eine zweite Person in die Küche. Schübe und gleichmäßig erhöhte Ringe
  nachts erklärten ohne einen gemeinsamen Pegel je Sekunde Personen am Rand des Strahls.
- **Textur je Ring statt Echoquellen** (7.10., Daten gelöscht): Ein langsam veränderlicher Gamma-Pegel
  je Ring (K-verteiltes Clutter, Ward 1981) trennt ein Echo-Ereignis nicht von Personen: Eine Textur,
  die das Ereignis erklärt, erklärt jede Person. Echoquellen haben eine Form (ein Profil) und eine
  Lebensdauer; daran trennen sie (4.3).
- **Echoquellen gegen welche Personen?** (7.10. bis 30.10., 15 belegte Fenster, Daten gelöscht):
  Echoquellen zuerst in der Kette, gegen die Personen, wie sie vorher waren, nahmen eintretenden
  Personen ihre Energie (Licht zu spät aus 5,3 statt 2,0 von 15). Danach waren sie Alternative nur zu
  Personen ohne Spur (3,0 von 15). Seit 30.10. sind sie Alternative zu den bekannten Personen, ohne die Unbekannten
  (10 „Echoquellen als Alternative“).
- **Amplitude je Aufenthalt beim LD2410C** (7./8.10., 15 belegte Fenster vom 6.10. und eine Episode,
  Daten gelöscht). Wie viel eine Person zurückgibt, streut stark zwischen Aufenthalten (4.3); ohne diese
  Amplitude erklärte eine zweite Person oder eine Echoquelle den Überschuss einer lauten Person, und eine
  leise wurde aus dem Raum geschoben. Versucht: (a) Gamma-Amplitude, meist an κ gekoppelt: Evidenz +1800
  bis +2250, aber mehr Licht zu spät aus (3,2–4,4 statt 3,0 von 15); die freie Amplitude nahm
  Fehlpassungen von Kalibrierung und Profil auf. (b) Maßstab oder Schweif je Sensor online gelernt:
  nahmen die Energie Sitzender auf (wer einen Sensor lauter macht, lässt die leise Person dort woanders
  hin), mehr Lichtfehler. (c) Eigene Stufen neben κ: keine neuen Fehler, übernommen. Seit dem Review
  8.10. ein Gitter in log g, über eine Gauß-Copula an κ gekoppelt (`ld_amp_levels` 9, `kappa_amp_corr`
  0,44 aus der gemessenen Rangkorrelation; volle Kopplung war zu stark, gemessen Spearman 0,4).
- **Neubeginn bei neuer Kalibrierung** (bis 0.10.0, eine Meldung 7.10., 6 nachgespielte Neubeginne,
  Daten gelöscht): Jede neue Konfiguration, auch nur Richtung oder Maßstab eines Sensors, ließ das Modell
  mit einer bekannten Person neu beginnen. Waren beide Bewohner in Sicht, erklärte die eine bekannte
  Person die andere, und die Spur am Schreibtisch blieb ein Geist (4 von 6 Fällen). Seitdem bleiben die
  Personen, wenn sich nur Sensoren ändern (5.3). Eine Intensität unbekannter Personen beim Start
  behob den Fall, holte aber an einem Abend eine dritte Person ins leere Wohnzimmer; das lag später an
  einer Episode, die alle Varianten hatten (unten). Heute ist das der Start ohne Wissen (`start_unknown`
  1, 5.3).
- **Neustart mit dem gespeicherten Zustand** (7.10., 15 / 41 Fenster vom 6.10., Daten gelöscht, 5.3): Nach
  einem App-Start mit dem gespeicherten Zustand statt mit festen bekannten Personen: Licht gleich bis auf
  eine Episode von 20 s, Evidenz +215. Ob dabei jemand im Wohnzimmer zurückblieb, kippte mit
  Kleinigkeiten (30 s Pause, eine beendete Spur); Ursache war eine überzählige bekannte Person in allen
  Varianten.
- **Energie auf mehrere Personen aufteilen?** (7.10., Leons Frage zu einer Meldung „zwei im
  Schlafzimmer, eine geht ins Arbeitszimmer, gemeldet werden beide dort“; Daten gelöscht.) Die
  Überlagerung teilt schon: Das Mittel ist die Summe, eine zweite Person am selben Ort bekommt nur, was
  die erste nicht erklärt. Eine Regel „wo schon jemand ist, bestätigt die Energie niemanden“ wäre keine
  Wahrscheinlichkeit und hätte geschadet, als wirklich zwei im Arbeitszimmer waren. Die drei Meldungen des
  Morgens kamen aus der Rate am Mittel statt über die Verteilung (oben, Sigma-Punkte), aus der fehlenden
  Amplitude je Aufenthalt (eine Person gab mehr, eine weniger als das Profil) und aus einer Echoquelle, die gegen Personen ohne Spur gewogen wurde, als
  wären sie da, und die Person dann, als nähme die Quelle ihren Platz ein (oben, Echoquellen).
- **Die Stille vor einem Frame** (7.10., 19 h leeres Haus, Daten gelöscht; 4.3, 4.4): Die Firmware sendet
  einen Frame je LD2450-Frame (0,089 s), solange der LD2450 ein Ziel hat oder ein Flag des LD2410C an ist,
  sonst alle 5 s einen Herzschlag. Der erste Frame nach einer Stille kommt, weil eine Energie stieg.
  Stand er für die ganze Stille davor (bis 2eea738), war die Energie bewegt um 25–30 % zu hoch und der
  gelernte Hintergrund entsprechend. Heute steht die Stille für die Energien des Frames davor (geprüft
  auf Strecken mit vollem Takt: ×0,99–1,07 gegen den vollen Strom). Die Flags sind keine Schwellen auf die
  gemeldeten Energien.
- **Woher erfundene Personen kamen und warum sie blieben** (7.10., ein Tag nachgespielt, Daten
  gelöscht). Neue Personen aus der Intensität waren selten; die überzähligen bekannten entstanden so:
  1. *Das Ende einer Geisterspur sprach für eine Person* (bis 26-mal am Tag). Heute konkurrierende
     Risiken (4.2).
  2. *Harte Schattenkanten*: Eine Spur am Rand eines Türschattens; an allen Sigma-Punkten der Vorhersage
     war die Sicht 0, „das ist Leon“ hatte das Gewicht 0, und Leon war zweimal da. Heute die Sicht über
     den Versatz (4.1).
  3. *Unsichtbare Streifen*: Kacheln entlang einer Wand ohne jede Sicht hielten eine Person. Ebenso 4.1.
  4. *Abschneiden statt Zusammenlegen*: „die Spur war ein Geist“ und „eine neue Person“ blieben zwei
     Hypothesen mit verschiedener Personenzahl; fiel die erste aus den 12, gab es keinen Weg zurück (im
     Haus erwartet 3,9–4,7 statt 2). Heute Bernoulli-Existenz und Zusammenlegen über die Personenzahl
     hinweg (5.5, 5.6).
  5. *Doppelte Personen in Ecken ohne Sicht*: Keine Messung konnte sie treffen, sie hielt sich durch ihre
     bloße Existenz (Küche 0,7–0,9 über eine Stunde). Heute klingt die Existenz ohne Stütze ab (5.5;
     erst 30 min, nach Messung der Lücken echter Sitzender 2 min, Herleitung in 10 „Tote Winkel und
     Existenz“).
- **Die Geisterkarte lernte einen Sitzplatz als Geisterquelle** (bis 0.12.0, ein Abend, Daten gelöscht).
  Zwei Lernschleifen aus dem eigenen Urteil des Filters: Die Karte zählte die Spuren einer Sitzenden, die
  der Filter für Geister hielt, und machte den Platz bis 21-mal so wahrscheinlich; die online gelernte
  Lebensdauer der Geister stieg von 39 auf 148 s, und damit sprach ein langes Leben kaum noch gegen einen
  Geist, das Merkmal, an dem Sitzende von Geistern zu trennen sind. Daher sind die Lebensdauern fest
  (`ghost_types`, 4.2). Die Karte selbst ist seit 30.10. weg.
- **Das Treppenhaus war ein Bereich der Wohnung** (bis 0.12.0): Wer hinausging, blieb dort minutenlang
  „im Haus“, und Home Assistant bekam eine Personenzahl für öffentlichen Raum (Leon, 7.10.: „Die
  Personenzahl hinter Treppe macht keinen Sinn, da dies öffentlicher Raum ist.“). Heute außer Haus (2,
  3.3).
- **Wer hinausging, blieb erwartet** (7.10., Daten gelöscht): Zurückgegeben wurde nur, wer zu weniger als
  1 % *im Haus* existierte. Wer hinausging, ging also mit seinem ganzen Gewicht an die Unbekannten
  draußen, und die kehrten an der Tür zurück und sammelten sich an schlecht sichtbaren Plätzen. Heute
  geht zurück, wer zu weniger als 1 % existiert, wo er auch ist (`give_up`, 5.5).
- **Bewegung als Merkmal einer Person?** (7.10., Leons Vorschlag; ein Tag Aufnahmen, Daten gelöscht.)
  Ein Ziel, das sich bewegt, eine Weile bleibt und neben dem sonst niemand ist, soll mehr als Person
  zählen. Die Literaturform wäre ein Merkmal mit eigenem Likelihood-Verhältnis je Klasse (Lerro &
  Bar-Shalom 1993) mit Verteilungen aus den Daten. Damals gemessen: Geister des LD2450 waren nach dem
  Verwerfen hinter Wänden so selten (drei Spuren an einem Tag), dass sich ihre Bewegung nicht schätzen
  ließ. Der Filter bestätigte Gehende ohnehin in 3–7 s (Lebensdauer der Geister, Türen); die eine
  Ausnahme scheiterte nicht an fehlender Bewegung, sondern daran, dass keine bekannte Person mehr dort
  hätte gehen können. Eine kleinere Entstehungsrate fern von Personen: Evidenz +81 / +2226, Licht und
  Zahl gleich, damals nicht geändert. Am 4.11. forderte Leon die Prüfung erneut; seitdem gibt es eine
  gemessene Rate der Geister mit Gehenden (`walker_ghosts`, 4.2) und 10 „Personen nur mit
  Bewegungsmuster“. `tests/test_together.py` hält fest: Wer allein hereinkommt, ist binnen Sekunden eine
  Person; eine Kopie neben einem Gehenden geht nicht mit.
- **Zwei an einer Stelle: Wer steht, kann zwei sein?** (7.10., Leons Vorschlag; eine Episode im Bad,
  Daten gelöscht.) Zwei standen lange im Bad, angezeigt wurde nach einer Weile nur eine. Ursache war
  nicht das Abklingen allein: Als die Spuren der zweiten Person an der Badtür endeten, lag sie 3 s später
  zu 0,89 im Schlafzimmer ohne Sensor, denn ungesehen im Bad zu gehen kostet die Erfassungsrate,
  ins Schlafzimmer zu gehen nichts. Danach stützte nichts eine zweite: Der LD2450 sah ein Ziel, und die
  Energien des LD2410C trennten eins und zwei im Bad nicht (im Arbeitszimmer besser). Geprüft:
  Überleben abhängig vom Ort (p_S(x), B_GarciaFernandez2018), kein Abklingen, wo sie von einem gemessenen
  Ziel nicht zu trennen wäre. In der Simulation hilft es, in der Episode nicht, weil ihre Masse an der
  Stelle schon klein war. Literaturform: unaufgelöste Messungen (Koch & van Keuk 1997; A_Beard2015).
  Seit 0.62/0.63 (6.–9.11.) nimmt das Modell zwei Ruhende als ein Ziel des LD2450 (10 „0.62: Zwei als
  ein Ziel“).
- **Vorausschauend einschalten und Ziel** (7.10., ein Tag Aufnahmen, Daten gelöscht; „wird betreten“
  seit 0.56 entfernt). Aus Ort und Geschwindigkeit allein zielt man im Median erst 1,0 s / 1,4 m vor der
  Tür auf sie (Tempo an der Tür ~1 m/s, also 1 s ≈ 1 m). Ein Vorlauf von 1 s / 1 m ging für etwa 60 % der
  Eintritte, mit ~4 vergeblichen 30-s-Lichtern je Stunde. Der offene Raumteiler zwischen Ess- und
  Wohnzimmer ist keine Wand; dort blieben vergebliche Lichter bei jeder Schwelle. Wer 1 m vor einer Tür
  auf sie zugeht, geht nur in 75–85 % der Fälle hindurch; mehr gibt es nur aus Wissen über Wege, und das
  nur für Routinen. *Ziel* (6) ist seit 0.56 nur die gelernte Karte; sie ist reine Ausgabe und ändert
  das Belegt-Urteil nicht (`tests/test_destination.py`).
- **Meldungen 7.10. abends** (Bad und Schlafzimmer neu mit Sensor; 11 belegte Fenster, Daten gelöscht).
  Eine Sitzende im Bad an der Westwand: Bad P = 0,00. Zwei Ursachen, beide nachgewiesen:
  1. *Hinter der Wand, an der der Sensor hängt* (4.1): Bad- und Schlafzimmersensor hängen an derselben
     Wand und blicken an ihr entlang. „0,4 m zurück entlang der Sichtlinie“ lag für ein Ziel wenige cm
     jenseits der Wand noch hinter ihr; 11 % bzw. 20 % der ruhigen Ziele zählten nicht. Heute quer zur
     Wand gemessen (2 % / 0,4 %). Allein das: Bad 0,00 → 0,75–0,98.
  2. *Amplitude als 3 Quadraturpunkte* (4.3): Die Person gab ein Drittel des Profils zurück, unter der
     untersten Stufe 0,58; eine Echoquelle erklärte die Energie besser, bis die Person-Hypothese
     abgeschnitten war. Mit dem Gitter in log g: Bad 0,92–0,98.
  Nicht die Ursache: Hintergrund und Kalibrierung. Gemessen schlechter: eine Person mit Spur nicht als
  Alternative zu einer Echoquelle zu wiegen (Bad unverändert, dafür Licht fälschlich an im Flur). Zwei im
  Bett: die zweite liegt ungesehen neben der ersten, nichts stützt sie, P(=2) 0,29; das Licht ist
  richtig.
- **Lernschleifen** (8.10., Review des Algorithmus; Nachspiele und Simulationen, Daten gelöscht). Was die
  App aus dem Urteil des Filters lernt, kann dessen Fehler lernen:
  - *Der Hintergrund lernte Personen*, in beiden Richtungen. Eine gekappte Energie zählte als 100, obwohl
    die Person mehr gab; so lernte der Hintergrund zu wenig (Arbeitszimmer Ring 2 2,0 statt leer 6,5),
    und der leere Platz sah nach dem Gehen wie eine Person aus. Im Schlafzimmer nahm eine Echoquelle die
    Energie der Schlafenden, und der Hintergrund lernte sie (Ring 2 7,7 → 29 über eine Nacht). Heute
    (4.3): gekappte Energien mit E[e | e ≥ 100]; gelernt nur aus der Zeit ohne Ziel des LD2450 im selben
    Gehäuse (ein von den Energien unabhängiges Signal) und nur, soweit keine Echoquelle an ist.
  - *Gemessen schlechter:* (1) Lernen nur, wo nach dem Posterior niemand im Blick ist (Koller et al.
    1994; Toyama et al. 1999): In der Simulation richtig, aber ein neuer Sensor lernte seinen Pegel nie,
    weil die zu leise Annahme eine Person erscheinen ließ, die das Lernen anhielt (ein Sensor, ein Abend:
    vier Stunden Licht im leeren Schlafzimmer). Die Energien halten die Person, die ein zu kleiner
    Hintergrund erfindet. (2) Langsamer vergessen (1 Tag statt damals 6 h): Evidenz −2530. Heute vergisst
    der Pegel mit `ld_forget` (0,7 h) zu einem über Tage gelernten Anker (4.3).
  - *Offen:* Wen der Filter verliert und der LD2450 nicht sieht, lernt der Hintergrund weiter. Aus den
    Energien allein ist eine reglose Person von anderem Hintergrund nicht zu trennen.
  - *Die Echorate lernte aus Personen*: Neben einer Person zählte, was ihr Profil nicht erklärt, als
    begonnene Quelle. Heute nur, wo niemand im Blick ist, und mit der Rate des Priors beurteilt
    (J_Park2020).
  - *Die Zielkarte* (6) ist nur Ausgabe; keine Schleife.
- **Review 8.10.: Abschneiden, κ als Gitter, κ und g gemeinsam** (5.1, 4.1, 4.3; 3 Wahrheitssätze,
  Daten gelöscht). Drei Näherungen, die in der Theorie falsch waren:
  1. *Abschneiden vor dem Zusammenlegen* (5.1): Heute werden die Kinder einer Verzweigung nach ihrem
     Schlüssel zusammengelegt, dann wird abgeschnitten (höchstens `hyp_mass` 10⁻⁷ der Masse verworfen,
     höchstens `max_hyps` 12). Die verworfene Masse war auf allen Sätzen unter 1 in der Evidenz: Die
     Grenzen schneiden fast nur die Zahl, kaum Masse ab.
  2. *κ als Posterior-Gitter* (4.1): 5 Zellen in log κ statt 3 Quadraturpunkten; 3 / 5 / 7 Zellen
     gaben gleiches Licht.
  3. *κ und g gemeinsam* (4.3): Gauß-Copula mit ρ aus der gemessenen Rangkorrelation.
  Licht blieb, wo es war. Die Evidenz bewegte sich je Satz um bis zu einige Hundert, mit wechselndem
  Vorzeichen zwischen 12 und 16 Hypothesen (5.1: nicht sicher). Die Schlafzimmernacht blieb unverändert:
  Die harten Zählungen der Geisterkarte kamen nicht vom Abschneiden, sondern von der Echoquelle, die die
  Schlafenden nahm.
- **Tote Winkel und Existenz** (8.10., Review des Algorithmus; 5.5). Bis 0.22 lief jede bekannte Person ohne
  Spur überall mit e^(−t/2 min) ab, auch in voller Sicht (damals hieß das `record_life`; heute meint der Name
  etwas anderes, unten). Das Review nannte das einen Sterbeprozess. Nachgespielt wie die App auf den Wahrheiten
  6.–8.10. (report_eval; Daten gelöscht), drei Varianten:
  1. *Ohne Ablauf* sammelten sich Einträge mit r 0,8–0,99 aus Identitätsteilungen in schwach gesehenen Ecken
     (Vorratsecke der Küche), und das LD2410C hob dort eine Person in 15 min von r 0,09 auf 0,99. Wer das Haus
     verließ, blieb bekannt; die Rechenzeit verdoppelte sich.
  2. *Tote Winkel als eigene Bereiche* mit Aufenthalt wie 3.3 waren schlechter: Gehende an der Grenze kamen per
     Diffusion hinein und blieben einen Aufenthalt lang. Ein Aufenthalt senkt r nur bei r < 1; Einträge mit
     r ≈ 1 hält er beliebig lange. Die Kacheln geben einem toten Winkel schon den Aufenthalt des Raums. Der Code
     fiel weg.
  3. *Ablauf nur, wo kein Sensor prüft* (`p_S(x)`, auch außer Haus) war am besten: In Sicht blieben Sitzende und
     zwei im Bett, die Vorratsecke wurde nach Minuten vergessen. Vergessen außer Haus erst nach einem Tag ließ
     Gegangene zurückkommen. Das ist heute `unseen_life` (`filter/step.rs`: Rate (1 − Sicht) / `unseen_life` je
     Kachel, in Bereichen und außer Haus ganz).

  *Herleitung der 2 min* (`unseen_life` = 120 s, live). Gemessen bis 0.22 auf 5,6 h bekannter Aufenthalte in
  Sicht (Aufnahmen 6./7.10.; Daten gelöscht). Stütze = eine LD2450-Messung näher als 1 m am Platz oder eine
  Sekunde LD2410C, die eine Person dem Hintergrund vorzieht. Lücken zwischen Stützen: höchstens 18,2 s, 99 % unter
  0,3 s. Ein leerer Platz in Sicht wird schnell widerlegt: Das LD2410C spricht mit −48 bis −125 log je Minute
  gegen eine Person, der LD2450 allein nur mit etwa −0,4. Der Ablauf wirkt also fast nur, wo nichts misst.
  Untergrenze: Eine Sitzende darf in ihrer längsten Lücke nicht unter die Lichtschwelle 2/3 fallen,
  `18,2 s / ln(3/2)` = 45 s. Durchgerechnet für τ von 45 s bis 30 min auf zwei Wahrheitssätzen: Die Unterschiede
  lagen in wenigen Fenstern; 2 min war die kürzeste ohne „fälschlich an“ in beiden. Eine Sitzende behält damit in
  ihrer längsten gemessenen Lücke r ≥ e^(−18,2/120) = 0,86. Die Messung trägt also nur die Untergrenze 45 s; die
  2 min sind auf einer gelöschten Wahrheit gewählt. Hinter Türen in die Intensität vergessen (Bernoulli →
  Poisson) war schlechter: Die Intensität vergisst, dass genau einer dort war (das Bad blieb 0,44 „besetzt“, neue
  Personen kamen leichter heraus).
  Seit 0.60 läuft außerdem jeder Eintrag ohne Spur überall mit `record_life` = 1 Tag ab (unten, „0.60“).
- **Hintergrundaktivität: Lernen pausieren** (8.10.; Leon fragte, ob man die Zeiten, in denen der Saugroboter
  aktiv war, automatisch aus den Trainingsdaten löschen kann). Nachgespielt 7./8.10. (Daten gelöscht): 17–90 min
  Saugroboter verschoben LD2410C-Hintergründe um bis zu +55 % (Küche Ring 2) und füllten die damaligen Geister-
  und Zielkarten entlang seines Wegs. Deshalb ein Schalter, den Node-RED setzt (Leons Vorschlag, 8.10.), und kein
  Wissen über das Gerät in der App. Solange er an ist, lernen LD2410C-Hintergrund, Zielkarte und Kalibrierdaten
  nichts (`learn/`, `calibration.py`). Das Verfolgen bleibt gleich, der Saugroboter darf weiter Licht schalten.
  Die Nachlaufzeit nach dem Andocken gehört zur Automation.
- **Filter oder LD2450 des Raums** (8.10.; live als `seen_hold` = 10 s bis 0.54, entfernt 30.10., unten „Eine
  Zuordnung“). Der Filter verlor damals Sitzende, die der LD2450 des Raums noch maß; der gemessene LD2450 als
  zweite Stütze überbrückte das. Was dabei gemessen wurde (vier Wahrheiten 6.–8.10.; Daten gelöscht): Die
  LD2410C-Flags taugen nicht als Stütze (in der leeren Nacht 74 % der Zeit an). Ein Raum-Latch bis zum Hinausgehen
  scheitert an Übergaben zwischen Sensoren. Nur der Sensor des Raums zählt, nicht jeder, der den Ort sieht (ein
  Flur-Sensor maß die Person am Esstisch aus 6–7 m).
- **Durch die Wand ins Arbeitszimmer** (8.10. 21:35, live 0.25.0; der Bad-LD2450 meldete kurz ein Ziel hinter
  der Wand). Nicht der Wandtest war die Ursache, sondern das Kalman-Update: Mit der Geschwindigkeit eines Sprungs
  lag das Mittel danach 0,3 m hinter der Wand, und für Ausgaben zählt die Seite des Mittels. Seitdem wird die
  Position nach jeder Messung auf die Sicht des messenden Sensors beschnitten (4.1, `filter/mixture.rs`).
  Geprüft und schlechter (je ein bis zwei Fenster; Daten gelöscht): die Person auf die Seite des Ziels spiegeln
  (strenger als das Modell, das ein Ziel bis 0,3 m neben der Person erlaubt) und eine Raumregel mit diesem Punkt
  (Echos knapp hinter einer Wand machten Licht). Der Schnitt hat eine Kehrseite: Bekommt eine Person die Spur
  eines Sensors hinter der Wand, zieht er sie in dessen Raum (Bad 31.10., unten „Nacht 8./9.11.“).
- **Meldungen 8.10. abends: Licht aus mit Personen im Raum** (App 0.24.0; Leon: „Sowas darf nicht passieren.“).
  Bad und Sofa wurden dunkel, während dort still gesessen wurde. Der LD2450 meldete die Sitzenden mit Lücken bis
  140 s (Bad) und 238 s (Sofa). Nachgespielt (Daten gelöscht), zwei Ursachen:
  *Ursache 1:* Jede Komponente einer Person mit Spur zählte im Blick jedes LD2410C, mit null Energie, und bekam
  dort `P(keine Echoquelle)`. Je Block mit einer wahrscheinlichen Echoquelle wuchs so ihr Teil „durch eine Tür
  hinaus“, bei allen Personen im Haus zugleich; die Person im Bad war in 16 s „außer Haus“. Seitdem wiegt ein
  LD2410C nur Komponenten in seinem Blick (4.3).
  *Ursache 2:* Begann eine Spur, wo der Filter niemanden kannte, fiel ihre Alternative „Person“ unter die Schwelle
  des Abschneidens. Danach sagten alle Hypothesen „Geist“, und die Spur blieb einer (Schlafzimmer 8.10.: bis zum
  Morgen jede neue Spur am Bett ein Geist). Seitdem verliert keine laufende Spur eine Alternative ganz (5.1).
- **Kandidat 0.26** (9.10.). Varianten nach den Meldungen 8.10., bewertet auf einer Wahrheit des 8.10. mit einem
  Gast vom Balkon. Diese Wahrheit war falsch (unten „0.27“). Der Gewinn der übernommenen Regel „geschlossene
  Bereiche laufen nicht ab“ kam ganz aus diesem Fenster; 0.27 nahm sie zurück. Die übrigen Varianten (Geburt an
  neuen Spuren, Amplitude je Aufenthalt auf den Kacheln, Balkon-Tor) sind damit unbewertet. Eine Beobachtung
  unabhängig davon (Schlafzimmer 7.10. abends): Eine Person mit kleiner Amplitude erklärt beständige Energie so gut
  wie eine Echoquelle, und ihre längere Lebensdauer spricht dann für die Person.
- **Sitzende im Bad und auf dem Sofa** (9.10.; Leon: „Das Modell sollte nicht darauf optimiert werden, jemand
  Schlafenden zu erkennen. Schlafende Personen wollen eh das Licht aus haben. Es ist viel wichtiger, dass Leute im
  Badezimmer oder auf dem Sofa im Wohnzimmer nicht verloren werden.“). Bewertet auf den Wahrheiten 6.–8.10. (Daten
  gelöscht; das Fenster 8.10. 19:04 noch mit dem falschen Gast). Gemessene Amplitude Sitzender (4.3): Bad 0,35,
  Sofa 0,58; je Platz Streuung von log g 0,3–0,6, zwischen den Plätzen Mediane 0,3–2,9. Was sich zeigte:
  - Personen ohne Spur hatten g = 1. Das LD2410C widerlegte eine stille Sitzende, sobald ihre Spur endete.
    *Gemessene Amplitude weitertragen* (nur was eine Spur gemessen hat, bleibt nach ihrem Ende; ohne Messung g = 1)
    hielt Bad und Sofa ohne zusätzliches Licht im leeren Raum. In 0.27 übernommen, heute in `filter/ld_weigh.rs`.
  - *Voller Amplituden-Prior auf den Kacheln* (jede Person ohne Spur darf leise sein) hielt das Bad, erfand aber,
    wo nichts widerspricht: 24,6 min Licht im leeren Schlafzimmer, jemand im Flur der leeren Wohnung. Eine
    Amplitude, die keine Messung stützt, macht das LD2410C stumm gegen jeden Fehler seines Hintergrunds.
  - Der Hintergrund wandert schneller, als 6 h Vergessen ihm folgen (Flur morgens: gelernt 1,9, gemessen 5,1).
    0,7 h (gemessen) beseitigte die Person im Flur; `ld_forget` ist heute 0,7 h.
  - Ablauf nach Prüfbarkeit (Chernoff-Stein) und ein Hintergrund-Prior aus der Streuung zwischen den Sensoren
    änderten den Flur nicht bzw. erfanden Personen in der Küche (je ein Fenster; nicht belastbar).
- **0.27: korrigierte Wahrheit 8.10.** (10.10.; Leon hat den Abend aus Sensoren und Telefonen neu rekonstruiert.)
  Auf dem Balkon war niemand. Die „Person am Esstisch“ 19:04–19:10 war ein Geist des Esszimmer-LD2450 (frische
  Spur ohne Weg dorthin, ruhige Ringe des LD2410C von etwa 10 auf 42–100; Ursache unbekannt). Das Ziel auf der
  Balkonseite (4,6–5,0 | 2,0–2,3) ist ein wiederkehrender Spiegel- oder Winkelgeist, so weit vom Sensor wie das
  Sofa. 0.27 = gemessene Amplitude weitertragen, Hintergrund 0,7 h, ohne die Balkon-Regel aus 0.26. Lehren: Eine
  Regel, deren ganzer Nutzen aus einem Fenster kommt, hängt an der Wahrheit dieses Fensters. Und 0.26.1 hielt das
  Sofa 19:04 nur zufällig: Ein falscher Eintrag auf dem Balkon war als Ersatz übrig (richtig im Ergebnis, falsch
  im Grund).
- **Gelerntes bleibt bei neuer Kalibrierung** (11./12.10.). Damals behielt die App Geisterkarte und Hintergrund bei
  kleinen Lageänderungen; auf der Wahrheit (Daten gelöscht) änderte das nichts. Überholt: Die Geisterkarte ist seit
  30.10. weg, und seit 29.10. verwirft jede Sensor- oder Geometrieänderung alle Kalibrier- und gelernten Daten
  (Leon, 29.10.).
- **Blick des LD2410C je Raum** (12.–14.10.; live 0.30.0 bis 30.10.). Ein gemessener Faktor je Paar Sensor ← Raum
  statt eines gemeinsamen. Manche Paare waren über Tage stabil (Esszimmer ← Wohnzimmer 0,53–0,65), andere nicht
  (Flur ← Esszimmer 0,51–1,00, ein Tag allein 2,1). Auf der Wahrheit änderte er nichts. Entfernt 30.10. (4.3 „Blick
  in einen anderen Raum“; Daten gelöscht).
- **Kaltstart: die Unbekannten liefen davon** (30.10.; 4.3 „Verrechnung“). Ohne Gelerntes und ohne Personenstand
  stiegen die erwarteten Personen im Wohnzimmer bis 18 (10.10. 10:00), am 8.10. 19:00 bis 43. Ursache war die
  Rechnung, nicht ein Gerät und nicht der Hintergrund. Die Unbekannten galten als „höchstens eine im Blick“, ihr
  Faktor `L̄ (1 + Λ) / (1 + Λ L̄)` wurde aber auf die Intensität angewandt. Für Λ ≪ 1 ist das der Bernoulli-Schritt;
  darüber erlaubt er `Λ' ≤ 1 + Λ`, also bis zu eine Person mehr je Block. Weil die Energie immer nur eine Person
  erklärte, gab es keinen Halt (Test mit den Energien von drei Personen: nach 30 Blöcken 29 im Blick). Schwache
  Gegenbelege wirkten kaum (L̄ = 0,9 bei Λ = 10: −0,1). Im warmen Betrieb lief dasselbe unsichtbar (8.10. 21:10
  Λ = 34 im Blick des Wohnzimmers, Echoquellen dabei aus). Seitdem `1 − M + ∫ m L` wie für eine Person, `M' ≤ 1`
  (4.3; im Code „at most one of them in view“, `filter/ld_weigh.rs`); für kleines M dieselbe Zahl bis auf M². Das
  begrenzt nicht die Personenzahl: Spuren und bekannte Personen ohne Spur sind unbegrenzt. Nur belegt Energie
  allein keine zweite Person je Blick. Erwogen, nicht gerechnet: die Überlagerung mit der eigenen mittleren Masse
  (Mahler 2009; Nannuru, Coates & Mahler 2013). Kaltstart, höchste Personenzahl im Haus vorher → nachher: 21,9 →
  5,5 und 44,6 → 3,7 (je 2–3 h Aufnahme; Daten gelöscht). Preis: Ohne die aufgeblähte Intensität ging die
  Echoquelle wieder an und nahm einer zweiten Sitzenden die Energie (nächster Eintrag).
- **Echoquellen als Alternative** (30.10.; 4.3 „Verrechnung“). Zwei sitzen, der Filter führt eine Person, die
  zweite ist nur Energie. Geht die eine, stand eine Echoquelle 4–9 min lang am Platz der Gebliebenen auf „an“, bei
  20 s Lebensdauer: Im Zustand der Quellen konkurrierte nur „aus“ mit ihr, und die Normierung hielt sie oben. Eine
  Person ohne Spur bekam im Blick `P(keine Quelle) · ℓ`, etwa 0,01 je Sekunde; niemand konnte die Energie
  übernehmen. Der Faktor war die Unabhängigkeit von Person und Quelle, in jedem Block neu angewandt. Bei Ziel gegen
  Clutter steht die Alternative nur im Nenner (B_GarciaFernandez2018 Gl. 45). Seitdem sind Echoquelle und Person
  Alternativen, gerechnet gegen die bekannten Personen ohne die Unbekannten (`filter/ld_weigh.rs`).
  Dazu `Z ≥ 1`: Eine Quelle, die nichts erklärt, ist kein Beleg für eine Person (im Code `ze.max(0.0)`). Ohne das
  zählte das Ende einer Quelle als Beleg für jemanden (Esszimmer 167 min unklar, lange Strecken nachts ohne
  Energie). Die Korrektur von 0.27 erfand Personen, weil `Z < 1` die Unbekannten aufblähte und die Intensität
  keinen Halt hatte; beides ist mit `Z ≥ 1` und `M' ≤ 1` zu.
  Weitere Formen, gemessen mit `fast_eval` über 96,4 h gegen Indizien (`proxy_seated`, keine Wahrheit), meist nur
  eine Stufe, also nicht belastbar (Daten gelöscht):
  - Gegen alle Personen samt Unbekannten: fast dasselbe Licht, doppelte Rechenzeit. Nur gegen Personen mit Spur:
    7,5 min mehr Sitzende dunkel für 13 min weniger Licht ohne Person (zwei Stufen).
  - Bernoulli-Nenner für die Unbekannten: kein Gewinn, mehr Code.
  - Quelle an die Person gebunden (`ρ + ν · P(jemand im Blick)`, J_Park2020 Gl. 16): auf der Wahrheit 3,00 statt
    5,38 min Licht im leeren Raum, auf den Indizien netto schlechter (mehr Sitzende dunkel). Die Zahl begonnener
    Quellen folgte dem Prior von ν; ν war so nicht messbar. Nicht übernommen. Befund: Die Quellen, die wirklich
    etwas halten, hingen an Personen.
  - Schweif oder Gewinn je Sensor fest: kaum Gewinn bzw. im Wohnzimmer 40,8 min mehr dunkel mit Person. Wer einen
    Sensor lauter macht, schickt die leise Person woanders hin.
- **Eine Zuordnung, Nachlauf entfernt** (30.10.; Leon: die Anzahl der Personen bleibt in der Webansicht gleich,
  obwohl eine Person den Raum verlässt; „Die Nachlaufzeit wird ja schon in Node-RED geregelt“, „Ich will, dass das
  Licht dem folgt, was angezeigt wird“, „Die Wahrscheinlichkeit hängt an der getrackten Person“). Bis 0.54 kamen
  Zahl, Licht und Punkte aus drei Quellen (Randverteilung, P(belegt) > c, wahrscheinlichste Hypothese), und
  `seen_hold` hielt einen Raum besetzt, solange sein LD2450 jemanden maß. Jetzt eine Zuordnung (6) mit
  `occupied_off` = 1/3; 0,5 hysteresierte kaum, 0,2 brachte gegen 1/3 fast nichts. Ohne `seen_hold` war auf der
  Wahrheit das Sofa 8.10. 19:06 3,3 min dunkel: Der Nachlauf in Node-RED ersetzt ihn nicht, wenn der Filter
  jemanden verliert, den der eigene LD2450 noch misst. Mit den Echoquellen als Alternative (0.55) hielt das Modell
  das Sofa selbst. Eine Sitzende, die das Modell nur als Energie führt, wird mit der Zuordnung nicht dunkel: Wer
  sitzt, hatte eine Spur und ist eine bekannte Person ohne Spur, und die trägt die Energie. Reine Masse der
  Unbekannten ohne je eine Spur gibt es nur im Kaltstart (`fast_eval` zwei Stufen, 96,4 h; Daten gelöscht).
- **Personen nur mit Bewegungsmuster** (4.11.; Leon, 4.11.: Personen dürfen in der Wohnung entstehen und
  verschwinden, aber nur, wenn das Bewegungsmuster es begründet, damit sich Fehler nicht endlos fortführen). Erste
  Messung auf In-App-Aufnahmen (`dev/personenzahl/evalp.py`), je Fenster ein Start, also nicht belastbar (die
  Zahlen hängen stark vom Start ab, nächster Eintrag). In 0.58 übernommen: breiter Versatz (4.1) und Geister an
  Gehenden (4.2): Bad „eine bleibt“ 5 → 0 s falsch, am 3.11. Personen in zwei Räumen 65 → 6 min. Mit diesem einen
  Start schlechter: κ strenger, Echoquellen 10/60 min, `start_unknown` 0,01, schneller vergessen. Befund: Die
  zweite Schlafende verliert nachts ihre Existenz durch das LD2410C und das Nicht-Erfassen, nicht durch den Ablauf
  (`unseen_life` = ∞ ändert nichts).
- **0.59: Licht nach Node-RED, leeres Haus, mehrere Starts** (4.11. abends; Leon, 4.11.: Licht aus, während
  jemand im Raum ist, ist schlimmer als Licht im leeren Raum; im leeren Haus darf nirgends Licht sein). Seitdem
  simuliert `evalp.py` das Präsenzlicht (an bei belegt, aus nach 120 s ohne) und spielt jedes Fenster von mehreren
  Startzeiten nach. Grundbefund: Die Kennzahlen hängen chaotisch von der Vorgeschichte der Hypothesen ab (3.11.
  „Licht zu viel“ unter 0.58 je Start zwischen 172 und 285 Raumminuten). Ein Unterschied aus einem Start sagt
  wenig. Leere Fenster (stundenlang kein LD2450-Ziel, am 5.11. mit den Telefonen bestätigt): 31.10., 1.11., 2.11.
  Mit 2–6 Starts im Rauschen oder schlechter (nicht belastbar): κ Gamma(2), κ neu gezogen alle 3 min, eine einzige
  Lebensdauer von 600 s (gut, aber nicht gemessen), lange Echoquellen 0,07 × 36 min (Licht im leeren Haus).
  Befund: Aus Dobbys Spuren wurde eine Person, die danach die Energie der Waschmaschine hielt (Bad 3.11.).
- **0.60: Kein Eintrag gewiss, Quellen so beweglich wie Personen, κ gemessen** (5.11.; Leon: 0.59 sei schlechter
  als main). Mittel über 2–6 Starts je Fenster, fünf Ein-Personen-Fenster. `record_life` (jeder Eintrag ohne Spur
  läuft überall mit 1 Tag ab; ein Eintrag ist nie gewiss, D_MusickiEvans2005): Personen zu viel 326 → 247
  Personenminuten, Licht zu viel 523 → 466 Raumminuten. Echoquellen 0,25 m und 8 Stärken: etwas besser. κ α = 3
  (gemessen): Personen zu viel 224 → 167. Leeres Haus überall 0. Geprüft und schlechter: Quellen 0,1 m / 12 Stärken
  (12 min Licht im leeren Haus, doppelte Rechenzeit); lange Quellen 0,07 × 36 min (Bad 3.11. besser, aber die
  Sitzende im Bad 31.10. 19:22 107 s dunkel und 4 h Licht im leeren Haus: lange Quellen nehmen Sitzenden ihre
  Energie). Mehrweg erster Ordnung (Ziel so weit vom Sensor wie die Person, F_Liu2025 Abschn. 2.2): Nur der Spiegel
  am Esstisch passt (0,14 m), sonst kein Muster; kein eigener Baustein.
- **Störquellen** (5.11., Meldung 5.11. 13:51 „Licht im leeren Wohnzimmer“: der Luftentfeuchter; 4.5). Gemessen
  auf App-Spuren leerer Fenster, bewertet mit `evalp.py` über alle Starts. Der Luftentfeuchter wird eine
  Störquelle, jede neue Spur dort hängt sich an sie: Meldung 756 → 0 s falsch, leeres Haus 5.11. 159 / 24 → 0
  Raumminuten. Was die Entwicklung zeigte:
  - *Fester Ort gegen zitternde Person:* Ein fester Ort sagt eine ruhende Spur besser voraus als eine Person,
    deren Ort zittert, bei jeder ruhenden Spur, auch bei einer echten Sitzenden (Test: Kaltstart einer Sitzenden,
    die nur ein Sensor sieht, P(belegt) 0,98 → 0,02). Seitdem zittert der Ort einer Störquelle wie der einer
    Stehenden (`still_noise`, `filter/clutter.rs`) und wird auf die Sicht ihres Sensors beschnitten (Test 0,72);
    `clutter_life` = 1,9 h nach Little (gemessene Belegung je Geburt) statt 5,3 h.
  - *Zwei, die nebeneinander liegen* und die beide LD2450 als ein Ziel sehen: Eine Person plus Störquelle erklärt
    zwei Sensoren mit eigenem Versatz an fast derselben Stelle billiger als zwei Personen; die Erfassung der
    Störquelle (0,115 /s) ist schneller als die einer Stehenden (0,034 κ /s). Leon fragte am 5.11., ob die andere
    Person dann zuverlässig wieder von einer Störquelle zu einer Person wird, und sagte, die Zahl der Schlafenden
    nachts sei egal, solange die Bleibende morgens wieder eine Person ist. Das prüft
    `test_when_one_of_two_lying_together_gets_up_the_other_is_still_there` (P(belegt) > 0,9 nach dem Hinausgehen).
  - *Eintrag an der Wohnungstür nach dem Aufbruch* (leeres Haus 1.11., ein Start von sechs, 236 min Licht im
    Flur). Der Eintrag erklärte den Rest der Flur-Energie, und weil sein Anteil im E-Schritt zählte, lernte der
    Hintergrund ihn nie (Fixpunkt `e = b + r·S`, 4.3). Im Gleichgewicht r ≈ 0,65 (LD2410C +0,5 bis +1,6 je Minute,
    Ablauf −0,6 bis −1,1). Getragen hat ihn auch der Startzustand: Der Hintergrund vom 3.11. lag im Flur halb so
    hoch wie am 1.11. gemessen. Seitdem startet die Bewertung jedes Fenster mit dem Gelernten seiner Sensorlage.
  - *An der Wurzel* (4.3): (a) Der Hintergrund lernt immer ohne die Personen ohne Spur: Die Schlafenden wurden
    Hintergrund (Nacht 1./2.11. 56,6 statt 0,5 min dunkel); schlechter. (b) Dasselbe erst nach 600 s ohne
    LD2450-Ziel im Gehäuse (`ld_anchor_quiet`). (b) galt zuerst als nicht übernommen, weil zwei Tests der
    Lernschleifen fehlschlugen (eine Sitzende, die der LD2450 nur alle 15 min sieht, wird Hintergrund). Mit den
    Störquellen zusammen ist (b) dann 0.61 geworden und steht so im Code (`ld2410/background.rs`). Bewertung `k061`
    gegen 0.60, 22 Starts: leeres Haus überall 0, Maßstab (Licht zu viel + 5 × dunkel mit Sitzer) 974 → 956,
    schlechter nur das Spiegelbild am Esstisch 4.11. 17:25 (182 → 213 s). Die Tests sehen die Sitzende heute so
    selten wie bei Wachen gemessen (alle 240 s). Preis: Wer länger als 10 min ohne LD2450-Ziel still ist, wird
    nach etwa 0,7 h Hintergrund; bei Schlafenden kommt das vor. Es ist eine Schwelle, die das Lernen umschaltet
    (vgl. 1).
- **0.61: Woher „Licht zu viel“ und „Personen zu viel“ kommen** (5.11. abends; je Ein-Personen-Fenster ein Start,
  gegen ein ideales Licht aus der eigenen Sensorspur ≥ 10 s mit 2 min Nachlauf). Von 324 Raumminuten ohne ideales
  Licht sind 98 echtes Licht bei Durchgängen (das Ideal kennt keine unter 10 s). Der Rest gehört zu einer zweiten
  Person: mit junger Spur (< 60 s; geboren aus einer gehaltenen LD2450-Spur am Ausgang des alten Raums, einem Geist
  oder dem Halten eines zweiten Sensors am selben Fleck), ohne Spur (vor allem Bad 3.11., Dobby und Waschmaschine)
  oder mit alter Spur (Spiegelbild am Esstisch). 51 Belegungen einer zweiten Person unter 10 s kosten mit ihrem
  Nachlauf 57 Raumminuten. Gemessen 2./3.11. 01–06 Uhr: Der Schlafzimmer-LD2450 meldet eine zweite Schlafende nur
  in Schüben (Lücken im Median 869 s bzw. 60 s, 90 %-Quantil 53 bzw. 40 min). Das Modell erwartet mit der
  untersten κ-Stufe (0,105) und q ≈ 0,6 neben dem Partner eine Erfassung etwa alle 8 min; darum ist die zweite
  Schlafende abends schon verloren (2.11. 22:49 r 1,0 → 0,07 in 2 min). `kappa_switch` 1/3600 s statt 1/600 s:
  morgens etwas weniger, tags mehr dunkel; ein Lauf, kein gemessener Wert, nicht übernommen (live 1/600 s).
- **0.62: Zwei als ein Ziel** (Meldung 6.11. 19:54: zu zweit im Bad, Leon geht hinaus, die zweite Person bleibt,
  das Licht geht aus). Nachgespielt unter 0.61: Ab 19:28:46 meldet der Bad-LD2450 nur ein Ziel, die zweite Person
  wird eine Person ohne Spur neben Leon. Ihr r fällt in 100 s von 0,997 auf 0,02, Δlogit −10,3, zerlegt:
  **Nicht-Wiederfinden der gehaltenen Spur Leons −6,0**, LD2410C −2,8, keine neue Spur −1,7, Ablauf −0,5,
  Zusammenlegen +0,6. Erst danach wanderte ihre Restmasse nach außer Haus. Ihre frische Spur 19:51:58 war in allen
  Hypothesen ein Geist, weil eine Person dort nur aus der Intensität der Unbekannten kommen konnte (log −13 bis −18
  gegen den Geist). Ursache: Nicht-Wiederfinden zählte gegen jede Person nahe der gehaltenen Stelle, als könne der
  Sensor die Unaufgelöste getrennt wiederfinden. Sie und der Eigentümer sind aber eine Quelle mit höchstens einem
  Ziel (A_Beard2015 Gl. 16–17). Seit 0.62 zählt nur der Anteil, den der Sensor auflöst (4.1). Danach P(Bad ≥ 2)
  im Mittel 0,996. `unseen_life` ∞, `clutter_birth` 0 und andere Auflösungen änderten unter 0.61 wenig: Nicht der
  Ablauf, das Wiederfinden war es (eine Meldung). Bewertung `res062` gegen `k061`, alle Starts: leeres Haus 0,
  Maßstab 956,5 → 958,5 (im Rauschen), Personen zu viel 275 → 407 Personenminuten (Unaufgelöste neben einer Person
  bleiben, auch wenn es keine sind), Rechenzeit 0,87×. Offen: schnelleres Wiederannehmen nach einem Verlust, ohne
  dass Personen ohne begründendes Bewegungsmuster entstehen.
- **Nacht 8./9.11.: Zwei auf dem Sofa, einer geht, Licht aus** (Meldung 8.11. 22:27: „Wir saßen zu zweit auf dem
  Sofa. Ich gehe, das Licht geht aus.“). Nachgespielt aus den App-Aufnahmen; unter 0.62 in allen 10 Starts
  (18:00–21:15) das Wohnzimmer ab 22:25:30 dunkel. Drei Wege, jeder für sich genug:
  - *A, zwei Ruhende als ein Ziel:* Ab 21:47 meldet der Wohnzimmer-LD2450 die beiden (0,45–0,9 m auseinander)
    36 min als ein Ziel. Das Modell erwartete neben einem Ziel eine Auflösung von 0,6/0,9 m; jede Minute ohne eigene
    Spur sprach gegen die Zweite. Gemessen 8.11.: Zwei Ruhende nah beieinander (Sofa, Bett in 7 Nächten) geben in
    1–4 % der Frames zwei Ziele. Maximum-Likelihood der Wiedererfassung einer verlorenen Zweiten (13 Episoden,
    1–30 min): R ≈ 1,5–2,0 m. Unabhängig davon wird ein ruhendes Ziel neben einem Gehenden gemeldet zu 0,4 %
    (< 0,6 m), 13 % (0,6–1 m), 37 % (1–2 m), sonst 89 %: dieselbe Form mit R ≈ 1,75 m. **Der LD2450 trennt Ruhende
    nicht über Doppler von Gehenden; er meldet die Gehende und lässt das ruhende Ziel fallen.** Heute
    `resolution` ruhig 1,4 / 2,1 m (Entfernung / quer), gehend 0,6 / 0,9 m (`params.rs`).
  - *B, die Bleibende geht mit:* Steht Leon auf, sprang der Gehanteil der verdeckten Sitzenden in 2 s von 0,3 % auf
    38 %. Die Bewegt-Energie des LD2410C nahe am Sensor lag beim Gehenden ×3 über dem alten Profil (gemessen 6.10.),
    ein zweiter Gehender erklärte sie besser. Neu gemessen (Frames mit genau einem gehenden Ziel im Gehäuse,
    7.–8.11., 41 883 Frames; Zeiten mit einer Person zu Hause, 24 665): A 178 / 166, n 1,80 / 1,71, δ 0,52 /
    0,55 m, σ 0,67 / 0,69 m, τ 0,48, ℓ 1,50 / 1,43 m; Log-Likelihood +1,3 je Frame, in keinem Sensor schlechter. In
    2–4 m gibt ein Gehender 1,6–2× mehr als das alte Profil. Dazu verbarg die Maskierung neben dem zusammengelegten
    Ziel auch das *Gehen* der Verdeckten: Sie konnte ungesehen aufstehen und sich setzen, und jeder Zyklus erneuerte
    ihren Aufenthalt.
  - *C, die Spur eines fernen Sensors:* Der Esszimmer-Sensor sieht das Sofa in 6–7,5 m und meldet die Zweite 1,5 m
    zu weit an der Südwand (Mehrweg), 20 min lang. Bekommt sie diese Spur, steht sie, wo der Wohnzimmer-Sensor
    niemanden sieht; nach 1–2 min ist Streichen billiger als Umziehen.

  **0.63** = Auflösung je Betriebsart (4.1) und Profil der Gehenden (4.3; heute `ld_moving` gehend in
  `params.rs`). Bewertung `evalp.py`, `sofa10` = 10 Starts 18:00–21:15: Sofa dunkel in 1 von 10 Starts (0.62: 10
  von 10; der eine scheitert an C). Bad 6.11. in 3 von 3 Starts gehalten; Bad 31.10. in 3 von 3 Starts 1,1 min
  dunkel wie 0.62 (Sprung durch die Wand, oben „Durch die Wand“; offen, 9). Ein-Personen-Tage: Licht zu viel
  314 → 323, dunkel mit Sitzer 3,8 → 1,4 min, Personen zu viel 238 → 172. Die Auflösung allein hält das Sofa in
  keinem Start; erst mit dem Profil der Gehenden trägt sie. Mit 10 Starts (Sofa) und 3 (Bad 31.10.) gemessen und
  schlechter: `record_life` ∞ dazu (3/10 dunkel); das Profil *ruhig, geht* neu (2/10); alle drei übrigen Profile
  neu (9/10: eine Sitzende, die wenig gibt, widerspricht sich dann selbst); die Chance des Beginns einer Spur auf
  einer Person mit Spur vor dem Schnitt auf die Sicht (10/10). Nur mit 3 Starts, nicht belastbar (chaotisch:
  dieselbe Fassung gelingt in einem Start und scheitert im nächsten): Amplitude der Gehenden je Block (Gamma(2)),
  dieselbe fest über einen Gang (Swerling III), breiter Versatz auch für die erste Spur einer bekannten Person ohne
  Spur. Doppler-Trennung: gemessen widerlegt (A).
- **Ohne Prüfung entfernt** (0.7/0.8): LD2410C (in der 0.6.7-Ablation nützlich, in 0.6.12/0.6.13
  verbessert; in 0.9 wieder drin, 4.3), Körperabstand zweier
  Personen, Ziele und Wege um Wände, Nachbilder.
- **In 0.6.x getestet und verworfen** (Daten gelöscht): gelernte P_D-Karte, Verhaltenskarten je Ort, harte
  Störzonen, ein fester Versatz je Sensor (mehr Doppelzählungen; jetzt als Versatz je Spur gelöst).
- **Lernen aus dem eigenen Urteil** lernt die eigenen Fehler mit (Verhaltenslernen in 0.6.0 deshalb aus). Ganz
  vermeiden lässt es sich nicht: Der LD2410C-Hintergrund lernt mit μ aus den Personen, wie der Filter sie sieht
  (`ld2410/background.rs`). So lernt er, wen der Filter verliert, und behält, was ein falscher Eintrag erklärt
  (oben „Störquellen“, 10 „Lernschleifen“). Die Gegenmittel im Code sind die Stille vor dem Lernen
  (`ld_anchor_quiet`) und die Lernpausen.
- **Eine einzelne Prozentzahl verdeckt Fehler** (in 0.6.4 versteckte sie einen ganz falschen
  Küchenschritt). **Gute Werte in der Simulation** sagten wenig: 0.5.0 war dort gut und in der Praxis
  schlecht.
- **Tür: Spur-zu-Spur** (11.11., Zweig `tuer-fusion` auf `kandidat-064`, 5.1; Meldung 10.11. 10:16 „Wenn eine Person den
  Raum wechselt, geht sie oft an der Grenze verloren …“, `20261110-101641-haus-inaccurate`). Nachgespielt (rprobe,
  10:14:45–10:15:25): Der Flur-LD2450 misst sie bis 10:14:48,3 (y 4,92), danach führt er die Spur nur weiter (gleiche
  Geschwindigkeit, „coast“; die Stellen liegen zudem hinter der Wand), sie endet 10:14:49,7. Die Arbeitszimmer-Spur 9 läuft
  ab 48,2 neben der weitergeführten Stelle (0,3–0,8 m); gemessen lagen beide Spuren nur 48,2–48,3 (0,1 s) ≤ 0,5 m beieinander.
  Die Mischung der Person blieb an der Wand hängen (Mittel bei 49,2 (−1,39 | 4,78), Spur 9 bei (−2,26 | 5,93); beide
  Sensoren setzen sie etwa 0,4 m westlich der Tür). Die Hypothese „9 gehört einer Person“ gab es schon (mit einer zweiten,
  verdeckten Person, deren r verblasst), 1–5 nats hinter „9 Geist, sie verdeckt“; mehr Hypothesen ändern das nicht.
  *Kennzahl* (Schritte zu 0,5 s ab 10:14:50 mit einem Arbeitszimmer-Ziel, in denen niemand „gesehen“ ist, von 65; gezählt mit
  `count.py`, das auch gehaltene Ziele zählt): ohne 62. Übergabe (5.1) [0,6, 0,75, 0,75] 8, [0,9, …] 6. Erste Fassung (auch
  zwei gemessene Spuren, nicht nur eine weitergeführte) [0,5, 0,75, 0,75] 9, [0,9, …] 6, [0,5, 0,5, 0,75] 8, [0,5, 0,75, 1,0]
  25 (der Lauf zu kurz, bevor die Flur-Spur endet). Nach dem Lauf (49,2) liegt „9 ist ihre“ bei −2,95 (0,9) und führt ab
  50,3 bis 10:15:20. Die übrigen Schritte ab 10:15:20 sind ein anderer Fall: Spur 9 wird gehalten und an der Ostwand
  (−0,96 | 8,52) wiedergefunden, die Sitzende am Schreibtisch ist wieder „Geist + verdeckt“ (Meldung 10:18, unten).
  *Skalen gemessen* (`dev/personenzahl/fusion_coast.py`, Aufnahmen; Spuren wie `versatz.py`, A nur gemessene Frames;
  „sicher Person“ = gehend ≥ 10 s und ≥ 2 m Weg; Ein-Personen-Zeiten 19,8 h am 2.–4.11. und 8.11., Zwei-Personen-Zeiten
  21,9 h am 31.10. und 6.–8.11.). Läufe beider gehend ≤ 0,75 m ≥ 1 s (B gemessen oder weitergeführt): zwei sichere Spuren
  in Ein-Personen-Zeiten 67 von 362 Paaren, eine nicht sichere neben einer sicheren 32 von 666; in Zwei-Personen-Zeiten
  zwei sichere, über ihre Überlappung im Median > 1,5 m auseinander (zwei Personen), 5 von 391, nahe 104. Nach einem Lauf
  ≥ 0,75 s trennen sich (> 2 m von jeder sicheren Spur, ≥ 3 Messungen) nicht sichere Spuren in Ein-Personen-Zeiten in 4 von
  40 Fällen, sichere derselben Person in 17 von 69 (das Maß hat selbst diesen Fehler). Nur Übergaben (B weitergeführt,
  `BCOAST=1`): selten, 23 Paare mit Lauf ≥ 0,75 s in 41,7 h, keines zwischen zwei entfernten Personen; danach getrennt
  Ein-Personen 0 von 6, Zwei-Personen 6 von 11 (zusammen 10 von 16 nicht: daher 0,6; wenige Fälle). 0,75 m: zwei
  Sensoren auf einer Gehenden im Median 0,64 m (4.1); 0,75 s: die längste Zeit, die im Türfall vor dem Ende der Flur-Spur
  bleibt.
  *Bewertung* (`evalp` QUICK, Schwellen light_cost 0,2 / aus 1/12 offline mit `schwelle.py`): `gq1q` (Kandidat) | `tfoff`
  (dieser Zweig, `fusion` aus, also dasselbe Modell, neu gerechnet) | `tfpl` (an mit 0,001: verzweigt, wiegt fast nichts) |
  `tf09` (erste Fassung 0,9) | `tfho` (Übergabe 0,6). Sofa dunkel sofa10 1 | 2 | 1 | 4 | 5 von 10 Starts, sofa0811 1 | 2 |
  1 | 3 | 4 von 5; MUSS dunkel 0,8 | 1,2 | 0,8 | 1,8 | 2,3 min; Ein-Personen Licht zu viel 884 | 884 | 883 | 886 | 879,
  dunkel mit Sitzer 23,9 | 23,9 | 23,9 | 21,4 | 21,9 min (morgen0411 7,1 → 4,6 / 5,1); zwei dunkel mit Sitzer 3,6 | 3,5 |
  3,7 | 3,6 | 3,4; leeres Haus 34,6 überall; Meldungen gleich. Einzeln nachgespielte Starts (sofa10 2, 3, 5, 9, sofa0811 1,
  bad3110 2, zwei0711 2) geben teils andere Ausgänge als derselbe Start im vollen Lauf.
  **Das Sofa ist zweigipflig:** In allen Läufen hat jeder dunkle sofa10-Start 16–18 Personenminuten „zu viel“ (einer 0),
  jeder helle 40–55. *Berichtigt 11.11.:* `zuviel` in evalp.py rechnet gegen 1, auch in Fenstern mit zwei Personen; die
  hellen Starts zählen in der Sofa-Stunde richtig 2, die dunklen 1 (die Zweite geht schon beim Hinsetzen 21:47 verloren,
  siehe 10 „0.64“).
  Die Übergabe legt Spuren einer Person zusammen (in sofa10 Start 2 der ersten Fassung 21:45:30 und 21:46:17 die zweite
  Person, die mit der ersten zum Sofa geht: zwei gemessene Spuren, daher nur noch Übergaben); der Platzhalter tfpl zeigt,
  dass nicht jede Störung den Ausgang verschiebt, die Gewichte der Übergabe tun es. Feuert je Fenster selten (sofa10 Start 3
  zweimal in 2,5 h, 20:36 und 20:38 an der Flur/Arbeitszimmer-Tür, fast 2 h vor dem Sofa).
  Nicht übernommen (Sofa schlechter; Entscheidung bei Leon). Verworfen: ein Tor gegen die Mischung der Person (griff im
  Türfall nie, die Mischung hing an der Wand); zwei gemessene Spuren als Lauf (zwei Personen auf dem Weg zum Sofa wurden eine).
- **Geister brauchen eine Quelle** (10.11., Zweig `geist-person`, 4.2; Meldung 10.11. 10:18 Arbeitszimmer: Die Sitzende am
  Schreibtisch hat 94 % der Zeit eine ruhende Spur des Arbeitszimmer-LD2450, die App zeigt sie nur 7 % gesehen, die beste
  Hypothese ist „Spur = Geist, Person verdeckt bei (−2,6 | 6,1)“). Form J_Park2020 Gl. 16: Geburtsrate eines Geists
  `λ₀ + ν Σᵢ rᵢ N(z; xᵢ, σ²)` über alle Personen der Hypothese (mit Spur, bekannte ohne, unbekannte), dazu „kein Geist
  geboren“ je Hypothese `exp(−ν Σᵢ rᵢ g_s(xᵢ) Δt)` (`ghost_near` = [ν, σ], Arten wie `ghost_types`). Skalen aus 4.2:
  λ₀ = 3 % der Rate (5,4·10⁻⁷ /m²/s, leeres Haus), ν = 5·10⁻⁴ /s je Person und Sensor (~1,9 /h: 58 % der 1,6 Geister je
  Sensorstunde über dem Zufall neben einem Ziel, etwa 0,5 Personen im Blick), σ = 0,5 m (≈ 90 % ≤ 1 m). Nicht neu
  gemessen: Spuren aus den Aufnahmen ohne Wandschnitt (wie `versatz.py`) geben im leeren Haus 13 Geburten je
  Sensorstunde, nicht die 0,05 der App; dafür bräuchte es die Spuren der App.
  *Wirkung auf die Meldung* (rprobe 10:08–10:18:16, Schritte mit Arbeitszimmer-Ziel, in denen P1 „gesehen“ ist): 0.63 174
  von 2 227 (7,8 %), mit Kern 301 (13,5 %), nur λ₀ ohne Kern 263 (11,8 %). Der Kern wirkt nur bei der Geburt: Spur 4
  (10:06:16–10:15:45) beginnt 1,6 nats für „Person“, der Vorsprung des Geists wächst danach in ~100 s auf ~10–12 nats
  und bleibt dort (der Geist zahlt seine 1/39 s, die Person auf der Spur ebenso viel an Fehlpassung, vor allem das
  LD2410C des Flurs); mit Kern beginnt sie ~2,4 nats weiter vorn. Die verdeckte Person lag 1,55 m neben der Spur, also
  höchstens ln(1,8·10⁻⁵ / λ₀) = 3,5 nats je Geburt. Eine Kopplung über das Leben des Geists (Quelle muss neben einer Person
  bleiben) gäbe hier wenig mehr: Die verdeckte Person ruht, ihre Lage bei der Geburt entscheidet.
  *Bewertung* (`evalp.py`, Lauf `gq1` gegen `b063`, light_cost 2): leeres Haus 34,8 → 32,9 Raumminuten (nur leer0711, das
  Ziel 0,3 m vor dem Wohnzimmer-Sensor; alle anderen 0); Ein-Personen Licht zu viel 843 → 852, dunkel mit Sitzer 23,8 →
  25,6 min, Personen zu viel 362 → 361; zwei Personen dunkel mit Sitzer 4,8 → 3,7, MUSS-Raum dunkel 1,5 → 0,8 min (bad3110
  in 1 statt 3 von 3 Starts 1,1 min dunkel; sofa10 1 von 10 wie 0.63, anderer Start; sofa0811 1 von 5 wie 0.63);
  Meldungen Bad 31.10. 19:22 177 → 0 s falsch, Sofa 8.11. 22:27 24 → 0 s, Bad 6.11. 122 s wie 0.63, die übrigen gleich.
  Das Mehr an „dunkel mit Sitzer“ bei einer Person kommt allein aus den Morgen mit je einem Start (morgen0411 5,1 → 8,0,
  morgen0211 5,6 → 4,4, morgen0311 5,0 → 5,2); die sechs Ein-Personen-Fenster mit 3 Starts sind gleich. Die Kennzahl zählt
  jede ruhende LD2450-Spur ≥ 60 s, Geister am Ort eingeschlossen; morgen0411 06:5x ist das Esszimmer (der Spiegel am
  Esstisch, 9). Mit light_cost 0,2 (`gq1lc02`; ohne passenden Bezug gerechnet) 881 / 23,9 / 361, zwei 3,6 / 0,8.
  Rechenzeit (Prozess-CPU, dieselbe Meldung nacheinander): 1,09–1,10×; `cpu_s` der Fenster 1,12× (unter Last, unsicher).
  Nicht übernommen (gemischt; Entscheidung bei Leon). 11.11. nachts: Die zusätzlichen 2,9 min „dunkel mit Sitzer“ am 4.11.
  liegen alle 07:00:30–07:03:39 im Esszimmer, ein ruhendes Ziel bei (2,03 | 4,2–4,4), die Stelle des Tischechos (9); 0.63
  machte dort Licht, A nicht. Nachts nach einem Neustart ohne Wissen (8 Nächte 1.–8.11. ab 02:07, 30 min, `neustart.py`):
  außer dem Schlafzimmer kein Licht, wie 0.63. Seit 0.64 an, zusammen mit `ld_other_reach` (4.3) und light_cost 0,2.
- **0.64: Geister mit Quelle, LD2410C-Sicht in Nachbarräume bis 1,5 m, light_cost 0,2** (11.11. nachts, Schnelllauf
  `QUICK=1`: je Fenster ein Start, Sofa 10 + 5 und Bad 31.10. alle 3 Starts; Schwellen offline mit `schwelle.py`, an 1/6,
  aus 1/12). Gegen 0.63 (`b063q` mit denselben Schwellen): leeres Haus 34,3 → 37,7 Raumminuten (nur leer0711; alle anderen
  0); Ein-Personen Licht zu viel 879 → 901 Raumminuten, dunkel mit Sitzer 23,2 → 21,4 min; zwei Personen dunkel mit
  Sitzer 4,1 → 2,8, MUSS-Raum dunkel 1,5 → 0,4 min (sofa10 0 von 10 statt 1, sofa0811 0 von 5 statt 1, bad3110 1 von 3
  statt 3); Meldung Arbeitszimmer 10.11. 10:18 „gesehen“ 7,8 → 57,9 %. Mit der Übergabe an der Tür (`fusion` 0,6, Lauf `acdq`):
  Licht zu viel 901 → 882, dunkel mit Sitzer 21,5, sonst gleich; Arbeitszimmer 62,5 %, Türgang 10:14:47 verloren 59 → 9 von 65. Rechenzeit (`cpu_s`, unter Last): leer0711 13,0 →
  14,1 min, sofa10 76 → 99 min (+30 %). *Sofa, geprüft 11.11.* (Einzel-Replays Start 21:15/21:00,
  `evalp`-Verläufe aller Starts): In der Sofa-Stunde zählen helle Starts 2 (richtig), dunkle 1. Die Zweite geht beim
  Hinsetzen 21:47:10–21:47:50 verloren: Das Wohnzimmer-LD2410C misst für zwei Sitzende nur das 1,1–1,8-Fache einer
  (Ringe 3–5: 201–246 gegen 139–186), eine neu Sitzende ohne Spur bekommt aber die volle Amplitude g = 1 (9 „Sitzende ohne
  gemessene Spur“); zwei sagen also viel mehr Energie voraus, und eine wird widerlegt. Im Kandidaten trifft das eine
  überzählige Person: ein ruhendes Ziel 0,2–1,0 m vor dem Schlafzimmer-Sensor (8.11. 21:23–21:45, 6 282 Frames; nie
  18–21 Uhr, nie im leeren Haus 7.11. 10–14 Uhr), das als Person mit Spur gilt (E bis 21:47 2,9–3,5). Das 0 von 15 des
  Kandidaten auf dem Sofa ist deshalb **keine stabile Verbesserung**, es hängt an dieser Person. Offen: (a) eine Amplitude
  für Aufenthalte, die ohne Spur neben jemandem beginnen (Prior statt g = 1, oder nicht additive Energie mehrerer im selben
  Ring); (b) ruhende Ziele direkt vor dem Sensor als Störquelle (Höhe 1,5 m, Reflexion 1,0 m: dort sitzt niemand minutenlang;
  dasselbe Ziel im Wohnzimmer leer 7.11. in 99,8 % der Frames, der Fall leer0711); nur zusammen, (b) allein nähme dem Sofa
  die auffangende Person; (c) das zusammengelegte ruhende Paar als eigener Zustand mit eigener Amplitude.
- **Nacht 11./12.11.: Zwei an einer Stelle, eine geht, Licht aus – die Energie zählte die Zweite weg** (Meldung 11.11. 22:53
  „Wo saßen zu zweit auf dem Sofa ist gehört. [Die zweite Person] bleibt da. Das Licht geht aus“, 0.64.0; Leon 11.11.: „Dieser Fehler
  darf nicht mehr auftreten!“). *Was die Sensoren messen* (Rohdaten der Meldung): Der Wohnzimmer-LD2450 meldet die beiden auf
  dem Sofa fast immer als EIN ruhendes Ziel, das zwischen ihren Plätzen springt (Sensor x ≈ −0,3 / +1,0 m); Leon geht 22:48:38
  mit dieser Spur hinaus; 22:48:55 beginnt am Platz der Bleibenden eine neue ruhende Spur und läuft ≈ 90 s, das LD2410C liest
  ruhig 30–100. Die App: Wohnzimmer 0,06–0,48, nie an; schon 22:38 zählte sie dort 1 statt 2. In allen vier Licht-aus-Meldungen
  (31.10. Bad, 6.11. Bad, 8.11. Sofa, 11.11. Sofa) dasselbe: Die Sensoren sehen die Bleibende, der Filter hatte sie gestrichen.
  *Zwei geben nicht mehr Energie als eine* (gemessen 11.11. vormittags, Zweig `sofa-energie`, nicht gemergt; Mittel je
  Ring über dem leeren Hintergrund): Sofa 8.11. Wohnzimmer ruhig Ring 3 / 4 / 5: eine 21:04–21:38 78 / 87 / 66, zwei
  21:50–22:20 61 / 76 / 45; Betten (zwei nachts gegen eine nach dem Aufstehen) Ring 3: 55 / 48 / 53 / 72 gegen 49 / 43 /
  57 / 65 (2.–5.11.). Was eine Person je Haltung gibt, streut um den Faktor 2–3.
  *Wo sie verloren geht:* Nachgespielt ab dem Modellstart der App (31.10. 11:35, ganzer Tag; die Starts später am Tag geben
  andere Ausgänge, das Modell ist chaotisch): 18:58 zwei im Bad, die Zweite ohne Spur mit r ≈ 1; 19:10–19:11:50 fällt r auf
  0,05, ohne dass jemand sich bewegt. Ein Protokoll jeder Änderung von r mit Ursache (Debug-Hook, nicht im Code geblieben):
  LD2410C 17 / 40 nats Log-Odds in den beiden Minuten, Nicht-Wiederfinden und keine neue Spur zusammen < 1 nat. Die Energie
  sank kurz (ruhig 100 → 0–80). Im Unit-Test (zwei an einer Kachel, das LD2410C liest, was eine hineingibt, keine Senke)
  streicht 0.64 die Zweite in 60 s auf r < 10⁻⁹.
  *Varianten* (Bewertung: `evalp` mit allen Starts der Fenster bad3110, bad0611, sofa10, sofa0811 und neu sofa1111 (18:00,
  20:00, 21:00, 21:30), dazu 16 Ereignisse „zu zweit, eine geht, eine bleibt“, die ein Subagent in den Aufnahmen 31.10.–11.11.
  fand und von Hand prüfte (10 tagsüber, 6 morgens im Schlafzimmer); Maß: Anteil der Sekunden im Pflichtzeitraum, in denen
  der Raum belegt ist):
  - 0.64: Bad 31.10. Start 18:30 0 % (3 min dunkel, wie gemeldet), sonst 99–100 %; Sofa 11.11. 98,9–99 % (kurze Lücken).
  - Leistungssumme (`ld_sum` 2, Zweig `sofa-energie`): Bad 31.10. 27,7 %, Bad 6.11. 91–93 %. Im Unit-Test r 0,99 → 0,07 in
    60 s. Dazu bildete sie den Erwartungswert von S^p: Eine unsichere Person (Masse m) gab √m statt m, die Unbekannten mit 5 %
    also 22 % einer Person.
  - Maximum statt Summe (`ld_max`): Bad 6.11. 90,9 % in 2 von 3 Starts. Dort strich der LD2450 die Zweite um 19:46 (34 nats in
    einer Minute: Leon stand 1,5 m weiter, die Sitzende war damit „auflösbar“ und wurde nicht erfasst); das Maximum änderte
    auch die Gewichte der Hypothesen.
  - Maximum + ein Anteil vom LD2450 nie gesehener Ruhender (κ = 0, 20 %): Personen ohne Beleg wucherten (Dutzende Einträge mit
    r 0,01–0,2 um 19:39), ab mehr als 6 ohne Spur paart das Zusammenlegen nicht mehr (5.6), und die echte Zweite verwischte.
    Beide Halte gegen zu viele Personen (LD2410C und LD2450) zugleich wegzunehmen ist falsch.
  - Energien ohne jeden Einfluss auf die Existenz (`ld_exist` 0 ohne `ld_alone`): alle 8 gemessenen Starts 100 %, aber
    Rechenzeit Bad 31.10. ×3,7, und eine Person allein an einer Stelle ohne Energie widerlegt das LD2410C dann nicht mehr.
  - Erste Fassung „nur wo niemand sonst die Energie erklärt“ (Deckung = Energie der übrigen in denselben Ringen): alle
    Zwei-Personen-Starts gehalten, aber eine Person allein 2.11. 08:13–09:27 Licht zu viel 60 → 113 Raumminuten: Ab 08:21 saß
    sie am Esstisch, das Küchen-LD2410C sendete nur „kein Wert“, und das Esszimmer-LD2410C sah einen Platz in der Küche in
    derselben Entfernung wie den Esstisch: Die Deckung hielt dort eine überzählige Person. Das LD2410C hat keinen Winkel;
    ob sich zwei verdecken, hängt am Abstand im Raum.
  - Nähe statt Deckung, jede Person mit ihrem r: Küche wieder 61, aber Rechenzeit bis zum 10-Fachen (ein0811 9 → 149,
    nacht0203 14 → 118 CPU-Minuten): Reste von Geistern (r 0,01–0,3) an derselben Stelle schützten sich gegenseitig.
  - **Übernommen (0.65): Nähe, nur durch Personen der Anzeige** (4.3; mit Spur, oder ohne mit r ≥ 0,5). Pflichtraum mit
    Präsenzlicht dunkel: 0.64 1 von 37 Starts (bad3110 Start 18:30 1,1 min), 0.65 0 von 28 (bad3110 3, bad0611 3, sofa0811
    und sofa1111 je 3, 16 Ereignisstarts tagsüber; sofa10 nur mit der Fassung davor, 0 von 10). Schlafzimmer morgens (6
    Ereignisse, je 2 Starts): 0.64 dunkel in 4, 0.65 in 5 von 12; 7.11. 07:08 Start 1 schlechter (belegt 97 → 25 %), 9.11.
    06:40 Start 1 besser (2 → 7 %); 5.11. und 9.11. sehen beide Fassungen die Liegende kaum (8 % / 2–7 %). Unbelegt im Pflichtzeitraum 183 → 83 s;
    die Lücken liegen direkt nach dem Gehen (Sofa 11.11. 22:48:45–55, Arbeitszimmer 11.11. 13:43:09–37), Präsenzlicht
    überbrückt sie, 0.64 hatte dort 0–2 s. Tagesnachlauf Bad 31.10. ab dem Modellstart: Die Zweite bleibt (r 0,92–1),
    das Bad ist nach 19:18 durchgehend belegt (0.64: dunkel 19:19:33–19:21:44). Leeres Haus, eine Person, Nächte: gleich
    (leer0211b 0, ein0311 147, nacht0708 Schlafzimmer 0,98; mit der Fassung davor alle Fenster ±1–5 Raumminuten, leer0711
    33,5 → 33,2). Rechenzeit (Prozess-CPU, unter Last): bad3110 1,5×, leer0211b 1,6×, ein0311 2,4×, nacht0708 4×.
    Offen: die Lücke direkt nach dem Gehen (10–30 s statt 0–2 s) und die Rechenzeit, wo zwei lange beieinander sind
    (Bett, Sofa).
