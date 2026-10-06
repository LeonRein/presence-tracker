# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand: Code 0.8.0 (6.10.2026). Beschreibt, was der Code rechnet; was fehlt oder nur genähert ist,
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
7. **Läuft als Home-Assistant-App**: wenig Rechenzeit, reproduzierbare Ergebnisse. Was gelernt wird
   (Geisterkarte), lernt die App selbst.

## 2. Die Welt

Aus dem Grundriss (`model.py`, `world.py`):
- **Beobachteter Bereich:** Räume, die die Sensoren zu mindestens 60 % sehen.
- **Bereiche ohne Sensor *Rₖ*:** übrige Räume, über Türen zusammengefasst. *Offen*, wenn man von dort
  das Haus verlassen kann (Eingang, Treppe), sonst *geschlossen* (Balkon).
- **Außer Haus.**
- Wände trennen (Personen gehen nicht hindurch, Radar sieht nicht hindurch); Türen sind Lücken.

**Zustand:** N Personen (zurzeit fest, `residents` = 2; siehe 3.4), jede an einem Ort: im beobachteten
Bereich mit Position und Betriebsart *steht* (mit Aufenthaltsart l und Erkennbarkeit κ) oder *geht*
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

### 3.3 Türen, Bereiche ohne Sensor, außer Haus
- Aufenthalt in *Rₖ*: log-normal, Median 2 min / Streuung von ln(Dauer) 1,5 (geschlossen) bzw.
  30 min / 2,0 (offen) (angenommen; `unobserved.py`). Nicht gelernt: Wann jemand hinein- und
  herausging, kennt der Filter nur als Wahrscheinlichkeit; seine Urteile als Dauern zu zählen, lernte
  seine Fehler. Zu schätzen über die Evidenz (7). Heraus kommt man gehend an einer Tür des Bereichs.
- Aus einem offenen Bereich nach außer Haus mit 1/(2 h); zurück mit 1/(4 h) je Weg hinein (offene
  Bereiche und Eingänge im beobachteten Bereich) (angenommen).

### 3.4 Gäste
Nicht umgesetzt. Geplant: Ankünfte als Poisson-Prozess je Weg nach draußen (Rate ν), danach wie
Bewohner; im Filter als Poisson-Intensität (der unentdeckte Teil des PMBM, B_GarciaFernandez2018
Gl. 7–10, 18–24). Dann entfällt die feste Zahl N.

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
- **Lernen in der App** (online-EM, I_Kantas2015 Abschn. 5): Endet eine Spur, zählt sie mit
  P(Geist) des Filters an ihrem Startpunkt; die Beobachtungszeit jedes Sensors ist die Belichtung; die
  Lebensdauern werden gleich gewichtet. Vergessen mit Zeitkonstante 14 Tage. Wird ein Sensor
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

Deterministisch: Was diskret ist und wenige Werte hat, wird aufgezählt, nicht gezogen. Der Filter
rechnet in Schritten von höchstens 0,2 s; das Ergebnis hängt nicht davon ab, wie die Zeit zerlegt
wird (Test).

### 5.1 Hypothesen über die Spuren
- Welche laufenden Spuren zu derselben Person gehören und welche Geister sind, ist eine diskrete
  Hypothese mit exaktem Gewicht (Datenassoziation eines PMBM, B_GarciaFernandez2018; δ-GLMB,
  B_Reuter2014).
- Eine neue Spur verzweigt jede Hypothese: Geist; eine Person, die schon Spuren anderer Sensoren hat;
  eine Person ohne Spur. Eine wiedergefundene: ihr Eigentümer; eine andere Person nahe der Stelle;
  Geist/Reflexion.
- Behalten werden höchstens 12, solange über 10⁻⁷ des stärksten (Abschneiden nach Gewicht,
  B_Vo2017).
- Gegeben eine Hypothese sind die Personen unabhängig. Die Zählverteilung je Raum ist die Faltung
  der Einzelwahrscheinlichkeiten (Poisson-Binomial), gemischt über die Hypothesen.

### 5.2 Personen mit Spur: Gauß-Mischung (IMM)
`gauss.py`. Gilt, solange eine Person mindestens eine laufende Spur hat (gemessen oder gehalten).
Zwei Komponenten *steht* / *geht*, je ein Kalman-Filter über Position, Geschwindigkeit und je Spur
(c, o); *steht* führt die Wahrscheinlichkeiten der Aufenthaltsarten und Erkennbarkeitsstufen. Je
Schritt (Buch_SarkkaSvensson2023 S. 352; B_Li2019 Gl. 26–33):
1. Übergänge *steht → geht* (`Σ p_l (1 − e^(−λ_l Δt))`) und *geht → steht* (`1 − e^(−μΔt)`), je
   Ziel-Betriebsart per Momentenabgleich zu einer Komponente zusammengefasst.
