// Kombi-Gehaeuse: HLK-LD2450 + HLK-LD2410C (oder LD2412) + ESP32-S3/C3 SuperMini
// Teile: Gehaeuse (shell), Deckel (lid), Eckhalter (corner), Schrankfuss (stand)
//
// Koordinaten (Sensor): x = rechts, y = Tiefe (0 = Front, +y Richtung Wand), z = oben
//                       z = 0 ist der Innenboden.
//
// Export:  openscad -D 'part="shell"'  -o shell.stl  radar-combo-case.scad
//          part = "shell" | "lid" | "corner" | "stand" | "assembly" | "assembly_stand"

part = "assembly";
secz = 20;
secx = -8;

$fn = 48;

// ---------- Parameter ----------
wall     = 1.6;    // Seitenwaende
front_t  = 0.8;    // Radom (Front) - duenn halten (<= lambda/8 in PLA ~ 0.95 mm)
R        = 4;      // Eckenradius (Frontansicht)
ch       = 1;      // 45-Grad-Fase an Vorder- und Hinterkante
cl       = 0.25;   // Spiel fuer Platinen
ri       = R - wall; // Innenradius der Gehaeuseecken -> ueberall gleich dicke Wand

l50 = 44;  w50 = 15; t50 = 1.2;   // LD2450
jst = 7;                          // Platz fuer den JST-Stecker des LD2450 (0 = Kabel direkt angeloetet)
g50 = 1.5;                        // Abstand LD2450 zur linken Wand, damit die Platinenecke
                                  // an der Eckenrundung (ri) vorbeikommt (mind. ~1,35 bei ri = 2,4)
// Radar fuer stille Personen (untere Tasche): "LD2410C" (22 x 16 mm) oder "LD2412" (28 x 11 mm)
static_radar = "LD2410C";
l12 = static_radar == "LD2412" ? 28  : 22;
w12 = static_radar == "LD2412" ? 11  : 16;
t12 = static_radar == "LD2412" ? 1.0 : 1.2;
so12 = static_radar == "LD2412" ? 1.0 : 1.2;   // Abstand, LD2410C hat Bauteile auf der Front
// Federstege zwischen den Taschen: tragen die inneren Rastnasen, nur an den Enden gehalten
fb   = 0.9;    // Dicke eines Federstegs
fgap = 1.0;    // Luft zwischen Steg und Front (Steg wird als Bruecke gedruckt, darf etwas durchhaengen)
sgap = 0.7;    // Schlitz zwischen den beiden Stegen
so  = 1.0;                        // Abstand Antenne -> Radom-Innenseite

esp_w = 18; esp_l = 23.0; esp_t = 1.0;   // ESP32-S3 SuperMini, Laenge am echten Board gemessen (Datenblatt: 22,52)
usb_over = 1.9;                          // USB-C-Buchse steht so weit ueber die Platinenkante (gemessen)
esp_z0 = 0.2;                            // Unterkante ESP-Platine (Innenboden = 0); Buchse ragt durch den Ausschnitt
usb_h = 3.2;                             // Hoehe USB-C-Buchse ueber der Platine

iw = g50 + l50 + cl + jst;   // Innenbreite
z12 = 1.5;                 // Unterkante untere Radar-Platine
ih = z12 + w12 + 3*cl + 2*fb + sgap + w50;   // Innenhoehe (LD2412: 31, LD2410C: 35.75)
split_y = 11.8;            // Trennebene Gehaeuse / Deckel
esp_y = split_y - 1.5 - esp_t;           // Vorderseite (Bauteilseite) der ESP-Platine
// Rastlippe am oberen ESP-Ende: nur die Haelfte ohne Keramikantenne (S3 SuperMini, von vorne gesehen links)
// Gemessen am echten Board: Antenne bei x = -0,9 ... 5,7, Pad von Pin 8 ab x = -7,6
lip_x0 = -6.0; lip_x1 = -2.4;
esp_top = esp_z0 + esp_l;                // Oberkante ESP-Platine
lid_t   = 4.2;             // Deckel (enthaelt Schwalbenschwanz-Nut)
D  = split_y + lid_t;      // Gesamttiefe
W  = iw + 2*wall;
zo0 = -wall;  zo1 = ih + wall;
H  = zo1 - zo0;

