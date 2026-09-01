"""Testy jednostkowe dla core/physics.py — czyste wzory fizyczne."""
import numpy as np
import pytest

from app.core.physics import (
    compute_cop,
    compute_hdd,
    compute_p_el_w,
    compute_p_el_w_array,
    compute_p_th_w,
    compute_p_th_w_array,
)


# =============================================================================
# compute_p_el_w
# =============================================================================

class TestComputePElW:
    """Testy mocy elektrycznej."""

    def test_typical_working_conditions(self) -> None:
        """Typowe warunki pracy: 230V, 5A (raw=50), cos_phi=0.95."""
        p = compute_p_el_w(230.0, 50.0, cos_phi=0.95, standby_power_w=25.0, active_power_w=40.0)
        # raw: 230 * 5 * 0.95 = 1092.5W → active → + 25 + 40 = 1157.5W
        assert abs(p - 1157.5) < 0.1

    def test_standby_mode(self) -> None:
        """Pompa w spoczynku: prąd 0, tylko standby."""
        p = compute_p_el_w(230.0, 0.0, cos_phi=0.95, standby_power_w=25.0, active_power_w=40.0)
        # raw: 0W → not active → + 25 + 0 = 25W
        assert abs(p - 25.0) < 0.1

    def test_sensor_factor(self) -> None:
        """Kalibracja z licznika: factor = 1.07 → 7% więcej."""
        p_base = compute_p_el_w(230.0, 50.0, sensor_factor=1.0)
        p_cal = compute_p_el_w(230.0, 50.0, sensor_factor=1.07)
        assert p_cal > p_base
        assert abs(p_cal / p_base - 1.07) < 0.001

    def test_no_standby(self) -> None:
        """Bez standby (standby_power_w=0)."""
        p = compute_p_el_w(230.0, 50.0, standby_power_w=0.0, active_power_w=0.0)
        expected = 230.0 * 5.0 * 0.95
        assert abs(p - expected) < 0.1

    def test_never_negative(self) -> None:
        """Moc nigdy ujemna."""
        p = compute_p_el_w(0.0, 0.0, standby_power_w=0.0, active_power_w=0.0)
        assert p >= 0.0

    def test_array_version_matches_scalar(self) -> None:
        """Wersja wektorowa daje takie same wyniki jak skalarna."""
        voltages = [230.0, 228.0, 232.0, 230.0, 0.0]
        currents = [50.0, 45.0, 55.0, 0.0, 0.0]

        scalar_results = [
            compute_p_el_w(v, c, cos_phi=0.95, standby_power_w=25.0, active_power_w=40.0)
            for v, c in zip(voltages, currents)
        ]

        array_results = compute_p_el_w_array(
            np.array(voltages), np.array(currents),
            cos_phi=0.95, standby_power_w=25.0, active_power_w=40.0,
        )

        np.testing.assert_allclose(array_results, scalar_results, rtol=1e-10)


# =============================================================================
# compute_p_th_w
# =============================================================================

class TestComputePThW:
    """Testy mocy cieplnej."""

    def test_typical_heating(self) -> None:
        """Typowe grzanie: flow=2.0 m³/h (raw=20), ΔT=5°C."""
        p = compute_p_th_w(20.0, 40.0, 35.0)
        # P = 2.0 * 4.186 * 5.0 / 3.6 * 1000 = 11627.78 W
        expected = 2.0 * 4.186 * 5.0 / 3.6 * 1000.0
        assert abs(p - expected) < 1.0

    def test_cwu_higher_delta_t(self) -> None:
        """CWU: wyższe ΔT = 8°C."""
        p = compute_p_th_w(20.0, 48.0, 40.0)
        expected = 2.0 * 4.186 * 8.0 / 3.6 * 1000.0
        assert p > 0
        assert abs(p - expected) < 1.0

    def test_defrost_negative(self) -> None:
        """Defrost: T_out < T_in → P_th < 0 (strata cieplna)."""
        p = compute_p_th_w(20.0, 30.0, 35.0)
        assert p < 0, "Defrost musi dać ujemną moc cieplną"

    def test_zero_flow(self) -> None:
        """Brak przepływu → P_th = 0."""
        p = compute_p_th_w(0.0, 40.0, 35.0)
        assert p == 0.0

    def test_zero_delta_t(self) -> None:
        """ΔT = 0 → P_th = 0."""
        p = compute_p_th_w(20.0, 35.0, 35.0)
        assert p == 0.0

    def test_array_version_matches_scalar(self) -> None:
        """Wersja wektorowa daje takie same wyniki jak skalarna."""
        flows = [20.0, 15.0, 25.0, 0.0, 20.0]
        t_outs = [40.0, 45.0, 38.0, 40.0, 30.0]
        t_ins = [35.0, 37.0, 33.0, 35.0, 35.0]

        scalar_results = [compute_p_th_w(f, to, ti) for f, to, ti in zip(flows, t_outs, t_ins)]
        array_results = compute_p_th_w_array(
            np.array(flows), np.array(t_outs), np.array(t_ins)
        )
        np.testing.assert_allclose(array_results, scalar_results, rtol=1e-10)


# =============================================================================
# compute_cop
# =============================================================================

class TestComputeCop:
    """Testy COP."""

    def test_typical_cop(self) -> None:
        """COP = 4.0: P_th=4000W, P_el=1000W."""
        assert abs(compute_cop(4000.0, 1000.0) - 4.0) < 0.001

    def test_zero_p_el(self) -> None:
        """P_el = 0 → COP = 0 (unikaj dzielenia przez zero)."""
        assert compute_cop(4000.0, 0.0) == 0.0

    def test_zero_p_th(self) -> None:
        """P_th = 0 → COP = 0."""
        assert compute_cop(0.0, 1000.0) == 0.0

    def test_negative_p_th(self) -> None:
        """P_th < 0 (defrost) → COP = 0."""
        assert compute_cop(-500.0, 1000.0) == 0.0

    def test_cop_too_high(self) -> None:
        """COP > 12 → odrzucony (nierealistyczny)."""
        assert compute_cop(13000.0, 1000.0) == 0.0

    def test_cop_too_low(self) -> None:
        """COP < 0.5 → odrzucony."""
        assert compute_cop(400.0, 1000.0) == 0.0

    def test_cop_boundary_valid(self) -> None:
        """COP = 0.5 → akceptowany."""
        assert compute_cop(500.0, 1000.0) == 0.5

    def test_cop_boundary_max(self) -> None:
        """COP = 12.0 → akceptowany."""
        assert abs(compute_cop(12000.0, 1000.0) - 12.0) < 0.001


# =============================================================================
# compute_hdd
# =============================================================================

class TestComputeHdd:
    """Testy Heating Degree Days."""

    def test_cold_day(self) -> None:
        """Mroźny dzień: -5°C, baza 15°C → HDD = 20."""
        assert compute_hdd(-5.0, 15.0) == 20.0

    def test_mild_day(self) -> None:
        """Łagodny dzień: 10°C → HDD = 5."""
        assert compute_hdd(10.0, 15.0) == 5.0

    def test_warm_day(self) -> None:
        """Ciepły dzień: 20°C → HDD = 0 (nie ujemny)."""
        assert compute_hdd(20.0, 15.0) == 0.0

    def test_exactly_base(self) -> None:
        """Dokładnie temperatura bazowa → HDD = 0."""
        assert compute_hdd(15.0, 15.0) == 0.0

    def test_never_negative(self) -> None:
        """HDD nigdy ujemne."""
        assert compute_hdd(30.0, 15.0) == 0.0
