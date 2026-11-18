"""Prüfstand: schnelle, live-getreue Bewertung eines Kerns auf den Wahrheitsfenstern (NACHTLAUF.md "Der Prüfstand").

usage:
  python3 -I tools/pruefstand.py schnell BIN [--name NAME] [Optionen]   Schnelltest (<= 5 min)
  python3 -I tools/pruefstand.py voll BIN [--name NAME] [Optionen]      alle Fenster (<= 30 min)
  python3 -I tools/pruefstand.py regel schnell|voll [--name NAME]       die einfache Regel (Referenz)
  python3 -I tools/pruefstand.py gegenprobe BIN [--name NAME]          Echtzeit-Gegenprobe der kritischen Fenster:
                       Thread-CPU statt Instruktionen, höchstens 8 Plätze, nur auf ruhiger Maschine (Last < 4)
  python3 -I tools/pruefstand.py zeige LAUF [LAUF...]                    Zusammenfassung gespeicherter Läufe
Optionen:
  --fenster a,b,c      nur diese Fenster (Namen aus fenster.json)
  --set k=v            Konfigurationsparameter überschreiben (z. B. light_cost=2.0, Wirkungsprobe der Schwellen)
  --tick S             Takt der Schritte (Vorgabe 0.1 s wie die App; evalp: 0.2)
  --evalp-start        mit evalps erstem Start statt dem kurzen Vorlauf (Eichung gegen evalp-Läufe)
  --faktor F           CPU-Faktor Inge/HERMANN für den simulierten Rückstand (Vorgabe referenzen/faktor.json)
  --ips-leer N         Instruktionen je CPU-Sekunde des Kerns auf der leeren Maschine (Vorgabe faktor.json): die
                       Kosten zählen als Instruktionen (perf), unabhängig von der Last; 0: Thread-CPU-Zeit
  --cpu S              CPU-Grenze je Fenster in s (Vorgabe schnell 600, voll 1500)
  --mit-064 BIN        0.64 (BIN) im selben Lauf auf denselben Fenstern mitlaufen lassen: ips_ist unter derselben
                       Last (sonst gegen die gespeicherte Referenz und das Mittel des Laufs); bei weniger als 5
                       Fenstern automatisch mit referenzen/bin/0.64 (abschalten: --ohne-064)
  --aus DIR            Ergebnisordner (Vorgabe ~/checkout/pt-nacht/laeufe/NAME)
  -v                   Fortschrittszeilen der Läufe mit ausgeben

BIN ist die presence-replay-Binärdatei des Kandidaten (mit dem Unterbefehl bench, nacht/pruefstand). Jedes Fenster
läuft als eigener Prozess über ~/checkout/pt-nacht/werkzeuge/slot.sh (globaler Zähler, nice); jede Zeile kommt
sofort.

Echtzeit ist eine geeichte Schätzung, kein Messwert von Inge: Faktor 3,0 ist auf den 17.11. (0.65) und 11.11. (0.64)
gefittet und nur zwischen 2,0 und 3,9 belegt (referenzen/faktor.json). Je Fenster:
- durchgefallen: Rückstand nach Instruktionen über 60 s, oder nach Thread-CPU über 60 s selbst lastbereinigt (/2,3,
  untere Schranke: fängt mehr Takte je Instruktion, z. B. Cache-Fehlzugriffe), oder CPU-Grenze;
- unsicher: ips_ist (Instruktionen je Thread-CPU-Sekunde) liegt über 15 % unter 0.64 im selben Fenster (und unter
  dem Mittel des Laufs), die Instruktionen unterschätzen also die Kosten, und der Rückstand nach Thread-CPU (obere
  Schranke) übersteigt 60 s -> Gegenprobe;
- Grenzfall: Rückstand über 25 s, oder über 60 s am oberen Ende der Faktorspanne (bei zu niedrigem ips_ist: nach
  Thread-CPU) -> nicht bestanden, Gegenprobe;
- sonst ok.
Die Gegenprobe (Unterbefehl gegenprobe) misst die kritischen Fenster direkt in Thread-CPU auf ruhiger Maschine. Ergebnis: je Fenster JSON (und .log mit dem Fortschritt), zusammenfassung.json, Vergleich mit den
gespeicherten Referenzen (~/checkout/pt-nacht/referenzen/{0.64,0.65,regel}/{schnell,voll}) und das Freigabe-Tor
(NACHTLAUF.md Regel 9) gegen 0.64. Die Wahrheit liegt außerhalb des Repos (~/.config/presence-tracker/dev/
fenster.json, erzeugt von tools/pruefstand_fenster.py). Ein Fenster, dessen simulierter Rückstand auf Inge 60 s
übersteigt oder das die CPU-Grenze reißt, wird abgebrochen und gilt als durchgefallen.
"""
import json, math, os, subprocess, sys, tempfile, threading, time

