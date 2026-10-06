// Kombi-Gehaeuse: HLK-LD2450 + HLK-LD2410C (oder LD2412) + ESP32-S3/C3 SuperMini oder ESP32-S3-Zero
// Teile: Gehaeuse (shell), Deckel (lid), Eckhalter (corner), Schrankfuss (stand)
//
// Koordinaten (Sensor): x = rechts, y = Tiefe (0 = Front, +y Richtung Wand), z = oben
//                       z = 0 ist der Innenboden.
//
// Anordnung (von vorne): LD2450 hochkant an der rechten Wand, JST-Buchse unten.
// Darunter liegt der LD2410C quer, also um 90 Grad zum LD2450 gedreht wie beim
// Apollo R PRO-1. Die Antennen beider Radare sind dann gekreuzt polarisiert.
// Zwischen beiden liegen der Raum fuer den JST-Stecker (Abstand) und eine
// Trennrippe mit Schlitz fuer ein optionales Abschirmblech.
//
// Export:  openscad -D 'part="shell"'  -o shell.stl  radar-combo-case.scad
//          part = "shell" | "lid" | "corner" | "stand" | "stand_flat" | "assembly" | "assembly_stand"
//          Deckel fuer den ESP32-S3-Zero: openscad -D 'part="lid"' -D 'esp_board="S3-Zero"' -o lid_s3zero.stl ...

part = "assembly";
esp_board = "SuperMini";   // "SuperMini" (ESP32-S3/C3 SuperMini) oder "S3-Zero" (Waveshare); aendert nur den Deckel
secz = 20;
secx = -8;

$fn = 48;

// ---------- Parameter ----------
wall     = 1.6;    // Seitenwaende
front_t  = 0.6;    // Radom (Front) - duenn halten (<= lambda/8 in PLA ~ 0.95 mm); 3 Schichten a 0,2 mm
R        = 4;      // Eckenradius (Frontansicht)
ch       = 1;      // 45-Grad-Fase an Vorder- und Hinterkante
cl       = 0.25;   // Spiel fuer Platinen
ri       = R - wall; // Innenradius der Gehaeuseecken -> ueberall gleich dicke Wand

l50 = 44;  w50 = 15; t50 = 1.2;   // LD2450 (gemessen 1,25; mit 1,2 sitzt er nachweislich perfekt)
jst = 7;                          // Platz fuer den JST-Stecker des LD2450 (0 = Kabel direkt angeloetet)
g50 = 1.5;                        // Abstand LD2450 zur oberen Wand, damit die Platinenecke
                                  // an der Eckenrundung (ri) vorbeikommt (mind. ~1,35 bei ri = 2,4)
// Radar fuer stille Personen (untere Tasche): "LD2410C" (22 x 16 mm) oder "LD2412" (28 x 11 mm)
static_radar = "LD2410C";
l12 = static_radar == "LD2412" ? 28  : 22;
w12 = static_radar == "LD2412" ? 11  : 16;
t12 = static_radar == "LD2412" ? 1.0 : 1.65;   // LD2410C gemessen: 1,65 mm
so12 = static_radar == "LD2412" ? 1.0 : 1.2;   // Abstand, LD2410C hat Bauteile auf der Front
// Federstege zwischen den Taschen: tragen die inneren Rastnasen, nur an den Enden gehalten
fb   = 1.2;    // Dicke eines Federstegs (= Dicke der uebrigen Innenwaende)
fgap = 1.0;    // Luft zwischen Steg und Front (Steg wird als Bruecke gedruckt, darf etwas durchhaengen)
sgap = 0.7;    // Federweg hinter einem Steg (LD2410C-Steg -> Trennrippe, LD2450-Steg -> freie Spalte)
// Trennrippe zwischen den Radaren: zwei Rippen mit Schlitz fuer ein Abschirmblech (optional)
fen_rib  = 0.8;   // Dicke je Rippe
fen_slot = 0.6;   // Schlitz (Alublech 0,1-0,3 mm oder gefaltetes Kupferband)
rib  = 1.2;    // Dicke der festen Innenrippen
npil = 4;      // Ausbrech-Saeulen unter dem LD2450-Steg (nur fuer den Druck, danach herausbrechen)
npil12 = 2;    // dito unter dem LD2410C-Steg
npil_esp = 3;  // dito unter dem ESP-Steg im Deckel
so  = 1.0;                        // Abstand Antenne -> Radom-Innenseite
// Gemeinsame Hoehe aller Innenwaende (y): knapp ueber den Rastnasen beider Radare
ridge_up  = 0.6;   // Rastnasen-Mitte ueber der Platinenrueckseite (Nase beginnt 0,1 mm darueber)
inner_top = front_t + max(so + t50, so12 + t12) + ridge_up + 0.5;   // knapp ueber den Nasen

// Platinenlaenge ohne USB-Buchse, gemessen: SuperMini 23,0 (Datenblatt 22,52), S3-Zero 24,1 (Datenblatt 23,5)
esp_play = 0.6;   // Laengsspiel; mit 0,4 sass der SuperMini aeusserst stramm
esp_w = 18; esp_l = (esp_board == "S3-Zero" ? 24.1 : 23.0) + esp_play; esp_t = 1.65;
                                         // Dicke angenommen (Standard 1,6) - federnder Arm gleicht 1,2..1,9 aus
usb_over = 1.9;                          // USB-C-Buchse steht so weit ueber die Platinenkante (gemessen)
esp_z0 = 0;                              // ESP-Unterkante liegt auf dem Boden bzw. der Deckelnase; Buchse ragt durch den Ausschnitt
usb_h = 3.2;                             // Hoehe USB-C-Buchse ueber der Platine

