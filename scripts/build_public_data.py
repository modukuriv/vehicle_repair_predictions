#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "app" / "data"

NHTSA_COMPLAINTS_URL = "https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip"
EPA_VEHICLES_URL = "https://www.fueleconomy.gov/feg/epadata/vehicles.csv.zip"
OBD_GENERIC_URL = "https://raw.githubusercontent.com/todrobbins/dtcdb/master/generic.csv"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    print(f"Downloading {url} -> {dest}")
    with urllib.request.urlopen(url) as response, dest.open("wb") as handle:
        handle.write(response.read())


def load_parts(out_dir: Path) -> List[str]:
    parts_path = out_dir / "parts.json"
    with parts_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [part["id"] for part in data.get("parts", [])]


def mileage_band(miles: int) -> str:
    if miles < 50000:
        return "0_50"
    if miles < 100000:
        return "50_100"
    if miles < 150000:
        return "100_150"
    return "150_plus"


PART_KEYWORDS = [
    ("alternator", "alternator"),
    ("starter", "starter"),
    ("battery", "battery"),
    ("spark plug", "spark_plugs"),
    ("ignition coil", "ignition_coil"),
    ("misfire", "ignition_coil"),
    ("fuel pump", "fuel_pump"),
    ("injector", "fuel_injector"),
    ("maf", "maf_sensor"),
    ("mass air", "maf_sensor"),
    ("oxygen sensor", "oxygen_sensor"),
    ("o2 sensor", "oxygen_sensor"),
    ("catalytic", "catalytic_converter"),
    ("transmission", "transmission"),
    ("brake", "brake_pads"),
    ("rotor", "brake_pads"),
    ("strut", "suspension"),
    ("shock", "suspension"),
    ("control arm", "control_arms"),
    ("ball joint", "control_arms"),
    ("bearing", "wheel_bearing"),
    ("thermostat", "cooling_system"),
    ("radiator", "cooling_system"),
    ("water pump", "cooling_system"),
    ("coolant", "cooling_system"),
    ("belt", "belts_hoses"),
    ("hose", "belts_hoses"),
    ("a/c", "ac_compressor"),
    ("air conditioner", "ac_compressor"),
    ("power steering", "power_steering"),
    ("rack and pinion", "power_steering"),
    ("turbo", "turbocharger"),
]

COMPONENT_MAP = {
    "SERVICE BRAKES": "brake_pads",
    "BRAKES": "brake_pads",
    "SUSPENSION": "suspension",
    "STEERING": "power_steering",
    "POWER TRAIN": "transmission",
    "POWERTRAIN": "transmission",
    "TRANSMISSION": "transmission",
    "ELECTRICAL SYSTEM": "battery",
    "ENGINE AND ENGINE COOLING": "cooling_system",
    "ENGINE": "spark_plugs",
    "FUEL/PROPULSION SYSTEM": "fuel_pump",
    "FUEL SYSTEM": "fuel_pump",
    "AIR CONDITIONER": "ac_compressor",
    "EXHAUST SYSTEM": "catalytic_converter",
}


def map_complaint_to_part(compdesc: str, cdescr: str, valid_parts: List[str]) -> str | None:
    text = f"{compdesc} {cdescr}".lower()
    for keyword, part_id in PART_KEYWORDS:
        if keyword in text and part_id in valid_parts:
            return part_id

    comp_upper = compdesc.upper().strip()
    for key, part_id in COMPONENT_MAP.items():
        if key in comp_upper and part_id in valid_parts:
            return part_id
    return None