HOME = os.path.expanduser("~")
FENSTER = os.path.join(HOME, ".config/presence-tracker/dev/fenster.json")
NACHT = os.path.join(HOME, "checkout/pt-nacht")
SLOT = os.path.join(NACHT, "werkzeuge/slot.sh")
REF = os.path.join(NACHT, "referenzen")
LAEUFE = os.path.join(NACHT, "laeufe")
BIN064 = os.path.join(REF, "bin/0.64/presence-replay")  # 0.64 mit bench (nacht/ref064), für --mit-064
# CPU-Faktor Inge/HERMANN: Inge-Sekunden je Leerlauf-CPU-Sekunde des Kerns hier (Kosten als Instruktionen / ips_leer,
# unabhängig von der Last), geeicht gegen den gemessenen Lag der App (17.11. 0.65: über 60 s ab 16:41; 11.11. 0.64:
# nie über 2,7 s) -> 3,0; Herleitung in referenzen/faktor.json
FAKTOR_DATEI = os.path.join(REF, "faktor.json")
FAKTOR_VORGABE = 3.0
MAX_RUECKSTAND = 60.0
GRENZFALL_S = 25.0  # faktor.json grenzfall_s
IPS_ABWEICHUNG = 0.15  # faktor.json ips_abweichung
GEGENPROBE_SLOTS = 8
GEGENPROBE_LAST = 4.0  # 1-min-Last, über der die Maschine nicht ruhig ist
# Freigabe-Tor "kein Wachstum" je Fenster: Kandidat höchstens max(0.64 im selben Fenster + Spielraum, Boden);
# Spielraum max(3, 50 %) gegen Startbedingungen und Zufall der Reihenfolge
WACHSTUM_BODEN = 6
WACHSTUM_SPIEL_ABS, WACHSTUM_SPIEL_REL = 3, 0.5
NOT_WINDOWS = ("zusammenfassung.json", "lauf.json")
# Schnelltest: die Fenster in fenster.json meta.schnell (privat: die kritischen Situationen, kurz, verschiedenartig)


def faktor(key="faktor", default=FAKTOR_VORGABE):
    try:
        v = json.load(open(FAKTOR_DATEI))[key]
        return [float(x) for x in v] if isinstance(v, list) else float(v)
    except (OSError, KeyError, ValueError, TypeError):
        return default


def opt(args, k, default=None):
    if k in args:
        i = args.index(k)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def multi(args, k):
    out = []
    while k in args:
        out.append(opt(args, k))
    return out


def parse_value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


# ------------------------------------------------------------------ Läufe

def job_for(name, w, args, tmp):
    cfg = w["config"]
    if args["set"]:
        c = json.load(open(cfg))
        for kv in args["set"]:
            k, v = kv.split("=", 1)
            c["params"][k] = parse_value(v)
        cfg = os.path.join(tmp, f"cfg_{name}.json")
        json.dump(c, open(cfg, "w"))
    start = w["evalp_start"] if args["evalp_start"] and w.get("evalp_start") else w["start"]
    lt = time.localtime(w["m0"])
    job = {"name": name, "kind": w["art"], "config": cfg, "learned": w["learned"], "files": w["files"],
           "start": start, "m0": w["m0"], "m1": w["m1"], "must": w["must"], "verbot": w["verbot"],
           "hoechstens_eins": w["hoechstens_eins"], "skip": w["skip"], "tick": args["tick"],
           "factor": args["faktor"], "faktor_spanne": args["spanne"], "last_max": args["last_max"],
           "max_backlog": MAX_RUECKSTAND, "cpu_limit": args["cpu"], "progress": 60, "tz": lt.tm_gmtoff}
    if args["ips"]:
        job["ips_leer"] = args["ips"]
    if args["regel"] is not None:
        job["regel"] = args["regel"]
        job["regel_raeume"] = args["beobachtet"]
    path = os.path.join(tmp, f"job_{name}.json")
    json.dump(job, open(path, "w"))
    return path, (w["m1"] - start)


def run_one(binary, name, job, out, verbose, lock, results, ref, env=None, quiet=False):
    log = open(os.path.join(out, f"{name}.log"), "w")
    t0 = time.time()
    cmd = [SLOT, binary, "bench", job]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
    last = None
    for line in p.stdout:
        log.write(line)
        log.flush()
        if line.startswith("#"):
            if verbose:
                with lock:
                    print("  " + line.rstrip(), flush=True)
        elif line.strip():
            last = line
    err = p.stderr.read()
    p.wait()
    log.write(err)
    log.close()
    if p.returncode != 0 or last is None:
        r = {"fenster": name, "status": "fehler", "grund": err.strip()[-400:] or f"Rückgabe {p.returncode}"}
    else:
        r = json.loads(last)
    r["wand_s"] = time.time() - t0
    e = echtzeit_urteil(r, ref.get(name))
    if e:
        r["echtzeit"] = e
    json.dump(r, open(os.path.join(out, f"{name}.json"), "w"), indent=1)
    with lock:
        results[name] = r
        if not quiet:
            print(line_of(r, ref.get(name)), flush=True)


