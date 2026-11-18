"""Eichung des Prüfstands gegen einen evalp-Lauf: je Fenster und Messgröße beide Werte und die Abweichung.

usage: python3 -I tools/pruefstand_eichung.py EVALP_LAUF PRUEFSTAND_LAUF [--json AUS.json]
  EVALP_LAUF       z. B. ~/.config/presence-tracker/dev/personenzahl/runs/q064 (<fenster>.json je Fenster)
  PRUEFSTAND_LAUF  Ergebnisordner von tools/pruefstand.py (z. B. ~/checkout/pt-nacht/laeufe/eich-q064-kompat)

Die Messgrößen heißen in beiden gleich, bis auf evalps schlaf_dunkel (Schlafzimmer nicht belegt), das beim Prüfstand
schlaf_unbelegt heißt (sein schlaf_dunkel ist das Licht).
"""
import json, os, sys

METRICS = [("licht", "licht"), ("licht_zuviel", "licht_zuviel"), ("muss_dunkel", "muss_dunkel"),
           ("dunkel_sitzer", "dunkel_sitzer"), ("schlaf_dunkel", "schlaf_unbelegt"), ("schlaf_e", "schlaf_e"),
           ("zuviel", "zuviel"), ("mehrraum", "mehrraum"), ("sitzer", "sitzer"), ("minuten", "bewertet_min")]


def main():
    args = sys.argv[1:]
    out = None
    if "--json" in args:
        i = args.index("--json")
        out = args[i + 1]
        del args[i:i + 2]
    ev_dir, ps_dir = (os.path.expanduser(a) for a in args[:2])
    rows, worst = [], {}
    print(f"{'Fenster':12} " + " ".join(f"{e[:12]:>24}" for e, _ in METRICS))
    for f in sorted(os.listdir(ev_dir)):
        if not f.endswith(".json") or "_" in f or f in ("model.json", "meldungen.json"):
            continue
        name = f[:-5]
        p = os.path.join(ps_dir, f)
        if not os.path.exists(p):
            continue
        e, r = json.load(open(os.path.join(ev_dir, f))), json.load(open(p))
        if r.get("status") == "fehler":
            print(f"{name:12} Prüfstand: Fehler")
            continue
        row = {"fenster": name, "art": e["art"], "status": r["status"]}
        cells = []
        for ek, pk in METRICS:
            a, b = e.get(ek), r.get(pk)
            if a is None or b is None:
                cells.append(f"{'-':>24}")
                continue
            row[ek] = [a, b]
            d = b - a
            worst[ek] = max(worst.get(ek, (0, ""))[0], abs(d)), name if abs(d) >= worst.get(ek, (0, ""))[0] else worst[ek][1]
            cells.append(f"{a:8.2f}/{b:8.2f} {d:+6.2f}")
        rows.append(row)
        print(f"{name:12} " + " ".join(cells))
    print("\ngrößte Abweichung je Messgröße (evalp -> Prüfstand):")
    for k, (v, n) in worst.items():
        print(f"  {k:14} {v:7.2f} ({n})")
    if out:
        json.dump({"evalp": ev_dir, "pruefstand": ps_dir, "fenster": rows,
                   "groesste_abweichung": {k: {"wert": v, "fenster": n} for k, (v, n) in worst.items()}},
                  open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
