"""Die Wahrheitsfenster des Prüfstands (tools/pruefstand.py) als Datendatei außerhalb des Repos erzeugen.

usage: python3 -I tools/pruefstand_fenster.py [--zusatz ZUSATZ.json] [--aus FENSTER.json]

Quelle sind WINDOWS, EVENTS/EVENT_MUST, MUST, SKIP und REPORTS aus evalp.py und eine private Zusatzdatei (Vorgabe
~/.config/presence-tracker/dev/pruefstand/zusatz.json: Pfad zu evalp.py, Aufnahmeordner, je Tag Konfiguration und
Gelerntes, bekannte Zeiten des Hereinkommens, Zusatzfenster, die Auswahl des Schnelltests und der Echtzeit-Gegenprobe). Je Fenster:
- die Konfiguration aus der Aufnahme (die erste app/config der Startstunde wie evalp.run_window, sonst die des Tags),
  mit den Live-Schwellen light_cost 0,2 und occupied_off 1/12, als Datei unter dev/pruefstand/cfg/;
- das Gelernte wie evalp.run_window (das des Tags; ein Tag, dessen Konfiguration andere Sensoren hat, wird durch den
  letzten mit denselben ersetzt);
- die Aufnahmedateien je Stunde (die größte Datei unter den Aufnahmeordnern);
- ein kurzer Vorlauf: Start 15 min vor dem Hereinkommen statt Stunden (Regel in `vorlauf`, Grund steht im Fenster).
Ausgabe: ~/.config/presence-tracker/dev/fenster.json (private Daten, nie ins Repo).
"""
import collections, gzip, json, math, os, sys, time

HOME = os.path.expanduser("~")
DEV = os.path.join(HOME, ".config/presence-tracker/dev")
OUT = os.path.join(DEV, "fenster.json")
BENCH = os.path.join(DEV, "pruefstand")
ZUSATZ = os.path.join(BENCH, "zusatz.json")
LIVE = {"light_cost": 0.2, "occupied_off": 1 / 12}
LEAD = 15 * 60
ROOMS_SHORT = {"Bade": "badezimmer", "Essz": "esszimmer", "Wohn": "wohnzimmer", "Schl": "schlafzimmer",
               "Arbe": "arbeitszimmer", "Flur": "flur", "Kuec": "kueche"}
Z = {}  # die Zusatzdatei (main)
pt = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def load_evalp(path):
    ns = {"__file__": path, "__name__": "evalp"}
    exec(compile(open(path).read(), "evalp", "exec"), ns)
    return ns


def hour_file(t):
    name = time.strftime("%Y%m%d-%H", time.localtime(t)) + ".jsonl.gz"
    found = [os.path.join(d, name) for d in Z["aufnahmeordner"] if os.path.exists(os.path.join(d, name))]
    return max(found, key=os.path.getsize) if found else None


def files_for(a, b):
    out, t = [], a - a % 3600
    while t <= b:
        f = hour_file(t)
        if f:
            out.append(f)
        t += 3600
    return out


def lines(path):
    try:
        with gzip.open(path, "rt") as f:
            for line in f:
                yield line
    except (EOFError, OSError):  # eine Aufnahme, die noch geschrieben wurde
        return


def config_at(t):
    f = hour_file(t)
    if f:
        for line in lines(f):
            if '"app/config"' in line:
                m = json.loads(line)
                if m["topic"] == "app/config":
                    return m["payload"]["config"]
    return None


def tag_cfg(tag):
    return json.load(open(Z["tage"][tag]["cfg"]))


def learned_path(tag):
    day = Z["tage"][tag]
    if "learned" in day:
        return day["learned"]
    p = os.path.join(BENCH, f"learned_{tag}.json")
    if not os.path.exists(p):  # Gelerntes im Format der App ({"geometry", "learned"}) wie evalp (learned())
        ld = json.load(open(day["ld2410"]))["learned"]
        dm = json.load(open(day["destinations"]))["learned"]
        json.dump({"ld_background": ld, "dest_map": dm}, open(p, "w"))
    return p


