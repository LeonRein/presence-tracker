# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand 0.8.0 (Entwurf zum Review, Branch `bayes-det`, 6.10.2026). Dieses Dokument ist die
Spezifikation: Der Code wertet genau dieses Modell aus. Jede Zahl steht mit ihrer Herkunft da:
**gemessen** auf Aufnahmen, **geschätzt** über die Evidenz ungelabelter Aufnahmen (Abschnitt 7) oder
**angenommen**. Jede Form (Verteilung, Prozess, Näherung) hat eine Quelle aus dem Literaturordner
(Dateikürzel wie dort, Seite) oder ist als *eigene Herleitung* markiert. Die Wahrheitsdatenbank
(Abschnitt 8) dient nur zur Bewertung, nie zum Einstellen.

## 0. Was sich gegenüber dem 0.7.0-Entwurf ändert, und warum

Der 0.7.0-Entwurf hat die richtige Messgröße eingeführt: die Spuren des LD2450 statt einzelner Frames
(Abschnitt 4). Das bleibt. Die Inferenz hat sich aber festgefahren. Jeder Fehler auf den Episoden wurde
mit einer weiteren Stichprobentechnik geflickt: Mindestanteile, defensive Gewichte, Mindestzahl je
Betriebsart, gemeinsame Wolken. Beispiele: Seeds, die um 60 Prozentpunkte auseinanderlaufen; eine Person,
die nach 15 min am Tisch aufsteht und deren Hypothese einbricht, weil von 100 Partikeln keines
„aufstehen“ gezogen hat. Die Ursache ist jedes Mal dieselbe: Diskrete Zustände (steht / geht,
Aufenthaltsart, Ziel) werden **gezogen**, obwohl sie nur zwei bis wenige Werte haben.

0.8.0 rechnet sie **aus**. Der Filter enthält keine Zufallszahlen mehr:
- **Personen mit einer messenden Spur:** eine Gauß-Mischung über die Betriebsart (steht / geht), je
  Komponente ein Kalman-Filter. Das ist das Standardverfahren für Systeme mit wechselnden Betriebsarten
  (GPB / IMM, Momentenabgleich: Buch_SarkkaSvensson2023 S. 352; für Mehrzielfilter mit Betriebsarten:
  B_Li2019 S. 3–5, Gl. 19–33). Wer aufsteht, ist eine Komponente mit kleinem Gewicht; die erste Messung
  der Bewegung hebt es sofort hoch. Es gibt nichts, was „nicht gezogen“ werden kann.
- **Personen ohne messende Spur:** ein Punktmassenfilter auf einem Raster, wie im 0.7.0-Code. Für
  endliche Zustandsräume ist der Gitterfilter die exakte Lösung (0_Arulampalam2002 Abschn. II-B).
- **Hypothesen über die Eigentümer der Spuren:** exakt aufgezählt, nach Gewicht abgeschnitten
  (B_Vo2017 S. 2–3).

Damit liefern gleiche Daten immer gleiche Ergebnisse (keine Seeds mehr), und der Rechenaufwand hängt
nicht mehr an einer Partikelzahl.

**Gestrichen** (bis ein Vergleich über die Evidenz oder die Episoden sie belegt, Abschnitt 9):

| Element (0.7.0-Entwurf) | Grund |
|---|---|
| Ziele, Wege um Wände, Social Force (3.1) | Die Literatur begründet Ziele für lange Vorhersagen ohne Messung. Gemessene Personen brauchen sie nicht. Ungesehene liefen schon im 0.7.0-Code ohne Ziele, als Zufallsweg. Nie per Ablation geprüft. |
| Nachbilder (4.2) | Aus zwei gesehenen Fällen abgeleitet; im Code schon entfernt. |
| Körperterm 0,3 m (3.1) | Mit unabhängigen Personen je Hypothese nicht darstellbar. Wichtig nur für „zwei Sitzende nebeneinander“, und das ist fürs Licht gleichgültig. |
| feste Bewohnerzahl ohne Gäste | Ersetzt durch Gäste als Poisson-Teil (2, 3.4, 5.5). |

## 1. Grundsätze

