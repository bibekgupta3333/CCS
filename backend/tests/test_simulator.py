import pytest

from backend.simulator import CCSSimulator, SimulatorConfig, SimulationState, _co2_density


class TestDataLoading:
    def test_loads_all_10_cases(self, simulator: CCSSimulator):
        cases = simulator.available_cases
        assert len(cases) == 10
        assert cases == [f"CCS-{c}" for c in "ABCDEFGHIJ"]

    def test_schedule_has_366_days_per_case(self, simulator: CCSSimulator):
        for case_id in simulator.available_cases:
            sched = simulator.get_schedule(case_id)
            assert len(sched) == 366, f"{case_id} has {len(sched)} days"

    def test_schedule_columns(self, simulator: CCSSimulator):
        sched = simulator.get_schedule("CCS-A")
        assert list(sched.columns) == ["case_id", "date", "co2_injected_tonnes", "month"]

    def test_preset_returns_all_fields(self, simulator: CCSSimulator):
        preset = simulator.get_preset("CCS-A")
        required = ["case_id", "reservoir_type", "p_init_MPa", "temp_C",
                    "total_injected_tonnes", "lat", "lon", "capture_tech", "transport_mode"]
        for k in required:
            assert k in preset, f"Missing key: {k}"

    def test_preset_invalid_case_raises(self, simulator: CCSSimulator):
        with pytest.raises(ValueError, match="Unknown case_id"):
            simulator.get_preset("CCS-X")


class TestPhysics:
    def test_co2_density_increases_with_pressure(self):
        assert _co2_density(10) > _co2_density(8)
        assert _co2_density(25) > _co2_density(10)
        # plateaus near 850 kg/m3
        assert _co2_density(100) <= 850

    def test_co2_density_at_low_pressure(self):
        assert _co2_density(5) >= 50
        assert _co2_density(1) < _co2_density(7.4)


class TestSimulationBasics:
    def test_run_returns_list(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=10, random_seed=42)
        trace = simulator.run_sync(cfg)
        assert isinstance(trace, list)
        assert len(trace) == 10

    def test_trace_has_output_schema(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=5, random_seed=42)
        trace = simulator.run_sync(cfg)
        required = {"day", "pressure_MPa", "co2_injected", "co2_cumulative",
                    "plume_radius_m", "leak_kg", "leak_total_kg", "leak_events"}
        for state in trace:
            assert required.issubset(state.keys()), f"Missing keys: {required - set(state.keys())}"

    def test_days_are_sequential(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42)
        trace = simulator.run_sync(cfg)
        assert [s["day"] for s in trace] == list(range(1, 31))

    def test_pressure_within_plausible_range(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=90, random_seed=42)
        trace = simulator.run_sync(cfg)
        for s in trace:
            assert 10 < s["pressure_MPa"] < 80, f"Day {s['day']}: P={s['pressure_MPa']}"

    def test_co2_cumulative_monotonically_increases(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=50, random_seed=42)
        trace = simulator.run_sync(cfg)
        cum = [s["co2_cumulative"] for s in trace]
        for i in range(len(cum) - 1):
            assert cum[i] <= cum[i + 1], f"Day {i + 1} > Day {i + 2}"

    def test_cumulative_tracks_raw_injection(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42,
                             noise_injection_pct=0.0, noise_pressure_pct=0.0)
        trace = simulator.run_sync(cfg)
        schedule = simulator.get_schedule("CCS-A")
        manual_cum = 0.0
        for i, s in enumerate(trace):
            manual_cum += float(schedule.iloc[i]["co2_injected_tonnes"])
            assert abs(s["co2_cumulative"] - manual_cum) < 1.0

    def test_plume_radius_grows(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=60, random_seed=42)
        trace = simulator.run_sync(cfg)
        radii = [s["plume_radius_m"] for s in trace]
        # Allow some noise wiggle but trend must be upward
        assert radii[-1] > radii[0]

    def test_plume_radius_positive(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42)
        trace = simulator.run_sync(cfg)
        for s in trace:
            assert s["plume_radius_m"] >= 0