lcl = 0.15;                // Spiel Deckellippe -> Gehaeuse
// Innenbreite: LD2450 (15) + Federsteg + freie Spalte links fuer Schraubdome und Kabel;
// mindestens so breit wie die LD2410C-/LD2412-Tasche
iw = max(27, l12 + 2*cl + 2*rib);
z12 = 1.5;                 // Unterkante untere Radar-Platine
z12t = z12 + w12 + cl + fb;          // Oberkante LD2410C-Tasche (Federsteg)
zf0 = z12t + sgap;                   // Trennrippe unten
zf1 = zf0 + 2*fen_rib + fen_slot;    // Trennrippe oben
z50b = zf1 + cl + jst;               // Unterkante LD2450 (JST-Ende)
ih = z50b + l50 + g50;               // Innenhoehe (LD2410C: 74,5)
x50 = iw/2 - cl - w50;               // linke Kante LD2450
split_y = 11.8;            // Trennebene Gehaeuse / Deckel
esp_y = split_y - 1.5 - esp_t;           // Vorderseite (Bauteilseite) der ESP-Platine
// Rastlippe am oberen ESP-Ende: nur die Haelfte ohne Keramikantenne (S3 SuperMini, von vorne gesehen links)
// Gemessen am echten Board: Antenne bei x = -0,9 ... 5,7, Pad von Pin 8 ab x = -7,6
// Federarm mit Rastnase (antennenfreier Streifen): x-Bereich
rid_x0 = -6.0; rid_x1 = -2.0;    // obere Rastnase im antennenfreien Streifen
esp_top = esp_z0 + esp_l;                // Oberkante ESP-Platine
// ESP wie die Radare: oben Rastnase auf einem schwebenden Federsteg, unten feste Keilnasen
// Federsteg zwischen zwei Pfosten (x), so lang wie das schmale Gehaeuse erlaubt (22,7 mm);
// die 45-Grad-Stuetzen der Pfosten enden an der Deckellippe
eb_x0 = -(iw/2 - lcl) + 2; eb_x1 = -eb_x0;
eb_t   = 1.2;               // Dicke des Federstegs (z), wie die Radar-Stege
eb_gap = 1.0;               // Luft zwischen Steg und Deckelinnenseite (Bruecke)
eb_ytop = 7.6;              // Steg reicht bis hier nach vorne (y)
notch_hw = 6.65;            // halbe Breite des USB-Ausschnitts (Platz fuer die Fluegel mit den Keilnasen)
wing_fl  = 1.5;             // 45-Grad-Verstaerkung der Fluegel nach aussen (im Boden, x); nach oben ist kein
                            // Platz: dort sitzen Bauteile und das 5V-Pad mit den Draehten
wing_y0  = esp_y + 0.15;    // Vorderkante der Nase = Fuss der freistehenden Fluegel
lid_t   = 4.2;             // Deckel (enthaelt Schwalbenschwanz-Nut)
D  = split_y + lid_t;      // Gesamttiefe
W  = iw + 2*wall;
zo0 = -wall;  zo1 = ih + wall;
H  = zo1 - zo0;

// M2-Schrauben Deckel: beide in der freien Spalte links neben dem LD2450,
// der untere Dom ueber den Pfosten des ESP-Federstegs
boss_x = -iw/2 + 4;
bosses = [[boss_x, 34], [boss_x, ih - 6.5]];

// Schwalbenschwanz (Halter-Schiene / Deckel-Nut)
dt_base = 5;  dt_tip = 7;  dt_h = 3;  dt_cl = 0.25;   // dt_cl: Spiel der Nut im Deckel
rail_cl = 0.1;   // zusaetzliches Spiel je Seite (x), nur an der Schiene von Eckhalter und Schrankfuss
dt_top  = ih - 12;              // Nut oben geschlossen -> Sensor liegt auf
z_r0    = zo0 + 13;             // Eckhalter: Schiene beginnt hier, darunter und darueber je eine Schraube

// Schrankfuss
tilt = 10;       // Neigung nach unten (Grad); "stand_flat" ist dieselbe Form mit 0 Grad
elev = 20;       // Abstand Sensor-Unterkante -> Schrank: Platz fuer einen gewinkelten USB-C-Stecker
usb_head = 18;   // Freiraum unter der Buchse fuer den Steckerkopf (gewinkelte Stecker: ca. 7-15 mm)
cable_w = 10; cable_h = 10;   // Kabeltunnel unter dem Fuss nach hinten (Breite, Hoehe)

// ---------- Grundformen ----------
module ycyl(h, r1, r2) { rotate([-90,0,0]) cylinder(h=h, r1=r1, r2=r2); }

module corner_piece(len) {
    ycyl(ch, R-ch, R);
    translate([0,ch,0]) ycyl(len-2*ch, R, R);
    translate([0,len-ch,0]) ycyl(ch, R, R-ch);
}

module body() {
    hull() for (sx=[-1,1], z=[zo0+R, zo1-R])
        translate([sx*(W/2-R), 0, z]) corner_piece(D);
}

module rrect_y(w, z0, z1, r, y0, y1) {   // abgerundetes Rechteck in xz, extrudiert in y
    hull() for (sx=[-1,1], z=[z0+r, z1-r])
        translate([sx*(w/2-r), y0, z]) ycyl(y1-y0, r, r);
}

module cavity() { rrect_y(iw, 0, ih, ri, front_t, D+1); }

module box(x0,x1,y0,y1,z0,z1) {   // Ecken in beliebiger Reihenfolge
    translate([min(x0,x1), min(y0,y1), min(z0,z1)]) cube([abs(x1-x0), abs(y1-y0), abs(z1-z0)]);
}