1. **Ein generatives Modell.** Es beschreibt, wie Personen und Geister sich bewegen und wie die
   Sensoren daraus ihre Ausgaben erzeugen. Der Tracker rechnet daraus rückwärts (Bayes). Keine Regel,
   keine Schwelle, die einen Mechanismus an- oder ausschaltet.
2. **Das Modell beschreibt, was der Sensor tatsächlich ausgibt.** Der LD2450 ist selbst ein Tracker
   (4.1), gemessen wird auf der Ebene seiner Spuren. Die Ausgabe eines Trackers in jedem Zyklus als neue
   Messung zu nehmen, macht massiv überkonfident (E_Aeberhard2017 S. 82).
3. **Personen entstehen und verschwinden nicht im Raum.** Ins Haus und hinaus nur über die Wege nach
   draußen, in den beobachteten Bereich und hinaus nur durch Türen.
4. **Ein Modell, mehrere Darstellungen.** Ob eine Person als Gauß-Mischung oder auf dem Raster
   gerechnet wird, ist eine Frage der Inferenz, nicht des Modells. Beide rechnen dieselbe Dynamik (3)
   und dieselbe Messung (4). Wo eine Darstellung nähert, steht es dabei.
5. **Deterministische Inferenz.** Was diskret ist und wenige Werte hat, wird aufgezählt, nicht gezogen.
6. **Parameter aus Daten ohne Wahrheit** (I_Kantas2015), Formen aus der Literatur.
7. **Was nicht nachweislich hilft, fliegt raus.** Jedes Element hat einen Ablationsvergleich (9).

## 2. Die Welt (Zustand)

**Geometrie** (aus dem Grundriss, wie bisher): Räume, die die Sensoren zu mindestens 60 % sehen, bilden
den beobachteten Bereich. Räume ohne Sensor, die über Türen zusammenhängen, bilden je einen Bereich
*Rₖ*; er ist *offen*, wenn man von dort das Haus verlassen kann. Dazu kommt *außer Haus*.

**Zustand zur Zeit t:**

| Teil | Inhalt |
|---|---|
| Bewohner i = 1 … N | Ort (beobachteter Bereich, *Rₖ*, außer Haus). Im beobachteten Bereich: Position x, Betriebsart *steht* (mit Aufenthaltsart l, 3.1, und Erkennbarkeit κ, 4.1) oder *geht* (mit Geschwindigkeit v). In *Rₖ*: wie lange schon dort. |
| Gäste | ein Poisson-Punktprozess über denselben Zustandsraum (B_GarciaFernandez2018 S. 3, Gl. 7–10) |
| Spuren | je laufende Spur eines Sensors: Eigentümer (ein Bewohner, ein Gast oder ein Geist) und Versatz auf dem Eigentümer (c, o; 4.1) |
| Geister | je Geisterspur ihre Quelle: Position und Betriebsart wie bei einer Person |

N ist die Zahl der Bewohner (Einstellung). Wer gerade außer Haus ist, gehört trotzdem dazu. Gäste
kommen und gehen nur über die Wege nach draußen. Personen sind anonym; Nummern sind Buchführung.

## 3. Dynamik

### 3.1 Stehen (und Sitzen)
- **Position:** bleibt bis auf ein kleines Zittern, Diffusion 0,02 m/√s (**gemessen**, wie bisher).
- **Wie lange man steht:** Die Überlebensfunktion der Aufenthaltsdauern ist
  `S(t) = (5,6 s / (t + 5,6 s))^0,58` (Lomax, **gemessen**, wie bisher).
  - Eine Lomax-Verteilung ist exakt eine Exponentialverteilung, deren Rate selbst Gamma-verteilt ist:
    λ ~ Gamma(Form 0,58; 5,6 s). Jeder Aufenthalt hat also seine eigene Rate des Aufstehens.
  - Diskretisiert in L = 7 Aufenthaltsarten l mit Raten λ_l und Gewichten w_l (Mischung von
    Exponentialverteilungen, Feldmann & Whitt 1998; Code aus 0.7.0).
  - Wer steht, führt die Wahrscheinlichkeiten p_l der Aufenthaltsarten mit. Weiterstehen über Δt
    verschiebt sie exakt zu den langsamen Arten: `p_l ← p_l · e^(−λ_l Δt) / Σ`. Das ist ein
    Hidden-Semi-Markov-Modell, geschrieben als HMM mit Zusatzzustand (D_Yu2010 Abschn. 3.2, Gl. 22).