class Sightings:
    """LD2450-Ziele je Raum (evalp.world/inside), sekündlich, aus den Aufnahmen; je Stunde einmal gelesen."""

    def __init__(self, ev):
        self.ev, self.cache = ev, {}

    def hour(self, t):
        f = hour_file(t)
        if f is None:
            return []
        if f not in self.cache:
            out, cfg = [], None
            for line in lines(f):
                m = json.loads(line)
                if m["topic"] == "app/config":
                    cfg = m["payload"]["config"]
                    S = {s["id"]: s for s in cfg["sensors"]}
                    continue
                if not m["topic"].endswith("/frame"):
                    continue
                if cfg is None:
                    cfg = config_at(t - 3600) or tag_cfg(list(Z["tage"])[-1])
                    S = {s["id"]: s for s in cfg["sensors"]}
                s = S.get(m["topic"].split("/")[1])
                if not s or not s.get("placed") or not s.get("enabled"):
                    continue
                th = cfg["params"].get("target_height", 1.0)
                for d in m["payload"].get("targets", []):
                    if math.hypot(d["x"], d["y"]) < 300:
                        continue
                    x, y = self.ev["world"](s, d["x"], d["y"], th)
                    for z in cfg["zones"]:
                        if z["id"] in self.ev["OBSERVED"] and self.ev["inside"](z["points"], x, y):
                            out.append((m["t"], z["id"], d.get("speed", 0) != 0))
                            break
            self.cache[f] = out
        return self.cache[f]

    def between(self, a, b):
        out, t = [], a - a % 3600
        while t <= b:
            out += [x for x in self.hour(t) if a <= x[0] <= b]
            t += 3600
        return out


def entry_gap(sg, room, ref, back=3 * 3600, gap=300):
    """Wann die Person(en) in den Raum kamen: das erste Ziel dort nach der letzten Lücke von `gap` s ohne Ziel
    vor `ref` (höchstens `back` s zurück)."""
    ts = sorted({int(t) for t, r, _ in sg.between(ref - back, ref) if r == room})
    if not ts:
        return None
    k = len(ts) - 1
    while k > 0 and ts[k] - ts[k - 1] < gap:
        k -= 1
    return float(ts[k])


def last_moving(sg, ref, back, outside=None):
    """Die letzte bewegte Erfassung vor ref in einem beobachteten Raum (außer `outside`)."""
    xs = [t for t, r, mv in sg.between(ref - back, ref) if mv and r != outside]
    return max(xs) if xs else None


def night_ref(t):
    """01:00 der Nacht, zu der t gehört (der Abend davor bis zum Morgen)."""
    lt = time.localtime(t - 12 * 3600)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 1, 0, 0, 0, 0, -1)) + 86400


def vorlauf(name, kind, m0, must, sg):
    """Start des Laufs und Grund: 15 min vor dem Hereinkommen."""
    if name in Z["bekannt"]:
        e, why = Z["bekannt"][name]
        return min(pt(e), m0) - LEAD, why
    if must:
        room, a = must[0][0], must[0][1]
        if room == "schlafzimmer" and 1 <= time.localtime(a).tm_hour < 12 or kind == "nacht":
            ref = min(night_ref(a), a)
            t = last_moving(sg, ref, 6 * 3600, outside="schlafzimmer")
            if t:
                return min(t, m0) - LEAD, f"Schlafenszeit: letzte Bewegung außerhalb des Schlafzimmers vor {fmt(ref)[11:16]} um {fmt(t)[11:19]}"
        e = entry_gap(sg, room, a)
        if e:
            return min(e, m0) - LEAD, f"erstes Ziel im {room} nach 5 min ohne, {fmt(e)[11:19]}"
    if kind == "nacht" or name.startswith("morgen"):
        ref = night_ref(m0)
        t = last_moving(sg, ref, 6 * 3600, outside="schlafzimmer")
        if t:
            return min(t, m0) - LEAD, f"Schlafenszeit: letzte Bewegung außerhalb des Schlafzimmers vor 01:00 um {fmt(t)[11:19]}"
    if kind == "leer":
        t = last_moving(sg, m0, 4 * 3600)
        if t:
            return max(min(t - LEAD, m0 - LEAD), m0 - 4 * 3600), f"letzte Bewegung im Haus {fmt(t)[11:19]}"
        return m0 - 4 * 3600, "keine Bewegung in 4 h vor dem Fenster"
    return m0 - LEAD, "15 min vor dem Fenster"


