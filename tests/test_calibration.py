"""Testy core/calibration.py — kalibracja z licznika fizycznego."""
import pytest

from app.core.calibration import (
    CalibrationResult,
    apply_calibration,
    compute_calibration,
)


class TestApplyCalibration:
    """Testy apply_calibration — model addytywny + multiplikatywny."""

    def test_no_calibration(self) -> None:
        """Brak kalibracji: hidden=0, factor=1.0 → bez zmian."""
        assert apply_calibration(10.0, 24.0, hidden_power_w=0, sensor_factor=1.0) == 10.0

    def test_hidden_power_only(self) -> None:
        """Tylko hidden_power: 20W × 24h = 0.48 kWh dodane."""
        result = apply_calibration(10.0, 24.0, hidden_power_w=20, sensor_factor=1.0)
        expected = 10.0 + 20 * 24 / 1000  # 10.48
        assert abs(result - expected) < 0.001

    def test_sensor_factor_only(self) -> None:
        """Tylko sensor_factor: 10 × 1.05 = 10.5."""
        result = apply_calibration(10.0, 24.0, hidden_power_w=0, sensor_factor=1.05)
        assert abs(result - 10.5) < 0.001

    def test_both_combined(self) -> None:
        """Oba parametry: 10 × 1.05 + 20W × 24h/1000 = 10.5 + 0.48 = 10.98."""
        result = apply_calibration(10.0, 24.0, hidden_power_w=20, sensor_factor=1.05)
        expected = 10.0 * 1.05 + 20 * 24 / 1000
        assert abs(result - expected) < 0.001

    def test_summer_scenario(self) -> None:
        """Lato: 2.5 kWh pracy, 24h, hidden=20W → 2.5 + 0.48 = 2.98."""
        result = apply_calibration(2.5, 24.0, hidden_power_w=20)
        assert abs(result - 2.98) < 0.01

    def test_winter_scenario(self) -> None:
        """Zima: 25 kWh pracy, 24h, hidden=20W → 25 + 0.48 = 25.48."""
        result = apply_calibration(25.0, 24.0, hidden_power_w=20)
        assert abs(result - 25.48) < 0.01

    def test_hidden_impact_constant_regardless_of_load(self) -> None:
        """Wpływ hidden jest stały (0.48 kWh/dobę), niezależnie od obciążenia."""
        r_summer = apply_calibration(2.5, 24.0, hidden_power_w=20) - 2.5
        r_winter = apply_calibration(25.0, 24.0, hidden_power_w=20) - 25.0
        assert abs(r_summer - r_winter) < 0.001  # oba = 0.48 kWh


class TestComputeCalibration:
    """Testy compute_calibration — wyznaczanie parametrów z odczytów."""

    def test_no_readings(self) -> None:
        """Brak odczytów → factor=1.0, hidden=0, confidence=none."""
        result = compute_calibration([], lambda a, b: 0)
        assert result.hidden_power_w == 0.0
        assert result.sensor_factor == 1.0
        assert result.confidence == "none"

    def test_one_reading(self) -> None:
        """Jeden odczyt → za mało, confidence=none."""
        result = compute_calibration([(1000, 100.0)], lambda a, b: 0)
        assert result.confidence == "none"

    def test_simple_hidden_power(self) -> None:
        """Dwa odczyty, 24h apart, meter=3kWh, sensor=2.5kWh → hidden ~20.8W."""
        ts1, ts2 = 0, 86400  # 24h
        readings = [(ts1, 100.0), (ts2, 103.0)]  # 3 kWh w 24h
        # Sensor widzi 2.5 kWh
        result = compute_calibration(readings, lambda a, b: 2.5)
        # hidden = (3.0 - 2.5) / 24 × 1000 = 20.83 W
        assert 15 < result.hidden_power_w < 25
        assert result.confidence == "low"  # 1 para

    def test_multiple_readings_medium_confidence(self) -> None:
        """4 odczyty → 3 pary → confidence=medium."""
        readings = [
            (0, 100.0),
            (86400, 103.0),     # +3 kWh
            (172800, 106.0),    # +3 kWh
            (259200, 109.0),    # +3 kWh
        ]
        result = compute_calibration(readings, lambda a, b: 2.5)
        assert result.confidence == "medium"
        assert result.readings_used == 3
        assert 15 < result.hidden_power_w < 25

    def test_negative_hidden_clamped_to_zero(self) -> None:
        """Licznik < telemetria → hidden_power = 0 (nie ujemny)."""
        readings = [(0, 100.0), (86400, 102.0)]
        # Sensor widzi więcej niż licznik: 3 kWh vs 2 kWh
        result = compute_calibration(readings, lambda a, b: 3.0)
        assert result.hidden_power_w == 0.0

    def test_sensor_fn_called_with_correct_timestamps(self) -> None:
        """sensor_energy_fn dostaje timestampy z odczytów, nie doby kalendarzowe."""
        calls = []
        def mock_fn(ts_from, ts_to):
            calls.append((ts_from, ts_to))
            return 2.5

        readings = [(1000, 100.0), (87400, 103.0)]
        compute_calibration(readings, mock_fn)
        assert len(calls) == 1
        assert calls[0] == (1000, 87400)  # dokładne timestampy odczytów
