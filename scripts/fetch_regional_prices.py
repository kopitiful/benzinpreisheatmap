"""Fetch current E10 (or best available proxy) gasoline prices per NUTS3 region
for France, Spain and Italy from open government fuel-price data, and aggregate
station-level prices to the NUTS3 ids used in data/geo/regions_fr_es_it.geojson.

Sources (no API key required):
- France: https://data.economie.gouv.fr (prix-carburants, Opendatasoft API)
- Spain:  https://sedeaplicaciones.minetur.gob.es/ServiciosRestCarburantes/
- Italy:  https://www.mimit.gov.it (Open Data carburanti, daily CSV export)
"""
import csv
import io
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import numpy as np
import requests


def curl_get(url: str, timeout: int) -> bytes:
    """Fallback for hosts whose TLS config trips up Python's bundled OpenSSL
    (observed on sedeaplicaciones.minetur.gob.es) but that curl handles fine."""
    result = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), url],
        capture_output=True, check=True,
    )
    return result.stdout

ROOT = Path(__file__).resolve().parent.parent
GEO_PATH = ROOT / "data" / "geo" / "regions_fr_es_it.geojson"
OUT_PATH = ROOT / "data" / "regional_prices.json"
HEATMAP_OUT_PATH = ROOT / "data" / "finegrid_prices.json"

RADIUS_KM = 10
GRID_SPACING_DEG = 0.1
MIN_STATIONS_PER_CELL = 1

# Rough mainland+island bounding boxes (lat_min, lat_max, lon_min, lon_max), chosen to
# exclude far-away territories (e.g. Canary Islands) that aren't in the NUTS3 geometry.
COUNTRY_BBOX = {
    "FR": (41.0, 51.5, -5.5, 9.7),
    "ES": (35.9, 43.9, -9.6, 4.5),
    "IT": (36.0, 47.2, 6.5, 18.6),
}

FR_API = "https://data.economie.gouv.fr/api/records/1.0/search/?dataset=prix-des-carburants-en-france-flux-instantane-v2&rows=10000"
ES_API = "https://sedeaplicaciones.minetur.gob.es/ServiciosRestCarburantes/PreciosCarburantes/EstacionesTerrestres/"
IT_PRICES_CSV = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"
IT_STATIONS_CSV = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"

TIMEOUT = 60

# Italian province code -> NUTS3 region name as used in data/geo/regions_fr_es_it.geojson
IT_PROVINCE_NAME = {
    "AG": "Agrigento", "AL": "Alessandria", "AN": "Ancona", "AO": "Valle d’Aosta/Vallée d’Aoste",
    "AR": "Arezzo", "AP": "Ascoli Piceno", "AT": "Asti", "AV": "Avellino", "BA": "Bari",
    "BT": "Barletta-Andria-Trani", "BL": "Belluno", "BN": "Benevento", "BG": "Bergamo", "BI": "Biella",
    "BO": "Bologna", "BS": "Brescia", "BR": "Brindisi", "CA": "Cagliari", "CL": "Caltanissetta",
    "CB": "Campobasso", "CE": "Caserta", "CT": "Catania", "CZ": "Catanzaro", "CH": "Chieti",
    "CO": "Como", "CS": "Cosenza", "CR": "Cremona", "KR": "Crotone", "CN": "Cuneo", "EN": "Enna",
    "FM": "Fermo", "FE": "Ferrara", "FI": "Firenze", "FG": "Foggia", "FC": "Forlì-Cesena",
    "FR": "Frosinone", "GE": "Genova", "GO": "Gorizia", "GR": "Grosseto", "IM": "Imperia",
    "IS": "Isernia", "SP": "La Spezia", "LT": "Latina", "LE": "Lecce", "LC": "Lecco", "LI": "Livorno",
    "LO": "Lodi", "LU": "Lucca", "AQ": "L’Aquila", "MC": "Macerata", "MN": "Mantova",
    "MS": "Massa-Carrara", "MT": "Matera", "ME": "Messina", "MI": "Milano", "MO": "Modena",
    "MB": "Monza e della Brianza", "NA": "Napoli", "NO": "Novara", "NU": "Nuoro", "OR": "Oristano",
    "PD": "Padova", "PA": "Palermo", "PR": "Parma", "PV": "Pavia", "PG": "Perugia",
    "PU": "Pesaro e Urbino", "PE": "Pescara", "PC": "Piacenza", "PI": "Pisa", "PT": "Pistoia",
    "PN": "Pordenone", "PZ": "Potenza", "PO": "Prato", "RG": "Ragusa", "RA": "Ravenna",
    "RC": "Reggio di Calabria", "RE": "Reggio nell’Emilia", "RI": "Rieti", "RN": "Rimini",
    "RM": "Roma", "RO": "Rovigo", "SA": "Salerno", "SS": "Sassari", "SV": "Savona", "SI": "Siena",
    "SR": "Siracusa",
    "SO": "Sondrio", "SU": "Sud Sardegna", "TA": "Taranto", "TE": "Teramo", "TR": "Terni",
    "TO": "Torino", "TP": "Trapani", "TN": "Trento", "TV": "Treviso", "TS": "Trieste", "UD": "Udine",
    "VA": "Varese", "VE": "Venezia", "VB": "Verbano-Cusio-Ossola", "VC": "Vercelli", "VR": "Verona",
    "VV": "Vibo Valentia", "VI": "Vicenza", "VT": "Viterbo", "BZ": "Bolzano-Bozen",
}

