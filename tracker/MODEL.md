# Das Wahrscheinlichkeitsmodell des Presence Trackers

Entwurf zur Durchsicht, noch nicht umgesetzt. Dieses Dokument ist die Spezifikation: Der Code wertet
genau dieses Modell aus und enthält keine eigenen Fallunterscheidungen. Jede Wahrscheinlichkeit
steht hier mit Formel und Herkunft (**gemessen** auf Aufnahmen, **gelernt** im Betrieb, **angenommen**).
Was hier nicht steht, gibt es im Tracker nicht.

## 1. Grundsätze

1. **Ein generatives Modell.** Es beschreibt, wie die Welt (Personen, Geister) sich bewegt und wie die
   Sensoren daraus ihre Frames erzeugen. Der Tracker rechnet daraus rückwärts, welche Welt die Frames am
   besten erklärt (Bayes). Es gibt keine Regeln der Art „nach 1,5 s gilt jemand als verloren“.
2. **Personen entstehen und verschwinden nicht.** Die Zahl der Personen ist Teil des Zustands und nicht
   festgelegt. Sie ändert sich im beobachteten Bereich nur dadurch, dass jemand durch eine Tür geht.
   Eine Messung mitten im Raum kann also nur von einer Person kommen, die vorher durch eine Tür kam
   und auf dem Weg dorthin hätte gesehen werden müssen, oder von einem Geist.
3. **Keine festen Haushaltsgrößen.** Dass bei uns selten eine dritte Person kommt, ist eine gelernte
   Ankunftsrate an der Wohnungstür, keine Regel. In einem öffentlichen Gebäude ist dieselbe Rate hoch.
4. **Frames sind gegeben den Zustand unabhängig.** Dass aufeinanderfolgende Frames nicht unabhängig
   *aussehen*, hat Ursachen: Wer an einer Stelle nicht gesehen wird, wird dort meist länger nicht
   gesehen (Verdeckung, Sitzhaltung), Geister leben eine Weile, und der LD2450 glättet intern, sodass sein Fehler eine knappe Sekunde lang bleibt (4.1,
   noch nicht modelliert). Diese Ursachen werden modelliert. Eine pauschale Dämpfung (bisher `evidence_time`) gibt es nicht mehr.
5. **Gelernt wird aus der ganzen Wahrscheinlichkeitsverteilung**, gewichtet mit ihrer
   Wahrscheinlichkeit (EM-Prinzip). Nicht nur aus der wahrscheinlichsten Hypothese, und nicht erst
   ab einer Sicherheitsschwelle. Jede gelernte Größe hat einen Startwert (Prior) mit einem Gewicht in
   „so viel wie N Beobachtungen“.

## 2. Die Welt (Zustand)

**Geometrie** (aus der Konfiguration): beobachteter Bereich *A* (Räume mit Sensoren) mit Wänden;
Bereiche ohne Sensor *R₁…Rₖ* (Küche, Balkon, Flur-Bereich, …) und *draußen*; Türen *q* verbinden
genau zwei davon. Draußen ist nur über bestimmte Türen erreichbar (bei uns über den Flur-Bereich).

Auch in einem Bereich ohne Sensor hat eine Person einen Ort. Kein Sensor sieht ihn, außer durch eine
offene Tür. Bis 0.6.4 hatte sie dort keinen Ort: Sah der Esszimmer-Sensor jemanden 1 m hinter der
Küchentür, konnte das keine Hypothese erklären, und „blieb vor der Tür stehen“ gewann (Küchentest 4.10.,
18:23).

**Zustand zur Zeit t:**

| Teil | Inhalt |
|---|---|
| Personen | beliebig viele; je Person: wo (ein Ort mit Geschwindigkeit und Modus geht / steht, im beobachteten Bereich oder in einem der Bereiche *Rₖ*, dort mit der Zeit seit dem Betreten, oder draußen), und seit wann sie von welchem Sensor nicht mehr gesehen wurde |
| Geister | je Sensor beliebig viele, je Geist Ort und Alter |

Personen sind anonym. Nummern in der Oberfläche sind nur Beschriftung und nicht Teil des Modells
(Abschnitt 6).

## 3. Dynamik (was zwischen zwei Zeitpunkten passiert)

### 3.1 Bewegung im beobachteten Bereich
- **Gehen:** fast konstante Geschwindigkeit mit zufälligen Richtungs- und Tempoänderungen, 0,6 m/s²
  je √s. **Gemessen** (5.10., 4,6 h Zielspuren des LD2450, Wege ab 1,5 m): Tempo im Median 0,8 m/s, 95 %
  unter 1,6 m/s; Geschwindigkeitsänderungen über 2–3 s wie 0,6 m/s²/√s (über 0,5 s mehr, darin steckt
  das Messrauschen der Geschwindigkeit). Bis 0.6.17 angenommen: 1,0.
- **Stehen / Sitzen:** Ort fast fest, kleines Wackeln. **Gemessen.**
  Mit 1,0 war die Richtung eines Wegs schnell vergessen, und wer zur Tür hinausging, blieb im Modell
  davor stehen (5.10., 16:44). Wahrheitsdatenbank, 12 Seeds: Leon geht hinaus 7,9 % → 2,8 % falsch
  (weg nach 33 s statt 36 s im Median), Abend 8,9 % → 0 %, Drehbuch 9,4 % → 9,3 %.
