"""Podstrona: Porównanie Okresów — bieżący miesiąc, sezon grzewczy, historia."""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.ui.styles import inject_css, render_about
from app.ui.helpers import cached_energy
from app.ui.labels import METRICS
from app.core.energy import compute_scop
from app.config import (
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR,
)

st.set_page_config(page_title="Porównanie Okresów", layout="wide", page_icon="📅")
inject_css()

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">📅 Porównanie Okresów</h3>', unsafe_allow_html=True)

# --- Sidebar ---
with st.sidebar:
    st.markdown("### ⚙️ Ustawienia")
    electricity_price = st.number_input("Cena prądu [zł/kWh]", value=1.10, step=0.05, key="p_el")

    with st.expander("🔧 Kalibracja"):
        cos_phi = st.number_input("cos φ", value=DEFAULT_COS_PHI, min_value=0.8, max_value=1.0, step=0.01, key="p_cos")
        standby_w = st.number_input("Standby [W]", value=DEFAULT_STANDBY_POWER_W, step=5.0, key="p_sbw")
        active_w = st.number_input("Active [W]", value=DEFAULT_ACTIVE_POWER_W, step=5.0, key="p_aw")
        hidden_w = st.number_input("Hidden power [W]", value=DEFAULT_HIDDEN_POWER_W, step=5.0, key="p_hw")
        sensor_f = st.number_input("Sensor factor", value=DEFAULT_SENSOR_FACTOR, step=0.01, key="p_sf")

    render_about()

cal = dict(cos_phi=cos_phi, standby_power_w=standby_w, active_power_w=active_w,
           hidden_power_w=hidden_w, sensor_factor=sensor_f)

# --- Dane: all-time z daily_breakdown ---
energy = cached_energy(daily_breakdown=True, **cal)

if energy.daily is None or energy.daily.empty:
    st.info("Brak danych. Porównanie okresów wymaga danych z co najmniej kilku dni.")
    st.stop()

daily = energy.daily.copy()
daily["date"] = pd.to_datetime(daily["date"])


# =============================================================================
# SEKCJA 1: Bieżący miesiąc z nawigacją ◀ ▶
# =============================================================================

# Session state: wybrany miesiąc
now = datetime.now()
if "selected_month" not in st.session_state:
    st.session_state.selected_month = now.replace(day=1)

sel_month = st.session_state.selected_month

# Dostępne miesiące
min_date = daily["date"].min()
max_date = daily["date"].max()
has_prev = sel_month.replace(day=1) > min_date.replace(day=1)
has_next = sel_month.replace(day=1) < max_date.replace(day=1)

# Nagłówek z nawigacją ◀ ▶
month_name = sel_month.strftime("%B %Y").capitalize()
col_prev, col_title, col_next = st.columns([1, 4, 1])

with col_prev:
    if st.button("◀", disabled=not has_prev, key="month_prev"):
        new = sel_month.replace(day=1) - timedelta(days=1)
        st.session_state.selected_month = new.replace(day=1)
        st.rerun()

with col_title:
    st.subheader(f"📊 {month_name}")

with col_next:
    if st.button("▶", disabled=not has_next, key="month_next"):
        if sel_month.month == 12:
            new = sel_month.replace(year=sel_month.year + 1, month=1, day=1)
        else:
            new = sel_month.replace(month=sel_month.month + 1, day=1)
        st.session_state.selected_month = new
        st.rerun()

# Filtruj dane na wybrany miesiąc
month_mask = (daily["date"].dt.year == sel_month.year) & (daily["date"].dt.month == sel_month.month)
month_daily = daily[month_mask]

if month_daily.empty:
    st.info(f"Brak danych za {month_name}.")
