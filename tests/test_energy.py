"""Testy compute_energy() — na rzeczywistych danych z bazy telemetry."""
import os
import pytest
import numpy as np

from app.core.energy import compute_energy, compute_scop, scop_from_result
from app.core.models import EnergyResult

# Ścieżka do bazy testowej
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tuya_telemetry.db")


def _skip_if_no_db() -> None:
    if not os.path.exists(DB_PATH):
        pytest.skip("Brak bazy testowej tuya_telemetry.db")


# =============================================================================
# Testy podstawowe
# =============================================================================

class TestComputeEnergyBasic:
    """Testy podstawowej funkcjonalności compute_energy()."""

    def test_returns_energy_result(self) -> None:
        """Funkcja zwraca obiekt EnergyResult."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        assert isinstance(result, EnergyResult)

    def test_all_time_has_data(self) -> None:
        """All-time powinno zwrócić dane (baza ma ~17 dni danych)."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        assert result.sample_count > 0
        assert result.e_el_total > 0, "Powinno być zużycie energii"
        assert result.e_th_total > 0, "Powinno być wygenerowane ciepło"

    def test_scop_reasonable(self) -> None:
        """SCOP powinien być w rozsądnym zakresie 2.0–6.0."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        assert 2.0 <= result.scop_nominal <= 6.0, (
            f"SCOP nominalny {result.scop_nominal:.2f} poza zakresem [2, 6]"
        )
        assert 2.0 <= result.scop_real <= 6.0, (
            f"SCOP realny {result.scop_real:.2f} poza zakresem [2, 6]"
        )

    def test_compute_time_reasonable(self) -> None:
        """Obliczenie all-time powinno trwać < 5 sekund."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        assert result.compute_time_ms < 5000, (
            f"Obliczenie trwało {result.compute_time_ms:.0f}ms (limit 5000ms)"
        )

    def test_empty_range_returns_zeros(self) -> None:
        """Pusty zakres (przyszłość) → zerowy wynik."""
        _skip_if_no_db()
        result = compute_energy(date_from="2099-01-01", date_to="2099-01-02", db_file=DB_PATH)
        assert result.e_el_total == 0.0
        assert result.e_th_total == 0.0
        assert result.scop_nominal == 0.0


# =============================================================================
# Testy defrostu
# =============================================================================