- **Wechsel gehen → stehen:** Rate `λ_stop` = 0,5 /s, überall gleich. *Gemessen und verworfen:* In den
  Zielspuren des LD2450 dauert ein Weg (ab 1,5 m) im Median 4 s, in den ersten 2 s bleibt fast niemand
  stehen, danach 10–20 % je s. Damit im Modell: Drehbuch 12,7 % statt 9,3 %, Leon geht hinaus 10,4 %.
  Vermutlich misst die Zielspur das Anhalten zu spät: Der LD2450 glättet intern und führt ein Ziel
  nach dem Anhalten noch eine Weile mit Tempo weiter (4.1, gehaltene Ziele). Die Statistik stammt
  damit zum Teil aus dem Bewegungsmodell des Sensors, nicht aus den Menschen.
- **Wechsel stehen → gehen:** Rate `λ_go(d) = 0,58 / (d + 5,6 s)`, abhängig davon, wie lange jemand
  schon steht oder sitzt (lange Sitzende stehen seltener auf). Gemessen an den Sitzdauern.
- **Karte der Bewegungen (Maps of Dynamics, CLiFF-Map; Kucner et al. 2017), offline geprüft (5.10.).**
  Gelernt je 50-cm-Feld aus Rohzielen des LD2450, die der andere Sensor bestätigt (unabhängig vom
  Filter), auf 70 % der Zeit (3249 Geschwindigkeiten, 56 Felder); geprüft auf den übrigen 30 %: Wo ist
  eine gehende Person in 1–3 s (618 Startpunkte)? Die Karte zieht die Geschwindigkeit mit 0,5 /s zu
  einer dort gelernten. Gegenüber der reinen Trägheit: nach 1 s nichts, nach 3 s 13 cm weniger Fehler.
  Dieselbe Rechnung mit allen Geschwindigkeiten ohne Ort bringt aber genauso viel (14 cm): Der Gewinn
  kommt daher, dass Menschen ihr Tempo und ihre Richtung nicht 3 s halten, nicht vom Ort. Eine Karte
  je Ort lohnt mit dieser Datenmenge nicht und trüge das Risiko, Fehler als Verhalten zu lernen (unten).
- **Kein Lernen je Ort.** Geplant war, beide Raten und die Aussetzer je 50-cm-Feld zu lernen (Sofa:
  lange Sitzdauern, Durchgang: kurze). Das Modell würde dabei aber eigene Messfehler als Verhalten
  lernen und verstärken (auf den Daten bestätigt, 4.10.). Das Lernen war aus, die Raster sind
  seit 0.6.7 entfernt.
- **Warum „geht / steht“ ein eigener Zustand ist und nicht aus der Geschwindigkeit folgt:** Er trägt
  die Sichtbarkeit. Ein Sitzender fällt beim LD2450 oft lange aus, ein Gehender nur kurz (3.3). Im
  Drehbuch mit der Aussetzer-Statistik der Sitzenden auch für Gehende: 84,6 % statt 94,4 %
  (8 Läufe). Die Dauer im Zustand (Aufstehrate abhängig von der Sitzdauer) war im Drehbuch dagegen
  nicht nachweisbar nützlich (konstante Rate: 95,2 %); die Sitzzeiten dort sind aber nur 1–3 min.
- **Wände:** Orte außerhalb des freien Raums haben Wahrscheinlichkeit 0.
- **Körper:** Zwei Personen stehen nicht im selben Fleck. Abstandsverteilung zweier Personen
  **gelernt** aus sicheren Paaren. Prior: unter 0,4 m selten.

### 3.2 Türen
- Wer sich durch eine Tür bewegt, ist danach im Bereich dahinter. Das ist eine Folge der Bewegung,
  kein eigenes Ereignis, und gilt in beide Richtungen: Wer in der Küche durch die offene Tür gesehen
  wird und herausgeht, läuft heraus.
- Wer in einem Bereich *Rₖ* ist, kommt mit der Ausfallrate `h_k(Alter)` an einer seiner Türen wieder
  heraus, gehend, in den Raum hinein. `h_k` stammt aus den **gelernten** Aufenthaltsdauern je Bereich.
  Prior heute: geschlossene Bereiche (Küche, Balkon) Median 2 min, offener Flur-Bereich 30 min. Später
  je Tageszeit (nachts Schlafzimmer).
- **Diese Schätzung ist ungenau, und das Modell weiß das.** Verwendet wird nicht eine gelernte Kurve,
  sondern die Vorhersage über alle zu den bisherigen Besuchen passenden Kurven (Bayes-Prädiktive):
  - Bei wenigen Besuchen ist sie breit.
  - Sie hat immer einen schweren Schwanz: Je länger jemand schon dort ist, desto langsamer sinkt die
    Chance, dass er gleich herauskommt. Wer deutlich länger bleibt als üblich, bleibt plausibel.
- Die Dauer zählt ohnehin nur so weit, wie die Tür unbeobachtet ist. Wer herauskommt, geht durch die
  Tür und wird dort normalerweise gesehen. Solange das nicht passiert, ist „noch drin“ die beste
  Erklärung, gleich wie lange es dauert.
- **Draußen** hat keine feste Bevölkerung. Ankunftsrate `α` an jeder Tür nach draußen, **gelernt**
  (Prior: 1 Person pro Tag). Wer im Flur-Bereich ist, kann über diese Tür auch nach draußen gehen.