2. Lineare Vorhersage je Komponente (3.1 bzw. OU-Näherung 3.2), Kalman-Update mit der Spur.
3. Nicht-Erfassung durch andere Sensoren: Faktor je Komponente, am Mittel der Komponente.
4. **Durch eine Tür:** Was die Rasterbewegung (5.3) von der Komponente *geht* durch eine Tür trägt,
   wandert in einen Teil „durch eine Tür“ (eine Rasterdichte mit Gewicht a). Eine Messung der Spur
   sagt „in Sicht“ und streicht ihn.

### 5.3 Personen ohne Spur: Raster
`hidden.py`. Punktmassenfilter (0_Arulampalam2002 Abschn. II-B) auf 0,2 m:
`geht[8 Richtungen, Zelle]`, `steht[Art l, Stufe κ, Zelle]`, `Bereich[k, Alter]` (Altersklassen
2 s … 36 h), `außer Haus`. Der Sprungprozess aus 3.2 als Markov-Kette: ein Feld je Takt HC/s in
Richtung h, Richtungswechsel mit λ_d, Anhalten mit μ, Aufstehen mit λ_l, Wand spiegelt, Tür führt in
den Bereich. Nicht-Erfassung, Nicht-Wiederfinden und die Rate einer neuen Spur sind exakte Summen
über die Zellen.

Start („nichts bekannt“): je Person 1/3 im beobachteten Bereich (stehend, gleichverteilt), 1/3 in den
Bereichen ohne Sensor, 1/3 außer Haus.

### 5.4 Wechsel der Darstellung
- **Raster → Gauß**, wenn eine Spur auf der Person beginnt oder wiedergefunden wird: Raster ×
  Erfassungsrate × Dichte der Messung, je Betriebsart per Momentenabgleich. Die Aufteilung von
  `z − x` auf c und o folgt aus ihren Varianzen (eigene Herleitung).
- **Gauß → Raster**, erst wenn die letzte laufende Spur endet, nicht schon beim Verlieren (gehaltene
  Spuren behalten so ihren Versatz). Komponenten werden mit ihrer Dichte verteilt, *geht* nach der
  Richtung der Geschwindigkeit; verloren gehen Tempo und Versätze.

### 5.5 Gäste
Nicht umgesetzt (3.4).

### 5.6 Zusammenlegen
Hypothesen, die über alle laufenden Spuren dasselbe sagen, werden eine:
- Personen mit denselben Spuren: Gauß-Mischungen vereinigt, je Betriebsart per Momentenabgleich.
- Personen ohne Spur sind austauschbar: so gepaart, dass die Summe der L1-Abstände minimal ist, dann
  gemischt (eigene Herleitung; beliebige Reihenfolge erzeugt „je zu 50 % hier und dort“ statt „einer
  hier, einer dort“, B_Reuter2014 Fig. 1–3). Für mehr als eine Person ist das die
  Multi-Bernoulli-Näherung.

## 6. Ausgaben
- **Je beobachtetem Raum:** Verteilung der Personenzahl (5.1). An Home Assistant geht die
  wahrscheinlichste Zahl; *belegt* heißt Zahl > 0.
- **Bereiche ohne Sensor:** P(jemand dort) als Attribut `probability`.
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
- **Feste Personenzahl** N = `residents`; Gäste fehlen (3.4).
- **Licht über die wahrscheinlichste Zahl**, nicht über `P(belegt) > c` mit
  `c = K_an / (K_an + K_dunkel)` aus den Kosten der Fehler (Bayes-Entscheidung,
  I_GneitingRaftery2007 Satz 3; Licht ohne Person ist teurer, also c > 0,5).
- **Die Geisterkarte lernt aus dem eigenen Urteil** (P(Geist) am Ende der Spur). Das kann sich
  selbst verstärken: Wer immer dort sitzt, wo nur ein Sensor sieht, kann als Geist weggelernt werden.
  Unabhängige Evidenz wäre z. B. die Vorhersage vor der Geburt (J_Park2020 Abschn. 5.2) oder ein
  zweiter Sensor.
- **LD2410C nicht ausgewertet**, obwohl er in 0.6.x nachweislich half (10).

Näherungen, die man prüfen oder ersetzen kann:
- Nicht-Erfassung und Erfassungsrate einer Gauß-Komponente am Mittel statt über ihre Verteilung.
- Auf dem Raster gehen alle mit Tempo s in 8 Richtungen; beim Wechsel Gauß → Raster gehen Tempo und
  Versätze verloren. Die Gauß-Näherung kennt keine Wände.
- Versatzvarianz je Achse gemittelt, obwohl entlang/quer verschieden gemessen.
- Jenseits von 7 m zählt die Entfernung zweimal: in `g_s` (Abfall ab Reichweite + 1 m, aus 0.6.17)
  und in `P_m(r)`. Gegen welches `g_s` ρ und r₅₀ gefittet wurden, ist nicht festgehalten.
- Erkennbarkeit nur für Stehende; α, Wechselrate, Ankunft/Weggang, Aufenthalts-Prior und λ_e sind
  angenommen, nicht über die Evidenz geschätzt.
- Höchstens 12 Hypothesen; Paarung beim Zusammenlegen bis 6 Personen.

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