// Radar-Positionen
bx0 = -iw/2 + g50;              // LD2450 linke Kante (Stecker-Seite rechts)
bz0 = ih - cl - w50;            // LD2450 Unterkante

boss_x = 21.5; boss_z = 6;      // M2-Schrauben Deckel

// Schwalbenschwanz (Halter-Schiene / Deckel-Nut)
dt_base = 5;  dt_tip = 7;  dt_h = 3;  dt_cl = 0.25;
dt_top  = ih - 7;               // Nut oben geschlossen -> Sensor liegt auf

// Schrankfuss
tilt = 10;       // Neigung nach unten (Grad)
elev = 16;       // Abstand Sensor-Unterkante -> Schrank (Platz fuer USB-Stecker, gewinkelt empfohlen)

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

// ---------- Gehaeuse ----------
module shell() {
    rib = 1.0;
    top50 = front_t + so + t50 + 0.8;
    top12 = front_t + so12 + t12 + 0.8;
    difference() {
        union() {
            difference() {
                intersection() { body(); box(-W, W, -1, split_y, zo0-1, zo1+1); }
                cavity();
            }
            // --- LD2450 Tasche ---
            // Abstandsleisten an den Enden
            // (bis an Wand, Rippe und Rippenstuecke verlaengert, damit keine 0,25-mm-Spalte bleiben)
            box(-iw/2, bx0+1.5, front_t, front_t+so, bz0-cl, ih);
            // Fuellrippe zwischen linker Wand und Platine: fuehrt die LD2450 seitlich (Spiel cl)
            box(-iw/2, bx0-cl, front_t, top50, bz0-cl-fb, ih);
            box(bx0+l50-1.5, bx0+l50+cl, front_t, front_t+so, bz0-cl, ih);
            // untere Rippe = Federsteg: schwebt fgap ueber der Front, gehalten nur an den
            // Enden (Abstandsleisten) und in der Mitte -> die inneren Rastnasen federn
            box(-iw/2, bx0+l50+cl+rib, front_t+fgap, top50, bz0-cl-fb, bz0-cl);
            box(-iw/2, bx0+1.5, front_t, top50, bz0-cl-fb, bz0-cl);             // linkes Ende
            box(-1, 1, front_t, top50, bz0-cl-fb, bz0-cl);                      // Mittelstuetze
            box(bx0+l50-1.5, bx0+l50+cl+rib, front_t, top50, bz0-cl-fb, bz0-cl); // rechtes Ende
            // Stecker-Seite: nur kurze Stuecke oben/unten
            box(bx0+l50+cl, bx0+l50+cl+rib, front_t, top50, bz0-cl-fb, bz0+3);
            box(bx0+l50+cl, bx0+l50+cl+rib, front_t, top50, ih-3, ih);
            // Rastnasen
            ridge_x(bx0+6, bx0+14, front_t+so+t50+0.35, bz0-cl, 1);
            ridge_x(bx0+30, bx0+38, front_t+so+t50+0.35, bz0-cl, 1);
            ridge_x(bx0+6, bx0+14, front_t+so+t50+0.35, ih, -1);
            ridge_x(bx0+30, bx0+38, front_t+so+t50+0.35, ih, -1);

            // --- Tasche LD2410C / LD2412 ---
            difference() {
                // steht auf dem Boden; obere Rippe = Federsteg (Dicke fb), darueber Schlitz sgap
                box(-l12/2-cl-rib, l12/2+cl+rib, front_t, top12, 0, z12+w12+cl+fb);
                box(-l12/2-cl, l12/2+cl, front_t-1, top12+1, z12-cl, z12+w12+cl);
                // Luft unter dem Steg: nur die Seitenrippen halten ihn
                box(-l12/2-cl, l12/2+cl, front_t-1, front_t+fgap, z12+w12+cl-0.01, z12+w12+cl+fb+0.01);
            }
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
                ridge_x(sx*6-3, sx*6+3, front_t+so12+t12+0.35, z12-cl, 1);
                ridge_x(sx*6-3, sx*6+3, front_t+so12+t12+0.35, z12+w12+cl, -1);
            }

            // --- Schraubdome ---
            for (sx=[-1,1]) translate([sx*boss_x, front_t, boss_z]) ycyl(split_y-front_t, 2.75, 2.75);
        }
        // Schraubloecher M2
        for (sx=[-1,1]) translate([sx*boss_x, split_y-9, boss_z]) ycyl(9.1, 0.85, 0.85);
        // USB-C Ausschnitt (unten, zur Rueckseite offen)
        // Vorderkante 0,3 mm vor der Buchse: haelt das untere ESP-Ende in Richtung Radar
        box(-5.25, 5.25, esp_y-usb_h-0.3, split_y+1, zo0-1, 0.01);
        // Lueftung oben und unten
        for (x=[-22:4:14]) box(x-0.7, x+0.7, 6, 10.5, ih-0.01, zo1+1);
        for (x=[-22,-18,-14,10,14,18]) box(x-0.7, x+0.7, 6, 10.5, zo0-1, 0.01);
    }
}

