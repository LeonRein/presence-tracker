# Das Wahrscheinlichkeitsmodell des Presence Trackers

Stand 0.6.19 (6.10.2026). Dieses Dokument ist die Spezifikation: Der Code wertet genau dieses Modell
aus und enthält keine eigenen Fallunterscheidungen. Jede Wahrscheinlichkeit steht hier mit ihrer
Herkunft: **gemessen** auf Aufnahmen, **gelernt** im Betrieb oder **angenommen**. Was noch nicht gebaut
ist, steht als *Offen* da. Zahlen in der Form „x % falsch“ stammen aus der Wahrheitsdatenbank
(Abschnitt 8).

## 1. Grundsätze

1. **Ein generatives Modell.** Es beschreibt, wie die Welt (Personen, Geister) sich bewegt und wie die
   Sensoren daraus ihre Frames erzeugen. Der Tracker rechnet daraus rückwärts, welche Welt die Frames am
   besten erklärt (Bayes). Es gibt keine Regeln der Art „nach 1,5 s gilt jemand als verloren“, sondern
   nur Wahrscheinlichkeiten; die vielen Partikel bilden die verschiedenen Möglichkeiten ab.
2. **Personen entstehen und verschwinden nicht im Raum.** Verfolgt werden die Bewohner (Einstellung
   „Bewohner“, bei uns 2). Jede ist irgendwo: in einem Raum mit Sensor, in einem Bereich ohne Sensor oder
   außer Haus. Ins Haus und hinaus geht es nur über die Wege nach draußen (Treppe), in den beobachteten
   Bereich und hinaus nur durch Türen. Eine Messung mitten im Raum kommt also von einer Person, die auf
   dem Weg dorthin hätte gesehen werden müssen, oder von einem Geist.
   *Offen:* unbekannte Neuankömmlinge (Gäste), siehe 3.2.
3. **Ohne Evidenz verschwindet eine Person.** Wird sie an einer Stelle nicht mehr bestätigt, an der die
   Sensoren sie sehen müssten, verliert diese Stelle Wahrscheinlichkeit an die anderen Möglichkeiten
   (Bereiche ohne Sensor, außer Haus). Wie schnell, folgt aus den gemessenen Aussetzern (3.3).
4. **Frames sind gegeben den Zustand unabhängig.** Dass aufeinanderfolgende Frames nicht unabhängig
   *aussehen*, hat Ursachen, und die sind modelliert: Wer an einer Stelle nicht gesehen wird, wird dort
   meist länger nicht gesehen (3.3), Geister leben eine Weile (3.4), und jeder Sensor sieht eine Person
   eine Weile an derselben falschen Stelle (4.1). Eine pauschale Dämpfung gibt es nicht.
5. **Gemessen vor angenommen, gelernt nur aus unabhängiger Evidenz.** Gelernt wird nur, wo ein zweiter
   Sensor oder eine sicher gesehene Person die Wahrheit liefert (Abschnitt 7). Aus den eigenen Schätzungen
   zu lernen verstärkt die eigenen Fehler (4.10. auf den Daten bestätigt).

## 2. Die Welt (Zustand)

**Geometrie** (aus dem Grundriss abgeleitet, nicht eingestellt): Räume, die die Sensoren zu mindestens
60 % sehen, bilden den beobachteten Bereich. Räume ohne Sensor, die über Türen zusammenhängen, bilden je
einen Bereich *Rₖ*; er ist *offen*, wenn man von dort das Haus verlassen kann (Treppe, Eingang). Dazu
kommt *außer Haus*. Bei uns seit 5.10., 17:23: beobachtet Wohnzimmer, Esszimmer, Küche; ohne Sensor die
offene Gruppe Arbeitszimmer, Bad, Treppe, Flur, Schlafzimmer und der geschlossene Balkon.

Auch in einem Bereich ohne Sensor hat eine Person einen Ort. Kein Sensor sieht ihn, außer durch eine
offene Tür. Bis 0.6.4 hatte sie dort keinen Ort: Sah der Esszimmer-Sensor jemanden 1 m hinter der
Küchentür, konnte das keine Hypothese erklären, und „blieb vor der Tür stehen“ gewann (4.10., 18:23).

**Zustand zur Zeit t:**

| Teil | Inhalt |
|---|---|
| Personen | die Bewohner; je Person: Ort (beobachteter Bereich, ein Bereich *Rₖ* oder außer Haus), dort Position und Geschwindigkeit, Modus geht / steht mit der Zeit darin, Zeit seit dem Betreten des Orts, je Sensor die Zeit seit dem letzten Treffer und der Versatz, mit dem dieser Sensor die Person gerade sieht (4.1) |
| Geister | je Sensor beliebig viele; je Geist Ort, Radialgeschwindigkeit, Wahrscheinlichkeit, noch da zu sein, Alter |

