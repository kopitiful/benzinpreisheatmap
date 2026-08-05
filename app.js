// Benzinpreis-Heatmap Europa (E10) — Leaflet choropleth, Länder- + Regionsebene.

const RAMP_STOPS = [
  "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
  "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
  "#184f95", "#104281", "#0d366b",
];
const NODATA_COLOR = "#dedcd4";

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex([r, g, b]) {
  return "#" + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
}

function makeColorScale(min, max) {
  return function (value) {
    if (value == null || Number.isNaN(value)) return NODATA_COLOR;
    let t = (value - min) / (max - min);
    t = Math.max(0, Math.min(1, t));
    const pos = t * (RAMP_STOPS.length - 1);
    const i0 = Math.floor(pos);
    const i1 = Math.min(RAMP_STOPS.length - 1, i0 + 1);
    const f = pos - i0;
    const c0 = hexToRgb(RAMP_STOPS[i0]);
    const c1 = hexToRgb(RAMP_STOPS[i1]);
    const mixed = c0.map((v, i) => v + (c1[i] - v) * f);
    return rgbToHex(mixed);
  };
}

const REGION_COUNTRIES = new Set(["FR", "ES", "IT"]);
const DOMAIN = [1.30, 2.50]; // EUR/L, fixed so country + region views share one scale
const colorScale = makeColorScale(DOMAIN[0], DOMAIN[1]);

document.getElementById("scale-min").textContent = DOMAIN[0].toFixed(2) + " €";
document.getElementById("scale-max").textContent = DOMAIN[1].toFixed(2) + " €";

const map = L.map("map", {
  zoomControl: false,
  attributionControl: false,
  minZoom: 3,
  maxZoom: 8,
  worldCopyJump: false,
  maxBounds: [[24, -35], [73, 48]],
  maxBoundsViscosity: 0.7,
}).setView([54, 15], 4);

L.control.zoom({ position: "topright" }).addTo(map);

let countryLayer = null;
let regionLayer = null;
let countryPrices = null;
let regionPrices = null;

const backBtn = document.getElementById("back-btn");
const metaText = document.getElementById("meta-text");

function fmtPrice(v) {
  return v == null ? "keine Daten" : v.toFixed(3).replace(".", ",") + " €/l";
}

function styleCountry(feature) {
  const code = feature.properties.id;
  const entry = countryPrices.countries[code];
  const price = entry ? entry.price_eur_per_l : null;
  return {
    fillColor: colorScale(price),
    fillOpacity: 0.92,
    color: "#ffffff",
    weight: 0.8,
    opacity: 0.9,
  };
}

function styleRegion(feature) {
  const code = feature.properties.id;
  const entry = regionPrices.regions[code];
  const price = entry ? entry.price_eur_per_l : null;
  return {
    fillColor: colorScale(price),
    fillOpacity: 0.92,
    color: "#ffffff",
    weight: 0.6,
    opacity: 0.85,
  };
}

function highlightStyle() {
  return { weight: 2.2, color: "#0b0b0b", fillOpacity: 1 };
}

function bindCountryInteractions(layer) {
  layer.on("mouseover", function () {
    this.setStyle(highlightStyle());
    this.bringToFront();
  });
  layer.on("mouseout", function () {
    countryLayer.resetStyle(this);
  });
  const code = layer.feature.properties.id;
  const entry = countryPrices.countries[code];
  const name = entry ? entry.name : layer.feature.properties.na;
  const price = entry ? entry.price_eur_per_l : null;
  layer.bindTooltip(
    `<b>${name}</b><br><b>${fmtPrice(price)}</b>` +
      (REGION_COUNTRIES.has(code) ? `<div class="sub">Klicken für Regionen</div>` : ""),
    { className: "price-tooltip", sticky: true }
  );
  if (REGION_COUNTRIES.has(code)) {
    layer.on("click", () => showRegions(code, layer));
  }
}

function bindRegionInteractions(layer) {
  layer.on("mouseover", function () {
    this.setStyle(highlightStyle());
    this.bringToFront();
  });
  layer.on("mouseout", function () {
    regionLayer.resetStyle(this);
  });
  const code = layer.feature.properties.id;
  const entry = regionPrices.regions[code];
  const name = entry ? entry.name : layer.feature.properties.na;
  const price = entry ? entry.price_eur_per_l : null;
  const sub = entry && entry.fuel_used && entry.fuel_used !== "E10" ? `<div class="sub">${entry.fuel_used}</div>` : "";
  layer.bindTooltip(`<b>${name}</b><br><b>${fmtPrice(price)}</b>${sub}`, {
    className: "price-tooltip",
    sticky: true,
  });
}

async function showRegions(countryCode, clickedLayer) {
  if (!regionPrices) {
    const res = await fetch("data/regional_prices.json");
    regionPrices = await res.json();
  }
  if (!window._regionsGeoJson) {
    const res = await fetch("data/geo/regions_fr_es_it.geojson");
    window._regionsGeoJson = await res.json();
  }
  const full = window._regionsGeoJson;
  const subset = {
    type: "FeatureCollection",
    features: full.features.filter((f) => f.properties.id.startsWith(countryCode)),
  };

  map.removeLayer(countryLayer);
  regionLayer = L.geoJSON(subset, { style: styleRegion });
  regionLayer.eachLayer(bindRegionInteractions);
  regionLayer.addTo(map);
  map.fitBounds(regionLayer.getBounds(), { padding: [30, 30] });

  backBtn.style.display = "inline-block";
}

function showCountries() {
  if (regionLayer) {
    map.removeLayer(regionLayer);
    regionLayer = null;
  }
  countryLayer.addTo(map);
  map.setView([54, 15], 4);
  backBtn.style.display = "none";
}

backBtn.addEventListener("click", showCountries);

async function init() {
  const [countriesRes, geoRes] = await Promise.all([
    fetch("data/country_prices.json"),
    fetch("data/geo/countries.geojson"),
  ]);
  countryPrices = await countriesRes.json();
  const geo = await geoRes.json();

  countryLayer = L.geoJSON(geo, { style: styleCountry });
  countryLayer.eachLayer(bindCountryInteractions);
  countryLayer.addTo(map);

  metaText.textContent = `Stand ${countryPrices.date} · Quelle: EU Weekly Oil Bulletin`;
  buildTable();
}

function buildTable() {
  const tbody = document.getElementById("table-body");
  const rows = Object.entries(countryPrices.countries)
    .map(([code, v]) => ({ code, ...v }))
    .sort((a, b) => a.price_eur_per_l - b.price_eur_per_l);
  for (const r of rows) {
    const tr = document.createElement("tr");
    const tdName = document.createElement("td");
    tdName.textContent = r.name;
    const tdPrice = document.createElement("td");
    tdPrice.className = "price";
    tdPrice.textContent = fmtPrice(r.price_eur_per_l);
    tr.append(tdName, tdPrice);
    tbody.appendChild(tr);
  }
}

document.getElementById("table-toggle").addEventListener("click", () => {
  document.getElementById("table-panel").classList.toggle("visible");
});

init();
