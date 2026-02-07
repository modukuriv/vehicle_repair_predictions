const form = document.getElementById("predict-form");
const resultsList = document.getElementById("results-list");
const statusEl = document.getElementById("status");
const yearSelect = document.getElementById("year");
const makeSelect = document.getElementById("make");
const modelSelect = document.getElementById("model");
const engineSelect = document.getElementById("engine");
const mileageSelect = document.getElementById("mileage");
const symptomsSelect = document.getElementById("symptoms");
const resetBtn = document.getElementById("reset-btn");

let vehicleData = null;
let availableYears = [];

function formatCurrency(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

function createResultCard(result) {
  const card = document.createElement("div");
  card.className = "result";

  const title = document.createElement("h3");
  title.textContent = result.part_name;

  const probability = document.createElement("div");
  probability.className = "probability";

  const label = document.createElement("div");
  label.textContent = `${result.probability_pct}% likelihood`;

  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("span");
  fill.style.width = `${Math.min(result.probability_pct, 100)}%`;
  bar.appendChild(fill);

  const meta = document.createElement("div");
  meta.className = "meta";
  const cost = `${formatCurrency(result.cost_range_usd.min)} - ${formatCurrency(result.cost_range_usd.max)}`;
  const parts = result.recommended_parts.join(", ");
  const signals = result.signals.length ? `Signals: ${result.signals.join(" | ")}` : "";
  meta.innerHTML = `DIY difficulty: <strong>${result.difficulty}</strong><br />Estimated cost: <strong>${cost}</strong><br />Parts: ${parts}<br />${signals}`;

  probability.appendChild(label);
  probability.appendChild(bar);

  card.appendChild(title);
  card.appendChild(probability);
  card.appendChild(meta);

  return card;
}

function setStatus(message) {
  statusEl.textContent = message;
}

function clearSelect(selectEl) {
  while (selectEl.firstChild) {
    selectEl.removeChild(selectEl.firstChild);
  }
}

function addOption(selectEl, label, value, selected = false) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  if (selected) {
    option.selected = true;
  }
  selectEl.appendChild(option);
}

function addPlaceholder(selectEl, label, selected = false, disabled = true) {
  const option = document.createElement("option");
  option.value = "";
  option.textContent = label;
  option.disabled = disabled;
  if (selected) {
    option.selected = true;
  }
  selectEl.appendChild(option);
}

function populateYears(years, selectedYear) {
  clearSelect(yearSelect);
  addPlaceholder(yearSelect, "Select year", !selectedYear, true);
  years.forEach((year) => {
    addOption(yearSelect, String(year), String(year), year === selectedYear);
  });
}

function populateMakes(makeEntries, defaultMake) {
  clearSelect(makeSelect);
  addPlaceholder(makeSelect, "Select make", !defaultMake, true);
  const entries = makeEntries || [];
  entries.forEach((make) => {
    addOption(makeSelect, make.make, make.make, make.make === defaultMake);
  });
}

function populateModels(models, defaultModel) {
  clearSelect(modelSelect);
  addPlaceholder(modelSelect, "Select model", !defaultModel, true);
  models.forEach((model) => {
    addOption(modelSelect, model.model, model.model, model.model === defaultModel);
  });
}

function populateEngines(engines, defaultEngine) {
  clearSelect(engineSelect);
  addPlaceholder(engineSelect, "Select engine (optional)", !defaultEngine, false);
  engines.forEach((engine) => {
    addOption(engineSelect, engine, engine, engine === defaultEngine);
  });
}

function clearDependentSelects() {
  clearSelect(modelSelect);
  addPlaceholder(modelSelect, "Select model", true, true);
  clearSelect(engineSelect);
  addPlaceholder(engineSelect, "Select engine (optional)", true, false);
}

function clearMakeModelEngine() {
  clearSelect(makeSelect);
  addPlaceholder(makeSelect, "Select make", true, true);
  clearDependentSelects();
}

function yearInRange(year, range) {
  if (!range || range.length < 2) return false;
  return year >= range[0] && year <= range[1];
}