Personen sind anonym. Die Nummern sind die Wolken der Bewohner; wechselt eine Wolke die Person, der sie
folgt (Personentausch), ändert das an den Zahlen je Raum nichts und ist nicht schlimm.

## 3. Dynamik (was zwischen zwei Zeitpunkten passiert)

### 3.1 Bewegung im beobachteten Bereich
- **Gehen:** fast konstante Geschwindigkeit mit zufälligen Richtungs- und Tempoänderungen, 0,6 m/s²
  je √s; höchstens 2 m/s. **Gemessen** (5.10., 4,6 h Zielspuren des LD2450, Wege ab 1,5 m): Tempo im
  Median 0,8 m/s, 95 % unter 1,6 m/s; Geschwindigkeitsänderungen über 2–3 s wie 0,6 m/s²/√s (über
  0,5 s mehr, darin steckt das Messrauschen der Geschwindigkeit). Bis 0.6.17 angenommen: 1,0. Damit
  war die Richtung eines Wegs schnell vergessen, und wer zur Tür hinausging, blieb im Modell davor
  stehen (5.10., 16:44). 12 Seeds: Leon geht hinaus 7,9 % → 2,8 % falsch, Abend 8,9 % → 0 %, Drehbuch
  9,4 % → 9,3 %. Gegenprobe: 0,45 und 0,8 sind beide schlechter.
- **Stehen / Sitzen:** Ort fast fest, Wackeln 0,02 m je √s. **Gemessen.**
- **Wechsel gehen → stehen:** Rate `λ_stop` = 0,5 /s, überall gleich (angenommen). *Gemessen und
  verworfen:* In den Zielspuren dauert ein Weg (ab 1,5 m) im Median 4 s, in den ersten 2 s bleibt fast
  niemand stehen, danach 10–20 % je s. Damit im Modell: Drehbuch 12,7 % statt 9,3 %, Leon geht hinaus
  10,4 %. Vermutlich misst die Zielspur das Anhalten zu spät: Der LD2450 glättet intern und führt ein
  Ziel nach dem Anhalten noch mit Tempo weiter (4.1, gehaltene Ziele). Die Statistik stammt damit zum
  Teil aus dem Bewegungsmodell des Sensors, nicht aus den Menschen.
- **Wechsel stehen → gehen:** Rate `λ_go(d) = 0,58 / (d + 5,6 s)`, abhängig davon, wie lange jemand
  schon steht oder sitzt (lange Sitzende stehen seltener auf). **Gemessen** an den Sitzdauern.
- **Warum „geht / steht“ ein eigener Zustand ist und nicht aus der Geschwindigkeit folgt:** Er trägt
  die Sichtbarkeit. Ein Sitzender fällt beim LD2450 oft lange aus, ein Gehender nur kurz (3.3). Mit
  der Aussetzer-Statistik der Sitzenden auch für Gehende war das Drehbuch deutlich schlechter.
- **Wände:** Orte außerhalb des freien Raums haben Wahrscheinlichkeit 0. Gehende kommen nur durch Türen
  in einen anderen Raum.
- **Körper:** Zwei Personen stehen nicht näher als 0,3 m beieinander (angenommen).
- *Geprüft und verworfen (5.10.):*
  - **Karte der Bewegungen** (Maps of Dynamics, CLiFF-Map; Kucner et al. 2017), offline: gelernt je
    50-cm-Feld aus Rohzielen des LD2450, die der andere Sensor bestätigt, auf 70 % der Zeit (3249
    Geschwindigkeiten, 56 Felder); geprüft auf den übrigen 30 %: Wo ist eine gehende Person in 1–3 s?
    Die Karte zieht die Geschwindigkeit mit 0,5 /s zu einer dort gelernten: nach 3 s 13 cm weniger
    Fehler. Dieselbe Rechnung ohne Ort bringt genauso viel (14 cm). Der Ort trägt bei dieser Datenmenge
    nichts bei.
  - **Rückkehr zu typischen Geschwindigkeiten** (integrierter Ornstein-Uhlenbeck-Prozess, T = 2,5 s),
    im Filter, 12 Seeds gegen 0.6.18: Drehbuch 9,3 % → 7,4 %, leeres Haus mit angedocktem Roboter 25 %
    → 13 %, aber Leon geht hinaus 2,8 % → 4,2 % und Abend 0 % → 16 % (eine Sitzende fehlt 9 Minuten).
    Es verbessert, was weniger zählt, und verschlechtert, was zählt.
  - **Lernen je Ort** (4.10.): Raten und Aussetzer je 50-cm-Feld (Sofa: lange Sitzdauern, Durchgang:
    kurze). Das Modell lernte dabei eigene Messfehler als Verhalten und verstärkte sie.

