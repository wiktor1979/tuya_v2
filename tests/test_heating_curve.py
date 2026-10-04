"""Testy modułu krzywej grzewczej (app/core/heating_curve.py).

Pokrywa:
- interpolację/ekstrapolację krzywej 2-punktowej (-15/+15)
- wykrywanie epizodów grzania z sekwencji work_mode
- filtr strefy (Z2 pomijana — stała temp. wody; Z1/obie liczone)
- duty cycle per bin temperatury zewnętrznej
- rekomendację: za ciepło (obniż), w normie, oba końce, progi rozpiętości 8/15
- przypadki brzegowe: brak danych, wąski zakres temperatur
"""
import math

import pytest

from app.core.heating_curve import (
    curve_slope,
    curve_water_temp,
    detect_heating_episodes,
    compute_duty_bins,
    recommend_curve_adjustment,
    HeatingEpisode,
    DutyBin,
    CURVE_POINT_LOW_AMB,
    CURVE_POINT_HIGH_AMB,
    TARGET_DUTY_PCT,
)

H = 3600  # sekund w godzinie


# =============================================================================
# 1. MODEL KRZYWEJ
# =============================================================================

class TestCurveModel:
    def test_slope_typical_negative(self):
        """Cieplej na zewnątrz → chłodniejsza woda: nachylenie ujemne."""
        s = curve_slope(t_low=43.0, t_high=28.0)  # -15/+15
        assert s == pytest.approx((28.0 - 43.0) / (15.0 - (-15.0)))
        assert s < 0

    def test_slope_zero_division_raises(self):
        """Te same punkty kotwiczące → błąd."""
        with pytest.raises(ValueError):
            curve_slope(40.0, 30.0, amb_low=0.0, amb_high=0.0)

    def test_water_temp_at_anchor_points(self):
        """W punktach kotwiczących zwraca dokładnie T_low i T_high."""
        assert curve_water_temp(CURVE_POINT_LOW_AMB, 43.0, 28.0) == pytest.approx(43.0)
        assert curve_water_temp(CURVE_POINT_HIGH_AMB, 43.0, 28.0) == pytest.approx(28.0)

    def test_water_temp_interpolation_midpoint(self):
        """W środku (amb=0) → średnia z końców dla symetrycznych punktów -15/+15."""
        assert curve_water_temp(0.0, 44.0, 28.0) == pytest.approx(36.0)

    def test_water_temp_extrapolation_below(self):
        """Poniżej -15°C ekstrapoluje w górę (cieplejsza woda)."""
        t = curve_water_temp(-20.0, 43.0, 28.0)
        assert t > 43.0

    def test_water_temp_extrapolation_above(self):
        """Powyżej +15°C ekstrapoluje w dół (chłodniejsza woda)."""
        t = curve_water_temp(20.0, 43.0, 28.0)
        assert t < 28.0


# =============================================================================
# 2. WYKRYWANIE EPIZODÓW
# =============================================================================