def iter_complaints(zip_path: Path) -> Iterable[Tuple[str, str, int, int, str, str]]:
    with zipfile.ZipFile(zip_path) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".txt")), None)
        if not name:
            raise RuntimeError("No .txt file found in complaints zip")
        with zf.open(name) as handle:
            text = io.TextIOWrapper(handle, encoding="latin-1", errors="ignore")
            reader = csv.reader(text, delimiter="\t")
            for row in reader:
                if len(row) < 46:
                    continue
                make = row[3].strip()
                model = row[4].strip()
                year_raw = row[5].strip()
                miles_raw = row[17].strip()
                compdesc = row[11].strip()
                cdescr = row[19].strip()
                prod_type = row[45].strip()
                if prod_type and prod_type != "V":
                    continue
                try:
                    year = int(year_raw)
                except ValueError:
                    continue
                if year < 1980 or year > 2030:
                    continue
                try:
                    miles = int(float(miles_raw)) if miles_raw else 0
                except ValueError:
                    miles = 0
                yield make, model, year, miles, compdesc, cdescr


def build_priors(complaints_zip: Path, out_dir: Path) -> Tuple[Dict, Counter]:
    valid_parts = load_parts(out_dir)

    vehicle_band_counts: Dict[Tuple[int, str, str, str], Counter] = defaultdict(Counter)
    global_band_counts: Dict[str, Counter] = defaultdict(Counter)
    vehicle_model_counts: Counter = Counter()

    total_rows = 0
    mapped_rows = 0

    for make, model, year, miles, compdesc, cdescr in iter_complaints(complaints_zip):
        total_rows += 1
        part_id = map_complaint_to_part(compdesc, cdescr, valid_parts)
        if not part_id:
            continue
        mapped_rows += 1
        band = mileage_band(miles) if miles > 0 else None
        if band:
            vehicle_band_counts[(year, make, model, band)][part_id] += 1
            global_band_counts[band][part_id] += 1
        vehicle_model_counts[(make, model)] += 1

    def normalize(counter: Counter) -> Dict[str, float]:
        total = sum(counter.values()) or 1
        return {key: value / total for key, value in counter.items()}

    global_priors = {band: normalize(counter) for band, counter in global_band_counts.items()}

    vehicle_priors = {}
    min_vehicle_band = 30
    for (year, make, model, band), counter in vehicle_band_counts.items():
        total = sum(counter.values())
        if total < min_vehicle_band:
            continue
        key = f"{year}|{make.lower()}|{model.lower()}"
        vehicle_priors.setdefault(key, {})[band] = normalize(counter)

    model_priors = {}
    model_band_counts: Dict[Tuple[str, str, str], Counter] = defaultdict(Counter)
    for (year, make, model, band), counter in vehicle_band_counts.items():
        model_band_counts[(make, model, band)].update(counter)

    for (make, model, band), counter in model_band_counts.items():
        total = sum(counter.values())
        if total < min_vehicle_band:
            continue
        key = f"{make.lower()}|{model.lower()}"
        model_priors.setdefault(key, {})[band] = normalize(counter)

    priors = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "nhtsa_complaints": NHTSA_COMPLAINTS_URL,
        },
        "global": global_priors,
        "vehicle_year": vehicle_priors,
        "vehicle_model": model_priors,
        "meta": {
            "total_rows": total_rows,
            "mapped_rows": mapped_rows,
        },
    }

    # Scale priors to additive weights for the MVP model
    scale = 0.3
    # Global is band -> part -> score
    for band, values in priors["global"].items():
        priors["global"][band] = {part: round(score * scale, 4) for part, score in values.items()}

    # Vehicle scopes are key -> band -> part -> score
    for scope in ("vehicle_year", "vehicle_model"):
        for key, bands in priors[scope].items():
            for band, values in bands.items():
                priors[scope][key][band] = {part: round(score * scale, 4) for part, score in values.items()}

    return priors, vehicle_model_counts


