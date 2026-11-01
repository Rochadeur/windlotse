# WindLotse

Windvorhersage für Kitespots aus den DWD-Modellen ICON-D2 (2,2 km, 48 h) und ICON-EU (7 km, 120 h),
Daten von [opendata.dwd.de](https://opendata.dwd.de).
Zusätzlich DMI HARMONIE (2 km) und ECMWF IFS (9 km) über [Open-Meteo](https://open-meteo.com) (CC BY 4.0, freie API nur nicht-kommerziell).

## So funktioniert es

- `fetch_icon.py` lädt die neuesten Modellläufe und schreibt pro Spot und Modell eine JSON-Datei nach `web/data/`.
- `fetch_openmeteo.py` holt DMI HARMONIE und ECMWF über die Open-Meteo-API (ein Aufruf pro Modell für alle Spots).
- `web/index.html` ist eine statische Seite, die diese Dateien anzeigt.
- `.github/workflows/forecast.yml` führt das Skript stündlich aus und veröffentlicht `web/` auf GitHub Pages.
  Die Daten werden nicht ins Repo committet.

## Spots ändern

`spots.json` bearbeiten (id, Name, Breite, Länge) und committen. Der nächste Lauf übernimmt sie.

## Einrichtung

Einmalig unter *Settings → Pages → Build and deployment → Source* „GitHub Actions“ wählen.
Danach unter *Actions → Vorhersage aktualisieren → Run workflow* den ersten Lauf starten.

## Lokal testen

```
pip install -r requirements.txt
python fetch_icon.py
python fetch_openmeteo.py
cd web && python -m http.server 8000   # http://localhost:8000
```