// Rastnase entlang x (haelt Platine gegen Herausfallen)
module ridge_x(x0, x1, y, z, dir) {   // dir: +1 = ragt nach +z, -1 = nach -z
    hull() {
        box(x0, x1, y-0.5, y+0.5, z - (dir<0 ? 0.01 : 0), z + (dir<0 ? 0 : 0.01));
        box(x0, x1, y-0.05, y+0.05, dir>0 ? z : z-0.55, dir>0 ? z+0.55 : z);
    }
}

// Grundriss (xy) von Nase und Fluegeln im Boden: vorne neben der Buchse schmal, zum Fuss der Fluegel
// hin unter 45 Grad nach aussen verbreitert (druckbar: der Deckel liegt beim Druck auf der Rueckseite).
// Der Ausschnitt im Gehaeuse ist derselbe Umriss mit Spiel.
module wing_outline(y1) {
    yf = wing_y0 - wing_fl;   // hier beginnt die Schraege
    xo = notch_hw - 0.15;
    polygon([[-xo, esp_y-usb_h-0.15], [xo, esp_y-usb_h-0.15], [xo, yf], [xo+wing_fl, wing_y0],
             [xo+wing_fl, y1], [-xo-wing_fl, y1], [-xo-wing_fl, wing_y0], [-xo, yf]]);
}

// ---------- Gehaeuse ----------
// LD2450 in eigenen Koordinaten (wie im alten, liegenden Gehaeuse):
//   u = laengs (0 = Ende mit der Stiftleiste, l50 = JST-Ende), v = quer (0 = Federsteg-Seite),
//   Endwand bei u = -g50, Seitenwand bei v = w50 + cl.
// place50() dreht das um 90 Grad im Uhrzeigersinn (von vorne gesehen): JST-Ende unten,
// Seitenwand = rechte Gehaeusewand, Endwand = Decke, Federsteg links.
module place50() { translate([x50, 0, ih - g50]) rotate([0, 90, 0]) children(); }

module pocket50() {
    top50 = inner_top;
    wv = w50 + cl;   // Seitenwand
    // Abstandsleisten an den Enden
    // (bis an Wand, Rippe und Rippenstuecke verlaengert, damit keine 0,25-mm-Spalte bleiben)
    box(-g50, 1.5, front_t, front_t+so, -cl, wv);
    // Fuellrippe zwischen Endwand und Platine: fuehrt die LD2450 in Laengsrichtung (Spiel cl)
    box(-g50, -cl, front_t, top50, -cl-fb, wv);
    box(l50-1.5, l50+cl, front_t, front_t+so, -cl, wv);
    // Rippe auf der Federsteg-Seite: schwebt fgap ueber der Front und ist nur an den Enden
    // gehalten -> federt ueber die ganze Laenge, keine Mittelstuetze, die abbrechen kann.
    box(-g50, l50+cl+rib, front_t+fgap, top50, -cl-fb, -cl);
    box(-g50, 1.5, front_t, top50, -cl-fb, -cl);             // Ende an der Decke
    box(l50-1.5, l50+cl+rib, front_t, top50, -cl-fb, -cl);   // JST-Ende
    // Ausbrech-Saeulen: stuetzen die Bruecke beim Druck (Spannweite ~8 mm),
    // werden danach herausgebrochen
    let (x0 = 1.5, x1 = l50-1.5)
        for (i = [1:npil]) let (xp = x0 + i*(x1-x0)/(npil+1))
            // ueber die volle Stegbreite, in Laengsrichtung nur 0,8 mm (bricht leicht ab)
            box(xp-0.4, xp+0.4, front_t-0.01, front_t+fgap+0.01, -cl-fb, -cl);
    // Stecker-Seite: nur kurze Stuecke an beiden Laengskanten
    // (mit 45-Grad-Stuetze nach aussen Richtung Stecker-Raum, wie im Deckel)
    for (zr = [[-cl-fb, 3], [wv-3, wv]]) hull() {
        box(l50+cl, l50+cl+rib, front_t, top50, zr[0], zr[1]);
        box(l50+cl, l50+cl+rib+(top50-front_t), front_t-0.01, front_t, zr[0], zr[1]);
    }
    // Rastnasen: fest an der Seitenwand, federnd am Steg
    ridge_x(6, 14, front_t+so+t50+0.35, -cl, 1);
    ridge_x(30, 38, front_t+so+t50+0.35, -cl, 1);
    ridge_x(6, 14, front_t+so+t50+0.35, wv, -1);
    ridge_x(30, 38, front_t+so+t50+0.35, wv, -1);
}