- *Stand 0.6.1:* Gebaut ist nur der Teil für die Bewohner. Jede Person kann auch draußen sein, verlässt
  das Haus aus dem Flur-Bereich (im Mittel nach 2 h dort) und kommt wieder (im Mittel nach 4 h).
  **Unbekannte Neuankömmlinge** sind ausprobiert, aber noch nicht drin. Im jetzigen Rechenverfahren wird
  aus jeder Fehlzuordnung an einer Tür sofort eine dauerhafte zusätzliche Person: Ein paar Sekunden
  ähnlicher Messungen zählen wie viele unabhängige Beweise und überwinden jede kleine Ankunftsrate. Das
  braucht zuerst die Geister mit Lebensdauer (3.4). Auf dem Drehbuch-Durchlauf fiel das Ergebnis damit
  auf 59–87 %.

### 3.3 Nicht gesehen werden
Es gibt keinen eigenen Zustand „ausgesetzt“ und keine Schwelle „verloren“. Es gibt eine
**gemessene** Wahrscheinlichkeit je Sensor und Modus (steht / geht), über die Erkennungswahrscheinlichkeit
des Orts (4.1) auch je Ort:

> `U_s(x, τ)` = P(eine Person, die an x bleibt, wird von Sensor s τ Sekunden lang nicht detektiert)

- Für kleine τ ist das die gewöhnliche Fehlquote eines Frames (1 − Erkennungswahrscheinlichkeit).
  Für große τ zeigt sie, wie lange Verdeckungen an dieser Stelle dauern: Sofalehne lange, Raummitte
  praktisch nie.
- **Gemessen** auf den Aufnahmen: wie lange sichere Personen ohne Treffer blieben (Stehende: langer
  Schwanz bis Minuten; Gehende: im Mittel 0,7 s). Je Ort gelernt wird das nicht: Wie beim Verhalten
  (3.1) würde das Modell eigene Fehler als „hier fällt man oft aus“ lernen.
- Kein Treffer im Frame gewichtet „die Person ist an x“ mit `U_s(x, τ+Δt) / U_s(x, τ)`. τ ist die Zeit
  seit dem letzten Treffer dieser Person bei diesem Sensor.
  *Offen:* Eigentlich gehört ein langer Aussetzer zu einer Haltung an einem Ort, nicht zur Person.
  Wer aufsteht und geht, müsste wieder normal sichtbar sein. Umgesetzt (τ ab dem letzten Wechsel zu
  „geht“) hat das auf dem Drehbuch-Durchlauf deutlich verschlechtert (89 % → 71–73 %). Das Zusammenspiel
  ist noch nicht verstanden.
  Gehende im Blickfeld werden nach 0,3–1,2 s wieder gefunden (gemessen). Die langen Lücken der Tabelle
  für Gehende waren Leute, die das Blickfeld verlassen hatten. Seit der gemessenen Geisterdichte (3.4)
  misst die kurze Lücke auch nicht mehr schlechter (92,3 % gegenüber 92,2 %). Die alte Tabelle hatte die
  viel zu hohe Geisterdichte ausgeglichen. Fehlmessungen hängen zusammen, und genau
  das bildet `U` ab. Unabhängig gerechnet wären 3 s ohne Treffer bei 90 % Erkennung 0,1³⁰, also
  „unmöglich“.

**Der Schwanz** jenseits der Tabelle (120 s) fällt mit ihrer letzten Steigung weiter, ein Drittel je
Minute. **Gemessen** (5.10., Wahrheitsdatenbank, 40 min mit genau einer ruhigen Person): Wer 10–80 s
nicht gesehen wurde, wird mit 1–7 % je Frame wiedergefunden, nicht immer seltener. Bis 0.6.15 fiel der
Schwanz wie 1/τ: Eine Person, die 20 Minuten niemand gesehen hatte, blieb mit 1:4,5 noch eine Stunde
ungesehen, und ein Phantom blieb mit ihr.

Daraus folgt ohne Sonderregel: An einer schlecht sichtbaren Stelle, ohne gesehenes Weggehen, ist „noch
dort“ die wahrscheinlichste Möglichkeit. An einer gut sichtbaren Stelle verliert sie schnell, und die
Wahrscheinlichkeit verteilt sich auf die Wege, die ungesehen möglich waren, und die Türen dahinter.

*Gemessen und verworfen (5.10., Auswertung `tools/evaluate.py` auf 5 Episoden):*
- **Gemeinsame Aussetzer.** Sitzende verlieren beide LD2450 gleichzeitig etwa 109-mal pro Stunde für
  mindestens 1 s (Hälfte über 3 s, 1 % über 30 s, längster 58 s; 2,1 h Sitzen). Lange Aussetzer
  (Minuten) sind dagegen meist die eines einzelnen Sensors, während der andere weiter sieht. Das Modell
  rechnet die Sensoren unabhängig und hält gemeinsame Aussetzer damit für fast unmöglich. Ein
  gemeinsamer Zustand „gerade für alle unsichtbar“ je Partikel (gemessene Rate und Dauer) machte aber
  Phantome billiger: Drehbuch 14,2 % falsche Sekunden statt 6,2 %.
- **„Die Person ist in Wirklichkeit woanders“** mit kleiner Rate (1/2 h bis 1/2 min, gezogen wie bei
  einem Start ohne Wissen): Phantome lösen sich, und eine vom Modell verlorene Person kommt nach etwa
  7 Minuten wieder (Abend 2 % statt 34 %). Aber echte Sitzende verschwinden in jedem Fall mit:
  Drehbuch 15–23 %. Beides ist wieder entfernt; das Problem (eine falsche frühe Entscheidung lässt sich
  nicht mehr korrigieren) bleibt offen.