- **Aufstehen:** mit der Rate `Σ_l p_l λ_l`. Danach geht man (3.2), in eine gleichverteilte Richtung.

### 3.2 Gehen
**Der Prozess** ist ein Geschwindigkeitssprung-Prozess, wie im 0.7.0-Code für Ungesehene:
- Man geht mit Tempo s geradeaus. **Gemessen** (6.10., 8,7 h LD2450-Spuren vom 5./6.10., Wege von
  mindestens 2 s): Median 0,85 m/s (10 % unter 0,44, 10 % über 1,37 m/s); s = 0,85 m/s.
- Die Richtung wechselt mit der Rate λ_d auf eine gleichverteilte neue. **Gemessen** an derselben
  Stichprobe: Die Autokorrelation der Geschwindigkeit ist 0,64 / 0,46 / 0,17 / −0,03 / −0,25 nach
  0,5 / 1 / 2 / 3 / 5 s. Die ersten drei Werte passen zu `e^(−λ_d t)` mit λ_d ≈ 0,85 /s. Die negativen
  Werte danach sind Umkehren (in einer Wohnung geht man selten weit geradeaus); das ergibt sich auf dem
  Raster aus den Wänden, die Gauß-Näherung kennt es nicht.
- Man bleibt mit der Rate μ = s / ℓ stehen. ℓ ist die mittlere Weglänge, **gemessen** 2 m (Median
  2,1 m über 579 Wege).
- Eine Wand reflektiert die Richtung. Wer durch eine Tür geht, ist danach im Bereich dahinter (3.3).
- *Gegen 0.6.21:* Dort ist die Geschwindigkeit ein Wiener-Prozess (0,6 m/s²/√s, höchstens 2 m/s). In
  der Simulation behält ein Gehender seine Richtung viel zu lange (Autokorrelation nach 5 s noch 0,54)
  und geht zu schnell (Median 1,33 m/s). Wer abbiegt oder kurz nicht gemessen wird, dem läuft die
  Vorhersage in der alten Richtung davon. Das passt zu Personen, die beim Gehen springen (Leon, 6.10.).
  *Aber:* Eine OU-Geschwindigkeit wurde in 0.6.x schon einmal versucht und war auf den Episoden
  schlechter (Commit 5379fd1). Deshalb ist das in 9 eine Ablation, keine Annahme.

**Seine Gauß-Näherung** (für gemessene Personen, 5.2; *eigene Herleitung*):
- Für diesen Prozess gilt `E[v(t)·v(0)] = s² e^(−λ_d t)`, solange man geht.
- Ein Ornstein-Uhlenbeck-Prozess mit Zeitkonstante τ = 1/λ_d und stationärer Varianz s²/2 je Achse hat
  genau dieselben ersten und zweiten Momente. Er wird exakt diskretisiert (Buch_SarkkaSolin2019
  Bsp. 6.2, Gl. 6.29/6.30; der Ort als Integral davon).
- So bestimmen λ_d und die Gauß-Näherung dieselbe Größe. Der gemessene Wert ersetzt das bisherige weiße
  Rauschen in der Beschleunigung (0,6 m/s²/√s), unter dem die Geschwindigkeit ohne Grenze streut.
- Wände und Türen kennt die Gauß-Näherung nicht. Sie gilt nur, solange eine Spur misst (5.2); dann
  liegen zwischen zwei verwendeten Messungen höchstens Sekundenbruchteile.

### 3.3 Türen, Bereiche ohne Sensor, außer Haus (übernommen)
- Wer durch eine Tür geht, ist danach im Bereich dahinter.
- Aus einem Bereich *Rₖ* kommt man mit der Ausfallrate seiner Aufenthaltsdauer wieder heraus, gehend an
  einer seiner Türen. Die Dauer ist log-normal, Median 2 min (geschlossen) bzw. 30 min (offen), breit
  (angenommen).
