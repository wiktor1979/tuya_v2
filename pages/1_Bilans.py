"""Podstrona: Bilans Energetyczny i SCOP — rozbicie CO/CWU, tabela dzienna, wykres SCOP."""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.ui.styles import inject_css, render_scop_box, STATUS_COLORS
from app.ui.helpers import cached_energy
from app.ui.labels import METRICS, scop_delta, e_el_help_with_standby
from app.config import (
    get_param_label,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR,
)
from app.core.energy import scop_from_result

st.set_page_config(page_title="Bilans i SCOP", layout="wide", page_icon="⚡")
inject_css()

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">⚡ Bilans Energetyczny i SCOP</h3>', unsafe_allow_html=True)

# --- Sidebar ---
with st.sidebar:
    st.markdown("### ⚙️ Ustawienia")

    selected_range = st.selectbox("Zakres:", [
        "Dzisiaj", "3 dni", "7 dni", "30 dni", "90 dni",
    ], index=2)

    with st.expander("🔧 Kalibracja"):
        cos_phi = st.number_input("cos φ", value=DEFAULT_COS_PHI, min_value=0.8, max_value=1.0, step=0.01, key="b_cos")
        standby_w = st.number_input("Standby [W]", value=DEFAULT_STANDBY_POWER_W, step=5.0, key="b_sbw")
        active_w = st.number_input("Active [W]", value=DEFAULT_ACTIVE_POWER_W, step=5.0, key="b_aw")
        hidden_w = st.number_input("Hidden power [W]", value=DEFAULT_HIDDEN_POWER_W, step=5.0, key="b_hw")
        sensor_f = st.number_input("Sensor factor", value=DEFAULT_SENSOR_FACTOR, step=0.01, key="b_sf")


# --- Obliczenie dat ---
now = datetime.now()
range_days_map = {"Dzisiaj": 0, "3 dni": 3, "7 dni": 7, "30 dni": 30, "90 dni": 90}
days_back = range_days_map[selected_range]

if days_back == 0:
    date_from = now.strftime("%Y-%m-%d")
else:
    date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")

cal = dict(cos_phi=cos_phi, standby_power_w=standby_w, active_power_w=active_w,
           hidden_power_w=hidden_w, sensor_factor=sensor_f)

# --- Obliczenia ---
# Jedno wywołanie (total) — SCOP CO/CWU/total liczymy przez compute_scop() z tego samego wyniku.
# Dzięki temu wszystkie SCOP są spójne i pochodzą z tych samych składowych energii.
energy = cached_energy(date_from=date_from, daily_breakdown=True, **cal)

if energy.e_el_total <= 0:
    st.info("Brak danych energetycznych w wybranym zakresie. Zmień zakres w panelu bocznym.")
    st.stop()

# Kanoniczne SCOP-y (realne, z odliczeniem defrostu)
scop_total = scop_from_result(energy, scope="total", kind="real")
scop_co = scop_from_result(energy, scope="co", kind="real")
scop_cwu = scop_from_result(energy, scope="cwu", kind="real")

# === KPI: 4 SCOP ===
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric(METRICS["scop_nominal"]["label"],
              f"{energy.scop_nominal:.2f}" if energy.scop_nominal > 0 else "—",
              help=METRICS["scop_nominal"]["help"])
with c2:
    delta, delta_color = scop_delta(scop_total)
    st.metric(METRICS["scop_real"]["label"],
              f"{scop_total:.2f}" if scop_total > 0 else "—",
              delta=delta, delta_color=delta_color,
              help=METRICS["scop_real"]["help"])
with c3:
    if energy.e_th_co >= 1.0:
        st.metric(METRICS["scop_co"]["label"], f"{scop_co:.2f}",
                  help=METRICS["scop_co"]["help"])
    else:
        st.metric(METRICS["scop_co_empty"]["label"], "—",
                  help=METRICS["scop_co_empty"]["help"])
with c4:
    if energy.e_th_cwu >= 1.0:
        st.metric(METRICS["scop_cwu"]["label"], f"{scop_cwu:.2f}",
                  help=METRICS["scop_cwu"]["help"])
    else:
        st.metric(METRICS["scop_cwu_empty"]["label"], "—",
                  help=METRICS["scop_cwu_empty"]["help"])

# === Energia: 3 metryki ===
e1, e2, e3 = st.columns(3)
e1.metric(METRICS["e_el"]["label"], f"{energy.e_el_total:.2f} kWh",
          help=e_el_help_with_standby(energy.e_el_standby, energy.e_el_total))
e2.metric(METRICS["e_th"]["label"], f"{energy.e_th_total:.2f} kWh",
          help=METRICS["e_th"]["help"])
e3.metric(METRICS["e_th_defrost"]["label"],
          f"{energy.e_th_defrost:.3f} kWh" if energy.e_th_defrost < 0 else "0 kWh",
          help=METRICS["e_th_defrost"]["help"])

# === Tabela podziału CO/CWU/Defrost/Total ===
st.markdown("---")
st.subheader("📊 Podział energii wg trybu")

rows = [
    ["🏠 CO", f"{energy.e_el_co:.2f}", f"{energy.e_th_co:.2f}", f"{scop_co:.2f}" if scop_co > 0 else "—"],
    ["🚿 CWU", f"{energy.e_el_cwu:.2f}", f"{energy.e_th_cwu:.2f}", f"{scop_cwu:.2f}" if scop_cwu > 0 else "—"],
]
if energy.e_el_standby > 0:
    rows.append(["⏸ Standby", f"{energy.e_el_standby:.2f}", "—", "—"])