else:
    m_el_co = month_daily["e_el_co"].sum()
    m_el_cwu = month_daily["e_el_cwu"].sum()
    m_el_standby = month_daily["e_el_standby"].sum() if "e_el_standby" in month_daily.columns else 0
    m_el_total = m_el_co + m_el_cwu + m_el_standby
    m_th_co = month_daily["e_th_co"].sum()
    m_th_cwu = month_daily["e_th_cwu"].sum()
    m_th_defrost = month_daily["e_th_defrost"].sum() if "e_th_defrost" in month_daily.columns else 0
    m_hdd = month_daily["hdd"].sum()
    # SCOP całkowity (real): (E_th CO+CWU + e_th_defrost[≤0]) / (E_el CO+CWU+standby)
    # e_th_defrost jest ujemne, więc dodanie obniża licznik (= odjęcie strat)
    m_scop = compute_scop(m_el_co, m_el_cwu, m_el_standby, m_th_co, m_th_cwu, m_th_defrost,
                          scope="total", kind="real")
    m_cost = m_el_total * electricity_price
    m_days = len(month_daily)

    st.caption(f"{m_days} dni danych")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("HDD", f"{m_hdd:.0f}", help=METRICS["hdd_total"]["help"])
    k2.metric("SCOP", f"{m_scop:.2f}" if m_scop > 0 else "—", help=METRICS["scop_total_period"]["help"])
    k3.metric("Energia", f"{m_el_total:.1f} kWh", help=METRICS["e_el_short"]["help"])
    k4.metric("Koszt", f"{m_cost:.0f} zł", help=METRICS["cost_total"]["help"])


# =============================================================================
# SEKCJA 2: Sezon grzewczy
# =============================================================================

st.markdown("---")

# Wyznacz aktualny sezon: wrz-kwi
if now.month >= 9:
    season_start = datetime(now.year, 9, 1)
    season_end = datetime(now.year + 1, 4, 30)
    season_label = f"{now.year}/{now.year + 1}"
else:
    season_start = datetime(now.year - 1, 9, 1)
    season_end = datetime(now.year, 4, 30)
    season_label = f"{now.year - 1}/{now.year}"

season_mask = (daily["date"] >= pd.Timestamp(season_start)) & (daily["date"] <= pd.Timestamp(season_end))
season_daily = daily[season_mask]

st.subheader(f"🏆 Sezon grzewczy {season_label}")

if season_daily.empty:
    st.info(f"Brak danych za sezon {season_label}. Dane pojawią się od września.")
else:
    s_el_co = season_daily["e_el_co"].sum()
    s_el_cwu = season_daily["e_el_cwu"].sum()
    s_el_standby = season_daily["e_el_standby"].sum() if "e_el_standby" in season_daily.columns else 0
    s_el_total = s_el_co + s_el_cwu + s_el_standby
    s_th_co = season_daily["e_th_co"].sum()
    s_th_cwu = season_daily["e_th_cwu"].sum()
    s_th_defrost = season_daily["e_th_defrost"].sum() if "e_th_defrost" in season_daily.columns else 0
    s_hdd = season_daily["hdd"].sum()
    # SCOP całkowity (real): (E_th CO+CWU + e_th_defrost[≤0]) / (E_el CO+CWU+standby)
    # e_th_defrost jest ujemne, więc dodanie obniża licznik (= odjęcie strat)
    s_scop = compute_scop(s_el_co, s_el_cwu, s_el_standby, s_th_co, s_th_cwu, s_th_defrost,
                          scope="total", kind="real")
    s_cost = s_el_total * electricity_price
    s_kwh_hdd = s_el_co / s_hdd if s_hdd > 5 else 0
    s_days = len(season_daily)

    date_from_s = season_daily["date"].min().strftime("%Y-%m-%d")
    date_to_s = season_daily["date"].max().strftime("%Y-%m-%d")
    st.caption(f"{date_from_s} — {date_to_s} ({s_days} dni)")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("SCOP sezon", f"{s_scop:.2f}" if s_scop > 0 else "—",
              help=METRICS["scop_total_period"]["help"])
    s2.metric("Σ HDD", f"{s_hdd:.0f}", help=METRICS["hdd_total"]["help"])
    s3.metric("kWh/HDD", f"{s_kwh_hdd:.2f}" if s_kwh_hdd > 0 else "—",
              help="Zużycie prądu CO na stopniodzień.\nIm niżej tym lepiej.")
    s4.metric("Σ Koszt", f"{s_cost:.0f} zł", help=METRICS["cost_total"]["help"])


# =============================================================================
# SEKCJA 3: Tabela miesięczna i wykresy (historia)
# =============================================================================

st.markdown("---")
st.subheader("📋 Statystyki miesięczne")