- Nochmals mit 0.6.15 (5.10. nachmittags), beides allein und zusammen, „woanders“ auch zurück in den
  Raum (an Messungen ausprobiert): Drehbuch 22–35 % statt 9 %, fast nur zu wenige Personen. Eine frisch
  eingespeiste Alternative schlägt eine echte Sitzende nach einer langen Lücke immer. Gelöst wurde das
  Problem stattdessen ohne erfundene Rate: Die Alternativen sterben nicht mehr aus (Abschnitt 5,
  Komponenten je Ort).

### 3.4 Geister
Je Sensor:
- **Entstehen:** Poisson-Rate je Ort `β_s(x)`, **gelernt** als Geisterkarte (nur wo ein zweiter Sensor
  gut hinsieht und nichts meldet; Startwert gemessen, unten). Zusätzlich mehr Geister in der Nähe von
  Gehenden (Mehrwegeechos). Ohne die Karte war das Drehbuch in drei Messreihen jedes Mal etwas schlechter
  (−0,9, −0,3, −1,7 Punkte; z. B. Spiegelungen durch die Küchentür als Person in der Küche).
- **Lebensdauer:** **gelernte** Verteilung, die meisten unter 1 s, manche viele Sekunden an derselben
  Stelle. Wo der Nutzer Störzonen zeichnet (Ventilator, Vorhang), ist `β` dort hoch.
- Ein Geist behält die Radialgeschwindigkeit, mit der er auftaucht (stehende Echos etwa 0; ein vom
  LD2450 nachgeführtes Ziel genau seine letzte), rückt mit seinen eigenen Zielen mit und ist nur für
  seinen Sensor da.

*Ein Geist, der 5 s an derselben Stelle steht, ist damit erklärbar. Dafür muss keine Person entstehen,
die dann versteckt bleibt.*

*Gemessen (4.10.):*
- In den leeren Räumen nachts (1–6 Uhr) meldet der Esszimmer-Sensor 0,4 Geister pro Stunde, je
  etwa 1,5 s lang. Der Wohnzimmer-Sensor meldet gar nichts.
- Neben sitzenden Personen gab es im Drehbuch keine Geister.
- Geister entstehen also fast nur als Echos von Gehenden. Die Simulation, die an die Aufnahmen
  angepasst ist, rechnet mit etwa 4 kurzen Geistern pro Minute, solange jemand läuft.
- Umgesetzt in 0.6.4: Grunddichte 1e-4 je m² und Frame (vorher 0,02, also um Größenordnungen zu hoch)
  plus 3e-3 je m² und Frame je gehender Person, irgendwo im Blickfeld.

*Geister als Objekte mit Lebensdauer* (umgesetzt in 0.6.4): Jede Messung kann von einer Person, einem
bestehenden Geist des Sensors oder einem neuen Geist kommen. Ein Geist lebt im Mittel 1,5 s (gemessen) und ist nie dort, wo wahrscheinlich ein Körper
steht. Ein Echo aus 17 Messungen in 1,5 s an derselben Stelle zählt damit als *ein* Geist, nicht als 17
Beweise für eine Person. Mit der früheren, viel zu hohen Geisterdichte erklärte das Modell Sitzende als
„Person im Aussetzer plus Geist“ (80–89 %). Mit der gemessenen Dichte steht der Drehbuch-Durchlauf bei
91 %, und die Phantom-Person nach dem Kalibrierlauf am 4.10. um 18:07 verschwindet.

*Geister mit Geschwindigkeit* (0.6.10): Bis dahin hatte ein Geist keine Radialgeschwindigkeit. Am 5.10.
um 06:20 meldete der Wohnzimmer-Sensor im leeren Esszimmer 16 Frames lang ein Ziel mit immer
derselben Geschwindigkeit (−0,72 m/s, nachgeführt), das langsam weiterrutschte. Im ersten Frame galt es
zu 99,7 % als Geist; ab dem dritten erklärte eine gehende Person die Geschwindigkeit tausendfach besser,
und daraus wurde eine Person, die stundenlang unsichtbar im Raum blieb. Mit Geistern, die ihre
Geschwindigkeit behalten und mitrücken: in der Nacht 23:15–06:28 keine Phantom-Sekunde (3 Läufe, vorher
bis 5 Minuten), Drehbuch 96,7 % (12 Läufe, vorher 95,6 %).

## 4. Messmodell (wie ein Frame entsteht)

### 4.1 LD2450 (bis zu 3 Ziele je Frame)
Gegeben der Zustand:
- Jede Person erzeugt ein Ziel oder nicht, wie in 3.3 beschrieben: die Wahrscheinlichkeit eines Treffers
  ist `1 − U_s(x, τ+Δt) / U_s(x, τ)`.
- Zwei Personen im Abstand d erzeugen mit Wahrscheinlichkeit `res(d)` zwei Ziele, sonst
  eines dazwischen. `res(d)` **gemessen** (0 % unter 0,25 m, 41 % bei 0,75–1 m, rund 75 % ab 1 m), **gelernt**.
- Erkennungswahrscheinlichkeit je Ort und Sensor aus der Geometrie: Sichtfeld, Reichweite, Wände.
  **Gemessen** (5.10., 22 h Aufnahmen; eine gehende Person, die der andere Sensor sicher sieht): bis
  7 m so gut wie nah (0,8–1,0 bei 5,5–7 m), und 10° über den nominellen Rand des Sichtfelds hinaus
  noch etwa die Hälfte. Angenommen wird darum: voll bis 15° vor dem Rand, 0,5 am Rand, 0 erst 15°
  dahinter; voll bis 1 m über die nominelle Reichweite, dann abfallend. Bis 0.6.16 fiel sie schon ab
  4,5 m und 15° vor dem Rand auf 0,4 (0,18–0,48 bei 5,5–7 m) und war außerhalb 0. Die Ränder sahen
  blind aus, und die Wolke einer ungesehenen Person floss dorthin und blieb: Partikel, die an gut
  gesehene Stellen laufen, verlieren Gewicht, übrig bleiben die an den angeblich blinden (5.10., 16:44,
  eine Person „hing“ an der linken Wohnzimmerwand, nachdem Leon in den Flur gegangen war).
  Bis 0.6.6 wurde sie zusätzlich je 25-cm-Feld gelernt; im Drehbuch brachte das nichts, entfernt.
