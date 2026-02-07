# Vehicle Repair Probability MVP

A working MVP for a vehicle-specific repair probability and parts recommendation engine. This ships with a simple web UI, a FastAPI backend, and a heuristic scoring model. It is designed to be swapped with real data pipelines and models later.

## What This MVP Does
- Collects Year, Make, Model, Engine, Mileage, and optional Symptoms
- Ranks the top 5 likely part failures
- Outputs probability, recommended parts, DIY difficulty, and cost range
- Uses mileage bands, symptom keywords, and demo vehicle priors

## Run Locally
1. Create a virtual environment and install requirements.
2. Start the API server.
3. Open the UI in your browser.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000`.

## Model Logic (MVP)
- Base failure rate per part category
- Mileage band adjustment (0-50k, 50-100k, 100-150k, 150k+)
- Optional symptom keyword boosts
- Optional demo priors by make and model

The model is intentionally simple so it is easy to replace with real feature pipelines and ML models.

## Data Files
- `app/data/parts.json`
- `app/data/symptom_map.json`
- `app/data/vehicle_priors.json`

These are synthetic placeholders used for the MVP UI. Replace them with real datasets and model outputs for production.

## Next Steps
- Connect NHTSA complaints and recalls to populate real failure frequencies
- Add OBD-II code mappings to strengthen symptom-to-part ranking
- Swap in a lightweight gradient boosting model
- Replace the demo priors with learned priors from real data
