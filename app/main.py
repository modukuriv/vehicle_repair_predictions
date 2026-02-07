from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


PARTS_DATA = _load_json(DATA_DIR / "parts.json")
SYMPTOM_RULES = _load_json(DATA_DIR / "symptom_map.json")
PRIORS = _load_json(DATA_DIR / "vehicle_priors.json")
VEHICLES_DATA = _load_json(DATA_DIR / "vehicles.json")

PARTS = PARTS_DATA["parts"]
DIFFICULTY_LABELS = PARTS_DATA["difficulty_labels"]
MAKE_ADJUSTMENTS = PRIORS.get("make_adjustments", {})
MODEL_ADJUSTMENTS = PRIORS.get("model_adjustments", {})


class PredictRequest(BaseModel):
    year: int = Field(..., ge=1980, le=2030)
    make: str
    model: str
    engine: Optional[str] = ""
    mileage: int = Field(..., ge=0, le=400000)
    symptoms: Optional[str] = ""


app = FastAPI(title="Vehicle Repair Probability MVP", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/vehicles")
def vehicles():
    return VEHICLES_DATA


@app.get("/api/symptoms")
def symptoms():
    return {"symptoms": [rule["keyword"] for rule in SYMPTOM_RULES]}


def _mileage_band(mileage: int) -> str:
    if mileage < 50000:
        return "0_50"
    if mileage < 100000:
        return "50_100"
    if mileage < 150000:
        return "100_150"
    return "150_plus"


def _engine_flags(engine: str) -> Dict[str, bool]:
    text = (engine or "").lower()
    return {
        "turbo": "turbo" in text or "t/t" in text,
        "diesel": "diesel" in text,
        "hybrid": "hybrid" in text,
        "supercharged": "supercharg" in text,
    }


def _normalize(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.lower()).split())


@app.post("/api/predict")
def predict(payload: PredictRequest):
    mileage_band = _mileage_band(payload.mileage)
    make_key = payload.make.strip().lower()
    model_key = payload.model.strip().lower()

    engine_flags = _engine_flags(payload.engine or "")
    symptom_text = _normalize(payload.symptoms or "")

    make_weights = MAKE_ADJUSTMENTS.get(make_key, {})
    model_weights = MODEL_ADJUSTMENTS.get(model_key, {})

    part_scores: Dict[str, float] = {}
    part_signals: Dict[str, List[str]] = {}

    for part in PARTS:
        part_id = part["id"]
        base = float(part["base_rate"])
        mileage_factor = float(part["mileage_factors"].get(mileage_band, 1.0))
        score = base * mileage_factor

        # Demo engine-based adjustments
        if part_id == "turbocharger":
            score *= 1.6 if engine_flags["turbo"] or engine_flags["supercharged"] else 0.4
        if engine_flags["diesel"] and part_id in {"fuel_injector", "fuel_pump"}:
            score *= 1.2
        if engine_flags["hybrid"] and part_id in {"starter", "alternator"}:
            score *= 0.7

        # Demo vehicle priors (small offsets for MVP UI)
        score += float(make_weights.get(part_id, 0.0))
        score += float(model_weights.get(part_id, 0.0))

        matched_keywords: List[str] = []
        if symptom_text:
            for rule in SYMPTOM_RULES:
                keyword = rule["keyword"]
                if keyword in symptom_text:
                    weight = rule.get("weights", {}).get(part_id)
                    if weight:
                        score += float(weight)
                        matched_keywords.append(keyword)

        score = max(score, 0.001)
        part_scores[part_id] = score

        signals: List[str] = []
        if matched_keywords:
            signals.append("Matched symptoms: " + ", ".join(sorted(set(matched_keywords))[:3]))
        band_label = {
            "0_50": "0-50k",
            "50_100": "50-100k",
            "100_150": "100-150k",
            "150_plus": "150k+",
        }.get(mileage_band, mileage_band)
        signals.append(f"Mileage band: {band_label}")
        if make_weights or model_weights:
            signals.append("Vehicle prior applied (demo)")
        part_signals[part_id] = signals

    total_score = sum(part_scores.values()) or 1.0

    ranked = sorted(PARTS, key=lambda p: part_scores[p["id"]], reverse=True)
    top = ranked[:5]

    results = []
    for part in top:
        part_id = part["id"]
        prob = part_scores[part_id] / total_score
        results.append(
            {
                "part_id": part_id,
                "part_name": part["name"],
                "probability": round(prob, 4),
                "probability_pct": round(prob * 100, 1),
                "difficulty": DIFFICULTY_LABELS.get(str(part["difficulty"]), "Moderate"),
                "cost_range_usd": {
                    "min": part["cost_usd_min"],
                    "max": part["cost_usd_max"],
                },
                "recommended_parts": part["recommended_parts"],
                "signals": part_signals.get(part_id, []),
            }
        )

    return {
        "inputs": payload.model_dump(),
        "results": results,
        "meta": {
            "model_version": "mvp-heuristic-0.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notes": "Probabilities are heuristic for MVP demo only.",
        },
    }