- Messort: `z ~ N(x, R_s(r))`, Streuung nach Entfernung r. Die beiden Sensoren messen dieselbe
  sitzende Person am Sofa bis 0,5 m auseinander. Ein je 50-cm-Feld gelernter Versatz (halbe-halbe auf
  die Sensoren) wurde ausprobiert (0.6.7): Das Drehbuch zählte damit öfter zu viele Personen (Median
  95 s gegen 78 s), also nicht übernommen.
- Radialgeschwindigkeit: `N(Projektion der Geschwindigkeit, σ_v)` mit σ_v = 0,25 m/s. **Gemessen**
  (4.10.) ist mehr: 0,40 m/s beim Gehen, 0,12 m/s im Stehen. Noch nicht übernommen.
- **Der Fehler bleibt eine Weile** (0.6.13). Der LD2450 glättet intern, und jeder Sensor sieht eine
  Person ein Stück woanders. **Gemessen** (Frau allein am Esstisch, 5.10.): Ortsfehler korrelieren von
  Frame zu Frame mit 0,98, nach 1 s mit 0,8, nach 3 s mit 0,5, nach 10 s nicht mehr; der Wohnzimmer-
  Sensor (6,5 m entfernt) wanderte seitlich zwischen 1,0 und 1,85 m, während der Esszimmer-Sensor
  stetig 1,6 m maß.
  Modell: Jedes Partikel trägt je Sensor einen Versatz `b_s` (wo das Ziel dieses Sensors gerade auf der
  Person sitzt), in Einheiten der Streuung (entlang der Sichtlinie, quer). `b_s` wandert als
  Ornstein-Uhlenbeck-Prozess mit Korrelationszeit 1 s und 90 % der Streuung; 10 % sind in jedem Frame
  neu. `z = x + L b_s + w`. Der Versatz wird nicht gezogen, sondern je Partikel als Gauß (Mittel,
  Varianz) mitgeführt und bei jedem Treffer wie ein Kalman-Filter fortgeschrieben (Rao-Blackwell); die
  Zuordnung zu einem Ziel wird dafür nach ihrem Anteil gezogen. Ohne Treffer kehrt er zur Vorgabe
  zurück: Die erste Messung zählt genau wie vorher, eine anhaltende Abweichung aber einmal je
  Korrelationszeit, nicht zehnmal je Sekunde.
  Ohne das erklärte „zwei Personen, jeder Sensor sieht eine“ einen wandernden Wohnzimmer-Sensor besser
  als „eine Person, ein Sensor misst 0,5 m daneben“: Jeder Frame bestrafte die Abweichung neu, während
  der zweiten Person das Nicht-gesehen-Werden nach den ersten Sekunden fast nichts mehr kostete (der
  lange Aussetzer-Schwanz). So entstanden nach einem Neustart zwei Personen am Tisch und am Sofa
  (Wahrheitsdatenbank, 12 Seeds: Reset 15 % → 0 %, Abend 25 % → 0 %).
  Ausprobiert: Korrelationszeit 3 s (Drehbuch 16 %, Orte bleiben an der Stelle der ersten Sekunden
  hängen, die Wolke findet den richtigen Ort zu langsam wieder), 0,5 s (Drehbuch 7,7 %), 30 % neu je
  Frame (Klone wieder 16 %). Früher (0.6.x) verworfen: jedes Partikel merkt sich nur seinen letzten
  Fehler ganz (ohne Vorgabe, ohne Rückkehr): Personen wurden gegenüber Geistern zu stark.
  **Offen:** Mit dem ehrlicheren LD2450 hat der LD2410C mehr Gewicht beim Ort. Am Esstisch (1,1 m vor dem
  Esszimmer-Sensor) liegt die Energie der Person in den Stufen 2–3 statt 1 und zieht die Wolke 0,1–0,2 m
  nach außen. Für die Zählung ohne Folgen.
- **Zwei nah beieinander, ein Ziel (offen).** Wer weniger als 1 m neben jemand anderem steht, bekommt
  oft kein eigenes Ziel (Auflösung `res(d)`, oben). Das Modell rechnet das derzeit nicht ein: Jede
  Person wird unabhängig erkannt oder nicht. Zwei Versuche wurden wieder entfernt:
  - 0.6.5: das Fehlen eines eigenen Ziels neben jemand anderem kostete nichts. Messen die beiden
    Sensoren dieselbe Person an verschiedenen Stellen (am Esstisch 0,5 m auseinander), erklärte
    „zwei Personen, jeder Sensor sieht eine“ jeden Frame besser als „eine Person, beide 25 cm
    daneben“: nach einem Neustart zwei Personen am Tisch (5.10.). Die richtige Rechnung bräuchte
    den Ort des gemeinsamen Ziels (zwischen beiden) und den festen Versatz der Sensoren.
  - ein gemeinsames Paar-Ziel in der Zuordnung (das Ziel zieht beide Wolken mit): eine unsichtbare
    zweite Person direkt neben einer sichtbaren war fast kostenlos (B „folgte“ A aus dem Flur),
    Drehbuch 69–73 %, doppelte Rechenzeit.
  Ohne die Auflösung bleibt beim gemeinsamen Gehen (Drehbuch-Schritt 8) manchmal eine Person zurück.
