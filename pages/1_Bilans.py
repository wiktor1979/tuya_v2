"""Podstrona: Bilans Energetyczny i SCOP — rozbicie CO/CWU, tabela dzienna, wykres SCOP."""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.ui.styles import inject_css, render_scop_box, STATUS_COLORS, render_about
from app.ui.helpers import cached_energy, load_calibration, cached_meter_energy, cached_meter_energy_daily, get_selected_pump
from app.ui.analiza_helpers import load_analiza_pivot
from app.ui.labels import METRICS, scop_delta, e_el_help_with_standby
from app.config import (
    get_param_label, list_pumps, get_pump,
)
from app.core.energy import scop_from_result

st.set_page_config(page_title="Bilans i SCOP", layout="wide", page_icon="⚡")
inject_css()

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">⚡ Bilans i SCOP</h3>', unsafe_allow_html=True)

# --- Sidebar ---
with st.sidebar:
    st.markdown("### ⚙️ Ustawienia")

    # --- Wybór pompy (zapamiętany w query_params: ?pump=...) ---
    _pumps = list_pumps()
    _pump_ids = [p["id"] for p in _pumps]
    _pump_names = {p["id"]: p["name"] for p in _pumps}
    _current_pump = get_selected_pump()
    _idx = _pump_ids.index(_current_pump["id"]) if _current_pump["id"] in _pump_ids else 0
    if len(_pumps) > 1:
        _sel_id = st.selectbox(
            "Pompa:", _pump_ids, index=_idx,
            format_func=lambda pid: _pump_names.get(pid, pid),
            key="pump_select",
        )
        if st.query_params.get("pump") != _sel_id:
            st.query_params["pump"] = _sel_id
            st.rerun()
    else:
        _sel_id = _current_pump["id"]
    selected_pump = get_pump(_sel_id)
    sel_device_id = selected_pump["device_id"]
    sel_meter_id = selected_pump["meter_id"]

    cal = load_calibration()

    render_about()


# --- Przełącznik zakresu (jeden wiersz, wygodny na telefonie) ---
now = datetime.now()

with st.container(key="bilans_range"):
    # Pełne etykiety z zakresem dat w comboboxie
    today_str = now.strftime("%d-%m")
    d3_from = (now - timedelta(days=3)).strftime("%d-%m")
    d7_from = (now - timedelta(days=7)).strftime("%d-%m")
    d30_from = (now - timedelta(days=30)).strftime("%d-%m")
    d90_from = (now - timedelta(days=90)).strftime("%d-%m")
    
    range_labels = [
        f"📅 Dzisiaj ({today_str})",
        f"📅 3 dni ({d3_from} — {today_str})",
        f"📅 7 dni ({d7_from} — {today_str})",
        f"📅 30 dni ({d30_from} — {today_str})",
        f"📅 90 dni ({d90_from} — {today_str})",
    ]
    
    selected_idx = st.selectbox(
        "Zakres:",
        range_labels,
        index=2,
        label_visibility="collapsed"
    )
    
    # Przetłumacz wybraną etykietę na liczbę dni
    range_days_map = {"Dzisiaj": 0, "3 dni": 3, "7 dni": 7, "30 dni": 30, "90 dni": 90}
    
    # Wyciągnij nazwę z etykiety (np. "📅 7 dni (x — y)" → "7 dni")
    if "Dzisiaj" in selected_idx:
        selected_range_name = "Dzisiaj"
    else:
        # "📅 7 dni (obecnie — 7 dni temu)" → split(" ") → ["📅", "7", "dni", ...]
        parts = selected_idx.split(" ")
        if len(parts) >= 3:
            selected_range_name = parts[1] + " " + parts[2]
        else:
            selected_range_name = "7 dni"
    
    days_back = range_days_map.get(selected_range_name, 7)

    # Oblicz zakres dat
    if days_back == 0:
        date_from = now.strftime("%Y-%m-%d")
        date_from_display = now.strftime("%d-%m")
        date_to_display = now.strftime("%d-%m")
        _okres = f"📅 Dzisiaj ({date_from_display})"
    else:
        date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        date_from_display = date_from[8:10] + "-" + date_from[5:7]
        date_to_display = now.strftime("%d-%m")
        _okres = f"📅 **{selected_range_name}** ({date_from_display} — {date_to_display})"
    
    # Usunięto st.caption(_okres) — informacja już w comboboxie

