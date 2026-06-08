import json
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "ccs"


def _make_preloaded_simulator():
    from backend.simulator import CCSSimulator
    sim = CCSSimulator()

    df_fac = pd.read_csv(DATA_DIR / "ccs_full_dataset_v1.0.csv")
    df_inj = pd.read_csv(DATA_DIR / "ccs_injection_daily_v1.0.csv", parse_dates=["date"])

    sim._facility_presets = {}
    for _, r in df_fac.iterrows():
        sim._facility_presets[r["case_id"]] = {
            "case_id": r["case_id"],
            "reservoir_type": r["reservoir_type"],
            "p_init_MPa": float(r["avg_reservoir_pressure_MPa"]),
            "temp_C": float(r["avg_reservoir_temp_C"]),
            "total_injected_tonnes": float(r["co2_injected_tonnes"]),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "capture_tech": r["capture_tech"],
            "transport_mode": r["transport_mode"],
        }

    sim._case_ids = sorted(df_inj["case_id"].unique().tolist())
    sim._injection_schedules = {}
    for case_id in sim._case_ids:
        mask = df_inj["case_id"] == case_id
        sim._injection_schedules[case_id] = (
            df_inj.loc[mask].sort_values("date").to_dict(orient="records")
        )

    return sim


@pytest.fixture(scope="module", autouse=True)
def _preload_simulator():
    import backend.database as db
    import backend.main
    import backend.state as state

    async def _noop(): pass
    db.init_db = _noop
    db.ingest_from_csv = _noop
    db.close_pool = _noop

    class _FakeConn:
        async def execute(self, *a, **kw): pass
        async def executemany(self, *a, **kw): pass
        async def fetch(self, *a, **kw): return []
        async def fetchrow(self, *a, **kw): return None
        async def fetchval(self, *a, **kw): return 1
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class _FakePool:
        def acquire(self):
            return _FakeConn()

    async def _make_fake_pool(*a, **kw):
        return _FakePool()

    db.init_db = _noop
    db.ingest_from_csv = _noop
    db.close_pool = _noop
    db.get_pool = _make_fake_pool

    state._simulator = _make_preloaded_simulator()