def build_vehicle_catalog(
    epa_zip: Path, out_dir: Path, top_models: List[Tuple[str, str]] | None
) -> Dict:
    top_model_set = None
    if top_models:
        top_model_set = {(make.lower().strip(), model.lower().strip()) for make, model in top_models}

    with zipfile.ZipFile(epa_zip) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if not name:
            raise RuntimeError("No CSV found in EPA zip")
        with zf.open(name) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8", errors="ignore")
            reader = csv.DictReader(text)
            catalog: Dict[str, Dict[str, Dict[str, set]]] = defaultdict(lambda: defaultdict(lambda: {
                "engines": set(),
                "years": set(),
            }))
            for row in reader:
                year_raw = (row.get("year") or "").strip()
                make = (row.get("make") or "").strip()
                model = (row.get("model") or "").strip()
                if not year_raw or not make or not model:
                    continue
                try:
                    year = int(year_raw)
                except ValueError:
                    continue

                if top_model_set and (make.lower(), model.lower()) not in top_model_set:
                    continue

                displ = (row.get("displ") or row.get("displacement") or "").strip()
                cyl = (row.get("cylinders") or row.get("cyl") or "").strip()
                fuel = (row.get("fuelType1") or row.get("fueltype1") or "").strip()
                atv = (row.get("atvType") or row.get("atvtype") or "").strip()

                engine_parts = []
                if displ:
                    engine_parts.append(f"{displ}L")
                if cyl:
                    engine_parts.append(f"{cyl}cyl")
                if atv:
                    engine_parts.append(atv)
                elif fuel:
                    engine_parts.append(fuel)
                engine = " ".join(engine_parts) if engine_parts else "Standard"

                entry = catalog[make][model]
                entry["years"].add(year)
                entry["engines"].add(engine)

    makes = []
    for make in sorted(catalog.keys()):
        models = []
        for model in sorted(catalog[make].keys()):
            years = sorted(catalog[make][model]["years"])
            engines = sorted(catalog[make][model]["engines"])
            models.append({
                "model": model,
                "engines": engines,
                "year_range": [years[0], years[-1]] if years else [1990, 2026],
            })
        makes.append({"make": make, "models": models})

    return {"makes": makes}


def build_obd_map(obd_csv: Path, out_dir: Path, valid_parts: List[str]) -> Dict:
    mapping: Dict[str, Dict] = {}
    with obd_csv.open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.reader(handle)
        _ = next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            code = row[0].strip()
            desc = row[1].strip()
            text = desc.lower()
            parts = []
            for keyword, part_id in PART_KEYWORDS:
                if keyword in text and part_id in valid_parts:
                    parts.append(part_id)
            if not parts:
                continue
            mapping[code] = {"description": desc, "parts": sorted(set(parts))}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": OBD_GENERIC_URL,
        "codes": mapping,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build public data priors for the MVP")
    parser.add_argument("--top-models", type=int, default=100)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    complaints_zip = RAW_DIR / "FLAT_CMPL.zip"
    epa_zip = RAW_DIR / "vehicles.csv.zip"
    obd_csv = RAW_DIR / "obd_generic.csv"

    if not args.skip_download:
        download(NHTSA_COMPLAINTS_URL, complaints_zip)
        download(EPA_VEHICLES_URL, epa_zip)
        download(OBD_GENERIC_URL, obd_csv)

    priors, model_counts = build_priors(complaints_zip, OUT_DIR)

    top_models = None
    if args.top_models > 0:
        top_models = [item[0] for item in model_counts.most_common(args.top_models)]
        if not top_models:
            top_models = None

    vehicles = build_vehicle_catalog(epa_zip, OUT_DIR, top_models)

    valid_parts = load_parts(OUT_DIR)
    obd_map = build_obd_map(obd_csv, OUT_DIR, valid_parts)

    priors_path = OUT_DIR / "priors.json"
    vehicles_path = OUT_DIR / "vehicles.json"
    obd_path = OUT_DIR / "obd_codes.json"

    with priors_path.open("w", encoding="utf-8") as handle:
        json.dump(priors, handle, indent=2, sort_keys=True)
    with vehicles_path.open("w", encoding="utf-8") as handle:
        json.dump(vehicles, handle, indent=2, sort_keys=True)
    with obd_path.open("w", encoding="utf-8") as handle:
        json.dump(obd_map, handle, indent=2, sort_keys=True)

    print(f"Wrote {priors_path}")
    print(f"Wrote {vehicles_path}")
    print(f"Wrote {obd_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
