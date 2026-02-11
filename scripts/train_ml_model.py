#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
import joblib

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "app" / "data"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pick_year_for_model(make: str, model: str, vehicles_data: Dict) -> int | None:
    for make_entry in vehicles_data.get("makes", []):
        if make_entry.get("make", "").lower() != make:
            continue
        for model_entry in make_entry.get("models", []):
            if model_entry.get("model", "").lower() != model:
                continue
            year_range = model_entry.get("year_range", [])
            if len(year_range) == 2:
                return int((year_range[0] + year_range[1]) / 2)
    return None


def build_training_rows(priors: Dict, vehicles_data: Dict, weight_scale: float) -> Tuple[List[Dict], List[str], List[float]]:
    rows: List[Dict] = []
    labels: List[str] = []
    weights: List[float] = []

    # Vehicle-year priors are most specific
    year_keys = set()
    for key, bands in priors.get("vehicle_year", {}).items():
        try:
            year_str, make, model = key.split("|", 2)
        except ValueError:
            continue
        year = int(year_str)
        make = make.strip().lower()
        model = model.strip().lower()
        year_keys.add((make, model))
        for band, parts in bands.items():
            for part_id, score in parts.items():
                rows.append({
                    "year": year,
                    "make": make,
                    "model": model,
                    "mileage_band": band,
                })
                labels.append(part_id)
                weights.append(max(score * weight_scale, 0.001))

    # Add model priors when year-specific is missing
    for key, bands in priors.get("vehicle_model", {}).items():
        try:
            make, model = key.split("|", 1)
        except ValueError:
            continue
        make = make.strip().lower()
        model = model.strip().lower()
        if (make, model) in year_keys:
            continue
        year = pick_year_for_model(make, model, vehicles_data) or 2010
        for band, parts in bands.items():
            for part_id, score in parts.items():
                rows.append({
                    "year": year,
                    "make": make,
                    "model": model,
                    "mileage_band": band,
                })
                labels.append(part_id)
                weights.append(max(score * weight_scale, 0.001))

    # Add global priors as a fallback signal
    for band, parts in priors.get("global", {}).items():
        for part_id, score in parts.items():
            rows.append({
                "year": 2010,
                "make": "unknown",
                "model": "unknown",
                "mileage_band": band,
            })
            labels.append(part_id)
            weights.append(max(score * weight_scale, 0.001))

    return rows, labels, weights


def main() -> int:
    parser = argparse.ArgumentParser(description="Train ML model from public priors")
    parser.add_argument("--weight-scale", type=float, default=1000.0)
    parser.add_argument("--model-out", type=Path, default=DATA_DIR / "model.joblib")
    parser.add_argument("--meta-out", type=Path, default=DATA_DIR / "model_meta.json")
    args = parser.parse_args()

    priors_path = DATA_DIR / "priors.json"
    vehicles_path = DATA_DIR / "vehicles.json"

    if not priors_path.exists():
        raise SystemExit("Missing app/data/priors.json. Run scripts/build_public_data.py first.")

    priors = load_json(priors_path)
    vehicles = load_json(vehicles_path) if vehicles_path.exists() else {"makes": []}

    rows, labels, weights = build_training_rows(priors, vehicles, args.weight_scale)
    if not rows:
        raise SystemExit("No training rows generated. Check priors.json.")

    df = pd.DataFrame(rows)

    categorical = ["make", "model", "mileage_band"]
    numeric = ["year"]

    preprocessor = ColumnTransformer(
        [
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
            ("num", "passthrough", numeric),
        ]
    )

    classifier = LogisticRegression(
        max_iter=200,
        multi_class="multinomial",
        n_jobs=-1,
    )

    pipeline = Pipeline([
        ("prep", preprocessor),
        ("model", classifier),
    ])

    pipeline.fit(df, labels, model__sample_weight=weights)

    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, args.model_out)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "weight_scale": args.weight_scale,
        "classes": sorted(set(labels)),
        "source": "public priors",
    }

    with args.meta_out.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)

    print(f"Saved model to {args.model_out}")
    print(f"Saved metadata to {args.meta_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