### 3.2 Türen, Bereiche ohne Sensor, außer Haus
- Wer sich durch eine Tür bewegt, ist danach im Bereich dahinter. Das ist eine Folge der Bewegung,
  kein eigenes Ereignis, und gilt in beide Richtungen.
- Wer in einem Bereich *Rₖ* ist, kommt mit der Ausfallrate `h_k(Alter)` an einer seiner Türen wieder
  heraus, gehend, in den Raum hinein. `h_k` stammt aus der Verteilung der Aufenthaltsdauern: Annahme
  geschlossene Bereiche (Balkon) Median 2 min, offene Bereiche (Flur-Gruppe mit Schlafzimmer) 30 min,
  breit gestreut, mit schwerem Schwanz (wer länger bleibt als üblich, bleibt plausibel). Dazu kommen
  die beobachteten Besuche, je wie ein Teil der Verteilung.
  *Offen:* Seit 0.6.11 werden keine neuen Besuche mehr gelernt; verwendet wird die Annahme.
- Die Dauer zählt nur so weit, wie die Tür unbeobachtet ist. Wer herauskommt, geht durch die Tür und
  wird dort normalerweise gesehen. Solange das nicht passiert, ist „noch drin“ die beste Erklärung.
- **Außer Haus:** Wer in einem offenen Bereich ist, verlässt das Haus mit der Rate 1/(2 h); wer außer
  Haus ist, kommt mit 1/(4 h) je Weg nach draußen wieder (angenommen).
- *Offen: unbekannte Neuankömmlinge.* Gedacht als gelernte Ankunftsrate an der Tür nach draußen. Im
  Versuch (0.6.1) wurde aus jeder Fehlzuordnung an einer Tür sofort eine dauerhafte zusätzliche Person.
  Der passende Filtertyp wäre Labeled Multi-Bernoulli (Reuter, Vo, Vo, Dietmayer 2014): je Person
  eine eigene Existenz.

### 3.3 Nicht gesehen werden
Es gibt keinen eigenen Zustand „ausgesetzt“ und keine Schwelle „verloren“. Es gibt eine
**gemessene** Wahrscheinlichkeit je Sensor und Modus (steht / geht), über die Erkennungswahrscheinlichkeit
des Orts (4.1) auch je Ort:

> `U_s(x, τ)` = P(eine Person, die an x bleibt, wird von Sensor s τ Sekunden lang nicht detektiert)

- Für kleine τ ist das die gewöhnliche Fehlquote eines Frames (1 − Erkennungswahrscheinlichkeit).
  Für große τ zeigt sie, wie lange Aussetzer dauern.
- Kein Treffer im Frame gewichtet „die Person ist an x“ mit `U_s(x, τ+Δt) / U_s(x, τ)`. τ ist die Zeit
  seit dem letzten Treffer dieser Person bei diesem Sensor, Δt die Zeit, für die der Frame steht (4.1:
  leere Frames lässt die Firmware aus).
- **Gemessen:** Stehende fallen im Mittel alle 70 s aus, mit langem Schwanz; die Tabelle reicht bis
  120 s. Gehende im Blickfeld werden nach 0,3–1,2 s wieder gefunden (im Mittel 0,7 s); die langen Lücken
  Gehender waren Leute, die das Blickfeld verlassen hatten.
- **Der Schwanz** jenseits der Tabelle fällt mit ihrer letzten Steigung weiter, ein Drittel je Minute.
  **Gemessen** (5.10., 40 min mit genau einer ruhigen Person): Wer 10–80 s nicht gesehen wurde, wird
  mit 1–7 % je Frame wiedergefunden, nicht immer seltener. Bis 0.6.15 fiel der Schwanz wie 1/τ: Eine
  Person, die 20 Minuten niemand gesehen hatte, blieb mit 1:4,5 noch eine Stunde ungesehen, und ein
  Phantom blieb mit ihr.
- Wie sichtbar Sitzende wirklich sind, hängt stark vom Ort ab (gemessen 5.10.): am Tisch 1 m vor dem
  Esszimmer-Sensor in 93 % der Frames, auf dem Sofa 5–6 m entfernt in 8–14 %, mit Lücken bis 94 s
  (Wohnzimmer-Sensor) und 317 s (Esszimmer-Sensor). Je Ort gelernt wird das nicht (1., Punkt 5).
