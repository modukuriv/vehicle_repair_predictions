from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import os

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


def _load_optional_json(path: Path, default):
    if path.exists():
        return _load_json(path)
    return default


PARTS_DATA = _load_json(DATA_DIR / "parts.json")
SYMPTOM_RULES = _load_json(DATA_DIR / "symptom_map.json")
LEGACY_PRIORS = _load_optional_json(DATA_DIR / "vehicle_priors.json", {})
PUBLIC_PRIORS = _load_optional_json(DATA_DIR / "priors.json", {})
VEHICLES_DATA = _load_optional_json(DATA_DIR / "vehicles.json", {"makes": []})

PARTS = PARTS_DATA["parts"]
DIFFICULTY_LABELS = PARTS_DATA["difficulty_labels"]
MAKE_ADJUSTMENTS = LEGACY_PRIORS.get("make_adjustments", {})
MODEL_ADJUSTMENTS = LEGACY_PRIORS.get("model_adjustments", {})

USE_PUBLIC_PRIORS = bool(
    PUBLIC_PRIORS.get("global") or PUBLIC_PRIORS.get("vehicle_year") or PUBLIC_PRIORS.get("vehicle_model")
)

MODEL_PATH = DATA_DIR / "model.joblib"
MODEL_META_PATH = DATA_DIR / "model_meta.json"

MODEL = None
MODEL_META = {}
MODEL_LOAD_ERROR = None
try:
    import joblib
    import pandas as pd

    if MODEL_PATH.exists():
        MODEL = joblib.load(MODEL_PATH)
        if MODEL_META_PATH.exists():
            MODEL_META = _load_json(MODEL_META_PATH)
except Exception as exc:  # pragma: no cover - optional dependency
    MODEL_LOAD_ERROR = str(exc)


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


def _ml_probabilities(payload: PredictRequest):
    if not MODEL:
        return None
    try:
        features = {
            "year": payload.year,
            "make": payload.make.strip().lower(),
            "model": payload.model.strip().lower(),
            "mileage_band": _mileage_band(payload.mileage),
        }
        df = pd.DataFrame([features])
        probs = MODEL.predict_proba(df)[0]
        classes = MODEL.classes_
        return {cls: float(prob) for cls, prob in zip(classes, probs)}
    except Exception:
        return None


def _template_explanation(payload: PredictRequest, results: List[Dict]) -> str:
    parts = ", ".join([item["part_name"] for item in results[:3]])
    symptom_text = (payload.symptoms or "").strip()
    if symptom_text:
        return (
            f"Based on your {payload.year} {payload.make} {payload.model} at {payload.mileage:,} miles, "
            f"the most likely next issues are {parts}. Your symptoms ({symptom_text}) align with these categories."
        )
    return (
        f"Based on your {payload.year} {payload.make} {payload.model} at {payload.mileage:,} miles, "
        f"the most likely next issues are {parts}. "
        "These are common wear items in this mileage band."
    )


def _ollama_explanation(payload: PredictRequest, results: List[Dict]) -> str | None:
    import json as _json
    import urllib.request

    model_name = os.getenv("LLM_MODEL", "llama3.1")
    prompt = (
        "You are an auto repair assistant. Summarize why the following parts are likely next failures "
        "based on the vehicle and symptoms. Keep it to 3-4 sentences.\n\n"
        f"Vehicle: {payload.year} {payload.make} {payload.model} ({payload.engine}), "
        f"Mileage: {payload.mileage}\n"
        f"Symptoms: {payload.symptoms or 'none'}\n"
        f"Top parts: {[item['part_name'] for item in results]}\n"
    )

    body = _json.dumps({"model": model_name, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    return data.get("response")


def _generate_explanation(payload: PredictRequest, results: List[Dict]) -> str:
    provider = (os.getenv("LLM_PROVIDER") or "").lower().strip()
    if provider == "ollama":
        try:
            response = _ollama_explanation(payload, results)
            if response:
                return response.strip()
        except Exception:
            pass
    return _template_explanation(payload, results)


def _public_prior_weight(part_id: str, mileage_band: str, year: int, make_key: str, model_key: str) -> float:
    if not USE_PUBLIC_PRIORS:
        return 0.0
    weight = 0.0
    global_band = PUBLIC_PRIORS.get("global", {}).get(mileage_band, {})
    weight += float(global_band.get(part_id, 0.0))

    key_year = f"{year}|{make_key}|{model_key}"
    year_band = PUBLIC_PRIORS.get("vehicle_year", {}).get(key_year, {}).get(mileage_band, {})
    if year_band:
        weight += float(year_band.get(part_id, 0.0))
        return weight

    key_model = f"{make_key}|{model_key}"
    model_band = PUBLIC_PRIORS.get("vehicle_model", {}).get(key_model, {}).get(mileage_band, {})
    weight += float(model_band.get(part_id, 0.0))
    return weight


@app.post("/api/predict")
def predict(payload: PredictRequest):
    mileage_band = _mileage_band(payload.mileage)
    make_key = payload.make.strip().lower()
    model_key = payload.model.strip().lower()

    engine_flags = _engine_flags(payload.engine or "")
    symptom_text = _normalize(payload.symptoms or "")

    make_weights = MAKE_ADJUSTMENTS.get(make_key, {})
    model_weights = MODEL_ADJUSTMENTS.get(model_key, {})
    ml_probs = _ml_probabilities(payload)

    part_scores: Dict[str, float] = {}
    part_signals: Dict[str, List[str]] = {}

    for part in PARTS:
        part_id = part["id"]
        base = float(part["base_rate"])
        mileage_factor = float(part["mileage_factors"].get(mileage_band, 1.0))
        if ml_probs:
            score = float(ml_probs.get(part_id, 0.0))
        else:
            score = base * mileage_factor

        # Demo engine-based adjustments
        if part_id == "turbocharger":
            score *= 1.6 if engine_flags["turbo"] or engine_flags["supercharged"] else 0.4
        if engine_flags["diesel"] and part_id in {"fuel_injector", "fuel_pump"}:
            score *= 1.2
        if engine_flags["hybrid"] and part_id in {"starter", "alternator"}:
            score *= 0.7

        public_weight = 0.0
        if not ml_probs:
            public_weight = _public_prior_weight(part_id, mileage_band, payload.year, make_key, model_key)
            if USE_PUBLIC_PRIORS:
                score += public_weight
            else:
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
        if ml_probs:
            signals.append("ML model scored")
        elif USE_PUBLIC_PRIORS and public_weight > 0:
            signals.append("Public-data prior applied")
        elif make_weights or model_weights:
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

    explanation = _generate_explanation(payload, results)

    return {
        "inputs": payload.model_dump(),
        "results": results,
        "explanation": explanation,
        "meta": {
            "model_version": "ml-public-0.2" if ml_probs else "mvp-heuristic-0.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notes": "Probabilities are heuristic for MVP demo only.",
            "ml_enabled": bool(ml_probs),
            "llm_provider": os.getenv("LLM_PROVIDER") or "template",
            "model_meta": MODEL_META,
            "model_load_error": MODEL_LOAD_ERROR,
        },
    }