module shell() {
    top12 = inner_top;
    difference() {
        union() {
            difference() {
                intersection() { body(); box(-W, W, -1, split_y, zo0-1, zo1+1); }
                cavity();
            }
            // Innenteile, aussen auf die Gehaeuseform beschnitten (45-Grad-Stuetzen laufen in die Wand)
            intersection() {
                intersection() { body(); box(-W, W, -1, split_y, zo0-1, zo1+1); }
                union() {
            // --- LD2450 Tasche (hochkant) ---
            place50() pocket50();

            // --- Trennrippe zwischen den Radaren ---
            // zwei Rippen von Wand zu Wand, dazwischen ein Schlitz bis auf das Radom: dort kann ein
            // Streifen Alublech oder gefaltetes Kupferband (ca. 4 x 27 mm) eingeschoben werden
            for (zz = [zf0, zf1 - fen_rib]) box(-iw/2, iw/2, front_t, inner_top, zz, zz + fen_rib);

            // --- Tasche LD2410C / LD2412 ---
            difference() {
                // steht auf dem Boden; obere Rippe = Federsteg (Dicke fb), darueber Luft sgap
                box(-l12/2-cl-rib, l12/2+cl+rib, front_t, top12, 0, z12t);
                box(-l12/2-cl, l12/2+cl, front_t-1, top12+1, z12-cl, z12+w12+cl);
                // Luft unter dem Steg: nur die Seitenrippen halten ihn
                box(-l12/2-cl, l12/2+cl, front_t-1, front_t+fgap, z12+w12+cl-0.01, z12t+0.01);
            }
            // 45-Grad-Stuetzen aussen an den Seitenrippen der LD2410C-Tasche (wie im Deckel)
            for (sx=[-1,1]) hull() {
                box(sx*(l12/2+cl), sx*(l12/2+cl+rib), front_t, top12, 0, z12t);
                box(sx*(l12/2+cl), sx*(l12/2+cl+rib+(top12-front_t)), front_t-0.01, front_t, 0, z12t);
            }
            // Ausbrech-Saeulen unter dem LD2410C-Federsteg (wie beim LD2450): stuetzen die Bruecke
            // beim Druck, werden danach herausgebrochen
            let (x0 = -l12/2-cl, x1 = l12/2+cl)
                for (i = [1:npil12]) let (xp = x0 + i*(x1-x0)/(npil12+1))
                    box(xp-0.4, xp+0.4, front_t-0.01, front_t+fgap+0.01, z12+w12+cl, z12t);
            if (static_radar == "LD2410C")
                // Auflage nur an den kurzen Seiten: auf der Front sitzen Bauteile entlang der
                // Unterkante, Chip und Antennen in der Mitte (am echten Board geprueft).
                // Die Leisten gehen in die Rahmenrippen ueber.
                for (sx=[-1,1]) box(sx*(l12/2+cl), sx*(l12/2-1.3), front_t, front_t+so12, z12+4.0, z12+w12-1.5);
            else
                // LD2412: Loetpads an den kurzen Seiten -> Auflage an den langen Kanten
                for (sx=[-1,1]) {
                    box(sx*8-1.5, sx*8+1.5, front_t, front_t+so12, z12-cl, z12+1.2);
                    box(sx*8-1.5, sx*8+1.5, front_t, front_t+so12, z12+w12-1.2, z12+w12+cl-fgap);
                }
            for (sx=[-1,1]) {
                ridge_x(sx*6-3, sx*6+3, front_t+so12+t12+ridge_up, z12-cl, 1);
                ridge_x(sx*6-3, sx*6+3, front_t+so12+t12+ridge_up, z12+w12+cl, -1);
            }

            // --- Schraubdome (freie Spalte links) ---
            for (b = bosses) {
                translate([b[0], front_t, b[1]]) ycyl(split_y-front_t, 2.75, 2.75);
                translate([b[0], front_t-0.01, b[1]]) ycyl(1.51, 4.25, 2.75);      // 45-Grad-Fase am Fuss
                // Rippe zur linken Seitenwand, endet 0,3 mm unter der Deckellippe
                box(b[0], -iw/2-0.01, front_t, split_y-1.5-0.3, b[1]-0.75, b[1]+0.75);
            }
                }
            }
        }
        // Schraubloecher M2
        for (b = bosses) translate([b[0], split_y-9, b[1]]) ycyl(9.1, 1.0, 1.0);   // M2 selbstschneidend: Kern 2,0
        // USB-C Ausschnitt (unten, zur Rueckseite offen)
        // Vorderkante 0,3 mm vor der Buchse: haelt das untere ESP-Ende in Richtung Radar
        // breiter als die Buchse: links/rechts sitzen die Fluegel der Deckelnase mit den Keilnasen
        // aussen 45-Grad-Schraege wie die Fluegel (Spiel 0,15)
        translate([0, 0, zo0-1]) linear_extrude(height = 0.01 - zo0 + 1) offset(delta = 0.15) wing_outline(split_y+1);
        // Lueftung oben und unten
        for (x=[-10:4:10]) box(x-0.7, x+0.7, 6, 10.5, ih-0.01, zo1+1);
        // je einer im Boden neben dem Ausschnitt, je zwei in den Seitenwaenden direkt ueber der Eckenrundung
        for (x=[-10,10]) box(x-0.7, x+0.7, 6, 10.5, zo0-1, 0.01);
        for (sx=[-1,1], z=[zo0+R+1.3, zo0+R+3.7]) box(sx*(iw/2-0.01), sx*(W/2+1), 6, 10.5, z-0.7, z+0.7);
    }
}

// ---------- Deckel ----------
module dovetail_profile(extra=0) {   // in xy, Basis bei y=0, Spitze bei y=-dt_h
    polygon([[-(dt_base+extra), 0.01], [dt_base+extra, 0.01],
             [dt_tip+extra, -dt_h-extra], [-(dt_tip+extra), -dt_h-extra]]);
}

module esp_pillars() {
    for (i = [1:npil_esp]) let (xp = eb_x0 + i*(eb_x1-eb_x0)/(npil_esp+1))
        box(xp-0.4, xp+0.4, split_y-eb_gap-0.01, split_y+0.01, esp_top+cl, esp_top+cl+eb_t);
}