def key_metric(r):
    """Die Hauptgröße eines Fensters je Art (Text)."""
    if r.get("status") == "fehler":
        return "FEHLER " + r.get("grund", "")[:120]
    art = r.get("art")
    parts = []
    if art == "leer":
        parts.append(f"Licht {r['licht']:.1f}")
    elif art == "ein":
        parts.append(f"zuviel {r['licht_zuviel']:.1f} Sitzer-dunkel {r['dunkel_sitzer']:.1f}")
    elif art == "zwei":
        parts.append(f"zuviel {r['licht_zuviel']:.1f}")
    elif art == "nacht":
        parts.append(f"Schlaf dunkel {r['schlaf_dunkel']:.1f} (unbelegt {r['schlaf_unbelegt']:.1f})")
    if r.get("muss_min"):
        parts.append(f"Pflicht dunkel {r['muss_dunkel']:.1f}/{r['muss_min']:.1f} Lücke {r['muss_luecke_max_s']:.0f}s")
    if r.get("verbot_min"):
        parts.append(f"Verbot hell {r['verbot_licht']:.1f}/{r['verbot_min']:.1f}")
    if r.get("zwei_p") is not None:
        parts.append(f"P(>=2) {r['zwei_p']:.2f}")
    return " ".join(parts)


def line_of(r, ref=None):
    name = r.get("fenster", "?")
    if r.get("status") == "fehler":
        return f"{name:30} FEHLER {r.get('grund', '')[:300]}"
    st = "OK " if r["status"] == "ok" else "DURCHGEFALLEN"
    load = ""
    if r.get("modus") == "modell":
        sp = r.get("rueckstand_spanne_max_s") or [r["rueckstand_max_s"]] * 2
        ips = f" ips {r['ips_ist'] / 1e9:.1f}e9" if r.get("ips_ist") else ""
        load = (f" | hyps {r['max_hyps']} ohne Spur {r['max_ohne_spur']} (Ende {r.get('ende_ohne_spur', '-')})"
                f" dichten {r['max_dichten']}"
                f" | CPU {r['cpu_ms_je_datenmin']:.0f} ms/min (max {r['cpu_ms_je_datenmin_max']:.0f}){ips}"
                f" | Echtzeit-Schätzung: Rückstand {r['rueckstand_max_s']:.1f} s (F 2,0-3,9: {sp[0]:.1f}-{sp[1]:.1f};"
                f" Thread-CPU {r.get('rueckstand_cpu_max_s', r['rueckstand_max_s']):.1f})")
    s = f"{name:30} {st} {key_metric(r)}{load} | {r['daten_min']:.0f} min Daten, {r.get('wand_s', 0):.0f} s"
    if r["status"] != "ok":
        s += f"\n{'':30}   -> {r['grund']}"
    elif r.get("echtzeit", {}).get("urteil", "ok") != "ok":
        s += f"\n{'':30}   -> Echtzeit {r['echtzeit']['urteil'].upper()}: {r['echtzeit']['grund']}"
    if ref and ref.get("status") in ("ok", "durchgefallen"):
        s += f"\n{'':30}   0.64: {key_metric(ref)}"
    return s


def echtzeit_urteil(r, ref, q_lauf=None):
    """Das Echtzeit-Urteil eines Fensters (geeichte Schätzung, siehe oben): ok, grenzfall, unsicher oder
    durchgefallen, mit Grund. ref: 0.64 im selben Fenster (im selben Lauf oder die gespeicherte Referenz); q_lauf:
    Median von ips_ist/ips_ist(0.64) über die Fenster dieses Laufs (die Last des Laufs)."""
    if r.get("status") == "fehler" or r.get("modus") != "modell":
        return None
    if r["status"] != "ok":
        return {"urteil": "durchgefallen", "grund": r.get("grund") or "", "q": None}
    q = None
    if r.get("ips_ist") and ref and ref.get("ips_ist"):
        q = r["ips_ist"] / ref["ips_ist"]
    # zu wenige Instruktionen je Takt gegenüber 0.64: absolut oder gegenüber den anderen Fenstern desselben Laufs
    qs = [x for x in (q, q / q_lauf if q is not None and q_lauf else None) if x is not None]
    mins = minuten_q(r, ref, q_lauf)
    ips_niedrig = (bool(qs) and min(qs) < 1.0 - IPS_ABWEICHUNG) or bool(mins)
    sp = r.get("rueckstand_spanne_max_s") or [r["rueckstand_max_s"]] * 2
    instr, oben = r["rueckstand_max_s"], sp[1]
    cpu = r.get("rueckstand_cpu_max_s", instr)
    qtxt = f"ips_ist {q:.2f} von 0.64" if q is not None else "ips_ist ohne 0.64-Vergleich"
    if mins:
        tz = time.localtime(mins[0][0] * 60).tm_gmtoff
        qtxt += (f", {len(mins)} Minuten unter {1 - IPS_ABWEICHUNG:.2f} (ab "
                 f"{time.strftime('%H:%M', time.gmtime(mins[0][0] * 60 + tz))}, kleinstes {min(x for _, x in mins):.2f})")
    if ips_niedrig and cpu > MAX_RUECKSTAND:
        return {"urteil": "unsicher", "q": q, "grund": f"{qtxt} (Lauf {format(q_lauf, ".2f") if q_lauf else "-"}): Instruktionen unterschätzen; "
                f"Rückstand nach Thread-CPU {cpu:.0f} s > {MAX_RUECKSTAND:.0f} s (obere Schranke, Last), nach "
                f"Instruktionen {instr:.1f} s -> Gegenprobe"}
    wert = max(instr, cpu) if ips_niedrig else instr
    if wert > GRENZFALL_S or oben > MAX_RUECKSTAND:
        return {"urteil": "grenzfall", "q": q, "grund": f"Rückstand {wert:.1f} s{' (Thread-CPU, ' + qtxt + ')' if ips_niedrig else ''}"
                f" > {GRENZFALL_S:.0f} s oder bei F 3,9 {oben:.1f} s > {MAX_RUECKSTAND:.0f} s: geeichte Schätzung, "
                f"nicht bestanden -> Gegenprobe"}
    return {"urteil": "ok", "q": q, "grund": qtxt}


