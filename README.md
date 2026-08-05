# Benzinpreis-Heatmap Europa (E10)

Statische Choroplethenkarte der E10-Benzinpreise (Euro-super 95) für Europa.
Länderebene für ganz Europa, Regionsebene (Département/Provinz) für Frankreich,
Spanien und Italien.

## Datenquellen

- **Länderebene**: [EU Weekly Oil Bulletin](https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en)
  (Europäische Kommission), Kraftstoff "Euro-super 95" — das ist der heute an
  fast allen europäischen Zapfsäulen verkaufte E10-Standardkraftstoff.
  Deckt die 27 EU-Mitgliedstaaten ab. Für Nicht-EU-Länder (CH, NO, UK, Balkan
  usw.) liegen keine offenen, vergleichbaren Daten vor — sie werden als
  "keine Daten" grau dargestellt.
- **Regionsebene** (FR, ES, IT): offene nationale Tankstellen-APIs, siehe
  `scripts/fetch_regional_prices.py`.
- **Kartengrenzen**: [Eurostat GISCO / Nuts2json](https://github.com/eurostat/Nuts2json)
  (CC BY 4.0), NUTS0 (Länder) und NUTS3 (Regionen).

## Struktur

```
index.html, app.js       – die Karte (Leaflet, kein Build-Schritt)
data/country_prices.json – Länderpreise (generiert)
data/regional_prices.json– Regionspreise FR/ES/IT (generiert)
data/geo/                – Kartengrenzen (GeoJSON)
scripts/                 – Python-Scripts zur Datenbeschaffung
.github/workflows/       – wöchentliche automatische Aktualisierung
```

## Lokal ausführen

```
python3 -m http.server 8000
```

Dann `http://localhost:8000` öffnen.

## Daten aktualisieren

```
pip install -r scripts/requirements.txt
python3 scripts/fetch_country_prices.py
python3 scripts/fetch_regional_prices.py
```