- Außer Haus: aus einem offenen Bereich mit 1/(2 h), zurück mit 1/(4 h) je Weg (angenommen).

### 3.4 Gäste
- Gäste kommen als Poisson-Prozess an jedem Weg nach draußen an, mit Rate ν je Weg (angenommen:
  1/(2 Tage)).
- Danach verhalten sie sich wie Bewohner (3.1–3.3) und gehen über denselben Weg wieder.
- Damit braucht es keine „Gästeplatz“-Regel: Ein dritter Mensch ist so wahrscheinlich, wie ν und die
  Daten es sagen.

## 4. Messmodell

### 4.0 Warum nicht je Frame (übernommen)
Der LD2450 ist ein Tracker (**gemessen**, 5.10., 16 h):
- etwa 23 Spuren je Sensor und Stunde, Median 60 s, bis zu 10 min;
- die Fehler zweier Sensoren auf derselben Person sind nach 20 s noch zu 0,41 korreliert.

Jeden Frame als unabhängige Messung zu nehmen, ist der Fehler aus E_Aeberhard2017 S. 82.

### 4.1 LD2450: Spuren (übernommen, bis auf die Erfassungsform)
**Decodieren** (`sensortracks.py`, nur das Format):
- Die drei Plätze rücken auf; Ziele werden über die Nähe von Frame zu Frame verknüpft.
- Gehaltene Frames sind keine Messung: Der Sensor lässt ein verlorenes Ziel mit der letzten
  Geschwindigkeit weiterlaufen oder friert es bitgleich ein.
- Ziele hinter Wänden, außerhalb aller Räume oder näher als 0,3 m am Sensor: keine Information.

**Ereignisse und ihre Wahrscheinlichkeit, gegeben der Zustand:**
- **Erfassung (neue Spur).** Eine Person ohne messende Spur dieses Sensors bekommt eine mit der Rate
  `ρ_m · P_m(r) · g_s(x) · q(x)`:
  - `κ`: die **Erkennbarkeit** einer stehenden oder sitzenden Person in diesem Aufenthalt (Haltung,
    Platz). Wie oft der LD2450 Sitzende erfasst, schwankt stark: dieselbe Person am Tisch alle halbe
    Minute, auf dem Sofa minutenlang nicht. κ ~ Gamma(α, α) mit Mittel 1, in K = 5 gleich
    wahrscheinlichen Stufen; neu bei jedem Stehenbleiben und innerhalb eines Aufenthalts mit der Rate
    1/600 s neu gezogen. Das ist die Erkennbarkeit als Teil des Zustands (D_Mahler2011 S. 3502, Gl.
    49–52) mit diskreten Stufen und Markov-Wechsel (D_Wilthil2019 PDF 2, Gl. 2). Ohne sie ist eine
    Sitzende, die 5 min nicht erfasst wird, exponentiell unwahrscheinlich, und das Modell schickt sie
    zur Tür (gemessen 6.10.: Abend-Episode 100 % falsch). α = 1 (angenommen; auf den Episoden 0,5 / 1 /
    2: 21 / 18,5 / 20,5 % falsch im Drehbuch, sonst gleich); über die Evidenz zu schätzen. Für Gehende
    gilt κ = 1 (sie werden zuverlässig erfasst).
  - `ρ_m · P_m(r)`: Rate nach Betriebsart m und Entfernung r.
    - Form: Swerling-I-Detektionskurve über der Radargleichung,
      `P(r) = P_FA^(1/(1 + SNR₅₀ (r₅₀/r)⁴))`.
    - ρ_m und r₅₀,m per Maximum-Likelihood aus 17 h (**gemessen**): steht ρ = 0,034 /s, r₅₀ = 5,0 m;
      geht ρ = 0,51 /s, r₅₀ = 4,55 m.
    - *Einschränkung:* Für Sitzende gibt es keine Literaturkurve; der LD2450 unterdrückt Ruhendes
      (Notizen Phase 3). Die Form ist eine empirische Wahl und wird über die Evidenz gegen eine
      logistische Form verglichen.
  - `g_s(x)`: Sicht des Sensors auf die Stelle nach Winkel und Wänden (Geometrie).
  - `q(x)`: Auflösung neben einem Ziel, das der Sensor gerade misst:
    `1 − exp(−ln 2 · ((Δr/0,6 m)² + (Δquer/0,9 m)²))` (Form aus A_Svensson2012 S. 11, Gl. 18–20).