MINUTE_RELEVANT_S = 1.0  # Thread-CPU je Minute, ab der eine Minute für den Vergleich der ips zählt


def minuten_q(r, ref, q_lauf=None):
    """Minuten (Zeit, q) mit zu wenigen Instruktionen je Thread-CPU-Sekunde gegenüber 0.64 auf denselben Frames:
    q = ips(Kandidat) / ips(0.64) in derselben Minute, geteilt durch das Last-Mittel des Laufs; nur Minuten, in denen
    der Kandidat mindestens MINUTE_RELEVANT_S Thread-CPU braucht (sonst zählt die Minute für die Echtzeit nicht).
    Eine örtliche Verlangsamung (wenige Minuten) verschwindet im Mittel des Fensters, hier nicht."""
    if not r.get("minuten") or not ref or not ref.get("minuten"):
        return []
    b = {m: (i, c) for m, i, c in ref["minuten"]}
    out = []
    for m, i, c in r["minuten"]:
        if c < MINUTE_RELEVANT_S or m not in b or i <= 0:
            continue
        bi, bc = b[m]
        if bi <= 0 or bc <= 0:
            continue
        q = (i / c) / (bi / bc) / (q_lauf or 1.0)
        if q < 1.0 - IPS_ABWEICHUNG:
            out.append((m, q))
    return out