module lid() {
    lip_h = 1.5; lip_w = 1.2;
    difference() {
        union() {
            intersection() { body(); box(-W, W, split_y, D+1, zo0-1, zo1+1); }
            // Nase: fuellt den USB-Ausschnitt in der Bodenwand hinter der Buchse (unter der Platine),
            // es bleibt nur die Oeffnung fuer den Stecker
            // ... und ist zugleich die Auflage fuer die ESP-Unterkante (z = 0)
            intersection() { body(); box(-4.65, 4.65, esp_y+0.15, split_y+0.01, zo0-1, 0); }
            // Fluegel links/rechts der Buchse bis an die Vorderkante des Ausschnitts, darauf je eine
            // feste Keilnase vor der Platinenvorderseite (45 Grad, druckbar; wie die festen Radar-Nasen)
            // Fluegel mit 45-Grad-Verstaerkung nach aussen: am Fuss 3,35 statt 1,85 mm breit
            intersection() {
                body();
                difference() {
                    translate([0, 0, zo0-1]) linear_extrude(height = -zo0 + 1) wing_outline(split_y+0.01);
                    box(-4.65, 4.65, esp_y-usb_h-1, wing_y0, zo0-2, 1);   // Platz fuer die Buchse
                }
            }
            // 45-Grad-Kehle am Fuss von Nase und Fluegeln, auf der Platinenseite hinter der ESP-Rueckseite
            // (dort ragt nichts heraus). Endet vor dem 5V-/TX-Pad mit den Drahtenden (ab x ~ 6,8);
            // so hoch wie die Auflage-Schienen (1,5 mm), die Platine liegt auch hier auf
            hull() {
                box(-6.3, 6.3, split_y-1.5, split_y+0.01, -0.01, 0);
                box(-6.3, 6.3, split_y-0.01, split_y+0.01, -0.01, 1.5);
            }
            // Keilnasen auf den Fluegeln: dasselbe Profil wie alle Rastnasen (45 Grad beidseitig, 0,55 hoch),
            // Einfuehrschraege nach vorne; die Halteschraege endet 0,05 vor der Platinenvorderseite
            for (sx=[-1,1]) ridge_x(sx*4.65, sx*(notch_hw-0.15), esp_y-0.55, 0, 1);
            // Teile im Gehaeuse: auf den Umriss der Lippe beschnitten (45-Grad-Stuetzen enden dort)
            intersection() {
                rrect_y(iw-2*lcl, lcl, ih-lcl, max(0.2, ri-lcl), 0, split_y+0.01);
                union() {
            // Lippe (steckt im Gehaeuse)
            difference() {
                rrect_y(iw-2*lcl, lcl, ih-lcl, max(0.2, ri-lcl), split_y-lip_h, split_y+0.01);
                // innen mit 0,8-mm-Fase am Fuss (Verstaerkung)
                hull() {
                    rrect_y(iw-2*lcl-2*lip_w, lcl+lip_w, ih-lcl-lip_w, max(0.5, ri-lcl-lip_w), split_y-lip_h-1, split_y-0.8);
                    rrect_y(iw-2*lcl-2*lip_w-1.6, lcl+lip_w+0.8, ih-lcl-lip_w-0.8, 0.5, split_y-0.01, split_y+1);
                }
                box(-10.5, 10.5, split_y-lip_h-1, split_y+1, -1, 3);        // Platz fuer ESP-Pins unten
                for (b = bosses) translate([b[0], split_y-5, b[1]]) ycyl(6, 3.0, 3.0);
            }
            // ESP-Auflage-Schienen
            // (Fuss beidseitig 1 mm breiter, 45 Grad)
            for (sx=[-1,1]) hull() {
                box(sx*3.5-1, sx*3.5+1, split_y-1.5, split_y+0.01, 2, 20);
                box(sx*3.5-2, sx*3.5+2, split_y-0.01, split_y+0.01, 2, 20);
            }
            // seitliche Fuehrungen und oberer Anschlag
            // Fuehrungen nur so hoch wie noetig (0,5 mm ueber die Platinenvorderseite),
            // aussen durchgehende 45-Grad-Schraege -> breiter Fuss, bricht nicht ab
            for (sx=[-1,1]) hull() {
                box(sx*(esp_w/2+0.3), sx*(esp_w/2+1.8), esp_y-0.5, split_y+0.01, 12, esp_top-1.0);
                box(sx*(esp_w/2+0.3), sx*(esp_w/2+1.8+(split_y-esp_y+0.5)), split_y-0.01, split_y+0.01, 12, esp_top-1.0);
            }
            // Federsteg ueber dem oberen ESP-Ende (wie zwischen den Radar-Taschen): schwebt eb_gap ueber
            // der Deckelinnenseite, haengt nur an zwei Pfosten, biegt in der Schichtebene
            box(eb_x0, eb_x1, eb_ytop, split_y-eb_gap, esp_top+cl, esp_top+cl+eb_t);
            // Pfosten: 2 mm bis zur Lippe, 45-Grad-Stuetzen nach aussen und nach hinten (weg vom ESP)
            for (e = [[eb_x0, -1], [eb_x1, 1]]) hull() {
                box(e[0]-e[1]*0.01, e[0]+e[1]*3, eb_ytop, split_y+0.01, esp_top+cl, esp_top+cl+eb_t+1.0);
                box(e[0]-e[1]*0.01, e[0]+e[1]*(3+split_y-eb_ytop), split_y-0.01, split_y+0.01,
                    esp_top+cl, esp_top+cl+eb_t+1.0+(split_y-eb_ytop));
            }
            // Ausbrech-Saeulen unter dem ESP-Federsteg (volle Stegbreite), danach herausbrechen
            esp_pillars();
            // Rastnase am Steg vor der Platinenvorderseite, 45 Grad beidseitig
            hull() {
                box(rid_x0, rid_x1, esp_y+0.2-1.1, esp_y+0.2, esp_top+cl-0.01, esp_top+cl);
                box(rid_x0, rid_x1, esp_y+0.2-0.6, esp_y+0.2-0.5, esp_top+cl-0.55, esp_top+cl);
            }
            // fester Anschlag hinter dem Steg: begrenzt den Federweg auf 0,6 mm (z. B. beim Einstecken)
            hull() {
                box(rid_x0, rid_x1, eb_ytop, split_y+0.01, esp_top+cl+eb_t+0.6, esp_top+cl+eb_t+1.8);
                box(rid_x0, rid_x1, split_y-0.01, split_y+0.01, esp_top+cl+eb_t+0.6, esp_top+cl+eb_t+1.8+(split_y-eb_ytop));
            }
                }
            }
        }
        // M2 Senkkopf
        for (b = bosses) translate([b[0], split_y-1, b[1]]) {
            ycyl(lid_t+2, 1.3, 1.3);                                   // Durchgang 2,6: Gewinde greift hier nicht
            translate([0, lid_t+1-1.2, 0]) ycyl(1.21, 1.3, 2.2);
            translate([0, lid_t+1, 0]) ycyl(1, 2.2, 2.2);
        }
        // Schwalbenschwanz-Nut (unten offen, oben geschlossen)
        translate([0, D, zo0-1]) linear_extrude(height = dt_top - zo0 + 1) dovetail_profile(dt_cl);
    }
}