class TestDetectEpisodes:
    def test_empty_events(self):
        assert detect_heating_episodes([]) == []

    def test_single_on_off_episode(self):
        """hot_water → heat_hot_water (ON) → hot_water (OFF) = jeden epizod."""
        events = [
            (0, "hot_water"),
            (1000, "heat_hot_water"),   # ON
            (1000 + 2 * H, "hot_water"),  # OFF po 2h
        ]
        eps = detect_heating_episodes(events)
        assert len(eps) == 1
        assert eps[0].start_ts == 1000
        assert eps[0].end_ts == 1000 + 2 * H
        assert eps[0].duration_sec == 2 * H

    def test_heat_mode_counts_as_demand(self):
        """Czyste 'heat' też jest żądaniem grzania."""
        events = [(0, "hot_water"), (100, "heat"), (100 + H, "hot_water")]
        eps = detect_heating_episodes(events)
        assert len(eps) == 1
        assert eps[0].duration_sec == H

    def test_short_episode_filtered(self):
        """Epizod krótszy niż min_episode_sec jest pomijany."""
        events = [(0, "hot_water"), (100, "heat_hot_water"), (130, "hot_water")]
        eps = detect_heating_episodes(events, min_episode_sec=60)
        assert eps == []

    def test_open_episode_closed_at_end(self):
        """Epizod otwarty na końcu danych zamykany na ostatnim zdarzeniu."""
        events = [(0, "hot_water"), (500, "heat_hot_water"), (500 + 3 * H, "heat_hot_water")]
        eps = detect_heating_episodes(events)
        assert len(eps) == 1
        assert eps[0].duration_sec == 3 * H

    def test_amb_avg_computed(self):
        """Średnia temp. zewnętrzna liczona z próbek w oknie epizodu."""
        events = [(0, "hot_water"), (0, "heat_hot_water"), (2 * H, "hot_water")]
        amb = [(0, -10.0), (H, -5.0), (2 * H, 0.0)]
        eps = detect_heating_episodes(events, amb_samples=amb)
        assert len(eps) == 1
        assert eps[0].amb_avg == pytest.approx(-5.0)

    def test_multiple_episodes_off_after(self):
        """off_after_sec = przerwa między kolejnymi epizodami."""
        events = [
            (0, "heat_hot_water"), (H, "hot_water"),       # ep1: 0..H
            (3 * H, "heat_hot_water"), (4 * H, "hot_water"),  # ep2: 3H..4H
        ]
        eps = detect_heating_episodes(events)
        assert len(eps) == 2
        assert eps[0].off_after_sec == 2 * H  # od H do 3H

    def test_long_continuous_on_is_single_episode(self):
        """Kolejne zdarzenia ON bez OFF = JEDEN ciągły epizod (work_mode jest
        zdarzeniowy, długi czas między zdarzeniami to normalna praca, nie luka)."""
        events = [
            (0, "heat_hot_water"),
            (H, "heat_hot_water"),           # wciąż ON
            (H + 10 * H, "heat_hot_water"),  # wciąż ON, 10h później
        ]
        eps = detect_heating_episodes(events)
        assert len(eps) == 1
        assert eps[0].start_ts == 0
        assert eps[0].end_ts == H + 10 * H


class TestZoneFilter:
    """Filtr strefy: Z2 (stała temp. wody) pomijana, Z1/obie liczone."""

    def _episode_events(self):
        # jeden epizod ON przez 2h
        return [(0, "hot_water"), (0, "heat_hot_water"), (2 * H, "hot_water")]

    def test_zone1_counted(self):
        """zone_select=1 (strefa 1) → epizod liczony."""
        zones = [(0, 1.0), (H, 1.0)]
        eps = detect_heating_episodes(self._episode_events(), zone_samples=zones)
        assert len(eps) == 1

    def test_both_zones_counted(self):
        """zone_select=3 (obie strefy) → epizod liczony."""
        zones = [(0, 3.0), (H, 3.0)]
        eps = detect_heating_episodes(self._episode_events(), zone_samples=zones)
        assert len(eps) == 1

    def test_zone2_only_skipped(self):
        """zone_select=2 (sama strefa 2, stała temp.) → epizod POMIJANY."""
        zones = [(0, 2.0), (H, 2.0)]
        eps = detect_heating_episodes(self._episode_events(), zone_samples=zones)
        assert eps == []

    def test_mostly_zone2_skipped(self):
        """Większość okna w strefie 2 → pomijany."""
        zones = [(0, 2.0), (int(0.3 * H), 2.0), (int(1.9 * H), 1.0)]
        eps = detect_heating_episodes(self._episode_events(), zone_samples=zones)
        assert eps == []

    def test_no_zone_data_counts(self):
        """Brak danych zone_select → nie odfiltrowujemy (bezpieczny default)."""
        eps = detect_heating_episodes(self._episode_events(), zone_samples=None)
        assert len(eps) == 1

    def test_curve_zones_param_counts_zone2_only(self):
        """curve_zones={2} liczy WYŁĄCZNIE epizody strefy 2 (diagnostyka).

        Dla danych gdzie grzeje tylko Z2: domyślny filtr (Z1/obie) daje 0 epizodów,
        a filtr {Z2} daje 1 — tak zakładka wykrywa dominację strefy 2.
        """
        events = self._episode_events()
        zones = [(0, 2.0), (H, 2.0)]
        # domyślny filtr (Z1/obie) — pomija Z2
        assert detect_heating_episodes(events, zone_samples=zones) == []
        # filtr {Z2} — liczy Z2
        z2 = detect_heating_episodes(
            events, zone_samples=zones, curve_zones=frozenset({2.0}),
        )
        assert len(z2) == 1