class TestDefrost:
    """Testy obsługi defrostu."""

    def test_defrost_energy_negative(self) -> None:
        """e_th_defrost musi być ujemne lub zero (NIGDY dodatnie)."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        assert result.e_th_defrost <= 0, (
            f"e_th_defrost = {result.e_th_defrost:.4f} — musi być ujemne (strata cieplna)"
        )

    def test_scop_real_le_nominal(self) -> None:
        """SCOP realny ≤ SCOP nominalny (defrost obniża SCOP)."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        if result.e_th_defrost < 0:
            assert result.scop_real <= result.scop_nominal, (
                f"SCOP real ({result.scop_real:.3f}) > nominal ({result.scop_nominal:.3f})"
            )

    def test_e_th_total_real_includes_defrost(self) -> None:
        """e_th_total_real = e_th_total + e_th_defrost (property)."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        expected = result.e_th_total + result.e_th_defrost
        assert abs(result.e_th_total_real - expected) < 1e-10


# =============================================================================
# Testy trybów CO / CWU
# =============================================================================

class TestModeFiltering:
    """Testy filtrowania po trybie pracy."""

    def test_co_plus_cwu_equals_total(self) -> None:
        """E_el(CO) + E_el(CWU) ≈ E_el(total)."""
        _skip_if_no_db()
        r_total = compute_energy(mode="total", db_file=DB_PATH)
        r_co = compute_energy(mode="co", db_file=DB_PATH)
        r_cwu = compute_energy(mode="cwu", db_file=DB_PATH)

        # Tolerancja: defrost wchodzi do CO → e_el_co z filtra "co" zawiera defrost
        e_el_sum = r_co.e_el_total + r_cwu.e_el_total
        assert abs(e_el_sum - r_total.e_el_total) < 0.01, (
            f"E_el CO({r_co.e_el_total:.3f}) + CWU({r_cwu.e_el_total:.3f}) "
            f"= {e_el_sum:.3f} ≠ total({r_total.e_el_total:.3f})"
        )

    def test_co_mode_has_no_cwu_energy(self) -> None:
        """Tryb 'co' nie powinien mieć energii CWU."""
        _skip_if_no_db()
        result = compute_energy(mode="co", db_file=DB_PATH)
        assert result.e_el_cwu == 0.0
        assert result.e_th_cwu == 0.0


# =============================================================================
# Testy daily_breakdown
# =============================================================================

class TestDailyBreakdown:
    """Testy rozbicia na dni."""

    def test_daily_dataframe_created(self) -> None:
        """daily_breakdown=True → result.daily jest DataFrame."""
        _skip_if_no_db()
        result = compute_energy(daily_breakdown=True, db_file=DB_PATH)
        assert result.daily is not None
        assert len(result.daily) > 0

    def test_daily_columns_present(self) -> None:
        """DataFrame ma wymagane kolumny."""
        _skip_if_no_db()
        result = compute_energy(daily_breakdown=True, db_file=DB_PATH)
        required_cols = [
            "date", "e_el_co", "e_el_cwu", "e_th_co", "e_th_cwu",
            "e_th_defrost", "scop_nominal", "scop_real", "hdd",
            "amb_temp_avg", "comp_starts", "defrost_count", "comp_hours",
        ]
        for col in required_cols:
            assert col in result.daily.columns, f"Brak kolumny '{col}' w daily DataFrame"

    def test_daily_sum_matches_total(self) -> None:
        """Suma daily E_el (CO+CWU+standby) ≈ E_el total."""
        _skip_if_no_db()
        result = compute_energy(date_from="2026-08-20", date_to="2026-08-27",
                                daily_breakdown=True, db_file=DB_PATH)
        daily_el_sum = result.daily["e_el_co"].sum() + result.daily["e_el_cwu"].sum()
        # Standby nie jest w daily CO/CWU — dodaj jeśli kolumna istnieje
        if "e_el_standby" in result.daily.columns:
            daily_el_sum += result.daily["e_el_standby"].sum()
        assert abs(daily_el_sum - result.e_el_total) < 0.1, (
            f"Daily sum E_el ({daily_el_sum:.3f}) != total ({result.e_el_total:.3f})"
        )

    def test_daily_defrost_always_negative(self) -> None:
        """Defrost w daily jest zawsze ≤ 0."""
        _skip_if_no_db()
        result = compute_energy(daily_breakdown=True, db_file=DB_PATH)
        assert (result.daily["e_th_defrost"] <= 0).all(), (
            "Znaleziono dodatni e_th_defrost w daily breakdown"
        )


# =============================================================================
# Testy gaps_skipped
# =============================================================================

class TestGapsSkipped:
    """Testy pomijania przerw w danych."""

    def test_gaps_skipped_nonnegative(self) -> None:
        """gaps_skipped >= 0."""
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        assert result.gaps_skipped >= 0

    def test_strict_dt_max_skips_more(self) -> None:
        """Niższy dt_max → więcej pominiętych interwałów."""
        _skip_if_no_db()
        r_normal = compute_energy(dt_max_sec=360, db_file=DB_PATH)
        r_strict = compute_energy(dt_max_sec=60, db_file=DB_PATH)
        assert r_strict.gaps_skipped >= r_normal.gaps_skipped


# =============================================================================
# Testy @property EnergyResult
# =============================================================================

class TestEnergyResultProperties:
    """Testy właściwości pomocniczych EnergyResult."""

    def test_e_el_total(self) -> None:
        r = EnergyResult(e_el_co=10.0, e_el_cwu=3.0)
        assert r.e_el_total == 13.0

    def test_e_th_total(self) -> None:
        r = EnergyResult(e_th_co=30.0, e_th_cwu=8.0)
        assert r.e_th_total == 38.0

    def test_e_th_total_real(self) -> None:
        r = EnergyResult(e_th_co=30.0, e_th_cwu=8.0, e_th_defrost=-1.5)
        assert abs(r.e_th_total_real - 36.5) < 1e-10

    def test_defrost_reduces_real_total(self) -> None:
        r = EnergyResult(e_th_co=30.0, e_th_cwu=8.0, e_th_defrost=-2.0)
        assert r.e_th_total_real < r.e_th_total


# =============================================================================
# Testy kalibracji
# =============================================================================

class TestCalibration:
    """Testy wpływu kalibracji na wyniki."""

    def test_hidden_power_increases_e_el(self) -> None:
        """hidden_power_w > 0 zwiększa E_el (addytywnie)."""
        _skip_if_no_db()
        r_base = compute_energy(hidden_power_w=0, db_file=DB_PATH)
        r_cal = compute_energy(hidden_power_w=20, db_file=DB_PATH)
        assert r_cal.e_el_total > r_base.e_el_total

    def test_hidden_power_does_not_affect_e_th(self) -> None:
        """hidden_power_w nie wpływa na E_th (tylko E_el)."""
        _skip_if_no_db()
        r_base = compute_energy(hidden_power_w=0, db_file=DB_PATH)
        r_cal = compute_energy(hidden_power_w=20, db_file=DB_PATH)
        assert abs(r_cal.e_th_total - r_base.e_th_total) < 0.001

    def test_sensor_factor_scales_e_el(self) -> None:
        """sensor_factor > 1 skaluje E_el proporcjonalnie."""
        _skip_if_no_db()
        r_base = compute_energy(sensor_factor=1.0, hidden_power_w=0, db_file=DB_PATH)
        r_cal = compute_energy(sensor_factor=1.1, hidden_power_w=0, db_file=DB_PATH)
        assert r_cal.e_el_total > r_base.e_el_total
        # Powinno być ~10% więcej
        ratio = r_cal.e_el_total / r_base.e_el_total
        assert 1.05 < ratio < 1.15, f"Expected ~1.1x, got {ratio:.3f}x"

    def test_hidden_power_additive_not_multiplicative(self) -> None:
        """hidden_power dodaje stałą ilość per godzinę, niezależnie od E_el_sensor."""
        _skip_if_no_db()
        r_0 = compute_energy(hidden_power_w=0, sensor_factor=1.0, db_file=DB_PATH)
        r_20 = compute_energy(hidden_power_w=20, sensor_factor=1.0, db_file=DB_PATH)
        # Różnica powinna odpowiadać ~20W × total_hours
        diff_kwh = r_20.e_el_total - r_0.e_el_total
        # 17 dni × 24h = 408h, 20W × 408h / 1000 ≈ 8.16 kWh
        assert diff_kwh > 5.0, f"Hidden power diff too small: {diff_kwh:.2f} kWh"
        assert diff_kwh < 15.0, f"Hidden power diff too large: {diff_kwh:.2f} kWh"


# =============================================================================
# Test spójności SCOP niezależnie od parametrów wyświetlania
# =============================================================================

class TestScopConsistency:
    """Kluczowy test v2: SCOP musi być identyczny niezależnie od sposobu wywołania."""

    def test_scop_same_for_same_range(self) -> None:
        """Dwa wywołania z tym samym zakresem → identyczny SCOP."""
        _skip_if_no_db()
        r1 = compute_energy(db_file=DB_PATH)
        r2 = compute_energy(db_file=DB_PATH)
        assert r1.scop_real == r2.scop_real
        assert r1.scop_nominal == r2.scop_nominal

    def test_daily_breakdown_does_not_change_scop(self) -> None:
        """daily_breakdown nie zmienia SCOP sumarycznego."""
        _skip_if_no_db()
        # Zakres <= 14 dni żeby oba szły bez chunkowania
        r_simple = compute_energy(date_from="2026-08-20", date_to="2026-08-27",
                                  daily_breakdown=False, db_file=DB_PATH)
        r_daily = compute_energy(date_from="2026-08-20", date_to="2026-08-27",
                                 daily_breakdown=True, db_file=DB_PATH)
        assert abs(r_simple.scop_real - r_daily.scop_real) < 0.001, (
            f"SCOP simple ({r_simple.scop_real:.4f}) != daily ({r_daily.scop_real:.4f})"
        )


# =============================================================================
# Testy kanonicznej funkcji compute_scop() — jedyne źródło wzoru
# =============================================================================

class TestComputeScop:
    """Testy jednostkowe compute_scop() — bez bazy, czysta arytmetyka wzorów."""

    def test_total_real_includes_standby_and_defrost(self) -> None:
        """total/real: (th_co+th_cwu+defrost) / (el_co+el_cwu+standby)."""
        s = compute_scop(e_el_co=10, e_el_cwu=5, e_el_standby=2,
                         e_th_co=30, e_th_cwu=15, e_th_defrost=-3,
                         scope="total", kind="real")
        assert abs(s - (30 + 15 - 3) / (10 + 5 + 2)) < 1e-9

    def test_total_nominal_ignores_defrost(self) -> None:
        """total/nominal: defrost NIE odejmowany od licznika."""
        s = compute_scop(e_el_co=10, e_el_cwu=5, e_el_standby=2,
                         e_th_co=30, e_th_cwu=15, e_th_defrost=-3,
                         scope="total", kind="nominal")
        assert abs(s - (30 + 15) / (10 + 5 + 2)) < 1e-9

    def test_nominal_ge_real(self) -> None:
        """SCOP nominalny zawsze >= realny (defrost <= 0)."""
        kw = dict(e_el_co=10, e_el_cwu=5, e_el_standby=2,
                  e_th_co=30, e_th_cwu=15, e_th_defrost=-3, scope="total")
        assert compute_scop(**kw, kind="nominal") >= compute_scop(**kw, kind="real")

    def test_co_scope_only_co_energy_with_defrost(self) -> None:
        """co: (th_co+defrost) / el_co — standby i cwu nie wchodzą."""
        s = compute_scop(e_el_co=10, e_el_cwu=5, e_el_standby=2,
                         e_th_co=30, e_th_cwu=15, e_th_defrost=-3,
                         scope="co", kind="real")
        assert abs(s - (30 - 3) / 10) < 1e-9

    def test_cwu_scope_excludes_defrost(self) -> None:
        """cwu: th_cwu / el_cwu — defrost NIE dotyczy CWU."""
        s = compute_scop(e_el_co=10, e_el_cwu=5, e_el_standby=2,
                         e_th_co=30, e_th_cwu=15, e_th_defrost=-3,
                         scope="cwu", kind="real")
        assert abs(s - 15 / 5) < 1e-9

    def test_zero_denominator_returns_zero(self) -> None:
        """Brak prądu → SCOP 0.0 (bez dzielenia przez zero)."""
        assert compute_scop(0, 0, 0, 0, 0, 0, scope="total", kind="real") == 0.0
        assert compute_scop(0, 5, 0, 0, 15, 0, scope="co", kind="real") == 0.0

    def test_scop_from_result_matches_compute_scop(self) -> None:
        """scop_from_result() daje ten sam wynik co compute_scop()."""
        r = EnergyResult(e_el_co=10, e_el_cwu=5, e_el_standby=2,
                         e_th_co=30, e_th_cwu=15, e_th_defrost=-3)
        assert scop_from_result(r, scope="total", kind="real") == compute_scop(
            10, 5, 2, 30, 15, -3, scope="total", kind="real")


# =============================================================================
# Test spójności single vs chunked (>14 dni)
# =============================================================================

class TestSingleVsChunked:
    """SCOP musi być identyczny dla ścieżki single (<=14 dni) i chunked (>14 dni)."""

    def test_engine_scop_uses_compute_scop(self) -> None:
        """SCOP z compute_energy() == compute_scop() z jego składowych."""
        _skip_if_no_db()
        r = compute_energy(db_file=DB_PATH)  # all-time, >14 dni → chunked
        expected_real = compute_scop(
            r.e_el_co, r.e_el_cwu, r.e_el_standby,
            r.e_th_co, r.e_th_cwu, r.e_th_defrost, scope="total", kind="real")
        expected_nom = compute_scop(
            r.e_el_co, r.e_el_cwu, r.e_el_standby,
            r.e_th_co, r.e_th_cwu, r.e_th_defrost, scope="total", kind="nominal")
        assert abs(r.scop_real - expected_real) < 1e-9
        assert abs(r.scop_nominal - expected_nom) < 1e-9

    def test_single_vs_chunked_same_scop(self) -> None:
        """Chunked (>14 dni): standby JEST w mianowniku SCOP (fix niespójności).

        Weryfikuje, że SCOP z akumulatorów chunked liczy się przez compute_scop()
        z mianownikiem CO+CWU+standby — dokładnie jak wersja single. Gdyby standby
        był pominięty (stary bug), scop_real byłby wyższy niż z pełnego mianownika.
        """
        _skip_if_no_db()
        r = compute_energy(db_file=DB_PATH)  # all-time >14 dni → chunked
        assert r.e_el_standby > 0, "Test wymaga niezerowego standby"

        # SCOP z pełnym mianownikiem (poprawny, aktualny)
        scop_with_standby = compute_scop(
            r.e_el_co, r.e_el_cwu, r.e_el_standby,
            r.e_th_co, r.e_th_cwu, r.e_th_defrost, scope="total", kind="real")
        # SCOP ze starym (błędnym) mianownikiem bez standby
        scop_without_standby = compute_scop(
            r.e_el_co, r.e_el_cwu, 0.0,
            r.e_th_co, r.e_th_cwu, r.e_th_defrost, scope="total", kind="real")

        # Silnik musi używać wariantu ZE standby
        assert abs(r.scop_real - scop_with_standby) < 1e-9
        # I ten wariant musi być różny od błędnego (standby realnie wpływa)
        assert scop_without_standby > scop_with_standby, (
            "Standby powinien zaniżać SCOP (większy mianownik)"
        )

    def test_chunked_totals_match_daily_sum(self) -> None:
        """Sumy z daily DataFrame == akumulatory totalne (chunked, dedup granic)."""
        _skip_if_no_db()
        r = compute_energy(daily_breakdown=True, db_file=DB_PATH)  # >14 dni → chunked
        assert r.daily is not None and not r.daily.empty
        d = r.daily
        for col in ["e_el_co", "e_el_cwu", "e_el_standby", "e_th_co", "e_th_cwu", "e_th_defrost"]:
            assert abs(d[col].sum() - getattr(r, col)) < 0.01, (
                f"{col}: daily sum {d[col].sum():.4f} != total {getattr(r, col):.4f}"
            )
        # SCOP totalny zgodny z compute_scop z zsumowanych składowych daily
        scop_from_daily = compute_scop(
            d["e_el_co"].sum(), d["e_el_cwu"].sum(), d["e_el_standby"].sum(),
            d["e_th_co"].sum(), d["e_th_cwu"].sum(), d["e_th_defrost"].sum(),
            scope="total", kind="real")
        assert abs(r.scop_real - scop_from_daily) < 1e-6