- Jeder lebende Geist erzeugt ein Ziel an seinem Ort.
- Mehr als 3 Ziele: der Sensor meldet 3 davon. Ein voller Frame sagt also nichts über die Fehlenden.
- **Leere Frames lässt die Firmware aus.** Solange weder der LD2450 ein Ziel noch der LD2410C Präsenz
  meldet, kommt nur alle 5 s ein Frame (Herzschlag). Jeder ausgelassene Frame war leer: Die ganze
  Lücke (bis 6 s; länger ist Datenverlust) ist Zeit ohne Treffer. Bis 0.6.16 zählte ein Frame höchstens
  1 s, und eine Person, die nichts mehr bestätigte, verlor nur ein Fünftel des Gewichts, das sie hätte
  verlieren müssen.

**Vorverarbeitung**, keine Wahrscheinlichkeit, sondern Datenreinigung:
- Bit-identisch wiederholte Ziele (Sensor friert ein) werden verworfen. **Gemessen:** echte Ziele ändern sich in jedem Frame.
- Ziele hinter Wänden oder außerhalb aller Räume werden verworfen. Dort kann keine Person sein, es sind Spiegelungen.
- Ziele näher als 0,3 m am Sensor werden verworfen. Der Sensor hängt in etwa 1,5 m Höhe und strahlt nach
  vorn. Eine Person so nah läge weit außerhalb seines senkrechten Blickwinkels, das Ziel kommt von der
  Montage (gesehen am umgehängten Wohnzimmer-Sensor, 4.10.).
- **Gehaltene Ziele.** Verliert der LD2450 ein Ziel, meldet er es noch gut eine Sekunde weiter, mit
  Frame für Frame derselben Geschwindigkeit und kaum wanderndem Ort. **Gemessen:** bei 70 % aller
  Zielenden 12–16 Frames dieselbe Geschwindigkeit (ungleich 0), mitten in einer Spur 8 Frames oder mehr
  nur in 2,5 % (5 Frames oder mehr: 6 %). Ab 5 gleichen Frames gilt ein Ziel als gehalten und wird wie
  ein eingefrorenes behandelt.
  So stand der Wohnzimmer-Sensor 1,5 s lang auf +0,24 m/s vor der Küchentür, während die Person schon
  in der Küche war.
- Eingefrorene Ziele sagen bis 35 s nichts über ihre Stelle (gemessen: eine still sitzende Person friert
  höchstens so lange ein). Länger eingefroren ist kein Mensch, und Fehlmessungen zählen dort wieder.
  Solange es nichts sagt, läuft auch die Uhr „nicht gesehen“ an dieser Stelle nicht: Sie beginnt erst,
  wenn das Einfrieren endet. Bis 0.6.4 lief sie weiter. Wer 35 s an einem eingefrorenen Ziel stand, galt
  danach schon als im langen Aussetzer, und dass ihn zwei Sensoren mit 95 % Trefferwahrscheinlichkeit
  nicht sahen, kostete fast nichts (Drehbuch-Schritt 5: „steht vor der Balkontür“).
- Ziele in einem Bereich ohne Sensor (durch eine offene Tür gesehen, z. B. jemand in der Küche) sind
  Messungen wie alle anderen: Personen haben auch dort einen Ort (Abschnitt 2).

### 4.2 LD2410C (Energie je Entfernungsstufe)
- Energie je Stufe gegeben „leer“, „jemand sitzt in dieser Stufe“ oder „jemand geht in dieser Stufe“:
  **gelernte** Histogramme. Mehrere Personen in einer Stufe: die stärkere zählt.
- Die Energien sind vom Gerät geglättet. Verwendet wird deshalb ein Wert je **gemessener**
  Korrelationszeit (mit einer Person in der Stufe 2,5–4 s, also alle 3 s), nicht jeder Frame. Das ist
  eine Aussage über das Gerät, keine Dämpfung.
- Gelernt nur von sicher gesehenen Personen (LD2450 als unabhängiger Beleg). Wer nach einem langen
  Aussetzer am selben Ort wieder gefunden wird, saß dort: Seine Stufe wird für diese Zeit rückwirkend
  als besetzt gelernt, sonst würden verdeckte Sitzende als „leer“ gelernt.
- Umgesetzt in 0.6.1. Auf dem Drehbuch-Durchlauf brachte das mit vorher gelernten Verteilungen
  88 % → 93 % und weniger Ausreißer.
- Eine Entfernungsstufe zählt erst als Beleg, wenn für sie genug gelernt ist: 100 Frames mit Person
  und 300 leer. Nach dem Umhängen hatte die angenommene Verteilung während eines Kalibrierlaufs eine
  Person an einer Stelle bestätigt, an der nie jemand war (4.10., 18:07).
- **Mehr Energie kann nur für eine Person sprechen.** Eine Person fügt reflektierte Energie hinzu, nie
  weniger: Das Verhältnis „mit Person“ zu „ohne“ steigt mit der Energie (angepasst an die gelernten
  Zählungen, gewichtet, im Mittel 1). Bis 0.6.11 nicht: In Stufen, in denen der LD2410C eine sitzende
  Person nicht mehr sieht (Esszimmer-Sensor, 4,5–6 m, das Sofa), hatte er „sehr niedrige Energie“ mit
  Person häufiger gelernt als ohne (79 % zu 59 %). Niedrige Energie zählte dann alle 3 s mit Faktor 1,3
  *für* eine Person in 5–6 m: eine Person an der Wohnzimmerwand, ohne ein einziges Ziel (5.10., 09:13
  bis mittags). Mit der Bedingung sind diese Stufen ohne Beweis (Faktor 1).