# --- Obliczenia ---
# Jedno wywołanie (total) — SCOP CO/CWU/total liczymy przez compute_scop() z tego samego wyniku.
# Dzięki temu wszystkie SCOP są spójne i pochodzą z tych samych składowych energii.
energy = cached_energy(date_from=date_from, daily_breakdown=True, device_id=sel_device_id, **cal)

# Energia pobrana wg fizycznego licznika (suma add_ele, ×0.001 kWh) — ten sam zakres.
# Pompa bez licznika (meter_id=None) → cached_meter_energy zwraca 0.0 (brak licznika).
has_meter = sel_meter_id is not None
meter_kwh = cached_meter_energy(date_from=date_from, meter_id=sel_meter_id)

if energy.e_el_total <= 0:
    st.info("Brak danych energetycznych w wybranym zakresie. Zmień zakres w panelu bocznym.")
    st.stop()

# Kanoniczne SCOP-y (realne, z odliczeniem defrostu)
scop_total = scop_from_result(energy, scope="total", kind="real")
scop_co = scop_from_result(energy, scope="co", kind="real")
scop_cwu = scop_from_result(energy, scope="cwu", kind="real")

# === KPI: 4 SCOP ===
with st.container(key="bilans_kpi"):
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

    # === Energia: 4 metryki (ostatnia = fizyczny licznik) ===
    e1, e2, e3, e4 = st.columns(4)
    e1.metric(METRICS["e_el"]["label"], f"{energy.e_el_total:.2f} kWh",
              help=e_el_help_with_standby(energy.e_el_standby, energy.e_el_total))
    e2.metric(METRICS["e_th"]["label"], f"{energy.e_th_total:.2f} kWh",
              help=METRICS["e_th"]["help"])
    e3.metric(METRICS["e_th_defrost"]["label"],
              f"{energy.e_th_defrost:.3f} kWh" if energy.e_th_defrost < 0 else "0 kWh",
              help=METRICS["e_th_defrost"]["help"])
    e4.metric(METRICS["e_el_meter"]["label"],
              (f"{meter_kwh:.2f} kWh" if meter_kwh > 0 else "—") if has_meter else "brak",
              help=METRICS["e_el_meter"]["help"] if has_meter else "Ta pompa nie ma fizycznego licznika energii.")

# === Tabela podziału CO/CWU/Defrost/Total ===
st.markdown("---")
st.subheader("📊 Podział energii wg trybu")

rows = [
    ["🏠 CO", f"{energy.e_el_co:.2f}", f"{energy.e_th_co:.2f}", f"{scop_co:.2f}" if scop_co > 0 else "—", "—"],
    ["🚿 CWU", f"{energy.e_el_cwu:.2f}", f"{energy.e_th_cwu:.2f}", f"{scop_cwu:.2f}" if scop_cwu > 0 else "—", "—"],
]
if energy.e_el_standby > 0:
    rows.append(["⏸ Standby", f"{energy.e_el_standby:.2f}", "—", "—", "—"])
if energy.e_th_defrost < 0:
    rows.append(["❄️ Defrost", "—", f"{energy.e_th_defrost:.3f}", "—", "—"])
rows.append([
    "**Σ Total (realny)**",
    f"**{energy.e_el_total:.2f}**",
    f"**{energy.e_th_total_real:.2f}**",
    f"**{scop_total:.2f}**",
    (f"**{meter_kwh:.2f}**" if meter_kwh > 0 else "—") if has_meter else "brak",
])

table_df = pd.DataFrame(rows, columns=["Tryb", "E_el [kWh]", "E_th [kWh]", "SCOP", "E_el licznik [kWh]"])
st.table(table_df)

# === Statystyki ===
with st.container(key="bilans_stats"):
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

# === Wykres COP chwilowego w czasie ===
st.markdown("---")
st.subheader("📈 COP chwilowy w czasie")

_hours_back = days_back * 24 if days_back > 0 else 24
cop_pivot = load_analiza_pivot(hours_back=_hours_back, cos_phi=cal["cos_phi"], device_id=sel_device_id)