- Daraus folgt ohne Sonderregel: An einer schlecht sichtbaren Stelle, ohne gesehenes Weggehen, ist
  „noch dort“ die wahrscheinlichste Möglichkeit. An einer gut sichtbaren Stelle verliert sie schnell,
  und die Wahrscheinlichkeit geht an die Wege, die ungesehen möglich waren, und die Orte dahinter.
  Dass diese Alternativen nicht aussterben, sichert die Inferenz (5, Komponenten je Ort).
- *Offen:* Eigentlich gehört ein langer Aussetzer zu einer Haltung an einem Ort, nicht zur Person.
  Umgesetzt (τ ab dem letzten Wechsel zu „geht“) war das Drehbuch deutlich schlechter.

*Gemessen und verworfen (5.10.):*
- **Gemeinsame Aussetzer.** Sitzende verlieren beide LD2450 gleichzeitig etwa 109-mal pro Stunde für
  mindestens 1 s (Hälfte über 3 s, 1 % über 30 s). Ein gemeinsamer Zustand „gerade für alle unsichtbar“
  je Partikel machte Phantome billiger: Drehbuch 14,2 % statt 6,2 %.
- **„Die Person ist in Wirklichkeit woanders“** mit kleiner Rate (1/2 h bis 1/10 min), auch zurück in
  den Raum, allein und mit den gemeinsamen Aussetzern: Phantome lösen sich, aber echte Sitzende
  verschwinden mit (Drehbuch 15–35 %). Eine frisch eingespeiste Alternative schlägt eine echte Sitzende
  nach einer langen Lücke immer. Gelöst wurde das ohne erfundene Rate (5, Komponenten je Ort).

### 3.4 Geister
Je Sensor:
- **Entstehen:** Dichte je Ort und Frame. Grundwert 1e-4 je m² (**gemessen**: nachts in leeren Räumen
  0,4 Geister pro Stunde beim Esszimmer-Sensor, keine beim Wohnzimmer-Sensor), plus 3e-3 je m² je
  gehender Person irgendwo im Blickfeld (Mehrwegeechos; **gemessen**: neben Sitzenden keine Geister,
  etwa 4 kurze pro Minute, solange jemand läuft). Je Ort **gelernt** als Geisterkarte, aber nur, wo ein
  zweiter Sensor gut hinsieht und nichts meldet (7). Ohne die Karte war das Drehbuch in drei Messreihen
  schlechter (z. B. Spiegelungen durch die Küchentür als Person in der Küche). Die Küche sieht seit 5.10.
  ein eigener Sensor, aber kein zweiter: Dort gilt nur der Grundwert.
- **Lebensdauer:** im Mittel 1,5 s, exponentiell (**gemessen**: Nachtgeister 1,5 s, Echos etwa 1 s).
  Ein lebender Geist erzeugt in 85 % der Frames ein Ziel an seinem Ort. Ein Echo aus 17 Messungen in
  1,5 s an derselben Stelle zählt damit als *ein* Geist, nicht als 17 Beweise für eine Person. Ein Geist
  ist nie dort, wo wahrscheinlich ein Körper steht.
- **Geschwindigkeit** (0.6.10): Ein Geist behält die Radialgeschwindigkeit, mit der er auftaucht
  (stehende Echos etwa 0, ein vom LD2450 nachgeführtes Ziel genau seine letzte), und rückt mit seinen
  eigenen Zielen mit. Vorher wurde aus einem nachgeführten Ziel mit −0,72 m/s im leeren Esszimmer eine
  Person, die stundenlang unsichtbar blieb (5.10., 06:20).
- Bis 0.6.14 gab es von Hand gezeichnete Störzonen; sie verwarfen jede Messung darin, auch die echter
  Personen. Entfernt; dafür ist die gelernte Geisterkarte da.
- *Offen:*
  - **Spiegelbilder an Wänden.** Der Küchen- und der Esszimmer-Sensor sehen eine gehende Person
    zusätzlich gespiegelt an der Küchenwand, gegenläufig (5.10., 19:18). Hinter der Wand werden sie
    verworfen, davor gelten sie als Geister oder ziehen eine zweite Person an. Sie ließen sich aus der
    Lage der Person und der Wand vorhersagen.
  - **Der Saugroboter.** Fahrend und angedockt meldet der Wohnzimmer-Sensor ihn als Ziel; er kann als
    Person gelten (für Leon nicht schlimm). Lage und Zustand stünden in Home Assistant
    (`camera.dobby_map`, `vacuum.dobby`).

## 4. Messmodell (wie ein Frame entsteht)