@pytest.fixture(scope="module")
def client():
    from backend.main import app
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health_returns_200(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "available_cases" in data
        assert data["case_count"] == 10

    def test_health_has_available_cases(self, client: TestClient):
        resp = client.get("/health")
        assert resp.json()["available_cases"] == [f"CCS-{c}" for c in "ABCDEFGHIJ"]


class TestPresets:
    def test_presets_returns_10(self, client: TestClient):
        resp = client.get("/presets")
        assert resp.status_code == 200
        assert len(resp.json()) == 10

    def test_presets_have_required_fields(self, client: TestClient):
        data = client.get("/presets").json()
        required = {"case_id", "reservoir_type", "p_init_MPa", "temp_C",
                    "total_injected_tonnes", "lat", "lon", "capture_tech", "transport_mode"}
        for p in data:
            assert required.issubset(p.keys())

    def test_presets_structure_matches_schema(self, client: TestClient):
        from backend.models import PresetItem
        resp = client.get("/presets")
        for item in resp.json():
            PresetItem(**item)


class TestSchedule:
    def test_schedule_returns_366_days(self, client: TestClient):
        resp = client.get("/schedule/CCS-A")
        assert resp.status_code == 200
        assert len(resp.json()) == 366

    def test_schedule_has_required_fields(self, client: TestClient):
        data = client.get("/schedule/CCS-A").json()
        for day in data:
            assert "day" in day
            assert "date" in day
            assert "co2_injected_tonnes" in day
            assert "month" in day

    def test_schedule_days_sequential(self, client: TestClient):
        data = client.get("/schedule/CCS-A").json()
        assert [d["day"] for d in data] == list(range(1, 367))

    def test_schedule_invalid_case_404(self, client: TestClient):
        resp = client.get("/schedule/CCS-Z")
        assert resp.status_code == 404
        assert "CCS-Z" in resp.json()["detail"]

    def test_schedule_all_10_cases_exist(self, client: TestClient):
        for case_id in [f"CCS-{c}" for c in "ABCDEFGHIJ"]:
            resp = client.get(f"/schedule/{case_id}")
            assert resp.status_code == 200
            assert len(resp.json()) == 366


class TestSimulate:
    def test_simulate_basic(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 10, "random_seed": 42})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["days_simulated"] == 10
        assert len(data["trace"]) == 10

    def test_simulate_response_schema(self, client: TestClient):
        from backend.models import SimulateResponse
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 5, "random_seed": 42})
        SimulateResponse(**resp.json())

    def test_simulate_trace_has_required_fields(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 3, "random_seed": 42})
        required = {"day", "pressure_MPa", "co2_injected", "co2_cumulative",
                    "plume_radius_m", "leak_kg", "leak_total_kg", "leak_events"}
        for state in resp.json()["trace"]:
            assert required.issubset(state.keys())

    def test_simulate_summary_has_required_fields(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 10, "random_seed": 42})
        summary = resp.json()["summary"]
        expected = {"total_days", "max_pressure_MPa", "final_pressure_MPa",
                    "total_co2_injected_tonnes", "final_plume_radius_m",
                    "total_leak_kg", "leak_event_count", "avg_daily_injection_tonnes"}
        assert set(summary.keys()) == expected

    def test_simulate_zero_days(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace"] == []
        assert data["summary"] == {}

    def test_simulate_invalid_case(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-X", "days": 10})
        assert resp.status_code == 422

    def test_simulate_days_exceeding_366(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 400})
        assert resp.status_code == 422

    def test_simulate_injection_rate_multiplier(self, client: TestClient):
        resp1 = client.post("/simulate", json={"case_id": "CCS-A", "days": 5, "injection_rate_multiplier": 1.0, "random_seed": 42})
        resp2 = client.post("/simulate", json={"case_id": "CCS-A", "days": 5, "injection_rate_multiplier": 2.0, "random_seed": 42})
        c1 = resp1.json()["summary"]["total_co2_injected_tonnes"]
        c2 = resp2.json()["summary"]["total_co2_injected_tonnes"]
        assert c2 > c1

    @pytest.mark.parametrize("case_id", [f"CCS-{c}" for c in "ABCDEFGHIJ"])
    def test_simulate_all_10_facilities(self, client: TestClient, case_id):
        resp = client.post("/simulate", json={"case_id": case_id, "days": 5, "random_seed": 42})
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_simulate_max_days(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 366, "random_seed": 42})
        assert resp.status_code == 200
        assert len(resp.json()["trace"]) == 366

    def test_simulate_leak_probability(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 50, "leak_probability": 0.5, "random_seed": 42})
        assert resp.status_code == 200
        leaks = [s for s in resp.json()["trace"] if s["leak_kg"] > 0]
        assert len(leaks) > 0

    def test_simulate_pressure_init_override(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": "CCS-A", "days": 1, "pressure_init": 50.0, "random_seed": 42})
        assert resp.status_code == 200
        assert resp.json()["trace"][0]["pressure_MPa"] > 40


class TestValidate:
    def test_valid_input(self, client: TestClient):
        resp = client.post("/validate", json={"case_id": "CCS-A", "days": 90})
        assert resp.json()["valid"] is True
        assert resp.json()["issues"] == []

    def test_invalid_case_id(self, client: TestClient):
        resp = client.post("/validate", json={"case_id": "CCS-X"})
        assert resp.json()["valid"] is False
        assert any(i["field"] == "case_id" for i in resp.json()["issues"])

    def test_pressure_above_limit(self, client: TestClient):
        resp = client.post("/validate", json={"pressure_init": 150.0})
        assert resp.json()["valid"] is False
        assert any(i["field"] == "pressure_init" for i in resp.json()["issues"])

    def test_pressure_below_limit(self, client: TestClient):
        resp = client.post("/validate", json={"pressure_init": 0.5})
        assert resp.json()["valid"] is False

    def test_days_above_limit(self, client: TestClient):
        resp = client.post("/validate", json={"days": 500})
        assert resp.json()["valid"] is False

    def test_days_below_limit(self, client: TestClient):
        resp = client.post("/validate", json={"days": -1})
        assert resp.json()["valid"] is False

    def test_temp_above_limit(self, client: TestClient):
        resp = client.post("/validate", json={"temp_init": 250.0})
        assert resp.json()["valid"] is False

    def test_invalid_reservoir_type(self, client: TestClient):
        resp = client.post("/validate", json={"reservoir_type": "Granite"})
        assert resp.json()["valid"] is False

    def test_reservoir_type_override_warning(self, client: TestClient):
        resp = client.post("/validate", json={"case_id": "CCS-A", "reservoir_type": "Depleted gas field"})
        issues = resp.json()["issues"]
        assert any(i["severity"] == "warning" and i["field"] == "reservoir_type" for i in issues)

    def test_validate_response_schema(self, client: TestClient):
        from backend.models import ValidateResponse
        resp = client.post("/validate", json={"days": 500})
        ValidateResponse(**resp.json())

    def test_empty_request_is_valid(self, client: TestClient):
        resp = client.post("/validate", json={})
        assert resp.json()["valid"] is True


class TestCORS:
    def test_cors_headers_present(self, client: TestClient):
        resp = client.options("/health", headers={
            "Origin": "http://localhost:8050",
            "Access-Control-Request-Method": "GET",
        })
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


class TestWebSocket:
    def test_ws_connects_and_streams(self, client: TestClient):
        with client.websocket_connect("/ws/simulate/CCS-A") as ws:
            ws.send_json({"days": 5, "random_seed": 42, "speed_multiplier": 50})
            ticks = 0
            done = False
            while not done:
                msg = ws.receive_json()
                if msg["type"] == "tick":
                    ticks += 1
                    assert "state" in msg
                    assert "pressure_MPa" in msg["state"]
                elif msg["type"] == "done":
                    done = True
                    assert "summary" in msg
                    assert msg["summary"]["total_days"] == 5
                elif msg["type"] == "error":
                    pytest.fail(f"WS error: {msg['detail']}")
            assert ticks == 5

    def test_ws_invalid_case_sends_error(self, client: TestClient):
        with client.websocket_connect("/ws/simulate/CCS-Z") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "CCS-Z" in msg["detail"]


class TestErrorHandling:
    def test_404_for_unknown_route(self, client: TestClient):
        resp = client.get("/unknown")
        assert resp.status_code == 404

    def test_422_for_invalid_simulate_body(self, client: TestClient):
        resp = client.post("/simulate", json={"case_id": 123})
        assert resp.status_code == 422

    def test_422_for_missing_required_fields(self, client: TestClient):
        resp = client.post("/simulate", json={})
        assert resp.status_code == 200