// ---------- Deckel ----------
module dovetail_profile(extra=0) {   // in xy, Basis bei y=0, Spitze bei y=-dt_h
    polygon([[-(dt_base+extra), 0.01], [dt_base+extra, 0.01],
             [dt_tip+extra, -dt_h-extra], [-(dt_tip+extra), -dt_h-extra]]);
}

module lid() {
    lip_h = 1.5; lip_w = 1.2; lcl = 0.15;
    difference() {
        union() {
            intersection() { body(); box(-W, W, split_y, D+1, zo0-1, zo1+1); }
            // Lippe (steckt im Gehaeuse)
            difference() {
                rrect_y(iw-2*lcl, lcl, ih-lcl, max(0.2, ri-lcl), split_y-lip_h, split_y+0.01);
                rrect_y(iw-2*lcl-2*lip_w, lcl+lip_w, ih-lcl-lip_w, max(0.5, ri-lcl-lip_w), split_y-lip_h-1, split_y+1);
                box(-10.5, 10.5, split_y-lip_h-1, split_y+1, -1, 3);        // Platz fuer ESP-Pins unten
                for (sx=[-1,1]) translate([sx*boss_x, split_y-5, boss_z]) ycyl(6, 3.0, 3.0);
            }
            // ESP-Auflage-Schienen
            for (sx=[-1,1]) box(sx*3.5-1, sx*3.5+1, split_y-1.5, split_y+0.01, 2, 20);
            // seitliche Fuehrungen und oberer Anschlag
            for (sx=[-1,1]) box(sx*(esp_w/2+0.3), sx*(esp_w/2+1.8), split_y-1.5-esp_t-2.5, split_y+0.01, 12, esp_top+1.5);
            // oberer Anschlag mit Rastlippe: haelt das obere ESP-Ende in Richtung Radar
            box(-6, 6, esp_y-1.5, split_y+0.01, esp_top+0.3, esp_top+1.8);
            hull() {
                box(lip_x0, lip_x1, esp_y-1.5, esp_y-0.05, esp_top+0.3, esp_top+0.31);
                box(lip_x0, lip_x1, esp_y-0.8, esp_y-0.05, esp_top-1.0, esp_top+0.31);
            }
        }
        // M2 Senkkopf
        for (sx=[-1,1]) translate([sx*boss_x, split_y-1, boss_z]) {
            ycyl(lid_t+2, 1.1, 1.1);
            translate([0, lid_t+1-1.2, 0]) ycyl(1.21, 1.1, 2.2);
            translate([0, lid_t+1, 0]) ycyl(1, 2.2, 2.2);
        }
        // Schwalbenschwanz-Nut (unten offen, oben geschlossen)
        translate([0, D, zo0-1]) linear_extrude(height = dt_top - zo0 + 1) dovetail_profile(dt_cl);
    }
}