### 4.1 LD2450 (bis zu 3 Ziele je Frame)
Gegeben der Zustand:
- Jede Person erzeugt ein Ziel oder nicht, wie in 3.3 beschrieben: die Wahrscheinlichkeit eines Treffers
  ist `1 − U_s(x, τ+Δt) / U_s(x, τ)`.
- **Erkennungswahrscheinlichkeit** je Ort und Sensor aus der Geometrie: Sichtfeld, Reichweite, Wände.
  **Gemessen** (5.10., 22 h Aufnahmen; eine gehende Person, die der andere Sensor sicher sieht): bis
  7 m so gut wie nah (0,8–1,0 bei 5,5–7 m), und 10° über den nominellen Rand des Sichtfelds hinaus
  noch etwa die Hälfte. Angenommen wird darum: voll bis 15° vor dem Rand, 0,5 am Rand, 0 erst 15°
  dahinter; voll bis 1 m über die nominelle Reichweite, dann abfallend. Bis 0.6.16 fiel sie schon ab
  4,5 m und 15° vor dem Rand auf 0,4 und war außerhalb 0. Die Ränder sahen blind aus, und die Wolke
  einer ungesehenen Person floss dorthin und blieb (5.10., 16:44: eine Person „hing“ an der linken
  Wohnzimmerwand, nachdem Leon in den Flur gegangen war). Bis 0.6.6 wurde sie zusätzlich je 25-cm-Feld
  gelernt; das brachte nichts.
- **Messort:** `z = x + L b_s + w`, Streuung `L` nach Entfernung (entlang der Sichtlinie
  0,15 m + 2 % der Entfernung, quer 0,10 m + 5 %, zum Rand des Sichtfelds mehr).
- **Der Fehler bleibt eine Weile** (0.6.13). Der LD2450 glättet intern, und jeder Sensor sieht eine
  Person ein Stück woanders. **Gemessen** (Frau allein am Esstisch, 5.10.): Ortsfehler korrelieren von
  Frame zu Frame mit 0,98, nach 1 s mit 0,8, nach 3 s mit 0,5, nach 10 s nicht mehr; der Wohnzimmer-
  Sensor (6,5 m entfernt) wanderte seitlich zwischen 1,0 und 1,85 m, während der Esszimmer-Sensor
  stetig 1,6 m maß.
  Modell: Jedes Partikel trägt je Sensor einen Versatz `b_s` in Einheiten der Streuung. `b_s` wandert als
  Ornstein-Uhlenbeck-Prozess mit Korrelationszeit 1 s und 90 % der Streuung; 10 % sind in jedem Frame
  neu (`w`). Der Versatz wird nicht gezogen, sondern je Partikel als Gauß mitgeführt und bei jedem
  Treffer wie ein Kalman-Filter fortgeschrieben (Rao-Blackwell). Die erste Messung zählt damit wie ohne
  Versatz, eine anhaltende Abweichung aber einmal je Korrelationszeit, nicht zehnmal je Sekunde.
  Ohne das erklärte „zwei Personen, jeder Sensor sieht eine“ einen wandernden Sensor besser als „eine
  Person, ein Sensor misst 0,5 m daneben“: nach einem Neustart zwei Personen am Tisch oder Sofa (Reset
  15 % → 0 %, Abend 25 % → 0 %). Ausprobiert: Korrelationszeit 3 s (Drehbuch 16 %: die Wolke findet den
  richtigen Ort zu langsam wieder), 0,5 s (7,7 %), 30 % neu je Frame (Klone wieder 16 %).
- **Radialgeschwindigkeit:** `N(Projektion der Geschwindigkeit, σ_v)` mit σ_v = 0,25 m/s.
  *Offen:* **gemessen** (4.10.) sind 0,40 m/s beim Gehen und 0,12 m/s im Stehen.
- **Leere Frames lässt die Firmware aus.** Solange weder der LD2450 ein Ziel noch der LD2410C Präsenz
  meldet, kommt nur alle 5 s ein Frame (Herzschlag). Jeder ausgelassene Frame war leer: Die ganze
  Lücke (bis 6 s; länger ist Datenverlust) ist Zeit ohne Treffer. Bis 0.6.16 zählte ein Frame höchstens
  1 s, und eine Person, die nichts mehr bestätigte, verlor nur ein Fünftel des Gewichts, das sie hätte
  verlieren müssen.
- Ein lebender Geist erzeugt ein Ziel (3.4). Mehr als 3 Ziele: Der Sensor meldet 3 davon. Ein voller
  Frame sagt also nichts über die Fehlenden.