# =============================================================================
# 3. DUTY CYCLE PER BIN
# =============================================================================

class TestDutyBins:
    def test_empty(self):
        assert compute_duty_bins([]) == []

    def test_episodes_without_amb_ignored(self):
        """Epizody bez amb_avg (nan) nie trafiają do binów."""
        eps = [HeatingEpisode(0, H, H, float("nan"), 0)]
        assert compute_duty_bins(eps) == []

    def test_duty_full_when_no_off(self):
        """Sam czas ON, zero OFF → duty 100%."""
        eps = [HeatingEpisode(0, H, H, amb_avg=-10.0, off_after_sec=0)]
        bins = compute_duty_bins(eps, bin_size=3.0)
        assert len(bins) == 1
        assert bins[0].duty_pct == pytest.approx(100.0)

    def test_duty_half(self):
        """ON = OFF → duty 50%."""
        eps = [HeatingEpisode(0, H, H, amb_avg=5.0, off_after_sec=H)]
        bins = compute_duty_bins(eps, bin_size=3.0)
        assert bins[0].duty_pct == pytest.approx(50.0)

    def test_binning_by_temperature(self):
        """Epizody w różnych temperaturach trafiają do różnych binów."""
        eps = [
            HeatingEpisode(0, H, H, amb_avg=-12.0, off_after_sec=0),
            HeatingEpisode(2 * H, 3 * H, H, amb_avg=12.0, off_after_sec=0),
        ]
        bins = compute_duty_bins(eps, bin_size=3.0)
        assert len(bins) == 2
        centers = sorted(b.amb_center for b in bins)
        assert centers[0] < 0 < centers[1]


# =============================================================================
# 4. REKOMENDACJA
# =============================================================================

def _bins_spanning(amb_lo, amb_hi, duty, hours=4.0):
    """Buduje 2 biny duty cycle w zadanych temperaturach (do testów rekomendacji)."""
    on = duty / 100.0 * hours * H
    off = hours * H - on
    return [
        DutyBin(amb_lo - 1, amb_lo + 1, amb_lo, on, hours * H, duty, 10, 600),
        DutyBin(amb_hi - 1, amb_hi + 1, amb_hi, on, hours * H, duty, 10, 600),
    ]


class TestRecommendation:
    def test_no_data(self):
        rec = recommend_curve_adjustment([], t_low=43.0, t_high=28.0)
        assert rec.data_quality == "none"
        assert not rec.has_recommendation
        assert any("Brak" in m for m in rec.messages)

    def test_duty_in_norm_no_change(self):
        """Duty >= cel w szerokim zakresie → brak zmian, krzywa OK."""
        bins = _bins_spanning(-13.0, 13.0, duty=95.0)
        rec = recommend_curve_adjustment(bins, t_low=43.0, t_high=28.0)
        assert not rec.has_recommendation
        assert rec.data_quality == "slope_reliable"
        assert any("prawidłowo" in m or "normie" in m for m in rec.messages)

    def test_too_warm_lowers_both_ends_reliable(self):
        """Niski duty w szerokim zakresie → obniż oba końce."""
        bins = _bins_spanning(-13.0, 13.0, duty=50.0)  # rozrzut 26°C
        rec = recommend_curve_adjustment(bins, t_low=43.0, t_high=28.0)
        assert rec.data_quality == "slope_reliable"
        assert rec.new_t_low is not None and rec.new_t_low < 43.0
        assert rec.new_t_high is not None and rec.new_t_high < 28.0

    def test_narrow_range_single_end(self):
        """Wąski zakres (< 8°C) → tylko jeden koniec + ostrzeżenie."""
        bins = _bins_spanning(10.0, 13.0, duty=50.0)  # rozrzut 3°C
        rec = recommend_curve_adjustment(bins, t_low=43.0, t_high=28.0)
        assert rec.data_quality == "narrow"
        # tylko jeden koniec ruszony (ten bliższy obserwacjom = ciepły)
        assert (rec.new_t_low is None) != (rec.new_t_high is None)
        assert any("Zbyt mały zakres" in m for m in rec.messages)

    def test_rough_range_quality(self):
        """Rozrzut 8..15°C → jakość 'slope_rough'."""
        bins = _bins_spanning(0.0, 10.0, duty=50.0)  # rozrzut 10°C
        rec = recommend_curve_adjustment(bins, t_low=43.0, t_high=28.0)
        assert rec.data_quality == "slope_rough"

    def test_missing_form_values_no_concrete(self):
        """Bez T_low/T_high nie podajemy konkretnych wartości, ale sygnalizujemy."""
        bins = _bins_spanning(-13.0, 13.0, duty=50.0)
        rec = recommend_curve_adjustment(bins, t_low=None, t_high=None)
        assert rec.new_t_low is None and rec.new_t_high is None
        assert any("formularz" in m.lower() or "T_low" in m for m in rec.messages)