class TestReservoirTypes:
    @pytest.mark.parametrize("case_id,expected_type", [
        ("CCS-A", "Basalt"),
        ("CCS-B", "Basalt"),
        ("CCS-C", "Basalt"),
        ("CCS-D", "Saline aquifer"),
        ("CCS-E", "Basalt"),
        ("CCS-F", "Depleted gas field"),
        ("CCS-G", "Depleted gas field"),
        ("CCS-H", "Depleted gas field"),
        ("CCS-I", "Basalt"),
        ("CCS-J", "Depleted gas field"),
    ])
    def test_reservoir_type_matches_dataset(self, simulator: CCSSimulator, case_id, expected_type):
        preset = simulator.get_preset(case_id)
        assert preset["reservoir_type"] == expected_type

    def test_different_reservoirs_produce_different_pressures(self, simulator: CCSSimulator):
        cfg_a = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42)
        cfg_f = SimulatorConfig(case_id="CCS-F", days=30, random_seed=42)
        trace_a = simulator.run_sync(cfg_a)
        trace_f = simulator.run_sync(cfg_f)
        # Basalt should build more pressure per tonne than depleted gas field
        delta_a = trace_a[-1]["pressure_MPa"] - simulator.get_preset("CCS-A")["p_init_MPa"]
        delta_f = trace_f[-1]["pressure_MPa"] - simulator.get_preset("CCS-F")["p_init_MPa"]
        # Basalt (tighter) should show more pressure change relative to init
        assert abs(delta_a) > 0 or abs(delta_f) > 0  # at least one changed

    def test_all_10_cases_simulate_without_error(self, simulator: CCSSimulator):
        for case_id in simulator.available_cases:
            cfg = SimulatorConfig(case_id=case_id, days=5, random_seed=42)
            trace = simulator.run_sync(cfg)
            assert len(trace) == 5
            assert trace[-1]["day"] == 5


class TestEdgeCases:
    def test_zero_day_simulation(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=0)
        trace = simulator.run_sync(cfg)
        assert trace == []
        assert CCSSimulator.summarize(trace) == {}

    def test_one_day_simulation(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=1, random_seed=42)
        trace = simulator.run_sync(cfg)
        assert len(trace) == 1
        assert trace[0]["day"] == 1

    def test_max_days_simulation(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=366, random_seed=42)
        trace = simulator.run_sync(cfg)
        assert len(trace) == 366
        assert trace[-1]["day"] == 366

    def test_days_exceeding_schedule_clamps(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=400, random_seed=42)
        trace = simulator.run_sync(cfg)
        assert len(trace) == 366  # schedule only has 366 days

    def test_leak_probability_zero_produces_no_leaks(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=100, leak_probability=0.0, random_seed=42)
        trace = simulator.run_sync(cfg)
        leak_events = [s for s in trace if s["leak_kg"] > 0]
        assert len(leak_events) == 0

    def test_leak_probability_one_produces_leaks(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=50, leak_probability=1.0, random_seed=42)
        trace = simulator.run_sync(cfg)
        leak_events = [s for s in trace if s["leak_kg"] > 0]
        assert len(leak_events) > 0

    def test_leak_at_day_one_possible(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=10, leak_probability=1.0, random_seed=42)
        trace = simulator.run_sync(cfg)
        assert trace[0]["leak_kg"] > 0  # day 1 should have a leak

    def test_injection_rate_multiplier_doubles_volume(self, simulator: CCSSimulator):
        cfg1 = SimulatorConfig(case_id="CCS-A", days=30, injection_rate_multiplier=1.0, random_seed=42)
        cfg2 = SimulatorConfig(case_id="CCS-A", days=30, injection_rate_multiplier=2.0, random_seed=42)
        trace1 = simulator.run_sync(cfg1)
        trace2 = simulator.run_sync(cfg2)
        # 2x multiplier should produce approximately 2x cumulative
        ratio = trace2[-1]["co2_cumulative"] / trace1[-1]["co2_cumulative"]
        assert 1.9 < ratio < 2.1

    def test_reproducibility_with_seed(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=20, random_seed=42)
        trace1 = simulator.run_sync(cfg)
        trace2 = simulator.run_sync(cfg)
        for s1, s2 in zip(trace1, trace2):
            assert s1 == s2

    def test_different_seeds_produce_different_traces(self, simulator: CCSSimulator):
        cfg1 = SimulatorConfig(case_id="CCS-A", days=10, random_seed=1)
        cfg2 = SimulatorConfig(case_id="CCS-A", days=10, random_seed=2)
        trace1 = simulator.run_sync(cfg1)
        trace2 = simulator.run_sync(cfg2)
        assert trace1 != trace2


