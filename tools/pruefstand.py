"""Prüfstand: schnelle, live-getreue Bewertung eines Kerns auf den Wahrheitsfenstern (NACHTLAUF.md "Der Prüfstand").

usage:
  python3 -I tools/pruefstand.py schnell BIN [--name NAME] [Optionen]   Schnelltest (<= 5 min)
  python3 -I tools/pruefstand.py voll BIN [--name NAME] [Optionen]      alle Fenster (<= 30 min)
  python3 -I tools/pruefstand.py regel schnell|voll [--name NAME]       die einfache Regel (Referenz)
  python3 -I tools/pruefstand.py gegenprobe BIN [--name NAME]          Echtzeit-Gegenprobe der kritischen Fenster:
                       Thread-CPU statt Instruktionen, höchstens 8 Plätze, nur auf ruhiger Maschine (Last < 4)
  python3 -I tools/pruefstand.py zeige LAUF [LAUF...]                    Zusammenfassung gespeicherter Läufe
  python3 -I tools/pruefstand.py aufstehen BIN --name N [--modell M] [--set k=v] [--uhr 2] [--mit LAUF]
                       "Aufstehen erkannt": morgen* und ev_schl_*, kalt um 2 Uhr gestartet (nächtlicher Neustart)
  python3 -I tools/pruefstand.py aufstehen-vergleiche LAUF [LAUF...]     dieselbe Tabelle für gespeicherte Läufe
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
  --gelernt kausal     (pruefe/aufstehen) die gelernte Datei vom spätesten Tag vor dem Fenster statt der aus
                       fenster.json (die teils den Fenstertag selbst enthält, z. B. learned_0811 für den 7.11.)
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

def raeume_von(cfg_path, beobachtet):
    """Die bewerteten Räume in der Reihenfolge der Konfiguration (so beobachtet sie der Filter)."""
    zones = json.load(open(cfg_path)).get("zones", [])
    out = [z["id"] for z in zones if z.get("id") in beobachtet]
    return out + [r for r in beobachtet if r not in out]


def set_dict(sets):
    out = {}
    for kv in sets:
        k, v = kv.split("=", 1)
        out[k] = parse_value(v)
    return out


FENSTER_OFFEN = os.path.join(LAEUFE, "zonen-daten", "fenster_offen.json")


def fenster_offen(a, b):
    """Je Raum die Zeiten mit offenem Fenster in [a, b] (HA *_fenster_contact, ab 10.11.; davor unbekannt)."""
    try:
        o = json.load(open(FENSTER_OFFEN))["offen"]
    except (OSError, ValueError, KeyError):
        return {}
    return {r: [x for x in v if x[0] <= b and x[1] >= a] for r, v in o.items()}


def gelernt_kausal(w):
    """Die gelernte Datei (learned_ddmm.json neben der des Fensters) mit dem spätesten Tag vor dem Tag des Fensters:
    was die App beim Fensterbeginn gespeichert haben konnte (fenster.json nimmt teils die des Tages oder eines
    späteren, z. B. learned_0811 für den 7.11.)."""
    import datetime as _dt, glob as _glob
    d = os.path.dirname(w["learned"])
    tag = _dt.date.fromtimestamp(w["m0"])
    best = None
    for f in _glob.glob(os.path.join(d, "learned_*.json")):
        k = os.path.basename(f)[8:12]
        try:
            dd = _dt.date(tag.year, int(k[2:]), int(k[:2]))
        except ValueError:
            continue
        if dd < tag and (best is None or dd > best[0]):
            best = (dd, f)
    return best[1] if best else w["learned"]


def job_for(name, w, args, tmp, start=None, m0=None, m1=None, must=None, files=None):
    """Der Job eines Laufs über das Fenster w (start/m0/m1/must: abweichend, z. B. ein Ereignis mit eigenem
    Vorlauf). Die Parameter aus --set gehen als "set" an bench: das Modell nimmt sie oder bricht ab."""
    if start is None:
        start = w["evalp_start"] if args["evalp_start"] and w.get("evalp_start") else w["start"]
    m0 = w["m0"] if m0 is None else m0
    m1 = w["m1"] if m1 is None else m1
    lt = time.localtime(m0)
    job = {"name": name, "kind": w["art"] if w["art"] != "meldung" or must is None else "zwei",
           "config": w["config"],
           "learned": gelernt_kausal(w) if args.get("gelernt") == "kausal" else w["learned"], "files": w["files"] if files is None else files,
           "start": start, "m0": m0, "m1": m1,
           "must": w["must"] if must is None else must, "verbot": w["verbot"] if must is None else [],
           "hoechstens_eins": w["hoechstens_eins"] if must is None else [],
           "skip": [x for x in w["skip"] if x[1] >= start and x[0] <= m1], "tick": args["tick"],
           "factor": args["faktor"], "faktor_spanne": args["spanne"], "last_max": args["last_max"],
           "max_backlog": MAX_RUECKSTAND, "cpu_limit": args["cpu"], "progress": 60, "tz": lt.tm_gmtoff,
           "modell": args.get("modell") or "filter", "set": set_dict(args["set"]),
           "raeume": raeume_von(w["config"], args["beobachtet"]),
           "fenster_offen": fenster_offen(start, m1)}
    if args["ips"]:
        job["ips_leer"] = args["ips"]
    if args["regel"] is not None:
        job["modell"] = "regel"
        job["set"] = {**args["regel"], **job["set"]}
    path = os.path.join(tmp, f"job_{name}.json")
    json.dump(job, open(path, "w"))
    return path, (m1 - start)


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
        load = (f" | hyps {r.get('max_hyps', '-')} ohne Spur {r.get('max_ohne_spur', '-')} (Ende {r.get('ende_ohne_spur', '-')})"
                f" dichten {r.get('max_dichten', '-')}"
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
         "daten_min": 0.0, "echtzeit_grenzfall": [], "echtzeit_unsicher": [],
         "licht_ohne_person": 0.0, "fenster_offen_raummin": 0.0, "nachlauf_lueften": [], "nachlauf_lueften_max_s": None}
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
        # Licht in einem Raum ohne Person (Leon, 20.11.): nur wo die Wahrheit je Raum bekannt ist - das leere Haus
        # (jeder Raum) und die Verbote der Meldungen (der Raum); sonst wird nicht geraten
        s["licht_ohne_person"] += (r["licht"] if art == "leer" else 0.0) + r["verbot_licht"]
        s["fenster_offen_raummin"] += (r.get("fenster_offen_raumsekunden") or 0.0) / 60
        for x in r.get("nachlauf_lueften") or []:
            s["nachlauf_lueften"].append({**x, "fenster": n})
            v = x.get("bis_dunkel_s")
            v = 1e9 if v is None else v
            s["nachlauf_lueften_max_s"] = v if s["nachlauf_lueften_max_s"] is None else max(s["nachlauf_lueften_max_s"], v)
        if r.get("zwei_p") is not None:
            s["zwei_p_max"] = max(s["zwei_p_max"] or 0.0, r["zwei_p"])
        s["daten_min"] += r["daten_min"]
        if r.get("modus") == "modell":
            for k in ("max_hyps", "max_ohne_spur", "max_dichten"):
                s[k] = max(s[k], r.get(k, 0))
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
    if "max_hyps" in r and "max_hyps" in b and r["max_hyps"] > max(2 * b["max_hyps"], 16):
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


# ------------------------------------------------------------------ Ereignisse, Starts, Statistik

# Vorlauf je Start (min vor dem Anker): der Anker ist der Moment, in dem die Personen hereinkommen (Fenster: Start
# + 15 min, wie bisher), bei Ereignissen nach --anker. Mehrere Starts messen, wie sehr ein Ergebnis an Kleinigkeiten
# des Anfangszustands hängt (Kippen), statt es aus einem Lauf abzulesen.
VORLAUF_STARTS = (20.0, 12.0, 5.0)
X_VERLOREN = 30.0  # s: ein Pflichtereignis ist verloren, wenn der Pflichtraum länger als X s am Stück dunkel war
ALPHA = 0.05
LEAD_FENSTER = 900.0  # pruefstand_fenster.LEAD: Start eines Fensters = Anker - 15 min


def vorlaeufe(n):
    if n == 3:
        return list(VORLAUF_STARTS)
    if n <= 1:
        return [15.0]
    return [round(20.0 - 15.0 * i / (n - 1), 1) for i in range(n)]


def ereignisse_von(W):
    """Die Pflichtereignisse: jedes verschiedene Pflichtintervall (Raum, von, bis) aus MUST, EVENTS und den Meldungen
    einmal, benannt nach dem Fenster, das es zuerst trägt (Fenster vor Meldung). Dazu die Laufgruppen: Ereignisse,
    die sich zeitlich überlappen (z. B. eine Meldung in einem Sofa-Abend), laufen in einem Lauf."""
    ivs = {}
    for n, w in W.items():
        for r, a, b in w["must"]:
            ivs.setdefault((r, a, b), []).append(n)
    events = []
    for (r, a, b), names in sorted(ivs.items(), key=lambda x: (x[0][1], x[0][2])):
        names = sorted(names, key=lambda n: (n.startswith("meld_"), W[n]["m1"] - W[n]["start"], n))
        events.append({"name": names[0], "raum": r, "von": a, "bis": b, "fenster": names[0], "auch": names[1:],
                       "quelle": W[names[0]].get("quelle", "")})
    groups, cur = [], None
    for e in events:
        if cur and e["von"] <= cur["bis"] + 60:
            cur["ereignisse"].append(e)
            cur["bis"] = max(cur["bis"], e["bis"])
        else:
            cur = {"ereignisse": [e], "von": e["von"], "bis": e["bis"]}
            groups.append(cur)
    for g in groups:
        main = max(g["ereignisse"], key=lambda e: e["bis"] - e["von"])
        g["name"], g["fenster"] = main["name"], main["fenster"]
        for e in g["ereignisse"]:
            e["gruppe"] = g["name"]
            if e is not main:
                e["teil_von"] = main["name"]
    return events, groups


class Dateien:
    """Die Aufnahmedateien je Stunde aus den Ordnern, die fenster.json nennt (die größte, wie pruefstand_fenster)."""

    def __init__(self, W):
        self.dirs = sorted({os.path.dirname(f) for w in W.values() for f in w["files"]})

    def fuer(self, a, b):
        out, t = [], a - a % 3600
        while t <= b:
            name = time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl.gz"
            found = [os.path.join(d, name) for d in self.dirs if os.path.exists(os.path.join(d, name))]
            if found:
                out.append(max(found, key=os.path.getsize))
            t += 3600
        return out


def pruef_jobs(data, args, tmp, teil, anker, n_starts, only=None):
    """Die Jobs von `ereignisse` (teil "ereignisse"), der übrigen Fenster ("rest", schnell oder voll) oder beider:
    je Gruppe/Fenster n_starts Starts. Rückgabe [(jobname, pfad, datenlänge, info)]."""
    W, meta = data["fenster"], data["meta"]
    files = Dateien(W)
    jobs = []
    events, groups = ereignisse_von(W)
    if "ereignisse" in teil:
        for g in groups:
            if only and g["name"] not in only and not any(e["name"] in only for e in g["ereignisse"]):
                continue
            w = W[g["fenster"]]
            ank = g["von"] if anker == "pflicht" else min(w["start"] + LEAD_FENSTER, g["von"])
            must = [[e["raum"], e["von"], e["bis"]] for e in g["ereignisse"]]
            for v in vorlaeufe(n_starts):
                s = ank - 60 * v
                name = f"{g['name']}@{v:g}"
                path, span = job_for(name, w, args, tmp, start=s, m0=g["von"], m1=g["bis"], must=must,
                                     files=files.fuer(s, g["bis"]))
                jobs.append((name, path, span, {"teil": "ereignis", "gruppe": g["name"], "vorlauf": v}))
    if "rest" in teil:
        sel = meta["schnell"] if "schnell" in teil else list(W)
        for n in sel:
            w = W.get(n)
            if w is None or w["must"] or (only and n not in only):
                continue
            ank = w["start"] + LEAD_FENSTER
            for v in vorlaeufe(n_starts):
                s = min(ank - 60 * v, w["m0"] - 60)
                name = f"{n}@{v:g}"
                path, span = job_for(name, w, args, tmp, start=s, files=files.fuer(s, w["m1"]))
                jobs.append((name, path, span, {"teil": "fenster", "fenster": n, "vorlauf": v}))
    return jobs, events, groups


def ereignis_ergebnisse(results, events, x=X_VERLOREN):
    """Je Ereignis die Ergebnisse je Start: gehalten (längste dunkle Lücke höchstens x s), dunkel (s), Lücke (s),
    P(jemand da) am wenigsten; ein Lauf, der durchfiel oder fehlt, zählt als None (nicht bewertet)."""
    out = {}
    for e in events:
        rows = []
        for job, r in sorted(results.items()):
            if not job.startswith(e["gruppe"] + "@"):
                continue
            v = float(job.split("@", 1)[1])
            m = None
            if r.get("status") == "ok":
                for me in r.get("muss_ereignisse", []):
                    if me["raum"] == e["raum"] and me["von"] == e["von"] and me["bis"] == e["bis"]:
                        m = me
            if m is None or not m.get("bewertet_s"):
                rows.append({"vorlauf": v, "gehalten": None, "grund": r.get("grund") or r.get("status")})
                continue
            rows.append({"vorlauf": v, "gehalten": m["luecke_max_s"] <= x, "dunkel_s": m["dunkel_s"],
                         "luecke_max_s": m["luecke_max_s"], "p_min": m.get("p_min"), "bewertet_s": m["bewertet_s"]})
        rows.sort(key=lambda q: -q["vorlauf"])
        ok = [q for q in rows if q["gehalten"] is not None]
        out[e["name"]] = {"raum": e["raum"], "von": e["von"], "bis": e["bis"], "gruppe": e["gruppe"],
                          "teil_von": e.get("teil_von"), "starts": rows,
                          "gehalten": sum(q["gehalten"] for q in ok), "n": len(ok),
                          "dunkel_s": [q["dunkel_s"] for q in ok]}
    return out


def rest_ergebnisse(results):
    """Die übrigen Messgrößen je Start (Vorlauf): summarize über die Fenster dieses Starts; dazu Mittel, kleinster
    und größter Wert über die Starts."""
    by_v = {}
    for job, r in results.items():
        if "@" not in job:
            continue
        n, v = job.split("@", 1)
        by_v.setdefault(float(v), {})[n] = r
    per = {v: summarize(rs) for v, rs in sorted(by_v.items(), reverse=True)}
    return per


def spanne(werte):
    xs = [x for x in werte if isinstance(x, (int, float))]
    if not xs:
        return None
    return {"mittel": sum(xs) / len(xs), "min": min(xs), "max": max(xs), "werte": xs}


# --- Statistik (exakt, ohne Abhängigkeiten)

def binom_tail(n, k):
    """P(X >= k) für X ~ Bin(n, 1/2)."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else 1.0


