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
   gesehen (Verdeckung, Sitzhaltung), Geister leben eine Weile, Messfehler sind teils systematisch
   (Versatz), und der LD2450 glättet intern, sodass sein Fehler eine knappe Sekunde lang bleibt (4.1,
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
- **Gehen:** fast konstante Geschwindigkeit mit zufälligen Richtungs- und Tempoänderungen.
  Rauschstärke **gemessen** auf den Aufnahmen.
- **Stehen / Sitzen:** Ort fast fest, kleines Wackeln. **Gemessen.**
- **Wechsel gehen → stehen:** Rate `λ_stop` = 0,5 /s, überall gleich.
- **Wechsel stehen → gehen:** Rate `λ_go(d) = 0,58 / (d + 5,6 s)`, abhängig davon, wie lange jemand
  schon steht oder sitzt (lange Sitzende stehen seltener auf). Gemessen an den Sitzdauern.
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
Es gibt keinen eigenen Zustand „ausgesetzt“ und keine Schwelle „verloren“. Es gibt eine **gelernte**
Wahrscheinlichkeit je Feld, Sensor und Modus (steht / geht):

> `U_s(x, τ)` = P(eine Person, die an x bleibt, wird von Sensor s τ Sekunden lang nicht detektiert)

- Für kleine τ ist das die gewöhnliche Fehlquote eines Frames (1 − Erkennungswahrscheinlichkeit).
  Für große τ zeigt sie, wie lange Verdeckungen an dieser Stelle dauern: Sofalehne lange, Raummitte
  praktisch nie.
- **Gelernt** aus sicheren Personen: wie lange sie an jeder Stelle ohne Treffer blieben. Ausgeklammert
  werden Zeiten, in denen jemand anders daneben das Ziel bekommen haben kann. Prior: aus den Aufnahmen.
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

Daraus folgt ohne Sonderregel: An einer schlecht sichtbaren Stelle, ohne gesehenes Weggehen, ist „noch
dort“ die wahrscheinlichste Möglichkeit. An einer gut sichtbaren Stelle verliert sie schnell, und die
Wahrscheinlichkeit verteilt sich auf die Wege, die ungesehen möglich waren, und die Türen dahinter.

### 3.4 Geister
Je Sensor:
- **Entstehen:** Poisson-Rate je Ort `β_s(x)`, **gelernt** als Geisterkarte. Zusätzlich mehr Geister in
  der Nähe von Gehenden (Mehrwegeechos), Faktor **gelernt**.
- **Lebensdauer:** **gelernte** Verteilung, die meisten unter 1 s, manche viele Sekunden an derselben
  Stelle. Wo der Nutzer Störzonen zeichnet (Ventilator, Vorhang), ist `β` dort hoch.
- Ein Geist bleibt an seinem Ort (kleines Wackeln) und ist nur für seinen Sensor da.

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
bestehenden Geist des Sensors oder einem neuen Geist kommen. Ein Geist steht still (keine
Radialgeschwindigkeit), lebt im Mittel 1,5 s (gemessen) und ist nie dort, wo wahrscheinlich ein Körper
steht. Ein Echo aus 17 Messungen in 1,5 s an derselben Stelle zählt damit als *ein* Geist, nicht als 17
Beweise für eine Person. Mit der früheren, viel zu hohen Geisterdichte erklärte das Modell Sitzende als
„Person im Aussetzer plus Geist“ (80–89 %). Mit der gemessenen Dichte steht der Drehbuch-Durchlauf bei
91 %, und die Phantom-Person nach dem Kalibrierlauf am 4.10. um 18:07 verschwindet.

## 4. Messmodell (wie ein Frame entsteht)

### 4.1 LD2450 (bis zu 3 Ziele je Frame)
Gegeben der Zustand:
- Jede Person erzeugt ein Ziel oder nicht, wie in 3.3 beschrieben: die Wahrscheinlichkeit eines Treffers
  ist `1 − U_s(x, τ+Δt) / U_s(x, τ)`.
- Zwei Personen im Abstand d erzeugen mit Wahrscheinlichkeit `res(d)` zwei Ziele, sonst
  eines dazwischen. `res(d)` **gemessen** (0 % unter 0,25 m, 41 % bei 0,75–1 m, rund 75 % ab 1 m), **gelernt**.
- Messort: `z ~ N(x + b_s(x), R_s(x))`. Versatz `b_s` je Sensor und Feld **gelernt** aus gleichzeitigen
  Messungen einer Person durch zwei Sensoren (nur der Unterschied ist beobachtbar, halbe-halbe).
  Streuung `R_s` nach Entfernung **gelernt**. Ein langsam wandernder Fehleranteil (Zeitkonstante
  etwa 30 s) ist Teil des Personenzustands je Sensor.
- Radialgeschwindigkeit: `N(Projektion der Geschwindigkeit, σ_v)` mit σ_v = 0,25 m/s. **Gemessen**
  (4.10.) ist mehr: 0,40 m/s beim Gehen, 0,12 m/s im Stehen. Noch nicht übernommen.
- **Offen: Der Fehler bleibt eine Weile.** Der LD2450 glättet intern. **Gemessen:** Von Frame zu Frame
  (0,09 s) korrelieren Ortsfehler mit 0,98 und Geschwindigkeitsfehler mit 0,91, nach 0,7 s kaum noch.
  Das Modell zählt jeden Frame als unabhängig und überschätzt so einzelne Messungen. Ein erster Versuch
  (jedes Partikel merkt sich je Sensor seinen letzten Fehler, nur der Rest zählt) machte Personen
  gegenüber Geistern zu stark: Phantome in leeren Räumen, Drehbuch 74 %. Verworfen; die Geister
  müssten dasselbe Gedächtnis bekommen.
- **Zwei nah beieinander, ein Ziel.** Wer weniger als 1 m neben jemand anderem steht, bekommt oft kein
  eigenes Ziel (Auflösung `res(d)`, oben). Diese Wahrscheinlichkeit, aus den Wolken der anderen
  berechnet, zählt zum „nicht gesehen“ dazu, nur im beobachteten Bereich. Ohne sie galt „läuft
  ungesehen neben dem anderen her“ als sehr teuer, und im Drehbuch-Schritt 8 (beide gehen nebeneinander
  zum Tisch) blieb oft eine Person auf dem Sofa zurück. Ein gemeinsames Paar-Ziel in der Zuordnung
  (das Ziel zieht beide Wolken mit) wurde ausprobiert und verworfen: Es macht eine unsichtbare zweite
  Person direkt neben einer sichtbaren fast kostenlos (B „folgte“ A aus dem Flur), Drehbuch 69–73 %,
  doppelte Rechenzeit.
- Jeder lebende Geist erzeugt ein Ziel an seinem Ort.
- Mehr als 3 Ziele: der Sensor meldet 3 davon. Ein voller Frame sagt also nichts über die Fehlenden.

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
alles, was von seinem Ort abhängt (Erkennung, Geister, Versatz, Aussetzerorte).

## 8. Was aus dem bisherigen Code wegfällt

| bisher | ersetzt durch |
|---|---|
| `evidence_time`-Dämpfung | Nicht-gesehen-Dauer `U` (3.3), Geisterlebensdauer (3.4), Versatz (4.1) |
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
