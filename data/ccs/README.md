# SCCS-MRV CCS Dataset

Synthetic Carbon Capture & Storage dataset from [SCCS-MRV v1.5](https://github.com/muktevisree/SCCS-MRV). Licensed CC-BY 4.0.

## Files

| File | Rows | Columns | Description |
|---|---|---|---|
| `ccs_injection_daily_v1.0.csv` | 3,660 | 4 | Daily CO2 injected per facility across 2024 |
| `ccs_injection_monthly_v1.0.csv` | 120 | 3 | Monthly CO2 totals per facility |
| `ccs_full_dataset_v1.0.csv` | 10 | 26 | Facility-level properties: reservoir, leaks, capture, transport |

## Context

This dataset models 10 synthetic CO2 capture and storage facilities over calendar year 2024. Each facility captures CO2 at a power plant, transports it via pipeline to an injection well, and stores it in a subsurface reservoir. Monitoring data includes leak events, reservoir pressure/temperature, and methane emissions.

**Simulator relevance:** A CCS simulator needs three inputs from data:
1. **Injection schedule** — `ccs_injection_daily` provides 366 days of per-facility injection rates (boundary condition)
2. **Reservoir initial conditions** — `ccs_full_dataset` provides pressure, temperature, and rock type per reservoir
3. **Validation targets** — `ccs_injection_monthly` provides monthly totals to benchmark simulator output against

## Coverage

- **Facilities:** 10 (CCS-A through CCS-J)
- **Date range:** 2024-01-01 to 2024-12-31
- **Capture technologies:** Amine, MDEA, MEA, PSA, Selexol
- **Reservoir types:** Basalt, Depleted gas field, Saline aquifer
- **Transport:** Pipeline only, lengths range ~161–270 km
- **Monitoring:** Pressure gauges, microseismic, 4D seismic, InSAR

## Daily Injection (`ccs_injection_daily_v1.0.csv`)

| Column | Type | Description |
|---|---|---|
| `case_id` | string | Facility code (CCS-A, CCS-B, ... CCS-J) |
| `date` | date | Calendar day (YYYY-MM-DD) |
| `co2_injected_tonnes` | float | CO2 injected that day in metric tonnes |
| `month` | string | YYYY-MM for grouping |

## Monthly Injection (`ccs_injection_monthly_v1.0.csv`)

| Column | Type | Description |
|---|---|---|
| `case_id` | string | Facility code |
| `month` | string | YYYY-MM |
| `co2_injected_tonnes` | float | Total CO2 injected that month |

## Facility Annual (`ccs_full_dataset_v1.0.csv`)

| Column | Type | Description |
|---|---|---|
| `record_id` | string | Unique row identifier |
| `case_id` | string | Facility case code |
| `facility_id` | string | Plant identifier (PLANT-00 through PLANT-09) |
| `country_code` | string | Country (all US) |
| `lat`, `lon` | float | Facility coordinates |
| `capture_tech` | string | CO2 separation technology |
| `co2_captured_tonnes` | float | Gross CO2 captured at plant |
| `capture_energy_MWh` | float | Energy consumed for capture |
| `transport_mode` | string | CO2 transport method (all Pipeline) |
| `pipeline_length_km` | float | Distance from plant to well |
| `transport_loss_tonnes` | float | CO2 lost during transport |
| `well_id` | string | Injection well identifier |
| `injection_start_date` | date | First injection day |
| `injection_end_date` | date | Last injection day |
| `co2_injected_tonnes` | float | Gross CO2 injected into reservoir |
| `co2_produced_tonnes` | float | CO2 produced back (all zero) |
| `reservoir_type` | string | Basalt / Depleted gas field / Saline aquifer |
| `avg_reservoir_pressure_MPa` | float | Mean reservoir pressure in MPa |
| `avg_reservoir_temp_C` | float | Mean reservoir temperature in Celsius |
| `mmv_methods` | string | Monitoring, Measurement, & Verification methods |
| `leak_events_count` | int | Number of leak incidents |
| `leak_mass_tonnes` | float | Total mass leaked |
| `ch4_emissions_tonnes` | float | Methane emissions |
| `ogmp_source_category` | string | OGMP 2.0 source category (ST/TR/PR) |
| `co2_net_stored_tonnes` | float | Net CO2 stored = injected - leaks - losses |

## Quick Start (Python)

```python
import pandas as pd

daily    = pd.read_csv("data/ccs/ccs_injection_daily_v1.0.csv", parse_dates=["date"])
monthly  = pd.read_csv("data/ccs/ccs_injection_monthly_v1.0.csv")
facility = pd.read_csv("data/ccs/ccs_full_dataset_v1.0.csv")

# Injection schedule for a single facility (simulator input)
schedule = daily[daily["case_id"] == "CCS-A"].set_index("date")["co2_injected_tonnes"]

# Reservoir initial conditions
props = facility[facility["case_id"] == "CCS-A"].iloc[0]
print(f"Pressure: {props['avg_reservoir_pressure_MPa']} MPa")
print(f"Temp:     {props['avg_reservoir_temp_C']} C")
print(f"Rock:     {props['reservoir_type']}")

# Validate monthly totals against reference
ref = monthly[monthly["case_id"] == "CCS-A"]
# your_simulated_monthly == ref["co2_injected_tonnes"]
```

## Source

**Dataset:** SCCS–MRV v1.5  
**Authors:** Sreekanth Muktevi, Yogesh Nagpal, Rajesh Leela Thotakura, Jyotsna Muktevi  
**DOI:** [10.5281/zenodo.17003094](https://doi.org/10.5281/zenodo.17003094)  
**GitHub:** [muktevisree/SCCS-MRV](https://github.com/muktevisree/SCCS-MRV)  
**License:** [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)

## See Also

- Notebook: `notebooks/05_ccs_data_exploration.ipynb` — visual exploration of all three files