def mcnemar(paare):
    """Exakter McNemar-Test über gepaarte Ergebnisse (a gehalten, b gehalten): (nur A, nur B, p zweiseitig)."""
    b = sum(1 for x, y in paare if x and not y)
    c = sum(1 for x, y in paare if y and not x)
    n = b + c
    p = min(1.0, 2 * binom_tail(n, max(b, c))) if n else 1.0
    return b, c, p


def vorzeichen(diffs):
    """Vorzeichentest über die Ereignisse (Differenz der Haltequoten A - B): (+, -, p zweiseitig)."""
    plus = sum(1 for d in diffs if d > 1e-12)
    minus = sum(1 for d in diffs if d < -1e-12)
    n = plus + minus
    return plus, minus, (min(1.0, 2 * binom_tail(n, max(plus, minus))) if n else 1.0)


def permutation(schichten):
    """Geschichteter exakter Permutationstest (je Ereignis eine Schicht): ist der Unterschied der gehaltenen Starts
    größer als das Kippen? Unter H0 sind die Starts von A und B eines Ereignisses austauschbar: in jeder Schicht
    (kA von nA, kB von nB) ist kA hypergeometrisch (nA aus nA+nB mit kA+kB gehalten). Statistik T = Summe kA;
    Rückgabe (T, E[T], p zweiseitig, p A besser, p A schlechter)."""
    dist = {0: 1.0}
    t_obs, et = 0, 0.0
    for ka, na, kb, nb in schichten:
        h, N = ka + kb, na + nb
        pmf = {}
        for k in range(max(0, h - nb), min(h, na) + 1):
            pmf[k] = math.comb(h, k) * math.comb(N - h, na - k) / math.comb(N, na)
        et += sum(k * q for k, q in pmf.items())
        t_obs += ka
        new = {}
        for t, q in dist.items():
            for k, r in pmf.items():
                new[t + k] = new.get(t + k, 0.0) + q * r
        dist = new
    eps = 1e-9
    p2 = sum(q for t, q in dist.items() if abs(t - et) >= abs(t_obs - et) - eps)
    p_besser = sum(q for t, q in dist.items() if t >= t_obs - eps)
    p_schlechter = sum(q for t, q in dist.items() if t <= t_obs + eps)
    return t_obs, et, min(1.0, p2), min(1.0, p_besser), min(1.0, p_schlechter)