- *Offen:* **Zwei nah beieinander, ein Ziel.** Wer weniger als 1 m neben jemand anderem steht, bekommt
  oft kein eigenes Ziel (**gemessen**: 0 % zwei Ziele unter 0,25 m, 41 % bei 0,75–1 m, rund 75 % ab
  1 m). Das Modell rechnet das nicht ein. Zwei Versuche wurden wieder entfernt: Das Fehlen eines eigenen
  Ziels neben jemand anderem kostete nichts (0.6.5; zwei Personen am Tisch, weil die Sensoren eine
  Person 0,5 m auseinander messen), und ein gemeinsames Paar-Ziel in der Zuordnung (eine unsichtbare
  zweite Person neben einer sichtbaren war fast kostenlos). Beim gemeinsamen Gehen (Drehbuch-Schritt 8)
  bleibt deshalb manchmal eine Person zurück.

**Vorverarbeitung**, keine Wahrscheinlichkeit, sondern Datenreinigung:
- Ziele hinter Wänden oder außerhalb aller Räume werden verworfen (bis 0,4 m hinter einer Wand gelten
  sie noch). Dort kann keine Person sein, es sind Spiegelungen.
- Ziele näher als 0,3 m am Sensor werden verworfen. Der Sensor hängt in etwa 1,5 m Höhe und strahlt nach
  vorn; eine Person so nah läge weit außerhalb seines senkrechten Blickwinkels.
- **Eingefrorene Ziele** (bitgenau wiederholt; echte Ziele ändern sich in jedem Frame) und **gehaltene
  Ziele** (Verliert der LD2450 ein Ziel, meldet er es noch gut eine Sekunde weiter, Frame für Frame mit
  derselben Geschwindigkeit; **gemessen** bei 70 % aller Zielenden; ab 5 gleichen Frames) sind keine
  Messung. Sonst sind sie nichts Besonderes: Die Person dort ist schlicht nicht detektiert, mit den
  Aussetzern von 3.3. Bis 0.6.18 galt dazu eine Sonderregel: Bis 35 s sagte ein eingefrorenes Ziel
  nichts über seine Stelle, und die Uhr „nicht gesehen“ lief dort nicht. **Gemessen** (5.10.): Ruhende
  Ziele in leeren Räumen sind zu 86 % eingefrorene, typisch 35 s lang, bei echten ruhigen Personen sind es
  22 %. Das Einfrieren ist vor allem die Spur einer Person, die gerade gegangen ist; die Regel gab ihr
  35 s Schonfrist. Ohne sie, 12 Seeds: Leon geht hinaus 2,8 % → 2,3 % falsch (weg nach 23 s statt
  33 s), Küche 7,3 % → 5,4 %, Drehbuch gleich mit weniger Wechseln der Anzeige; leeres Haus mit
  angedocktem Roboter 25 % → 31 %.
- Ziele in einem Bereich ohne Sensor (durch eine offene Tür gesehen) sind Messungen wie alle anderen:
  Personen haben auch dort einen Ort (2).

### 4.2 LD2410C (Energie je Entfernungsstufe)
- Energie je Stufe (0,75 m) gegeben „leer“, „jemand sitzt in dieser Stufe“ oder „jemand geht in dieser
  Stufe“: **gelernte** Histogramme (7). Eine Stufe zählt erst als Beleg, wenn für sie genug gelernt ist:
  100 Frames mit Person und 300 leer.
- Als Beleg zählt die Energie nur innerhalb von ±25° vor dem Sensor; erzeugt wird sie von Personen
  innerhalb von ±60°. Das Sofa und Dobbys Station liegen außerhalb des ausgewerteten Kegels.
- Die Energien sind vom Gerät geglättet. Verwendet wird deshalb ein Wert je **gemessener**
  Korrelationszeit (mit einer Person in der Stufe 2,5–4 s, also alle 3 s), nicht jeder Frame. Das ist
  eine Aussage über das Gerät, keine Dämpfung.
- **Mehr Energie kann nur für eine Person sprechen.** Das Verhältnis „mit Person“ zu „ohne“ steigt mit
  der Energie (an die gelernten Zählungen angepasst). Bis 0.6.11 nicht: In Stufen, in denen der LD2410C
  eine sitzende Person kaum sieht (Esszimmer-Sensor, 4,5–6 m, das Sofa), hatte er „sehr niedrige
  Energie“ mit Person häufiger gelernt als ohne. Niedrige Energie zählte dann alle 3 s *für* eine Person
  an der Wohnzimmerwand, ohne ein einziges Ziel (5.10., 09:13 bis mittags).
