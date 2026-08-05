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
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import requests

ROOT = Path(__file__).resolve().parent.parent
GEO_PATH = ROOT / "data" / "geo" / "regions_fr_es_it.geojson"
OUT_PATH = ROOT / "data" / "regional_prices.json"

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
    return out, unmatched


def parse_es_float(s):
    if not s or not s.strip():
        return None
    return float(s.strip().replace(",", "."))


def fetch_spain(region_lookup):
    r = requests.get(ES_API, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    by_prov = {}
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
    return out, unmatched


def fetch_italy():
    rs = requests.get(IT_STATIONS_CSV, timeout=TIMEOUT)
    rs.raise_for_status()
    rp = requests.get(IT_PRICES_CSV, timeout=TIMEOUT)
    rp.raise_for_status()

    station_province = {}
    lines = rs.content.decode("utf-8", errors="replace").splitlines()[1:]
    reader = csv.DictReader(lines, delimiter="|")
    for row in reader:
        prov = (row.get("Provincia") or "").strip().upper()
        impianto = row.get("idImpianto")
        if impianto and prov in IT_PROVINCE_NAME:
            station_province[impianto] = prov

    by_prov = {}
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

    out = {}
    for prov, prices in by_prov.items():
        name = IT_PROVINCE_NAME[prov]
        out[f"IT_{prov}"] = {
            "name": name, "country": "IT", "code": prov,
            "price_eur_per_l": round(mean(prices), 4),
            "n_stations": len(prices),
            "fuel_used": "Benzina (Super 95, meist E5 - kein separates E10-Label in Italien)",
        }
    return out


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


def main():
    lookup = load_region_names()

    fr, fr_unmatched = fetch_france(lookup["FR"])
    print(f"FR: {len(fr)}/96 Departements befuellt, unmatched: {fr_unmatched}")

    es, es_unmatched = fetch_spain(lookup["ES"])
    print(f"ES: {len(es)}/52 Provinzen befuellt, unmatched: {es_unmatched}")

    it_raw = fetch_italy()
    it, it_unmatched = match_italy(it_raw, lookup["IT"])
    print(f"IT: {len(it)}/107 Provinzen befuellt, unmatched: {it_unmatched}")

    regions = {**fr, **es, **it}

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
