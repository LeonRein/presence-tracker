# Testdrehbuch: Wahrheitsdaten für den Tracker

Etwa 20 Minuten zu zweit (A und B). Die App zeichnet wie immer alle Sensordaten auf. Für jeden Schritt
beim Start den Knopf auf der Drehbuch-Seite drücken (`tools/drehbuch_server.py`, Port 8765), sonst die
Uhrzeit notieren. Wer
gerade nichts zu tun hat, wartet **außerhalb** von Wohn- und Esszimmer (Flur oder Schlafzimmer), mit
geschlossener oder angelehnter Tür wie sonst auch.

Positionen müssen nicht exakt sein, nur der Ort (Sofa, Tisch, …) zählt.

| # | Wer | Was | Dauer | Prüft |
|---|---|---|---|---|
| 0 | – | beide draußen, Wohn- und Esszimmer leer | 2 min | leerer Raum: keine Geister-Personen |
| 1 | A | aus dem Flur rein, zum Sofa, hinsetzen, **still sitzen** (Handy ok) | 3 min | Hereinkommen, Sitzende mit Aussetzern |
| 2 | B | aus dem Flur rein, an den Esstisch setzen | 2 min | zweite Person durch die Tür |
| 3 | B | vom Tisch in die Küche, dort bleiben | 2 min | Küche: weg, aber nicht verschwunden |
| 4 | B | aus der Küche zurück an den Tisch | 1 min | Rückkehr mit derselben Person |
| 5 | A | vom Sofa auf den Balkon, dort bleiben | 2 min | Balkon |
| 6 | A | vom Balkon zurück aufs Sofa | 1 min | Rückkehr |
| 7 | B | vom Tisch aufs Sofa, **direkt neben A** setzen | 3 min | zwei dicht nebeneinander |
| 8 | A + B | gleichzeitig aufstehen, **nebeneinander** zum Esstisch gehen, hinsetzen | 1 min | zwei gehen als ein Ziel |
| 9 | A + B | A geht zur Küchentür und zurück, B gleichzeitig zum Sofa und zurück, Wege **kreuzen sich** | 1 min | Kreuzen, keine dritte Person |
| 10 | A | in den Flur hinaus (bleibt draußen) | – | Gehen durch die Flurtür |
| 11 | B | allein am Tisch sitzen bleiben | 3 min | keine Phantomperson neben B |
| 12 | B | in den Flur hinaus | – | |
| 13 | – | beide draußen, Räume leer | 3 min | nach dem Gehen bleibt niemand „versteckt“ |
| 14 | A | rein, **mitten im Raum** stehen bleiben (nicht sitzen) | 1 min | Stehende Person ohne Sitzplatz |
| 15 | A | wieder raus in den Flur | – | Ende |

Ihr könnt das Drehbuch auch auf
mehrere Tage verteilen oder einzelne Schritte wiederholen. Wichtig ist nur, dass zu jeder Zeit klar
ist, wer wo war.