- **Was andere erklären, zuerst; dann die Nachbarstufen.** Die genaue Schrägentfernung ist unsicher,
  deshalb mischt jede Stufe die Nachbarn (25/50/25 %). Energie, die eine andere Person wahrscheinlich
  schon erklärt (innerhalb zweier Stufen von ihr), sagt über diese Person nichts. Das muss je Stufe
  *vor* dem Mischen gelten. Bis 0.6.12 umgekehrt: Die Frau saß 1,1 m vor dem Esszimmer-Sensor, ihre
  Energie füllte die Stufen 2 und 3 (Verhältnis 10). Ein Viertel davon landete gemischt in Stufe 4, wo
  niemand es erklärte: Faktor 2,8 alle 3 s für eine Person 3–3,75 m vor dem Sensor. Die zweite
  Bewohnerin des Modells wurde dort binnen 30 s von „außer Haus“ (92 %) zu „im Esszimmer“ (99 %), nie
  von einem Ziel gesehen, und blieb eine Stunde (5.10., 08:47; Wand-Morgen 17 % → 0,4 %).
- Störungen durch den LD2450 im selben Gehäuse (etwa alle 7 s) sind ein eigener, **gelernter**
  Geisteranteil der LD2410C-Energie.

## 5. Inferenz

Gesucht ist die Verteilung über den Zustand gegeben alle Frames. Das Verfahren berechnet sie
näherungsweise, aber es fügt **nichts hinzu**, was nicht im Modell steht.

**Je Person eine Partikelwolke, das Raster als Feld-Speicher (wie FLIP):**
- Jede Person ist eine Wolke aus etwa 500–1000 gewichteten Partikeln. Ein Partikel ist eine mögliche
  Lage der Person:
  - ein Ort mit Geschwindigkeit, Modus (geht / steht), Zeit im Modus und Zeit seit dem letzten Treffer
    je Sensor; in Küche / Balkon / Flur-Bereich zusätzlich die Zeit seit dem Betreten,
  - oder „draußen“ mit der Zeit seit dem Verlassen.

  Die Wolke *ist* die Superposition. Sitzende sind Partikel mit Geschwindigkeit null.
- Ein Kalman-Filter wird nicht gebraucht. Er ist der Sonderfall des Bayes-Filters für eine einzelne
  Glockenkurve, und genau die gibt es hier oft nicht: „Balkon oder an der Tür“ sind zwei Berge, Wände
  schneiden ab, tote Winkel verformen.
- **Zwischen Frames** bewegt sich jedes Partikel nach 3.1:
  - Wände halten es auf.
  - Durch eine Tür kommt es in den Bereich dahinter.
  - Aus einem Bereich kommt es nach 3.2 wieder heraus.
- **Jeder Frame** multipliziert jedes Partikelgewicht mit „wie gut erklärt die Person an dieser Stelle
  diesen Frame“: Treffer nach 4.1, kein Treffer nach 3.3. Danach wird normiert. Wenn wenige Partikel
  fast alles Gewicht tragen, wird neu gezogen (Resampling).
- **Jeder Ort ist eine Komponente** (Mixture Particle Filter, Vermaak, Doucet, Pérez 2003): beobachteter
  Bereich, jeder Bereich ohne Sensor, außer Haus. Das Gesamtgewicht einer Komponente ist die
  Wahrscheinlichkeit, dass die Person dort ist; es folgt exakt Bayes. Neu gezogen wird nur innerhalb
  einer Komponente, und jede behält mindestens 16 Partikel. „Außer Haus“ ist damit dasselbe wie die
  Existenzwahrscheinlichkeit eines Bernoulli-Filters (Ristic, Vo, Vo, Farina 2013).
  Bis 0.6.15 wurde die ganze Wolke gemeinsam neu gezogen. Ein Ort mit kleinem Gewicht verlor dabei alle
  Partikel, und mit ihnen die Möglichkeit „die Person ist gar nicht hier“: Eine Person, die nichts mehr
  bestätigte, konnte den Raum nur noch sichtbar durch eine Tür verlassen, also nie. So blieben
  Phantome stundenlang (5.10.: zwei Personen im Wohnzimmer, niemand zu Hause). Der Gegenbeweis
  (LD2410C ohne Energie, kein Treffer) traf alle Partikel gleich und verpuffte im Normieren.
  Seltene Übergänge (Kommen aus einem Bereich, Heimkommen) werden nur dann öfter ausprobiert, wenn
  genug Partikel am Ort bleiben; sonst probierte sich ein Ort leer, und seine Masse sprang um hunderte
  Zehnerpotenzen.
- **Seltene Übergänge** werden öfter ausprobiert, als sie vorkommen, und das Gewicht wird exakt
  korrigiert (Importance Sampling). Ein Beispiel ist „kommt jetzt aus dem Flur“, sonst ist es zu selten,
  um überhaupt ein Partikel an die Tür zu bringen. Das ändert nur die Rechengenauigkeit, nicht das Modell.
- **Das Raster** (etwa 20 cm) speichert die gelernten Größen je Feld (3.1, 3.3, 3.4, 4.1). Es nimmt
  außerdem die Summe der Partikelgewichte je Feld auf: für die Wärmekarte und die Zonenwerte.