def vergleich(ea, eb):
    """Gepaarter Vergleich zweier Läufe über die Ereignisse beider: Haltequoten, Permutationstest (Hauptaussage),
    McNemar über Ereignis x Start, Vorzeichentest über Ereignisse; Kippen je Lauf."""
    gemeinsam = [n for n in ea if n in eb and ea[n]["n"] and eb[n]["n"]]
    schichten, paare, diffs = [], [], []
    for n in gemeinsam:
        a, b = ea[n], eb[n]
        schichten.append((a["gehalten"], a["n"], b["gehalten"], b["n"]))
        sb = {q["vorlauf"]: q["gehalten"] for q in b["starts"] if q["gehalten"] is not None}
        for q in a["starts"]:
            if q["gehalten"] is not None and q["vorlauf"] in sb:
                paare.append((q["gehalten"], sb[q["vorlauf"]]))
        diffs.append(a["gehalten"] / a["n"] - b["gehalten"] / b["n"])
    t, et, p2, pb, ps = permutation(schichten)
    nur_a, nur_b, p_mc = mcnemar(paare)
    plus, minus, p_vz = vorzeichen(diffs)
    return {"ereignisse": len(gemeinsam), "a_gehalten": sum(x[0] for x in schichten), "a_n": sum(x[1] for x in schichten),
            "b_gehalten": sum(x[2] for x in schichten), "b_n": sum(x[3] for x in schichten),
            "permutation": {"T": t, "erwartet": et, "p": p2, "p_a_besser": pb, "p_a_schlechter": ps},
            "mcnemar": {"nur_a": nur_a, "nur_b": nur_b, "p": p_mc},
            "vorzeichen": {"a_besser": plus, "b_besser": minus, "p": p_vz},
            "ueber_kippen": p2 < ALPHA,
            "kipplig_a": sum(1 for n in gemeinsam if 0 < ea[n]["gehalten"] < ea[n]["n"]),
            "kipplig_b": sum(1 for n in gemeinsam if 0 < eb[n]["gehalten"] < eb[n]["n"])}