# Spanish provinces whose NUTS3 name isn't a simple normalized match (islands split 3-way).
ES_BALEARIC_ISLANDS = ["Mallorca", "Menorca", "Eivissa y Formentera"]


def normalize(name: str) -> str:
    name = name.split("/")[0]
    m = re.match(r"^(.*)\s*\((L?AS?|EL|LA|LOS)\)$", name.strip(), re.IGNORECASE)
    if m:
        name = f"{m.group(2)} {m.group(1)}"
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z]", "", name).lower()
    return name


def load_region_names():
    geo = json.loads(GEO_PATH.read_text())
    by_country = {"FR": {}, "ES": {}, "IT": {}}
    for feat in geo["features"]:
        nuts_id = feat["properties"]["id"]
        name = feat["properties"]["na"]
        country = nuts_id[:2]
        if country in by_country:
            by_country[country][normalize(name)] = (nuts_id, name)
    return by_country


def fetch_france(region_lookup):
    r = requests.get(FR_API, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    by_dept = {}
    points = []
    for rec in data["records"]:
        f = rec["fields"]
        dept = f.get("departement")
        if not dept:
            continue
        price = f.get("e10_prix") or f.get("sp95_prix")
        if not price:
            continue
        by_dept.setdefault(dept, {"e10": [], "sp95": []})
        if f.get("e10_prix"):
            by_dept[dept]["e10"].append(f["e10_prix"])
        elif f.get("sp95_prix"):
            by_dept[dept]["sp95"].append(f["sp95_prix"])
        coords = rec.get("geometry", {}).get("coordinates")
        if coords and len(coords) == 2:
            lon, lat = coords
            points.append((lat, lon, price))

    out = {}
    unmatched = []
    for dept, prices in by_dept.items():
        key = normalize(dept)
        match = region_lookup.get(key)
        if not match:
            unmatched.append(dept)
            continue
        nuts_id, name = match
        if prices["e10"]:
            price = mean(prices["e10"])
            fuel = "E10"
            n = len(prices["e10"])
        else:
            price = mean(prices["sp95"])
            fuel = "SP95 (E10 an diesen Stationen nicht gemeldet)"
            n = len(prices["sp95"])
        out[nuts_id] = {
            "name": name, "country": "FR",
            "price_eur_per_l": round(price, 4),
            "n_stations": n, "fuel_used": fuel,
        }
    return out, unmatched, points


def parse_es_float(s):
    if not s or not s.strip():
        return None
    return float(s.strip().replace(",", "."))


def fetch_spain(region_lookup):
    try:
        r = requests.get(ES_API, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.ConnectionError:
        data = json.loads(curl_get(ES_API, TIMEOUT))
    by_prov = {}
    points = []
    for rec in data["ListaEESSPrecio"]:
        prov = rec.get("Provincia", "").strip()
        if not prov:
            continue
        e10 = parse_es_float(rec.get("Precio Gasolina 95 E10", ""))
        e5 = parse_es_float(rec.get("Precio Gasolina 95 E5", ""))
        if e10 is None and e5 is None:
            continue
        by_prov.setdefault(prov, {"e10": [], "e5": []})
        if e10 is not None:
            by_prov[prov]["e10"].append(e10)
        if e5 is not None:
            by_prov[prov]["e5"].append(e5)
        try:
            lat = parse_es_float(rec.get("Latitud", ""))
            lon = parse_es_float(rec.get("Longitud (WGS84)", ""))
        except ValueError:
            lat = lon = None
        if lat is not None and lon is not None:
            points.append((lat, lon, e10 if e10 is not None else e5))

    out = {}
    unmatched = []
    for prov, prices in by_prov.items():
        if prices["e10"]:
            price, fuel, n = mean(prices["e10"]), "E10", len(prices["e10"])
        else:
            price, fuel, n = mean(prices["e5"]), "Super 95 E5 (E10 in Spanien kaum verfügbar)", len(prices["e5"])

        if prov.upper().startswith("BALEARS"):
            for island in ES_BALEARIC_ISLANDS:
                nuts_id, name = region_lookup[normalize(island)]
                out[nuts_id] = {
                    "name": name, "country": "ES",
                    "price_eur_per_l": round(price, 4),
                    "n_stations": n,
                    "fuel_used": fuel + " · Provinzdurchschnitt Balearen",
                }
            continue

        key = normalize(prov)
        match = region_lookup.get(key)
        if not match:
            unmatched.append(prov)
            continue
        nuts_id, name = match
        out[nuts_id] = {
            "name": name, "country": "ES",
            "price_eur_per_l": round(price, 4),
            "n_stations": n, "fuel_used": fuel,
        }
    return out, unmatched, points


def fetch_italy():
    rs = requests.get(IT_STATIONS_CSV, timeout=TIMEOUT)
    rs.raise_for_status()
    rp = requests.get(IT_PRICES_CSV, timeout=TIMEOUT)
    rp.raise_for_status()

    station_province = {}
    station_coords = {}
    lines = rs.content.decode("utf-8", errors="replace").splitlines()[1:]
    reader = csv.DictReader(lines, delimiter="|")
    for row in reader:
        prov = (row.get("Provincia") or "").strip().upper()
        impianto = row.get("idImpianto")
        if impianto and prov in IT_PROVINCE_NAME:
            station_province[impianto] = prov
        try:
            lat = float(row.get("Latitudine", "").strip())
            lon = float(row.get("Longitudine", "").strip())
            station_coords[impianto] = (lat, lon)
        except (ValueError, AttributeError):
            pass

    by_prov = {}
    points = []
    lines2 = rp.content.decode("utf-8", errors="replace").splitlines()[1:]
    reader2 = csv.DictReader(lines2, delimiter="|")
    for row in reader2:
        if (row.get("descCarburante") or "").strip() != "Benzina":
            continue
        impianto = row.get("idImpianto")
        prov = station_province.get(impianto)
        if not prov:
            continue
        try:
            price = float(row.get("prezzo", "").strip())
        except (ValueError, AttributeError):
            continue
        by_prov.setdefault(prov, []).append(price)
        coords = station_coords.get(impianto)
        if coords:
            points.append((coords[0], coords[1], price))

    out = {}
    for prov, prices in by_prov.items():
        name = IT_PROVINCE_NAME[prov]
        out[f"IT_{prov}"] = {
            "name": name, "country": "IT", "code": prov,
            "price_eur_per_l": round(mean(prices), 4),
            "n_stations": len(prices),
            "fuel_used": "Benzina (Super 95, meist E5 - kein separates E10-Label in Italien)",
        }
    return out, points


def match_italy(raw_by_code, region_lookup):
    out = {}
    unmatched = []
    for _, entry in raw_by_code.items():
        key = normalize(entry["name"])
        match = region_lookup.get(key)
        if not match:
            unmatched.append(entry["name"])
            continue
        nuts_id, name = match
        entry = dict(entry)
        entry["name"] = name
        del entry["code"]
        out[nuts_id] = entry
    return out, unmatched


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def compute_grid(country, points):
    if not points:
        return []
    lat_min, lat_max, lon_min, lon_max = COUNTRY_BBOX[country]
    pts = np.array(points, dtype=float)  # lat, lon, price
    lats, lons, prices = pts[:, 0], pts[:, 1], pts[:, 2]

    grid_lats = np.arange(lat_min, lat_max, GRID_SPACING_DEG)
    grid_lons = np.arange(lon_min, lon_max, GRID_SPACING_DEG)

    cells = []
    for glat in grid_lats:
        # cheap pre-filter by latitude band before the full haversine pass
        lat_band = np.abs(lats - glat) < (RADIUS_KM / 111.0) + 0.2
        if not lat_band.any():
            continue
        band_lats, band_lons, band_prices = lats[lat_band], lons[lat_band], prices[lat_band]
        for glon in grid_lons:
            d = haversine_km(glat, glon, band_lats, band_lons)
            mask = d <= RADIUS_KM
            n = int(mask.sum())
            if n < MIN_STATIONS_PER_CELL:
                continue
            cells.append((round(float(glat), 3), round(float(glon), 3),
                           round(float(band_prices[mask].mean()), 3), n))
    return cells


def main():
    lookup = load_region_names()

    fr, fr_unmatched, fr_points = fetch_france(lookup["FR"])
    print(f"FR: {len(fr)}/96 Departements befuellt, unmatched: {fr_unmatched}")

    es, es_unmatched, es_points = fetch_spain(lookup["ES"])
    print(f"ES: {len(es)}/52 Provinzen befuellt, unmatched: {es_unmatched}")

    it_raw, it_points = fetch_italy()
    it, it_unmatched = match_italy(it_raw, lookup["IT"])
    print(f"IT: {len(it)}/107 Provinzen befuellt, unmatched: {it_unmatched}")

    regions = {**fr, **es, **it}

    print("Berechne 10-km-Grid fuer Feinauflösung ...")
    raw_points = {"FR": fr_points, "ES": es_points, "IT": it_points}
    grid = {c: compute_grid(c, pts) for c, pts in raw_points.items()}
    for c, cells in grid.items():
        print(f"{c}: {len(cells)} Gitterzellen aus {len(raw_points[c])} Tankstellen")

    heatmap_out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "radius_km": RADIUS_KM,
        "grid_spacing_deg": GRID_SPACING_DEG,
        "note": "Preis je Gitterzelle = Durchschnitt aller Tankstellen im 10-km-Umkreis um den Zellmittelpunkt.",
        "cells": {
            c: [{"lat": la, "lon": lo, "price_eur_per_l": p, "n_stations": n} for la, lo, p, n in cells]
            for c, cells in grid.items()
        },
    }
    HEATMAP_OUT_PATH.write_text(json.dumps(heatmap_out, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {HEATMAP_OUT_PATH}")

    all_ids = {f["properties"]["id"] for f in json.loads(GEO_PATH.read_text())["features"]}
    missing = sorted(all_ids - regions.keys())
    print(f"Insgesamt {len(regions)}/{len(all_ids)} NUTS3-Regionen befuellt. Fehlend: {missing}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "FR": "https://data.economie.gouv.fr (prix-carburants, E10-Direktwert)",
            "ES": "https://sedeaplicaciones.minetur.gob.es (Gasolina 95 E5, E10 in ES kaum gelabelt)",
            "IT": "https://www.mimit.gov.it (Benzina/Super95, kein separates E10-Label)",
        },
        "regions": regions,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