- **Was daraus von selbst folgt:**
  - Ungesehene Wege: Partikel, die durch gut gesehene Felder müssten, verlieren dort ihr Gewicht.
  - Die Rückkehr aus einem Bereich ist dieselbe Person.
  - Die Wärmekarte jeder Person zeigt, warum das Modell etwas glaubt.

**Mehrere Personen:** Die Wolken werden getrennt gerechnet. Was Personen verbindet, steckt in der
Zuordnung je Frame:
- Welches Ziel kommt von wem, welches von einem Geist oder einem neuen Geist, welches von einer
  gerade hereinkommenden Person. Alle Zuordnungen werden mit ihrer Wahrscheinlichkeit aufsummiert,
  wie beim JPDA-Verfahren.
- Wer verdeckt wen (4.1, Auflösung), und zwei Körper nicht am selben Fleck (3.1).

Das ist eine Näherung: Gemeinsame Abhängigkeiten über mehrere Frames hinweg gehen verloren. Ob sie reicht,
zeigen die Drehbuch-Schritte 7 bis 9.

**Anzahl der Personen:** Neue Personen entstehen nur in den Bereichsfeldern „draußen“ mit der Rate `α`
und kommen von dort durch die Türen. Eine Person, deren Wolke fast ganz „draußen“ liegt, ist weg.
Es gibt sie im Modell nicht mehr, bis wieder jemand hereinkommt.

**Rechenaufwand:** 500–1000 Partikel je Person, mit numpy je Frame wenige Millisekunden. Die
Partikelzahl ist eine Einstellung der Näherung, kein Modellparameter. Zeigt sich, dass die Wolke einen
Gehenden zu unruhig verfolgt, kann jedes Partikel eine kleine Glocke für die Geschwindigkeit tragen
(Rao-Blackwell). Das wäre eine Rechenverbesserung, kein zweites Modell.

**Superposition**, Beispiel: A geht zur Balkontür und wird nicht mehr gesehen. Dann liegen etwa 95 % von
As Partikeln auf dem Balkon und 5 % an der Tür. Sieht der Sensor die Tür gut und meldet nichts,
schwinden die 5 %. Kommt jemand durch die Balkontür herein, erklären As Balkon-Partikel das, und es ist A.
Es entsteht keine neue Spur.

## 6. Ausgaben
- Je Zone: Verteilung der Personenzahl (wahrscheinlichster Wert, P(mindestens 1)), P(jemand bewegt
  sich), P(gleich betreten). Alles direkt aus den Wolken summiert.
- Für die Anzeige: jede Person an ihrem wahrscheinlichsten Ort. Ist das ein Bereich ohne Sensor (z. B.
  Balkon), wird sie dort angezeigt und nicht als Punkt an der Tür. Eine Person, deren Ort unsicher
  ist, wird blass und mit ihren Möglichkeiten angezeigt. Es gibt keine liegengebliebenen Spuren, weil
  es keine Spuren gibt, nur Personen. Nummern werden über die Zeit durch Zuordnung zur vorherigen
  Anzeige stabil gehalten. Das ist reine Beschriftung.

## 7. Lernen
Alle Größen aus 3 und 4 mit „gelernt“ werden aus den Wolken geschätzt, gewichtet mit ihrer Wahrscheinlichkeit:
erwartete Zählungen (wie oft war hier jemand sichtbar, wie oft gab es ein Ziel, wie lange dauerten
Aussetzer, wo entstanden Geister, …), gemischt mit dem Prior. Wird ein Sensor umgehängt, vergisst er
alles, was von seinem Ort abhängt (Geister, LD2410C).

## 8. Was aus dem bisherigen Code wegfällt

| bisher | ersetzt durch |
|---|---|
| `evidence_time`-Dämpfung | Nicht-gesehen-Dauer `U` (3.3), Geisterlebensdauer (3.4) |
| `lost_after`, `coast_time`, „verloren“-Status, Aussetzer-Beginn | `U` (3.3) |
| Erklärungsarten track / getup / out / jump / any | Übergänge aus 3, ein Messmodell |
| Gast-Platz, `residents`, `guests` | Ankunftsrate `α` (3.2) |
| Aufteilung beim Verlust, `doorway_walk`, Sammelanteile `spawn_share` | Tür = Bewegung (3.2) |
| `getup_share`, `getup_time`, `getup_spread` | `λ_go(x, d)` (3.1) |
| Hypothesen, Zusammenlegen, `max_hypotheses`, `hypothesis_floor`, Kalman/IMM | Partikelwolke je Person + Zuordnung je Frame (5) |
| LD-Regel „schon von jemand anderem erklärt“, Körperabstoßung im LD-Teil | Messmodell 4.2, Körperabstand 3.1 |
| Dijkstra-Karten | entfallen: ungesehene Wege folgen aus der Wolke (5) |

## 9. Prüfung
1. **Je Wahrscheinlichkeit ein Test** gegen eine Handrechnung (z. B. ein Frame, eine Person, ein Ziel:
   die Gewichte von „Person“ und „Geist“ stimmen auf drei Stellen mit der Formel überein).
2. **Kleine Szenen** (eine Person geht rein und setzt sich, zwei kreuzen sich, ein Geist steht 10 s).
3. **Das Drehbuch** (`TESTDREHBUCH.md`) mit echten Aufnahmen und notierten Zeiten. Das ist das
   Freigabekriterium. Die simulierten Abende dienen nur zum Entwickeln.
