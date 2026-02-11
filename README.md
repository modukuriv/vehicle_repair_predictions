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
- `app/data/vehicle_priors.json` (legacy demo priors)
- `app/data/priors.json` (public-data priors)

The legacy files are synthetic placeholders used for the MVP UI. Replace them with real datasets and model outputs for production.

## Public Data ETL (NHTSA + EPA + OBD-II)
This repo includes a public-data ETL that builds priors and dropdowns from free sources:
- NHTSA complaints (mileage + component)
- EPA fuel economy vehicle catalog
- OBD-II generic code list

Run:
```bash
python scripts/build_public_data.py --top-models 100
```

Outputs:
- `app/data/priors.json` (public-data priors)
- `app/data/vehicles.json` (dropdown catalog)
- `app/data/obd_codes.json` (DTC → part mapping for future use)

Note: The NHTSA complaints download is large. Expect a long first run.

## ML Model (Public Priors)
Train a lightweight ML model on the public priors:

```bash
python3 scripts/train_ml_model.py
```

This writes:
- `app/data/model.joblib`
- `app/data/model_meta.json`

If these files exist, the API will use the ML model for base probabilities.

## LLM Explanations (Optional)
By default, explanations are templated. To enable LLM explanations with a local Ollama model:

```bash
export LLM_PROVIDER=ollama
export LLM_MODEL=llama3.1
```

Make sure Ollama is running locally and has the model installed.

## Next Steps
- Connect NHTSA complaints and recalls to populate real failure frequencies
- Add OBD-II code mappings to strengthen symptom-to-part ranking
- Swap in a lightweight gradient boosting model
- Replace the demo priors with learned priors from real data
