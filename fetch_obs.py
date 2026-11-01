"""Lädt gemessene Winddaten der nächsten DWD-Station je Spot und hebt die Vorhersage dazu auf.

Quelle: opendata.dwd.de, 10-Minuten-Werte (Wind, Böen, Temperatur), Zeit in UTC.
Ausgabe: web/data/obs/<spot>.json mit
  station  – nächste Station mit aktuellen Winddaten (Name, Lage, Entfernung zum Spot)
  obs      – 10-Minuten-Werte [Zeit, Wind kn, Böe kn, Richtung °, Temperatur °C] der letzten Tage
  fc       – je Modell die stündliche Vorhersage für vergangene Stunden [Zeit, Wind, Böe, Richtung]
             (jeweils vom neuesten Lauf, der diese Stunde noch vorhergesagt hat)

Die Seite wird ohne Datenbank gebaut. Damit die Vergangenheit erhalten bleibt, holt das Skript
die zuletzt veröffentlichte Datei von der Website (--prev) und hängt die neuen Werte an.
Fehlt sie, werden die Messwerte der letzten Tage aus den "recent"-Dateien des DWD nachgeladen.

Aufruf:  python fetch_obs.py [--out web/data] [--prev https://…/data/obs] [--days 7]
"""
import argparse
import datetime as dt
import io
import json
import math
import pathlib
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes"
STATIONS = f"{BASE}/wind/now/zehn_now_ff_Beschreibung_Stationen.txt"
# Produkt -> (Ordner, Dateikürzel, Spalten)
PRODUCTS = {
    "ff": ("wind", "wind", ["FF_10", "DD_10"]),
    "fx": ("extreme_wind", "extrema_wind", ["FX_10"]),
    "tu": ("air_temperature", "TU", ["TT_10"]),
}
MODELS = ["icon-d2", "icon-eu", "dmi", "ecmwf"]
MS_TO_KN = 1.943844
MAX_DIST_KM = 60

session = requests.Session()
iso = lambda t: t.strftime("%Y-%m-%dT%H:%MZ")
parse = lambda s: dt.datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)


def dist_km(a_lat, a_lon, b_lat, b_lon):
    p = math.radians
    h = math.sin(p(b_lat - a_lat) / 2) ** 2 + math.cos(p(a_lat)) * math.cos(p(b_lat)) * math.sin(p(b_lon - a_lon) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def stations():
    """Stationen, die in den letzten Tagen Winddaten geliefert haben."""
    r = session.get(STATIONS, timeout=30)
    r.raise_for_status()
    recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).strftime("%Y%m%d")
    out = []
    for line in r.content.decode("latin-1").splitlines()[2:]:
        p = line.split()
        if len(p) < 9 or p[2] < recent:
            continue
        # Name kann Leerzeichen enthalten; hinten stehen Bundesland und "Frei"
        out.append({"id": p[0], "name": " ".join(p[6:-2]), "height": int(p[3]), "lat": float(p[4]), "lon": float(p[5])})
    return out


