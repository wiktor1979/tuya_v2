"""Analityka pompy ciepła v2 — diagnostyka, wykrywanie anomalii, korelacje.

Przepisane z v1. Różnice:
- Energia/SCOP/HDD NIE są liczone tutaj — brane z compute_energy()
- Diagnostyka sprężarki, krzywa grzewcza — pracują na surowych df_pivot (bez zmian)
- decode_fault_bitmap — bez zmian
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from app.config import FAULT_BITMAP_LABELS


# =============================================================================
# FAULT BITMAP — bez zmian z v1
# =============================================================================

def decode_fault_bitmap(fault_value: float) -> List[str]:
    """Dekoduje bitmapę fault na listę aktywnych kodów błędów.

    Bitmapa ma 30 bitów: E01-E16 (bit 0-15), P01-P14 (bit 16-29).
    """
    if fault_value is None or fault_value == 0:
        return []
    bitmap = int(fault_value)
    return [label for i, label in enumerate(FAULT_BITMAP_LABELS) if bitmap & (1 << i)]


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class ShortCycleEvent:
    """Pojedynczy cykl krótki."""
    start_time: datetime
    end_time: datetime
    duration_sec: int
    off_duration_sec: int
    cop_avg: float
    energy_wasted_wh: float  # v2: Wh zamiast kWh (precyzja)


@dataclass
class InverterAnalysis:
    """Wyniki analizy pracy inwertera."""
    avg_frequency: float
    max_frequency: float
    min_frequency: float
    std_frequency: float
    stability_score: float
    modulation_efficiency: float
    frequent_starts: int
    optimal_range_pct: float


@dataclass
class WeatherCorrelation:
    """Korelacja pogoda vs wydajność. HDD z compute_energy(), nie liczone tutaj."""
    temp_outside_avg: float
    cop_vs_temp_correlation: float
    cop_at_temp_ranges: Dict[str, float]
    efficiency_drop_per_degree: float
    optimal_temp_range: Tuple[float, float]


@dataclass
class DiagnosticReport:
    """Kompletny raport diagnostyczny."""
    timestamp: datetime
    short_cycles: List[ShortCycleEvent] = field(default_factory=list)
    inverter_analysis: Optional[InverterAnalysis] = None
    weather_correlation: Optional[WeatherCorrelation] = None
    recommendations: List[str] = field(default_factory=list)


# =============================================================================
# DETECT SHORT CYCLES — lekko poprawiony z v1
# =============================================================================

def detect_short_cycles(
    df_pivot: pd.DataFrame,
    max_on_time_sec: int = 300,
    min_power_threshold: float = 5.0,
) -> List[ShortCycleEvent]:
    """Wykrywa cykle krótkie (taktowanie) — częste starty/stop sprężarki.

    Pracuje na df_pivot z kolumnami: czas, comp_freq, COP, P_el_kw (opcjonalna).
    NIE liczy energii z resample — energy_wasted jest przybliżona z P_el × dt.

    Args:
        df_pivot: DataFrame z surową telemetrią (pivotowana, z ffill).
        max_on_time_sec: Maks. czas pracy by uznać za krótki cykl [s].
        min_power_threshold: Próg comp_freq by uznać sprężarkę za pracującą [Hz].
    """
    if df_pivot is None or df_pivot.empty or "comp_freq" not in df_pivot.columns:
        return []

    df = df_pivot[["czas", "comp_freq"]].copy()
    if "COP" in df_pivot.columns:
        df["COP"] = df_pivot["COP"]
    if "P_el_kw" in df_pivot.columns:
        df["P_el_kw"] = df_pivot["P_el_kw"]

    df = df.sort_values("czas")
    df["is_on"] = df["comp_freq"] > min_power_threshold
    df["state_change"] = df["is_on"].astype(int).diff().fillna(0)

    cycles: List[ShortCycleEvent] = []
    last_off_time: Optional[datetime] = None
    last_on_time: Optional[datetime] = None

    for _, row in df.iterrows():
        if row["state_change"] == 1:  # start
            last_on_time = row["czas"]
        elif row["state_change"] == -1 and last_on_time is not None:  # stop
            on_duration = (row["czas"] - last_on_time).total_seconds()

            if 30 < on_duration < max_on_time_sec:
                cycle_data = df[(df["czas"] >= last_on_time) & (df["czas"] <= row["czas"])]
                cop_avg = cycle_data["COP"].mean() if "COP" in cycle_data.columns else 0.0
                cop_avg = cop_avg if not np.isnan(cop_avg) else 0.0

                # Przybliżona energia zmarnowana
                if "P_el_kw" in cycle_data.columns:
                    e_wh = cycle_data["P_el_kw"].mean() * on_duration / 3.6  # kW * s / 3.6 = Wh
                else:
                    e_wh = 0.0

                off_dur = (last_on_time - last_off_time).total_seconds() if last_off_time else 0

                cycles.append(ShortCycleEvent(
                    start_time=last_on_time,
                    end_time=row["czas"],
                    duration_sec=int(on_duration),
                    off_duration_sec=int(off_dur),
                    cop_avg=cop_avg,
                    energy_wasted_wh=e_wh if not np.isnan(e_wh) else 0.0,
                ))

            last_off_time = row["czas"]

    return cycles


# =============================================================================
# ANALYZE INVERTER — bez zmian z v1
# =============================================================================

def analyze_inverter_performance(df_pivot: pd.DataFrame) -> InverterAnalysis:
    """Analizuje pracę inwertera (sprężarki) — stabilność i efektywność.

    Pracuje na comp_freq, nie na energii.
    """
    empty = InverterAnalysis(0, 0, 0, 0, 0, 0, 0, 0)
    if df_pivot is None or df_pivot.empty or "comp_freq" not in df_pivot.columns:
        return empty

    freq = df_pivot["comp_freq"].dropna()
    freq_running = freq[freq > 3]
    if len(freq_running) == 0:
        return empty

    avg_freq = freq_running.mean()
    max_freq = freq_running.max()
    min_freq = freq_running.min()
    std_freq = freq_running.std() if len(freq_running) > 1 else 0.0

    stability_score = max(0, min(100, 100 - (std_freq * 2.5)))

    optimal_mask = (freq_running >= 30) & (freq_running <= 60)
    optimal_range_pct = optimal_mask.sum() / len(freq_running) * 100

    df_sorted = df_pivot.sort_values("czas") if "czas" in df_pivot.columns else df_pivot
    freq_was_zero = df_sorted["comp_freq"].shift(1).fillna(0) <= 5
    freq_now_active = df_sorted["comp_freq"] > 5
    frequent_starts = int((freq_was_zero & freq_now_active).sum())

    running_mask = freq > 0
    modulation_eff = optimal_mask.sum() / running_mask.sum() * 100 if running_mask.sum() > 0 else 0

    return InverterAnalysis(
        avg_frequency=avg_freq,
        max_frequency=max_freq,
        min_frequency=min_freq,
        std_frequency=std_freq,
        stability_score=stability_score,
        modulation_efficiency=modulation_eff,
        frequent_starts=frequent_starts,
        optimal_range_pct=optimal_range_pct,
    )


# =============================================================================
# CORRELATE WEATHER — przepisane, HDD z compute_energy()
# =============================================================================

def correlate_weather_performance(
    df_pivot: pd.DataFrame,
) -> Optional[WeatherCorrelation]:
    """Korelacja COP vs temperatura zewnętrzna.

    Pracuje na df_pivot z kolumnami: COP, amb_temp.
    HDD NIE jest liczone tutaj — brane z compute_energy().
    """
    if df_pivot is None or df_pivot.empty:
        return None
    if "COP" not in df_pivot.columns or "amb_temp" not in df_pivot.columns:
        return None

    valid = df_pivot[
        df_pivot["COP"].notna() & (df_pivot["COP"] > 0.5) & (df_pivot["COP"] < 10)
        & df_pivot["amb_temp"].notna()
    ].copy()

    if len(valid) < 10:
        return None

    temp_avg = valid["amb_temp"].mean()
    cop_vals = valid["COP"].values
    temp_vals = valid["amb_temp"].values

    # Korelacja Pearsona
    correlation = np.corrcoef(cop_vals, temp_vals)[0, 1] if len(cop_vals) > 1 else 0.0
    if np.isnan(correlation):
        correlation = 0.0

    # COP w zakresach temperatur
    ranges = {}
    for label, t_min, t_max in [
        ("<0", -50, 0), ("0-5", 0, 5), ("5-10", 5, 10),
        ("10-15", 10, 15), (">15", 15, 50),
    ]:
        mask = (valid["amb_temp"] >= t_min) & (valid["amb_temp"] < t_max)
        avg = valid.loc[mask, "COP"].mean()
        ranges[label] = float(avg) if not np.isnan(avg) else 0.0

    # Spadek COP na °C (regresja liniowa)
    if len(cop_vals) > 10:
        coeffs = np.polyfit(temp_vals, cop_vals, 1)
        drop_per_degree = float(coeffs[0])
    else:
        drop_per_degree = 0.0

    # Optymalny zakres
    valid["temp_bin"] = pd.cut(valid["amb_temp"], bins=10)
    avg_by_bin = valid.groupby("temp_bin", observed=False)["COP"].mean()
    best_bin = avg_by_bin.idxmax()
    optimal = (best_bin.left, best_bin.right) if best_bin is not None else (0, 0)

    return WeatherCorrelation(
        temp_outside_avg=temp_avg,
        cop_vs_temp_correlation=correlation,
        cop_at_temp_ranges=ranges,
        efficiency_drop_per_degree=drop_per_degree,
        optimal_temp_range=optimal,
    )


# =============================================================================
# GENERATE RECOMMENDATIONS — przepisane, bez energii
# =============================================================================

def generate_recommendations(
    short_cycles: List[ShortCycleEvent],
    inverter: InverterAnalysis,
    weather: Optional[WeatherCorrelation],
) -> List[str]:
    """Generuje rekomendacje na podstawie analiz diagnostycznych."""
    recs: List[str] = []

    if len(short_cycles) > 5:
        total_wh = sum(sc.energy_wasted_wh for sc in short_cycles)
        recs.append(
            f"Wykryto {len(short_cycles)} cykli krótkich (~{total_wh:.0f} Wh straty). "
            "Rozważ zwiększenie histerezy termostatu lub dodanie bufora ciepła."
        )

    if inverter.stability_score < 50:
        recs.append(
            f"Niska stabilność inwertera ({inverter.stability_score:.0f}/100). "
            "Sprawdź zasilanie i sterowanie."
        )

    if inverter.optimal_range_pct < 40 and inverter.avg_frequency > 0:
        recs.append(
            f"Inwerter rzadko w optymalnym zakresie 30-60 Hz ({inverter.optimal_range_pct:.0f}%). "
            "Rozważ optymalizację nastaw."
        )

    if weather and abs(weather.cop_vs_temp_correlation) > 0.7:
        recs.append(
            f"Silna korelacja COP↔temp. (r={weather.cop_vs_temp_correlation:.2f}). "
            "Wydajność mocno zależy od pogody."
        )

    if not recs:
        recs.append("Brak istotnych anomalii. Pompa pracuje prawidłowo.")

    return recs


# =============================================================================
# DIAGNOSTIC REPORT — przepisane, energia z compute_energy()
# =============================================================================

def generate_diagnostic_report(
    df_pivot: pd.DataFrame,
) -> DiagnosticReport:
    """Generuje raport diagnostyczny.

    Energia, SCOP, HDD NIE są liczone tutaj — używaj compute_energy() osobno.
    Ten raport obejmuje: cykle krótkie, inwerter, korelację pogodową, rekomendacje.
    """
    short_cycles = detect_short_cycles(df_pivot)
    inverter = analyze_inverter_performance(df_pivot)
    weather = correlate_weather_performance(df_pivot)
    recs = generate_recommendations(short_cycles, inverter, weather)

    return DiagnosticReport(
        timestamp=datetime.now(),
        short_cycles=short_cycles,
        inverter_analysis=inverter,
        weather_correlation=weather,
        recommendations=recs,
    )


# =============================================================================
# HEATING CURVE ANALYSIS — skopiowane z v1 (pracuje na comp_freq, nie energii)
# =============================================================================

@dataclass
class HeatingCurveBin:
    """Wyniki analizy dla jednego przedziału temperaturowego."""
    temp_min: float
    temp_max: float
    duty_cycle_pct: float
    avg_heat_temp_set: float
    avg_cop: float
    total_hours_co: float
    avg_comp_freq: float
    avg_radiation: Optional[float]
    stop_reason_thermostat_pct: float
    sufficient_data: bool


@dataclass
class HeatingCurveAnalysis:
    """Kompletna analiza krzywej grzewczej."""
    bins: List[HeatingCurveBin]
    temp_range_sufficient: bool
    temp_min_observed: float
    temp_max_observed: float
    estimated_slope: Optional[float]
    recommendation: Optional[str]
    recommendation_detail: Optional[str]
    curve_low_current: Optional[float]
    curve_high_current: Optional[float]


def analyze_heating_curve(
    df_pivot: pd.DataFrame,
    weather_df: pd.DataFrame = None,
    curve_low: float = None,
    curve_high: float = None,
    bin_size: float = 3.0,
    min_hours_per_bin: float = 4.0,
    target_duty_cycle: float = 85.0,
) -> Optional[HeatingCurveAnalysis]:
    """Analizuje krzywą grzewczą na podstawie duty cycle sprężarki w trybie CO.

    Pracuje na comp_freq i heat_temp_set — NIE na energii.
    Skopiowane z v1 bez zmian w logice.
    """
    required = {"amb_temp", "comp_freq", "Tryb", "heat_temp_set", "out_water_temp", "COP", "czas"}
    if df_pivot is None or df_pivot.empty or not required.issubset(df_pivot.columns):
        return None

    co_df = df_pivot[df_pivot["Tryb"] == "CO"].copy()
    if co_df.empty or co_df["amb_temp"].isna().all():
        return None

    if "zone_select" in co_df.columns and co_df["zone_select"].notna().any():
        co_df = co_df[co_df["zone_select"] != 2].copy()
        if co_df.empty:
            return None

    co_df["czas"] = pd.to_datetime(co_df["czas"])
    temp_min_obs = co_df["amb_temp"].min()
    temp_max_obs = co_df["amb_temp"].max()
    temp_range_ok = (temp_max_obs - temp_min_obs) >= 5.0

    # Nasłonecznienie z weather_df
    co_df["total_radiation"] = np.nan
    if weather_df is not None and not weather_df.empty:
        rad_cols = [c for c in ["direct_radiation", "diffuse_radiation"] if c in weather_df.columns]
        if rad_cols:
            w_df = weather_df.copy()
            if "timestamp" in w_df.columns:
                w_df["czas_w"] = pd.to_datetime(w_df["timestamp"], unit="s", utc=True).dt.tz_localize(None)
            elif "czas" in w_df.columns:
                w_df["czas_w"] = pd.to_datetime(w_df["czas"])
            else:
                w_df = None

            if w_df is not None:
                total_rad = w_df[rad_cols].sum(axis=1)
                w_rad = pd.DataFrame({"czas_w": w_df["czas_w"], "total_radiation": total_rad})
                w_rad = w_rad.set_index("czas_w").sort_index()
                co_df = co_df.sort_values("czas")
                co_df["czas"] = pd.to_datetime(co_df["czas"]).dt.as_unit("s")
                w_rad.index = w_rad.index.as_unit("s")
                co_df = pd.merge_asof(
                    co_df, w_rad, left_on="czas", right_index=True,
                    direction="nearest", tolerance=pd.Timedelta("2h"),
                    suffixes=("", "_weather"),
                )
                if "total_radiation_weather" in co_df.columns:
                    co_df["total_radiation"] = co_df["total_radiation_weather"]

    # Biny temperaturowe
    bin_start = np.floor(temp_min_obs / bin_size) * bin_size
    bin_end = np.ceil(temp_max_obs / bin_size) * bin_size
    bin_edges = np.arange(bin_start, bin_end + bin_size, bin_size)

    bins_result: List[HeatingCurveBin] = []
    for i in range(len(bin_edges) - 1):
        b_min, b_max = bin_edges[i], bin_edges[i + 1]
        mask = (co_df["amb_temp"] >= b_min) & (co_df["amb_temp"] < b_max)
        bd = co_df[mask]
        if bd.empty:
            continue

        if len(bd) > 1:
            dt_sec = bd["czas"].diff().dt.total_seconds().median()
            total_hours = len(bd) * dt_sec / 3600.0
        else:
            total_hours = 0.1

        sufficient = total_hours >= min_hours_per_bin
        comp_on = (bd["comp_freq"] > 5).sum()
        duty = comp_on / len(bd) * 100.0 if len(bd) > 0 else 0.0

        avg_set = bd["heat_temp_set"].mean()
        avg_cop = bd.loc[bd["COP"].notna() & (bd["COP"] > 0), "COP"].mean()
        avg_freq = bd.loc[bd["comp_freq"] > 5, "comp_freq"].mean()
        avg_rad = bd["total_radiation"].mean() if bd["total_radiation"].notna().any() else None

        stops = bd[(bd["comp_freq"] <= 5) & (bd["comp_freq"].shift(1) > 5)]
        total_stops = len(stops)
        therm_stops = (stops["out_water_temp"] < stops["heat_temp_set"] - 1.0).sum() if total_stops > 0 else 0
        therm_pct = therm_stops / total_stops * 100 if total_stops > 0 else 0

        bins_result.append(HeatingCurveBin(
            temp_min=b_min, temp_max=b_max, duty_cycle_pct=duty,
            avg_heat_temp_set=avg_set if pd.notna(avg_set) else 0,
            avg_cop=avg_cop if pd.notna(avg_cop) else 0,
            total_hours_co=total_hours,
            avg_comp_freq=avg_freq if pd.notna(avg_freq) else 0,
            avg_radiation=avg_rad,
            stop_reason_thermostat_pct=therm_pct,
            sufficient_data=sufficient,
        ))

    if not bins_result:
        return None

    # Estymacja nachylenia
    valid_bins = [b for b in bins_result if b.sufficient_data and b.avg_heat_temp_set > 0]
    slope = None
    if len(valid_bins) >= 2:
        temps = [(b.temp_min + b.temp_max) / 2 for b in valid_bins]
        sets = [b.avg_heat_temp_set for b in valid_bins]
        if len(set(temps)) >= 2:
            slope = float(np.polyfit(temps, sets, 1)[0])

    rec, detail = _generate_curve_recommendation(bins_result, curve_low, curve_high, target_duty_cycle, slope)

    return HeatingCurveAnalysis(
        bins=bins_result, temp_range_sufficient=temp_range_ok,
        temp_min_observed=temp_min_obs, temp_max_observed=temp_max_obs,
        estimated_slope=slope, recommendation=rec, recommendation_detail=detail,
        curve_low_current=curve_low, curve_high_current=curve_high,
    )


def _generate_curve_recommendation(
    bins: List[HeatingCurveBin], curve_low, curve_high, target_dc, slope,
) -> Tuple[Optional[str], Optional[str]]:
    """Generuje rekomendację zmiany krzywej na podstawie duty cycle."""
    valid = [b for b in bins if b.sufficient_data]
    if not valid:
        return None, "Za mało danych. Potrzeba min. 4h pracy CO per zakres temperatur."

    max_duty = max(b.duty_cycle_pct for b in valid)
    if max_duty < 5.0:
        return (
            "ℹ️ Pompa prawie nie pracuje w trybie CO",
            "Duty cycle < 5%. Prawdopodobnie poza sezonem grzewczym.",
        )

    mid = np.median([(b.temp_min + b.temp_max) / 2 for b in valid])
    low_bins = [b for b in valid if (b.temp_min + b.temp_max) / 2 <= mid]
    high_bins = [b for b in valid if (b.temp_min + b.temp_max) / 2 > mid]

    def w_duty(bl):
        th = sum(b.total_hours_co for b in bl)
        return sum(b.duty_cycle_pct * b.total_hours_co for b in bl) / th if th > 0 else None

    def w_freq(bl):
        th = sum(b.total_hours_co for b in bl)
        return sum(b.avg_comp_freq * b.total_hours_co for b in bl) / th if th > 0 else None

    duty_low = w_duty(low_bins)
    duty_high = w_duty(high_bins)
    comp_low = w_freq(low_bins)

    def delta(dc):
        if dc is None:
            return 0
        gap = target_dc - dc
        return round(gap / 7.0) if gap > 0 else 0

    low_ok = duty_low is None or duty_low >= target_dc * 0.85
    high_ok = duty_high is None or duty_high >= target_dc * 0.85
    overloaded = comp_low is not None and comp_low > 80

    if low_ok and high_ok:
        return "✅ Krzywa grzewcza ustawiona prawidłowo", "Duty cycle w normie."

    parts, details = [], []
    if not low_ok and not overloaded:
        d = delta(duty_low)
        if curve_low and d > 0:
            parts.append(f"Obniż nastawę -15°C z {curve_low:.0f}°C na ~{curve_low - d:.0f}°C")
        elif d > 0:
            parts.append(f"Obniż nastawę -15°C o ~{d}°C")
        details.append(f"Mróz: duty {duty_low:.0f}% (cel {target_dc:.0f}%). Nastawa za wysoka.")

    if overloaded:
        parts.append("Podnieś nastawę -15°C o 1-2°C")
        details.append(f"Mróz: sprężarka na max ({comp_low:.0f} Hz). Nastawa za niska.")

    if not high_ok:
        d = delta(duty_high)
        if curve_high and d > 0:
            parts.append(f"Obniż nastawę +20°C z {curve_high:.0f}°C na ~{curve_high - d:.0f}°C")
        elif d > 0:
            parts.append(f"Obniż nastawę +20°C o ~{d}°C")
        details.append(f"Ciepło: duty {duty_high:.0f}% (cel {target_dc:.0f}%). Nastawa za wysoka.")

    if not parts:
        return None, "Brak jednoznacznej rekomendacji."

    return "🔧 " + " | ".join(parts), "\n".join(details)