def lauf_q(results, refs):
    qs = sorted(r["ips_ist"] / refs[n]["ips_ist"] for n, r in results.items()
                if r.get("ips_ist") and refs.get(n, {}).get("ips_ist") and r.get("status") == "ok")
    return qs[len(qs) // 2] if len(qs) >= 5 else None


def load_ref(which, test):
    d = os.path.join(REF, which, test)
    out = {}
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json") and f not in NOT_WINDOWS:
                r = json.load(open(os.path.join(d, f)))
                out[r.get("fenster", f[:-5])] = r
    return out


# ------------------------------------------------------------------ Zusammenfassung und Tor

def summarize(results):
    s = {"fenster": len(results), "durchgefallen": sorted(n for n, r in results.items() if r.get("status") != "ok"),
         "leer_licht": 0.0, "leer_licht_max": 0.0, "ein_zuviel": 0.0, "ein_sitzer_dunkel": 0.0, "zwei_zuviel": 0.0,
         "muss_dunkel": 0.0, "muss_luecke_max_s": 0.0, "muss_min": 0.0, "nacht_schlaf_dunkel": 0.0,
         "nacht_schlaf_unbelegt": 0.0, "verbot_licht": 0.0, "zwei_p_max": None, "max_hyps": 0, "max_ohne_spur": 0,
         "max_dichten": 0, "cpu_modell_s": 0.0, "cpu_ms_je_datenmin_max": 0.0, "rueckstand_max_s": 0.0,
         "daten_min": 0.0, "echtzeit_grenzfall": [], "echtzeit_unsicher": []}
    for n, r in results.items():
        if r.get("status") == "fehler":
            continue
        art = r["art"]
        if art == "leer":
            s["leer_licht"] += r["licht"]
            s["leer_licht_max"] = max(s["leer_licht_max"], r["licht"])
        elif art == "ein":
            s["ein_zuviel"] += r["licht_zuviel"]
            s["ein_sitzer_dunkel"] += r["dunkel_sitzer"]
        elif art == "zwei":
            s["zwei_zuviel"] += r["licht_zuviel"]
        elif art == "nacht":
            s["nacht_schlaf_dunkel"] += r["schlaf_dunkel"]
            s["nacht_schlaf_unbelegt"] += r["schlaf_unbelegt"]
        s["muss_dunkel"] += r["muss_dunkel"]
        s["muss_min"] += r["muss_min"]
        s["muss_luecke_max_s"] = max(s["muss_luecke_max_s"], r["muss_luecke_max_s"])
        s["verbot_licht"] += r["verbot_licht"]
        if r.get("zwei_p") is not None:
            s["zwei_p_max"] = max(s["zwei_p_max"] or 0.0, r["zwei_p"])
        s["daten_min"] += r["daten_min"]
        if r.get("modus") == "modell":
            for k in ("max_hyps", "max_ohne_spur", "max_dichten"):
                s[k] = max(s[k], r[k])
            s["cpu_modell_s"] += r["cpu_modell_s"]
            s["cpu_ms_je_datenmin_max"] = max(s["cpu_ms_je_datenmin_max"], r["cpu_ms_je_datenmin_max"])
            s["rueckstand_max_s"] = max(s["rueckstand_max_s"], r["rueckstand_max_s"])
            u = (r.get("echtzeit") or {}).get("urteil")
            if u in ("grenzfall", "unsicher"):
                s["echtzeit_" + u].append(n)
    s["echtzeit_grenzfall"].sort()
    s["echtzeit_unsicher"].sort()
    s["cpu_ms_je_datenmin"] = 1000 * s["cpu_modell_s"] / max(s["daten_min"], 1e-9)
    return s


ROWS = [("leer_licht", "Licht im leeren Haus (Raummin)"), ("leer_licht_max", "  schlechtestes Fenster"),
        ("nacht_schlaf_dunkel", "Schlafzimmer nachts dunkel (min)"), ("muss_dunkel", "Pflichtraum dunkel (min)"),
        ("muss_luecke_max_s", "  längste dunkle Lücke (s)"), ("ein_zuviel", "Licht zu viel, eine Person (Raummin)"),
        ("zwei_zuviel", "Licht zu viel, zwei Personen (Raummin)"), ("ein_sitzer_dunkel", "dunkel mit Sitzer, eine Person (min)"),
        ("verbot_licht", "Meldungen: Licht, wo niemand war (min)"), ("zwei_p_max", "Meldungen: P(>=2) höchstens"),
        ("max_hyps", "höchste Zahl Hypothesen"), ("max_ohne_spur", "höchste Zahl Personen ohne Spur"),
        ("max_dichten", "höchste Zahl Dichten"), ("cpu_ms_je_datenmin", "CPU je Datenminute (ms, Mittel)"),
        ("cpu_ms_je_datenmin_max", "CPU je Datenminute (ms, schlechteste)"),
        ("rueckstand_max_s", "Rückstand Inge geschätzt (s, max; F 3,0)"),
        ("echtzeit_grenzfall", "  Echtzeit-Grenzfälle (Fenster)"), ("echtzeit_unsicher", "  Echtzeit unsicher (Fenster)")]


def fmtv(v):
    if v is None:
        return "-"
    if isinstance(v, list):
        return str(len(v))
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def table(cols):
    """cols: [(Titel, Zusammenfassung)]"""
    out = [f"{'':42}" + "".join(f"{t:>14}" for t, _ in cols)]
    for k, label in ROWS:
        out.append(f"{label:42}" + "".join(f"{fmtv(s.get(k)) if s else '-':>14}" for _, s in cols))
    return "\n".join(out)


def wachstum_grenze(ref_wert):
    return max(ref_wert + max(WACHSTUM_SPIEL_ABS, math.ceil(WACHSTUM_SPIEL_REL * ref_wert)), WACHSTUM_BODEN)


def wachstum(r, b):
    """Gründe, aus denen ein Fenster gegenüber 0.64 im selben Fenster wächst (leer: keiner). Nur der bewertete Teil
    (max_*, ohne den kalten Vorlauf anlauf_*), dazu die letzten 10 min (ende_*) und der Zuwachs vom Anfang
    (anfang_*, erste 10 min) zum Ende."""
    out = []
    if r.get("modus") != "modell" or b.get("modus") != "modell":
        return out
    for k, lab in (("max_ohne_spur", "ohne Spur"), ("ende_ohne_spur", "ohne Spur am Ende")):
        if k in r and k in b and r[k] > wachstum_grenze(b[k]):
            out.append(f"{lab} {r[k]} > {wachstum_grenze(b[k])} (0.64: {b[k]})")
    if all(k in x for k in ("anfang_ohne_spur", "ende_ohne_spur") for x in (r, b)):
        zr = r["ende_ohne_spur"] - r["anfang_ohne_spur"]
        zb = max(b["ende_ohne_spur"] - b["anfang_ohne_spur"], 0)
        if zr > zb + WACHSTUM_SPIEL_ABS:
            out.append(f"ohne Spur wächst im Fenster um {zr} (0.64: {zb})")
    if r["max_hyps"] > max(2 * b["max_hyps"], 16):
        out.append(f"Hypothesen {r['max_hyps']} > {max(2 * b['max_hyps'], 16)}")
    return out


def gate(cand, base, results, base_results):
    """Freigabe-Tor (NACHTLAUF.md Regel 9) gegen 0.64 auf denselben Fenstern: durchgefallen darf nur, was auch 0.64
    nicht schafft; Wachstum je Fenster gegen 0.64 im selben Fenster; Echtzeit: ja nur ohne Grenzfall und ohne
    unsicheres Fenster (sonst GRENZFALL/UNSICHER: Gegenprobe); Erkennung und Licht auf den Fenstern, die beide ganz
    durchlaufen haben. Gibt (Tor, gemeinsame Fenster, Einzelheiten) zurück."""
    common = sorted(set(results) & set(base_results))
    neu = [n for n in common if results[n].get("status") != "ok" and base_results[n].get("status") == "ok"]
    both = [n for n in common if results[n].get("status") == "ok" and base_results[n].get("status") == "ok"]
    c = summarize({n: results[n] for n in both})
    b = summarize({n: base_results[n] for n in both})
    eps = 0.05
    wachs = {n: w for n in common if results[n].get("status") != "fehler" and base_results[n].get("status") != "fehler"
             and (w := wachstum(results[n], base_results[n]))}
    urteile = {n: (results[n].get("echtzeit") or {}).get("urteil") for n in common}
    grenz = sorted(n for n, u in urteile.items() if u == "grenzfall")
    unsicher = sorted(n for n, u in urteile.items() if u == "unsicher")
    if neu:
        echt = False
    elif unsicher:
        echt = "UNSICHER"
    elif grenz:
        echt = "GRENZFALL"
    else:
        echt = True
    tor = {
        "kein_durchgefallen": not neu,
        "kein_wachstum": not wachs,
        "echtzeit": echt,
        "leeres_haus": c["leer_licht"] <= b["leer_licht"] + eps,
        "nachts": c["nacht_schlaf_dunkel"] <= b["nacht_schlaf_dunkel"] + eps,
        "pflichtfenster": c["muss_dunkel"] <= b["muss_dunkel"] + eps,
        "licht_zu_viel": c["ein_zuviel"] <= b["ein_zuviel"] + eps,
    }
    info = {"neu_durchgefallen": neu, "wachstum": wachs, "echtzeit_grenzfall": grenz, "echtzeit_unsicher": unsicher}
    return tor, common, info


def gegenprobe_urteil(results, base_results):
    """Die Gegenprobe gegen 0.64 in derselben Gegenprobe (Thread-CPU, ruhige Maschine): bestanden, wenn kein Fenster
    durchfällt, das 0.64 schafft, und der Rückstand je Fenster höchstens max(25 s, 0.64 * 1,1 + 2 s) beträgt (0.64
    selbst kommt hier in einem Fenster auf über 50 s; live waren es höchstens 2,7 s: der Faktor ist eine Schätzung)."""
    zeilen, ok = [], True
    for n in sorted(results):
        r, b = results[n], base_results.get(n)
        if r.get("status") == "fehler" or not b or b.get("status") == "fehler":
            zeilen.append(f"  {n:28} ohne Vergleich")
            ok = False
            continue
        grenze = max(GRENZFALL_S, 1.1 * b["rueckstand_max_s"] + 2.0)
        gut = (r["status"] == "ok" or b["status"] != "ok") and r["rueckstand_max_s"] <= grenze
        ok &= gut
        zeilen.append(f"  {n:28} {'ja  ' if gut else 'NEIN'} Rückstand {r['rueckstand_max_s']:5.1f} s ({r['status']}), "
                      f"0.64 {b['rueckstand_max_s']:5.1f} s ({b['status']}), Grenze {grenze:.1f} s")
    return ok, zeilen


def torwert(v):
    return "ja" if v is True else "NEIN" if v is False else v


def gleiche_bedingungen(args, ref_dir):
    """Ob der Lauf unter denselben Bedingungen läuft wie die Referenz (sonst ist das Tor nicht anwendbar: andere
    Startzeit oder anderer Takt ändern den Zustand und damit Wachstum und Licht)."""
    try:
        ref = json.load(open(os.path.join(ref_dir, "lauf.json")))
    except OSError:
        return ["keine lauf.json der Referenz"]
    diff = []
    for k in ("tick", "evalp_start", "set", "faktor"):
        if ref.get(k, args.get(k)) != args.get(k):
            diff.append(f"{k} {args.get(k)} statt {ref.get(k)}")
    return diff


# ------------------------------------------------------------------ main

def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(2)
    cmd = argv.pop(0)
    if cmd == "zeige":
        cols = []
        for d in argv:
            d = d if os.path.isdir(d) else os.path.join(LAEUFE, d)
            res = {f[:-5]: json.load(open(os.path.join(d, f))) for f in os.listdir(d)
                   if f.endswith(".json") and f not in NOT_WINDOWS}
            cols.append((os.path.basename(d.rstrip("/"))[:13], summarize(res)))
        print(table(cols))
        return
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    evalp_start = "--evalp-start" in argv
    argv = [a for a in argv if a != "--evalp-start"]
    trotzdem = "--trotzdem" in argv
    argv = [a for a in argv if a != "--trotzdem"]
    name = opt(argv, "--name")
    only = opt(argv, "--fenster")
    sets = multi(argv, "--set")
    tick = float(opt(argv, "--tick", "0.1"))
    fak = float(opt(argv, "--faktor", str(faktor())))
    ips = float(opt(argv, "--ips-leer", str(faktor("ips_leer", 0.0))))
    mit064 = opt(argv, "--mit-064")
    ohne064 = "--ohne-064" in argv
    argv = [a for a in argv if a != "--ohne-064"]
    aus = opt(argv, "--aus")
    cpu = opt(argv, "--cpu")
    regel = None
    env = None
    data = json.load(open(FENSTER))
    W = data["fenster"]
    if cmd == "regel":
        test = argv.pop(0)
        variant = opt(argv, "--variante", "alle")
        regel = {"alle": {}, "bewegt": {"bewegt": True}, "eigener": {"eigener": True},
                 "bewegt_eigener": {"bewegt": True, "eigener": True}}[variant]
        binary = argv.pop(0) if argv else os.path.join(NACHT, "wt/pruefstand/tracker/core/target/release/presence-replay")
        name = name or f"regel-{variant}-{test}"
    else:
        test = cmd
        binary = os.path.abspath(argv.pop(0))
        name = name or f"{os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(binary)))))}-{test}-{time.strftime('%H%M%S')}"
    if test not in ("schnell", "voll", "gegenprobe"):
        print(__doc__)
        sys.exit(2)
    if test == "gegenprobe":
        # Thread-CPU statt Instruktionen, wenige Plätze, ruhige Maschine: misst die Kosten direkt, auch die Takte je
        # Instruktion (Speicher), die die Instruktionszählung nicht sieht
        ips = 0.0
        last = os.getloadavg()[0]
        if last > GEGENPROBE_LAST and not trotzdem:
            print(f"Gegenprobe abgelehnt: Maschine nicht ruhig (1-min-Last {last:.1f} > {GEGENPROBE_LAST:.0f}); "
                  f"später wiederholen oder --trotzdem (dann nicht verwertbar)", flush=True)
            sys.exit(3)
        env = dict(os.environ, SLOTS=str(GEGENPROBE_SLOTS))
    sel = {"schnell": "schnell", "gegenprobe": "gegenprobe"}.get(test)
    names = only.split(",") if only else (data["meta"][sel] if sel else list(W))
    names = [n for n in names if n in W]
    if (not mit064 and not ohne064 and regel is None and test != "gegenprobe" and len(names) < 5
            and os.path.exists(BIN064)):
        # zu wenige Fenster für ein Last-Mittel des Laufs: 0.64 läuft mit, damit ips_ist unter derselben Last
        # verglichen wird (allein ist die Maschine leerer als bei der Referenz, das verdeckt zu wenige
        # Instruktionen je Takt)
        mit064 = BIN064
    out = aus or os.path.join(LAEUFE, name)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="pruefstand-")
    args = {"set": sets, "tick": tick, "faktor": fak, "ips": ips, "evalp_start": evalp_start, "regel": regel,
            "cpu": float(cpu) if cpu else (600.0 if test == "schnell" else 1500.0),
            "beobachtet": data["meta"]["beobachtet"], "spanne": faktor("faktor_spanne", [2.0, 3.9]),
            "last_max": faktor("last_max", 2.3)}
    jobs = sorted(((n,) + job_for(n, W[n], args, tmp) for n in names), key=lambda j: -j[2])
    lauf = {"befehl": sys.argv, "binary": binary, "test": test, "fenster": names, "mit_064": mit064,
            "last_1min": os.getloadavg()[0], **{k: v for k, v in args.items()}}
    json.dump(lauf, open(os.path.join(out, "lauf.json"), "w"), indent=1)
    ref = load_ref("0.64", test)
    print(f"Prüfstand {test}: {len(jobs)} Fenster, {sum(j[2] for j in jobs) / 3600:.1f} h Daten, Takt {tick} s, "
          f"Faktor {fak:.1f} (geeichte Schätzung, gültig {args['spanne'][0]:.1f}-{args['spanne'][1]:.1f}), Kosten "
          f"{'Instruktionen/' + format(ips, '.2g') if ips else 'Thread-CPU'}, Grenze Rückstand {MAX_RUECKSTAND:.0f} s "
          f"/ CPU {args['cpu']:.0f} s{', 0.64 läuft mit' if mit064 else ''} -> {out}", flush=True)
    lock, results, res064 = threading.Lock(), {}, {}
    w0, c0 = time.time(), os.times()
    threads = [threading.Thread(target=run_one, args=(binary, n, j, out, verbose, lock, results, ref, env))
               for n, j, _ in jobs]
    if mit064:
        out064 = os.path.join(out, "mit-064")
        os.makedirs(out064, exist_ok=True)
        threads += [threading.Thread(target=run_one, args=(os.path.abspath(mit064), n, j, out064, False, lock,
                                                           res064, {}, env, True)) for n, j, _ in jobs]
        # abwechselnd, damit beide unter derselben Last laufen
        k = len(jobs)
        threads = [t for pair in zip(threads[:k], threads[k:]) for t in pair]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c1 = os.times()
    # das Echtzeit-Urteil noch einmal mit der Last des ganzen Laufs (und 0.64 aus demselben Lauf, falls mitgelaufen)
    vergleich = res064 if mit064 else ref
    q_lauf = lauf_q(results, vergleich)
    for n, r in results.items():
        e = echtzeit_urteil(r, vergleich.get(n), q_lauf)
        if e and e != r.get("echtzeit"):
            if e["urteil"] != (r.get("echtzeit") or {}).get("urteil"):
                print(f"{n:30} Echtzeit nach dem ganzen Lauf (ips-Mittel {q_lauf or 0:.2f}): {e['urteil'].upper()} "
                      f"- {e['grund']}", flush=True)
            r["echtzeit"] = e
            json.dump(r, open(os.path.join(out, f"{n}.json"), "w"), indent=1)
    s = summarize(results)
    s["wand_s"] = time.time() - w0
    s["cpu_kinder_s"] = (c1.children_user + c1.children_system) - (c0.children_user + c0.children_system)
    s["ips_lauf_gegen_064"] = q_lauf
    json.dump(s, open(os.path.join(out, "zusammenfassung.json"), "w"), indent=1)
    refs = {w: load_ref(w, test) for w in ("0.64", "0.65", "regel")}
    # die Summen nur über Fenster, die im Lauf und in jeder vorhandenen Referenz ganz durchliefen (ein abgebrochener
    # Lauf hat weniger Minuten); die Zahl der durchgefallenen über alle
    ok = [n for n in results if results[n].get("status") == "ok"
          and all(r.get(n, {}).get("status") == "ok" for r in refs.values() if r)]
    cols = [("Lauf", summarize({n: results[n] for n in ok}))]
    for which, r in refs.items():
        cols.append((which, summarize({n: r[n] for n in ok if n in r}) if r else None))
    print(f"\nSummen über {len(ok)} von {len(results)} Fenstern, die überall ganz durchliefen; "
          f"durchgefallen: Lauf {len(s['durchgefallen'])}" +
          "".join(f", {w} {sum(1 for n in results if n in r and r[n].get('status') != 'ok')}" for w, r in refs.items() if r))
    print(table(cols))
    print("durchgefallen im Lauf: " + (", ".join(s["durchgefallen"]) or "keins"))
    print("Echtzeit (geeichte Schätzung) Grenzfall: " + (", ".join(s["echtzeit_grenzfall"]) or "keiner") +
          "; unsicher: " + (", ".join(s["echtzeit_unsicher"]) or "keins") +
          (f"; ips des Laufs gegen 0.64 (Median) {q_lauf:.2f}" if q_lauf else ""))
    if test == "gegenprobe" and ref:
        ok_g, zeilen = gegenprobe_urteil(results, ref)
        print(f"\nGegenprobe gegen 0.64 (Thread-CPU, ruhige Maschine; 1-min-Last beim Start {lauf['last_1min']:.1f}): "
              f"{'bestanden' if ok_g else 'NICHT bestanden'}")
        print("\n".join(zeilen))
        s["gegenprobe_bestanden"] = ok_g
        json.dump(s, open(os.path.join(out, "zusammenfassung.json"), "w"), indent=1)
    elif ref and regel is None:
        abw = gleiche_bedingungen(args, os.path.join(REF, "0.64", test))
        tor, common, info = gate(s, None, results, ref)
        if abw:
            print(f"\nFreigabe-Tor nicht anwendbar: andere Bedingungen als die Referenz 0.64 ({'; '.join(abw)})")
            s["tor"] = None
        else:
            print(f"\nFreigabe-Tor gegen 0.64 ({len(common)} gemeinsame Fenster): " +
                  ", ".join(f"{k} {torwert(v)}" for k, v in tor.items()))
            for n, w in info["wachstum"].items():
                print(f"  Wachstum {n}: {'; '.join(w)}")
            if info["echtzeit_grenzfall"] or info["echtzeit_unsicher"]:
                print("  Echtzeit nicht bestanden, sondern offen: Gegenprobe nötig (tools/pruefstand.py gegenprobe BIN)")
            s["tor"], s["tor_info"] = tor, info
        json.dump(s, open(os.path.join(out, "zusammenfassung.json"), "w"), indent=1)
    print(f"\nfertig nach {s['wand_s']:.0f} s Wanduhr, {s['cpu_kinder_s']:.0f} s Prozess-CPU der Läufe -> {out}", flush=True)


if __name__ == "__main__":
    main()