// ---------- Schiene fuer Halter ----------
module rail() {
    translate([0, D, zo0]) linear_extrude(height = dt_top - 0.3 - zo0)
        polygon([[-dt_base, 0.01], [dt_base, 0.01], [dt_tip, -(dt_h-0.3)], [-dt_tip, -(dt_h-0.3)]]);
}

// ---------- Eckhalter ----------
cW = W/2 + D - ch + 0.6;   // Wandebene: |x| + y = cW  (Fase des Sensors liegt parallel zur Wand)

// Bohrung entlang lokaler z-Achse mit Tropfenspitze Richtung Welt-+z (nach rotate([-90,0,0]))
module teardrop_bore(h, r) {
    linear_extrude(height = h) hull() {
        circle(r = r);
        translate([0, -r*sqrt(2)]) square(0.01, center = true);
    }
}

module corner() {
    hz = (zo0 + zo1) / 2;
    difference() {
        union() {
            translate([0,0,zo0]) linear_extrude(height=H)
                polygon([[-(cW-D), D], [cW-D, D], [0, cW]]);
            rail();
        }
        // je eine Schraube pro Wand (3-3,5 mm, Kopf bis 7,5 mm), Kopf versenkt, Zugang von vorne.
        // Eintritt bei x = +-15, damit der Schraubendreher an der Schiene vorbeikommt.
        // Tropfenform, damit die liegenden Bohrungen ohne Stuetzen druckbar sind.
        for (sx=[-1,1]) {
            xe = sx*15;
            to_wall = (cW - D - 15)/2*sqrt(2);      // Laenge der Achse bis zur Wand
            translate([xe, D, hz]) rotate([0,0,-sx*45]) rotate([-90,0,0]) {
                translate([0,0,-1]) teardrop_bore(to_wall + 2, 1.9);
                // beginnt 5 mm vor dem Eintrittspunkt: die Bohrung trifft die Front unter 45 Grad,
                // sonst bleibt innen ein Steg stehen, an dem der Schraubenkopf haengt
                translate([0,0,-5]) teardrop_bore(to_wall - 3 + 5, 4.0);   // 3 mm Material unter dem Kopf
            }
        }
    }
}

// ---------- Schrankfuss ----------
ep = elev - zo0*cos(tilt);
module T() { translate([0,0,ep]) rotate([tilt,0,0]) children(); }

module stand() {
    y0 = (D + ep*sin(tilt))/cos(tilt) + 0.5;
    difference() {
        hull() {
            T() box(-15, 15, D, D+8, zo0, dt_top+2);
            box(-20, 20, y0, y0+32, 0, 5);
        }
        box(-3.5, 3.5, -50, 200, -1, 4);   // Kabelkanal unten
    }
    T() rail();
}

// ---------- Vorschau ----------
module boards() {
    color("seagreen")  box(bx0, bx0+l50, front_t+so, front_t+so+t50, bz0, bz0+w50);
    color("seagreen")  box(-l12/2, l12/2, front_t+so12, front_t+so12+t12, z12, z12+w12);
    color("white")     box(bx0+l50-6, bx0+l50+4, front_t+so+t50, front_t+so+t50+4.5, bz0+4, bz0+11);  // JST
    color("royalblue") box(-esp_w/2, esp_w/2, split_y-1.5-esp_t, split_y-1.5, esp_z0, esp_top);
    color("red")       esp_antenna();
    color("black")     ld2450_header();
    color("silver")    esp_usb();
}

// Stiftleiste 2x4 (2,0 mm) auf der LD2450-Rueckseite, am Ende gegenueber der JST-Buchse
module ld2450_header(pin_len = 6) {
    box(bx0+2, bx0+6, front_t+so+t50, front_t+so+t50+pin_len, bz0+3.8, bz0+11.9);
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
}
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
        box(bx0+0.05, bx0+l50-0.05, front_t+so+0.05, front_t+so+t50-0.05, bz0+0.05, bz0+w50-0.05);
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
    rotate([90,0,0]) intersection() { shell(); box(-100, 100, -1, front_t + so + t50 + 1.2, zo0-1, zo1+1); }