def vergleich_text(v, name_a, name_b):
    p = v["permutation"]
    s = (f"{name_a} hält {v['a_gehalten']}/{v['a_n']}, {name_b} {v['b_gehalten']}/{v['b_n']} "
         f"({v['ereignisse']} Ereignisse; kipplig: {v['kipplig_a']} bzw. {v['kipplig_b']}). "
         f"Permutationstest je Ereignis: p = {p['p']:.3f}"
         f" -> {'Unterschied ÜBER dem Kippen' if v['ueber_kippen'] else 'im Kippen (kein belegter Unterschied)'}; "
         f"McNemar (Ereignis x Start): nur {name_a} {v['mcnemar']['nur_a']}, nur {name_b} {v['mcnemar']['nur_b']}, "
         f"p = {v['mcnemar']['p']:.3f}; Vorzeichen (Ereignisse): {v['vorzeichen']['a_besser']}:"
         f"{v['vorzeichen']['b_besser']}, p = {v['vorzeichen']['p']:.3f}")
    return s


# --- Tor über Ereignisquoten und Streuung

# Leon, 20.11.: "nachts dunkel" wird nur noch berichtet; an seiner Stelle prüft `aufstehen` ("Aufstehen erkannt").
# "Licht zu viel" (helle Räume über der Personenzahl im Haus) bestraft richtig gehaltene Schlafende und wird nur noch
# berichtet; geprüft wird "Licht in einem Raum ohne Person", wo die Wahrheit je Raum bekannt ist
REST_TOR = (("licht_ohne_person", "Licht ohne Person", 1.0),)


def pruef_tor(ev, ev_ref, rest, rest_ref, results, ref_results):
    """Das Tor gegen die Referenz (0.64) aus demselben Verfahren:
    - Pflicht: NEIN, wenn der Kandidat nach dem Permutationstest über dem Kippen schlechter hält (p < 0,05 einseitig)
      oder ein Ereignis, das die Referenz in allen Starts hält, in keinem Start hält (ein klarer neuer Verlust);
    - Licht ohne Person: NEIN, wenn der Kandidat in jedem Start schlechter ist als die Referenz in
      ihrem schlechtesten Start (die Spannen trennen sich) und mehr als die Schwelle (Raummin) darüber liegt;
    - durchgefallen (Echtzeit): NEIN, wenn ein Lauf durchfällt, den die Referenz schafft; offen (GRENZFALL/UNSICHER)
      wie bisher;
    - Wachstum je Lauf gegen die Referenz im selben Lauf (wie bisher)."""
    tor, info = {}, {}
    v = vergleich(ev, ev_ref)
    neu_verloren = [n for n in ev if n in ev_ref and ev_ref[n]["n"] and ev[n]["n"]
                    and ev_ref[n]["gehalten"] == ev_ref[n]["n"] and ev[n]["gehalten"] == 0]
    tor["pflicht"] = not (v["permutation"]["p_a_schlechter"] < ALPHA and v["a_gehalten"] * v["b_n"] < v["b_gehalten"] * v["a_n"]) \
        and not neu_verloren
    info["pflicht"] = {"vergleich": v, "neu_verloren": neu_verloren}
    for k, lab, schwelle in REST_TOR:
        c = spanne([s.get(k) for s in rest.values()])
        b = spanne([s.get(k) for s in rest_ref.values()])
        if not c or not b:
            tor[lab] = None
            continue
        tor[lab] = not (c["min"] > b["max"] + schwelle)
        info[lab] = {"lauf": c, "referenz": b}
    common = sorted(set(results) & set(ref_results))
    neu = [n for n in common if results[n].get("status") != "ok" and ref_results[n].get("status") == "ok"
           and results[n].get("status") != "fehler"]
    fehler = sorted(n for n, r in results.items() if r.get("status") == "fehler")
    # offen nur, wo die Referenz im selben Lauf sicher in Echtzeit bleibt (ein kalter Start kurz vor dem Ereignis
    # treibt den Rückstand auch bei 0.64 in einzelnen Läufen über 25 s)
    urteile = {n: (results[n].get("echtzeit") or {}).get("urteil") for n in common
               if (ref_results[n].get("echtzeit") or {}).get("urteil", "ok") == "ok"}
    grenz = sorted(n for n, u in urteile.items() if u == "grenzfall")
    unsicher = sorted(n for n, u in urteile.items() if u == "unsicher")
    tor["echtzeit"] = False if neu or fehler else "UNSICHER" if unsicher else "GRENZFALL" if grenz else True
    wachs = {n: w for n in common if results[n].get("status") not in ("fehler",)
             and ref_results[n].get("status") != "fehler" and (w := wachstum(results[n], ref_results[n]))}
    tor["kein_wachstum"] = not wachs
    info.update({"neu_durchgefallen": neu, "fehler": fehler, "echtzeit_grenzfall": grenz,
                 "echtzeit_unsicher": unsicher, "wachstum": wachs})
    return tor, info


# --- Ausgabe

PRUEF_ROWS = [("licht_ohne_person", "Licht in einem Raum ohne Person (Raummin)"),
              ("leer_licht", "  davon im leeren Haus (Raummin)"), ("nacht_schlaf_dunkel", "Schlafzimmer nachts dunkel (min)"),
              ("ein_zuviel", "Licht zu viel, eine Person (Raummin)"), ("zwei_zuviel", "Licht zu viel, zwei Personen (Raummin)"),
              ("verbot_licht", "  davon Meldungen: Licht, wo niemand war (min)"), ("zwei_p_max", "Meldungen: P(>=2) höchstens"),
              ("fenster_offen_raummin", "Fenster offen, nicht bewertet (Raummin)"),
              ("nachlauf_lueften_max_s", "Nachlauf nach Lüften, längster (s; leerer Raum)"),
              ("max_hyps", "höchste Zahl Hypothesen"), ("max_ohne_spur", "höchste Zahl Personen ohne Spur"),
              ("cpu_ms_je_datenmin_max", "CPU je Datenminute (ms, schlechteste)"),
              ("rueckstand_max_s", "Rückstand Inge geschätzt (s, max)")]


def fmt_spanne(sp, nk=1):
    if not sp:
        return "-"
    if abs(sp["max"] - sp["min"]) < 0.05:
        return f"{sp['mittel']:.{nk}f}"
    return f"{sp['mittel']:.{nk}f} [{sp['min']:.{nk}f}-{sp['max']:.{nk}f}]"


def ev_summen(ev):
    ok = [e for e in ev.values() if e["n"]]
    by_v = {}
    for e in ok:
        for q in e["starts"]:
            if q["gehalten"] is not None:
                by_v.setdefault(q["vorlauf"], []).append(q["dunkel_s"] / 60)
    return {"gehalten": sum(e["gehalten"] for e in ok), "n": sum(e["n"] for e in ok),
            "sicher": sum(1 for e in ok if e["gehalten"] == e["n"]),
            "kipplig": sum(1 for e in ok if 0 < e["gehalten"] < e["n"]),
            "verloren": sum(1 for e in ok if e["gehalten"] == 0), "ereignisse": len(ok),
            "dunkel_min": spanne([sum(x) for x in by_v.values()]),
            "nicht_bewertet": sorted(n for n, e in ev.items() if not e["n"])}