// ---------- Schiene fuer Halter ----------
// unteres Ende unter 45 Grad (in Drucklage) abgeschraegt, sonst haengt die Schiene dort frei ueber;
// t = Neigung, mit der die Schiene gedruckt wird (Schrankfuss), die Schraege gleicht sie aus
module rail(z0 = zo0, t = 0) {
    k = tan(45 + t);   // Anstieg der Schraege im Sensor-Koordinatensystem
    intersection() {
        translate([0, D, z0]) linear_extrude(height = dt_top - 0.3 - z0)
            polygon([[-(dt_base-rail_cl), 0.01], [dt_base-rail_cl, 0.01],
                     [dt_tip-rail_cl, -(dt_h-0.3)], [-(dt_tip-rail_cl), -(dt_h-0.3)]]);
        // Prisma in der yz-Ebene, entlang x extrudiert: oberhalb der Linie z = z0 + (D - y) * k
        rotate([90, 0, 90]) linear_extrude(height = 2*dt_tip + 2, center = true)
            polygon([[D + 1, z0 - k], [D + 1, dt_top + 1], [D - dt_h - 1, dt_top + 1], [D - dt_h - 1, z0 + (dt_h + 1)*k]]);
    }
}

// ---------- Eckhalter ----------
cW = W/2 + D - ch + 0.6;   // Wandebene: |x| + y = cW  (Fase des Sensors liegt parallel zur Wand)
// Raumecken sind nie scharf (Putz, Spachtel, Acrylfuge): die Spitze des Keils ist abgeflacht und
// beruehrt die Ecke nicht. Die Flaechen liegen erst ab corner_r von der Ecke an der Wand an,
// dazwischen passt eine Rundung bis Radius corner_r. Grenze: Schraubenbohrung ab ca. 8,5 mm.
corner_r = 6;

// Bohrung entlang lokaler z-Achse mit Tropfenspitze Richtung Welt-+z (nach rotate([-90,0,0]))
module teardrop_bore(h, r) {
    linear_extrude(height = h) hull() {
        circle(r = r);
        translate([0, -r*sqrt(2)]) square(0.01, center = true);
    }
}

module corner() {
    difference() {
        union() {
            translate([0,0,zo0]) linear_extrude(height=H)
                polygon([[-(cW-D), D], [cW-D, D], [corner_r/sqrt(2), cW-corner_r/sqrt(2)],
                         [-corner_r/sqrt(2), cW-corner_r/sqrt(2)]]);
            rail(z_r0);
        }
        // je eine Schraube pro Wand (3-3,5 mm, Kopf bis 7,5 mm), Kopf versenkt, Zugang von vorne.
        // Der Sensor ist schmal: die Schrauben sitzen mittig unter und ueber der Schiene,
        // dort kommt der Schraubendreher frei an. Unten in die rechte, oben in die linke Wand.
        // Tropfenform, damit die liegenden Bohrungen ohne Stuetzen druckbar sind.
        for (s = [[1, (zo0 + z_r0)/2], [-1, (dt_top - 0.3 + zo1)/2]]) {
            sx = s[0];
            to_wall = (cW - D)/sqrt(2);             // Laenge der Achse bis zur Wand
            translate([0, D, s[1]]) rotate([0,0,-sx*45]) rotate([-90,0,0]) {
                translate([0,0,-1]) teardrop_bore(to_wall + 2, 1.9);
                // beginnt 5 mm vor dem Eintrittspunkt: die Bohrung trifft die Front unter 45 Grad,
                // sonst bleibt innen ein Steg stehen, an dem der Schraubenkopf haengt
                translate([0,0,-5]) teardrop_bore(to_wall - 3 + 5, 4.0);   // 3 mm Material unter dem Kopf
            }
        }
    }
}

// ---------- Schrankfuss ----------
// Wird auf den Schrank geklebt, darum ohne Kufen nach vorne. t = Neigung nach unten (Grad).
function ep(t) = elev - zo0*cos(t);
module T(t = tilt) { translate([0,0,ep(t)]) rotate([t,0,0]) children(); }

module stand(t = tilt) {
    y0 = (D + ep(t)*sin(t))/cos(t) + 0.5;
    difference() {
        hull() {
            T(t) box(-15, 15, D, D+8, zo0, dt_top+2);
            box(-20, 20, y0, y0+32, 0, 5);
        }
        // Kabeltunnel: das Kabel laeuft vom Stecker unter dem Sensor nach hinten unter dem Fuss durch
        box(-cable_w/2, cable_w/2, -50, 200, -1, cable_h);
    }
    T(t) rail(zo0, t);
}

// Freiraum fuer USB-C-Stecker und Kabel (muss frei bleiben): Kopf unter der Buchse (Sensor-Koordinaten),
// Kabel auf dem Schrank nach hinten
module usb_keepout(t = tilt) {
    T(t) box(-6.5, 6.5, esp_y-usb_h/2-4, esp_y-usb_h/2+4, zo0-usb_head, zo0-0.01);
    box(-cable_w/2+0.5, cable_w/2-0.5, 0, 200, 0.01, cable_h-0.5);
}