- **Was andere erklären, zuerst; dann die Nachbarstufen.** Die genaue Schrägentfernung ist unsicher,
  deshalb mischt jede Stufe die Nachbarn (25/50/25 %). Energie, die eine andere Person wahrscheinlich
  schon erklärt (innerhalb zweier Stufen von ihr), sagt über diese Person nichts, und das gilt je Stufe
  *vor* dem Mischen. Bis 0.6.12 umgekehrt: Die Energie der Frau 1,1 m vor dem Esszimmer-Sensor füllte die
  Stufen 2 und 3, ein Viertel davon landete in Stufe 4, und dort entstand eine zweite, nie gesehene
  Person, die eine Stunde blieb (5.10., 08:47).
- *Offen:* Am Esstisch liegt die Energie der Person in den Stufen 2–3 statt 1 und zieht die Wolke
  0,1–0,2 m nach außen. Für die Zählung ohne Folgen. Die Störungen durch den LD2450 im selben Gehäuse
  (etwa alle 7 s) sind nicht eigens modelliert.

## 5. Inferenz

Gesucht ist die Verteilung über den Zustand gegeben alle Frames. Das Verfahren berechnet sie
näherungsweise, aber es fügt **nichts hinzu**, was nicht im Modell steht.

**Je Person eine Partikelwolke (800 Partikel):**
- Ein Partikel ist eine mögliche Lage der Person mit allen Größen aus 2. Die Wolke *ist* die
  Superposition. Sitzende sind Partikel mit Geschwindigkeit null.
- Ein einzelner Kalman-Filter reicht nicht: „Balkon oder an der Tür“ sind zwei Berge, Wände schneiden ab,
  tote Winkel verformen. Nur der Versatz der Sensoren (4.1) wird je Partikel als Gauß gerechnet.
- **Zwischen Frames** bewegt sich jedes Partikel nach 3.1 und 3.2.
- **Jeder Frame** multipliziert jedes Partikelgewicht mit „wie gut erklärt die Person an dieser Stelle
  diesen Frame“: Treffer nach 4.1, kein Treffer nach 3.3; der LD2410C alle 3 s nach 4.2.
- **Jeder Ort ist eine Komponente** (Mixture Particle Filter, Vermaak, Doucet, Pérez 2003): beobachteter
  Bereich, jeder Bereich ohne Sensor, außer Haus. Das Gesamtgewicht einer Komponente ist die
  Wahrscheinlichkeit, dass die Person dort ist; es folgt exakt Bayes. Wenn wenige Partikel fast alles
  Gewicht tragen, wird neu gezogen, aber nur innerhalb einer Komponente, und jede behält mindestens 16
  Partikel. „Außer Haus“ ist damit dasselbe wie die Existenzwahrscheinlichkeit eines Bernoulli-Filters
  (Ristic, Vo, Vo, Farina 2013).
  Bis 0.6.15 wurde die ganze Wolke gemeinsam neu gezogen. Ein Ort mit kleinem Gewicht verlor dabei alle
  Partikel, und mit ihnen die Möglichkeit „die Person ist gar nicht hier“: Eine Person, die nichts mehr
  bestätigte, konnte den Raum nur noch sichtbar durch eine Tür verlassen, also nie. So blieben Phantome
  stundenlang (5.10.: zwei Personen im Wohnzimmer, niemand zu Hause). Der Gegenbeweis (LD2410C ohne
  Energie, kein Treffer) traf alle Partikel gleich und verpuffte im Normieren.
- **Seltene Übergänge** (aus einem Bereich kommen, heimkommen, aufstehen in Richtung einer Messung)
  werden öfter ausprobiert, als sie vorkommen, und das Gewicht wird exakt korrigiert (Importance
  Sampling). Das ändert nur die Rechengenauigkeit, nicht das Modell. Öfter ausprobiert wird nur, solange
  genug Partikel am Ort bleiben; sonst probierte sich ein Ort leer, und seine Masse sprang um hunderte
  Zehnerpotenzen.
- **Das Raster** (0,25 m) hält je Sensor die Erkennungswahrscheinlichkeit aus der Geometrie und die
  gelernte Geisterkarte.

**Mehrere Personen:** Die Wolken werden getrennt gerechnet. Was Personen verbindet, steckt in der
Zuordnung je Frame: Welches Ziel kommt von welcher Person, von einem bestehenden Geist oder von einem
neuen Geist. Alle Zuordnungen werden mit ihrer Wahrscheinlichkeit aufsummiert, wie beim JPDA-Verfahren.
Dazu kommt, dass zwei Körper nicht am selben Fleck stehen (3.1). Das ist eine Näherung: Gemeinsame
Abhängigkeiten über mehrere Frames hinweg gehen verloren.