- **Verlieren und Wiederfinden.**
  - Verlieren ist bei Personen und Geistern gleich häufig: keine Information.
  - Eine gehaltene Spur wird wiedergefunden auf ihrem Eigentümer, auf einer anderen Person nahe der
    gehaltenen Stelle (0,7 m), auf dem Geist, der sie war, oder auf einer Reflexion dort (Gewicht wie
    0,05 Personen).
  - Zeit bis zum Wiederfinden, **gemessen**: log-normal, Median 0,13 s (bewegt) bzw. 0,2 s
    (eingefroren); 3 % bzw. 2 % werden nie wiedergefunden.
  - Nicht wiederfinden spricht gegen „noch dort“, ohne dass dafür eine Frist gilt.
- **Fallenlassen.** Folgt aus dem Nicht-Wiederfinden; keine eigene Information.
- **Position.** `z = x + c + o + w`:
  - c bleibt eine Spur lang, o wandert (OU, τ = 3,5 s), w ist 5 cm Rauschen je Frame. 57 % der Varianz
    bleibt, 43 % wandert (**gemessen**).
  - Gesamtstreuung je Achse nach Entfernung: entlang 0,15 m + 2 %, quer 0,10 m + 5 %.
  - Gerechnet als Zustandserweiterung um den farbigen Fehler (E_BrysonHenrikson1967 S. 2–3).
  - Verwendet wird höchstens alle 0,25 s eine Position.
- **Spuren beim Start und nach Datenlücken:** Eine Person ist zu einem beliebigen Zeitpunkt mit dem
  Anteil `a·L / (1 + a·L)` erfasst (L Lebensdauer einer Spur, **gemessen** 60 s stehend, 30 s gehend).

### 4.2 Geister
Ein Geist ist eine Spur, die zu keiner Person gehört.

**Entstehen** je Sensor s, mit der Dichte `λ_s(x) + λ_e · (Gehende im Blickfeld von s)` je m² und s:
- `λ_s(x)` ist eine **Geisterkarte**: je Sensor und Rasterzelle ein Poisson-Prozess mit Gamma-Prior
  (J_Luber2014 Kap. 6, S. 90–92, Gl. 6.8–6.13).
  - Der Prior hat das Mittel der globalen Rate (**gemessen** nachts: eine Geisterspur in 7 h) und die
    Stärke einiger Stunden Belichtung.
  - Gelernt wird offline über EM auf ungelabelten Aufnahmen (I_Kantas2015 Abschn. 5):
    - E-Schritt: P(Geist) jeder Spurgeburt aus dem Filter, gezählt mit der Vorhersage, nicht mit dem
      eigenen Urteil danach (J_Park2020 Abschn. 5.2).
    - M-Schritt: Gamma-Update je Zelle.
  - Danach eingefroren und nur auf Zeiten bewertet, aus denen nicht gelernt wurde (`tools/ghostmap.py`
    lässt die Episoden der Wahrheitsdatenbank aus; je Sensoraufstellung eine Karte, Zellen 0,4 m).
  - Wo eine Spur beginnt, streut um ihre Quelle mit dem Versatz des Sensors (0,2–0,3 m, 4.1). Die
    Zählungen werden deshalb mit 0,3 m verteilt (eine Zelle einer Clutter-Karte ist sonst zu scharf,
    J_Park2020 Abschn. 1).
  - Begründung: Wiederkehrende Reflexionen (Möbel, Dock des Saugroboters) stehen an festen Stellen. Mit
    einer gleichverteilten Dichte erklärt das Modell sie fast immer als Person (Licht an ohne Person).
    0.6.x hat das gemessen: Ohne Karte war es schlechter.
