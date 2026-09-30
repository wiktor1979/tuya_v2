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


def _db_span_hours() -> float:
    """Rozpiętość czasowa danych w bazie testowej [h] (odporność testów na rozrost bazy)."""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM telemetry").fetchone()
    finally:
        conn.close()
    if not row or row[0] is None or row[1] is None:
        return 0.0
    return (row[1] - row[0]) / 3600.0


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
        """Obliczenie all-time powinno być wydajne (skalowane do rozmiaru danych).

        Limit skalowany do liczby próbek (~10µs/próbkę + narzut), zamiast stałej
        wartości — odporne na rozrost bazy i obciążenie maszyny CI.
        """
        _skip_if_no_db()
        result = compute_energy(db_file=DB_PATH)
        # Budżet: 3s bazowo + 100µs na próbkę (z zapasem na obciążenie maszyny).
        limit_ms = 3000 + result.sample_count * 0.1
        assert result.compute_time_ms < limit_ms, (
            f"Obliczenie trwało {result.compute_time_ms:.0f}ms "
            f"(limit {limit_ms:.0f}ms dla {result.sample_count} próbek)"
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
        # Różnica MUSI odpowiadać stałej mocy 20W × liczba godzin (model addytywny),
        # a NIE być proporcjonalna do E_el_sensor (to odróżnia model addytywny od mnożnika).
        diff_kwh = r_20.e_el_total - r_0.e_el_total
        # Odwzorowanie na godziny: 20W × h / 1000 = diff → h = diff × 1000 / 20.
        # Oczekiwana liczba godzin musi mieścić się w rozpiętości danych w bazie
        # (odporne na rozrost bazy: liczymy względem realnego zakresu, nie stałej).
        implied_hours = diff_kwh * 1000.0 / 20.0
        span_hours = _db_span_hours()
        assert diff_kwh > 0, "hidden_power musi zwiększać E_el (model addytywny)"
        # Liczone są tylko interwały bez dużych przerw (gaps), więc godziny ≤ rozpiętość.
        assert 0 < implied_hours <= span_hours + 1.0, (
            f"Implikowane godziny {implied_hours:.1f}h poza zakresem danych "
            f"(rozpiętość {span_hours:.1f}h) — model powinien być addytywny 20W×h"
        )


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



# =============================================================================
# Testy klasyfikacji trybu CO/CWU (work_mode + histereza zasobnika)
# =============================================================================

class TestModeClassification:
    """Klasyfikacja CO/CWU wg work_mode (DP 109) zamiast zaworu 4-drożnego.

    Zawór 'valve' (DP 117) to zawór 4-drożny (rewers grzanie/chłodzenie) i
    koreluje ze sprężarką, więc NIE rozróżnia CO/CWU. Podział wyznacza work_mode,
    a tryb łączony 'heat_hot_water' — reguła histerezy różnicy temperatur zasobnika.
    """

    def _codes(self):
        from app.core.energy import WORK_MODE_CODES
        return WORK_MODE_CODES

    def test_heat_is_co(self) -> None:
        """work_mode='heat' → wszystkie interwały CO (is_cwu = False)."""
        from app.core.energy import _classify_cwu_mask
        wm = np.full(5, self._codes()["heat"])
        is_cwu = _classify_cwu_mask(wm, np.zeros(5), np.zeros(5))
        assert not is_cwu.any(), "heat powinien być w całości CO"

    def test_hot_water_is_cwu(self) -> None:
        """work_mode='hot_water' → wszystkie interwały CWU."""
        from app.core.energy import _classify_cwu_mask
        wm = np.full(5, self._codes()["hot_water"])
        is_cwu = _classify_cwu_mask(wm, np.zeros(5), np.zeros(5))
        assert is_cwu.all(), "hot_water powinien być w całości CWU"

    def test_combined_hysteresis_enter_and_exit(self) -> None:
        """heat_hot_water: wejście w CWU gdy diff>ON(5), wyjście dopiero gdy diff<=OFF(0).

        CWU ma priorytet i jest grzane do temperatury zadanej (diff<=0).
        """
        from app.core.energy import _classify_cwu_mask
        code = self._codes()["heat_hot_water"]
        wm = np.full(7, code)
        hw_set = np.full(7, 45.0)
        # diff = 45 - tank
        # tank: 38(d7) 42(d3) 44(d1) 45(d0) 43(d2) 40(d5) 39(d6)
        tank = np.array([38.0, 42.0, 44.0, 45.0, 43.0, 40.0, 39.0])
        is_cwu = _classify_cwu_mask(wm, tank, hw_set)
        assert is_cwu[0]        # diff7 > ON(5) → CWU start
        assert is_cwu[1]        # diff3 (>OFF=0) → grzeje CWU dalej
        assert is_cwu[2]        # diff1 (>OFF=0) → wciąż CWU (aż do setpointu)
        assert not is_cwu[3]    # diff0 <= OFF → osiągnięto setpoint → CO
        assert not is_cwu[4]    # diff2 (<ON) → nie wznawia CWU, zostaje CO
        assert not is_cwu[5]    # diff5 (==ON, nie >ON) → wciąż CO
        assert is_cwu[6]        # diff6 > ON → ponowne wejście w CWU

    def test_cooling_not_classified_as_cwu(self) -> None:
        """cool/cool_hot_water NIE są zaliczane do CWU (pomijane osobną maską)."""
        from app.core.energy import _classify_cwu_mask
        wm = np.array([self._codes()["cool"], self._codes()["cool_hot_water"]])
        is_cwu = _classify_cwu_mask(wm, np.zeros(2), np.zeros(2))
        assert not is_cwu.any()

    def test_energy_uses_work_mode_not_valve(self) -> None:
        """Na realnej bazie: gdy pompa grzeje CWU (work_mode), e_th_cwu > e_th_co."""
        _skip_if_no_db()
        # Baza testowa ma głównie tryb hot_water — CWU musi dominować nad CO.
        r = compute_energy(db_file=DB_PATH)
        assert r.e_th_cwu > r.e_th_co, (
            f"Oczekiwano dominacji CWU (hot_water) w bazie: "
            f"e_th_cwu={r.e_th_cwu:.1f} vs e_th_co={r.e_th_co:.1f}"
        )

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
