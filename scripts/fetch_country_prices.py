"""Extract latest Euro-super 95 (E10) prices per country from the EU Weekly Oil Bulletin.

Source: https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en
"Price developments 2005 onwards (xlsx)" -> sheet "Prices with taxes".
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_XLSX = ROOT / "data" / "raw" / "oil_bulletin_history.xlsx"
OUT_JSON = ROOT / "data" / "country_prices.json"

BULLETIN_PAGE = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
DOWNLOAD_URL = (
    "https://energy.ec.europa.eu/document/download/"
    "906e60ca-8b6a-44e7-8589-652854d2fd3f_en"
    "?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx"
)

# EU Oil Bulletin country code -> ISO/NUTS0 code used in our GeoJSON.
# The bulletin uses "GR" for Greece and "UK" for United Kingdom; GISCO/NUTS use "EL" and "UK".
CODE_MAP = {
    "GR": "EL",
}

COUNTRY_NAMES = {
    "AT": "Österreich", "BE": "Belgien", "BG": "Bulgarien", "CY": "Zypern",
    "CZ": "Tschechien", "DE": "Deutschland", "DK": "Dänemark", "EE": "Estland",
    "ES": "Spanien", "FI": "Finnland", "FR": "Frankreich", "EL": "Griechenland",
    "HR": "Kroatien", "HU": "Ungarn", "IE": "Irland", "IT": "Italien",
    "LT": "Litauen", "LU": "Luxemburg", "LV": "Lettland", "MT": "Malta",
    "NL": "Niederlande", "PL": "Polen", "PT": "Portugal", "RO": "Rumänien",
    "SE": "Schweden", "SI": "Slowenien", "SK": "Slowakei", "UK": "Vereinigtes Königreich",
}


def download_if_missing():
    if RAW_XLSX.exists():
        return
    RAW_XLSX.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(DOWNLOAD_URL, timeout=60)
    r.raise_for_status()
    RAW_XLSX.write_bytes(r.content)


def find_latest_complete_row(ws, country_cols):
    """Walk rows from the top (most recent) until we find one with enough countries populated."""
    rows = list(ws.iter_rows(min_row=4, values_only=True))
    for row in rows:
        date_val = row[0]
        if not isinstance(date_val, datetime):
            continue
        values = {code: row[col] for code, col in country_cols.items() if row[col] is not None}
        if len(values) >= len(country_cols) * 0.7:
            return date_val, values
    raise RuntimeError("No sufficiently complete row found in bulletin")


def main():
    download_if_missing()
    wb = openpyxl.load_workbook(RAW_XLSX, read_only=True, data_only=True)
    ws = wb["Prices with taxes"]
    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))

    country_cols = {}
    for idx, val in enumerate(header):
        if isinstance(val, str) and val.endswith("_price_with_tax_euro95"):
            code = val.split("_")[0]
            if code in ("EU", "EUR"):
                continue
            country_cols[code] = idx

    date_val, values = find_latest_complete_row(ws, country_cols)

    out = {
        "source": BULLETIN_PAGE,
        "fuel": "Euro-super 95 (E10)",
        "unit": "EUR / 1000 l",
        "date": date_val.strftime("%Y-%m-%d"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "countries": {},
    }
    for code, price in sorted(values.items()):
        mapped = CODE_MAP.get(code, code)
        out["countries"][mapped] = {
            "name": COUNTRY_NAMES.get(mapped, mapped),
            "price_eur_per_l": round(price / 1000, 4),
        }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_JSON} with {len(out['countries'])} countries, date={out['date']}")


if __name__ == "__main__":
    main()