daily["month"] = daily["date"].dt.to_period("M")
monthly = daily.groupby("month").agg(
    e_el_co=("e_el_co", "sum"),
    e_el_cwu=("e_el_cwu", "sum"),
    e_el_standby=("e_el_standby", "sum") if "e_el_standby" in daily.columns else ("e_el_co", lambda x: 0),
    e_th_co=("e_th_co", "sum"),
    e_th_cwu=("e_th_cwu", "sum"),
    e_th_defrost=("e_th_defrost", "sum"),
    hdd=("hdd", "sum"),
    amb_temp_avg=("amb_temp_avg", "mean"),
    comp_starts=("comp_starts", "sum"),
    defrost_count=("defrost_count", "sum"),
    comp_hours=("comp_hours", "sum"),
    days=("date", "count"),
).reset_index()

monthly["e_el_total"] = monthly["e_el_co"] + monthly["e_el_cwu"] + monthly["e_el_standby"]
monthly["e_th_total"] = monthly["e_th_co"] + monthly["e_th_cwu"]
# SCOP przez kanoniczną compute_scop() — te same wzory co w kartach i silniku
monthly["SCOP_total"] = monthly.apply(
    lambda r: compute_scop(r["e_el_co"], r["e_el_cwu"], r["e_el_standby"],
                           r["e_th_co"], r["e_th_cwu"], r["e_th_defrost"],
                           scope="total", kind="real"), axis=1)
monthly["SCOP_co"] = monthly.apply(
    lambda r: compute_scop(r["e_el_co"], r["e_el_cwu"], r["e_el_standby"],
                           r["e_th_co"], r["e_th_cwu"], r["e_th_defrost"],
                           scope="co", kind="real"), axis=1)
monthly["SCOP_cwu"] = monthly.apply(
    lambda r: compute_scop(r["e_el_co"], r["e_el_cwu"], r["e_el_standby"],
                           r["e_th_co"], r["e_th_cwu"], r["e_th_defrost"],
                           scope="cwu", kind="real"), axis=1)
# 0.0 (brak danych) → NaN dla czytelności tabeli/wykresu
for _c in ["SCOP_total", "SCOP_co", "SCOP_cwu"]:
    monthly[_c] = monthly[_c].where(monthly[_c] > 0, np.nan)
monthly["kWh_per_HDD"] = np.where(monthly["hdd"] > 1, monthly["e_el_co"] / monthly["hdd"], np.nan)
monthly["koszt"] = monthly["e_el_total"] * electricity_price
monthly["month_str"] = monthly["month"].dt.strftime("%Y-%m")

# Tabela
display = monthly[[
    "month_str", "days", "hdd", "e_el_co", "e_el_cwu", "e_el_total",
    "SCOP_total", "SCOP_co", "SCOP_cwu", "kWh_per_HDD", "koszt",
    "comp_hours", "comp_starts", "defrost_count", "amb_temp_avg",
]].copy()
display.columns = [
    "Miesiąc", "Dni", "HDD", "E_el CO", "E_el CWU", "E_el Σ",
    "SCOP Σ", "SCOP CO", "SCOP CWU", "kWh/HDD", "Koszt [zł]",
    "Praca [h]", "Starty", "Defrosty", "Śr. temp. [°C]",
]
for col in ["HDD", "E_el CO", "E_el CWU", "E_el Σ", "Koszt [zł]"]:
    display[col] = display[col].round(1)
for col in ["SCOP Σ", "SCOP CO", "SCOP CWU", "kWh/HDD"]:
    display[col] = display[col].round(2)
display["Praca [h]"] = display["Praca [h]"].round(1)
display["Śr. temp. [°C]"] = display["Śr. temp. [°C]"].round(1)
display["Starty"] = display["Starty"].astype(int)
display["Defrosty"] = display["Defrosty"].astype(int)

st.dataframe(display, width="stretch", hide_index=True)