function computeAvailableYears() {
  const years = new Set();
  vehicleData.makes.forEach((makeEntry) => {
    makeEntry.models.forEach((modelEntry) => {
      const range = modelEntry.year_range || [];
      if (range.length < 2) return;
      for (let year = range[0]; year <= range[1]; year += 1) {
        years.add(year);
      }
    });
  });
  return Array.from(years).sort((a, b) => b - a);
}

function getSelectedModel() {
  const make = makeSelect.value;
  const makeEntry = vehicleData.makes.find((entry) => entry.make === make);
  if (!makeEntry) return null;
  return makeEntry.models.find((model) => model.model === modelSelect.value) || null;
}

function getSelectedSymptoms() {
  return Array.from(symptomsSelect.selectedOptions).map((option) => option.value);
}

async function loadDropdownData() {
  const [vehiclesResponse, symptomsResponse] = await Promise.all([
    fetch("/api/vehicles"),
    fetch("/api/symptoms"),
  ]);

  vehicleData = await vehiclesResponse.json();
  const symptomsData = await symptomsResponse.json();

  availableYears = computeAvailableYears();
  populateYears(availableYears, null);
  clearMakeModelEngine();
  mileageSelect.selectedIndex = 0;

  clearSelect(symptomsSelect);
  symptomsData.symptoms.forEach((symptom) => {
    addOption(symptomsSelect, symptom, symptom, false);
  });

  setStatus("Select vehicle details to run a prediction.");
}

function refreshMakesForYear() {
  if (!yearSelect.value) {
    clearMakeModelEngine();
    return;
  }
  const year = Number(yearSelect.value);
  const makesForYear = vehicleData.makes.filter((makeEntry) =>
    makeEntry.models.some((modelEntry) => yearInRange(year, modelEntry.year_range))
  );
  populateMakes(makesForYear, null);
  clearDependentSelects();
}

function refreshModelsForMakeYear() {
  const year = Number(yearSelect.value);
  if (!year || !makeSelect.value) {
    clearDependentSelects();
    return;
  }
  const makeEntry = vehicleData.makes.find((entry) => entry.make === makeSelect.value);
  const models = makeEntry
    ? makeEntry.models.filter((modelEntry) => yearInRange(year, modelEntry.year_range))
    : [];
  populateModels(models, null);
  clearSelect(engineSelect);
  addPlaceholder(engineSelect, "Select engine (optional)", true, false);
}

function refreshEnginesForModel() {
  const modelEntry = getSelectedModel();
  if (!modelEntry) {
    clearSelect(engineSelect);
    addPlaceholder(engineSelect, "Select engine (optional)", true, false);
    return;
  }
  populateEngines(modelEntry.engines || [], null);
}

async function runPrediction(payload) {
  setStatus("Running prediction...");
  resultsList.innerHTML = "";

  try {
    const response = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`API error (${response.status})`);
    }

    const data = await response.json();
    data.results.forEach((result) => {
      resultsList.appendChild(createResultCard(result));
    });

    setStatus(`Updated ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    setStatus("Something went wrong. Check the API server.");
    console.error(error);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!form.reportValidity()) {
    return;
  }
  const payload = {
    year: Number(yearSelect.value),
    make: String(makeSelect.value),
    model: String(modelSelect.value),
    engine: String(engineSelect.value || ""),
    mileage: Number(mileageSelect.value),
    symptoms: getSelectedSymptoms().join(", "),
  };
  runPrediction(payload);
});

yearSelect.addEventListener("change", () => {
  refreshMakesForYear();
});

makeSelect.addEventListener("change", () => {
  refreshModelsForMakeYear();
});

modelSelect.addEventListener("change", () => {
  refreshEnginesForModel();
});

resetBtn.addEventListener("click", () => {
  populateYears(availableYears, null);
  clearMakeModelEngine();
  mileageSelect.selectedIndex = 0;
  Array.from(symptomsSelect.options).forEach((option) => {
    option.selected = false;
  });
  resultsList.innerHTML = "";
  setStatus("Form cleared.");
});

loadDropdownData()
  .then(() => {})
  .catch((error) => {
    console.error(error);
    setStatus("Failed to load dropdown data.");
  });