**Superposition**, Beispiel: A geht zur Balkontür und wird nicht mehr gesehen. Dann liegen etwa 95 % von
As Partikeln auf dem Balkon und 5 % an der Tür. Sieht der Sensor die Tür gut und meldet nichts,
schwinden die 5 %. Kommt jemand durch die Balkontür herein, erklären As Balkon-Partikel das, und es ist A.

## 6. Ausgaben
- **Je Raum mit Sensor:** die wahrscheinlichste Personenzahl (aus den Wahrscheinlichkeiten der einzelnen
  Personen, dort zu sein), dazu bewegt / ruhig und „wird gleich betreten“ aus dem angezeigten Ort und der
  Geschwindigkeit der Personen, die gerade gesehen werden.
- **Je Bereich ohne Sensor:** die Wahrscheinlichkeit je Person, dort zu sein; eine Zahl je Raum gibt es
  nur, wenn der Bereich aus einem Raum besteht. Diese Zahlen dienen der Plausibilität an den Türen
  („konnte gerade jemand herauskommen?“); bewertet wird der Tracker an den Räumen mit Sensor.
- **Im Haus:** die wahrscheinlichste Zahl über alle Orte; in der Oberfläche nur nachrangig, weil sie
  Vermutungen über Räume ohne Sensor enthält.
- **Für die Karte:** jede Person an ihrem wahrscheinlichsten Ort (im beobachteten Bereich das dichteste
  20-cm-Feld, sonst der Bereich), dazu ihre Wolke als Wärmekarte. Nicht gesehene Personen werden blass
  angezeigt, mit ihren Möglichkeiten in Prozent.

## 7. Lernen
Gelernt wird nur, wo eine unabhängige Quelle die Wahrheit liefert (1., Punkt 5):
- **Geisterkarte** je Sensor und 0,25-m-Feld: Wie oft meldet der Sensor dort ein Ziel, während ein
  anderer Sensor die Stelle gut sieht (Erkennung ≥ 0,8), nichts meldet und keine sichere Person in der
  Nähe ist.
- **LD2410C-Histogramme** je Stufe: Energie mit einer sicher gesehenen sitzenden oder gehenden Person in
  der Stufe (LD2450 als unabhängiger Beleg) und ohne jemanden innerhalb zweier Stufen. Wer nach einem
  langen Aussetzer am selben Ort wieder gefunden wird, saß dort: Seine Stufe wird für diese Zeit
  rückwirkend als besetzt gelernt, sonst würden verdeckte Sitzende als „leer“ gelernt.
- Alles Gelernte verblasst mit einer Halbwertszeit von 7 Tagen (Möbel werden umgestellt). Wird ein
  Sensor umgehängt, vergisst er alles, was von seinem Ort abhängt. Das Gelernte übersteht Neustarts.
- *Offen:* Aufenthaltsdauern in Bereichen ohne Sensor (3.2).
- Bewusst nicht gelernt: Erkennungswahrscheinlichkeit, Aussetzer und Verhalten je Ort (3.1, 3.3, 4.1).

## 8. Prüfung
1. **Die Wahrheitsdatenbank** (`~/.config/presence-tracker/truth`, privat: sie enthält den Grundriss):
   Episoden aus echten Aufnahmen mit bekannter Wahrheit je Raum mit Sensor. Die Wahrheit kommt von Leon
   (Notizen, die Seite „Jetzt gerade“ der Drehbuch-App) und von den Handy-Trackern (`person.leon`,
   `person.alisa`: niemand zu Hause). Dazu je Episode die Konfiguration und das bis dahin Gelernte.
   Heute: das Drehbuch vom 4.10., zwei Nächte, Reset, Abend, Morgen mit Wand-Phantom, leeres Haus mit
   Saugroboter, Leons Gang durch den Flur, der Küchengang vom 5.10.
2. **`tools/evaluate.py`** spielt jede Episode mit mehreren Seeds ab (12 für eine Entscheidung, die
   Streuung zwischen den Seeds ist groß) und misst je Sekunde: falsche Zahl je Raum, zu viele / zu
   wenige Personensekunden, Wechsel der Anzeige ohne Grund, Zeit bis zur richtigen Zahl.
   **`tools/replay.py`** zeigt für eine einzelne Szene, was der Tracker wann glaubt.
3. **Freigabe:** Eine Änderung kommt nur hinein, wenn sie auf der Wahrheitsdatenbank verbessert, was
   zählt: Wer geht, verschwindet; wer sitzt, bleibt angezeigt; die Zahlen je Raum stimmen. Weniger
   wichtig sind der Saugroboter als Person, kurze Aussetzer Sitzender und vertauschte Personen.
4. **Tests** (`tests/`) für kleine simulierte Szenen und die Vorverarbeitung.