if energy.e_th_defrost < 0:
    rows.append(["❄️ Defrost", "—", f"{energy.e_th_defrost:.3f}", "—"])
rows.append(["**Σ Total (realny)**", f"**{energy.e_el_total:.2f}**", f"**{energy.e_th_total_real:.2f}**", f"**{scop_total:.2f}**"])

table_df = pd.DataFrame(rows, columns=["Tryb", "E_el [kWh]", "E_th [kWh]", "SCOP"])
st.table(table_df)

# === Statystyki ===
s1, s2, s3, s4 = st.columns(4)
s1.metric(METRICS["comp_starts"]["label"], f"{energy.comp_starts}",
          help=METRICS["comp_starts"]["help"])
s2.metric(METRICS["comp_hours"]["label"], f"{energy.comp_hours:.1f} h",
          help=METRICS["comp_hours"]["help"])
s3.metric(METRICS["defrost_count"]["label"], f"{energy.defrost_count}",
          help=METRICS["defrost_count"]["help"])
s4.metric(METRICS["amb_temp_avg"]["label"],
          f"{energy.amb_temp_avg:.1f} °C" if energy.amb_temp_avg != 0 else "—",
          help=METRICS["amb_temp_avg"]["help"])

# === Wykres SCOP dziennego ===
st.markdown("---")
st.subheader("📈 SCOP dzienny")

if energy.daily is not None and not energy.daily.empty:
    daily = energy.daily.copy()
    daily["date_str"] = daily["date"].astype(str)

    # Koloruj słupki: zielony >= 3.1, czerwony < 3.1
    colors = [
        "rgba(46,204,113,0.8)" if v >= 3.1 else "rgba(244,67,54,0.8)"
        for v in daily["scop_real"]
    ]

    fig_scop = go.Figure()

    # Słupki SCOP realny
    fig_scop.add_trace(go.Bar(
        x=daily["date_str"], y=daily["scop_real"],
        name="SCOP realny",
        marker_color=colors,
        text=daily["scop_real"].apply(lambda x: f"{x:.2f}" if x > 0 else ""),
        textposition="outside",
    ))

    # Linia SCOP nominalny
    if "scop_nominal" in daily.columns:
        fig_scop.add_trace(go.Scatter(
            x=daily["date_str"], y=daily["scop_nominal"],
            mode="markers+lines", name="SCOP nominalny",
            line=dict(color="rgba(150,150,150,0.5)", width=1, dash="dot"),
            marker=dict(size=4),
        ))

    # Próg opłacalności
    fig_scop.add_hline(y=3.1, line_dash="dash", line_color="#FF9800", line_width=2,
                       annotation_text="⚡ Próg opłacalności (3.1)",
                       annotation=dict(font_size=11, font_color="#FF9800"))

    fig_scop.update_layout(
        template="plotly_dark",
        xaxis_title="Dzień",
        yaxis_title="SCOP",
        yaxis=dict(range=[0, max(6, daily["scop_real"].max() * 1.3 if daily["scop_real"].max() > 0 else 6)]),
        height=400,
        margin=dict(t=20, b=60),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )
    st.plotly_chart(fig_scop, width="stretch")

    # Podsumowanie tekstowe
    days_above = (daily["scop_real"] >= 3.1).sum()
    days_total = len(daily[daily["scop_real"] > 0])
    if days_total > 0:
        if days_above == days_total:
            st.success(f"✅ Wszystkie {days_total} dni powyżej progu opłacalności 3.1")
        else:
            st.warning(
                f"⚠️ {days_total - days_above} z {days_total} dni poniżej progu 3.1 — "
                f"pompa w tych dniach mniej opłacalna niż ogrzewanie gazowe."
            )
else:
    st.info("Brak danych dziennych w wybranym zakresie.")

# === Tabela dzienna ===
st.markdown("---")
st.subheader("📅 Tabela dzienna")

if energy.daily is not None and not energy.daily.empty:
    display = energy.daily.copy()
    display = display.rename(columns={
        "date": "Data",
        "e_el_co": "E_el CO",
        "e_el_cwu": "E_el CWU",
        "e_th_co": "E_th CO",
        "e_th_cwu": "E_th CWU",
        "e_th_defrost": "Defrost",
        "scop_nominal": "SCOP nom.",
        "scop_real": "SCOP real.",
        "hdd": "HDD",
        "amb_temp_avg": "Śr. temp.",
        "comp_starts": "Starty",
        "defrost_count": "Defrosty",
        "comp_hours": "Praca [h]",
    })

    # Formatowanie
    for col in ["E_el CO", "E_el CWU", "E_th CO", "E_th CWU", "HDD"]:
        if col in display.columns:
            display[col] = display[col].round(2)
    for col in ["SCOP nom.", "SCOP real.", "Defrost"]:
        if col in display.columns:
            display[col] = display[col].round(3)
    for col in ["Śr. temp.", "Praca [h]"]:
        if col in display.columns:
            display[col] = display[col].round(1)
    if "Starty" in display.columns:
        display["Starty"] = display["Starty"].astype(int)
    if "Defrosty" in display.columns:
        display["Defrosty"] = display["Defrosty"].astype(int)

    display = display.sort_values("Data", ascending=False)
    st.dataframe(display, hide_index=True, width="stretch")

    st.caption(
        f"Obliczone z surowych danych (bez resample) w {energy.compute_time_ms:.0f} ms. "
        f"Próbek: {energy.sample_count:,}. Pominięte gaps: {energy.gaps_skipped}."
    )
else:
    st.info("Brak danych dziennych.")