- `λ_e`: Mehrwegeechos laufen mit Gehenden mit (3·10⁻⁴; angenommen, aus 0.7.0).

**Lebensdauer:** eine Mischung zweier Exponentialverteilungen (kurz, lang), im selben EM geschätzt:
die Lebensdauern der Spuren, gewichtet mit P(Geist). **Geschätzt** (6.10., Aufstellung vom 4.10.
18:06, 10,8 h): 40 % kurz (Mittel 3 s), 60 % lang (Mittel 39 s); Log-Evidenz +106 gegenüber der Annahme
91 % mit 1,5 s / 9 % mit 300 s. Mit der Annahme verlor eine Geisterspur, die länger als ein paar
Sekunden lebt, gegen „eine Person“ (Ecke im Wohnzimmer, 5.10. 16:22).

**Quelle:** bewegt sich wie eine Person (3.1, 3.2), mit demselben Versatz; falsche Spuren eines
Trackers folgen seinem Bewegungsmodell. Geist und Person unterscheiden sich also durch Entstehung,
Lebensdauer und Ort, nicht durch eine andere Rauschannahme.

### 4.3 LD2410C
Wird in 0.8.0 nicht verwendet (nur angezeigt). Er ist der erste Kandidat für zusätzliche Information
über Sitzende, die Schwäche des LD2450. Dafür muss er wie der LD2450 auf der Ebene seiner eigenen
Ausgabe modelliert werden, denn er hat selbst eine Haltezeit.

### 4.4 Lücken zwischen Frames (übernommen)
Die Firmware sendet leere Frames nur alle 5 s. Eine Lücke bis 6 s war leer; eine längere ist
Datenverlust und sagt nichts.

## 5. Inferenz

### 5.1 Hypothesen über die Spuren
- Welche laufenden Spuren zu derselben Person gehören und welche Geister sind, ist eine diskrete
  Hypothese mit exaktem Gewicht (δ-GLMB / PMBM; B_Reuter2014, B_GarciaFernandez2018 S. 10–12).
- Eine neue oder wiedergefundene Spur verzweigt jede Hypothese: Geist, eine Person mit Spur, eine
  Person ohne Spur, ein Gast.
- Hypothesen, die über die laufenden Spuren dasselbe sagen, werden zusammengelegt (5.6).
- Behalten werden die schwersten, höchstens 12, solange ihr Gewicht über 10⁻⁷ des stärksten liegt.
  Abschneiden nach Gewicht minimiert den L1-Fehler (B_Vo2017 S. 2–3).
- Gegeben eine Hypothese sind die Personen unabhängig (Multi-Bernoulli mit Existenz 1). Die
  Zählverteilung je Raum ist je Hypothese die Faltung der Einzelwahrscheinlichkeiten, gemischt über die
  Hypothesen.

### 5.2 Personen mit einer messenden Spur: Gauß-Mischung (IMM)
Gilt für jede Person mit einer laufenden Spur, gemessen oder gehalten (5.4). Zwei Komponenten,
*steht* und *geht*, mit Gewichten. Je Komponente ein Kalman-Filter über Position, Geschwindigkeit (bei
*steht* fest 0) und je Spur den Versatz (c, o). *Steht* führt zusätzlich die Wahrscheinlichkeiten der
Aufenthaltsarten (3.1) und der Erkennbarkeitsstufen (4.1).

Je Rechenschritt (Buch_SarkkaSvensson2023 S. 352; B_Li2019 Gl. 26–33):
1. **Übergänge:**
   - *steht → geht* mit `Σ_l p_l (1 − e^(−λ_l Δt))`; die Geschwindigkeit danach ist N(0, s²/2) je
     Achse.
   - *geht → steht* mit `1 − e^(−μΔt)`; die Geschwindigkeit danach ist 0.
2. **Mischen:** Je Ziel-Betriebsart werden die hereinkommenden Teile per Momentenabgleich zu einer
   Gauß-Komponente zusammengefasst (IMM).