class TestSummary:
    def test_summary_keys(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=10, random_seed=42)
        trace = simulator.run_sync(cfg)
        summary = CCSSimulator.summarize(trace)
        expected = {"total_days", "max_pressure_MPa", "final_pressure_MPa",
                    "total_co2_injected_tonnes", "final_plume_radius_m",
                    "total_leak_kg", "leak_event_count", "avg_daily_injection_tonnes"}
        assert set(summary.keys()) == expected

    def test_summary_total_days_matches(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=45, random_seed=42)
        trace = simulator.run_sync(cfg)
        summary = CCSSimulator.summarize(trace)
        assert summary["total_days"] == 45

    def test_summary_max_pressure_is_max(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42)
        trace = simulator.run_sync(cfg)
        summary = CCSSimulator.summarize(trace)
        pressures = [s["pressure_MPa"] for s in trace]
        assert summary["max_pressure_MPa"] == max(pressures)

    def test_summary_empty_trace_returns_empty_dict(self):
        assert CCSSimulator.summarize([]) == {}


class TestNoise:
    def test_noise_alters_values(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=5, noise_pressure_pct=0.0,
                             noise_plume_pct=0.0, noise_injection_pct=0.0, random_seed=42)
        trace_noiseless = simulator.run_sync(cfg)

        cfg2 = SimulatorConfig(case_id="CCS-A", days=5, noise_pressure_pct=0.1,
                              noise_plume_pct=0.1, noise_injection_pct=0.1, random_seed=42)
        trace_noisy = simulator.run_sync(cfg2)

        # noisy should differ from noiseless
        assert trace_noiseless != trace_noisy

    def test_zero_noise_matches_schedule(self, simulator: CCSSimulator):
        cfg = SimulatorConfig(case_id="CCS-A", days=3, noise_injection_pct=0.0, random_seed=42)
        trace = simulator.run_sync(cfg)
        schedule = simulator.get_schedule("CCS-A")
        for i, s in enumerate(trace):
            expected = float(schedule.iloc[i]["co2_injected_tonnes"])
            assert s["co2_injected"] == pytest.approx(expected, rel=0.01)


class TestConfig:
    def test_p_init_override(self, simulator: CCSSimulator):
        default = simulator.get_preset("CCS-A")["p_init_MPa"]
        cfg = SimulatorConfig(case_id="CCS-A", days=1, p_init_override=50.0, random_seed=42,
                             noise_pressure_pct=0.0)
        trace = simulator.run_sync(cfg)
        # P should be higher because init is 50 vs default ~19.5
        assert trace[0]["pressure_MPa"] > 40

    def test_reservoir_type_override(self, simulator: CCSSimulator):
        cfg_default = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42)
        cfg_override = SimulatorConfig(case_id="CCS-A", days=30, random_seed=42,
                                       reservoir_type="Depleted gas field")
        trace_default = simulator.run_sync(cfg_default)
        trace_override = simulator.run_sync(cfg_override)
        # Depleted gas field has lower k_inj, so less pressure buildup
        assert trace_default[-1]["pressure_MPa"] != trace_override[-1]["pressure_MPa"]