def read_zip(url, cols):
    """Liest eine DWD-Zip-Datei -> {Zeit: [Werte]} (fehlende Werte = None)."""
    r = session.get(url, timeout=60)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next(n for n in z.namelist() if n.startswith("produkt"))
        lines = z.read(name).decode("latin-1").splitlines()
    head = [h.strip() for h in lines[0].split(";")]
    idx = [head.index(c) for c in cols]
    out = {}
    for line in lines[1:]:
        p = [x.strip() for x in line.split(";")]
        if len(p) < len(head):
            continue
        t = dt.datetime.strptime(p[1], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        vals = []
        for i in idx:
            v = float(p[i])
            vals.append(None if v <= -999 else v)
        out[t] = vals
    return out


def station_obs(sid, since, with_recent):
    """10-Minuten-Werte ab `since` -> {Zeit: [Wind kn, Böe kn, Richtung, Temp]}."""
    parts = {}
    for key, (folder, short, cols) in PRODUCTS.items():
        data = {}
        if with_recent:
            data.update(read_zip(f"{BASE}/{folder}/recent/10minutenwerte_{short}_{sid}_akt.zip", cols))
        data.update(read_zip(f"{BASE}/{folder}/now/10minutenwerte_{short}_{sid}_now.zip", cols))
        parts[key] = data
    out = {}
    for t, (ff, dd) in parts["ff"].items():
        if t < since or ff is None:
            continue
        fx = (parts["fx"].get(t) or [None])[0]
        tt = (parts["tu"].get(t) or [None])[0]
        out[t] = [round(ff * MS_TO_KN, 1), None if fx is None else round(fx * MS_TO_KN, 1),
                  None if dd is None else round(dd), tt]
    return out


def previous(prev_base, spot_id):
    if not prev_base:
        return None
    try:
        if not prev_base.startswith("http"):  # lokaler Ordner, zum Testen
            f = pathlib.Path(prev_base) / f"{spot_id}.json"
            return json.loads(f.read_text()) if f.exists() else None
        r = session.get(f"{prev_base.rstrip('/')}/{spot_id}.json", timeout=20)
        return r.json() if r.ok else None
    except Exception as e:
        print(f"  alte Datei für {spot_id} nicht lesbar: {e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spots", default=pathlib.Path(__file__).with_name("spots.json"), type=pathlib.Path)
    ap.add_argument("--out", default=pathlib.Path(__file__).with_name("web") / "data", type=pathlib.Path)
    ap.add_argument("--prev", default="", help="Adresse der veröffentlichten obs-Dateien (für den Verlauf)")
    ap.add_argument("--days", default=7, type=int, help="so viele Tage Verlauf aufheben")
    a = ap.parse_args()
    spots = json.loads(a.spots.read_text())
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=a.days)).replace(minute=0, second=0, microsecond=0)
    out_dir = a.out / "obs"
    out_dir.mkdir(parents=True, exist_ok=True)

    st = stations()
    plan = {}
    for s in spots:
        best = min(st, key=lambda x: dist_km(s["lat"], s["lon"], x["lat"], x["lon"]))
        d = dist_km(s["lat"], s["lon"], best["lat"], best["lon"])
        if d <= MAX_DIST_KM:
            plan[s["id"]] = {**best, "dist": round(d, 1)}
        else:
            print(f"{s['name']}: keine Station im Umkreis von {MAX_DIST_KM} km", file=sys.stderr)

    prev = {s["id"]: previous(a.prev, s["id"]) for s in spots}
    # "recent" (bis gestern, ca. 1 MB je Datei) nur laden, wenn der Verlauf fehlt
    need_recent = {plan[sid]["id"] for sid, p in prev.items()
                   if sid in plan and not (p and p.get("station", {}).get("id") == plan[sid]["id"] and p.get("obs"))}
    ids = sorted({x["id"] for x in plan.values()})
    with ThreadPoolExecutor(max_workers=6) as ex:
        obs = dict(zip(ids, ex.map(lambda i: station_obs(i, since, i in need_recent), ids)))
    print(f"{len(ids)} Stationen, davon {len(need_recent)} mit Nachladen", file=sys.stderr)

    for s in spots:
        sid = s["id"]
        if sid not in plan:
            continue
        stn = plan[sid]
        p = prev[sid] or {}
        same = p.get("station", {}).get("id") == stn["id"]
        rows = {parse(r[0]): r[1:] for r in (p.get("obs") or [])} if same else {}
        rows.update(obs[stn["id"]])
        rows = {t: v for t, v in rows.items() if t >= since}

        # Vorhersage-Archiv: für jede Stunde gilt der neueste Lauf, der sie noch vorhergesagt hat
        fc = {}
        for m in MODELS:
            old = {parse(r[0]): r[1:] for r in (p.get("fc", {}).get(m) or [])}
            f = a.out / f"{sid}.{m}.json"
            if f.exists():
                d = json.loads(f.read_text())
                run = parse(d["run"])
                for r in d["rows"]:
                    t = parse(r["t"])
                    # Startzeitpunkt auslassen (dort fehlen bei ICON die Böen), Zukunft noch nicht aufheben
                    if run < t <= now + dt.timedelta(hours=1):
                        old[t] = [r["wind"], r["gust"], r["dir"]]
            old = {t: v for t, v in old.items() if t >= since}
            if old:
                fc[m] = [[iso(t), *old[t]] for t in sorted(old)]

        (out_dir / f"{sid}.json").write_text(json.dumps({
            "spot": s,
            "station": stn,
            "fetched": iso(now),
            "obs": [[iso(t), *rows[t]] for t in sorted(rows)],
            "fc": fc,
        }, ensure_ascii=False, separators=(",", ":")))
        last = max(rows) if rows else None
        print(f"  {s['name']}: {stn['name']} ({stn['dist']} km), {len(rows)} Werte, letzter {last}", file=sys.stderr)


if __name__ == "__main__":
    main()