3. **Vorhersage und Messung je Komponente:** Vorhersage nach 3.1 bzw. der OU-Näherung aus 3.2, dann
   Kalman-Update. Neue Gewichte ∝ altes Gewicht × Likelihood; ihre Summe ist der Faktor für die
   Hypothese.
4. **Nicht-Erfassung** durch andere Sensoren (4.1): ein Faktor je Komponente, am Mittel der Komponente,
   bei *steht* gemischt über die Erkennbarkeitsstufen.
5. **Durch eine Tür:** Die Gauß-Näherung kennt keine Türen. Deshalb hat eine Person zusätzlich einen
   Teil „durch eine Tür gegangen“ (eine Rasterdichte mit Gewicht). Was die Markov-Kette des Rasters (5.3)
   von der Komponente *geht* in diesem Schritt durch eine Tür trägt, wandert dorthin und lebt dort nach
   5.3 weiter. Eine Messung ihrer Spur sagt „in Sicht“: Der Teil fällt weg (dass jemand hinausgeht und
   genau an diese Stelle zurückkommt, wird vernachlässigt).

### 5.3 Personen ohne messende Spur: Raster
Punktmassenfilter (0_Arulampalam2002 Abschn. II-B) auf 0,2 m, wie im 0.7.0-Code (`hidden.py`).
Zustände: `geht[Richtung h, Zelle]` (8 Richtungen), `steht[Art l, Zelle]`, `Bereich[k, Alter]`,
`außer Haus`.

Der Sprungprozess aus 3.2 ist darauf eine Markov-Kette:
- weiter in Richtung h, Richtungswechsel mit λ_d, Anhalten mit μ, Aufstehen mit λ_l;
- an einer Wand wird die Richtung gespiegelt, durch eine Tür geht es in den Bereich.

Nicht-Erfassung, Nicht-Wiederfinden und die Wahrscheinlichkeit einer neuen Spur sind exakte Summen über
die Zellen.

### 5.4 Wechsel der Darstellung
- **Gauß → Raster**, wenn die letzte laufende Spur einer Person endet:
  - Jede Komponente wird mit ihrer Dichte auf die Zellen verteilt, *geht* nach der Richtung ihrer
    Geschwindigkeit, *steht* mit Aufenthaltsarten und Erkennbarkeit; dazu der Teil „durch eine Tür“.
  - Was dabei verloren geht: das Tempo (auf dem Raster immer s) und die Versätze der Spuren.
- **Nicht schon beim Verlieren** (gemessen 6.10., Morgen-Episode): Kam eine Person aufs Raster, sobald
  der Sensor ihre Spur verlor, verlor sie ihren gelernten Versatz. Beim Wiederfinden passte die Messung
  dann etwa neunmal besser zu einem Geist (dessen Quelle Gauß blieb) als zu ihr; nach drei Aussetzern
  war die Frau am Tisch „ein Geist“ (18 % falsch, 743 s dunkel). Behält sie ihre Gauß-Mischung, solange
  eine Spur läuft: 2,7 %, 0 s dunkel.
- **Raster → Gauß**, wenn eine Spur auf der Person beginnt oder wiedergefunden wird: Raster ×
  Likelihood der Spur, je Betriebsart per Momentenabgleich eine Komponente.

### 5.5 Gäste
- Die Gäste ohne Spur sind eine Poisson-Intensität auf demselben Raster (der unentdeckte Teil des PMBM,
  B_GarciaFernandez2018 S. 4, Gl. 18–24), geboren an den Wegen nach draußen (3.4).
- Eine neue Spur auf einem Gast hat das Gewicht `∫ Intensität × Rate der Erfassung`. Der Gast wird dann
  eine Person mit Spur (5.2).
- Ein Gast, dessen Masse ganz außer Haus ist, geht in die Intensität zurück.

### 5.6 Zusammenlegen
Sagen zwei Hypothesen über alle laufenden Spuren dasselbe, werden sie eine:
- **Personen mit denselben Spuren:** Ihre Gauß-Mischungen werden vereinigt und je Betriebsart per
  Momentenabgleich zusammengefasst.