def pruef_tabelle(cols):
    """cols: [(Titel, Lauf-Zusammenfassung {"ereignisse", "rest"})] -> Text."""
    w0, w = 40, 24
    out = [f"{'':{w0}}" + "".join(f"{t[:w - 1]:>{w}}" for t, _ in cols)]
    def row(label, f):
        out.append(f"{label:{w0}}" + "".join(f"{(f(z) if z else '-'):>{w}}" for _, z in cols))
    row("Pflicht gehalten (Starts)", lambda z: f"{z['summe']['gehalten']}/{z['summe']['n']}")
    row("  Ereignisse sicher / kipplig / verloren",
        lambda z: f"{z['summe']['sicher']}/{z['summe']['kipplig']}/{z['summe']['verloren']}")
    row("Pflicht dunkel (min, Summe je Start)", lambda z: fmt_spanne(z["summe"]["dunkel_min"]))
    for k, lab in PRUEF_ROWS:
        row(lab, lambda z, k=k: fmt_spanne(spanne([s.get(k) for s in z["rest"].values()]),
                                           0 if k.startswith(("max_", "cpu")) else 1 if k != "zwei_p_max" else 2))
    row("durchgefallen (Läufe)", lambda z: str(len(z.get("durchgefallen", []))))
    return "\n".join(out)


def ereignis_zeilen(ev, ref=None, x=X_VERLOREN):
    out = []
    for n, e in sorted(ev.items(), key=lambda kv: kv[1]["von"]):
        if not e["starts"]:
            continue
        tz = time.localtime(e["von"])
        st = " ".join("-" if q["gehalten"] is None else (f"{q['luecke_max_s']:.0f}s" if not q["gehalten"] else "ok")
                      for q in e["starts"])
        r = ""
        if ref and n in ref and ref[n]["n"]:
            r = f"  | Ref {ref[n]['gehalten']}/{ref[n]['n']}"
        teil = f" (in {e['teil_von']})" if e.get("teil_von") else ""
        out.append(f"  {n[:30]:30} {e['raum'][:12]:12} {time.strftime('%d.%m. %H:%M', tz)} "
                   f"{(e['bis'] - e['von']) / 60:5.1f} min  gehalten {e['gehalten']} von {e['n']}  [{st}]{r}{teil}")
    return out


def lade_lauf(d):
    res = {}
    for f in os.listdir(d):
        if f.endswith(".json") and f not in NOT_WINDOWS:
            r = json.load(open(os.path.join(d, f)))
            res[r.get("fenster", f[:-5])] = r
    return res


def pruef_zusammenfassung(results, events, x):
    ev = ereignis_ergebnisse(results, events, x)
    rest = rest_ergebnisse({n: r for n, r in results.items() if not any(n.startswith(e["gruppe"] + "@") for e in events)})
    return {"ereignisse": ev, "summe": ev_summen(ev), "rest": {f"{v:g}": s for v, s in rest.items()},
            "durchgefallen": sorted(n for n, r in results.items() if r.get("status") != "ok"), "x_s": x}


PRUEF_REF = os.path.join(REF, "pruefe")
ANKER_VORGABE = "pflicht"


def parameter_text(r):
    ps = r.get("parameter") or []
    if not ps:
        return "Vorgaben"
    return ", ".join(f"{p['name']}={p['jetzt']} ({p['wo']}, vorher {p['vorher']})" for p in ps)


def pruef_zeile(r):
    name = r.get("fenster", "?")
    if r.get("status") == "fehler":
        return f"{name:34} FEHLER {r.get('grund', '')[:300]}"
    st = "ok" if r["status"] == "ok" else "DURCHGEFALLEN"
    ev = " ".join(f"{m['raum'][:4]} dunkel {m['dunkel_s']:.0f}s Lücke {m['luecke_max_s']:.0f}s"
                  + (f" Pmin {m['p_min']:.2f}" if m.get("p_min") is not None else "")
                  for m in r.get("muss_ereignisse", []))
    s = (f"{name:34} {st} {ev or key_metric(r)} | Rückstand {r['rueckstand_max_s']:.1f} s"
         f" | {r['daten_min']:.0f} min Daten, {r.get('wand_s', 0):.0f} s")
    if r["status"] != "ok":
        s += f"\n{'':34}   -> {r['grund']}"
    elif (r.get("echtzeit") or {}).get("urteil", "ok") != "ok":
        s += f"\n{'':34}   -> Echtzeit {r['echtzeit']['urteil'].upper()}: {r['echtzeit']['grund']}"
    return s


def lade_pruef_refs(events, x, bedingungen):
    """Die gespeicherten Referenzen des Verfahrens (referenzen/pruefe/<name>) mit denselben Bedingungen."""
    out = {}
    if not os.path.isdir(PRUEF_REF):
        return out
    for nm in sorted(os.listdir(PRUEF_REF)):
        d = os.path.join(PRUEF_REF, nm)
        try:
            lauf = json.load(open(os.path.join(d, "lauf.json")))
        except OSError:
            continue
        if any(lauf.get(k) != v for k, v in bedingungen.items()) or not os.path.exists(os.path.join(d, "zusammenfassung.json")):
            continue  # andere Bedingungen, oder noch nicht fertig
        res = lade_lauf(d)
        out[nm] = (res, pruef_zusammenfassung(res, events, x))
    return out