st.caption(
    "**HDD** = Heating Degree Days (baza 15°C). "
    "**kWh/HDD** = zużycie energii el. CO na stopniodzień (bez CWU) — im niżej tym lepiej.\n\n"
    "**SCOP Σ** = całkowity SCOP systemu: (ciepło CO+CWU − straty defrostu) / (prąd CO+CWU+standby). "
    "To ta sama wartość co karta „SCOP” u góry.  \n"
    "**SCOP CO** = tylko ogrzewanie: (ciepło CO − straty defrostu) / prąd CO.  \n"
    "**SCOP CWU** = tylko ciepła woda: ciepło CWU / prąd CWU (defrost nie dotyczy CWU).  \n"
    "Wszystkie liczone jedną funkcją `compute_scop()` — realny (z odliczeniem defrostu)."
)

# === Wykres: SCOP miesięczny CO vs CWU ===
st.markdown("---")
st.subheader("🏆 SCOP — porównanie miesięczne")

scop_df = monthly[monthly["SCOP_co"].notna() | monthly["SCOP_cwu"].notna()].copy()
if not scop_df.empty:
    fig_scop = go.Figure()
    if scop_df["SCOP_co"].notna().any():
        fig_scop.add_trace(go.Bar(
            x=scop_df["month_str"], y=scop_df["SCOP_co"],
            name="SCOP CO", marker_color="#2ECC71",
            text=scop_df["SCOP_co"].round(2), textposition="outside",
        ))
    if scop_df["SCOP_cwu"].notna().any():
        fig_scop.add_trace(go.Bar(
            x=scop_df["month_str"], y=scop_df["SCOP_cwu"],
            name="SCOP CWU", marker_color="#3498DB",
            text=scop_df["SCOP_cwu"].round(2), textposition="outside",
        ))
    fig_scop.add_hline(y=3.1, line_dash="dash", line_color="orange",
                       annotation_text="Próg opłacalności 3.1")
    fig_scop.update_layout(
        template="plotly_dark", xaxis_title="Miesiąc", yaxis_title="SCOP",
        barmode="group", hovermode="x unified", height=400, margin=dict(t=20, b=60),
    )
    st.plotly_chart(fig_scop, width="stretch")

# === Wykres: HDD vs Energia CO ===
st.markdown("---")
st.subheader("🌡️ HDD vs Zużycie energii CO")

fig_hdd = go.Figure()
fig_hdd.add_trace(go.Bar(
    x=monthly["month_str"], y=monthly["hdd"],
    name="HDD", marker_color="#3498DB", opacity=0.6, yaxis="y",
))
fig_hdd.add_trace(go.Scatter(
    x=monthly["month_str"], y=monthly["e_el_co"],
    name="E_el CO [kWh]", mode="lines+markers",
    line=dict(color="#E74C3C", width=3), yaxis="y2",
))
fig_hdd.update_layout(
    template="plotly_dark", xaxis_title="Miesiąc",
    yaxis=dict(title="HDD", side="left"),
    yaxis2=dict(title="Energia el. CO [kWh]", side="right", overlaying="y"),
    hovermode="x unified", height=400, margin=dict(t=20, b=60),
    legend=dict(x=0, y=1.1, orientation="h"),
)
st.plotly_chart(fig_hdd, width="stretch")

# === Metodologia ===
st.markdown("---")
with st.expander("📖 Metodologia"):
    st.markdown(f"""
**Źródło:** `compute_energy(daily_breakdown=True)` — energia z surowych danych, bez resample.

**SCOP** (wszystkie liczone jedną funkcją `compute_scop()`, wariant *realny* = z odliczeniem defrostu):
- **SCOP Σ (total)** = (E_th CO + E_th CWU + E_th_defrost) / (E_el CO + E_el CWU + E_el standby)
- **SCOP CO** = (E_th CO + E_th_defrost) / E_el CO — defrost obciąża tylko ogrzewanie
- **SCOP CWU** = E_th CWU / E_el CWU — defrost nie dotyczy CWU

`E_th_defrost` jest ujemne (strata ciepła oddanego z instalacji podczas odszraniania).
Prąd standby (sprężarka OFF) wchodzi wyłącznie do mianownika SCOP Σ.

**HDD:** max(0, 15°C - średnia_temp_dzienna). **kWh/HDD:** E_el CO / HDD (tylko CO).

**Sezon grzewczy:** 1 września – 30 kwietnia. **Koszt:** E_el × {electricity_price:.2f} zł/kWh.

Obliczone z {energy.sample_count:,} próbek w {energy.compute_time_ms:.0f} ms.
""")