- **Personen ohne Spur** sind austauschbar. Sie werden so gepaart, dass sich die Paare am wenigsten
  unterscheiden (Zuordnung mit kleinster Summe der L1-Abstände), und dann gemischt.
  *Eigene Herleitung:* Mischen in beliebiger Reihenfolge erzeugt den Fehler aus B_Reuter2014 Fig. 1–3
  („je zu 50 % hier und dort“ statt „einer hier, einer dort“).

## 6. Ausgaben
- **Je Raum mit Sensor:** die Verteilung der Personenzahl (5.1) und P(Raum belegt) = 1 − P(0).
- **Licht** (der Hauptzweck): Der Raum gilt als belegt, wenn `P(belegt) > c`.
  - Die Schwelle folgt aus den Kosten: c = K_an / (K_an + K_dunkel). K_an sind die Kosten je Sekunde
    Licht ohne Person, K_dunkel je Sekunde Dunkel mit Person (Bayes-Entscheidung;
    I_GneitingRaftery2007 S. 364–365, Satz 3).
  - Licht ohne Person ist am schlimmsten (Leon, 6.10.): K_an > K_dunkel, also c > 0,5.
  - Das verzögerte Ausschalten bleibt in Home Assistant.
- **Angezeigte Zahl:** die wahrscheinlichste.
- **Bewegt / ruhig:** das Gewicht der Komponente *geht*. „Wird gleich betreten“, Karte und Wärmekarten
  wie bisher.

## 7. Parameter aus der Evidenz
- Die Summe der Normierungen des Filters ist die Log-Evidenz der Aufnahmen, also die Summe der
  prequentiellen Log-Scores (I_GneitingRaftery2007 S. 372).
- Weil der Filter deterministisch ist, ist sie eine glatte Funktion der Parameter, anders als beim
  Partikelfilter (I_Kantas2015 S. 8–9). Vergleiche brauchen keine Seeds.
- Geschätzt wird auf ungelabelten Aufnahmen; die Wahrheits-Episoden bleiben draußen.

### 7.1 Ergebnisse
(folgt)

## 8. Prüfung
1. **Wahrheitsdatenbank** (`~/.config/presence-tracker/truth`) mit `tools/evaluate.py`, ein Lauf je
   Episode (deterministisch). Freigabe nach dem Licht, in dieser Reihenfolge: Licht ohne Person, zu
   spätes Einschalten, Dunkel mit Person über die Ausschaltverzögerung hinaus.
2. **Simulationsbasierte Kalibrierung** (0_Talts2018 Alg. 1; 0_Cook2006): Welten aus genau diesem
   Modell erzeugen; der Rang der wahren Zahl je Raum unter der Filterverteilung muss gleichverteilt sein.
3. **Determinismus:** Zwei Läufe liefern bitgleiche Ausgaben (Test).

## 9. Ablationen (offen, je mit Evidenz und Episoden)
- λ_d = 0,85 /s (gemessen) gegen 0,3 /s; OU-Näherung gegen weißes Rauschen in der Beschleunigung.
- Form der Erkennbarkeit α über die Evidenz (D_Mahler2011).
- Erfassungsform Swerling-I gegen logistisch.
- Geisterkarte gegen globale Rate.
- Gäste (ν) gegen keine.
- Später: LD2410C; Ziele bzw. eine Aufenthaltskarte als Ziel-Prior der Ungesehenen (J_Luber2014
  Kap. 6).

## 10. Umsetzung in Schritten (jeder mit Abnahme auf der Wahrheitsdatenbank)
1. **Gauß-Mischung statt Partikelwolke** (5.2, 5.4), Sprungprozess ohne Ziele, Zusammenlegen mit
   Paarung (5.6). Abnahme: bitgleiche Läufe; Morgen-Episode gelöst; Licht-Werte nicht schlechter als
   9a70790.
2. **Licht-Entscheidung über P(belegt)** (6) in Tracker und `evaluate.py`.
3. **Geisterkarte** (4.2) mit EM auf ungelabelten Tagen. Abnahme: weniger Licht ohne Person.
4. **Gäste** (3.4, 5.5); die feste Personenzahl ohne Gäste entfällt.
5. **Ablationen** aus 9 und Parameter aus der Evidenz (7.1).