def main_pruefe(cmd, argv):
    """pruefe / ereignisse: je Pflichtereignis (und je übrigem Fenster) mehrere Starts, Quoten, Streuung, Tor."""
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    voll = "--voll" in argv
    argv = [a for a in argv if a != "--voll"]
    name = opt(argv, "--name")
    only = opt(argv, "--fenster")
    sets = multi(argv, "--set")
    modell = opt(argv, "--modell", "filter")
    n_starts = int(opt(argv, "--starts", "3"))
    x = float(opt(argv, "--x", str(X_VERLOREN)))
    anker = opt(argv, "--anker", ANKER_VORGABE)
    tick = float(opt(argv, "--tick", "0.1"))
    fak = float(opt(argv, "--faktor", str(faktor())))
    ips = float(opt(argv, "--ips-leer", str(faktor("ips_leer", 0.0))))
    aus = opt(argv, "--aus")
    cpu = opt(argv, "--cpu")
    ref_name = opt(argv, "--referenz", "0.64")
    gelernt = opt(argv, "--gelernt", "fenster")
    binary = os.path.abspath(argv.pop(0))
    data = json.load(open(FENSTER))
    teil = {"ereignisse"} if cmd == "ereignisse" else {"ereignisse", "rest", "voll" if voll else "schnell"}
    name = name or f"{modell}-{cmd}-{time.strftime('%H%M%S')}"
    out = aus or os.path.join(LAEUFE, name)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="pruefstand-")
    args = {"set": sets, "tick": tick, "faktor": fak, "ips": ips, "evalp_start": False, "regel": None,
            "modell": modell, "cpu": float(cpu) if cpu else 900.0, "beobachtet": data["meta"]["beobachtet"],
            "spanne": faktor("faktor_spanne", [2.0, 3.9]), "last_max": faktor("last_max", 2.3), "gelernt": gelernt}
    jobs, events, groups = pruef_jobs(data, args, tmp, teil, anker, n_starts, set(only.split(",")) if only else None)
    bedingungen = {"test": cmd, "anker": anker, "starts": n_starts, "tick": tick, "voll": voll, "gelernt": gelernt}
    # die Parameter vorab: was wirkt (bench bricht bei einem unbekannten ab)
    if modell == "filter":
        p = subprocess.run([binary, "parameter"] + sets, capture_output=True, text=True)
        if p.returncode != 0:
            print(p.stderr.strip())
            sys.exit(2)
        wirkt = json.loads(p.stdout) if sets else []
    else:
        wirkt = sets
    refs = lade_pruef_refs(events, x, {**bedingungen, "test": "pruefe"})
    if cmd == "ereignisse":
        # die Ereignisse sind in pruefe und ereignisse dieselben Läufe
        refs.update({k: v for k, v in lade_pruef_refs(events, x, bedingungen).items() if k not in refs})
    # der Lauf selbst ist keine Referenz (auch wenn er in referenzen/pruefe geschrieben wird)
    refs = {k: v for k, v in refs.items() if os.path.realpath(os.path.join(PRUEF_REF, k)) != os.path.realpath(out)}
    lauf = {"befehl": sys.argv, "binary": binary, "modell": modell, "set": sets, "last_1min": os.getloadavg()[0],
            "jobs": [j[0] for j in jobs], "faktor": fak, "ips": ips, **bedingungen}
    json.dump(lauf, open(os.path.join(out, "lauf.json"), "w"), indent=1)
    ref064 = refs.get(ref_name, ({}, None))
    # die teuersten zuerst (Kosten aus der Referenz, sonst nach der Datenlänge): die Wanduhr ist der längste Lauf
    jobs.sort(key=lambda j: -(ref064[0].get(j[0], {}).get("cpu_prozess_s") or j[2] / 60 * 3))
    laufend = {j[3]["gruppe"] for j in jobs if j[3]["teil"] == "ereignis"}
    print(f"Prüfstand {cmd}: Modell {modell}, Parameter: "
          f"{', '.join(f'{p['name']}={p['jetzt']} ({p['wo']}, vorher {p['vorher']})' for p in wirkt) if wirkt and isinstance(wirkt[0], dict) else (', '.join(wirkt) or 'Vorgaben')}; "
          f"{sum(1 for e in events if e['gruppe'] in laufend)} Pflichtereignisse in {len(laufend)} Läufen" + (" + übrige Fenster" if "rest" in teil else "") +
          f", je {n_starts} Starts (Vorlauf {', '.join(f'{v:g}' for v in vorlaeufe(n_starts))} min vor "
          f"{'dem Beginn der Pflicht' if anker == 'pflicht' else 'dem Hereinkommen'}); {len(jobs)} Läufe, "
          f"{sum(j[2] for j in jobs) / 3600:.1f} h Daten; verloren = länger als {x:g} s dunkel -> {out}", flush=True)
    lock, results = threading.Lock(), {}
    w0, c0 = time.time(), os.times()

    def one(n, j):
        run_one(binary, n, j, out, verbose, lock, results, ref064[0], None, quiet=True)
        with lock:
            print(pruef_zeile(results[n]), flush=True)

    threads = [threading.Thread(target=one, args=(n, j)) for n, j, _, _ in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c1 = os.times()
    q_lauf = lauf_q(results, ref064[0])
    for n, r in results.items():
        e = echtzeit_urteil(r, ref064[0].get(n), q_lauf)
        if e:
            r["echtzeit"] = e
            json.dump(r, open(os.path.join(out, f"{n}.json"), "w"), indent=1)
    z = pruef_zusammenfassung(results, events, x)
    z["wand_s"] = time.time() - w0
    z["cpu_kinder_s"] = (c1.children_user + c1.children_system) - (c0.children_user + c0.children_system)
    z["parameter"] = wirkt
    gemeldet = {r.get("fenster"): r.get("parameter") for r in results.values() if r.get("parameter") is not None}
    z["parameter_bench"] = next(iter(gemeldet.values()), None)
    print(f"\nPflichtereignisse (je Start: ok = gehalten, sonst die längste dunkle Lücke; verloren ab {x:g} s):")
    print("\n".join(ereignis_zeilen(z["ereignisse"], ref064[1]["ereignisse"] if ref064[1] else None, x)))
    cols = [("Lauf", z)] + [(nm, rz) for nm, (_, rz) in refs.items()]
    print(f"\nMittel [kleinster-größter Wert] über die {n_starts} Starts; Referenzen aus demselben Verfahren:")
    print(pruef_tabelle(cols))
    z["vergleiche"] = {}
    for nm, (_, rz) in refs.items():
        v = vergleich(z["ereignisse"], rz["ereignisse"])
        z["vergleiche"][nm] = v
        print(f"\nPflicht gegen {nm}: " + vergleich_text(v, "Lauf", nm))
    if ref064[1] and "rest" in teil:
        tor, info = pruef_tor(z["ereignisse"], ref064[1]["ereignisse"], z["rest"], ref064[1]["rest"], results, ref064[0])
        z["tor"], z["tor_info"] = tor, info
        print(f"\nTor gegen {ref_name} (Ereignisquoten und Streuung): " +
              ", ".join(f"{k} {torwert(v)}" for k, v in tor.items()) +
              f"  => {'BESTANDEN' if all(v is True for v in tor.values()) else 'NICHT bestanden'}")
        if info["pflicht"]["neu_verloren"]:
            print("  neu verloren (Referenz hält in allen Starts, Lauf in keinem): " + ", ".join(info["pflicht"]["neu_verloren"]))
        for n, w in info["wachstum"].items():
            print(f"  Wachstum {n}: {'; '.join(w)}")
        if info["neu_durchgefallen"] or info["fehler"]:
            print("  durchgefallen/Fehler: " + ", ".join(info["neu_durchgefallen"] + info["fehler"]))
    elif "rest" in teil:
        print(f"\nTor nicht anwendbar: keine Referenz {ref_name} mit denselben Bedingungen ({bedingungen}) in {PRUEF_REF}")
    json.dump(z, open(os.path.join(out, "zusammenfassung.json"), "w"), indent=1)
    print(f"\nfertig nach {z['wand_s']:.0f} s Wanduhr, {z['cpu_kinder_s']:.0f} s Prozess-CPU der Läufe -> {out}", flush=True)


def main_vergleiche(argv):
    x = float(opt(argv, "--x", str(X_VERLOREN)))
    data = json.load(open(FENSTER))
    events, _ = ereignisse_von(data["fenster"])
    ds = [d if os.path.isdir(d) else os.path.join(LAEUFE, d) for d in argv]
    zs = [(os.path.basename(d.rstrip("/")), pruef_zusammenfassung(lade_lauf(d), events, x)) for d in ds]
    print(pruef_tabelle(zs))
    for i in range(len(zs)):
        for j in range(i + 1, len(zs)):
            (na, a), (nb, b) = zs[i], zs[j]
            print(f"\n{na} gegen {nb}: " + vergleich_text(vergleich(a["ereignisse"], b["ereignisse"]), na, nb))
    print("\nje Ereignis gehalten (Starts):")
    names = sorted(zs[0][1]["ereignisse"], key=lambda n: zs[0][1]["ereignisse"][n]["von"])
    print(f"  {'':30}" + "".join(f"{n[:14]:>15}" for n, _ in zs))
    for e in names:
        print(f"  {e[:30]:30}" + "".join(f"{(str(z['ereignisse'][e]['gehalten']) + '/' + str(z['ereignisse'][e]['n'])) if e in z['ereignisse'] else '-':>15}"
                                       for _, z in zs))



# ------------------------------------------------------------------ Aufstehen erkannt

AUF_UHR = 2.0  # h Ortszeit: die App startet nachts um 2 Uhr neu (kalt), dann muss sie die Schlafenden finden


def auf_fenster(W, only=None):
    """Die Fenster für "Aufstehen erkannt": morgen* und ev_schl_*, die nach dem Neustart um AUF_UHR beginnen."""
    out = []
    for n, w in sorted(W.items(), key=lambda kv: kv[1]["m0"]):
        if not (n.startswith("morgen") or n.startswith("ev_schl_")) or (only and n not in only):
            continue
        lt = time.localtime(w["m0"])
        if lt.tm_hour < AUF_UHR or lt.tm_hour >= 12:
            continue  # abends (ev_schl_1031_2347): kein Neustart davor
        out.append(n)
    return out


def auf_start(w, uhr=AUF_UHR):
    lt = time.localtime(w["m0"])
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(uhr), int(round(uhr % 1 * 60)), 0, 0, 0, -1))