class TestCurveSettingKeys:
    """Nastawy krzywej PER POMPA — klucze settings zawierają pump_id."""

    def test_keys_contain_pump_id(self):
        from app.ui.tab_heating_curve import _curve_setting_keys
        keys = _curve_setting_keys("pompa1")
        assert keys["low"] == "curve_low_temp_pompa1"
        assert keys["high"] == "curve_high_temp_pompa1"
        assert keys["room"] == "curve_room_target_pompa1"

    def test_different_pumps_isolated(self):
        """Dwie pompy → rozłączne zestawy kluczy (nastawy się nie nadpisują)."""
        from app.ui.tab_heating_curve import _curve_setting_keys
        k1 = _curve_setting_keys("pompa1")
        k2 = _curve_setting_keys("pompa2")
        assert set(k1.values()).isdisjoint(set(k2.values()))

    def test_empty_pump_id_falls_back(self):
        """Pusty pump_id → sufiks 'default' (brak gołych kluczy globalnych)."""
        from app.ui.tab_heating_curve import _curve_setting_keys
        keys = _curve_setting_keys("")
        assert keys["low"] == "curve_low_temp_default"

    def test_changed_at_key_present(self):
        """Klucz daty zmiany krzywej też per pompa."""
        from app.ui.tab_heating_curve import _curve_setting_keys
        keys = _curve_setting_keys("pompa2")
        assert keys["changed_at"] == "curve_changed_at_pompa2"


class TestSinceTsFilter:
    """Filtr since_ts — analiza tylko danych wygenerowanych przez aktualną krzywą."""

    def test_episodes_before_since_ts_dropped(self):
        """Epizody rozpoczęte przed since_ts są pomijane."""
        events = [
            (0, "heat_hot_water"), (H, "hot_water"),            # ep1 start=0
            (10 * H, "heat_hot_water"), (11 * H, "hot_water"),  # ep2 start=10H
        ]
        # since_ts między epizodami → zostaje tylko drugi
        eps = detect_heating_episodes(events, since_ts=5 * H)
        assert len(eps) == 1
        assert eps[0].start_ts == 10 * H

    def test_none_since_ts_keeps_all(self):
        """since_ts=None → bez filtra (całość)."""
        events = [
            (0, "heat_hot_water"), (H, "hot_water"),
            (10 * H, "heat_hot_water"), (11 * H, "hot_water"),
        ]
        assert len(detect_heating_episodes(events, since_ts=None)) == 2

    def test_since_ts_after_all_drops_everything(self):
        """since_ts po wszystkich epizodach → pusto."""
        events = [(0, "heat_hot_water"), (H, "hot_water")]
        assert detect_heating_episodes(events, since_ts=100 * H) == []


class TestDateToEpoch:
    """Konwersja daty lokalnej na epoch (odporna na Windows, z korektą strefy)."""

    def test_roundtrip_known_date(self):
        from app.ui.tab_heating_curve import _date_to_epoch, _parse_iso_date
        from app.config import SERVER_TIMEZONE_OFFSET
        d = _parse_iso_date("2026-01-15")
        assert d is not None
        ep = _date_to_epoch(d)
        # Rekonstrukcja: północ lokalna 2026-01-15 minus offset
        from datetime import datetime
        expected = int((datetime(2026, 1, 15) - datetime(1970, 1, 1)).total_seconds()
                       - SERVER_TIMEZONE_OFFSET * 3600)
        assert ep == expected

    def test_parse_invalid_returns_none(self):
        from app.ui.tab_heating_curve import _parse_iso_date
        assert _parse_iso_date("") is None
        assert _parse_iso_date("nie-data") is None
        assert _parse_iso_date(None) is None
