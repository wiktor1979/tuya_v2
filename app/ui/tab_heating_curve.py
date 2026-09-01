"""Zakładka: Doradca Krzywej Grzewczej — analiza duty cycle i rekomendacje.

Przeniesione z v1. Logika bez zmian — analyze_heating_curve() w analytics v2
ma te same pola wyniku. Importy get_setting/set_setting i analyze_heating_curve
wskazują na moduły v2 (identyczne ścieżki jak v1).
"""
import pandas as pd
import numpy as np
import streamlit as st

from app.services.analytics import analyze_heating_curve
from app.services.database import get_setting, set_setting


def render(df_pivot: pd.DataFrame, weather_df: pd.DataFrame = None):
    """Renderuje zakładkę Doradca Krzywej Grzewczej."""

    # --- Formularz ustawień krzywej ---
    with st.expander("⚙️ Ustawienia krzywej grzewczej", expanded=False):
        st.caption(
            "Wpisz aktualne wartości krzywej ze sterownika pompy. "
            "Krzywa jest definiowana przez 2 punkty: temperaturę wody przy -15°C i +20°C."
        )
        col_l, col_r = st.columns(2)
        with col_l:
            saved_low = get_setting("curve_low_temp", "")
            curve_low_str = st.text_input(
                "Temp. wody przy **-15°C** [°C]",
                value=saved_low,
                key="curve_low_input",
                placeholder="np. 43",
            )
        with col_r:
            saved_high = get_setting("curve_high_temp", "")
            curve_high_str = st.text_input(
                "Temp. wody przy **+20°C** [°C]",
                value=saved_high,
                key="curve_high_input",
                placeholder="np. 26",
            )

        if st.button("💾 Zapisz ustawienia krzywej", key="save_curve"):
            set_setting("curve_low_temp", curve_low_str.strip())
            set_setting("curve_high_temp", curve_high_str.strip())
            st.success("Zapisano ustawienia krzywej.")

    # Parsuj wartości
    curve_low = None
    curve_high = None
    try:
        if curve_low_str.strip():
            curve_low = float(curve_low_str.strip())
    except ValueError:
        pass
    try:
        if curve_high_str.strip():
            curve_high = float(curve_high_str.strip())
    except ValueError:
        pass

    # --- Analiza ---
    if df_pivot is None or df_pivot.empty:
        st.info("Brak danych do analizy krzywej grzewczej.")
        return

    analysis = analyze_heating_curve(
        df_pivot,
        weather_df=weather_df,
        curve_low=curve_low,
        curve_high=curve_high,
    )

    if analysis is None:
        st.info("Brak danych trybu CO w wybranym zakresie czasu. Analiza krzywej wymaga danych ogrzewania.")
        return

    # --- Warunek rozrzutu temperatur ---
    st.caption(
        f"Zakres temperatur zewnętrznych w danych: "
        f"**{analysis.temp_min_observed:.1f}°C** do **{analysis.temp_max_observed:.1f}°C** "
        f"(rozrzut: {analysis.temp_max_observed - analysis.temp_min_observed:.1f}°C)"
    )

    if not analysis.temp_range_sufficient:
        st.warning(
            "⚠️ Rozrzut temperatur zewnętrznych < 5°C. "
            "Rekomendacje mogą być mało wiarygodne — potrzeba danych z bardziej zróżnicowanych warunków."
        )

    # --- Rekomendacja ---
    if analysis.recommendation:
        if analysis.recommendation.startswith("✅"):
            st.success(analysis.recommendation)
        else:
            st.warning(analysis.recommendation)
        if analysis.recommendation_detail:
            st.caption(analysis.recommendation_detail)

    # --- Estymacja nachylenia ---
    if analysis.estimated_slope is not None:
        slope_info = f"Estymowane nachylenie krzywej: **{analysis.estimated_slope:.2f} °C/°C** "
        if curve_low is not None and curve_high is not None:
            theoretical_slope = (curve_low - curve_high) / (-15.0 - 20.0)
            slope_info += f"(teoretyczne z formularza: {theoretical_slope:.2f} °C/°C)"
        st.caption(slope_info)

    # --- Tabela binów ---
    st.markdown("##### Duty cycle sprężarki wg temperatury zewnętrznej (tryb CO)")

    valid_bins = [b for b in analysis.bins if b.total_hours_co > 0.5]
    if not valid_bins:
        st.info("Za mało danych do wyświetlenia tabeli.")
        return

    rows = []
    for b in valid_bins:
        # Status
        if not b.sufficient_data:
            status = "⚪ <4h"
        elif b.avg_comp_freq > 80 and b.duty_cycle_pct > 95:
            status = "🔴 Max"
        elif b.duty_cycle_pct >= 75:
            status = "✅ OK"
        elif b.duty_cycle_pct >= 55:
            status = "⚠️"
        else:
            status = "🔴"

        # Duty cycle bar (ASCII)
        bar_len = int(b.duty_cycle_pct / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)

        row = {
            "Zakres temp.": f"{b.temp_min:.0f} do {b.temp_max:.0f}°C",
            "Nastawa": f"{b.avg_heat_temp_set:.1f}°C",
            "Duty cycle": f"{bar} {b.duty_cycle_pct:.0f}%",
            "COP": f"{b.avg_cop:.2f}" if b.avg_cop > 0 else "—",
            "Godziny CO": f"{b.total_hours_co:.1f}h",
            "Status": status,
        }

        # Kolumna nasłonecznienia
        if b.avg_radiation is not None:
            row["☀️ Rad."] = f"{b.avg_radiation:.0f} W/m²"

        # Kolumna przyczyn zatrzymań
        if b.stop_reason_thermostat_pct > 0:
            row["Termostat %"] = f"{b.stop_reason_thermostat_pct:.0f}%"

        rows.append(row)

    table_df = pd.DataFrame(rows)
    st.dataframe(table_df, hide_index=True, width="stretch")

    st.caption(
        "**Status:** ✅ OK = duty cycle ≥75% · ⚠️ = 55-75% · 🔴 = <55% lub sprężarka na max · ⚪ = <4h danych\n\n"
        "**Termostat %** = procent zatrzymań gdy woda nie osiągnęła nastawy (termostat pokojowy odciął)"
    )