def main():
    global Z
    argv = sys.argv[1:]
    out = argv[argv.index("--aus") + 1] if "--aus" in argv else OUT
    Z = json.load(open(argv[argv.index("--zusatz") + 1] if "--zusatz" in argv else ZUSATZ))
    ev = load_evalp(Z["evalp"])
    os.makedirs(os.path.join(BENCH, "cfg"), exist_ok=True)
    sg = Sightings(ev)
    W, MUST, SKIP = ev["WINDOWS"], ev["MUST"], ev["SKIP"]
    windows = {}
    specs = [(n, w[0], w[1], w[2], w[3], w[4], MUST.get(n, []), [], [], "evalp WINDOWS" + (" (EVENTS)" if n in ev["EVENTS"] else ""))
             for n, w in W.items()]
    # die Meldungen (REPORTS): Wahrheit 1 = der Raum muss hell sein, 0 = er muss dunkel sein, None = nicht zwei
    for f, short, a, b, truth in ev["REPORTS"]:
        day = f[:8]
        d = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        room = ROOMS_SHORT[short]
        iv = [(room, f"{d} {a}", f"{d} {b}")]
        tag = Z["meldung_tag"][day]
        specs.append((f"meld_{f[:15]}_{room[:4]}", tag, [], f"{d} {a}", f"{d} {b}", "meldung",
                      iv if truth == 1 else [], iv if truth == 0 else [], iv if truth is None else [], f"evalp REPORTS {f}"))
    # Zusatzfenster (ohne evalp-Wahrheit, z. B. das Wuchern unter 0.65 am 17.11.): Start fest
    fixed = {}
    for x in Z["zusatzfenster"]:
        specs.append((x["name"], x["tag"], [x["start"]], x["von"], x["bis"], x["art"], [], [], [], x["quelle"]))
        fixed[x["name"]] = x["vorlauf_grund"]
    for name, tag, starts, m0s, m1s, kind, must, verbot, zwei, quelle in specs:
        m0, m1 = pt(m0s), pt(m1s)
        must = [(r, pt(a), pt(b)) for r, a, b in must]
        if name in fixed:
            start, why = pt(starts[0]), fixed[name]
        else:
            ref_must = must or [(r, pt(a), pt(b)) for r, a, b in verbot + zwei]
            start, why = vorlauf(name, kind, m0, ref_must, sg)
        cfg = config_at(pt(starts[0]) if starts else start) or config_at(start) or tag_cfg(tag)
        own = [t for t in Z["tage"] if tag_cfg(t)["sensors"] == cfg["sensors"]]
        ltag = tag if (not own or tag in own) else own[-1]
        cfg = {**cfg, "params": {**cfg["params"], **LIVE}}
        cpath = os.path.join(BENCH, "cfg", f"{name}.json")
        json.dump(cfg, open(cpath, "w"))
        evalp_start = pt(starts[0]) if starts else None
        windows[name] = {
            "art": kind, "quelle": quelle, "m0": m0, "m1": m1, "von": m0s, "bis": m1s,
            "start": start, "start_text": fmt(start), "vorlauf_grund": why, "vorlauf_min": (min(start, m0) - m0) / -60,
            "evalp_start": evalp_start, "evalp_start_text": starts[0] if starts else None,
            "config": cpath, "learned": learned_path(ltag), "gelernt_tag": ltag,
            "files": files_for(min(start, evalp_start or start), m1),
            "must": [[r, a, b] for r, a, b in must],
            "verbot": [[r, pt(a), pt(b)] for r, a, b in verbot],
            "hoechstens_eins": [[r, pt(a), pt(b)] for r, a, b in zwei],
            "skip": [[pt(a), pt(b)] for a, b in SKIP if pt(b) >= start and pt(a) <= m1],
        }
        print(f"{name:32} {kind:7} {fmt(start)} → {m0s[11:]}–{m1s[11:]} ({windows[name]['vorlauf_min']:.0f} min Vorlauf; {why}) gelernt {ltag}", flush=True)
    meta = {"erzeugt": time.strftime("%Y-%m-%d %H:%M:%S"), "quelle": Z["evalp"], "schwellen": LIVE, "vorlauf_s": LEAD,
            "beobachtet": sorted(ev["OBSERVED"]), "schnell": [n for n in Z["schnell"] if n in windows],
            "gegenprobe": [n for n in Z.get("gegenprobe", []) if n in windows]}
    json.dump({"meta": meta, "fenster": windows}, open(out, "w"), indent=1)
    print(f"{len(windows)} Fenster -> {out}")


if __name__ == "__main__":
    main()