if cop_pivot is not None and not cop_pivot.empty and cop_pivot["COP"].notna().any():
    fig_cop = go.Figure()
    fig_cop.add_trace(go.Scatter(
        x=cop_pivot["czas"], y=cop_pivot["COP"],
        mode="lines", name="COP",
        line=dict(color="#4CAF50", width=2),
        fill="tozeroy", fillcolor="rgba(76,175,80,0.1)",
    ))
    # Progi referencyjne
    fig_cop.add_hline(y=4.2, line_dash="dash", line_color="rgba(76,175,80,0.5)",
                      annotation_text="Norma A7/W35 (4.2)")
    fig_cop.add_hline(y=3.1, line_dash="dash", line_color="rgba(255,152,0,0.8)",
                      annotation_text="⚡ Próg opłacalności (3.1)",
                      annotation=dict(font_size=11, font_color="#FF9800"))
    fig_cop.add_hline(y=2.5, line_dash="dash", line_color="rgba(244,67,54,0.5)",
                      annotation_text="Min A-7/W35 (2.5)")
    fig_cop.update_layout(
        yaxis_title="COP", xaxis_title="Czas",
        template="plotly_dark", height=350, margin=dict(t=20, b=40),
        yaxis=dict(range=[0, 8]),
    )
    st.plotly_chart(fig_cop, width="stretch")
    st.caption(
        "COP chwilowy = P_cieplna / P_elektryczna w danej próbce (tylko do wizualizacji, "
        "z surowych danych). SCOP okresowy liczony osobno przez compute_energy()."
    )
else:
    st.info("Brak danych COP w wybranym zakresie (pompa nie pracowała lub brak przepływu).")

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

    # Dołącz dzienne zużycie z fizycznego licznika (add_ele) — to samo źródło
    # co box "Prąd pobrany (licznik)", tylko rozbite na doby lokalne.
    meter_daily = cached_meter_energy_daily(date_from=date_from, meter_id=sel_meter_id)
    display["e_el_meter"] = display["date"].astype(str).map(meter_daily)

    display = display.rename(columns={
        "date": "Data",
        "e_el_co": "E_el CO [kWh]",
        "e_el_cwu": "E_el CWU [kWh]",
        "e_el_meter": "E_el licznik [kWh]",
        "e_th_co": "E_th CO [kWh]",
        "e_th_cwu": "E_th CWU [kWh]",
        "e_th_defrost": "E_th defrost [kWh]",
        "scop_nominal": "SCOP nom.",
        "scop_real": "SCOP real.",
        "hdd": "HDD",
        "amb_temp_avg": "Śr. temp. zewn.",
        "comp_starts": "Starty",
        "defrost_count": "Defrosty",
        "comp_hours": "Praca [h]",
    })

    # Formatowanie
    for col in ["E_el CO [kWh]", "E_el CWU [kWh]", "E_el licznik [kWh]",
                "E_th CO [kWh]", "E_th CWU [kWh]", "HDD"]:
        if col in display.columns:
            display[col] = display[col].round(2)
    for col in ["SCOP nom.", "SCOP real.", "E_th defrost [kWh]"]:
        if col in display.columns:
            display[col] = display[col].round(3)
    for col in ["Śr. temp. zewn.", "Praca [h]"]:
        if col in display.columns:
            display[col] = display[col].round(1)
    if "Starty" in display.columns:
        display["Starty"] = display["Starty"].astype(int)
    if "Defrosty" in display.columns:
        display["Defrosty"] = display["Defrosty"].astype(int)

    # Jawna kolejność kolumn: prąd (z licznikiem) → ciepło → SCOP nom.
    # → E_th defrost → SCOP real. → reszta.
    col_order = [
        "Data",
        "E_el CO [kWh]", "E_el CWU [kWh]", "E_el licznik [kWh]",
        "E_th CO [kWh]", "E_th CWU [kWh]",
        "SCOP nom.", "E_th defrost [kWh]", "SCOP real.",
        "HDD", "Śr. temp. zewn.", "Starty", "Defrosty", "Praca [h]",
    ]
    display = display[[c for c in col_order if c in display.columns]]

    display = display.sort_values("Data", ascending=False)
    st.dataframe(display, hide_index=True, width="stretch")

    st.caption(
        "E_el = prąd (model z sondy), E_el licznik = pomiar fizyczny (add_ele). "
        "E_th defrost = ciepło odebrane z instalacji podczas odszraniania (ujemne). "
        f"Obliczone z surowych danych (bez resample) w {energy.compute_time_ms:.0f} ms. "
        f"Próbek: {energy.sample_count:,}. Pominięte gaps: {energy.gaps_skipped}."
    )
else:
    st.info("Brak danych dziennych.")