// ---------- Vorschau ----------
module boards() {
    color("seagreen")  ld2450_board();
    color("seagreen")  box(-l12/2, l12/2, front_t+so12, front_t+so12+t12, z12, z12+w12);
    color("white")     ld2450_jst();
    color("royalblue") box(-esp_w/2, esp_w/2, split_y-1.5-esp_t, split_y-1.5, esp_z0, esp_top);
    color("red")       esp_antenna();
    color("black")     ld2450_header();
    color("silver")    esp_usb();
}

// LD2450 in seinen eigenen Koordinaten (u laengs, v quer, siehe pocket50), gedreht platziert
module ld2450_board(e = 0) { place50() box(e, l50-e, front_t+so+e, front_t+so+t50-e, e, w50-e); }
// JST-Stecker (ragt 4 mm ueber das Platinenende)
module ld2450_jst() { place50() box(l50-6, l50+4, front_t+so+t50, front_t+so+t50+4.5, 4, 11); }
// Stiftleiste 2x4 (2,0 mm) auf der LD2450-Rueckseite, am Ende gegenueber der JST-Buchse
module ld2450_header(pin_len = 6) {
    place50() box(2, 6, front_t+so+t50, front_t+so+t50+pin_len, 3.8, 11.9);
}
// Bauteile auf der LD2410C-Vorderseite (vom Foto des echten Boards; Loetloecher oben)
module ld2410c_front_parts() {
    box(-10.3, 9.8, front_t+0.01, front_t+so12, z12+0.3, z12+3.9);   // Bauteilreihe an der Unterkante
    box(-2.1, 2.3, front_t+0.01, front_t+so12, z12+2.7, z12+7.0);    // Chip
}
// USB-C-Buchse (ragt usb_over ueber die Platinenkante)
module esp_usb() { box(-4.5, 4.5, esp_y-usb_h, esp_y, esp_z0-usb_over, esp_z0+7); }
// Keramikantenne am oberen Ende des ESP32-S3 SuperMini (am echten Board gemessen)
module esp_antenna() {
    box(-0.9, 5.7, esp_y-1.2, esp_y, esp_top-2.2, esp_top-0.2);
}

module sensor(explode=0) {
    color("whitesmoke") shell();
    translate([0, explode, 0]) color("gainsboro") lid();
}

if (part == "shell") rotate([90,0,0]) shell();                 // Front nach unten drucken
else if (part == "lid") translate([0,0,D]) rotate([-90,0,0]) lid();   // Rueckseite nach unten
else if (part == "corner") translate([0,0,-zo0]) corner();
else if (part == "stand") stand();
else if (part == "stand_flat") stand(0);
else if (part == "assembly") {
    sensor(); boards();
    translate([0, 0, 0]) color("tan", 0.9) corner();
}
else if (part == "exploded") {
    sensor(18); boards();
    translate([0, 40, 0]) color("tan") corner();
}
else if (part == "assembly_stand") {
    T() { sensor(); boards(); }
    color("tan") stand();
    color("red", 0.4) usb_keepout();
}
else if (part == "assembly_stand_flat") {
    T(0) { sensor(); boards(); }
    color("tan") stand(0);
    color("red", 0.4) usb_keepout(0);
}
else if (part == "chk_stand_usb")      intersection() { union() { stand(); T() sensor(); } usb_keepout(); }    // muss leer sein
else if (part == "chk_stand_flat_usb") intersection() { union() { stand(0); T(0) sensor(); } usb_keepout(0); }  // muss leer sein
else if (part == "section_x") {   // Schnitt bei x = 0 (Seitenansicht)
    intersection() { union() { sensor(); boards(); color("tan") corner(); } box(-100, 0, -50, 100, -50, 100); }
}
else if (part == "section_z") {   // Schnitt auf Hoehe z (Draufsicht)
    intersection() { union() { sensor(); boards(); color("tan") corner(); } box(-100, 100, -50, 100, -50, secz); }
}
else if (part == "open") {        // Gehaeuse ohne Deckel + Platinen
    color("whitesmoke") shell(); boards();
}
else if (part == "lid_inside") { color("gainsboro") lid(); color("royalblue") box(-esp_w/2, esp_w/2, split_y-1.5-esp_t, split_y-1.5, esp_z0, esp_top); }
else if (part == "section_stand") {
    intersection() { union() { T() { sensor(); boards(); } color("tan") stand(); } box(-100, 0, -50, 100, -50, 100); }
}
else if (part == "pretty_corner") {   // nur Optik: Sensor in der Raumecke
    color("white") body();
    color("white") corner();
    color("lightgray") for (sx=[-1,1]) mirror([sx<0?1:0,0,0])
        translate([0,cW,0]) rotate([0,0,-45]) translate([0,0,-40]) cube([90, 2, 120]);
}
else if (part == "pretty_stand") {
    T() color("white") body();
    color("white") stand();
    color("burlywood") translate([-60,-40,-3]) cube([120, 120, 3]);
}
else if (part == "pretty_exploded") {
    color("white") shell(); boards();
    translate([0, 22, 0]) color("gainsboro") lid();
    translate([0, 50, 0]) color("white") corner();
}
else if (part == "m_corner")   mirror([0,1,0]) {
    color("white") body(); color("white") corner();
    color("lightgray") for (sx=[-1,1]) mirror([sx<0?1:0,0,0])
        translate([0,cW,0]) rotate([0,0,-45]) translate([0,0,-40]) cube([90, 2, 120]);
}
else if (part == "m_stand")    mirror([0,1,0]) {
    T() color("white") body(); color("white") stand();
    color("burlywood") translate([-60,-40,-3]) cube([120, 120, 3]);
}
else if (part == "m_stand_flat") mirror([0,1,0]) {
    T(0) color("white") body(); color("white") stand(0);
    color("burlywood") translate([-60,-40,-3]) cube([120, 120, 3]);
}
else if (part == "m_exploded") mirror([0,1,0]) {
    color("white") shell(); boards();
    translate([0, 22, 0]) color("gainsboro") lid();
    translate([0, 48, 0]) color("white") corner();
}
else if (part == "lid_only") color("gainsboro") lid();
else if (part == "section_esp") {  // Schnitt bei x = 0, nur Deckel + ESP + Gehaeuse unten
    intersection() { union() { color("whitesmoke") shell(); color("gainsboro") lid(); boards(); } box(-100, 0, -50, 100, -50, 100); }
}
else if (part == "clash_header")  intersection() { union() { shell(); lid(); } ld2450_header(); }
else if (part == "clash_antenna") intersection() { union() { shell(); lid(); } esp_antenna(); }
else if (part == "corner_section") intersection() { corner(); box(-60, 60, 0, 80, zo0-1, (zo0+zo1)/2); }
else if (part == "clash_boards") intersection() {
    union() { shell(); lid(); }
    union() {   // Platinen mit 0,05 mm Abstand verkleinert, damit reine Auflageflaechen nicht zaehlen
        ld2450_board(0.05);
        box(-l12/2+0.05, l12/2-0.05, front_t+so12+0.05, front_t+so12+t12-0.05, z12+0.05, z12+w12-0.05);
        box(-esp_w/2+0.05, esp_w/2-0.05, esp_y+0.05, esp_y+esp_t-0.05, esp_z0+0.05, esp_top-0.05);
        ld2450_header(); esp_antenna(); esp_usb();
        if (static_radar == "LD2410C") ld2410c_front_parts();
    }
}
else if (part == "section_corner50") intersection() { union() { shell(); boards(); } box(-100, 100, -50, 100, 30, 100); }
else if (part == "lid_esp") { color("gainsboro") lid(); color("royalblue") box(-esp_w/2, esp_w/2, esp_y, esp_y+esp_t, esp_z0, esp_top); color("red") esp_antenna(); color("silver") esp_usb(); }
else if (part == "section_beams") intersection() { shell(); box(-100, secx, -50, 100, -50, 100); }
else if (part == "shell_only") color("whitesmoke") shell();
else if (part == "test_beams")  // Teststueck: Front mit Taschen, Federstegen und Rastnasen, Waende gekuerzt
    rotate([90,0,0]) intersection() { shell(); box(-100, 100, -1, inner_top + 0.4, zo0-1, zo1+1); }