def auf_zusammenfassung(results, x=X_VERLOREN):
    """Je Fenster die Episoden (erstes bewegtes LD2450-Ziel im Raum nach >= 10 min Ruhe) mit der Zeit bis belegt,
    die Pflicht darin; Summen für das Schlafzimmer und alle Räume."""
    fen, alle = {}, []
    for n, r in sorted(results.items()):
        if r.get("status") != "ok":
            fen[n] = {"status": r.get("status"), "grund": r.get("grund")}
            continue
        eps = [{**e, "fenster": n} for e in r.get("aufstehen", [])]
        alle.extend(eps)
        must = [{"raum": m["raum"], "gehalten": m["luecke_max_s"] <= x, "luecke_max_s": m["luecke_max_s"],
                 "dunkel_s": m["dunkel_s"]} for m in r.get("muss_ereignisse", []) if m.get("bewertet_s")]
        fen[n] = {"status": "ok", "aufstehen": eps, "pflicht": must,
                  "nacht_schlaf_dunkel": r.get("nacht_schlaf_dunkel"), "cpu_prozess_s": r.get("cpu_prozess_s")}

    def summe(eps):
        lat = sorted(e["latenz_s"] for e in eps if e["latenz_s"] is not None)
        n = len(eps)
        return {"episoden": n, "erkannt": len(lat), "nicht_erkannt": n - len(lat),
                "sofort": sum(1 for v in lat if v <= 1.0), "bis_10s": sum(1 for v in lat if v <= 10.0),
                "median_s": lat[len(lat) // 2] if lat else None, "max_s": lat[-1] if lat else None,
                "summe_s": sum(lat) + 300.0 * (n - len(lat))}
    return {"fenster": fen, "schlafzimmer": summe([e for e in alle if e["raum"] == "schlafzimmer"]),
            "alle_raeume": summe(alle), "episoden": alle}


def auf_text(cols):
    w0, w = 34, 22
    out = [f"{'':{w0}}" + "".join(f"{t[:w - 1]:>{w}}" for t, _ in cols)]
    for teil, lab in (("schlafzimmer", "Schlafzimmer"), ("alle_raeume", "alle Räume")):
        for k, l2 in (("episoden", "Episoden"), ("sofort", "  belegt sofort (<= 1 s)"), ("bis_10s", "  belegt bis 10 s"),
                      ("nicht_erkannt", "  nicht belegt in 300 s"), ("median_s", "  Zeit bis belegt, Median (s)"),
                      ("max_s", "  Zeit bis belegt, längste (s)"), ("summe_s", "  Summe (s, nicht belegt = 300)")):
            out.append(f"{(lab + ': ' + l2.strip()) if k == 'episoden' else l2:{w0}}" +
                       "".join(f"{fmtv(z[teil][k]):>{w}}" for _, z in cols))
    def pflicht(z):
        ms = [m for f in z["fenster"].values() for m in f.get("pflicht", [])]
        return f"{sum(m['gehalten'] for m in ms)}/{len(ms)}"
    out.append(f"{'Pflicht gehalten (Fenster)':{w0}}" + "".join(f"{pflicht(z):>{w}}" for _, z in cols))
    return "\n".join(out)


def auf_episoden_text(cols):
    """Je Episode (Fenster, Raum, Uhrzeit) die Zeit bis belegt je Lauf."""
    keys = sorted({(e["fenster"], e["raum"], e["um"]) for _, z in cols for e in z["episoden"]})
    out = [f"  {'Fenster':22} {'Raum':13} {'um':9}" + "".join(f"{t[:13]:>14}" for t, _ in cols)]
    for k in keys:
        row = []
        for _, z in cols:
            e = next((e for e in z["episoden"] if (e["fenster"], e["raum"], e["um"]) == k), None)
            row.append("-" if e is None else ("nie" if e["latenz_s"] is None else f"{e['latenz_s']:.1f}"))
        out.append(f"  {k[0][:22]:22} {k[1][:13]:13} {k[2]:9}" + "".join(f"{v:>14}" for v in row))
    return "\n".join(out)


def auf_pflicht(z):
    ms = [m for f in z["fenster"].values() for m in f.get("pflicht", [])]
    return sum(m["gehalten"] for m in ms), len(ms)


def auf_tor(z, ref):
    """Tor "Aufstehen erkannt" (Leon, 20.11., ersetzt "nachts dunkel"): im Schlafzimmer mindestens so viele Episoden
    sofort (<= 1 s) belegt wie die Referenz, keine mehr "nie", und die Pflicht in diesen Fenstern (kalt um 2 Uhr)
    mindestens so oft gehalten."""
    a, b = z["schlafzimmer"], ref["schlafzimmer"]
    pa, pb = auf_pflicht(z), auf_pflicht(ref)
    return {"aufstehen_sofort": a["sofort"] >= b["sofort"] and a["nicht_erkannt"] <= b["nicht_erkannt"],
            "morgen_pflicht": pa[0] >= pb[0]}


def main_aufstehen(argv):
    """aufstehen BIN --name N [--modell M] [--set k=v] [--uhr H] [--fenster a,b] [--mit LAUF ...]: je Fenster morgen*
    und ev_schl_* ein Lauf, kalt gestartet um H Uhr (Vorgabe 2, wie der nächtliche Neustart der App), bewertet über
    das ganze Fenster; Messgröße "Aufstehen erkannt" und die Pflicht darin."""
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    name = opt(argv, "--name")
    only = opt(argv, "--fenster")
    sets = multi(argv, "--set")
    mit = multi(argv, "--mit")
    modell = opt(argv, "--modell", "filter")
    uhr = float(opt(argv, "--uhr", str(AUF_UHR)))
    aus = opt(argv, "--aus")
    ref_opt = opt(argv, "--referenz")
    gelernt = opt(argv, "--gelernt", "fenster")
    binary = os.path.abspath(argv.pop(0))
    data = json.load(open(FENSTER))
    W = data["fenster"]
    name = name or f"{modell}-aufstehen-{time.strftime('%H%M%S')}"
    out = aus or os.path.join(LAEUFE, name)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="pruefstand-")
    args = {"set": sets, "tick": 0.1, "faktor": faktor(), "ips": faktor("ips_leer", 0.0), "evalp_start": False,
            "regel": None, "modell": modell, "cpu": 3000.0, "beobachtet": data["meta"]["beobachtet"],
            "spanne": faktor("faktor_spanne", [2.0, 3.9]), "last_max": faktor("last_max", 2.3), "gelernt": gelernt}
    files = Dateien(W)
    jobs = []
    for n in auf_fenster(W, set(only.split(",")) if only else None):
        w = W[n]
        s = auf_start(w, uhr)
        path, span = job_for(n, w, args, tmp, start=s, files=files.fuer(s, w["m1"]))
        jobs.append((n, path, span))
    json.dump({"befehl": sys.argv, "binary": binary, "modell": modell, "set": sets, "uhr": uhr, "test": "aufstehen",
               "jobs": [j[0] for j in jobs]}, open(os.path.join(out, "lauf.json"), "w"), indent=1)
    print(f"Prüfstand aufstehen: Modell {modell}, Parameter: {', '.join(sets) or 'Vorgaben'}; {len(jobs)} Fenster, "
          f"kalt um {uhr:g} Uhr, {sum(j[2] for j in jobs) / 3600:.1f} h Daten -> {out}", flush=True)
    lock, results = threading.Lock(), {}
    threads = [threading.Thread(target=run_one, args=(binary, n, j, out, verbose, lock, results, {}))
               for n, j, _ in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    z = auf_zusammenfassung(results)
    json.dump(z, open(os.path.join(out, "zusammenfassung.json"), "w"), indent=1)
    cols = [("Lauf", z)]
    for d in mit:
        d = d if os.path.isdir(d) else os.path.join(LAEUFE, d)
        cols.append((os.path.basename(d.rstrip("/")), auf_zusammenfassung(lade_lauf(d))))
    print("\nAufstehen erkannt: erstes bewegtes LD2450-Ziel im Raum nach >= 10 min ohne eines, bis der Raum belegt ist")
    print(auf_text(cols))
    print(auf_episoden_text(cols))
    ref = ref_opt or "auf-064"
    rd = ref if os.path.isdir(ref) else os.path.join(LAEUFE, ref)
    if os.path.isdir(rd):
        tor = auf_tor(z, auf_zusammenfassung(lade_lauf(rd)))
        z["tor"] = tor
        json.dump(z, open(os.path.join(out, "zusammenfassung.json"), "w"), indent=1)
        print(f"\nTor Aufstehen gegen {os.path.basename(rd.rstrip('/'))}: " +
              ", ".join(f"{k} {torwert(v)}" for k, v in tor.items()) +
              f"  => {'BESTANDEN' if all(v is True for v in tor.values()) else 'NICHT bestanden'}")


def main_aufstehen_vergleiche(argv):
    """aufstehen-vergleiche LAUF [LAUF...] [--referenz LAUF]: Tabelle, Episoden, Tor gegen die Referenz (auf-064)."""
    ref = opt(argv, "--referenz") or "auf-064"
    cols = []
    for d in argv:
        d = d if os.path.isdir(d) else os.path.join(LAEUFE, d)
        cols.append((os.path.basename(d.rstrip("/")), auf_zusammenfassung(lade_lauf(d))))
    print(auf_text(cols))
    print(auf_episoden_text(cols))
    rd = ref if os.path.isdir(ref) else os.path.join(LAEUFE, ref)
    if os.path.isdir(rd):
        rz = auf_zusammenfassung(lade_lauf(rd))
        for n, z in cols:
            tor = auf_tor(z, rz)
            print(f"Tor Aufstehen {n} gegen {os.path.basename(rd.rstrip('/'))}: " +
                  ", ".join(f"{k} {torwert(v)}" for k, v in tor.items()) +
                  f"  => {'BESTANDEN' if all(v is True for v in tor.values()) else 'NICHT bestanden'}")

# ------------------------------------------------------------------ main

def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(2)
    cmd = argv.pop(0)
    if cmd in ("pruefe", "ereignisse"):
        return main_pruefe(cmd, argv)
    if cmd == "vergleiche":
        return main_vergleiche(argv)
    if cmd == "aufstehen":
        return main_aufstehen(argv)
    if cmd == "aufstehen-vergleiche":
        return main_aufstehen_vergleiche(argv)
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
        binary = argv.pop(0) if argv else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tracker/core/target/release/presence-replay")
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