else if (part == "clash_lid_shell") intersection() { shell(); lid(); }
else if (part == "bottom_view") { color("whitesmoke") shell(); color("gainsboro") lid(); color("royalblue") box(-esp_w/2, esp_w/2, esp_y, esp_y+esp_t, esp_z0, esp_top); color("silver") esp_usb(); }
else if (part == "section_arm") intersection() { union() { color("gainsboro") lid(); color("royalblue") box(-esp_w/2, esp_w/2, esp_y, esp_y+esp_t, esp_z0, esp_top); } box(-100, (rid_x0+rid_x1)/2, -50, 100, -50, 100); }
else if (part == "chk_eb_gap") intersection() { lid(); difference() {   // muss leer sein (ausser Saeulen)
    box(eb_x0+0.05, eb_x1-0.05, split_y-eb_gap+0.05, split_y-0.05, esp_top+cl+0.05, esp_top+cl+eb_t-0.05);
    for (i = [1:npil_esp]) let (xp = eb_x0 + i*(eb_x1-eb_x0)/(npil_esp+1)) box(xp-0.45, xp+0.45, 0, 20, 0, 40); } }
else if (part == "chk_eb_free") intersection() { lid(); box(eb_x0+0.05, eb_x1-0.05, eb_ytop+0.05, split_y-0.05, esp_top+cl+eb_t+0.05, esp_top+cl+eb_t+0.55); }  // muss leer sein: Federweg hinter dem Steg
else if (part == "clash_lid_jst") intersection() { lid(); ld2450_jst(); }   // muss leer sein: JST-Stecker
else if (part == "chk_pil_esp") intersection() { lid(); box(eb_x0+0.5, eb_x1-0.5, split_y-eb_gap+0.1, split_y-0.1, esp_top+cl+0.05, esp_top+cl+eb_t-0.05); }   // 3 Saeulen erwartet
else if (part == "chk_pil_12") intersection() { shell(); box(-l12/2, l12/2, front_t+0.1, front_t+fgap-0.1, z12+w12+cl+0.05, z12+w12+cl+fb-0.05); }    // 2 Saeulen erwartet
else if (part == "clash_shell_jst") intersection() { shell(); place50() box(l50+0.05, l50+4, front_t+so+t50, front_t+so+t50+4.5, 4, 11); }   // muss leer sein: JST-Stecker
else if (part == "chk_beam50_free") intersection() { shell(); place50() box(1.6, l50-1.6, front_t+0.05, front_t+fgap-0.05, -cl-fb+0.05, -cl-0.05); }   // nur die 4 Saeulen erwartet
else if (part == "chk_beam50_flex") intersection() { shell(); place50() box(1.6, l50-1.6, front_t+fgap+0.05, inner_top-0.05, -cl-fb-sgap, -cl-fb-0.05); }   // muss leer sein: Federweg neben dem LD2450-Steg
else if (part == "chk_beam12_flex") intersection() { shell(); box(-l12/2+0.05, l12/2-0.05, front_t+fgap+0.05, inner_top-0.05, z12t+0.05, zf0-0.05); }   // muss leer sein: Federweg ueber dem LD2410C-Steg
else if (part == "chk_fence_slot") intersection() { shell(); box(-iw/2+0.05, iw/2-0.05, front_t+0.05, D, zf0+fen_rib+0.05, zf1-fen_rib-0.05); }   // muss leer sein: Schlitz fuer das Blech
else if (part == "front_view") { color("whitesmoke") shell(); boards(); }
